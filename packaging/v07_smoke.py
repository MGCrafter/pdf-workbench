"""Version 0.7 acceptance using real PDFs and the shipped native dialogs."""
from dataclasses import replace
import io
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from pdf_workbench.core import OperationCancelled, Workspace, export_pdf, load_source
from pdf_workbench.comparison import compare_documents
from pdf_workbench.form_design import FieldPlacement, FormDesign, apply_form_designs
from pdf_workbench.forms import fill_source_form, form_fields
from pdf_workbench.page_marks import (PageMarksOptions, PageMarksRequest, PageNumberOptions,
                                     WatermarkOptions, prepare_page_marks)


def acceptance_class(base, fixture):
    class V07Acceptance(base):
        @classmethod
        def setUpClass(cls):
            cls.qt_app = QApplication.instance() or QApplication([])

        def test_document_comparison_runtime(self):
            fixture(self.b, ('INTRO', 'A1', 'A2 updated', 'A3'))
            left, right = load_source(self.a), load_source(self.b)
            result = compare_documents(left, right)
            self.assertEqual(result.count('added'), 1)
            self.assertEqual(result.count('changed'), 1)
            self.assertEqual(result.count('unchanged'), 2)
            self.assertTrue(any(page.text_changes and page.visual_regions for page in result.pages))
            self.assertEqual(self.a.read_bytes(), self.original_a)
            with self.assertRaises(OperationCancelled):
                compare_documents(left, right, cancel=lambda: True)

        def test_form_design_radio_runtime(self):
            source = load_source(self.a)
            designs = (
                FormDesign('name', 'name', 'Name', 'text', (FieldPlacement(0, (.1, .2, .8, .28)),), value='Wien'),
                FormDesign('choice', 'entscheidung', 'Entscheidung', 'radio',
                           (FieldPlacement(0, (.1, .35, .18, .4), 'yes'), FieldPlacement(2, (.1, .35, .18, .4), 'no')),
                           options=(('yes', 'Ja'), ('no', 'Nein')), value='no'),
            )
            edited = apply_form_designs(source, designs)
            filled = fill_source_form(edited, {'name': 'Grüße Wien', 'entscheidung': 'yes'})
            self.assertEqual({field.name: field.value for field in form_fields(filled)}, {'name': 'Grüße Wien', 'entscheidung': 'yes'})
            compared = compare_documents(edited, filled)
            self.assertEqual(compared.count('changed'), 2)
            self.assertTrue(all(page.visual_regions for page in compared.pages if page.status == 'changed'))
            workspace = Workspace(); workspace.add_sources([source])
            workspace.replace_source(source.source_id, filled)
            export_pdf(workspace.sources, workspace.pages[:1], self.out)
            reader = PdfReader(self.out)
            self.assertEqual(len(reader.pages), 1)
            reopened = form_fields(load_source(self.out))
            self.assertEqual(sorted(field.value for field in reopened), ['Grüße Wien', 'yes'])
            workspace.undo(); self.assertEqual(workspace.pages[0].source_id, source.source_id)
            self.assertEqual(source.data, self.a.read_bytes())

        def test_page_marks_selected_copy_runtime(self):
            workspace = Workspace(); workspace.add_sources([load_source(self.a)])
            copied = workspace.duplicate([workspace.pages[0].uid])[0]
            before = tuple(workspace.pages)
            options = PageMarksOptions(WatermarkOptions(text='Grüße V07', font_size=18),
                                       PageNumberOptions(template='WB-{page}', start_number=7, counting='targets'))
            result = prepare_page_marks(workspace.sources, workspace.pages, options, (copied,))
            workspace.apply_page_replacements(result.sources, result.pages)
            export_pdf(workspace.sources, workspace.pages, self.out)
            texts = [page.extract_text() for page in PdfReader(self.out).pages]
            self.assertNotIn('WB-7', texts[0]); self.assertIn('WB-7', texts[1])
            self.assertIn('Grüße V07', texts[1])
            workspace.undo(); self.assertEqual(tuple(workspace.pages), before)
            workspace.redo(); self.assertEqual(tuple(workspace.pages), result.pages)
            self.assertEqual(self.a.read_bytes(), self.original_a)

    return V07Acceptance


def v07_acceptance(window, directory: Path, app, wait_for, fixture):
    """Exercise actual new dialogs, exports and the main-window undo paths."""
    checks = []
    from pdf_workbench.ui.compare_dialog import ComparisonDialog
    from pdf_workbench.ui.form_editor import FormEditorDialog
    from pdf_workbench.ui.page_marks_dialog import PageMarksDialog

    left, right = directory / 'v07-before.pdf', directory / 'v07-after.pdf'
    fixture(left, ('Agreement amount 125', 'Appendix'))
    fixture(right, ('Cover', 'Agreement amount 175', 'Appendix'))
    window.saved_state = tuple(window.workspace.pages)
    window.new_workspace()
    window.import_files([str(left)])
    wait_for(lambda: window.job is None)
    before = tuple(window.workspace.pages)
    dialog = ComparisonDialog(load_source(left), load_source(right), window, motion=window.motion)
    dialog.show()
    wait_for(lambda: dialog.comparison is not None and dialog.worker is None)
    assert dialog.comparison.count('added') == 1 and dialog.comparison.count('changed') == 1
    changed_index = next(i for i, pair in enumerate(dialog.comparison.pages) if pair.status == 'changed')
    item = next(item for item in dialog._page_items if item.data(0, Qt.ItemDataRole.UserRole)[0] == changed_index)
    dialog.changes.setCurrentItem(item)
    dialog.zoom_box.setCurrentIndex(dialog.zoom_box.findData(2.))
    wait_for(lambda: all(not pane.image.isNull() for pane in dialog.panes))
    assert all(pane.zoom == 2. for pane in dialog.panes)
    left_scroll, right_scroll = (pane.scroll.verticalScrollBar() for pane in dialog.panes)
    left_scroll.setValue(left_scroll.maximum() // 2); app.processEvents()
    if left_scroll.maximum() and right_scroll.maximum():
        assert abs(left_scroll.value()/left_scroll.maximum() - right_scroll.value()/right_scroll.maximum()) < .03
    dialog.grab().save(str(directory / 'v07-comparison.png'))
    dialog.reject(); wait_for(lambda: dialog.is_closed)
    assert tuple(window.workspace.pages) == before
    checks.append('PDF comparison text/visual alignment and read-only navigation')

    def edit_fields(editor):
        editor.type_combo.setCurrentIndex(editor.type_combo.findData('text'))
        uid = editor.add_field()
        editor._select_field(uid)
        editor.name_edit.setText('v07_name'); editor.label_edit.setText('Vorname')
        editor.value_edit.setText('Grüße Wien')
        assert editor.apply_properties()
        temporary = editor.add_field()
        editor._select_field(temporary)
        editor.delete_field()
        editor.type_combo.setCurrentIndex(editor.type_combo.findData('radio'))
        editor.add_field()
        editor.show(); app.processEvents()
        editor.grab().save(str(directory / 'v07-form-editor.png'))
        editor.accept()
        assert editor.result_source is not None
        return 1
    with patch.object(FormEditorDialog, 'exec', edit_fields):
        window.form_editor_action.trigger()
    assert tuple(window.workspace.pages) != before
    forms_out = directory / 'v07-designed-form.pdf'
    export_pdf(window.workspace.sources, window.workspace.pages, forms_out)
    fields = form_fields(load_source(forms_out))
    assert any(field.name.endswith('v07_name') and field.value == 'Grüße Wien' for field in fields)
    assert any(field.kind == 'radio' for field in fields)
    window.undo(); assert tuple(window.workspace.pages) == before
    window.redo()
    checks.append('AcroForm design create/edit/delete/radio and undo')

    original = tuple(window.workspace.pages)
    def number_pages(editor):
        editor.scope.setCurrentIndex(editor.scope.findData('all'))
        editor.text.setText('V07-{page}/{pages}')
        editor.show()
        wait_for(lambda: editor.worker is None and editor._ready_request is not None and editor.apply_button.isEnabled())
        editor.grab().save(str(directory / 'v07-numbering.png'))
        editor.apply_button.click()
        return 1
    with patch.object(PageMarksDialog, 'exec', number_pages):
        window.numbering_action.trigger()
        wait_for(lambda: window.job is None)
    assert tuple(window.workspace.pages) != original
    marked_out = directory / 'v07-numbered.pdf'
    export_pdf(window.workspace.sources, window.workspace.pages, marked_out)
    assert 'V07-' in PdfReader(marked_out).pages[0].extract_text()
    assert any(field.kind == 'radio' for field in form_fields(load_source(marked_out)))
    window.undo(); assert tuple(window.workspace.pages) == original
    window.redo()
    # A real watermark preview targets just one duplicate, not its source copy.
    duplicate_uid = window.workspace.duplicate([window.workspace.pages[0].uid])[0]
    window.refresh([duplicate_uid], duplicate_uid)
    before_watermark = tuple(window.workspace.pages)
    def watermark_page(editor):
        editor.scope.setCurrentIndex(editor.scope.findData('selected'))
        editor.text.setText('Grüße V07'); editor.font_size.setValue(18)
        editor.show()
        wait_for(lambda: editor.worker is None and editor._ready_request is not None and editor.apply_button.isEnabled())
        assert editor.request.target_ids == (duplicate_uid,)
        editor.grab().save(str(directory / 'v07-watermark.png'))
        editor.apply_button.click()
        return 1
    with patch.object(PageMarksDialog, 'exec', watermark_page):
        window.watermark_action.trigger()
        wait_for(lambda: window.job is None)
    watermark_out = directory / 'v07-watermarked-copy.pdf'
    export_pdf(window.workspace.sources, window.workspace.pages, watermark_out)
    watermark_texts = [page.extract_text() for page in PdfReader(watermark_out).pages]
    assert 'Grüße V07' not in watermark_texts[0] and 'Grüße V07' in watermark_texts[1]
    assert any(field.kind == 'radio' for field in form_fields(load_source(watermark_out)))
    window.undo(); assert tuple(window.workspace.pages) == before_watermark
    window.redo()
    checks.append('page marks preview/export/reopen/selection and undo')
    return checks
