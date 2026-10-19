"""Focused regressions for asynchronous rendering, geometry and UI state."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QMimeData, QPoint, QPointF, QSize, Qt
from PyQt6.QtGui import QIcon, QImage
from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QListWidgetItem
from pypdf import PdfWriter
from pdf_workbench.app import MainWindow
from pdf_workbench.core import OperationCancelled, PageRef, PdfWorkbenchError, SourceDocument, load_source
from pdf_workbench.ui.dialogs import PageRangeDialog, SeparateExportDialog
from pdf_workbench.ui.icons import VectorIcon
from pdf_workbench.ui.pages import MIME, PageList
from pdf_workbench.ui.preview import Preview
from pdf_workbench.ui.rendering import RenderBroker
from test_core import make_pdf

APP = QApplication.instance() or QApplication([])


def pump_until(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while not predicate():
        APP.processEvents(); time.sleep(0.005)
        if time.monotonic() > deadline: raise AssertionError("Qt regression timed out")
    APP.processEvents()


class UiRenderingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_path = self.root / "source.pdf"
        make_pdf(self.source_path, ("A1", "A2", "A3"))
        self.source = load_source(self.source_path)
        self.page = PageRef("page-1", self.source.source_id, 0)
        self.brokers = []
        self.windows = []

    def broker(self):
        result = RenderBroker(); self.brokers.append(result); return result

    def tearDown(self):
        for window in self.windows:
            if window.job: pump_until(lambda: window.job is None)
            window.saved_state = tuple(window.workspace.pages); window.close()
            pump_until(lambda: window.render_broker.is_closed)
        for broker in self.brokers:
            broker.shutdown(); pump_until(lambda: broker.is_closed)
        self.temp.cleanup()

    def test_same_key_is_queued_after_generation_invalidation(self):
        broker = self.broker()
        broker.dispatch.disconnect(broker.worker.render)  # Hold a real in-flight slot deterministically.
        key = broker.request(self.source, self.page, (180, 260), "preview")
        old_generation = broker.generation
        broker.invalidate()
        broker.request(self.source, self.page, (180, 260), "preview")
        self.assertIn(key, broker.pending)
        delivered = []
        broker.ready.connect(lambda *args: delivered.append(args))
        image = QImage(180, 260, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        broker._finished((old_generation, key, image, (300, 500), None))
        self.assertEqual(delivered, [])
        self.assertEqual(broker.inflight[4], broker.generation)
        broker._finished((broker.generation, key, image, (300, 500), None))
        self.assertEqual(len(delivered), 1)
        self.assertIsNotNone(broker.get(key))

    def test_failure_cache_is_presented_again(self):
        bad = self.root / "bad.pdf"
        bad.write_bytes(self.source_path.read_bytes().replace(b"%PDF-", b"XXXXX", 1))
        source = load_source(bad)
        broker = self.broker()
        preview = Preview(broker); preview.resize(400, 300); preview.show()
        try:
            page = PageRef("bad", source.source_id, 0)
            preview.show_page(source, page)
            pump_until(lambda: bool(broker.failures) and "nicht verfügbar" in preview.message)
            preview.show_page(self.source, self.page)
            pump_until(lambda: not preview.image.isNull())
            preview.show_page(source, page)
            pump_until(lambda: "nicht verfügbar" in preview.message)
            self.assertTrue(preview.image.isNull())
        finally: preview.close()

    def test_fit_width_has_no_horizontal_overflow(self):
        preview = Preview(self.broker()); preview.resize(400, 300); preview.show()
        try:
            preview.show_page(self.source, self.page); preview.fit_page(); preview.fit_width()
            pump_until(lambda: preview.scroll.horizontalScrollBar().maximum() == 0 and preview.canvas.minimumWidth() <= preview.scroll.viewport().width())
            self.assertGreater(preview.scroll.verticalScrollBar().maximum(), 0)
        finally: preview.close()

    def test_zoom_out_does_not_enlarge_a_huge_fitted_page(self):
        source = SourceDocument("huge", self.source.path, self.source.data, 1, ((10000.0, 10000.0),))
        page = PageRef("huge-page", "huge", 0)
        preview = Preview(self.broker()); preview.resize(400, 300)
        preview.show_page(source, page)
        before = preview.zoom_factor
        preview.zoom_out()
        self.assertLess(preview.zoom_factor, before)
        preview.close()

    def test_zero_width_pdf_does_not_raise_in_preview(self):
        writer = PdfWriter(); writer.add_blank_page(0, 500)
        path = self.root / "zero.pdf"; writer.write(path)
        with self.assertRaises(PdfWorkbenchError):
            load_source(path)
        # Keep the view defensive even if a caller provides geometry directly.
        source = SourceDocument("zero", path, path.read_bytes(), 1, ((0.0, 500.0),))
        preview = Preview(self.broker()); preview.resize(400, 300); preview.show()
        try:
            preview.show_page(source, PageRef("zero-page", source.source_id, 0))
            pump_until(lambda: "nicht verfügbar" in preview.message)
            self.assertTrue(preview.image.isNull())
        finally: preview.close()

    def test_icon_rasterization_is_transparent_at_two_scales(self):
        for name in ("add", "duplicate", "rotate_left", "fit_width", "export"):
            for scale in (1.0, 2.0):
                image = VectorIcon(name).scaledPixmap(QSize(24, 24), QIcon.Mode.Normal, QIcon.State.Off, scale).toImage()
                self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
                self.assertTrue(any(image.pixelColor(x, y).alpha() > 0 for y in range(image.height()) for x in range(image.width())))

    def test_cancelled_job_keeps_remaining_file_detail(self):
        window = MainWindow(); self.windows.append(window)
        remaining = self.root / "remaining.pdf"; remaining.write_bytes(b"existing remainder")
        detail = f"Einzelexport abgebrochen. 1 eigene PDF-Datei(en) verblieben: {remaining}. Nicht vollständig bereinigt."
        messages = []
        window.show_error = lambda title, message: messages.append(message)
        def task(cancel, progress): raise OperationCancelled(detail)
        window.start_job(task, lambda result: self.fail("Cancelled job must not succeed"))
        pump_until(lambda: window.job is None)
        self.assertEqual(window.status.text(), detail)
        self.assertEqual(messages, [detail])
        self.assertEqual(remaining.read_bytes(), b"existing remainder")

    def test_dialog_validation_rejects_existing_folder_and_bad_range(self):
        ranges = PageRangeDialog(None, 5)
        ranges.range_edit.setText("5, 1-3")
        self.assertEqual(ranges.selected_indices, [0, 1, 2, 4])
        ranges.range_edit.setText("6")
        self.assertFalse(ranges.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
        ranges.close()
        dialog = SeparateExportDialog(None, 2)
        dialog.parent_edit.setText(str(self.root)); dialog.folder_edit.setText("new-output")
        self.assertEqual(dialog.destination, self.root / "new-output")
        (self.root / "new-output").mkdir(); dialog.validate()
        self.assertFalse(dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled())
        dialog.close()

    def test_drag_edge_scroll_and_insertion_feedback(self):
        window = MainWindow(); self.windows.append(window)
        writer = PdfWriter()
        for _ in range(100): writer.add_blank_page(300, 500)
        path = self.root / "hundred.pdf"; writer.write(path)
        ids = window.workspace.add_sources([load_source(path)])
        window.refresh([ids[0]], ids[0]); window.resize(800, 560); window.show(); APP.processEvents()
        view = window.page_list
        mime = QMimeData(); mime.setData(MIME, json.dumps(ids[:2]).encode())
        class DragEvent:
            def source(self): return view
            def mimeData(self): return mime
            def position(self): return QPointF(30, view.viewport().height() - 3)
            def acceptProposedAction(self): pass
            def ignore(self): pass
        before = view.verticalScrollBar().value()
        view.dragMoveEvent(DragEvent())
        pump_until(lambda: view.verticalScrollBar().value() > before)
        self.assertIsNotNone(view._drop_row)
        self.assertTrue(view._drag_scroll.isActive())
        view._clear_drag_feedback()
        order = view.reordered_ids(ids[:2], 50, True)
        self.assertEqual(len(order), 100)
        self.assertEqual(set(order), set(ids))
        self.assertEqual(order.index(ids[1]), order.index(ids[0]) + 1)

    def test_insertion_line_remains_visible_at_both_viewport_edges(self):
        view = PageList(self.broker()); view.resize(145, 330)
        for index in range(100): view.addItem(QListWidgetItem(str(index)))
        view.show(); APP.processEvents()
        try:
            # A narrow real view has a single card column touching both edges.
            rect = view.visualItemRect(view.item(0))
            self.assertLessEqual(rect.left(), 1)
            self.assertGreaterEqual(rect.right(), view.viewport().width() - 4)
            for after in (False, True):
                view._drop_row = 0; view._drop_after = after
                view.viewport().update(); APP.processEvents()
                image = view.viewport().grab().toImage()
                width, height = image.width(), image.height()
                edge = range(max(0, width - 8), width) if after else range(min(8, width))
                pixels = sum(image.pixelColor(x, y).name() == "#285cc4" for x in edge for y in range(height))
                self.assertGreater(pixels, 100)
        finally:
            view.close()

    def test_visible_active_card_survives_grid_reflow_on_resize(self):
        path = self.root / "five.pdf"
        make_pdf(path, tuple(f"P{i}" for i in range(5)))
        window = MainWindow(); self.windows.append(window)
        window.resize(1160, 780); window.show()
        ids = window.workspace.add_sources([load_source(path)])
        window.refresh([ids[-1]], ids[-1], ensure_visible=True)
        pump_until(lambda: window.page_list.viewport().rect().contains(window.page_list.visualItemRect(window.page_list.currentItem())))
        APP.processEvents()
        window.resize(800, 560)
        pump_until(lambda: window.page_list.viewport().rect().contains(window.page_list.visualItemRect(window.page_list.currentItem())))
        # A deliberate free scroll away from the active page is respected.
        window.page_list.verticalScrollBar().setValue(0)
        APP.processEvents()
        self.assertIsNone(window.page_list._last_visible_active_uid)
        window.resize(1160, 780); APP.processEvents()
        self.assertEqual(window.page_list.verticalScrollBar().value(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
