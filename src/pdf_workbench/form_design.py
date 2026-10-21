"""Immutable, local authoring of ordinary interactive AcroForm fields.

Placements are fractions of the visible source CropBox with its intrinsic page
rotation applied. Editing a field rebuilds that field with a plain appearance;
unchanged imported fields retain their fonts and appearances. No source path is
ever written by this module.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import io
import math
import re
from uuid import uuid4

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject,
                          FloatObject, NameObject, NumberObject, TextStringObject)

from .annotations import normalized_point_to_pdf, visible_page_box
from .forms import (_chain, _checkbox_on, _error, _inherited, _name, _object, _radio_export_option, _widgets,
                    _apply_updates, form_fields, validate_forms)


@dataclass(frozen=True)
class FieldPlacement:
    page_index: int
    rect: tuple[float, float, float, float]
    option: str = ""
    export_value: str | None = None

    def __post_init__(self):
        if type(self.page_index) is not int or self.page_index < 0:
            raise _error("Die Formularseite ist ungültig.")
        try: rect = tuple(float(value) for value in self.rect)
        except (ValueError, TypeError): raise _error("Die Feldposition enthält ungültige Zahlen.") from None
        if (len(rect) != 4 or any(not math.isfinite(value) or not 0 <= value <= 1 for value in rect)
                or rect[2] <= rect[0] or rect[3] <= rect[1]):
            raise _error("Das Formularfeld muss vollständig innerhalb der sichtbaren Seite liegen.")
        object.__setattr__(self, "rect", rect)
        if self.export_value is not None and not isinstance(self.export_value,str):
            raise _error("Der Exportwert der Optionsgruppe ist ungültig.")


@dataclass(frozen=True)
class FormDesign:
    uid: str
    name: str
    label: str
    kind: str
    placements: tuple[FieldPlacement, ...]
    options: tuple[tuple[str, str], ...] = ()
    value: str | bool = ""
    required: bool = False
    read_only: bool = False
    tab_order: int = 0
    multiline: bool = False
    max_length: int | None = None

    def __post_init__(self):
        if not isinstance(self.uid, str) or not self.uid:
            raise _error("Das Formularfeld benötigt eine Kennung.")
        if (not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 200
                or any(ord(char) < 32 for char in self.name) or any(not part for part in self.name.split("."))):
            raise _error("Bitte verwende einen eindeutigen Feldnamen mit höchstens 200 Zeichen.")
        if self.kind not in ("text", "checkbox", "choice", "radio"):
            raise _error("Dieser Formularfeldtyp wird nicht unterstützt.")
        if not isinstance(self.label, str) or any(ord(char) < 32 for char in self.label):
            raise _error("Die Beschriftung darf keine Steuerzeichen enthalten.")
        object.__setattr__(self, "placements", tuple(self.placements))
        object.__setattr__(self, "options", tuple(tuple(option) for option in self.options))
        if not self.placements or any(not isinstance(item, FieldPlacement) for item in self.placements):
            raise _error("Bitte platziere das Feld auf mindestens einer Seite.")
        if type(self.tab_order) is not int or not 0 <= self.tab_order <= 9999:
            raise _error("Die Tabulatorreihenfolge muss zwischen 0 und 9999 liegen.")
        if self.max_length is not None and (type(self.max_length) is not int or not 1 <= self.max_length <= 1_000_000):
            raise _error("Die Zeichenbegrenzung muss eine positive ganze Zahl sein.")
        if self.kind == "checkbox":
            if type(self.value) is not bool:
                raise _error("Ein Kontrollkästchen benötigt einen Wahrheitswert.")
        elif not isinstance(self.value, str):
            raise _error("Der Feldwert muss Text sein.")
        if self.kind == "text":
            if self.max_length is not None and len(self.value) > self.max_length:
                raise _error("Der Anfangswert überschreitet die Zeichenbegrenzung.")
            if not self.multiline and any(char in self.value for char in "\r\n"):
                raise _error("Ein einzeiliges Textfeld erlaubt keine Zeilenumbrüche.")
        if self.kind in ("choice", "radio"):
            if not self.options or any(len(option) != 2 or any(not isinstance(value, str) for value in option) for option in self.options):
                raise _error("Bitte gib Auswahlwerte mit Beschriftungen an.")
            keys = [option[0] for option in self.options]
            if len(set(keys)) != len(keys):
                raise _error("Auswahlwerte müssen eindeutig sein.")
            if self.value and self.value not in keys:
                raise _error("Der Anfangswert muss einer angebotenen Auswahl entsprechen.")
            if self.kind == "radio":
                if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key) or key == "Off" for key in keys):
                    raise _error("Optionswerte erlauben Buchstaben, Ziffern, Minus und Unterstrich; „Off“ ist reserviert.")
                if {item.option for item in self.placements} != set(keys):
                    raise _error("Jede Radioauswahl benötigt eine passende Platzierung.")
        if self.kind != "radio" and any(item.option for item in self.placements):
            raise _error("Nur Optionsgruppen dürfen Platzierungen mit Auswahlwerten enthalten.")


def _normalized_rect(page, rect):
    left, bottom, right, top = visible_page_box(page)
    width, height = right-left, top-bottom
    def point(x, y):
        if page.rotation % 360 == 0: return (x-left)/width, (top-y)/height
        if page.rotation % 360 == 90: return (y-bottom)/height, (x-left)/width
        if page.rotation % 360 == 180: return (right-x)/width, (y-bottom)/height
        if page.rotation % 360 == 270: return (top-y)/height, (right-x)/width
        raise _error("Die Seitenrotation ist ungültig.")
    points = [point(float(rect[x]), float(rect[y])) for x, y in ((0, 1), (0, 3), (2, 1), (2, 3))]
    # Existing widgets extending outside CropBox cannot be moved faithfully by
    # a visible-page editor. Reject instead of silently clipping their area.
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def form_designs(source) -> tuple[FormDesign, ...]:
    reader = PdfReader(io.BytesIO(source.data)); validate_forms(reader)
    fields = form_fields(source)
    placements = {field.name: [] for field in fields}
    for index, page in enumerate(reader.pages):
        for widget in _widgets(page):
            name = _name(_chain(widget))
            field = next(item for item in fields if item.name == name)
            option = _checkbox_on(widget).removeprefix("/") if field.kind == "radio" else ""
            export_value=_radio_export_option(widget) if field.kind=="radio" else None
            placements[name].append(FieldPlacement(index, _normalized_rect(page, widget["/Rect"]), option,
                                                    str(export_value) if export_value is not None else None))
    return tuple(FormDesign(field.name, field.name, field.label, field.kind, tuple(placements[field.name]),
                            field.options, field.value, field.required, field.read_only, index,
                            field.multiline, field.max_length) for index, field in enumerate(fields))


def _ensure_form(writer):
    form = writer._root_object.get("/AcroForm")
    if form is None:
        form = DictionaryObject({NameObject("/Fields"): ArrayObject()})
        writer._root_object[NameObject("/AcroForm")] = writer._add_object(form)
    form = _object(form)
    form[NameObject("/NeedAppearances")] = BooleanObject(False)
    return form


def _design_font(writer):
    cached = getattr(writer, "_workbench_design_font", None)
    if cached:
        return cached
    form = _ensure_form(writer)
    resources = _object(form.setdefault(NameObject("/DR"), DictionaryObject()))
    fonts = _object(resources.setdefault(NameObject("/Font"), DictionaryObject()))
    name = "/WBFormFont"
    while name in fonts: name += "_"
    fonts[NameObject(name)] = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding")}))
    writer._workbench_design_font = name
    return name


def _array(values):
    return ArrayObject(FloatObject(value) for value in values)


def _appearance_matrix(width, height, rotation):
    if rotation == 90: return (height, width), (0, 1, -1, 0, width, 0)
    if rotation == 180: return (width, height), (-1, 0, 0, -1, width, height)
    if rotation == 270: return (height, width), (0, -1, 1, 0, 0, height)
    return (width, height), (1, 0, 0, 1, 0, 0)


def _button_appearance(writer, width, height, rotation, *, radio, checked, label):
    (w, h), matrix = _appearance_matrix(width, height, rotation)
    edge = min(w, h)-4
    if edge < 3:
        raise _error("Das Kontrollfeld ist zu klein. Bitte vergrößere die Platzierung.")
    x, y = 2, (h-edge)/2
    commands = ["q", "1 1 1 rg", "0.25 0.35 0.32 RG", "1 w"]
    if radio:
        r = edge/2; cx, cy = x+r, y+r; k=.55228475*r
        commands += [f"{cx+r} {cy} m {cx+r} {cy+k} {cx+k} {cy+r} {cx} {cy+r} c",
                     f"{cx-k} {cy+r} {cx-r} {cy+k} {cx-r} {cy} c",
                     f"{cx-r} {cy-k} {cx-k} {cy-r} {cx} {cy-r} c",
                     f"{cx+k} {cy-r} {cx+r} {cy-k} {cx+r} {cy} c B"]
        if checked:
            r *= .5; k=.55228475*r
            commands += ["0.08 0.3 0.23 rg", f"{cx+r} {cy} m {cx+r} {cy+k} {cx+k} {cy+r} {cx} {cy+r} c",
                         f"{cx-k} {cy+r} {cx-r} {cy+k} {cx-r} {cy} c",
                         f"{cx-r} {cy-k} {cx-k} {cy-r} {cx} {cy-r} c",
                         f"{cx+k} {cy-r} {cx+r} {cy-k} {cx+r} {cy} c f"]
    else:
        commands.append(f"{x} {y} {edge} {edge} re B")
        if checked:
            commands += ["0.08 0.3 0.23 RG", "2 w", f"{x+edge*.2} {y+edge*.5} m {x+edge*.43} {y+edge*.25} l {x+edge*.85} {y+edge*.8} l S"]
    resources = DictionaryObject()
    if label and w > edge+15:
        try: encoded = label.encode("cp1252").hex()
        except UnicodeError: raise _error("Die Beschriftung unterstützt derzeit die Zeichen der Windows-1252-Schrift.") from None
        font = _design_font(writer)
        fonts = _object(_object(_ensure_form(writer)["/DR"])["/Font"])
        resources[NameObject("/Font")] = DictionaryObject({NameObject(font): fonts[font]})
        commands += ["0.08 0.18 0.15 rg", "BT", f"{font} {min(11, h-5)} Tf", f"1 0 0 1 {edge+7} {max(2,(h-11)/2+2)} Tm <{encoded}> Tj", "ET"]
    commands += ["Q"]
    stream = DecodedStreamObject(); stream.set_data("\n".join(commands).encode("ascii"))
    stream.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                   NameObject("/BBox"): _array((0,0,w,h)), NameObject("/Matrix"): _array(matrix), NameObject("/Resources"): resources})
    return writer._add_object(stream)


def _remove_fields(writer, names):
    form = _object(writer._root_object.get("/AcroForm", {}))
    def prune(nodes):
        result = ArrayObject()
        for reference in nodes:
            node = _object(reference)
            if _name(_chain(node)) in names: continue
            if "/Kids" in node:
                children = prune(_object(node["/Kids"]))
                if not children: continue
                node[NameObject("/Kids")] = children
            result.append(reference)
        return result
    if form: form[NameObject("/Fields")] = prune(_object(form.get("/Fields", [])))
    for page in writer.pages:
        if "/Annots" in page:
            page[NameObject("/Annots")] = ArrayObject(reference for reference in page["/Annots"]
                if _object(reference).get("/Subtype") != "/Widget" or _name(_chain(_object(reference))) not in names)


def _add_field(writer, design):
    form = _ensure_form(writer)
    flags = int(design.read_only) | (2 if design.required else 0)
    flags |= ((1 << 12) if design.multiline and design.kind == "text" else 0)
    flags |= ((1 << 17) if design.kind == "choice" else 0)
    flags |= ((1 << 15) if design.kind == "radio" else 0)
    field = DictionaryObject({NameObject("/T"): TextStringObject(design.name), NameObject("/TU"): TextStringObject(design.label),
        NameObject("/FT"): NameObject({"text":"/Tx", "checkbox":"/Btn", "radio":"/Btn", "choice":"/Ch"}[design.kind]),
        NameObject("/Ff"): NumberObject(flags), NameObject("/Kids"): ArrayObject()})
    if design.kind in ("text", "choice"):
        field[NameObject("/DA")] = TextStringObject(f"{_design_font(writer)} 11 Tf 0 g")
        field[NameObject("/V")] = TextStringObject(design.value)
        if design.max_length is not None: field[NameObject("/MaxLen")] = NumberObject(design.max_length)
    else:
        field[NameObject("/V")] = NameObject(("/Yes" if design.value else "/Off") if design.kind == "checkbox" else ("/"+design.value if design.value else "/Off"))
    if design.kind == "choice":
        field[NameObject("/Opt")] = ArrayObject(ArrayObject(TextStringObject(value) for value in option) for option in design.options)
    elif design.kind == "radio" and any(place.export_value is not None for place in design.placements):
        field[NameObject("/Opt")]=ArrayObject(TextStringObject(place.export_value if place.export_value is not None else place.option)
                                              for place in design.placements)
    field_ref = writer._add_object(field); form["/Fields"].append(field_ref)
    for placement in design.placements:
        page = writer.pages[placement.page_index]
        points = [normalized_point_to_pdf(page,x,y) for x,y in ((placement.rect[0],placement.rect[1]),(placement.rect[2],placement.rect[3]))]
        rect = (min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points))
        width, height = rect[2]-rect[0], rect[3]-rect[1]
        if min(width,height)<5: raise _error("Ein Formularfeld muss mindestens 5 PDF-Punkte hoch und breit sein.")
        widget = DictionaryObject({NameObject("/Type"):NameObject("/Annot"), NameObject("/Subtype"):NameObject("/Widget"),
            NameObject("/Rect"):_array(rect), NameObject("/Parent"):field_ref, NameObject("/P"):page.indirect_reference,
            NameObject("/F"):NumberObject(4), NameObject("/WBFormDesign"):BooleanObject(True),
            NameObject("/WBOrientation"):NumberObject(page.rotation % 360),
            NameObject("/MK"):DictionaryObject({NameObject("/BG"):_array((1,1,1)),NameObject("/BC"):_array((.25,.35,.32)),NameObject("/R"):NumberObject(page.rotation % 360)}),
            NameObject("/BS"):DictionaryObject({NameObject("/W"):NumberObject(1),NameObject("/S"):NameObject("/S")})})
        if design.kind in ("checkbox","radio"):
            state = "/"+placement.option if design.kind=="radio" else "/Yes"
            label = dict(design.options)[placement.option] if design.kind=="radio" else design.label
            if design.kind == "radio": widget[NameObject("/TU")] = TextStringObject(label)
            widget[NameObject("/AP")] = DictionaryObject({NameObject("/N"):DictionaryObject({
                NameObject("/Off"):_button_appearance(writer,width,height,page.rotation%360,radio=design.kind=="radio",checked=False,label=label),
                NameObject(state):_button_appearance(writer,width,height,page.rotation%360,radio=design.kind=="radio",checked=True,label=label)})})
            widget[NameObject("/AS")] = NameObject(state if str(field["/V"])==state else "/Off")
        widget_ref=writer._add_object(widget);field["/Kids"].append(widget_ref)
        page.setdefault(NameObject("/Annots"),ArrayObject()).append(widget_ref)


def refresh_designed_appearances(writer, fields, updates):
    """Keep authored text/dropdown orientation after ordinary form filling."""
    from pypdf.generic._appearance_stream import TextStreamAppearance
    form = _ensure_form(writer)
    for page in writer.pages:
        for widget in _widgets(page):
            chain=_chain(widget);name=_name(chain)
            if name not in updates or fields[name].kind not in ("text","choice") or not widget.get("/WBFormDesign"):
                continue
            rect=widget["/Rect"];w,h=float(rect[2]-rect[0]),float(rect[3]-rect[1])
            (width,height),matrix=_appearance_matrix(w,h,int(widget.get("/WBOrientation",0)))
            parent=next(node for node in chain if "/T" in node)
            effective=DictionaryObject(parent);fake=DictionaryObject(widget);fake.pop("/AP",None)
            fake[NameObject("/Rect")]=_array((0,0,width,height))
            fake[NameObject("/DA")]=_inherited(chain,"/DA",form.get("/DA"))
            if fields[name].kind=="choice":
                effective[NameObject("/V")]=TextStringObject(dict(fields[name].options).get(str(updates[name]),str(updates[name])))
            appearance=TextStreamAppearance.from_text_annotation(form,effective,fake)
            appearance[NameObject("/Matrix")]=_array(matrix)
            widget[NameObject("/AP")]=DictionaryObject({NameObject("/N"):writer._add_object(appearance)})


def apply_form_designs(source, designs):
    """Create/edit/delete the described fields as one immutable source snapshot."""
    designs=tuple(designs);original={field.uid:field for field in form_designs(source)}
    if any(not isinstance(field,FormDesign) for field in designs): raise _error("Die Formularbeschreibung ist ungültig.")
    if len({field.uid for field in designs})!=len(designs) or len({field.name for field in designs})!=len(designs):
        raise _error("Feldnamen und Kennungen müssen eindeutig sein.")
    if any(place.page_index>=source.page_count for field in designs for place in field.placements):
        raise _error("Eine Formularplatzierung verweist auf eine fehlende Seite.")
    if tuple(original.values())==designs:return source
    reader=PdfReader(io.BytesIO(source.data));validate_forms(reader)
    writer=PdfWriter(clone_from=reader)
    changed={field.uid for field in designs if field.uid not in original or replace(original[field.uid],tab_order=field.tab_order)!=field}
    removed={field.name for uid,field in original.items() if uid in changed or uid not in {item.uid for item in designs}}
    _remove_fields(writer,removed)
    for field in designs:
        if field.uid in changed:_add_field(writer,field)
    form=_object(writer._root_object.get("/AcroForm",{}))
    if form and form.get("/Fields"):
        current={field.name:field for field in form_fields(replace(source,data=_writer_bytes(writer)))}
        updates={field.name:(("/Yes" if field.value else "/Off") if field.kind=="checkbox" else
                            ("/"+field.value if field.value else "/Off") if field.kind=="radio" else field.value)
                 for field in designs if field.uid in changed}
        if updates:_apply_updates(writer,current,updates)
        order={field.name:field.tab_order for field in designs}
        for page in writer.pages:
            if "/Annots" not in page:continue
            notes=[ref for ref in page["/Annots"] if _object(ref).get("/Subtype")!="/Widget"]
            widgets=[ref for ref in page["/Annots"] if _object(ref).get("/Subtype")=="/Widget"]
            widgets.sort(key=lambda ref:order[_name(_chain(_object(ref)))])
            page[NameObject("/Annots")]=ArrayObject([*notes,*widgets]);page.pop("/Tabs",None)
    elif form:
        writer._root_object.pop("/AcroForm",None)
    # Removed parent/widget pairs reference each other. Counting references in
    # all writer objects would retain those orphan cycles and their old values.
    # Clone the live document roots into a fresh writer before final compaction.
    writer=PdfWriter(clone_from=PdfReader(io.BytesIO(_writer_bytes(writer))))
    writer.compress_identical_objects(remove_duplicates=True,remove_unreferenced=True)
    data=_writer_bytes(writer);result=replace(source,source_id=uuid4().hex,data=data)
    actual={field.name:field for field in form_fields(result)}
    if set(actual)!={field.name for field in designs}:raise _error("Die Formularfelder konnten nicht vollständig übernommen werden.")
    if any(actual[field.name].value!=field.value for field in designs):raise _error("Ein Formularwert konnte nicht erhalten werden.")
    return result


def _writer_bytes(writer):
    stream=io.BytesIO();writer.write(stream);return stream.getvalue()
