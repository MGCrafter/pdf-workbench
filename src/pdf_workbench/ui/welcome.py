"""A quiet landing workspace, using original, resolution-independent artwork."""
from PyQt6.QtCore import QRectF, QSize, Qt, pyqtProperty
from PyQt6.QtGui import QPainter, QPen, QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .icons import icon
from .theme import color


class DocumentArtwork(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._reveal = 1.
        self.setFixedHeight(138)
        self.setMinimumWidth(250)
        self.setAccessibleName("Dokumente zu einer neuen PDF zusammenfügen")

    @pyqtProperty(float)
    def reveal(self):
        return self._reveal

    @reveal.setter
    def reveal(self, value):
        self._reveal = value
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        scale = min(1., self.height() / 138)
        painter.scale(scale, scale)
        for position, angle in ((-1, -13), (1, 13), (0, 0)):
            painter.save()
            painter.translate(position * (37 + 9 * self._reveal), abs(position) * 5 + (1-self._reveal)*16)
            painter.rotate(angle * self._reveal)
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(color("shadow"))
            painter.drawRoundedRect(QRectF(-35, -48, 78, 104), 6, 6)
            painter.setBrush(color("surface")); painter.setPen(QPen(color("border"), 1))
            painter.drawRoundedRect(QRectF(-39, -52, 78, 104), 6, 6)
            painter.setPen(QPen(color("accent" if position == 0 else "border"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(-24, -30, 11, -30)
            painter.setPen(QPen(color("divider"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for y, width in ((-14, 45), (-3, 47), (8, 32), (26, 42), (36, 26)):
                painter.drawLine(-24, y, -24 + width, y)
            if position == 0:
                painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(color("accent"))
                painter.drawRoundedRect(QRectF(18, 33, 30, 30), 10, 10)
                painter.setPen(QPen(color("on_accent"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawLine(26, 48, 40, 48); painter.drawLine(33, 41, 33, 55)
            painter.restore()


class WelcomeWorkspace(QFrame):
    def __init__(self, open_files, import_images, parent=None):
        super().__init__(parent)
        self.setObjectName("welcomeWorkspace")
        layout = QVBoxLayout(self); layout.setContentsMargins(32, 12, 32, 20); layout.setSpacing(12)
        layout.addStretch(2)
        self.artwork = DocumentArtwork(); layout.addWidget(self.artwork)
        title = QLabel("Gute Dokumente.\nGanz einfach.")
        self.title = title
        title.setObjectName("welcomeTitle"); title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        hint = QLabel("PDFs zusammenfügen, Seiten ordnen und Ideen ergänzen.\nAlles lokal. Ihre Originale bleiben erhalten.")
        hint.setObjectName("welcomeHint"); hint.setAlignment(Qt.AlignmentFlag.AlignCenter); hint.setWordWrap(True)
        layout.addWidget(hint)
        action_row = QHBoxLayout(); action_row.setSpacing(10); action_row.addStretch()
        self.open_button = QPushButton("PDFs auswählen …"); self.open_button.setObjectName("primary")
        self.open_button.setIcon(icon("add", "on_accent")); self.open_button.setIconSize(QSize(19, 19))
        self.open_button.setMinimumHeight(44); self.open_button.clicked.connect(open_files)
        self.images_button = QPushButton("Bilder zu PDF …")
        self.images_button.setIcon(icon("image")); self.images_button.setMinimumHeight(44); self.images_button.clicked.connect(import_images)
        action_row.addWidget(self.open_button); action_row.addWidget(self.images_button); action_row.addStretch()
        layout.addLayout(action_row)
        drag = QLabel("oder Dateien in dieses Fenster ziehen  ·  Strg+O")
        drag.setObjectName("muted"); drag.setAlignment(Qt.AlignmentFlag.AlignCenter); layout.addWidget(drag)
        layout.addStretch(2)
        self.cards = QWidget()
        cards = QHBoxLayout(self.cards); cards.setContentsMargins(0,0,0,0); cards.setSpacing(12)
        self.feature_icons = []
        for symbol, title, detail in (
            ("grid", "Seiten gestalten", "Ordnen, drehen & zusammenfügen"),
            ("signature", "Persönlich ergänzen", "Text, Kommentare & Unterschriften"),
            ("lock", "Bewusst speichern", "Komprimieren & mit Passwort schützen"),
        ):
            card = QFrame(); card.setObjectName("featureCard")
            content = QVBoxLayout(card); content.setContentsMargins(18, 16, 18, 16); content.setSpacing(7)
            symbol_label = QLabel(); symbol_label.setPixmap(icon(symbol, "accent").pixmap(QSize(23,23)))
            self.feature_icons.append((symbol_label, symbol))
            content.addWidget(symbol_label)
            heading = QLabel(title); heading.setObjectName("featureTitle"); content.addWidget(heading)
            description = QLabel(detail); description.setObjectName("muted"); description.setWordWrap(True); content.addWidget(description)
            cards.addWidget(card, 1)
        layout.addWidget(self.cards); layout.addStretch(1)

    def adapt(self, height):
        compact = height < 700
        self.cards.setVisible(not compact)
        self.artwork.setFixedHeight(72 if compact else 138)
        self.title.setText("Gute Dokumente. Ganz einfach." if compact else "Gute Dokumente.\nGanz einfach.")

    def refresh_icons(self):
        for label, symbol in self.feature_icons:
            label.setPixmap(icon(symbol, "accent").pixmap(QSize(23, 23)))
        self.artwork.update()
