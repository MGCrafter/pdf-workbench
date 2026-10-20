"""Real PDF coverage for text, immutable images and vector object annotations."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import gc
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Rectangle
from pypdf.generic import ArrayObject, DecodedStreamObject, NameObject, NumberObject, TextStringObject
from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QSize
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions
from PyQt6.QtWidgets import QApplication

from pdf_workbench.annotations import AnnotationError, PageAnnotation, TextStyle, load_image_asset, rotate_annotation
from pdf_workbench.annotation_assets import ImageAsset, MAX_IMAGE_BYTES, image_pixels, restore_image_asset
from pdf_workbench.core import Workspace, export_pdf, load_source
from pdf_workbench.text_appearance import font_metrics, layout_text


def image_bytes(format="PNG", alpha=True):
    image = QImage(40, 24, QImage.Format.Format_RGBA8888)
    image.fill(QColor(255, 0, 0, 128 if alpha else 255))
    for y in range(12):
        for x in range(20):
            image.setPixelColor(x, y, QColor(0, 180, 0, 255))
    data = QByteArray()
    stream = QBuffer(data)
    stream.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(stream, format)
    return bytes(data)


def pdf_bytes(writer):
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def annots(page):
    return [ref.get_object() for ref in page.get("/Annots", [])]


class AnnotationObjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.asset = load_image_asset(image_bytes())
        self.originals = {}

    def tearDown(self):
        for path, data in self.originals.items():
            self.assertEqual(path.read_bytes(), data)
        self.temp.cleanup()

    def fixture(self, intrinsic=0, foreign=True, user_unit=1):
        writer = PdfWriter()
        page = writer.add_blank_page(width=340, height=460)
        page.cropbox.lower_left = (30, 40)
        page.cropbox.upper_right = (310, 420)
        page[NameObject("/UserUnit")] = NumberObject(user_unit)
        content = DecodedStreamObject()
        content.set_data(b"q 0.8 0.9 1 rg 0 0 340 460 re f Q\n")
        page[NameObject("/Contents")] = writer._add_object(content)
        if intrinsic:
            page.rotate(intrinsic)
        if foreign:
            foreign_pdf = Rectangle(rect=(40, 50, 60, 70), interior_color="00FF00")
            foreign_pdf[NameObject("/Contents")] = TextStringObject("Foreign preserved")
            writer.add_annotation(page, foreign_pdf)
        path = self.directory / f"source-{intrinsic}-{foreign}-{user_unit}.pdf"
        path.write_bytes(pdf_bytes(writer))
        self.originals[path] = path.read_bytes()
        return path

    def objects(self):
        return (
            PageAnnotation("text", "text", (.08, .06, .92, .38), "ÄÖÜ äöü ß €\nGrüße aus Österreich", style=TextStyle(13, (.1, .2, .7), "center")),
            PageAnnotation("image", "image", (.08, .44, .46, .65), asset=self.asset),
            PageAnnotation("stamp", "stamp", (.54, .44, .92, .65), "Stempel", asset=self.asset, content_rotation=90),
            PageAnnotation("check", "checkmark", (.10, .74, .34, .91), style=TextStyle(color=(0, .5, 0))),
            PageAnnotation("cross", "cross", (.60, .74, .84, .91), style=TextStyle(color=(.8, 0, 0))),
        )

    def workspace(self, path, objects=True):
        result = Workspace()
        result.add_sources([load_source(path)])
        if objects:
            for annotation in self.objects():
                result.add_annotation(result.pages[0].uid, annotation)
        return result

    def export(self, workspace, name):
        path = self.directory / name
        export_pdf(workspace.sources, workspace.pages, path)
        return path

    def render_poppler(self, path):
        return subprocess.run(["pdftoppm", "-r", "72", "-cropbox", "-singlefile", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout

    def render_qt(self, path):
        pdf = QPdfDocument(None)
        self.assertEqual(pdf.load(str(path)), QPdfDocument.Error.None_)
        size = pdf.pagePointSize(0).toSize()
        options = QPdfDocumentRenderOptions()
        options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
        image = pdf.render(0, size, options)
        self.assertFalse(image.isNull())
        pdf.close()
        return image

    def test_self_contained_text_font_unicode_and_image_alpha(self):
        output = self.export(self.workspace(self.fixture()), "objects.pdf")
        page = PdfReader(output).pages[0]
        objects = annots(page)[1:]
        self.assertEqual([a["/Subtype"] for a in objects], ["/FreeText", "/Stamp", "/Stamp", "/Stamp", "/Stamp"])
        self.assertEqual(objects[0]["/Contents"], self.objects()[0].text)
        font = objects[0]["/AP"]["/N"]["/Resources"]["/Font"]["/F0"]
        self.assertEqual(font["/Subtype"], "/Type0")
        embedded = font["/DescendantFonts"][0].get_object()["/FontDescriptor"]["/FontFile2"].get_data()
        self.assertEqual(embedded, font_metrics()[0])
        self.assertIn(b"<20AC>", font["/ToUnicode"].get_data())
        # Text stays available to PDF tools that inspect the actual annotation appearance.
        ap = objects[0]["/AP"]["/N"]
        copy = PdfWriter()
        text_page = copy.add_blank_page(width=300, height=200)
        text_page[NameObject("/Resources")] = ap["/Resources"].clone(copy)
        text_page[NameObject("/Contents")] = ap.clone(copy)
        extracted = PdfReader(io.BytesIO(pdf_bytes(copy))).pages[0].extract_text()
        self.assertIn("ÄÖÜ äöü ß €", extracted)
        self.assertIn("Grüße aus Österreich", extracted)
        first_image = objects[1]["/AP"]["/N"]["/Resources"]["/XObject"].raw_get("/Im0")
        second_image = objects[2]["/AP"]["/N"]["/Resources"]["/XObject"].raw_get("/Im0")
        self.assertEqual(first_image, second_image)
        self.assertEqual(len(first_image.get_object()["/SMask"].get_data()), 40*24)
        self.assertEqual(objects[1]["/PDFWorkbench"].raw_get("/Asset"), objects[2]["/PDFWorkbench"].raw_get("/Asset"))
        for obj in objects:
            marker = obj["/PDFWorkbench"]
            self.assertEqual(marker["/Version"], 3)
            self.assertLess(len(marker["/Model"]), 2000)
            self.assertNotIn("data", json.loads(marker["/Model"]))

    @unittest.skipUnless(shutil.which("pdftoppm"), "Poppler unavailable")
    def test_all_sixteen_rotations_roundtrip_edit_delete_and_foreign_preservation(self):
        for intrinsic in (0, 90, 180, 270):
            for rotation in (0, 90, 180, 270):
                with self.subTest(intrinsic=intrinsic, rotation=rotation):
                    workspace = self.workspace(self.fixture(intrinsic))
                    if rotation:
                        workspace.rotate([workspace.pages[0].uid], rotation)
                    first = self.export(workspace, f"first-{intrinsic}-{rotation}.pdf")
                    reopened = self.workspace(first, objects=False)
                    self.assertEqual(len(reopened.pages[0].annotations), 5)
                    models = reopened.pages[0].annotations
                    self.assertEqual(models, tuple(rotate_annotation(a, rotation) for a in self.objects()))
                    clean = PdfReader(io.BytesIO(next(iter(reopened.sources.values())).data))
                    self.assertEqual(len(annots(clean.pages[0])), 1)
                    second = self.export(reopened, f"second-{intrinsic}-{rotation}.pdf")
                    self.assertEqual(self.render_poppler(first), self.render_poppler(second))
                    edited = replace(models[0], text="Geändert: ÄÖÜäöüß€", style=TextStyle(11, (.6, .1, .2), "right"))
                    reopened.update_annotation(reopened.pages[0].uid, edited)
                    edited_pdf = self.export(reopened, f"edited-{intrinsic}-{rotation}.pdf")
                    self.assertNotEqual(self.render_poppler(second), self.render_poppler(edited_pdf))
                    reopened.undo()
                    self.assertEqual(reopened.pages[0].annotations, models)
                    for model in models:
                        reopened.remove_annotation(reopened.pages[0].uid, model.uid)
                    clean_pdf = self.export(reopened, f"clean-{intrinsic}-{rotation}.pdf")
                    self.assertEqual(len(annots(PdfReader(clean_pdf).pages[0])), 1)
                    baseline = self.workspace(self.fixture(intrinsic), objects=False)
                    if rotation:
                        baseline.rotate([baseline.pages[0].uid], rotation)
                    self.assertEqual(self.render_poppler(clean_pdf), self.render_poppler(self.export(baseline, f"baseline-{intrinsic}-{rotation}.pdf")))

    def test_v2_and_v3_can_be_mixed_and_deleted_without_ghosts(self):
        workspace = self.workspace(self.fixture())
        workspace.add_annotation(workspace.pages[0].uid, PageAnnotation("v2", "comment", (.92, .9, .99, .98), "V2"))
        first = self.export(workspace, "mixed.pdf")
        self.assertEqual([a["/PDFWorkbench"]["/Version"] for a in annots(PdfReader(first).pages[0])[1:]], [3]*5+[2])
        reopened = self.workspace(first, objects=False)
        self.assertEqual(len(reopened.pages[0].annotations), 6)
        for model in tuple(reopened.pages[0].annotations):
            reopened.remove_annotation(reopened.pages[0].uid, model.uid)
        output = self.export(reopened, "deleted.pdf")
        self.assertEqual(len(annots(PdfReader(output).pages[0])), 1)

    def test_external_v3_asset_font_style_and_ap_changes_are_preserved_as_foreign(self):
        output = self.export(self.workspace(self.fixture(foreign=False)), "editable.pdf")
        def edit_font(obj):
            font = obj["/AP"]["/N"]["/Resources"]["/Font"]["/F0"]
            font[NameObject("/BaseFont")] = NameObject("/ExternalFont")
        def edit_asset(obj):
            obj["/PDFWorkbench"]["/Asset"].set_data(image_bytes(alpha=False))
        def edit_model(obj):
            marker = obj["/PDFWorkbench"]
            model = json.loads(marker["/Model"])
            model["style"]["font_size"] = 20
            marker[NameObject("/Model")] = TextStringObject(json.dumps(model))
        def edit_ap(obj):
            ap = obj["/AP"]["/N"]
            replacement = DecodedStreamObject()
            replacement.set_data(ap.get_data()+b"\n0 0 1 RG 0 0 m 100 100 l S\n")
            replacement.update({key: value for key,value in ap.items() if key not in ("/Filter", "/Length", "/DecodeParms")})
            obj["/AP"][NameObject("/N")] = replacement
        for index, edit in ((0, edit_font), (1, edit_asset), (0, edit_model), (2, edit_ap)):
            with self.subTest(edit=edit.__name__):
                reader = PdfReader(output)
                writer = PdfWriter()
                writer.clone_document_from_reader(reader)
                edit(annots(writer.pages[0])[index])
                changed = self.directory / f"external-{edit.__name__}.pdf"
                changed.write_bytes(pdf_bytes(writer))
                reopened = self.workspace(changed, objects=False)
                affected = 2 if edit is edit_asset else 1  # Shared immutable asset has two owning annotations.
                self.assertEqual(len(reopened.pages[0].annotations), 5-affected)
                base = PdfReader(io.BytesIO(next(iter(reopened.sources.values())).data))
                self.assertEqual(len(annots(base.pages[0])), affected)

    def test_assets_are_immutable_deduplicated_collectable_and_independent_of_path(self):
        original = image_bytes()
        asset = load_image_asset(original)
        self.assertIs(asset, load_image_asset(original))
        with self.assertRaises(FrozenInstanceError):
            asset.data = b"changed"
        path = self.directory / "temporary.png"
        path.write_bytes(original)
        snapshot = load_image_asset(path.read_bytes())
        path.unlink()
        workspace = self.workspace(self.fixture(foreign=False), objects=False)
        workspace.add_annotation(workspace.pages[0].uid, PageAnnotation("i", "image", (.1, .1, .9, .9), asset=snapshot))
        workspace.duplicate([workspace.pages[0].uid])
        first, second = workspace.pages
        self.assertIs(first.annotations[0].asset, second.annotations[0].asset)
        self.assertNotEqual(first.annotations[0].uid, second.annotations[0].uid)
        workspace.update_annotation(second.uid, replace(second.annotations[0], rect=(.2, .2, .8, .8)))
        self.assertEqual(workspace.pages[0].annotations[0].rect, (.1, .1, .9, .9))
        self.assertEqual(len(PdfReader(self.export(workspace, "copies.pdf")).pages), 2)
        workspace.undo()
        self.assertEqual(workspace.pages[1].annotations[0].rect, (.1, .1, .9, .9))
        # The global interning table must not retain abandoned large pixel snapshots.
        other = load_image_asset(image_bytes(alpha=False))
        reference = weakref.ref(other)
        del other
        gc.collect()
        self.assertIsNone(reference())

    def test_limits_unicode_errors_and_complete_text_fit(self):
        with self.assertRaisesRegex(AnnotationError, "U\\+65E5"):
            PageAnnotation("t", "text", (.1, .1, .9, .9), "日本語")
        with self.assertRaises(AnnotationError):
            PageAnnotation("t", "text", (.1, .1, .9, .9), "x"*20_001)
        for style in (dict(font_size=float("nan")), dict(font_size=True), dict(color=(1, 2, 0)), dict(alignment="justify")):
            with self.assertRaises(AnnotationError):
                TextStyle(**style)
        for data in (b"", b"not a PNG", b"x"*(MAX_IMAGE_BYTES+1)):
            with self.assertRaises(AnnotationError):
                load_image_asset(data)
        huge = QImage(2001, 2000, QImage.Format.Format_RGB32)
        huge.fill(QColor("white"))
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.assertTrue(huge.save(buffer, "PNG"))
        with self.assertRaises(AnnotationError):
            load_image_asset(bytes(data))
        normalized = PageAnnotation("t", "text", (.1, .1, .9, .9), "A\u0308\tÖ\r\nß€")
        self.assertEqual(normalized.text, "Ä    Ö\nß€")
        indented = PageAnnotation("indent", "text", (.1, .1, .9, .9), "  eingerückt\n\tzweite Zeile")
        self.assertEqual([line[0] for line in layout_text(indented.text, 500, 200, 12)], ["  eingerückt", "    zweite Zeile"])
        with self.assertRaisesRegex(AnnotationError, "vollständig"):
            layout_text("many\nlines\nhere", 100, 12, 12)
        workspace = self.workspace(self.fixture(), objects=False)
        workspace.add_annotation(workspace.pages[0].uid, PageAnnotation("t", "text", (.1, .1, .11, .11), "does not fit"))
        output = self.directory / "must-not-exist.pdf"
        with self.assertRaises(Exception):
            export_pdf(workspace.sources, workspace.pages, output)
        self.assertFalse(output.exists())

    def test_jpeg_exif_orientation_is_applied_once_and_roundtrip_keeps_snapshot(self):
        jpeg = image_bytes("JPEG", alpha=False)
        # Minimal EXIF orientation 6: rotate 90 degrees clockwise.
        tiff = b"II" + struct.pack("<HIH", 42, 8, 1) + struct.pack("<HHI", 0x112, 3, 1) + struct.pack("<H", 6) + b"\0\0" + struct.pack("<I", 0)
        exif = b"Exif\0\0" + tiff
        jpeg = jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif)+2) + exif + jpeg[2:]
        asset = load_image_asset(jpeg)
        self.assertEqual((asset.width, asset.height), (24, 40))
        self.assertFalse(asset.has_alpha)
        self.assertEqual(restore_image_asset(asset.data), asset)
        rgb, alpha = image_pixels(asset)
        self.assertEqual(len(rgb), 24*40*3)
        self.assertIsNone(alpha)
        # Rotated source's original green top-left moves into its top-right.
        def pixel(x,y): return rgb[(y*24+x)*3:(y*24+x)*3+3]
        self.assertGreater(pixel(20,5)[1], pixel(20,5)[0])
        self.assertGreater(pixel(2,35)[0], pixel(2,35)[1])

    @unittest.skipUnless(shutil.which("pdftoppm"), "Poppler unavailable")
    def test_qt_and_poppler_really_show_unicode_and_transparent_image_over_background(self):
        output = self.export(self.workspace(self.fixture(foreign=False)), "visible.pdf")
        base = self.fixture(foreign=False)
        qt = self.render_qt(output)
        qt_base = self.render_qt(base)
        self.assertEqual(qt.size(), qt_base.size())
        # QtPdf uses CropBox, as does the independent Poppler render above.
        width, height = qt.width(), qt.height()
        def dark_pixels(image, rect):
            left,top,right,bottom = rect
            return sum(image.pixelColor(x,y).lightness() < 130 for y in range(int(top*height),int(bottom*height)) for x in range(int(left*width),int(right*width)))
        self.assertGreater(dark_pixels(qt, (.08,.06,.92,.38)), dark_pixels(qt_base, (.08,.06,.92,.38))+100)
        mixed = qt.pixelColor(int(.40*width),int(.60*height))
        background = qt_base.pixelColor(int(.40*width),int(.60*height))
        self.assertGreater(mixed.red(), background.red())
        self.assertGreater(mixed.blue(), 60)  # Nonzero background survives semitransparent red.
        self.assertLess(mixed.blue(), background.blue())
        poppler = self.render_poppler(output)
        self.assertNotEqual(poppler, self.render_poppler(base))
        poppler_image = QImage.fromData(poppler)
        self.assertFalse(poppler_image.isNull())
        self.assertGreater(dark_pixels(poppler_image, (.08,.06,.92,.38)), 100)
        transparent = poppler_image.pixelColor(int(.40*width),int(.60*height))
        self.assertGreater(transparent.blue(), 60)
        self.assertLess(transparent.blue(), 220)

    @unittest.skipUnless(shutil.which("pdftoppm"), "Poppler unavailable")
    def test_external_user_unit_rotation_and_compression_preserve_exact_appearance(self):
        output = self.export(self.workspace(self.fixture(foreign=False)), "editable.pdf")
        reader = PdfReader(output)
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        writer.pages[0][NameObject("/UserUnit")] = NumberObject(2)
        writer.pages[0].rotate(90)
        for obj in annots(writer.pages[0]):
            obj["/AP"][NameObject("/N")] = writer._add_object(obj["/AP"]["/N"].flate_encode())
            if obj["/PDFWorkbench"].get("/Asset") is not None:
                obj["/PDFWorkbench"][NameObject("/Asset")] = writer._add_object(obj["/PDFWorkbench"]["/Asset"].flate_encode())
        external = self.directory / "external-page-change.pdf"
        external.write_bytes(pdf_bytes(writer))
        reopened = self.workspace(external, objects=False)
        self.assertEqual(len(reopened.pages[0].annotations), 5)
        self.assertEqual(reopened.pages[0].annotations[0].style.font_size, 26)
        self.assertEqual(reopened.pages[0].annotations[0].content_rotation, 90)
        result = self.export(reopened, "external-reexport.pdf")
        self.assertEqual(self.render_poppler(external), self.render_poppler(result))

    def test_user_unit_does_not_multiply_requested_physical_font_size(self):
        normal = self.export(self.workspace(self.fixture(user_unit=1)), "unit1.pdf")
        doubled = self.export(self.workspace(self.fixture(user_unit=2)), "unit2.pdf")
        for path, expected in ((normal, b"/F0 13.0000000 Tf"), (doubled, b"/F0 6.5000000 Tf")):
            text = annots(PdfReader(path).pages[0])[1]
            self.assertIn(expected, text["/AP"]["/N"].get_data())
            reopened = self.workspace(path, objects=False)
            self.assertEqual(reopened.pages[0].annotations[0].style.font_size, 13)


if __name__ == "__main__":
    unittest.main()
