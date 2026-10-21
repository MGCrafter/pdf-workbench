"""A read-only, synchronized PDF comparison workspace."""
from __future__ import annotations

import threading

from PyQt6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QKeySequence, QPainter, QPen, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..comparison import compare_documents
from ..core import OperationCancelled, PageRef
from .icons import icon
from .motion import MotionController
from .rendering import RenderBroker
from .theme import COLORS, color, semantic_label, theme_controller


STATUS = {"changed": "Geändert", "added": "Hinzugefügt", "removed": "Entfernt", "unchanged": "Unverändert"}


def _excerpt(text, length=48):
    return text if len(text) <= length else text[:length - 1].rstrip() + "…"


class ComparisonWorker(QThread):
    ready = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, str)

    def __init__(self, left, right, parent=None):
        super().__init__(parent)
        self.left, self.right = left, right
        self.cancel_event = threading.Event()

    def run(self):
        try:
            self.ready.emit(compare_documents(self.left, self.right, self.cancel_event.is_set, self.progress.emit))
        except OperationCancelled:
            pass
        except Exception as error:
            self.failed.emit(str(error))


class _ElidedLabel(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.full_text = text
        self.setToolTip(text)
        self.setAccessibleName(text)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def resizeEvent(self, event):
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideMiddle, self.width()))
        super().resizeEvent(event)


class _Canvas(QWidget):
    def __init__(self, pane):
        super().__init__()
        self.pane = pane
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAccessibleName(pane.side + ": PDF-Vorschau. Pfeiltasten scrollen, Strg und Mausrad zoomen.")
        self._drag = None

    def paper(self):
        width, height = self.pane.paper_size
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def paintEvent(self, event):
        from PyQt6.QtGui import QColor
        p = QPainter(self)
        p.fillRect(self.rect(), color("canvas"))
        pane = self.pane
        if pane.image.isNull():
            p.setPen(color("muted"))
            p.drawText(self.rect().adjusted(16,16,-16,-16), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, pane.message)
        else:
            paper = self.paper()
            p.fillRect(paper.translated(3,4), color("shadow"))
            p.fillRect(paper, Qt.GlobalColor.white)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(paper, pane.image)
            if pane.mark_regions:
                # Paper remains white in both app themes; use semantic ink
                # intended for a light background for readable region outlines.
                ink = QColor(COLORS["light"]["error" if pane.side == "Vorher" else "success"])
                wash = QColor(ink); wash.setAlpha(24)
                for index, region in enumerate(pane.regions):
                    box = QRectF(paper.left() + region[0]*paper.width(), paper.top() + region[1]*paper.height(),
                                 (region[2]-region[0])*paper.width(), (region[3]-region[1])*paper.height())
                    p.setBrush(wash)
                    p.setPen(QPen(ink, 2 if index == pane.active_region else 1.3, Qt.PenStyle.DashLine))
                    p.drawRect(box)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(color("focus"),2))
            p.drawRect(self.rect().adjusted(1,1,-2,-2))

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta: self.pane.zoom_requested.emit(1.2 if delta > 0 else 1/1.2)
            event.accept()
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event):
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._drag = (event.globalPosition(), self.pane.scroll.horizontalScrollBar().value(), self.pane.scroll.verticalScrollBar().value())
            self.setCursor(Qt.CursorShape.ClosedHandCursor); event.accept()
        else: super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag:
            start, x, y = self._drag; delta = event.globalPosition() - start
            self.pane.scroll.horizontalScrollBar().setValue(round(x-delta.x()))
            self.pane.scroll.verticalScrollBar().setValue(round(y-delta.y())); event.accept()
        else: super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag = None; self.setCursor(Qt.CursorShape.OpenHandCursor); super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        bars = self.pane.scroll.horizontalScrollBar(), self.pane.scroll.verticalScrollBar()
        actions = {Qt.Key.Key_Left:(0,-40), Qt.Key.Key_Right:(0,40), Qt.Key.Key_Up:(1,-40), Qt.Key.Key_Down:(1,40),
                   Qt.Key.Key_PageUp:(1,-bars[1].pageStep()), Qt.Key.Key_PageDown:(1,bars[1].pageStep())}
        if event.key() in actions:
            axis, delta = actions[event.key()]; bars[axis].setValue(bars[axis].value()+delta); event.accept()
        else: super().keyPressEvent(event)


class ComparisonPane(QFrame):
    zoom_requested = pyqtSignal(float)

    def __init__(self, side, source, broker, parent=None):
        super().__init__(parent)
        self.side, self.source, self.broker = side, source, broker
        self.display_sizes = source.page_sizes
        self.page_index = None
        self.image = QImage()
        self.message = "Vergleich wird vorbereitet …"
        self.points = (300.,500.)
        self.paper_size = (300,500)
        self.zoom = 1.0
        self.regions, self.active_region = (), -1
        self.mark_regions = True
        self.key = None
        self.setMinimumWidth(160)
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(8)
        self.caption = QLabel(side); self.caption.setObjectName("sectionTitle"); layout.addWidget(self.caption)
        self.filename = _ElidedLabel(source.label); self.filename.setObjectName("muted"); layout.addWidget(self.filename)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setAccessibleName(side + " · synchronisierte PDF-Vorschau")
        self.canvas = _Canvas(self); self.scroll.setWidget(self.canvas); layout.addWidget(self.scroll,1)
        broker.ready.connect(self._rendered)

    def set_page(self, index, regions=()):
        if index != self.page_index:
            self.image = QImage(); self.key = None
        self.page_index, self.regions, self.active_region = index, regions, -1
        self.caption.setText(f"{self.side} · Seite {index+1}" if index is not None else self.side + " · keine Seite")
        self.message = "Vorschau wird geladen …" if index is not None else "Keine entsprechende Seite\nin dieser Version."
        if index is not None:
            self.points = self.display_sizes[index]
        self.canvas.update()

    def set_zoom(self, zoom):
        self.zoom = zoom
        if self.page_index is None:
            self.canvas.setMinimumSize(0,0)
        else:
            self.paper_size = tuple(max(1,round(n*zoom)) for n in self.points)
            self.canvas.setMinimumSize(self.paper_size[0]+24,self.paper_size[1]+24)
        self.canvas.update()

    def request(self):
        if self.page_index is None: return
        bounds = tuple(max(32, min(2400, round(n*self.zoom*self.devicePixelRatioF()))) for n in self.points)
        self.key = self.broker.request(self.source, PageRef(f"comparison-{self.side}-{self.page_index}", self.source.source_id, self.page_index),
                                       bounds, "comparison-"+self.side)
        cached = self.broker.get(self.key)
        if cached: self._rendered(self.key, cached[0], cached[1], None)
        elif self.key in self.broker.failures: self._rendered(self.key,QImage(),None,self.broker.failures[self.key])

    def _rendered(self, key, image, points, error):
        if key != self.key: return
        self.image = image
        if error: self.message = "Vorschau nicht verfügbar: " + error
        self.canvas.update()

    def reveal(self, index):
        self.active_region = index
        if 0 <= index < len(self.regions):
            rect = self.regions[index]; paper = self.canvas.paper()
            self.scroll.ensureVisible(round(paper.left()+(rect[0]+rect[2])/2*paper.width()),
                                      round(paper.top()+(rect[1]+rect[3])/2*paper.height()),36,36)
        self.canvas.update()


class ComparisonDialog(QDialog):
    comparison_ready = pyqtSignal(object)

    def __init__(self, left, right, parent=None, *, motion=None):
        super().__init__(parent)
        self.left, self.right = left, right
        self.comparison = None
        self.worker = None
        self._closing = False
        self.is_closed = False
        self._syncing = False
        self._page_items = []
        self.current_pair = None
        self.zoom_factor = 1.0
        self._fit = True
        self.motion = motion or getattr(parent,"motion",None) or MotionController(self,theme_controller().settings)
        self.setWindowTitle("PDF-Versionen vergleichen"); self.resize(1160,780); self.setMinimumSize(740,500)
        layout = QVBoxLayout(self); layout.setContentsMargins(20,20,20,16); layout.setSpacing(12)
        title = QLabel("Versionen vergleichen."); title.setObjectName("emptyTitle"); layout.addWidget(title)
        self.summary = QLabel("Seiten werden gelesen und automatisch zugeordnet …")
        self.summary.setObjectName("muted"); self.summary.setWordWrap(True); layout.addWidget(self.summary)
        self.progress = QProgressBar(); self.progress.setRange(0,100); self.progress.setFixedHeight(4); self.progress.setTextVisible(False); layout.addWidget(self.progress)
        tools = QHBoxLayout(); tools.setSpacing(8)
        self.previous = QPushButton("Zurück"); self.next = QPushButton("Weiter")
        self.previous.setToolTip("Vorherige Seite mit Änderung · Umschalt+F7"); self.next.setToolTip("Nächste Seite mit Änderung · F7")
        self.previous.clicked.connect(lambda:self.navigate(-1)); self.next.clicked.connect(lambda:self.navigate(1))
        tools.addWidget(self.previous); tools.addWidget(self.next)
        self.only_changes = QCheckBox("Nur Änderungen"); self.only_changes.setChecked(True); self.only_changes.toggled.connect(self._filter); tools.addWidget(self.only_changes)
        tools.addStretch()
        self.zoom_out = QPushButton(); self.zoom_out.setIcon(icon("zoom_out")); self.zoom_out.setAccessibleName("Beide Seiten verkleinern"); self.zoom_out.setToolTip("Verkleinern · Strg+−")
        self.zoom_in = QPushButton(); self.zoom_in.setIcon(icon("zoom_in")); self.zoom_in.setAccessibleName("Beide Seiten vergrößern"); self.zoom_in.setToolTip("Vergrößern · Strg++")
        self.zoom_out.clicked.connect(lambda:self.change_zoom(1/1.2)); self.zoom_in.clicked.connect(lambda:self.change_zoom(1.2))
        self.zoom_box = QComboBox(); self.zoom_box.setAccessibleName("Gemeinsamer Zoom beider PDF-Versionen")
        self.zoom_box.addItem("Einpassen",None)
        for value in (.5,1.,1.5,2.,4.): self.zoom_box.addItem(f"{value*100:.0f} %",value)
        self.zoom_box.currentIndexChanged.connect(self._zoom_selected)
        tools.addWidget(self.zoom_out); tools.addWidget(self.zoom_box); tools.addWidget(self.zoom_in); layout.addLayout(tools)
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal); self.main_splitter.setHandleWidth(14)
        sidebar = QWidget(); side = QVBoxLayout(sidebar); side.setContentsMargins(0,0,0,0); side.setSpacing(8)
        label = QLabel("Änderungen"); label.setObjectName("sectionTitle"); side.addWidget(label)
        self.changes = QTreeWidget(); self.changes.setHeaderHidden(True); self.changes.setIndentation(14)
        self.changes.setAccessibleName("Änderungsliste. Seite oder Änderungsbereich auswählen.")
        # Some platform styles animate expansion and ignore clicks during it.
        # Keep navigation immediate; only the result summary uses shared motion.
        self.changes.setAnimated(False)
        self.changes.setMinimumWidth(180); self.changes.setUniformRowHeights(True); self.changes.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.changes.currentItemChanged.connect(self._selection_changed)
        self.changes.itemExpanded.connect(self._populate_children); side.addWidget(self.changes,1)
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True); self.detail.setMaximumHeight(116); self.detail.setMinimumHeight(64)
        self.detail.setAccessibleName("Beschreibung der ausgewählten Änderung"); self.detail.setPlainText("Wähle eine Änderung, um ihre Details zu sehen.")
        side.addWidget(self.detail); self.main_splitter.addWidget(sidebar)
        self.broker = RenderBroker(self)
        self.panes = (ComparisonPane("Vorher",left,self.broker),ComparisonPane("Nachher",right,self.broker))
        documents = QSplitter(Qt.Orientation.Horizontal)
        for pane in self.panes:
            documents.addWidget(pane); pane.zoom_requested.connect(self.change_zoom)
        documents.setChildrenCollapsible(False); documents.setHandleWidth(12)
        self.main_splitter.addWidget(documents); self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setStretchFactor(0,0); self.main_splitter.setStretchFactor(1,1); self.main_splitter.setSizes([230,870])
        layout.addWidget(self.main_splitter,1)
        footer = QHBoxLayout(); footer.setSpacing(12)
        self.mark_regions = QCheckBox("Bereiche markieren"); self.mark_regions.setChecked(True)
        self.mark_regions.toggled.connect(self._mark_regions); footer.addWidget(self.mark_regions)
        note = QLabel("Seiten + Text · Bildvergleich bis 1.000 px · keine OCR.\nAutomatische Zuordnung bitte prüfen.")
        note.setObjectName("muted"); note.setWordWrap(True)
        note.setToolTip("Es werden sichtbare Seiten und extrahierter Text verglichen. Keine Prüfung von Metadaten, Anhängen, versteckten Inhalten oder digitalen Signaturen. Kleinste Bilddetails können bei dieser Auflösung entgehen. Wiederholte oder stark veränderte Seiten können uneindeutig zugeordnet werden.")
        footer.addWidget(note,1)
        self.close_button = QPushButton("Schließen"); self.close_button.clicked.connect(self.reject); footer.addWidget(self.close_button); layout.addLayout(footer)
        self._render_timer = QTimer(self); self._render_timer.setSingleShot(True); self._render_timer.setInterval(70); self._render_timer.timeout.connect(self._render_pair)
        for origin, target in ((self.panes[0],self.panes[1]),(self.panes[1],self.panes[0])):
            for orientation in ("horizontalScrollBar","verticalScrollBar"):
                first,second = getattr(origin.scroll,orientation)(),getattr(target.scroll,orientation)()
                first.valueChanged.connect(lambda value,a=first,b=second:self._sync_scroll(value,a,b))
        self.broker.closed.connect(self._maybe_closed)
        for keys,callback in (("F7",lambda:self.navigate(1)),("Shift+F7",lambda:self.navigate(-1)),
                              ("Ctrl++",lambda:self.change_zoom(1.2)),("Ctrl+-",lambda:self.change_zoom(1/1.2)),("Ctrl+0",self.fit_pages)):
            shortcut=QShortcut(QKeySequence(keys),self); shortcut.activated.connect(callback)
        self._set_ready(False)
        QTimer.singleShot(0,self._start)

    def _start(self):
        if self._closing: return
        self.worker = ComparisonWorker(self.left,self.right,self)
        self.worker.ready.connect(self._ready); self.worker.failed.connect(self._failed)
        self.worker.progress.connect(self._progress); self.worker.finished.connect(self._worker_finished); self.worker.start()

    def _progress(self,value,message):
        if not self._closing: self.progress.setValue(value); self.summary.setText(message)

    def _set_ready(self,ready):
        for control in (self.previous,self.next,self.only_changes,self.zoom_box,self.zoom_in,self.zoom_out,self.mark_regions,self.changes):
            control.setEnabled(ready)

    def _ready(self,result):
        if self._closing: return
        self.comparison = result; self.progress.hide(); self._set_ready(True)
        for pane,source,sizes in zip(self.panes,(result.left_source,result.right_source),(result.left_page_sizes,result.right_page_sizes)):
            if source is not None:pane.source=source
            if sizes:pane.display_sizes=sizes
        self.summary.setText(f"{result.count('changed')} geändert · {result.count('added')} hinzugefügt · {result.count('removed')} entfernt · {result.count('unchanged')} unverändert")
        semantic_label(self.summary,"normal")
        for pair_index,page in enumerate(result.pages):
            a,b = (str(page.left_index+1) if page.left_index is not None else "–"),(str(page.right_index+1) if page.right_index is not None else "–")
            label = f"{STATUS[page.status]} · {a} ↔ {b}"
            if page.moved_from is not None or page.moved_to is not None: label += " · verschoben"
            item = QTreeWidgetItem([label]); item.setData(0,Qt.ItemDataRole.UserRole,(pair_index,"page",-1)); item.setToolTip(0,label)
            self.changes.addTopLevelItem(item); self._page_items.append(item)
            if page.text_changes or page.visual_regions:
                item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        if not any(page.status != "unchanged" for page in result.pages): self.only_changes.setChecked(False)
        self._filter(); self.changes.setFocus(Qt.FocusReason.OtherFocusReason)
        self.motion.feedback(self.summary); self.comparison_ready.emit(result)

    def _populate_children(self,item):
        if self.comparison is None or item.parent() is not None or item.childCount():return
        pair_index,kind,_=item.data(0,Qt.ItemDataRole.UserRole)
        if kind != "page":return
        page=self.comparison.pages[pair_index]
        # Populate at most one page's bounded changes during navigation. A
        # 300-page comparison must not construct tens of thousands of rows at once.
        for index,change in enumerate(page.text_changes):
            text=f"Text {index+1} · {_excerpt(change.before or '∅',24)} → {_excerpt(change.after or '∅',24)}"
            child=QTreeWidgetItem([text]);child.setData(0,Qt.ItemDataRole.UserRole,(pair_index,"text",index));child.setToolTip(0,text);item.addChild(child)
        for index,_ in enumerate(page.visual_regions):
            child=QTreeWidgetItem([f"Bild/Layout · Bereich {index+1}"])
            child.setData(0,Qt.ItemDataRole.UserRole,(pair_index,"visual",index));item.addChild(child)

    def _failed(self,error):
        if self._closing: return
        self.progress.hide(); self.summary.setText(error); semantic_label(self.summary,"error")
        self.detail.setPlainText("Vergleich nicht möglich. Die beiden Originaldateien wurden nicht verändert.")

    def _worker_finished(self):
        worker,self.worker = self.worker,None
        worker.deleteLater(); self._maybe_closed()

    def _filter(self):
        if self.comparison is None: return
        for item,page in zip(self._page_items,self.comparison.pages): item.setHidden(self.only_changes.isChecked() and page.status == "unchanged")
        current = self.changes.currentItem()
        while current and current.parent(): current=current.parent()
        if current is None or current.isHidden():
            visible = next((item for item in self._page_items if not item.isHidden()),None)
            self.changes.setCurrentItem(visible)
        self._navigation_state()

    def _navigation_state(self):
        visible=[item for item in self._page_items if not item.isHidden()]
        current=self.changes.currentItem()
        while current and current.parent():current=current.parent()
        position=visible.index(current) if current in visible else -1
        self.previous.setEnabled(position>0); self.next.setEnabled(0<=position<len(visible)-1)

    def navigate(self,direction):
        if self.comparison is None: return
        visible=[item for item in self._page_items if not item.isHidden()]
        current=self.changes.currentItem()
        while current and current.parent():current=current.parent()
        if current not in visible:return
        index=max(0,min(len(visible)-1,visible.index(current)+direction))
        self.changes.setCurrentItem(visible[index]); self.changes.scrollToItem(visible[index])

    def _selection_changed(self,item,previous):
        if item is None or self.comparison is None:return
        pair_index,kind,index = item.data(0,Qt.ItemDataRole.UserRole)
        page=self.comparison.pages[pair_index]
        if kind == "page":self._populate_children(item)
        if pair_index != self.current_pair:
            self.current_pair=pair_index
            for pane,source_index in zip(self.panes,(page.left_index,page.right_index)):
                pane.set_page(source_index,page.visual_regions)
                pane.scroll.horizontalScrollBar().setValue(0); pane.scroll.verticalScrollBar().setValue(0)
            self._apply_zoom(); self._render_timer.start()
        if kind == "text":
            change=page.text_changes[index]
            self.detail.setPlainText(f"Text · {STATUS[change.kind]}\n\nVorher: {_excerpt(change.before or 'Kein Text',1500)}\n\nNachher: {_excerpt(change.after or 'Kein Text',1500)}")
        elif kind == "visual":
            for pane in self.panes:pane.reveal(index)
            self.detail.setPlainText(f"Bild/Layout · Bereich {index+1}\n\nDie gestrichelten Rahmen zeigen Unterschiede in der Seitendarstellung. Dazu gehören auch geänderte Textformen.")
        else:
            description = f"{STATUS[page.status]}\n"
            if page.moved_from is not None:description+=f"Unveränderter Seiteninhalt von vorheriger Seite {page.moved_from+1} hierher verschoben.\n"
            if page.moved_to is not None:description+=f"Unveränderter Seiteninhalt auf neue Seite {page.moved_to+1} verschoben.\n"
            if page.size_changed:description+="Seitenformat geändert.\n"
            description+=f"{len(page.text_changes)} Textänderung(en) · {len(page.visual_regions)} Bild-/Layoutbereich(e)."
            if page.text_changes_truncated:description+="\nTextvergleich gekürzt; weitere Details manuell prüfen."
            if page.status == "changed" and not page.text_changes and not page.visual_regions:
                description+="\nKleine Darstellungsdetails unterscheiden sich unterhalb der Markierungsschwelle."
            if page.status == "unchanged":description="Unverändert im extrahierten Text und im Bildvergleich bis 1.000 Pixel."
            self.detail.setPlainText(description)
        self._navigation_state()

    def _mark_regions(self,enabled):
        for pane in self.panes:pane.mark_regions=enabled; pane.canvas.update()

    def _sync_scroll(self,value,origin,target):
        if self._syncing:return
        self._syncing=True
        try:target.setValue(round(value/max(1,origin.maximum())*target.maximum()))
        finally:self._syncing=False

    def fit_pages(self):
        self.zoom_box.setCurrentIndex(0); self._fit=True; self._apply_zoom(); self._render_timer.start()

    def _zoom_selected(self):
        self._fit=self.zoom_box.currentData() is None
        if not self._fit:self.zoom_factor=float(self.zoom_box.currentData())
        self._apply_zoom(); self._render_timer.start()

    def _apply_zoom(self):
        active=[pane for pane in self.panes if pane.page_index is not None]
        if not active:return
        limit=min(4.,8000/max(max(pane.points) for pane in active))
        if self._fit:
            self.zoom_factor=min(min(max(1,pane.scroll.viewport().width()-24)/pane.points[0],
                                     max(1,pane.scroll.viewport().height()-24)/pane.points[1]) for pane in active)
        self.zoom_factor=max(.01,min(limit,self.zoom_factor))
        for pane in self.panes:pane.set_zoom(self.zoom_factor)

    def change_zoom(self,factor):
        if self.comparison is None:return
        self._fit=False; self.zoom_factor*=factor; self._apply_zoom()
        label=f"{self.zoom_factor*100:.0f} %"
        self.zoom_box.blockSignals(True)
        found=self.zoom_box.findText(label)
        if found<0:
            if self.zoom_box.count()>6:self.zoom_box.removeItem(6)
            self.zoom_box.addItem(label,self.zoom_factor); found=self.zoom_box.count()-1
        self.zoom_box.setCurrentIndex(found); self.zoom_box.blockSignals(False); self._render_timer.start()

    def _render_pair(self):
        if self._closing:return
        self.broker.invalidate()
        for pane in self.panes:pane.request()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,"_render_timer") and self._fit:
            QTimer.singleShot(0,self._resize_fit)

    def _resize_fit(self):
        if not self._closing:self._apply_zoom(); self._render_timer.start()

    def reject(self):
        if self._closing:return
        self._closing=True; self._render_timer.stop()
        if self.worker:self.worker.cancel_event.set()
        self._set_ready(False); self.close_button.setEnabled(False)
        self.summary.setText("Vergleich wird beendet …")
        self.broker.shutdown(); self._maybe_closed()

    def _maybe_closed(self):
        if self._closing and self.worker is None and self.broker.is_closed:
            self.is_closed=True; super().reject()

    def closeEvent(self,event):
        if self.is_closed:super().closeEvent(event)
        else:self.reject(); event.ignore()
