"""Password/size regression checks with real documents and Poppler rendering."""
from __future__ import annotations

import io
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageChops
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from pdf_workbench.compression import CompressionOptions
from pdf_workbench.core import (IncorrectPasswordError, OperationCancelled,
    PasswordRequiredError, PdfWorkbenchError, Workspace, export_pdf, load_source,
    preview_compression)


def make_text_pdf(path):
    document = canvas.Canvas(str(path), pagesize=(300, 500), pageCompression=0)
    for label in ("Password page one", "Second searchable page"):
        document.setFillColorRGB(.1, .2, .5)
        document.setFont("Helvetica", 17)
        document.drawString(20, 460, label)
        for line in range(150):
            document.line(20, 40 + line * 2, 280, 40 + line * 2)
        document.showPage()
    document.save()


class SecurityCompressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.path = self.directory / "original.pdf"
        make_text_pdf(self.path)
        self.original = self.path.read_bytes()
        self.workspace = Workspace()
        self.workspace.add_sources([load_source(self.path)])
        self.output = self.directory / "new.pdf"

    def tearDown(self):
        self.temp.cleanup()

    def export(self, **kwargs):
        return export_pdf(self.workspace.sources, self.workspace.pages, self.output, **kwargs)

    def protected_input(self):
        path = self.directory / "protected-input.pdf"
        writer = PdfWriter(clone_from=self.path)
        writer.encrypt("sensitive-input-password", algorithm="AES-256")
        writer.write(path)
        return path

    def test_password_required_wrong_password_and_decrypted_snapshot(self):
        path = self.protected_input()
        encrypted_original = path.read_bytes()
        with self.assertRaises(PasswordRequiredError):
            load_source(path)
        with self.assertRaises(IncorrectPasswordError) as error:
            load_source(path, password="do-not-display-this-secret")
        self.assertNotIn("do-not-display-this-secret", str(error.exception))
        source = load_source(path, password="sensitive-input-password")
        self.assertTrue(source.was_encrypted)
        self.assertEqual(source.original_size, len(encrypted_original))
        self.assertFalse(PdfReader(io.BytesIO(source.data)).is_encrypted)
        self.assertIn("Password page one", PdfReader(io.BytesIO(source.data)).pages[0].extract_text())
        self.assertNotIn("sensitive-input-password", repr(source))
        self.assertEqual(path.read_bytes(), encrypted_original)
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()),
                         ["original.pdf", "protected-input.pdf"])

    def test_aes256_export_correct_wrong_password_and_original_preservation(self):
        self.workspace.rotate([self.workspace.pages[0].uid], 90)
        self.export(password="a different secret")
        reader = PdfReader(self.output)
        self.assertTrue(reader.is_encrypted)
        self.assertEqual(reader.trailer["/Encrypt"]["/V"], 5)
        self.assertEqual(reader.trailer["/Encrypt"]["/R"], 6)
        self.assertEqual(reader.trailer["/Encrypt"]["/Length"], 256)
        self.assertFalse(reader.decrypt("wrong"))
        self.assertTrue(reader.decrypt("a different secret"))
        self.assertEqual(reader.pages[0].rotation, 90)
        self.assertEqual(len(reader.pages), 2)
        self.assertIn("Second searchable page", reader.pages[1].extract_text())
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(list(self.directory.glob(".pdf-workbench-*")))

    def test_opened_encrypted_snapshot_can_export_unencrypted_copy(self):
        path = self.protected_input()
        source = load_source(path, password="sensitive-input-password")
        workspace = Workspace()
        workspace.add_sources([source])
        export_pdf(workspace.sources, workspace.pages, self.output)
        self.assertFalse(PdfReader(self.output).is_encrypted)
        self.assertTrue(PdfReader(path).is_encrypted)

    def test_empty_or_truncated_password_refused_without_partial_output(self):
        for password in ("", "ä" * 64):
            with self.assertRaises(PdfWorkbenchError):
                self.export(password=password)
            self.assertFalse(self.output.exists())
            self.assertFalse(list(self.directory.glob(".pdf-workbench-*")))

    def test_password_export_preserves_existing_destination(self):
        self.output.write_bytes(b"keep me")
        with self.assertRaises(PdfWorkbenchError):
            self.export(password="secret")
        self.assertEqual(self.output.read_bytes(), b"keep me")

    def test_lossless_compression_measured_report_and_preview_creates_no_files(self):
        report = preview_compression(self.workspace.sources, self.workspace.pages, CompressionOptions())
        self.assertGreater(report.saved_bytes, 0)
        self.assertEqual([p.name for p in self.directory.iterdir()], ["original.pdf"])
        reports = []
        self.export(optimization=CompressionOptions(), compression_report=reports.append)
        saved = reports[0]
        self.assertEqual(saved.output_bytes, self.output.stat().st_size)
        self.assertGreater(saved.saved_percent, 0)
        self.assertEqual(saved.images_changed, 0)
        self.assertEqual(PdfReader(self.output).pages[0].extract_text(),
                         PdfReader(self.path).pages[0].extract_text())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_compression_with_encryption_reports_committed_size(self):
        reports = []
        self.export(password="combined", optimization=CompressionOptions(), compression_report=reports.append)
        self.assertEqual(reports[0].output_bytes, self.output.stat().st_size)
        self.assertGreater(reports[0].saved_bytes, 0)
        reader = PdfReader(self.output)
        self.assertTrue(reader.decrypt("combined"))
        self.assertIn("Password page one", reader.pages[0].extract_text())

    def test_cancel_during_compression_keeps_inputs_and_no_output(self):
        cancelled = [False]
        def progress(value, _):
            if value >= 85:
                cancelled[0] = True
        with self.assertRaises(OperationCancelled):
            self.export(optimization=CompressionOptions(), cancel=lambda: cancelled[0], progress=progress)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.path.read_bytes(), self.original)

    def image_source(self, *, transparent=False):
        path = self.directory / "scan.pdf"
        noise = random.Random(31).randbytes(1800 * 1600 * 3)
        image = Image.frombytes("RGB", (1800, 1600), noise)
        if transparent:
            image = image.convert("RGBA")
            image.putalpha(160)
        document = canvas.Canvas(str(path), pagesize=(300, 500))
        document.drawString(20, 475, "Text remains searchable")
        document.drawImage(ImageReader(image), 20, 50, 260, 400, mask="auto")
        document.save()
        workspace = Workspace()
        workspace.add_sources([load_source(path)])
        return path, workspace

    def test_lossy_reduces_image_dimensions_and_retains_text(self):
        path, workspace = self.image_source()
        original = path.read_bytes()
        reports = []
        export_pdf(workspace.sources, workspace.pages, self.output,
                   optimization=CompressionOptions("small"), compression_report=reports.append)
        self.assertEqual(reports[0].images_changed, 1)
        self.assertLess(reports[0].output_bytes, reports[0].baseline_bytes)
        reader = PdfReader(self.output)
        self.assertLessEqual(max(reader.pages[0].images[0].image.size), 1400)
        self.assertIn("Text remains searchable", reader.pages[0].extract_text())
        self.assertEqual(path.read_bytes(), original)

    def test_transparency_preserved_by_skipping_masked_image(self):
        path, workspace = self.image_source(transparent=True)
        before = PdfReader(path).pages[0].images[0].image.convert("RGBA").tobytes()
        reports = []
        export_pdf(workspace.sources, workspace.pages, self.output,
                   optimization=CompressionOptions("small"), compression_report=reports.append)
        self.assertEqual(reports[0].images_changed, 0)
        self.assertGreaterEqual(reports[0].images_skipped, 1)
        image = PdfReader(self.output).pages[0].images[0].image.convert("RGBA")
        self.assertEqual(image.tobytes(), before)

    def test_workspace_snapshot_replacement_is_undoable(self):
        old = next(iter(self.workspace.sources.values()))
        updated = replace(old, source_id="updated-source")
        uid = self.workspace.pages[0].uid
        self.workspace.replace_source(old.source_id, updated)
        self.assertEqual(self.workspace.pages[0].source_id, updated.source_id)
        self.assertEqual(self.workspace.pages[0].uid, uid)
        self.workspace.undo()
        self.assertEqual(self.workspace.pages[0].source_id, old.source_id)
        self.workspace.redo()
        self.assertEqual(self.workspace.pages[0].source_id, updated.source_id)
        with self.assertRaises(PdfWorkbenchError):
            self.workspace.replace_source(updated.source_id, replace(updated, page_count=1))

    @unittest.skipUnless(shutil.which("pdftoppm"), "Poppler not available")
    def test_poppler_lossless_pixels_match_and_aes_export_renders(self):
        self.export(optimization=CompressionOptions())
        def render(path, prefix, password=None):
            command = [shutil.which("pdftoppm"), "-f", "1", "-singlefile", "-r", "72", "-png"]
            if password is not None:
                command += ["-upw", password]
            subprocess.run([*command, str(path), str(prefix)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            return Image.open(str(prefix) + ".png").convert("RGB")
        first = render(self.path, self.directory / "before")
        after = render(self.output, self.directory / "after")
        self.assertIsNone(ImageChops.difference(first, after).getbbox())
        encrypted = self.directory / "encrypted.pdf"
        export_pdf(self.workspace.sources, self.workspace.pages, encrypted, password="renderer-test")
        protected = render(encrypted, self.directory / "protected", "renderer-test")
        self.assertIsNone(ImageChops.difference(first, protected).getbbox())


if __name__ == "__main__":
    unittest.main()
