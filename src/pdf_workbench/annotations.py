"""Immutable user annotations and PDF appearances, with no GUI dependency.

Coordinates are fractions of the visible source CropBox/MediaBox intersection,
with its intrinsic /Rotate already applied, before any workspace rotation.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
from typing import Literal, Sequence, TYPE_CHECKING

from pypdf.generic import (
    ArrayObject, BooleanObject, ByteStringObject, DecodedStreamObject, DictionaryObject,
    FloatObject, IndirectObject, NameObject, NullObject, NumberObject,
    StreamObject, TextStringObject,
)

if TYPE_CHECKING:
    from pypdf import PageObject, PdfWriter

Point = tuple[float, float]
Rect = tuple[float, float, float, float]


class AnnotationError(ValueError):
    """Invalid user annotation, with a message suitable for the UI."""


def _coordinate(value: object) -> float:
    if isinstance(value, bool):
        raise AnnotationError("Anmerkungskoordinaten müssen Zahlen zwischen 0 und 1 sein.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AnnotationError("Anmerkungskoordinaten müssen Zahlen zwischen 0 und 1 sein.") from exc
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise AnnotationError("Anmerkungskoordinaten müssen endlich und zwischen 0 und 1 sein.")
    return result


def _rect(value: Sequence[float]) -> Rect:
    try:
        if len(value) != 4:
            raise AnnotationError("Ein Anmerkungsrechteck benötigt vier Koordinaten.")
        left, top, right, bottom = (_coordinate(v) for v in value)
    except TypeError as exc:
        raise AnnotationError("Das Anmerkungsrechteck ist ungültig.") from exc
    if left >= right or top >= bottom:
        raise AnnotationError("Ein Anmerkungsrechteck muss eine positive Breite und Höhe haben.")
    return left, top, right, bottom


@dataclass(frozen=True)
class PageAnnotation:
    uid: str
    kind: Literal["comment", "highlight", "signature"]
    rect: Rect
    text: str = ""
    rects: tuple[Rect, ...] = ()
    strokes: tuple[tuple[Point, ...], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.uid, str) or not self.uid.strip():
            raise AnnotationError("Eine Anmerkung benötigt eine eindeutige Kennung.")
        if self.kind not in ("comment", "highlight", "signature"):
            raise AnnotationError("Dieser Anmerkungstyp wird nicht unterstützt.")
        if not isinstance(self.text, str):
            raise AnnotationError("Der Anmerkungstext muss Text sein.")
        object.__setattr__(self, "rect", _rect(self.rect))
        try:
            object.__setattr__(self, "rects", tuple(_rect(value) for value in self.rects))
            strokes = []
            for stroke in self.strokes:
                points = []
                for point in stroke:
                    if len(point) != 2:
                        raise AnnotationError("Ein Unterschriftspunkt benötigt zwei Koordinaten.")
                    points.append((_coordinate(point[0]), _coordinate(point[1])))
                if len(points) < 2 or len(set(points)) < 2:
                    raise AnnotationError("Jeder Unterschriftsstrich benötigt mindestens zwei verschiedene Punkte.")
                strokes.append(tuple(points))
            object.__setattr__(self, "strokes", tuple(strokes))
        except TypeError as exc:
            raise AnnotationError("Die Anmerkungsgeometrie ist ungültig.") from exc
        if self.kind == "comment" and not self.text.strip():
            raise AnnotationError("Bitte gib einen Kommentartext ein.")
        if self.kind == "highlight" and not self.rects:
            raise AnnotationError("Eine Textmarkierung benötigt mindestens ein ausgewähltes Textrechteck.")
        if self.kind == "signature" and not self.strokes:
            raise AnnotationError("Bitte zeichne zuerst eine Unterschrift.")


def rotate_annotation(annotation: PageAnnotation, degrees: int) -> PageAnnotation:
    """Rotate normalized display coordinates, including local signature strokes."""
    if degrees % 90:
        raise AnnotationError("Die Anmerkungsrotation muss ein Vielfaches von 90 Grad sein.")
    angle = degrees % 360
    if not angle:
        return annotation
    def point(x: float, y: float) -> Point:
        if angle == 90:
            return 1-y, x
        if angle == 180:
            return 1-x, 1-y
        return y, 1-x
    def rect(value: Rect) -> Rect:
        points = [point(value[0], value[1]), point(value[2], value[3])]
        return _bounds(points)
    return replace(annotation, rect=rect(annotation.rect),
                   rects=tuple(rect(value) for value in annotation.rects),
                   strokes=tuple(tuple(point(*p) for p in stroke) for stroke in annotation.strokes))


def visible_page_box(page: PageObject) -> Rect:
    """Return the finite visible intersection in default PDF user space."""
    media = tuple(float(v) for v in page.mediabox)
    crop = tuple(float(v) for v in page.cropbox)
    if not all(math.isfinite(v) for v in (*media, *crop)):
        raise AnnotationError("Die sichtbare PDF-Seitenfläche hat ungültige Abmessungen.")
    left, bottom = max(media[0], crop[0]), max(media[1], crop[1])
    right, top = min(media[2], crop[2]), min(media[3], crop[3])
    if left >= right or bottom >= top:
        raise AnnotationError("CropBox und MediaBox besitzen keine gültige gemeinsame Seitenfläche.")
    return left, bottom, right, top


def normalized_point_to_pdf(page: PageObject, x: float, y: float) -> Point:
    """Map a normalized visible top-left point to default PDF user space."""
    x, y = _coordinate(x), _coordinate(y)
    left, bottom, right, top = visible_page_box(page)
    width, height = right - left, top - bottom
    rotation = page.rotation % 360
    if rotation == 0:
        return left + x * width, top - y * height
    if rotation == 90:
        return left + y * width, bottom + x * height
    if rotation == 180:
        return right - x * width, bottom + y * height
    if rotation == 270:
        return right - y * width, top - x * height
    raise AnnotationError("Die PDF-Seitenrotation muss ein Vielfaches von 90 Grad sein.")


def _corners(page: PageObject, rect: Rect) -> tuple[Point, Point, Point, Point]:
    left, top, right, bottom = rect
    return tuple(normalized_point_to_pdf(page, x, y) for x, y in (
        (left, top), (right, top), (left, bottom), (right, bottom),
    ))


def _bounds(points: Sequence[Point]) -> Rect:
    return min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)


def _array(values: Sequence[float]) -> ArrayObject:
    return ArrayObject(FloatObject(value) for value in values)


def _number(value: float) -> str:
    return f"{value:.7f}".rstrip("0").rstrip(".") or "0"


def _appearance(writer: PdfWriter, bounds: Rect, commands: list[str], transparent: bool = False):
    left, bottom, right, top = bounds
    stream = DecodedStreamObject()
    stream.set_data(("q\n" + "\n".join(commands) + "\nQ\n").encode("ascii"))
    resources = DictionaryObject()
    if transparent:
        resources[NameObject("/ExtGState")] = DictionaryObject({NameObject("/GS0"): DictionaryObject({
            NameObject("/Type"): NameObject("/ExtGState"),
            NameObject("/ca"): FloatObject(.32),
            NameObject("/BM"): NameObject("/Multiply"),
        })})
    stream.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                   NameObject("/FormType"): NumberObject(1), NameObject("/BBox"): _array((0, 0, right-left, top-bottom)),
                   NameObject("/Resources"): resources})
    # PDF stream objects must be indirect; pypdf exposes no public stream-adder.
    return DictionaryObject({NameObject("/N"): writer._add_object(stream)})


def _annotation_fingerprint(pdf: DictionaryObject) -> str:
    """Bind our editable model to its complete PDF annotation representation.

    Object numbers and stream compression are serialization details. Page /P
    references also change when cloning or duplicating pages. All other fields,
    decoded appearance streams and their resources must remain understood.
    This detects external edits; it is not an authenticated digital signature.
    """
    digest = hashlib.sha256()
    active = set()
    nodes = 0
    data_size = 0

    def emit(tag: bytes, data: bytes = b"") -> None:
        nonlocal data_size
        data_size += len(data)
        if data_size > 32 * 1024 * 1024:
            raise AnnotationError("Die Anmerkungsdaten sind zu groß für einen sicheren Modellimport.")
        digest.update(tag + str(len(data)).encode("ascii") + b":" + data)

    def visit(value, path=(), depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 1_000_000 or depth > 32:
            raise AnnotationError("Die Anmerkungsstruktur ist zu komplex für einen sicheren Modellimport.")
        if isinstance(value, IndirectObject):
            value = value.get_object()
        if isinstance(value, (DictionaryObject, ArrayObject)):
            identity = id(value)
            if identity in active:
                raise AnnotationError("Die Anmerkungsstruktur enthält einen zyklischen Verweis.")
            active.add(identity)
            try:
                if isinstance(value, DictionaryObject):
                    stream = isinstance(value, StreamObject)
                    emit(b"stream" if stream else b"dict")
                    for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
                        if path == () and key == "/P":
                            continue
                        if path == ("/PDFWorkbench",) and key == "/Fingerprint":
                            continue
                        if stream and key in ("/Length", "/Filter", "/DecodeParms"):
                            continue
                        emit(b"key", str(key).encode("utf-8"))
                        visit(item, (*path, str(key)), depth + 1)
                    if stream:
                        emit(b"decoded", value.get_data())
                    emit(b"enddict")
                else:
                    emit(b"array")
                    for index, item in enumerate(value):
                        visit(item, (*path, index), depth + 1)
                    emit(b"endarray")
            finally:
                active.remove(identity)
        elif isinstance(value, BooleanObject):
            emit(b"bool", b"true" if value.value else b"false")
        elif isinstance(value, NullObject):
            emit(b"null")
        elif isinstance(value, NameObject):
            emit(b"name", str(value).encode("utf-8"))
        elif isinstance(value, TextStringObject):
            emit(b"text", str(value).encode("utf-8"))
        elif isinstance(value, ByteStringObject):
            emit(b"bytes", bytes(value))
        elif isinstance(value, (NumberObject, FloatObject)):
            if not math.isfinite(float(value)):
                raise AnnotationError("Die Anmerkungsdaten enthalten eine ungültige Zahl.")
            # Match pypdf's written precision, then unify 0.0/0 and 100.0/100.
            number = value.myrepr() if isinstance(value, FloatObject) else str(int(value))
            if "." in number:
                number = number.rstrip("0").rstrip(".")
            emit(b"number", ("0" if number in ("", "-0") else number).encode("ascii"))
        else:
            raise AnnotationError("Die Anmerkungsdaten enthalten ein unbekanntes PDF-Objekt.")

    visit(pdf)
    return digest.hexdigest()


def add_pdf_annotations(writer: PdfWriter, output_page: PageObject, source_page: PageObject,
                        annotations: Sequence[PageAnnotation], workspace_rotation: int = 0) -> None:
    """Append real PDF annotations; preserve the source's existing annotations."""
    for annotation in annotations:
        if not isinstance(annotation, PageAnnotation):
            raise AnnotationError("Eine Seitenanmerkung ist ungültig.")
        rects = annotation.rects if annotation.kind == "highlight" else (annotation.rect,)
        quads = [_corners(source_page, rect) for rect in rects]
        bounds = _bounds([point for quad in quads for point in quad])
        left, bottom, right, top = bounds
        def point_command(point: Point) -> str:
            return _number(point[0]-left) + " " + _number(point[1]-bottom)
        def local_point(x: float, y: float) -> Point:
            r = annotation.rect
            return normalized_point_to_pdf(source_page, r[0]+x*(r[2]-r[0]), r[1]+y*(r[3]-r[1]))
        pdf = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Rect"): _array(bounds),
                                NameObject("/NM"): TextStringObject("PDFWorkbench:" + annotation.uid),
                                NameObject("/T"): TextStringObject("PDF Workbench"),
                                NameObject("/F"): NumberObject(4),
                                NameObject("/Contents"): TextStringObject(annotation.text)})
        commands = []
        if annotation.kind == "highlight":
            pdf[NameObject("/Subtype")] = NameObject("/Highlight")
            pdf[NameObject("/QuadPoints")] = _array([v for quad in quads for p in quad for v in p])
            pdf[NameObject("/C")] = _array((1, .84, 0))
            pdf[NameObject("/CA")] = FloatObject(.32)
            commands.extend(["/GS0 gs", "1 0.84 0 rg"])
            for tl, tr, bl, br in quads:
                commands.extend([point_command(tl)+" m", point_command(tr)+" l", point_command(br)+" l", point_command(bl)+" l", "h f"])
        elif annotation.kind == "signature":
            pdf[NameObject("/Subtype")] = NameObject("/Ink")
            pdf[NameObject("/C")] = _array((.08, .16, .33))
            width = max(.7, min(2.5, min(right-left, top-bottom)*.025))
            pdf[NameObject("/BS")] = DictionaryObject({NameObject("/W"): FloatObject(width), NameObject("/S"): NameObject("/S")})
            mapped = [tuple(local_point(*point) for point in stroke) for stroke in annotation.strokes]
            pdf[NameObject("/InkList")] = ArrayObject(_array([v for point in stroke for v in point]) for stroke in mapped)
            commands.extend(["0.08 0.16 0.33 RG", "1 J 1 j", _number(width)+" w"])
            for stroke in mapped:
                commands.append(point_command(stroke[0])+" m")
                commands.extend(point_command(point)+" l" for point in stroke[1:])
                commands.append("S")
        else:
            pdf[NameObject("/Subtype")] = NameObject("/Text")
            pdf[NameObject("/Name")] = NameObject("/Comment")
            pdf[NameObject("/Open")] = BooleanObject(False)
            pdf[NameObject("/C")] = _array((1, .82, .15))
            commands.extend(["1 0.82 0.15 rg", "0.35 0.26 0.04 RG", "0.7 w"])
            # Rotationally symmetric comment marker remains pixel-stable after
            # export/reopen, where workspace rotation becomes intrinsic.
            commands.append(point_command(local_point(.5,.05))+" m")
            for curve in (((.748528,.05),(.95,.251472),(.95,.5)),
                          ((.95,.748528),(.748528,.95),(.5,.95)),
                          ((.251472,.95),(.05,.748528),(.05,.5)),
                          ((.05,.251472),(.251472,.05),(.5,.05))):
                commands.append(" ".join(point_command(local_point(*p)) for p in curve)+" c")
            commands.append("h B")
        pdf[NameObject("/AP")] = _appearance(writer, bounds, commands, annotation.kind == "highlight")
        final_model = rotate_annotation(annotation, workspace_rotation)
        payload = {"uid": final_model.uid, "kind": final_model.kind, "rect": final_model.rect,
                   "text": final_model.text, "rects": final_model.rects, "strokes": final_model.strokes}
        pdf[NameObject("/PDFWorkbench")] = DictionaryObject({
            NameObject("/Version"): NumberObject(2),
            NameObject("/BaseRotation"): NumberObject((source_page.rotation + workspace_rotation) % 360),
            NameObject("/Model"): TextStringObject(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        })
        pdf["/PDFWorkbench"][NameObject("/Fingerprint")] = TextStringObject(_annotation_fingerprint(pdf))
        writer.add_annotation(output_page, pdf)


def _read_own_annotation(page: PageObject, pdf: DictionaryObject) -> PageAnnotation | None:
    """Unknown, corrupt or geometrically inconsistent private metadata is foreign."""
    try:
        marker = pdf.get("/PDFWorkbench")
        if marker is None:
            return None
        marker = marker.get_object()
        if marker.get("/Version") != 2:
            return None
        rotation = marker.get("/BaseRotation")
        if isinstance(rotation, bool) or rotation not in (0, 90, 180, 270):
            return None
        model = marker.get("/Model")
        if not isinstance(model, str) or len(model) > 2_000_000:
            return None
        payload = json.loads(model)
        if not isinstance(payload, dict) or set(payload) != {"uid", "kind", "rect", "text", "rects", "strokes"}:
            return None
        annotation = PageAnnotation(**payload)
        if pdf.get("/NM") != "PDFWorkbench:" + annotation.uid:
            return None
        if pdf.get("/Subtype") != {"comment": "/Text", "highlight": "/Highlight", "signature": "/Ink"}[annotation.kind]:
            return None
        if pdf.get("/Contents", "") != annotation.text:
            return None
        annotation = rotate_annotation(annotation, (page.rotation-int(rotation)) % 360)
        rects = annotation.rects if annotation.kind == "highlight" else (annotation.rect,)
        expected = _bounds([p for rect in rects for p in _corners(page, rect)])
        actual = pdf.get("/Rect")
        if actual is None or len(actual) != 4 or any(abs(float(a)-b) > .01 for a,b in zip(actual,expected)):
            return None
        normal = pdf.get("/AP", {}).get("/N")
        if normal is None or not hasattr(normal.get_object(), "get_data"):
            return None
        fingerprint = marker.get("/Fingerprint")
        if not isinstance(fingerprint, str) or len(fingerprint) != 64 or fingerprint != _annotation_fingerprint(pdf):
            return None
        return annotation
    except Exception:
        # Preserve unfamiliar original annotations rather than deleting them or
        # replacing their appearance from partially understood model data.
        return None


def extract_own_annotations(reader) -> tuple[tuple[tuple[PageAnnotation, ...], ...], bool]:
    """Remove only understood own annotations from this in-memory reader.

    Caller can serialize this as a clean preview/export base. Disk input remains
    untouched; imported model plus base form the complete logical snapshot.
    """
    pages = []
    changed = False
    for page in reader.pages:
        original = page.get("/Annots", [])
        if hasattr(original, "get_object"):
            original = original.get_object()
        candidates = [_read_own_annotation(page, ref.get_object()) for ref in original]
        counts = {}
        for item in candidates:
            if item:
                counts[item.uid] = counts.get(item.uid, 0) + 1
        imported, remaining = [], []
        for ref, model in zip(original, candidates):
            if model is not None and counts[model.uid] == 1:
                imported.append(model)
                changed = True
            else:
                remaining.append(ref)
        if len(remaining) != len(original):
            # PdfReader's flattened PageObject can differ from the dictionary
            # reached through the page tree. clone_document_from_reader follows
            # that indirect dictionary, so clean both views of this page.
            reference = page.indirect_reference
            backing = reference.get_object() if reference is not None else page
            for target in (page, backing):
                if remaining:
                    target[NameObject("/Annots")] = ArrayObject(remaining)
                else:
                    target.pop("/Annots", None)
        pages.append(tuple(imported))
    return tuple(pages), changed
