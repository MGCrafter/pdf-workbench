"""Local, immutable scan tools and final image/text/navigation exports.

Page detection deliberately returns candidates; callers must review them before
deletion. Scan cleanup is an explicitly rasterizing operation. It does not
promise to retain text, widgets, links, signatures or editable annotations.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import io
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from uuid import uuid4

from PIL import Image, ImageFilter, ImageMath, ImageOps
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link
from pypdf.generic import ArrayObject, Fit, NameObject

from .bookmarks import validate_bookmarks, write_bookmarks
from .annotations import visible_page_box
from .core import (OperationCancelled, PageRef, PdfWorkbenchError, _build_writer,
                   _check_cancel, _protect_destination)


def pdf_engine():
    try:
        if sys.platform == "win32":
            # Qt ships the C++ runtime used by PyMuPDF on portable Windows.
            # Load it before MuPDF's extension, including command-line tests.
            from PyQt6 import QtCore  # noqa: F401
        import pymupdf
        return pymupdf
    except ImportError as error:
        raise PdfWorkbenchError("Für diese Funktion fehlt PyMuPDF. Bitte eine vollständige PDF-Workbench-Installation verwenden.") from error


def workspace_pdf(sources, pages, cancel=None, *, bookmarks=()):
    writer = _build_writer(sources, tuple(pages), "PDF Workbench", cancel, bookmarks=bookmarks)
    stream = io.BytesIO(); writer.write(stream)
    _check_cancel(cancel)
    return stream.getvalue()


@dataclass(frozen=True)
class PublishedFile:
    path: Path
    identity: tuple[int, int]
    size: int
    digest: str


def _unchanged(record):
    """Do not delete replacements OR files edited in place during cancellation."""
    try:
        status = record.path.lstat()
        if (not stat.S_ISREG(status.st_mode) or (status.st_dev, status.st_ino) != record.identity
                or status.st_size != record.size):
            return False
        with record.path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != record.identity:
                return False
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        after = record.path.lstat()
        return ((after.st_dev, after.st_ino) == record.identity
                and digest == record.digest and after.st_mtime_ns == status.st_mtime_ns)
    except OSError:
        return False


def discard_published(records):
    retained = []
    for record in reversed(tuple(records)):
        if _unchanged(record):
            try: record.path.unlink()
            except OSError: retained.append(str(record.path))
        elif os.path.lexists(record.path):
            retained.append(str(record.path))
    return tuple(retained)


def publish_bytes(data, destination, sources=(), cancel=None):
    """Exclusive creation, fsync, and identity/content-aware failure cleanup."""
    target = Path(destination).expanduser().absolute()
    _protect_destination(target, sources if hasattr(sources, "values") else {})
    _check_cancel(cancel)
    digest = hashlib.sha256(data).hexdigest()
    record = None
    try:
        with target.open("xb") as stream:
            status = os.fstat(stream.fileno())
            record = PublishedFile(target, (status.st_dev, status.st_ino), len(data), digest)
            for start in range(0, len(data), 1024 * 1024):
                _check_cancel(cancel); stream.write(data[start:start + 1024 * 1024])
            stream.flush(); os.fsync(stream.fileno())
        _check_cancel(cancel)
        if not _unchanged(record):
            raise PdfWorkbenchError(f"Die neue Datei wurde zwischenzeitlich verändert und bleibt erhalten: {target}")
        return record
    except BaseException as error:
        # An incomplete own file cannot match the full digest. Capture its
        # current bytes only while the original descriptor identity survives.
        if record is not None:
            try:
                current = target.lstat()
                if (current.st_dev, current.st_ino) == record.identity and current.st_size < len(data):
                    partial = target.read_bytes()
                    # Delete only an exact prefix we wrote, never a foreign edit.
                    if partial == data[:len(partial)]:
                        record = replace(record, size=len(partial), digest=hashlib.sha256(partial).hexdigest())
            except OSError: pass
            retained = discard_published((record,))
            if retained:
                message = "Das Ziel wurde zwischenzeitlich verändert und bleibt erhalten: " + ", ".join(retained)
                if isinstance(error, OperationCancelled): raise OperationCancelled(str(error) + "\n" + message) from error
                raise PdfWorkbenchError(str(error) + "\n" + message) from error
        if isinstance(error, (PdfWorkbenchError, KeyboardInterrupt, SystemExit)): raise
        raise PdfWorkbenchError(f"Die neue Datei konnte nicht gespeichert werden: {error}") from error


def _render(page, dpi, *, grayscale=False, cancel=None):
    if type(dpi) is not int or not 72 <= dpi <= 600:
        raise PdfWorkbenchError("Die Auflösung muss zwischen 72 und 600 dpi liegen.")
    width, height = page.rect.width * dpi / 72, page.rect.height * dpi / 72
    if (not all(math.isfinite(value) and value > 0 for value in (width, height))
            or math.ceil(width) * math.ceil(height) > 40_000_000 or max(width, height) > 15000):
        raise PdfWorkbenchError("Diese Seite ist für die gewählte Auflösung zu groß. Bitte weniger dpi wählen.")
    _check_cancel(cancel)
    fitz = pdf_engine()
    pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72),
                            colorspace=fitz.csGRAY if grayscale else fitz.csRGB, alpha=False, annots=True)
    _check_cancel(cancel)
    return Image.frombytes("L" if grayscale else "RGB", (pixmap.width, pixmap.height), pixmap.samples)


def export_images(sources, pages, directory, *, image_format="png", dpi=150, quality=90,
                  prefix="Seite", cancel=None, progress=None):
    pages = tuple(pages); destination = Path(directory).expanduser().absolute()
    if not destination.is_dir() or not pages:
        raise PdfWorkbenchError("Bitte Arbeitsseiten und einen vorhandenen Zielordner auswählen.")
    if image_format not in ("png", "jpeg") or type(quality) is not int or not 1 <= quality <= 100:
        raise PdfWorkbenchError("Bitte PNG oder JPEG und eine Qualität von 1 bis 100 auswählen.")
    if not re.fullmatch(r"[\w -]{1,80}", prefix):
        raise PdfWorkbenchError("Der Dateipräfix darf nur Buchstaben, Zahlen, Leerzeichen, Bindestriche und Unterstriche enthalten.")
    extension = "jpg" if image_format == "jpeg" else "png"
    paths = tuple(destination / f"{prefix}-{index + 1:04d}.{extension}" for index in range(len(pages)))
    for path in paths: _protect_destination(path, sources)
    records = []
    try:
        with pdf_engine().open(stream=workspace_pdf(sources, pages, cancel), filetype="pdf") as document:
            for index, page in enumerate(document):
                image = _render(page, dpi, cancel=cancel)
                stream = io.BytesIO()
                image.save(stream, format=image_format.upper(), dpi=(dpi, dpi),
                           **({"quality": quality, "optimize": True} if image_format == "jpeg" else {}))
                records.append(publish_bytes(stream.getvalue(), paths[index], sources, cancel))
                if progress: progress(round((index + 1) * 100 / len(pages)), f"Bild {index + 1} von {len(pages)} gespeichert …")
        _check_cancel(cancel)
        return paths
    except BaseException as error:
        retained = discard_published(records)
        if retained:
            detail = "\nZwischenzeitlich veränderte Dateien bleiben erhalten: " + ", ".join(retained)
            if isinstance(error, OperationCancelled): raise OperationCancelled(str(error) + detail) from error
            raise PdfWorkbenchError(str(error) + detail) from error
        raise


def export_text(sources, pages, destination, *, cancel=None, progress=None):
    target = Path(destination)
    if target.suffix.lower() != ".txt": raise PdfWorkbenchError("Bitte die Dateiendung .txt verwenden.")
    texts = []
    with pdf_engine().open(stream=workspace_pdf(sources, pages, cancel), filetype="pdf") as document:
        for index, page in enumerate(document):
            _check_cancel(cancel); texts.append(page.get_text("text", sort=True))
            if progress: progress(round((index + 1) * 90 / len(document)), f"Text von Seite {index + 1} lesen …")
    # Form feeds delimit pages even when they contain no extractable text.
    return publish_bytes("\f\n".join(texts).encode("utf-8"), target, sources, cancel).path


@dataclass(frozen=True)
class PageCandidate:
    page_uid: str
    page_number: int
    reason: str
    ink_fraction: float
    duplicate_of: int | None = None
    preview_png: bytes = b""


def find_page_candidates(sources, pages, *, find_blank=True, find_duplicates=True,
                         blank_fraction=.0005, cancel=None, progress=None):
    if not (isinstance(blank_fraction, (int, float)) and not isinstance(blank_fraction, bool)
            and math.isfinite(blank_fraction) and 0 <= blank_fraction <= .02):
        raise PdfWorkbenchError("Die Leerseitentoleranz muss zwischen 0 und 2 Prozent liegen.")
    pages = tuple(pages); seen = {}; result = []
    with pdf_engine().open(stream=workspace_pdf(sources, pages, cancel), filetype="pdf") as document:
        for index, page in enumerate(document):
            image = _render(page, 90, grayscale=True, cancel=cancel)
            histogram = image.histogram(); ink = sum(histogram[:245]) / (image.width * image.height)
            # Byte-identical rendered images at this resolution are candidates,
            # not a proof that hidden data or finer details are equal.
            key = (image.size, round(page.rect.width, 3), round(page.rect.height, 3), hashlib.sha256(image.tobytes()).digest())
            duplicate = seen.get(key); seen.setdefault(key, index + 1)
            blank = ink <= blank_fraction and not page.get_text().strip()
            if (find_blank and blank) or (find_duplicates and duplicate is not None):
                reasons = []
                if find_blank and blank: reasons.append("Möglicherweise leer")
                if find_duplicates and duplicate is not None: reasons.append(f"Visuell gleich wie Seite {duplicate}")
                image.thumbnail((260, 340)); thumbnail = io.BytesIO(); image.save(thumbnail, "PNG")
                result.append(PageCandidate(pages[index].uid, index + 1, "; ".join(reasons), ink,
                                            duplicate if find_duplicates else None, thumbnail.getvalue()))
            if progress: progress(round((index + 1) * 100 / len(pages)), f"Seite {index + 1} von {len(pages)} prüfen …")
    return tuple(result)


def duplex_order(pages, front_ids, back_ids, *, reverse_backs=False, backs_rotation=0):
    """Merge explicit disjoint stacks; one final unmatched front is supported."""
    pages = tuple(pages); fronts = tuple(front_ids); backs = tuple(back_ids)
    lookup = {page.uid: page for page in pages}
    if (len(lookup) != len(pages) or not fronts or len(fronts) not in (len(backs), len(backs) + 1)
            or len(set((*fronts, *backs))) != len(fronts) + len(backs)
            or set((*fronts, *backs)) != set(lookup)):
        raise PdfWorkbenchError("Vorder- und Rückseiten müssen alle Arbeitsseiten genau einmal enthalten. Erlaubt sind gleich viele Seiten oder eine letzte Vorderseite ohne Rückseite.")
    if type(backs_rotation) is not int or backs_rotation not in (0, 90, 180, 270):
        raise PdfWorkbenchError("Bitte eine Rückseitendrehung von 0, 90, 180 oder 270 Grad wählen.")
    if reverse_backs: backs = tuple(reversed(backs))
    result = []
    for index, uid in enumerate(fronts):
        result.append(lookup[uid])
        if index < len(backs):
            back = lookup[backs[index]]
            result.append(replace(back, rotation=(back.rotation + backs_rotation) % 360))
    return tuple(result)


@dataclass(frozen=True)
class ScanOptions:
    dpi: int = 200
    deskew: bool = True
    clean_background: bool = False
    auto_orientation: bool = False
    rotation: int = 0

    def __post_init__(self):
        if type(self.dpi) is not int or not 72 <= self.dpi <= 600:
            raise PdfWorkbenchError("Die Scanauflösung muss zwischen 72 und 600 dpi liegen.")
        if type(self.rotation) is not int or self.rotation not in (0, 90, 180, 270):
            raise PdfWorkbenchError("Die Scandrehung ist ungültig.")
        if any(type(value) is not bool for value in (self.deskew, self.clean_background, self.auto_orientation)):
            raise PdfWorkbenchError("Die Scaneinstellungen sind ungültig.")


@dataclass(frozen=True)
class ScanResult:
    sources: tuple
    pages: tuple[PageRef, ...]
    processed: int
    warnings: tuple[str, ...]
    angles: tuple[float, ...] = ()


def deskew_angle(image, cancel=None):
    """Conservative bounded projection search, with a minimum improvement."""
    sample = ImageOps.grayscale(image); sample.thumbnail((700, 700))
    binary = sample.point(lambda pixel: 255 if pixel < 160 else 0)
    if sum(binary.histogram()[1:]) < binary.width * binary.height * .001: return 0.
    def score(angle):
        _check_cancel(cancel)
        rotated = binary.rotate(angle, Image.Resampling.NEAREST, expand=False, fillcolor=0)
        return sum((sum(rotated.crop((0, y, rotated.width, y + 1)).histogram()[1:])) ** 2
                   for y in range(rotated.height))
    baseline = score(0); best_angle = 0.; best = baseline
    for step in range(-10, 11):
        angle = step * .5
        if angle == 0: continue
        candidate = score(angle)
        if candidate > best: best, best_angle = candidate, angle
    return best_angle if best > baseline * 1.08 and abs(best_angle) < 5 else 0.


def _orientation(image, cancel):
    from .ocr import _capture_tool, discover_engine
    engine = discover_engine(cancel=cancel)
    if engine is None: return 0, "Automatische Ausrichtung übersprungen: Tesseract ist nicht installiert."
    if not engine.tessdata_dir or not (engine.tessdata_dir / "osd.traineddata").is_file():
        return 0, "Automatische Ausrichtung übersprungen: osd.traineddata fehlt lokal."
    with tempfile.TemporaryDirectory(prefix="pdf-workbench-orientation-") as directory:
        path = Path(directory) / "orientation.png"; image.save(path, "PNG", dpi=(150, 150))
        try:
            stdout, stderr = _capture_tool([str(engine.executable), str(path), "stdout", "--psm", "0",
                                           "--tessdata-dir", str(engine.tessdata_dir)], cancel, timeout=30)
            rotation = re.search(r"Rotate:\s*(0|90|180|270)\b", stdout)
            confidence = re.search(r"Orientation confidence:\s*([\d.]+)", stdout)
            if rotation and confidence and float(confidence.group(1)) >= 4:
                return int(rotation.group(1)), ""
        except OperationCancelled: raise
        except Exception: pass
    return 0, "Automatische Ausrichtung unsicher: Seite unverändert ausgerichtet."


def prepare_scans(sources, pages, options=ScanOptions(), target_ids=None, cancel=None, progress=None):
    from .page_marks import target_pages
    pages = tuple(pages); targets = target_pages(pages, target_ids)
    replacements = {}; new_sources = []; warnings = []; angles = []
    with pdf_engine().open(stream=workspace_pdf(sources, pages, cancel), filetype="pdf") as document:
        for ordinal, (index, ref) in enumerate(targets):
            page = document[index]; image = _render(page, options.dpi, cancel=cancel)
            width, height = page.rect.width, page.rect.height
            rotation = options.rotation
            if options.auto_orientation:
                recognized, warning = _orientation(image, cancel); rotation = (rotation + recognized) % 360
                if warning: warnings.append(f"Seite {index + 1}: {warning}")
            if rotation:
                image = image.rotate(-rotation, expand=True)
                if rotation % 180: width, height = height, width
            angle = deskew_angle(image, cancel) if options.deskew else 0.
            angles.append(angle)
            if angle:
                # Expand and scale the page too: never crop away corner content.
                previous = image.size
                image = image.rotate(angle, Image.Resampling.BICUBIC, expand=True, fillcolor="white")
                width *= image.width / previous[0]; height *= image.height / previous[1]
            if options.clean_background:
                # Explicit grayscale cleanup; broad background variations are
                # estimated locally, while dark strokes are retained.
                gray = ImageOps.grayscale(image)
                background = gray.filter(ImageFilter.GaussianBlur(max(3, options.dpi / 12)))
                image = ImageMath.lambda_eval(lambda args: args["gray"] * 255 / (args["background"] + 1),
                                             gray=gray, background=background).convert("L")
                image = image.point(lambda pixel: 255 if pixel > 225 else pixel)
            _check_cancel(cancel)
            raster = io.BytesIO(); image.convert("RGB").save(raster, "PNG")
            fitz = pdf_engine()
            with fitz.open() as output:
                added = output.new_page(width=width, height=height)
                added.insert_image(added.rect, stream=raster.getvalue())
                data = output.tobytes(garbage=4, deflate=True)
            source = sources[ref.source_id]
            new_source = replace(source, source_id=uuid4().hex, data=data, page_count=1,
                                 page_sizes=((width, height),), page_annotations=(), bookmarks=(), bookmark_warnings=())
            new_sources.append(new_source)
            replacements[ref.uid] = replace(ref, source_id=new_source.source_id, page_index=0, rotation=0, annotations=())
            if progress: progress(round((ordinal + 1) * 100 / len(targets)), f"Scan {ordinal + 1} von {len(targets)} aufbereiten …")
    return ScanResult(tuple(new_sources), tuple(replacements.get(ref.uid, ref) for ref in pages), len(targets), tuple(warnings), tuple(angles))


@dataclass(frozen=True)
class InternalLink:
    page_uid: str
    target_uid: str
    rect: tuple[float, float, float, float]

    def __post_init__(self):
        if not isinstance(self.page_uid, str) or not self.page_uid or not isinstance(self.target_uid, str) or not self.target_uid:
            raise PdfWorkbenchError("Die Linkseiten sind ungültig.")
        try:
            values = tuple(self.rect)
            valid = (len(values) == 4 and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in values)
                     and values[0] < values[2] and values[1] < values[3])
        except (ValueError, TypeError): valid = False
        if not valid:
            raise PdfWorkbenchError("Der Linkbereich muss innerhalb der sichtbaren Seite liegen und eine positive Größe haben.")
        object.__setattr__(self, "rect", values)


def _link_pdf_rect(page, rect):
    """Visible normalized top-left coordinates -> raw PDF user space.

    Compute this explicitly: MuPDF's page matrices currently omit UserUnit and
    CropBox translations for rotated pages, causing misplaced annotations.
    """
    left, bottom, right, top = visible_page_box(page); width, height = right-left, top-bottom
    rotation = page.rotation % 360
    def raw(x, y):
        if rotation == 0: return left+x*width, top-y*height
        if rotation == 90: return left+y*width, bottom+x*height
        if rotation == 180: return right-x*width, bottom+y*height
        return right-y*width, top-x*height
    first = raw(rect[0], rect[1]); second = raw(rect[2], rect[3])
    return (min(first[0], second[0]), min(first[1], second[1]), max(first[0], second[0]), max(first[1], second[1]))


def export_internal_links(sources, pages, destination, links, *, bookmarks=(), cancel=None, progress=None):
    pages = tuple(pages); lookup = {page.uid: index for index, page in enumerate(pages)}
    links = tuple(links)
    if not links or len(links) > 5000 or len(lookup) != len(pages):
        raise PdfWorkbenchError("Bitte 1 bis 5.000 interne Links für gültige Arbeitsseiten anlegen.")
    if Path(destination).suffix.lower() != ".pdf": raise PdfWorkbenchError("Bitte einen PDF-Dateinamen verwenden.")
    writer = _build_writer(sources, pages, "PDF mit internen Links", cancel, bookmarks=bookmarks)
    for index, link in enumerate(links):
        _check_cancel(cancel)
        if not isinstance(link, InternalLink) or link.page_uid not in lookup or link.target_uid not in lookup:
            raise PdfWorkbenchError("Ein interner Link verweist auf eine fehlende Seite.")
        page_index = lookup[link.page_uid]
        annotation = writer.add_annotation(page_index, Link(rect=_link_pdf_rect(writer.pages[page_index], link.rect),
                                                           target_page_index=lookup[link.target_uid], fit=Fit.fit()))
        annotation[NameObject("/Dest")] = ArrayObject([writer.pages[lookup[link.target_uid]].indirect_reference, NameObject("/Fit")])
        if progress: progress(round((index + 1) * 90 / len(links)), f"Link {index + 1} von {len(links)} setzen …")
    stream = io.BytesIO(); writer.write(stream)
    return publish_bytes(stream.getvalue(), destination, sources, cancel).path


def export_table_of_contents(sources, pages, destination, bookmarks, *, title="Inhaltsverzeichnis", cancel=None, progress=None):
    pages = tuple(pages); lookup = {page.uid: index for index, page in enumerate(pages)}
    validate_bookmarks(bookmarks, set(lookup))
    if not bookmarks: raise PdfWorkbenchError("Bitte zuerst Lesezeichen für das Inhaltsverzeichnis anlegen.")
    if not isinstance(title, str) or not title.strip() or len(title) > 80 or any(ord(char) < 32 for char in title):
        raise PdfWorkbenchError("Der Titel benötigt 1 bis 80 Zeichen ohne Steuerzeichen.")
    if Path(destination).suffix.lower() != ".pdf": raise PdfWorkbenchError("Bitte einen PDF-Dateinamen verwenden.")
    fitz = pdf_engine(); font_path = Path(__file__).parent / "fonts/LiberationSans-Regular.ttf"
    font = fitz.Font(fontfile=str(font_path))
    checked_characters = set()
    def check_glyphs(text):
        for character in set(text) - checked_characters:
            if not font.has_glyph(ord(character)):
                raise PdfWorkbenchError(f"Die Schrift des Inhaltsverzeichnisses kann das Zeichen U+{ord(character):04X} nicht darstellen. Bitte den Titel oder betroffenen Lesezeichentext ändern; es wurde keine Datei gespeichert.")
            checked_characters.add(character)
    check_glyphs(title)
    entries = []
    def visit(items, level):
        for item in items:
            _check_cancel(cancel); check_glyphs(item.title)
            # Thirty-two bookmark levels fit using a capped visual indent.
            available = 460 - min(level, 12) * 12
            lines = []; line = ""
            for character in item.title:
                if line and font.text_length(line + character, fontsize=11) > available:
                    lines.append(line.rstrip()); line = ""
                line += character
            if line: lines.append(line.rstrip())
            entries.append((level, item, tuple(lines)))
            visit(item.children, level + 1)
    visit(bookmarks, 0)
    sheets = [[]]; y = 90
    for level, item, lines in entries:
        height = len(lines) * 15 + 8
        if height <= 695 and y + height > 785: sheets.append([]); y = 90
        remaining = lines
        while remaining:
            capacity = int((785-y-8) // 15)
            if capacity < 1: sheets.append([]); y = 90; continue
            chunk, remaining = remaining[:capacity], remaining[capacity:]
            sheets[-1].append((y, (level, item, chunk))); y += len(chunk)*15+8
            if remaining: sheets.append([]); y = 90
    offset = len(sheets)
    pending_links = []
    # Author only the new contents sheets with MuPDF. Existing PDF pages and
    # editable Workbench appearance fingerprints must not be rewritten by it.
    with fitz.open() as document:
        for index in range(offset): document.new_page(width=595.276, height=841.89)
        for sheet_index, rows in enumerate(sheets):
            _check_cancel(cancel); sheet = document[sheet_index]
            sheet.insert_font(fontname="PWBFont", fontfile=str(font_path))
            # Long headings are fitted to the printable width.
            title_size = min(24., 490 * 24 / max(1, font.text_length(title, fontsize=24)))
            sheet.insert_text((48, 52), title, fontsize=title_size, fontname="PWBFont", color=(.12, .32, .31))
            for y, (level, item, lines) in rows:
                left = 48 + min(level, 12) * 12
                for line_index, line in enumerate(lines):
                    sheet.insert_text((left, y + line_index * 15), line, fontsize=11, fontname="PWBFont")
                if item.page_uid is not None:
                    number = lookup[item.page_uid] + offset + 1
                    label = str(number)
                    sheet.insert_text((547 - font.text_length(label, fontsize=11), y), label, fontsize=11, fontname="PWBFont")
                    pending_links.append((sheet_index, (left, sheet.rect.height-y-(len(lines)-1)*15-5, 550, sheet.rect.height-y+12), number-1))
            sheet.insert_text((48, 812), f"{sheet_index + 1} / {offset}", fontsize=9, fontname="PWBFont", color=(.35, .4, .4))
            if progress: progress(round((sheet_index + 1) * 90 / offset), "Klickbares Inhaltsverzeichnis erstellen …")
        data = document.tobytes(garbage=4, deflate=True)
    # Explicit /Fit targets work for rotated/cropped/UserUnit pages as well;
    # renderer-specific /XYZ coordinates can otherwise land outside their view.
    contents = PdfReader(io.BytesIO(data))
    writer = _build_writer(sources, pages, title, cancel, bookmarks=())
    for index, sheet in enumerate(contents.pages): writer.insert_page(sheet, index)
    toc_pages = tuple(PageRef("toc-"+uuid4().hex, "toc", index) for index in range(offset))
    write_bookmarks(writer, (*toc_pages, *pages), bookmarks, cancel)
    for page_index, rect, target in pending_links:
        _check_cancel(cancel)
        annotation = writer.add_annotation(page_index, Link(rect=rect, target_page_index=target, fit=Fit.fit()))
        annotation[NameObject("/Dest")] = ArrayObject([writer.pages[target].indirect_reference, NameObject("/Fit")])
    stream = io.BytesIO(); writer.write(stream)
    return publish_bytes(stream.getvalue(), destination, sources, cancel).path
