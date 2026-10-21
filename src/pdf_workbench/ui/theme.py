"""Application colours and a persisted System/Light/Dark preference."""
from __future__ import annotations

from PyQt6.QtCore import QObject, QSettings, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPalette, QPixmapCache
from PyQt6.QtWidgets import QApplication, QFileDialog


COLORS = {
    "light": {
        "window": "#f5f5f2", "surface": "#ffffff", "canvas": "#e8ece9",
        "list": "#eeefeb", "text": "#233b38", "muted": "#51645e",
        "border": "#c9d3cd", "control_border": "#7d8d84", "divider": "#dce2dd",
        "hover": "#eaf2ed", "pressed": "#dcebe3", "selected": "#dcece4",
        "selection_border": "#7da997", "card_selected": "#e8f3ed", "card_hover": "#f6faf7",
        "accent": "#176552", "accent_hover": "#10513f", "focus": "#21745d",
        "on_accent": "#ffffff", "icon": "#3b554b", "disabled": "#7b8981",
        "disabled_surface": "#ecefeb", "disabled_border": "#d2dad3",
        "error": "#a52b2b", "warning": "#885600", "success": "#176647",
        "shadow": "#d5ddd6", "placeholder": "#f5f7f3", "scroll_handle": "#9bac9e",
    },
    "dark": {
        "window": "#181f1e", "surface": "#222e2b", "canvas": "#111a17",
        "list": "#1b2421", "text": "#edf3ee", "muted": "#b1c3b8",
        "border": "#455b50", "control_border": "#71897c", "divider": "#35473e",
        "hover": "#2d4037", "pressed": "#355145", "selected": "#2c473b",
        "selection_border": "#77b79d", "card_selected": "#294337", "card_hover": "#2a3a31",
        "accent": "#8ed7b7", "accent_hover": "#a3e6c8", "focus": "#8ed7b7",
        "on_accent": "#17372b", "icon": "#d2e2d7", "disabled": "#80998b",
        "disabled_surface": "#26342c", "disabled_border": "#3c5145",
        "error": "#ff9f9f", "warning": "#efc77b", "success": "#8bd6af",
        "shadow": "#0c1410", "placeholder": "#2a3930", "scroll_handle": "#647e6d",
    },
}


def stylesheet(name: str) -> str:
    c = COLORS[name]
    return """
QMainWindow, QWidget { background: %(window)s; color: %(text)s; }
QWidget { font-size: 13px; }
QLabel#title { font-size: 21px; font-weight: 650; margin-right: 10px; }
QLabel#brandMark { background: %(accent)s; border-radius: 12px; padding: 7px; }
QLabel#eyebrow { color: %(muted)s; font-size: 11px; letter-spacing: 2px; }
QLabel#welcomeTitle { font-size: 34px; font-weight: 650; }
QLabel#welcomeHint { font-size: 14px; color: %(muted)s; line-height: 1.5; }
QFrame#welcomeWorkspace { background: %(window)s; border: none; }
QFrame#featureCard { background: %(surface)s; border: 1px solid %(divider)s; border-radius: 12px; }
QFrame#featureCard QLabel { background: transparent; }
QLabel#featureTitle { font-size: 14px; font-weight: 600; }
QLabel#sectionTitle { font-size: 15px; font-weight: 600; }
QLabel#emptyTitle { font-size: 20px; font-weight: 600; }
QLabel#muted, QLabel#toolCaption { color: %(muted)s; font-size: 12px; }
QLabel#toolCaption { margin-left: 3px; margin-right: 5px; }
QFrame#toolbar { background: %(surface)s; border: 1px solid %(divider)s; border-radius: 10px; }
QFrame#toolbar QLabel { background: transparent; }
QFrame#separator { color: %(divider)s; }
QFrame#footer { border-top: 1px solid %(divider)s; }
QPushButton { background: %(surface)s; border: 1px solid %(control_border)s; border-radius: 7px; padding: 6px 12px; }
QPushButton:hover { background: %(hover)s; border-color: %(selection_border)s; }
QPushButton:pressed { background: %(pressed)s; }
QPushButton:focus { border: 2px solid %(focus)s; padding: 5px 11px; }
QPushButton:disabled { background: %(disabled_surface)s; color: %(disabled)s; border-color: %(disabled_border)s; }
QPushButton#primary { color: %(on_accent)s; background: %(accent)s; border-color: %(accent)s; }
QPushButton#primary:hover { background: %(accent_hover)s; }
QPushButton#primary:focus { border: 2px solid %(on_accent)s; }
QPushButton#primary:disabled { background: %(disabled_surface)s; color: %(disabled)s; border-color: %(disabled_border)s; }
QToolButton { border: 1px solid transparent; border-radius: 7px; padding: 5px; background: transparent; }
QToolButton:enabled:hover { background: %(hover)s; border-color: %(border)s; }
QToolButton:checked { background: %(selected)s; border-color: %(selection_border)s; }
QToolButton:focus { border: 2px solid %(focus)s; padding: 4px; }
QToolButton:disabled { color: %(disabled)s; }
QListWidget, QTreeView { background: %(list)s; border: 1px solid %(divider)s; border-radius: 10px; padding: 6px; selection-background-color: %(selected)s; selection-color: %(text)s; }
QListWidget:focus { border: 2px solid %(selection_border)s; padding: 5px; }
QListWidget::item:selected, QTreeView::item:selected { background: %(selected)s; color: %(text)s; }
QFrame#welcome { background: %(surface)s; border: 1px dashed %(control_border)s; border-radius: 6px; }
QFrame#welcome QLabel { background: transparent; border: none; }
QComboBox, QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox { background: %(surface)s; color: %(text)s; border: 1px solid %(control_border)s; padding: 6px; border-radius: 4px; selection-background-color: %(selected)s; selection-color: %(text)s; }
QComboBox:focus, QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 2px solid %(focus)s; padding: 5px; }
QComboBox:disabled, QLineEdit:disabled, QTextEdit:disabled { background: %(disabled_surface)s; color: %(disabled)s; }
QComboBox QAbstractItemView { background: %(surface)s; color: %(text)s; selection-background-color: %(selected)s; selection-color: %(text)s; }
QProgressBar { background: %(pressed)s; border: none; border-radius: 2px; }
QProgressBar::chunk { background: %(accent)s; }
QMenuBar, QMenu { background: %(surface)s; color: %(text)s; }
QMenuBar::item:selected, QMenu::item:selected { background: %(selected)s; color: %(text)s; }
QMenu::item { padding: 8px 25px 8px 10px; }
QMenu::item:disabled { color: %(disabled)s; }
QMenu::separator { height: 1px; background: %(divider)s; margin: 4px 8px; }
QToolTip { background: %(surface)s; color: %(text)s; border: 1px solid %(control_border)s; padding: 5px; }
QSplitter::handle { background: %(window)s; }
QScrollBar:vertical { background: %(list)s; width: 14px; margin: 0; }
QScrollBar:horizontal { background: %(list)s; height: 14px; margin: 0; }
QScrollBar::handle { background: %(scroll_handle)s; border: 3px solid %(list)s; border-radius: 6px; min-width: 24px; min-height: 24px; }
QScrollBar::handle:hover { background: %(muted)s; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QLabel[tone="muted"] { color: %(muted)s; }
QLabel[tone="error"], QLabel#muted[tone="error"] { color: %(error)s; }
QLabel[tone="warning"] { color: %(warning)s; }
QLabel[tone="success"] { color: %(success)s; }
QLabel[tone="normal"] { color: %(text)s; }
""" % c


def make_palette(name: str) -> QPalette:
    c = COLORS[name]
    palette = QPalette()
    roles = {
        "Window": "window", "WindowText": "text", "Base": "surface",
        "AlternateBase": "list", "Text": "text", "Button": "surface",
        "ButtonText": "text", "Highlight": "selected", "HighlightedText": "text",
        "ToolTipBase": "surface", "ToolTipText": "text", "PlaceholderText": "muted",
        "Link": "accent", "LinkVisited": "accent", "Light": "control_border",
        "Midlight": "border", "Mid": "divider", "Dark": "shadow", "Shadow": "shadow",
        "BrightText": "on_accent", "Accent": "accent",
    }
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        for role, token in roles.items():
            palette.setColor(group, getattr(QPalette.ColorRole, role), QColor(c[token]))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["disabled"]))
    return palette


class ThemeController(QObject):
    changed = pyqtSignal(str)
    preference_changed = pyqtSignal(str)
    KEY = "appearance/theme"

    def __init__(self, app: QApplication, settings=None, style_hints=None):
        super().__init__(app)
        self.app = app
        app._pdf_workbench_theme = self
        self.settings = settings if settings is not None else QSettings("PDF Workbench", "PDF Workbench")
        self.style_hints = style_hints if style_hints is not None else app.styleHints()
        stored = self.settings.value(self.KEY, "system")
        self.preference = stored if stored in ("system", "light", "dark") else "system"
        self.effective_name = None
        self.style_hints.colorSchemeChanged.connect(self._system_changed)
        self.apply()

    def resolved_name(self):
        if self.preference != "system":
            return self.preference
        return "dark" if self.style_hints.colorScheme() == Qt.ColorScheme.Dark else "light"

    def set_preference(self, preference: str):
        if preference not in ("system", "light", "dark"):
            raise ValueError("Unbekannte Darstellung.")
        if preference == self.preference:
            return
        self.preference = preference
        self.settings.setValue(self.KEY, preference)
        self.settings.sync()
        self.apply()
        self.preference_changed.emit(preference)

    def _system_changed(self, *_):
        if self.preference == "system":
            self.apply()

    def apply(self):
        name = self.resolved_name()
        self.effective_name = name
        # Keep Python-owned widgets alive throughout the global repolish.
        widgets = self.app.allWidgets()
        self.app.setPalette(make_palette(name))
        self.app.setStyleSheet(stylesheet(name))
        QPixmapCache.clear()
        for widget in widgets:
            widget.update()
        self.changed.emit(name)


def theme_controller(app=None) -> ThemeController:
    app = app or QApplication.instance()
    if app is None:
        raise RuntimeError("Die Darstellung benötigt eine QApplication.")
    controller = getattr(app, "_pdf_workbench_theme", None)
    if controller is None:
        controller = ThemeController(app)
        app._pdf_workbench_theme = controller
    return controller


def color(token: str) -> QColor:
    app = QApplication.instance()
    controller = getattr(app, "_pdf_workbench_theme", None) if app else None
    name = controller.effective_name if controller else "light"
    return QColor(COLORS[name][token])


def semantic_label(label, tone="normal"):
    label.setProperty("tone", tone)
    label.style().unpolish(label)
    label.style().polish(label)
    label.update()


def file_dialog_options():
    # Explicit app themes must also apply when the OS uses the other scheme.
    return (QFileDialog.Option.DontUseNativeDialog if theme_controller().preference != "system"
            else QFileDialog.Option(0))
