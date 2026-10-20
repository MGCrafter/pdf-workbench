"""Native dialogs and PDF-aligned object overlays, separate from app wiring."""
from dataclasses import replace
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject
from PyQt6.QtCore import QRectF, Qt, QSettings, QByteArray, QBuffer, QIODevice
from PyQt6.QtGui import QColor, QImage, QPainter, QTransform
from PyQt6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from pdf_workbench.annotations import AnnotationError, PageAnnotation, TextStyle
from pdf_workbench.core import Workspace, export_pdf, load_source
from pdf_workbench.ui.annotation_overlay import draw_annotations, source_page_geometry, validate_text_object
from pdf_workbench.ui.object_dialogs import TextObjectDialog, ImageObjectDialog, SymbolObjectDialog
from pdf_workbench.ui.theme import ThemeController
from test_annotation_objects import image_bytes


class ObjectOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def dialog(self, cls, *args, **kwargs):
        result = cls(*args,**kwargs)
        self.dialogs.append(result)
        result.show()
        self.app.processEvents()
        return result

    def source(self, rotation=0, unit=1):
        writer = PdfWriter()
        page = writer.add_blank_page(width=340,height=460)
        page.cropbox.lower_left=(30,40)
        page.cropbox.upper_right=(310,420)
        page[NameObject("/UserUnit")]=NumberObject(unit)
        if rotation:
            page.rotate(rotation)
        path=self.directory/f"source-{rotation}-{unit}.pdf"
        writer.write(path)
        return load_source(path)

    def render(self, path):
        document=QPdfDocument(None)
        self.assertEqual(document.load(str(path)),QPdfDocument.Error.None_)
        points=document.pagePointSize(0)
        options=QPdfDocumentRenderOptions()
        options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
        image=document.render(0,points.toSize(),options)
        document.close()
        opaque=QImage(image.size(),QImage.Format.Format_RGB32)
        opaque.fill(QColor("white"))
        painter=QPainter(opaque);painter.drawImage(0,0,image);painter.end()
        return opaque

    def test_text_dialog_validates_unicode_and_fit_then_preserves_unchanged_model(self):
        annotation=PageAnnotation("t","text",(.1,.1,.9,.5),"  ÄÖÜ äöü ß €\nZweite Zeile",style=TextStyle(13.33333333,(.12345678,.2,.7654321),"right"),content_rotation=270)
        dialog=self.dialog(TextObjectDialog,annotation=annotation,rotation=90,point_size=(380,280))
        self.assertEqual(dialog.rotation_box.currentData(),0)
        self.assertEqual(dialog.annotation(),annotation)
        self.assertTrue(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
        dialog.editor.setPlainText("日本語")
        self.assertFalse(dialog.validate())
        self.assertIn("U+65E5",dialog.validation.text())
        self.assertFalse(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
        dialog.accept()
        self.assertNotEqual(dialog.result(),QDialog.DialogCode.Accepted)
        dialog.editor.setPlainText("ÄÖÜ €")
        dialog.font_size.setValue(18)
        dialog.set_color(QColor("#aa2244"))
        dialog.alignment.setCurrentIndex(dialog.alignment.findData("center"))
        self.assertTrue(dialog.validate())
        candidate=dialog.annotation()
        self.assertEqual(candidate.uid,annotation.uid)
        self.assertEqual(candidate.style.font_size,18)
        self.assertEqual(candidate.style.alignment,"center")
        dialog.accept()
        self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)

    def test_new_objects_are_upright_in_a_rotated_view_and_cancel_does_not_mutate(self):
        dialog=self.dialog(TextObjectDialog,rotation=90,point_size=(380,280))
        dialog.editor.setPlainText("Hallo €")
        self.assertEqual(dialog.rotation_box.currentData(),0)
        self.assertEqual(dialog.annotation().content_rotation,270)
        dialog.reject()
        self.assertEqual(dialog.result(),QDialog.DialogCode.Rejected)
        image=self.dialog(ImageObjectDialog,rotation=270)
        self.assertFalse(image.validate())
        asset=image.load_bytes(image_bytes())
        self.assertTrue(image.validate())
        self.assertEqual(image.annotation().content_rotation,90)
        self.assertIs(image.annotation().asset,asset)
        self.assertIn("40 × 24",image.image_info.text())
        self.assertIn("Transparenz",image.image_info.text())
        original=image.annotation()
        image.description.setText("Mein Stempel")
        image.kind_box.setCurrentIndex(image.kind_box.findData("stamp"))
        self.assertEqual(image.annotation().kind,"stamp")
        self.assertEqual(original.kind,"image")
        self.assertEqual(original.text,"")

    def test_new_image_and_rotation_fit_pixel_aspect_but_noop_freeform_is_exact(self):
        for rotation in (0,90,180,270):
            points=(300,400) if rotation%180==0 else (400,300)
            dialog=self.dialog(ImageObjectDialog,rotation=rotation,point_size=points,rect=(.1,.1,.8,.8))
            dialog.load_bytes(image_bytes())
            result=dialog.annotation()
            from pdf_workbench.ui.annotation_overlay import map_rect
            bounds=map_rect(result.rect,QRectF(0,0,*points),rotation)
            self.assertAlmostEqual(bounds.width()/bounds.height(),40/24)
            dialog.rotation_box.setCurrentIndex(dialog.rotation_box.findData(90))
            rotated=dialog.annotation()
            bounds=map_rect(rotated.rect,QRectF(0,0,*points),rotation)
            self.assertAlmostEqual(bounds.width()/bounds.height(),24/40)
            self.assertGreaterEqual(min(rotated.rect),.1-1e-12)
            self.assertLessEqual(max(rotated.rect),.8+1e-12)
        original=PageAnnotation("free","image",(.1,.1,.8,.8),asset=dialog.asset)
        unchanged=self.dialog(ImageObjectDialog,annotation=original,point_size=(300,400))
        self.assertEqual(unchanged.annotation(),original)
        replacement=QImage.fromData(image_bytes()).transformed(QTransform().rotate(90))
        encoded=QByteArray();buffer=QBuffer(encoded);buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.assertTrue(replacement.save(buffer,"PNG"))
        unchanged.load_bytes(bytes(encoded))
        changed=unchanged.annotation()
        self.assertNotEqual(changed.asset.sha256,original.asset.sha256)
        width=(changed.rect[2]-changed.rect[0])*300
        height=(changed.rect[3]-changed.rect[1])*400
        self.assertAlmostEqual(width/height,24/40)

    def test_symbol_dialog_has_keyboard_access_and_edit_keeps_metadata(self):
        original=PageAnnotation("s","cross",(.1,.1,.3,.3),"Beschriftung",style=TextStyle(17,(.2,.4,.6),"right"))
        dialog=self.dialog(SymbolObjectDialog,annotation=original)
        self.assertEqual(dialog.annotation(),original)
        self.assertEqual(dialog.kind_box.accessibleName(),"Häkchen oder Kreuz")
        dialog.kind_box.setFocus()
        QTest.keyClick(dialog.kind_box,Qt.Key.Key_Home)
        self.assertEqual(dialog.annotation().kind,"checkmark")
        self.assertEqual(dialog.annotation().text,"Beschriftung")
        QTest.keyClick(dialog,Qt.Key.Key_Escape)
        self.assertEqual(dialog.result(),QDialog.DialogCode.Rejected)

    def test_source_geometry_is_crop_rotation_unit_correct_and_cached_without_pdf_bytes(self):
        source=self.source(rotation=90,unit=2)
        workspace=Workspace();workspace.add_sources([source])
        page=workspace.pages[0]
        points,unit=source_page_geometry(source,page)
        self.assertEqual(points,(380,280))
        self.assertEqual(unit,2)
        with mock.patch("pypdf.PdfReader",side_effect=AssertionError("reparsed immutable source")):
            self.assertEqual(source_page_geometry(source,replace(page,rotation=90)),((280,380),2))

    def test_all_sixteen_overlay_positions_match_exported_pdf(self):
        from pdf_workbench.annotations import load_image_asset
        asset=load_image_asset(image_bytes())
        for intrinsic in (0,90,180,270):
            for rotation in (0,90,180,270):
                with self.subTest(intrinsic=intrinsic,rotation=rotation):
                    source=self.source(rotation=intrinsic)
                    workspace=Workspace();workspace.add_sources([source])
                    models=(PageAnnotation("t","text",(.1,.08,.9,.38),"ÄÖÜ äöü ß €\n  eingerückt",style=TextStyle(13,(.1,.2,.7),"center")),
                        PageAnnotation("i","image",(.1,.45,.5,.7),asset=asset,content_rotation=90),
                        PageAnnotation("s","checkmark",(.60,.65,.9,.9),style=TextStyle(color=(0,.5,0))))
                    for model in models:
                        workspace.add_annotation(workspace.pages[0].uid,model)
                    if rotation:
                        workspace.rotate([workspace.pages[0].uid],rotation)
                    page=workspace.pages[0]
                    points,unit=source_page_geometry(source,page)
                    # Qt and overlay both render the visible CropBox at one raw point per pixel.
                    image=QImage(round(points[0]),round(points[1]),QImage.Format.Format_RGB32)
                    image.fill(QColor("white"))
                    painter=QPainter(image)
                    draw_annotations(painter,QRectF(0,0,*points),page,points,unit)
                    painter.end()
                    output=self.directory/f"objects-{intrinsic}-{rotation}.pdf"
                    export_pdf(workspace.sources,workspace.pages,output)
                    exported=self.render(output)
                    self.assertEqual(image.size(),exported.size())
                    # Renderer antialiasing differs; compare ink masks after a 1px neighbourhood.
                    def mask(value):
                        return {(x,y) for y in range(value.height()) for x in range(value.width()) if value.pixelColor(x,y).lightness()<200}
                    expected,actual=mask(exported),mask(image)
                    self.assertGreater(len(actual),500)
                    near={(x+dx,y+dy) for x,y in expected for dx in (-1,0,1) for dy in (-1,0,1)}
                    self.assertLess(len(actual-near),max(10,len(actual)*.01))
                    near={(x+dx,y+dy) for x,y in actual for dx in (-1,0,1) for dy in (-1,0,1)}
                    self.assertLess(len(expected-near),max(10,len(expected)*.01))

    def test_dialogs_remain_valid_and_legible_in_light_and_dark_mode(self):
        original_palette=self.app.palette()
        original_stylesheet=self.app.styleSheet()
        original_controller=getattr(self.app,"_pdf_workbench_theme",None)
        settings=QSettings(str(self.directory/"appearance.ini"),QSettings.Format.IniFormat)
        controller=ThemeController(self.app,settings=settings)
        try:
            for mode in ("light","dark"):
                controller.set_preference(mode)
                text=self.dialog(TextObjectDialog,point_size=(280,380))
                text.editor.setPlainText("ÄÖÜ äöü ß €")
                image=self.dialog(ImageObjectDialog)
                image.load_bytes(image_bytes())
                symbol=self.dialog(SymbolObjectDialog)
                for dialog in (text,image,symbol):
                    self.app.processEvents()
                    self.assertTrue(dialog.validate())
                    self.assertFalse(dialog.grab().isNull())
                    self.assertEqual(dialog.layout().count(),dialog.body_layout.count())
                    self.assertTrue(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
                    dialog.close()
        finally:
            controller.style_hints.colorSchemeChanged.disconnect(controller._system_changed)
            if original_controller is not None:
                self.app._pdf_workbench_theme=original_controller
            else:
                del self.app._pdf_workbench_theme
            self.app.setPalette(original_palette)
            self.app.setStyleSheet(original_stylesheet)
            controller.deleteLater()

    def test_invalid_preview_draft_does_not_corrupt_painter_state(self):
        workspace=Workspace();workspace.add_sources([self.source()])
        workspace.add_annotation(workspace.pages[0].uid,PageAnnotation("t","text",(.1,.1,.11,.11),"too large"))
        image=QImage(280,380,QImage.Format.Format_RGB32);image.fill(QColor("white"))
        painter=QPainter(image)
        before=painter.transform()
        draw_annotations(painter,QRectF(0,0,280,380),workspace.pages[0],(280,380))
        self.assertEqual(painter.transform(),before)
        painter.fillRect(QRectF(0,0,10,10),QColor("red"))
        painter.end()
        self.assertEqual(image.pixelColor(2,2),QColor("red"))


if __name__=="__main__":
    unittest.main()
