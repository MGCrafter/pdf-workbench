"""Native Qt integration smoke: real async previews, tools, exports and bounded rendering."""
from __future__ import annotations
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from PyQt6.QtCore import QItemSelectionModel, QPoint, QSize
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Rectangle
from pdf_workbench.app import MainWindow, STYLE
from pdf_workbench.ui.icons import VectorIcon
from test_core import make_pdf

app = QApplication.instance() or QApplication([])
app.setStyle("Fusion"); app.setStyleSheet(STYLE)


def wait_for(predicate, seconds=15):
    until = time.monotonic() + seconds
    while not predicate():
        app.processEvents(); time.sleep(0.005)
        if time.monotonic() > until: raise AssertionError("Qt operation timed out")
    app.processEvents()


def close_window(window):
    if window.job:
        window.job.cancel_event.set()
        wait_for(lambda: window.job is None)
    window.saved_state = tuple(window.workspace.pages)
    window.close()
    wait_for(lambda: window.render_broker.is_closed)
    app.processEvents()


windows = []
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    source, second = root / "source.pdf", root / "second.pdf"
    make_pdf(source, ("UI1", "UI2", "UI3")); make_pdf(second, ("B1", "B2"))
    writer = PdfWriter()
    for page in PdfReader(source).pages: writer.add_page(page)
    writer.add_annotation(0, Rectangle((60, 180, 240, 300), interior_color="00ff00")); writer.write(source)
    original = source.read_bytes()
    try:
        window = MainWindow(); windows.append(window)
        errors = []
        window.show_error = lambda title, detail: errors.append((title, detail))
        window.show(); app.processEvents()
        assert not window.export_button.isEnabled()
        window.import_files([str(source)])
        wait_for(lambda: window.job is None)
        wait_for(lambda: not window.preview.image.isNull())
        assert window.page_list.count() == 3
        assert window.preview.image.height() > window.preview.image.width()
        image = window.preview.image
        assert any((color := image.pixelColor(x, y)).green() > 180 and color.red() < 80 and color.blue() < 80 for y in range(0, image.height(), 4) for x in range(0, image.width(), 4)), "Annotation missing from preview"

        # Import B while A2 is active: focus, selection, preview and scroll follow B.
        window.select_page_range("2")
        window.resize(800, 560); app.processEvents()
        window.import_files([str(second)])
        wait_for(lambda: window.job is None)
        wait_for(lambda: not window.preview.image.isNull() and window.preview.source.path == second)
        assert len(window.selected_ids()) == 2
        assert window.page_list.currentRow() == 3
        assert window.page_list.viewport().rect().contains(window.page_list.visualItemRect(window.page_list.currentItem()))
        # All-failed import must preserve an existing multiple selection and focus.
        previous = (window.selected_ids(), window._active_uid, window.preview.page)
        window.import_files([str(root / "missing.pdf")])
        wait_for(lambda: window.job is None)
        assert previous == (window.selected_ids(), window._active_uid, window.preview.page)
        assert errors; errors.clear()
        window.delete_button.click()
        assert len(window.workspace.pages) == 3
        assert len(window.selected_ids()) == 1 and window.page_list.currentRow() == 2
        assert window.preview.page.uid in window.selected_ids()
        window.undo_button.click()
        assert len(window.workspace.pages) == 5
        window.duplicate_button.click()
        assert len(window.workspace.pages) == 6 and len(window.selected_ids()) == 1
        assert window.workspace.pages[window.page_list.currentRow()].uid in window.selected_ids()
        window.undo_button.click()
        assert len(window.workspace.pages) == 5

        with patch("pdf_workbench.app.PageRangeDialog.get_selection", return_value=[0]):
            window.range_button.click()
        window.right_button.click()
        wait_for(lambda: not window.preview.image.isNull() and window.preview.image.width() > window.preview.image.height())
        assert window.workspace.pages[0].rotation == 90
        window.down_button.click()
        assert window.workspace.pages[1].page_index == 0
        window.delete_button.click(); assert len(window.workspace.pages) == 4
        window.undo_button.click(); assert len(window.workspace.pages) == 5
        window.select_page_range("1-2")
        result = root / "selected.pdf"
        with patch("pdf_workbench.app.QFileDialog.getSaveFileName", return_value=(str(result), "PDF")):
            window.split_button.click(); wait_for(lambda: window.job is None)
        assert [p.extract_text().strip() for p in PdfReader(result).pages] == ["UI2", "UI1"]
        individual = root / "individual"
        with patch("pdf_workbench.app.SeparateExportDialog.get_destination", return_value=individual):
            window.separate_button.click(); wait_for(lambda: window.job is None)
        assert [PdfReader(p).pages[0].extract_text().strip() for p in sorted(individual.glob("*.pdf"))] == ["UI2", "UI1"]
        assert source.read_bytes() == original

        selected, active = window.selected_ids(), window._active_uid
        window.list_button.click(); assert not window.page_list.grid_mode
        window.grid_button.click(); assert window.page_list.grid_mode
        assert (selected, active) == (window.selected_ids(), window._active_uid)
        before = window.preview.zoom_factor
        window.zoom_in_button.click(); assert window.preview.zoom_factor > before
        window.fit_width_button.click(); assert window.preview.zoom_mode == "width"
        wait_for(lambda: window.preview.scroll.horizontalScrollBar().maximum() == 0)
        window.fit_page_button.click(); assert window.preview.zoom_mode == "page"
        window.preview_toggle.click(); assert not window.preview_panel.isVisible()
        window.preview_toggle.click(); assert window.preview_panel.isVisible()
        for width, height in [(800, 560), (1160, 780), (1440, 900)]:
            window.resize(width, height); app.processEvents()
            assert window.width() == width and window.height() == height
            for button in (window.export_button, window.separate_button, window.preview_toggle):
                point = button.mapTo(window, QPoint(0, 0))
                assert window.rect().contains(point) and window.rect().contains(point + QPoint(button.width() - 1, button.height() - 1))
        # A defined transparent pixmap is necessary: paint()-only engines can expose garbage pixels.
        for scale in (1.0, 2.0):
            pixmap = VectorIcon("duplicate").scaledPixmap(QSize(20, 20), QIcon.Mode.Normal, QIcon.State.Off, scale)
            assert pixmap.toImage().pixelColor(0, 0).alpha() == 0
            assert pixmap.devicePixelRatioF() == scale
        window.resize(1160, 780)
        wait_for(lambda: not window.preview.image.isNull())
        screenshot = os.environ.get("PDF_WORKBENCH_SCREENSHOT")
        if screenshot: window.grab().save(screenshot)
        assert not errors, errors
        close_window(window); windows.remove(window)

        # A 500-page source must not trigger a synchronous or unbounded thumbnail pass.
        large = root / "many-pages.pdf"
        make_pdf(large, tuple(f"PAGE{i + 1}" for i in range(500)))
        many = MainWindow(); windows.append(many)
        many.show(); many.import_files([str(large)])
        wait_for(lambda: many.job is None)
        wait_for(lambda: not many.preview.image.isNull())
        assert many.page_list.count() == 500
        assert many.render_broker.pending_count <= many.render_broker.MAX_PENDING + 1
        assert many.render_broker.render_count < 50
        assert many.render_broker.cache_bytes <= many.render_broker.MAX_CACHE_BYTES
        many.page_list.setCurrentRow(499, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        wait_for(lambda: many.preview._key is not None and many.preview._key[1] == 499 and not many.preview.image.isNull())
        assert many.preview.page.page_index == 499
        close_window(many); windows.remove(many)
    finally:
        for window in windows:
            close_window(window)
print(f"Qt UI smoke passed ({app.platformName()}): async thumbnails/preview, import focus, delete neighbor, duplicate, range, exports, zoom, layout, icons, 500-page lazy rendering.")
