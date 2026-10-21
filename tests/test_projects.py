"""Persistence acceptance: embedded originals, complete Undo, corruption and safe saves."""
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from pypdf import PdfReader,PdfWriter
from PyQt6.QtWidgets import QApplication
from pdf_workbench.annotations import PageAnnotation
from pdf_workbench.annotation_assets import load_image_asset
from pdf_workbench.core import Workspace,export_pdf,load_source,PdfWorkbenchError,OperationCancelled
from pdf_workbench.form_design import FieldPlacement,FormDesign,apply_form_designs
from pdf_workbench.forms import form_fields
from pdf_workbench.projects import (ProjectSnapshot,ProjectPasswordRequired,IncorrectProjectPassword,
    encode_project,decode_project,read_project,write_project,RecoveryStore,list_recoveries)

APP=QApplication.instance() or QApplication([])


def rewrite(data,mutate,extra=None):
    source=zipfile.ZipFile(io.BytesIO(data));out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as archive:
        for name in source.namelist():
            payload=source.read(name)
            if name=='manifest.json':
                manifest=json.loads(payload);mutate(manifest);payload=json.dumps(manifest).encode()
            archive.writestr(name,payload)
        if extra:archive.writestr(*extra)
    return out.getvalue()


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        writer=PdfWriter();writer.add_blank_page(300,500);writer.add_blank_page(400,600)
        self.original=self.root/'original.pdf';writer.write(self.original);self.original_bytes=self.original.read_bytes()
        self.workspace=Workspace();ids=self.workspace.add_sources([load_source(self.original)])
        self.workspace.add_annotation(ids[0],PageAnnotation('comment','comment',(.1,.2,.3,.4),'Private note'))
        self.workspace.add_bookmark('Kapitel',ids[0]);self.workspace.rotate(ids[:1],90)
        self.workspace.duplicate(ids[:1]);self.workspace.undo()
        self.snapshot=ProjectSnapshot(self.workspace.snapshot(),(ids[0],),ids[0],'custom',2.)

    def tearDown(self):
        if self.original.exists():self.assertEqual(self.original.read_bytes(),self.original_bytes)
        self.temp.cleanup()

    def test_embedded_pdf_bookmarks_annotations_and_both_histories_round_trip(self):
        path=self.root/'work.pdfwb';identity=write_project(self.snapshot,path)
        self.original.unlink()  # The session must not depend on the original disk file.
        loaded=read_project(path);self.assertEqual(loaded.identity,identity)
        self.assertEqual(loaded.snapshot,self.snapshot)
        restored=Workspace();restored.restore_state(loaded.snapshot.state)
        self.assertEqual(restored.pages,self.workspace.pages);self.assertTrue(restored.can_redo)
        restored.redo();self.assertEqual(len(restored.pages),3)
        restored.undo();restored.undo();self.assertEqual(restored.pages[0].rotation,0)
        out=self.root/'export.pdf';export_pdf(restored.sources,restored.pages,out,bookmarks=restored.bookmarks)
        self.assertEqual(len(PdfReader(out).pages),2);self.assertEqual(PdfReader(out).outline[0].title,'Kapitel')
        self.assertEqual(len(restored.pages[0].annotations),1)

    def test_form_values_and_image_assets_remain_editable_after_project_reopen(self):
        from PyQt6.QtCore import QByteArray,QBuffer,QIODevice
        from PyQt6.QtGui import QImage,QColor
        source=next(iter(self.workspace.sources.values()))
        designed=apply_form_designs(source,(FormDesign('name','name','Name','text',(FieldPlacement(1,(.1,.2,.7,.3)),),value='Grüße'),))
        self.workspace.replace_source(source.source_id,designed)
        image=QImage(12,12,QImage.Format.Format_ARGB32);image.fill(QColor('#387b64'))
        data=QByteArray();buffer=QBuffer(data);buffer.open(QIODevice.OpenModeFlag.WriteOnly);image.save(buffer,'PNG')
        asset=load_image_asset(bytes(data));self.workspace.add_annotation(self.workspace.pages[1].uid,PageAnnotation('image','image',(.1,.4,.3,.6),asset=asset))
        loaded=decode_project(encode_project(ProjectSnapshot(self.workspace.snapshot())))
        restored=Workspace();restored.restore_state(loaded.state)
        output=self.root/'restored.pdf';export_pdf(restored.sources,restored.pages,output,bookmarks=restored.bookmarks)
        fields=form_fields(load_source(output));self.assertEqual(fields[0].value,'Grüße')
        self.assertEqual(restored.pages[1].annotations[0].asset.sha256,asset.sha256)
        restored.remove_annotation(restored.pages[1].uid,'image');restored.undo()
        self.assertEqual(restored.pages[1].annotations[0].asset,asset)

    def test_previously_encrypted_pdf_requires_protected_project_including_undo_sources(self):
        source=next(iter(self.workspace.sources.values()));protected=replace(source,was_encrypted=True)
        state=replace(self.snapshot.state,sources=(protected,))
        snapshot=replace(self.snapshot,state=state)
        with self.assertRaises(ProjectPasswordRequired):encode_project(snapshot)
        encoded=encode_project(snapshot,password='Project secret')
        self.assertNotIn(b'Private note',encoded);self.assertNotIn(b'%PDF-',encoded);self.assertNotIn(b'Project secret',encoded)
        with self.assertRaises(ProjectPasswordRequired):decode_project(encoded)
        with self.assertRaises(IncorrectProjectPassword):decode_project(encoded,password='wrong secret')
        self.assertEqual(decode_project(encoded,password='Project secret'),snapshot)
        changed=encoded[:-1]+bytes([encoded[-1]^1])
        with self.assertRaises(IncorrectProjectPassword):decode_project(changed,password='Project secret')
        # A forged plaintext flag cannot be loaded as an unprotected project.
        plain=encode_project(self.snapshot)
        with self.assertRaises(PdfWorkbenchError):decode_project(rewrite(plain,lambda m:m['sources'][0].update(was_encrypted=True)))

    def test_corrupt_paths_extra_members_bad_hashes_and_invalid_history_are_rejected(self):
        data=encode_project(self.snapshot)
        cases=[rewrite(data,lambda m:m['sources'][0].update(data_sha256='0'*64)),
               rewrite(data,lambda m:m['pages'][0].update(page_index=500)),
               rewrite(data,lambda m:m['undo'][0].append(dict(m['pages'][0],source_id='missing'))),
               rewrite(data,lambda m:m.update(schema_version=True)),
               rewrite(data,lambda m:None,('../outside.txt',b'No extraction')),
               rewrite(data,lambda m:None,('unlisted.txt',b'No hidden member')),
               data[:len(data)//2]]
        for case in cases:
            with self.subTest(size=len(case)),self.assertRaises(PdfWorkbenchError):decode_project(case)
        self.assertFalse((self.root.parent/'outside.txt').exists())

    def test_existing_foreign_outputs_aliases_and_changed_projects_are_preserved(self):
        project=self.root/'safe.pdfwb';identity=write_project(self.snapshot,project);original=project.read_bytes()
        with self.assertRaises(PdfWorkbenchError):write_project(self.snapshot,project)
        changed=replace(self.snapshot,zoom_factor=3.)
        identity2=write_project(changed,project,expected=identity)
        self.assertNotEqual(identity,identity2);self.assertEqual(read_project(project).snapshot,changed)
        project.write_bytes(original)
        with self.assertRaises(PdfWorkbenchError):write_project(changed,project,expected=identity2)
        self.assertEqual(project.read_bytes(),original)
        alias=self.root/'alias.pdfwb'
        try:alias.symlink_to(project)
        except OSError:pass
        else:
            with self.assertRaises(PdfWorkbenchError):read_project(alias)
            with self.assertRaises(PdfWorkbenchError):write_project(self.snapshot,alias)
        self.assertEqual(project.read_bytes(),original)

    def test_concurrent_replacement_during_commit_is_preserved_and_previous_project_recovers(self):
        project=self.root/'work.pdfwb';identity=write_project(self.snapshot,project);previous=project.read_bytes()
        import pdf_workbench.projects as projects
        original_commit=projects._commit_new
        def conflict(temporary,target):
            if target==project:target.write_bytes(b'Concurrent foreign file')
            return original_commit(temporary,target)
        with patch.object(projects,'_commit_new',side_effect=conflict):
            with self.assertRaises(PdfWorkbenchError):write_project(self.snapshot,project,expected=identity)
        self.assertEqual(project.read_bytes(),b'Concurrent foreign file')
        backups=list(self.root.glob('.work.pdfwb.previous-*'))
        self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),previous)
        project.unlink()
        recovered=read_project(project);self.assertEqual(recovered.snapshot,self.snapshot)
        self.assertEqual(recovered.recovered_from,backups[0]);self.assertIsNone(recovered.identity)

    def test_cancel_creates_no_destination_and_hardlink_fallback_writes_real_project(self):
        target=self.root/'cancelled.pdfwb'
        with self.assertRaises(OperationCancelled):write_project(self.snapshot,target,cancel=lambda:True)
        self.assertFalse(target.exists())
        with patch('pdf_workbench.projects.os.link',side_effect=PermissionError(1,'Unsupported')):
            write_project(self.snapshot,target)
        self.assertEqual(read_project(target).snapshot,self.snapshot)

    def test_replacement_after_commit_never_becomes_an_owned_project_identity(self):
        project = self.root / 'after-commit.pdfwb'
        identity = write_project(self.snapshot, project); previous = project.read_bytes()
        import pdf_workbench.projects as projects
        commit = projects._commit_new
        foreign = self.root / 'foreign.bin'; foreign.write_bytes(b'Foreign application data')
        def replace_after_commit(temporary, target):
            owned = commit(temporary, target)
            if target == project: os.replace(foreign, target)
            return owned
        with patch.object(projects, '_commit_new', side_effect=replace_after_commit):
            with self.assertRaises(PdfWorkbenchError): write_project(self.snapshot, project, expected=identity)
        self.assertEqual(project.read_bytes(), b'Foreign application data')
        backups = list(self.root.glob('.after-commit.pdfwb.previous-*'))
        self.assertEqual(len(backups), 1); self.assertEqual(backups[0].read_bytes(), previous)
        with self.assertRaises(PdfWorkbenchError): write_project(self.snapshot, project, expected=identity)
        self.assertEqual(project.read_bytes(), b'Foreign application data')

    def test_recovery_keeps_two_generations_and_excludes_live_sessions(self):
        store=RecoveryStore(self.root/'recovery')
        try:
            first=store.save(self.snapshot);second=store.save(self.snapshot);third=store.save(self.snapshot)
            self.assertFalse(first.exists());self.assertTrue(second.exists());self.assertTrue(third.exists())
            self.assertEqual(list_recoveries(store.directory),())
        finally:store.close()
        recoveries=list_recoveries(store.directory)
        self.assertEqual(recoveries[0].path,third)
        self.assertEqual(read_project(recoveries[0].path).snapshot,self.snapshot)
        another=RecoveryStore(store.directory);own=another.save(self.snapshot)
        another.close(discard=True);self.assertFalse(own.exists());self.assertTrue(third.exists())


if __name__=='__main__':unittest.main()
