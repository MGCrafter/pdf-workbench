"""Permanent regressions for independently reproduced session boundary failures."""
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pypdf import PdfWriter
from PyQt6.QtCore import QCoreApplication,QEvent,QSettings
from PyQt6.QtWidgets import QApplication,QMessageBox
from pdf_workbench.app import MainWindow
from pdf_workbench.core import load_source
from pdf_workbench.projects import list_recoveries,read_project

APP=QApplication.instance() or QApplication([])


def wait_for(predicate,timeout=8):
    deadline=time.monotonic()+timeout
    while not predicate() and time.monotonic()<deadline:APP.processEvents();time.sleep(.003)
    APP.processEvents()
    if not predicate():raise AssertionError('Session worker did not finish')


@dataclass(frozen=True)
class PreparedPlan:
    restart_args:tuple[str,...]=()


class SessionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.pdf=self.root/'original.pdf';writer=PdfWriter();writer.add_blank_page(300,400);writer.write(self.pdf)
        self.original=self.pdf.read_bytes();self.window=MainWindow();self.old_settings=self.window.theme.settings
        self.window.theme.settings=QSettings(str(self.root/'settings.ini'),QSettings.Format.IniFormat)
        self.window.show();self.ids=self.window.workspace.add_sources([load_source(self.pdf)]);self.window.refresh()

    def tearDown(self):
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Discard),patch('pdf_workbench.update_installer.discard_install'):
            self.window.close();wait_for(lambda:self.window.render_broker.is_closed and self.window.search_bar.is_closed and self.window._background_closed())
        self.window.theme.settings=self.old_settings;self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.assertEqual(self.pdf.read_bytes(),self.original);self.temp.cleanup()

    def test_history_only_update_requires_confirmation_and_save_restores_entire_session(self):
        self.window.workspace.delete(self.ids);self.window.refresh();before=self.window.workspace.snapshot();plan=PreparedPlan()
        self.assertFalse(self.window.workspace.pages);self.assertTrue(self.window.workspace.can_undo)
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Cancel) as question,patch('pdf_workbench.update_installer.discard_install') as discard,patch('pdf_workbench.update_installer.launch_install') as launch:
            self.window._install_update(plan);question.assert_called_once();discard.assert_called_once_with(plan);launch.assert_not_called()
        self.assertEqual(self.window.workspace.snapshot(),before)
        target=self.root/'before-update.pdfwb'
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Save),patch('pdf_workbench.ui.workspace_session.QFileDialog.getSaveFileName',return_value=(str(target),'')),patch('pdf_workbench.update_installer.launch_install') as launch:
            self.window._install_update(plan)
            wait_for(lambda:self.window.job is None and launch.called and self.window.render_broker.is_closed and self.window.search_bar.is_closed)
        self.assertEqual(read_project(target).snapshot.state,before)
        self.assertEqual(launch.call_args.args[0].restart_args,(str(target),));self.assertIsNone(self.window._pending_update_plan)

    def test_cancelled_project_save_keeps_preparation_for_retry_cancel_or_close(self):
        plan=PreparedPlan()
        def abort_save():
            with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Save),patch('pdf_workbench.ui.workspace_session.QFileDialog.getSaveFileName',return_value=('','')),patch('pdf_workbench.update_installer.launch_install') as launch:
                self.window._install_update(plan);launch.assert_not_called()
            self.assertEqual(self.window._pending_update_plan,plan);self.assertTrue(self.window.isVisible())
        abort_save()
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Cancel),patch('pdf_workbench.update_installer.discard_install') as discard:
            self.window._install_update(plan);discard.assert_called_once_with(plan)
        self.assertIsNone(self.window._pending_update_plan)
        abort_save()
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Discard),patch('pdf_workbench.update_installer.discard_install') as discard:
            self.window.close();wait_for(lambda:not self.window.isVisible() and self.window.render_broker.is_closed and self.window.search_bar.is_closed)
            discard.assert_called_once_with(plan)
        self.assertIsNone(self.window._pending_update_plan)

    def test_recovery_cleanup_io_failure_releases_os_lock_store_and_password(self):
        self.window.enable_background_services(data_dir=self.root/'data',prompt_recovery=False,check_updates=False)
        self.window._autosave_timer.stop();self.window._autosave();wait_for(lambda:self.window._autosave_job is None);self.window._autosave_timer.stop()
        store=self.window._recovery_store;self.assertEqual(list_recoveries(store.directory),())
        files=list(store.directory.glob('*.pdfwb'));self.assertEqual(len(files),1)
        self.window._project_password='memory-only'
        with patch.object(store,'discard',side_effect=PermissionError('Recovery directory is read-only')):
            self.window._reset_project_session();self.assertIsNone(self.window._project_password)
            self.window._project_password='memory-only'
            self.window._finish_session()
        self.assertIsNone(store.lock.stream);self.assertTrue(store.closed)
        self.assertIsNone(self.window._recovery_store);self.assertIsNone(self.window._project_password)
        self.assertEqual([entry.path for entry in list_recoveries(store.directory)],files)
        self.assertEqual(read_project(files[0]).snapshot.state.pages,tuple(self.window.workspace.pages))


if __name__=='__main__':unittest.main()
