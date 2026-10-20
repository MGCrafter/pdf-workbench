"""Optional OCR adds searchable text while preserving original PDF appearance."""
from __future__ import annotations

from dataclasses import replace
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QCoreApplication, QEvent, QSettings, QThread, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter
from PyQt6.QtWidgets import QApplication, QDialogButtonBox
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.annotations import Rectangle
from pypdf.generic import NameObject, NumberObject, RectangleObject
from reportlab.pdfgen import canvas
from pdf_workbench.annotations import PageAnnotation
from pdf_workbench.core import OperationCancelled, PdfWorkbenchError, Workspace, export_pdf, load_source
from pdf_workbench import ocr
from pdf_workbench.ui.ocr_dialog import OcrDialog

APP = QApplication.instance() or QApplication([])


def wait_for(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while not predicate():
        APP.processEvents(); time.sleep(.005)
        if time.monotonic() > deadline: raise AssertionError("OCR worker did not finish")
    APP.processEvents()


class OcrSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()

    def tearDown(self): self.temp.cleanup()

    def test_download_is_explicit_pinned_verified_and_reuses_a_complete_cache(self):
        data = b"small pinned language fixture"
        lock = {"eng": {"name": "Englisch", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}}
        progress = []
        with patch.dict(ocr.LANGUAGE_PACKS, lock, clear=True), patch.object(ocr.urllib.request, "urlopen", return_value=io.BytesIO(data)) as remote:
            path = ocr.download_language("eng", self.root, progress=lambda *event: progress.append(event))
            self.assertEqual(path.read_bytes(), data)
            self.assertIn(ocr.TESSDATA_COMMIT, remote.call_args.args[0].full_url)
            self.assertEqual(progress[-1][0], 100)
            self.assertEqual(ocr.download_language("eng", self.root), path)
            self.assertEqual(remote.call_count, 1)
            license_data=(self.root/"tessdata_fast-LICENSE").read_bytes()
            self.assertEqual(len(license_data),11358)
            self.assertEqual(hashlib.sha256(license_data).hexdigest(),ocr.TESSDATA_LICENSE_SHA256)
            self.assertIn(ocr.TESSDATA_COMMIT,(self.root/"tessdata_fast-SOURCE.txt").read_text())
        self.assertEqual(list(self.root.glob("*.partial")), [])

    def test_cancel_hash_failure_and_existing_cache_never_publish_partial_or_replace_files(self):
        data = b"expected model bytes"
        lock = {"eng": {"name": "Englisch", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}}
        with patch.dict(ocr.LANGUAGE_PACKS, lock, clear=True):
            with patch.object(ocr.urllib.request, "urlopen", return_value=io.BytesIO(b"wrong bytes")):
                with self.assertRaises(ocr.OcrError): ocr.download_language("eng", self.root)
            self.assertFalse((self.root / "eng.traineddata").exists())
            cancelled = [False]
            with patch.object(ocr.urllib.request, "urlopen", return_value=io.BytesIO(data)):
                with self.assertRaises(OperationCancelled):
                    ocr.download_language("eng", self.root, lambda: cancelled[0], lambda *_: cancelled.__setitem__(0, True))
            self.assertFalse((self.root / "eng.traineddata").exists())
            original = self.root / "eng.traineddata"; original.write_bytes(b"local user model")
            with patch.object(ocr.urllib.request, "urlopen", return_value=io.BytesIO(data)):
                with self.assertRaises(ocr.OcrError): ocr.download_language("eng", self.root)
            self.assertEqual(original.read_bytes(), b"local user model")
            self.assertFalse(any(path.name.endswith(".partial") for path in self.root.iterdir()))

    def test_cancel_cleanup_preserves_replacements_of_model_and_temporary_files(self):
        data = b"expected model bytes"
        lock = {"eng": {"name": "Englisch", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}}
        for replaced in ("target", "temporary"):
            with self.subTest(replaced=replaced):
                directory = self.root/replaced; directory.mkdir()
                target = directory/"eng.traineddata"
                original = directory/"owned-file-retained"
                replacement = b"another writer's replacement"
                changed = []
                def replace(path):
                    try:
                        path.rename(original)
                    except PermissionError:
                        if os.name != "nt": raise
                        # Windows denies renaming this open descriptor. Cancel
                        # must then remove our own incomplete file normally.
                        changed.append((path, False))
                    else:
                        path.write_bytes(replacement); changed.append((path, True))
                def cancel():
                    if replaced == "target" and target.exists() and not changed:
                        replace(target)
                    return bool(changed)
                def progress(*_):
                    if replaced == "temporary" and not changed:
                        temporary = next(directory.glob(".eng-*.partial"))
                        replace(temporary)
                with patch.dict(ocr.LANGUAGE_PACKS, lock, clear=True), patch.object(ocr.urllib.request,"urlopen",return_value=io.BytesIO(data)), patch.object(ocr.os,"link",side_effect=OSError(errno.EOPNOTSUPP,"no hardlinks")):
                    with self.assertRaises(OperationCancelled):
                        ocr.download_language("eng",directory,cancel,progress)
                path, was_replaced = changed[0]
                if was_replaced:
                    self.assertTrue(original.exists())
                    self.assertEqual(path.read_bytes(),replacement)
                else:
                    self.assertEqual(os.name,"nt")
                    self.assertFalse(original.exists())
                    self.assertFalse(path.exists())

    def test_modeled_replacement_during_cancel_is_preserved_on_every_platform(self):
        # Some Windows opens forbid rename. Model the changed directory entry
        # as well, so the replacement-identity guard is exercised there too.
        data = b"expected model bytes"
        lock = {"eng": {"name": "Englisch", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}}
        real_lstat = Path.lstat
        for replaced in ("target", "temporary"):
            with self.subTest(replaced=replaced):
                directory = self.root/replaced; directory.mkdir()
                target = directory/"eng.traineddata"
                changed = []
                def namespace_status(path,*args,**kwargs):
                    status = real_lstat(path,*args,**kwargs)
                    if changed and path == changed[0]:
                        return types.SimpleNamespace(st_dev=status.st_dev,st_ino=status.st_ino+1,st_mode=status.st_mode)
                    return status
                def cancel():
                    if replaced == "target" and target.exists() and not changed:changed.append(target)
                    return bool(changed)
                def progress(*_):
                    if replaced == "temporary" and not changed:changed.append(next(directory.glob(".eng-*.partial")))
                with patch.dict(ocr.LANGUAGE_PACKS,lock,clear=True),patch.object(ocr.urllib.request,"urlopen",return_value=io.BytesIO(data)),patch.object(ocr.os,"link",side_effect=OSError(errno.EOPNOTSUPP,"no hardlinks")),patch.object(Path,"lstat",namespace_status):
                    with self.assertRaises(OperationCancelled):ocr.download_language("eng",directory,cancel,progress)
                self.assertTrue(changed[0].is_file())

    def test_notice_write_failure_preserves_a_concurrent_replacement(self):
        target = self.root/"tessdata_fast-LICENSE"
        retained = self.root/"owned-license-retained"
        replacement = b"another writer's license note"
        real_fsync = os.fsync
        renamed = []
        def fail_after_replacement(descriptor):
            if target.exists():
                try:
                    target.rename(retained)
                except PermissionError:
                    if os.name != "nt": raise
                    renamed.append(False)
                else:
                    target.write_bytes(replacement); renamed.append(True)
                raise OSError("simulated write failure")
            real_fsync(descriptor)
        with patch.object(ocr.os,"link",side_effect=OSError(errno.EOPNOTSUPP,"no hardlinks")),patch.object(ocr.os,"fsync",side_effect=fail_after_replacement):
            with self.assertRaises(OSError):ocr._install_model_notices(self.root)
        if renamed[0]:
            self.assertEqual(target.read_bytes(),replacement)
            self.assertEqual(retained.read_bytes(),ocr.TESSDATA_LICENSE_TEXT.encode())
        else:
            self.assertEqual(os.name,"nt")
            self.assertFalse(target.exists())
            self.assertFalse(retained.exists())
        self.assertEqual(list(self.root.glob("*.partial")),[])

    def test_modeled_notice_replacement_survives_a_failed_write_on_every_platform(self):
        target = self.root/"tessdata_fast-LICENSE"
        real_fsync,real_lstat = os.fsync,Path.lstat
        changed = []
        def namespace_status(path,*args,**kwargs):
            status = real_lstat(path,*args,**kwargs)
            if changed and path == target:
                return types.SimpleNamespace(st_dev=status.st_dev,st_ino=status.st_ino+1,st_mode=status.st_mode)
            return status
        def fail_after_replacement(descriptor):
            if target.exists():
                changed.append(True)
                raise OSError("simulated write failure after namespace replacement")
            real_fsync(descriptor)
        with patch.object(ocr.os,"link",side_effect=OSError(errno.EOPNOTSUPP,"no hardlinks")),patch.object(ocr.os,"fsync",side_effect=fail_after_replacement),patch.object(Path,"lstat",namespace_status):
            with self.assertRaises(OSError):ocr._install_model_notices(self.root)
        self.assertTrue(changed)
        self.assertTrue(target.is_file())
        self.assertEqual(list(self.root.glob("*.partial")),[])

    def test_cleanup_requires_a_regular_file_with_a_usable_identity(self):
        candidate = Mock()
        for inode, kind in ((0,stat.S_IFREG),(12,stat.S_IFLNK),(12,stat.S_IFDIR)):
            with self.subTest(inode=inode,kind=kind):
                candidate.lstat.return_value=types.SimpleNamespace(st_dev=4,st_ino=inode,st_mode=kind)
                ocr._unlink_owned(candidate,(4,inode))
                candidate.unlink.assert_not_called()

    def test_external_engine_does_not_inherit_frozen_library_search_paths(self):
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/app/_internal", "LD_LIBRARY_PATH_ORIG": "/system/library"}):
            self.assertEqual(ocr.external_environment()["LD_LIBRARY_PATH"], "/system/library")
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/app/_internal"}, clear=True), patch.object(ocr.sys, "frozen", True, create=True):
            self.assertNotIn("LD_LIBRARY_PATH", ocr.external_environment())

    def test_frozen_windows_restores_the_dll_directory_even_when_process_start_fails(self):
        original=r'C:\PDFWorkbench\_internal'
        def get_directory(size,buffer):
            if buffer is not None:buffer.value=original
            return len(original)
        kernel=types.SimpleNamespace(GetDllDirectoryW=Mock(side_effect=get_directory),SetDllDirectoryW=Mock(return_value=True))
        for fails in (False,True):
            with self.subTest(process_start_fails=fails):
                kernel.SetDllDirectoryW.reset_mock()
                child=object()
                with patch.object(ocr.sys,"platform","win32"),patch.object(ocr.sys,"frozen",True,create=True),patch.object(ocr.ctypes,"windll",types.SimpleNamespace(kernel32=kernel),create=True),patch.object(ocr.subprocess,"Popen",side_effect=OSError("cannot start") if fails else None,return_value=child):
                    if fails:
                        with self.assertRaises(OSError):ocr._external_popen(['tesseract','--version'])
                    else:self.assertIs(ocr._external_popen(['tesseract','--version']),child)
                self.assertEqual([call.args[0] for call in kernel.SetDllDirectoryW.call_args_list],[None,original])

    def test_child_timeout_and_cancel_terminate_the_process(self):
        class Child:
            returncode = None
            terminated = False
            def poll(self): return self.returncode
            def terminate(self): self.terminated = True; self.returncode = -15
            def wait(self, timeout=None): return self.returncode
        engine = ocr.OcrEngine(Path("test-tesseract"), "tesseract 5.5.3", None, None, ())
        for cancellation in (False, True):
            child = Child()
            with self.subTest(cancel=cancellation), patch.object(ocr.subprocess, "Popen", return_value=child):
                with self.assertRaises(OperationCancelled if cancellation else ocr.OcrError):
                    ocr._run_tesseract(engine, self.root / "in.png", self.root / "out", ("eng",), self.root, 300, .001, lambda: cancellation)
                self.assertTrue(child.terminated)

    def test_dialog_requires_engine_languages_and_a_new_destination_without_downloading(self):
        font = self.root / "pdf.ttf"; font.write_bytes(b"font fixture")
        engine = ocr.OcrEngine(Path("test-tesseract"), "tesseract 5.5.3", self.root, font, ("eng",))
        with patch("pdf_workbench.ui.ocr_dialog.discover_engine", return_value=engine), patch("pdf_workbench.ui.ocr_dialog.download_language") as download:
            settings = QSettings(str(self.root/"ocr.ini"), QSettings.Format.IniFormat)
            dialog = OcrDialog(None, 3, 1, cache_dir=self.root, settings=settings)
            try:
                wait_for(lambda: dialog.engine_job is None)
                save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
                self.assertFalse(save.isEnabled())
                dialog.deu.setChecked(False); dialog.output.setText(str(self.root / "searchable.pdf"))
                self.assertTrue(save.isEnabled())
                dialog.scope.setCurrentIndex(1); dialog.accept()
                self.assertEqual(dialog.request.scope, "selected")
                self.assertEqual(dialog.request.languages, ("eng",))
                self.assertEqual(dialog.request.destination, self.root / "searchable.pdf")
                download.assert_not_called()
            finally:
                dialog.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_engine_choice_is_restored_and_slow_probe_remains_cancelable(self):
        executable = self.root/"local-tesseract"; executable.write_bytes(b"engine fixture")
        engine = ocr.OcrEngine(executable, "tesseract 5.5.3", self.root, None, ())
        settings = QSettings(str(self.root/"ocr.ini"), QSettings.Format.IniFormat)
        with patch("pdf_workbench.ui.ocr_dialog.discover_engine", return_value=engine):
            first = OcrDialog(None, 2, 0, cache_dir=self.root, executable=executable, settings=settings)
            wait_for(lambda: first.engine_job is None)
            self.assertEqual(settings.value("ocr/executable"), str(executable))
            first.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            restored = OcrDialog(None, 2, 0, cache_dir=self.root, settings=settings)
            wait_for(lambda: restored.engine_job is None)
            self.assertEqual(restored.engine_path.text(), str(executable))
            restored.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        started = threading.Event()
        def slow_probe(*_, cancel=None):
            started.set()
            while not cancel(): time.sleep(.01)
            raise OperationCancelled("cancelled")
        with patch("pdf_workbench.ui.ocr_dialog.discover_engine", side_effect=slow_probe):
            dialog = OcrDialog(None, 2, 0, cache_dir=self.root, settings=settings)
            wait_for(started.is_set)
            self.assertIsNotNone(dialog.engine_job)
            dialog.reject()
            wait_for(lambda: dialog.engine_job is None)
            self.assertIsNone(dialog.request)
            dialog.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_standard_windows_install_is_found_without_modifying_path(self):
        executable = self.root/"Tesseract-OCR/tesseract.exe"
        executable.parent.mkdir(); executable.write_bytes(b"engine fixture")
        (self.root/"eng.traineddata").write_bytes(b"system language fixture")
        responses = [("tesseract 5.5.3\n", ""), (f'List of available languages in "{self.root}" (1):\neng\n', "")]
        with patch.object(ocr.sys, "platform", "win32"), patch.dict(os.environ, {"ProgramFiles":str(self.root)}), patch.object(ocr.shutil, "which", return_value=None), patch.object(ocr, "_capture_tool", side_effect=responses):
            engine = ocr.discover_engine(cache_dir=self.root)
        self.assertEqual(engine.executable, executable)
        self.assertIn("eng", engine.languages)

    def test_supported_distro_engine_4_1_is_accepted_but_older_versions_are_rejected(self):
        executable = self.root/"tesseract"; executable.write_bytes(b"engine fixture")
        for version in ("4.1.1", "5.5.3", "10.0.0"):
            with self.subTest(version=version), patch.object(ocr,"_capture_tool",side_effect=[(f"tesseract {version}\n", ""),("eng\n", "")]):
                self.assertEqual(ocr.discover_engine(executable,self.root).version,f"tesseract {version}")
        for version in ("4.0.0","v4.0.0"):
            with self.subTest(version=version),patch.object(ocr,"_capture_tool",return_value=(f"tesseract {version}\n", "")),self.assertRaises(ocr.OcrError):
                ocr.discover_engine(executable,self.root)

    def test_real_ub_mannheim_version_prefix_and_portable_data_directory_are_accepted(self):
        executable = self.root/"Tesseract-OCR/tesseract.exe"
        executable.parent.mkdir();executable.write_bytes(b"engine fixture")
        data = executable.parent/"tessdata";data.mkdir()
        font = data/"pdf.ttf";font.write_bytes(b"PDF font fixture")
        model = data/"eng.traineddata";model.write_bytes(b"system language fixture")
        responses = [("tesseract v5.4.0.20240606\n leptonica-1.84.1\n", ""),(f'List of available languages in "{data}" (1):\neng\n', "")]
        with patch.object(ocr,"_capture_tool",side_effect=responses):
            engine = ocr.discover_engine(executable,self.root/"empty-cache")
        self.assertEqual(engine.version,"tesseract v5.4.0.20240606")
        self.assertEqual(engine.executable,executable)
        self.assertEqual(engine.tessdata_dir,data)
        self.assertEqual(engine.pdf_font,font)
        self.assertEqual(ocr._language_files(engine,("eng",),self.root/"empty-cache"),{"eng":model})

    def test_windows_crlf_language_inventory_finds_installed_english_and_pdf_font(self):
        executable = self.root/"Tesseract-OCR/tesseract.exe"
        executable.parent.mkdir();executable.write_bytes(b"engine fixture")
        data = executable.parent/"tessdata";data.mkdir()
        font = data/"pdf.ttf";font.write_bytes(b"PDF font fixture")
        english = data/"eng.traineddata";english.write_bytes(b"installed English fixture")
        (data/"osd.traineddata").write_bytes(b"installed orientation fixture")
        inventory = f'List of available languages in "{data}" (2):\r\neng\r\nosd\r\n'
        for output in ((inventory,""),("",inventory)):
            with self.subTest(stream="stdout" if output[0] else "stderr"):
                responses = [("tesseract v5.4.0.20240606\r\n leptonica-1.84.1\r\n", ""),output]
                with patch.object(ocr,"_capture_tool",side_effect=responses):
                    engine = ocr.discover_engine(executable,self.root/"empty-cache")
                self.assertEqual(engine.version,"tesseract v5.4.0.20240606")
                self.assertEqual(engine.tessdata_dir,data)
                self.assertEqual(engine.pdf_font,font)
                self.assertEqual(engine.languages,("eng","osd"))
                self.assertEqual(ocr._language_files(engine,("eng",),self.root/"empty-cache"),{"eng":english})

    def test_4_1_data_without_a_directory_header_is_found_in_distro_and_configured_paths(self):
        for layout in ("ubuntu", "prefix-data", "prefix-parent"):
            with self.subTest(layout=layout):
                prefix = self.root/layout
                executable = prefix/"bin/tesseract"; executable.parent.mkdir(parents=True)
                executable.write_bytes(b"engine fixture")
                directory = prefix/("share/tesseract-ocr/4.00/tessdata" if layout == "ubuntu" else "custom/tessdata")
                directory.mkdir(parents=True)
                font = directory/"pdf.ttf"; font.write_bytes(b"system PDF font fixture")
                model = directory/"eng.traineddata"; model.write_bytes(b"system language fixture")
                cache = prefix/"empty-cache"
                setting = "" if layout == "ubuntu" else str(directory if layout == "prefix-data" else directory.parent)
                responses = [("tesseract 4.1.1\n", ""), ("List of available languages (1):\neng\n", "")]
                with patch.dict(os.environ,{"TESSDATA_PREFIX":setting}),patch.object(ocr,"_capture_tool",side_effect=responses):
                    engine = ocr.discover_engine(executable,cache)
                self.assertEqual(engine.tessdata_dir,directory)
                self.assertEqual(engine.pdf_font,font)
                self.assertEqual(engine.languages,("eng",))
                self.assertEqual(ocr._language_files(engine,("eng",),cache),{"eng":model})

    def test_a_visible_pdf_layer_is_rejected_instead_of_changing_the_document(self):
        path=self.root/"visible.pdf"
        pdf=canvas.Canvas(str(path));pdf.drawString(30,100,"Visible text is not an OCR layer");pdf.save()
        with self.assertRaises(ocr.OcrError): ocr._text_only_page(path)

    def test_canceling_a_language_download_closes_only_after_the_worker_stops(self):
        settings=QSettings(str(self.root/"ocr.ini"),QSettings.Format.IniFormat)
        started=threading.Event()
        def slow_download(*args,**kwargs):
            cancel=args[2]
            started.set()
            while not cancel(): time.sleep(.01)
            raise OperationCancelled("cancelled")
        with patch("pdf_workbench.ui.ocr_dialog.discover_engine",return_value=None),patch("pdf_workbench.ui.ocr_dialog.download_language",side_effect=slow_download):
            dialog=OcrDialog(None,2,0,cache_dir=self.root,settings=settings)
            wait_for(lambda:dialog.engine_job is None)
            dialog.start_download();wait_for(started.is_set)
            job=dialog.download_job;dialog.reject()
            wait_for(lambda:dialog.download_job is None)
            self.assertFalse(any(worker.isRunning() for worker in dialog.findChildren(QThread)))
            self.assertIsNone(dialog.request)
            dialog.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


class RealTesseractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = Path(os.environ.get("PDF_WORKBENCH_OCR_TEST_TESSDATA", ocr.default_cache_dir()))
        cls.engine = ocr.discover_engine(cache_dir=cls.cache)
        if cls.engine is None or "eng" not in cls.engine.languages or cls.engine.pdf_font is None:
            if os.environ.get("PDF_WORKBENCH_REQUIRE_OCR") == "1":
                raise AssertionError("Required OCR engine/English/pdf.ttf is missing")
            raise unittest.SkipTest("Optional Tesseract with English and pdf.ttf is not installed; no download is started by tests.")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        image = QImage(1800, 500, QImage.Format.Format_RGB32); image.fill(Qt.GlobalColor.white)
        painter = QPainter(image); painter.setPen(QColor("#152840")); painter.setFont(QFont("DejaVu Sans", 68))
        painter.drawText(60, 175, "LOCAL OCR TEST 123")
        painter.setFont(QFont("DejaVu Sans", 45)); painter.drawText(60, 320, "Original graphics stay sharp."); painter.end()
        self.scan = self.root / "scan.png"; image.save(str(self.scan))
        self.source_path = self.root / "scan.pdf"
        pdf = canvas.Canvas(str(self.source_path), pagesize=(460, 330))
        pdf.drawImage(str(self.scan), 65, 140, width=360, height=100)
        pdf.setStrokeColorRGB(.8, .1, .1); pdf.setLineWidth(.7); pdf.rect(60, 130, 370, 120)
        pdf.showPage(); pdf.setFont("Helvetica", 15); pdf.drawString(70, 220, "ALREADY SEARCHABLE"); pdf.save()
        reader = PdfReader(self.source_path); writer = PdfWriter()
        for page in reader.pages:
            page.cropbox = RectangleObject([40, 70, 440, 290]); writer.add_page(page)
        writer.add_annotation(0, Rectangle((68, 145, 110, 165), interior_color="ffeedd"))
        writer.write(self.source_path)
        self.original = self.source_path.read_bytes()
        self.workspace = Workspace(); self.workspace.add_sources([load_source(self.source_path)])
        self.workspace.add_annotation(self.workspace.pages[0].uid, PageAnnotation("ocr-note", "comment", (.75,.7,.80,.75), "Keep editable"))

    def tearDown(self):
        self.assertEqual(self.source_path.read_bytes(), self.original)
        self.temp.cleanup()

    def test_real_text_only_ocr_preserves_vectors_images_annotations_and_pixels(self):
        base, output = self.root / "base.pdf", self.root / "searchable.pdf"
        export_pdf(self.workspace.sources, self.workspace.pages, base)
        result = ocr.export_searchable_pdf(self.workspace.sources, self.workspace.pages, output,
                                           engine=self.engine, languages=("eng",), cache_dir=self.cache, dpi=200)
        self.assertEqual((result.processed_pages, result.skipped_pages), (1, 1))
        before, after = PdfReader(base), PdfReader(output)
        self.assertIn("LOCAL", after.pages[0].extract_text())
        self.assertIn("OCR TEST 123", after.pages[0].extract_text())
        self.assertIn("ALREADY SEARCHABLE", after.pages[1].extract_text())
        self.assertEqual(after.pages[0].cropbox, before.pages[0].cropbox)
        self.assertEqual(len(after.pages[0]["/Annots"]), 2)
        self.assertEqual(load_source(output).page_annotations[0][0].text, "Keep editable")
        def images(page):
            resources = page["/Resources"].get("/XObject", {})
            return sorted(hashlib.sha256(ref.get_object().get_data()).hexdigest() for ref in resources.values() if ref.get_object().get("/Subtype") == "/Image")
        self.assertEqual(images(before.pages[0]), images(after.pages[0]))
        if shutil_which := __import__("shutil").which("pdftoppm"):
            for name, path in (("base", base), ("ocr", output)):
                subprocess.run([shutil_which, "-cropbox", "-r", "100", "-png", str(path), str(self.root/name)], check=True, capture_output=True)
            for index in (1, 2):
                self.assertEqual(QImage(str(self.root/f"base-{index}.png")), QImage(str(self.root/f"ocr-{index}.png")))

    def test_rotated_cropped_userunit_geometry_remains_searchable_and_visually_identical(self):
        reader = PdfReader(self.source_path); writer = PdfWriter()
        page = writer.add_page(reader.pages[0]); page.rotate(90); page[NameObject("/UserUnit")] = NumberObject(2)
        rotated_path = self.root / "rotated.pdf"; writer.write(rotated_path)
        workspace = Workspace(); workspace.add_sources([load_source(rotated_path)])
        output = self.root / "rotated-ocr.pdf"
        result = ocr.export_searchable_pdf(workspace.sources, workspace.pages, output,
                                           engine=self.engine, languages=("eng",), cache_dir=self.cache, dpi=150)
        self.assertEqual(result.processed_pages, 1)
        after = PdfReader(output).pages[0]
        self.assertEqual(after["/Rotate"], 90); self.assertEqual(after["/UserUnit"], 2)
        self.assertEqual(after.cropbox, page.cropbox)
        self.assertIn("LOCAL", after.extract_text())
        tool = __import__("shutil").which("pdftoppm")
        if tool:
            for label, path in (("rotated-base", rotated_path), ("rotated-result", output)):
                subprocess.run([tool,"-cropbox","-r","90","-png",str(path),str(self.root/label)],check=True,capture_output=True)
            self.assertEqual(QImage(str(self.root/"rotated-base-1.png")),QImage(str(self.root/"rotated-result-1.png")))

    def test_german_and_english_models_recognize_umlauts_in_a_local_scan(self):
        if 'deu' not in self.engine.languages:self.skipTest('Optional German model is not installed')
        image=QImage(1800,450,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.white)
        painter=QPainter(image);painter.setFont(QFont('DejaVu Sans',70));painter.setPen(Qt.GlobalColor.black)
        painter.drawText(70,200,'Grüße für Österreich');painter.end()
        scan=self.root/'german.png';image.save(str(scan))
        source=self.root/'german.pdf';pdf=canvas.Canvas(str(source),pagesize=(450,280))
        pdf.drawImage(str(scan),30,100,width=390,height=97.5);pdf.save()
        workspace=Workspace();workspace.add_sources([load_source(source)])
        output=self.root/'german-searchable.pdf'
        ocr.export_searchable_pdf(workspace.sources,workspace.pages,output,engine=self.engine,
                                  languages=('deu','eng'),cache_dir=self.cache,dpi=250)
        text=PdfReader(output).pages[0].extract_text()
        self.assertIn('Grüße',text);self.assertIn('Österreich',text)

    def test_export_cancel_and_existing_destination_preserve_every_original(self):
        output = self.root / "cancelled.pdf"
        with self.assertRaises(OperationCancelled):
            ocr.export_searchable_pdf(self.workspace.sources, self.workspace.pages, output,
                                      engine=self.engine, languages=("eng",), cache_dir=self.cache, cancel=lambda: True)
        self.assertFalse(output.exists())
        output.write_bytes(b"existing output")
        with self.assertRaises(PdfWorkbenchError):
            ocr.export_searchable_pdf(self.workspace.sources, self.workspace.pages, output,
                                      engine=self.engine, languages=("eng",), cache_dir=self.cache)
        self.assertEqual(output.read_bytes(), b"existing output")
        self.assertFalse(list(self.root.glob(".pdf-workbench-ocr-*")))

    def test_cancel_after_render_removes_staging_and_does_not_publish_a_file(self):
        output = self.root/"cancel-in-progress.pdf"
        with patch.object(ocr,"_run_tesseract",side_effect=OperationCancelled("Stopped during recognition")):
            with self.assertRaises(OperationCancelled):
                ocr.export_searchable_pdf(self.workspace.sources,self.workspace.pages,output,
                                          engine=self.engine,languages=("eng",),cache_dir=self.cache)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob(".pdf-workbench-ocr-*")))

    def test_text_selection_geometry_handles_all_intrinsic_workspace_rotations_and_negative_crop(self):
        from PyQt6.QtPdf import QPdfDocument
        source=PdfReader(self.source_path)
        expected=None
        for intrinsic in (0,90,180,270):
            for workspace_rotation in (0,90,180,270):
                with self.subTest(intrinsic=intrinsic,workspace=workspace_rotation):
                    writer=PdfWriter();page=writer.add_page(source.pages[0])
                    page.mediabox=RectangleObject([-200,-100,280,380])
                    page.cropbox=RectangleObject([-160,-60,240,340])
                    page[NameObject('/UserUnit')]=NumberObject(2)
                    page.add_transformation(Transformation().translate(-245,-190).rotate(intrinsic+workspace_rotation).translate(40,140))
                    page.rotate(intrinsic)
                    path=self.root/f'matrix-{intrinsic}-{workspace_rotation}.pdf';writer.write(path)
                    workspace=Workspace();workspace.add_sources([load_source(path)])
                    workspace.pages[0]=replace(workspace.pages[0],rotation=workspace_rotation)
                    output=self.root/f'ocr-{intrinsic}-{workspace_rotation}.pdf'
                    ocr.export_searchable_pdf(workspace.sources,workspace.pages,output,
                                              engine=self.engine,languages=('eng',),cache_dir=self.cache,dpi=150)
                    document=QPdfDocument(None)
                    try:
                        document.load(str(output));selection=document.getAllText(0)
                        self.assertIn('LOCAL',selection.text())
                        boxes=[polygon.boundingRect().normalized() for polygon in selection.bounds()]
                        self.assertTrue(boxes)
                        bounds=boxes[0]
                        for box in boxes[1:]: bounds=bounds.united(box)
                        size=document.pagePointSize(0)
                        actual=(bounds.left()/size.width(),bounds.top()/size.height(),bounds.right()/size.width(),bounds.bottom()/size.height())
                        if expected is None: expected=actual
                        for value,reference in zip(actual,expected): self.assertAlmostEqual(value,reference,delta=.015)
                    finally: document.close()


if __name__ == "__main__": unittest.main(verbosity=2)
