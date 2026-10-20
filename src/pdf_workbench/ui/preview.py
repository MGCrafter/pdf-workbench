"""Scroll/zoom preview; all PDF rasterization is delegated to RenderBroker."""
import math
import uuid
from dataclasses import replace

from PyQt6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QMenu, QScrollArea, QSizePolicy, QWidget
from .annotation_overlay import display_to_basis, draw_annotations, map_point, map_rect, source_page_geometry, validate_text_object
from ..annotations import AnnotationError
from .theme import color


class PreviewCanvas(QWidget):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("PDF-Seite: Objekte auswählen, verschieben und bearbeiten")
        self._object_drag = None
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
        page = view.page
        if view._draft_annotation is not None:
            page = replace(page, annotations=tuple(view._draft_annotation if item.uid == view._draft_annotation.uid else item for item in page.annotations))
        draw_annotations(painter, paper, page, view.points, view.user_unit)
        selected = view._draft_annotation or view.selected_annotation
        if selected is not None:
            rect = map_rect(selected.rect, paper, view.page.rotation)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(color("error" if view._draft_error else "accent"), 1.3, Qt.PenStyle.DashLine))
            painter.drawRect(rect)
            painter.setBrush(color("surface"))
            for handle in view.object_handles(selected).values():
                painter.drawRect(handle)
        if view.search_hit and view.search_hit.page_uid == view.page.uid:
            fill = color('accent'); fill.setAlpha(55)
            painter.setBrush(fill); painter.setPen(QPen(color('accent'), 2))
            for rect in view.search_hit.rectangles:
                painter.drawRect(QRectF(view.basis_to_canvas(rect[:2]), view.basis_to_canvas(rect[2:])).normalized())
        if self._placement_start is not None and self._placement_end is not None:
            painter.setPen(QPen(color("accent"), 1.5, Qt.PenStyle.DashLine))
            fill = color("accent"); fill.setAlpha(25)
            painter.setBrush(fill)
            painter.drawRect(QRectF(self._placement_start, self._placement_end).normalized())

    def wheelEvent(self, event):
        if not self.view.handle_wheel(event): super().wheelEvent(event)

    def mousePressEvent(self, event):
        view = self.view
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if event.button() == Qt.MouseButton.MiddleButton or (event.button() == Qt.MouseButton.LeftButton and view.tool == "pan"):
            self.cancel_placement()
            self._pan = (event.globalPosition(), view.scroll.horizontalScrollBar().value(), view.scroll.verticalScrollBar().value())
            self.setCursor(Qt.CursorShape.ClosedHandCursor); event.accept(); return
        if event.button() == Qt.MouseButton.LeftButton and view.tool == "select" and view.geometry_valid and not view.image.isNull():
            view.begin_object_drag(event.position())
            event.accept(); return
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
        if self._object_drag is not None:
            self.view.update_object_drag(event.position(), event.modifiers())
            event.accept(); return
        if self._placement_start is not None:
            paper = self.view.paper_rect()
            self._placement_end = QPointF(max(paper.left(), min(paper.right(), event.position().x())), max(paper.top(), min(paper.bottom(), event.position().y())))
            self.update(); event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pan is not None and event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            self._pan = None; self.view.update_cursor(); event.accept(); return
        if event.button() == Qt.MouseButton.LeftButton and self._object_drag is not None:
            self.view.finish_object_drag()
            event.accept(); return
        if event.button() == Qt.MouseButton.LeftButton and self._placement_start is not None:
            start, end = self._placement_start, self._placement_end
            self._placement_start = self._placement_end = None; self.update()
            self.view.place_annotation(start, end); event.accept(); return
        super().mouseReleaseEvent(event)

    def cancel_object_drag(self):
        if self._object_drag is not None:
            self._object_drag = None
            self.view._draft_annotation = None
            self.view._draft_error = ""
            self.view.selection_pending_changed.emit(self.view.selection_pending)
            self.update()

    def cancel_placement(self):
        self.cancel_object_drag()
        if self._placement_start is not None:
            self._placement_start = self._placement_end = None
            self.update()
            self.view.status_message.emit("Platzierung nach Ansichtswechsel abgebrochen. Für eine Anmerkung erneut klicken oder ziehen.")

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.view.tool == "select":
            self.cancel_object_drag()
            annotation = self.view.hit_test_annotation(event.position())
            if annotation:
                self.view.set_selected_annotation(annotation.uid)
                self.view.annotation_edit_requested.emit(self.view.page.uid, annotation)
                event.accept(); return
        super().mouseDoubleClickEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride:
            selected = self.view.selected_annotation
            if (event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace, Qt.Key.Key_Escape) or
                    (selected is not None and event.key() in (Qt.Key.Key_Left,Qt.Key.Key_Right,Qt.Key.Key_Up,Qt.Key.Key_Down,Qt.Key.Key_Return,Qt.Key.Key_Enter,Qt.Key.Key_F2)) or
                    (event.key() == Qt.Key.Key_D and event.modifiers() & Qt.KeyboardModifier.ControlModifier)):
                event.accept(); return True
        return super().event(event)

    def keyPressEvent(self, event):
        view = self.view
        key = event.key()
        if key == Qt.Key.Key_Escape:
            pending = self._object_drag is not None or self._placement_start is not None
            self.cancel_placement()
            if not pending:
                view.clear_object_selection()
            event.accept(); return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if view.selected_annotation is not None:
                self.cancel_object_drag()
                view.annotation_action.emit("delete")
            event.accept(); return
        if key == Qt.Key.Key_D and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if view.selected_annotation is not None:
                self.cancel_object_drag()
                view.annotation_action.emit("duplicate")
            event.accept(); return
        if view.selected_annotation is not None:
            if key in (Qt.Key.Key_F2,Qt.Key.Key_Return,Qt.Key.Key_Enter):
                self.cancel_object_drag()
                view.annotation_edit_requested.emit(view.page.uid,view.selected_annotation)
                event.accept(); return
            delta = {Qt.Key.Key_Left:(-1,0),Qt.Key.Key_Right:(1,0),Qt.Key.Key_Up:(0,-1),Qt.Key.Key_Down:(0,1)}.get(key)
            if delta:
                self.cancel_object_drag()
                step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
                view.nudge_object(delta[0]*step,delta[1]*step)
                event.accept(); return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        annotation = self.view.hit_test_annotation(event.pos())
        if annotation is not None:
            self.cancel_object_drag()
            self.view.set_selected_annotation(annotation.uid)
            menu = QMenu(self)
            edit = menu.addAction("Bearbeiten …")
            duplicate = menu.addAction("Duplizieren")
            delete = menu.addAction("Löschen")
            chosen = menu.exec(event.globalPos())
            if chosen is edit:
                self.view.annotation_edit_requested.emit(self.view.page.uid,annotation)
            elif chosen is duplicate:
                self.view.annotation_action.emit("duplicate")
            elif chosen is delete:
                self.view.annotation_action.emit("delete")
            event.accept(); return
        super().contextMenuEvent(event)

    def focusOutEvent(self, event):
        self.cancel_placement()
        super().focusOutEvent(event)

    def resizeEvent(self, event):
        self.cancel_placement()
        super().resizeEvent(event)


class Preview(QWidget):
    scale_changed = pyqtSignal(float, str)
    annotation_added = pyqtSignal(str, object)
    comment_requested = pyqtSignal(str, object)
    status_message = pyqtSignal(str)
    selection_pending_changed = pyqtSignal(bool)
    annotation_updated = pyqtSignal(str, object)
    annotation_selected = pyqtSignal(object)
    annotation_edit_requested = pyqtSignal(str, object)
    object_requested = pyqtSignal(str, str, object)
    annotation_action = pyqtSignal(str)

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
        self.search_hit = None
        self.user_unit = 1.0
        self.selected_annotation_uid = None
        self._draft_annotation = None
        self._draft_error = ""
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

    def reveal_search_hit(self):
        if not self.search_hit or not self.page or self.search_hit.page_uid != self.page.uid: return
        rect = self.search_hit.rectangles[0]
        center = self.basis_to_canvas(((rect[0]+rect[2])/2, (rect[1]+rect[3])/2))
        self.scroll.ensureVisible(round(center.x()), round(center.y()), 60, 60)
        self.canvas.update()

    def basis_to_canvas(self, point):
        return map_point(point, self.paper_rect(), self.page.rotation if self.page else 0)

    @property
    def selected_annotation(self):
        return next((item for item in self.page.annotations if item.uid == self.selected_annotation_uid),None) if self.page else None

    @property
    def object_edit_pending(self):
        return self.canvas._object_drag is not None

    def set_selected_annotation(self, uid):
        uid = uid if self.page and any(item.uid == uid for item in self.page.annotations) else None
        if uid != self.selected_annotation_uid:
            self.canvas.cancel_object_drag()
            self.selected_annotation_uid = uid
            self.annotation_selected.emit(uid)
        self.canvas.update()

    def clear_object_selection(self):
        self.set_selected_annotation(None)

    def hit_test_annotation(self, point):
        if self.page is None or not self.paper_rect().contains(point):
            return None
        for item in reversed(self.page.annotations):
            rects = item.rects if item.kind == "highlight" else (item.rect,)
            if any(map_rect(rect,self.paper_rect(),self.page.rotation).adjusted(-3,-3,3,3).contains(point) for rect in rects):
                return item
        return None

    def object_handles(self, annotation=None):
        annotation = annotation or self.selected_annotation
        if annotation is None:
            return {}
        rect = map_rect(annotation.rect,self.paper_rect(),self.page.rotation)
        corners = {"nw":rect.topLeft(),"ne":rect.topRight(),"sw":rect.bottomLeft(),"se":rect.bottomRight()}
        return {name:QRectF(point.x()-4,point.y()-4,8,8) for name,point in corners.items()}

    def begin_object_drag(self, point):
        selected = self.selected_annotation
        handle = next((name for name,rect in self.object_handles().items() if rect.contains(point)),None)
        annotation = selected if handle else self.hit_test_annotation(point)
        self.set_selected_annotation(annotation.uid if annotation else None)
        if annotation is None:
            return
        self._draft_annotation = None
        self._draft_error = ""
        self.canvas._object_drag = dict(original=annotation,start=self.canvas_to_basis(point),handle=handle,
                                       revision=self._revision,page_uid=self.page.uid)
        if handle:
            rect = map_rect(annotation.rect,self.paper_rect(),self.page.rotation)
            opposite = {"nw":rect.bottomRight(),"ne":rect.bottomLeft(),"sw":rect.topRight(),"se":rect.topLeft()}[handle]
            anchor = self.canvas_to_basis(opposite)
            center = ((annotation.rect[0]+annotation.rect[2])/2,(annotation.rect[1]+annotation.rect[3])/2)
            self.canvas._object_drag.update(anchor=anchor,sign=(1 if center[0]>anchor[0] else -1,1 if center[1]>anchor[1] else -1))
        self.selection_pending_changed.emit(True)
        self.canvas.update()

    def _with_object_rect(self, annotation, rect):
        rect = tuple(old if abs(new-old) < 1e-12 else new for old,new in zip(annotation.rect,rect))
        if rect == annotation.rect:
            return annotation
        left,top,right,bottom = annotation.rect
        def transformed(value):
            return (rect[0]+(value[0]-left)/(right-left)*(rect[2]-rect[0]),
                    rect[1]+(value[1]-top)/(bottom-top)*(rect[3]-rect[1]),
                    rect[0]+(value[2]-left)/(right-left)*(rect[2]-rect[0]),
                    rect[1]+(value[3]-top)/(bottom-top)*(rect[3]-rect[1]))
        return replace(annotation,rect=rect,rects=tuple(transformed(value) for value in annotation.rects))

    def _moved_object(self, annotation, dx, dy):
        left,top,right,bottom = annotation.rect
        x = max(0,min(1-(right-left),left+dx))
        y = max(0,min(1-(bottom-top),top+dy))
        dx,dy = x-left,y-top
        if abs(dx) < 1e-12 and abs(dy) < 1e-12:
            return annotation
        return self._with_object_rect(annotation,(x,y,min(1,right+dx),min(1,bottom+dy)))

    def _validate_object(self, annotation):
        if annotation.kind == "text":
            validate_text_object(annotation,self.points,self.page.rotation,self.user_unit)

    def update_object_drag(self, point, modifiers=Qt.KeyboardModifier.NoModifier):
        drag = self.canvas._object_drag
        if drag is None:
            return
        original = drag["original"]
        x,y = self.canvas_to_basis(point)
        try:
            if drag["handle"] is None:
                candidate = self._moved_object(original,x-drag["start"][0],y-drag["start"][1])
            else:
                ax,ay = drag["anchor"]; sx,sy = drag["sign"]
                old_width,old_height = original.rect[2]-original.rect[0],original.rect[3]-original.rect[1]
                width = max(min(old_width,.005),sx*(x-ax))
                height = max(min(old_height,.005),sy*(y-ay))
                max_width,max_height = (1-ax if sx>0 else ax),(1-ay if sy>0 else ay)
                if original.kind in ("image","stamp") and not modifiers & Qt.KeyboardModifier.ShiftModifier:
                    ratio = old_width/old_height
                    width = max(width,height*ratio)
                    height = width/ratio
                    factor = min(1,max_width/width,max_height/height)
                    width,height = width*factor,height*factor
                else:
                    width,height = min(width,max_width),min(height,max_height)
                bx,by = max(0,min(1,ax+sx*width)),max(0,min(1,ay+sy*height))
                candidate = self._with_object_rect(original,(min(ax,bx),min(ay,by),max(ax,bx),max(ay,by)))
            self._draft_annotation = candidate
            self._validate_object(candidate)
            self._draft_error = ""
        except AnnotationError as exc:
            self._draft_error = str(exc)
        self.canvas.update()

    def finish_object_drag(self):
        drag = self.canvas._object_drag
        candidate,error = self._draft_annotation,self._draft_error
        self.canvas.cancel_object_drag()
        if drag and error:
            self.status_message.emit(error)
        elif (drag and candidate is not None and self.page is not None and drag["page_uid"] == self.page.uid
              and drag["revision"] == self._revision and candidate != drag["original"]):
            self.annotation_updated.emit(self.page.uid,candidate)

    def nudge_object(self, dx, dy):
        selected = self.selected_annotation
        if selected is None:
            return
        origin = display_to_basis((0,0),self.page.rotation)
        target = display_to_basis((dx/(self.points[0]*self.user_unit),dy/(self.points[1]*self.user_unit)),self.page.rotation)
        try:
            candidate = self._moved_object(selected,target[0]-origin[0],target[1]-origin[1])
            self._validate_object(candidate)
            if candidate != selected:
                self.annotation_updated.emit(self.page.uid,candidate)
        except AnnotationError as exc:
            self.status_message.emit(str(exc))

    def set_tool(self, tool, strokes=()):
        if tool not in ("pan", "select", "comment", "highlight", "signature", "text", "image", "stamp", "checkmark", "cross"): raise ValueError("Unbekanntes Vorschauwerkzeug")
        self.canvas.cancel_placement()
        self.tool = tool; self.signature_strokes = strokes
        if tool not in ("pan", "select"):
            self.clear_object_selection()
        self._clear_selection()
        self.canvas._placement_start = self.canvas._placement_end = None
        self.update_cursor(); self.canvas.update()

    def update_cursor(self):
        self.canvas.setCursor(Qt.CursorShape.OpenHandCursor if self.tool == "pan" else (Qt.CursorShape.ArrowCursor if self.tool == "select" else Qt.CursorShape.CrossCursor))

    def place_annotation(self, start, end):
        if self.page is None or not self.geometry_valid: return
        if self.tool in ("text", "image", "stamp", "checkmark", "cross"):
            if (start-end).manhattanLength() < 8:
                paper = self.paper_rect()
                normalized_size = {"text": (.55,.18),"image":(.32,.25),"stamp":(.32,.25),"checkmark":(.10,.10),"cross":(.10,.10)}[self.tool]
                width,height = paper.width()*normalized_size[0],paper.height()*normalized_size[1]
                left,top = min(start.x(),paper.right()-width),min(start.y(),paper.bottom()-height)
                start,end = QPointF(left,top),QPointF(left+width,top+height)
            a,b = self.canvas_to_basis(start),self.canvas_to_basis(end)
            rect = min(a[0],b[0]),min(a[1],b[1]),max(a[0],b[0]),max(a[1],b[1])
            if rect[2]-rect[0] < .005 or rect[3]-rect[1] < .005:
                self.status_message.emit("Bitte einen größeren Objektbereich ziehen oder einmal klicken."); return
            self.object_requested.emit(self.page.uid,self.tool,rect)
        elif self.tool == "comment":
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
        return self._selection_token is not None or self.object_edit_pending

    def _clear_selection(self):
        if self._selection_token is not None:
            self._selection_token = None
            self.selection_pending_changed.emit(self.selection_pending)

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
        self.canvas.cancel_placement()
        if changed:
            self.clear_object_selection()
        self.source, self.page = source, page
        if self.selected_annotation_uid is not None and self.selected_annotation is None:
            self.clear_object_selection()
        self._revision += 1
        self._clear_selection()
        self.canvas._placement_start = self.canvas._placement_end = None
        if changed:
            self.image = QImage(); self._key = None
            self.geometry_valid = source is not None and page is not None
            self.message = "Seite wird geladen …" if page else "Eine Seite auswählen, um sie hier zu prüfen."
            if source is not None and page is not None:
                try:
                    self.points, self.user_unit = source_page_geometry(source,page)
                except (AnnotationError, ValueError):
                    self.points, self.user_unit = source.page_sizes[page.page_index],1.0
                    if page.rotation % 180:
                        self.points = self.points[::-1]
                if any(not math.isfinite(n) or n <= 0 for n in self.points):
                    self.geometry_valid = False
                    self.points = (300.0, 500.0)
                    self.message = "Vorschau nicht verfügbar: ungültiges Seitenformat."
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
        self.reveal_search_hit()

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
