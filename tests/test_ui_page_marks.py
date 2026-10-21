"""Real asynchronous PDF previews and validation for both page marking tools."""
import os
from pathlib import Path
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pypdf import PdfReader, PdfWriter
from PyQt6.QtWidgets import QApplication, QDialog

from pdf_workbench.core import Workspace, export_pdf, load_source
from pdf_workbench.page_marks import apply_page_marks
from pdf_workbench.form_design import FieldPlacement, FormDesign, apply_form_designs
from pdf_workbench.ui.page_marks_dialog import PageMarksDialog
from pdf_workbench.ui.theme import stylesheet

APP = QApplication.instance() or QApplication([])


def wait_for(predicate,timeout=15):
    until = time.monotonic()+timeout
    while not predicate():
        APP.processEvents(); time.sleep(.005)
        if time.monotonic()>until: raise AssertionError("Page mark preview did not finish")
    APP.processEvents()


class PageMarksDialogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.path = Path(self.temp.name)/"original.pdf"
        writer = PdfWriter(); writer.add_blank_page(width=400,height=500); writer.add_blank_page(width=400,height=500); writer.write(self.path)
        self.original = self.path.read_bytes(); self.workspace = Workspace(); self.workspace.add_sources([load_source(self.path)])
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.reject(); wait_for(lambda:dialog.worker is None)
            dialog.deleteLater(); APP.processEvents()
        self.assertEqual(self.path.read_bytes(),self.original); self.temp.cleanup()

    def dialog(self,mode="watermark",selected=()):
        dialog = PageMarksDialog(None,self.workspace,selected,mode=mode); self.dialogs.append(dialog); dialog.show(); APP.processEvents(); return dialog

    def ready(self,dialog):
        wait_for(lambda:dialog.worker is None and not dialog.timer.isActive() and (dialog._ready_request is not None or bool(dialog.error.text())))
        self.assertEqual(dialog.error.text(),""); self.assertTrue(dialog.apply_button.isEnabled()); self.assertFalse(dialog.preview.image.isNull())

    def test_watermark_preview_applies_only_selected_copy_and_export_contains_visible_text(self):
        duplicate = self.workspace.duplicate([self.workspace.pages[0].uid])[0]
        dialog = self.dialog(selected=(duplicate,)); dialog.text.setText("Prüfung €"); self.ready(dialog)
        self.assertEqual(dialog.request.target_ids,(duplicate,)); self.assertEqual(dialog.preview_page.count(),1)
        dialog.apply_button.click(); self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)
        apply_page_marks(self.workspace,dialog._ready_request)
        output = self.path.with_name("marked.pdf"); export_pdf(self.workspace.sources,self.workspace.pages,output)
        pages = PdfReader(output).pages; self.assertNotIn("Prüfung",pages[0].extract_text()); self.assertIn("Prüfung €",pages[1].extract_text())

    def test_numbering_page_range_start_and_live_template_validation(self):
        dialog = self.dialog("numbering"); dialog.scope.setCurrentIndex(dialog.scope.findData("range")); dialog.range_edit.setText("2")
        dialog.start.setValue(5); self.ready(dialog)
        self.assertEqual(dialog.request.target_ids,(self.workspace.pages[1].uid,))
        self.assertEqual(dialog.preview_page.currentData(),self.workspace.pages[1].uid)
        dialog.text.setText("{page.__class__}"); wait_for(lambda:bool(dialog.error.text()) and dialog.worker is None)
        self.assertFalse(dialog.apply_button.isEnabled()); self.assertIn("{page}",dialog.error.text())
        dialog.text.setText("Blatt {page} / {pages}"); self.ready(dialog)
        apply_page_marks(self.workspace,dialog.request)
        output = self.path.with_name("numbered.pdf"); export_pdf(self.workspace.sources,self.workspace.pages,output)
        self.assertIn("Blatt 6 / 2",PdfReader(output).pages[1].extract_text())

    def test_changed_settings_immediately_disable_stale_preview_and_acceptance(self):
        dialog = self.dialog(); self.ready(dialog); dialog.font_size.setValue(288)
        self.assertFalse(dialog.apply_button.isEnabled()); dialog.accept(); self.assertEqual(dialog.result(),QDialog.DialogCode.Rejected)
        wait_for(lambda:bool(dialog.error.text()) and dialog.worker is None); self.assertIn("passt nicht",dialog.error.text())
        dialog.font_size.setValue(20); dialog.opacity.setValue(75); dialog.angle.setValue(0); self.ready(dialog)
        self.assertEqual(dialog.request.options.watermark.opacity,.75)

    def test_invalid_color_and_empty_selection_are_explicit(self):
        dialog = self.dialog(); dialog.color_edit.setText("#GG0011")
        wait_for(lambda:bool(dialog.error.text())); self.assertIn("#RRGGBB",dialog.error.text()); self.assertFalse(dialog.apply_button.isEnabled())
        dialog.color_edit.setText("#123456"); dialog.scope.setCurrentIndex(dialog.scope.findData("selected"))
        wait_for(lambda:bool(dialog.error.text())); self.assertFalse(dialog.apply_button.isEnabled()); self.assertEqual(dialog.preview_page.count(),0)

    def test_compact_light_dark_layout_keeps_preview_and_apply_visible(self):
        for theme in ("light","dark"):
            for mode in ("watermark","numbering"):
                with self.subTest(theme=theme,mode=mode):
                    dialog = self.dialog(mode); dialog.setStyleSheet(stylesheet(theme)); dialog.resize(760,480); self.ready(dialog)
                    self.assertEqual((dialog.width(),dialog.height()),(760,480))
                    self.assertGreater(dialog.preview.width(),220); self.assertGreater(dialog.preview.height(),160)
                    self.assertTrue(dialog.rect().contains(dialog.apply_button.mapTo(dialog,dialog.apply_button.rect().center())))
                    self.assertTrue(dialog.text.accessibleName()); self.assertTrue(dialog.preview_page.accessibleName())
                    dialog.reject(); wait_for(lambda:dialog.worker is None)

    def test_close_during_preview_finishes_worker_before_reject(self):
        dialog = self.dialog(); wait_for(lambda:dialog.worker is not None)
        dialog.reject(); wait_for(lambda:dialog.worker is None)
        self.assertEqual(dialog.result(),QDialog.DialogCode.Rejected); self.assertFalse(dialog.isVisible())

    def test_preview_includes_live_form_appearance_while_export_keeps_widgets(self):
        original = next(iter(self.workspace.sources.values()))
        source = apply_form_designs(original,(FormDesign("name","name","Name","text",(FieldPlacement(0,(.1,.1,.8,.25)),),value="SICHTBAR"),))
        self.workspace.replace_source(original.source_id,source)
        dialog = self.dialog(); self.ready(dialog); image = dialog.preview.image
        black = sum((lambda c:c.alpha()>0 and max(c.red(),c.green(),c.blue())<60)(image.pixelColor(x,y))
                    for y in range(round(image.height()*.1),round(image.height()*.25))
                    for x in range(round(image.width()*.1),round(image.width()*.8)))
        self.assertGreater(black,60)
        apply_page_marks(self.workspace,dialog.request)
        output = self.path.with_name("live-form.pdf"); export_pdf(self.workspace.sources,self.workspace.pages,output)
        self.assertEqual(PdfReader(output).get_fields()["name"]["/V"],"SICHTBAR")


if __name__ == "__main__": unittest.main()
