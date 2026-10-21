"""Author real interactive field trees, then fill, subset, merge and render them."""
from __future__ import annotations

from dataclasses import replace
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject
from pdf_workbench.core import PdfWorkbenchError, Workspace, export_pdf, load_source
from pdf_workbench.form_design import FieldPlacement, FormDesign, apply_form_designs, form_designs
from pdf_workbench.forms import _chain, _name, _widgets, fill_source_form, form_fields


def sample_designs():
    return (
        FormDesign("text","name","Name","text",(FieldPlacement(0,(.1,.12,.7,.19)),),value="Jürgen",required=True,max_length=30),
        FormDesign("check","agree","Einverstanden","checkbox",(FieldPlacement(0,(.1,.25,.7,.31)),),value=True,tab_order=1),
        FormDesign("choice","country","Land","choice",(FieldPlacement(0,(.1,.4,.7,.47)),),(("AT","Österreich"),("DE","Deutschland")),"AT",tab_order=2),
        FormDesign("radio","plan","Tarif","radio",(FieldPlacement(0,(.1,.58,.7,.65),"basic"),FieldPlacement(1,(.1,.2,.7,.27),"premium")),
                   (("basic","Basis"),("premium","Premium")),"premium",tab_order=3))


class FormDesignTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name);self.path=self.directory/"original.pdf"
        writer=PdfWriter();writer.add_blank_page(width=400,height=500);writer.add_blank_page(width=400,height=500);writer.write(self.path)
        self.original=self.path.read_bytes();self.source=load_source(self.path)

    def tearDown(self):self.temp.cleanup()

    def authored(self):return apply_form_designs(self.source,sample_designs())

    def test_all_four_types_canonical_values_flags_and_immutable_original(self):
        result=self.authored();fields={field.name:field for field in form_fields(result)}
        self.assertNotEqual(result.source_id,self.source.source_id);self.assertEqual(result.page_count,2)
        self.assertEqual(self.path.read_bytes(),self.original);self.assertEqual(self.source.data,self.original)
        self.assertEqual({name:field.value for name,field in fields.items()},{"name":"Jürgen","agree":True,"country":"AT","plan":"premium"})
        self.assertTrue(fields["name"].required);self.assertEqual(fields["name"].max_length,30)
        self.assertEqual(fields["plan"].options,(("basic","Basis"),("premium","Premium")))
        self.assertEqual(fields["plan"].label,"Tarif");self.assertEqual(fields["plan"].page_numbers,(1,2))
        reader=PdfReader(io.BytesIO(result.data));self.assertEqual(len(reader.get_fields()),4)
        self.assertEqual(reader.get_fields()["plan"]["/V"],"/premium")
        for page in reader.pages:
            for widget in _widgets(page):self.assertIn("/AP",widget);self.assertEqual(widget["/P"].indirect_reference,page.indirect_reference)
        filled=fill_source_form(result,{"name":"Graz","agree":False,"country":"DE","plan":"basic"})
        self.assertEqual({f.name:f.value for f in form_fields(filled)},{"name":"Graz","agree":False,"country":"DE","plan":"basic"})
        radio=[widget for page in PdfReader(io.BytesIO(filled.data)).pages for widget in _widgets(page) if _name(_chain(widget))=="plan"]
        self.assertEqual([str(widget["/AS"]) for widget in radio],["/basic","/Off"])
        self.assertEqual(str(radio[0]["/Parent"]["/V"]),"/basic")

    def test_edit_rename_delete_and_tab_order_remove_old_data(self):
        source=self.authored();designs=list(form_designs(source));old=designs[0]
        designs[0]=replace(old,name="customer",label="Kundin",value="New",required=False,read_only=True,tab_order=3,
                           placements=(FieldPlacement(0,(.2,.1,.8,.17)),))
        designs=[replace(field,tab_order=0) if field.name=="country" else field for field in designs if field.name!="agree"]
        result=apply_form_designs(source,designs);reader=PdfReader(io.BytesIO(result.data))
        self.assertEqual(set(reader.get_fields()),{"customer","country","plan"});self.assertNotIn(b"Einverstanden",result.data)
        self.assertNotIn(b"J\374rgen",result.data);self.assertNotIn(b"J\303\274rgen",result.data)
        fields={f.name:f for f in form_fields(result)};self.assertTrue(fields["customer"].read_only);self.assertFalse(fields["customer"].required)
        self.assertEqual(_name(_chain(next(_widgets(reader.pages[0])))),"country")
        with self.assertRaises(PdfWorkbenchError):fill_source_form(result,{"customer":"Should fail"})
        empty=apply_form_designs(result,());self.assertEqual(form_fields(empty),());self.assertNotIn("/AcroForm",PdfReader(io.BytesIO(empty.data)).trailer["/Root"])
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_noop_preserves_existing_appearance_and_exact_positions(self):
        source=self.authored();self.assertIs(apply_form_designs(source,form_designs(source)),source)
        reader=PdfReader(io.BytesIO(source.data));before=list(_widgets(reader.pages[0]))[0]["/AP"]["/N"].get_data()
        designs=list(form_designs(source));designs[-1]=replace(designs[-1],label="Anderer Tarif")
        result=apply_form_designs(source,designs);after=list(_widgets(PdfReader(io.BytesIO(result.data)).pages[0]))[0]["/AP"]["/N"].get_data()
        self.assertEqual(before,after)

    def test_radio_subset_omits_selected_option_and_merge_duplicate_refill(self):
        source=self.authored();workspace=Workspace();workspace.add_sources([source]);out=self.directory/"subset.pdf"
        export_pdf(workspace.sources,workspace.pages[:1],out)
        fields={f.name:f for f in form_fields(load_source(out))};self.assertEqual(fields["plan"].value,"");self.assertEqual(fields["plan"].options,(("basic","Basis"),))
        self.assertEqual(fields["plan"].label,"Tarif");self.assertNotIn(b"Premium",out.read_bytes())
        again=fill_source_form(load_source(out),{"plan":"basic"});self.assertEqual(next(f.value for f in form_fields(again) if f.name=="plan"),"basic")
        # A reversed export restores selection when its selected widget is added later.
        reversed_path=self.directory/"reversed.pdf";export_pdf(workspace.sources,tuple(reversed(workspace.pages)),reversed_path)
        self.assertEqual(next(f.value for f in form_fields(load_source(reversed_path)) if f.name=="plan"),"premium")
        workspace.duplicate([workspace.pages[0].uid]);other=apply_form_designs(self.source,sample_designs());workspace.add_sources([other])
        out=self.directory/"merged.pdf";export_pdf(workspace.sources,workspace.pages,out);merged=load_source(out)
        groups=[f for f in form_fields(merged) if f.kind=="radio"];self.assertEqual(len(groups),2);self.assertNotEqual(groups[0].name,groups[1].name)
        filled=fill_source_form(merged,{groups[0].name:"basic",groups[1].name:""})
        self.assertEqual([f.value for f in form_fields(filled) if f.kind=="radio"],["basic",""])

    def test_crop_rotation_geometry_and_appearance_survive_filling(self):
        for rotation in (0,90,180,270):
            with self.subTest(rotation=rotation):
                writer=PdfWriter();page=writer.add_blank_page(width=500,height=600)
                page[NameObject("/CropBox")]=ArrayObject(FloatObject(v) for v in (40,70,440,570));page.rotate(rotation)
                path=self.directory/f"rotated{rotation}.pdf";writer.write(path);source=load_source(path)
                field=FormDesign("uid","rotated","Text","text",(FieldPlacement(0,(.1,.2,.7,.3)),),value="Visible")
                result=apply_form_designs(source,(field,));place=form_designs(result)[0].placements[0]
                for actual,expected in zip(place.rect,field.placements[0].rect):self.assertAlmostEqual(actual,expected)
                widget=next(_widgets(PdfReader(io.BytesIO(result.data)).pages[0]));before=tuple(widget["/AP"]["/N"]["/Matrix"])
                filled=fill_source_form(result,{"rotated":"Changed"});widget=next(_widgets(PdfReader(io.BytesIO(filled.data)).pages[0]))
                self.assertEqual(tuple(widget["/AP"]["/N"]["/Matrix"]),before);self.assertIn(b"Changed",widget["/AP"]["/N"].get_data())
                if shutil.which("pdftoppm"):
                    pdf=self.directory/f"filled{rotation}.pdf";pdf.write_bytes(filled.data)
                    subprocess.run(["pdftoppm","-singlefile","-cropbox","-scale-to","500","-png",str(pdf),str(pdf.with_suffix(""))],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                    from PIL import Image
                    image=Image.open(pdf.with_suffix(".png")).convert("RGB");pixels=image.load();w,h=image.size
                    nonwhite=[(x/w,y/h) for y in range(h) for x in range(w) if max(pixels[x,y])-min(pixels[x,y])>20 or min(pixels[x,y])<150]
                    self.assertTrue(nonwhite)
                    self.assertGreater(min(p[0] for p in nonwhite),.085);self.assertLess(max(p[0] for p in nonwhite),.715)
                    self.assertGreater(min(p[1] for p in nonwhite),.185);self.assertLess(max(p[1] for p in nonwhite),.315)

    def test_radio_export_metadata_filters_with_selected_widgets_and_duplicates(self):
        source=self.authored();writer=PdfWriter(clone_from=io.BytesIO(source.data))
        group=next(field.get_object() for field in writer._root_object["/AcroForm"]["/Fields"] if field.get_object().get("/T")=="plan")
        group[NameObject("/Opt")]=ArrayObject([TextStringObject("BUSINESS BASIC"),TextStringObject("SECRET PREMIUM VALUE")])
        stream=io.BytesIO();writer.write(stream);source=replace(source,data=stream.getvalue())
        workspace=Workspace();workspace.add_sources([source])
        for label,pages,expected in (("full",workspace.pages,["BUSINESS BASIC","SECRET PREMIUM VALUE"]),
                ("reverse",tuple(reversed(workspace.pages)),["SECRET PREMIUM VALUE","BUSINESS BASIC"]),
                ("subset",workspace.pages[:1],["BUSINESS BASIC"]),
                ("duplicate",(workspace.pages[0],workspace.pages[0]),["BUSINESS BASIC","BUSINESS BASIC"])):
            with self.subTest(label=label):
                out=self.directory/f"opt-{label}.pdf";export_pdf(workspace.sources,pages,out)
                result=PdfReader(out);self.assertEqual(list(result.get_fields()["plan"]["/Opt"]),expected)
                if label in ("subset","duplicate"):self.assertNotIn(b"SECRET PREMIUM VALUE",out.read_bytes())
        designs=[replace(field,required=True,placements=tuple(replace(place,rect=(.2,.3,.8,.37)) for place in field.placements))
                 if field.kind=="radio" else field for field in form_designs(source)]
        edited=apply_form_designs(source,designs)
        self.assertEqual(list(PdfReader(io.BytesIO(edited.data)).get_fields()["plan"]["/Opt"]),["BUSINESS BASIC","SECRET PREMIUM VALUE"])

    def test_nested_delete_removes_unreachable_widget_parent_cycles_and_values(self):
        source=self.authored();writer=PdfWriter(clone_from=io.BytesIO(source.data))
        fields=writer._root_object["/AcroForm"]["/Fields"]
        group=DictionaryObject({NameObject("/T"):TextStringObject("Section"),NameObject("/Kids"):ArrayObject([fields[0],fields[1]])})
        reference=writer._add_object(group)
        for field in group["/Kids"]:field.get_object()[NameObject("/Parent")]=reference
        writer._root_object["/AcroForm"][NameObject("/Fields")]=ArrayObject([reference,*fields[2:]])
        fields[0].get_object()[NameObject("/V")]=TextStringObject("DELETED TEXT SECRET")
        stream=io.BytesIO();writer.write(stream);source=replace(source,data=stream.getvalue())
        designs=[field for field in form_designs(source) if field.name!="Section.name"]
        result=apply_form_designs(source,designs)
        self.assertNotIn(b"DELETED TEXT SECRET",result.data);self.assertNotIn("Section.name",PdfReader(io.BytesIO(result.data)).get_fields())

    def test_validation_and_snapshot_undo(self):
        for kwargs in ({"max_length":2,"value":"long"},{"value":"two\nlines"},{"name":""},{"options":(("same","A"),("same","B")),"kind":"choice"}):
            values=dict(uid="x",name="field",label="Field",kind="text",placements=(FieldPlacement(0,(.1,.1,.5,.2)),));values.update(kwargs)
            with self.subTest(kwargs=kwargs),self.assertRaises(PdfWorkbenchError):FormDesign(**values)
        with self.assertRaises(PdfWorkbenchError):apply_form_designs(self.source,(replace(sample_designs()[0],placements=(FieldPlacement(2,(.1,.1,.5,.2)),)),))
        workspace=Workspace();workspace.add_sources([self.source]);before=tuple(workspace.pages);result=self.authored();workspace.replace_source(self.source.source_id,result)
        after=tuple(workspace.pages);self.assertEqual([p.uid for p in before],[p.uid for p in after]);workspace.undo();self.assertEqual(tuple(workspace.pages),before);workspace.redo();self.assertEqual(tuple(workspace.pages),after)


if __name__=="__main__":unittest.main()
