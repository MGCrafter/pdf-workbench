"""Archive containment, user-file preservation and real start/rollback swaps."""
from dataclasses import replace
import hashlib,io,json,os
from pathlib import Path
import shutil,stat,subprocess,sys,tarfile,tempfile,time,unittest,zipfile
from unittest.mock import patch

from pdf_workbench.core import OperationCancelled
from pdf_workbench.updates import DownloadedUpdate,ReleaseAsset,UpdateError,UpdateRelease
from pdf_workbench.update_installer import (MANIFEST,InstallPlan,apply_install,discard_install,extract_update,
    launch_install,load_plan,prepare_install,validate_bundle,write_bundle_manifest)


def bundle(root,version,*,fail_start=False):
    root.mkdir();(root/"_internal").mkdir();(root/"_internal/APP_PROVENANCE.json").write_text(json.dumps({"app_version":version}))
    script="""#!/usr/bin/env python3
import json,sys,pathlib
root=pathlib.Path(__file__).parent
if len(sys.argv)>1 and sys.argv[1]=='--update-probe':
 path,token=sys.argv[2:4];p=pathlib.Path(path);plan=json.loads(p.read_text());p.with_name('probe-ready.json').write_text(json.dumps({'token':token,'version':plan['new_version']}))
elif len(sys.argv)>1 and sys.argv[1]=='--update-started':
 path,token=sys.argv[2:4]
 if FAIL_START:sys.exit(7)
 p=pathlib.Path(path);plan=json.loads(p.read_text());p.with_name('gui-started.json').write_text(json.dumps({'token':token,'version':plan['new_version']}))
else:(root/'restarted-old.txt').write_text('old version restarted')
""".replace("FAIL_START",str(fail_start))
    exe=root/"PDFWorkbench";exe.write_text(script);exe.chmod(0o755)
    (root/"_internal/library.bin").write_bytes(b"native runtime "+version.encode());write_bundle_manifest(root,version);return root


def archive(bundle_path,target="linux",directory=None):
    directory=bundle_path.parent if directory is None else directory
    version=json.loads((bundle_path/MANIFEST).read_text())["app_version"]
    name=f"PDFWorkbench-v{version}-{target}-x86_64"+(".zip" if target=="windows" else ".tar.gz");path=directory/name
    if target=="linux":
        with tarfile.open(path,"w:gz") as tar:tar.add(bundle_path,arcname="PDFWorkbench")
    else:
        with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as zipped:
            for file in bundle_path.rglob("*"):
                if file.is_file():zipped.write(file,"PDFWorkbench/"+file.relative_to(bundle_path).as_posix())
    release=UpdateRelease(version,target,ReleaseAsset(name,"https://github.com/unused",path.stat().st_size,hashlib.sha256(path.read_bytes()).hexdigest()),ReleaseAsset("SHA256SUMS","",1,"0"*64),"")
    return DownloadedUpdate(release,path)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.processes=[]
        original=__import__('pdf_workbench.update_installer',fromlist=['_start'])._start
        def tracked(*args,**kwargs):
            child=original(*args,**kwargs);self.processes.append(child);return child
        self.start_patch=patch('pdf_workbench.update_installer._start',side_effect=tracked);self.start_patch.start()
    def tearDown(self):
        for child in self.processes:
            try:child.wait(timeout=3)
            except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=3)
        self.start_patch.stop();self.temp.cleanup()

    def prepare(self,*,fail=False):
        old=bundle(self.root/"installed","0.7.99");new=bundle(self.root/"publisher","0.8.0",fail_start=fail);download=archive(new)
        with patch.object(sys,"frozen",True,create=True),patch.object(sys,"executable",str(old/"PDFWorkbench")),patch("pdf_workbench.__version__","0.7.99"),patch("pdf_workbench.update_installer.native_platform",return_value="linux"):
            plan=prepare_install(download)
        return old,replace(plan,old_pid=999999999)

    def test_manifest_covers_every_file_detects_modified_owned_files_and_allows_user_files(self):
        root=bundle(self.root/"bundle","0.8.0");manifest=validate_bundle(root);self.assertNotIn(MANIFEST,manifest["files"])
        (root/"my-project.pwp").write_text("user data")
        with self.assertRaises(UpdateError):validate_bundle(root)
        validate_bundle(root,allow_foreign=True)
        (root/"_internal/library.bin").write_bytes(b"changed")
        with self.assertRaises(UpdateError):validate_bundle(root,allow_foreign=True)

    def test_manifest_and_archive_reject_links_case_duplicates_and_device_names(self):
        root=bundle(self.root/"bundle","0.8.0")
        try:(root/"link").symlink_to(root/"PDFWorkbench")
        except OSError:pass
        else:
            with self.assertRaises(UpdateError):write_bundle_manifest(root,"0.8.0")
            (root/"link").unlink()
        bad_names=("../outside","PDFWorkbench/../../outside","/absolute","PDFWorkbench/C:/oops","PDFWorkbench/back\\slash","PDFWorkbench/CON.txt","PDFWorkbench/dot. ")
        for index,name in enumerate(bad_names):
            path=self.root/f"bad{index}.zip"
            with zipfile.ZipFile(path,"w") as z:z.writestr(name,b"x")
            release=UpdateRelease("0.8.0","windows",ReleaseAsset(path.name,"",path.stat().st_size,hashlib.sha256(path.read_bytes()).hexdigest()),ReleaseAsset("","",0,""),"")
            with self.subTest(name=name),self.assertRaises(UpdateError):extract_update(DownloadedUpdate(release,path))
        self.assertFalse((self.root/"outside").exists())

    def test_tar_hardlinks_symlinks_sparse_and_zip_collision_are_rejected(self):
        for kind in (tarfile.SYMTYPE,tarfile.LNKTYPE,tarfile.CHRTYPE):
            path=self.root/(kind.decode()+".tar.gz")
            with tarfile.open(path,"w:gz") as tar:
                info=tarfile.TarInfo("PDFWorkbench/link");info.type=kind;info.linkname="../../outside";tar.addfile(info)
            release=UpdateRelease("0.8.0","linux",ReleaseAsset(path.name,"",path.stat().st_size,hashlib.sha256(path.read_bytes()).hexdigest()),ReleaseAsset("","",0,""),"")
            with self.subTest(kind=kind),self.assertRaises(UpdateError):extract_update(DownloadedUpdate(release,path))
        for names in (("PDFWorkbench/file","PDFWorkbench/file"),("PDFWorkbench/A/x","PDFWorkbench/a/y"),("PDFWorkbench/a","PDFWorkbench/a/b")):
            path=self.root/"collision.zip"
            with zipfile.ZipFile(path,"w") as zipped:
                for name in names:zipped.writestr(name,b"x")
            release=UpdateRelease("0.8.0","windows",ReleaseAsset(path.name,"",path.stat().st_size,hashlib.sha256(path.read_bytes()).hexdigest()),ReleaseAsset("","",0,""),"")
            with self.assertRaises(UpdateError):extract_update(DownloadedUpdate(release,path))

    def test_archive_tamper_bomb_and_cancel_leave_no_partial_candidate(self):
        root=bundle(self.root/"bundle","0.8.0");download=archive(root)
        download.archive.write_bytes(download.archive.read_bytes()+b"tampered")
        with self.assertRaises(UpdateError):extract_update(download)
        with self.assertRaises(OperationCancelled):extract_update(download,cancel=lambda:True)
        path=self.root/"bomb.zip"
        with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as zipped:zipped.writestr("PDFWorkbench/bomb",b"x"*1000000)
        release=UpdateRelease("0.8.0","windows",ReleaseAsset(path.name,"",path.stat().st_size,hashlib.sha256(path.read_bytes()).hexdigest()),ReleaseAsset("","",0,""),"")
        with self.assertRaises(UpdateError):extract_update(DownloadedUpdate(release,path))
        self.assertEqual(list(self.root.glob("unpacked-*")),[])

    @unittest.skipIf(os.name=="nt","Executable script fixture is POSIX; real frozen EXE is tested separately on Windows")
    def test_prepare_real_probe_swap_preserves_latest_user_save_and_backup(self):
        old,plan=self.prepare();(old/"project.pwp").write_text("saved after preparation")
        original_start=__import__('pdf_workbench.update_installer',fromlist=['_start'])._start
        def tracked(*args,**kwargs):
            child=original_start(*args,**kwargs);self.processes.append(child);return child
        with patch("pdf_workbench.update_installer._start",side_effect=tracked):self.assertTrue(apply_install(plan,start_timeout=5))
        self.assertEqual((old/"project.pwp").read_text(),"saved after preparation")
        self.assertEqual((Path(plan.backup_dir)/"project.pwp").read_text(),"saved after preparation")
        self.assertEqual(validate_bundle(old,allow_foreign=True)["app_version"],"0.8.0")
        self.assertEqual(json.loads(plan.status_path.read_text())["state"],"installed")

    @unittest.skipIf(os.name=="nt","Executable script fixture is POSIX; real frozen EXE is tested separately on Windows")
    def test_failed_real_start_rolls_back_and_restarts_previous_program(self):
        old,plan=self.prepare(fail=True);before=(old/"PDFWorkbench").read_bytes();(old/"documents").mkdir();(old/"documents/private.pdf").write_bytes(b"PRIVATE ORIGINAL")
        original_start=__import__('pdf_workbench.update_installer',fromlist=['_start'])._start
        def tracked(*args,**kwargs):
            child=original_start(*args,**kwargs);self.processes.append(child);return child
        with patch("pdf_workbench.update_installer._start",side_effect=tracked):self.assertFalse(apply_install(plan,start_timeout=5))
        for child in self.processes:child.wait(timeout=3)
        self.assertEqual((old/"PDFWorkbench").read_bytes(),before);self.assertEqual((old/"documents/private.pdf").read_bytes(),b"PRIVATE ORIGINAL")
        self.assertTrue((old/"restarted-old.txt").exists());self.assertEqual(json.loads(plan.status_path.read_text())["state"],"rolled_back")

    def test_source_checkout_is_never_replaced(self):
        new=bundle(self.root/"publisher","0.8.0");download=archive(new)
        with patch.object(sys,"frozen",False,create=True),self.assertRaisesRegex(UpdateError,"Quellcode"):
            prepare_install(download)

    @unittest.skipIf(os.name=="nt","POSIX probe fixture")
    def test_foreign_collision_and_changed_old_manifest_abort_before_swap(self):
        old=bundle(self.root/"installed","0.7.99");new=bundle(self.root/"publisher","0.8.0");(new/"project.pwp").write_text("new application asset");write_bundle_manifest(new,"0.8.0");download=archive(new)
        (old/"project.pwp").write_text("MY PROJECT")
        with patch.object(sys,"frozen",True,create=True),patch.object(sys,"executable",str(old/"PDFWorkbench")),patch("pdf_workbench.__version__","0.7.99"),patch("pdf_workbench.update_installer.native_platform",return_value="linux"),self.assertRaisesRegex(UpdateError,"eigene Datei"):
            prepare_install(download)
        self.assertEqual((old/"project.pwp").read_text(),"MY PROJECT");self.assertEqual(list(self.root.glob(".installed.update-*")),[])

    @unittest.skipIf(os.name=="nt","POSIX probe fixture")
    def test_plan_token_paths_process_identity_and_cancel_are_validated(self):
        old,plan=self.prepare()
        with self.assertRaises(UpdateError):load_plan(plan.plan_path,"0"*64)
        data=json.loads(Path(plan.plan_path).read_text());data['candidate_dir']=str(self.root/'outside');Path(plan.plan_path).write_text(json.dumps(data))
        with self.assertRaises(UpdateError):load_plan(plan.plan_path,plan.token)
        data['candidate_dir']=plan.candidate_dir;Path(plan.plan_path).write_text(json.dumps(data))
        self.assertFalse(apply_install(replace(plan,old_pid=os.getpid()),wait_timeout=.1));self.assertTrue(old.is_dir());self.assertFalse(Path(plan.backup_dir).exists())

    @unittest.skipIf(os.name=="nt","POSIX probe fixture")
    def test_spaces_quotes_unicode_and_shell_metacharacters_remain_literal(self):
        directory=self.root/"Eigene Ä Dateien ' ; $(not-a-command)";directory.mkdir();previous=self.root;self.root=directory
        try:
            old,plan=self.prepare();self.assertTrue(apply_install(plan,start_timeout=5))
            self.assertTrue(old.is_dir());self.assertFalse((previous/"not-a-command").exists())
        finally:self.root=previous

    @unittest.skipIf(os.name=="nt","POSIX probe fixture")
    def test_failed_second_rename_restores_old_folder_and_preserves_foreign_files(self):
        old,plan=self.prepare();(old/"private.pdf").write_bytes(b"KEEP ME");original=Path.rename
        def rename(path,target):
            if path==Path(plan.candidate_dir):raise PermissionError("simulated locked destination")
            return original(path,target)
        with patch.object(Path,"rename",rename):self.assertFalse(apply_install(plan,start_timeout=3))
        self.assertEqual(validate_bundle(old,allow_foreign=True)["app_version"],"0.7.99");self.assertEqual((old/"private.pdf").read_bytes(),b"KEEP ME")


if __name__=="__main__":unittest.main()
