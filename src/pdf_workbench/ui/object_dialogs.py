"""Native properties dialogs for plain text, image/stamp and vector objects.

Dialogs return immutable PageAnnotation values. The caller commits once through
Workspace, keeping Cancel, Undo and the original input file predictable.
"""
from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from ..annotations import AnnotationError, PageAnnotation, TextStyle, load_image_asset
from ..annotation_assets import MAX_IMAGE_BYTES
from .annotation_overlay import draw_annotations, map_rect, registered_text_font_family, validate_text_object
from .theme import color, file_dialog_options, semantic_label


class _ObjectPreview(QWidget):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.setMinimumHeight(130)
        self.setAccessibleName("Objektvorschau auf weißem PDF-Hintergrund")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), color("canvas"))
        try:
            annotation = self.dialog.annotation()
            points = self.dialog.point_size
            if points:
                rect = map_rect(annotation.rect, QRectF(0,0,*points), self.dialog.rotation)
                points = (rect.width(), rect.height())
            else:
                points = (260.0, 100.0)
            bounds = QRectF(10, 10, max(1,self.width()-20), max(1,self.height()-20))
            ratio = min(bounds.width()/points[0], bounds.height()/points[1])
            paper = QRectF(0,0,points[0]*ratio,points[1]*ratio)
            paper.moveCenter(bounds.center())
            painter.fillRect(paper, QColor("white"))
            preview = replace(annotation, rect=(0,0,1,1), content_rotation=(annotation.content_rotation+self.dialog.rotation)%360)
            class Page:
                rotation = 0
                annotations = (preview,)
            draw_annotations(painter, paper, Page(), points, self.dialog.user_unit)
        except AnnotationError:
            painter.setPen(color("muted"))
            painter.drawText(self.rect().adjusted(12,12,-12,-12), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, "Die Vorschau erscheint, sobald die Eingaben gültig sind.")
        painter.end()


class _ObjectDialog(QDialog):
    def __init__(self, parent, annotation, title, rect, rotation, content_rotation, point_size, user_unit):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.existing = annotation
        self.uid = annotation.uid if annotation else uuid4().hex
        self.model_rect = annotation.rect if annotation else rect
        self.rotation = rotation % 360
        self.content_rotation = annotation.content_rotation if annotation else ((-rotation)%360 if content_rotation is None else content_rotation)
        self.point_size = point_size
        self.user_unit = user_unit
        self.body_layout = QVBoxLayout(self)
        self.body_layout.setSpacing(10)
        self.resize(520, 470)
        self.color_rgb = annotation.style.color if annotation else TextStyle().color
        self.color_value = QColor.fromRgbF(*self.color_rgb)

    def _label(self, text, widget):
        label = QLabel(text)
        label.setBuddy(widget)
        return label

    def _rotation_control(self):
        control = QComboBox()
        control.setAccessibleName("Objektrotation in der aktuellen Ansicht")
        for angle in (0,90,180,270):
            control.addItem(f"{angle}°", angle)
        control.setCurrentIndex(control.findData((self.content_rotation+self.rotation)%360))
        return control

    def _selected_rotation(self):
        return (self.rotation_box.currentData()-self.rotation)%360

    def _color_control(self):
        button = QPushButton()
        button.setAccessibleName("Objektfarbe auswählen")
        button.clicked.connect(self.choose_color)
        self.color_button = button
        self._update_color_button()
        return button

    def _update_color_button(self):
        self.color_button.setText("Farbe " + self.color_value.name().upper())
        self.color_button.setAccessibleDescription("Aktuelle RGB-Farbe " + self.color_value.name().upper())

    def set_color(self, value):
        value = QColor(value)
        if value.isValid():
            self.color_value = value
            self.color_rgb = (value.redF(), value.greenF(), value.blueF())
            self._update_color_button()
            self.validate()

    def choose_color(self):
        selected = QColorDialog.getColor(self.color_value, self, "Objektfarbe", QColorDialog.ColorDialogOption.DontUseNativeDialog)
        if selected.isValid():
            self.set_color(selected)

    def _style(self, font_size=None, alignment=None):
        original = self.existing.style if self.existing else TextStyle()
        return TextStyle(font_size if font_size is not None else original.font_size, self.color_rgb,
                         alignment if alignment is not None else original.alignment)

    def _finish(self):
        self.preview = _ObjectPreview(self)
        self.body_layout.addWidget(self.preview)
        self.validation = QLabel()
        self.validation.setWordWrap(True)
        self.validation.setAccessibleName("Eingabeprüfung")
        self.validation.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        semantic_label(self.validation, "error")
        self.body_layout.addWidget(self.validation)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Übernehmen")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.body_layout.addWidget(self.buttons)
        self.validate()

    def validate(self, *_):
        try:
            self.annotation()
            error = ""
        except AnnotationError as exc:
            error = str(exc)
        self.validation.setText(error)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not error)
        self.preview.update()
        return not error

    def accept(self):
        if self.validate():
            super().accept()
        else:
            self.validation.setFocus(Qt.FocusReason.OtherFocusReason)

    @classmethod
    def get_annotation(cls, parent=None, annotation=None, **kwargs):
        dialog = cls(parent, annotation, **kwargs)
        return dialog.annotation() if dialog.exec() == QDialog.DialogCode.Accepted else None


class TextObjectDialog(_ObjectDialog):
    def __init__(self, parent=None, annotation=None, *, rect=(.1,.1,.9,.35), rotation=0,
                 content_rotation=None, point_size=None, user_unit=1):
        super().__init__(parent, annotation, "Textfeld bearbeiten" if annotation else "Textfeld hinzufügen", rect, rotation, content_rotation, point_size, user_unit)
        self.resize(540, 600)
        self.editor = QPlainTextEdit()
        self.editor.setAccessibleName("Textfeldinhalt")
        self.editor.setPlainText(annotation.text if annotation else "")
        editor_font = QFont(registered_text_font_family())
        editor_font.setPointSizeF(12)
        self.editor.setFont(editor_font)
        self.editor.setMinimumHeight(130)
        self.body_layout.addWidget(self._label("&Text (mehrere Zeilen möglich)", self.editor))
        self.body_layout.addWidget(self.editor, 1)
        form = QFormLayout()
        self.font_size = QDoubleSpinBox()
        self.font_size.setRange(4,288)
        self.font_size.setDecimals(3)
        self.font_size.setSuffix(" pt")
        self._original_size = annotation.style.font_size if annotation else 12
        self._size_changed = False
        self.font_size.setValue(self._original_size)
        self.font_size.setAccessibleName("Schriftgröße in Punkt")
        form.addRow(self._label("&Schriftgröße", self.font_size), self.font_size)
        self.alignment = QComboBox()
        for label, value in (("Links", "left"), ("Mittig", "center"), ("Rechts", "right")):
            self.alignment.addItem(label,value)
        self.alignment.setCurrentIndex(self.alignment.findData(annotation.style.alignment if annotation else "left"))
        self.alignment.setAccessibleName("Textausrichtung")
        form.addRow(self._label("&Ausrichtung", self.alignment), self.alignment)
        form.addRow("Textfarbe", self._color_control())
        self.rotation_box = self._rotation_control()
        form.addRow(self._label("&Drehung", self.rotation_box), self.rotation_box)
        self.body_layout.addLayout(form)
        hint = QLabel("Liberation Sans ist in der App und im PDF enthalten. Nicht unterstützte Zeichen und zu kleine Textfelder werden vor dem Speichern gemeldet.")
        hint.setWordWrap(True)
        semantic_label(hint,"muted")
        self.body_layout.addWidget(hint)
        self._finish()
        self.editor.textChanged.connect(self.validate)
        self.font_size.valueChanged.connect(self._changed_size)
        self.alignment.currentIndexChanged.connect(self.validate)
        self.rotation_box.currentIndexChanged.connect(self.validate)

    def _changed_size(self, *_):
        self._size_changed = True
        self.validate()

    def annotation(self):
        size = self.font_size.value() if self._size_changed else self._original_size
        values = dict(text=self.editor.toPlainText(),style=self._style(size,self.alignment.currentData()),content_rotation=self._selected_rotation())
        result = replace(self.existing, **values) if self.existing else PageAnnotation(self.uid,"text",self.model_rect,**values)
        if self.point_size:
            validate_text_object(result,self.point_size,self.rotation,self.user_unit)
        return result


class ImageObjectDialog(_ObjectDialog):
    def __init__(self, parent=None, annotation=None, *, asset=None, kind="image", rect=(.1,.1,.7,.6),
                 rotation=0, content_rotation=None, point_size=None, user_unit=1):
        super().__init__(parent, annotation, "Bild oder Stempel bearbeiten" if annotation else "Bild oder Stempel hinzufügen", rect, rotation, content_rotation, point_size, user_unit)
        self.asset = annotation.asset if annotation else asset
        self.choose_button = QPushButton("PNG oder JPEG auswählen …")
        self.choose_button.clicked.connect(self.choose_image)
        self.body_layout.addWidget(self.choose_button)
        self.image_info = QLabel()
        self.image_info.setWordWrap(True)
        self.body_layout.addWidget(self.image_info)
        form = QFormLayout()
        self.kind_box = QComboBox()
        self.kind_box.addItem("Bild", "image")
        self.kind_box.addItem("Stempel", "stamp")
        self.kind_box.setCurrentIndex(self.kind_box.findData(annotation.kind if annotation else kind))
        self.kind_box.setAccessibleName("Bild oder Stempel")
        form.addRow(self._label("Objekt&typ", self.kind_box), self.kind_box)
        self.description = QLineEdit(annotation.text if annotation else "")
        self.description.setAccessibleName("Bildbeschreibung")
        form.addRow(self._label("&Beschreibung", self.description), self.description)
        self.rotation_box = self._rotation_control()
        form.addRow(self._label("&Drehung", self.rotation_box), self.rotation_box)
        self.body_layout.addLayout(form)
        hint = QLabel("PNG-Transparenz und die JPEG-Bildausrichtung bleiben sichtbar. Das Bild wird als Kopie eingebettet (max. 8 MiB und 4 Millionen Pixel).")
        hint.setWordWrap(True)
        semantic_label(hint,"muted")
        self.body_layout.addWidget(hint)
        self._finish()
        self._update_info()
        self.kind_box.currentIndexChanged.connect(self.validate)
        self.description.textChanged.connect(self.validate)
        self.rotation_box.currentIndexChanged.connect(self.validate)

    def _update_info(self):
        self.image_info.setText(f"{self.asset.width} × {self.asset.height} Pixel · " + ("mit Transparenz" if self.asset.has_alpha else "ohne Transparenz") if self.asset else "Noch kein Bild ausgewählt.")

    def load_bytes(self, data):
        self.asset = load_image_asset(data)
        self._update_info()
        self.validate()
        return self.asset

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(self,"PNG oder JPEG auswählen","","Bilder (*.png *.jpg *.jpeg)",options=file_dialog_options())
        if path:
            try:
                with open(path,"rb") as stream:
                    self.load_bytes(stream.read(MAX_IMAGE_BYTES+1))
            except (OSError, AnnotationError) as exc:
                self.validation.setText(str(exc))

    def annotation(self):
        values = dict(kind=self.kind_box.currentData(),text=self.description.text(),asset=self.asset,content_rotation=self._selected_rotation())
        result = replace(self.existing,**values) if self.existing else PageAnnotation(self.uid,rect=self.model_rect,**values)
        should_fit = (self.existing is None or self.asset.sha256 != self.existing.asset.sha256 or
                      result.content_rotation != self.existing.content_rotation)
        if should_fit and self.point_size:
            # Keep a user's existing free-form object untouched on a no-op edit.
            # A newly chosen image instead starts with its real pixel proportions.
            basis_width,basis_height = self.point_size[::-1] if self.rotation % 180 else self.point_size
            left,top,right,bottom = self.model_rect
            image_width,image_height = self.asset.width,self.asset.height
            if result.content_rotation % 180:
                image_width,image_height = image_height,image_width
            factor = min((right-left)*basis_width/image_width,(bottom-top)*basis_height/image_height)
            width,height = image_width*factor/basis_width,image_height*factor/basis_height
            cx,cy = (left+right)/2,(top+bottom)/2
            result = replace(result,rect=(max(left,cx-width/2),max(top,cy-height/2),min(right,cx+width/2),min(bottom,cy+height/2)))
        return result


class SymbolObjectDialog(_ObjectDialog):
    def __init__(self, parent=None, annotation=None, *, kind="checkmark", rect=(.1,.1,.3,.3),
                 rotation=0, content_rotation=None, point_size=None, user_unit=1):
        super().__init__(parent, annotation, "Symbol bearbeiten" if annotation else "Häkchen oder Kreuz hinzufügen", rect, rotation, content_rotation, point_size, user_unit)
        form = QFormLayout()
        self.kind_box = QComboBox()
        self.kind_box.addItem("Häkchen", "checkmark")
        self.kind_box.addItem("Kreuz", "cross")
        self.kind_box.setCurrentIndex(self.kind_box.findData(annotation.kind if annotation else kind))
        self.kind_box.setAccessibleName("Häkchen oder Kreuz")
        form.addRow(self._label("&Symbol", self.kind_box),self.kind_box)
        form.addRow("Symbolfarbe",self._color_control())
        self.rotation_box = self._rotation_control()
        form.addRow(self._label("&Drehung", self.rotation_box),self.rotation_box)
        self.body_layout.addLayout(form)
        self._finish()
        self.kind_box.currentIndexChanged.connect(self.validate)
        self.rotation_box.currentIndexChanged.connect(self.validate)

    def annotation(self):
        values = dict(kind=self.kind_box.currentData(),style=self._style(),content_rotation=self._selected_rotation())
        return replace(self.existing,**values) if self.existing else PageAnnotation(self.uid,rect=self.model_rect,**values)
