"""Native dialog interactions, explicit candidate review and local OCR pipeline."""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest

if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import QApplication, QDialog
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdf_workbench.batch import BatchPreset, process_batch
from pdf_workbench.core import Workspace, load_source
from pdf_workbench.ocr import discover_engine
from pdf_workbench.page_marks import PageNumberOptions, WatermarkOptions
from pdf_workbench.ui.theme import stylesheet
from pdf_workbench.ui.workflow_dialogs import (BatchDialog, CandidateReviewDialog, ContentsDialog,
    DuplexDialog, InternalLinksDialog, ScanDialog, ScopeDialog)
from pdf_workbench.workflows import ScanOptions, _orientation, find_page_candidates

APP = QApplication.instance() or QApplication([])


class WorkflowDialogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.directory = Path(self.temporary.name)
        self.path = self.directory / "original.pdf"; document = canvas.Canvas(str(self.path), pagesize=(300, 420))
        for text in ("First", "", "First", "Last"):
            if text: document.drawString(25, 360, text)
            document.showPage()
        document.save(); self.original = self.path.read_bytes()
        self.workspace = Workspace(); self.workspace.add_sources((load_source(self.path),)); self.dialogs = []
        self.settings = QSettings(str(self.directory / "settings.ini"), QSettings.Format.IniFormat)

    def tearDown(self):
        for dialog in self.dialogs: dialog.reject(); dialog.deleteLater()
        APP.processEvents(); self.assertEqual(self.path.read_bytes(), self.original); self.temporary.cleanup()

    def show(self, dialog):
        self.dialogs.append(dialog); dialog.show(); APP.processEvents(); return dialog

    def test_candidates_start_unchecked_and_require_review(self):
        candidates = find_page_candidates(self.workspace.sources, self.workspace.pages)
        dialog = self.show(CandidateReviewDialog(None, candidates))
        self.assertFalse(dialog.ok.isEnabled()); self.assertEqual(dialog.selected_ids, ())
        dialog.list.item(1).setCheckState(Qt.CheckState.Checked)
        self.assertTrue(dialog.ok.isEnabled()); self.assertEqual(dialog.selected_ids, (self.workspace.pages[2].uid,))
        dialog.ok.click(); self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_duplex_preview_validation_and_rotation(self):
        dialog = self.show(DuplexDialog(None, self.workspace.pages))
        self.assertIn("1 → 3 → 2 → 4", dialog.preview.text()); dialog.reverse.setChecked(True)
        self.assertIn("1 → 4 → 2 → 3", dialog.preview.text()); dialog.rotation.setCurrentIndex(2)
        self.assertEqual([page.rotation for page in dialog.ordered_pages], [0, 180, 0, 180])
        dialog.backs.setText("2-4"); self.assertFalse(dialog.ok.isEnabled()); dialog.accept(); self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

    def test_internal_link_add_edit_delete_and_graphical_region(self):
        dialog = self.show(InternalLinksDialog(None, self.workspace))
        self.assertFalse(dialog.ok.isEnabled()); self.assertFalse(dialog.preview.image.isNull())
        dialog._rectangle_changed((.05, .15, .85, .35)); dialog._coordinates_changed(); dialog.apply.click()
        self.assertEqual(dialog.links[0].rect, (.05, .15, .85, .35)); self.assertTrue(dialog.ok.isEnabled())
        dialog.list.setCurrentRow(0); dialog.target.setCurrentIndex(3); dialog.apply.click()
        self.assertEqual(len(dialog.links), 1); self.assertEqual(dialog.links[0].target_uid, self.workspace.pages[3].uid)
        dialog.new.click(); dialog.apply.click(); self.assertEqual(len(dialog.links), 2)
        dialog.list.setCurrentRow(0); dialog.remove.click(); self.assertEqual(len(dialog.links), 1)
        dialog.coords[0].setValue(95); dialog.coords[2].setValue(85); dialog.apply.click()
        self.assertIn("Linkbereich", dialog.error.text()); self.assertEqual(len(dialog.links), 1)

    def test_batch_presets_reusable_and_advanced_import_options_retained(self):
        dialog = self.show(BatchDialog(None, settings=self.settings))
        original = BatchPreset("Prüfung", "balanced", 90, WatermarkOptions("DRAFT", 22, opacity=.55),
                               PageNumberOptions("{page}/{pages}", 5, position="top-right"), ScanOptions(150, False, True, False, 180), ("eng",))
        dialog._set_preset(original); self.assertEqual(dialog.preset, original)
        dialog.save.click(); self.assertEqual(dialog.error.text(), "")
        second = self.show(BatchDialog(None, settings=self.settings)); second.saved.setCurrentIndex(second.saved.findData("Prüfung"))
        self.assertEqual(second.preset, original); second.watermark_text.setText("NEU")
        self.assertEqual(second.preset.watermark.opacity, .55); self.assertEqual(second.preset.watermark.text, "NEU")

    def test_batch_empty_folder_is_validation_error(self):
        dialog = self.show(BatchDialog(None, settings=self.settings)); dialog.files.addItem(str(self.path))
        dialog.ok.click(); self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected); self.assertIn("Zielordner", dialog.error.text())
        dialog.directory.setText(str(self.directory)); dialog.ok.click(); self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_scope_scan_options_and_compact_light_dark_layouts(self):
        for theme in ("light", "dark"):
            for constructor in (lambda: ScanDialog(None, 4, 1), lambda: ScopeDialog(None, "Bilder exportieren", 4, 1, image=True),
                                lambda: InternalLinksDialog(None, self.workspace), lambda: BatchDialog(None, settings=self.settings), lambda: ContentsDialog(None)):
                dialog = self.show(constructor()); dialog.setStyleSheet(stylesheet(theme)); APP.processEvents()
                if isinstance(dialog, InternalLinksDialog):
                    dialog.resize(800, 560); APP.processEvents()
                    self.assertTrue(all(field.height() >= 40 for field in dialog.coords))
                    self.assertGreater(dialog.editor_scroll.verticalScrollBar().maximum(), 0)
                self.assertTrue(dialog.rect().contains(dialog.ok.mapTo(dialog, dialog.ok.rect().center())))
                self.assertTrue(dialog.ok.text()); self.assertFalse(dialog.grab().isNull())
        scan = self.show(ScanDialog(None, 4, 1)); scan.rotation.setCurrentIndex(3); scan.cleanup.setChecked(True)
        self.assertEqual(scan.options.rotation, 270); self.assertTrue(scan.options.clean_background); self.assertEqual(scan.scope.currentData(), "selected")

    def test_batch_real_ocr_before_page_numbers(self):
        engine = discover_engine()
        if engine is None or "eng" not in engine.languages or not engine.pdf_font:
            self.skipTest("Local Tesseract English/pdf.ttf unavailable")
        font = ImageFont.truetype(str(Path(__file__).resolve().parents[1] / "src/pdf_workbench/fonts/LiberationSans-Regular.ttf"), 48)
        image = Image.new("RGB", (1200, 1600), "white"); draw = ImageDraw.Draw(image)
        for index, line in enumerate(("LOCAL DOCUMENT", "Searchable sample text", "Invoice number 12345", "Original scan remains safe")):
            draw.text((80, 140 + index * 110), line, fill="black", font=font)
        scan = self.directory / "scan.pdf"; document = canvas.Canvas(str(scan), pagesize=(432, 576)); document.drawImage(ImageReader(image), 0, 0, 432, 576); document.save()
        preset = BatchPreset("OCR", "lossless", numbering=PageNumberOptions(), ocr_languages=("eng",))
        result = process_batch((scan,), self.directory, preset, engine=engine)
        text = PdfReader(result.outputs[0]).pages[0].extract_text()
        self.assertIn("12345", text); self.assertIn("Seite 1 von 1", text); self.assertIn("LOCAL", text)

    def test_local_tesseract_orientation_detects_upright_and_clockwise(self):
        engine = discover_engine()
        if not engine or not engine.tessdata_dir or not (engine.tessdata_dir / "osd.traineddata").is_file():
            self.skipTest("Local Tesseract OSD unavailable")
        font = ImageFont.truetype(str(Path(__file__).resolve().parents[1] / "src/pdf_workbench/fonts/LiberationSans-Regular.ttf"), 26)
        image = Image.new("RGB", (1000, 1400), "white"); draw = ImageDraw.Draw(image)
        for line in range(36): draw.text((35, 35 + line * 36), "The quick brown fox jumps over a lazy dog 12345", fill="black", font=font)
        self.assertEqual(_orientation(image, None), (0, ""))
        self.assertEqual(_orientation(image.rotate(-90, expand=True), None), (270, ""))


if __name__ == "__main__": unittest.main()
