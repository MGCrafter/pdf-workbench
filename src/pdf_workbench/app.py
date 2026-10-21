"""Native PDF page organizer with asynchronous previews and safe local exports."""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import sys
import threading
import uuid

from PyQt6.QtCore import QItemSelectionModel, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QComboBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSizePolicy, QSplitter, QStackedWidget, QToolButton, QMenu,
    QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox,
    QVBoxLayout, QWidget,
)

from . import __version__
from .core import (IncorrectPasswordError, OperationCancelled, PageRef, PasswordRequiredError,
                   PdfWorkbenchError, Workspace, export_pdf, load_source)
from .forms import fill_source_form, form_fields
from .features import export_pages_separately, parse_page_selection
from .ui.dialogs import PageRangeDialog, SeparateExportDialog
from .ui.annotation_dialogs import AnnotationListDialog, CommentDialog, SignatureDialog
from .ui.annotation_overlay import display_to_basis, source_page_geometry, validate_text_object
from .ui.object_dialogs import TextObjectDialog, ImageObjectDialog, SymbolObjectDialog
from .ui.icons import icon
from .ui.pages import PAGE_ROLE, POSITION_ROLE, SOURCE_ROLE, UID_ROLE, PageList
from .ui.preview import Preview
from .ui.rendering import RenderBroker
from .ui.search_bar import SearchBar
from .ui.image_dialog import ImagePagesDialog
from .image_import import load_image_source
from .ocr import export_searchable_pdf
from .ui.ocr_dialog import OcrDialog
from .ui.status import StatusLabel
from .ui.motion import MotionController
from .ui.welcome import WelcomeWorkspace
from .ui.pdf_dialogs import CompressionDialog, FormDialog, PasswordDialog, size_text
from .ui.theme import file_dialog_options, semantic_label, stylesheet, theme_controller


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
        finally:
            self.task = None


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.theme = theme_controller()
        self.motion = MotionController(self, self.theme.settings)
        self.workspace = Workspace()
        self.job = None
        self.saved_state = ()
        self._active_uid = None
        self._page_by_uid = {}
        self._form_field_cache = {}
        self._pending_locked_files = []
        self._closing_requested = False
        self.render_broker = RenderBroker(self)
        self.render_broker.closed.connect(self._rendering_closed)
        self.setWindowTitle("PDF Workbench")
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry().size() if screen else QSize(1200, 900)
        self.setMinimumSize(min(800, max(600, available.width() - 32)), min(560, max(440, available.height() - 48)))
        self.resize(min(1240, available.width() - 32), min(840, available.height() - 48))
        self.setAcceptDrops(True)
        self._build_ui()
        self._build_menu()
        self.theme.changed.connect(self._theme_changed)
        self.theme.preference_changed.connect(self._sync_theme_actions)
        self.refresh()

    def _button(self, text, slot, symbol=None, primary=False):
        button = QPushButton(text)
        button.setMinimumHeight(36)
        if symbol:
            button.setIcon(icon(symbol, "on_accent" if primary else None)); button.setIconSize(QSize(18, 18))
        if primary:
            button.setObjectName("primary")
        button.clicked.connect(slot)
        return button

    def _tool(self, label, symbol, slot, shortcut=None, text=False):
        button = QToolButton()
        button.setIcon(icon(symbol)); button.setIconSize(QSize(19, 19))
        button.setText(label); button.setAccessibleName(label)
        button.setToolTip(label + (f" ({shortcut})" if shortcut else ""))
        button.setMinimumSize(36, 36)
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
        outer = QVBoxLayout(central); outer.setContentsMargins(22, 18, 22, 14); outer.setSpacing(14)
        header = QHBoxLayout(); header.setSpacing(10)
        brand = QLabel(); brand.setObjectName("brandMark")
        brand.setPixmap(icon("document", "on_accent").pixmap(QSize(26, 26)))
        brand.setFixedSize(42, 42); header.addWidget(brand)
        self.brand_mark = brand
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
        self.appearance_button = self._tool("Darstellung und Bewegung", "appearance", lambda: None)
        self.appearance_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        header.addWidget(self.appearance_button)
        outer.addLayout(header)

        toolbar = QFrame(); toolbar.setObjectName("toolbar")
        self.organize_toolbar = toolbar
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
        self.document_tools_button = self._tool("Werkzeuge", "form", lambda: None, text=True)
        self.document_tools_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tools.addWidget(self.document_tools_button)
        self.preview_toggle = self._tool("Vorschau", "preview", self.toggle_preview, text=True)
        self.preview_toggle.setCheckable(True); self.preview_toggle.setChecked(True)
        tools.addWidget(self.preview_toggle)
        outer.addWidget(toolbar)

        self.search_bar = SearchBar(lambda: (dict(self.workspace.sources), tuple(self.workspace.pages)), self)
        self.search_bar.hit_selected.connect(self.show_search_hit)
        self.search_bar.cleared.connect(self.clear_search_hit)
        self.search_bar.closed.connect(self._rendering_closed)
        outer.addWidget(self.search_bar)

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
        self.empty_document = QLabel(); self.empty_document.setPixmap(icon("document", "muted").pixmap(QSize(42, 42))); welcome_layout.addWidget(self.empty_document)
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
        self.object_select_button = self._tool("Objekte auswählen und verschieben", "select", lambda: self.set_annotation_tool("select"))
        self.comment_button = self._tool("Kommentar: auf die Seite klicken", "comment", lambda: self.set_annotation_tool("comment"))
        self.highlight_button = self._tool("Text markieren: über PDF-Text ziehen", "highlight", lambda: self.set_annotation_tool("highlight"))
        self.signature_button = self._tool("Gezeichnete Unterschrift …", "signature", self.choose_signature)
        self.annotation_group = QButtonGroup(self); self.annotation_group.setExclusive(True)
        for button in (self.pan_button, self.object_select_button, self.comment_button, self.highlight_button, self.signature_button):
            button.setCheckable(True); self.annotation_group.addButton(button); annotation_bar.addWidget(button)
        self.insert_button = self._tool("Einfügen", "add", lambda: None, text=True)
        self.insert_button.setCheckable(True); self.annotation_group.addButton(self.insert_button)
        self.insert_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        insert_menu = QMenu(self.insert_button)
        self.object_actions = []
        for kind, label in (("text", "Textfeld …"), ("image", "Bild …"), ("stamp", "Stempel aus Bild …"), ("checkmark", "Häkchen"), ("cross", "Kreuz")):
            self.object_actions.append(self._action(insert_menu, label, None, lambda checked=False, value=kind: self.set_annotation_tool(value)))
        self.insert_button.setMenu(insert_menu); annotation_bar.addWidget(self.insert_button)
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
        self.preview.object_requested.connect(self.add_object)
        self.preview.annotation_updated.connect(self.update_annotation)
        self.preview.annotation_selected.connect(lambda *_: self.update_controls())
        self.preview.annotation_edit_requested.connect(self.edit_object)
        self.preview.annotation_action.connect(self.object_action)
        self.splitter.addWidget(self.preview_panel); self.splitter.setHandleWidth(18); self.splitter.setSizes([365, 810])
        left.setMinimumWidth(275); self.preview_panel.setMinimumWidth(350)
        self.workspace_stack = QStackedWidget()
        self.welcome_workspace = WelcomeWorkspace(self.open_dialog, self.import_images)
        self.empty_open_button = self.welcome_workspace.open_button
        self.workspace_stack.addWidget(self.welcome_workspace)
        self.workspace_stack.addWidget(self.splitter)
        outer.addWidget(self.workspace_stack, 1)

        footer = QFrame(); footer.setObjectName("footer")
        footer_layout = QHBoxLayout(footer); footer_layout.setContentsMargins(0, 10, 0, 0); footer_layout.setSpacing(8)
        status_layout = QVBoxLayout(); status_layout.setSpacing(4)
        self.status = StatusLabel("Bereit. Dateien bleiben auf diesem Gerät.")
        self.status.setWordWrap(False); self.status.setMaximumHeight(36)
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
        self.images_action = self._action(file_menu, "Bilder zu PDF-Seiten …", None, self.import_images, "document")
        self.new_action = self._action(file_menu, "Neuer Arbeitsbereich", QKeySequence.StandardKey.New, self.new_workspace, "new")
        file_menu.addSeparator()
        self.export_action = self._action(file_menu, "Alle Seiten als neue PDF speichern …", QKeySequence.StandardKey.SaveAs, lambda: self.save_pdf(False), "export")
        self.split_action = self._action(file_menu, "Auswahl als neue PDF speichern …", "Ctrl+Shift+E", lambda: self.save_pdf(True), "export")
        self.separate_action = self._action(file_menu, "Auswahl als einzelne PDFs speichern …", None, self.save_separate_pages, "separate")
        self.ocr_action = self._action(file_menu, "Durchsuchbare PDF erstellen (OCR) …", None, self.save_searchable_pdf, "document")
        self.compress_action = self._action(file_menu, "PDF komprimieren …", None, self.compress_pdf, "compress")
        self.protect_action = self._action(file_menu, "Mit Passwort geschützt speichern …", None, self.protect_pdf, "lock")
        file_menu.addSeparator(); self._action(file_menu, "Beenden", QKeySequence.StandardKey.Quit, self.close)
        edit = self.menuBar().addMenu("&Bearbeiten")
        self.undo_action = self._action(edit, "Rückgängig", QKeySequence.StandardKey.Undo, self.undo, "undo")
        self.redo_action = self._action(edit, "Wiederholen", QKeySequence.StandardKey.Redo, self.redo, "redo")
        edit.addSeparator()
        self.search_action = self._action(edit, "Text suchen …", QKeySequence.StandardKey.Find, self.search_bar.open, "zoom_in")
        self.select_action = self._action(edit, "Alle Seiten auswählen", QKeySequence.StandardKey.SelectAll, self.page_list.selectAll)
        self.range_action = self._action(edit, "Seitenbereich auswählen …", "Ctrl+G", self.choose_page_range, "range")
        self.duplicate_action = self._action(edit, "Auswahl duplizieren", "Ctrl+D", self.duplicate_selection, "duplicate")
        self.delete_action = self._action(edit, "Auswahl entfernen", "Delete", self.delete_selection, "delete")
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
        view_menu.addSeparator()
        appearance = view_menu.addMenu("Darstellung")
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        self.theme_actions = {}
        for mode, label in (("system", "System"), ("light", "Hell"), ("dark", "Dunkel")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, selected=mode: self.theme.set_preference(selected))
            self.theme_group.addAction(action); appearance.addAction(action)
            self.theme_actions[mode] = action
        self._sync_theme_actions()
        self.reduce_motion_action = QAction("Bewegung reduzieren", self)
        self.reduce_motion_action.setCheckable(True); self.reduce_motion_action.setChecked(self.motion.reduced)
        self.reduce_motion_action.setToolTip("Deaktiviert Übergänge und die Animation beim Start. Die Einstellung wird gespeichert.")
        self.reduce_motion_action.toggled.connect(self.motion.set_reduced)
        appearance.addSeparator(); appearance.addAction(self.reduce_motion_action)
        self.appearance_button.setMenu(appearance)
        annotate = self.menuBar().addMenu("&Anmerkungen")
        self.pan_action = self._action(annotate, "Hand: Seite verschieben", None, lambda: self.set_annotation_tool("pan"), "hand")
        self.comment_action = self._action(annotate, "Kommentar hinzufügen", None, lambda: self.set_annotation_tool("comment"), "comment")
        self.highlight_action = self._action(annotate, "Text markieren", None, lambda: self.set_annotation_tool("highlight"), "highlight")
        self.signature_action = self._action(annotate, "Gezeichnete Unterschrift …", None, self.choose_signature, "signature")
        self.object_select_action = self._action(annotate, "Objekte auswählen und verschieben", None, lambda: self.set_annotation_tool("select"), "select")
        self.edit_object_action = self._action(annotate, "Ausgewähltes Objekt bearbeiten …", None, self.edit_selected_object)
        self.duplicate_object_action = self._action(annotate, "Ausgewähltes Objekt duplizieren", None, lambda: self.object_action("duplicate"), "duplicate")
        self.delete_object_action = self._action(annotate, "Ausgewähltes Objekt löschen", None, lambda: self.object_action("delete"), "delete")
        annotate.addSeparator()
        self.annotations_action = self._action(annotate, "Anmerkungen bearbeiten oder löschen …", None, self.manage_annotations, "list")
        self.form_action = self._action(annotate, "Formular ausfüllen …", "Ctrl+Shift+F", self.fill_form, "form")
        help_menu = self.menuBar().addMenu("&Hilfe"); self._action(help_menu, "Über PDF Workbench", None, self.about)
        document_tools = QMenu(self.document_tools_button)
        document_tools.addAction(self.images_action); document_tools.addSeparator()
        document_tools.addAction(self.form_action); document_tools.addAction(self.ocr_action)
        document_tools.addSeparator(); document_tools.addAction(self.compress_action); document_tools.addAction(self.protect_action)
        self.document_tools_menu = document_tools
        self.document_tools_button.setMenu(document_tools)

    def _sync_theme_actions(self, *_):
        self.theme_actions[self.theme.preference].setChecked(True)

    def _theme_changed(self, *_):
        self.empty_document.setPixmap(icon("document", "muted").pixmap(QSize(42, 42)))
        self.page_list.viewport().update()
        self.preview.canvas.update()
        self.brand_mark.setPixmap(icon("document", "on_accent").pixmap(QSize(26, 26)))
        self.welcome_workspace.refresh_icons()

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
        self.workspace_stack.setCurrentIndex(1 if self.workspace.pages else 0)
        self.organize_toolbar.setVisible(bool(self.workspace.pages))
        self.selection_changed(); self.load_preview(); self.update_controls(); self._update_title()
        self.search_bar.workspace_changed(self.workspace.pages)
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
        self.page_details.setText(f"{source.label} · Originalseite {page.page_index + 1} · +{page.rotation}°" +
                                  (" · Entsperrt; normaler Export ohne Passwort" if source.was_encrypted else ""))
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
        for widget in (self.object_select_button, self.insert_button, self.object_select_action, *self.object_actions): widget.setEnabled(has_pages and not busy)
        for widget in (self.edit_object_action, self.duplicate_object_action, self.delete_object_action): widget.setEnabled(self.preview.selected_annotation is not None and not busy)
        self.images_action.setEnabled(not busy)
        self.search_action.setEnabled(has_pages and not self._closing_requested)
        self.ocr_action.setEnabled(has_pages and not busy and not self.preview.selection_pending)
        for action in (self.compress_action, self.protect_action):
            action.setEnabled(has_pages and not busy and not self.preview.selection_pending)
        source = self.workspace.sources[page.source_id] if page else None
        if source and source.source_id not in self._form_field_cache:
            self._form_field_cache[source.source_id] = bool(form_fields(source))
        self.form_action.setEnabled(bool(source and self._form_field_cache.get(source.source_id)) and not busy)
        self.welcome_workspace.images_button.setEnabled(not busy)
        self.document_tools_button.setEnabled(not busy)
        for button in (self.separate_button, self.split_button, self.export_button):
            button.setVisible(has_pages)

    def clear_search_hit(self):
        self.preview.search_hit = None
        self.preview.canvas.update()

    def show_search_hit(self, hit):
        if hit.page_uid not in self._page_by_uid: return
        if not self.preview_toggle.isChecked():
            self.preview_toggle.setChecked(True); self.toggle_preview()
        for row in range(self.page_list.count()):
            item = self.page_list.item(row)
            if item.data(UID_ROLE) == hit.page_uid:
                self.page_list.setCurrentItem(item, QItemSelectionModel.SelectionFlag.NoUpdate)
                break
        self.preview.search_hit = hit
        self.preview.reveal_search_hit()

    def set_annotation_tool(self, tool, strokes=()):
        if self.job: return
        if not self.preview_toggle.isChecked():
            self.preview_toggle.setChecked(True); self.toggle_preview()
        self.preview.set_tool(tool, strokes)
        self._tool_button(tool).setChecked(True)
        hints = {
            "pan": "Hand: Seite mit gedrückter Maustaste verschieben. Seitliches Mausrad oder Umschalt+Mausrad scrollt horizontal; Strg+Mausrad zoomt.",
            "comment": "Kommentar: auf die gewünschte Stelle klicken. Zum Verschieben der Ansicht jederzeit die mittlere Maustaste verwenden.",
            "highlight": "Textmarkierung: über vorhandenen PDF-Text ziehen. Gescannte Seiten ohne Textschicht können so nicht markiert werden.",
            "signature": "Gezeichnete Unterschrift: einmal klicken für Standardgröße oder einen Bereich für die gewünschte Größe ziehen.",
            "select": "Objekt anklicken und ziehen; an den Ecken die Größe ändern. Doppelklick bearbeitet, Esc bricht ab. Entf löscht nur das ausgewählte Objekt.",
        }
        self.set_status(hints.get(tool, "Auf die Seite klicken oder einen Bereich für das neue Objekt ziehen. Esc bricht die Platzierung ab."))

    def _tool_button(self, tool):
        return {"pan": self.pan_button, "select": self.object_select_button, "comment": self.comment_button, "highlight": self.highlight_button, "signature": self.signature_button}.get(tool, self.insert_button)

    def choose_signature(self):
        if self.job or not self.workspace.pages: return
        strokes = SignatureDialog.get_strokes(self)
        if strokes:
            self.set_annotation_tool("signature", strokes)
        else:
            self._tool_button(self.preview.tool).setChecked(True)

    def _object_dialog(self, page_uid, kind, rect=None, annotation=None):
        page = self._page_by_uid.get(page_uid)
        if not page: return None
        points, unit = source_page_geometry(self.workspace.sources[page.source_id], page)
        values = dict(rotation=page.rotation, point_size=points, user_unit=unit)
        if rect is not None: values['rect'] = rect
        if kind == 'text': return TextObjectDialog.get_annotation(self, annotation, **values)
        if kind in ('image', 'stamp'): return ImageObjectDialog.get_annotation(self, annotation, kind=kind, **values)
        if kind in ('checkmark', 'cross'): return SymbolObjectDialog.get_annotation(self, annotation, kind=kind, **values)

    def add_object(self, page_uid, kind, rect):
        if self.job: return
        try:
            annotation = self._object_dialog(page_uid, kind, rect)
            if annotation:
                self.add_annotation(page_uid, annotation)
                self.set_annotation_tool('select'); self.preview.set_selected_annotation(annotation.uid)
                self.preview.canvas.setFocus()
        except (ValueError, PdfWorkbenchError) as error: self.show_error('Objekt konnte nicht eingefügt werden.', str(error))

    def update_annotation(self, page_uid, annotation):
        if self.job or page_uid not in self._page_by_uid: return
        try:
            if annotation.kind == 'text':
                page = self._page_by_uid[page_uid]
                points, unit = source_page_geometry(self.workspace.sources[page.source_id], page)
                validate_text_object(annotation, points, page.rotation, unit)
            self.workspace.update_annotation(page_uid, annotation)
            self.refresh(active_uid=page_uid)
        except (ValueError, PdfWorkbenchError) as error: self.show_error('Objekt konnte nicht geändert werden.', str(error))

    def edit_object(self, page_uid, annotation):
        if self.job: return
        try:
            if annotation.kind in ('text', 'image', 'stamp', 'checkmark', 'cross'):
                updated = self._object_dialog(page_uid, annotation.kind, annotation=annotation)
            elif annotation.kind == 'signature':
                strokes = SignatureDialog.get_strokes(self)
                if not strokes: return
                rotation = self._page_by_uid[page_uid].rotation
                updated = replace(annotation, strokes=tuple(tuple(display_to_basis(point, rotation) for point in stroke) for stroke in strokes))
            else:
                text = CommentDialog.get_text(self, annotation.text, 'Anmerkung bearbeiten')
                updated = replace(annotation, text=text) if text is not None else None
            if updated: self.update_annotation(page_uid, updated)
        except (ValueError, PdfWorkbenchError) as error: self.show_error('Objekt konnte nicht bearbeitet werden.', str(error))

    def edit_selected_object(self):
        if self.preview.page and self.preview.selected_annotation:
            self.edit_object(self.preview.page.uid, self.preview.selected_annotation)

    def object_action(self, action):
        annotation, page = self.preview.selected_annotation, self.preview.page
        if self.job or not annotation or not page: return
        if action == 'edit': self.edit_object(page.uid, annotation)
        elif action == 'delete':
            self.workspace.remove_annotation(page.uid, annotation.uid); self.refresh(active_uid=page.uid)
        elif action == 'duplicate':
            left, top, right, bottom = annotation.rect
            dx, dy = min(.025, 1-right), min(.025, 1-bottom)
            shifted = tuple((a+dx,b+dy,c+dx,d+dy) for a,b,c,d in annotation.rects)
            copy = replace(annotation, uid=uuid.uuid4().hex, rect=(left+dx,top+dy,right+dx,bottom+dy), rects=shifted)
            self.add_annotation(page.uid, copy); self.preview.set_selected_annotation(copy.uid)

    def _text_input_has_focus(self):
        return isinstance(QApplication.focusWidget(), (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox))

    def duplicate_selection(self):
        if self._text_input_has_focus(): return
        if self.preview.canvas.hasFocus() and self.preview.selected_annotation: self.object_action('duplicate')
        elif not self.preview.canvas.hasFocus(): self.duplicate_pages()

    def delete_selection(self):
        if self._text_input_has_focus(): return
        if self.preview.canvas.hasFocus() and self.preview.selected_annotation: self.object_action('delete')
        elif not self.preview.canvas.hasFocus(): self.delete_pages()

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
        def edit(annotation): self.edit_object(uid, annotation)
        def remove(annotation):
            self.workspace.remove_annotation(uid, annotation.uid); self.refresh(active_uid=uid)
        AnnotationListDialog(self, annotations, edit, remove).exec()

    def selection_changed(self):
        count, sources = len(self.workspace.pages), len({p.source_id for p in self.workspace.pages})
        self.source_summary.setText((
            f"{count} {'Seite' if count == 1 else 'Seiten'} · "
            f"{sources} {'Datei' if sources == 1 else 'Dateien'}"
        ) if count else "Ihr Platz für gute Dokumente")
        current = self.page_list.currentRow() + 1
        self.summary.setText(f"{len(self.selected_ids())} ausgewählt" + (f" · Vorschau: Seite {current}" if current else " · Strg/Cmd oder Umschalt für Mehrfachauswahl"))
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
        files, _ = QFileDialog.getOpenFileNames(self, "PDFs hinzufügen", "", "PDF-Dateien (*.pdf);;Alle Dateien (*)", options=file_dialog_options())
        if files: self.import_files(files)

    def import_images(self, checked=False, files=()):
        if self.job: return
        request = ImagePagesDialog.get_request(self, files)
        if request:
            paths, options = request
            self.import_files(paths, options)

    def import_files(self, files, image_options=None, passwords=None):
        if self.job or not files: return
        paths = list(dict.fromkeys(str(Path(path)) for path in files))
        passwords = {} if passwords is None else dict(passwords)
        def task(cancel, progress):
            sources, errors, locked = [], [], []
            for index, path in enumerate(paths):
                if cancel(): raise OperationCancelled()
                progress(int(index / len(paths) * 100), f"{Path(path).name} öffnen …")
                try:
                    if Path(path).suffix.lower() in ('.png', '.jpg', '.jpeg'):
                        source = load_image_source(path, image_options, cancel)
                    else:
                        source = load_source(path, cancel, password=passwords.get(path))
                    source_page_geometry(source, PageRef('', source.source_id, 0))
                    sources.append(source)
                except OperationCancelled: raise
                except (PasswordRequiredError, IncorrectPasswordError) as error:
                    locked.append((path, isinstance(error, IncorrectPasswordError)))
                except Exception as error: errors.append(f"{Path(path).name}: {error}")
            passwords.clear()
            return sources, errors, locked
        def success(result):
            sources, errors, locked = result
            if sources:
                selected = self.workspace.add_sources(sources)
                self.refresh(selected, selected[0], ensure_visible=True)
            self.set_status(f"{len(sources)} Datei(en) hinzugefügt.")
            if any(source.was_encrypted for source in sources):
                self.set_status("Geschützte PDF geöffnet. Normaler Export ist unverschlüsselt; das Werkzeug „Mit Passwort schützen“ erstellt eine geschützte neue PDF.", "warning")
            self._pending_locked_files = locked
            if errors: self.show_error("Einige Dateien konnten nicht geöffnet werden.", "\n\n".join(errors))
        self.start_job(task, success)

    def _request_import_passwords(self):
        pending, self._pending_locked_files = self._pending_locked_files, []
        if self._closing_requested: return
        passwords = {}
        for path, incorrect in pending:
            password = PasswordDialog.get_password(self, source_name=Path(path).name, incorrect=incorrect)
            if password is not None: passwords[path] = password
        if passwords:
            self.import_files(list(passwords), passwords=passwords)
            passwords.clear()

    def set_status(self, text, tone="normal"):
        self.status.setText(text); self.status.setToolTip(text)
        semantic_label(self.status, tone)
        if not self.job: self.motion.feedback(self.status)

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_welcome_animated", False):
            self._welcome_animated = True
            self.motion.animate(self.welcome_workspace.artwork, "reveal", 0., 1., 460)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "source_summary"): return
        compact = self.width() < 1020
        self.welcome_workspace.adapt(self.height())
        self.source_summary.setVisible(not compact)
        for button in (self.fit_page_button, self.fit_width_button, self.annotations_button):
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if compact else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

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
        self.set_status(message, "warning")
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
        if self._pending_locked_files:
            QTimer.singleShot(0, self._request_import_passwords)

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
        if self._text_input_has_focus():
            widget = QApplication.focusWidget()
            if hasattr(widget, 'undo'): widget.undo()
            return
        if not self.job: self.workspace.undo(); self.refresh(ensure_visible=True)

    def redo(self):
        if self._text_input_has_focus():
            widget = QApplication.focusWidget()
            if hasattr(widget, 'redo'): widget.redo()
            return
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

    def save_pdf(self, selected_only, *, password=None, optimization=None):
        if self.job: return
        if self.preview.selection_pending:
            self.set_status("Bitte die laufende PDF-Textauswahl abwarten oder das Werkzeug wechseln."); return
        selected = self.selected_ids()
        pages = [page for page in self.workspace.pages if not selected_only or page.uid in selected]
        if not pages: return
        title = "Ausgewählte Seiten als neue PDF speichern" if selected_only else "Alle Seiten als neue PDF speichern"
        if password is not None: title = "Mit Passwort geschützte neue PDF speichern"
        elif any(self.workspace.sources[page.source_id].was_encrypted for page in pages): title = "Neue PDF ohne Passwortschutz speichern"
        filename, _ = QFileDialog.getSaveFileName(self, title, "auswahl.pdf" if selected_only else "zusammengefuegt.pdf", "PDF-Dateien (*.pdf)", options=QFileDialog.Option.DontConfirmOverwrite | file_dialog_options())
        if not filename: return
        path = Path(filename)
        if not path.suffix: path = path.with_suffix(".pdf")
        sources, snapshot = dict(self.workspace.sources), tuple(self.workspace.pages)
        reports = []
        export_options = {}
        if password is not None: export_options["password"] = password
        if optimization is not None:
            export_options.update(optimization=optimization, compression_report=reports.append)
        def task(cancel, progress): return export_pdf(sources, pages, path, cancel, progress, **export_options)
        def success(saved):
            if tuple(pages) == snapshot: self.saved_state = snapshot; self._update_title()
            detail = " · Mit AES-256 geschützt" if password is not None else ""
            if reports:
                report = reports[-1]
                detail += f" · {size_text(report.baseline_bytes)} → {size_text(report.output_bytes)} ({report.saved_percent:.1f} % kleiner)"
            self.set_status(f"{len(pages)} Seite(n) gespeichert: {saved}{detail}")
        self.start_job(task, success)

    def compress_pdf(self):
        if self.job or not self.workspace.pages or self.preview.selection_pending: return
        options = CompressionDialog.get_options(self, dict(self.workspace.sources), tuple(self.workspace.pages))
        if options is not None: self.save_pdf(False, optimization=options)

    def protect_pdf(self):
        if self.job or not self.workspace.pages or self.preview.selection_pending: return
        password = PasswordDialog.get_password(self)
        if password is not None: self.save_pdf(False, password=password)

    def fill_form(self):
        if self.job or self._active_uid not in self._page_by_uid: return
        page = self._page_by_uid[self._active_uid]
        source = self.workspace.sources[page.source_id]
        values = FormDialog.get_values(self, source)
        if not values: return
        selected, active = self.selected_ids(), self._active_uid
        def task(cancel, progress):
            if cancel(): raise OperationCancelled()
            result = fill_source_form(source, values)
            if cancel(): raise OperationCancelled()
            return result
        def success(updated):
            self.workspace.replace_source(source.source_id, updated)
            self.refresh(selected, active)
            self.set_status("Formular ausgefüllt. Mit „PDF speichern“ in eine neue Datei übernehmen. Rückgängig ist verfügbar.")
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

    def save_searchable_pdf(self):
        if self.job or not self.workspace.pages or self.preview.selection_pending: return
        request = OcrDialog.get_request(self, len(self.workspace.pages), len(self.selected_ids()))
        if request is None: return
        selected = self.selected_ids()
        pages = tuple(page for page in self.workspace.pages if request.scope == 'all' or page.uid in selected)
        sources = dict(self.workspace.sources)
        def task(cancel, progress):
            return export_searchable_pdf(sources, pages, request.destination, engine=request.engine,
                                         languages=request.languages, cache_dir=request.cache_dir,
                                         dpi=request.dpi, timeout=request.timeout, cancel=cancel, progress=progress)
        def success(result):
            self.set_status(f'Durchsuchbare PDF gespeichert: {result.path} · {result.processed_pages} Seite(n) erkannt, {result.skipped_pages} mit vorhandener Textschicht übersprungen.')
        self.start_job(task, success)

    def confirm_discard(self):
        if tuple(self.workspace.pages) == self.saved_state: return True
        return QMessageBox.question(self, "Ungespeicherte Änderungen verwerfen?", "Die aktuelle Seitenanordnung und ihre Anmerkungen wurden noch nicht vollständig als PDF gespeichert. Verwerfen?\n\nDie Originaldateien bleiben erhalten.", QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Discard

    def new_workspace(self):
        if not self.job and self.confirm_discard():
            self.workspace = Workspace(); self.saved_state = (); self._active_uid = None
            self._pending_locked_files = []; self._form_field_cache.clear()
            self.preview.set_tool("pan"); self.pan_button.setChecked(True)
            self.render_broker.invalidate(clear_cache=True); self.refresh()
            self.set_status("Neuer Arbeitsbereich. PDFs können hinzugefügt werden.")

    def closeEvent(self, event):
        if self.job:
            self.cancel_job(); self.set_status("Bitte den laufenden Abbruch abwarten und anschließend schließen."); event.ignore()
        elif self.render_broker.is_closed and self.search_bar.is_closed:
            event.accept()
        elif self._closing_requested:
            event.ignore()
        elif self.confirm_discard():
            self._closing_requested = True; self.update_controls(); self.render_broker.shutdown(); self.search_bar.shutdown(); event.ignore()
        else: event.ignore()

    def _rendering_closed(self):
        if self._closing_requested and self.render_broker.is_closed and self.search_bar.is_closed: self.close()

    def show_error(self, title, detail):
        self.set_status(title, "error")
        box = QMessageBox(QMessageBox.Icon.Warning, "PDF Workbench", title, QMessageBox.StandardButton.Ok, self)
        box.setInformativeText(detail[:1800])
        if len(detail) > 1800: box.setDetailedText(detail)
        box.exec()

    def about(self):
        QMessageBox.about(self, "Über PDF Workbench", f"<b>PDF Workbench {__version__}</b><br>Freie, lokale PDF-Werkzeuge.<br><br>Seiten ordnen, zusammenfügen, trennen und drehen.<br>Kommentare, Markierungen, Unterschriften, Textfelder,<br>Bilder und Stempel; Suche und Bilder als PDF-Seiten.<br>Komprimierung, Passwortschutz und normale AcroForms<br>mit Textfeldern, Kontrollkästchen und einfacher Auswahl.<br>Optionale lokale Texterkennung mit Tesseract.<br><br>GPL-3.0-or-later · PyQt6 / QtPdf · pypdf<br>Liberation Sans · SIL Open Font License<br>Gezeichnete Unterschriften sind sichtbare Zeichnungen.<br>XFA, Skripte und Zertifikatssignaturen sind nicht unterstützt.<br>Dokumentweite Lesezeichen, Tags und Anhänge werden<br>nicht erhalten; PDF/A-Konformität ist nicht garantiert.<br><br>Unabhängiges Projekt ohne Adobe-Programmcode.")

    def dragEnterEvent(self, event):
        if not self.job and event.mimeData().hasUrls(): event.acceptProposedAction()

    def dropEvent(self, event):
        if not self.job:
            self.import_files([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]); event.acceptProposedAction()


# Kept for existing source smoke launchers; normal startup uses ThemeController.
STYLE = stylesheet("light")


def main(argv=None):
    parser = argparse.ArgumentParser(description="PDF Workbench – lokale PDF-Seitenverwaltung")
    parser.add_argument("pdfs", nargs="*", help="PDF-Dateien beim Start hinzufügen")
    args = parser.parse_args(argv)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("PDF Workbench"); app.setApplicationVersion(__version__); app.setOrganizationName("PDF Workbench")
    app.setStyle("Fusion")
    theme_controller(app)
    window = MainWindow(); window.show()
    if args.pdfs: QTimer.singleShot(0, lambda: window.import_files(args.pdfs))
    return app.exec()
