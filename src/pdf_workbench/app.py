"""Native PDF page organizer with asynchronous previews and safe local exports."""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import sys
import threading
import uuid

from PyQt6.QtCore import QItemSelectionModel, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QKeySequence, QPalette
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QComboBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSizePolicy, QSplitter, QStackedWidget, QToolButton,
    QVBoxLayout, QWidget,
)

from . import __version__
from .core import OperationCancelled, PageRef, PdfWorkbenchError, Workspace, export_pdf, load_source
from .features import export_pages_separately, parse_page_selection
from .ui.dialogs import PageRangeDialog, SeparateExportDialog
from .ui.annotation_dialogs import AnnotationListDialog, CommentDialog, SignatureDialog
from .ui.annotation_overlay import display_to_basis
from .ui.icons import icon
from .ui.pages import PAGE_ROLE, POSITION_ROLE, SOURCE_ROLE, UID_ROLE, PageList
from .ui.preview import Preview
from .ui.rendering import RenderBroker


class Job(QThread):
    progressed = pyqtSignal(int, str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal(str)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.task = task
        self.cancel_event = threading.Event()

    def run(self):
        try:
            self.succeeded.emit(self.task(self.cancel_event.is_set, self.progressed.emit))
        except OperationCancelled as error:
            self.cancelled.emit(str(error) or "Vorgang abgebrochen.")
        except Exception as error:
            self.failed.emit(str(error))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.workspace = Workspace()
        self.job = None
        self.saved_state = ()
        self._active_uid = None
        self._page_by_uid = {}
        self._closing_requested = False
        self.render_broker = RenderBroker(self)
        self.render_broker.closed.connect(self._rendering_closed)
        self.setWindowTitle("PDF Workbench")
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry().size() if screen else QSize(1200, 900)
        self.setMinimumSize(min(800, max(600, available.width() - 32)), min(560, max(440, available.height() - 48)))
        self.resize(min(1160, available.width() - 32), min(780, available.height() - 48))
        self.setAcceptDrops(True)
        self._build_ui()
        self._build_menu()
        self.refresh()

    def _button(self, text, slot, symbol=None, primary=False):
        button = QPushButton(text)
        button.setMinimumHeight(34)
        if symbol:
            button.setIcon(icon(symbol, "white" if primary else None)); button.setIconSize(QSize(18, 18))
        if primary:
            button.setObjectName("primary")
        button.clicked.connect(slot)
        return button

    def _tool(self, label, symbol, slot, shortcut=None, text=False):
        button = QToolButton()
        button.setIcon(icon(symbol)); button.setIconSize(QSize(19, 19))
        button.setText(label); button.setAccessibleName(label)
        button.setToolTip(label + (f" ({shortcut})" if shortcut else ""))
        button.setMinimumSize(34, 34)
        if text:
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.clicked.connect(slot)
        return button

    @staticmethod
    def _separator():
        line = QFrame(); line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedHeight(22); line.setObjectName("separator")
        return line

    def _build_ui(self):
        central = QWidget()
        outer = QVBoxLayout(central); outer.setContentsMargins(18, 14, 18, 12); outer.setSpacing(12)
        header = QHBoxLayout(); header.setSpacing(8)
        title = QLabel("PDF Workbench"); title.setObjectName("title")
        header.addWidget(title)
        self.source_summary = QLabel(); self.source_summary.setObjectName("muted")
        header.addWidget(self.source_summary); header.addStretch()
        self.new_button = self._tool("Neuer Arbeitsbereich", "new", self.new_workspace, "Strg+N")
        self.undo_button = self._tool("Rückgängig", "undo", self.undo, "Strg+Z")
        self.redo_button = self._tool("Wiederholen", "redo", self.redo, "Strg+Umschalt+Z")
        for button in (self.new_button, self.undo_button, self.redo_button): header.addWidget(button)
        header.addWidget(self._separator())
        self.open_button = self._button("PDFs hinzufügen …", self.open_dialog, "add")
        header.addWidget(self.open_button)
        outer.addLayout(header)

        toolbar = QFrame(); toolbar.setObjectName("toolbar")
        tools = QHBoxLayout(toolbar); tools.setContentsMargins(10, 6, 10, 6); tools.setSpacing(4)
        order_label = QLabel("Anordnen"); order_label.setObjectName("toolCaption"); tools.addWidget(order_label)
        self.up_button = self._tool("Nach oben verschieben", "up", lambda: self.move(-1), "Alt+↑")
        self.down_button = self._tool("Nach unten verschieben", "down", lambda: self.move(1), "Alt+↓")
        self.duplicate_button = self._tool("Auswahl duplizieren", "duplicate", self.duplicate_pages, "Strg+D")
        self.delete_button = self._tool("Auswahl entfernen", "delete", self.delete_pages, "Entf")
        for button in (self.up_button, self.down_button, self.duplicate_button, self.delete_button): tools.addWidget(button)
        tools.addSpacing(6); tools.addWidget(self._separator()); tools.addSpacing(6)
        rotate_label = QLabel("Drehen"); rotate_label.setObjectName("toolCaption"); tools.addWidget(rotate_label)
        self.left_button = self._tool("90° links drehen", "rotate_left", lambda: self.rotate(-90), "Strg+L")
        self.right_button = self._tool("90° rechts drehen", "rotate_right", lambda: self.rotate(90), "Strg+R")
        tools.addWidget(self.left_button); tools.addWidget(self.right_button)
        tools.addSpacing(6); tools.addWidget(self._separator()); tools.addSpacing(6)
        self.range_button = self._tool("Bereich …", "range", self.choose_page_range, "Strg+G", text=True)
        tools.addWidget(self.range_button)
        tools.addStretch()
        self.preview_toggle = self._tool("Vorschau", "preview", self.toggle_preview, text=True)
        self.preview_toggle.setCheckable(True); self.preview_toggle.setChecked(True)
        tools.addWidget(self.preview_toggle)
        outer.addWidget(toolbar)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        left = QWidget(); left_layout = QVBoxLayout(left); left_layout.setContentsMargins(0, 0, 0, 0); left_layout.setSpacing(8)
        pages_header = QHBoxLayout(); pages_header.setSpacing(6)
        pages_title = QLabel("Seiten"); pages_title.setObjectName("sectionTitle")
        pages_header.addWidget(pages_title); pages_header.addStretch()
        self.grid_button = self._tool("Miniaturraster", "grid", lambda: self.set_view_mode(True))
        self.list_button = self._tool("Miniaturliste", "list", lambda: self.set_view_mode(False))
        self.view_group = QButtonGroup(self); self.view_group.setExclusive(True)
        for button in (self.grid_button, self.list_button):
            button.setCheckable(True); self.view_group.addButton(button); pages_header.addWidget(button)
        self.grid_button.setChecked(True)
        left_layout.addLayout(pages_header)
        self.stack = QStackedWidget()
        welcome = QFrame(); welcome.setObjectName("welcome")
        welcome_layout = QVBoxLayout(welcome); welcome_layout.setContentsMargins(24, 28, 24, 28); welcome_layout.setSpacing(12)
        welcome_layout.addStretch()
        document = QLabel(); document.setPixmap(icon("document", "#647da6").pixmap(QSize(42, 42))); welcome_layout.addWidget(document)
        empty_title = QLabel("PDFs hinzufügen"); empty_title.setObjectName("emptyTitle"); welcome_layout.addWidget(empty_title)
        empty_hint = QLabel("Dateien hierher ziehen oder auswählen. Danach Seiten ordnen und als neue PDF speichern.")
        empty_hint.setWordWrap(True); empty_hint.setObjectName("muted"); welcome_layout.addWidget(empty_hint)
        self.empty_open_button = self._button("Dateien auswählen …", self.open_dialog, "add", primary=True)
        welcome_layout.addWidget(self.empty_open_button); welcome_layout.addStretch()
        self.stack.addWidget(welcome)
        self.page_list = PageList(self.render_broker)
        self.page_list.files_dropped.connect(self.import_files)
        self.page_list.order_changed.connect(self.reordered)
        self.page_list.itemSelectionChanged.connect(self.selection_changed)
        self.page_list.currentItemChanged.connect(self.current_changed)
        self.stack.addWidget(self.page_list); left_layout.addWidget(self.stack, 1)
        self.summary = QLabel(); self.summary.setObjectName("muted"); self.summary.setWordWrap(True)
        left_layout.addWidget(self.summary)
        self.splitter.addWidget(left)

        self.preview_panel = QWidget()
        right_layout = QVBoxLayout(self.preview_panel); right_layout.setContentsMargins(0, 0, 0, 0); right_layout.setSpacing(8)
        preview_header = QHBoxLayout()
        self.preview_label = QLabel("Vorschau"); self.preview_label.setObjectName("sectionTitle")
        preview_header.addWidget(self.preview_label); preview_header.addStretch()
        self.active_label = QLabel(); self.active_label.setObjectName("muted"); preview_header.addWidget(self.active_label)
        right_layout.addLayout(preview_header)
        self.preview = Preview(self.render_broker)
        zoom_bar = QHBoxLayout(); zoom_bar.setContentsMargins(0, 0, 0, 0); zoom_bar.setSpacing(4)
        self.zoom_out_button = self._tool("Verkleinern", "zoom_out", self.preview.zoom_out, "Strg+−")
        self.zoom_in_button = self._tool("Vergrößern", "zoom_in", self.preview.zoom_in, "Strg++")
        self.zoom_combo = QComboBox(); self.zoom_combo.setEditable(True); self.zoom_combo.setFixedWidth(75)
        self.zoom_combo.setAccessibleName("Vergrößerung in Prozent, 1 bis 400")
        self.zoom_combo.addItems(["25 %", "50 %", "75 %", "100 %", "125 %", "150 %", "200 %", "300 %", "400 %"])
        self.zoom_combo.activated.connect(lambda *_: self.apply_zoom_text())
        self.zoom_combo.lineEdit().editingFinished.connect(self.apply_zoom_text)
        self.fit_page_button = self._tool("Seite", "fit_page", self.preview.fit_page, "Strg+0", text=True)
        self.fit_width_button = self._tool("Breite", "fit_width", self.preview.fit_width, "Strg+1", text=True)
        for button in (self.fit_page_button, self.fit_width_button): button.setCheckable(True)
        for widget in (self.zoom_out_button, self.zoom_combo, self.zoom_in_button): zoom_bar.addWidget(widget)
        zoom_bar.addSpacing(4); zoom_bar.addWidget(self._separator())
        zoom_bar.addWidget(self.fit_page_button); zoom_bar.addWidget(self.fit_width_button); zoom_bar.addStretch()
        right_layout.addLayout(zoom_bar)
        annotation_bar = QHBoxLayout(); annotation_bar.setSpacing(3)
        self.pan_button = self._tool("Hand: Seite verschieben", "hand", lambda: self.set_annotation_tool("pan"))
        self.comment_button = self._tool("Kommentar: auf die Seite klicken", "comment", lambda: self.set_annotation_tool("comment"))
        self.highlight_button = self._tool("Text markieren: über PDF-Text ziehen", "highlight", lambda: self.set_annotation_tool("highlight"))
        self.signature_button = self._tool("Gezeichnete Unterschrift …", "signature", self.choose_signature)
        self.annotation_group = QButtonGroup(self); self.annotation_group.setExclusive(True)
        for button in (self.pan_button, self.comment_button, self.highlight_button, self.signature_button):
            button.setCheckable(True); self.annotation_group.addButton(button); annotation_bar.addWidget(button)
        self.pan_button.setChecked(True)
        annotation_bar.addStretch()
        self.annotations_button = self._tool("Anmerkungen (0) …", "list", self.manage_annotations, text=True)
        annotation_bar.addWidget(self.annotations_button)
        right_layout.addLayout(annotation_bar)
        right_layout.addWidget(self.preview, 1)
        self.page_details = QLabel("Originaldateien bleiben unverändert.")
        self.page_details.setObjectName("muted"); self.page_details.setWordWrap(True); self.page_details.setMaximumHeight(34)
        right_layout.addWidget(self.page_details)
        self.preview.scale_changed.connect(self.update_zoom_controls)
        self.preview.annotation_added.connect(self.add_annotation)
        self.preview.comment_requested.connect(self.add_comment)
        self.preview.status_message.connect(self.set_status)
        self.preview.selection_pending_changed.connect(lambda *_: self.update_controls())
        self.splitter.addWidget(self.preview_panel); self.splitter.setHandleWidth(14); self.splitter.setSizes([465, 660])
        outer.addWidget(self.splitter, 1)

        footer = QFrame(); footer.setObjectName("footer")
        footer_layout = QHBoxLayout(footer); footer_layout.setContentsMargins(0, 10, 0, 0); footer_layout.setSpacing(8)
        status_layout = QVBoxLayout(); status_layout.setSpacing(4)
        self.status = QLabel("Bereit. Dateien bleiben auf diesem Gerät.")
        self.status.setWordWrap(True); self.status.setMaximumHeight(36)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status.setAccessibleName("Bearbeitungsstatus")
        status_layout.addWidget(self.status)
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setMaximumHeight(5); self.progress.setTextVisible(False); self.progress.hide()
        status_layout.addWidget(self.progress); footer_layout.addLayout(status_layout, 1)
        self.cancel_button = self._button("Abbrechen", self.cancel_job); self.cancel_button.hide()
        self.separate_button = self._button("Einzelseiten …", self.save_separate_pages, "separate")
        self.split_button = self._button("Auswahl …", lambda: self.save_pdf(True), "export")
        self.export_button = self._button("PDF speichern …", lambda: self.save_pdf(False), "export", primary=True)
        for button in (self.cancel_button, self.separate_button, self.split_button, self.export_button): footer_layout.addWidget(button)
        self.separate_button.setToolTip("Jede ausgewählte Seite als eigene PDF in einem neuen Unterordner speichern")
        self.split_button.setToolTip("Ausgewählte Seiten zusammen als neue PDF speichern")
        self.export_button.setToolTip("Alle Seiten in aktueller Reihenfolge als neue PDF speichern")
        outer.addWidget(footer)
        self.setCentralWidget(central)

    def _action(self, menu, label, shortcut, slot, symbol=None):
        action = QAction(icon(symbol) if symbol else icon("document"), label, self)
        if shortcut: action.setShortcut(shortcut)
        action.triggered.connect(slot); menu.addAction(action)
        return action

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("&Datei")
        self.open_action = self._action(file_menu, "PDFs hinzufügen …", QKeySequence.StandardKey.Open, self.open_dialog, "add")
        self.new_action = self._action(file_menu, "Neuer Arbeitsbereich", QKeySequence.StandardKey.New, self.new_workspace, "new")
        file_menu.addSeparator()
        self.export_action = self._action(file_menu, "Alle Seiten als neue PDF speichern …", QKeySequence.StandardKey.SaveAs, lambda: self.save_pdf(False), "export")
        self.split_action = self._action(file_menu, "Auswahl als neue PDF speichern …", "Ctrl+Shift+E", lambda: self.save_pdf(True), "export")
        self.separate_action = self._action(file_menu, "Auswahl als einzelne PDFs speichern …", None, self.save_separate_pages, "separate")
        file_menu.addSeparator(); self._action(file_menu, "Beenden", QKeySequence.StandardKey.Quit, self.close)
        edit = self.menuBar().addMenu("&Bearbeiten")
        self.undo_action = self._action(edit, "Rückgängig", QKeySequence.StandardKey.Undo, self.undo, "undo")
        self.redo_action = self._action(edit, "Wiederholen", QKeySequence.StandardKey.Redo, self.redo, "redo")
        edit.addSeparator()
        self.select_action = self._action(edit, "Alle Seiten auswählen", QKeySequence.StandardKey.SelectAll, self.page_list.selectAll)
        self.range_action = self._action(edit, "Seitenbereich auswählen …", "Ctrl+G", self.choose_page_range, "range")
        self.duplicate_action = self._action(edit, "Auswahl duplizieren", "Ctrl+D", self.duplicate_pages, "duplicate")
        self.delete_action = self._action(edit, "Auswahl entfernen", "Delete", self.delete_pages, "delete")
        edit.addSeparator()
        self.move_up_action = self._action(edit, "Nach oben", "Alt+Up", lambda: self.move(-1), "up")
        self.move_down_action = self._action(edit, "Nach unten", "Alt+Down", lambda: self.move(1), "down")
        self.rotate_left_action = self._action(edit, "90° links drehen", "Ctrl+L", lambda: self.rotate(-90), "rotate_left")
        self.rotate_right_action = self._action(edit, "90° rechts drehen", "Ctrl+R", lambda: self.rotate(90), "rotate_right")
        view_menu = self.menuBar().addMenu("&Ansicht")
        self.zoom_in_action = self._action(view_menu, "Vergrößern", QKeySequence.StandardKey.ZoomIn, self.preview.zoom_in, "zoom_in")
        self.zoom_out_action = self._action(view_menu, "Verkleinern", QKeySequence.StandardKey.ZoomOut, self.preview.zoom_out, "zoom_out")
        self.fit_page_action = self._action(view_menu, "Ganze Seite einpassen", "Ctrl+0", self.preview.fit_page, "fit_page")
        self.fit_width_action = self._action(view_menu, "Seitenbreite einpassen", "Ctrl+1", self.preview.fit_width, "fit_width")
        annotate = self.menuBar().addMenu("&Anmerkungen")
        self.pan_action = self._action(annotate, "Hand: Seite verschieben", None, lambda: self.set_annotation_tool("pan"), "hand")
        self.comment_action = self._action(annotate, "Kommentar hinzufügen", None, lambda: self.set_annotation_tool("comment"), "comment")
        self.highlight_action = self._action(annotate, "Text markieren", None, lambda: self.set_annotation_tool("highlight"), "highlight")
        self.signature_action = self._action(annotate, "Gezeichnete Unterschrift …", None, self.choose_signature, "signature")
        annotate.addSeparator()
        self.annotations_action = self._action(annotate, "Anmerkungen bearbeiten oder löschen …", None, self.manage_annotations, "list")
        help_menu = self.menuBar().addMenu("&Hilfe"); self._action(help_menu, "Über PDF Workbench", None, self.about)

    def selected_ids(self):
        return {item.data(UID_ROLE) for item in self.page_list.selectedItems()}

    def refresh(self, selected=None, active_uid=None, ensure_visible=False):
        selected = self.selected_ids() if selected is None else set(selected)
        active_uid = self._active_uid if active_uid is None else active_uid
        self._page_by_uid = {page.uid: page for page in self.workspace.pages}
        selected &= self._page_by_uid.keys()
        if active_uid not in self._page_by_uid:
            active_uid = next((page.uid for page in self.workspace.pages if page.uid in selected), None)
        if active_uid is None and self.workspace.pages:
            active_uid = self.workspace.pages[0].uid
        if not selected and active_uid:
            selected = {active_uid}
        self._active_uid = active_uid
        self.render_broker.invalidate()
        self.page_list.blockSignals(True); self.page_list.clear()
        current = None
        for position, page in enumerate(self.workspace.pages, 1):
            source = self.workspace.sources[page.source_id]
            item = QListWidgetItem(f"Seite {position}, {source.label}, Originalseite {page.page_index + 1}, Drehung {page.rotation} Grad")
            item.setData(UID_ROLE, page.uid); item.setData(PAGE_ROLE, page); item.setData(SOURCE_ROLE, source); item.setData(POSITION_ROLE, position)
            item.setToolTip(f"Seite {position} · {source.path}\nOriginalseite {page.page_index + 1} · zusätzliche Drehung {page.rotation}°")
            self.page_list.addItem(item); item.setSelected(page.uid in selected)
            if page.uid == active_uid: current = item
        if current: self.page_list.setCurrentItem(current, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.page_list.blockSignals(False)
        self.stack.setCurrentIndex(1 if self.workspace.pages else 0)
        self.selection_changed(); self.load_preview(); self.update_controls(); self._update_title()
        if ensure_visible and current:
            self.page_list.setFocus(Qt.FocusReason.OtherFocusReason)
            self.page_list.scrollToItem(current, QAbstractItemView.ScrollHint.EnsureVisible)
            QTimer.singleShot(0, self._ensure_current_visible)

    def _ensure_current_visible(self):
        if self.page_list.currentItem(): self.page_list.scrollToItem(self.page_list.currentItem(), QAbstractItemView.ScrollHint.EnsureVisible)

    def current_changed(self, current, previous=None):
        self._active_uid = current.data(UID_ROLE) if current else None
        self.load_preview(); self.selection_changed(); self.page_list.viewport().update()

    def load_preview(self):
        page = self._page_by_uid.get(self._active_uid)
        if page is None:
            self.preview.show_page(None, None); self.active_label.clear()
            self.annotations_button.setText("Anmerkungen (0) …")
            self.page_details.setText("Originaldateien bleiben unverändert."); return
        source = self.workspace.sources[page.source_id]
        position = next(i for i, p in enumerate(self.workspace.pages, 1) if p.uid == page.uid)
        self.active_label.setText(f"Seite {position} von {len(self.workspace.pages)}")
        self.page_details.setText(f"{source.label} · Originalseite {page.page_index + 1} · +{page.rotation}°")
        self.page_details.setToolTip(str(source.path))
        self.preview.show_page(source, page)
        self.annotations_button.setText(f"Anmerkungen ({len(page.annotations)}) …")

    def _update_title(self):
        self.setWindowTitle(f"{'* ' if tuple(self.workspace.pages) != self.saved_state else ''}PDF Workbench")

    def update_controls(self):
        busy = self.job is not None or self._closing_requested
        selected, has_pages = bool(self.selected_ids()), bool(self.workspace.pages)
        for widget in (self.open_button, self.empty_open_button, self.new_button, self.open_action, self.new_action): widget.setEnabled(not busy)
        self.page_list.setEnabled(not busy)
        for widget in (self.up_button, self.down_button, self.left_button, self.right_button, self.delete_button, self.duplicate_button, self.move_up_action, self.move_down_action, self.rotate_left_action, self.rotate_right_action, self.delete_action, self.duplicate_action, self.split_button, self.split_action, self.separate_button, self.separate_action): widget.setEnabled(selected and not busy)
        for widget in (self.export_button, self.export_action, self.select_action, self.range_button, self.range_action): widget.setEnabled(has_pages and not busy)
        if self.preview.selection_pending:
            for widget in (self.export_button, self.export_action, self.split_button, self.split_action, self.separate_button, self.separate_action): widget.setEnabled(False)
        for widget in (self.undo_button, self.undo_action): widget.setEnabled(self.workspace.can_undo and not busy)
        for widget in (self.redo_button, self.redo_action): widget.setEnabled(self.workspace.can_redo and not busy)
        for widget in (self.zoom_in_button, self.zoom_out_button, self.zoom_combo, self.fit_page_button, self.fit_width_button, self.zoom_in_action, self.zoom_out_action, self.fit_page_action, self.fit_width_action): widget.setEnabled(has_pages)
        for widget in (self.pan_button, self.comment_button, self.highlight_button, self.signature_button, self.pan_action, self.comment_action, self.highlight_action, self.signature_action): widget.setEnabled(has_pages and not busy)
        page = self._page_by_uid.get(self._active_uid)
        for widget in (self.annotations_button, self.annotations_action): widget.setEnabled(bool(page and page.annotations) and not busy)
        self.preview.canvas.setEnabled(not busy)

    def set_annotation_tool(self, tool, strokes=()):
        if self.job: return
        if not self.preview_toggle.isChecked():
            self.preview_toggle.setChecked(True); self.toggle_preview()
        self.preview.set_tool(tool, strokes)
        {"pan": self.pan_button, "comment": self.comment_button, "highlight": self.highlight_button, "signature": self.signature_button}[tool].setChecked(True)
        hints = {
            "pan": "Hand: Seite mit gedrückter Maustaste verschieben. Seitliches Mausrad oder Umschalt+Mausrad scrollt horizontal; Strg+Mausrad zoomt.",
            "comment": "Kommentar: auf die gewünschte Stelle klicken. Zum Verschieben der Ansicht jederzeit die mittlere Maustaste verwenden.",
            "highlight": "Textmarkierung: über vorhandenen PDF-Text ziehen. Gescannte Seiten ohne Textschicht können so nicht markiert werden.",
            "signature": "Gezeichnete Unterschrift: einmal klicken für Standardgröße oder einen Bereich für die gewünschte Größe ziehen.",
        }
        self.set_status(hints[tool])

    def choose_signature(self):
        if self.job or not self.workspace.pages: return
        strokes = SignatureDialog.get_strokes(self)
        if strokes:
            self.set_annotation_tool("signature", strokes)
        else:
            {"pan": self.pan_button, "comment": self.comment_button, "highlight": self.highlight_button, "signature": self.signature_button}[self.preview.tool].setChecked(True)

    def add_annotation(self, page_uid, annotation):
        if self.job or page_uid not in self._page_by_uid: return
        try:
            self.workspace.add_annotation(page_uid, annotation)
            self.refresh(active_uid=page_uid)
            self.set_status("Anmerkung hinzugefügt. Rückgängig ist verfügbar; mit PDF speichern in die neue Datei übernehmen.")
        except (ValueError, PdfWorkbenchError) as error:
            self.show_error("Anmerkung konnte nicht hinzugefügt werden.", str(error))

    def add_comment(self, page_uid, rect):
        if self.job or page_uid not in self._page_by_uid: return
        text = CommentDialog.get_text(self)
        if text:
            from .annotations import PageAnnotation
            self.add_annotation(page_uid, PageAnnotation(uuid.uuid4().hex, "comment", rect, text=text))

    def manage_annotations(self):
        if self.job or self._active_uid not in self._page_by_uid: return
        uid = self._active_uid
        def annotations():
            return self._page_by_uid[uid].annotations if uid in self._page_by_uid else ()
        def edit(annotation):
            if annotation.kind == "signature":
                strokes = SignatureDialog.get_strokes(self)
                if not strokes: return
                rotation = self._page_by_uid[uid].rotation
                updated = replace(annotation, strokes=tuple(tuple(display_to_basis(point, rotation) for point in stroke) for stroke in strokes))
            else:
                text = CommentDialog.get_text(self, annotation.text, "Kommentar bearbeiten" if annotation.kind == "comment" else "Textmarkierung: Notiz bearbeiten")
                if text is None: return
                updated = replace(annotation, text=text)
            self.workspace.update_annotation(uid, updated); self.refresh(active_uid=uid)
        def remove(annotation):
            self.workspace.remove_annotation(uid, annotation.uid); self.refresh(active_uid=uid)
        AnnotationListDialog(self, annotations, edit, remove).exec()

    def selection_changed(self):
        count, sources = len(self.workspace.pages), len({p.source_id for p in self.workspace.pages})
        self.source_summary.setText(
            f"{count} {'Seite' if count == 1 else 'Seiten'} · "
            f"{sources} {'Datei' if sources == 1 else 'Dateien'}"
        )
        current = self.page_list.currentRow() + 1
        self.summary.setText(f"{len(self.selected_ids())} ausgewählt" + (f" · Blauer Rahmen: Vorschau Seite {current}" if current else " · Strg/Cmd oder Umschalt für Mehrfachauswahl"))
        self.update_controls()

    def set_view_mode(self, grid):
        self.page_list.set_grid_mode(grid)
        self.grid_button.setChecked(grid); self.list_button.setChecked(not grid)
        self._ensure_current_visible()

    def toggle_preview(self):
        visible = self.preview_toggle.isChecked()
        self.preview_panel.setVisible(visible)
        self.preview_toggle.setToolTip("Vorschau ausblenden" if visible else "Vorschau einblenden")
        if visible: self.load_preview()

    def update_zoom_controls(self, factor, mode):
        self.zoom_combo.blockSignals(True); self.zoom_combo.setCurrentText(f"{round(factor * 100)} %"); self.zoom_combo.blockSignals(False)
        self.fit_page_button.setChecked(mode == "page"); self.fit_width_button.setChecked(mode == "width")

    def apply_zoom_text(self):
        try:
            value = float(self.zoom_combo.currentText().replace("%", "").replace(",", ".").strip())
            self.preview.set_zoom(value / 100)
        except ValueError:
            self.update_zoom_controls(self.preview.zoom_factor, self.preview.zoom_mode)

    def open_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "PDFs hinzufügen", "", "PDF-Dateien (*.pdf);;Alle Dateien (*)")
        if files: self.import_files(files)

    def import_files(self, files):
        if self.job or not files: return
        paths = list(dict.fromkeys(str(Path(path)) for path in files))
        def task(cancel, progress):
            sources, errors = [], []
            for index, path in enumerate(paths):
                if cancel(): raise OperationCancelled()
                progress(int(index / len(paths) * 100), f"{Path(path).name} öffnen …")
                try: sources.append(load_source(path, cancel))
                except OperationCancelled: raise
                except Exception as error: errors.append(f"{Path(path).name}: {error}")
            return sources, errors
        def success(result):
            sources, errors = result
            if sources:
                selected = self.workspace.add_sources(sources)
                self.refresh(selected, selected[0], ensure_visible=True)
            self.set_status(f"{len(sources)} Datei(en) hinzugefügt.")
            if errors: self.show_error("Einige Dateien konnten nicht geöffnet werden.", "\n\n".join(errors))
        self.start_job(task, success)

    def set_status(self, text):
        self.status.setText(text); self.status.setToolTip(text)

    def start_job(self, task, success):
        job = Job(task, self); self.job = job
        job.progressed.connect(self.job_progress); job.succeeded.connect(success)
        job.failed.connect(lambda error: self.show_error("Der Vorgang konnte nicht abgeschlossen werden.", error))
        job.cancelled.connect(self.job_cancelled)
        job.finished.connect(self.job_finished)
        self.progress.setValue(0); self.progress.show(); self.cancel_button.setEnabled(True); self.cancel_button.show()
        self.set_status("Vorgang wird vorbereitet …"); self.update_controls(); job.start()

    def job_progress(self, value, message):
        self.progress.setValue(value); self.set_status(message)

    def job_cancelled(self, message):
        self.set_status(message)
        if "verblieben:" in message or "Nicht vollständig bereinigt" in message:
            self.show_error("Abgebrochen: Hinweise zu verbliebenen Dateien", message)

    def cancel_job(self):
        if self.job:
            self.job.cancel_event.set(); self.cancel_button.setEnabled(False)
            self.set_status("Abbruch angefordert. Der aktuelle Arbeitsschritt wird beendet …")

    def job_finished(self):
        job, self.job = self.job, None
        self.progress.hide(); self.cancel_button.hide(); self.update_controls()
        if job: job.deleteLater()

    def move(self, direction):
        if not self.job:
            selected = self.selected_ids(); self.workspace.move(selected, direction); self.refresh(selected, ensure_visible=True)

    def rotate(self, degrees):
        if not self.job:
            selected = self.selected_ids(); self.workspace.rotate(selected, degrees); self.refresh(selected)

    def duplicate_pages(self):
        if not self.job and self.selected_ids():
            new_ids = self.workspace.duplicate(self.selected_ids())
            self.refresh(new_ids, new_ids[0] if new_ids else self._active_uid, ensure_visible=True)
            self.set_status(f"{len(new_ids)} Seite(n) dupliziert. Rückgängig ist verfügbar.")

    def delete_pages(self):
        if self.job: return
        selected = self.selected_ids()
        if selected:
            position = min(i for i, page in enumerate(self.workspace.pages) if page.uid in selected)
            self.workspace.delete(selected)
            neighbor = self.workspace.pages[min(position, len(self.workspace.pages) - 1)].uid if self.workspace.pages else None
            self.refresh([neighbor] if neighbor else [], neighbor, ensure_visible=True)
            self.set_status(f"{len(selected)} Seite(n) entfernt. Mit Rückgängig wiederherstellen.")

    def reordered(self, ordered_ids):
        if not self.job:
            self.workspace.reorder(ordered_ids); self.refresh(ensure_visible=True)

    def undo(self):
        if not self.job: self.workspace.undo(); self.refresh(ensure_visible=True)

    def redo(self):
        if not self.job: self.workspace.redo(); self.refresh(ensure_visible=True)

    def _select_indices(self, indices):
        selected = [self.workspace.pages[i].uid for i in indices]
        self.refresh(selected, selected[0] if selected else self._active_uid, ensure_visible=True)

    def select_page_range(self, text):
        self._select_indices(parse_page_selection(text, len(self.workspace.pages)))

    def choose_page_range(self):
        if self.job or not self.workspace.pages: return
        indices = PageRangeDialog.get_selection(self, len(self.workspace.pages))
        if indices is not None: self._select_indices(indices)

    def save_pdf(self, selected_only):
        if self.job: return
        if self.preview.selection_pending:
            self.set_status("Bitte die laufende PDF-Textauswahl abwarten oder das Werkzeug wechseln."); return
        selected = self.selected_ids()
        pages = [page for page in self.workspace.pages if not selected_only or page.uid in selected]
        if not pages: return
        title = "Ausgewählte Seiten als neue PDF speichern" if selected_only else "Alle Seiten als neue PDF speichern"
        filename, _ = QFileDialog.getSaveFileName(self, title, "auswahl.pdf" if selected_only else "zusammengefuegt.pdf", "PDF-Dateien (*.pdf)", options=QFileDialog.Option.DontConfirmOverwrite)
        if not filename: return
        path = Path(filename)
        if not path.suffix: path = path.with_suffix(".pdf")
        sources, snapshot = dict(self.workspace.sources), tuple(self.workspace.pages)
        def task(cancel, progress): return export_pdf(sources, pages, path, cancel, progress)
        def success(saved):
            if tuple(pages) == snapshot: self.saved_state = snapshot; self._update_title()
            self.set_status(f"{len(pages)} Seite(n) gespeichert: {saved}")
        self.start_job(task, success)

    def save_separate_pages(self):
        if self.job: return
        if self.preview.selection_pending:
            self.set_status("Bitte die laufende PDF-Textauswahl abwarten oder das Werkzeug wechseln."); return
        selected = self.selected_ids(); pages = [page for page in self.workspace.pages if page.uid in selected]
        if not pages: return
        destination = SeparateExportDialog.get_destination(self, len(pages))
        if destination is None: return
        sources = dict(self.workspace.sources)
        def task(cancel, progress): return export_pages_separately(sources, pages, destination, cancel, progress)
        self.start_job(task, lambda saved: self.set_status(f"{len(pages)} einzelne PDFs gespeichert: {saved}"))

    def confirm_discard(self):
        if tuple(self.workspace.pages) == self.saved_state: return True
        return QMessageBox.question(self, "Ungespeicherte Änderungen verwerfen?", "Die aktuelle Seitenanordnung und ihre Anmerkungen wurden noch nicht vollständig als PDF gespeichert. Verwerfen?\n\nDie Originaldateien bleiben erhalten.", QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Discard

    def new_workspace(self):
        if not self.job and self.confirm_discard():
            self.workspace = Workspace(); self.saved_state = (); self._active_uid = None
            self.preview.set_tool("pan"); self.pan_button.setChecked(True)
            self.render_broker.invalidate(clear_cache=True); self.refresh()
            self.set_status("Neuer Arbeitsbereich. PDFs können hinzugefügt werden.")

    def closeEvent(self, event):
        if self.job:
            self.cancel_job(); self.set_status("Bitte den laufenden Abbruch abwarten und anschließend schließen."); event.ignore()
        elif self.render_broker.is_closed:
            event.accept()
        elif self._closing_requested:
            event.ignore()
        elif self.confirm_discard():
            self._closing_requested = True; self.update_controls(); self.render_broker.shutdown(); event.ignore()
        else: event.ignore()

    def _rendering_closed(self):
        if self._closing_requested: self.close()

    def show_error(self, title, detail):
        self.set_status(title)
        box = QMessageBox(QMessageBox.Icon.Warning, "PDF Workbench", title, QMessageBox.StandardButton.Ok, self)
        box.setInformativeText(detail[:1800])
        if len(detail) > 1800: box.setDetailedText(detail)
        box.exec()

    def about(self):
        QMessageBox.about(self, "Über PDF Workbench", f"<b>PDF Workbench {__version__}</b><br>Freie, lokale PDF-Seitenverwaltung.<br><br>Zusammenfügen, duplizieren, trennen, drehen und sortieren.<br>Kommentare, echte Textmarkierungen und gezeichnete<br>Unterschriften; Miniaturen und Vorschau mit Zoom.<br><br>GPL-3.0-or-later · PyQt6 / QtPdf · pypdf<br>Gezeichnete Unterschriften sind sichtbare Zeichnungen.<br>Formulare und Zertifikatssignaturen sind nicht unterstützt.<br>Dokumentweite Lesezeichen, Tags und Anhänge werden<br>nicht erhalten; PDF/A-Konformität ist nicht garantiert.<br><br>Unabhängiges Projekt ohne Adobe-Programmcode.")

    def dragEnterEvent(self, event):
        if not self.job and event.mimeData().hasUrls(): event.acceptProposedAction()

    def dropEvent(self, event):
        if not self.job:
            self.import_files([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]); event.acceptProposedAction()


STYLE = """
QMainWindow, QWidget { background: #f4f6f9; color: #17243b; }
QWidget { font-size: 13px; }
QLabel#title { font-size: 20px; font-weight: 650; margin-right: 10px; }
QLabel#sectionTitle { font-size: 15px; font-weight: 600; }
QLabel#emptyTitle { font-size: 20px; font-weight: 600; }
QLabel#muted, QLabel#toolCaption { color: #526278; font-size: 12px; }
QLabel#toolCaption { margin-left: 3px; margin-right: 5px; }
QFrame#toolbar { background: #ffffff; border: 1px solid #d6dde6; border-radius: 6px; }
QFrame#toolbar QLabel { background: transparent; }
QFrame#separator { color: #d6dde6; }
QFrame#footer { border-top: 1px solid #d6dde6; }
QPushButton { background: #ffffff; border: 1px solid #a7b5c8; border-radius: 5px; padding: 5px 11px; }
QPushButton:hover { background: #e9eff8; border-color: #6683ae; }
QPushButton:pressed { background: #dae5f7; }
QPushButton:focus { border: 2px solid #2f5fc5; padding: 4px 10px; }
QPushButton:disabled { background: #eef1f5; color: #78869a; border-color: #d0d7e2; }
QPushButton#primary { color: white; background: #285cc4; border-color: #285cc4; }
QPushButton#primary:hover { background: #204da7; }
QPushButton#primary:focus { border: 2px solid #142c5b; }
QPushButton#primary:disabled { background: #cbd6e9; color: #526278; border-color: #cbd6e9; }
QToolButton { border: 1px solid transparent; border-radius: 4px; padding: 4px; background: transparent; }
QToolButton:hover { background: #e9eff8; border-color: #c1cfe4; }
QToolButton:checked { background: #dde8fc; border-color: #9cb5e2; }
QToolButton:focus { border: 2px solid #2f5fc5; padding: 3px; }
QToolButton:disabled { color: #8390a3; }
QListWidget { background: #edf1f6; border: 1px solid #c7d1df; border-radius: 5px; padding: 4px; }
QListWidget:focus { border: 2px solid #6683ae; padding: 3px; }
QFrame#welcome { background: #ffffff; border: 1px dashed #a7b5c8; border-radius: 6px; }
QFrame#welcome QLabel { background: transparent; border: none; }
QComboBox, QLineEdit { background: white; border: 1px solid #a7b5c8; padding: 6px; border-radius: 4px; }
QComboBox:focus, QLineEdit:focus { border: 2px solid #2f5fc5; padding: 5px; }
QProgressBar { background: #dae5f7; border: none; border-radius: 2px; }
QProgressBar::chunk { background: #2f5fc5; }
QMenuBar, QMenu { background: #ffffff; }
QMenu::item:selected { background: #dde8fc; }
QSplitter::handle { background: #f4f6f9; }
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description="PDF Workbench – lokale PDF-Seitenverwaltung")
    parser.add_argument("pdfs", nargs="*", help="PDF-Dateien beim Start hinzufügen")
    args = parser.parse_args(argv)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("PDF Workbench"); app.setApplicationVersion(__version__); app.setOrganizationName("PDF Workbench")
    app.setStyle("Fusion")
    palette = app.palette()
    for role, value in ((QPalette.ColorRole.Window, "#f4f6f9"), (QPalette.ColorRole.WindowText, "#17243b"), (QPalette.ColorRole.Base, "#ffffff"), (QPalette.ColorRole.Text, "#17243b"), (QPalette.ColorRole.Button, "#ffffff"), (QPalette.ColorRole.ButtonText, "#17243b"), (QPalette.ColorRole.Highlight, "#dde8fc"), (QPalette.ColorRole.HighlightedText, "#17243b")):
        palette.setColor(role, QColor(value))
    app.setPalette(palette); app.setStyleSheet(STYLE)
    window = MainWindow(); window.show()
    if args.pdfs: QTimer.singleShot(0, lambda: window.import_files(args.pdfs))
    return app.exec()
