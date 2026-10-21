"""Real exports, reopening and independent renderers for visible-page geometry."""
from dataclasses import replace
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PIL import Image
from pypdf import PdfReader,PdfWriter
from pypdf.generic import ArrayObject,DecodedStreamObject,DictionaryObject,FloatObject,NameObject,NumberObject,TextStringObject
from PyQt6.QtCore import QSize,Qt
from PyQt6.QtPdf import QPdfDocument,QPdfDocumentRenderOptions
from PyQt6.QtWidgets import QApplication
from pdf_workbench.annotations import PageAnnotation,rotate_annotation
from pdf_workbench.core import Workspace,PdfWorkbenchError,OperationCancelled,export_pdf,load_source
from pdf_workbench.form_design import FormDesign,FieldPlacement,apply_form_designs
from pdf_workbench.form_rendering import form_render_data
from pdf_workbench.forms import form_fields,fill_source_form
from pdf_workbench.page_geometry import PageGeometryOptions,PageGeometryRequest,prepare_page_geometry,apply_page_geometry,page_display_size
from pdf_workbench.text_appearance import TextStyle


class PageGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.originals={}
    def tearDown(self):
        for path,sha in self.originals.items():self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),sha)
        self.temp.cleanup()
    def fixture(self,rotation=0,extra=0,unit=1,count=1):
        writer=PdfWriter()
        for _ in range(count):
            p=writer.add_blank_page(width=360,height=480);p.cropbox.lower_left=(30,40);p.cropbox.upper_right=(330,440)
            p[NameObject('/UserUnit')]=NumberObject(unit)
            font=writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}))
            p[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
            if rotation:p.rotate(rotation)
            stream=DecodedStreamObject();stream.set_data(b'0.1 0.7 0.2 rg 90 280 60 60 re f\n1 0 0 rg 0 0 20 20 re f\n0 0 0 rg BT /F1 10 Tf 95 300 Td (Probe) Tj ET\n')
            p[NameObject('/Contents')]=writer._add_object(stream)
        path=self.root/f'original-{rotation}-{extra}-{unit}-{count}.pdf';writer.write(path)
        self.originals[path]=hashlib.sha256(path.read_bytes()).hexdigest();ws=Workspace();ws.add_sources([load_source(path)])
        if extra:ws.rotate([p.uid for p in ws.pages],extra)
        return ws,path
    def export(self,ws,name='out.pdf'):
        path=self.root/name;export_pdf(ws.sources,ws.pages,path);return path
    def apply(self,ws,options,ids=None):
        result=prepare_page_geometry(ws.sources,ws.pages,options,ids);ws.apply_page_replacements(result.sources,result.pages);return result
    def qt(self,path):
        doc=QPdfDocument(None);doc.load(str(path));self.assertEqual(doc.status(),QPdfDocument.Status.Ready)
        size=doc.pagePointSize(0).toSize().scaled(QSize(600,600),Qt.AspectRatioMode.KeepAspectRatio)
        opts=QPdfDocumentRenderOptions();opts.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
        image=doc.render(0,size,opts).convertToFormat(__import__('PyQt6.QtGui',fromlist=['QImage']).QImage.Format.Format_RGBA8888)
        data=image.bits();data.setsize(image.sizeInBytes());pil=Image.frombytes('RGBA',(image.width(),image.height()),bytes(data)).convert('RGB');doc.close();return pil
    def poppler(self,path):
        if not shutil.which('pdftoppm'):self.skipTest('Poppler unavailable')
        stem=path.with_suffix('');subprocess.run(['pdftoppm','-singlefile','-cropbox','-scale-to','600','-png',str(path),str(stem)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        return Image.open(stem.with_suffix('.png')).convert('RGB')
    def green_bounds(self,image):
        points=[(x/image.width,y/image.height) for y in range(image.height) for x in range(image.width) if (lambda c:c[1]>c[0]*1.7 and c[1]>c[2]*1.5)(image.getpixel((x,y)))]
        self.assertTrue(points);return min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)
    def test_all_16_rotations_crop_and_resize_match_two_renderers_reopen_models(self):
        for intrinsic in (0,90,180,270):
            for extra in (0,90,180,270):
                for mode in ('crop','resize'):
                    with self.subTest(intrinsic=intrinsic,extra=extra,mode=mode):
                        ws,_=self.fixture(intrinsic,extra,2)
                        note=PageAnnotation('text','text',(.25,.25,.5,.35),'Grüße',style=TextStyle(12),content_rotation=90)
                        ws.add_annotation(ws.pages[0].uid,note)
                        options=PageGeometryOptions(crop_margins=(20,20,20,20)) if mode=='crop' else PageGeometryOptions(target_size=(500,700),margin=20)
                        result=self.apply(ws,options);self.assertEqual(result.flattened_annotation_count,0)
                        out=self.export(ws,f'{intrinsic}-{extra}-{mode}.pdf');reader=PdfReader(out)
                        reopened=load_source(out);self.assertEqual(len(reopened.page_annotations[0]),1)
                        expected=rotate_annotation(ws.pages[0].annotations[0],extra)
                        actual=reopened.page_annotations[0][0]
                        for a,b in zip(actual.rect,expected.rect):self.assertAlmostEqual(a,b,places=6)
                        self.assertEqual(actual.content_rotation,expected.content_rotation)
                        self.assertAlmostEqual(actual.style.font_size,expected.style.font_size)
                        if mode=='resize':
                            self.assertAlmostEqual(float(reader.pages[0].get('/UserUnit',1)),1)
                            self.assertEqual(tuple(round(v,3) for v in page_display_size(ws.sources[ws.pages[0].source_id],ws.pages[0])),(500,700))
                        q,p=self.green_bounds(self.qt(out)),self.green_bounds(self.poppler(out))
                        from pdf_workbench.search import search_workspace
                        hits=search_workspace(ws.sources,ws.pages,'Probe').hits
                        self.assertEqual(len(hits),1)
                        l,t,r,b=hits[0].rectangles[0]
                        def turn(x,y):return (x,y) if extra==0 else ((1-y,x) if extra==90 else ((1-x,1-y) if extra==180 else (y,1-x)))
                        corners=[turn(l,t),turn(r,b)]
                        x1,y1=min(x for x,y in corners),min(y for x,y in corners)
                        x2,y2=max(x for x,y in corners),max(y for x,y in corners)
                        self.assertGreaterEqual(x1,q[0]-.015);self.assertGreaterEqual(y1,q[1]-.015)
                        self.assertLessEqual(x2,q[2]+.015);self.assertLessEqual(y2,q[3]+.015)
                        own=search_workspace(ws.sources,ws.pages,'Grüße').hits
                        self.assertEqual(own[0].annotation_uid,'text');self.assertEqual(own[0].rectangles,(ws.pages[0].annotations[0].rect,))
                        for a,b in zip(q,p):self.assertAlmostEqual(a,b,delta=.012)
    def test_crop_hidden_text_is_preserved_and_cut_objects_become_ordinary_AP(self):
        ws,_=self.fixture();ws.add_annotation(ws.pages[0].uid,PageAnnotation('cut','text',(.01,.1,.3,.2),'Kein Löschen'))
        ws.add_annotation(ws.pages[0].uid,PageAnnotation('hidden','comment',(.01,.01,.04,.04),'Verborgene Notiz'))
        before=ws.pages;result=self.apply(ws,PageGeometryOptions(crop_margins=(60,60,0,0)))
        self.assertEqual(result.flattened_annotation_count,2);self.assertTrue(result.warnings);self.assertEqual(ws.pages[0].annotations,())
        out=self.export(ws);reader=PdfReader(out);self.assertEqual(len(reader.pages[0]['/Annots']),2)
        for ref in reader.pages[0]['/Annots']:self.assertNotIn('/PDFWorkbench',ref.get_object())
        self.assertIn('Kein Löschen',str(reader.pages[0]['/Annots'][0].get_object()['/Contents']))
        self.assertEqual(load_source(out).page_annotations,((),));ws.undo();self.assertEqual(ws.pages,before)
    def test_selected_duplicate_independent_single_undo_and_no_original_overwrite(self):
        ws,path=self.fixture();copy=ws.duplicate([ws.pages[0].uid])[0];before=ws.pages;data=ws.sources[ws.pages[0].source_id].data
        self.apply(ws,PageGeometryOptions(target_size=(595.2756,841.8898)),(copy,));out=self.export(ws)
        pages=PdfReader(out).pages;self.assertAlmostEqual(float(pages[0].cropbox.width),300);self.assertAlmostEqual(float(pages[1].cropbox.width),595.2756)
        self.assertEqual(ws.sources[before[0].source_id].data,data);after=ws.pages;ws.undo();self.assertEqual(ws.pages,before);ws.redo();self.assertEqual(ws.pages,after)
        with self.assertRaises(PdfWorkbenchError):export_pdf(ws.sources,ws.pages,path)
    def test_live_cross_page_fields_refill_and_per_widget_font_scale(self):
        ws,_=self.fixture(count=2);source=next(iter(ws.sources.values()))
        designed=apply_form_designs(source,(
            FormDesign('shared','shared','Gemeinsam','text',(FieldPlacement(0,(.2,.2,.7,.3)),FieldPlacement(1,(.2,.2,.7,.3))),value='Alt'),
            FormDesign('radio','radio','Auswahl','radio',(FieldPlacement(0,(.2,.5,.7,.6),'A'),FieldPlacement(1,(.2,.5,.7,.6),'B')),(('A','A'),('B','B')),'B'),))
        ws.replace_source(source.source_id,designed)
        self.apply(ws,PageGeometryOptions(target_size=(600,800)),(ws.pages[0].uid,));out=self.export(ws);reopened=load_source(out)
        fields={f.name:f.value for f in form_fields(reopened)};self.assertEqual(fields,{'shared':'Alt','radio':'B'})
        filled=fill_source_form(reopened,{'shared':'Neuer Text','radio':'A'});reader=PdfReader(io.BytesIO(filled.data))
        self.assertEqual({f.name:f.value for f in form_fields(filled)},{'shared':'Neuer Text','radio':'A'})
        widgets=[next(ref.get_object() for ref in p['/Annots'] if ref.get_object()['/Parent']['/T']=='shared') for p in reader.pages]
        import re
        sizes=[float(re.search(r'([\d.]+)\s+Tf',str(w.get('/DA',w['/Parent']['/DA'])))[1]) for w in widgets]
        self.assertAlmostEqual(sizes[0]/sizes[1],2)
        rendered=self.root/'fields.pdf';rendered.write_bytes(form_render_data(filled));self.assertFalse(self.qt(rendered).getbbox() is None)
    def test_invalid_late_page_cancel_and_font_limits_leave_workspace_atomic(self):
        ws,_=self.fixture(count=2);before=ws.pages;ids=tuple(p.uid for p in ws.pages)
        with self.assertRaises(PdfWorkbenchError):self.apply(ws,PageGeometryOptions(crop_margins=(299,0,2,0)))
        self.assertEqual(ws.pages,before)
        with self.assertRaises(OperationCancelled):prepare_page_geometry(ws.sources,ws.pages,PageGeometryOptions(target_size=(500,700)),cancel=lambda:True)
        ws.add_annotation(ids[0],PageAnnotation('small','text',(.1,.1,.5,.3),'Text',style=TextStyle(4)))
        before=ws.pages
        with self.assertRaises(PdfWorkbenchError):self.apply(ws,PageGeometryOptions(target_size=(100,100)))
        self.assertEqual(ws.pages,before)
    def test_resize_does_not_reveal_content_hidden_by_old_cropbox(self):
        ws,_=self.fixture();self.apply(ws,PageGeometryOptions(target_size=(700,700),margin=100));out=self.export(ws)
        for image in (self.qt(out),self.poppler(out)):
            red=sum(1 for r,g,b in image.get_flattened_data() if r>180 and g<70 and b<70)
            self.assertEqual(red,0)
    def test_native_link_ink_quadpoints_and_border_transform_with_contents(self):
        ws,_=self.fixture();source=next(iter(ws.sources.values()));writer=PdfWriter(clone_from=io.BytesIO(source.data));page=writer.pages[0]
        from pypdf.annotations import Link
        writer.add_annotation(page,Link(rect=(90,280,150,340),url='https://example.org'))
        ink=DictionaryObject({NameObject('/Type'):NameObject('/Annot'),NameObject('/Subtype'):NameObject('/Ink'),NameObject('/Rect'):ArrayObject(FloatObject(v) for v in (90,280,150,340)),NameObject('/InkList'):ArrayObject([ArrayObject(FloatObject(v) for v in (90,280,150,340))]),NameObject('/BS'):DictionaryObject({NameObject('/W'):FloatObject(2)})})
        writer.add_annotation(page,ink);stream=io.BytesIO();writer.write(stream);ws.replace_source(source.source_id,replace(source,source_id='native-annotations',data=stream.getvalue()))
        self.apply(ws,PageGeometryOptions(target_size=(600,800)));reader=PdfReader(self.export(ws));link,ink=[ref.get_object() for ref in reader.pages[0]['/Annots']]
        self.assertEqual(tuple(map(float,link['/Rect'])),(120,480,240,600));self.assertEqual(link['/A']['/URI'],'https://example.org')
        self.assertEqual(tuple(map(float,ink['/InkList'][0])),(120,480,240,600));self.assertEqual(float(ink['/BS']['/W']),4)
    def test_highlight_signature_and_image_keep_models_and_assets_after_reopen(self):
        ws,_=self.fixture(unit=2)
        from pdf_workbench.annotation_assets import load_image_asset
        assetpath=self.root/'asset.png';Image.new('RGBA',(30,20),(240,20,90,170)).save(assetpath);asset=load_image_asset(assetpath.read_bytes())
        models=(PageAnnotation('ink','signature',(.2,.2,.5,.4),strokes=(((.1,.2),(.8,.9)),)),
            PageAnnotation('hl','highlight',(.2,.5,.5,.6),rects=((.2,.5,.35,.6),(.36,.5,.5,.6))),
            PageAnnotation('image','image',(.6,.4,.8,.6),asset=asset,content_rotation=270))
        for model in models:ws.add_annotation(ws.pages[0].uid,model)
        result=self.apply(ws,PageGeometryOptions(target_size=(800,1000),margin=50));self.assertEqual(result.flattened_annotation_count,0)
        loaded=load_source(self.export(ws));self.assertEqual({a.uid for a in loaded.page_annotations[0]},{a.uid for a in models})
        for actual,expected in zip(loaded.page_annotations[0],ws.pages[0].annotations):
            for a,b in zip(actual.rect,expected.rect):self.assertAlmostEqual(a,b,places=6)
            self.assertEqual(actual.strokes,expected.strokes);self.assertEqual(actual.asset,expected.asset);self.assertEqual(actual.content_rotation,expected.content_rotation)
    def test_forms_remain_visible_and_refillable_in_all_rotations(self):
        for intrinsic in (0,90,180,270):
            for extra in (0,90,180,270):
                with self.subTest(intrinsic=intrinsic,extra=extra):
                    ws,_=self.fixture(intrinsic,extra,2);source=next(iter(ws.sources.values()))
                    designed=apply_form_designs(source,(FormDesign('f','f','Form','text',(FieldPlacement(0,(.25,.25,.7,.4)),),value='ALT'),))
                    writer=PdfWriter(clone_from=io.BytesIO(designed.data));widget=writer.pages[0]['/Annots'][0].get_object()
                    mk=widget['/MK'];mk[NameObject('/BG')]=ArrayObject(FloatObject(v) for v in (1,0,1))
                    stream=io.BytesIO();writer.write(stream);designed=replace(designed,data=stream.getvalue());designed=fill_source_form(designed,{'f':'FARBE'})
                    ws.replace_source(source.source_id,designed);self.apply(ws,PageGeometryOptions(target_size=(550,730),margin=20));out=self.export(ws,f'forms-{intrinsic}-{extra}.pdf')
                    reopened=load_source(out);filled=fill_source_form(reopened,{'f':'NEU'});self.assertEqual(form_fields(filled)[0].value,'NEU')
                    visible=self.root/f'flat-{intrinsic}-{extra}.pdf';visible.write_bytes(form_render_data(filled));q=self.qt(visible);p=self.poppler(out)
                    def magenta(image):
                        pixels=image.load();points=[(x/image.width,y/image.height) for y in range(image.height) for x in range(image.width) if pixels[x,y][0]>170 and pixels[x,y][1]<80 and pixels[x,y][2]>170]
                        self.assertTrue(points);return min(x for x,y in points),min(y for x,y in points),max(x for x,y in points),max(y for x,y in points)
                    for a,b in zip(magenta(q),magenta(p)):self.assertAlmostEqual(a,b,delta=.015)
    def test_resize_rejects_clipped_native_AP_and_widgets_atomically(self):
        from pypdf.annotations import Rectangle
        for kind in ('native','widget'):
            ws,_=self.fixture();source=next(iter(ws.sources.values()))
            if kind=='widget':
                designed=apply_form_designs(source,(FormDesign('f','f','Feld','text',(FieldPlacement(0,(.02,.2,.5,.3)),),value='Inhalt'),))
                ws.replace_source(source.source_id,designed)
            else:
                writer=PdfWriter(clone_from=io.BytesIO(source.data));writer.add_annotation(writer.pages[0],Rectangle(rect=(35,100,120,150),interior_color='00FF00'))
                stream=io.BytesIO();writer.write(stream);ws.replace_source(source.source_id,replace(source,source_id='with-native',data=stream.getvalue()))
            self.apply(ws,PageGeometryOptions(crop_margins=(30,0,0,0)));before=ws.pages;sources=dict(ws.sources)
            with self.assertRaisesRegex(PdfWorkbenchError,'abgeschnittene PDF-Anmerkungen oder Formularfelder'):
                self.apply(ws,PageGeometryOptions(target_size=(500,700)))
            self.assertEqual(ws.pages,before);self.assertEqual(ws.sources,sources)
    def test_shared_native_border_style_is_scaled_once_per_annotation(self):
        ws,_=self.fixture();source=next(iter(ws.sources.values()));writer=PdfWriter(clone_from=io.BytesIO(source.data));page=writer.pages[0]
        style=writer._add_object(DictionaryObject({NameObject('/W'):FloatObject(2),NameObject('/D'):ArrayObject([NumberObject(3),NumberObject(2)])}))
        for rect in ((90,100,150,140),(180,200,230,250)):
            annotation=DictionaryObject({NameObject('/Type'):NameObject('/Annot'),NameObject('/Subtype'):NameObject('/Square'),NameObject('/Rect'):ArrayObject(FloatObject(v) for v in rect),NameObject('/BS'):style})
            writer.add_annotation(page,annotation)
        stream=io.BytesIO();writer.write(stream);ws.replace_source(source.source_id,replace(source,source_id='shared-style',data=stream.getvalue()))
        self.apply(ws,PageGeometryOptions(target_size=(600,800)));reader=PdfReader(self.export(ws))
        for ref in reader.pages[0]['/Annots']:
            self.assertEqual(float(ref.get_object()['/BS']['/W']),4);self.assertEqual(tuple(map(float,ref.get_object()['/BS']['/D'])),(6,4))
    def test_source_bookmark_indices_remap_after_subset_and_project_reopen(self):
        from pdf_workbench.bookmarks import SourceBookmark
        from pdf_workbench.projects import ProjectSnapshot,encode_project,decode_project
        ws,_=self.fixture(count=3);source=next(iter(ws.sources.values()))
        imported=replace(source,source_id='bookmarked-source',bookmarks=(SourceBookmark('Kapitel',0,(SourceBookmark('Bleibt',2),)),),bookmark_warnings=('Importhinweis',))
        ws.replace_source(source.source_id,imported);target=ws.pages[2].uid;ws.add_bookmark('Arbeitsziel',target)
        ws.delete([ws.pages[0].uid,ws.pages[1].uid]);result=self.apply(ws,PageGeometryOptions(target_size=(500,700)))
        new=result.sources[0];self.assertEqual(new.bookmarks[0].page_index,None);self.assertEqual(new.bookmarks[0].children[0].page_index,0);self.assertEqual(new.bookmark_warnings,('Importhinweis',))
        restored=Workspace();restored.restore_state(decode_project(encode_project(ProjectSnapshot(ws.snapshot()))).state)
        self.assertEqual(restored.bookmarks[0].page_uid,target);self.assertEqual(len(restored.pages),1)
        output=self.root/'bookmarks.pdf';export_pdf(restored.sources,restored.pages,output,bookmarks=restored.bookmarks)
        self.assertEqual(PdfReader(output).outline[0].title,'Arbeitsziel')
