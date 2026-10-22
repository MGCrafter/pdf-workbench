"""Regression tests with real PDFs and distinct text, dimensions and rotations."""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject
from reportlab.pdfgen import canvas

from pdf_workbench.core import (
    OperationCancelled, PageRef, PdfWorkbenchError, Workspace, export_pdf, load_source,
)


def make_pdf(path: Path, labels=("A1", "A2", "A3")):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(300, 500))
    for label in labels:
        pdf.setFillColorRGB(0.1, 0.2, 0.4)
        pdf.setFont("Helvetica", 28)
        pdf.drawString(30, 440, label)
        pdf.setFillColorRGB(1, 0, 0)
        pdf.rect(12, 470, 18, 18, fill=1)
        pdf.showPage()
    pdf.save()
    path.write_bytes(buffer.getvalue())


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.a = self.directory / "a.pdf"
        self.b = self.directory / "b.pdf"
        make_pdf(self.a)
        make_pdf(self.b, ("B1", "B2"))
        self.original_a = self.a.read_bytes()
        self.workspace = Workspace()
        self.workspace.add_sources([load_source(self.a), load_source(self.b)])
        self.out = self.directory / "out.pdf"

    def tearDown(self):
        self.temp.cleanup()

    def export(self, pages=None, path=None, **kwargs):
        return export_pdf(self.workspace.sources, self.workspace.pages if pages is None else pages, path or self.out, **kwargs)

    def texts(self, path=None):
        return [page.extract_text().strip() for page in PdfReader(path or self.out).pages]

    def test_merge_reorder_rotate_and_originals(self):
        pages = self.workspace.pages
        order = [pages[3].uid, pages[1].uid, pages[4].uid, pages[0].uid, pages[2].uid]
        self.workspace.reorder(order)
        self.workspace.rotate([pages[3].uid], 90)
        self.export()
        self.assertEqual(self.texts(), ["B1", "A2", "B2", "A1", "A3"])
        self.assertEqual(PdfReader(self.out).pages[0].rotation, 90)
        self.assertEqual(self.a.read_bytes(), self.original_a)
        self.assertEqual(len(PdfReader(self.b).pages), 2)

    def test_split_keeps_current_order(self):
        chosen = [self.workspace.pages[4], self.workspace.pages[0]]
        self.export(chosen)
        self.assertEqual(self.texts(), ["B2", "A1"])

    def test_snapshot_survives_external_change(self):
        self.a.write_bytes(b"external change")
        self.export()
        self.assertEqual(self.texts()[:3], ["A1", "A2", "A3"])
        self.assertEqual(self.a.read_bytes(), b"external change")

    def test_existing_output_never_overwritten(self):
        self.out.write_bytes(b"valuable")
        with self.assertRaises(PdfWorkbenchError):
            self.export()
        self.assertEqual(self.out.read_bytes(), b"valuable")

    def test_original_path_never_overwritten(self):
        with self.assertRaises(PdfWorkbenchError):
            self.export(path=self.a)
        self.assertEqual(self.a.read_bytes(), self.original_a)

    def test_deleted_original_path_still_protected(self):
        self.a.unlink()
        with self.assertRaises(PdfWorkbenchError):
            self.export(path=self.a)
        self.assertFalse(self.a.exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symbolic_link_and_dangling_link_protected(self):
        for target in (self.a, self.directory / "missing.pdf"):
            link = self.directory / "alias.pdf"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlink privilege unavailable")
            with self.assertRaises(PdfWorkbenchError):
                self.export(path=link)
            self.assertTrue(link.is_symlink())
            link.unlink()
        self.assertEqual(self.a.read_bytes(), self.original_a)

    def test_hardlink_protected(self):
        link = self.directory / "hardlink.pdf"
        try:
            os.link(self.a, link)
        except OSError:
            self.skipTest("hardlinks unavailable")
        with self.assertRaises(PdfWorkbenchError):
            self.export(path=link)
        self.assertEqual(link.read_bytes(), self.original_a)

    def test_destination_created_during_export_not_overwritten(self):
        def race(source, target):
            Path(target).write_bytes(b"created by another process")
            raise FileExistsError()
        with patch("pdf_workbench.core.os.link", side_effect=race):
            with self.assertRaises(PdfWorkbenchError):
                self.export()
        self.assertEqual(self.out.read_bytes(), b"created by another process")
        self.assertFalse(list(self.directory.glob(".pdf-workbench-*")))

    def test_cancel_during_page_processing_leaves_no_output(self):
        cancelled = [False]
        def progress(*_):
            cancelled[0] = True
        with self.assertRaises(OperationCancelled):
            self.export(cancel=lambda: cancelled[0], progress=progress)
        self.assertFalse(self.out.exists())

    def test_cancel_after_serializing_removes_temporary(self):
        cancelled = [False]
        def sync(_):
            cancelled[0] = True
        with patch("pdf_workbench.core.os.fsync", side_effect=sync):
            with self.assertRaises(OperationCancelled):
                self.export(cancel=lambda: cancelled[0])
        self.assertFalse(self.out.exists())
        self.assertFalse(list(self.directory.glob(".pdf-workbench-*")))

    def test_fallback_without_hardlink(self):
        with patch("pdf_workbench.core.os.link", side_effect=OSError("unsupported")):
            self.export()
        self.assertEqual(self.texts(), ["A1", "A2", "A3", "B1", "B2"])
        self.assertFalse(list(self.directory.glob(".pdf-workbench-*")))

    def test_cancel_during_fallback_does_not_delete_a_replaced_destination(self):
        target = self.directory / 'replacement.pdf'
        moved = self.directory / 'moved-partial.pdf'
        def progress(value, message):
            # At 100% the output handle is closed on both POSIX and Windows.
            # A callback can fail after another application replaces that file.
            if value == 100:
                target.rename(moved)
                target.write_bytes(b'foreign replacement')
                raise OperationCancelled('Vorgang abgebrochen.')
        with patch('pdf_workbench.core.os.link', side_effect=OSError('unsupported')):
            with self.assertRaisesRegex(OperationCancelled, 'fremde Datei bleibt erhalten'):
                self.export(path=target, progress=progress)
        self.assertEqual(target.read_bytes(), b'foreign replacement')
        self.assertTrue(moved.exists())
        self.assertEqual(self.a.read_bytes(), self.original_a)
        self.assertFalse(list(self.directory.glob(".pdf-workbench-*")))

    def test_fallback_write_failure_removes_partial_file(self):
        real_sync = os.fsync
        calls = [0]
        def sync(descriptor):
            calls[0] += 1
            if calls[0] == 2:
                raise OSError("simulated disk failure")
            real_sync(descriptor)
        with patch("pdf_workbench.core.os.link", side_effect=OSError("unsupported")), patch("pdf_workbench.core.os.fsync", side_effect=sync):
            with self.assertRaises(PdfWorkbenchError):
                self.export()
        self.assertFalse(self.out.exists())
        self.assertFalse(list(self.directory.glob(".pdf-workbench-*")))

    def test_cancel_during_fallback_preserves_foreign_edit_at_same_inode(self):
        target = self.directory / 'edited-in-place.pdf'
        def progress(value, message):
            if value == 100:
                target.write_bytes(b'foreign in-place edit')
                raise OperationCancelled('Vorgang abgebrochen.')
        with patch('pdf_workbench.core.os.link', side_effect=OSError('unsupported')):
            with self.assertRaisesRegex(OperationCancelled, 'fremde Datei bleibt erhalten'):
                self.export(path=target, progress=progress)
        self.assertEqual(target.read_bytes(), b'foreign in-place edit')
        self.assertEqual(self.a.read_bytes(), self.original_a)
        self.assertFalse(list(self.directory.glob('.pdf-workbench-*')))

    def test_source_rotation_crop_and_size_preserved(self):
        writer = PdfWriter()
        page = writer.add_page(PdfReader(self.a).pages[0])
        page.cropbox.lower_left = (10, 20)
        page.cropbox.upper_right = (280, 480)
        page.rotate(90)
        path = self.directory / "cropped.pdf"
        writer.write(path)
        source = load_source(path)
        self.workspace.add_sources([source])
        selected = self.workspace.pages[-1]
        self.workspace.rotate([selected.uid], 90)
        self.export(self.workspace.pages[-1:])
        result = PdfReader(self.out).pages[0]
        self.assertEqual(result.rotation, 180)
        self.assertEqual(list(result.cropbox), [10, 20, 280, 480])
        self.assertEqual(list(result.mediabox), [0, 0, 300, 500])

    def test_repeated_source_page_has_independent_rotation(self):
        page = self.workspace.pages[0]
        duplicate = PageRef("duplicate", page.source_id, page.page_index, 90)
        self.export([page, duplicate])
        reader = PdfReader(self.out)
        self.assertEqual([p.rotation for p in reader.pages], [0, 90])

    def test_move_multiselection_preserves_relative_order(self):
        ids = [page.uid for page in self.workspace.pages]
        self.workspace.move([ids[1], ids[2]], -1)
        self.assertEqual([p.uid for p in self.workspace.pages], [ids[1], ids[2], ids[0], ids[3], ids[4]])
        self.workspace.move([ids[1], ids[2]], 1)
        self.assertEqual([p.uid for p in self.workspace.pages], ids)

    def test_undo_redo_delete_rotate_and_branch(self):
        original = list(self.workspace.pages)
        self.workspace.rotate([original[0].uid], -90)
        self.workspace.delete([original[1].uid])
        self.workspace.undo()
        self.assertEqual(len(self.workspace.pages), 5)
        self.workspace.undo()
        self.assertEqual(self.workspace.pages, original)
        self.workspace.redo()
        self.assertEqual(self.workspace.pages[0].rotation, 270)
        self.workspace.delete([original[2].uid])
        self.assertFalse(self.workspace.can_redo)

    def test_invalid_order_and_rotation_rejected(self):
        with self.assertRaises(PdfWorkbenchError):
            self.workspace.reorder([self.workspace.pages[0].uid] * 5)
        with self.assertRaises(ValueError):
            self.workspace.rotate([], 45)

    def test_empty_selection_and_bad_extension(self):
        with self.assertRaises(PdfWorkbenchError):
            self.export([])
        with self.assertRaises(PdfWorkbenchError):
            self.export(path=self.directory / "output.txt")

    def test_corrupt_empty_and_encrypted_pdfs_rejected(self):
        invalid = self.directory / "invalid.pdf"
        invalid.write_bytes(b"not a pdf")
        with self.assertRaises(PdfWorkbenchError):
            load_source(invalid)
        empty = PdfWriter()
        empty.write(invalid)
        with self.assertRaises(PdfWorkbenchError):
            load_source(invalid)
        encrypted = PdfWriter()
        encrypted.add_page(PdfReader(self.a).pages[0])
        encrypted.encrypt("password")
        encrypted.write(invalid)
        with self.assertRaises(PdfWorkbenchError):
            load_source(invalid)

    def test_orphaned_widget_rejected(self):
        writer = PdfWriter()
        page = writer.add_page(PdfReader(self.a).pages[0])
        widget = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"), NameObject("/Rect"): ArrayObject([NumberObject(n) for n in [10, 10, 40, 40]])})
        page[NameObject("/Annots")] = ArrayObject([writer._add_object(widget)])
        path = self.directory / "widget.pdf"
        writer.write(path)
        with self.assertRaisesRegex(PdfWorkbenchError, "Formular"):
            load_source(path)

    def test_annotation_array_can_be_an_indirect_object(self):
        writer = PdfWriter()
        page = writer.add_page(PdfReader(self.a).pages[0])
        note = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Text"),
            NameObject("/Rect"): ArrayObject([NumberObject(n) for n in [10, 10, 40, 40]]),
        })
        page[NameObject("/Annots")] = writer._add_object(ArrayObject([writer._add_object(note)]))
        path = self.directory / "annotation.pdf"
        writer.write(path)
        source = load_source(path)
        self.workspace.add_sources([source])
        self.export(self.workspace.pages[-1:])
        result = PdfReader(self.out).pages[0]["/Annots"].get_object()
        self.assertEqual(result[0].get_object()["/Subtype"], "/Text")


if __name__ == "__main__":
    unittest.main()
