"""Real Qt input delivery and annotation interaction regressions."""
from __future__ import annotations
from dataclasses import replace
import io
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
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PyQt6.QtGui import QMouseEvent, QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialogButtonBox
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from reportlab.pdfgen import canvas
from pdf_workbench.app import MainWindow
from pdf_workbench.annotations import PageAnnotation
from pdf_workbench.core import export_pdf, load_source
from pdf_workbench.ui.annotation_dialogs import AnnotationListDialog, CommentDialog, SignatureDialog
from pdf_workbench.ui.annotation_overlay import basis_to_display

APP = QApplication.instance() or QApplication([])


def wait_for(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while not predicate():
        APP.processEvents(); time.sleep(.005)
        if time.monotonic() > deadline: raise AssertionError("Qt annotation regression timed out")
    APP.processEvents()


class UiAnnotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        data = io.BytesIO(); pdf = canvas.Canvas(data, pagesize=(300, 420))
        pdf.setFont("Helvetica", 14); pdf.drawString(60, 340, "ALPHA beta"); pdf.save()
        reader, writer = PdfReader(data), PdfWriter()
        for rotation in (0, 90, 180, 270):
            writer.reset_translation(reader); page = writer.add_page(reader.pages[0])
            page.cropbox = RectangleObject([40, 50, 260, 380]); page.rotate(rotation)
        self.path = self.root / "source.pdf"; writer.write(self.path)
        self.original = self.path.read_bytes()
        self.window = MainWindow(); self.window.resize(1160, 780); self.window.show()
        self.assertTrue(QTest.qWaitForWindowExposed(self.window))
        self.ids = self.window.workspace.add_sources([load_source(self.path)])
        self.window.refresh([self.ids[0]], self.ids[0]); self.wait_render()

    def wait_render(self):
        wait_for(lambda: not self.window.preview.image.isNull()
                 and not self.window.preview._timer.isActive()
                 and self.window.render_broker.pending_count == 0)

    def tearDown(self):
        if self.window.job: wait_for(lambda: self.window.job is None)
        self.window.saved_state = tuple(self.window.workspace.pages); self.window.close()
        wait_for(lambda: self.window.render_broker.is_closed)
        self.assertEqual(self.path.read_bytes(), self.original); self.temp.cleanup()

    def wheel(self, angle=(0, 0), pixel=(0, 0), modifiers=Qt.KeyboardModifier.NoModifier):
        canvas = self.window.preview.canvas
        event = QWheelEvent(QPointF(80, 80), QPointF(canvas.mapToGlobal(QPoint(80, 80))), QPoint(*pixel), QPoint(*angle), Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(canvas, event); APP.processEvents()

    def mouse(self, kind, local, global_pos, button, buttons):
        QApplication.sendEvent(self.window.preview.canvas, QMouseEvent(kind, local, global_pos, button, buttons, Qt.KeyboardModifier.NoModifier))
        APP.processEvents()

    def test_horizontal_wheel_pixel_tilt_shift_diagonal_and_ctrl_zoom(self):
        preview = self.window.preview; preview.set_zoom(3); APP.processEvents()
        horizontal, vertical = preview.scroll.horizontalScrollBar(), preview.scroll.verticalScrollBar()
        def center():
            horizontal.setValue(horizontal.maximum() // 2); vertical.setValue(vertical.maximum() // 2)
            return horizontal.value(), vertical.value()
        x, y = center(); zoom = preview.zoom_factor; self.wheel(angle=(120, 0))
        self.assertLess(horizontal.value(), x); self.assertEqual(vertical.value(), y)
        x, y = center(); self.wheel(pixel=(17, 0)); self.assertEqual(horizontal.value(), x - 17); self.assertEqual(vertical.value(), y)
        x, y = center(); self.wheel(angle=(0, -120), modifiers=Qt.KeyboardModifier.ShiftModifier)
        self.assertGreater(horizontal.value(), x); self.assertEqual(vertical.value(), y)
        x, y = center(); self.wheel(pixel=(-13, -19))
        self.assertEqual((horizontal.value(), vertical.value()), (x + 13, y + 19))
        self.assertEqual(preview.zoom_factor, zoom)
        x, y = center()
        for _ in range(12): self.wheel(angle=(1, 0))
        expected = round(12 / 120 * QApplication.wheelScrollLines() * horizontal.singleStep())
        self.assertEqual(horizontal.value(), x - expected)
        self.assertEqual(vertical.value(), y)
        self.wheel(angle=(0, 120), modifiers=Qt.KeyboardModifier.ControlModifier)
        self.assertGreater(preview.zoom_factor, zoom)

    def test_middle_pan_uses_global_position_in_annotation_mode(self):
        preview = self.window.preview; preview.set_zoom(3); self.window.set_annotation_tool("comment"); APP.processEvents()
        horizontal, vertical = preview.scroll.horizontalScrollBar(), preview.scroll.verticalScrollBar()
        horizontal.setValue(horizontal.maximum() // 2); vertical.setValue(vertical.maximum() // 2)
        old = horizontal.value(), vertical.value()
        local = QPointF(100, 100); origin = QPointF(preview.canvas.mapToGlobal(local.toPoint()))
        self.mouse(QEvent.Type.MouseButtonPress, local, origin, Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton)
        for delta in (QPointF(20, 30), QPointF(35, 45)):
            # Local coordinates can change as the canvas itself scrolls.
            self.mouse(QEvent.Type.MouseMove, local + delta * 2, origin + delta, Qt.MouseButton.NoButton, Qt.MouseButton.MiddleButton)
            self.assertEqual((horizontal.value(), vertical.value()), (old[0] - round(delta.x()), old[1] - round(delta.y())))
        self.mouse(QEvent.Type.MouseButtonRelease, local, origin + QPointF(35, 45), Qt.MouseButton.MiddleButton, Qt.MouseButton.NoButton)
        self.assertIsNone(preview.canvas._pan); self.assertFalse(self.window.workspace.pages[0].annotations)

    def test_ctrl_wheel_preserves_zoom_direction_outside_normal_fit_limits(self):
        preview = self.window.preview
        preview.set_zoom(6, relative=True)
        self.wheel(angle=(0, 120), modifiers=Qt.KeyboardModifier.ControlModifier)
        self.assertGreaterEqual(preview.zoom_factor, 6)
        self.wheel(angle=(0, -120), modifiers=Qt.KeyboardModifier.ControlModifier)
        self.assertLess(preview.zoom_factor, 6)
        preview.points = (1e9, 1e9); preview.fit_page(); before = preview.zoom_factor
        self.wheel(angle=(0, -120), modifiers=Qt.KeyboardModifier.ControlModifier)
        self.assertLess(preview.zoom_factor, before)

    def test_zoom_during_annotation_drag_does_not_place_using_stale_canvas_points(self):
        preview = self.window.preview; self.window.set_annotation_tool("signature", (((.1, .2), (.9, .8)),))
        local = preview.basis_to_canvas((.4, .5)); origin = QPointF(preview.canvas.mapToGlobal(local.toPoint()))
        self.mouse(QEvent.Type.MouseButtonPress, local, origin, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        self.assertIsNotNone(preview.canvas._placement_start)
        self.wheel(angle=(0, 120), modifiers=Qt.KeyboardModifier.ControlModifier)
        self.mouse(QEvent.Type.MouseButtonRelease, local, origin, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
        self.assertFalse(self.window.workspace.pages[0].annotations)
        self.assertEqual(preview.tool, "signature")

    def test_comment_placement_inverse_mapping_with_crop_intrinsic_and_workspace_rotation(self):
        preview = self.window.preview
        for index in range(4):
            for rotation in (0, 90, 180, 270):
                uid = self.ids[index]
                self.window.workspace.pages[index] = replace(self.window.workspace.pages[index], rotation=rotation)
                self.window.refresh([uid], uid); wait_for(lambda: not preview.image.isNull())
                preview.set_zoom(.8); APP.processEvents()
                point = preview.basis_to_canvas((.3, .4))
                mapped = preview.canvas_to_basis(point)
                self.assertAlmostEqual(mapped[0], .3); self.assertAlmostEqual(mapped[1], .4)
                self.window.set_annotation_tool("comment")
                with patch("pdf_workbench.app.CommentDialog.get_text", return_value=f"Note {index}/{rotation}"):
                    preview.place_annotation(point, point)
                annotation = self.window.workspace.pages[index].annotations[-1]
                self.assertAlmostEqual(annotation.rect[0], .3); self.assertAlmostEqual(annotation.rect[1], .4)

    def test_signature_click_and_drag_remain_upright_in_current_rotation(self):
        preview = self.window.preview; uid = self.ids[0]
        for rotation in (0, 90, 180, 270):
            self.window.workspace.pages[0] = replace(self.window.workspace.pages[0], rotation=rotation)
            self.window.refresh([uid], uid); wait_for(lambda: not preview.image.isNull())
            self.window.set_annotation_tool("signature", (((.1, .2), (.9, .8)),))
            start = preview.basis_to_canvas((.5, .5)); preview.place_annotation(start, start)
            annotation = self.window.workspace.pages[0].annotations[-1]
            self.assertEqual(annotation.kind, "signature")
            for expected, actual in zip(((.1, .2), (.9, .8)), annotation.strokes[0]):
                transformed = basis_to_display(actual, rotation)
                self.assertAlmostEqual(transformed[0], expected[0]); self.assertAlmostEqual(transformed[1], expected[1])
            before = len(self.window.workspace.pages[0].annotations)
            paper = preview.paper_rect()
            preview.place_annotation(QPointF(paper.left() + 10, paper.top() + 10), QPointF(paper.left() + 100, paper.top() + 60))
            self.assertEqual(len(self.window.workspace.pages[0].annotations), before + 1)

    def test_actual_comment_click_uses_paper_position_after_rotation(self):
        expected = {0: (.2, .3), 90: (.3, .8), 180: (.8, .7), 270: (.7, .2)}
        uid = self.ids[0]; preview = self.window.preview
        for rotation in (0, 90, 180, 270):
            self.window.workspace.pages[0] = replace(self.window.workspace.pages[0], rotation=rotation)
            self.window.refresh([uid], uid); wait_for(lambda: not preview.image.isNull())
            self.window.set_annotation_tool("comment")
            paper = preview.paper_rect(); local = QPointF(paper.left() + .2 * paper.width(), paper.top() + .3 * paper.height())
            global_pos = QPointF(preview.canvas.mapToGlobal(local.toPoint()))
            with patch("pdf_workbench.app.CommentDialog.get_text", return_value="Actual mouse click"):
                self.mouse(QEvent.Type.MouseButtonPress, local, global_pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
                self.mouse(QEvent.Type.MouseButtonRelease, local, global_pos, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
            rect = self.window.workspace.pages[0].annotations[-1].rect
            self.assertAlmostEqual(rect[0], expected[rotation][0]); self.assertAlmostEqual(rect[1], expected[rotation][1])

    def test_real_text_highlight_and_stale_selection_are_page_bound(self):
        preview = self.window.preview; self.window.set_annotation_tool("highlight")
        start, end = preview.basis_to_canvas((19 / 220, 37 / 330)), preview.basis_to_canvas((101 / 220, 37 / 330))
        preview.place_annotation(start, end)
        wait_for(lambda: bool(self.window.workspace.pages[0].annotations))
        annotation = self.window.workspace.pages[0].annotations[0]
        self.assertEqual(annotation.text, "ALPHA beta"); self.assertEqual(len(annotation.rects), 1)
        preview.broker.dispatch_selection.disconnect(preview.broker.worker.select_text)
        preview.place_annotation(start, end); token = preview._selection_token
        self.window.refresh([self.ids[1]], self.ids[1])
        preview.broker.text_selected.emit(token, ((.1, .1, .5, .2),), "stale", None)
        self.assertFalse(self.window.workspace.pages[1].annotations)
        self.assertEqual(len(self.window.workspace.pages[0].annotations), 1)

    def test_empty_text_selection_reports_scan_limit_without_annotation(self):
        preview = self.window.preview; self.window.set_annotation_tool("highlight")
        preview._selection_token = "blank"; preview._selection_revision = preview._revision
        preview.broker.text_selected.emit("blank", (), "", None)
        self.assertIn("kein auswählbarer", self.window.status.text()); self.assertFalse(self.window.workspace.pages[0].annotations)

    def test_export_waits_for_pending_text_selection_instead_of_dropping_annotation(self):
        preview = self.window.preview; broker = preview.broker
        broker.dispatch_selection.disconnect(broker.worker.select_text)
        self.window.set_annotation_tool("highlight")
        preview.place_annotation(preview.basis_to_canvas((.1, .1)), preview.basis_to_canvas((.5, .2)))
        self.assertTrue(preview.selection_pending)
        self.assertFalse(self.window.export_button.isEnabled()); self.assertFalse(self.window.separate_button.isEnabled())
        with patch("pdf_workbench.app.QFileDialog.getSaveFileName") as dialog:
            self.window.save_pdf(False); dialog.assert_not_called()
        self.assertIsNone(self.window.job)
        token = preview._selection_token
        broker._selection_finished((broker.generation, token, ((.1, .1, .5, .2),), "Selected text", None))
        self.assertFalse(preview.selection_pending); self.assertTrue(self.window.export_button.isEnabled())
        self.assertEqual(self.window.workspace.pages[0].annotations[0].text, "Selected text")

    def test_overlay_edit_keeps_pan_and_zoom_and_undo(self):
        preview = self.window.preview; preview.set_zoom(3); APP.processEvents()
        horizontal, vertical = preview.scroll.horizontalScrollBar(), preview.scroll.verticalScrollBar()
        horizontal.setValue(60); vertical.setValue(90)
        annotation = PageAnnotation("note", "comment", (.2, .3, .26, .35), "Kommentar")
        self.window.add_annotation(self.ids[0], annotation)
        self.assertEqual((horizontal.value(), vertical.value(), preview.zoom_factor), (60, 90, 3))
        self.assertTrue(self.window.annotations_button.isEnabled())
        self.window.undo(); self.assertFalse(self.window.workspace.pages[0].annotations)
        self.window.redo(); self.assertEqual(self.window.workspace.pages[0].annotations, (annotation,))

    def test_annotation_is_visible_in_preview_and_thumbnail_without_changing_source_cache(self):
        preview = self.window.preview
        wait_for(lambda: self.window.render_broker.pending_count == 0)
        cached_images = {key: value[0].cacheKey() for key, value in self.window.render_broker.cache.items()}
        self.window.add_annotation(self.ids[0], PageAnnotation("visible-note", "comment", (.35, .4, .42, .46), "Visible note"))
        def gold_pixels(widget):
            image = widget.grab().toImage()
            return sum(image.pixelColor(x, y).name() == "#ffe6a1" for y in range(image.height()) for x in range(image.width()))
        wait_for(lambda: gold_pixels(preview.canvas) > 5 and gold_pixels(self.window.page_list.viewport()) > 1)
        for key, cache_key in cached_images.items():
            self.assertEqual(self.window.render_broker.cache[key][0].cacheKey(), cache_key)

    def test_dialog_cancel_clear_empty_validation_and_list_removal(self):
        comment = CommentDialog(self.window); self.assertFalse(comment.buttons.button(comment.buttons.StandardButton.Ok).isEnabled()); comment.reject()
        signature = SignatureDialog(self.window); signature.accept(); self.assertTrue(signature.validation.text())
        signature.pad.strokes = [[QPointF(5, 5), QPointF(20, 15)], [QPointF(2, 3), QPointF(2, 3)]]
        self.assertEqual(len(signature.pad.normalized_strokes()), 1); signature.pad.clear(); self.assertFalse(signature.pad.normalized_strokes()); signature.reject()
        note = PageAnnotation("list-note", "comment", (.1, .1, .2, .2), "Notiz"); self.window.add_annotation(self.ids[0], note)
        removed = []
        dialog = AnnotationListDialog(self.window, lambda: self.window.workspace.pages[0].annotations, lambda _: None, lambda annotation: (removed.append(annotation.uid), self.window.workspace.remove_annotation(self.ids[0], annotation.uid)))
        dialog.remove(); self.assertEqual(removed, [note.uid]); self.assertEqual(dialog.list.count(), 0); dialog.close()

    def test_gui_annotation_tools_export_real_pdf_annotations(self):
        preview = self.window.preview
        self.window.set_annotation_tool("highlight")
        preview.place_annotation(preview.basis_to_canvas((19 / 220, 37 / 330)), preview.basis_to_canvas((101 / 220, 37 / 330)))
        wait_for(lambda: len(self.window.workspace.pages[0].annotations) == 1)
        self.window.set_annotation_tool("comment")
        point = preview.basis_to_canvas((.7, .3))
        with patch("pdf_workbench.app.CommentDialog.get_text", return_value="Exportierter Kommentar"):
            preview.place_annotation(point, point)
        self.window.set_annotation_tool("signature", (((.1, .2), (.4, .9), (.9, .1)),))
        point = preview.basis_to_canvas((.3, .7)); preview.place_annotation(point, point)
        output = self.root / "ui-export.pdf"
        with patch("pdf_workbench.app.QFileDialog.getSaveFileName", return_value=(str(output), "PDF")):
            self.window.export_button.click(); wait_for(lambda: self.window.job is None)
        result = PdfReader(output)
        self.assertEqual(len(result.pages), 4)
        annotations = [item.get_object() for item in result.pages[0]["/Annots"]]
        self.assertEqual({item["/Subtype"] for item in annotations}, {"/Text", "/Highlight", "/Ink"})
        self.assertTrue(all("/AP" in item for item in annotations))
        self.assertEqual(next(item for item in annotations if item["/Subtype"] == "/Highlight")["/Contents"], "ALPHA beta")
        self.assertEqual(next(item for item in annotations if item["/Subtype"] == "/Text")["/Contents"], "Exportierter Kommentar")
        self.assertEqual(self.window.saved_state, tuple(self.window.workspace.pages))

    def test_gui_export_reopen_edit_delete_keeps_one_editable_copy_and_clean_preview(self):
        """Use the actual dialogs/jobs so saved appearances cannot hide in base bytes."""
        preview = self.window.preview
        # Fix the raster resolution independently of compositor/fit-page timing.
        preview.set_zoom(.8); self.wait_render()
        baseline = preview.image.copy()
        dialog_errors = []

        def queue_dialog(action):
            def run():
                try:
                    action()
                except BaseException as error:
                    dialog_errors.append(error)
                    dialog = QApplication.activeModalWidget()
                    if dialog is not None: dialog.reject()
            QTimer.singleShot(0, run)

        def check_dialogs():
            if dialog_errors: raise dialog_errors.pop(0)

        def drag(start, end):
            a, b = preview.basis_to_canvas(start).toPoint(), preview.basis_to_canvas(end).toPoint()
            QTest.mousePress(preview.canvas, Qt.MouseButton.LeftButton, pos=a)
            QTest.mouseMove(preview.canvas, b)
            QTest.mouseRelease(preview.canvas, Qt.MouseButton.LeftButton, pos=b)
            APP.processEvents()

        def fill_comment(text):
            dialog = QApplication.activeModalWidget()
            if not isinstance(dialog, CommentDialog):
                raise AssertionError("The real comment editor did not open")
            dialog.editor.setPlainText(text)
            QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)

        def save_as(path):
            with patch("pdf_workbench.app.QFileDialog.getSaveFileName", return_value=(str(path), "PDF")):
                self.window.export_button.click()
            wait_for(lambda: self.window.job is None)
            self.assertTrue(path.is_file(), self.window.status.text())

        def reopen(path):
            self.window.new_button.click()
            self.assertFalse(self.window.workspace.pages)
            with patch("pdf_workbench.app.QFileDialog.getOpenFileNames", return_value=([str(path)], "PDF")):
                self.window.open_button.click()
            wait_for(lambda: self.window.job is None)
            self.wait_render()
            self.assertEqual(len(self.window.workspace.pages), 4)
            # QtPdf must see the clean source, while our model supplies each overlay once.
            source = self.window.workspace.sources[self.window.workspace.pages[0].source_id]
            self.assertFalse(PdfReader(io.BytesIO(source.data)).pages[0].get("/Annots"))
            self.assertEqual(preview.image, baseline)

        self.window.highlight_button.click()
        drag((19 / 220, 37 / 330), (101 / 220, 37 / 330))
        wait_for(lambda: len(self.window.workspace.pages[0].annotations) == 1)
        self.window.comment_button.click()
        queue_dialog(lambda: fill_comment("Vor dem Wiederöffnen"))
        drag((.7, .3), (.7, .3))
        check_dialogs()
        self.assertEqual(len(self.window.workspace.pages[0].annotations), 2)

        def draw_signature():
            dialog = QApplication.activeModalWidget()
            if not isinstance(dialog, SignatureDialog):
                raise AssertionError("The real signature drawing dialog did not open")
            QTest.mousePress(dialog.pad, Qt.MouseButton.LeftButton, pos=QPoint(20, 50))
            QTest.mouseMove(dialog.pad, QPoint(80, 20))
            QTest.mouseMove(dialog.pad, QPoint(130, 80))
            QTest.mouseRelease(dialog.pad, Qt.MouseButton.LeftButton, pos=QPoint(130, 80))
            QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)

        queue_dialog(draw_signature)
        self.window.signature_button.click()
        check_dialogs()
        drag((.3, .7), (.65, .85))
        originals = tuple(self.window.workspace.pages[0].annotations)
        self.assertEqual({item.kind for item in originals}, {"highlight", "comment", "signature"})
        first = self.root / "editable-first.pdf"
        save_as(first)
        reopen(first)
        self.assertEqual(self.window.workspace.pages[0].annotations, originals)

        def edit_and_remove():
            dialog = QApplication.activeModalWidget()
            if not isinstance(dialog, AnnotationListDialog):
                raise AssertionError("The real annotation manager did not open")
            self.assertEqual(dialog.list.count(), 3)
            comment_row = next(i for i in range(dialog.list.count()) if dialog.list.item(i).data(Qt.ItemDataRole.UserRole).kind == "comment")
            dialog.list.setCurrentRow(comment_row)
            queue_dialog(lambda: fill_comment("Nach Wiederöffnen geändert – äöü"))
            QTest.mouseClick(dialog.edit_button, Qt.MouseButton.LeftButton)
            check_dialogs()
            highlight_row = next(i for i in range(dialog.list.count()) if dialog.list.item(i).data(Qt.ItemDataRole.UserRole).kind == "highlight")
            dialog.list.setCurrentRow(highlight_row)
            QTest.mouseClick(dialog.remove_button, Qt.MouseButton.LeftButton)
            self.assertEqual(dialog.list.count(), 2)
            dialog.accept()

        queue_dialog(edit_and_remove)
        self.window.annotations_button.click()
        check_dialogs()
        edited = tuple(self.window.workspace.pages[0].annotations)
        self.assertEqual({item.kind for item in edited}, {"comment", "signature"})
        self.assertEqual(next(item.text for item in edited if item.kind == "comment"), "Nach Wiederöffnen geändert – äöü")
        self.assertEqual(preview.image, baseline)
        second = self.root / "editable-second.pdf"
        save_as(second)
        second_annots = [ref.get_object() for ref in PdfReader(second).pages[0]["/Annots"]]
        self.assertEqual([item["/Subtype"] for item in second_annots], ["/Text", "/Ink"])
        reopen(second)
        self.assertEqual(self.window.workspace.pages[0].annotations, edited)

        def remove_all():
            dialog = QApplication.activeModalWidget()
            if not isinstance(dialog, AnnotationListDialog):
                raise AssertionError("The annotation manager did not reopen")
            while dialog.list.count():
                QTest.mouseClick(dialog.remove_button, Qt.MouseButton.LeftButton)
            self.assertFalse(dialog.remove_button.isEnabled())
            dialog.accept()

        queue_dialog(remove_all)
        self.window.annotations_button.click()
        check_dialogs()
        self.assertFalse(self.window.workspace.pages[0].annotations)
        self.assertFalse(self.window.annotations_button.isEnabled())
        self.assertEqual(preview.image, baseline)
        third = self.root / "editable-deleted.pdf"
        save_as(third)
        self.assertFalse(PdfReader(third).pages[0].get("/Annots"))
        # Export/reimport/edit/delete never mutates either earlier saved document.
        self.assertEqual(len(PdfReader(first).pages[0]["/Annots"]), 3)
        self.assertEqual(len(PdfReader(second).pages[0]["/Annots"]), 2)


if __name__ == "__main__": unittest.main(verbosity=2)
