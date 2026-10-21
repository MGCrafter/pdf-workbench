"""Actual window/worker acceptance for persistence, recovery and shutdown."""
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QSettings
from PyQt6.QtWidgets import QApplication, QMessageBox
from pypdf import PdfWriter

from pdf_workbench.app import MainWindow
from pdf_workbench.core import load_source
from pdf_workbench.projects import read_project, list_recoveries,RecoveryStore,ProjectSnapshot

APP = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents(); time.sleep(.003)
    APP.processEvents()
    if not predicate(): raise AssertionError("Window worker timed out")


class ProjectWindowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.root = Path(self.temporary.name)
        self.settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        self.window = MainWindow(); self.old_settings = self.window.theme.settings
        self.window.theme.settings = self.settings; self.window.show()
        writer = PdfWriter(); writer.add_blank_page(240, 400)
        self.pdf = self.root / 'source.pdf'; writer.write(self.pdf)
        self.original = self.pdf.read_bytes()
        self.ids = self.window.workspace.add_sources([load_source(self.pdf)])
        self.window.refresh()

    def tearDown(self):
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Discard):
            self.window.close(); wait_for(lambda: not self.window.isVisible())
        self.window.theme.settings = self.old_settings
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); APP.processEvents()
        self.assertEqual(self.pdf.read_bytes(), self.original)
        self.temporary.cleanup()

    def save(self, name='session.pdfwb', **options):
        path = self.root / name
        with patch('pdf_workbench.ui.workspace_session.QFileDialog.getSaveFileName', return_value=(str(path), '')):
            self.window.save_project(**options)
        wait_for(lambda: self.window.job is None)
        return path

    def test_project_round_trip_restores_bookmark_only_changes_undo_and_view_without_original(self):
        self.window.workspace.add_bookmark('Kapitel', self.ids[0]); self.window.workspace.rotate(self.ids, 90)
        self.window.preview.set_zoom(1.5)
        path = self.save(); self.assertFalse(self.window._is_dirty())
        self.window.workspace.update_bookmark(self.window.workspace.bookmarks[0].uid, title='Changed')
        self.window.refresh(); self.assertTrue(self.window._is_dirty())
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Discard):
            self.window.open_project(path=path)
        wait_for(lambda: self.window.job is None)
        self.assertEqual(self.window.workspace.bookmarks[0].title, 'Kapitel')
        self.assertEqual(self.window.workspace.pages[0].rotation, 90)
        self.assertEqual(self.window.preview.zoom_mode, 'custom')
        self.assertAlmostEqual(self.window.preview.zoom_factor, 1.5)
        self.window.undo(); self.assertEqual(self.window.workspace.pages[0].rotation, 0)

    def test_save_before_new_is_asynchronous_and_cancel_never_discards(self):
        path = self.root / 'before-new.pdfwb'
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Cancel): self.window.new_workspace()
        self.assertEqual(len(self.window.workspace.pages), 1)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Save), patch('pdf_workbench.ui.workspace_session.QFileDialog.getSaveFileName', return_value=(str(path), '')):
            self.window.new_workspace()
        wait_for(lambda: self.window.job is None and not self.window.workspace.pages)
        self.assertEqual(len(read_project(path).snapshot.state.pages), 1)

    def test_autosave_recovers_embedded_content_and_clean_close_discards_only_own_session(self):
        self.window.enable_background_services(data_dir=self.root / 'data', prompt_recovery=False, check_updates=False)
        self.window._autosave(); wait_for(lambda: self.window._autosave_job is None)
        store = self.window._recovery_store; paths = list(store.directory.glob('*.pdfwb'))
        self.assertEqual(len(paths), 1); self.assertEqual(list_recoveries(store.directory), ())
        self.assertEqual(read_project(paths[0]).snapshot.state.pages, tuple(self.window.workspace.pages))
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Discard):
            self.window.close(); wait_for(lambda: not self.window.isVisible())
        self.assertFalse(paths[0].exists())

    def test_sensitive_history_pauses_plaintext_autosave_then_writes_encrypted_recovery(self):
        source = next(iter(self.window.workspace.sources.values()))
        self.window.workspace.sources[source.source_id] = replace(source, was_encrypted=True)
        self.window.enable_background_services(data_dir=self.root / 'data', prompt_recovery=False, check_updates=False)
        self.window._autosave(); self.assertIsNone(self.window._autosave_job)
        self.assertEqual(list(self.window._recovery_store.directory.glob('*.pdfwb')), [])
        with patch('pdf_workbench.ui.workspace_session.ProjectPasswordDialog.ask', return_value='secret passphrase'):
            path = self.save()
        self.assertNotIn(b'%PDF-', path.read_bytes())
        self.window._autosave(); wait_for(lambda: self.window._autosave_job is None)
        recoveries = list(self.window._recovery_store.directory.glob('*.pdfwb')); self.assertEqual(len(recoveries), 1)
        self.assertNotIn(b'%PDF-', recoveries[0].read_bytes())
        self.assertEqual(read_project(recoveries[0], password='secret passphrase').snapshot.state.pages, tuple(self.window.workspace.pages))

    def test_corrupt_project_does_not_replace_current_workspace_and_recovery_never_trusts_path_hint(self):
        path = self.save(); before = self.window._content_key()
        broken = self.root / 'broken.pdfwb'; broken.write_bytes(b'corrupted input')
        with patch.object(self.window, 'show_error') as error:
            self.window.open_project(path=broken); wait_for(lambda: self.window.job is None)
            self.assertTrue(error.called)
        self.assertEqual(self.window._content_key(), before)
        self.window.open_project(path=path, recovery=True); wait_for(lambda: self.window.job is None)
        self.assertIsNone(self.window._project_path); self.assertIsNone(self.window._project_identity)
        self.assertTrue(self.window._is_dirty())


    def test_real_open_uses_embedded_pdf_when_original_is_missing(self):
        self.window.workspace.add_bookmark('Bleibt',self.ids[0]);self.window.workspace.rotate(self.ids,90)
        self.window.refresh(self.ids,self.ids[0]);self.window.preview.set_zoom(1.75)
        path=self.save();saved=read_project(path).snapshot
        self.pdf.unlink()
        try:
            self.window.workspace.rotate(self.ids,90);self.window.refresh()
            with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Discard),patch.object(self.window,'show_error') as error:
                self.window.open_project(path=path);wait_for(lambda:self.window.job is None)
                error.assert_not_called()
            self.assertEqual(tuple(self.window.workspace.pages),saved.state.pages)
            self.assertEqual(self.window.workspace.bookmarks,saved.state.bookmarks)
            self.assertEqual(self.window._active_uid,saved.active_uid)
            self.assertEqual(self.window.preview.zoom_factor,1.75)
            self.assertFalse(self.pdf.exists());self.window.undo();self.assertEqual(self.window.workspace.pages[0].rotation,0)
        finally:self.pdf.write_bytes(self.original)

    def test_recovery_can_select_preceding_generation_when_latest_is_corrupt(self):
        self.window.enable_background_services(data_dir=self.root/'data',prompt_recovery=False,check_updates=False)
        crashed=RecoveryStore(self.window._recovery_directory)
        snapshot=ProjectSnapshot(self.window.workspace.snapshot(),project_path=str(self.root/'foreign-hint.pdfwb'))
        previous=crashed.save(snapshot);latest=crashed.save(snapshot);crashed.close();latest.write_bytes(b'corrupt latest generation')
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Discard),patch('pdf_workbench.ui.workspace_session.RecoveryDialog.pick',return_value=next(e for e in list_recoveries(crashed.directory) if e.path==previous)):
            self.window.recover_project();wait_for(lambda:self.window.job is None)
        self.assertEqual(tuple(self.window.workspace.pages),snapshot.state.pages)
        self.assertIsNone(self.window._project_path);self.assertIsNone(self.window._project_identity)
        self.assertTrue(self.window._is_dirty());self.assertTrue(previous.exists());self.assertEqual(latest.read_bytes(),b'corrupt latest generation')


if __name__ == '__main__': unittest.main()
