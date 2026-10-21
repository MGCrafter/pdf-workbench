"""Conservative AcroForm filling and explicit field-tree reconstruction.

The supported scope is ordinary text, checkboxes and single-choice fields.
Widgets are rebuilt from the selected pages, rather than cloning their parent
trees (which can otherwise pull omitted pages into an export).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import io
import math
import re
from typing import Mapping
from uuid import uuid4

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject, BooleanObject, DictionaryObject, NameObject, TextStringObject,
)


def _error(message):
    from .core import PdfWorkbenchError
    return PdfWorkbenchError(message)


def _object(value):
    return value.get_object() if hasattr(value, "get_object") else value


@dataclass(frozen=True)
class FormField:
    name: str
    label: str
    kind: str
    value: str | bool
    options: tuple[tuple[str, str], ...] = ()
    read_only: bool = False
    max_length: int | None = None
    page_numbers: tuple[int, ...] = ()
    multiline: bool = False


def _chain(widget):
    chain, seen = [], set()
    current = widget
    while current is not None:
        current = current.get_object()
        identity = id(current)
        if identity in seen or len(chain) > 64:
            raise _error("Die Formularstruktur ist zyklisch oder zu tief verschachtelt.")
        seen.add(identity)
        chain.append(current)
        current = current.get("/Parent")
    return chain


def _inherited(chain, key, default=None):
    return next((item[key] for item in chain if key in item), default)


def _name(chain):
    return ".".join(str(item["/T"]) for item in reversed(chain) if item.get("/T"))


def _widgets(page):
    annotations = page.get("/Annots", [])
    for reference in annotations.get_object() if hasattr(annotations, "get_object") else annotations:
        annotation = reference.get_object()
        if annotation.get("/Subtype") == "/Widget":
            yield annotation


def page_has_fields(page):
    return any(_widgets(page))


def _options(chain):
    result = []
    for option in _inherited(chain, "/Opt", []):
        if isinstance(option, (list, ArrayObject)):
            if len(option) != 2:
                raise _error("Das Formular enthält eine ungültige Auswahlliste.")
            result.append((str(option[0]), str(option[1])))
        else:
            result.append((str(option), str(option)))
    return tuple(result)


def _checkbox_on(widget):
    appearances = _object(widget.get("/AP", {}))
    normal = _object(appearances.get("/N", {}))
    states = [str(key) for key in normal if str(key) != "/Off"]
    if len(states) != 1:
        raise _error("Dieses Kontrollkästchen hat keine eindeutige Darstellung und wird nicht unterstützt.")
    return states[0]


def validate_forms(reader):
    """Reject forms whose behavior or data cannot be retained in this scope."""
    root = reader.trailer["/Root"]
    form = root.get("/AcroForm")
    form = form.get_object() if form else {}
    if "/XFA" in form:
        raise _error("XFA-Formulare werden noch nicht unterstützt. Bitte verwende eine normale AcroForm-PDF.")
    if form.get("/CO") or root.get("/AA") or root.get("/OpenAction"):
        if form.get("/Fields"):
            raise _error("Formulare mit Aktionen, Skripten oder Berechnungen werden noch nicht unterstützt.")
    names = root.get("/Names")
    if names and names.get_object().get("/JavaScript") and form.get("/Fields"):
        raise _error("Formulare mit JavaScript werden noch nicht unterstützt.")
    known, stack, seen = set(), list(form.get("/Fields", [])), set()
    while stack:
        node = stack.pop().get_object()
        if id(node) in seen:
            raise _error("Die Formularstruktur enthält mehrfach verknüpfte oder zyklische Felder.")
        seen.add(id(node))
        chain = _chain(node)
        if node.get("/AA") or node.get("/A"):
            raise _error("Formulare mit Feldaktionen oder Skripten werden noch nicht unterstützt.")
        kind = _inherited(chain, "/FT")
        flags = int(_inherited(chain, "/Ff", 0))
        if kind == "/Sig":
            raise _error("PDFs mit digitalen Zertifikatssignaturen werden derzeit nicht unterstützt.")
        if kind and kind not in ("/Tx", "/Btn", "/Ch"):
            raise _error("Dieser Formularfeldtyp wird noch nicht unterstützt.")
        if kind == "/Btn" and flags & ((1 << 15) | (1 << 16)):
            raise _error("Formulare mit Optionsschaltern oder Aktionsschaltflächen werden noch nicht unterstützt.")
        if kind == "/Tx" and flags & ((1 << 13) | (1 << 20) | (1 << 25)):
            raise _error("Passwort-, Dateiauswahl- und Richtext-Formularfelder werden noch nicht unterstützt.")
        if kind == "/Ch" and flags & ((1 << 18) | (1 << 21)):
            raise _error("Editierbare Auswahllisten und Mehrfachauswahl werden noch nicht unterstützt.")
        kids = node.get("/Kids", [])
        stack.extend(kids)
        if node.get("/Subtype") == "/Widget":
            known.add(id(node))
        elif not kids and kind:
            raise _error("Formulare mit unsichtbaren Datenfeldern werden noch nicht unterstützt.")
    visible_widgets = set()
    terminals_by_name = {}
    for page in reader.pages:
        if page.get("/AA") and form.get("/Fields"):
            raise _error("Formulare mit Seitenaktionen werden noch nicht unterstützt.")
        for widget in _widgets(page):
            visible_widgets.add(id(widget))
            chain = _chain(widget)
            if id(widget) not in known or not _name(chain) or not _inherited(chain, "/FT"):
                raise _error("Das Formular enthält unvollständige oder verwaiste Formularfelder.")
            terminal = next((node for node in chain if "/T" in node), None)
            name = _name(chain)
            if name in terminals_by_name and terminals_by_name[name] != id(terminal):
                raise _error("Das Formular enthält mehrdeutige gleichnamige Felder. Sie werden noch nicht unterstützt.")
            terminals_by_name[name] = id(terminal)
            if widget.get("/AA") or widget.get("/A"):
                raise _error("Formulare mit Feldaktionen werden noch nicht unterstützt.")
            rect = widget.get("/Rect", [])
            if (len(rect) != 4 or any(not math.isfinite(float(number)) for number in rect)
                    or float(rect[2]) <= float(rect[0]) or float(rect[3]) <= float(rect[1])):
                raise _error("Das Formular enthält ein Feld mit ungültiger Größe.")
            if _inherited(chain, "/FT") == "/Btn":
                _checkbox_on(widget)
    if known - visible_widgets:
        raise _error("Das Formular enthält Felder ohne zugeordnete Dokumentseite. Sie werden noch nicht unterstützt.")
    _read_fields(reader)


def form_fields(source):
    reader = PdfReader(io.BytesIO(source.data))
    validate_forms(reader)
    return _read_fields(reader)


def _read_fields(reader):
    fields = {}
    for index, page in enumerate(reader.pages):
        for widget in _widgets(page):
            chain = _chain(widget)
            name = _name(chain)
            field_type = _inherited(chain, "/FT")
            flags = int(_inherited(chain, "/Ff", 0))
            kind = {"/Tx": "text", "/Btn": "checkbox", "/Ch": "choice"}[field_type]
            value = _inherited(chain, "/V", "")
            value = str(value) not in ("", "/Off") if kind == "checkbox" else str(value)
            if name in fields:
                previous = fields[name]
                if previous.kind != kind or previous.value != value:
                    raise _error("Das Formular enthält widersprüchliche gleichnamige Felder.")
                fields[name] = replace(previous, page_numbers=tuple(sorted(set((*previous.page_numbers, index + 1)))))
            else:
                maximum = _inherited(chain, "/MaxLen")
                if maximum is not None and (not isinstance(maximum, int) or maximum <= 0):
                    raise _error("Das Formular enthält eine ungültige Zeichenbegrenzung.")
                fields[name] = FormField(name, str(_inherited(chain, "/TU", name)), kind, value,
                                        _options(chain), bool(flags & 1), int(maximum) if maximum else None,
                                        (index + 1,), bool(flags & (1 << 12)))
    return tuple(fields.values())


def prepare_form_appearances(reader):
    """Resolve a viewer-regeneration request before retaining a local snapshot.

    A stored appearance can lag behind /V when /NeedAppearances is true. Export
    must never turn that flag off while retaining the stale visible value.
    """
    form = _object(reader.trailer["/Root"].get("/AcroForm", {}))
    flag = form.get("/NeedAppearances", False)
    needs_refresh = bool(getattr(flag, "value", flag))
    for page in reader.pages:
        for widget in _widgets(page):
            if _inherited(_chain(widget), "/FT") != "/Btn":
                normal = _object(widget.get("/AP", {})).get("/N")
                needs_refresh |= normal is None
    if not needs_refresh or not form.get("/Fields"):
        return None
    descriptors = {field.name: field for field in _read_fields(reader)}
    updates = {}
    for page in reader.pages:
        for widget in _widgets(page):
            name = _name(_chain(widget))
            field = descriptors[name]
            updates[name] = (_checkbox_on(widget) if field.value else "/Off") if field.kind == "checkbox" else field.value
    writer = PdfWriter(clone_from=reader)
    _normalize_writer_fields(writer)
    _apply_updates(writer, descriptors, updates)
    output = io.BytesIO(); writer.write(output)
    return output.getvalue()


def fill_source_form(source, values: Mapping[str, str | bool]):
    """Return an immutable source replacement; originals and old undo states survive."""
    descriptors = {field.name: field for field in form_fields(source)}
    unknown = set(values) - set(descriptors)
    if unknown:
        raise _error("Ein zu bearbeitendes Formularfeld wurde nicht gefunden.")
    if not any(descriptors[name].value != value for name, value in values.items()):
        return source
    reader = PdfReader(io.BytesIO(source.data))
    writer = PdfWriter(clone_from=reader)
    _normalize_writer_fields(writer)
    updates = {}
    for name, value in values.items():
        field = descriptors[name]
        if field.read_only and value != field.value:
            raise _error(f"Das Feld „{field.label}“ ist schreibgeschützt.")
        if field.kind == "checkbox":
            if not isinstance(value, bool):
                raise _error("Ein Kontrollkästchen benötigt einen Wahrheitswert.")
            widgets = [widget for page in reader.pages for widget in _widgets(page) if _name(_chain(widget)) == name]
            updates[name] = _checkbox_on(widgets[0]) if value else "/Off"
        else:
            if not isinstance(value, str):
                raise _error("Der Formularfeldinhalt muss Text sein.")
            if field.max_length is not None and len(value) > field.max_length:
                raise _error(f"Das Feld „{field.label}“ erlaubt höchstens {field.max_length} Zeichen.")
            if field.kind == "choice" and value and value not in {option[0] for option in field.options} and value != field.value:
                raise _error(f"Bitte wähle für „{field.label}“ einen angebotenen Wert.")
            if not field.multiline and ("\n" in value or "\r" in value):
                raise _error(f"Das Feld „{field.label}“ erlaubt nur eine Textzeile.")
            updates[name] = value
    if updates:
        _apply_updates(writer, descriptors, updates)
    stream = io.BytesIO()
    writer.write(stream)
    data = stream.getvalue()
    result = replace(source, source_id=uuid4().hex, data=data)
    actual = {field.name: field.value for field in form_fields(result)}
    for name, value in values.items():
        if actual[name] != value:
            raise _error("Die Formularwerte konnten nicht vollständig übernommen werden.")
    return result


def _apply_updates(writer, descriptors, updates):
    _check_encodings(writer, descriptors, updates)
    plain_updates = {name: value for name, value in updates.items() if descriptors[name].kind != "choice"}
    writer.update_page_form_field_values(None, plain_updates, auto_regenerate=False)
    _update_choices(writer, descriptors, updates)
    _decorate_appearances(writer, descriptors, updates)
    _validate_appearances(writer, descriptors, updates)


def _pdf_color(values, stroke=False):
    values = _object(values)
    if not isinstance(values, (list, ArrayObject)) or len(values) not in (1, 3, 4):
        return ""
    numbers = [float(value) for value in values]
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in numbers):
        return ""
    command = {1: "g", 3: "rg", 4: "k"}[len(numbers)]
    return " ".join(f"{value:.6f}" for value in numbers) + " " + (command.upper() if stroke else command)


def _decorate_appearances(writer, fields, updates):
    """Retain ordinary widget background and frame when rebuilding its text."""
    for page in writer.pages:
        for widget in _widgets(page):
            name = _name(_chain(widget))
            if name not in updates or fields[name].kind == "checkbox":
                continue
            appearance = widget["/AP"]["/N"].get_object()
            bounds = appearance["/BBox"]
            left, bottom, right, top = map(float, bounds)
            width, height = right - left, top - bottom
            characteristics = _object(widget.get("/MK", {}))
            background = _pdf_color(characteristics.get("/BG"))
            border = _pdf_color(characteristics.get("/BC"), stroke=True)
            style = _object(widget.get("/BS", {}))
            old_border = _object(widget.get("/Border", []))
            thickness = float(style.get("/W", old_border[2] if len(old_border) >= 3 else 1))
            if not math.isfinite(thickness) or thickness < 0:
                raise _error("Das Formular enthält eine ungültige Rahmenbreite.")
            commands = ["q"]
            if background:
                commands.append(f"{background} {left:.6f} {bottom:.6f} {width:.6f} {height:.6f} re f")
            if border and thickness > 0:
                thickness = min(thickness, width / 2, height / 2)
                commands.append(f"{border} {thickness:.6f} w")
                dash = _object(style.get("/D", [3]))
                if style.get("/S") == "/D" and dash:
                    numbers = [float(number) for number in dash]
                    if all(math.isfinite(number) and number >= 0 for number in numbers) and any(numbers):
                        commands.append("[" + " ".join(f"{number:.6f}" for number in numbers) + "] 0 d")
                offset = thickness / 2
                if style.get("/S") == "/U":
                    commands.append(f"{left:.6f} {bottom + offset:.6f} m {right:.6f} {bottom + offset:.6f} l S")
                else:
                    commands.append(f"{left + offset:.6f} {bottom + offset:.6f} {width-thickness:.6f} {height-thickness:.6f} re S")
            commands.append("Q\n")
            appearance.set_data("\n".join(commands).encode("ascii") + appearance.get_data())


def _normalize_writer_fields(writer):
    """Materialize inheritance where pypdf's updater expects a terminal field."""
    for page in writer.pages:
        for widget in _widgets(page):
            chain = _chain(widget)
            terminal = widget if "/T" in widget else _object(widget.get("/Parent"))
            if terminal is None:
                raise _error("Das Formularfeld hat keine gültige Feldzuordnung.")
            for key in ("/FT", "/Ff", "/DA", "/Q", "/Opt", "/MaxLen", "/V"):
                value = _inherited(chain, key)
                if value is not None and key not in terminal:
                    terminal[NameObject(key)] = value


def _check_encodings(writer, fields, updates):
    from pypdf._font import Font
    from pypdf.generic._appearance_stream import TextStreamAppearance
    form = writer._root_object["/AcroForm"]
    for page in writer.pages:
        for widget in _widgets(page):
            chain = _chain(widget)
            name = _name(chain)
            if name not in updates or fields[name].kind == "checkbox":
                continue
            text = str(updates[name])
            if fields[name].kind == "choice":
                text = "".join(label for _, label in fields[name].options)
            da = str(_inherited(chain, "/DA", form.get("/DA", "/Helv 0 Tf 0 g")))
            match = re.search(r"(/[^\s]+)\s+[\d.+-]+\s+Tf", da)
            font_name = match[1] if match else "/Helv"
            _, resource = TextStreamAppearance._find_annotation_font_resource(font_name, widget, form)
            font = Font.from_font_resource(resource)
            value = text.replace("\r", "").replace("\n", "")
            try:
                if isinstance(font.encoding, str):
                    value.encode(font.encoding, "strict")
                elif not set(value) <= set(font.encoding.values()):
                    raise UnicodeError
            except UnicodeError:
                raise _error("Die Schrift dieses Formularfelds unterstützt nicht alle eingegebenen Zeichen. Der Inhalt wurde nicht geändert.") from None
            if any(ord(character) < 32 and character not in "\r\n\t" for character in text):
                raise _error("Formulartexte dürfen keine unsichtbaren Steuerzeichen enthalten.")


def _update_choices(writer, fields, updates):
    from pypdf.generic._appearance_stream import TextStreamAppearance
    form = writer._root_object["/AcroForm"]
    for page in writer.pages:
        for widget in _widgets(page):
            chain = _chain(widget)
            name = _name(chain)
            if name not in updates:
                continue
            parent = widget if "/FT" in widget and "/T" in widget else widget["/Parent"]
            if fields[name].kind == "checkbox":
                parent[NameObject("/V")] = NameObject(updates[name])
                continue
            if fields[name].kind != "choice":
                continue
            value = updates[name]
            parent[NameObject("/V")] = TextStringObject(value)
            if not value and not any(option[0] == "" for option in fields[name].options):
                parent.pop("/V", None)
            parent.pop("/I", None)
            labels = dict(fields[name].options)
            appearance_field = DictionaryObject(parent)
            appearance_field[NameObject("/V")] = TextStringObject(labels.get(value, value))
            appearance_field[NameObject("/Opt")] = ArrayObject(TextStringObject(label) for _, label in fields[name].options)
            appearance_widget = DictionaryObject(widget)
            appearance_widget[NameObject("/Opt")] = appearance_field["/Opt"]
            appearance = TextStreamAppearance.from_text_annotation(form, appearance_field, appearance_widget)
            widget[NameObject("/AP")] = DictionaryObject({NameObject("/N"): writer._add_object(appearance)})


def _validate_appearances(writer, fields, updates):
    """Fail before returning data when an updated field has no visible appearance."""
    for page in writer.pages:
        for widget in _widgets(page):
            name = _name(_chain(widget))
            if name not in updates or fields[name].kind == "checkbox":
                continue
            normal = _object(widget.get("/AP", {})).get("/N")
            if normal is None or not hasattr(normal.get_object(), "get_data"):
                raise _error("Für ein Formularfeld konnte keine Darstellung erzeugt werden.")


def copy_page_fields(writer, added, reader, source_page, namespace):
    """Copy only selected-page widgets and retain a fresh editable AcroForm tree."""
    if not page_has_fields(source_page):
        return
    form = writer._root_object.get("/AcroForm")
    if form is None:
        form = DictionaryObject({NameObject("/Fields"): ArrayObject(), NameObject("/NeedAppearances"): BooleanObject(False)})
        writer._root_object[NameObject("/AcroForm")] = writer._add_object(form)
    form = form.get_object()
    state = getattr(writer, "_workbench_form_fields", None)
    if state is None:
        state = writer._workbench_form_fields = {}
    annotations = ArrayObject()
    for reference in source_page.get("/Annots", []).get_object():
        source_widget = reference.get_object()
        if source_widget.get("/Subtype") != "/Widget":
            cloned = DictionaryObject({key: value for key, value in source_widget.items() if key != "/P"}).clone(writer, force_duplicate=True)
            cloned[NameObject("/P")] = added.indirect_reference
            annotations.append(writer._add_object(cloned))
            continue
        chain = _chain(source_widget)
        original_name = _name(chain)
        key = namespace, original_name
        parent = state.get(key)
        if parent is None:
            used_names = {str(item.get_object().get("/T")) for item in form["/Fields"]}
            name = original_name
            if name in used_names:
                name = f"{namespace}.{original_name}"
                while name in used_names:
                    name = "_" + name
            parent = DictionaryObject({NameObject("/T"): TextStringObject(name), NameObject("/Kids"): ArrayObject()})
            for field_key in ("/FT", "/Ff", "/V", "/DV", "/Opt", "/MaxLen", "/Q", "/TU", "/TM"):
                value = _inherited(chain, field_key)
                if value is not None:
                    parent[NameObject(field_key)] = value.clone(writer)
            # Each group keeps the source form's font resources locally. This
            # prevents /Helv collisions when merging unrelated documents.
            source_form = reader.trailer["/Root"]["/AcroForm"]
            resources = _object(source_form.get("/DR", {}))
            font_names = {}
            if resources:
                destination_resources = form.setdefault(NameObject("/DR"), DictionaryObject())
                destination_fonts = destination_resources.setdefault(NameObject("/Font"), DictionaryObject())
                for font_name, font in _object(resources.get("/Font", {})).items():
                    new_name = NameObject(f"/WB_{namespace}_{str(font_name)[1:]}")
                    destination_fonts[new_name] = font.clone(writer)
                    font_names[str(font_name)] = str(new_name)
            da = _inherited(chain, "/DA", source_form.get("/DA"))
            if da is not None:
                parent[NameObject("/DA")] = TextStringObject(re.sub(r"/[^\s]+", lambda match: font_names.get(match[0], match[0]), str(da)))
            parent_ref = writer._add_object(parent)
            state[key] = parent_ref
            form["/Fields"].append(parent_ref)
        parent_ref = state[key]
        parent = parent_ref.get_object()
        excluded = {"/Parent", "/Kids", "/P", "/T", "/FT", "/Ff", "/V", "/DV", "/Opt", "/MaxLen", "/DA", "/DR", "/Q", "/TU", "/TM"}
        widget = DictionaryObject({key: value for key, value in source_widget.items() if key not in excluded}).clone(writer, force_duplicate=True)
        widget[NameObject("/Parent")] = parent_ref
        widget[NameObject("/P")] = added.indirect_reference
        widget_ref = writer._add_object(widget)
        parent["/Kids"].append(widget_ref)
        annotations.append(widget_ref)
    added[NameObject("/Annots")] = annotations
