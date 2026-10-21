"""Exercise the 0.6 dialogs and exports inside the actual native executable."""
from __future__ import annotations

import io
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from release_checks import V06_GUI_CHECKS


def form_fixture(path, fixture):
    fixture(path, ("NATIVE FORM",))
    writer = PdfWriter(clone_from=path)
    fields = ArrayObject()
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding")}))
    for name, kind, value, rectangle in (("answer", "/Tx", "Before", (30,300,250,330)),
                                       ("agree", "/Btn", "/Off", (30,250,50,270)),
                                       ("country", "/Ch", "AT", (30,200,250,230))):
        field = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject(kind), NameObject("/T"): TextStringObject(name),
            NameObject("/V"): TextStringObject(value), NameObject("/DA"): TextStringObject("/Helv 12 Tf 0 g"),
            NameObject("/Rect"): ArrayObject(NumberObject(n) for n in rectangle)})
        if kind == "/Btn":
            states = DictionaryObject()
            for state, commands in (("/Off", b""), ("/Yes", b"q 0 0 0 RG 2 w 3 10 m 8 4 l 17 17 l S Q")):
                stream = DecodedStreamObject(); stream.set_data(commands)
                stream[NameObject("/Type")] = NameObject("/XObject")
                stream[NameObject("/Subtype")] = NameObject("/Form")
                stream[NameObject("/BBox")] = ArrayObject(NumberObject(n) for n in (0,0,20,20))
                states[NameObject(state)] = writer._add_object(stream)
            field[NameObject("/AP")] = DictionaryObject({NameObject("/N"): states})
            field[NameObject("/AS")] = NameObject("/Off")
            field[NameObject("/V")] = NameObject("/Off")
        if kind == "/Ch":
            field[NameObject("/Ff")] = NumberObject(1 << 17)
            field[NameObject("/Opt")] = ArrayObject(TextStringObject(value) for value in ("AT", "DE"))
        fields.append(writer._add_object(field))
    writer.pages[0][NameObject("/Annots")] = fields
    writer._root_object[NameObject("/AcroForm")] = writer._add_object(DictionaryObject({NameObject("/Fields"): fields,
        NameObject("/DR"): DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): font})})}))
    writer.write(path)


def upgrade_acceptance(window, directory, app, wait_for, fixture):
    from PyQt6.QtCore import QPoint, QRect, QSettings
    from PyQt6.QtWidgets import QDialog
    from pdf_workbench.core import export_pdf, load_source
    from pdf_workbench.forms import form_fields
    from pdf_workbench.ui.motion import MotionController
    from pdf_workbench.ui.pdf_dialogs import CompressionDialog, FormDialog, PasswordDialog
    from pdf_workbench.ui.welcome import DocumentArtwork

    source_path = directory / "native-form.pdf"
    form_fixture(source_path, fixture)
    original = source_path.read_bytes()
    before_count = len(window.workspace.pages)
    window.import_files([str(source_path)])
    wait_for(lambda: window.job is None and len(window.workspace.pages) == before_count + 1)
    before_form = tuple(window.workspace.pages)
    source = window.workspace.sources[window.workspace.pages[-1].source_id]
    dialog = FormDialog(source, window); dialog.show(); app.processEvents()
    dialog.editors["answer"].setText("Grüße aus Wien")
    dialog.editors["agree"].setChecked(True)
    choice = dialog.editors["country"]; choice.setCurrentIndex(choice.findData("DE"))
    assert dialog.editors["answer"].isVisible()
    values = dialog.values(); dialog.reject()
    with patch("pdf_workbench.app.FormDialog.get_values", return_value=values):
        window.form_action.trigger(); wait_for(lambda: window.job is None)
    after_form = tuple(window.workspace.pages)
    form_output = directory / "native-filled-form.pdf"
    with patch("pdf_workbench.app.QFileDialog.getSaveFileName", return_value=(str(form_output), "")):
        window.save_pdf(True); wait_for(lambda: window.job is None)
    reopened = load_source(form_output)
    assert {field.name: field.value for field in form_fields(reopened)} == {"answer":"Grüße aus Wien", "agree":True, "country":"DE"}
    window.page_list.setFocus(); window.undo(); assert tuple(window.workspace.pages) == before_form
    window.redo(); assert tuple(window.workspace.pages) == after_form

    locked = directory / "native-locked.pdf"
    writer = PdfWriter(clone_from=form_output); writer.encrypt("native-opening", algorithm="AES-256"); writer.write(locked)
    current_count = len(window.workspace.pages)
    with patch("pdf_workbench.app.PasswordDialog.get_password", side_effect=("wrong", "native-opening")) as prompt:
        window.import_files([str(locked)])
        wait_for(lambda: window.job is None and len(window.workspace.pages) == current_count + 1)
        assert prompt.call_count == 2
    assert any(source.was_encrypted for source in window.workspace.sources.values())
    with patch("pdf_workbench.app.PasswordDialog.get_password", return_value=None) as prompt:
        window.import_files([str(locked)])
        wait_for(lambda: window.job is None and prompt.call_count == 1)
    assert len(window.workspace.pages) == current_count + 1
    password_dialog = PasswordDialog(window); password_dialog.show(); app.processEvents()
    password_dialog.password.setText("native-export"); password_dialog.confirm.setText("different")
    password_dialog.accept(); assert password_dialog.isVisible() and password_dialog.error.text()
    password_dialog.confirm.setText("native-export"); password_dialog.accept()
    assert password_dialog.result() == QDialog.DialogCode.Accepted
    password_dialog.password.clear(); password_dialog.confirm.clear(); password_dialog.deleteLater()
    protected = directory / "native-protected-export.pdf"
    with patch("pdf_workbench.app.PasswordDialog.get_password", return_value="native-export"), \
         patch("pdf_workbench.app.QFileDialog.getSaveFileName", return_value=(str(protected), "")):
        window.protect_action.trigger(); wait_for(lambda: window.job is None)
    reader = PdfReader(protected)
    assert reader.is_encrypted and not reader.decrypt("wrong") and reader.decrypt("native-export")
    assert len(reader.pages) == len(window.workspace.pages)
    assert reader.trailer["/Encrypt"]["/Length"] == 256

    comparison = CompressionDialog(window, dict(window.workspace.sources), tuple(window.workspace.pages))
    comparison.show(); wait_for(lambda: comparison.worker is None and comparison.report is not None)
    assert comparison.save_button.isEnabled()
    options = comparison.options; comparison.reject()
    compressed = directory / "native-compressed-export.pdf"
    with patch("pdf_workbench.app.CompressionDialog.get_options", return_value=options), \
         patch("pdf_workbench.app.QFileDialog.getSaveFileName", return_value=(str(compressed), "")):
        window.compress_action.trigger(); wait_for(lambda: window.job is None)
    assert compressed.is_file() and len(PdfReader(compressed).pages) == len(window.workspace.pages)
    measured = []; measured_path = directory / "native-measured.pdf"
    export_pdf(dict(window.workspace.sources), tuple(window.workspace.pages), measured_path,
               optimization=options, compression_report=measured.append)
    assert measured[0].output_bytes == measured_path.stat().st_size

    old_size, old_theme = window.size(), window.theme.preference
    for theme in ("light", "dark"):
        window.theme.set_preference(theme); window.resize(800,560); app.processEvents()
        for button in (window.open_button, window.export_button, window.document_tools_button):
            assert button.isVisible() and window.rect().contains(QRect(button.mapTo(window,QPoint()),button.size()))
    window.theme.set_preference(old_theme); window.resize(old_size)
    settings = QSettings(str(directory / "motion.ini"), QSettings.Format.IniFormat)
    motion = MotionController(window, settings); artwork = DocumentArtwork(window)
    motion.set_reduced(True); motion.animate(artwork,"reveal",0.,1.,460)
    assert artwork.reveal == 1. and not motion.enabled
    restored = MotionController(window, QSettings(str(directory / "motion.ini"),QSettings.Format.IniFormat))
    assert restored.reduced
    artwork.hide(); artwork.deleteLater()
    assert source_path.read_bytes() == original
    return sorted(V06_GUI_CHECKS)
