"""Update UI never installs on a check, a download, or Cancel."""
import os
from pathlib import Path
import tempfile,time,unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from PyQt6.QtCore import QCoreApplication,QEvent,QSettings
from PyQt6.QtWidgets import QApplication,QDialog
from pdf_workbench.core import OperationCancelled
from pdf_workbench.ui.update_dialog import UpdateDialog
from pdf_workbench.ui.theme import stylesheet
from pdf_workbench.updates import CHECK_ENABLED_KEY,release_from_metadata
from test_updates import metadata

APP=QApplication.instance() or QApplication([])
def wait(predicate,timeout=10):
    end=time.monotonic()+timeout
    while not predicate():
        APP.processEvents();time.sleep(.005)
        if time.monotonic()>end:raise AssertionError("Update dialog did not finish")
    APP.processEvents()


class UpdateUiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.settings=QSettings(str(self.root/"settings.ini"),QSettings.Format.IniFormat)
        self.release=release_from_metadata(metadata()[0],"0.7.99","linux");self.dialogs=[]
    def tearDown(self):
        for dialog in self.dialogs:
            with patch("pdf_workbench.ui.update_dialog.discard_install"):dialog.reject()
            wait(lambda:dialog.worker is None);dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);self.temp.cleanup()
    def dialog(self,release=None):
        dialog=UpdateDialog(None,"0.7.99",self.root,self.settings,release);self.dialogs.append(dialog);dialog.show();return dialog

    def test_daily_preference_current_version_and_real_worker_finish(self):
        with patch("pdf_workbench.ui.update_dialog.check_latest",return_value=None) as check:
            dialog=self.dialog();wait(lambda:dialog.worker is None and "aktuelle stabile" in dialog.status.text())
            self.assertEqual(check.call_count,1);self.assertTrue(dialog.automatic.isChecked());dialog.automatic.setChecked(False)
            self.assertFalse(self.settings.value(CHECK_ENABLED_KEY,True,type=bool));self.assertIsNone(dialog.plan)

    def test_source_checkout_can_see_release_but_cannot_prepare_installation(self):
        with patch("pdf_workbench.ui.update_dialog.sys.frozen",False,create=True),patch("pdf_workbench.ui.update_dialog.prepare_install") as prepare:
            dialog=self.dialog(self.release);self.assertFalse(dialog.action.isEnabled());self.assertIn("Quellcode",dialog.detail.text());dialog.download();prepare.assert_not_called()

    def test_download_prepares_only_and_requires_separate_explicit_restart_acceptance(self):
        plan=object()
        with patch("pdf_workbench.ui.update_dialog.sys.frozen",True,create=True),patch("pdf_workbench.ui.update_dialog.download_update",return_value=object()),patch("pdf_workbench.ui.update_dialog.prepare_install",return_value=plan):
            dialog=self.dialog(self.release);dialog.action.click();wait(lambda:dialog.worker is None and dialog.plan is not None)
            self.assertTrue(dialog.isVisible());self.assertEqual(dialog.result(),QDialog.DialogCode.Rejected)
            self.assertIn("Neustarten",dialog.action.text());dialog.action.click();self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)

    def test_cancel_prepared_plan_discards_staging_and_never_launches_helper(self):
        plan=object()
        with patch("pdf_workbench.ui.update_dialog.sys.frozen",True,create=True):
            dialog=self.dialog(self.release);dialog.plan=plan
            with patch("pdf_workbench.ui.update_dialog.discard_install") as discard:dialog.reject();discard.assert_called_once_with(plan)
            self.assertIsNone(dialog.plan);self.assertFalse(dialog.isVisible())

    def test_cancel_active_check_joins_worker_before_dialog_returns(self):
        def check(*args,cancel=None,**kwargs):
            while not cancel():time.sleep(.005)
            raise OperationCancelled("cancel")
        with patch("pdf_workbench.ui.update_dialog.check_latest",side_effect=check):
            dialog=self.dialog();wait(lambda:dialog.worker is not None);dialog.reject();wait(lambda:dialog.worker is None)
            self.assertFalse(dialog.isVisible());self.assertIsNone(dialog.plan)

    def test_failure_and_light_dark_compact_layout(self):
        for theme in ("light","dark"):
            with self.subTest(theme=theme),patch("pdf_workbench.ui.update_dialog.check_latest",side_effect=RuntimeError("Testverbindung nicht verfügbar")):
                dialog=self.dialog();dialog.setStyleSheet(stylesheet(theme));dialog.resize(540,420)
                wait(lambda:dialog.worker is None and bool(dialog.error.text()))
                self.assertIn("Testverbindung",dialog.error.text());self.assertTrue(dialog.action.isEnabled())
                self.assertTrue(dialog.rect().contains(dialog.action.mapTo(dialog,dialog.action.rect().center())))


if __name__=="__main__":unittest.main()
