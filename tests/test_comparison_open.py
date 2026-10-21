"""Comparison loading stays read-only and handles password retry and cancellation."""
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication
from pypdf import PdfWriter
from pdf_workbench.app import MainWindow

APP = QApplication.instance() or QApplication([])


def wait(predicate):
    deadline = time.monotonic() + 15
    while not predicate():
        APP.processEvents()
        time.sleep(.005)
        if time.monotonic() > deadline:
            raise AssertionError('Comparison loading did not finish')
    APP.processEvents()


class ComparisonOpenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = [self.root / 'left.pdf', self.root / 'right.pdf']
        for path in self.paths:
            writer = PdfWriter()
            writer.add_blank_page(300, 500)
            writer.write(path)
        self.window = MainWindow()
        self.errors = []
        self.window.show_error = lambda *args: self.errors.append(args)
        self.dialog = Mock(return_value=SimpleNamespace(exec=lambda: 0, deleteLater=Mock()))
        self.module = patch.dict(sys.modules, {'pdf_workbench.ui.compare_dialog': SimpleNamespace(ComparisonDialog=self.dialog)})
        self.module.start()

    def tearDown(self):
        if self.window.job:
            self.window.cancel_job()
            wait(lambda: self.window.job is None)
        self.module.stop()
        self.window.saved_state = tuple(self.window.workspace.pages)
        self.window.close()
        wait(lambda: self.window.render_broker.is_closed and self.window.search_bar.is_closed)
        self.temp.cleanup()

    def encrypt_right(self):
        writer = PdfWriter(clone_from=self.paths[1])
        writer.encrypt('correct secret', algorithm='AES-256')
        writer.write(self.paths[1])

    def test_loaded_versions_do_not_change_workspace_or_undo(self):
        originals = [path.read_bytes() for path in self.paths]
        self.window.compare_pdfs(paths=self.paths)
        wait(lambda: self.dialog.called and self.window.job is None)
        self.assertEqual([source.path for source in self.dialog.call_args.args[:2]],
                         [path.resolve() for path in self.paths])
        self.assertEqual(self.window.workspace.pages, [])
        self.assertFalse(self.window.workspace.can_undo)
        self.assertEqual([path.read_bytes() for path in self.paths], originals)
        self.assertEqual(self.errors, [])

    def test_encrypted_second_version_retries_then_opens_without_exposing_password(self):
        self.encrypt_right()
        original = self.paths[1].read_bytes()
        with patch('pdf_workbench.app.PasswordDialog.get_password', side_effect=['wrong secret', 'correct secret']) as prompt:
            self.window.compare_pdfs(paths=self.paths)
            wait(lambda: self.dialog.called and self.window.job is None)
        self.assertEqual(prompt.call_count, 2)
        self.assertFalse(prompt.call_args_list[0].kwargs['incorrect'])
        self.assertTrue(prompt.call_args_list[1].kwargs['incorrect'])
        self.assertEqual(self.paths[1].read_bytes(), original)
        self.assertEqual(self.window.workspace.sources, {})
        self.assertNotIn('secret', self.window.status.text())

    def test_password_cancellation_discards_partial_comparison(self):
        self.encrypt_right()
        with patch('pdf_workbench.app.PasswordDialog.get_password', return_value=None) as prompt:
            self.window.compare_pdfs(paths=self.paths)
            wait(lambda: prompt.called and self.window.job is None)
        self.dialog.assert_not_called()
        self.assertEqual(self.window.workspace.sources, {})
        self.assertEqual(self.window.workspace.pages, [])
        self.assertEqual(self.errors, [])

    def test_cancellation_after_loading_finishes_does_not_open_comparison(self):
        self.window.compare_pdfs(paths=self.paths)
        self.window.job.succeeded.connect(lambda _: self.window.cancel_job())
        wait(lambda: self.window.job is None)
        APP.processEvents()
        self.dialog.assert_not_called()
        self.assertEqual(self.window.workspace.sources, {})


if __name__ == '__main__':
    unittest.main()
