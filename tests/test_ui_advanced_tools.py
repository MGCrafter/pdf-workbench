"""Native confirmation, privacy hints, preview cancellation and keyboard fields."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication,QDialog,QDialogButtonBox,QLabel,QLineEdit
from reportlab.pdfgen.canvas import Canvas
from pdf_workbench.core import Workspace,load_source
from pdf_workbench.ui.advanced_dialogs import (CertificateDialog,ExistingTextDialog,PdfaDialog,
    RedactionDialog,SignatureReportDialog,SignatureVerificationDialog)
from pdf_workbench.ui.theme import stylesheet

APP=QApplication.instance() or QApplication([])


def wait_for(predicate,timeout=12):
    until=time.monotonic()+timeout
    while not predicate():
        APP.processEvents();time.sleep(.005)
        if time.monotonic()>until:raise AssertionError('Advanced PDF preview did not finish')
    APP.processEvents()


class AdvancedDialogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.source=self.root/'original.pdf'
        c=Canvas(str(self.source),pagesize=(360,240));c.drawString(30,180,'Original text');c.showPage();c.drawString(40,170,'Second text');c.save()
        self.workspace=Workspace();self.workspace.add_sources([load_source(self.source)]);self.dialogs=[]

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.reject()
            if isinstance(dialog,RedactionDialog):wait_for(lambda:dialog.worker is None)
            dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents();self.temp.cleanup()

    def save(self,d):return d.buttons.button(QDialogButtonBox.StandardButton.Save)

    def test_redaction_requires_added_region_and_explicit_confirmation(self):
        d=RedactionDialog(None,self.workspace);self.dialogs.append(d);d.show()
        wait_for(lambda:d.worker is None);self.assertFalse(d.preview.image.isNull());self.assertFalse(self.save(d).isEnabled())
        d.confirm.setChecked(True);self.assertFalse(self.save(d).isEnabled())
        d.add_button.click();self.assertEqual(len(d.regions),1);self.assertFalse(d.confirm.isChecked());self.assertFalse(self.save(d).isEnabled())
        d.confirm.setChecked(True);self.assertTrue(self.save(d).isEnabled())
        d.coordinates[0].setValue(80);d.coordinates[2].setValue(50);self.assertFalse(d.add_button.isEnabled())
        self.assertTrue(self.save(d).isEnabled())
        d.remove_button.click();self.assertEqual(len(d.regions),0);self.assertFalse(self.save(d).isEnabled())
        hints=' '.join(l.text() for l in d.findChildren(QLabel));self.assertIn('Undo-Historie',hints);self.assertIn('unverschlüsselt',hints)

    def test_rapid_page_change_back_restarts_cancelled_same_page_worker(self):
        d=RedactionDialog(None,self.workspace);self.dialogs.append(d)
        # run() has not delivered its queued signal before the GUI switches.
        d.page_box.setCurrentIndex(1);d.page_box.setCurrentIndex(0);d.show()
        wait_for(lambda:d.worker is None)
        self.assertEqual(d._ready_uid,self.workspace.pages[0].uid);self.assertFalse(d.preview.image.isNull());self.assertTrue(d.add_button.isEnabled())

    def test_text_run_selection_and_real_replacement_request(self):
        source=next(iter(self.workspace.sources.values()));d=ExistingTextDialog(None,source,0);self.dialogs.append(d);d.show()
        self.assertIn('Original text',d.original.toPlainText());self.assertFalse(self.save(d).isEnabled())
        d.replacement.setText('New text');self.assertTrue(self.save(d).isEnabled());d.accept()
        self.assertEqual(d.result(),QDialog.DialogCode.Accepted);self.assertEqual(d.request,('0','New text'))

    def test_certificate_password_is_masked_and_cleared_on_cancel_and_accept(self):
        d=CertificateDialog(None);self.dialogs.append(d)
        self.assertEqual(d.password.echoMode(),QLineEdit.EchoMode.Password);d.password.setText('secret');d.reject();self.assertEqual(d.password.text(),'')
        d.certificate.setText(str(self.source));d.password.setText('only-in-ram');d.accept()
        self.assertEqual(d.request[1],'only-in-ram');self.assertEqual(d.password.text(),'')

    def test_verification_can_use_explicit_roots_and_reports_absence(self):
        d=SignatureVerificationDialog(None);self.dialogs.append(d)
        self.assertFalse(self.save(d).isEnabled());d.document.setText(str(self.source));self.assertTrue(self.save(d).isEnabled())
        d.trust_roots=(self.source,);d.clear_roots();d.accept();self.assertEqual(d.request,(self.source,()))
        r=SignatureReportDialog(None,(),self.source);self.dialogs.append(r)
        from PyQt6.QtWidgets import QPlainTextEdit
        self.assertIn('Keine eingebetteten',r.findChild(QPlainTextEdit).toPlainText())

    def test_pdfa_invalid_tools_never_accept_and_compact_dialogs_have_buttons(self):
        for mode in ('light','dark'):
            for cls in (CertificateDialog,SignatureVerificationDialog,PdfaDialog):
                with self.subTest(mode=mode,dialog=cls.__name__):
                    d=cls(None);self.dialogs.append(d);d.setStyleSheet(stylesheet(mode));d.resize(680,480);d.show();APP.processEvents()
                    button=self.save(d);self.assertTrue(button.isVisible());self.assertTrue(d.rect().contains(button.mapTo(d,button.rect().center())))
                    if isinstance(d,PdfaDialog):
                        for field in d.paths:field.setText(str(self.root/'missing'))
                        d.accept();self.assertEqual(d.result(),QDialog.DialogCode.Rejected);self.assertIn('nicht gefunden',d.status.text())


if __name__=='__main__':unittest.main()
