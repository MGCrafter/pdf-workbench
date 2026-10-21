"""Qt-only Widget flattening agrees with interactive Poppler rendering."""
from dataclasses import replace
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from PIL import Image, ImageChops
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, FloatObject, NameObject, NumberObject
from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QSize
from PyQt6.QtGui import QImage, QTransform
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtWidgets import QApplication
from pdf_workbench.core import OperationCancelled, PdfWorkbenchError, Workspace, export_pdf, load_source
from pdf_workbench.form_design import FieldPlacement, FormDesign, apply_form_designs
from pdf_workbench.form_rendering import form_render_data
from pdf_workbench.forms import _widgets, fill_source_form, form_fields


def qt_image(data,extra_rotation=0):
    buffer=QBuffer();buffer.setData(QByteArray(data));buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    document=QPdfDocument(None);document.load(buffer);points=document.pagePointSize(0);factor=500/max(points.width(),points.height())
    image=document.render(0,QSize(round(points.width()*factor),round(points.height()*factor)))
    text=document.getAllText(0).text();document.close();buffer.close()
    if extra_rotation:image=image.transformed(QTransform().rotate(extra_rotation))
    image=image.convertToFormat(QImage.Format.Format_RGBA8888)
    rgba=Image.frombytes("RGBA",(image.width(),image.height()),image.constBits().asstring(image.sizeInBytes()),"raw","RGBA",image.bytesPerLine())
    return Image.alpha_composite(Image.new("RGBA",rgba.size,"white"),rgba).convert("RGB"),text


class FormRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def source(self,rotation=0):
        writer=PdfWriter();page=writer.add_blank_page(width=500,height=650)
        page[NameObject("/CropBox")]=ArrayObject(FloatObject(n) for n in (40,80,440,580));page.rotate(rotation)
        path=self.root/f"source-{rotation}.pdf";writer.write(path);base=load_source(path)
        designs=(FormDesign("t","name","Name","text",(FieldPlacement(0,(.1,.15,.8,.25)),),value="ORIGINAL"),
            FormDesign("r","plan","Plan","radio",(FieldPlacement(0,(.1,.4,.8,.47),"one"),FieldPlacement(0,(.1,.55,.8,.62),"two")),
                       (("one","Option One"),("two","Option Two")),"two",tab_order=1),
            FormDesign("c","check","Enabled","checkbox",(FieldPlacement(0,(.1,.72,.8,.79)),),value=True,tab_order=2))
        return fill_source_form(apply_form_designs(base,designs),{"name":"REFILLED VALUE","plan":"one"})

    @unittest.skipUnless(shutil.which("pdftoppm"),"Poppler not installed")
    def test_qt_and_poppler_all_intrinsic_workspace_rotations_and_crop(self):
        for intrinsic in (0,90,180,270):
            source=self.source(intrinsic);original=source.data;render=form_render_data(source)
            self.assertEqual(source.data,original);self.assertEqual(len(form_fields(source)),3)
            for workspace_rotation in (0,90,180,270):
                with self.subTest(intrinsic=intrinsic,workspace=workspace_rotation):
                    qt,text=qt_image(render,workspace_rotation);self.assertIn("REFILLED VALUE",text)
                    workspace=Workspace();workspace.add_sources([source]);workspace.rotate([workspace.pages[0].uid],workspace_rotation)
                    pdf=self.root/f"view-{intrinsic}-{workspace_rotation}.pdf";export_pdf(workspace.sources,workspace.pages,pdf)
                    subprocess.run(["pdftoppm","-singlefile","-cropbox","-scale-to","500","-png",str(pdf),str(pdf.with_suffix(""))],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                    poppler=Image.open(pdf.with_suffix(".png")).convert("RGB");self.assertEqual(qt.size,poppler.size)
                    def ink(image):return image.convert("L").point(lambda x:255 if x<180 else 0)
                    first,second=ink(qt),ink(poppler);union=ImageChops.lighter(first,second).histogram()[255]
                    overlap=ImageChops.darker(first,second).histogram()[255]
                    self.assertGreater(overlap/union,.65)
                    self.assertEqual(set(PdfReader(pdf).get_fields()),{"name","plan","check"})

    def test_hidden_noview_states_cancel_and_original_protection(self):
        source=self.source();writer=PdfWriter(clone_from=io.BytesIO(source.data));widgets=list(_widgets(writer.pages[0]))
        widgets[0][NameObject("/F")]=NumberObject(32);widgets[-1][NameObject("/F")]=NumberObject(2)
        stream=io.BytesIO();writer.write(stream);source=replace(source,data=stream.getvalue());original=source.data
        data=form_render_data(source);image,text=qt_image(data)
        self.assertNotIn("REFILLED VALUE",text);self.assertNotIn("Enabled",text);self.assertIn("Option One",text)
        reader=PdfReader(io.BytesIO(data));self.assertNotIn("/AcroForm",reader.trailer["/Root"]);self.assertEqual(list(_widgets(reader.pages[0])),[])
        self.assertEqual(source.data,original);self.assertEqual(len(form_fields(source)),3)
        with self.assertRaises(OperationCancelled):form_render_data(source,lambda:True)

    def test_original_content_final_transform_and_clip_cannot_hide_widgets(self):
        source=self.source();writer=PdfWriter(clone_from=io.BytesIO(source.data))
        content=DecodedStreamObject();content.set_data(b"0.3 0 0 0.3 0 0 cm 0 0 10 10 re W n\n")
        writer.pages[0][NameObject("/Contents")]=writer._add_object(content)
        stream=io.BytesIO();writer.write(stream);clipped=replace(source,data=stream.getvalue())
        normal,text=qt_image(form_render_data(source));rendered,rendered_text=qt_image(form_render_data(clipped))
        self.assertIn("REFILLED VALUE",rendered_text);self.assertEqual(rendered.tobytes(),normal.tobytes())

    def test_special_viewport_widgets_explicitly_rejected_before_a_misleading_preview(self):
        for flag in (8,16):
            with self.subTest(flag=flag):
                source=self.source(90);writer=PdfWriter(clone_from=io.BytesIO(source.data))
                next(_widgets(writer.pages[0]))[NameObject("/F")]=NumberObject(4|flag)
                path=self.root/f"unsupported-{flag}.pdf";writer.write(path)
                with self.assertRaisesRegex(PdfWorkbenchError,"NoZoom.*NoRotate"):load_source(path)
                stream=io.BytesIO();writer.write(stream)
                with self.assertRaisesRegex(PdfWorkbenchError,"NoZoom.*NoRotate"):form_render_data(replace(source,data=stream.getvalue()))


if __name__=="__main__":unittest.main()
