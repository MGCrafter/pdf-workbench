"""Real Qt PDF preview, pointer/keyboard controls and compact geometry dialogs."""
import os
from pathlib import Path
import tempfile
import time
import unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pypdf import PdfReader,PdfWriter
from pypdf.generic import DecodedStreamObject,NameObject
from PyQt6.QtCore import QPoint,Qt,QCoreApplication,QEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication,QDialog
from pdf_workbench.core import Workspace,export_pdf,load_source
from pdf_workbench.page_geometry import apply_page_geometry
from pdf_workbench.ui.page_geometry_dialog import PageGeometryDialog,PT_PER_MM
from pdf_workbench.ui.theme import stylesheet
APP=QApplication.instance() or QApplication([])

def wait_for(predicate,timeout=15):
    until=time.monotonic()+timeout
    while not predicate():
        APP.processEvents();time.sleep(.005)
        if time.monotonic()>until:raise AssertionError('Geometry preview did not finish')
    APP.processEvents()

class GeometryDialogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'original.pdf';w=PdfWriter()
        for _ in range(2):
            page=w.add_blank_page(width=400,height=500);s=DecodedStreamObject();s.set_data(b'0.1 0.7 0.2 rg 100 200 100 120 re f');page[NameObject('/Contents')]=w._add_object(s)
        w.write(self.path);self.original=self.path.read_bytes();self.workspace=Workspace();self.workspace.add_sources([load_source(self.path)]);self.dialogs=[]
    def tearDown(self):
        for d in self.dialogs:d.reject();wait_for(lambda:d.worker is None);d.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents();self.assertEqual(self.path.read_bytes(),self.original);self.temp.cleanup()
    def dialog(self,mode='crop',selected=()):
        d=PageGeometryDialog(None,self.workspace,selected,mode=mode);self.dialogs.append(d);d.show();APP.processEvents();return d
    def ready(self,d):
        wait_for(lambda:d.worker is None and not d.timer.isActive() and (d._ready_request is not None or bool(d.error.text())))
        self.assertEqual(d.error.text(),'');self.assertTrue(d.apply_button.isEnabled());self.assertFalse(d.preview.image.isNull());self.assertFalse(d.preview.result_image.isNull())
    def test_real_drag_numeric_keyboard_and_result_export_match(self):
        d=self.dialog(selected=(self.workspace.pages[0].uid,));self.ready(d)
        paper=d.preview.paper_rect();start=paper.topLeft().toPoint();end=(paper.topLeft()+__import__('PyQt6.QtCore',fromlist=['QPointF']).QPointF(paper.width()*.1,paper.height()*.15)).toPoint()
        QTest.mousePress(d.preview,Qt.MouseButton.LeftButton,pos=start);QTest.mouseMove(d.preview,end);QTest.mouseRelease(d.preview,Qt.MouseButton.LeftButton,pos=end)
        self.ready(d);self.assertGreater(d.margin_controls[0].value(),10);self.assertGreater(d.margin_controls[1].value(),15)
        d.margin_controls[2].setValue(5);self.ready(d);before=d.margin_controls[0].value();QTest.keyClick(d.preview,Qt.Key.Key_Right);self.ready(d);self.assertGreater(d.margin_controls[0].value(),before)
        d.result_toggle.click();self.assertTrue(d.preview.show_result)
        d.apply_button.click();self.assertEqual(d.result(),QDialog.DialogCode.Accepted);apply_page_geometry(self.workspace,d._ready_request)
        out=self.path.with_name('crop.pdf');export_pdf(self.workspace.sources,self.workspace.pages,out);pages=PdfReader(out).pages
        self.assertAlmostEqual(float(pages[0].cropbox.width),400-sum(d.request.options.crop_margins[::2]),places=4);self.assertEqual(float(pages[1].cropbox.width),400)
    def test_A4_letter_custom_range_and_proportional_fit(self):
        d=self.dialog('resize');d.scope.setCurrentIndex(d.scope.findData('range'));d.range_edit.setText('2');d.paper.setCurrentIndex(1);d.landscape.setChecked(True);d.margin.setValue(10);self.ready(d)
        self.assertEqual(d.request.options.target_size,(792.,612.));self.assertEqual(d.request.target_ids,(self.workspace.pages[1].uid,))
        d.paper.setCurrentIndex(2);d.target_width.setValue(180);d.target_height.setValue(240);self.ready(d)
        apply_page_geometry(self.workspace,d.request);out=self.path.with_name('size.pdf');export_pdf(self.workspace.sources,self.workspace.pages,out);p=PdfReader(out).pages[1]
        self.assertAlmostEqual(float(p.mediabox.width),180*PT_PER_MM,places=4);self.assertAlmostEqual(float(p.mediabox.height),240*PT_PER_MM,places=4)
    def test_invalid_settings_disable_stale_preview_and_no_accept(self):
        d=self.dialog();self.ready(d);d.margin_controls[0].setValue(500)
        self.assertFalse(d.apply_button.isEnabled());d.accept();self.assertEqual(d.result(),QDialog.DialogCode.Rejected)
        wait_for(lambda:bool(d.error.text()) and d.worker is None);self.assertIn('keine nutzbare',d.error.text())
        d.reset.click();self.ready(d);self.assertTrue(d.apply_button.isEnabled())
    def test_empty_scope_error_and_rapid_changes_close_worker(self):
        d=self.dialog();d.scope.setCurrentIndex(d.scope.findData('selected'));wait_for(lambda:bool(d.error.text()));self.assertFalse(d.apply_button.isEnabled())
        d.scope.setCurrentIndex(0);wait_for(lambda:d.worker is not None)
        for value in range(10):d.margin_controls[0].setValue(value)
        d.reject();wait_for(lambda:d.worker is None);self.assertFalse(d.isVisible())
    def test_compact_light_dark_labels_focus_and_visible_apply(self):
        for theme in ('light','dark'):
            for mode in ('crop','resize'):
                with self.subTest(theme=theme,mode=mode):
                    d=self.dialog(mode);d.setStyleSheet(stylesheet(theme));d.resize(800,560);self.ready(d)
                    self.assertEqual((d.width(),d.height()),(800,560));self.assertGreaterEqual(d.preview.width(),220);self.assertGreaterEqual(d.preview.height(),170)
                    self.assertTrue(d.rect().contains(d.apply_button.mapTo(d,d.apply_button.rect().center())))
                    self.assertTrue(d.preview.accessibleDescription());self.assertTrue(d.margin_controls[0].accessibleName() if mode=='crop' else d.target_width.accessibleName())
                    d.reject();wait_for(lambda:d.worker is None)
