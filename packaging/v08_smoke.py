"""Native 0.8 acceptance: real local documents and isolated update fixtures."""
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

from pypdf import PdfReader,PdfWriter
from PyQt6.QtCore import QCoreApplication,QEvent,QSettings,Qt
from PyQt6.QtWidgets import QApplication,QDialog,QMessageBox

from pdf_workbench.annotations import PageAnnotation
from pdf_workbench.core import OperationCancelled,PdfWorkbenchError,Workspace,export_pdf,load_source
from pdf_workbench.page_geometry import PageGeometryOptions,prepare_page_geometry
from pdf_workbench.projects import (ProjectSnapshot,ProjectPasswordRequired,RecoveryStore,
    decode_project,encode_project,list_recoveries,read_project,write_project)
from pdf_workbench.updates import REPOSITORY,UpdateError,check_latest,download_update,release_from_metadata


def update_fixture(version='99.0.0',target='linux',payload=b'local verified package fixture'):
    """Official-shaped metadata only; all bytes are supplied by the test."""
    name=f'PDFWorkbench-v{version}-{target}-x86_64'+('.zip' if target=='windows' else '.tar.gz')
    digest=hashlib.sha256(payload).hexdigest()
    checksums=''.join(f'{digest}  PDFWorkbench-v{version}-{platform}-x86_64{suffix}\n' for platform,suffix in (('linux','.tar.gz'),('windows','.zip'))).encode()
    def asset(name,data):return {'name':name,'browser_download_url':f'https://github.com/{REPOSITORY}/releases/download/v{version}/{name}',
        'size':len(data),'digest':'sha256:'+hashlib.sha256(data).hexdigest(),'state':'uploaded'}
    return {'tag_name':'v'+version,'html_url':f'https://github.com/{REPOSITORY}/releases/tag/v{version}',
        'draft':False,'prerelease':False,'assets':[asset(name,payload),asset('SHA256SUMS',checksums)]},checksums,payload


class Response(io.BytesIO):
    def __init__(self,data):super().__init__(data);self.headers={'Content-Length':str(len(data))}


def acceptance_class(base,fixture):
    class V08Acceptance(base):
        def test_bookmarks_reorder_subset_runtime(self):
            workspace=Workspace();workspace.add_sources([load_source(self.a)])
            ids=[p.uid for p in workspace.pages];parent=workspace.add_bookmark('Kapitel',ids[0]);workspace.add_bookmark('Anhang',ids[2],parent_uid=parent)
            workspace.reorder(tuple(reversed(ids)));export_pdf(workspace.sources,workspace.pages,self.out,bookmarks=workspace.bookmarks)
            reader=PdfReader(self.out);self.assertEqual(reader.outline[0].title,'Kapitel');self.assertEqual(reader.get_destination_page_number(reader.outline[0]),2)
            self.assertEqual(reader.get_destination_page_number(reader.outline[1][0]),0)
            subset=self.directory/'subset.pdf';export_pdf(workspace.sources,workspace.pages[:1],subset,bookmarks=workspace.bookmarks)
            subset_reader=PdfReader(subset);self.assertEqual(subset_reader.outline[0].title,'Kapitel')
            self.assertEqual(subset_reader.outline[1][0].title,'Anhang');self.assertEqual(subset_reader.get_destination_page_number(subset_reader.outline[1][0]),0)
            workspace.undo();self.assertEqual([p.uid for p in workspace.pages],ids)
            self.assertEqual(self.a.read_bytes(),self.original_a)

        def test_page_geometry_crop_resize_runtime(self):
            workspace=Workspace();workspace.add_sources([load_source(self.a)]);uid=workspace.pages[0].uid
            workspace.add_annotation(uid,PageAnnotation('own','text',(.25,.25,.7,.35),'Grüße Geometrie'))
            duplicate=workspace.duplicate([uid])[0];before=workspace.pages
            result=prepare_page_geometry(workspace.sources,workspace.pages,PageGeometryOptions(crop_margins=(10,20,30,40)),(duplicate,))
            workspace.apply_page_replacements(result.sources,result.pages);cropped=self.directory/'crop.pdf';export_pdf(workspace.sources,workspace.pages,cropped)
            pages=PdfReader(cropped).pages;self.assertEqual(tuple(map(float,pages[0].cropbox)),(0,0,300,500));self.assertEqual(tuple(map(float,pages[1].cropbox)),(10,40,270,480))
            self.assertEqual(len(load_source(cropped).page_annotations[1]),1);workspace.undo();self.assertEqual(workspace.pages,before)
            resized=prepare_page_geometry(workspace.sources,workspace.pages,PageGeometryOptions(target_size=(600,1000)),(duplicate,))
            workspace.apply_page_replacements(resized.sources,resized.pages);export_pdf(workspace.sources,workspace.pages,self.out)
            self.assertEqual(float(PdfReader(self.out).pages[1].mediabox.width),600);self.assertIn('A1',PdfReader(self.out).pages[1].extract_text())
            self.assertEqual(load_source(self.out).page_annotations[1][0].text,'Grüße Geometrie');self.assertEqual(self.a.read_bytes(),self.original_a)
            with self.assertRaises(OperationCancelled):prepare_page_geometry(workspace.sources,workspace.pages,PageGeometryOptions(target_size=(500,700)),cancel=lambda:True)

        def test_project_history_encryption_runtime(self):
            self.ws.add_bookmark('Kapitel',self.ws.pages[0].uid);self.ws.rotate([self.ws.pages[0].uid],90);self.ws.duplicate([self.ws.pages[0].uid]);self.ws.undo()
            snapshot=ProjectSnapshot(self.ws.snapshot(),(self.ws.pages[0].uid,),self.ws.pages[0].uid,'custom',1.5)
            path=self.directory/'work.pdfwb';write_project(snapshot,path);loaded=read_project(path);self.assertEqual(loaded.snapshot,snapshot)
            restored=Workspace();restored.restore_state(loaded.snapshot.state);self.assertTrue(restored.can_redo);restored.redo();self.assertEqual(len(restored.pages),6)
            restored.undo();restored.undo();self.assertEqual(restored.pages[0].rotation,0)
            protected=replace(snapshot,state=replace(snapshot.state,sources=tuple(replace(s,was_encrypted=True) for s in snapshot.state.sources)))
            with self.assertRaises(ProjectPasswordRequired):encode_project(protected)
            encrypted=encode_project(protected,password='native smoke passphrase');self.assertNotIn(b'%PDF-',encrypted)
            self.assertEqual(decode_project(encrypted,password='native smoke passphrase'),protected)
            self.assertEqual(self.a.read_bytes(),self.original_a)

        def test_recovery_generations_runtime(self):
            store=RecoveryStore(self.directory/'recovery');snapshot=ProjectSnapshot(self.ws.snapshot())
            try:
                one=store.save(snapshot);two=store.save(snapshot);three=store.save(snapshot)
                self.assertFalse(one.exists());self.assertTrue(two.exists());self.assertTrue(three.exists());self.assertEqual(list_recoveries(store.directory),())
            finally:store.close()
            entries=list_recoveries(store.directory);self.assertEqual(len(entries),2);self.assertEqual(read_project(entries[0].path).snapshot,snapshot)
            other=RecoveryStore(store.directory)
            try:other.save(snapshot);other.discard();self.assertTrue(two.exists());self.assertTrue(three.exists())
            finally:other.close(discard=True)

        def test_update_metadata_integrity_runtime(self):
            metadata,checksums,payload=update_fixture();release=release_from_metadata(metadata,'0.8.0','linux')
            self.assertEqual(release.version,'99.0.0');self.assertIsNone(release_from_metadata(metadata,'99.0.0','linux'))
            with patch('pdf_workbench.updates._open',return_value=Response(json.dumps(metadata).encode())):
                self.assertEqual(check_latest('0.8.0','linux'),release)
            with patch('pdf_workbench.updates.native_platform',return_value='linux'),patch('pdf_workbench.updates._open',side_effect=[Response(checksums),Response(payload)]):
                downloaded=download_update(release,self.directory/'update-cache');self.assertEqual(downloaded.archive.read_bytes(),payload)
            bad=metadata.copy();bad['html_url']='https://github.com/another/repository/releases/tag/v99.0.0'
            with self.assertRaises(UpdateError):release_from_metadata(bad,'0.8.0','linux')
            with patch('pdf_workbench.updates.native_platform',return_value='linux'),patch('pdf_workbench.updates._open',side_effect=[Response(checksums),Response(b'tampered')]),self.assertRaises(UpdateError):
                download_update(release,self.directory/'bad-update-cache')
    return V08Acceptance


def v08_acceptance(window,directory,app,wait_for,fixture):
    """Exercise actual dialogs and main-window jobs; never query the network."""
    from pdf_workbench import __version__
    from pdf_workbench.ui.bookmarks_dialog import BookmarksDialog
    from pdf_workbench.ui.page_geometry_dialog import PageGeometryDialog
    from pdf_workbench.ui.project_dialogs import ProjectPasswordDialog,RecoveryDialog
    from pdf_workbench.ui.update_dialog import UpdateDialog
    checks=[]
    window._project_saved_key=None;window.saved_state=tuple(window.workspace.pages);window._saved_bookmarks=window.workspace.bookmarks;window.new_workspace()
    source_path=directory/'v08-document.pdf';fixture(source_path,('Chapter 1','Chapter 2'))
    window.import_files([str(source_path)]);wait_for(lambda:window.job is None);ids=tuple(p.uid for p in window.workspace.pages)
    before=window.workspace.bookmarks
    def edit_bookmarks(editor):
        editor.show();uid=editor.add_bookmark();editor.title_edit.setText('Kapitel Eins');editor.target_combo.setCurrentIndex(editor.target_combo.findData(ids[0]));assert editor.apply_properties()
        child=editor.add_bookmark(child=True);editor.title_edit.setText('Kapitel Zwei');editor.target_combo.setCurrentIndex(editor.target_combo.findData(ids[1]));assert editor.apply_properties()
        editor.resize(800,560);app.processEvents();editor.grab().save(str(directory/'v08-bookmarks.png'));editor.accept();return QDialog.DialogCode.Accepted
    with patch.object(BookmarksDialog,'exec',edit_bookmarks):window.bookmarks_action.trigger()
    assert window.workspace.bookmarks[0].title=='Kapitel Eins';window.workspace.reorder(tuple(reversed(ids)));window.refresh()
    out=directory/'v08-bookmarks.pdf';export_pdf(window.workspace.sources,window.workspace.pages,out,bookmarks=window.workspace.bookmarks)
    r=PdfReader(out);assert r.get_destination_page_number(r.outline[0])==1 and r.get_destination_page_number(r.outline[1][0])==0
    window.undo();window.undo();assert window.workspace.bookmarks==before;window.redo()
    checks.append('bookmarks edit/export/page targets and undo')
    duplicate=window.workspace.duplicate([window.workspace.pages[0].uid])[0];window.refresh([duplicate],duplicate);before=window.workspace.pages
    def crop(editor):
        editor.show();editor.resize(800,560)
        for spin in editor.margin_controls:spin.setValue(3)
        wait_for(lambda:editor.worker is None and editor._ready_request is not None and editor.apply_button.isEnabled())
        assert editor.request.target_ids==(duplicate,);assert not editor.preview.image.isNull();editor.result_toggle.click();editor.grab().save(str(directory/'v08-crop.png'));editor.apply_button.click();return QDialog.DialogCode.Accepted
    with patch.object(PageGeometryDialog,'exec',crop):window.crop_action.trigger();wait_for(lambda:window.job is None)
    assert window.workspace.pages!=before;window.undo();assert window.workspace.pages==before
    def resize(editor):
        editor.show();editor.resize(800,560);editor.paper.setCurrentIndex(1);editor.landscape.setChecked(True);editor.margin.setValue(5)
        wait_for(lambda:editor.worker is None and editor._ready_request is not None and editor.apply_button.isEnabled())
        assert not editor.preview.result_image.isNull();editor.grab().save(str(directory/'v08-resize.png'));editor.apply_button.click();return QDialog.DialogCode.Accepted
    with patch.object(PageGeometryDialog,'exec',resize):window.resize_pages_action.trigger();wait_for(lambda:window.job is None)
    size_out=directory/'v08-resized.pdf';export_pdf(window.workspace.sources,window.workspace.pages,size_out,bookmarks=window.workspace.bookmarks)
    target_index=next(i for i,p in enumerate(window.workspace.pages) if p.uid==duplicate);page=PdfReader(size_out).pages[target_index]
    assert tuple(round(float(v),3) for v in (page.mediabox.width,page.mediabox.height))==(792.,612.)
    window.undo();assert window.workspace.pages==before;window.redo()
    checks.append('page geometry real preview/crop/resize/selection and undo')
    project_path=directory/'v08-session.pdfwb';window.preview.set_zoom(1.5)
    with patch('pdf_workbench.ui.workspace_session.QFileDialog.getSaveFileName',return_value=(str(project_path),'')):window.save_project()
    wait_for(lambda:window.job is None);snapshot=read_project(project_path).snapshot;assert not window._is_dirty()
    original_source=source_path.read_bytes();source_path.unlink()
    window.workspace.rotate([duplicate],90);window.refresh()
    with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Cancel):window.new_workspace()
    assert window.workspace.pages
    with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Discard):window.open_project(path=project_path)
    wait_for(lambda:window.job is None)
    assert tuple(window.workspace.pages)==snapshot.state.pages and window.workspace.bookmarks==snapshot.state.bookmarks, (window.status.text(), window.workspace.pages, snapshot.state.pages, window.workspace.bookmarks, snapshot.state.bookmarks)
    assert window.preview.zoom_mode=='custom' and window.preview.zoom_factor==1.5;assert window.workspace.can_undo
    source_path.write_bytes(original_source)
    checks.append('project save/open/history/view and original independence')
    window.enable_background_services(data_dir=directory/'v08-local-data',prompt_recovery=False,check_updates=False);window._autosave();wait_for(lambda:window._autosave_job is None)
    store=window._recovery_store;paths=list(store.directory.glob('*.pdfwb'));assert paths and list_recoveries(store.directory)==()
    assert read_project(paths[0]).snapshot.state.pages==tuple(window.workspace.pages)
    protected=replace(window._project_snapshot(),state=replace(window.workspace.snapshot(),sources=tuple(replace(s,was_encrypted=True) for s in window.workspace.snapshot().sources)))
    encrypted=encode_project(protected,password='V08 recovery secret');encrypted_path=directory/'v08-protected.pdfwb';encrypted_path.write_bytes(encrypted)
    assert b'%PDF-' not in encrypted and decode_project(encrypted,password='V08 recovery secret')==protected
    with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Cancel):window.workspace.rotate([duplicate],90);window.refresh();window.close()
    assert window.isVisible() and not window._closing_requested
    checks.append('autosave/recovery/encrypted snapshot and safe close')
    metadata,_,_=update_fixture(target='windows' if sys.platform=='win32' else 'linux')
    release=release_from_metadata(metadata,__version__,'windows' if sys.platform=='win32' else 'linux')
    with patch('pdf_workbench.ui.update_dialog.check_latest',return_value=release),patch('pdf_workbench.ui.update_dialog.prepare_install') as prepare:
        dialog=UpdateDialog(window,__version__,directory/'v08-update-cache',window.theme.settings);dialog.show()
        wait_for(lambda:dialog.worker is None and dialog.release is not None)
        assert '99.0.0' in dialog.status.text();dialog.automatic.setChecked(False)
        assert window.theme.settings.value('updates/check_automatically',True,type=bool) is False
        dialog.grab().save(str(directory/'v08-updater.png'));dialog.reject();assert dialog.plan is None;prepare.assert_not_called();dialog.deleteLater()
    checks.append('updater fixture check/cancel/preferences without installation')
    window._autosave_timer.stop();window.saved_state=tuple(window.workspace.pages);window._saved_bookmarks=window.workspace.bookmarks;window._project_saved_key=window._content_key()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()
    return checks
