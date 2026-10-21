"""Compact outline editing with keyboard movement and cancel/commit isolation."""
from pathlib import Path
import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from pypdf import PdfWriter
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication,QDialog
from pdf_workbench.core import Workspace,WorkspaceBookmark,load_source
from pdf_workbench.ui.bookmarks_dialog import BookmarksDialog
from pdf_workbench.ui.theme import stylesheet


class BookmarkUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);path=self.root/"source.pdf";writer=PdfWriter()
        for _ in range(3):writer.add_blank_page(300,500)
        writer.add_outline_item("Importiert",0);writer.write(path);self.workspace=Workspace();self.workspace.add_sources([load_source(path)]);self.dialogs=[]

    def tearDown(self):
        for dialog in self.dialogs:dialog.close();dialog.deleteLater()
        self.app.processEvents();self.app.setStyleSheet("");self.temp.cleanup()

    def dialog(self):
        d=BookmarksDialog(self.workspace,page_uid=self.workspace.pages[1].uid);self.dialogs.append(d);d.resize(800,560);d.show();self.app.processEvents();return d

    def test_rename_target_nested_move_keyboard_accept_and_workspace_undo(self):
        before=self.workspace.snapshot();d=self.dialog();a=d.add_bookmark();d.title_edit.setText("Neues Kapitel");d.target_combo.setCurrentIndex(0);self.assertTrue(d.apply_properties())
        b=d.add_bookmark(child=True);d.title_edit.setText("Grüße Wien");d.target_combo.setCurrentIndex(3);self.assertTrue(d.apply_properties())
        d.tree.setFocus();QTest.keyClick(d,Qt.Key.Key_Left,Qt.KeyboardModifier.AltModifier);self.assertEqual(d.workspace.bookmarks[-1].uid,b)
        QTest.keyClick(d,Qt.Key.Key_Right,Qt.KeyboardModifier.AltModifier);self.assertEqual(d.workspace.bookmarks[-1].children[0].uid,b)
        d.undo();self.assertEqual(d.workspace.bookmarks[-1].uid,b);d.redo();self.assertEqual(d.workspace.bookmarks[-1].children[0].uid,b)
        self.assertEqual(self.workspace.snapshot(),before);d.accept();self.assertEqual(d.result(),QDialog.DialogCode.Accepted)
        self.workspace.set_bookmarks(d.result_bookmarks);self.assertEqual(self.workspace.bookmarks[-1].children[0].title,"Grüße Wien")
        self.assertEqual(self.workspace.bookmarks[-1].children[0].page_uid,self.workspace.pages[2].uid)
        self.workspace.undo();self.assertEqual(self.workspace.bookmarks,before.bookmarks)

    def test_selected_tree_and_properties_stay_aligned_and_invalid_input_survives(self):
        d=self.dialog();first=d.workspace.bookmarks[0].uid;second=d.add_bookmark();d.title_edit.setText("Second");d.apply_properties();d.select_bookmark(first)
        self.assertEqual(d.tree.currentItem().data(0,Qt.ItemDataRole.UserRole),first);self.assertEqual(d.title_edit.text(),"Importiert")
        d.title_edit.setText("");d.select_bookmark(second);self.assertEqual(d._uid,first);self.assertEqual(d.title_edit.text(),"");self.assertTrue(d.error.text())
        d.accept();self.assertTrue(d.isVisible());self.assertIsNone(d.result_bookmarks)
        d.title_edit.setText("Valid");d.select_bookmark(second);self.assertEqual(d.title_edit.text(),"Second");self.assertEqual(d.tree.currentItem().data(0,Qt.ItemDataRole.UserRole),second)

    def test_delete_and_cancel_are_local_and_compact_themes_fit(self):
        before=self.workspace.snapshot()
        for theme in ("light","dark"):
            self.app.setStyleSheet(stylesheet(theme));d=self.dialog();d.delete_bookmark();self.assertEqual(d.workspace.bookmarks,())
            d.undo();self.assertTrue(d.workspace.bookmarks);d.reject();self.assertIsNone(d.result_bookmarks)
            self.assertEqual((d.width(),d.height()),(800,560));self.assertGreater(d.tree.viewport().width(),250)
        self.assertEqual(self.workspace.snapshot(),before)

    def test_maximum_depth_failure_is_visible_atomic_and_keyboard_safe(self):
        subtree=WorkspaceBookmark('level32','Level 32')
        for depth in range(31,0,-1):subtree=WorkspaceBookmark(f'level{depth}',f'Level {depth}',children=(subtree,))
        self.workspace.set_bookmarks((WorkspaceBookmark('first','First'),subtree))
        d=self.dialog();d.select_bookmark('level1');before=d.workspace.snapshot()
        d.title_edit.setText('Uncommitted rename');d.indent_button.click();self.app.processEvents()
        self.assertEqual(d.workspace.snapshot(),before);self.assertIn('32 Ebenen',d.error.text())
        self.assertEqual(d._uid,'level1');self.assertEqual(d.title_edit.text(),'Uncommitted rename')
        d.tree.setFocus();QTest.keyClick(d,Qt.Key.Key_Right,Qt.KeyboardModifier.AltModifier)
        self.assertEqual(d.workspace.snapshot(),before);self.assertTrue(d.isVisible())
        d.title_edit.setText('Level 1');d.select_bookmark('level32');before=d.workspace.snapshot()
        d.child_button.click();self.assertEqual(d.workspace.snapshot(),before);self.assertIn('32 Ebenen',d.error.text())
        d.outdent_button.click();self.assertFalse(d.error.text());self.assertNotEqual(d.workspace.snapshot(),before)
        d.undo();self.assertEqual(d.workspace.bookmarks,before.bookmarks);self.assertTrue(d.workspace.can_redo)


if __name__=="__main__":unittest.main()
