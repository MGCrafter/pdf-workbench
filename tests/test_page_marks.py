"""Real PDF regressions for page content, selected copies and crop/rotation."""
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Rectangle
from pypdf.generic import DecodedStreamObject, NameObject, NumberObject, TextStringObject
from PyQt6.QtCore import QSize
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtWidgets import QApplication

from pdf_workbench.annotations import PageAnnotation
from pdf_workbench.core import OperationCancelled, PdfWorkbenchError, Workspace, export_pdf, load_source
from pdf_workbench.forms import fill_source_form, form_fields
from pdf_workbench.form_design import FieldPlacement, FormDesign, apply_form_designs
from pdf_workbench.page_marks import (
    PageMarksOptions, PageMarksRequest, PageNumberOptions, WatermarkOptions,
    apply_page_marks, prepare_page_marks,
)
from test_form_support import make_form


class PageMarksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name); self.originals = {}

    def tearDown(self):
        for path, digest in self.originals.items(): self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.temp.cleanup()

    def fixture(self, rotation=0, count=1, unit=1, transformed=False):
        path = self.root / f"original-{rotation}-{count}-{unit}-{transformed}.pdf"
        writer = PdfWriter(); writer.add_metadata({"/Author":"Urheber ÄÖÜ", "/Title":"Original"})
        for index in range(count):
            page = writer.add_blank_page(width=340, height=460)
            page.cropbox.lower_left = (30,40); page.cropbox.upper_right = (310,420)
            page[NameObject("/UserUnit")] = NumberObject(unit)
            if rotation: page.rotate(rotation)
            if transformed:
                stream = DecodedStreamObject(); stream.set_data(b"0.3 0 0 0.3 0 0 cm 0 0 10 10 re W n\n")
                page[NameObject("/Contents")] = writer._add_object(stream)
            annotation = Rectangle(rect=(40,220,60,240),interior_color="00FF00")
            annotation[NameObject("/Contents")] = TextStringObject(f"Foreign {index}")
            writer.add_annotation(page,annotation)
        writer.write(path); self.protect(path); workspace = Workspace(); workspace.add_sources([load_source(path)]); return workspace,path

    def protect(self,path):
        self.originals[path] = hashlib.sha256(path.read_bytes()).hexdigest()

    def export(self,workspace,name="export.pdf"):
        path = self.root/name; export_pdf(workspace.sources,workspace.pages,path); return path

    def request(self,*,mark=None,number=None,ids=None):
        return PageMarksRequest(PageMarksOptions(watermark=mark,numbering=number),ids)

    def test_selected_duplicate_gets_content_only_once_and_undo_redo_is_atomic(self):
        workspace,original = self.fixture()
        workspace.add_annotation(workspace.pages[0].uid,PageAnnotation("note","comment",(.1,.1,.2,.2),"Eigene Notiz"))
        copied = workspace.duplicate([workspace.pages[0].uid])[0]
        before = tuple(workspace.pages); data = {key:source.data for key,source in workspace.sources.items()}
        result = apply_page_marks(workspace,self.request(mark=WatermarkOptions("Änderung €",20,angle=0),ids=(copied,)))
        self.assertEqual(result,1); self.assertEqual(workspace.pages[0].uid,before[0].uid); self.assertEqual(workspace.pages[1].uid,copied)
        self.assertEqual(workspace.pages[1].annotations,before[1].annotations)
        output = self.export(workspace); reader = PdfReader(output)
        self.assertNotIn("Änderung",reader.pages[0].extract_text()); self.assertIn("Änderung €",reader.pages[1].extract_text())
        for page in reader.pages:
            self.assertEqual([ref.get_object()["/Contents"] for ref in page["/Annots"]],["Foreign 0","Eigene Notiz"])
        reopened = load_source(output); self.assertEqual([len(items) for items in reopened.page_annotations],[1,1])
        self.assertEqual({key:workspace.sources[key].data for key in data},data)
        after = tuple(workspace.pages); workspace.undo(); self.assertEqual(tuple(workspace.pages),before)
        workspace.redo(); self.assertEqual(tuple(workspace.pages),after)
        with self.assertRaises(PdfWorkbenchError): export_pdf(workspace.sources,workspace.pages,original)

    def test_marking_one_page_preserves_cross_page_radio_and_repeated_text_group(self):
        workspace,_ = self.fixture(count=2)
        original = next(iter(workspace.sources.values()))
        source = apply_form_designs(original,(
            FormDesign("radio","radio","Auswahl","radio",(
                FieldPlacement(0,(.1,.2,.7,.3),"A"),FieldPlacement(1,(.1,.2,.7,.3),"B")),
                (("A","Antwort A"),("B","Antwort B")),"B"),
            FormDesign("shared","shared","Gemeinsamer Text","text",(
                FieldPlacement(0,(.1,.4,.8,.5)),FieldPlacement(1,(.1,.4,.8,.5))),value="Beide Seiten"),
        ))
        workspace.replace_source(original.source_id,source)
        before = tuple(workspace.pages)
        apply_page_marks(workspace,self.request(mark=WatermarkOptions("NUR SEITE EINS",18,angle=0),ids=(before[0].uid,)))
        self.assertEqual(workspace.pages[0].source_id,workspace.pages[1].source_id)
        output = self.export(workspace); reader = PdfReader(output)
        fields = reader.get_fields(); self.assertEqual(set(fields),{"radio","shared"})
        self.assertEqual(str(fields["radio"]["/V"]),"/B"); self.assertEqual(fields["shared"]["/V"],"Beide Seiten")
        self.assertIn("NUR SEITE EINS",reader.pages[0].extract_text()); self.assertNotIn("NUR SEITE EINS",reader.pages[1].extract_text())
        marked = load_source(output)
        refilled = fill_source_form(marked,{"radio":"A","shared":"Weiter gemeinsam"})
        values = {field.name:field.value for field in form_fields(refilled)}
        self.assertEqual(values,{"radio":"A","shared":"Weiter gemeinsam"})
        refreshed = PdfReader(io.BytesIO(refilled.data))
        self.assertEqual([str(page["/Annots"][1].get_object()["/AS"]) for page in refreshed.pages],["/A","/Off"])
        workspace.undo(); self.assertEqual(tuple(workspace.pages),before)

    def test_numbering_follows_workspace_order_and_explicit_counting_scope(self):
        workspace,_ = self.fixture(count=3)
        workspace.reorder([page.uid for page in reversed(workspace.pages)])
        ids = (workspace.pages[0].uid,workspace.pages[2].uid)
        apply_page_marks(workspace,self.request(number=PageNumberOptions(start_number=10),ids=ids))
        reader = PdfReader(self.export(workspace))
        self.assertIn("Seite 10 von 3",reader.pages[0].extract_text()); self.assertEqual(reader.pages[1].extract_text(),"")
        self.assertIn("Seite 12 von 3",reader.pages[2].extract_text())
        workspace.undo()
        apply_page_marks(workspace,self.request(number=PageNumberOptions("{page}/{pages}",4,counting="targets"),ids=ids))
        reader = PdfReader(self.export(workspace,"selection-count.pdf"))
        self.assertIn("4/2",reader.pages[0].extract_text()); self.assertIn("5/2",reader.pages[2].extract_text())

    def test_forms_and_foreign_annotations_remain_editable_without_omitted_data(self):
        path = self.root/"form.pdf"; make_form(path); self.protect(path)
        workspace = Workspace(); workspace.add_sources([load_source(path)])
        original = workspace.sources[workspace.pages[0].source_id]
        workspace.replace_source(original.source_id,fill_source_form(original,{"name":"Before marks"}))
        selected = workspace.pages[0].uid
        apply_page_marks(workspace,self.request(mark=WatermarkOptions(font_size=20),ids=(selected,)))
        marked = workspace.sources[workspace.pages[0].source_id]
        self.assertEqual(form_fields(marked)[0].value,"Before marks")
        workspace.replace_source(marked.source_id,fill_source_form(marked,{"name":"After marks"}))
        output = self.root/"first-only.pdf"; export_pdf(workspace.sources,[workspace.pages[0]],output)
        reader = PdfReader(output); self.assertEqual(reader.get_fields()["name"]["/V"],"After marks")
        self.assertIn("ENTWURF",reader.pages[0].extract_text())
        raw = b"".join(obj.get_data() if hasattr(obj,"get_data") else str(obj).encode("utf-8") for obj in reader.resolved_objects.values())
        self.assertNotIn(b"SECRET SECOND VALUE",raw); self.assertNotIn("second",reader.get_fields())

    def test_preview_contains_own_objects_and_keeps_actual_number_context(self):
        workspace,_ = self.fixture(count=3)
        page = workspace.pages[2]
        annotation = PageAnnotation("own","comment",(.1,.1,.2,.2),"Vorschaunotiz")
        workspace.add_annotation(page.uid,annotation)
        options = PageMarksOptions(numbering=PageNumberOptions())
        result = prepare_page_marks(workspace.sources,workspace.pages,options,preview_uid=page.uid)
        self.assertEqual(result.changed_ids,(page.uid,)); self.assertEqual(result.sources[0].page_count,1)
        preview = PdfReader(io.BytesIO(result.sources[0].data))
        self.assertIn("Seite 3 von 3",preview.pages[0].extract_text())
        self.assertIn("Vorschaunotiz",[item.get_object().get("/Contents") for item in preview.pages[0]["/Annots"]])
        self.assertEqual(result.pages[2].annotations,()); self.assertEqual(workspace.pages[2].annotations,(annotation,))

    def test_late_invalid_page_and_cancellation_do_not_mutate_workspace(self):
        workspace,_ = self.fixture(count=2)
        writer = PdfWriter(); writer.add_blank_page(width=10,height=10)
        tiny = self.root/"tiny.pdf"; writer.write(tiny); workspace.add_sources([load_source(tiny)]); self.protect(tiny)
        before,sources = tuple(workspace.pages),dict(workspace.sources)
        with self.assertRaisesRegex(PdfWorkbenchError,"passt nicht"):
            apply_page_marks(workspace,self.request(number=PageNumberOptions()))
        self.assertEqual(tuple(workspace.pages),before); self.assertEqual(workspace.sources,sources)
        with self.assertRaises(OperationCancelled):
            apply_page_marks(workspace,self.request(number=PageNumberOptions()),cancel=lambda:True)
        self.assertEqual(tuple(workspace.pages),before); self.assertEqual(workspace.sources,sources)

    def test_invalid_templates_nonfinite_values_and_missing_targets_are_rejected(self):
        for template in ("{page.__class__}","{page!r}","{page:05}","{unknown}","{", "{}"):
            with self.subTest(template=template),self.assertRaises(PdfWorkbenchError): PageNumberOptions(template)
        for kwargs in ({"opacity":float("nan")},{"angle":float("inf")},{"opacity":0},{"margin":-1},{"position":"elsewhere"}):
            with self.subTest(kwargs=kwargs),self.assertRaises(PdfWorkbenchError): WatermarkOptions(**kwargs)
        workspace,_ = self.fixture()
        for ids in ((),("missing",)):
            with self.assertRaises(PdfWorkbenchError): apply_page_marks(workspace,self.request(number=PageNumberOptions(),ids=ids))

    def test_pdf_content_isolated_from_original_clip_and_transform(self):
        workspace,_ = self.fixture(transformed=True)
        apply_page_marks(workspace,self.request(mark=WatermarkOptions("SICHTBAR",22,(1,0,0),1,0)))
        path = self.export(workspace)
        document = QPdfDocument(None); self.assertEqual(document.load(str(path)),QPdfDocument.Error.None_)
        image = document.render(0,QSize(280,380)); document.close()
        red = [(x,y) for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x,y).red()>180 and image.pixelColor(x,y).green()<90]
        self.assertGreater(len(red),150)

    def test_userunit_changes_local_font_size_and_preserves_source_metadata(self):
        workspace,_ = self.fixture(unit=2)
        apply_page_marks(workspace,self.request(number=PageNumberOptions(font_size=12)))
        source = workspace.sources[workspace.pages[0].source_id]; reader = PdfReader(io.BytesIO(source.data)); page = reader.pages[0]
        self.assertEqual(page["/UserUnit"],2); self.assertEqual(reader.metadata.author,"Urheber ÄÖÜ")
        appearances = page["/Resources"]["/XObject"].values()
        self.assertTrue(any(b"/F0 6.0000000 Tf" in ref.get_object().get_data() for ref in appearances))

    @unittest.skipUnless(shutil.which("pdftoppm"),"Second PDF renderer Poppler is not installed")
    def test_all_intrinsic_and_workspace_rotations_cropbox_render_footer_upright(self):
        for intrinsic in (0,90,180,270):
            for additional in (0,90,180,270):
                with self.subTest(intrinsic=intrinsic,additional=additional):
                    workspace,_ = self.fixture(rotation=intrinsic)
                    workspace.rotate([workspace.pages[0].uid],additional)
                    apply_page_marks(workspace,self.request(number=PageNumberOptions("Grüße ÄÖÜ €",position="bottom-left",font_size=12,color=(1,0,0))))
                    path = self.export(workspace,f"rot-{intrinsic}-{additional}.pdf")
                    raw = subprocess.run(["pdftoppm","-r","72","-cropbox","-singlefile",str(path)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
                    image = Image.open(io.BytesIO(raw)).convert("RGB")
                    points = [(x,y) for y in range(image.height) for x in range(image.width) if (lambda c:c[0]>180 and c[1]<90 and c[2]<90)(image.getpixel((x,y)))]
                    self.assertGreater(len(points),100)
                    self.assertGreaterEqual(min(x for x,y in points),17); self.assertLess(min(x for x,y in points),22)
                    self.assertGreater(min(y for x,y in points),image.height-40)
                    self.assertLess(max(y for x,y in points),image.height-17)
                    document = QPdfDocument(None); document.load(str(path)); qt = document.render(0,document.pagePointSize(0).toSize()); document.close()
                    self.assertEqual((qt.width(),qt.height()),image.size)
                    qt_red = sum(qt.pixelColor(x,y).red()>180 and qt.pixelColor(x,y).green()<90 for y in range(qt.height()-45,qt.height()) for x in range(150))
                    self.assertGreater(qt_red,100)

    @unittest.skipUnless(shutil.which("pdftoppm"),"Second PDF renderer Poppler is not installed")
    def test_opacity_has_real_blending_and_angle_changes_rendered_bounds(self):
        images = []
        for opacity,angle in ((1,0),(.25,0),(1,45)):
            workspace,_ = self.fixture()
            apply_page_marks(workspace,self.request(mark=WatermarkOptions("WASSER",22,(1,0,0),opacity,angle)))
            path = self.export(workspace,f"alpha-{opacity}-{angle}.pdf")
            raw = subprocess.run(["pdftoppm","-r","72","-cropbox","-singlefile",str(path)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
            images.append(Image.open(io.BytesIO(raw)).convert("RGB"))
        for index,expected in ((0,0),(1,191)):
            pixels = [images[index].getpixel((x,y)) for y in range(140,240) for x in range(50,230)]
            self.assertAlmostEqual(min(g for r,g,b in pixels if r>240),expected,delta=2)
        def bbox(image):
            points = [(x,y) for y in range(100,280) for x in range(30,250) if (lambda c:c[0]>180 and c[1]<90)(image.getpixel((x,y)))]
            return max(y for x,y in points)-min(y for x,y in points)
        self.assertGreater(bbox(images[2]),bbox(images[0])*2)


if __name__ == "__main__": unittest.main()
