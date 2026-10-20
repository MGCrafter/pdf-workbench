"""Application colours and a persisted System/Light/Dark preference."""
from __future__ import annotations

from PyQt6.QtCore import QObject, QSettings, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPalette, QPixmapCache
from PyQt6.QtWidgets import QApplication, QFileDialog


COLORS = {
    "light": {
        "window": "#f4f6f9", "surface": "#ffffff", "canvas": "#e8edf3",
        "list": "#edf1f6", "text": "#17243b", "muted": "#526278",
        "border": "#c7d1df", "control_border": "#7a8ca6", "divider": "#d6dde6",
        "hover": "#e9eff8", "pressed": "#dae5f7", "selected": "#dde8fc",
        "selection_border": "#9cb5e2", "card_selected": "#e9f0ff", "card_hover": "#f7f9fc",
        "accent": "#285cc4", "accent_hover": "#204da7", "focus": "#2f5fc5",
        "on_accent": "#ffffff", "icon": "#334155", "disabled": "#78869a",
        "disabled_surface": "#eef1f5", "disabled_border": "#d0d7e2",
        "error": "#a52b2b", "warning": "#885600", "success": "#176647",
        "shadow": "#cbd4e0", "placeholder": "#f5f7fa", "scroll_handle": "#9aabc2",
    },
    "dark": {
        "window": "#191d24", "surface": "#242a34", "canvas": "#141820",
        "list": "#171c24", "text": "#edf1f7", "muted": "#acb9cc",
        "border": "#465268", "control_border": "#6e809b", "divider": "#3e495c",
        "hover": "#303b4d", "pressed": "#394a64", "selected": "#2d4264",
        "selection_border": "#7da6e8", "card_selected": "#293e5d", "card_hover": "#2c3543",
        "accent": "#78a9f9", "accent_hover": "#98bfff", "focus": "#8db8ff",
        "on_accent": "#10213d", "icon": "#d1dded", "disabled": "#8190a5",
        "disabled_surface": "#272e39", "disabled_border": "#3e495a",
        "error": "#ff9f9f", "warning": "#efc77b", "success": "#8bd6af",
        "shadow": "#090c12", "placeholder": "#29313e", "scroll_handle": "#687b98",
    },
}


def stylesheet(name: str) -> str:
    c = COLORS[name]
    return """
QMainWindow, QWidget { background: %(window)s; color: %(text)s; }
QWidget { font-size: 13px; }
QLabel#title { font-size: 20px; font-weight: 650; margin-right: 10px; }
QLabel#sectionTitle { font-size: 15px; font-weight: 600; }
QLabel#emptyTitle { font-size: 20px; font-weight: 600; }
QLabel#muted, QLabel#toolCaption { color: %(muted)s; font-size: 12px; }
QLabel#toolCaption { margin-left: 3px; margin-right: 5px; }
QFrame#toolbar { background: %(surface)s; border: 1px solid %(divider)s; border-radius: 6px; }
QFrame#toolbar QLabel { background: transparent; }
QFrame#separator { color: %(divider)s; }
QFrame#footer { border-top: 1px solid %(divider)s; }
QPushButton { background: %(surface)s; border: 1px solid %(control_border)s; border-radius: 5px; padding: 5px 11px; }
QPushButton:hover { background: %(hover)s; border-color: %(selection_border)s; }
QPushButton:pressed { background: %(pressed)s; }
QPushButton:focus { border: 2px solid %(focus)s; padding: 4px 10px; }
QPushButton:disabled { background: %(disabled_surface)s; color: %(disabled)s; border-color: %(disabled_border)s; }
QPushButton#primary { color: %(on_accent)s; background: %(accent)s; border-color: %(accent)s; }
QPushButton#primary:hover { background: %(accent_hover)s; }
QPushButton#primary:focus { border: 2px solid %(on_accent)s; }
QPushButton#primary:disabled { background: %(disabled_surface)s; color: %(disabled)s; border-color: %(disabled_border)s; }
QToolButton { border: 1px solid transparent; border-radius: 4px; padding: 4px; background: transparent; }
QToolButton:enabled:hover { background: %(hover)s; border-color: %(border)s; }
QToolButton:checked { background: %(selected)s; border-color: %(selection_border)s; }
QToolButton:focus { border: 2px solid %(focus)s; padding: 3px; }
QToolButton:disabled { color: %(disabled)s; }
QListWidget, QTreeView { background: %(list)s; border: 1px solid %(border)s; border-radius: 5px; padding: 4px; selection-background-color: %(selected)s; selection-color: %(text)s; }
QListWidget:focus { border: 2px solid %(selection_border)s; padding: 3px; }
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
