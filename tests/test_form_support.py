"""Real form values, field trees, independent rendering and undo regression checks."""
from __future__ import annotations

import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, BooleanObject, DictionaryObject, NameObject, NumberObject, TextStringObject
from reportlab.pdfgen import canvas
from pdf_workbench.core import PdfWorkbenchError, Workspace, export_pdf, load_source
from pdf_workbench.forms import fill_source_form, form_fields


def make_form(path, prefix=""):
    pdf = canvas.Canvas(str(path), pagesize=(400, 500))
    pdf.drawString(20, 475, prefix + "FORM PAGE ONE")
    pdf.acroForm.textfield(name="name", tooltip="Dein Name", x=30, y=390, width=280, height=30, value="Initial", maxlen=30)
    pdf.acroForm.checkbox(name="agree", tooltip="Einverstanden", x=30, y=340, checked=False)
    pdf.acroForm.choice(name="country", tooltip="Land", x=30, y=280, width=220, height=30,
                        options=[("Austria", "AT"), ("Germany", "DE")], value="AT")
    pdf.showPage()
    pdf.drawString(20, 475, prefix + "OMITTED SECRET PAGE TWO")
    pdf.acroForm.textfield(name="second", x=30, y=390, width=280, height=30, value="SECRET SECOND VALUE")
    pdf.showPage()
    pdf.save()


class FormSupportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.path = self.directory / "form.pdf"
        make_form(self.path)
        self.original = self.path.read_bytes()
        self.source = load_source(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def workspace(self, source=None):
        workspace = Workspace()
        workspace.add_sources([source or self.source])
        return workspace

    def test_fields_values_and_immutable_filling(self):
        fields = {field.name: field for field in form_fields(self.source)}
        self.assertEqual(set(fields), {"name", "agree", "country", "second"})
        self.assertEqual(fields["name"].label, "Dein Name")
        self.assertEqual(fields["name"].page_numbers, (1,))
        self.assertEqual(fields["agree"].value, False)
        result = fill_source_form(self.source, {"name": "Jürgen (Wien)", "agree": True, "country": "DE"})
        self.assertNotEqual(result.source_id, self.source.source_id)
        self.assertEqual(self.path.read_bytes(), self.original)
        values = {field.name: field.value for field in form_fields(result)}
        self.assertEqual(values["name"], "Jürgen (Wien)")
        self.assertEqual(values["agree"], True)
        self.assertEqual(values["country"], "DE")
        widget = PdfReader(io.BytesIO(result.data)).pages[0]["/Annots"][0].get_object()
        appearance = widget["/AP"]["/N"].get_data()
        self.assertIn(b"re f", appearance)
        self.assertIn(b"re S", appearance)
        self.assertEqual({field.name: field.value for field in form_fields(self.source)}["name"], "Initial")

    def test_export_subset_omits_other_page_and_values_even_from_raw_objects(self):
        workspace = self.workspace(fill_source_form(self.source, {"name": "Wien", "agree": True}))
        destination = self.directory / "subset.pdf"
        export_pdf(workspace.sources, workspace.pages[:1], destination)
        reader = PdfReader(destination)
        self.assertEqual(len(reader.pages), 1)
        self.assertEqual(set(reader.get_fields()), {"name", "agree", "country"})
        self.assertNotIn(b"SECRET SECOND VALUE", destination.read_bytes())
        for objnum in range(1, reader.trailer["/Size"]):
            from pypdf.generic import IndirectObject
            obj = reader.get_object(IndirectObject(objnum, 0, reader))
            if hasattr(obj, "get_data"):
                self.assertNotIn(b"OMITTED SECRET PAGE TWO", obj.get_data())
        reopened = load_source(destination)
        filled_again = fill_source_form(reopened, {"name": "Graz", "country": "DE"})
        self.assertEqual({f.name: f.value for f in form_fields(filled_again)}["name"], "Graz")

    def test_reorder_rotate_duplicate_merge_and_refill_do_not_lose_fields(self):
        filled = fill_source_form(self.source, {"name": "First", "agree": True})
        workspace = self.workspace(filled)
        second_path = self.directory / "other.pdf"
        make_form(second_path, "OTHER ")
        other = fill_source_form(load_source(second_path), {"name": "Other"})
        workspace.add_sources([other])
        workspace.duplicate([workspace.pages[0].uid])
        workspace.rotate([workspace.pages[1].uid], 90)
        workspace.reorder([page.uid for page in reversed(workspace.pages)])
        destination = self.directory / "merged.pdf"
        export_pdf(workspace.sources, workspace.pages, destination)
        reader = PdfReader(destination)
        self.assertEqual(len(reader.pages), 5)
        self.assertEqual(reader.pages[3].rotation, 90)
        descriptors = form_fields(load_source(destination))
        names = [field for field in descriptors if field.name.endswith("name")]
        self.assertEqual({field.value for field in names}, {"First", "Other"})
        first = next(field for field in names if field.value == "First")
        self.assertEqual(len(first.page_numbers), 2)
        filled_again = fill_source_form(load_source(destination), {first.name: "New"})
        values = [field.value for field in form_fields(filled_again) if field.name.endswith("name")]
        self.assertEqual(set(values), {"New", "Other"})
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_form_edit_undo_redo_preserves_page_uids_and_snapshot(self):
        workspace = self.workspace()
        before = tuple(workspace.pages)
        replacement = fill_source_form(self.source, {"name": "Edited"})
        workspace.replace_source(self.source.source_id, replacement)
        after = tuple(workspace.pages)
        self.assertEqual([page.uid for page in before], [page.uid for page in after])
        workspace.undo()
        self.assertEqual(tuple(workspace.pages), before)
        workspace.redo()
        self.assertEqual(tuple(workspace.pages), after)

    def test_inherited_field_type_updates_visible_appearance(self):
        writer = PdfWriter(clone_from=io.BytesIO(self.source.data))
        widget = writer.pages[0]["/Annots"][0].get_object()
        original_appearance = widget["/AP"]["/N"].get_data()
        terminal = DictionaryObject({NameObject("/T"): TextStringObject("name"),
                                     NameObject("/V"): TextStringObject("Initial"),
                                     NameObject("/Kids"): ArrayObject([widget.indirect_reference])})
        terminal_ref = writer._add_object(terminal)
        group = writer._add_object(DictionaryObject({NameObject("/T"): TextStringObject("group"),
            NameObject("/FT"): NameObject("/Tx"), NameObject("/Kids"): ArrayObject([terminal_ref])}))
        terminal[NameObject("/Parent")] = group
        for key in ("/FT", "/T", "/V"):
            widget.pop(key, None)
        widget[NameObject("/Parent")] = terminal_ref
        writer._root_object["/AcroForm"]["/Fields"][0] = group
        path = self.directory / "inherited.pdf"; writer.write(path)
        filled = fill_source_form(load_source(path), {"group.name": "Changed"})
        self.assertEqual({field.name: field.value for field in form_fields(filled)}["group.name"], "Changed")
        result = PdfReader(io.BytesIO(filled.data)).pages[0]["/Annots"][0].get_object()
        self.assertNotEqual(result["/AP"]["/N"].get_data(), original_appearance)
        self.assertIn(b"Changed", result["/AP"]["/N"].get_data())

    def test_unattached_and_ambiguous_fields_rejected(self):
        for mode in ("unattached", "ambiguous"):
            writer = PdfWriter(clone_from=io.BytesIO(self.source.data))
            if mode == "unattached":
                # Keep canonical field data while dropping only its page link.
                del writer.pages[1]["/Annots"]
            else:
                second = writer.pages[1]["/Annots"][0].get_object()
                second[NameObject("/T")] = TextStringObject("name")
                second[NameObject("/V")] = TextStringObject("Initial")
            path = self.directory / (mode + ".pdf"); writer.write(path)
            with self.subTest(mode=mode), self.assertRaises(PdfWorkbenchError):
                load_source(path)

    def test_choice_can_clear_and_reselect_after_export(self):
        cleared = fill_source_form(self.source, {"country": ""})
        self.assertEqual({field.name: field.value for field in form_fields(cleared)}["country"], "")
        workspace = self.workspace(cleared)
        path = self.directory / "cleared.pdf"
        export_pdf(workspace.sources, workspace.pages, path)
        selected = fill_source_form(load_source(path), {"country": "DE"})
        self.assertEqual({field.name: field.value for field in form_fields(selected)}["country"], "DE")

    def test_malformed_options_are_rejected_at_import_before_gui_callbacks(self):
        writer = PdfWriter(clone_from=io.BytesIO(self.source.data))
        choice = writer.pages[0]["/Annots"][2].get_object()
        choice[NameObject("/Opt")] = ArrayObject([ArrayObject([TextStringObject("incomplete")])])
        path = self.directory / "malformed.pdf"; writer.write(path)
        with self.assertRaisesRegex(PdfWorkbenchError, "Auswahlliste"):
            load_source(path)

    def test_regeneration_request_and_missing_appearance_are_resolved_at_import(self):
        for mode in ("stale", "missing"):
            writer = PdfWriter(clone_from=io.BytesIO(self.source.data))
            widget = writer.pages[0]["/Annots"][0].get_object()
            widget[NameObject("/V")] = TextStringObject("LATEST VALUE")
            if mode == "stale":
                writer._root_object["/AcroForm"][NameObject("/NeedAppearances")] = BooleanObject(True)
            else:
                widget.pop("/AP", None)
            path = self.directory / (mode + ".pdf"); writer.write(path)
            original = path.read_bytes()
            source = load_source(path)
            normalized = PdfReader(io.BytesIO(source.data))
            appearance = normalized.pages[0]["/Annots"][0].get_object()["/AP"]["/N"].get_data()
            self.assertIn(b"LATEST VALUE", appearance)
            self.assertFalse(normalized.trailer["/Root"]["/AcroForm"]["/NeedAppearances"].value)
            workspace = self.workspace(source)
            out = self.directory / (mode + "-export.pdf")
            export_pdf(workspace.sources, workspace.pages[:1], out)
            exported = PdfReader(out)
            self.assertEqual(exported.get_fields()["name"]["/V"], "LATEST VALUE")
            self.assertIn(b"LATEST VALUE", exported.pages[0]["/Annots"][0].get_object()["/AP"]["/N"].get_data())
            self.assertEqual(path.read_bytes(), original)

    def test_invalid_values_read_only_lengths_unicode_and_unknown_are_rejected(self):
        for updates in ({"name": "x" * 31}, {"agree": "yes"}, {"country": "unknown"},
                        {"missing": "value"}, {"name": "你好"}, {"name": "two\nlines"}):
            with self.subTest(updates=updates), self.assertRaises(PdfWorkbenchError):
                fill_source_form(self.source, updates)
        writer = PdfWriter(clone_from=io.BytesIO(self.source.data))
        writer.pages[0]["/Annots"][0].get_object()[NameObject("/Ff")] = NumberObject(1)
        locked = self.directory / "locked.pdf"
        writer.write(locked)
        with self.assertRaises(PdfWorkbenchError):
            fill_source_form(load_source(locked), {"name": "Changed"})

    def test_xfa_signatures_scripts_and_radio_rejected(self):
        for key in ("xfa", "signature", "script", "radio"):
            writer = PdfWriter(clone_from=io.BytesIO(self.source.data))
            form = writer._root_object["/AcroForm"]
            field = writer.pages[0]["/Annots"][0].get_object()
            if key == "xfa":
                form[NameObject("/XFA")] = TextStringObject("dynamic")
            elif key == "signature":
                field[NameObject("/FT")] = NameObject("/Sig")
            elif key == "radio":
                field[NameObject("/FT")] = NameObject("/Btn")
                field[NameObject("/Ff")] = NumberObject(1 << 15)
            else:
                field[NameObject("/AA")] = DictionaryObject({NameObject("/K"): DictionaryObject({NameObject("/S"): NameObject("/JavaScript")})})
            destination = self.directory / (key + ".pdf")
            writer.write(destination)
            with self.subTest(kind=key), self.assertRaises(PdfWorkbenchError):
                load_source(destination)

    @unittest.skipUnless(shutil.which("pdftoppm"), "Poppler is unavailable")
    def test_second_renderer_sees_values_checkbox_and_choice_label(self):
        from PIL import Image, ImageChops
        workspace = self.workspace(fill_source_form(self.source, {"name": "Jürgen", "agree": True, "country": "DE"}))
        destination = self.directory / "render.pdf"
        export_pdf(workspace.sources, workspace.pages[:1], destination)
        for path, stem in ((self.path, "before"), (destination, "after")):
            subprocess.run(["pdftoppm", "-f", "1", "-singlefile", "-scale-to", "800", "-png", str(path), str(self.directory / stem)],
                           check=True, capture_output=True)
        with Image.open(self.directory / "before.png") as before, Image.open(self.directory / "after.png") as after:
            self.assertIsNotNone(ImageChops.difference(before.convert("RGB"), after.convert("RGB")).getbbox())
        reopened = load_source(destination)
        workspace = self.workspace(reopened)
        copy = self.directory / "roundtrip.pdf"
        export_pdf(workspace.sources, workspace.pages, copy)
        subprocess.run(["pdftoppm", "-f", "1", "-singlefile", "-scale-to", "800", "-png", str(copy), str(self.directory / "roundtrip")], check=True, capture_output=True)
        with Image.open(self.directory / "after.png") as first, Image.open(self.directory / "roundtrip.png") as second:
            self.assertIsNone(ImageChops.difference(first.convert("RGB"), second.convert("RGB")).getbbox())


if __name__ == "__main__":
    unittest.main()
