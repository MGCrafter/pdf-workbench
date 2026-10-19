"""Real-PDF regressions for editable annotations and lossless own round trips."""
from __future__ import annotations

from dataclasses import replace
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Rectangle
from pypdf.generic import FloatObject, NameObject, NumberObject, TextStringObject
from reportlab.pdfgen import canvas

from pdf_workbench.annotations import (
    AnnotationError, PageAnnotation, extract_own_annotations, rotate_annotation,
)
from pdf_workbench.core import PdfWorkbenchError, Workspace, export_pdf, load_source


def examples():
    return (
        PageAnnotation("note", "comment", (.12, .15, .20, .23), "Grüße – 日本語 ✍"),
        PageAnnotation("marker", "highlight", (.10, .30, .75, .42), "ALPHA beta\nSECOND line",
                       rects=((.10, .30, .75, .34), (.10, .38, .55, .42))),
        PageAnnotation("signed", "signature", (.25, .64, .82, .84), "",
                       strokes=(((.03, .80), (.25, .10), (.40, .85), (.95, .30)),
                                ((.10, .92), (.85, .75)))),
    )


def pdf_annotations(page):
    return [ref.get_object() for ref in page.get("/Annots", [])]


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.originals = {}

    def tearDown(self):
        for path, data in self.originals.items():
            self.assertEqual(path.read_bytes(), data, f"source changed: {path}")
        self.temp.cleanup()

    def fixture(self, name="source.pdf", intrinsic=0, foreign=False):
        raw = io.BytesIO()
        pdf = canvas.Canvas(raw, pagesize=(320, 440))
        pdf.setFont("Helvetica", 20)
        pdf.drawString(50, 340, "ALPHA beta")
        pdf.drawString(50, 310, "SECOND line")
        pdf.save()
        writer = PdfWriter()
        page = writer.add_page(PdfReader(io.BytesIO(raw.getvalue())).pages[0])
        page.cropbox.lower_left = (40, 50)
        page.cropbox.upper_right = (280, 400)
        if intrinsic:
            page.rotate(intrinsic)
        if foreign:
            annotation = Rectangle(rect=(60, 80, 100, 120), interior_color="FF0000")
            annotation[NameObject("/Contents")] = TextStringObject("Foreign annotation – erhalten")
            writer.add_annotation(0, annotation)
        path = self.directory / name
        writer.write(path)
        self.originals[path] = path.read_bytes()
        return path

    def workspace(self, path, add=True):
        workspace = Workspace()
        workspace.add_sources([load_source(path)])
        if add:
            for annotation in examples():
                workspace.add_annotation(workspace.pages[0].uid, annotation)
        return workspace

    def export(self, workspace, name):
        path = self.directory / name
        export_pdf(workspace.sources, workspace.pages, path)
        return path

    def assert_numbers(self, actual, expected, places=6):
        self.assertEqual(len(actual), len(expected))
        for left, right in zip(actual, expected):
            self.assertAlmostEqual(float(left), float(right), places=places)

    def test_real_pdf_subtypes_unicode_quads_ink_and_self_contained_appearances(self):
        output = self.export(self.workspace(self.fixture()), "annotated.pdf")
        page = PdfReader(output).pages[0]
        annotations = pdf_annotations(page)
        self.assertEqual([a["/Subtype"] for a in annotations], ["/Text", "/Highlight", "/Ink"])
        self.assertEqual(annotations[0]["/Contents"], "Grüße – 日本語 ✍")
        self.assertEqual(len(annotations[1]["/QuadPoints"]), 16)
        self.assertEqual([len(stroke) for stroke in annotations[2]["/InkList"]], [8, 4])
        self.assert_numbers(annotations[0]["/Rect"], (68.8, 319.5, 88, 347.5))
        self.assert_numbers(annotations[1]["/QuadPoints"][:8], (64, 295, 220, 295, 64, 281, 220, 281))
        for annotation in annotations:
            self.assertEqual(annotation.raw_get("/P"), page.indirect_reference)
            appearance = annotation["/AP"]["/N"]
            self.assertEqual(appearance["/Subtype"], "/Form")
            self.assertGreater(len(appearance.get_data()), 20)
            self.assertGreater(float(appearance["/BBox"][2]), 0)
            self.assertGreater(float(appearance["/BBox"][3]), 0)
        self.assertEqual(annotations[1]["/AP"]["/N"]["/Resources"]["/ExtGState"]["/GS0"]["/BM"], "/Multiply")

    def test_extraction_cleans_flattened_and_indirect_page_before_cloning(self):
        output = self.export(self.workspace(self.fixture()), "annotated.pdf")
        reader = PdfReader(output)
        models, changed = extract_own_annotations(reader)
        self.assertTrue(changed)
        self.assertEqual(models, (examples(),))
        self.assertFalse(reader.pages[0].get("/Annots"))
        self.assertFalse(reader.pages[0].indirect_reference.get_object().get("/Annots"))
        clone = PdfWriter()
        clone.clone_document_from_reader(reader)
        data = io.BytesIO()
        clone.write(data)
        self.assertFalse(PdfReader(io.BytesIO(data.getvalue())).pages[0].get("/Annots"))

    def test_reopen_edit_delete_and_export_does_not_duplicate_or_leave_ghosts(self):
        first = self.export(self.workspace(self.fixture()), "first.pdf")
        first_bytes = first.read_bytes()
        reopened = self.workspace(first, add=False)
        self.assertEqual(reopened.pages[0].annotations, examples())
        base = next(iter(reopened.sources.values()))
        self.assertFalse(PdfReader(io.BytesIO(base.data)).pages[0].get("/Annots"))
        uid = reopened.pages[0].uid
        reopened.update_annotation(uid, replace(examples()[0], text="Bearbeitet äÖ✓"))
        reopened.remove_annotation(uid, "marker")
        second = self.export(reopened, "second.pdf")
        annotations = pdf_annotations(PdfReader(second).pages[0])
        self.assertEqual([a["/Subtype"] for a in annotations], ["/Text", "/Ink"])
        self.assertEqual(annotations[0]["/Contents"], "Bearbeitet äÖ✓")
        again = self.workspace(second, add=False)
        for annotation in tuple(again.pages[0].annotations):
            again.remove_annotation(again.pages[0].uid, annotation.uid)
        third = self.export(again, "third.pdf")
        self.assertFalse(PdfReader(third).pages[0].get("/Annots"))
        self.assertEqual(first.read_bytes(), first_bytes)
        with self.assertRaises(PdfWorkbenchError):
            export_pdf(reopened.sources, reopened.pages, first)
        self.assertEqual(first.read_bytes(), first_bytes)

    def test_mixed_foreign_annotations_survive_reopen_and_own_deletion(self):
        source = self.fixture(foreign=True)
        first = self.export(self.workspace(source), "mixed-first.pdf")
        reopened = self.workspace(first, add=False)
        base = PdfReader(io.BytesIO(next(iter(reopened.sources.values())).data))
        foreign = pdf_annotations(base.pages[0])
        self.assertEqual(len(foreign), 1)
        self.assertEqual(foreign[0]["/Subtype"], "/Square")
        self.assertEqual(foreign[0]["/Contents"], "Foreign annotation – erhalten")
        self.assert_numbers(foreign[0]["/Rect"], (60, 80, 100, 120))
        for annotation in tuple(reopened.pages[0].annotations):
            reopened.remove_annotation(reopened.pages[0].uid, annotation.uid)
        final = self.export(reopened, "mixed-final.pdf")
        annotations = pdf_annotations(PdfReader(final).pages[0])
        self.assertEqual(len(annotations), 1)
        self.assertEqual(annotations[0]["/Contents"], "Foreign annotation – erhalten")

    def test_all_sixteen_crop_intrinsic_workspace_rotations_reopen_stably(self):
        for intrinsic in (0, 90, 180, 270):
            source = self.fixture(f"source-{intrinsic}.pdf", intrinsic=intrinsic)
            for extra in (0, 90, 180, 270):
                with self.subTest(intrinsic=intrinsic, workspace=extra):
                    workspace = self.workspace(source)
                    workspace.rotate([workspace.pages[0].uid], extra)
                    first = self.export(workspace, f"first-{intrinsic}-{extra}.pdf")
                    reopened = self.workspace(first, add=False)
                    self.assertEqual(reopened.pages[0].rotation, 0)
                    expected = tuple(rotate_annotation(a, extra) for a in examples())
                    for actual, wanted in zip(reopened.pages[0].annotations, expected):
                        self.assertEqual((actual.uid, actual.kind, actual.text), (wanted.uid, wanted.kind, wanted.text))
                        self.assert_numbers(actual.rect, wanted.rect)
                    self.assertFalse(PdfReader(io.BytesIO(next(iter(reopened.sources.values())).data)).pages[0].get("/Annots"))
                    second = self.export(reopened, f"second-{intrinsic}-{extra}.pdf")
                    left_page, right_page = PdfReader(first).pages[0], PdfReader(second).pages[0]
                    self.assertEqual(left_page.rotation % 360, (intrinsic + extra) % 360)
                    self.assertEqual(right_page.rotation, left_page.rotation)
                    self.assertEqual(list(right_page.cropbox), [40, 50, 280, 400])
                    left, right = pdf_annotations(left_page), pdf_annotations(right_page)
                    self.assertEqual(len(right), 3)
                    for a, b in zip(left, right):
                        self.assertEqual(a["/Subtype"], b["/Subtype"])
                        self.assertEqual(a["/Contents"], b["/Contents"])
                        self.assert_numbers(a["/Rect"], b["/Rect"])
                        if "/QuadPoints" in a:
                            # The saved display orientation can choose a new
                            # starting corner; each quad must cover the same
                            # four PDF-space points in the same line sequence.
                            def quads(values):
                                return [sorted((float(values[i]), float(values[i+1]))
                                               for i in range(start, start+8, 2))
                                        for start in range(0, len(values), 8)]
                            for x, y in zip(quads(a["/QuadPoints"]), quads(b["/QuadPoints"])):
                                self.assert_numbers([n for p in x for n in p], [n for p in y for n in p])
                        if "/InkList" in a:
                            for x, y in zip(a["/InkList"], b["/InkList"]):
                                self.assert_numbers(x, y)

    @unittest.skipUnless(shutil.which("pdftoppm"), "Poppler renderer unavailable")
    def test_poppler_all_sixteen_rotations_have_visible_stable_appearances(self):
        def render(path):
            return subprocess.run(["pdftoppm", "-r", "72", "-cropbox", "-singlefile", str(path)],
                                  capture_output=True, check=True).stdout
        for intrinsic in (0, 90, 180, 270):
            source = self.fixture(f"visual-{intrinsic}.pdf", intrinsic=intrinsic)
            for extra in (0, 90, 180, 270):
                with self.subTest(intrinsic=intrinsic, workspace=extra):
                    workspace = self.workspace(source)
                    workspace.rotate([workspace.pages[0].uid], extra)
                    first = self.export(workspace, f"visual-first-{intrinsic}-{extra}.pdf")
                    reopened = self.workspace(first, add=False)
                    second = self.export(reopened, f"visual-second-{intrinsic}-{extra}.pdf")
                    first_pixels = render(first)
                    self.assertEqual(first_pixels, render(second), "appearance changed after reopening")
                    for annotation in tuple(reopened.pages[0].annotations):
                        reopened.remove_annotation(reopened.pages[0].uid, annotation.uid)
                    clean = self.export(reopened, f"visual-clean-{intrinsic}-{extra}.pdf")
                    self.assertNotEqual(first_pixels, render(clean), "annotations have no rendered appearance")

    def test_annotation_history_duplicate_ids_and_independent_editing(self):
        workspace = self.workspace(self.fixture())
        original = workspace.pages[0]
        copies = workspace.duplicate([original.uid])
        copied = workspace.pages[1]
        self.assertEqual(copied.uid, copies[0])
        self.assertNotEqual({a.uid for a in original.annotations}, {a.uid for a in copied.annotations})
        after_duplicate = tuple(workspace.pages)
        workspace.undo()
        self.assertEqual(workspace.pages, [original])
        workspace.redo()
        self.assertEqual(tuple(workspace.pages), after_duplicate)
        workspace.update_annotation(copied.uid, replace(copied.annotations[0], text="Kopie"))
        workspace.remove_annotation(copied.uid, copied.annotations[1].uid)
        self.assertEqual(workspace.pages[0].annotations, original.annotations)
        workspace.undo()
        self.assertEqual(len(workspace.pages[1].annotations), 3)
        workspace.undo()
        self.assertEqual(tuple(workspace.pages), after_duplicate)
        workspace.redo()
        workspace.rotate(copies, 90)
        output = self.export(workspace, "duplicates.pdf")
        pages = PdfReader(output).pages
        self.assertEqual([p.rotation for p in pages], [0, 90])
        self.assertEqual(len({ref.idnum for p in pages for ref in p["/Annots"]}), 6)
        for page in pages:
            for annotation in pdf_annotations(page):
                self.assertEqual(annotation.raw_get("/P"), page.indirect_reference)

    def test_invalid_private_metadata_is_preserved_as_foreign(self):
        first = self.export(self.workspace(self.fixture()), "valid.pdf")
        reader = PdfReader(first)
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        annotations = pdf_annotations(writer.pages[0])
        annotations[0]["/PDFWorkbench"][NameObject("/Version")] = NumberObject(999)
        annotations[1][NameObject("/Contents")] = TextStringObject("external edit")
        annotations[2]["/PDFWorkbench"][NameObject("/Model")] = TextStringObject("invalid JSON")
        altered = self.directory / "altered.pdf"
        writer.write(altered)
        original = altered.read_bytes()
        source = load_source(altered)
        self.assertEqual(source.page_annotations, ((),))
        self.assertEqual(source.data, original)
        workspace = Workspace()
        workspace.add_sources([source])
        output = self.export(workspace, "preserved.pdf")
        final = pdf_annotations(PdfReader(output).pages[0])
        self.assertEqual(len(final), 3)
        self.assertEqual(final[1]["/Contents"], "external edit")

    def test_duplicate_private_uids_are_preserved_without_ambiguous_models(self):
        first = self.export(self.workspace(self.fixture()), "valid.pdf")
        writer = PdfWriter()
        writer.clone_document_from_reader(PdfReader(first))
        writer.add_annotation(0, pdf_annotations(writer.pages[0])[0])
        altered = self.directory / "ambiguous.pdf"
        writer.write(altered)
        source = load_source(altered)
        self.assertEqual([a.uid for a in source.page_annotations[0]], ["marker", "signed"])
        remaining = pdf_annotations(PdfReader(io.BytesIO(source.data)).pages[0])
        self.assertEqual(len(remaining), 2)
        self.assertTrue(all(a["/Contents"] == examples()[0].text for a in remaining))

    def test_external_intrinsic_rotation_still_imports_editable_geometry(self):
        first = self.export(self.workspace(self.fixture()), "first.pdf")
        writer = PdfWriter()
        writer.clone_document_from_reader(PdfReader(first))
        writer.pages[0].rotate(90)
        external = self.directory / "external-rotation.pdf"
        writer.write(external)
        reopened = self.workspace(external, add=False)
        self.assertEqual(len(reopened.pages[0].annotations), 3)
        for actual, original in zip(reopened.pages[0].annotations, examples()):
            expected = rotate_annotation(original, 90)
            self.assert_numbers(actual.rect, expected.rect)
        output = self.export(reopened, "external-rotation-output.pdf")
        before = pdf_annotations(PdfReader(external).pages[0])
        after = pdf_annotations(PdfReader(output).pages[0])
        for left, right in zip(before, after):
            self.assert_numbers(left["/Rect"], right["/Rect"])

    def test_external_geometry_appearance_resource_and_model_edits_remain_foreign(self):
        first = self.export(self.workspace(self.fixture()), "first.pdf")
        def change_ink(annotation):
            annotation["/InkList"][0][2] = FloatObject(float(annotation["/InkList"][0][2]) + 12)
        def change_quad(annotation):
            annotation["/QuadPoints"][0] = FloatObject(float(annotation["/QuadPoints"][0]) + 5)
        def change_appearance(annotation):
            appearance = annotation["/AP"]["/N"]
            appearance.set_data(appearance.get_data() + b"\n1 0 0 RG 3 w 0 0 m 12 12 l S\n")
        def change_resource(annotation):
            annotation["/AP"]["/N"]["/Resources"]["/ExtGState"]["/GS0"][NameObject("/ca")] = FloatObject(.9)
        def change_model(annotation):
            model = json.loads(annotation["/PDFWorkbench"]["/Model"])
            model["strokes"][0][0][0] = .9
            annotation["/PDFWorkbench"][NameObject("/Model")] = TextStringObject(json.dumps(model))
        def missing_fingerprint(annotation):
            del annotation["/PDFWorkbench"]["/Fingerprint"]
        mutations = (
            (2, change_ink), (1, change_quad), (0, change_appearance),
            (1, change_resource), (2, change_model), (0, missing_fingerprint),
            (2, lambda a: a["/C"].__setitem__(0, FloatObject(.9))),
        )
        for index, (target, mutate) in enumerate(mutations):
            with self.subTest(mutation=index):
                writer = PdfWriter()
                writer.clone_document_from_reader(PdfReader(first))
                mutate(pdf_annotations(writer.pages[0])[target])
                external = self.directory / f"externally-edited-{index}.pdf"
                writer.write(external)
                data = external.read_bytes()
                source = load_source(external)
                # Only untouched own entries remain editable. The changed entry
                # must stay in the base, not be reconstructed from stale data.
                self.assertEqual(len(source.page_annotations[0]), 2)
                self.assertNotIn(examples()[target].uid, [a.uid for a in source.page_annotations[0]])
                remaining = pdf_annotations(PdfReader(io.BytesIO(source.data)).pages[0])
                self.assertEqual(len(remaining), 1)
                self.assertEqual(remaining[0]["/NM"], "PDFWorkbench:" + examples()[target].uid)
                workspace = Workspace()
                workspace.add_sources([source])
                final = self.export(workspace, f"external-preserved-{index}.pdf")
                final_annotations = pdf_annotations(PdfReader(final).pages[0])
                self.assertEqual(len(final_annotations), 3)
                preserved = final_annotations[0]
                original = pdf_annotations(PdfReader(external).pages[0])[target]
                self.assertEqual(preserved["/AP"]["/N"].get_data(), original["/AP"]["/N"].get_data())
                if "/InkList" in original:
                    for actual, expected in zip(preserved["/InkList"], original["/InkList"]):
                        self.assert_numbers(actual, expected)
                if "/QuadPoints" in original:
                    self.assert_numbers(preserved["/QuadPoints"], original["/QuadPoints"])
                self.assertEqual(external.read_bytes(), data)

    def test_object_renumbering_key_order_and_stream_compression_keep_models_editable(self):
        first = self.export(self.workspace(self.fixture()), "first.pdf")
        writer = PdfWriter()
        writer.clone_document_from_reader(PdfReader(first))
        for annotation in pdf_annotations(writer.pages[0]):
            compressed = annotation["/AP"]["/N"].flate_encode()
            annotation["/AP"][NameObject("/N")] = writer._add_object(compressed)
            reordered = list(annotation.items())[::-1]
            annotation.clear()
            annotation.update(reordered)
        compressed = self.directory / "recompressed.pdf"
        writer.write(compressed)
        source = load_source(compressed)
        self.assertEqual(source.page_annotations, (examples(),))
        self.assertFalse(PdfReader(io.BytesIO(source.data)).pages[0].get("/Annots"))

    def test_validation_and_missing_annotation_operations_preserve_history(self):
        for kwargs in (
            dict(uid="", kind="comment", rect=(0, 0, .1, .1), text="x"),
            dict(uid="a", kind="comment", rect=(0, 0, .1, .1), text=" "),
            dict(uid="a", kind="comment", rect=(float("nan"), 0, .1, .1), text="x"),
            dict(uid="a", kind="highlight", rect=(0, 0, .1, .1)),
            dict(uid="a", kind="signature", rect=(0, 0, .1, .1), strokes=(((.1, .1), (.1, .1)),)),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(AnnotationError):
                PageAnnotation(**kwargs)
        workspace = self.workspace(self.fixture())
        before = tuple(workspace.pages)
        for operation in (
            lambda: workspace.add_annotation(before[0].uid, examples()[0]),
            lambda: workspace.remove_annotation(before[0].uid, "missing"),
            lambda: workspace.update_annotation("missing-page", examples()[0]),
        ):
            with self.assertRaises(PdfWorkbenchError):
                operation()
            self.assertEqual(tuple(workspace.pages), before)
