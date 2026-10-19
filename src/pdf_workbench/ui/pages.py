"""Lazy thumbnail view with explicit current-page and selection decoration."""
import json

from PyQt6.QtCore import QMimeData, QPoint, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QAbstractItemView, QListView, QListWidget, QStyle, QStyledItemDelegate
from .annotation_overlay import draw_annotations

UID_ROLE = Qt.ItemDataRole.UserRole
PAGE_ROLE = int(Qt.ItemDataRole.UserRole) + 1
SOURCE_ROLE = int(Qt.ItemDataRole.UserRole) + 2
POSITION_ROLE = int(Qt.ItemDataRole.UserRole) + 3
MIME = "application/x-pdf-workbench-pages"


class PageDelegate(QStyledItemDelegate):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index):
        return QSize(128, 196) if self.view.grid_mode else QSize(280, 104)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(3, 3, -3, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        active = self.view.currentIndex() == index
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        fill = "#e9f0ff" if selected else ("#f7f9fc" if hover else "#ffffff")
        painter.setBrush(QColor(fill))
        painter.setPen(QPen(QColor("#2f5fc5" if active else ("#a9bfeb" if selected else "#d6dde6")), 2 if active else 1))
        painter.drawRoundedRect(QRectF(rect), 6, 6)
        page = index.data(PAGE_ROLE)
        source = index.data(SOURCE_ROLE)
        position = index.data(POSITION_ROLE)
        if page is None or source is None:
            painter.restore(); return
        if self.view.grid_mode:
            image_box = rect.adjusted(12, 13, -12, -57)
            title_box = rect.adjusted(10, rect.height() - 45, -10, -25)
            detail_box = rect.adjusted(10, rect.height() - 25, -10, -7)
        else:
            image_box = rect.adjusted(11, 10, -(rect.width() - 66), -10)
            title_box = rect.adjusted(86, 16, -12, -(rect.height() - 39))
            detail_box = rect.adjusted(86, 43, -12, -15)
        bounds = (236, 270) if self.view.grid_mode else (120, 156)
        key = self.view.broker.request(source, page, bounds)
        cached = self.view.broker.get(key)
        if cached:
            image, _ = cached
            scaled = image.size().scaled(image_box.size(), Qt.AspectRatioMode.KeepAspectRatio)
            paper = QRectF(0, 0, scaled.width(), scaled.height())
            paper.moveCenter(QRectF(image_box).center())
            painter.fillRect(paper.translated(2, 2), QColor("#d6dde6"))
            painter.fillRect(paper, QColor("#ffffff"))
            painter.drawImage(paper, image)
            draw_annotations(painter, paper, page)
        else:
            paper = QRectF(0, 0, min(image_box.width(), image_box.height() * 0.68), image_box.height())
            paper.moveCenter(QRectF(image_box).center())
            painter.setPen(QPen(QColor("#d4dce7"), 1)); painter.setBrush(QColor("#f5f7fa"))
            painter.drawRect(paper)
            painter.setPen(QColor("#64748b"))
            painter.drawText(paper, Qt.AlignmentFlag.AlignCenter, str(position))
        font = QFont(option.font)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#17243b"))
        title = f"Seite {position}" if self.view.grid_mode else f"{position:02d}  {source.label}"
        painter.drawText(title_box, Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, title_box.width()))
        font.setBold(False); font.setPointSizeF(max(8.5, option.font.pointSizeF() - 0.5)); painter.setFont(font)
        detail = source.label if self.view.grid_mode else f"Originalseite {page.page_index + 1}  ·  +{page.rotation}°"
        painter.setPen(QColor("#526278"))
        painter.drawText(detail_box, Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(detail, Qt.TextElideMode.ElideRight, detail_box.width()))
        if selected:
            badge = QRectF(rect.right() - 24, rect.top() + 8, 16, 16)
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor("#2f5fc5")); painter.drawEllipse(badge)
            painter.setPen(QPen(QColor("white"), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            path = QPainterPath(QPointF(badge.x() + 4, badge.y() + 8)); path.lineTo(badge.x() + 7, badge.y() + 11); path.lineTo(badge.x() + 12, badge.y() + 5)
            painter.drawPath(path)
        if active:
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor("#2f5fc5"))
            painter.drawRoundedRect(QRectF(rect.left(), rect.top() + 15, 3, rect.height() - 30), 1.5, 1.5)
        painter.restore()


class PageList(QListWidget):
    files_dropped = pyqtSignal(list)
    order_changed = pyqtSignal(list)

    def __init__(self, broker):
        super().__init__()
        self.broker = broker
        self.grid_mode = True
        self._drop_row = None
        self._drop_after = False
        self._drag_position = None
        self._last_visible_active_uid = None
        self._drag_scroll = QTimer(self)
        self._drag_scroll.setInterval(50)
        self._drag_scroll.timeout.connect(self._scroll_during_drag)
        self.setItemDelegate(PageDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True); self.setAcceptDrops(True); self.setDropIndicatorShown(True)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setAccessibleName("PDF-Seiten: Miniaturen in der Exportreihenfolge")
        self.setAccessibleDescription("Blauer Rahmen: aktuelle Vorschau. Haken: für Werkzeuge ausgewählt. Mehrfachauswahl mit Strg/Cmd oder Umschalt, Verschieben mit Alt und Pfeiltasten.")
        self.set_grid_mode(True)
        broker.ready.connect(lambda *_: self.viewport().update())

    def set_grid_mode(self, grid):
        self.grid_mode = bool(grid)
        self.setViewMode(QListView.ViewMode.IconMode if grid else QListView.ViewMode.ListMode)
        self.setFlow(QListView.Flow.LeftToRight if grid else QListView.Flow.TopToBottom)
        self.setWrapping(grid)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setSpacing(5 if grid else 2)
        self.setGridSize(QSize(128, 196) if grid else QSize())
        self.doItemsLayout()
        self.viewport().update()

    def startDrag(self, supported_actions):
        ids = [self.item(i).data(UID_ROLE) for i in range(self.count()) if self.item(i).isSelected()]
        if not ids:
            return
        drag = QDrag(self)
        mime = QMimeData(); mime.setData(MIME, json.dumps(ids).encode())
        drag.setMimeData(mime)
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self._clear_drag_feedback()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or (event.source() == self and event.mimeData().hasFormat(MIME)):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)
        if event.source() == self and event.mimeData().hasFormat(MIME):
            self._drag_position = event.position().toPoint()
            self._update_drop_feedback()
            if self._drag_position.y() < 32 or self._drag_position.y() > self.viewport().height() - 32:
                self._drag_scroll.start()
            else:
                self._drag_scroll.stop()

    def _update_drop_feedback(self):
        if self._drag_position is None:
            return
        index = self.indexAt(self._drag_position)
        if not index.isValid():
            for offset in (-7, 7):
                index = self.indexAt(self._drag_position + QPoint(offset, 0))
                if index.isValid():
                    break
        self._drop_row, self._drop_after = self.count(), False
        if index.isValid():
            self._drop_row = index.row()
            rect = self.visualRect(index)
            self._drop_after = self._drag_position.x() > rect.center().x() if self.grid_mode else self._drag_position.y() > rect.center().y()
        self.viewport().update()

    def _scroll_during_drag(self):
        if self._drag_position is None:
            self._drag_scroll.stop(); return
        delta = -18 if self._drag_position.y() < 32 else 18
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + delta)
        self._update_drop_feedback()

    def _clear_drag_feedback(self):
        self._drag_scroll.stop()
        self._drag_position = None; self._drop_row = None
        self.viewport().update()

    def dragLeaveEvent(self, event):
        self._clear_drag_feedback()
        event.accept()

    def paintEvent(self, event):
        super().paintEvent(event)
        current = self.currentItem()
        self._last_visible_active_uid = current.data(UID_ROLE) if current and self.viewport().rect().intersects(self.visualItemRect(current)) else None
        if self._drop_row is None or not self.count():
            return
        row = min(self._drop_row, self.count() - 1)
        rect = self.visualItemRect(self.item(row))
        after = self._drop_after or self._drop_row == self.count()
        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor("#285cc4"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.grid_mode:
            x = rect.right() + 2 if after else rect.left() - 2
            # Keep the entire three-pixel stroke inside the viewport, including
            # before its first column and after a column touching the edge.
            x = max(2, min(self.viewport().width() - 3, x))
            painter.drawLine(x, rect.top() + 6, x, rect.bottom() - 6)
        else:
            y = rect.bottom() + 2 if after else rect.top() - 2
            y = max(2, min(self.viewport().height() - 3, y))
            painter.drawLine(10, y, self.viewport().width() - 10, y)

    def resizeEvent(self, event):
        current = self.currentItem()
        # IconMode can already have recomputed item coordinates when this event
        # arrives. Anchor against the last painted layout, before that reflow.
        visible = bool(current and current.data(UID_ROLE) == self._last_visible_active_uid)
        super().resizeEvent(event)
        if visible:
            QTimer.singleShot(0, self._keep_current_visible)

    def _keep_current_visible(self):
        if self.currentItem():
            self.doItemsLayout()
            self.scrollToItem(self.currentItem(), QAbstractItemView.ScrollHint.EnsureVisible)

    def reordered_ids(self, selected_ids, target_row, after=False):
        """A deterministic order operation, shared by drag/drop and regression tests."""
        order = [self.item(i).data(UID_ROLE) for i in range(self.count())]
        selected = set(selected_ids)
        moving = [uid for uid in order if uid in selected]
        boundary = max(0, min(len(order), target_row + int(after)))
        insertion = sum(uid not in selected for uid in order[:boundary])
        remaining = [uid for uid in order if uid not in selected]
        return remaining[:insertion] + moving + remaining[insertion:]

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            self.files_dropped.emit([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
            event.acceptProposedAction()
        elif event.source() == self and event.mimeData().hasFormat(MIME):
            selected = json.loads(bytes(event.mimeData().data(MIME)))
            self._drag_position = event.position().toPoint()
            self._update_drop_feedback()
            row, after = self._drop_row, self._drop_after
            self._clear_drag_feedback()
            self.order_changed.emit(self.reordered_ids(selected, row, after))
            event.setDropAction(Qt.DropAction.MoveAction); event.accept()
        else:
            event.ignore()
