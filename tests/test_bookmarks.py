"""Real outline trees, safe destinations, UID remapping and complete history."""
from dataclasses import replace
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from pypdf import PdfReader,PdfWriter
from pypdf.generic import ArrayObject,DictionaryObject,NameObject,TextStringObject
from pdf_workbench.bookmarks import SourceBookmark,WorkspaceBookmark,bookmark_location,remap_source_bookmarks
from pdf_workbench.core import OperationCancelled,PdfWorkbenchError,Workspace,WorkspaceState,export_pdf,load_source


def outline_values(reader,items=None):
    result=[]
    for item in reader.outline if items is None else items:
        if isinstance(item,list):result.append(outline_values(reader,item))
        else:result.append((item.title,reader.get_destination_page_number(item)))
    return result


class BookmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.path=self.root/"source.pdf"
        writer=PdfWriter()
        for index in range(3):writer.add_blank_page(300+index*10,500)
        first=writer.add_outline_item("Einführung",0);writer.add_outline_item("Grüße Wien",2,parent=first)
        writer.add_outline_item("Mitte",1);writer.write(self.path)
        self.original=self.path.read_bytes();self.source=load_source(self.path);self.workspace=Workspace();self.workspace.add_sources([self.source])

    def tearDown(self):
        self.assertEqual(self.path.read_bytes(),self.original);self.temp.cleanup()

    def export(self,pages=None,bookmarks=Ellipsis,name="output.pdf",**kwargs):
        path=self.root/name
        if bookmarks is Ellipsis:bookmarks=self.workspace.bookmarks
        export_pdf(self.workspace.sources,self.workspace.pages if pages is None else pages,path,bookmarks=bookmarks,**kwargs)
        return path

    def test_safe_import_merge_reorder_and_second_reader_page_targets(self):
        self.assertEqual(self.source.bookmarks,(SourceBookmark("Einführung",0,(SourceBookmark("Grüße Wien",2),)),SourceBookmark("Mitte",1)))
        original_ids=[p.uid for p in self.workspace.pages];self.workspace.reorder([original_ids[2],original_ids[0],original_ids[1]])
        other=replace(self.source,source_id="another-source");self.workspace.add_sources([other]);path=self.export()
        expected=[("Einführung",1),[("Grüße Wien",0)],("Mitte",2),("Einführung",3),[("Grüße Wien",5)],("Mitte",4)]
        self.assertEqual(outline_values(PdfReader(path)),expected)
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtPdf import QPdfDocument,QPdfBookmarkModel
        app=QApplication.instance() or QApplication([]);document=QPdfDocument(None);document.load(str(path));model=QPdfBookmarkModel(None);model.setDocument(document);app.processEvents()
        self.assertEqual(model.rowCount(),4)
        self.assertEqual([model.data(model.index(i,0),QPdfBookmarkModel.Role.Page) for i in range(4)],[1,2,3,4])
        self.assertEqual(model.data(model.index(0,0,model.index(0,0)),QPdfBookmarkModel.Role.Page),0)
        model.setDocument(None);document.close()

    def test_rebuilt_source_bookmarks_remap_subset_reorder_and_first_duplicate(self):
        remapped=remap_source_bookmarks(self.source.bookmarks,(2,1,2))
        self.assertEqual(remapped,(SourceBookmark('Einführung',None,(SourceBookmark('Grüße Wien',0),)),SourceBookmark('Mitte',1)))
        self.assertEqual(remap_source_bookmarks(self.source.bookmarks,(1,)),(SourceBookmark('Mitte',0),))
        self.assertEqual(remap_source_bookmarks(self.source.bookmarks,()),())

    def test_page_marks_keep_source_metadata_and_remap_rebuilt_source_outline(self):
        from pdf_workbench.page_marks import PageMarksOptions,WatermarkOptions,prepare_page_marks
        from pdf_workbench.projects import ProjectSnapshot,encode_project,decode_project
        self.workspace.delete([self.workspace.pages[0].uid])
        self.workspace.reorder([p.uid for p in reversed(self.workspace.pages)])
        before=self.workspace.bookmarks
        result=prepare_page_marks(self.workspace.sources,self.workspace.pages,PageMarksOptions(watermark=WatermarkOptions(text='DRAFT')))
        self.workspace.apply_page_replacements(result.sources,result.pages)
        current=self.workspace.sources[self.workspace.pages[0].source_id]
        self.assertEqual(current.bookmarks,remap_source_bookmarks(self.source.bookmarks,(2,1)))
        self.assertEqual(current.bookmark_warnings,self.source.bookmark_warnings)
        self.assertEqual(self.workspace.bookmarks,before)
        self.assertEqual(decode_project(encode_project(ProjectSnapshot(self.workspace.snapshot()))).state,self.workspace.snapshot())
        self.assertEqual(outline_values(PdfReader(self.export(bookmarks=None))),[('Einführung',None),[('Grüße Wien',0)],('Mitte',1)])

    def test_ocr_final_export_preserves_edited_outline_and_explicit_deletion(self):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QApplication
        from pypdf.generic import DecodedStreamObject
        from pdf_workbench import ocr
        app=QApplication.instance() or QApplication([])
        writer=PdfWriter(clone_from=io.BytesIO(self.source.data))
        font=writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}))
        for page in writer.pages:
            page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
            content=DecodedStreamObject();content.set_data(b'BT /F1 12 Tf 20 20 Td (Already searchable) Tj ET')
            page[NameObject('/Contents')]=writer._add_object(content)
        output=io.BytesIO();writer.write(output);self.workspace.replace_source(self.source.source_id,replace(self.source,source_id='text-source',data=output.getvalue()))
        self.workspace.update_bookmark(self.workspace.bookmarks[0].uid,title='Edited',page_uid=self.workspace.pages[2].uid)
        fontpath=self.root/'font.ttf';fontpath.write_bytes(b'not used: all pages already searchable')
        engine=ocr.OcrEngine(self.root/'unused-tesseract','test',self.root,fontpath,('eng',))
        for tree,name in ((self.workspace.bookmarks,'edited-ocr.pdf'),((),'empty-ocr.pdf')):
            path=self.root/name
            with patch.object(ocr,'_language_files',return_value={}),patch.object(ocr,'_run_tesseract') as recognizer:
                result=ocr.export_searchable_pdf(self.workspace.sources,self.workspace.pages,path,engine=engine,cache_dir=self.root/'cache',bookmarks=tree)
                recognizer.assert_not_called();self.assertEqual(result.skipped_pages,3)
            expected=[('Edited',2),[('Grüße Wien',2)],('Mitte',1)] if tree else []
            self.assertEqual(outline_values(PdfReader(path)),expected)

    def test_selection_keeps_child_as_chapter_and_no_omitted_pages_in_raw_graph(self):
        path=self.export(self.workspace.pages[2:]);reader=PdfReader(path)
        self.assertEqual(len(reader.pages),1);self.assertEqual(outline_values(reader),[("Einführung",None),[("Grüße Wien",0)]])
        from pypdf.generic import IndirectObject
        pages=[]
        for index in range(1,reader.trailer["/Size"]):
            obj=reader.get_object(IndirectObject(index,0,reader))
            if isinstance(obj,DictionaryObject) and obj.get("/Type")=="/Page":pages.append(obj)
        self.assertEqual(len(pages),1)

    def test_duplicate_uid_target_keeps_exact_copy_and_source_replacement(self):
        original=self.workspace.pages[0].uid;duplicate=self.workspace.duplicate([original])[0]
        self.workspace.update_bookmark(self.workspace.bookmarks[0].uid,page_uid=duplicate)
        self.workspace.reorder([self.workspace.pages[0].uid,*[p.uid for p in self.workspace.pages[2:]],duplicate])
        self.workspace.replace_source(self.source.source_id,replace(self.source,source_id="edited-source"))
        self.assertEqual(outline_values(PdfReader(self.export()))[0],("Einführung",3))
        # Automatic source mode uses the first included occurrence deterministically.
        automatic=self.export(bookmarks=None,name="automatic.pdf");self.assertEqual(outline_values(PdfReader(automatic))[0],("Einführung",0))
        empty=self.export(bookmarks=(),name="without.pdf");self.assertNotIn("/Outlines",PdfReader(empty).trailer["/Root"])

    def test_page_delete_prunes_targets_and_undo_redo_restores_both(self):
        before=self.workspace.snapshot();first=self.workspace.pages[0].uid
        self.workspace.delete([first]);self.assertIsNone(self.workspace.bookmarks[0].page_uid);self.assertTrue(self.workspace.bookmarks[0].children)
        self.workspace.undo();self.assertEqual(self.workspace.pages,list(before.pages));self.assertEqual(self.workspace.bookmarks,before.bookmarks)
        self.workspace.redo();self.assertIsNone(self.workspace.bookmarks[0].page_uid)
        self.workspace.delete([self.workspace.pages[-1].uid]);self.assertEqual([b.title for b in self.workspace.bookmarks],["Mitte"])

    def test_edits_and_full_session_history_snapshot_roundtrip(self):
        one=self.workspace.add_bookmark("Eigenes Kapitel",parent_uid=self.workspace.bookmarks[0].uid)
        self.workspace.update_bookmark(one,title="Umbenannt",page_uid=self.workspace.pages[1].uid)
        self.workspace.move_bookmark(one,index=1);self.workspace.rotate([self.workspace.pages[1].uid],90);self.workspace.undo()
        state=self.workspace.snapshot();restored=Workspace();restored.restore_state(state)
        self.assertEqual(restored.snapshot(),state)
        for action in ("redo","undo","undo","undo","undo"):
            getattr(self.workspace,action)();getattr(restored,action)();self.assertEqual(restored.snapshot(),self.workspace.snapshot())
        restored.restore_state(state,clear_history=True);self.assertFalse(restored.can_undo);self.assertFalse(restored.can_redo)

    def test_invalid_history_or_targets_are_rejected_atomically(self):
        state=self.workspace.snapshot()
        cases=[replace(state,bookmarks=(WorkspaceBookmark("bad","Wrong","missing"),)),
               replace(state,undo_bookmarks=()),
               replace(state,sources=(replace(self.source,bookmarks=(SourceBookmark("Bad",99),)),)),
               replace(state,pages=(*state.pages,state.pages[0]))]
        for invalid in cases:
            with self.subTest(invalid=invalid),self.assertRaises(PdfWorkbenchError):self.workspace.restore_state(invalid)
            self.assertEqual(self.workspace.snapshot(),state)
        parent=self.workspace.bookmarks[0];child=parent.children[0]
        with self.assertRaises(PdfWorkbenchError):self.workspace.move_bookmark(parent.uid,parent_uid=child.uid)
        self.assertEqual(self.workspace.snapshot(),state)

    def test_history_remains_paired_and_bounded(self):
        uid=self.workspace.bookmarks[0].uid
        for index in range(70):self.workspace.update_bookmark(uid,title=f"Title {index}")
        self.assertEqual(len(self.workspace._undo),50);self.assertEqual(len(self.workspace._undo_bookmarks),50)
        for _ in range(50):self.workspace.undo()
        self.assertEqual(len(self.workspace._redo),len(self.workspace._redo_bookmarks));self.assertEqual(self.workspace.bookmarks[0].title,"Title 19")

    def test_external_script_launch_and_action_chains_are_omitted_never_cloned(self):
        writer=PdfWriter(clone_from=io.BytesIO(self.original));outlines=writer._root_object["/Outlines"]
        first=outlines["/First"];first["/A"][NameObject("/S")]=NameObject("/JavaScript");first["/A"][NameObject("/JS")]=TextStringObject("SECRET SCRIPT")
        for mode in ("/Launch","/URI","/GoToR"):
            ref=writer.add_outline_item("Unsafe "+mode,0);ref.get_object()["/A"][NameObject("/S")]=NameObject(mode);ref.get_object()["/A"][NameObject("/F")]=TextStringObject("SECRET EXTERNAL")
        chained=writer.add_outline_item("Chained",0).get_object();chained["/A"][NameObject("/Next")]=DictionaryObject({NameObject("/S"):NameObject("/Launch")})
        path=self.root/"unsafe.pdf";writer.write(path);source=load_source(path);self.assertTrue(any("Aktionen" in x for x in source.bookmark_warnings))
        workspace=Workspace();workspace.add_sources([source]);path=self.root/"safe.pdf";export_pdf(workspace.sources,workspace.pages,path,bookmarks=workspace.bookmarks)
        data=path.read_bytes();self.assertNotIn(b"SECRET SCRIPT",data);self.assertNotIn(b"SECRET EXTERNAL",data)
        self.assertEqual(outline_values(PdfReader(path)),[("Einführung",None),[("Grüße Wien",2)],("Mitte",1)])

    def test_named_destinations_cycles_and_cancel_are_bounded(self):
        writer=PdfWriter(clone_from=io.BytesIO(self.original));first=writer._root_object["/Outlines"]["/First"]
        writer._root_object[NameObject("/Names")]=DictionaryObject({NameObject("/Dests"):DictionaryObject({NameObject("/Names"):ArrayObject([TextStringObject("chapter"),ArrayObject([writer.pages[2].indirect_reference,NameObject("/Fit")])])})})
        first.pop("/A");first[NameObject("/Dest")]=TextStringObject("chapter")
        path=self.root/"named.pdf";writer.write(path);source=load_source(path);self.assertEqual(source.bookmarks[0].page_index,2)
        first[NameObject("/Next")]=first.indirect_reference;path=self.root/"cycle.pdf";writer.write(path);source=load_source(path);self.assertTrue(source.bookmark_warnings)
        with self.assertRaises(OperationCancelled):load_source(path,cancel=lambda:True)

    def test_encrypted_and_compressed_bookmark_roundtrip_and_original_protection(self):
        from pdf_workbench.compression import CompressionOptions
        path=self.export(password="secret",optimization=CompressionOptions("lossless"));source=load_source(path,password="secret")
        self.assertEqual(source.bookmarks,self.source.bookmarks)
        with self.assertRaises(PdfWorkbenchError):export_pdf(self.workspace.sources,self.workspace.pages,self.path,bookmarks=self.workspace.bookmarks)


if __name__=="__main__":unittest.main()
