"""Scroll/zoom preview; all PDF rasterization is delegated to RenderBroker."""
import math
import uuid

from PyQt6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QScrollArea, QSizePolicy, QWidget
from .annotation_overlay import display_to_basis, draw_annotations, map_point
from .theme import color


class PreviewCanvas(QWidget):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self._pan = None
        self._placement_start = None
        self._placement_end = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), color("canvas"))
        view = self.view
        if view.page is None or view.image.isNull():
            painter.setPen(color("muted"))
            painter.drawText(self.rect().adjusted(24, 24, -24, -24), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, view.message)
            return
        paper = view.paper_rect()
        painter.fillRect(paper.translated(3, 4), color("shadow"))
        painter.fillRect(paper, QColor("white"))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(paper, view.image)
        draw_annotations(painter, paper, view.page)
        if self._placement_start is not None and self._placement_end is not None:
            painter.setPen(QPen(color("accent"), 1.5, Qt.PenStyle.DashLine))
            fill = color("accent"); fill.setAlpha(25)
            painter.setBrush(fill)
            painter.drawRect(QRectF(self._placement_start, self._placement_end).normalized())

    def wheelEvent(self, event):
        if not self.view.handle_wheel(event): super().wheelEvent(event)

    def mousePressEvent(self, event):
        view = self.view
        if event.button() == Qt.MouseButton.MiddleButton or (event.button() == Qt.MouseButton.LeftButton and view.tool == "pan"):
            self.cancel_placement()
            self._pan = (event.globalPosition(), view.scroll.horizontalScrollBar().value(), view.scroll.verticalScrollBar().value())
            self.setCursor(Qt.CursorShape.ClosedHandCursor); event.accept(); return
        if event.button() == Qt.MouseButton.LeftButton and view.geometry_valid and not view.image.isNull() and view.paper_rect().contains(event.position()):
            self._placement_start = self._placement_end = event.position()
            event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan is not None:
            origin, horizontal, vertical = self._pan
            delta = event.globalPosition() - origin
            self.view.scroll.horizontalScrollBar().setValue(horizontal - round(delta.x()))
            self.view.scroll.verticalScrollBar().setValue(vertical - round(delta.y()))
            event.accept(); return
        if self._placement_start is not None:
            paper = self.view.paper_rect()
            self._placement_end = QPointF(max(paper.left(), min(paper.right(), event.position().x())), max(paper.top(), min(paper.bottom(), event.position().y())))
            self.update(); event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pan is not None and event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            self._pan = None; self.view.update_cursor(); event.accept(); return
        if event.button() == Qt.MouseButton.LeftButton and self._placement_start is not None:
            start, end = self._placement_start, self._placement_end
            self._placement_start = self._placement_end = None; self.update()
            self.view.place_annotation(start, end); event.accept(); return
        super().mouseReleaseEvent(event)

    def cancel_placement(self):
        if self._placement_start is not None:
            self._placement_start = self._placement_end = None
            self.update()
            self.view.status_message.emit("Platzierung nach Ansichtswechsel abgebrochen. Für eine Anmerkung erneut klicken oder ziehen.")

    def resizeEvent(self, event):
        self.cancel_placement()
        super().resizeEvent(event)


class Preview(QWidget):
    scale_changed = pyqtSignal(float, str)
    annotation_added = pyqtSignal(str, object)
    comment_requested = pyqtSignal(str, object)
    status_message = pyqtSignal(str)
    selection_pending_changed = pyqtSignal(bool)

    def __init__(self, broker, parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QVBoxLayout
        self.broker = broker
        self.source = self.page = None
        self.geometry_valid = False
        self.image = QImage()
        self.message = "Eine Seite auswählen, um sie hier zu prüfen."
        self.points = (300.0, 500.0)
        self.paper_size = (300, 500)
        self.zoom_factor = 1.0
        self.zoom_mode = "page"
        self._key = None
        self._revision = 0
        self.tool = "pan"
        self.signature_strokes = ()
        self._selection_token = None
        self._selection_revision = None
        self._wheel_remainder = [0.0, 0.0]
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setAccessibleName("PDF-Vorschau mit Zoom und Scrollen")
        self.canvas = PreviewCanvas(self)
        self.scroll.setWidget(self.canvas)
        self.scroll.viewport().installEventFilter(self)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.addWidget(self.scroll)
        self.setMinimumSize(240, 180)
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(90)
        self._timer.timeout.connect(self._request)
        broker.ready.connect(self._rendered)
        broker.text_selected.connect(self._text_selected)
        self.update_cursor()

    def paper_rect(self):
        width, height = self.paper_size
        return QRectF((self.canvas.width() - width) / 2, (self.canvas.height() - height) / 2, width, height)

    def canvas_to_basis(self, point):
        paper = self.paper_rect()
        x = max(0, min(1, (point.x() - paper.left()) / paper.width()))
        y = max(0, min(1, (point.y() - paper.top()) / paper.height()))
        return display_to_basis((x, y), self.page.rotation if self.page else 0)

    def basis_to_canvas(self, point):
        return map_point(point, self.paper_rect(), self.page.rotation if self.page else 0)

    def set_tool(self, tool, strokes=()):
        if tool not in ("pan", "comment", "highlight", "signature"): raise ValueError("Unbekanntes Vorschauwerkzeug")
        self.tool = tool; self.signature_strokes = strokes
        self._clear_selection()
        self.canvas._placement_start = self.canvas._placement_end = None
        self.update_cursor(); self.canvas.update()

    def update_cursor(self):
        self.canvas.setCursor(Qt.CursorShape.OpenHandCursor if self.tool == "pan" else Qt.CursorShape.CrossCursor)

    def place_annotation(self, start, end):
        if self.page is None or not self.geometry_valid: return
        if self.tool == "comment":
            x, y = self.canvas_to_basis(start)
            width, height = .055, .04
            x, y = min(x, 1 - width), min(y, 1 - height)
            self.comment_requested.emit(self.page.uid, (x, y, x + width, y + height))
        elif self.tool == "highlight":
            if (start - end).manhattanLength() < 3:
                self.status_message.emit("Zum Markieren über vorhandenen PDF-Text ziehen."); return
            token = uuid.uuid4().hex
            self._selection_token, self._selection_revision = token, self._revision
            self.selection_pending_changed.emit(True)
            self.status_message.emit("PDF-Text wird ausgewählt …")
            self.broker.request_text_selection(self.source, self.page, self.canvas_to_basis(start), self.canvas_to_basis(end), token)
        elif self.tool == "signature" and self.signature_strokes:
            if (start - end).manhattanLength() < 8:
                paper = self.paper_rect()
                width, height = paper.width() * .30, paper.height() * .12
                left = min(start.x(), paper.right() - width); top = min(start.y(), paper.bottom() - height)
                start, end = QPointF(left, top), QPointF(left + width, top + height)
            a, b = self.canvas_to_basis(start), self.canvas_to_basis(end)
            rect = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
            if rect[2] - rect[0] < .005 or rect[3] - rect[1] < .005:
                self.status_message.emit("Für die Unterschrift einen größeren Bereich ziehen oder einmal klicken."); return
            from ..annotations import PageAnnotation
            strokes = tuple(tuple(display_to_basis(point, self.page.rotation) for point in stroke) for stroke in self.signature_strokes)
            self.annotation_added.emit(self.page.uid, PageAnnotation(uuid.uuid4().hex, "signature", rect, strokes=strokes))

    def _text_selected(self, token, rects, text, error):
        if token != self._selection_token or self._selection_revision != self._revision or self.page is None or self.tool != "highlight": return
        self._clear_selection()
        if error:
            self.status_message.emit("Textauswahl nicht verfügbar: " + error); return
        if not rects or not text.strip():
            self.status_message.emit("Hier wurde kein auswählbarer PDF-Text gefunden. Gescannte Seiten benötigen zuerst Texterkennung."); return
        from ..annotations import PageAnnotation
        rect = (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))
        self.annotation_added.emit(self.page.uid, PageAnnotation(uuid.uuid4().hex, "highlight", rect, text=text, rects=tuple(rects)))

    @property
    def selection_pending(self):
        return self._selection_token is not None

    def _clear_selection(self):
        if self._selection_token is not None:
            self._selection_token = None
            self.selection_pending_changed.emit(False)

    def handle_wheel(self, event):
        pixel, angle = event.pixelDelta(), event.angleDelta()
        dx, dy = (pixel.x(), pixel.y()) if not pixel.isNull() else (angle.x(), angle.y())
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and not dx and dy:
            exponent = max(-8, min(8, dy / (240 if not pixel.isNull() else 120)))
            factor = math.pow(1.25, exponent)
            target = self.zoom_factor * factor
            if factor >= 1:
                target = max(self.zoom_factor, min(4.0, target))
            self.set_zoom(target, relative=True)
            event.accept(); return True
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and not dx:
            dx, dy = dy, 0
        bars = (self.scroll.horizontalScrollBar(), self.scroll.verticalScrollBar())
        handled = False
        for index, (bar, delta) in enumerate(zip(bars, (dx, dy))):
            if not delta: continue
            handled |= bar.maximum() > bar.minimum()
            distance = delta if not pixel.isNull() else delta / 120 * QApplication.wheelScrollLines() * bar.singleStep()
            distance += self._wheel_remainder[index]
            rounded = round(distance); self._wheel_remainder[index] = distance - rounded
            bar.setValue(bar.value() - rounded)
        if handled:
            event.accept(); return True
        event.ignore(); return False

    def show_page(self, source, page):
        # Editing an overlay preserves the current zoom/pan and source raster.
        changed = (self.source != source or self.page is None or page is None or
                   (self.page.uid, self.page.page_index, self.page.rotation) != (page.uid, page.page_index, page.rotation))
        self.source, self.page = source, page
        self._revision += 1
        self._clear_selection()
        self.canvas._placement_start = self.canvas._placement_end = None
        if changed:
            self.image = QImage(); self._key = None
            self.geometry_valid = source is not None and page is not None
            self.message = "Seite wird geladen …" if page else "Eine Seite auswählen, um sie hier zu prüfen."
            if source is not None and page is not None:
                self.points = source.page_sizes[page.page_index]
                if any(not math.isfinite(n) or n <= 0 for n in self.points):
                    self.geometry_valid = False
                    self.points = (300.0, 500.0)
                    self.message = "Vorschau nicht verfügbar: ungültiges Seitenformat."
                if page.rotation % 180:
                    self.points = self.points[::-1]
            self.scroll.horizontalScrollBar().setValue(0); self.scroll.verticalScrollBar().setValue(0)
        self._layout_page()
        self.canvas.update()
        if page is not None:
            self._timer.start(0)

    def _layout_page(self):
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded if self.zoom_mode == "custom" else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        base_width, base_height = (point * 96 / 72 for point in self.points)
        viewport = self.scroll.viewport().size()
        if self.zoom_mode == "page":
            self.zoom_factor = min(max(40, viewport.width() - 48) / base_width, max(40, viewport.height() - 48) / base_height)
        elif self.zoom_mode == "width":
            self.zoom_factor = max(40, viewport.width() - 48) / base_width
        self.paper_size = (max(1, round(base_width * self.zoom_factor)), max(1, round(base_height * self.zoom_factor)))
        self.canvas.setMinimumSize(self.paper_size[0] + 48, self.paper_size[1] + 48)
        self.scale_changed.emit(self.zoom_factor, self.zoom_mode)
        self.canvas.update()

    def set_zoom(self, factor, relative=False):
        if not math.isfinite(factor) or factor <= 0:
            return
        self.canvas.cancel_placement()
        self.zoom_mode = "custom"
        self.zoom_factor = factor if relative else max(0.01, min(4.0, factor))
        self._layout_page(); self._timer.start()

    def zoom_in(self):
        self.set_zoom(max(self.zoom_factor, min(4.0, self.zoom_factor * 1.25)), relative=True)

    def zoom_out(self):
        self.set_zoom(self.zoom_factor / 1.25, relative=True)

    def fit_page(self):
        self.canvas.cancel_placement()
        self.zoom_mode = "page"; self._layout_page(); self._timer.start()

    def fit_width(self):
        self.canvas.cancel_placement()
        self.zoom_mode = "width"; self._layout_page(); self._timer.start()

    def _request(self):
        if self.page is None or not self.geometry_valid or not self.isVisible():
            return
        ratio = min(2.0, max(1.0, self.devicePixelRatioF()))
        bounds = (min(3200, round(self.paper_size[0] * ratio)), min(3200, round(self.paper_size[1] * ratio)))
        self._key = self.broker.request(self.source, self.page, bounds, "preview")
        cached = self.broker.get(self._key)
        if cached:
            self._accept(*cached)
        elif self._key in self.broker.failures:
            self._rendered(self._key, QImage(), None, self.broker.failures[self._key])

    def _rendered(self, key, image, points, error):
        if key != self._key or self.page is None:
            return
        if error:
            self.message = "Vorschau nicht verfügbar. Die Seiten können weiterhin bearbeitet werden."
            self.image = QImage(); self.canvas.update()
        else:
            self._accept(image, points)

    def _accept(self, image, points):
        self.image = image
        if points and tuple(points) != tuple(self.points):
            self.points = points
            self._layout_page()
            # CropBox/intrinsic rotation can change the initial MediaBox guess.
            # Re-render once at the resulting display size, rather than stretching
            # a smaller first image indefinitely.
            self._timer.start()
        self.canvas.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_page(); self._timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self._layout_page(); self._timer.start()

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Wheel:
            return self.handle_wheel(event)
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Resize and hasattr(self, "_timer"):
            QTimer.singleShot(0, self._viewport_resized)
        return super().eventFilter(watched, event)

    def _viewport_resized(self):
        self._layout_page(); self._timer.start()
