"""Content removal and constrained text replacement, using immutable snapshots.

Redaction deliberately produces an image-only PDF of the visible page canvas.
The output contains no original PDF streams, metadata, attachments or revisions.
This trades text search and interactive features for an auditable privacy boundary.
Ordinary text replacement is an editable workspace operation, not sanitisation.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import io
import math
from pathlib import Path
import sys
from uuid import uuid4

from pypdf import PdfReader

from .bookmarks import remap_source_bookmarks
from .core import (PdfWorkbenchError, SourceDocument, _build_writer, _check_cancel,
                   _protect_destination)


def mupdf():
    try:
        if sys.platform == "win32":
            # Qt bundles the MSVC runtime used by the portable Windows build.
            # Load it before importing MuPDF's extension in source checkouts.
            from PyQt6 import QtCore  # noqa: F401
        import pymupdf
        return pymupdf
    except ImportError:
        raise PdfWorkbenchError("Dieses Werkzeug benötigt PyMuPDF. Bitte die vollständige PDF-Workbench-Installation verwenden.") from None


def workspace_pdf_bytes(sources, pages, cancel=None, progress=None, *, bookmarks=None):
    writer = _build_writer(sources, pages, "PDF Workbench", cancel, progress, bookmarks=bookmarks)
    stream = io.BytesIO(); writer.write(stream); _check_cancel(cancel)
    return stream.getvalue()


def publish_new_pdf(data, destination, sources, cancel=None, *, expected_pages=None):
    """Commit checked bytes exclusively. Never replace input, output or symlink."""
    target = Path(destination).expanduser().absolute()
    if target.suffix.lower() != ".pdf":
        raise PdfWorkbenchError("Bitte verwende die Dateiendung .pdf.")
    _protect_destination(target, sources); _check_cancel(cancel)
    try:
        checked = PdfReader(io.BytesIO(data))
        if checked.is_encrypted or not checked.pages or (expected_pages is not None and len(checked.pages) != expected_pages):
            raise PdfWorkbenchError("Die neue PDF konnte nicht vollständig geprüft werden.")
        # Publish the validated immutable bytes directly. A mutable intermediate
        # path could otherwise be exchanged after validation and hard-linked as
        # if its content had passed the checks above.
        from .workflows import publish_bytes
        return publish_bytes(data, target, sources, cancel).path
    except Exception as error:
        if isinstance(error, PdfWorkbenchError):
            raise
        raise PdfWorkbenchError(f"Die neue PDF konnte nicht gespeichert werden: {error}") from None


@dataclass(frozen=True)
class RedactionRegion:
    page_uid: str
    rect: tuple[float, float, float, float]

    def __post_init__(self):
        try:
            values = tuple(self.rect)
            valid = len(values) == 4 and all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in values)
            valid = valid and values[0] < values[2] and values[1] < values[3]
        except (ValueError, TypeError): valid = False
        if not isinstance(self.page_uid, str) or not self.page_uid or not valid:
            raise PdfWorkbenchError("Der Schwärzungsbereich muss innerhalb einer vorhandenen sichtbaren Seite liegen.")
        object.__setattr__(self, "rect", values)


@dataclass(frozen=True)
class RedactionResult:
    path: Path
    regions: int
    pages: int
    dpi: int


def export_redacted_pdf(sources, pages, destination, regions, *, confirmed=False,
                        dpi=200, cancel=None, progress=None, bookmarks=None):
    """Physically remove marked content, then rebuild only the visible pixels.

    Rectangle coordinates are relative to the final rotated visible page, with
    origin at its top left. No undo/project snapshot is included in this export.
    """
    if confirmed is not True:
        raise PdfWorkbenchError("Die dauerhafte Schwärzung muss ausdrücklich bestätigt werden.")
    pages = tuple(pages); regions = tuple(regions)
    known = {page.uid for page in pages}
    if not regions or any(not isinstance(region, RedactionRegion) or region.page_uid not in known for region in regions):
        raise PdfWorkbenchError("Bitte mindestens einen Bereich auf vorhandenen Seiten markieren.")
    if type(dpi) is not int or not 100 <= dpi <= 400:
        raise PdfWorkbenchError("Die Auflösung muss zwischen 100 und 400 dpi liegen.")
    fitz = mupdf(); data = workspace_pdf_bytes(sources, pages, cancel, bookmarks=bookmarks)
    with fitz.open(stream=data, filetype="pdf") as document, fitz.open() as output:
        for index, ref in enumerate(pages):
            _check_cancel(cancel); page = document[index]
            width, height = page.rect.width, page.rect.height
            if width * height * (dpi / 72) ** 2 > 80_000_000:
                raise PdfWorkbenchError("Eine Seite ist für diese Schwärzungsauflösung zu groß. Bitte eine kleinere Auflösung wählen.")
            display_regions = []
            for region in regions:
                if region.page_uid != ref.uid: continue
                l, t, r, b = region.rect
                display = fitz.Rect(l * width, t * height, r * width, b * height)
                display_regions.append(display)
                page.add_redact_annot(display * page.derotation_matrix, fill=(0, 0, 0), cross_out=False)
            if display_regions:
                # Keep surrounding vector shapes: removing a touched full-page
                # path could erase a background outside the chosen rectangle.
                # None of those PDF objects enter the output; final blackened
                # pixels below are the complete content of the fresh document.
                page.apply_redactions(images=2, graphics=0, text=0)
            _check_cancel(cancel)
            # Render annotations as well. A later annotation must never draw a
            # secret over the black mark; blacken final pixels a second time.
            pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False, annots=True)
            for display in display_regions:
                scale = dpi / 72
                box = fitz.IRect(math.floor(display.x0 * scale), math.floor(display.y0 * scale),
                                 math.ceil(display.x1 * scale), math.ceil(display.y1 * scale))
                box &= pix.irect
                pix.set_rect(box, (0, 0, 0))
            fresh = output.new_page(width=width, height=height)
            fresh.insert_image(fresh.rect, stream=pix.tobytes("png"))
            if progress: progress(round((index + 1) * 90 / len(pages)), "Sichtbare Seiten werden sicher geschwärzt …")
        _check_cancel(cancel)
        clean = output.tobytes(garbage=4, deflate=True)
    # A second parser verifies the privacy boundary: no text streams, widgets,
    # comments, outlines, attachments or document metadata from the inputs.
    checked = PdfReader(io.BytesIO(clean))
    if any(page.extract_text() or page.get("/Annots") for page in checked.pages):
        raise PdfWorkbenchError("Die sichere Schwärzung konnte nicht geprüft werden. Es wurde keine Datei gespeichert.")
    path = publish_new_pdf(clean, destination, sources, cancel, expected_pages=len(pages))
    if progress: progress(100, "Geschwärzte Bild-PDF gespeichert")
    return RedactionResult(path, len(regions), len(pages), dpi)


# Supported base fonts have known metrics and Latin encodings. Subset fonts,
# vertical writing, ligatures and transformed glyphs are rejected explicitly.
BASE_FONTS = {"Helvetica": "helv", "Helvetica-Bold": "hebo", "Helvetica-Oblique": "heit", "Helvetica-BoldOblique": "hebi",
              "Times-Roman": "tiro", "Times-Bold": "tibo", "Times-Italic": "tiit", "Times-BoldItalic": "tibi",
              "Courier": "cour", "Courier-Bold": "cobo", "Courier-Oblique": "coit", "Courier-BoldOblique": "cobi"}


@dataclass(frozen=True)
class ExistingTextRun:
    run_id: str
    text: str
    rect: tuple[float, float, float, float]
    origin: tuple[float, float]
    font: str
    font_size: float
    color: int
    supported: bool
    reason: str = ""


def _text_runs(page):
    runs = []
    matrix = page.transformation_matrix
    unit_is_standard = abs(abs(matrix.a)-1) < 1e-6 and abs(abs(matrix.d)-1) < 1e-6 and abs(matrix.b) < 1e-6 and abs(matrix.c) < 1e-6
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0: continue
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                if not span["text"].strip(): continue
                reason = ""
                if not unit_is_standard:
                    reason = "Besondere PDF-Seitenmaßeinheit oder Seitentransformation."
                elif span["font"] not in BASE_FONTS:
                    reason = "Eingebettete, spezielle oder nicht unterstützte Schrift."
                elif tuple(line.get("dir", ())) != (1., 0.) or line.get("wmode", 0):
                    reason = "Gedrehter oder vertikaler Text."
                elif span.get("alpha", 255) != 255 or span.get("flags", 0) & 1:
                    reason = "Transparenter oder hochgestellter Text."
                elif any(ord(c) < 32 or ord(c) > 255 for c in span["text"]):
                    reason = "Nicht unterstützte Zeichenkodierung."
                runs.append(ExistingTextRun(str(len(runs)), span["text"], tuple(span["bbox"]), tuple(span["origin"]),
                                            span["font"], span["size"], span["color"], not reason, reason))
    return tuple(runs)


def existing_text_runs(source, page_index):
    fitz = mupdf()
    if not 0 <= page_index < source.page_count: raise PdfWorkbenchError("Die Textseite ist ungültig.")
    with fitz.open(stream=source.data, filetype="pdf") as document:
        return _text_runs(document[page_index])


@dataclass(frozen=True)
class TextReplacementResult:
    sources: tuple[SourceDocument, ...]
    pages: tuple
    changed_ids: tuple[str, ...]


def prepare_text_replacement(sources, pages, page_uid, run_id, replacement, cancel=None, progress=None):
    """Replace one whole, supported text run. Old characters are removed.

    Existing image/vector backgrounds remain untouched. No paragraph reflow,
    font substitution or changes to other workspace copies are performed.
    """
    pages = tuple(pages); ref = next((p for p in pages if p.uid == page_uid), None)
    if ref is None or ref.source_id not in sources:
        raise PdfWorkbenchError("Die zu bearbeitende Seite wurde nicht gefunden.")
    if not isinstance(replacement, str) or not replacement.strip() or len(replacement) > 2000:
        raise PdfWorkbenchError("Bitte 1 bis 2000 sichtbare Zeichen in einer Textzeile eingeben.")
    try: replacement.encode("cp1252")
    except UnicodeEncodeError:
        raise PdfWorkbenchError("Diese Schrift unterstützt den eingegebenen Text nicht. Unterstützt werden westliche Windows-Zeichen.") from None
    if any(ord(c) < 32 or ord(c) == 127 for c in replacement):
        raise PdfWorkbenchError("Bitte eine einzelne Textzeile ohne Steuerzeichen eingeben.")
    source = sources[ref.source_id]; fitz = mupdf(); _check_cancel(cancel)
    with fitz.open(stream=source.data, filetype="pdf") as document:
        # The immutable replacement contains only the edited source page. MuPDF
        # would delete internal destinations into excluded pages, and incoming
        # links in other source pages could keep pointing at the old snapshot.
        # Reject that source rather than silently changing navigation actions.
        for link_page in document:
            _check_cancel(cancel)
            if any(link.get("kind", fitz.LINK_NONE) in (fitz.LINK_NONE, fitz.LINK_GOTO, fitz.LINK_NAMED) for link in link_page.get_links()):
                raise PdfWorkbenchError("Die PDF enthält interne oder nicht auflösbare Links. Der Text wurde nicht geändert, damit deren Seitenziele erhalten bleiben.")
        page = document[ref.page_index]; runs = _text_runs(page)
        if any(True for _ in page.widgets() or ()):
            # Document.select() does not retain a usable AcroForm field tree.
            # A later ordinary export must never inherit orphaned widgets.
            raise PdfWorkbenchError("Diese Seite enthält Formularfelder. Die Textänderung wurde abgebrochen, damit Feldstruktur, Werte und Darstellung erhalten bleiben.")
        run = next((value for value in runs if value.run_id == str(run_id)), None)
        if run is None: raise PdfWorkbenchError("Der ausgewählte Text wurde nicht gefunden.")
        if not run.supported: raise PdfWorkbenchError(f"Dieser Text kann nicht sicher ersetzt werden: {run.reason}")
        rect = fitz.Rect(run.rect)
        if any(other.run_id != run.run_id and fitz.Rect(other.rect).intersects(rect) for other in runs):
            raise PdfWorkbenchError("Der Text überlappt eine andere Textzeile. Die Änderung wurde vollständig abgebrochen.")
        # Native annotations/widgets and editable Workbench objects can carry
        # separate appearances. Reject intersections rather than changing them.
        boxes = [fitz.Rect(a.rect) for a in page.annots() or ()]
        boxes += [fitz.Rect(w.rect) for w in page.widgets() or ()]
        # /Link annotations are intentionally excluded from Page.annots().
        # Applying a redaction would otherwise silently remove their actions.
        boxes += [fitz.Rect(link["from"]) for link in page.get_links() if "from" in link]
        if any(box.intersects(rect) for box in boxes):
            raise PdfWorkbenchError("Der Text überlappt eine PDF-Anmerkung, einen Link oder ein Formularfeld.")
        fontname = BASE_FONTS[run.font]; font = fitz.Font(fontname)
        if abs(font.text_length(run.text, fontsize=run.font_size) - rect.width) > .4:
            raise PdfWorkbenchError("Die Textzeile verwendet Zeichenabstände oder eine besondere Skalierung. Sie kann nicht sicher ersetzt werden.")
        width = font.text_length(replacement, fontsize=run.font_size)
        if width > rect.width + .25:
            raise PdfWorkbenchError("Der neue Text ist breiter als die ursprüngliche Textzeile. Bitte einen kürzeren Text verwenden.")
        newrect = fitz.Rect(run.origin[0], run.origin[1] - font.ascender * run.font_size,
                            run.origin[0] + width, run.origin[1] - font.descender * run.font_size)
        unrotated = page.rect * page.derotation_matrix
        if not unrotated.contains(rect) or not unrotated.contains(newrect):
            raise PdfWorkbenchError("Der neue Text würde über den sichtbaren Seitenrand hinausragen.")
        # The clipping mode is intentionally preserved. Text selection is
        # restricted to visible runs; reject clipping/invisible render modes.
        traces = page.get_texttrace()
        overlapping = [t for t in traces if fitz.Rect(t["bbox"]).intersects(rect)]
        if not overlapping or any(t.get("type", 0) != 0 or t.get("opacity", 1) != 1 for t in overlapping):
            raise PdfWorkbenchError("Der Text besitzt eine spezielle oder unsichtbare Darstellung und kann nicht sicher ersetzt werden.")
        before = [(r.text, r.origin) for r in runs if r.run_id != run.run_id]
        page.add_redact_annot(rect, fill=False, cross_out=False)
        page.apply_redactions(images=0, graphics=0, text=0)
        remaining = _text_runs(page)
        # Guard against text-engine side effects in unrelated text positions.
        after = [(r.text, r.origin) for r in remaining]
        if len(before) != len(after) or any(a != b or any(abs(x-y) > .1 for x,y in zip(p,q)) for (a,p),(b,q) in zip(before,after)):
            raise PdfWorkbenchError("Die PDF-Textstruktur erlaubt keine isolierte Änderung. Es wurde nichts übernommen.")
        color = ((run.color >> 16 & 255) / 255, (run.color >> 8 & 255) / 255, (run.color & 255) / 255)
        page.insert_text(run.origin, replacement, fontname=fontname, fontsize=run.font_size, color=color, overlay=True)
        _check_cancel(cancel)
        document.select([ref.page_index]); data = document.tobytes(garbage=4, deflate=True)
    with fitz.open(stream=data, filetype="pdf") as checked:
        if replacement not in checked[0].get_text():
            raise PdfWorkbenchError("Der neue Text konnte nicht vollständig geprüft werden. Es wurde nichts übernommen.")
    reader = PdfReader(io.BytesIO(data)); new_id = uuid4().hex
    new_source = replace(source, source_id=new_id, data=data, page_count=1,
                         page_sizes=((float(reader.pages[0].mediabox.width), float(reader.pages[0].mediabox.height)),),
                         page_annotations=(), bookmarks=remap_source_bookmarks(source.bookmarks, [ref.page_index]))
    updated = tuple(replace(p, source_id=new_id, page_index=0) if p.uid == page_uid else p for p in pages)
    _check_cancel(cancel)
    if progress: progress(100, "Bestehender Text wurde ersetzt")
    return TextReplacementResult((new_source,), updated, (page_uid,))
