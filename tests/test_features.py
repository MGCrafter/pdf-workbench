"""Real PDF, annotation, selection and batch data-protection regressions."""
from __future__ import annotations

import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Rectangle
from reportlab.pdfgen import canvas

from pdf_workbench import __version__
from pdf_workbench.core import OperationCancelled, PageRef, PdfWorkbenchError, Workspace, export_pdf, load_source
from pdf_workbench.features import export_pages_separately, parse_page_selection


class SelectionTests(unittest.TestCase):
    def test_ranges_are_unique_and_in_workspace_order(self):
        self.assertEqual(parse_page_selection(" 5, 1 - 3; 2, 4 ", 5), [0, 1, 2, 3, 4])
        self.assertEqual(parse_page_selection("3, 1–2, 2—3", 5), [0, 1, 2])
        self.assertEqual(parse_page_selection("\n2,\t4", 5), [1, 3])

    def test_invalid_ranges_explain_the_problem(self):
        for text in ("", "0", "0-2", "-1", "3-2", "6", "1-6", "1.5", "eins", "2-", "1,,2", "1 2", "1/2", "1" * 5000):
            with self.subTest(text=text[:30]):
                with self.assertRaises(PdfWorkbenchError):
                    parse_page_selection(text, 5)
        with self.assertRaisesRegex(PdfWorkbenchError, "keine Seiten"):
            parse_page_selection("1", 0)


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        # Windows may expose the same temporary directory through an 8.3 alias.
        self.directory = Path(self.temporary.name).resolve()
        self.source_path = self.directory / "quelle.pdf"
        raw = io.BytesIO()
        document = canvas.Canvas(raw, pagesize=(300, 500))
        for text in ("ALPHA", "BETA"):
            document.setFont("Helvetica", 24)
            document.drawString(50, 220, text)
            document.showPage()
        document.save()
        writer = PdfWriter()
        writer.append(PdfReader(io.BytesIO(raw.getvalue())))
        page = writer.pages[0]
        page.cropbox.lower_left = (15, 30)
        page.cropbox.upper_right = (280, 460)
        page.rotate(90)
        writer.add_annotation(0, Rectangle(rect=(40, 80, 100, 160), interior_color="FF0000"))
        writer.write(self.source_path)
        self.original = self.source_path.read_bytes()
        self.workspace = Workspace()
        self.source = load_source(self.source_path)
        self.workspace.add_sources([self.source])
        self.destination = self.directory / "einzelseiten"

    def tearDown(self):
        self.assertEqual(self.source_path.read_bytes(), self.original)
        self.temporary.cleanup()

    def batch(self, pages=None, **kwargs):
        return export_pages_separately(self.workspace.sources, self.workspace.pages if pages is None else pages, self.destination, **kwargs)

    def test_duplicate_positions_rotation_unique_ids_and_one_undo(self):
        before = list(self.workspace.pages)
        self.workspace.rotate([before[0].uid], 90)
        before = list(self.workspace.pages)
        ids = self.workspace.duplicate([before[1].uid, before[0].uid, before[0].uid])
        self.assertEqual([page.uid for page in self.workspace.pages], [before[0].uid, ids[0], before[1].uid, ids[1]])
        self.assertEqual([page.rotation for page in self.workspace.pages], [90, 90, 0, 0])
        self.assertEqual(len({page.uid for page in self.workspace.pages}), 4)
        after = list(self.workspace.pages)
        self.workspace.undo()
        self.assertEqual(self.workspace.pages, before)
        self.workspace.redo()
        self.assertEqual(self.workspace.pages, after)
        self.workspace.rotate([ids[0]], 90)
        self.assertEqual([page.rotation for page in self.workspace.pages[:2]], [90, 180])

    def test_empty_duplicate_preserves_history_branch(self):
        self.workspace.rotate([self.workspace.pages[0].uid], 90)
        self.workspace.undo()
        before = list(self.workspace.pages)
        self.assertEqual(self.workspace.duplicate(["not-a-workspace-page"]), [])
        self.assertEqual(self.workspace.pages, before)
        self.assertTrue(self.workspace.can_redo)

    def duplicated_pages(self):
        first = self.workspace.pages[0]
        return [PageRef(f"copy-{angle}", first.source_id, first.page_index, angle) for angle in (0, 90, 180)]

    def test_duplicate_export_has_independent_annotations_and_rotations(self):
        output = self.directory / "duplicates.pdf"
        export_pdf(self.workspace.sources, self.duplicated_pages(), output)
        reader = PdfReader(output)
        self.assertEqual([page.rotation for page in reader.pages], [90, 180, 270])
        self.assertEqual(len({page.indirect_reference.idnum for page in reader.pages}), 3)
        annotations = []
        for page in reader.pages:
            self.assertEqual(page.extract_text().strip(), "ALPHA")
            self.assertEqual(list(page.cropbox), [15, 30, 280, 460])
            annotation = page["/Annots"][0]
            annotations.append(annotation.idnum)
            self.assertEqual(annotation.get_object().raw_get("/P"), page.indirect_reference)
        self.assertEqual(len(set(annotations)), 3)
        self.assertIn(__version__, reader.metadata.producer)

    def test_single_page_exports_follow_input_order_and_preserve_annotations(self):
        pages = [self.workspace.pages[1], *self.duplicated_pages()]
        result = self.batch(pages)
        self.assertEqual(result, self.destination.resolve())
        files = sorted(result.glob("*.pdf"))
        self.assertEqual(len(files), 4)
        readers = [PdfReader(file) for file in files]
        self.assertEqual([len(reader.pages) for reader in readers], [1, 1, 1, 1])
        self.assertEqual([reader.pages[0].extract_text().strip() for reader in readers], ["BETA", "ALPHA", "ALPHA", "ALPHA"])
        self.assertEqual([reader.pages[0].rotation for reader in readers], [0, 90, 180, 270])
        for reader in readers[1:]:
            page = reader.pages[0]
            self.assertEqual(page["/Annots"][0].get_object().raw_get("/P"), page.indirect_reference)
            self.assertEqual(list(page.cropbox), [15, 30, 280, 460])

    @unittest.skipUnless(shutil.which("pdftoppm") and shutil.which("pdftotext"), "Poppler is unavailable")
    def test_poppler_renders_duplicate_crop_rotation_and_visible_annotations(self):
        from PIL import Image
        self.batch(self.duplicated_pages())
        for index, path in enumerate(sorted(self.destination.glob("*.pdf"))):
            text = subprocess.run(["pdftotext", str(path), "-"], check=True, capture_output=True, text=True).stdout
            self.assertIn("ALPHA", text)
            prefix = self.directory / f"render-{index}"
            subprocess.run(["pdftoppm", "-cropbox", "-r", "72", "-singlefile", "-png", str(path), str(prefix)], check=True, capture_output=True)
            with Image.open(prefix.with_suffix(".png")) as image:
                self.assertEqual(image.size, (430, 265) if index != 1 else (265, 430))
                red = sum(1 for r, g, b in image.convert("RGB").getdata() if r > 200 and g < 80 and b < 80)
                self.assertGreater(red, 1000)

    def test_existing_directory_file_and_missing_parent_are_rejected(self):
        self.destination.mkdir()
        valuable = self.destination / "valuable.txt"
        valuable.write_text("unverändert")
        with self.assertRaises(PdfWorkbenchError):
            self.batch()
        self.assertEqual(valuable.read_text(), "unverändert")
        self.destination = self.source_path
        with self.assertRaises(PdfWorkbenchError):
            self.batch()
        self.destination = self.directory / "missing-parent" / "child"
        with self.assertRaises(PdfWorkbenchError):
            self.batch()
        self.assertFalse(self.destination.parent.exists())

    def test_existing_directory_aliases_and_dangling_symlinks_are_rejected(self):
        real = self.directory / "real"
        real.mkdir()
        for destination in (real, self.directory / "not-there" / ".." / "real"):
            self.destination = destination
            with self.assertRaises(PdfWorkbenchError):
                self.batch()
        for index, link_target in enumerate((real, self.directory / "absent")):
            alias = self.directory / f"alias-{index}"
            try:
                alias.symlink_to(link_target, target_is_directory=True)
            except OSError:
                self.skipTest("Symlinks unavailable")
            self.destination = alias
            with self.assertRaises(PdfWorkbenchError):
                self.batch()
            self.assertTrue(alias.is_symlink())

    def test_invalid_page_and_empty_selection_never_create_a_folder(self):
        for pages in ([], [self.workspace.pages[0], PageRef("bad", "unknown-source", 0)]):
            with self.subTest(pages=pages):
                with self.assertRaises(PdfWorkbenchError):
                    self.batch(pages)
                self.assertFalse(self.destination.exists())

    def test_cancellation_before_and_after_first_completed_pdf_rolls_back(self):
        with self.assertRaises(OperationCancelled):
            self.batch(cancel=lambda: True)
        self.assertFalse(self.destination.exists())
        cancelled = False
        def progress(_value, message):
            nonlocal cancelled
            if "PDF gespeichert" in message:
                cancelled = True
        with self.assertRaises(OperationCancelled) as error:
            self.batch(cancel=lambda: cancelled, progress=progress)
        self.assertIn("0 eigene PDF-Datei(en) verblieben", str(error.exception))
        self.assertNotIn("keine Datei gespeichert", str(error.exception))
        self.assertFalse(self.destination.exists())

    def test_cancel_after_batch_completion_notification_rolls_back(self):
        cancelled = False
        def progress(value, _message):
            nonlocal cancelled
            cancelled = value == 100
        with self.assertRaises(OperationCancelled):
            self.batch(cancel=lambda: cancelled, progress=progress)
        self.assertFalse(self.destination.exists())

    def test_failure_in_second_file_removes_only_own_outputs(self):
        calls = 0
        def failing_export(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("Datenträger voll")
            return export_pdf(*args, **kwargs)
        with patch("pdf_workbench.features.export_pdf", side_effect=failing_export):
            with self.assertRaisesRegex(PdfWorkbenchError, "Datenträger voll"):
                self.batch()
        self.assertFalse(self.destination.exists())

    def test_foreign_file_created_during_batch_is_never_deleted(self):
        cancelled = False
        foreign = self.destination / "fremde-datei.txt"
        def progress(_value, message):
            nonlocal cancelled
            if "PDF gespeichert" in message:
                foreign.write_bytes(b"valuable")
                cancelled = True
        with self.assertRaises(OperationCancelled) as error:
            self.batch(cancel=lambda: cancelled, progress=progress)
        self.assertEqual(foreign.read_bytes(), b"valuable")
        self.assertEqual(list(self.destination.iterdir()), [foreign])
        self.assertIn(str(self.destination), str(error.exception))
        self.assertIn("0 eigene PDF-Datei(en) verblieben", str(error.exception))

    def test_racing_output_is_preserved(self):
        foreign = None
        def collide(sources, pages, destination, **kwargs):
            nonlocal foreign
            foreign = Path(destination)
            foreign.write_bytes(b"valuable new file")
            return export_pdf(sources, pages, destination, **kwargs)
        with patch("pdf_workbench.features.export_pdf", side_effect=collide):
            with self.assertRaises(PdfWorkbenchError):
                self.batch()
        self.assertEqual(foreign.read_bytes(), b"valuable new file")

    def test_replaced_directory_reports_unknown_remaining_files(self):
        cancelled = False
        moved = self.directory / "verschoben"
        foreign = self.destination / "foreign.txt"
        def progress(_value, message):
            nonlocal cancelled
            if "PDF gespeichert" in message:
                self.destination.rename(moved)
                self.destination.mkdir()
                foreign.write_bytes(b"valuable")
                cancelled = True
        with self.assertRaises(OperationCancelled) as error:
            self.batch(cancel=lambda: cancelled, progress=progress)
        self.assertEqual(foreign.read_bytes(), b"valuable")
        self.assertEqual(len(list(moved.glob("*.pdf"))), 1)
        self.assertIn("Verbleib der 1 bereits erzeugten", str(error.exception))
        self.assertIn("unbekannt", str(error.exception))
        self.assertNotIn("0 eigene PDF-Datei(en) verblieben", str(error.exception))

    def test_unremovable_own_file_is_counted_with_exact_path(self):
        cancelled = False
        real_unlink = Path.unlink
        def progress(_value, message):
            nonlocal cancelled
            if "PDF gespeichert" in message:
                cancelled = True
        def deny_output_unlink(path, *args, **kwargs):
            if path.parent == self.destination and path.suffix == ".pdf":
                raise PermissionError("Datei gesperrt")
            return real_unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", deny_output_unlink):
            with self.assertRaises(OperationCancelled) as error:
                self.batch(cancel=lambda: cancelled, progress=progress)
        files = list(self.destination.glob("*.pdf"))
        self.assertEqual(len(files), 1)
        self.assertIn("1 eigene PDF-Datei(en) verblieben", str(error.exception))
        self.assertIn(str(files[0]), str(error.exception))
        self.assertEqual(len(PdfReader(files[0]).pages), 1)

    def test_invalid_media_box_is_rejected_before_import(self):
        for width in (0, -10, float("inf")):
            writer = PdfWriter()
            page = writer.add_blank_page(300, 500)
            page.mediabox.upper_right = (width, 500)
            invalid = self.directory / f"invalid-{width}.pdf"
            try:
                writer.write(invalid)
            except (ValueError, OverflowError):
                continue  # pypdf itself refuses non-finite PDF number serialization.
            with self.assertRaisesRegex(PdfWorkbenchError, "Abmessungen"):
                load_source(invalid)


if __name__ == "__main__":
    unittest.main()
