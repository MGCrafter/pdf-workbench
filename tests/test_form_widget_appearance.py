"""Shared fields keep per-widget appearance overrides through export and refill."""
from dataclasses import replace
import io
from pathlib import Path
import re
import tempfile
import unittest

from pypdf import PdfReader,PdfWriter
from pypdf.generic import NameObject,TextStringObject
from pdf_workbench.core import Workspace,export_pdf,load_source
from pdf_workbench.form_design import FormDesign,FieldPlacement,apply_form_designs
from pdf_workbench.forms import fill_source_form


class WidgetAppearanceTests(unittest.TestCase):
    def test_shared_field_retains_widget_font_override_in_both_export_orders(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);path=folder/'original.pdf';writer=PdfWriter()
            for _ in range(2):writer.add_blank_page(300,500)
            writer.write(path);original=path.read_bytes();source=load_source(path)
            designed=apply_form_designs(source,(FormDesign('shared','shared','Shared','text',
                (FieldPlacement(0,(.1,.1,.8,.25)),FieldPlacement(1,(.1,.1,.8,.25))),value='Before'),))
            writer=PdfWriter(clone_from=io.BytesIO(designed.data))
            widget=writer.pages[0]['/Annots'][0].get_object()
            widget[NameObject('/DA')]=TextStringObject(str(widget['/Parent']['/DA']).replace('11 Tf','22 Tf'))
            stream=io.BytesIO();writer.write(stream);designed=replace(designed,data=stream.getvalue())
            for order in ((0,1),(1,0)):
                workspace=Workspace();workspace.add_sources([designed]);pages=[workspace.pages[i] for i in order]
                out=folder/f'export-{order[0]}.pdf';export_pdf(workspace.sources,pages,out)
                filled=fill_source_form(load_source(out),{'shared':'After'});reader=PdfReader(io.BytesIO(filled.data))
                for page,original_index in zip(reader.pages,order):
                    widget=page['/Annots'][0].get_object();expected=22 if original_index==0 else 11
                    da=str(widget.get('/DA',widget['/Parent']['/DA']))
                    self.assertEqual(float(re.search(r'([\d.]+)\s+Tf',da)[1]),expected)
                    appearance=widget['/AP']['/N'].get_data().decode('latin-1')
                    self.assertEqual(float(re.search(r'([\d.]+)\s+Tf',appearance)[1]),expected)
            self.assertEqual(path.read_bytes(),original)


if __name__=='__main__':unittest.main()
