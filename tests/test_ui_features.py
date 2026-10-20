"""New object tools, search and image imports through the application UI."""
from dataclasses import replace
import importlib.util
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog
from pypdf import PdfReader
from pdf_workbench.annotations import PageAnnotation, load_image_asset
from pdf_workbench.app import MainWindow
from pdf_workbench.core import Workspace, export_pdf, load_source
from pdf_workbench.image_import import ImagePageOptions

APP = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('ui_features_fixture',ROOT/'packaging/smoke.py')
smoke=importlib.util.module_from_spec(spec);spec.loader.exec_module(smoke)


def wait_for(predicate):
    until=time.monotonic()+20
    while not predicate():
        APP.processEvents();time.sleep(.005)
        if time.monotonic()>until:raise AssertionError('Application operation timed out')
    APP.processEvents()


class UiFeaturesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.path=self.root/'source.pdf';smoke.fixture(self.path,('First page','Second page'))
        self.original=self.path.read_bytes()
        self.window=MainWindow();self.errors=[]
        self.window.show_error=lambda *args:self.errors.append(args)
        self.window.show();self.window.import_files([str(self.path)])
        wait_for(lambda:self.window.job is None and not self.window.preview.image.isNull())

    def tearDown(self):
        if self.window.job:self.window.cancel_job();wait_for(lambda:self.window.job is None)
        self.window.saved_state=tuple(self.window.workspace.pages);self.window.close()
        wait_for(lambda:self.window.render_broker.is_closed and self.window.search_bar.is_closed)
        self.assertEqual(self.path.read_bytes(),self.original)
        self.assertEqual(self.errors,[]);self.temp.cleanup()

    def add_text(self):
        def enter_text(dialog):
            dialog.editor.setPlainText('  Workbench Text\nUnicode: Grüße €')
            dialog.font_size.setValue(12)
            dialog.accept()
            return QDialog.DialogCode.Accepted
        self.window.set_annotation_tool('text')
        p=self.window.preview
        with patch('pdf_workbench.app.TextObjectDialog.exec',enter_text):
            p.place_annotation(p.basis_to_canvas((.1,.1)),p.basis_to_canvas((.9,.4)))
        self.assertEqual(len(p.page.annotations),1)
        self.assertEqual(p.page.annotations[0].kind,'text')
        self.assertEqual(p.tool,'select')
        self.assertEqual(p.selected_annotation.uid,p.page.annotations[0].uid)
        self.assertTrue(self.window.workspace.can_undo)
        return p.selected_annotation

    def test_create_edit_nudge_duplicate_delete_and_undo_preserve_the_page(self):
        annotation=self.add_text();w=self.window;p=w.preview
        p.canvas.setFocus();APP.processEvents()
        before=tuple(w.workspace.pages)
        QTest.keyClick(p.canvas,Qt.Key.Key_Right)
        self.assertGreater(p.selected_annotation.rect[0],annotation.rect[0])
        w.undo();self.assertEqual(tuple(w.workspace.pages),before)
        p.set_selected_annotation(annotation.uid);p.canvas.setFocus()
        QTest.keyClick(p.canvas,Qt.Key.Key_D,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(w.workspace.pages),2);self.assertEqual(len(p.page.annotations),2)
        duplicate=p.selected_annotation
        self.assertNotEqual(duplicate.uid,annotation.uid)
        QTest.keyClick(p.canvas,Qt.Key.Key_Delete)
        self.assertEqual(len(w.workspace.pages),2);self.assertEqual(len(p.page.annotations),1)
        w.undo();self.assertEqual(len(p.page.annotations),2)
        p.set_selected_annotation(annotation.uid)
        with patch('pdf_workbench.app.TextObjectDialog.get_annotation',return_value=replace(annotation,text='Edited text')):
            QTest.keyClick(p.canvas,Qt.Key.Key_F2)
        self.assertEqual(p.selected_annotation.text,'Edited text')

    def test_export_reopen_all_object_kinds_remain_editable_and_embedded(self):
        self.add_text();w=self.window;p=w.preview
        image=QImage(100,50,QImage.Format.Format_ARGB32);image.fill(QColor(30,160,80,128))
        data=QByteArray();buffer=QBuffer(data);buffer.open(QIODevice.OpenModeFlag.WriteOnly);image.save(buffer,'PNG');buffer.close()
        asset=load_image_asset(bytes(data))
        for index,kind in enumerate(('image','stamp','checkmark','cross')):
            values={'asset':asset} if kind in ('image','stamp') else {}
            w.add_annotation(p.page.uid,PageAnnotation(kind,kind,(.1,.45+index*.1,.4,.53+index*.1),**values))
        output=self.root/'objects.pdf';export_pdf(w.workspace.sources,w.workspace.pages,output)
        reader=PdfReader(output)
        self.assertEqual(len(reader.pages[0]['/Annots']),5)
        text=reader.pages[0]['/Annots'][0].get_object()
        font=next(iter(text['/AP']['/N']['/Resources']['/Font'].values())).get_object()
        self.assertIn('/ToUnicode',font)
        source=load_source(output);self.assertEqual([a.kind for a in source.page_annotations[0]],['text','image','stamp','checkmark','cross'])
        w.workspace=Workspace();w.workspace.add_sources([source]);w.refresh()
        self.assertEqual(len(w.preview.page.annotations),5)
        self.assertIn('Grüße',w.preview.page.annotations[0].text)
        w.preview.set_selected_annotation(w.preview.page.annotations[-1].uid);w.object_action('delete')
        self.assertEqual(len(w.preview.page.annotations),4)
        w.undo();self.assertEqual(len(w.preview.page.annotations),5)

    def test_search_navigates_without_changing_export_selection_and_input_shortcuts_do_not_delete_pages(self):
        annotation=self.add_text();w=self.window
        selected=w.selected_ids();before=tuple(w.workspace.pages)
        w.search_bar.open();w.search_bar.editor.setText('Second')
        wait_for(lambda:w.search_bar.worker is None and len(w.search_bar.results.hits)==1)
        self.assertEqual(w.preview.page.uid,w.workspace.pages[1].uid)
        self.assertEqual(w.selected_ids(),selected)
        w.search_bar.editor.setText('Workbench')
        wait_for(lambda:w.search_bar.worker is None and len(w.search_bar.results.hits)==1)
        self.assertEqual(w.preview.search_hit.annotation_uid,annotation.uid)
        w.search_bar.editor.setFocus();w.delete_action.trigger();w.duplicate_action.trigger()
        self.assertEqual(tuple(w.workspace.pages),before)
        w.search_bar.dismiss();self.assertIsNone(w.preview.search_hit)

    def test_image_dialog_imports_multiple_pages_in_one_undo_step(self):
        w=self.window;paths=[]
        for index in range(2):
            image=QImage(240,120,QImage.Format.Format_RGB32);image.fill(QColor('green'))
            path=self.root/f'photo{index}.png';image.save(str(path));paths.append(str(path))
        before=tuple(w.workspace.pages)
        with patch('pdf_workbench.app.ImagePagesDialog.get_request',return_value=(paths,ImagePageOptions('Letter','landscape',5))):w.import_images()
        wait_for(lambda:w.job is None)
        self.assertEqual(len(w.workspace.pages),4)
        for page in w.workspace.pages[2:]:
            source=w.workspace.sources[page.source_id]
            self.assertEqual(source.page_count,1)
            self.assertGreater(source.page_sizes[0][0],source.page_sizes[0][1])
        w.page_list.setFocus();w.undo();self.assertEqual(tuple(w.workspace.pages),before)
