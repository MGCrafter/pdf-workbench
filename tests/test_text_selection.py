"""Real QtPdf text selection and asynchronous lifetime regressions."""
from __future__ import annotations

import io
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from reportlab.pdfgen import canvas
from pdf_workbench.core import PageRef, load_source
from pdf_workbench.ui.rendering import RenderBroker

APP = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        APP.processEvents()
        time.sleep(.005)
        if time.monotonic() > deadline:
            raise AssertionError("Text selection timed out")
    APP.processEvents()


class TextSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        data = io.BytesIO()
        pdf = canvas.Canvas(data, pagesize=(300, 420))
        pdf.setFont("Helvetica", 14)
        pdf.drawString(60, 340, "ALPHA beta")
        pdf.drawString(60, 310, "GAMMA delta")
        pdf.save()
        reader, writer = PdfReader(data), PdfWriter()
        for rotation in (0, 90, 180, 270):
            writer.reset_translation(reader)
            page = writer.add_page(reader.pages[0])
            page.cropbox = RectangleObject([40, 50, 260, 380])
            page.rotate(rotation)
        self.path = self.directory / "selection.pdf"
        writer.write(self.path)
        self.source = load_source(self.path)
        self.broker = RenderBroker()
        self.received = []
        self.broker.text_selected.connect(lambda *result: self.received.append(result))

    def tearDown(self):
        self.broker.shutdown()
        wait_for(lambda: self.broker.is_closed)
        self.temporary.cleanup()

    def request(self, page, start, end, token):
        self.broker.request_text_selection(self.source, page, start, end, token)
        wait_for(lambda: any(result[0] == token for result in self.received))
        return next(result for result in self.received if result[0] == token)

    def test_actual_text_bounds_with_crop_and_all_intrinsic_rotations(self):
        # These endpoints cross only the first actual text line, not a region
        # guessed from PDF content operators or a full drag bounding box.
        for index, rotation in enumerate((0, 90, 180, 270)):
            def normalized(x, y):
                return ((x / 220, y / 330), (1 - y / 330, x / 220),
                        (1 - x / 220, 1 - y / 330), (y / 330, 1 - x / 220))[index]
            start, end = normalized(19, 37), normalized(101, 37)
            # Workbench rotation must not change the source selection space.
            page = PageRef(f"page-{index}", self.source.source_id, index, 270)
            for reverse in (False, True):
                token = f"{rotation}-{reverse}"
                _, rects, text, error = self.request(page, end if reverse else start, start if reverse else end, token)
                self.assertIsNone(error)
                self.assertEqual(text, "ALPHA beta")
                self.assertEqual(len(rects), 1)
                points = [normalized(x, y) for x, y in ((20, 30), (97, 30), (20, 40), (97, 40))]
                expected = (min(p[0] for p in points), min(p[1] for p in points),
                            max(p[0] for p in points), max(p[1] for p in points))
                for actual, value in zip(rects[0], expected):
                    self.assertAlmostEqual(actual, value, delta=.01)

    def test_multiple_lines_keep_distinct_regions(self):
        page = PageRef("multi", self.source.source_id, 0)
        _, rects, text, error = self.request(page, (19 / 220, 37 / 330), (111 / 220, 67 / 330), "multi")
        self.assertIsNone(error)
        self.assertIn("ALPHA beta", text)
        self.assertIn("GAMMA delta", text)
        self.assertEqual(len(rects), 2)
        self.assertLess(rects[0][3], rects[1][1])

    def test_blank_page_does_not_invent_text_regions(self):
        writer = PdfWriter()
        writer.add_blank_page(300, 500)
        path = self.directory / "blank.pdf"
        writer.write(path)
        source = load_source(path)
        self.broker.request_text_selection(source, PageRef("blank", source.source_id, 0), (0, 0), (1, 1), "blank")
        wait_for(lambda: bool(self.received))
        _, rects, text, error = self.received[0]
        self.assertEqual(rects, ())
        self.assertFalse(text.strip())
        self.assertIsNone(error)

    def test_latest_pending_selection_is_bounded_and_stale_result_is_dropped(self):
        self.broker.dispatch_selection.disconnect(self.broker.worker.select_text)
        page = PageRef("held", self.source.source_id, 0)
        self.broker.request_text_selection(self.source, page, (0, 0), (1, 1), "first")
        old_generation = self.broker.generation
        for number in range(100):
            self.broker.request_text_selection(self.source, page, (0, 0), (1, 1), number)
        self.assertEqual(self.broker._selection_inflight[1], "first")
        self.assertEqual(self.broker._selection_pending[1], 99)
        self.broker.invalidate()
        self.broker.request_text_selection(self.source, page, (0, 0), (1, 1), "new")
        self.broker._selection_finished((old_generation, "first", ((0, 0, 1, 1),), "stale", None))
        self.assertEqual(self.received, [])
        self.assertEqual(self.broker._selection_inflight[1], "new")
        self.broker._selection_finished((self.broker.generation, "new", ((0, 0, 1, 1),), "current", None))
        self.assertEqual([result[0] for result in self.received], ["new"])

    def test_invalid_coordinate_error_is_async_and_generation_checked(self):
        page = PageRef("invalid", self.source.source_id, 0)
        self.broker.request_text_selection(self.source, page, (float("nan"), 0), (1, 1), "bad")
        self.assertFalse(self.received)
        wait_for(lambda: bool(self.received))
        self.assertIn("Ungültige", self.received[0][3])
        self.received.clear()
        self.broker.request_text_selection(self.source, page, (float("inf"), 0), (1, 1), "stale-bad")
        self.broker.invalidate()
        APP.processEvents()
        self.assertFalse(self.received)

    def test_shutdown_ignores_pending_selection(self):
        page = PageRef("closing", self.source.source_id, 0)
        self.broker.request_text_selection(self.source, page, (0, 0), (1, 1), "closing")
        self.broker.shutdown()
        wait_for(lambda: self.broker.is_closed)
        self.assertEqual(self.received, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
