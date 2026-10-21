"""Actual PDF content, alignment and read-only comparison regressions."""
from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
import unittest

if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtWidgets import QApplication
from pypdf import PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdf_workbench.comparison import ComparisonOptions, compare_documents
from pdf_workbench.annotations import PageAnnotation
from pdf_workbench.core import OperationCancelled, PdfWorkbenchError, Workspace, export_pdf, load_source
from pdf_workbench.form_design import FieldPlacement, FormDesign, apply_form_designs
from pdf_workbench.forms import fill_source_form

APP = QApplication.instance() or QApplication([])


def comparison_pdf(path, labels, *, image_color=None, text_x=30, page_size=(300,500)):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=page_size)
    for label in labels:
        pdf.setFont("Helvetica",18); pdf.drawString(text_x,page_size[1]-60,label)
        if image_color:
            pdf.drawImage(ImageReader(Image.new("RGB",(40,40),image_color)),40,100,100,100)
        pdf.showPage()
    pdf.save(); path.write_bytes(buffer.getvalue())
    return load_source(path)


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.originals={}

    def tearDown(self):
        for path,original in self.originals.items():self.assertEqual(path.read_bytes(),original)
        self.temp.cleanup()

    def source(self,name,labels,**kwargs):
        path=self.root/(name+'.pdf');source=comparison_pdf(path,labels,**kwargs)
        self.originals[path]=path.read_bytes();return source

    def test_identical_visible_documents_with_different_pdf_bytes_are_unchanged(self):
        left=self.source('left',['Identical document'])
        writer=PdfWriter(clone_from=io.BytesIO(left.data));writer.add_metadata({'/Title':'Different metadata','/Author':'Another author'})
        path=self.root/'metadata.pdf';writer.write(path);right=load_source(path)
        self.assertNotEqual(left.data,right.data)
        result=compare_documents(left,right)
        self.assertEqual([page.status for page in result.pages],['unchanged'])

    def test_true_changed_text_with_same_page_count_has_meaningful_word_diff(self):
        left=self.source('left',['Total price 125 Euro','Footer stays'])
        right=self.source('right',['Total price 175 Euro','Footer stays'])
        result=compare_documents(left,right);page=result.pages[0]
        self.assertEqual([entry.status for entry in result.pages],['changed','unchanged'])
        self.assertEqual([(c.before,c.after) for c in page.text_changes],[('125','175')])
        self.assertGreater(page.visual_difference,0)
        self.assertTrue(page.visual_regions)
        for region in page.visual_regions:self.assertTrue(0<=region[0]<region[2]<=1 and 0<=region[1]<region[3]<=1)

    def test_image_only_change_is_detected_when_text_and_page_count_are_identical(self):
        left=self.source('left',['Same text'],image_color='red')
        right=self.source('right',['Same text'],image_color='blue')
        page=compare_documents(left,right).pages[0]
        self.assertEqual(page.status,'changed');self.assertEqual(page.text_changes,())
        self.assertGreater(page.visual_difference,.02);self.assertTrue(page.visual_regions)
        self.assertTrue(any(rect[0]<.2 and rect[2]>.4 and rect[1]<.65 and rect[3]>.75 for rect in page.visual_regions))

    def test_layout_change_with_same_extracted_text_is_detected(self):
        left=self.source('left',['Text moved'],text_x=30)
        right=self.source('right',['Text moved'],text_x=85)
        page=compare_documents(left,right).pages[0]
        self.assertEqual(page.status,'changed');self.assertEqual(page.text_changes,())
        self.assertTrue(page.visual_regions)

    def test_front_and_middle_insertions_do_not_shift_unchanged_page_pairs(self):
        left=self.source('left',['First anchor','Second anchor','Last anchor'])
        right=self.source('right',['Inserted front','First anchor','Inserted middle','Second anchor','Last anchor'])
        result=compare_documents(left,right)
        self.assertEqual([(p.left_index,p.right_index,p.status) for p in result.pages],
                         [(None,0,'added'),(0,1,'unchanged'),(None,2,'added'),(1,3,'unchanged'),(2,4,'unchanged')])

    def test_removed_middle_page_and_changed_page_with_equal_counts_stay_separate(self):
        left=self.source('left',['First anchor','Removed section','Third anchor'])
        right=self.source('right',['First anchor','Third anchor','New final section'])
        result=compare_documents(left,right)
        self.assertEqual([(p.left_index,p.right_index,p.status) for p in result.pages],
                         [(0,0,'unchanged'),(1,None,'removed'),(2,1,'unchanged'),(None,2,'added')])

    def test_reordered_pages_are_linked_removed_added_entries(self):
        left=self.source('left',['Alpha section','Beta section','Gamma section'])
        right=self.source('right',['Gamma section','Alpha section','Beta section'])
        result=compare_documents(left,right)
        removed=next(page for page in result.pages if page.status=='removed')
        added=next(page for page in result.pages if page.status=='added')
        self.assertEqual((removed.left_index,removed.moved_to),(2,0))
        self.assertEqual((added.right_index,added.moved_from),(0,2))
        self.assertEqual(result.count('unchanged'),2)

    def test_repeated_pages_preserve_every_original_and_new_page(self):
        left=self.source('left',['Same','Same','Final'])
        right=self.source('right',['Same','Same','Same','Final'])
        result=compare_documents(left,right)
        self.assertEqual(result.count('added'),1);self.assertEqual(result.count('unchanged'),3)
        self.assertEqual(sorted(p.left_index for p in result.pages if p.left_index is not None),[0,1,2])
        self.assertEqual(sorted(p.right_index for p in result.pages if p.right_index is not None),[0,1,2,3])

    def test_page_format_change_is_visible_even_if_the_content_is_blank(self):
        left=self.source('left',[''],page_size=(300,500));right=self.source('right',[''],page_size=(600,1000))
        page=compare_documents(left,right).pages[0]
        self.assertEqual(page.status,'changed');self.assertTrue(page.size_changed)
        self.assertEqual(page.visual_regions,((0.,0.,1.,1.),))

    def test_exported_workbench_text_objects_are_compared_and_restored_for_preview(self):
        base=self.source('base',['Same original'])
        workspace=Workspace();workspace.add_sources([base]);uid=workspace.pages[0].uid
        sources=[]
        for index,text in enumerate(('Previous visible text','Changed visible text')):
            if index:workspace.remove_annotation(uid,'object')
            workspace.add_annotation(uid,PageAnnotation('object','text',(.1,.3,.9,.5),text))
            output=self.root/f'annotated-{index}.pdf';export_pdf(workspace.sources,workspace.pages,output)
            self.originals[output]=output.read_bytes();sources.append(load_source(output))
        self.assertTrue(all(source.page_annotations[0] for source in sources))
        result=compare_documents(*sources);page=result.pages[0]
        self.assertEqual(page.status,'changed');self.assertTrue(page.visual_regions)
        self.assertTrue(any('Previous' in change.before and 'Changed' in change.after for change in page.text_changes))
        self.assertFalse(any(result.left_source.page_annotations))
        self.assertNotEqual(result.left_source.data,sources[0].data)
        self.assertEqual(sources[0].page_annotations[0][0].text,'Previous visible text')

    def test_exported_highlight_only_changes_are_visible_without_text_changes(self):
        base=self.source('base',['Same original'])
        workspace=Workspace();workspace.add_sources([base]);uid=workspace.pages[0].uid
        baseline=self.root/'without-highlight.pdf';export_pdf(workspace.sources,workspace.pages,baseline)
        workspace.add_annotation(uid,PageAnnotation('mark','highlight',(.1,.1,.5,.15),rects=((.1,.1,.5,.15),)))
        marked=self.root/'with-highlight.pdf';export_pdf(workspace.sources,workspace.pages,marked)
        result=compare_documents(load_source(baseline),load_source(marked));page=result.pages[0]
        self.assertEqual(page.status,'changed');self.assertEqual(page.text_changes,())
        self.assertTrue(page.visual_regions);self.assertGreater(page.visual_difference,0)

    def test_only_form_text_value_changes_have_text_and_visual_differences(self):
        base=self.source('base',['Same original'])
        form=apply_form_designs(base,(FormDesign('name','name','Name','text',(FieldPlacement(0,(.1,.3,.9,.4)),),value='Previous value'),))
        changed=fill_source_form(form,{'name':'Updated value'})
        result=compare_documents(form,changed);page=result.pages[0]
        self.assertEqual(page.status,'changed');self.assertTrue(page.visual_regions)
        self.assertTrue(any('Previous' in change.before and 'Updated' in change.after for change in page.text_changes))
        self.assertNotEqual(form.data,result.left_source.data)

    def test_only_radio_selection_changes_are_detected_in_live_exported_forms(self):
        base=self.source('base',['Same original'])
        form=apply_form_designs(base,(FormDesign('group','choice','Choice','radio',
            (FieldPlacement(0,(.1,.3,.6,.36),'yes'),FieldPlacement(0,(.1,.4,.6,.46),'no')),
            options=(('yes','Yes'),('no','No')),value='yes'),))
        changed=fill_source_form(form,{'choice':'no'})
        sources=[]
        for index,source in enumerate((form,changed)):
            workspace=Workspace();workspace.add_sources([source]);path=self.root/f'radio-{index}.pdf'
            export_pdf(workspace.sources,workspace.pages,path);self.originals[path]=path.read_bytes();sources.append(load_source(path))
        page=compare_documents(*sources).pages[0]
        self.assertEqual(page.status,'changed');self.assertTrue(page.visual_regions)
        self.assertGreater(page.visual_difference,0)

    def test_cancellation_after_real_first_page_and_page_limit_fail_without_outputs(self):
        left=self.source('left',['A','B']);right=self.source('right',['A','C'])
        messages=[]
        def progress(value,message):messages.append((value,message))
        with self.assertRaises(OperationCancelled):compare_documents(left,right,lambda:bool(messages),progress)
        self.assertTrue(messages)
        with self.assertRaisesRegex(PdfWorkbenchError,'höchstens 1 Seiten'):
            compare_documents(left,right,options=ComparisonOptions(max_pages=1))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),['left.pdf','right.pdf'])

    def test_progress_finishes_and_text_limit_is_explicit(self):
        left=self.source('left',['Same']);right=self.source('right',['Same'])
        messages=[];compare_documents(left,right,progress=lambda value,message:messages.append(value))
        self.assertEqual(messages[-1],100);self.assertEqual(messages,sorted(messages))
        verbose=self.source('verbose',['A '*800])
        with self.assertRaisesRegex(PdfWorkbenchError,'zu viel Text'):
            compare_documents(verbose,right,options=ComparisonOptions(max_text_characters=1000))
