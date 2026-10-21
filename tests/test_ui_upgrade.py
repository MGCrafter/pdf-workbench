"""User flows for password import/export, measured compression and form editing."""
from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings, QTimer, QRect, QPoint
from PyQt6.QtWidgets import QApplication, QDialog
from pypdf import PdfReader, PdfWriter

from pdf_workbench.app import MainWindow
from pdf_workbench.core import OperationCancelled, export_pdf, load_source
from pdf_workbench.forms import form_fields
from pdf_workbench.ui.motion import MotionController
from pdf_workbench.ui.pdf_dialogs import CompressionDialog, FormDialog, PasswordDialog
from pdf_workbench.ui.welcome import DocumentArtwork
from test_form_support import make_form

APP = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=15):
    until=time.monotonic()+timeout
    while not predicate():
        APP.processEvents();time.sleep(.005)
        if time.monotonic()>until: raise AssertionError("UI upgrade flow timed out")
    APP.processEvents()


class UpgradeUiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.original=self.root/"original.pdf";make_form(self.original)
        self.bytes=self.original.read_bytes()
        self.window=MainWindow();self.errors=[]
        self.window.show_error=lambda *args:self.errors.append(args)
        self.old_theme=self.window.theme.preference
        self.window.show();APP.processEvents()

    def tearDown(self):
        if self.window.job:
            self.window.cancel_job();wait_for(lambda:self.window.job is None)
        self.window.theme.set_preference(self.old_theme)
        self.window.saved_state=tuple(self.window.workspace.pages);self.window.close()
        wait_for(lambda:self.window.render_broker.is_closed and self.window.search_bar.is_closed)
        self.assertEqual(self.original.read_bytes(),self.bytes)
        self.temp.cleanup()

    def import_original(self):
        self.window.import_files([str(self.original)])
        wait_for(lambda:self.window.job is None)
        self.assertEqual(len(self.window.workspace.pages),2)

    def protected_input(self,password="open this"):
        path=self.root/"protected.pdf"
        writer=PdfWriter(clone_from=self.original);writer.encrypt(password,algorithm="AES-256");writer.write(path)
        return path

    def test_wrong_import_password_retries_without_losing_other_imports_or_exposing_secret(self):
        protected=self.protected_input();original=protected.read_bytes()
        with patch("pdf_workbench.app.PasswordDialog.get_password",side_effect=("wrong secret","open this")) as prompt:
            self.window.import_files([str(self.original),str(protected)])
            wait_for(lambda:len(self.window.workspace.pages)==4 and self.window.job is None)
        self.assertEqual(prompt.call_count,2)
        self.assertFalse(prompt.call_args_list[0].kwargs["incorrect"])
        self.assertTrue(prompt.call_args_list[1].kwargs["incorrect"])
        source=next(source for source in self.window.workspace.sources.values() if source.was_encrypted)
        self.assertFalse(PdfReader(io.BytesIO(source.data)).is_encrypted)
        self.assertIn("normaler Export ohne Passwort",self.window.page_details.text())
        self.assertNotIn("wrong secret",self.window.status.text())
        self.assertEqual(protected.read_bytes(),original);self.assertEqual(self.errors,[])

    def test_cancel_password_keeps_successful_documents_and_does_not_create_files(self):
        protected=self.protected_input()
        with patch("pdf_workbench.app.PasswordDialog.get_password",return_value=None) as prompt:
            self.window.import_files([str(self.original),str(protected)])
            wait_for(lambda:self.window.job is None and prompt.call_count==1)
        self.assertEqual(len(self.window.workspace.pages),2)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()),["original.pdf","protected.pdf"])
        self.assertEqual(self.errors,[])

    def test_protected_save_action_creates_real_aes_document(self):
        self.import_original();output=self.root/"new-protected.pdf"
        with patch("pdf_workbench.app.PasswordDialog.get_password",return_value="new secure passphrase"),\
             patch("pdf_workbench.app.QFileDialog.getSaveFileName",return_value=(str(output),"")):
            self.window.protect_action.trigger();wait_for(lambda:self.window.job is None)
        reader=PdfReader(output);self.assertTrue(reader.is_encrypted)
        self.assertTrue(reader.decrypt("new secure passphrase"))
        self.assertEqual(len(reader.pages),2);self.assertIn("name",reader.get_fields())
        self.assertEqual(self.errors,[])

    def test_measured_compression_dialog_and_export_agree(self):
        self.import_original()
        dialog=CompressionDialog(self.window,dict(self.window.workspace.sources),tuple(self.window.workspace.pages))
        dialog.show();wait_for(lambda:dialog.report is not None and dialog.worker is None)
        report=dialog.report;output=self.root/"compressed.pdf"
        actual=[]
        export_pdf(self.window.workspace.sources,self.window.workspace.pages,output,optimization=dialog.options,compression_report=actual.append)
        self.assertEqual(output.stat().st_size,actual[0].output_bytes)
        self.assertLess(abs(actual[0].output_bytes-report.output_bytes),128)  # Filename becomes PDF title after preview.
        self.assertGreaterEqual(report.baseline_bytes,report.output_bytes)
        self.assertEqual(len(PdfReader(output).pages),2)
        dialog.reject();self.assertEqual(self.errors,[])

    def test_closing_compression_during_work_waits_for_cancelled_worker(self):
        self.import_original()
        def slow_comparison(sources,pages,options,cancel):
            until=time.monotonic()+5
            while not cancel() and time.monotonic()<until: time.sleep(.005)
            raise OperationCancelled()
        with patch("pdf_workbench.ui.pdf_dialogs.preview_compression",side_effect=slow_comparison):
            dialog=CompressionDialog(self.window,dict(self.window.workspace.sources),tuple(self.window.workspace.pages))
            dialog.show();wait_for(lambda:dialog.worker is not None)
            dialog.close();wait_for(lambda:dialog.worker is None and not dialog.isVisible())
        self.assertFalse(list(self.root.glob(".pdf-workbench-*")))

    def test_form_dialog_edits_field_values_then_export_and_undo_keep_original(self):
        self.import_original();before=tuple(self.window.workspace.pages)
        source=self.window.workspace.sources[before[0].source_id]
        dialog=FormDialog(source,self.window)
        dialog.editors["name"].setText("Jürgen in Wien");dialog.editors["agree"].setChecked(True)
        choice=dialog.editors["country"];choice.setCurrentIndex(choice.findData("DE"))
        self.assertGreaterEqual(choice.findData(""),0)  # Ordinary choices can also be cleared.
        self.assertTrue(self.window.form_action.isEnabled())
        with patch("pdf_workbench.app.FormDialog.get_values",return_value=dialog.values()):
            self.window.form_action.trigger();wait_for(lambda:self.window.job is None)
        after=tuple(self.window.workspace.pages);self.assertEqual([p.uid for p in before],[p.uid for p in after])
        source=self.window.workspace.sources[after[0].source_id]
        values={field.name:field.value for field in form_fields(source)}
        self.assertEqual((values["name"],values["agree"],values["country"]),("Jürgen in Wien",True,"DE"))
        output=self.root/"form-export.pdf";export_pdf(self.window.workspace.sources,after,output)
        self.assertEqual({f.name:f.value for f in form_fields(load_source(output))}["name"],"Jürgen in Wien")
        self.window.page_list.setFocus();self.window.undo();self.assertEqual(tuple(self.window.workspace.pages),before)
        dialog.reject();self.assertEqual(self.errors,[])

    def test_compact_light_and_dark_keep_real_export_buttons_inside_window(self):
        self.import_original()
        for theme in ("light","dark"):
            self.window.theme.set_preference(theme)
            self.window.resize(800,560);APP.processEvents()
            self.assertEqual((self.window.width(),self.window.height()),(800,560))
            for button in (self.window.export_button,self.window.split_button,self.window.separate_button,self.window.open_button,self.window.document_tools_button):
                self.assertTrue(button.isVisible())
                self.assertTrue(self.window.rect().contains(QRect(button.mapTo(self.window,QPoint()),button.size())))

    def test_motion_preference_persists_and_reduced_motion_finishes_immediately(self):
        settings=QSettings(str(self.root/"appearance.ini"),QSettings.Format.IniFormat)
        with patch("pdf_workbench.ui.motion.system_reduces_motion",return_value=False):
            motion=MotionController(self.window,settings);artwork=DocumentArtwork(self.window)
            motion.set_reduced(True);motion.animate(artwork,"reveal",0.,1.,460)
            self.assertEqual(artwork.reveal,1.);self.assertFalse(motion.enabled)
            restored=MotionController(self.window,QSettings(str(self.root/"appearance.ini"),QSettings.Format.IniFormat))
            self.assertTrue(restored.reduced)
            restored.set_reduced(False);restored.animate(artwork,"reveal",0.,1.,460)
            self.assertLess(artwork.reveal,1.)
            restored.set_reduced(True);self.assertEqual(artwork.reveal,1.)

    def test_password_dialog_validation_keeps_secrets_masked_and_accepts_empty_input_password(self):
        protect=PasswordDialog(self.window)
        protect.password.setText("first");protect.confirm.setText("second");protect.accept()
        self.assertNotEqual(protect.result(),QDialog.DialogCode.Accepted)
        self.assertIn("nicht überein",protect.error.text())
        protect.confirm.setText("first");protect.accept();self.assertEqual(protect.result(),QDialog.DialogCode.Accepted)
        unlock=PasswordDialog(self.window,source_name="empty-user-password.pdf");unlock.accept()
        self.assertEqual(unlock.result(),QDialog.DialogCode.Accepted)


if __name__=="__main__": unittest.main(verbosity=2)
