"""Theme changes preserve documents and stay consistent across existing widgets."""
from __future__ import annotations

from collections import Counter
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QSettings, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPalette, QPixmapCache
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
from pdf_workbench.app import MainWindow
from pdf_workbench.annotations import PageAnnotation
from pdf_workbench.core import load_source
from pdf_workbench.ui.annotation_dialogs import AnnotationListDialog, CommentDialog, SignatureDialog
from pdf_workbench.ui.dialogs import PageRangeDialog, SeparateExportDialog
from pdf_workbench.ui.icons import icon
from pdf_workbench.ui.theme import COLORS, ThemeController, color, file_dialog_options
from test_core import make_pdf

APP = QApplication.instance() or QApplication([])


def wait_for(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while not predicate():
        APP.processEvents(); time.sleep(.005)
        if time.monotonic() > deadline:
            raise AssertionError("Theme regression timed out")
    APP.processEvents()


class SystemHints(QObject):
    colorSchemeChanged = pyqtSignal(object)

    def __init__(self):
        super().__init__(); self.scheme = Qt.ColorScheme.Light

    def colorScheme(self):
        return self.scheme

    def change(self, scheme):
        self.scheme = scheme; self.colorSchemeChanged.emit(scheme)


class ThemeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings_path = self.root / "appearance.ini"
        self.settings = QSettings(str(self.settings_path), QSettings.Format.IniFormat)
        self.hints = SystemHints()
        self.previous_controller = getattr(APP, "_pdf_workbench_theme", None)
        self.previous_palette, self.previous_style = QPalette(APP.palette()), APP.styleSheet()
        self.controller = ThemeController(APP, self.settings, self.hints)
        self.windows, self.dialogs = [], []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
        for window in self.windows:
            if window.job: wait_for(lambda: window.job is None)
            window.saved_state = tuple(window.workspace.pages); window.close()
            wait_for(lambda: window.render_broker.is_closed)
        for dialog in self.dialogs:
            dialog.deleteLater()
        for window in self.windows:
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        self.hints.colorSchemeChanged.disconnect(self.controller._system_changed)
        self.controller.deleteLater()
        if self.previous_controller is None:
            del APP._pdf_workbench_theme
        else:
            APP._pdf_workbench_theme = self.previous_controller
        APP.setPalette(self.previous_palette); APP.setStyleSheet(self.previous_style)
        QPixmapCache.clear(); APP.processEvents()
        self.temp.cleanup()

    def restart_controller(self):
        self.hints.colorSchemeChanged.disconnect(self.controller._system_changed)
        self.controller.deleteLater()
        settings = QSettings(str(self.settings_path), QSettings.Format.IniFormat)
        self.controller = ThemeController(APP, settings, self.hints)

    def window(self):
        window = MainWindow(); self.windows.append(window)
        window.resize(1160, 780); window.show()
        self.assertTrue(QTest.qWaitForWindowExposed(window))
        return window

    def test_saved_choice_is_restored_before_the_first_window_is_shown(self):
        self.controller.set_preference("dark")
        persisted = QSettings(str(self.settings_path), QSettings.Format.IniFormat)
        self.assertEqual(persisted.value(ThemeController.KEY), "dark")
        self.restart_controller()
        window = MainWindow(); self.windows.append(window)
        self.assertFalse(window.isVisible())
        self.assertEqual(window.theme.effective_name, "dark")
        self.assertTrue(window.theme_actions["dark"].isChecked())
        self.assertEqual(window.palette().color(QPalette.ColorRole.Window).name(), COLORS["dark"]["window"])

    def test_system_changes_apply_live_but_manual_choices_remain_stable(self):
        window = self.window()
        self.assertEqual(self.controller.preference, "system")
        self.hints.change(Qt.ColorScheme.Dark)
        self.assertEqual(self.controller.effective_name, "dark")
        self.assertTrue(window.theme_actions["system"].isChecked())
        self.assertFalse(self.settings.contains(ThemeController.KEY))
        window.theme_actions["light"].trigger()
        self.hints.change(Qt.ColorScheme.Dark)
        self.assertEqual(self.controller.effective_name, "light")
        window.theme_actions["dark"].trigger()
        self.hints.change(Qt.ColorScheme.Light)
        self.assertEqual(self.controller.effective_name, "dark")
        window.theme_actions["system"].trigger()
        self.assertEqual(self.controller.effective_name, "light")
        self.assertEqual(self.settings.value(ThemeController.KEY), "system")

    def test_unknown_stored_choice_and_unknown_system_scheme_have_a_safe_default(self):
        self.settings.setValue(ThemeController.KEY, "unsupported-theme"); self.settings.sync()
        self.hints.scheme = Qt.ColorScheme.Unknown
        self.restart_controller()
        self.assertEqual((self.controller.preference, self.controller.effective_name), ("system", "light"))
        with self.assertRaises(ValueError):
            self.controller.set_preference("unsupported-theme")
        self.assertEqual(self.controller.preference, "system")

    def test_live_switch_preserves_document_history_zoom_pan_and_cached_pdf_pixels(self):
        path = self.root / "original.pdf"; make_pdf(path, ("First", "Second"))
        original = path.read_bytes()
        window = self.window()
        ids = window.workspace.add_sources([load_source(path)])
        window.workspace.add_annotation(ids[0], PageAnnotation("note", "comment", (.2, .3, .25, .35), "Keep this note"))
        window.refresh([ids[0]], ids[0])
        preview = window.preview
        wait_for(lambda: not preview.image.isNull() and not preview._timer.isActive() and window.render_broker.pending_count == 0)
        preview.set_zoom(2.5)
        wait_for(lambda: not preview._timer.isActive() and window.render_broker.pending_count == 0)
        x, y = preview.scroll.horizontalScrollBar(), preview.scroll.verticalScrollBar()
        x.setValue(min(65, x.maximum())); y.setValue(min(95, y.maximum()))
        snapshot = (tuple(window.workspace.pages), tuple(window.workspace._undo), tuple(window.workspace._redo), window.saved_state,
                    window.selected_ids(), window._active_uid, preview.zoom_factor, preview.zoom_mode,
                    x.value(), y.value(), preview._revision, window.render_broker.generation)
        raster = preview.image.copy(); source_data = {uid: source.data for uid, source in window.workspace.sources.items()}
        for mode in ("dark", "light", "system"):
            window.theme_actions[mode].trigger(); APP.processEvents(); QTest.qWait(110)
            self.assertEqual(snapshot, (tuple(window.workspace.pages), tuple(window.workspace._undo), tuple(window.workspace._redo), window.saved_state,
                             window.selected_ids(), window._active_uid, preview.zoom_factor, preview.zoom_mode,
                             x.value(), y.value(), preview._revision, window.render_broker.generation))
            self.assertEqual(preview.image, raster)
            self.assertEqual({uid: source.data for uid, source in window.workspace.sources.items()}, source_data)
        self.assertEqual(path.read_bytes(), original)

    def test_existing_icons_and_primary_disabled_states_repaint_with_the_theme(self):
        def foreground(image):
            return Counter(image.pixelColor(x, y).name() for y in range(image.height()) for x in range(image.width())
                           if image.pixelColor(x, y).alpha() > 220).most_common(1)[0][0]
        ordinary, primary = icon("add"), icon("export", "on_accent")
        light = ordinary.pixmap(QSize(24, 24)).toImage()
        self.controller.set_preference("dark")
        self.assertEqual(foreground(ordinary.pixmap(QSize(24, 24)).toImage()), COLORS["dark"]["icon"])
        self.assertNotEqual(ordinary.pixmap(QSize(24, 24)).toImage(), light)
        self.assertEqual(foreground(primary.pixmap(QSize(24, 24)).toImage()), COLORS["dark"]["on_accent"])
        self.assertEqual(foreground(ordinary.pixmap(QSize(24, 24), QIcon.Mode.Disabled).toImage()), COLORS["dark"]["disabled"])

    def test_open_dialogs_errors_and_status_recolour_without_changing_document_ink(self):
        window = self.window()
        page_range = PageRangeDialog(window, 3); page_range.range_edit.setText("99")
        separate = SeparateExportDialog(window, 2); separate.folder_edit.setText("..")
        comment = CommentDialog(window)
        signature = SignatureDialog(window); signature.accept()
        annotations = AnnotationListDialog(window, lambda: (), lambda _: None, lambda _: None)
        message = QMessageBox(QMessageBox.Icon.Warning, "Hinweis", "Ein Beispielhinweis", QMessageBox.StandardButton.Ok, window)
        self.dialogs.extend((page_range, separate, comment, signature, annotations, message))
        for dialog in self.dialogs: dialog.show()
        APP.processEvents()
        pad = signature.pad.grab().toImage()
        window.set_status("Beispiel einer Fehlermeldung", "error")
        self.controller.set_preference("dark"); APP.processEvents()
        for dialog in self.dialogs:
            self.assertEqual(dialog.palette().color(QPalette.ColorRole.Window).name(), COLORS["dark"]["window"])
        for label in (page_range.error_label, separate.error_label, signature.validation, window.status):
            self.assertEqual(label.palette().color(QPalette.ColorRole.WindowText).name(), COLORS["dark"]["error"])
        self.assertEqual(comment.editor.palette().color(QPalette.ColorRole.Base).name(), COLORS["dark"]["surface"])
        self.assertEqual(signature.pad.grab().toImage(), pad)
        window.set_status("Bereit.")
        self.assertEqual(window.status.property("tone"), "normal")

    def test_explicit_themes_use_app_styled_file_dialogs_and_keep_overwrite_protection(self):
        window = self.window()
        self.assertFalse(file_dialog_options() & QFileDialog.Option.DontUseNativeDialog)
        self.controller.set_preference("dark")
        self.assertTrue(file_dialog_options() & QFileDialog.Option.DontUseNativeDialog)
        with patch("pdf_workbench.app.QFileDialog.getOpenFileNames", return_value=([], "")) as opener:
            window.open_dialog()
        self.assertTrue(opener.call_args.kwargs["options"] & QFileDialog.Option.DontUseNativeDialog)
        path = self.root / "source.pdf"; make_pdf(path, ("Source",))
        ids = window.workspace.add_sources([load_source(path)]); window.refresh(ids, ids[0])
        with patch("pdf_workbench.app.QFileDialog.getSaveFileName", return_value=("", "")) as saver:
            window.save_pdf(False)
        self.assertTrue(saver.call_args.kwargs["options"] & QFileDialog.Option.DontUseNativeDialog)
        self.assertTrue(saver.call_args.kwargs["options"] & QFileDialog.Option.DontConfirmOverwrite)

    def test_both_palettes_keep_text_and_meaningful_icons_legible(self):
        def luminance(hex_color):
            rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            channels = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in rgb]
            return sum(weight * value for weight, value in zip((.2126, .7152, .0722), channels))
        def contrast(a, b):
            first, second = sorted((luminance(a), luminance(b)))
            return (second + .05) / (first + .05)
        for name, colors in COLORS.items():
            for foreground in ("text", "muted", "error", "warning", "success"):
                for background in ("window", "surface", "list", "selected"):
                    with self.subTest(theme=name, foreground=foreground, background=background):
                        self.assertGreaterEqual(contrast(colors[foreground], colors[background]), 4.5)
            self.assertGreaterEqual(contrast(colors["on_accent"], colors["accent"]), 4.5)
            self.assertGreaterEqual(contrast(colors["icon"], colors["surface"]), 3)
            self.assertGreaterEqual(contrast(colors["focus"], colors["surface"]), 3)
            self.assertGreaterEqual(contrast(colors["control_border"], colors["surface"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
