"""Shared source-coordinate overlays for previews and lazy thumbnails."""
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainterPath, QPen


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


def draw_annotations(painter, paper, page):
    painter.save()
    painter.setClipRect(paper)
    painter.setRenderHint(painter.RenderHint.Antialiasing)
    for annotation in getattr(page, "annotations", ()):
        if annotation.kind == "highlight":
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
