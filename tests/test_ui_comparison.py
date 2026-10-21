"""Real dialog interactions, asynchronous rendering and close/cancel safety."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QSettings, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from pypdf import PdfWriter

from pdf_workbench.annotations import PageAnnotation
from pdf_workbench.core import OperationCancelled, Workspace, export_pdf, load_source
from pdf_workbench.ui.compare_dialog import ComparisonDialog
from pdf_workbench.ui.motion import MotionController
from pdf_workbench.ui.theme import theme_controller
from test_comparison import comparison_pdf

APP=QApplication.instance() or QApplication([])


def wait_for(predicate,timeout=15):
    deadline=time.monotonic()+timeout
    while not predicate():
        APP.processEvents();time.sleep(.005)
        if time.monotonic()>deadline:raise AssertionError('Comparison UI timed out')
    APP.processEvents()


class ComparisonUiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.left=comparison_pdf(self.root/'before.pdf',['First anchor','Price 125 Euro','Last anchor'])
        self.right=comparison_pdf(self.root/'after.pdf',['Inserted','First anchor','Price 175 Euro','Last anchor'])
        self.originals={source.path:source.data for source in (self.left,self.right)}
        self.dialogs=[]
        self.theme=theme_controller();self.old_theme=self.theme.preference
        self.motion=MotionController(None,QSettings(str(self.root/'settings.ini'),QSettings.Format.IniFormat));self.motion.set_reduced(True)

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.reject();wait_for(lambda:dialog.is_closed)
        self.theme.set_preference(self.old_theme)
        for path,data in self.originals.items():self.assertEqual(path.read_bytes(),data)
        self.temp.cleanup()

    def dialog(self,ready=True):
        dialog=ComparisonDialog(self.left,self.right,motion=self.motion);self.dialogs.append(dialog)
        dialog.resize(800,560);dialog.show()
        if ready:wait_for(lambda:dialog.comparison is not None and dialog.worker is None)
        return dialog

    def test_change_navigation_missing_side_and_real_rendering(self):
        dialog=self.dialog()
        self.assertEqual(dialog.current_pair,0)
        self.assertIsNone(dialog.panes[0].page_index)
        wait_for(lambda:not dialog.panes[1].image.isNull())
        QTest.mouseClick(dialog.next,Qt.MouseButton.LeftButton)
        wait_for(lambda:all(not pane.image.isNull() for pane in dialog.panes))
        self.assertEqual((dialog.panes[0].page_index,dialog.panes[1].page_index),(1,2))
        item=dialog.changes.currentItem();self.assertTrue(item.childCount())
        visual=next(item.child(i) for i in range(item.childCount()) if item.child(i).data(0,Qt.ItemDataRole.UserRole)[1]=='visual')
        dialog.changes.expandItem(item);dialog.changes.scrollToItem(visual);APP.processEvents()
        QTest.mouseClick(dialog.changes.viewport(),Qt.MouseButton.LeftButton,pos=dialog.changes.visualItemRect(visual).center())
        self.assertEqual(dialog.panes[0].active_region,0)
        self.assertIn('Bild/Layout',dialog.detail.toPlainText())
        text=item.child(0);dialog.changes.setCurrentItem(text)
        self.assertIn('125',dialog.detail.toPlainText());self.assertIn('175',dialog.detail.toPlainText())
        self.assertEqual(dialog.summary.graphicsEffect().opacity(),1.)

    def test_keyboard_filter_zoom_and_scroll_stay_synchronized(self):
        dialog=self.dialog();dialog.navigate(1)
        QTest.mouseClick(dialog.only_changes,Qt.MouseButton.LeftButton)
        self.assertFalse(dialog.only_changes.isChecked())
        self.assertEqual(sum(not item.isHidden() for item in dialog._page_items),4)
        dialog.zoom_box.setCurrentIndex(dialog.zoom_box.findData(2.))
        wait_for(lambda:all(pane.scroll.verticalScrollBar().maximum()>0 for pane in dialog.panes))
        self.assertEqual(dialog.panes[0].zoom,dialog.panes[1].zoom)
        a,b=(pane.scroll.verticalScrollBar() for pane in dialog.panes)
        a.setValue(a.maximum()//2);APP.processEvents()
        self.assertAlmostEqual(a.value()/a.maximum(),b.value()/b.maximum(),delta=.01)
        dialog.panes[1].canvas.setFocus();QTest.keyClick(dialog.panes[1].canvas,Qt.Key.Key_Down)
        self.assertAlmostEqual(a.value()/a.maximum(),b.value()/b.maximum(),delta=.01)
        QTest.keyClick(dialog,Qt.Key.Key_F7)
        self.assertEqual(dialog.panes[0].page_index,2)
        self.assertLessEqual(dialog.broker.cache_bytes,dialog.broker.MAX_CACHE_BYTES)

    def test_identical_documents_show_unchanged_pages_instead_of_an_empty_list(self):
        self.right=self.left
        dialog=self.dialog()
        self.assertFalse(dialog.only_changes.isChecked())
        self.assertIn('3 unverändert',dialog.summary.text())
        self.assertEqual(dialog.current_pair,0)
        wait_for(lambda:all(not pane.image.isNull() for pane in dialog.panes))

    def test_cancel_busy_comparison_keeps_event_loop_responsive_and_closes_workers(self):
        entered=threading.Event()
        def busy(left,right,cancel,progress):
            entered.set()
            while not cancel():time.sleep(.005)
            raise OperationCancelled('test cancel')
        with patch('pdf_workbench.ui.compare_dialog.compare_documents',side_effect=busy):
            dialog=self.dialog(ready=False);wait_for(entered.is_set)
            QTest.keyClick(dialog,Qt.Key.Key_Escape)
            wait_for(lambda:dialog.is_closed)
            self.assertIsNone(dialog.worker);self.assertTrue(dialog.broker.is_closed)

    def test_cropped_intrinsic_rotation_preserves_the_rendered_page_aspect_ratio(self):
        writer=PdfWriter();page=writer.add_blank_page(width=600,height=800)
        page.cropbox.lower_left=(100,100);page.cropbox.upper_right=(500,700);page.rotate(90)
        path=self.root/'rotated-cropped.pdf';writer.write(path);self.left=self.right=load_source(path)
        dialog=self.dialog();wait_for(lambda:all(not pane.image.isNull() for pane in dialog.panes))
        for pane in dialog.panes:
            self.assertEqual(pane.points,(600.,400.))
            self.assertAlmostEqual(pane.paper_size[0]/pane.paper_size[1],pane.image.width()/pane.image.height(),delta=.01)

    def test_loaded_own_text_objects_are_visible_in_both_comparison_previews(self):
        workspace=Workspace();workspace.add_sources([self.left]);uid=workspace.pages[0].uid
        sources=[]
        for index,text in enumerate(('Previous object','Updated object')):
            if index:workspace.remove_annotation(uid,'own')
            workspace.add_annotation(uid,PageAnnotation('own','text',(.1,.3,.9,.5),text))
            output=self.root/f'own-{index}.pdf';export_pdf(workspace.sources,workspace.pages,output)
            self.originals[output]=output.read_bytes();sources.append(load_source(output))
        self.left,self.right=sources
        dialog=self.dialog();wait_for(lambda:all(not pane.image.isNull() for pane in dialog.panes))
        self.assertEqual(dialog.comparison.pages[0].status,'changed')
        for pane,original in zip(dialog.panes,sources):
            self.assertNotEqual(pane.source.data,original.data)
            self.assertFalse(any(pane.source.page_annotations))
        a,b=(pane.image for pane in dialog.panes)
        self.assertEqual(a.size(),b.size())
        self.assertNotEqual(a.constBits().asstring(a.sizeInBytes()),b.constBits().asstring(b.sizeInBytes()))

    def test_compact_light_dark_controls_and_panes_remain_visible(self):
        dialog=self.dialog();dialog.navigate(1)
        for theme in ('light','dark'):
            self.theme.set_preference(theme);APP.processEvents()
            wait_for(lambda:all(not pane.image.isNull() for pane in dialog.panes))
            self.assertEqual((dialog.width(),dialog.height()),(800,560))
            for control in (dialog.close_button,dialog.zoom_in,dialog.only_changes):
                point=control.mapTo(dialog,QPoint(0,0))
                self.assertTrue(dialog.rect().contains(point));self.assertTrue(dialog.rect().contains(point+QPoint(control.width()-1,control.height()-1)))
            self.assertTrue(all(pane.scroll.viewport().width()>=150 and pane.scroll.viewport().height()>=180 for pane in dialog.panes))
