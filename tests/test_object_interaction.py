"""Qt-delivered selection, drag, resize and keyboard object interaction tests."""
from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from pypdf import PdfWriter
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QAction, QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit

from pdf_workbench.annotations import PageAnnotation, TextStyle, load_image_asset
from pdf_workbench.core import Workspace, load_source
from pdf_workbench.ui.preview import Preview
from pdf_workbench.ui.rendering import RenderBroker
from test_annotation_objects import image_bytes


class ObjectInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def wait(self,predicate):
        deadline=time.monotonic()+10
        while not predicate():
            self.app.processEvents();time.sleep(.003)
            if time.monotonic()>deadline:
                raise AssertionError("Qt object interaction timed out")
        self.app.processEvents()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        writer=PdfWriter();page=writer.add_blank_page(width=340,height=460)
        page.cropbox.lower_left=(30,40);page.cropbox.upper_right=(310,420)
        self.path=self.directory/'source.pdf';writer.write(self.path);self.original=self.path.read_bytes()
        self.workspace=Workspace();self.workspace.add_sources([load_source(self.path)])
        self.source=next(iter(self.workspace.sources.values()))
        self.broker=RenderBroker();self.preview=Preview(self.broker)
        self.preview.resize(640,680);self.preview.show()
        self.updated=[];self.actions=[];self.edits=[];self.placements=[];self.pending=[];self.status=[]
        self.preview.annotation_updated.connect(self.commit)
        self.preview.annotation_action.connect(self.actions.append)
        self.preview.annotation_edit_requested.connect(lambda *args:self.edits.append(args))
        self.preview.object_requested.connect(lambda *args:self.placements.append(args))
        self.preview.selection_pending_changed.connect(self.pending.append)
        self.preview.status_message.connect(self.status.append)
        self.refresh()

    def tearDown(self):
        self.preview.close();self.broker.shutdown();self.wait(lambda:self.broker.is_closed)
        self.assertEqual(self.path.read_bytes(),self.original)
        self.temp.cleanup()

    def refresh(self):
        self.preview.show_page(self.source,self.workspace.pages[0])
        self.wait(lambda:not self.preview.image.isNull() and not self.preview._timer.isActive() and self.broker.pending_count==0)

    def commit(self,page_uid,annotation):
        self.updated.append(annotation)
        self.workspace.update_annotation(page_uid,annotation)
        self.preview.show_page(self.source,self.workspace.pages[0])

    def add(self,annotation):
        self.workspace.add_annotation(self.workspace.pages[0].uid,annotation);self.refresh()
        self.preview.set_tool('select')
        return annotation

    def point(self,x,y):
        return self.preview.basis_to_canvas((x,y))

    def mouse(self,kind,point,button,buttons,modifiers=Qt.KeyboardModifier.NoModifier):
        canvas=self.preview.canvas
        event=QMouseEvent(kind,point,QPointF(canvas.mapToGlobal(point.toPoint())),button,buttons,modifiers)
        QApplication.sendEvent(canvas,event);self.app.processEvents()

    def press(self,point,modifiers=Qt.KeyboardModifier.NoModifier):
        self.mouse(QEvent.Type.MouseButtonPress,point,Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,modifiers)

    def move(self,point,modifiers=Qt.KeyboardModifier.NoModifier):
        self.mouse(QEvent.Type.MouseMove,point,Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,modifiers)

    def release(self,point,modifiers=Qt.KeyboardModifier.NoModifier):
        self.mouse(QEvent.Type.MouseButtonRelease,point,Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton,modifiers)

    def test_move_is_ephemeral_then_one_commit_one_undo_at_each_rotation(self):
        original=self.add(PageAnnotation('object','text',(.2,.2,.7,.4),'ÄÖÜ äöü ß €',style=TextStyle(12)))
        for rotation in (0,90,180,270):
            with self.subTest(rotation=rotation):
                self.workspace.pages[0]=replace(self.workspace.pages[0],rotation=rotation,annotations=(original,))
                self.refresh();self.preview.set_tool('select')
                before=len(self.workspace._undo);count=len(self.updated)
                start,end=self.point(.45,.3),self.point(.55,.4)
                self.press(start);self.assertEqual(self.preview.selected_annotation_uid,'object')
                self.assertTrue(self.preview.selection_pending)
                for step in (1,2,3):
                    self.move(start+(end-start)*(step/3))
                    self.assertEqual(self.workspace.pages[0].annotations[0],original)
                self.release(end)
                self.assertFalse(self.preview.selection_pending)
                self.assertEqual(len(self.updated),count+1)
                self.assertEqual(len(self.workspace._undo),before+1)
                moved=self.workspace.pages[0].annotations[0]
                for a,b in zip(moved.rect,(.3,.3,.8,.5)):
                    self.assertAlmostEqual(a,b)
                self.workspace.undo();self.assertEqual(self.workspace.pages[0].annotations[0],original)

    def test_escape_zoom_and_page_refresh_abort_drafts_without_commit(self):
        original=self.add(PageAnnotation('s','signature',(.2,.2,.6,.5),strokes=(((0,.5),(1,.5)),)))
        for cancel in ('escape','zoom','refresh'):
            with self.subTest(cancel=cancel):
                self.preview.set_tool('select');start,end=self.point(.4,.35),self.point(.6,.55)
                self.press(start);self.move(end);self.assertIsNotNone(self.preview._draft_annotation)
                if cancel=='escape':QTest.keyClick(self.preview.canvas,Qt.Key.Key_Escape)
                elif cancel=='zoom':self.preview.set_zoom(1.1)
                else:self.preview.show_page(self.source,self.workspace.pages[0])
                self.release(end)
                self.assertEqual(self.updated,[])
                self.assertFalse(self.preview.selection_pending)
                self.assertEqual(self.workspace.pages[0].annotations[0],original)
                self.assertIsNone(self.preview._draft_annotation)
                self.refresh()

    def test_resize_image_keeps_ratio_unless_shift_and_clamps_to_page(self):
        original=self.add(PageAnnotation('i','image',(.2,.2,.6,.5),asset=load_image_asset(image_bytes())))
        self.preview.set_selected_annotation('i')
        handle=self.preview.object_handles()['se'].center()
        self.press(handle);end=self.point(.8,.52);self.move(end);self.release(end)
        current=self.workspace.pages[0].annotations[0]
        self.assertAlmostEqual((current.rect[2]-current.rect[0])/(current.rect[3]-current.rect[1]),4/3)
        self.workspace.undo();self.refresh();self.preview.set_selected_annotation('i')
        handle=self.preview.object_handles()['se'].center()
        self.press(handle,Qt.KeyboardModifier.ShiftModifier);self.move(end,Qt.KeyboardModifier.ShiftModifier);self.release(end,Qt.KeyboardModifier.ShiftModifier)
        current=self.workspace.pages[0].annotations[0]
        self.assertNotAlmostEqual((current.rect[2]-current.rect[0])/(current.rect[3]-current.rect[1]),4/3)
        center=self.point((current.rect[0]+current.rect[2])/2,(current.rect[1]+current.rect[3])/2)
        self.press(center);outside=self.preview.paper_rect().bottomRight()+QPointF(1000,1000)
        self.move(outside);self.release(outside)
        self.assertLessEqual(max(self.workspace.pages[0].annotations[0].rect),1)

    def test_highlight_resize_transforms_all_quads_and_signature_strokes_stay_local(self):
        original=self.add(PageAnnotation('h','highlight',(.2,.2,.6,.4),'two lines',rects=((.2,.2,.6,.25),(.2,.35,.4,.4))))
        self.preview.set_selected_annotation('h')
        self.press(self.preview.object_handles()['se'].center());end=self.point(.8,.6);self.move(end);self.release(end)
        resized=self.workspace.pages[0].annotations[0]
        self.assertEqual(len(resized.rects),2)
        for actual,expected in zip(resized.rects,((.2,.2,.8,.3),(.2,.5,.5,.6))):
            for a,b in zip(actual,expected):self.assertAlmostEqual(a,b)
        self.assertEqual(resized.text,original.text)

    def test_text_overflow_resize_rejected_and_signal_keyboard_shortcuts_are_scoped(self):
        original=self.add(PageAnnotation('t','text',(.2,.2,.8,.5),'ÄÖÜ äöü ß €\nZweite Zeile'))
        self.preview.set_selected_annotation('t')
        self.press(self.preview.object_handles()['se'].center());end=self.point(.21,.21);self.move(end)
        self.assertTrue(self.preview._draft_error)
        self.release(end);self.assertEqual(self.updated,[])
        self.assertEqual(self.workspace.pages[0].annotations[0],original)
        self.assertTrue(self.status)
        page_deletes=[];action=QAction('page delete',self.preview);action.setShortcut(Qt.Key.Key_Delete);action.triggered.connect(lambda:page_deletes.append(True));self.preview.addAction(action)
        self.preview.canvas.setFocus();QTest.keyClick(self.preview.canvas,Qt.Key.Key_Delete)
        self.assertEqual(self.actions,['delete']);self.assertEqual(page_deletes,[])
        QTest.keyClick(self.preview.canvas,Qt.Key.Key_D,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.actions,['delete','duplicate'])
        QTest.keyClick(self.preview.canvas,Qt.Key.Key_F2);self.assertEqual(len(self.edits),1)
        count=len(self.updated);QTest.keyClick(self.preview.canvas,Qt.Key.Key_Right)
        self.assertEqual(len(self.updated),count+1)
        self.assertAlmostEqual(self.workspace.pages[0].annotations[0].rect[0],original.rect[0]+1/280)
        editor=QLineEdit('abc');self.preview.layout().addWidget(editor);editor.show();editor.setFocus();editor.setSelection(1,1)
        QTest.keyClick(editor,Qt.Key.Key_Delete)
        self.assertEqual(editor.text(),'ac');self.assertEqual(self.actions,['delete','duplicate']);self.assertEqual(page_deletes,[])

    def test_all_types_hit_test_doubleclick_and_noop_drag(self):
        asset=load_image_asset(image_bytes())
        definitions=(('comment',(.05,.05,.25,.2),dict(text='Kommentar')),
            ('highlight',(.3,.05,.5,.2),dict(text='Highlight',rects=((.3,.05,.5,.2),))),
            ('signature',(.55,.05,.75,.2),dict(strokes=(((0,.5),(1,.5)),))),
            ('text',(.05,.3,.25,.5),dict(text='€')),
            ('image',(.3,.3,.5,.5),dict(asset=asset)),
            ('stamp',(.55,.3,.75,.5),dict(asset=asset)),
            ('checkmark',(.05,.6,.25,.8),{}),('cross',(.3,.6,.5,.8),{}))
        for kind,rect,kwargs in definitions:
            self.workspace.add_annotation(self.workspace.pages[0].uid,PageAnnotation(kind,kind,rect,**kwargs))
        self.refresh();self.preview.set_tool('select')
        for kind,rect,_ in definitions:
            center=self.point((rect[0]+rect[2])/2,(rect[1]+rect[3])/2)
            self.assertEqual(self.preview.hit_test_annotation(center).uid,kind)
            self.press(center);self.move(center);self.release(center)
            self.assertEqual(self.preview.selected_annotation_uid,kind)
        self.assertEqual(self.updated,[])
        QTest.mouseDClick(self.preview.canvas,Qt.MouseButton.LeftButton,pos=center.toPoint())
        self.assertEqual(self.edits[-1][1].kind,'cross')

    def test_each_new_tool_emits_placement_request_without_mutating_workspace(self):
        initial=tuple(self.workspace.pages)
        for kind in ('text','image','stamp','checkmark','cross'):
            self.preview.set_tool(kind);start,end=self.point(.1,.1),self.point(.5,.3)
            self.press(start);self.move(end);self.release(end)
            self.assertEqual(self.placements[-1][1],kind)
            for a,b in zip(self.placements[-1][2],(.1,.1,.5,.3)):self.assertAlmostEqual(a,b)
            self.assertEqual(tuple(self.workspace.pages),initial)


if __name__=='__main__':unittest.main()
