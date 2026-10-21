"""Desktop authoring actions at the supported compact size, with both themes."""
from __future__ import annotations

from dataclasses import replace
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
from PyQt6.QtWidgets import QApplication, QDialog
from pdf_workbench.core import load_source
from pdf_workbench.form_design import FieldPlacement, FormDesign, apply_form_designs, form_designs
from pdf_workbench.forms import form_fields
from pdf_workbench.ui.form_editor import FormEditorDialog
from pdf_workbench.ui.pdf_dialogs import FormDialog
from pdf_workbench.ui.theme import stylesheet


class FormEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/"original.pdf"
        writer=PdfWriter();writer.add_blank_page(width=400,height=500);writer.add_blank_page(width=400,height=500);writer.write(self.path)
        self.original=self.path.read_bytes();self.source=load_source(self.path);self.dialogs=[]

    def tearDown(self):
        for dialog in self.dialogs:dialog.close();dialog.deleteLater()
        self.app.processEvents();self.app.setStyleSheet("");self.temp.cleanup()

    def dialog(self,source=None,**kwargs):
        dialog=FormEditorDialog(source or self.source,**kwargs);self.dialogs.append(dialog)
        dialog.resize(800,560);dialog.show();QTest.qWait(100);return dialog

    def test_create_edit_delete_radio_accept_light_and_dark(self):
        for theme in ("light","dark"):
            with self.subTest(theme=theme):
                self.app.setStyleSheet(stylesheet(theme));dialog=self.dialog()
                self.assertEqual(dialog.size().width(),800);self.assertEqual(dialog.size().height(),560)
                self.assertFalse(dialog.canvas.image.isNull())
                uids=[]
                for kind in ("text","checkbox","choice","radio"):
                    dialog.type_combo.setCurrentIndex(dialog.type_combo.findData(kind));uids.append(dialog.add_field())
                dialog.name_edit.setText("plan");dialog.label_edit.setText("Tarif")
                dialog.options_edit.setPlainText("basic=Basis\npremium=Premium");dialog.value_edit.setText("premium")
                dialog.required_check.setChecked(True);self.assertTrue(dialog.apply_properties(),dialog.error.text())
                dialog._select_field(uids[0]);dialog.name_edit.setText("customer");dialog.value_edit.setText("Wien")
                dialog.readonly_check.setChecked(True);self.assertTrue(dialog.apply_properties())
                dialog._select_field(uids[1]);dialog.delete_field()
                self.assertEqual(dialog.field_list.count(),3);dialog.accept()
                self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted);self.assertIsNotNone(dialog.result_source)
                fields={f.name:f for f in form_fields(dialog.result_source)}
                self.assertEqual(set(fields),{"customer","Dropdown1","plan"});self.assertTrue(fields["customer"].read_only)
                self.assertTrue(fields["plan"].required);self.assertEqual(fields["plan"].value,"premium")
                filling=FormDialog(dialog.result_source);self.dialogs.append(filling)
                self.assertEqual(filling.editors["plan"].currentData(),"premium")
                self.assertEqual(self.path.read_bytes(),self.original)

    def test_keyboard_and_numeric_geometry_in_rotated_workspace(self):
        dialog=self.dialog(workspace_rotation=90);uid=dialog.add_field();before=dialog._field().placements[0].rect
        dialog.canvas.setFocus();QTest.keyClick(dialog.canvas,Qt.Key.Key_Right)
        after=dialog._field().placements[0].rect
        # Display-right is source-up after a clockwise workspace turn.
        self.assertAlmostEqual(after[0],before[0]);self.assertAlmostEqual(after[1],before[1]-.002)
        dialog.geometry[0].setValue(20);dialog.geometry[1].setValue(30);dialog.geometry[2].setValue(40);dialog.geometry[3].setValue(10)
        self.assertTrue(dialog.apply_properties(),dialog.error.text())
        rect=dialog._field().placements[0].rect
        for actual,expected in zip(rect,(.3,.4,.4,.8)):self.assertAlmostEqual(actual,expected)
        dialog.accept();self.assertIsNotNone(dialog.result_source)
        for actual,expected in zip(form_designs(dialog.result_source)[0].placements[0].rect,rect):self.assertAlmostEqual(actual,expected)

    def test_noop_does_not_round_imported_geometry_or_replace_snapshot(self):
        field=FormDesign("uid","precise","Precise","text",(FieldPlacement(0,(.123456,.234567,.712345,.345678)),),value="Value")
        source=apply_form_designs(self.source,(field,));dialog=self.dialog(source);dialog._select_field("precise");dialog.accept()
        self.assertIs(dialog.result_source,source)

    def test_invalid_name_keeps_dialog_open_and_cancel_has_no_result(self):
        dialog=self.dialog();dialog.add_field();dialog.name_edit.setText("");dialog.accept()
        self.assertIsNone(dialog.result_source);self.assertTrue(dialog.error.text());self.assertTrue(dialog.isVisible())
        dialog.reject();self.assertIsNone(dialog.result_source);self.assertEqual(self.path.read_bytes(),self.original)

    def test_radio_placement_selector_keeps_geometry_and_visible_selection_together(self):
        dialog=self.dialog();dialog.type_combo.setCurrentIndex(dialog.type_combo.findData("radio"));dialog.add_field()
        dialog.placement_combo.setCurrentIndex(1);self.assertEqual(dialog._placement,1);self.assertEqual(dialog.placement_combo.currentIndex(),1)
        second=dialog._field().placements[1];dialog.geometry[0].setValue(25);self.assertTrue(dialog.apply_properties())
        self.assertAlmostEqual(dialog._field().placements[1].rect[0],.25);self.assertEqual(dialog._field().placements[0].rect[0],.12)


if __name__=="__main__":unittest.main()
