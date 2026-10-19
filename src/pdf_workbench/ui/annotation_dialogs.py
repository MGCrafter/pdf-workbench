"""Native comment, hand-drawn signature and annotation management dialogs."""
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QTextEdit, QVBoxLayout, QWidget


class CommentDialog(QDialog):
    def __init__(self, parent=None, text="", title="Kommentar hinzufügen"):
        super().__init__(parent); self.setWindowTitle(title); self.resize(420, 260)
        layout = QVBoxLayout(self); layout.addWidget(QLabel("Kommentartext"))
        self.editor = QTextEdit(); self.editor.setPlainText(text); self.editor.setAccessibleName("Kommentartext")
        layout.addWidget(self.editor)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject); layout.addWidget(self.buttons)
        self.editor.textChanged.connect(self.validate); self.validate()

    def validate(self): self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.editor.toPlainText().strip()))

    @staticmethod
    def get_text(parent, text="", title="Kommentar hinzufügen"):
        dialog = CommentDialog(parent, text, title)
        return dialog.editor.toPlainText().strip() if dialog.exec() == QDialog.DialogCode.Accepted else None


class SignaturePad(QWidget):
    def __init__(self):
        super().__init__(); self.strokes = []; self.setMinimumSize(300, 150)
        self.setAccessibleName("Zeichenfläche für die gezeichnete Unterschrift")
        self.setCursor(Qt.CursorShape.CrossCursor)

    def clear(self): self.strokes.clear(); self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.strokes.append([event.position()]); self.update(); event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self.strokes:
            point = event.position()
            self.strokes[-1].append(QPointF(max(0, min(self.width(), point.x())), max(0, min(self.height(), point.y())))); self.update(); event.accept()

    def paintEvent(self, event):
        painter = QPainter(self); painter.fillRect(self.rect(), QColor("white"))
        painter.setPen(QPen(QColor("#bdc8d8"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(18, self.height() - 35, self.width() - 18, self.height() - 35)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#173867"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        for stroke in self.strokes:
            path = QPainterPath(stroke[0])
            for point in stroke[1:]: path.lineTo(point)
            painter.drawPath(path)

    def normalized_strokes(self):
        strokes = [stroke for stroke in self.strokes if len({(p.x(), p.y()) for p in stroke}) > 1]
        points = [point for stroke in strokes for point in stroke]
        if not points: return ()
        left, right = min(p.x() for p in points), max(p.x() for p in points)
        top, bottom = min(p.y() for p in points), max(p.y() for p in points)
        if right - left < 2 and bottom - top < 2: return ()
        width, height = max(2, right - left), max(2, bottom - top)
        return tuple(tuple((.02 + .96 * (p.x() - left) / width, .05 + .9 * (p.y() - top) / height) for p in stroke) for stroke in strokes)


class SignatureDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent); self.setWindowTitle("Gezeichnete Unterschrift"); self.resize(500, 310)
        layout = QVBoxLayout(self)
        hint = QLabel("Mit gedrückter linker Maustaste zeichnen. Danach auf die PDF klicken oder einen Bereich für die gewünschte Größe ziehen.")
        hint.setWordWrap(True); layout.addWidget(hint)
        self.pad = SignaturePad(); layout.addWidget(self.pad, 1)
        note = QLabel("Sichtbare Zeichnung, keine kryptografische Zertifikatssignatur."); note.setObjectName("muted"); note.setWordWrap(True); layout.addWidget(note)
        row = QHBoxLayout(); clear = QPushButton("Neu zeichnen"); clear.clicked.connect(self.pad.clear); row.addWidget(clear); row.addStretch()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject); row.addWidget(self.buttons); layout.addLayout(row)
        self.validation = QLabel(); self.validation.setStyleSheet("color: #9f3c21"); layout.addWidget(self.validation)

    def accept(self):
        if self.pad.normalized_strokes(): super().accept()
        else: self.validation.setText("Bitte zuerst eine Unterschrift auf die Zeichenfläche zeichnen.")

    @staticmethod
    def get_strokes(parent):
        dialog = SignatureDialog(parent)
        return dialog.pad.normalized_strokes() if dialog.exec() == QDialog.DialogCode.Accepted else None


class AnnotationListDialog(QDialog):
    """Uses callbacks so every change goes through the workspace's undo history."""
    def __init__(self, parent, annotations, edit, remove):
        super().__init__(parent); self.setWindowTitle("Anmerkungen auf der aktuellen Seite"); self.resize(480, 330)
        self.annotations, self.edit_callback, self.remove_callback = annotations, edit, remove
        layout = QVBoxLayout(self)
        hint = QLabel("Eigene Kommentare, Textmarkierungen und gezeichnete Unterschriften. Änderungen lassen sich im Hauptfenster rückgängig machen."); hint.setWordWrap(True); layout.addWidget(hint)
        self.list = QListWidget(); self.list.setAccessibleName("Eigene Anmerkungen auf der aktuellen Seite"); layout.addWidget(self.list, 1)
        self.list.itemDoubleClicked.connect(lambda *_: self.edit())
        row = QHBoxLayout(); self.edit_button = QPushButton("Bearbeiten …"); self.remove_button = QPushButton("Löschen")
        self.edit_button.clicked.connect(self.edit); self.remove_button.clicked.connect(self.remove)
        row.addWidget(self.edit_button); row.addWidget(self.remove_button); row.addStretch()
        close = QPushButton("Schließen"); close.clicked.connect(self.accept); row.addWidget(close); layout.addLayout(row)
        self.list.currentItemChanged.connect(self.update_buttons); self.refresh()

    def refresh(self, uid=None):
        self.list.clear()
        labels = {"comment": "Kommentar", "highlight": "Textmarkierung", "signature": "Gezeichnete Unterschrift"}
        for annotation in self.annotations():
            text = annotation.text.replace("\n", " ")
            item = QListWidgetItem(labels[annotation.kind] + (": " + text[:100] if text else ""))
            item.setData(Qt.ItemDataRole.UserRole, annotation); item.setToolTip(annotation.text or labels[annotation.kind]); self.list.addItem(item)
            if annotation.uid == uid: self.list.setCurrentItem(item)
        if self.list.count() and self.list.currentRow() < 0: self.list.setCurrentRow(0)
        self.update_buttons()

    def update_buttons(self, *_):
        enabled = self.list.currentItem() is not None
        self.edit_button.setEnabled(enabled); self.remove_button.setEnabled(enabled)

    def edit(self):
        if self.list.currentItem():
            annotation = self.list.currentItem().data(Qt.ItemDataRole.UserRole)
            self.edit_callback(annotation); self.refresh(annotation.uid)

    def remove(self):
        if self.list.currentItem():
            self.remove_callback(self.list.currentItem().data(Qt.ItemDataRole.UserRole)); self.refresh()
