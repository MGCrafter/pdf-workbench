"""Acceptance checks for source and frozen builds, without test-only dependencies."""
from __future__ import annotations

import argparse
import contextlib
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import time
import traceback
import unittest
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject, TextStringObject
from pdf_workbench import __version__
from pdf_workbench.annotations import PageAnnotation
from pdf_workbench.core import OperationCancelled, PdfWorkbenchError, Workspace, export_pdf, load_source
from pdf_workbench.features import export_pages_separately, parse_page_selection
from pdf_workbench.compression import CompressionOptions
from pdf_workbench.forms import fill_source_form, form_fields


def fixture(path: Path, labels=("A1", "A2", "A3"), rotation=0, crop=False, annotation=False):
    """Author tiny PDFs directly, independently of the library under test."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    pages = []
    annotation_id = 4 + 2 * len(labels)
    for index, label in enumerate(labels):
        page_id = len(objects) + 1
        pages.append(page_id)
        content = (
            f"q 0.12 0.22 0.42 rg BT /F1 28 Tf 32 438 Td ({label}) Tj ET "
            "1 0 0 rg 12 470 18 18 re f Q\n"
        ).encode("ascii")
        extra = f" /Rotate {rotation}"
        if crop:
            extra += " /CropBox [10 20 280 480]"
        if annotation and index == 0:
            extra += f" /Annots [{annotation_id} 0 R]"
        objects.append((
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 500] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R{extra} >>"
        ).encode("ascii"))
        objects.append(f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"endstream")
    if annotation:
        objects.append(b"<< /Type /Annot /Subtype /Square /Rect [60 180 240 300] /IC [0 1 0] /C [0 1 0] >>")
    objects[1] = (f"<< /Type /Pages /Count {len(pages)} /Kids [" + " ".join(f"{p} 0 R" for p in pages) + "] >>").encode("ascii")
    data = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    path.write_bytes(data)


class PdfAcceptance(unittest.TestCase):
    base: Path

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="core-", dir=self.base))
        self.a, self.b, self.out = (self.directory / name for name in ("a.pdf", "b.pdf", "output.pdf"))
        fixture(self.a)
        fixture(self.b, ("B1", "B2"))
        self.original_a = self.a.read_bytes()
        self.ws = Workspace()
        self.ws.add_sources([load_source(self.a), load_source(self.b)])

    def tearDown(self):
        shutil.rmtree(self.directory)

    def export(self, pages=None, **kwargs):
        return export_pdf(self.ws.sources, self.ws.pages if pages is None else pages, self.out, **kwargs)

    def test_merge_reorder_rotate(self):
        p = self.ws.pages
        self.ws.reorder([p[i].uid for i in [4, 0, 2, 1, 3]])
        self.ws.rotate([p[4].uid], 90)
        self.export()
        r = PdfReader(self.out)
        self.assertEqual([p.extract_text().strip() for p in r.pages], ["B2", "A1", "A3", "A2", "B1"])
        self.assertEqual(r.pages[0].rotation, 90)
        self.assertEqual(self.a.read_bytes(), self.original_a)

    def test_split_and_no_overwrite(self):
        self.export([self.ws.pages[1], self.ws.pages[0]])
        self.assertEqual([p.extract_text().strip() for p in PdfReader(self.out).pages], ["A2", "A1"])
        saved = self.out.read_bytes()
        with self.assertRaises(PdfWorkbenchError): self.export()
        self.assertEqual(saved, self.out.read_bytes())

    def test_sources_and_snapshot(self):
        with self.assertRaises(PdfWorkbenchError): export_pdf(self.ws.sources, self.ws.pages, self.a)
        self.a.write_bytes(b"External change")
        self.export()
        self.assertEqual(PdfReader(self.out).pages[0].extract_text().strip(), "A1")
        self.assertEqual(self.a.read_bytes(), b"External change")

    def test_crop_and_existing_rotation(self):
        path = self.directory / "crop.pdf"
        fixture(path, ("C1",), rotation=90, crop=True)
        self.ws.add_sources([load_source(path)])
        self.ws.rotate([self.ws.pages[-1].uid], 90)
        self.export(self.ws.pages[-1:])
        page = PdfReader(self.out).pages[0]
        self.assertEqual(page.rotation, 180)
        self.assertEqual(list(page.cropbox), [10, 20, 280, 480])

    def test_undo_redo_move_delete(self):
        original = list(self.ws.pages)
        ids = [p.uid for p in original]
        self.ws.move(ids[1:3], -1)
        self.assertEqual([p.uid for p in self.ws.pages], [ids[i] for i in [1, 2, 0, 3, 4]])
        self.ws.delete([ids[0]])
        self.ws.undo()
        self.assertEqual(len(self.ws.pages), 5)
        self.ws.redo()
        self.assertEqual(len(self.ws.pages), 4)
        self.ws.undo(); self.ws.undo()
        self.assertEqual(self.ws.pages, original)

    def test_cancel_before_and_after_write(self):
        with self.assertRaises(OperationCancelled): self.export(cancel=lambda: True)
        cancelled = [False]
        with patch("pdf_workbench.core.os.fsync", side_effect=lambda _: cancelled.__setitem__(0, True)):
            with self.assertRaises(OperationCancelled): self.export(cancel=lambda: cancelled[0])
        self.assertFalse(self.out.exists())
        self.assertFalse(list(self.directory.glob(".pdf-workbench-*")))

    def test_exclusive_fallback(self):
        with patch("pdf_workbench.core.os.link", side_effect=OSError("unsupported")):
            self.export()
        self.assertEqual(len(PdfReader(self.out).pages), 5)

    def test_hardlink_is_protected(self):
        try: os.link(self.a, self.out)
        except OSError as error: self.skipTest(f"Hardlink unavailable: {error}")
        with self.assertRaises(PdfWorkbenchError): self.export()
        self.assertEqual(self.out.read_bytes(), self.original_a)

    def test_password_required_and_orphaned_widget_rejected(self):
        writer = PdfWriter()
        writer.add_page(PdfReader(self.a).pages[0])
        writer.encrypt("test-password")
        writer.write(self.out)
        with self.assertRaises(PdfWorkbenchError): load_source(self.out)
        writer = PdfWriter()
        page = writer.add_page(PdfReader(self.a).pages[0])
        widget = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"), NameObject("/Rect"): ArrayObject([NumberObject(n) for n in [10, 10, 40, 40]])})
        page[NameObject("/Annots")] = ArrayObject([writer._add_object(widget)])
        writer.write(self.out)
        with self.assertRaises(PdfWorkbenchError): load_source(self.out)

    def test_aes256_and_image_compression_runtime(self):
        # This runs inside the frozen app too, exercising both native providers.
        from PIL import Image
        image = Image.new("RGB", (2400, 100), (180, 120, 80))
        writer = PdfWriter()
        page = writer.add_blank_page(300, 500)
        raster = DecodedStreamObject()
        raster.set_data(image.tobytes())
        raster.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                       NameObject("/Width"): NumberObject(image.width), NameObject("/Height"): NumberObject(image.height),
                       NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8)})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/Scan"): writer._add_object(raster)})})
        content = DecodedStreamObject(); content.set_data(b"q 300 0 0 100 0 200 cm /Scan Do Q")
        page[NameObject("/Contents")] = writer._add_object(content)
        scan = self.directory / "scan.pdf"; writer.write(scan)
        workspace = Workspace(); workspace.add_sources([load_source(scan)])
        reports = []
        export_pdf(workspace.sources, workspace.pages, self.out, password="acceptance-only",
                   optimization=CompressionOptions("balanced"), compression_report=reports.append)
        reader = PdfReader(self.out)
        self.assertTrue(reader.is_encrypted)
        self.assertEqual(reader.decrypt("wrong"), 0)
        self.assertTrue(reader.decrypt("acceptance-only"))
        self.assertEqual(reader.trailer["/Encrypt"]["/Length"], 256)
        self.assertLess(reader.pages[0].images[0].image.width, 2400)
        self.assertGreater(reports[0].saved_bytes, 0)
        source = load_source(self.out, password="acceptance-only")
        self.assertTrue(source.was_encrypted)
        self.assertFalse(PdfReader(io.BytesIO(source.data)).is_encrypted)

    def test_acroform_fill_export_reopen_runtime(self):
        writer = PdfWriter(clone_from=self.a)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")})
        widget = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
                                   NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject("answer"),
                                   NameObject("/V"): TextStringObject("Before"), NameObject("/DA"): TextStringObject("/Helv 12 Tf 0 g"),
                                   NameObject("/Rect"): ArrayObject(NumberObject(n) for n in (30, 300, 250, 330))})
        field = writer._add_object(widget)
        writer.pages[0][NameObject("/Annots")] = ArrayObject([field])
        writer._root_object[NameObject("/AcroForm")] = writer._add_object(DictionaryObject({
            NameObject("/Fields"): ArrayObject([field]),
            NameObject("/DR"): DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): writer._add_object(font)})})}))
        path = self.directory / "form.pdf"; writer.write(path)
        original = path.read_bytes()
        source = fill_source_form(load_source(path), {"answer": "Grüße aus Wien"})
        workspace = Workspace(); workspace.add_sources([source])
        export_pdf(workspace.sources, workspace.pages[:1], self.out)
        reopened = load_source(self.out)
        self.assertEqual(form_fields(reopened)[0].value, "Grüße aus Wien")
        again = fill_source_form(reopened, {"answer": "Again"})
        self.assertEqual(form_fields(again)[0].value, "Again")
        self.assertEqual(path.read_bytes(), original)

    def test_duplicate_rotation_and_single_undo(self):
        original = list(self.ws.pages)
        copies = self.ws.duplicate([original[0].uid, original[4].uid])
        self.assertEqual(len(copies), 2)
        self.assertEqual(len({p.uid for p in self.ws.pages}), 7)
        self.ws.undo()
        self.assertEqual(self.ws.pages, original)
        self.ws.redo()
        self.ws.rotate([copies[0]], 90)
        self.ws.rotate([copies[1]], 270)
        self.export()
        pages = PdfReader(self.out).pages
        self.assertEqual([p.extract_text().strip() for p in pages], ['A1', 'A1', 'A2', 'A3', 'B1', 'B2', 'B2'])
        self.assertEqual([p.rotation for p in pages], [0, 90, 0, 0, 0, 0, 270])

    def test_range_and_separate_export_protection(self):
        indices = parse_page_selection('5, 1-2, 2', 5)
        self.assertEqual(indices, [0, 1, 4])
        target = self.directory / 'individual'
        export_pages_separately(self.ws.sources, [self.ws.pages[i] for i in indices], target)
        outputs = sorted(target.glob('*.pdf'))
        self.assertEqual(len(outputs), 3)
        self.assertEqual([PdfReader(p).pages[0].extract_text().strip() for p in outputs], ['A1', 'A2', 'B2'])
        saved = {p: p.read_bytes() for p in outputs}
        with self.assertRaises(PdfWorkbenchError):
            export_pages_separately(self.ws.sources, self.ws.pages, target)
        self.assertEqual(saved, {p: p.read_bytes() for p in outputs})

    def test_comment_highlight_signature_roundtrip(self):
        uid = self.ws.pages[0].uid
        note = PageAnnotation('note', 'comment', (.2, .2, .26, .26), text='Prüfung: Größe und Grüße')
        marker = PageAnnotation('marker', 'highlight', (.10, .08, .3, .13),
                                text='A1', rects=((.10, .08, .3, .13),))
        signature = PageAnnotation('signature', 'signature', (.25, .7, .75, .85),
                                   strokes=(((0, .8), (.2, .15), (.4, .8), (.7, .2), (1, .6)),))
        for annotation in (note, marker, signature):
            self.ws.add_annotation(uid, annotation)
        self.export(self.ws.pages[:1])
        page = PdfReader(self.out).pages[0]
        annotations = [item.get_object() for item in page['/Annots']]
        self.assertEqual([item['/Subtype'] for item in annotations], ['/Text', '/Highlight', '/Ink'])
        self.assertEqual(annotations[0]['/Contents'], note.text)
        for item in annotations:
            self.assertTrue(item['/AP']['/N'].get_object().get_data())
        reopened = Workspace()
        reopened.add_sources([load_source(self.out)])
        self.assertEqual(reopened.pages[0].annotations, (note, marker, signature))
        reopened.update_annotation(reopened.pages[0].uid, replace(note, text='Nach Wiederöffnen bearbeitet'))
        reopened.remove_annotation(reopened.pages[0].uid, marker.uid)
        second = self.directory / 'edited-after-reopen.pdf'
        export_pdf(reopened.sources, reopened.pages, second)
        edited = [item.get_object() for item in PdfReader(second).pages[0]['/Annots']]
        self.assertEqual([item['/Subtype'] for item in edited], ['/Text', '/Ink'])
        self.assertEqual(edited[0]['/Contents'], 'Nach Wiederöffnen bearbeitet')
        self.assertEqual(self.a.read_bytes(), self.original_a)

    def test_annotation_history_and_rotated_duplicate(self):
        original = self.ws.pages[0]
        note = PageAnnotation('original-note', 'comment', (.2, .2, .26, .26), text='Original comment')
        self.ws.add_annotation(original.uid, note)
        self.ws.undo()
        self.assertEqual(self.ws.pages[0].annotations, ())
        self.ws.redo()
        duplicate_uid = self.ws.duplicate([original.uid])[0]
        duplicate = self.ws.pages[1]
        self.assertNotEqual(duplicate.annotations[0].uid, note.uid)
        self.ws.update_annotation(duplicate_uid, replace(duplicate.annotations[0], text='Copy comment'))
        self.ws.rotate([duplicate_uid], 90)
        self.export(self.ws.pages[:2])
        pages = PdfReader(self.out).pages
        self.assertEqual([p.rotation for p in pages], [0, 90])
        self.assertEqual([p['/Annots'][0].get_object()['/Contents'] for p in pages], ['Original comment', 'Copy comment'])
        self.assertNotEqual(pages[0]['/Annots'][0], pages[1]['/Annots'][0])
        for page in pages:
            self.assertEqual(page['/Annots'][0].get_object().raw_get('/P'), page.indirect_reference)

def feature_acceptance(window, directory, app, wait_for):
    from dataclasses import replace
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PyQt6.QtGui import QColor, QFont, QImage, QKeyEvent, QPainter
    from PyQt6.QtWidgets import QDialog
    from pdf_workbench.annotations import PageAnnotation, load_image_asset
    from pdf_workbench.core import PageRef
    from pdf_workbench.image_import import ImagePageOptions, load_image_source
    from pdf_workbench.ocr import discover_engine, export_searchable_pdf
    from pdf_workbench.ui.annotation_overlay import registered_text_font_family
    preview = window.preview
    baseline = tuple(window.workspace.pages)
    page_uid = preview.page.uid
    def enter_text(dialog):
        dialog.show(); app.processEvents()
        dialog.editor.setPlainText('  Workbench Text\nGrüße €')
        dialog.font_size.setValue(12)
        assert dialog.validate()
        dialog.accept()
        return QDialog.DialogCode.Accepted
    window.set_annotation_tool('text')
    with patch('pdf_workbench.app.TextObjectDialog.exec', enter_text):
        preview.place_annotation(preview.basis_to_canvas((.1,.1)), preview.basis_to_canvas((.85,.35)))
    text = preview.selected_annotation
    assert text.kind == 'text' and text.text.startswith('  ')
    assert preview.tool == 'select'
    preview.canvas.setFocus(); app.processEvents()
    def key(value, modifiers=Qt.KeyboardModifier.NoModifier):
        event = QKeyEvent(QKeyEvent.Type.KeyPress, value, modifiers)
        app.sendEvent(preview.canvas, event); app.processEvents()
    key(Qt.Key.Key_Right)
    assert preview.selected_annotation.rect[0] > text.rect[0]
    window.undo_button.click(); assert preview.selected_annotation == text
    key(Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    assert len(preview.page.annotations) == len(baseline[0].annotations)+2
    key(Qt.Key.Key_Delete)
    assert len(preview.page.annotations) == len(baseline[0].annotations)+1
    with patch('pdf_workbench.app.TextObjectDialog.get_annotation', return_value=replace(text, text='  Workbench Text\nBearbeitet €')):
        preview.set_selected_annotation(text.uid); key(Qt.Key.Key_F2)
    assert preview.selected_annotation.text.endswith('Bearbeitet €')
    image = QImage(120, 60, QImage.Format.Format_ARGB32); image.fill(QColor(30,160,80,128))
    data = QByteArray(); buffer = QBuffer(data); buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, 'PNG'); buffer.close()
    asset = load_image_asset(bytes(data))
    for index, kind in enumerate(('image','stamp','checkmark','cross')):
        values = {'asset': asset} if kind in ('image','stamp') else {}
        window.add_annotation(page_uid, PageAnnotation('native-'+kind, kind, (.1,.4+index*.1,.4,.48+index*.1), **values))
    objects = directory/'gui-objects.pdf'
    export_pdf(window.workspace.sources, window.workspace.pages, objects)
    reopened = load_source(objects)
    kinds = [a.kind for a in reopened.page_annotations[0]]
    assert kinds[-5:] == ['text','image','stamp','checkmark','cross']
    own_text = reopened.page_annotations[0][-5]
    assert own_text.text == '  Workbench Text\nBearbeitet €'
    pdf = PdfReader(objects); fonts = pdf.pages[0]['/Annots'][-5].get_object()['/AP']['/N']['/Resources']['/Font']
    font = next(iter(fonts.values())).get_object()
    assert '/ToUnicode' in font and '/FontFile2' in font['/DescendantFonts'][0].get_object()['/FontDescriptor']
    # Exercise the GUI's asynchronous search against the newly inserted text.
    window.search_bar.open(); window.search_bar.editor.setText('Workbench Text')
    wait_for(lambda: window.search_bar.worker is None and bool(window.search_bar.results.hits))
    assert window.search_bar.results.hits[0].annotation_uid == text.uid
    assert preview.search_hit is not None
    window.search_bar.dismiss(); assert preview.search_hit is None
    # Image import is one undo transaction and a self-contained PDF snapshot.
    path = directory/'native-image.png'; assert image.save(str(path))
    before_images = tuple(window.workspace.pages)
    with patch('pdf_workbench.app.ImagePagesDialog.get_request', return_value=([str(path)], ImagePageOptions('Letter','landscape',5))):
        window.import_images()
    wait_for(lambda: window.job is None)
    assert len(window.workspace.pages) == len(before_images)+1
    window.page_list.setFocus(); window.undo_button.click()
    assert tuple(window.workspace.pages) == before_images
    checks = ['objects select/move/duplicate/delete/undo', 'objects text/image/stamp/symbol PDF roundtrip',
              'objects bundled font and Unicode', 'asynchronous text search', 'images to PDF and undo']
    # OCR is optional for users, mandatory in release CI with a prepared engine.
    ocr_cache = os.environ.get('PDF_WORKBENCH_OCR_TEST_TESSDATA')
    engine = discover_engine(cache_dir=ocr_cache)
    ocr_info = {'version': engine.version if engine else None,
                'executable': str(engine.executable) if engine else None,
                'languages': list(engine.languages) if engine else [],
                'pdf_font': str(engine.pdf_font) if engine and engine.pdf_font else None}
    print('Native OCR engine: ' + json.dumps(ocr_info))
    if engine and engine.pdf_font and 'eng' in engine.languages:
        scanned = QImage(900, 250, QImage.Format.Format_RGB32); scanned.fill(QColor('white'))
        painter = QPainter(scanned); painter.setPen(QColor('black'))
        painter.setFont(QFont(registered_text_font_family(), 32)); painter.drawText(50, 145, 'LOCAL OCR TEST 123'); painter.end()
        scan = directory/'native-scan.png'; assert scanned.save(str(scan))
        source = load_image_source(scan)
        page = PageRef('native-scan', source.source_id, 0)
        ocr_directory = directory / 'OCR-Grüße'; ocr_directory.mkdir()
        result = export_searchable_pdf({source.source_id: source}, [page], ocr_directory/'durchsuchbar.pdf', engine=engine, languages=('eng',), cache_dir=ocr_cache)
        assert result.processed_pages == 1 and result.skipped_pages == 0
        assert 'OCR TEST 123' in PdfReader(result.path).pages[0].extract_text()
        assert PdfReader(result.path).pages[0]['/Resources']['/XObject']
        checks.append('local OCR searchable text and preserved image')
    elif os.environ.get('PDF_WORKBENCH_REQUIRE_OCR') == '1':
        raise AssertionError('The native release OCR check requires Tesseract, English and pdf.ttf: ' + json.dumps(ocr_info))
    return checks, ocr_info


def gui_acceptance(directory: Path):
    from PyQt6.QtCore import QEvent, QItemSelectionModel, QObject, QPoint, QPointF, QSettings, QSize, QT_VERSION_STR, PYQT_VERSION_STR, Qt, pyqtSignal, qVersion
    from PyQt6.QtGui import QMouseEvent, QPalette, QWheelEvent
    from PyQt6.QtWidgets import QApplication
    from pdf_workbench.app import MainWindow
    from pdf_workbench.ui.theme import ThemeController, COLORS
    from pdf_workbench.ui.annotation_dialogs import CommentDialog
    from PyQt6.QtPdf import QPdfDocument
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    class SystemHints(QObject):
        colorSchemeChanged = pyqtSignal(object)
        scheme = Qt.ColorScheme.Light
        def colorScheme(self): return self.scheme
    hints = SystemHints()
    settings_path = directory / 'appearance-test.ini'
    settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    settings.setValue(ThemeController.KEY, 'light')
    controller = ThemeController(app, settings=settings, style_hints=hints)
    if os.name == "nt" and app.platformName() != "windows":
        raise AssertionError(f"Expected native Windows Qt platform, got {app.platformName()}")
    source_a, source_b = directory / "gui-a.pdf", directory / "gui-b.pdf"
    fixture(source_a, annotation=True)
    fixture(source_b, ("B1", "B2"))
    window = MainWindow()
    errors = []
    window.show_error = lambda title, detail: errors.append(f"{title}: {detail}")

    def wait_for(predicate, seconds=45):
        until = time.monotonic() + seconds
        while not predicate():
            app.processEvents()
            time.sleep(0.01)
            if errors: raise AssertionError("; ".join(errors))
            if time.monotonic() > until: raise AssertionError("Native Qt operation timed out")
        app.processEvents()

    window.show()
    app.processEvents()
    try:
        assert not window.export_button.isEnabled()
        window.import_files([str(source_a)])
        wait_for(lambda: window.job is None)
        wait_for(lambda: not window.preview.image.isNull())
        image = window.preview.image
        assert any((c := image.pixelColor(x, y)).green() > 180 and c.red() < 80 and c.blue() < 80 for y in range(0, image.height(), 4) for x in range(0, image.width(), 4)), "Annotation was not rendered"
        window.import_files([str(source_b)])
        wait_for(lambda: window.job is None)
        assert len(window.workspace.pages) == 5
        added_source = window.workspace.pages[3].source_id
        assert window.page_list.currentItem().data(Qt.ItemDataRole.UserRole) == window.workspace.pages[3].uid, 'Import left active page on the old document'
        wait_for(lambda: not window.preview.image.isNull() and window.preview.image != image)
        wait_for(lambda: window.render_broker.pending_count == 0)
        assert added_source != window.workspace.pages[0].source_id
        window.page_list.clearSelection()
        window.page_list.item(0).setSelected(True)
        window.page_list.setCurrentItem(window.page_list.item(0), QItemSelectionModel.SelectionFlag.NoUpdate)
        window.right_button.click()
        assert window.workspace.pages[0].rotation == 90
        wait_for(lambda: window.preview.image.width() > window.preview.image.height())
        window.down_button.click()
        assert window.workspace.pages[1].page_index == 0
        window.delete_button.click()
        assert len(window.workspace.pages) == 4
        window.undo_button.click()
        assert len(window.workspace.pages) == 5
        window.page_list.clearSelection()
        window.page_list.item(0).setSelected(True)
        selected = directory / "gui-selected.pdf"
        with patch("pdf_workbench.app.QFileDialog.getSaveFileName", return_value=(str(selected), "PDF")):
            window.split_button.click()
            wait_for(lambda: window.job is None)
        assert len(PdfReader(selected).pages) == 1
        assert PdfReader(selected).pages[0].extract_text().strip() == "A2"
        merged = directory / "gui-merged.pdf"
        with patch("pdf_workbench.app.QFileDialog.getSaveFileName", return_value=(str(merged), "PDF")):
            window.export_button.click()
            wait_for(lambda: window.job is None)
        assert len(PdfReader(merged).pages) == 5

        with patch('pdf_workbench.app.PageRangeDialog.get_selection', return_value=[0, 4]):
            window.range_button.click()
        assert len(window.selected_ids()) == 2
        window.duplicate_button.click()
        assert len(window.workspace.pages) == 7
        assert len(window.selected_ids()) == 2
        duplicates = list(window.selected_ids())
        window.right_button.click()
        assert all(p.rotation == 90 for p in window.workspace.pages if p.uid in duplicates)
        individual = directory / 'gui-individual'
        with patch('pdf_workbench.app.SeparateExportDialog.get_destination', return_value=individual):
            window.separate_button.click()
            wait_for(lambda: window.job is None)
        individual_files = sorted(individual.glob('*.pdf'))
        assert len(individual_files) == 2
        assert [PdfReader(p).pages[0].extract_text().strip() for p in individual_files] == ['A2', 'B2']
        assert all(PdfReader(p).pages[0].rotation == 90 for p in individual_files)
        wait_for(lambda: not window.preview.image.isNull() and window.render_broker.pending_count == 0)
        window.preview.fit_page()
        app.processEvents()
        fitted = window.preview.zoom_factor
        window.zoom_in_button.click()
        app.processEvents()
        assert window.preview.zoom_factor > fitted
        window.fit_width_button.click()
        app.processEvents()
        assert window.preview.zoom_mode == 'width'
        window.fit_page_button.click()
        window.list_button.click()
        window.grid_button.click()
        for width, height in [(800, 560), (1160, 780), (1440, 900)]:
            window.resize(width, height)
            app.processEvents()
            assert window.export_button.isVisible() and window.page_list.isVisible()
        window.resize(1160, 780)
        app.processEvents()

        # Deliver native input to the canvas: tilt wheel, touchpad pixels,
        # Shift+wheel and middle-button panning while zoomed.
        preview = window.preview
        preview.set_zoom(3)
        app.processEvents()
        horizontal, vertical = preview.scroll.horizontalScrollBar(), preview.scroll.verticalScrollBar()
        def center():
            horizontal.setValue(horizontal.maximum() // 2)
            vertical.setValue(vertical.maximum() // 2)
            assert horizontal.maximum() > 0 and vertical.maximum() > 0
            return horizontal.value(), vertical.value()
        def wheel(angle=(0, 0), pixel=(0, 0), modifiers=Qt.KeyboardModifier.NoModifier):
            local = QPointF(80, 80)
            event = QWheelEvent(local, QPointF(preview.canvas.mapToGlobal(local.toPoint())),
                                QPoint(*pixel), QPoint(*angle), Qt.MouseButton.NoButton,
                                modifiers, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(preview.canvas, event)
            app.processEvents()
        x, y = center(); wheel(angle=(120, 0))
        assert horizontal.value() < x and vertical.value() == y
        x, y = center(); wheel(pixel=(-13, -19))
        assert (horizontal.value(), vertical.value()) == (x + 13, y + 19)
        x, y = center(); wheel(angle=(0, -120), modifiers=Qt.KeyboardModifier.ShiftModifier)
        assert horizontal.value() > x and vertical.value() == y
        x, y = center()
        window.comment_button.click()
        local = QPointF(100, 100)
        global_pos = QPointF(preview.canvas.mapToGlobal(local.toPoint()))
        before = tuple(window.workspace.pages)
        for kind, point, screen, button, buttons in (
            (QEvent.Type.MouseButtonPress, local, global_pos, Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton),
            (QEvent.Type.MouseMove, local + QPointF(60, 80), global_pos + QPointF(30, 40), Qt.MouseButton.NoButton, Qt.MouseButton.MiddleButton),
            (QEvent.Type.MouseButtonRelease, local, global_pos + QPointF(30, 40), Qt.MouseButton.MiddleButton, Qt.MouseButton.NoButton),
        ):
            QApplication.sendEvent(preview.canvas, QMouseEvent(kind, point, screen, button, buttons, Qt.KeyboardModifier.NoModifier))
            app.processEvents()
        assert (horizontal.value(), vertical.value()) == (x - 30, y - 40)
        assert tuple(window.workspace.pages) == before

        # A fresh PDF avoids relying on the state left by the page-operation checks.
        annotation_input = directory / 'gui-annotation-input.pdf'
        fixture(annotation_input, ('A1',))
        window.workspace = Workspace()
        annotation_ids = window.workspace.add_sources([load_source(annotation_input)])
        window.refresh(annotation_ids, annotation_ids[0])
        wait_for(lambda: not preview.image.isNull())
        preview.fit_page(); app.processEvents()
        def click_basis(point):
            local = preview.basis_to_canvas(point)
            screen = QPointF(preview.canvas.mapToGlobal(local.toPoint()))
            for kind, buttons in ((QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
                                  (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)):
                QApplication.sendEvent(preview.canvas, QMouseEvent(kind, local, screen, Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier))
                app.processEvents()
        window.comment_button.click()
        with patch('pdf_workbench.app.CommentDialog.get_text', return_value='Windows: Größe und Grüße'):
            click_basis((.65, .35))
        assert [a.kind for a in window.workspace.pages[0].annotations] == ['comment']
        window.highlight_button.click()
        preview.place_annotation(preview.basis_to_canvas((33 / 300, 52 / 500)), preview.basis_to_canvas((65 / 300, 52 / 500)))
        wait_for(lambda: not preview.selection_pending)
        assert [a.kind for a in window.workspace.pages[0].annotations] == ['comment', 'highlight']
        assert window.workspace.pages[0].annotations[1].text == 'A1'
        with patch('pdf_workbench.app.SignatureDialog.get_strokes', return_value=(((.05, .8), (.3, .1), (.6, .85), (.95, .2)),)):
            window.signature_button.click()
        click_basis((.25, .7))
        assert [a.kind for a in window.workspace.pages[0].annotations] == ['comment', 'highlight', 'signature']
        annotated = directory / 'gui-annotated.pdf'
        with patch('pdf_workbench.app.QFileDialog.getSaveFileName', return_value=(str(annotated), 'PDF')):
            window.export_button.click(); wait_for(lambda: window.job is None)
        assert len(PdfReader(annotated).pages[0]['/Annots']) == 3
        window.workspace = Workspace()
        window.refresh()
        window.import_files([str(annotated)])
        wait_for(lambda: window.job is None and not preview.image.isNull())
        assert len(window.workspace.pages[0].annotations) == 3
        base = PdfReader(io.BytesIO(next(iter(window.workspace.sources.values())).data))
        assert not base.pages[0].get('/Annots'), 'Own annotations remained in the immutable base'
        def edit_and_delete(dialog):
            with patch('pdf_workbench.app.CommentDialog.get_text', return_value='Windows: bearbeitet nach Wiederöffnen'):
                dialog.list.setCurrentRow(0); dialog.edit()
            dialog.list.setCurrentRow(1); dialog.remove()
            return 0
        with patch('pdf_workbench.app.AnnotationListDialog.exec', edit_and_delete):
            window.annotations_button.click()
        assert [a.kind for a in window.workspace.pages[0].annotations] == ['comment', 'signature']
        edited_output = directory / 'gui-annotated-edited.pdf'
        with patch('pdf_workbench.app.QFileDialog.getSaveFileName', return_value=(str(edited_output), 'PDF')):
            window.export_button.click(); wait_for(lambda: window.job is None)
        edited = [a.get_object() for a in PdfReader(edited_output).pages[0]['/Annots']]
        assert [a['/Subtype'] for a in edited] == ['/Text', '/Ink']
        assert edited[0]['/Contents'] == 'Windows: bearbeitet nach Wiederöffnen'
        preview.fit_page(); window.pan_button.click(); app.processEvents()
        # Exercise the actual theme actions against isolated persisted settings.
        preview.set_zoom(2)
        wait_for(lambda: not preview._timer.isActive() and window.render_broker.pending_count == 0)
        horizontal, vertical = preview.scroll.horizontalScrollBar(), preview.scroll.verticalScrollBar()
        horizontal.setValue(horizontal.maximum() // 2)
        vertical.setValue(vertical.maximum() // 2)
        state = (tuple(window.workspace.pages), window.saved_state, window.workspace.can_undo,
                 window.workspace.can_redo, preview.zoom_factor, horizontal.value(), vertical.value())
        paper_image = preview.image.copy()
        light_icon = window.pan_button.icon().pixmap(QSize(24, 24)).toImage()
        window.theme_actions['dark'].trigger(); app.processEvents()
        assert controller.preference == 'dark' and controller.effective_name == 'dark'
        assert window.pan_button.icon().pixmap(QSize(24, 24)).toImage() != light_icon
        assert (tuple(window.workspace.pages), window.saved_state, window.workspace.can_undo,
                window.workspace.can_redo, preview.zoom_factor, horizontal.value(), vertical.value()) == state
        assert preview.image == paper_image
        restored = QSettings(str(settings_path), QSettings.Format.IniFormat)
        assert restored.value(ThemeController.KEY) == 'dark'
        dialog = CommentDialog(window)
        dialog.show(); app.processEvents()
        assert dialog.editor.palette().color(QPalette.ColorRole.Base).name() == COLORS['dark']['surface']
        window.theme_actions['light'].trigger(); app.processEvents()
        assert controller.effective_name == 'light'
        assert dialog.editor.palette().color(QPalette.ColorRole.Base).name() == COLORS['light']['surface']
        dialog.close(); dialog.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete); app.processEvents()
        hints.scheme = Qt.ColorScheme.Dark
        window.theme_actions['system'].trigger(); app.processEvents()
        assert controller.preference == 'system' and controller.effective_name == 'dark'
        hints.scheme = Qt.ColorScheme.Light; hints.colorSchemeChanged.emit(hints.scheme); app.processEvents()
        assert controller.effective_name == 'light'
        window.theme_actions['dark'].trigger()
        hints.colorSchemeChanged.emit(Qt.ColorScheme.Light); app.processEvents()
        assert controller.effective_name == 'dark' and controller.preference == 'dark'
        themed_output = directory / 'gui-dark-export.pdf'
        export_pdf(window.workspace.sources, window.workspace.pages, themed_output)
        def render_pdf(path):
            document = QPdfDocument(None)
            document.load(str(path))
            assert document.status() == QPdfDocument.Status.Ready
            from PyQt6.QtPdf import QPdfDocumentRenderOptions
            options = QPdfDocumentRenderOptions()
            options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
            image = document.render(0, QSize(300, 500), options)
            document.close()
            return image
        assert render_pdf(edited_output) == render_pdf(themed_output), 'Theme changed exported document colors'
        feature_checks, ocr_info = feature_acceptance(window, directory, app, wait_for)
        from upgrade_smoke import upgrade_acceptance
        feature_checks += upgrade_acceptance(window, directory, app, wait_for, fixture)
        from v07_smoke import v07_acceptance
        feature_checks += v07_acceptance(window, directory, app, wait_for, fixture)
        preview.fit_page(); app.processEvents()
        wait_for(lambda: not preview.image.isNull() and not preview._timer.isActive() and window.render_broker.pending_count == 0)
        screenshot = directory / "native-window.png"
        assert window.grab().save(str(screenshot))
        return {"result": "PASS", "app_version": __version__, "qt_platform": app.platformName(), "qt_runtime": qVersion(), "qt_binding_compile_version": QT_VERSION_STR, "pyqt": PYQT_VERSION_STR, "screenshot": str(screenshot), "checks": ["multiple import and active page", "rendered annotation", "async preview", "rotate", "move", "delete/undo", "selected export", "merged export", "range selection", "duplicate", "individual-page export", "zoom and fit", "grid/list", "resize", "horizontal tilt wheel", "touchpad diagonal pixels", "Shift+wheel", "middle-button pan in comment mode", "comment Unicode", "real PDF text highlight", "drawn signature", "export/reopen/edit/delete annotations without duplicates", "appearance dark/light/system", "appearance persisted", "appearance preserves document and viewport", "appearance document/export colors", "appearance dialogs and icons"] + feature_checks, "ocr": ocr_info}
    finally:
        if window.job:
            window.job.cancel_event.set()
            window.job.wait(5000)
        window.saved_state = tuple(window.workspace.pages)
        window.close()
        closing_deadline = time.monotonic() + 30
        while not window.render_broker.is_closed and time.monotonic() < closing_deadline:
            app.processEvents()
            time.sleep(0.01)
        assert window.render_broker.is_closed, 'PDF renderer did not shut down cleanly'
        app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="run-", dir=args.output)).resolve()
    report = {"result": "RUNNING", "app_version": __version__, "frozen": bool(getattr(sys, "frozen", False)), "os": platform.platform(), "python": sys.version, "session_name": os.environ.get("SESSIONNAME"), "run_directory": str(run), "source_sha256": {str(p.relative_to(APP_ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((APP_ROOT / 'src').rglob('*.py'))}}
    if report['frozen']:
        report['executable_sha256'] = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
        report['provenance'] = json.loads((Path(sys._MEIPASS) / 'APP_PROVENANCE.json').read_text(encoding='utf-8'))
        if report['provenance']['app_version'] != __version__:
            raise AssertionError('Frozen app and embedded build provenance have different versions')
    report_path = run / "result.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    with (run / "test.log").open("w", encoding="utf-8") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        try:
            from v07_smoke import acceptance_class
            acceptance = acceptance_class(PdfAcceptance, fixture)
            acceptance.base = run
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(acceptance))
            report["core"] = {"tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "skipped": [(str(test), reason) for test, reason in result.skipped],
                              "checks": unittest.defaultTestLoader.getTestCaseNames(acceptance)}
            if not result.wasSuccessful(): raise AssertionError("PDF acceptance tests failed; see test.log")
            report["gui"] = gui_acceptance(run)
            report["result"] = "PASS"
        except BaseException:
            report["result"] = "FAIL"
            report["error"] = traceback.format_exc()
            traceback.print_exc()
        finally:
            report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{report['result']}: {report_path}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
