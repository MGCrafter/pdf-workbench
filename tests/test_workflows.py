"""Real PDFs, independent Poppler rendering, cancellation and immutable edits."""
from __future__ import annotations

from dataclasses import replace
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageChops, ImageDraw
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from pdf_workbench.batch import BatchPreset, load_preset, process_batch, save_preset
from pdf_workbench.core import OperationCancelled, PdfWorkbenchError, Workspace, export_pdf, load_source
from pdf_workbench.page_marks import PageNumberOptions, WatermarkOptions
from pdf_workbench.workflows import (InternalLink, ScanOptions, deskew_angle, duplex_order,
    export_images, export_internal_links, export_table_of_contents, export_text,
    find_page_candidates, pdf_engine, prepare_scans, publish_bytes)


def fixture(path, labels=("First page", "", "First page", "Last page")):
    pdf = canvas.Canvas(str(path), pagesize=(300, 420))
    for label in labels:
        if label:
            pdf.setFont("Helvetica", 16); pdf.drawString(25, 380, label)
            pdf.setFillColorRGB(.12, .32, .31); pdf.rect(25, 35, 85, 75, fill=True, stroke=False)
        pdf.showPage()
    pdf.save()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.directory = Path(self.temporary.name)
        self.source = self.directory / "original.pdf"; fixture(self.source); self.original = self.source.read_bytes()
        self.workspace = Workspace(); self.workspace.add_sources((load_source(self.source),))
        self.output = self.directory / "new.pdf"

    def tearDown(self):
        self.assertEqual(self.source.read_bytes(), self.original)
        self.temporary.cleanup()

    def test_candidates_are_review_only_with_real_blank_and_duplicate(self):
        before = self.workspace.snapshot()
        candidates = find_page_candidates(self.workspace.sources, self.workspace.pages)
        self.assertEqual([item.page_number for item in candidates], [2, 3])
        self.assertIsNone(candidates[0].duplicate_of); self.assertEqual(candidates[1].duplicate_of, 1)
        self.assertTrue(candidates[0].preview_png.startswith(b"\x89PNG")); self.assertEqual(self.workspace.snapshot(), before)
        self.workspace.delete((candidates[1].page_uid,)); self.workspace.undo(); self.assertEqual(tuple(self.workspace.pages), before.pages)

    def test_duplicate_detection_accounts_for_rotations_and_own_objects(self):
        self.workspace.rotate((self.workspace.pages[2].uid,), 90)
        self.assertEqual([item.page_number for item in find_page_candidates(self.workspace.sources, self.workspace.pages)], [2])

    def test_text_export_follows_workspace_order_and_page_delimiters(self):
        self.workspace.reorder([page.uid for page in reversed(self.workspace.pages)])
        output = export_text(self.workspace.sources, self.workspace.pages, self.directory / "text.txt")
        text = output.read_text(); self.assertEqual(text.count("\f"), 3); self.assertTrue(text.startswith("Last page"))
        with self.assertRaises(PdfWorkbenchError): export_text(self.workspace.sources, self.workspace.pages, output)
        self.assertEqual(output.read_text(), text)

    def test_image_export_current_rotation_and_second_renderer(self):
        self.workspace.rotate((self.workspace.pages[0].uid,), 90)
        paths = export_images(self.workspace.sources, self.workspace.pages[:1], self.directory, dpi=144)
        with Image.open(paths[0]) as image: self.assertEqual(image.size, (840, 600))
        pdf = export_pdf(self.workspace.sources, self.workspace.pages[:1], self.output)
        if not shutil.which("pdftoppm"): self.skipTest("Poppler unavailable")
        subprocess.run(["pdftoppm", "-singlefile", "-r", "144", "-png", str(pdf), str(self.directory / "poppler")], check=True, capture_output=True)
        with Image.open(paths[0]) as first, Image.open(self.directory / "poppler.png") as second:
            self.assertEqual(first.size, second.size)
            difference = ImageChops.difference(first.convert("RGB"), second.convert("RGB"))
            self.assertLess(sum(difference.convert("L").histogram()[30:]) / (first.width * first.height), .03)

    def test_jpeg_export_dpi_quality_and_refuse_existing_preflight(self):
        paths = export_images(self.workspace.sources, self.workspace.pages[:1], self.directory, image_format="jpeg", dpi=72, quality=75)
        with Image.open(paths[0]) as image: self.assertEqual(image.format, "JPEG"); self.assertEqual(image.size, (300, 420))
        original = paths[0].read_bytes()
        with self.assertRaises(PdfWorkbenchError): export_images(self.workspace.sources, self.workspace.pages, self.directory, image_format="jpeg", dpi=72)
        self.assertEqual(paths[0].read_bytes(), original); self.assertFalse((self.directory / "Seite-0002.jpg").exists())

    def test_image_cancellation_removes_own_outputs(self):
        cancelled = False
        def progress(value, message):
            nonlocal cancelled
            cancelled = True
        with self.assertRaises(OperationCancelled):
            export_images(self.workspace.sources, self.workspace.pages, self.directory, cancel=lambda: cancelled, progress=progress)
        self.assertFalse(list(self.directory.glob("Seite-*.png")))

    def test_image_cancellation_retains_foreign_same_inode_edit(self):
        cancelled = False; output = self.directory / "Seite-0001.png"
        def progress(value, message):
            nonlocal cancelled
            output.write_bytes(b"foreign changed data"); cancelled = True
        with self.assertRaises(OperationCancelled) as error:
            export_images(self.workspace.sources, self.workspace.pages, self.directory, cancel=lambda: cancelled, progress=progress)
        self.assertEqual(output.read_bytes(), b"foreign changed data"); self.assertIn("erhalten", str(error.exception))

    def test_symlink_outputs_and_sources_refused(self):
        target = self.directory / "text.txt"
        try: target.symlink_to(self.source)
        except OSError: self.skipTest("Symlink rights unavailable")
        with self.assertRaises(PdfWorkbenchError): export_text(self.workspace.sources, self.workspace.pages, target)
        self.assertTrue(target.is_symlink())
        with self.assertRaises(PdfWorkbenchError): publish_bytes(b"bad", self.source, self.workspace.sources)

    def test_image_invalid_prefix_dpi_and_pixel_bound(self):
        for arguments in ({"prefix": "../bad"}, {"dpi": True}, {"dpi": 601}, {"image_format": "bmp"}):
            with self.subTest(arguments=arguments), self.assertRaises(PdfWorkbenchError):
                export_images(self.workspace.sources, self.workspace.pages, self.directory, **arguments)
        fitz = pdf_engine()
        with fitz.open() as document:
            document.new_page(width=30000, height=30000); large = self.directory / "large.pdf"; document.save(large)
        workspace = Workspace(); workspace.add_sources((load_source(large),))
        with self.assertRaises(PdfWorkbenchError): export_images(workspace.sources, workspace.pages, self.directory)

    def test_duplex_backwards_and_rotation_one_undo(self):
        pages = tuple(self.workspace.pages); ids = tuple(page.uid for page in pages)
        result = duplex_order(pages, ids[:2], ids[2:], reverse_backs=True, backs_rotation=180)
        self.assertEqual([page.uid for page in result], [ids[0], ids[3], ids[1], ids[2]])
        self.assertEqual([page.rotation for page in result], [0, 180, 0, 180])
        self.workspace._set(result); self.workspace.undo(); self.assertEqual(tuple(self.workspace.pages), pages)
        self.workspace.redo(); export_pdf(self.workspace.sources, self.workspace.pages, self.output)
        reader = PdfReader(self.output); self.assertEqual([page.rotation for page in reader.pages], [0, 180, 0, 180])
        self.assertIn("Last page", reader.pages[1].extract_text())

    def test_duplex_odd_final_front_and_invalid_partition(self):
        pages = tuple(self.workspace.pages[:3]); ids = tuple(page.uid for page in pages)
        result = duplex_order(pages, ids[:2], ids[2:]); self.assertEqual([page.uid for page in result], [ids[0], ids[2], ids[1]])
        for fronts, backs in ((ids[:1], ids[1:]), (ids[:2], ids[1:]), (ids[:1], ()), ((), ids)):
            with self.assertRaises(PdfWorkbenchError): duplex_order(pages, fronts, backs)

    def test_scan_selected_only_undo_bookmarks_and_sensitive_metadata(self):
        pages = tuple(self.workspace.pages); selected = pages[0].uid
        old_source = self.workspace.sources[pages[0].source_id]
        self.workspace.sources[old_source.source_id] = replace(old_source, was_encrypted=True)
        self.workspace.add_bookmark("Erstes Kapitel", selected)
        self.workspace.rotate((selected,), 90); before = self.workspace.snapshot()
        result = prepare_scans(self.workspace.sources, self.workspace.pages, ScanOptions(dpi=90, deskew=False), (selected,))
        self.assertEqual(result.processed, 1); self.assertTrue(result.sources[0].was_encrypted)
        self.assertEqual(result.pages[0].uid, selected); self.assertEqual(result.pages[0].rotation, 0)
        self.assertEqual(result.pages[1:], tuple(self.workspace.pages[1:]))
        self.assertEqual(result.sources[0].page_sizes, ((420., 300.),))
        self.workspace.apply_page_replacements(result.sources, result.pages); self.assertEqual(self.workspace.bookmarks, before.bookmarks)
        export_pdf(self.workspace.sources, self.workspace.pages, self.output, bookmarks=self.workspace.bookmarks)
        reader = PdfReader(self.output); self.assertFalse(reader.pages[0].extract_text().strip()); self.assertIn("Last page", reader.pages[-1].extract_text())
        self.workspace.undo(); self.assertEqual(tuple(self.workspace.pages), before.pages)

    def test_scan_cleanup_and_orientation_unavailable_report(self):
        with patch("pdf_workbench.ocr.discover_engine", return_value=None):
            result = prepare_scans(self.workspace.sources, self.workspace.pages[:1], ScanOptions(dpi=72, deskew=False, clean_background=True, auto_orientation=True))
        self.assertEqual(result.processed, 1); self.assertIn("Tesseract", result.warnings[0])
        with pdf_engine().open(stream=result.sources[0].data, filetype="pdf") as document:
            self.assertEqual(len(document), 1); self.assertEqual(document[0].rect.width, 300)

    def test_deskew_uses_real_slanted_lines_and_cancel(self):
        image = Image.new("RGB", (600, 800), "white"); draw = ImageDraw.Draw(image)
        for y in range(100, 700, 30): draw.line((70, y, 530, y), fill="black", width=3)
        self.assertEqual(deskew_angle(image), 0)
        rotated = image.rotate(3, expand=False, fillcolor="white")
        self.assertAlmostEqual(deskew_angle(rotated), -3, delta=.6)
        with self.assertRaises(OperationCancelled): deskew_angle(rotated, lambda: True)

    def test_toc_clickable_nested_and_second_renderer(self):
        pages = tuple(self.workspace.pages); root = self.workspace.add_bookmark("Kapitel mit Umlauten ÄÖÜ", pages[0].uid)
        self.workspace.add_bookmark("Unterkapitel: Ende", pages[-1].uid, parent_uid=root)
        export_table_of_contents(self.workspace.sources, pages, self.output, self.workspace.bookmarks)
        with pdf_engine().open(self.output) as document:
            self.assertEqual(len(document), 5); links = document[0].get_links(); self.assertEqual([link["page"] for link in links], [1, 4])
            self.assertIn("ÄÖÜ", document[0].get_text()); self.assertEqual([entry[0] for entry in document.get_toc()], [1, 2])
        reader = PdfReader(self.output); self.assertIn("Inhaltsverzeichnis", reader.pages[0].extract_text()); self.assertIn("Last page", reader.pages[-1].extract_text())
        if shutil.which("pdftoppm"):
            subprocess.run(["pdftoppm", "-f", "1", "-singlefile", "-scale-to", "700", "-png", str(self.output), str(self.directory / "toc")], check=True, capture_output=True)
            with Image.open(self.directory / "toc.png") as image: self.assertLess(image.convert("L").getextrema()[0], 100)

    def test_toc_wraps_long_titles_across_multiple_sheets(self):
        for index in range(85): self.workspace.add_bookmark(f"Kapitel {index}: " + "Ein langer Titel mit Wörtern " * 6, self.workspace.pages[index % 4].uid)
        export_table_of_contents(self.workspace.sources, self.workspace.pages, self.output, self.workspace.bookmarks)
        with pdf_engine().open(self.output) as document:
            offset = len(document) - 4; self.assertGreater(offset, 1)
            all_links = [link for page in list(document)[:offset] for link in page.get_links()]
            self.assertEqual(len(all_links), 85); self.assertTrue(all(offset <= link["page"] < len(document) for link in all_links))

    def test_toc_maximum_legal_title_at_maximum_depth_keeps_every_character(self):
        from pdf_workbench.bookmarks import WorkspaceBookmark
        child = None
        for level in range(32):
            child = WorkspaceBookmark(f"nested-{level}", "W"*500, self.workspace.pages[0].uid, (child,) if child else ())
        export_table_of_contents(self.workspace.sources, self.workspace.pages, self.output, (child,))
        with pdf_engine().open(self.output) as document:
            count = len(document)-len(self.workspace.pages); self.assertGreater(count, 1)
            text = "".join(document[index].get_text() for index in range(count))
            self.assertEqual(text.count("W"), 500*32)
            for index in range(count):
                for link in document[index].get_links(): self.assertTrue(document[index].rect.contains(link["from"]))

    def test_toc_unsupported_bookmark_or_heading_glyph_rejects_without_output(self):
        self.workspace.add_bookmark("Kapitel 中文", self.workspace.pages[0].uid)
        with self.assertRaisesRegex(PdfWorkbenchError, "Zeichen U\\+"):
            export_table_of_contents(self.workspace.sources, self.workspace.pages, self.output, self.workspace.bookmarks)
        self.assertFalse(self.output.exists())
        self.workspace.update_bookmark(self.workspace.bookmarks[0].uid, title="Kapitel ÄÖÜ")
        with self.assertRaisesRegex(PdfWorkbenchError, "Zeichen U\\+"):
            export_table_of_contents(self.workspace.sources, self.workspace.pages, self.output, self.workspace.bookmarks, title="Inhalt 😀")
        self.assertFalse(self.output.exists())

    def test_link_authoring_rotated_visible_rect_and_targets(self):
        ids = tuple(page.uid for page in self.workspace.pages); self.workspace.rotate((ids[0],), 90)
        links = (InternalLink(ids[0], ids[-1], (.1, .2, .9, .4)),)
        export_internal_links(self.workspace.sources, self.workspace.pages, self.output, links)
        with pdf_engine().open(self.output) as document:
            link = document[0].get_links()[0]; self.assertEqual(link["page"], 3)
            self.assertAlmostEqual(link["from"].x0, document[0].rect.width * .1, delta=.01)
            self.assertAlmostEqual(link["from"].y0, document[0].rect.height * .2, delta=.01)
        with self.assertRaises(PdfWorkbenchError): InternalLink(ids[0], ids[1], (0, 0, 1.1, .2))
        with self.assertRaises(PdfWorkbenchError): export_internal_links(self.workspace.sources, self.workspace.pages, self.directory / "bad.pdf", (InternalLink("missing", ids[1], (0, 0, .5, .5)),))

    def test_links_cropped_userunit_all_rotations_fit_page(self):
        from pypdf import PdfWriter
        from pypdf.generic import IndirectObject, NameObject, NumberObject
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                source = self.directory / f"unit-{rotation}.pdf"; writer = PdfWriter()
                page = writer.add_blank_page(400, 600); page.cropbox.lower_left = (20, 40); page.cropbox.upper_right = (380, 560)
                page[NameObject("/UserUnit")] = NumberObject(2); page.rotate(rotation); writer.add_blank_page(300, 400); writer.write(source)
                workspace = Workspace(); workspace.add_sources((load_source(source),)); ids = tuple(page.uid for page in workspace.pages)
                output = self.directory / f"unit-out-{rotation}.pdf"
                export_internal_links(workspace.sources, workspace.pages, output, (InternalLink(ids[0], ids[1], (.1, .2, .9, .4)),))
                with pdf_engine().open(output) as document:
                    page = document[0]; link = page.get_links()[0]; rect = link["from"]
                    expected = (.1*page.rect.width, .2*page.rect.height, .9*page.rect.width, .4*page.rect.height)
                    for actual, desired in zip(rect, expected): self.assertAlmostEqual(actual, desired, delta=.02)
                    self.assertEqual(link["page"], 1)
                annotation = PdfReader(output).pages[0]["/Annots"][0].get_object(); self.assertEqual(annotation["/Dest"][1], "/Fit")
                self.assertIsInstance(annotation["/Dest"][0], IndirectObject)

    def test_batch_preset_roundtrip_and_dangerous_unknown_json(self):
        preset = BatchPreset("Meine Vorlage", "small", 90, WatermarkOptions("TEST", 20), PageNumberOptions(), ScanOptions(90, False), ("eng",))
        self.assertEqual(BatchPreset.from_json(preset.to_json()), preset)
        saved = save_preset(preset, self.directory / "preset.json"); self.assertEqual(load_preset(saved), preset)
        with self.assertRaises(PdfWorkbenchError): save_preset(preset, saved)
        for value in ('{}', '[1]', '{"format":"pdf-workbench-batch","version":2,"preset":{}}', '"bad"', "x" * 70000):
            with self.assertRaises(PdfWorkbenchError): BatchPreset.from_json(value)

    def test_batch_marks_rotation_and_no_source_changes(self):
        second = self.directory / "second.pdf"; fixture(second, ("Other input",)); original = second.read_bytes()
        preset = BatchPreset("Test", "lossless", 90, WatermarkOptions("TEST", 20), PageNumberOptions())
        result = process_batch((self.source, second), self.directory, preset)
        self.assertEqual(len(result.outputs), 2); self.assertEqual(second.read_bytes(), original)
        reader = PdfReader(result.outputs[0]); self.assertEqual([page.rotation for page in reader.pages], [90] * 4)
        self.assertIn("TEST", reader.pages[0].extract_text()); self.assertIn("Seite 4 von 4", reader.pages[-1].extract_text())
        self.assertFalse(list(self.directory.glob(".pdf-workbench-batch-*")))

    def test_batch_duplicate_names_and_existing_outputs_fail_before_publish(self):
        other = self.directory / "sub"; other.mkdir(); second = other / self.source.name; fixture(second)
        with self.assertRaises(PdfWorkbenchError): process_batch((self.source, second), self.directory)
        target = self.directory / "original-bearbeitet.pdf"; target.write_bytes(b"foreign")
        with self.assertRaises(PdfWorkbenchError): process_batch((self.source,), self.directory)
        self.assertEqual(target.read_bytes(), b"foreign")

    def test_batch_late_cancel_keeps_foreign_output_and_cleans_own(self):
        second = self.directory / "second.pdf"; fixture(second, ("Second",)); cancelled = False
        target = self.directory / "original-bearbeitet.pdf"
        def progress(value, message):
            nonlocal cancelled
            if value >= 100:
                target.write_bytes(b"foreign"); cancelled = True
        with self.assertRaises(OperationCancelled): process_batch((self.source, second), self.directory, cancel=lambda: cancelled, progress=progress)
        self.assertEqual(target.read_bytes(), b"foreign"); self.assertFalse((self.directory / "second-bearbeitet.pdf").exists())

    def test_batch_cancel_retains_foreign_addition_in_private_staging(self):
        cancelled = False; foreign = None
        def progress(value, message):
            nonlocal cancelled, foreign
            if value >= 100:
                staging = next(self.directory.glob(".pdf-workbench-batch-*"))
                foreign = staging / "foreign.txt"; foreign.write_text("keep me"); cancelled = True
        with self.assertRaises(OperationCancelled) as error:
            process_batch((self.source,), self.directory, cancel=lambda: cancelled, progress=progress)
        self.assertEqual(foreign.read_text(), "keep me"); self.assertIn(str(foreign.parent), str(error.exception))
        self.assertEqual(list(foreign.parent.iterdir()), [foreign]); self.assertFalse((self.directory / "original-bearbeitet.pdf").exists())

    def test_batch_public_progress_cannot_adopt_foreign_stage_bytes(self):
        modified = []
        def progress(value, message):
            if message.endswith("PDF gespeichert"):
                for staging in self.directory.glob(".pdf-workbench-batch-*"):
                    target = staging / "result-0.pdf"
                    if target.exists(): target.write_bytes(b"foreign modification before register"); modified.append(target)
        with self.assertRaises(PdfWorkbenchError): process_batch((self.source,), self.directory, progress=progress)
        self.assertTrue(modified); self.assertEqual(modified[-1].read_bytes(), b"foreign modification before register")
        self.assertFalse((self.directory / "original-bearbeitet.pdf").exists())

    def test_shared_publisher_checks_final_bytes_after_cancel_callback_returns_false(self):
        target = self.directory / "publish.txt"; calls = 0
        def cancel():
            nonlocal calls
            if target.exists() and target.stat().st_size > 0:
                target.write_bytes(b"foreign last callback change")
            calls += 1
            return False
        with self.assertRaises(PdfWorkbenchError): publish_bytes(b"own validated bytes", target, cancel=cancel)
        self.assertEqual(target.read_bytes(), b"foreign last callback change")

    def test_toc_preserves_own_editable_objects_on_original_pages(self):
        from pdf_workbench.annotations import PageAnnotation
        self.workspace.add_annotation(self.workspace.pages[0].uid, PageAnnotation("own", "text", (.1, .4, .5, .5), "OWN RETAINED"))
        self.workspace.add_bookmark("Start", self.workspace.pages[0].uid)
        export_table_of_contents(self.workspace.sources, self.workspace.pages, self.output, self.workspace.bookmarks)
        imported = load_source(self.output)
        self.assertEqual(sum(len(items) for items in imported.page_annotations), 1)
        self.assertEqual(imported.page_annotations[1][0].text, "OWN RETAINED")
        for page in PdfReader(self.output).pages[:1]:
            for item in page.get("/Annots", ()):
                from pypdf.generic import IndirectObject
                self.assertIsInstance(item.get_object()["/Dest"][0], IndirectObject)

    def test_toc_preserves_native_form_values_and_original_page_pixels(self):
        source = self.directory / "form-source.pdf"; document = canvas.Canvas(str(source), pagesize=(300, 420))
        document.drawString(25, 380, "Unchanged source page")
        document.acroForm.textfield(name="Name", value="Alice", x=30, y=40, width=180, height=25)
        document.showPage(); document.save(); original = source.read_bytes()
        workspace = Workspace(); workspace.add_sources((load_source(source),))
        workspace.add_bookmark("Formular", workspace.pages[0].uid)
        baseline = self.directory / "form-baseline.pdf"; export_pdf(workspace.sources, workspace.pages, baseline, bookmarks=workspace.bookmarks)
        export_table_of_contents(workspace.sources, workspace.pages, self.output, workspace.bookmarks)
        fields = PdfReader(self.output).get_fields(); self.assertTrue(fields)
        self.assertTrue(any(field.get("/V") == "Alice" for field in fields.values()))
        self.assertEqual(source.read_bytes(), original)
        if shutil.which("pdftoppm"):
            for path, page, prefix in ((baseline, 1, "form-before"), (self.output, 2, "form-after")):
                subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile", "-r", "96", "-png", str(path), str(self.directory / prefix)], check=True, capture_output=True)
            with Image.open(self.directory / "form-before.png") as first, Image.open(self.directory / "form-after.png") as second:
                self.assertEqual(first.size, second.size); self.assertIsNone(ImageChops.difference(first.convert("RGB"), second.convert("RGB")).getbbox())


if __name__ == "__main__": unittest.main()
