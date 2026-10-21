"""Visible page content for watermarks and page numbers, without changing inputs.

Coordinates and margins describe the final, cropped workspace view. The source's
rotation and each individual workspace copy's rotation remain intact. Editable
Workbench annotations stay in the page model; supported form widgets stay live.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import io
import math
import string
from typing import Iterable, Mapping, Sequence
from uuid import uuid4

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, NumberObject

from .annotations import add_pdf_annotations, visible_page_box
from .core import PageRef, PdfWorkbenchError, SourceDocument, _check_cancel
from .bookmarks import remap_source_bookmarks
from .forms import copy_page_fields, page_has_fields
from .text_appearance import TextStyle, font_metrics, normalize_text, pdf_text


POSITIONS = ("center", "top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right")


def _finite(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise PdfWorkbenchError(f"{label} muss zwischen {low:g} und {high:g} liegen.")


@dataclass(frozen=True)
class WatermarkOptions:
    text: str = "ENTWURF"
    font_size: float = 48
    color: tuple[float, float, float] = (.12, .32, .31)
    opacity: float = .20
    angle: float = 45
    position: str = "center"
    margin: float = 24

    def __post_init__(self):
        object.__setattr__(self, "text", normalize_text(self.text))
        style = TextStyle(self.font_size, self.color)
        object.__setattr__(self, "color", style.color)
        _finite(self.opacity, .01, 1, "Die Deckkraft")
        _finite(self.angle, -180, 180, "Die Drehung")
        _finite(self.margin, 0, 288, "Der Rand in pt")
        if self.position not in POSITIONS:
            raise PdfWorkbenchError("Die Wasserzeichenposition ist ungültig.")


@dataclass(frozen=True)
class PageNumberOptions:
    template: str = "Seite {page} von {pages}"
    start_number: int = 1
    position: str = "bottom-center"
    font_size: float = 10
    color: tuple[float, float, float] = (.12, .20, .23)
    margin: float = 18
    counting: str = "document"

    def __post_init__(self):
        object.__setattr__(self, "template", normalize_text(self.template))
        style = TextStyle(self.font_size, self.color)
        object.__setattr__(self, "color", style.color)
        if isinstance(self.start_number, bool) or not isinstance(self.start_number, int) or not 0 <= self.start_number <= 1_000_000_000:
            raise PdfWorkbenchError("Die Startnummer muss eine ganze Zahl zwischen 0 und 1000000000 sein.")
        _finite(self.margin, 0, 288, "Der Rand in pt")
        if self.position not in POSITIONS or self.position == "center":
            raise PdfWorkbenchError("Bitte eine Kopf- oder Fußzeilenposition wählen.")
        if self.counting not in ("document", "targets"):
            raise PdfWorkbenchError("Die Zählweise ist ungültig.")
        try:
            for _, field, spec, conversion in string.Formatter().parse(self.template):
                if field is not None and (field not in ("page", "pages") or spec or conversion):
                    raise ValueError
            self.template.format(page=self.start_number, pages=1)
        except (ValueError, KeyError, IndexError):
            raise PdfWorkbenchError("Die Vorlage erlaubt nur {page} und {pages}; geschweifte Klammern mit {{ und }} schreiben.") from None


@dataclass(frozen=True)
class PageMarksOptions:
    watermark: WatermarkOptions | None = None
    numbering: PageNumberOptions | None = None

    def __post_init__(self):
        if not isinstance(self.watermark, (WatermarkOptions, type(None))) or not isinstance(self.numbering, (PageNumberOptions, type(None))):
            raise PdfWorkbenchError("Die Einstellungen für die Seitenmarkierungen sind ungültig.")
        if self.watermark is None and self.numbering is None:
            raise PdfWorkbenchError("Bitte ein Wasserzeichen oder eine Seitennummerierung wählen.")


@dataclass(frozen=True)
class PageMarksRequest:
    options: PageMarksOptions
    target_ids: tuple[str, ...] | None = None

    def __post_init__(self):
        if not isinstance(self.options, PageMarksOptions):
            raise PdfWorkbenchError("Die Einstellungen für die Seitenmarkierungen sind ungültig.")
        if self.target_ids is not None:
            object.__setattr__(self, "target_ids", tuple(self.target_ids))


@dataclass(frozen=True)
class PageMarksResult:
    sources: tuple[SourceDocument, ...]
    pages: tuple[PageRef, ...]
    changed_ids: tuple[str, ...]


def target_pages(pages: Sequence[PageRef], target_ids: Iterable[str] | None):
    lookup = {page.uid for page in pages}
    selected = lookup if target_ids is None else set(target_ids)
    if not selected or not selected <= lookup or len(lookup) != len(pages):
        raise PdfWorkbenchError("Bitte vorhandene Arbeitsseiten für die Markierung auswählen.")
    return tuple((index, page) for index, page in enumerate(pages) if page.uid in selected)


def _text_size(text, size):
    _, units, advances, cmap, bbox, ascent, descent = font_metrics()
    lines = text.split("\n")
    width = max(sum(advances[cmap[ord(char)]] for char in line) * size / units for line in lines)
    ascent, descent = max(ascent, bbox[3]), min(descent, bbox[1])
    leading = max(size * 1.2, (ascent - descent) * size / units)
    # Tiny padding prevents a metrics rounding error from wrapping a final glyph.
    return max(width, size / 10) + .001, (ascent - descent) * size / units + (len(lines) - 1) * leading + .001


def _mark(writer, page, workspace_rotation, text, options, *, opacity=1, angle=0):
    left, bottom, right, top = visible_page_box(page)
    unit = float(page.get("/UserUnit", 1))
    if not math.isfinite(unit) or unit <= 0:
        raise PdfWorkbenchError("Die PDF-Seite besitzt eine ungültige UserUnit.")
    rotation = (page.rotation + workspace_rotation) % 360
    if rotation not in (0, 90, 180, 270):
        raise PdfWorkbenchError("Die PDF-Seitenrotation muss ein Vielfaches von 90 Grad sein.")
    width, height = right-left, top-bottom
    display_width, display_height = (height, width) if rotation % 180 else (width, height)
    text_width, text_height = _text_size(text, options.font_size / unit)
    cosine, sine = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    rotated_width = abs(cosine) * text_width + abs(sine) * text_height
    rotated_height = abs(sine) * text_width + abs(cosine) * text_height
    margin = options.margin / unit
    if rotated_width > display_width - 2 * margin + .001 or rotated_height > display_height - 2 * margin + .001:
        raise PdfWorkbenchError("Die Markierung passt nicht vollständig auf die sichtbare Seite. Bitte Schriftgröße, Drehung oder Rand verkleinern.")
    vertical, horizontal = options.position.split("-") if options.position != "center" else ("center", "center")
    cx = margin + rotated_width / 2 if horizontal == "left" else (display_width - margin - rotated_width / 2 if horizontal == "right" else display_width / 2)
    cy = display_height - margin - rotated_height / 2 if vertical == "top" else (margin + rotated_height / 2 if vertical == "bottom" else display_height / 2)

    def raw(x, y):
        if rotation == 0: return left + x, bottom + y
        if rotation == 90: return right - y, bottom + x
        if rotation == 180: return right - x, top - y
        return left + y, top - x

    # Local text x/y axes -> final displayed page -> original PDF user space.
    origin = raw(cx - cosine * text_width/2 + sine * text_height/2,
                 cy - sine * text_width/2 - cosine * text_height/2)
    unit_x = raw(cx + cosine, cy + sine)
    unit_y = raw(cx - sine, cy + cosine)
    center = raw(cx, cy)
    matrix = (unit_x[0]-center[0], unit_x[1]-center[1], unit_y[0]-center[0], unit_y[1]-center[1], *origin)
    resources, commands = pdf_text(writer, text, TextStyle(options.font_size, options.color), text_width, text_height, unit)
    resources[NameObject("/ExtGState")] = DictionaryObject({NameObject("/PWBAlpha"): DictionaryObject({
        NameObject("/Type"): NameObject("/ExtGState"), NameObject("/ca"): FloatObject(opacity),
        NameObject("/CA"): FloatObject(opacity), NameObject("/BM"): NameObject("/Normal")})})
    appearance = DecodedStreamObject()
    appearance.set_data(("q\n/PWBAlpha gs\n" + "\n".join(commands) + "\nQ\n").encode("ascii"))
    appearance.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                       NameObject("/FormType"): NumberObject(1), NameObject("/BBox"): ArrayObject(FloatObject(value) for value in (0, 0, text_width, text_height)),
                       NameObject("/Resources"): resources})
    # Detach shared resources/content arrays: duplicated pages must never gain
    # each other's marks through pypdf's otherwise useful object sharing.
    page_resources = DictionaryObject(page.get("/Resources", {}).get_object() if "/Resources" in page else {})
    xobjects = DictionaryObject(page_resources.get("/XObject", {}).get_object() if "/XObject" in page_resources else {})
    name = NameObject("/PWBMark" + uuid4().hex)
    xobjects[name] = writer._add_object(appearance.flate_encode())
    page_resources[NameObject("/XObject")] = xobjects
    page[NameObject("/Resources")] = page_resources
    invocation = DecodedStreamObject()
    invocation.set_data(("q\n" + " ".join(f"{value:.9f}" for value in matrix) + f" cm\n{name} Do\nQ\n").encode("ascii"))
    existing = page.get("/Contents")
    contents = existing.get_object() if existing is not None else None
    # Isolate the source graphics state, even if its final operators leave a
    # transformed CTM or clip active. The new mark starts in default user space.
    start, end = DecodedStreamObject(), DecodedStreamObject()
    start.set_data(b"q\n"); end.set_data(b"\nQ\n")
    previous = list(contents) if isinstance(contents, ArrayObject) else ([existing] if existing is not None else [])
    page[NameObject("/Contents")] = ArrayObject([writer._add_object(start), *previous, writer._add_object(end), writer._add_object(invocation)])


def prepare_page_marks(sources: Mapping[str, SourceDocument], pages: Sequence[PageRef], options: PageMarksOptions,
                       target_ids=None, cancel=None, progress=None, *, preview_uid: str | None = None) -> PageMarksResult:
    """Prepare new snapshots only; callers commit all pages as one undoable edit.

    Preview builds just one target but retains its actual document/selection
    numbering. Every currently present copy of an affected source is rebuilt,
    including unmarked pages: cross-page form groups must keep one namespace.
    """
    request = PageMarksRequest(options, target_ids)
    pages = tuple(pages)
    targets = target_pages(pages, request.target_ids)
    if preview_uid is not None and preview_uid not in {page.uid for _, page in targets}:
        raise PdfWorkbenchError("Die Vorschauseite gehört nicht zur Seitenauswahl.")
    target_indices = {page.uid: target_index for target_index, (_, page) in enumerate(targets)}
    affected_sources = {page.source_id for _, page in targets}
    workset = tuple((index, page) for index, page in enumerate(pages)
                    if page.source_id in affected_sources and (preview_uid is None or page.uid == preview_uid))
    states, changed, replacements, source_indexes = {}, [], {}, {}
    for work_index, (index, page) in enumerate(workset):
        _check_cancel(cancel)
        is_target = page.uid in target_indices
        if page.source_id not in sources:
            raise PdfWorkbenchError("Die ursprüngliche PDF-Quelle wurde nicht gefunden.")
        source = sources[page.source_id]
        source_indexes.setdefault(source.source_id, []).append(page.page_index)
        if source.source_id not in states:
            reader, writer = PdfReader(io.BytesIO(source.data)), PdfWriter()
            writer.pdf_header = "%PDF-1.4"  # Constant-alpha transparency needs PDF 1.4.
            if reader.metadata:
                writer.add_metadata({key: value for key, value in reader.metadata.items() if isinstance(value, str)})
            states[source.source_id] = (source, reader, writer, uuid4().hex)
        _, reader, writer, new_id = states[source.source_id]
        if not 0 <= page.page_index < len(reader.pages) or page.rotation % 90:
            raise PdfWorkbenchError("Die zu markierende Arbeitsseite ist ungültig.")
        source_page = reader.pages[page.page_index]
        # Duplicated page content, widgets and resources must be independent.
        writer.reset_translation(reader)
        added = writer.add_page(source_page, excluded_keys=("/Annots",)) if page_has_fields(source_page) else writer.add_page(source_page)
        copy_page_fields(writer, added, reader, source_page, namespace=source.source_id)
        if is_target and request.options.watermark is not None:
            mark = request.options.watermark
            _mark(writer, added, page.rotation, mark.text, mark, opacity=mark.opacity, angle=mark.angle)
        if is_target and request.options.numbering is not None:
            numbering = request.options.numbering
            ordinal = index if numbering.counting == "document" else target_indices[page.uid]
            total = len(pages) if numbering.counting == "document" else len(targets)
            text = normalize_text(numbering.template.format(page=numbering.start_number + ordinal, pages=total))
            _mark(writer, added, page.rotation, text, numbering)
        replacements[page.uid] = replace(page, source_id=new_id, page_index=len(writer.pages)-1)
        if preview_uid is not None:
            add_pdf_annotations(writer, added, source_page, page.annotations, page.rotation)
            replacements[page.uid] = replace(replacements[page.uid], annotations=())
        if is_target: changed.append(page.uid)
        if progress: progress(round((work_index+1) * 100 / len(workset)), "Seitenmarkierungen werden vorbereitet …")
    result_sources = []
    for source, _, writer, source_id in states.values():
        _check_cancel(cancel)
        stream = io.BytesIO(); writer.write(stream)
        data = stream.getvalue()
        sizes = tuple((float(page.mediabox.width), float(page.mediabox.height)) for page in writer.pages)
        result_sources.append(replace(source, source_id=source_id, data=data,
                                      page_count=len(sizes), page_sizes=sizes, page_annotations=(),
                                      bookmarks=remap_source_bookmarks(source.bookmarks, source_indexes[source.source_id])))
    _check_cancel(cancel)
    return PageMarksResult(tuple(result_sources), tuple(replacements.get(page.uid, page) for page in pages), tuple(changed))


def apply_page_marks(workspace, request: PageMarksRequest, cancel=None, progress=None) -> int:
    """Apply an immutable prepared result using the workspace's atomic API."""
    result = prepare_page_marks(workspace.sources, workspace.pages, request.options, request.target_ids, cancel, progress)
    _check_cancel(cancel)
    workspace.apply_page_replacements(result.sources, result.pages)
    return len(result.changed_ids)
