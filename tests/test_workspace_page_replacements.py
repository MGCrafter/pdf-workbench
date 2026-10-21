"""Per-copy PDF edits must commit atomically and retain originals and history."""
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from pdf_workbench.core import PdfWorkbenchError, Workspace, export_pdf, load_source


def source_file(path, label):
    writer = PdfWriter()
    page = writer.add_blank_page(300, 500)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
    stream = DecodedStreamObject()
    stream.set_data(f'BT /F1 20 Tf 30 400 Td ({label}) Tj ET'.encode())
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)
    return load_source(path)


class PageReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original = source_file(self.root / 'original.pdf', 'Original')
        self.replacement = source_file(self.root / 'replacement.pdf', 'Changed')
        self.workspace = Workspace()
        self.workspace.add_sources([self.original])
        self.workspace.duplicate([self.workspace.pages[0].uid])

    def tearDown(self):
        self.temp.cleanup()

    def test_only_selected_copy_changes_and_one_undo_restores_both(self):
        original_pages = tuple(self.workspace.pages)
        changed = (original_pages[0], replace(original_pages[1], source_id=self.replacement.source_id, page_index=0))
        self.workspace.apply_page_replacements([self.replacement], changed)
        output = self.root / 'changed.pdf'
        export_pdf(self.workspace.sources, self.workspace.pages, output)
        self.assertEqual([page.extract_text().strip() for page in PdfReader(output).pages], ['Original', 'Changed'])
        self.workspace.undo()
        self.assertEqual(tuple(self.workspace.pages), original_pages)
        self.workspace.redo()
        self.assertEqual(tuple(self.workspace.pages), changed)
        self.assertEqual(self.original.data, self.original.path.read_bytes())

    def test_invalid_late_reference_adds_no_sources_and_preserves_redo(self):
        self.workspace.rotate([self.workspace.pages[1].uid], 90)
        self.workspace.undo()
        before = dict(self.workspace.sources), tuple(self.workspace.pages), list(self.workspace._undo), list(self.workspace._redo)
        pages = list(self.workspace.pages)
        pages[0] = replace(pages[0], source_id=self.replacement.source_id)
        for invalid in (replace(pages[1], source_id='missing'), replace(pages[1], page_index=99)):
            with self.subTest(invalid=invalid), self.assertRaises(PdfWorkbenchError):
                self.workspace.apply_page_replacements([self.replacement], [pages[0], invalid])
            self.assertEqual((self.workspace.sources, tuple(self.workspace.pages), self.workspace._undo, self.workspace._redo), before)

    def test_reorder_and_source_identity_collision_are_atomic_errors(self):
        before = dict(self.workspace.sources), tuple(self.workspace.pages)
        with self.assertRaises(PdfWorkbenchError):
            self.workspace.apply_page_replacements([self.replacement], list(reversed(self.workspace.pages)))
        collision = replace(self.replacement, source_id=self.original.source_id)
        with self.assertRaises(PdfWorkbenchError):
            self.workspace.apply_page_replacements([collision], self.workspace.pages)
        self.assertEqual((self.workspace.sources, tuple(self.workspace.pages)), before)

    def test_original_paths_stay_protected_after_all_copies_are_replaced(self):
        pages = [replace(page, source_id=self.replacement.source_id, page_index=0) for page in self.workspace.pages]
        self.workspace.apply_page_replacements([self.replacement], pages)
        self.original.path.unlink()
        with self.assertRaises(PdfWorkbenchError):
            export_pdf(self.workspace.sources, self.workspace.pages, self.original.path)
        self.assertFalse(self.original.path.exists())


if __name__ == '__main__':
    unittest.main()
