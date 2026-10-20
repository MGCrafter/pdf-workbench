"""Real-file regressions for search and photo-to-PDF import."""
from dataclasses import replace
import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor, QImage, QPainter
from pypdf import PdfReader
from pdf_workbench.core import OperationCancelled, PdfWorkbenchError, Workspace, load_source
from pdf_workbench.image_import import ImagePageOptions, load_image_source
from pdf_workbench.search import search_workspace

spec = importlib.util.spec_from_file_location('search_fixture', ROOT / 'packaging/smoke.py')
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)


class SearchImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def workspace(self, rotation=0):
        path = self.directory / f'source-{rotation}.pdf'
        fixture_module.fixture(path, ('ALPHA', 'BETA', 'ALPHA'), rotation=rotation, crop=True)
        workspace = Workspace()
        workspace.add_sources([load_source(path)])
        return workspace

    def image(self, transparent=False):
        path = self.directory / 'photo.png'
        image = QImage(600, 200, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0) if transparent else QColor('red'))
        if transparent:
            painter = QPainter(image)
            painter.fillRect(0, 0, 300, 200, QColor(0, 255, 0, 128))
            painter.end()
        self.assertTrue(image.save(str(path)))
        return path

    def test_search_preserves_workspace_order_and_independent_duplicate_uids(self):
        workspace = self.workspace()
        original = workspace.pages[:]
        copies = workspace.duplicate([original[0].uid])
        workspace.reorder([original[2].uid, copies[0], original[1].uid, original[0].uid])
        before = tuple(workspace.pages)
        result = search_workspace(workspace.sources, workspace.pages, 'alpha')
        self.assertEqual([hit.page_uid for hit in result.hits], [original[2].uid, copies[0], original[0].uid])
        self.assertEqual(tuple(workspace.pages), before)
        self.assertFalse(result.truncated)
        self.assertEqual(search_workspace(workspace.sources, workspace.pages, 'not present').hits, ())
        limited = search_workspace(workspace.sources, workspace.pages, 'ALPHA', max_hits=1)
        self.assertEqual(len(limited.hits), 1)
        self.assertTrue(limited.truncated)

    def test_search_rectangles_handle_intrinsic_and_workspace_rotations(self):
        for original_rotation in (0, 90, 180, 270):
            workspace = self.workspace(original_rotation)
            reference = None
            for rotation in (0, 90, 180, 270):
                pages = [replace(page, rotation=rotation) for page in workspace.pages]
                result = search_workspace(workspace.sources, pages, 'ALPHA')
                self.assertEqual(len(result.hits), 2)
                rects = tuple(hit.rectangles for hit in result.hits)
                if reference is None:
                    reference = rects
                self.assertEqual(rects, reference)
                for hit in result.hits:
                    for left, top, right, bottom in hit.rectangles:
                        self.assertTrue(0 <= left < right <= 1 and 0 <= top < bottom <= 1)

    def test_search_cancellation_and_query_limits(self):
        workspace = self.workspace()
        with self.assertRaises(OperationCancelled):
            search_workspace(workspace.sources, workspace.pages, 'ALPHA', cancel=lambda: True)
        with self.assertRaises(PdfWorkbenchError):
            search_workspace(workspace.sources, workspace.pages, 'a' * 257)
        self.assertEqual(search_workspace(workspace.sources, workspace.pages, '   ').hits, ())

    def test_image_pdf_orientation_margin_and_source_snapshot(self):
        path = self.image()
        original = path.read_bytes()
        for orientation, landscape in (('auto', True), ('portrait', False), ('landscape', True)):
            source = load_image_source(path, ImagePageOptions(orientation=orientation))
            width, height = source.page_sizes[0]
            self.assertEqual(width > height, landscape)
            self.assertEqual(source.page_count, 1)
            self.assertFalse(PdfReader(io.BytesIO(source.data)).get_fields())
        source = load_image_source(path)
        path.unlink()
        self.assertGreater(len(PdfReader(io.BytesIO(source.data)).pages), 0)
        self.assertGreater(len(original), 0)

    def test_invalid_images_and_cancellation_make_no_pdf_file(self):
        path = self.directory / 'bad.png'
        path.write_bytes(b'not a real image')
        with self.assertRaises(PdfWorkbenchError):
            load_image_source(path)
        with self.assertRaises(OperationCancelled):
            load_image_source(path, cancel=lambda: True)
        self.assertEqual(list(self.directory.glob('*.pdf')), [])
        with self.assertRaises(ValueError):
            ImagePageOptions(margin_mm=float('nan'))

    def test_tiny_images_never_create_zero_size_or_empty_pdf_pages(self):
        for width, height in ((1, 1), (1, 20), (20, 1)):
            with self.subTest(size=(width, height)):
                image = QImage(width, height, QImage.Format.Format_RGB32)
                image.fill(QColor('red'))
                path = self.directory / f'tiny-{width}-{height}.png'
                self.assertTrue(image.save(str(path)))
                with self.assertRaisesRegex(PdfWorkbenchError, 'zu klein'):
                    load_image_source(path, ImagePageOptions('image', margin_mm=0))
                source = load_image_source(path, ImagePageOptions('A4', margin_mm=0))
                page = PdfReader(io.BytesIO(source.data)).pages[0]
                self.assertGreater(page.mediabox.width, 0)
                self.assertGreater(page.mediabox.height, 0)
                self.assertTrue(page['/Resources']['/XObject'])

    @unittest.skipUnless(shutil.which('pdftoppm'), 'Poppler is unavailable')
    def test_poppler_sees_proportions_transparency_and_white_margins(self):
        path = self.image(transparent=True)
        source = load_image_source(path, ImagePageOptions(orientation='landscape', margin_mm=10))
        output = self.directory / 'image.pdf'
        output.write_bytes(source.data)
        prefix = self.directory / 'render'
        subprocess.run(['pdftoppm', '-r', '72', '-singlefile', '-png', str(output), str(prefix)], check=True, capture_output=True)
        image = QImage(str(prefix.with_suffix('.png')))
        self.assertEqual((image.width(), image.height()), (842, 595))
        self.assertEqual(image.pixelColor(5, 5), QColor('white'))
        green = image.pixelColor(200, image.height() // 2)
        self.assertGreater(green.green(), 240)
        self.assertTrue(100 < green.red() < 160 and 100 < green.blue() < 160)
        self.assertEqual(image.pixelColor(700, image.height() // 2), QColor('white'))


if __name__ == '__main__':
    unittest.main()
