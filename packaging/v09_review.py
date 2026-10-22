"""Independent v0.9 acceptance using real PDFs, with persistent evidence.

Run in a fresh work directory. This runner never overwrites a previous report or
operates on user documents. External-tool cases are explicitly skipped when the
actual tool is unavailable; that does not imply PDF/A platform acceptance.
"""
from __future__ import annotations

import argparse
import contextlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time
import traceback
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "packaging")]

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject
from pdf_workbench.core import Workspace, PdfWorkbenchError, OperationCancelled, export_pdf, load_source


def fixture(path, labels=("A1", "A2")):
    """Independent authoring: ordinary vector/text pages and an explicit blank."""
    writer = PdfWriter()
    from pypdf.generic import DictionaryObject
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
    for label in labels:
        page = writer.add_blank_page(width=300, height=500)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        data = (f"q 0.1 0.2 0.4 rg BT /F1 24 Tf 32 438 Td ({label}) Tj ET "
                "1 0 0 rg 12 470 18 18 re f 0 0.7 0 rg 250 20 30 30 re f "
                "0 0 0 rg BT /F1 12 Tf 32 100 Td (PUBLIC) Tj ET Q\n").encode("ascii") if label else b""
        stream.set_data(data); page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)
    return path


def workspace(path):
    result = Workspace(); result.add_sources([load_source(path)]); return result


def extracted(path):
    return [page.extract_text().strip() for page in PdfReader(path).pages]


def certificate(path, password):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PDF Workbench Test Only")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=2)).add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(True, True, False, False, False, True, True, False, False), critical=True)
            .sign(key, hashes.SHA256()))
    path.write_bytes(pkcs12.serialize_key_and_certificates(b"test-only", key, cert, None, serialization.BestAvailableEncryption(password.encode())))
    root = path.with_suffix(".pem"); root.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return root


class Acceptance(unittest.TestCase):
    base: Path
    verapdf: str | None = None
    ghostscript: str | None = None

    def setUp(self):
        self.directory = self.base / self._testMethodName
        self.directory.mkdir()
        self.source = fixture(self.directory / "source.pdf")
        self.original = self.source.read_bytes()
        self.ws = workspace(self.source)

    def tearDown(self):
        self.assertEqual(self.original, self.source.read_bytes(), "Original PDF changed")

    def test_batch_recipe_order_rotation_originals_and_collisions(self):
        from pdf_workbench.batch import BatchPreset, process_batch, save_preset, load_preset
        from pdf_workbench.page_marks import WatermarkOptions, PageNumberOptions
        second = fixture(self.directory / "second.pdf", ("B1",))
        preset = BatchPreset(name="Prüfung Ä", rotation=90, watermark=WatermarkOptions(text="REVIEW"), numbering=PageNumberOptions())
        saved = self.directory / "recipe.json"; save_preset(preset, saved)
        self.assertEqual(preset, load_preset(saved))
        before = second.read_bytes(); result = process_batch([self.source, second], self.directory, load_preset(saved))
        self.assertEqual(len(result.outputs), 2)
        self.assertEqual([p.rotation for p in PdfReader(result.outputs[0]).pages], [90, 90])
        text = extracted(result.outputs[0]); self.assertIn("A1", text[0]); self.assertIn("A2", text[1]); self.assertIn("REVIEW", text[0])
        output_bytes = [p.read_bytes() for p in result.outputs]
        with self.assertRaises(PdfWorkbenchError): process_batch([self.source, second], self.directory, preset)
        self.assertEqual(output_bytes, [p.read_bytes() for p in result.outputs]); self.assertEqual(before, second.read_bytes())

    def test_batch_cancel_retains_foreign_edits_and_cleans_own_output(self):
        from pdf_workbench.batch import BatchPreset, process_batch
        second = fixture(self.directory / "second.pdf", ("B1",)); cancelled = [False]
        altered = self.directory / "source-bearbeitet.pdf"
        def progress(value, message):
            if value >= 95 and altered.exists():
                altered.write_bytes(b"EXTERNAL USER EDIT"); cancelled[0] = True
        with self.assertRaises(OperationCancelled) as raised:
            process_batch([self.source, second], self.directory, BatchPreset(), cancel=lambda: cancelled[0], progress=progress)
        self.assertEqual(altered.read_bytes(), b"EXTERNAL USER EDIT")
        self.assertFalse((self.directory / "second-bearbeitet.pdf").exists())
        self.assertIn(str(altered), str(raised.exception)); self.assertFalse(list(self.directory.glob(".pdf-workbench-batch-*")))

    def test_image_text_exports_follow_final_selection_and_rotation(self):
        from pdf_workbench.workflows import export_images, export_text
        from PIL import Image
        first, second = self.ws.pages; self.ws.rotate([second.uid], 90)
        selected = [self.ws.pages[1], self.ws.pages[0]]
        outputs = export_images(self.ws.sources, selected, self.directory, dpi=72)
        self.assertEqual([Image.open(p).size for p in outputs], [(500, 300), (300, 500)])
        text = self.directory / "selection.txt"; export_text(self.ws.sources, selected, text)
        segments = text.read_text().split("\f\n"); self.assertIn("A2", segments[0]); self.assertIn("A1", segments[1])
        saved = [p.read_bytes() for p in outputs]
        with self.assertRaises(PdfWorkbenchError): export_images(self.ws.sources, selected, self.directory, dpi=72)
        with self.assertRaises(PdfWorkbenchError): export_text(self.ws.sources, selected, text)
        self.assertEqual(saved, [p.read_bytes() for p in outputs])
        export_pdf(self.ws.sources, selected, self.directory / "selected.pdf")

    def test_blank_and_duplicate_are_review_candidates_without_mutation(self):
        from pdf_workbench.workflows import find_page_candidates
        fixture_path = fixture(self.directory / "candidates.pdf", ("A1", "", "A1", "A2"))
        ws = workspace(fixture_path); before = ws.snapshot()
        candidates = find_page_candidates(ws.sources, ws.pages)
        self.assertEqual([p.page_number for p in candidates], [2, 3])
        self.assertIsNone(candidates[0].duplicate_of); self.assertEqual(candidates[1].duplicate_of, 1)
        self.assertTrue(all(c.preview_png.startswith(b"\x89PNG") for c in candidates)); self.assertEqual(before, ws.snapshot())

    def test_duplex_reverse_odd_fronts_and_one_undo(self):
        from pdf_workbench.workflows import duplex_order
        scan = fixture(self.directory / "duplex.pdf", ("F1", "F2", "F3", "B2", "B1")); ws = workspace(scan)
        initial = tuple(ws.pages); ids = [p.uid for p in initial]
        ws._set(duplex_order(ws.pages, ids[:3], ids[3:], reverse_backs=True, backs_rotation=180))
        out = self.directory / "ordered.pdf"; export_pdf(ws.sources, ws.pages, out)
        self.assertEqual([text.splitlines()[0] for text in extracted(out)], ["F1", "B1", "F2", "B2", "F3"])
        self.assertEqual([p.rotation for p in PdfReader(out).pages], [0, 180, 0, 180, 0])
        ws.undo(); self.assertEqual(tuple(ws.pages), initial)
        with self.assertRaises(PdfWorkbenchError): duplex_order(initial, ids[:2], ids[2:])

    def test_scan_selected_page_does_not_change_unselected_page(self):
        from pdf_workbench.workflows import prepare_scans, ScanOptions
        from pdf_workbench.advanced_pdf import mupdf
        before = tuple(self.ws.pages)
        result = prepare_scans(self.ws.sources, self.ws.pages, ScanOptions(dpi=72, deskew=False, rotation=90, clean_background=True), [before[1].uid])
        self.assertEqual(result.processed, 1); self.assertEqual(result.pages[0], before[0]); self.assertEqual(result.pages[1].uid, before[1].uid)
        self.ws.apply_page_replacements(result.sources, result.pages)
        out = self.directory / "scan.pdf"; export_pdf(self.ws.sources, self.ws.pages, out)
        with mupdf().open(out) as doc:
            self.assertIn("A1", doc[0].get_text()); self.assertEqual(doc[1].get_text(), "")
            self.assertEqual(tuple(doc[1].rect)[2:], (500., 300.)); self.assertTrue(doc[1].get_images())
        self.ws.undo(); self.assertEqual(tuple(self.ws.pages), before)

    def test_toc_and_links_follow_reordered_rotated_pages(self):
        from pdf_workbench.bookmarks import WorkspaceBookmark
        from pdf_workbench.workflows import export_table_of_contents, export_internal_links, InternalLink, pdf_engine
        first, second = self.ws.pages; self.ws.reorder([second.uid, first.uid]); self.ws.rotate([second.uid], 90)
        bookmarks = (WorkspaceBookmark("heading", "Kapitel Ä", None, (WorkspaceBookmark("first", "A1", first.uid), WorkspaceBookmark("second", "A2", second.uid))),)
        out = self.directory / "toc.pdf"; export_table_of_contents(self.ws.sources, self.ws.pages, out, bookmarks)
        with pdf_engine().open(out) as doc:
            self.assertEqual(len(doc), 3); self.assertIn("Kapitel Ä", doc[0].get_text())
            self.assertEqual([link["page"] for link in doc[0].get_links()], [2, 1]); self.assertIn("A2", doc[1].get_text()); self.assertIn("A1", doc[2].get_text())
        out = self.directory / "linked.pdf"
        export_internal_links(self.ws.sources, self.ws.pages, out, [InternalLink(second.uid, first.uid, (.1, .2, .4, .3))])
        with pdf_engine().open(out) as doc:
            link = doc[0].get_links()[0]; self.assertEqual(link["page"], 1)
            rect = link["from"]; self.assertAlmostEqual(rect.x0, 50, places=2); self.assertAlmostEqual(rect.y0, 60, places=2)
            self.assertAlmostEqual(rect.x1, 200, places=2); self.assertAlmostEqual(rect.y1, 90, places=2)

    def test_redaction_physically_removes_original_streams_metadata_and_annotations(self):
        from pdf_workbench.advanced_pdf import export_redacted_pdf, RedactionRegion, mupdf
        from pdf_workbench.annotations import PageAnnotation
        secret = "SECRET-8495-REVIEW"
        sensitive = fixture(self.directory / "sensitive.pdf", (secret,))
        writer = PdfWriter(sensitive); writer.add_metadata({"/Title": secret}); writer.add_attachment("hidden-secret.txt", secret.encode()); writer.write(self.directory / "attached.pdf")
        ws = workspace(self.directory / "attached.pdf"); uid = ws.pages[0].uid
        ws.add_annotation(uid, PageAnnotation("private-note", "comment", (.1, .05, .2, .1), text=secret))
        out = self.directory / "redacted.pdf"
        with self.assertRaises(PdfWorkbenchError): export_redacted_pdf(ws.sources, ws.pages, out, [RedactionRegion(uid, (.05, .02, .95, .18))])
        result = export_redacted_pdf(ws.sources, ws.pages, out, [RedactionRegion(uid, (.05, .02, .95, .18))], confirmed=True, dpi=100)
        self.assertEqual(result.regions, 1); self.assertNotIn(secret.encode(), out.read_bytes())
        reader = PdfReader(out); self.assertFalse(reader.attachments); self.assertFalse(reader.pages[0].get("/Annots")); self.assertEqual(reader.pages[0].extract_text(), "")
        with mupdf().open(out) as doc:
            pix = doc[0].get_pixmap(); self.assertEqual(pix.pixel(70, 60), (0, 0, 0)); self.assertTrue(doc[0].get_images())
        self.assertIn(secret, extracted(self.directory / "attached.pdf")[0])

    def test_text_replacement_isolated_copy_rejects_overflow_and_undo(self):
        from pdf_workbench.advanced_pdf import existing_text_runs, prepare_text_replacement
        first = self.ws.pages[0]; duplicate = self.ws.duplicate([first.uid])[0]
        before = self.ws.snapshot(); runs = existing_text_runs(self.ws.sources[first.source_id], first.page_index)
        run = next(r for r in runs if r.text == "A1")
        with self.assertRaises(PdfWorkbenchError): prepare_text_replacement(self.ws.sources, self.ws.pages, duplicate, run.run_id, "A VERY LONG REPLACEMENT THAT MUST NOT FIT")
        self.assertEqual(before, self.ws.snapshot())
        result = prepare_text_replacement(self.ws.sources, self.ws.pages, duplicate, run.run_id, "Z1")
        self.ws.apply_page_replacements(result.sources, result.pages)
        out = self.directory / "text.pdf"; export_pdf(self.ws.sources, self.ws.pages, out)
        self.assertIn("A1", extracted(out)[0]); self.assertIn("Z1", extracted(out)[1]); self.assertNotIn("A1", extracted(out)[1])
        self.assertIn("PUBLIC", extracted(out)[1]); self.ws.undo(); self.assertEqual(before.pages, tuple(self.ws.pages)); self.assertEqual(before.bookmarks, self.ws.bookmarks); self.assertTrue(self.ws.can_redo)

    def test_signature_integrity_trust_separation_and_tamper(self):
        from pdf_workbench.security_signatures import sign_pdf_certificate, verify_pdf_signatures
        pfx = self.directory / "test-only.pfx"; root = certificate(pfx, "review-only")
        out = self.directory / "signed.pdf"
        result = sign_pdf_certificate(self.ws.sources, self.ws.pages, out, pfx, "review-only")
        self.assertTrue(result.status.integrity_ok); self.assertFalse(result.status.trusted); self.assertFalse(result.status.modified_after_signing)
        trusted = verify_pdf_signatures(out, trust_roots=[root])[0]; self.assertTrue(trusted.integrity_ok); self.assertTrue(trusted.trusted)
        raw = out.read_bytes(); self.assertIn(b"/ByteRange", raw)
        # Valid-length modification inside the signed body, independently of the PDF engine.
        self.assertIn(b"/Producer", raw); tampered = raw.replace(b"/Producer", b"/ProduceX", 1)
        statuses = verify_pdf_signatures(tampered)
        self.assertTrue(statuses); self.assertFalse(all(s.integrity_ok for s in statuses))
        with self.assertRaises(PdfWorkbenchError): sign_pdf_certificate(self.ws.sources, self.ws.pages, self.directory / "wrong.pdf", pfx, "incorrect")
        self.assertFalse((self.directory / "wrong.pdf").exists())

    def test_pdfa_requires_actual_validator_and_preserves_order_orientation(self):
        from pdf_workbench.pdfa import export_pdfa, validate_pdfa
        if not self.verapdf or not self.ghostscript:
            if os.environ.get("PDF_WORKBENCH_REQUIRE_PDFA") == "1":
                self.fail("Actual Ghostscript and veraPDF are mandatory for this acceptance run")
            self.skipTest("Actual local Ghostscript/veraPDF not supplied")
        first, second = self.ws.pages; self.ws.reorder([second.uid, first.uid]); self.ws.rotate([second.uid], 90)
        out = self.directory / "archive.pdf"
        result = export_pdfa(self.ws.sources, self.ws.pages, out, verapdf=self.verapdf, ghostscript=self.ghostscript)
        self.assertTrue(result.validation.compliant); self.assertEqual(result.validation.flavour, "2b")
        (self.directory / "verapdf.xml").write_text(result.validation.report)
        self.assertTrue(validate_pdfa(out, verapdf=self.verapdf).compliant)
        text = extracted(out); self.assertIn("A2", text[0]); self.assertIn("A1", text[1])
        self.assertEqual(float(PdfReader(out).pages[0].mediabox.width), 500)
        saved = out.read_bytes()
        with self.assertRaises(PdfWorkbenchError): export_pdfa(self.ws.sources, self.ws.pages, out, verapdf=self.verapdf, ghostscript=self.ghostscript)
        self.assertEqual(saved, out.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verapdf")
    parser.add_argument("--ghostscript")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    Acceptance.base = args.output; Acceptance.verapdf = args.verapdf; Acceptance.ghostscript = args.ghostscript
    report = {"result": "RUNNING", "os": platform.platform(), "python": sys.version, "source_sha256": {str(p.relative_to(ROOT)).replace(os.sep, "/"): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT / "src").rglob("*.py"))}}
    start = time.monotonic()
    with (args.output / "acceptance.log").open("w", encoding="utf-8") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        try:
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(Acceptance)
            result = unittest.TextTestRunner(verbosity=2, stream=log).run(suite)
            report.update(result="PASS" if result.wasSuccessful() else "FAIL", tests=result.testsRun, failures=[(str(t), detail) for t, detail in result.failures], errors=[(str(t), detail) for t, detail in result.errors], skipped=[(str(t), reason) for t, reason in result.skipped])
        except BaseException: report.update(result="ERROR", error=traceback.format_exc()); traceback.print_exc()
    report["elapsed_seconds"] = time.monotonic() - start
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{report['result']}: {args.output / 'result.json'}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__": raise SystemExit(main())
