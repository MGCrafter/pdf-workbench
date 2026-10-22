"""Document isolation, reversible transfer and multi-tab crash/update safety."""
from dataclasses import dataclass, replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QEventLoop, QSettings, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox
from pypdf import PdfReader, PdfWriter

from pdf_workbench.app import MainWindow
from pdf_workbench.core import Workspace, PdfWorkbenchError, export_pdf, load_source
from pdf_workbench.document_transfer import transfer_pages
from pdf_workbench.projects import read_project


APP = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=10):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        APP.processEvents(); time.sleep(.004)
    APP.processEvents()
    if not predicate(): raise AssertionError("Document-tab worker timed out")


@dataclass(frozen=True)
class PreparedPlan:
    restart_args: tuple[str, ...] = ()


class DocumentTabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.pdf = self.root / "original.pdf"
        writer = PdfWriter(); writer.add_blank_page(240, 400); writer.add_blank_page(300, 500)
        writer.write(self.pdf); self.original = self.pdf.read_bytes()
        self.window = MainWindow(); self.settings = self.window.theme.settings
        self.window.theme.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.window.show()
        self.ids = self.window.workspace.add_sources([load_source(self.pdf)])
        self.window.refresh(self.ids, self.ids[0])

    def tearDown(self):
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
            self.window.close(); wait_for(lambda: not self.window.isVisible())
        self.window.theme.settings = self.settings
        self.window.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        self.assertEqual(self.pdf.read_bytes(), self.original)
        self.temp.cleanup()

    def save(self, name, **options):
        path = self.root / name
        with patch("pdf_workbench.ui.workspace_session.QFileDialog.getSaveFileName", return_value=(str(path), "")):
            self.window.save_project(**options)
        wait_for(lambda: self.window.job is None)
        return path

    def test_tabs_isolate_history_selection_zoom_and_project_identity(self):
        original = self.window.workspace
        original.rotate(self.ids, 90); self.window.refresh(self.ids, self.ids[-1])
        self.window.preview.set_zoom(1.75)
        project = self.save("first.pdfwb")
        identity = self.window._project_identity
        self.window.new_document_tab()
        self.assertFalse(self.window.workspace.pages)
        self.assertIsNone(self.window._project_path)
        self.assertFalse(self.window.workspace.can_undo)
        second = self.window.workspace
        self.window.workspace.add_sources([load_source(self.pdf)]); self.window.refresh()
        self.window.preview.set_zoom(.65)
        self.window.switch_document_tab(0)
        self.assertIs(self.window.workspace, original)
        self.assertEqual(self.window._project_path, project)
        self.assertEqual(self.window._project_identity, identity)
        self.assertEqual(self.window._active_uid, self.ids[-1])
        self.assertEqual(self.window.preview.zoom_factor, 1.75)
        self.window.undo(); self.assertEqual(original.pages[0].rotation, 0)
        self.window.switch_document_tab(1)
        self.assertIs(self.window.workspace, second)
        self.assertAlmostEqual(self.window.preview.zoom_factor, .65)
        self.assertTrue(self.window.workspace.can_undo)

    def test_transfer_preserves_rotation_bookmark_targets_history_and_original_export(self):
        original = self.window.workspace
        original.add_bookmark("Zweite Seite", self.ids[-1]); original.rotate([self.ids[-1]], 270)
        self.window.refresh(self.ids)
        self.window.new_document_tab(); self.window.switch_document_tab(0)
        self.window.copy_pages_to_tab(target_index=1, move=True)
        self.assertFalse(original.pages)
        target = self.window._document_tabs[1].workspace
        self.assertEqual([page.rotation for page in target.pages], [0, 270])
        self.assertEqual(target.bookmarks[0].page_uid, target.pages[-1].uid)
        self.assertTrue(set(self.ids).isdisjoint(page.uid for page in target.pages))
        output = self.root / "transferred.pdf"
        export_pdf(target.sources, target.pages, output, bookmarks=target.bookmarks)
        reader = PdfReader(output)
        self.assertEqual([page.rotation for page in reader.pages], [0, 270])
        self.assertEqual(reader.get_destination_page_number(reader.outline[0]), 1)
        original.undo(); self.assertEqual([page.uid for page in original.pages], self.ids)
        target.undo(); self.assertFalse(target.pages)
        target.redo(); self.assertEqual(len(target.pages), 2)

    def test_protected_project_password_never_crosses_tabs(self):
        source = next(iter(self.window.workspace.sources.values()))
        self.window.workspace.sources[source.source_id] = replace(source, was_encrypted=True)
        with patch("pdf_workbench.ui.workspace_session.ProjectPasswordDialog.ask", return_value="first tab only"):
            project = self.save("protected.pdfwb")
        self.window.new_document_tab()
        self.assertIsNone(self.window._project_password)
        self.window.switch_document_tab(0)
        self.assertEqual(self.window._project_password, "first tab only")
        self.window.copy_pages_to_tab(target_index=1)
        self.window.switch_document_tab(1)
        self.assertIsNone(self.window._project_password)
        self.assertTrue(any(source.was_encrypted for source in self.window.workspace.sources.values()))
        self.assertNotIn(b"%PDF-", project.read_bytes())

    def test_all_tabs_receive_distinct_recovery_and_inactive_move_is_saved(self):
        self.window.enable_background_services(data_dir=self.root / "data", prompt_recovery=False, check_updates=False)
        self.window.new_document_tab(); self.window.switch_document_tab(0)
        self.window.copy_pages_to_tab(target_index=1, move=True)
        self.window._autosave(); wait_for(lambda: self.window._autosave_job is None)
        self.window._autosave(); wait_for(lambda: self.window._autosave_job is None)
        first, second = self.window._document_tabs
        stores = [tab.session["_recovery_store"] for tab in (first, second)]
        self.assertNotEqual(stores[0].session_id, stores[1].session_id)
        snapshots = []
        for store in stores:
            paths = list(store.directory.glob(f"{store.session_id}-*.pdfwb"))
            self.assertTrue(paths)
            snapshots.append(read_project(sorted(paths)[-1]).snapshot)
        self.assertEqual([len(snapshot.state.pages) for snapshot in snapshots], [0, 2])
        self.assertTrue(snapshots[0].state.undo)

    def test_cancel_window_close_preserves_every_tab_and_close_target_only(self):
        first = self.window.workspace
        self.window.new_document_tab()
        second_ids = self.window.workspace.add_sources([load_source(self.pdf)]); self.window.refresh()
        with patch.object(QMessageBox, "question", side_effect=[QMessageBox.StandardButton.Discard, QMessageBox.StandardButton.Cancel]):
            self.window.close()
        self.assertTrue(self.window.isVisible())
        self.assertEqual(len(self.window._document_tabs), 2)
        self.assertEqual(len(first.pages), 2)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
            self.window.close_document_tab(0)
        self.assertEqual(len(self.window._document_tabs), 1)
        self.assertEqual([page.uid for page in self.window.workspace.pages], second_ids)

    def test_restore_multiple_embedded_projects_into_tabs(self):
        first = self.save("first.pdfwb")
        self.window.new_document_tab()
        self.window.workspace.add_sources([load_source(self.pdf)]); self.window.refresh()
        self.window.workspace.rotate(self.window.selected_ids(), 90); self.window.refresh()
        second = self.save("second.pdfwb")
        self.window.new_document_tab()
        self.window.restore_document_tabs((first, second))
        wait_for(lambda: self.window.job is None and len(self.window._document_tabs) == 4)
        self.assertEqual([tab.session.get("_project_path") for tab in self.window._document_tabs][-2:], [first, second])
        self.assertEqual(self.window.workspace.pages[0].rotation, 90)

    def test_collision_rejection_is_atomic_for_both_workspaces(self):
        origin = self.window.workspace; target = Workspace()
        source = next(iter(origin.sources.values()))
        target.add_sources([replace(source, path=self.root / "different.pdf")])
        before_origin, before_target = origin.snapshot(), target.snapshot()
        with self.assertRaises(PdfWorkbenchError): transfer_pages(origin, target, self.ids, move=True)
        self.assertEqual(origin.snapshot(), before_origin)
        self.assertEqual(target.snapshot(), before_target)

    def test_failed_update_launch_never_reuses_discard_approval_after_new_edits(self):
        self.window.new_document_tab(); self.window.switch_document_tab(0)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard), \
                patch("pdf_workbench.update_installer.launch_install", side_effect=OSError("test launch failure")), \
                patch.object(self.window, "show_error"):
            self.window._install_update(PreparedPlan())
        self.assertFalse(self.window._update_discarded_tabs)
        self.window.workspace.rotate(self.ids, 90); self.window.refresh()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as question, \
                patch("pdf_workbench.update_installer.discard_install"), \
                patch("pdf_workbench.update_installer.launch_install") as launch:
            self.window._install_update(PreparedPlan())
            self.assertEqual(question.call_count, 1); launch.assert_not_called()
        self.assertEqual(self.window.workspace.pages[0].rotation, 90)

    def test_multi_tab_update_restarts_with_all_saved_projects_without_password_arguments(self):
        first = self.save("first.pdfwb")
        self.window.new_document_tab()
        self.window.workspace.add_sources([load_source(self.pdf)]); self.window.refresh()
        second = self.save("second.pdfwb")
        with patch("pdf_workbench.update_installer.launch_install") as launch, patch.object(self.window, "close"):
            self.window._install_update(PreparedPlan())
        self.assertEqual(launch.call_args.args[0].restart_args, (str(first), str(second)))

    def test_tab_close_modal_cannot_start_autosave_on_store_being_removed(self):
        self.window.enable_background_services(data_dir=self.root / "data", prompt_recovery=False, check_updates=False)
        store = self.window._recovery_store
        self.window.new_document_tab()
        self.window._autosave_timer.setInterval(5)
        def discard(*args, **kwargs):
            self.assertFalse(self.window._autosave_timer.isActive())
            QTimer.singleShot(1, self.window._session_changed)
            QTimer.singleShot(2, self.window._autosave)
            loop = QEventLoop(); QTimer.singleShot(30, loop.quit); loop.exec()
            self.assertIsNone(self.window._autosave_job)
            return QMessageBox.StandardButton.Discard
        with patch.object(QMessageBox, "question", side_effect=discard):
            self.window.close_document_tab(0)
        self.assertTrue(store.closed)
        self.assertEqual(len(self.window._document_tabs), 1)
        self.assertIsNone(self.window._autosave_job)

    def test_cancelled_tab_close_resumes_autosave_after_late_modal_view_events(self):
        self.window.enable_background_services(data_dir=self.root / "data", prompt_recovery=False, check_updates=False)
        self.window.new_document_tab(); self.window.switch_document_tab(0)
        self.window._autosave_timer.setInterval(5)
        def cancel(*args, **kwargs):
            QTimer.singleShot(1, self.window._session_changed)
            QTimer.singleShot(2, self.window._autosave)
            loop = QEventLoop(); QTimer.singleShot(30, loop.quit); loop.exec()
            self.assertIsNone(self.window._autosave_job)
            return QMessageBox.StandardButton.Cancel
        with patch.object(QMessageBox, "question", side_effect=cancel): self.window.close_document_tab(0)
        self.assertEqual(len(self.window._document_tabs), 2)
        self.assertEqual(self.window._autosave_suppression, 0)
        self.assertTrue(self.window._autosave_timer.isActive())


if __name__ == "__main__": unittest.main()
