"""Shared source-coordinate overlays for previews and lazy thumbnails."""
from collections import OrderedDict
from functools import lru_cache
import io
import math
import threading
import weakref

from PyQt6.QtCore import QByteArray, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QGlyphRun, QImage, QPainterPath, QPen, QRawFont, QTransform

from ..annotations import AnnotationError, visible_page_box
from ..text_appearance import font_metrics, layout_text

_geometry = OrderedDict()
_geometry_lock = threading.RLock()
_font_id = None


def registered_text_font_family():
    """Register only the bundled font, on the GUI thread, for native editors."""
    global _font_id
    if _font_id is None:
        _font_id = QFontDatabase.addApplicationFontFromData(QByteArray(font_metrics()[0]))
    families = QFontDatabase.applicationFontFamilies(_font_id)
    if not families:
        raise AnnotationError("Die mitgelieferte Textschrift konnte nicht geladen werden.")
    return families[0]


def source_page_geometry(source, page):
    """Return (display CropBox size in raw PDF units, UserUnit).

    One source parse collects all page sizes; the small cache holds only floats
    and a weak source reference, never another copy of the PDF or its images.
    Can be prewarmed by an import worker before the first object operation.
    """
    with _geometry_lock:
        key = id(source)
        cached = _geometry.get(key)
        if cached is None or cached[0]() is not source:
            from pypdf import PdfReader
            dimensions = []
            for item in PdfReader(io.BytesIO(source.data)).pages:
                left, bottom, right, top = visible_page_box(item)
                width, height = right-left, top-bottom
                if item.rotation % 180:
                    width, height = height, width
                unit = float(item.get("/UserUnit", 1))
                if not math.isfinite(unit) or unit <= 0:
                    raise AnnotationError("Die PDF-Seite hat eine ungültige Maßeinheit.")
                dimensions.append(((width, height), unit))
            cached = (weakref.ref(source), tuple(dimensions))
            _geometry[key] = cached
            while len(_geometry) > 16:
                _geometry.popitem(last=False)
        _geometry.move_to_end(key)
        points, unit = cached[1][page.page_index]
        return (points[::-1] if page.rotation % 180 else points), unit


@lru_cache(maxsize=32)
def _raw_font(size):
    return QRawFont(QByteArray(font_metrics()[0]), size, QFont.HintingPreference.PreferNoHinting)


@lru_cache(maxsize=4)
def _asset_image(asset):
    image = QImage.fromData(asset.data, "PNG")
    if image.isNull():
        raise AnnotationError("Das eingebettete Bild kann nicht dargestellt werden.")
    return image


def object_content_geometry(annotation, paper, rotation, point_size):
    """Map top-left object-local raw PDF units into the currently rotated canvas."""
    left, top, right, bottom = annotation.rect
    def point(x,y):
        x,y = basis_to_display((x,y), annotation.content_rotation)
        return map_point((left+x*(right-left), top+y*(bottom-top)), paper, rotation)
    origin, px, py = point(0,0), point(1,0), point(0,1)
    basis_width, basis_height = point_size[::-1] if rotation % 180 else point_size
    width, height = (right-left)*basis_width, (bottom-top)*basis_height
    if annotation.content_rotation % 180:
        width, height = height, width
    transform = QTransform((px.x()-origin.x())/width, (px.y()-origin.y())/width,
                           (py.x()-origin.x())/height, (py.y()-origin.y())/height, origin.x(), origin.y())
    return width, height, transform


def validate_text_object(annotation, point_size, rotation=0, user_unit=1):
    """Same fit check used by PDF export; call before committing a UI edit/resize."""
    width, height, _ = object_content_geometry(annotation, QRectF(0,0,*point_size), rotation, point_size)
    return layout_text(annotation.text, width, height, annotation.style.font_size/user_unit)


def _draw_object(painter, paper, page, annotation, point_size, user_unit):
    width, height, transform = object_content_geometry(annotation, paper, page.rotation, point_size)
    painter.save()
    try:
        painter.setWorldTransform(transform, True)
        painter.setClipRect(QRectF(0,0,width,height), Qt.ClipOperation.IntersectClip)
        if annotation.kind == "text":
            size = annotation.style.font_size/user_unit
            lines = layout_text(annotation.text, width, height, size)
            _, units, advances, cmap, *_ = font_metrics()
            painter.setPen(QColor.fromRgbF(*annotation.style.color))
            font = _raw_font(size)
            for line, measured, baseline in lines:
                x = 0 if annotation.style.alignment == "left" else ((width-measured)/2 if annotation.style.alignment == "center" else width-measured)
                positions, glyphs = [], []
                for char in line:
                    glyph = cmap[ord(char)]
                    glyphs.append(glyph)
                    positions.append(QPointF(x, height-baseline))
                    x += advances[glyph]*size/units
                run = QGlyphRun()
                run.setRawFont(font)
                run.setGlyphIndexes(glyphs)
                run.setPositions(positions)
                painter.drawGlyphRun(QPointF(0,0), run)
        elif annotation.kind in ("image", "stamp"):
            painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(QRectF(0,0,width,height), _asset_image(annotation.asset))
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor.fromRgbF(*annotation.style.color), max(.5,min(width,height)*.08), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            strokes = (((.14,.52),(.40,.80),(.86,.18)),) if annotation.kind == "checkmark" else (((.18,.18),(.82,.82)),((.82,.18),(.18,.82)))
            for stroke in strokes:
                path = QPainterPath(QPointF(stroke[0][0]*width,stroke[0][1]*height))
                for x,y in stroke[1:]:
                    path.lineTo(x*width,y*height)
                painter.drawPath(path)
    finally:
        painter.restore()



def basis_to_display(point, rotation):
    x, y = point
    return {0: (x, y), 90: (1 - y, x), 180: (1 - x, 1 - y), 270: (y, 1 - x)}[rotation % 360]


def display_to_basis(point, rotation):
    return basis_to_display(point, -rotation)


def map_point(point, paper, rotation):
    x, y = basis_to_display(point, rotation)
    return QPointF(paper.left() + x * paper.width(), paper.top() + y * paper.height())


def map_rect(rect, paper, rotation):
    left, top, right, bottom = rect
    points = [map_point(point, paper, rotation) for point in ((left, top), (right, top), (right, bottom), (left, bottom))]
    xs, ys = [p.x() for p in points], [p.y() for p in points]
    return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def draw_annotations(painter, paper, page, point_size=None, user_unit=1):
    """Paint models over a clean raster base. Pass broker dimensions for text.

    point_size is the visible size after workspace rotation, in raw PDF units;
    QtPdf omits UserUnit, so that value is supplied separately (normally 1).
    """
    point_size = point_size or (paper.width(), paper.height())
    painter.save()
    painter.setClipRect(paper)
    painter.setRenderHint(painter.RenderHint.Antialiasing)
    for annotation in getattr(page, "annotations", ()):
        if annotation.kind in ("text", "image", "stamp", "checkmark", "cross"):
            try:
                _draw_object(painter, paper, page, annotation, point_size, user_unit)
            except AnnotationError:
                # Transient invalid resize drafts must never escape a Qt paint event.
                # Commit/export is separately blocked with the exact validation error.
                painter.setPen(QPen(QColor.fromRgbF(*annotation.style.color), 1, Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(map_rect(annotation.rect, paper, page.rotation))
        elif annotation.kind == "highlight":
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(255, 214, 55, 100))
            for rect in annotation.rects or (annotation.rect,):
                painter.drawRect(map_rect(rect, paper, page.rotation))
        elif annotation.kind == "comment":
            rect = map_rect(annotation.rect, paper, page.rotation)
            painter.setPen(QPen(QColor("#b07511"), max(0.7, min(1.4, paper.width() / 500))))
            painter.setBrush(QColor("#ffe6a1")); painter.drawRoundedRect(rect, 2, 2)
            painter.drawLine(QPointF(rect.left() + rect.width() * .22, rect.top() + rect.height() * .37), QPointF(rect.right() - rect.width() * .22, rect.top() + rect.height() * .37))
            painter.drawLine(QPointF(rect.left() + rect.width() * .22, rect.top() + rect.height() * .62), QPointF(rect.right() - rect.width() * .34, rect.top() + rect.height() * .62))
        elif annotation.kind == "signature":
            left, top, right, bottom = annotation.rect
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#173867"), max(.65, paper.width() * .0025), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            for stroke in annotation.strokes:
                path = QPainterPath()
                for index, (x, y) in enumerate(stroke):
                    point = map_point((left + x * (right - left), top + y * (bottom - top)), paper, page.rotation)
                    if index: path.lineTo(point)
                    else: path.moveTo(point)
                painter.drawPath(path)
    painter.restore()
