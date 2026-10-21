"""Small original vector icons, painted at the target display resolution."""
from PyQt6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPainterPath, QPen, QPixmap
from .theme import COLORS, color as theme_color


class VectorIcon(QIconEngine):
    def __init__(self, name, color=None):
        super().__init__()
        self.name, self.color = name, color

    def clone(self):
        return VectorIcon(self.name, self.color)

    def pixmap(self, size, mode, state):
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size, mode, state, scale):
        pixmap = QPixmap(QSize(max(1, round(size.width() * scale)), max(1, round(size.height() * scale))))
        pixmap.fill(Qt.GlobalColor.transparent)
        pixmap.setDevicePixelRatio(scale)
        painter = QPainter(pixmap)
        self.paint(painter, QRect(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return pixmap

    def paint(self, painter, rect, mode, state):
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        painter.translate(rect.x(), rect.y())
        painter.scale(rect.width() / 24, rect.height() / 24)
        if mode == QIcon.Mode.Disabled:
            foreground = theme_color("disabled")
        elif self.color in COLORS["light"]:
            foreground = theme_color(self.color)
        else:
            foreground = QColor(self.color) if self.color else theme_color("icon")
        painter.setPen(QPen(foreground, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        p = QPainterPath()
        name = self.name
        if name in ("up", "down"):
            if name == "down":
                painter.translate(24, 24); painter.rotate(180)
            p.moveTo(12, 20); p.lineTo(12, 4); p.moveTo(5, 11); p.lineTo(12, 4); p.lineTo(19, 11)
        elif name in ("undo", "redo"):
            if name == "redo": painter.translate(24, 0); painter.scale(-1, 1)
            p.moveTo(8, 5); p.lineTo(3, 10); p.lineTo(8, 15)
            p.moveTo(3, 10); p.lineTo(14, 10); p.cubicTo(22, 10, 22, 20, 14, 20)
        elif name in ("rotate_left", "rotate_right"):
            if name == "rotate_right": painter.translate(24, 0); painter.scale(-1, 1)
            p.moveTo(4, 3); p.lineTo(4, 9); p.lineTo(10, 9)
            p.moveTo(4, 9); p.cubicTo(8, -1, 22, 3, 21, 13); p.cubicTo(20, 22, 7, 25, 3, 16)
        elif name in ("add", "new", "document"):
            p.moveTo(5, 3); p.lineTo(14, 3); p.lineTo(19, 8); p.lineTo(19, 21); p.lineTo(5, 21); p.closeSubpath()
            p.moveTo(14, 3); p.lineTo(14, 8); p.lineTo(19, 8)
            if name == "add": p.moveTo(9, 14); p.lineTo(15, 14); p.moveTo(12, 11); p.lineTo(12, 17)
        elif name == "duplicate":
            painter.drawRoundedRect(QRectF(8, 8, 12, 13), 1.5, 1.5)
            p.moveTo(15, 5); p.lineTo(15, 3); p.lineTo(3, 3); p.lineTo(3, 16); p.lineTo(5, 16)
        elif name == "delete":
            p.moveTo(3, 6); p.lineTo(21, 6); p.moveTo(9, 3); p.lineTo(15, 3)
            p.moveTo(6, 6); p.lineTo(7, 21); p.lineTo(17, 21); p.lineTo(18, 6)
            p.moveTo(10, 10); p.lineTo(10, 17); p.moveTo(14, 10); p.lineTo(14, 17)
        elif name in ("grid", "list"):
            if name == "grid":
                for x in (3, 14):
                    for y in (3, 14): painter.drawRoundedRect(QRectF(x, y, 7, 7), 1, 1)
            else:
                for y in (5, 12, 19):
                    painter.drawRect(QRectF(3, y - 2, 4, 4)); p.moveTo(11, y); p.lineTo(21, y)
        elif name == "range":
            painter.drawRoundedRect(QRectF(3, 4, 18, 16), 2, 2)
            p.moveTo(7, 9); p.lineTo(17, 9); p.moveTo(7, 15); p.lineTo(12, 15)
        elif name == "lock":
            painter.drawRoundedRect(QRectF(5, 10, 14, 11), 2, 2)
            p.moveTo(8, 10); p.lineTo(8, 7); p.cubicTo(8, 1, 16, 1, 16, 7); p.lineTo(16, 10)
            p.moveTo(12, 14); p.lineTo(12, 17)
        elif name == "compress":
            p.moveTo(3, 3); p.lineTo(9, 9); p.moveTo(9, 4); p.lineTo(9, 9); p.lineTo(4, 9)
            p.moveTo(21, 21); p.lineTo(15, 15); p.moveTo(20, 15); p.lineTo(15, 15); p.lineTo(15, 20)
            p.moveTo(15, 3); p.lineTo(21, 3); p.lineTo(21, 9); p.moveTo(3, 15); p.lineTo(3, 21); p.lineTo(9, 21)
        elif name == "image":
            painter.drawRoundedRect(QRectF(3, 4, 18, 16), 2, 2); painter.drawEllipse(QRectF(6, 7, 4, 4))
            p.moveTo(3, 18); p.lineTo(9, 12); p.lineTo(13, 16); p.lineTo(17, 11); p.lineTo(21, 15)
        elif name == "appearance":
            painter.drawEllipse(QRectF(3, 3, 18, 18))
            p.moveTo(12, 3); p.lineTo(12, 21)
            painter.setBrush(foreground); painter.drawPie(QRectF(6, 6, 12, 12), -90*16, 180*16)
        elif name == "form":
            painter.drawRoundedRect(QRectF(3, 3, 18, 18), 2, 2)
            painter.drawRoundedRect(QRectF(7, 7, 10, 4), 1, 1)
            painter.drawRect(QRectF(7, 15, 3, 3)); p.moveTo(13, 16); p.lineTo(17, 16)
        elif name in ("zoom_in", "zoom_out"):
            painter.drawEllipse(QRectF(3, 3, 13, 13)); p.moveTo(14, 14); p.lineTo(21, 21)
            p.moveTo(6, 9.5); p.lineTo(13, 9.5)
            if name == "zoom_in": p.moveTo(9.5, 6); p.lineTo(9.5, 13)
        elif name in ("fit_page", "fit_width"):
            if name == "fit_page": painter.drawRoundedRect(QRectF(7, 5, 10, 14), 1, 1)
            else:
                p.moveTo(6, 12); p.lineTo(18, 12); p.moveTo(9, 9); p.lineTo(6, 12); p.lineTo(9, 15)
                p.moveTo(15, 9); p.lineTo(18, 12); p.lineTo(15, 15)
            p.moveTo(3, 7); p.lineTo(3, 3); p.lineTo(7, 3)
            p.moveTo(17, 3); p.lineTo(21, 3); p.lineTo(21, 7)
            p.moveTo(3, 17); p.lineTo(3, 21); p.lineTo(7, 21)
            p.moveTo(17, 21); p.lineTo(21, 21); p.lineTo(21, 17)
        elif name == "preview":
            painter.drawRoundedRect(QRectF(3, 4, 18, 16), 2, 2); p.moveTo(10, 4); p.lineTo(10, 20)
        elif name == "comment":
            p.moveTo(4, 4); p.lineTo(20, 4); p.lineTo(20, 17); p.lineTo(10, 17); p.lineTo(5, 21); p.lineTo(5, 17); p.lineTo(4, 17); p.closeSubpath()
            p.moveTo(8, 9); p.lineTo(16, 9); p.moveTo(8, 13); p.lineTo(14, 13)
        elif name == "highlight":
            p.moveTo(5, 15); p.lineTo(14, 3); p.lineTo(21, 8); p.lineTo(12, 20); p.closeSubpath()
            p.moveTo(5, 15); p.lineTo(3, 21); p.lineTo(12, 20); p.moveTo(3, 23); p.lineTo(21, 23)
        elif name == "signature":
            p.moveTo(2, 17); p.cubicTo(14, -2, 16, 1, 9, 15); p.cubicTo(4, 24, 14, 10, 14, 16); p.cubicTo(13, 22, 18, 9, 18, 16); p.lineTo(22, 15)
            p.moveTo(3, 22); p.lineTo(21, 22)
        elif name == "select":
            p.moveTo(4, 2); p.lineTo(4, 20); p.lineTo(9, 15); p.lineTo(13, 23); p.lineTo(17, 21); p.lineTo(13, 14); p.lineTo(21, 13); p.closeSubpath()
        elif name == "hand":
            p.moveTo(6, 13); p.lineTo(6, 6); p.cubicTo(6, 3, 9, 3, 9, 6); p.lineTo(9, 12); p.moveTo(9, 6); p.lineTo(9, 3); p.cubicTo(9, 1, 12, 1, 12, 3); p.lineTo(12, 11)
            p.moveTo(12, 5); p.cubicTo(12, 2, 15, 3, 15, 5); p.lineTo(15, 12); p.moveTo(15, 7); p.cubicTo(15, 4, 18, 5, 18, 7); p.lineTo(18, 16); p.cubicTo(18, 24, 8, 24, 6, 18); p.lineTo(3, 13); p.cubicTo(1, 10, 4, 8, 6, 13)
        elif name in ("export", "separate"):
            painter.drawRoundedRect(QRectF(4, 3, 12, 15), 1, 1)
            if name == "separate":
                p.moveTo(8, 21); p.lineTo(20, 21); p.lineTo(20, 8)
            else:
                p.moveTo(12, 13); p.lineTo(22, 13); p.moveTo(18, 9); p.lineTo(22, 13); p.lineTo(18, 17)
        painter.drawPath(p)
        painter.restore()


def icon(name, color=None):
    return QIcon(VectorIcon(name, color))
