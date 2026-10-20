"""Search navigation, stale queries and thread shutdown on real PDF snapshots."""
import importlib.util
from pathlib import Path
import tempfile
import time
import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from pdf_workbench.core import Workspace, load_source
from pdf_workbench.ui.search_bar import SearchBar

APP = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('search_bar_fixture', ROOT / 'packaging/smoke.py')
smoke = importlib.util.module_from_spec(spec); spec.loader.exec_module(smoke)


def wait_for(predicate):
    until = time.monotonic()+15
    while not predicate():
        APP.processEvents(); time.sleep(.005)
        if time.monotonic()>until: raise AssertionError('Search worker timed out')
    APP.processEvents()


class SearchBarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name)/'search.pdf'; smoke.fixture(path, ('Find first', 'Find second'))
        self.workspace = Workspace(); self.workspace.add_sources([load_source(path)])
        self.bar = SearchBar(lambda: (dict(self.workspace.sources), tuple(self.workspace.pages)))
        self.hits = []; self.bar.hit_selected.connect(self.hits.append)
        self.bar.open(); APP.processEvents()

    def tearDown(self):
        self.bar.shutdown(); wait_for(lambda: self.bar.is_closed)
        self.bar.close(); self.bar.deleteLater(); APP.processEvents()

    def test_native_query_navigation_and_keyboard_close(self):
        self.bar.editor.setText('Find')
        wait_for(lambda: len(self.bar.results.hits)==2 and self.bar.worker is None)
        self.assertEqual(self.hits[-1].page_uid, self.workspace.pages[0].uid)
        QTest.keyClick(self.bar.editor, Qt.Key.Key_Return)
        self.assertEqual(self.hits[-1].page_uid, self.workspace.pages[1].uid)
        QTest.keyClick(self.bar.editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.hits[-1].page_uid, self.workspace.pages[0].uid)
        QTest.keyClick(self.bar.editor, Qt.Key.Key_Escape)
        self.assertFalse(self.bar.isVisible()); self.assertFalse(self.bar.results.hits)

    def test_query_replaced_during_running_search_never_shows_old_hits(self):
        self.bar.editor.setText('first'); self.bar.timer.stop(); self.bar._launch()
        self.bar.editor.setText('second')
        wait_for(lambda: self.bar.worker is None and len(self.bar.results.hits)==1)
        self.assertEqual(self.bar.results.hits[0].page_uid, self.workspace.pages[1].uid)
        self.assertTrue(all(hit.page_uid == self.workspace.pages[1].uid for hit in self.hits))

    def test_document_change_restarts_visible_search_with_new_page_order(self):
        self.bar.editor.setText('Find'); wait_for(lambda: len(self.bar.results.hits)==2)
        self.workspace.reorder([page.uid for page in reversed(self.workspace.pages)])
        self.bar.workspace_changed(self.workspace.pages)
        wait_for(lambda: self.bar.worker is None and len(self.bar.results.hits)==2)
        self.assertEqual(self.bar.results.hits[0].page_uid,self.workspace.pages[0].uid)
        self.bar.shutdown(); wait_for(lambda: self.bar.is_closed)
        self.assertIsNone(self.bar.worker)
