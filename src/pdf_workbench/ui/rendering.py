"""Bounded, asynchronous QtPdf rendering with immutable source snapshots."""
from __future__ import annotations

from collections import OrderedDict
import math

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QPointF, QSize, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage, QTransform
from PyQt6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions


class PdfRenderWorker(QObject):
    result = pyqtSignal(object)
    selection_result = pyqtSignal(object)
    stopped = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.documents = OrderedDict()

    def _document(self, source):
        """Create and use every QtPdf object on this worker's thread."""
        if source.source_id not in self.documents:
            document = QPdfDocument(self)
            buffer = QBuffer(self)
            buffer.setData(QByteArray(source.data))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            document.load(buffer)
            self.documents[source.source_id] = (document, buffer)
            while len(self.documents) > 3:
                _, (old_document, old_buffer) = self.documents.popitem(last=False)
                old_document.close(); old_document.deleteLater(); old_buffer.deleteLater()
        self.documents.move_to_end(source.source_id)
        document, _ = self.documents[source.source_id]
        if document.status() != QPdfDocument.Status.Ready:
            raise ValueError("QtPdf konnte diese Seite nicht öffnen.")
        return document

    @pyqtSlot(object)
    def render(self, task):
        key, source, page, bounds, generation = task
        try:
            document = self._document(source)
            points = document.pagePointSize(page.page_index)
            if any(not math.isfinite(n) or n <= 0 for n in (points.width(), points.height())):
                raise ValueError("Ungültiges Seitenformat; für diese Seite ist keine Vorschau verfügbar.")
            target = QSize(*bounds)
            if page.rotation % 180:
                target.transpose()
            size = points.toSize().scaled(target, Qt.AspectRatioMode.KeepAspectRatio)
            options = QPdfDocumentRenderOptions()
            options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
            image = document.render(page.page_index, size, options)
            if image.isNull():
                raise ValueError("Für diese Seite ist keine Vorschau verfügbar.")
            width, height = points.width(), points.height()
            if page.rotation:
                image = image.transformed(QTransform().rotate(page.rotation))
                if page.rotation % 180:
                    width, height = height, width
            self.result.emit((generation, key, image, (width, height), None))
        except Exception as error:
            self.result.emit((generation, key, QImage(), None, str(error)))

    @pyqtSlot(object)
    def select_text(self, task):
        generation, token, source, page, start, end = task
        try:
            document = self._document(source)
            points = document.pagePointSize(page.page_index)
            width, height = points.width(), points.height()
            if any(not math.isfinite(n) or n <= 0 for n in (width, height)):
                raise ValueError("Für dieses Seitenformat ist keine Textauswahl verfügbar.")
            selection = document.getSelection(
                page.page_index,
                QPointF(start[0] * width, start[1] * height),
                QPointF(end[0] * width, end[1] * height),
            )
            text = selection.text()
            rectangles = []
            if selection.isValid() and text.strip():
                for polygon in selection.bounds():
                    box = polygon.boundingRect().normalized()
                    rect = (
                        max(0.0, min(1.0, box.left() / width)),
                        max(0.0, min(1.0, box.top() / height)),
                        max(0.0, min(1.0, box.right() / width)),
                        max(0.0, min(1.0, box.bottom() / height)),
                    )
                    if all(math.isfinite(n) for n in rect) and rect[2] > rect[0] and rect[3] > rect[1]:
                        rectangles.append(rect)
            self.selection_result.emit((generation, token, tuple(rectangles), text, None))
        except Exception as error:
            self.selection_result.emit((generation, token, (), "", str(error)))

    @pyqtSlot()
    def clear_documents(self):
        for document, buffer in self.documents.values():
            document.close(); document.deleteLater(); buffer.deleteLater()
        self.documents.clear()

    @pyqtSlot()
    def stop(self):
        self.clear_documents()
        self.stopped.emit()


class RenderBroker(QObject):
    """Only visible thumbnails are requested; one worker owns all QtPdf objects."""

    dispatch = pyqtSignal(object)
    dispatch_selection = pyqtSignal(object)
    stop_worker = pyqtSignal()
    clear_worker_documents = pyqtSignal()
    ready = pyqtSignal(object, object, object, object)
    text_selected = pyqtSignal(object, object, str, object)
    closed = pyqtSignal()

    MAX_CACHE_BYTES = 64 * 1024 * 1024
    MAX_PENDING = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        self.generation = 0
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.pending = OrderedDict()
        self.inflight = None
        self.render_count = 0
        self.failures = OrderedDict()
        self._selection_inflight = None
        self._selection_pending = None
        self._closing = False
        self.is_closed = False
        self.thread = QThread(self)
        self.worker = PdfRenderWorker()
        self.worker.moveToThread(self.thread)
        self.dispatch.connect(self.worker.render)
        self.dispatch_selection.connect(self.worker.select_text)
        self.stop_worker.connect(self.worker.stop)
        self.clear_worker_documents.connect(self.worker.clear_documents)
        self.worker.result.connect(self._finished)
        self.worker.selection_result.connect(self._selection_finished)
        self.worker.stopped.connect(self.thread.quit)
        self.worker.stopped.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._closed)
        self.thread.start()

    @property
    def pending_count(self):
        return len(self.pending) + bool(self.inflight)

    @staticmethod
    def key(source, page, bounds, kind):
        return (source.source_id, page.page_index, page.rotation, int(bounds[0]), int(bounds[1]), kind)

    def get(self, key):
        result = self.cache.get(key)
        if result is not None:
            self.cache.move_to_end(key)
        return result

    def request(self, source, page, bounds, kind="thumbnail"):
        bounds = (max(32, min(3200, int(bounds[0]))), max(32, min(3200, int(bounds[1]))))
        key = self.key(source, page, bounds, kind)
        if self._closing or key in self.cache or key in self.failures or (self.inflight and self.inflight[0] == key and self.inflight[4] == self.generation):
            return key
        if key not in self.pending:
            if kind == "preview":
                # The active page is more useful than obsolete zoom requests.
                for old in [k for k in self.pending if k[-1] == "preview"]:
                    self.pending.pop(old)
                if len(self.pending) >= self.MAX_PENDING:
                    self.pending.popitem(last=True)
            elif len(self.pending) >= self.MAX_PENDING:
                return key
            self.pending[key] = (key, source, page, bounds, self.generation)
        if kind == "preview":
            self.pending.move_to_end(key, last=False)
        self._pump()
        return key

    def invalidate(self, clear_cache=False):
        self.generation += 1
        self.pending.clear()
        self.failures.clear()
        self._selection_pending = None
        if clear_cache:
            self.cache.clear(); self.cache_bytes = 0
            self.clear_worker_documents.emit()

    def request_text_selection(self, source, page, start, end, token):
        """Select text in source display coordinates, before extra page rotation.

        Endpoints and result rectangles are normalized to the visible page.
        At most one selection runs and one newer request waits; older pending
        drags are replaced. The caller also checks its token after page/tool edits.
        """
        if self._closing:
            return
        try:
            coordinates = tuple(float(n) for point in (start, end) for n in point)
            if len(coordinates) != 4 or not all(math.isfinite(n) for n in coordinates):
                raise ValueError("Ungültige Textauswahl.")
            coordinates = tuple(max(0.0, min(1.0, n)) for n in coordinates)
        except (TypeError, ValueError, OverflowError):
            # Match the asynchronous signal contract, even for invalid input.
            from PyQt6.QtCore import QTimer
            generation = self.generation
            QTimer.singleShot(0, lambda: self.text_selected.emit(token, (), "", "Ungültige Textauswahl.")
                              if not self._closing and generation == self.generation else None)
            return
        task = (self.generation, token, source, page, coordinates[:2], coordinates[2:])
        if self._selection_inflight is not None:
            self._selection_pending = task
        else:
            self._selection_pending = None
            self._selection_inflight = task
            self.dispatch_selection.emit(task)

    def _selection_finished(self, result):
        generation, token, rectangles, text, error = result
        self._selection_inflight = None
        if not self._closing and generation == self.generation:
            self.text_selected.emit(token, rectangles, text, error)
        # A handler can synchronously start a new selection; preserve that slot.
        if not self._closing and self._selection_inflight is None and self._selection_pending is not None:
            task, self._selection_pending = self._selection_pending, None
            if task[0] == self.generation:
                self._selection_inflight = task
                self.dispatch_selection.emit(task)

    def _pump(self):
        if not self._closing and self.inflight is None and self.pending:
            _, task = self.pending.popitem(last=False)
            self.inflight = task
            self.render_count += 1
            self.dispatch.emit(task)

    def _finished(self, result):
        generation, key, image, points, error = result
        self.inflight = None
        if not self._closing and generation == self.generation:
            if error is None:
                size = image.sizeInBytes()
                previous = self.cache.pop(key, None)
                if previous:
                    self.cache_bytes -= previous[0].sizeInBytes()
                if size <= self.MAX_CACHE_BYTES:
                    self.cache[key] = (image, points)
                    self.cache_bytes += size
                    while self.cache_bytes > self.MAX_CACHE_BYTES and self.cache:
                        _, (old, _) = self.cache.popitem(last=False)
                        self.cache_bytes -= old.sizeInBytes()
            else:
                self.failures[key] = error
                while len(self.failures) > 256:
                    self.failures.popitem(last=False)
            self.ready.emit(key, image, points, error)
        self._pump()

    def shutdown(self):
        if not self._closing:
            self._closing = True
            self.invalidate(clear_cache=True)
            self.stop_worker.emit()

    def _closed(self):
        self.is_closed = True
        self.closed.emit()
