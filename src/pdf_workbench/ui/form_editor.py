"""Visual AcroForm authoring with keyboard alternatives to every drag action."""
from __future__ import annotations

from dataclasses import replace
import io
from uuid import uuid4

from pypdf import PdfReader, PdfWriter
from PyQt6.QtCore import (QByteArray, QBuffer, QIODevice, QPointF, QRectF, QSize, Qt,
                          QTimer, pyqtSignal)
from PyQt6.QtGui import QColor, QImage, QPainter, QPalette, QPen, QTransform
from PyQt6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QSplitter, QVBoxLayout, QWidget)

from ..core import PdfWorkbenchError
from ..form_design import FieldPlacement, FormDesign, apply_form_designs, form_designs
from .pdf_dialogs import heading
from .theme import COLORS, semantic_label


KINDS = (("Textfeld", "text"), ("Kontrollkästchen", "checkbox"),
         ("Dropdown", "choice"), ("Optionsgruppe", "radio"))


def _rotate_point(x, y, rotation):
    if rotation == 90: return 1-y, x
    if rotation == 180: return 1-x, 1-y
    if rotation == 270: return y, 1-x
    return x, y


def _rotate_rect(rect, rotation):
    points = [_rotate_point(x,y,rotation) for x,y in ((rect[0],rect[1]), (rect[2],rect[3]))]
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


class FormCanvas(QWidget):
    selected = pyqtSignal(str, int)
    rect_changed = pyqtSignal(str, int, object)
    delete_requested = pyqtSignal()
    resized = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 220); self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Formularvorschau. Felder mit Pfeiltasten verschieben; Umschalt für größere Schritte.")
        self.image = QImage(); self.designs = (); self.page_index = 0; self.rotation = 0
        self.selection = None; self._drag = None; self._draft = None

    def page_rect(self):
        if self.image.isNull(): return QRectF()
        available = self.rect().adjusted(15,15,-15,-15)
        size = self.image.size(); size.scale(available.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF((self.width()-size.width())/2, (self.height()-size.height())/2, size.width(), size.height())

    def _placed(self):
        page = self.page_rect()
        if page.isEmpty(): return []
        result = []
        for field in self.designs:
            for index, placement in enumerate(field.placements):
                if placement.page_index != self.page_index: continue
                rect = self._draft if self._drag and self._drag[:2] == (field.uid,index) and self._draft else _rotate_rect(placement.rect,self.rotation)
                result.append((field,index,QRectF(page.x()+rect[0]*page.width(),page.y()+rect[1]*page.height(),
                                                   (rect[2]-rect[0])*page.width(),(rect[3]-rect[1])*page.height())))
        return result

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self.palette().color(QPalette.ColorRole.AlternateBase))
        if self.image.isNull():
            painter.setPen(self.palette().color(QPalette.ColorRole.Text))
            painter.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,"Seitenvorschau wird geladen …"); return
        painter.fillRect(self.page_rect(),Qt.GlobalColor.white)
        painter.drawImage(self.page_rect(),self.image)
        # The page remains white in either theme. Use the semantic paper ink.
        accent = QColor(COLORS["light"]["accent"])
        for field,index,rect in self._placed():
            selected = self.selection == (field.uid,index)
            fill = QColor(accent); fill.setAlpha(65 if selected else 30)
            painter.fillRect(rect,fill); painter.setPen(QPen(accent,2 if selected else 1))
            painter.drawRect(rect)
            # Text is drawn on an opaque semantic background, including in dark mode.
            text = field.name + (" · "+field.placements[index].option if field.kind=="radio" else "")
            label = QRectF(rect.x(), max(self.page_rect().y(),rect.y()-18),min(rect.width()+90,220),18)
            painter.fillRect(label,self.palette().color(QPalette.ColorRole.Base))
            painter.setPen(self.palette().color(QPalette.ColorRole.Text)); painter.drawText(label.adjusted(3,0,-2,0),Qt.AlignmentFlag.AlignVCenter,text)
            if selected:
                painter.fillRect(QRectF(rect.right()-5,rect.bottom()-5,10,10),accent)
        if self.hasFocus():
            painter.setPen(QPen(accent,2,Qt.PenStyle.DashLine)); painter.drawRect(self.rect().adjusted(2,2,-3,-3))

    def resizeEvent(self, event):
        super().resizeEvent(event); self.resized.emit()

    def mousePressEvent(self, event):
        if event.button()!=Qt.MouseButton.LeftButton:return
        self.setFocus()
        for field,index,rect in reversed(self._placed()):
            if rect.adjusted(-5,-5,5,5).contains(event.position()):
                self.selected.emit(field.uid,index)
                if self.selection != (field.uid,index): return
                norm = _rotate_rect(field.placements[index].rect,self.rotation)
                resize = QRectF(rect.right()-8,rect.bottom()-8,16,16).contains(event.position())
                self._drag=(field.uid,index,event.position(),norm,resize); self.update(); return

    def mouseMoveEvent(self, event):
        if not self._drag:return
        page=self.page_rect();uid,index,start,rect,resizing=self._drag
        dx=(event.position().x()-start.x())/page.width();dy=(event.position().y()-start.y())/page.height()
        x,y,right,bottom=rect
        if resizing:
            right=max(x+.015,min(1,right+dx));bottom=max(y+.015,min(1,bottom+dy))
        else:
            dx=max(-x,min(1-right,dx));dy=max(-y,min(1-bottom,dy))
            x+=dx;right+=dx;y+=dy;bottom+=dy
        self._draft=(x,y,right,bottom);self.update()

    def mouseReleaseEvent(self, event):
        if self._drag and self._draft:
            self.rect_changed.emit(self._drag[0],self._drag[1],_rotate_rect(self._draft,(-self.rotation)%360))
        self._drag=None;self._draft=None;self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace):self.delete_requested.emit();return
        steps={Qt.Key.Key_Left:(-1,0),Qt.Key.Key_Right:(1,0),Qt.Key.Key_Up:(0,-1),Qt.Key.Key_Down:(0,1)}
        if self.selection and event.key() in steps:
            uid,index=self.selection;field=next((item for item in self.designs if item.uid==uid),None)
            if field:
                x,y,right,bottom=_rotate_rect(field.placements[index].rect,self.rotation)
                step=.01 if event.modifiers()&Qt.KeyboardModifier.ShiftModifier else .002
                dx,dy=steps[event.key()];dx=max(-x,min(1-right,dx*step));dy=max(-y,min(1-bottom,dy*step))
                self.rect_changed.emit(uid,index,_rotate_rect((x+dx,y+dy,right+dx,bottom+dy),(-self.rotation)%360));return
        super().keyPressEvent(event)


class FormEditorDialog(QDialog):
    """``result_source`` is set only after a successful, accepted edit.

    Existing fields keep their original appearance until a property is changed.
    Changed/new fields use a plain, local Helvetica appearance (Windows-1252).
    Workspace rotations affect preview coordinates only, never source geometry.
    """
    def __init__(self, source, parent=None, *, page_index=0, workspace_rotation=0):
        super().__init__(parent)
        self.source=source;self.result_source=None;self.designs=list(form_designs(source))
        self._uid=None;self._placement=0;self._loading=False
        self.initial_page=max(0,min(source.page_count-1,page_index));self.workspace_rotation=workspace_rotation%360
        self.setWindowTitle("Formular gestalten");self.resize(1030,740);self.setMinimumSize(800,560)
        layout=QVBoxLayout(self);layout.setContentsMargins(18,16,18,16);layout.setSpacing(10)
        heading(layout,"Felder, die sich ausfüllen lassen.","Felder hinzufügen, auswählen und verschieben. Position und Größe sind auch per Tastatur einstellbar. Geänderte Felder erhalten eine schlichte Standardschrift.")
        row=QHBoxLayout();row.addWidget(QLabel("Seite"));self.page_combo=QComboBox()
        for index in range(source.page_count):self.page_combo.addItem(str(index+1),index)
        self.page_combo.setCurrentIndex(self.initial_page);self.page_combo.setAccessibleName("Vorschauseite");row.addWidget(self.page_combo)
        row.addStretch();self.type_combo=QComboBox()
        for label,value in KINDS:self.type_combo.addItem(label,value)
        self.type_combo.setAccessibleName("Neuer Feldtyp");row.addWidget(self.type_combo)
        self.add_button=QPushButton("Feld hinzufügen");self.add_button.clicked.connect(self.add_field);row.addWidget(self.add_button);layout.addLayout(row)
        splitter=QSplitter();self.canvas=FormCanvas();splitter.addWidget(self.canvas)
        panel=QWidget();panel.setMinimumWidth(302);side=QVBoxLayout(panel);side.setContentsMargins(7,0,0,0);side.setSpacing(7)
        caption=QLabel("Felder · Reihenfolge für Tabulator");caption.setObjectName("sectionTitle");side.addWidget(caption)
        self.field_list=QListWidget();self.field_list.setAccessibleName("Formularfelder in Tabulatorreihenfolge");self.field_list.setMaximumHeight(116);side.addWidget(self.field_list)
        actions=QHBoxLayout();self.up_button=QPushButton("Nach oben");self.down_button=QPushButton("Nach unten");self.delete_button=QPushButton("Löschen")
        self.up_button.clicked.connect(lambda:self._move(-1));self.down_button.clicked.connect(lambda:self._move(1));self.delete_button.clicked.connect(self.delete_field)
        for button in (self.up_button,self.down_button,self.delete_button):actions.addWidget(button)
        side.addLayout(actions)
        area=QScrollArea();area.setWidgetResizable(True);area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.properties=QWidget();form=QFormLayout(self.properties);form.setContentsMargins(2,4,7,8);form.setSpacing(8);form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.name_edit=QLineEdit();self.label_edit=QLineEdit();self.value_edit=QLineEdit()
        self.required_check=QCheckBox("Pflichtfeld");self.readonly_check=QCheckBox("Schreibgeschützt")
        flags=QWidget();flagsrow=QHBoxLayout(flags);flagsrow.setContentsMargins(0,0,0,0);flagsrow.addWidget(self.required_check);flagsrow.addWidget(self.readonly_check)
        self.placement_combo=QComboBox();self.placement_combo.setAccessibleName("Platzierung oder Radiooption")
        self.placement_page=QSpinBox();self.placement_page.setRange(1,source.page_count)
        self.geometry=[]
        for title in ("Links (%)","Oben (%)","Breite (%)","Höhe (%)"):
            spin=QDoubleSpinBox();spin.setRange(0,100);spin.setDecimals(2);spin.setSingleStep(.5);spin.setAccessibleName(title);self.geometry.append(spin)
        self.options_edit=QPlainTextEdit();self.options_edit.setMaximumHeight(85);self.options_edit.setPlaceholderText("wert1=Beschriftung 1\nwert2=Beschriftung 2")
        self.checkbox_initial=QCheckBox("Anfangs aktiviert");self.multiline_check=QCheckBox("Mehrzeilig")
        self.max_length_spin=QSpinBox();self.max_length_spin.setRange(0,1_000_000);self.max_length_spin.setSpecialValueText("Keine Begrenzung")
        for label,widget in (("Feldname",self.name_edit),("Beschriftung",self.label_edit),("Eigenschaften",flags),
            ("Platzierung",self.placement_combo),("Auf Seite",self.placement_page),
            *zip(("Links (%)","Oben (%)","Breite (%)","Höhe (%)"),self.geometry),
            ("Auswahlwerte",self.options_edit),("Anfangswert",self.value_edit),("",self.checkbox_initial),
            ("Text",self.multiline_check),("Max. Zeichen",self.max_length_spin)):
            form.addRow(label,widget)
            if label:widget.setAccessibleName(label)
        self.property_button=QPushButton("Eigenschaften übernehmen");self.property_button.clicked.connect(self.apply_properties);form.addRow(self.property_button)
        self.property_hint=QLabel("Auswahlwerte: eine Zeile je Option, als wert=Beschriftung. Anfangswert: der Wert links vom Gleichheitszeichen. Radiooptionen werden gemeinsam als ein Feld gespeichert.")
        self.property_hint.setWordWrap(True);self.property_hint.setObjectName("muted");form.addRow(self.property_hint)
        area.setWidget(self.properties);side.addWidget(area,1);splitter.addWidget(panel);splitter.setStretchFactor(0,1);splitter.setSizes([610,350]);layout.addWidget(splitter,1)
        self.error=QLabel();self.error.setWordWrap(True);semantic_label(self.error,"error");layout.addWidget(self.error)
        footer=QHBoxLayout();hint=QLabel("Originaldatei bleibt erhalten · Änderungen sind im Arbeitsbereich rückgängig machbar");hint.setWordWrap(True);hint.setObjectName("muted");footer.addWidget(hint,1)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.Save)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Formular übernehmen");self.buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        self.buttons.accepted.connect(self.accept);self.buttons.rejected.connect(self.reject);footer.addWidget(self.buttons);layout.addLayout(footer)
        self._render_timer=QTimer(self);self._render_timer.setSingleShot(True);self._render_timer.timeout.connect(self._render)
        self.canvas.resized.connect(lambda:self._render_timer.start(80));self.canvas.selected.connect(self._select_field)
        self.canvas.rect_changed.connect(self._rect_changed);self.canvas.delete_requested.connect(self.delete_field)
        self.field_list.currentItemChanged.connect(self._list_selected);self.page_combo.currentIndexChanged.connect(self._page_changed)
        self.placement_combo.currentIndexChanged.connect(self._placement_changed)
        self._make_document();self._refresh_list();self._load_properties();QTimer.singleShot(0,self._render)

    def _make_document(self):
        if any(self.source.page_annotations):
            from ..core import PageRef, _build_writer
            pages=tuple(PageRef(f"form-editor-{index}",self.source.source_id,index,
                annotations=self.source.page_annotations[index] if index<len(self.source.page_annotations) else ())
                for index in range(self.source.page_count))
            writer=_build_writer({self.source.source_id:self.source},pages,"Formularvorschau")
        else:
            reader=PdfReader(io.BytesIO(self.source.data));writer=PdfWriter(clone_from=reader)
        writer.remove_annotations(subtypes="/Widget");writer._root_object.pop("/AcroForm",None)
        stream=io.BytesIO();writer.write(stream)
        self._buffer=QBuffer(self);self._buffer.setData(QByteArray(stream.getvalue()));self._buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self._document=QPdfDocument(self);self._document.load(self._buffer)
        if self._document.status()!=QPdfDocument.Status.Ready:raise PdfWorkbenchError("Die Formularvorschau konnte nicht geöffnet werden.")

    def _rotation(self):
        return self.workspace_rotation if self.page_combo.currentData()==self.initial_page else 0

    def _render(self):
        page=self.page_combo.currentData();size=self._document.pagePointSize(page)
        scale=min(1600/max(size.width(),size.height()),max(1,self.canvas.width())/size.width())
        options=QPdfDocumentRenderOptions();options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
        image=self._document.render(page,QSize(max(1,round(size.width()*scale)),max(1,round(size.height()*scale))),options)
        if self._rotation():image=image.transformed(QTransform().rotate(self._rotation()))
        self.canvas.image=image;self._sync_canvas()

    def _sync_canvas(self):
        self.canvas.designs=tuple(self.designs);self.canvas.page_index=self.page_combo.currentData();self.canvas.rotation=self._rotation()
        self.canvas.selection=(self._uid,self._placement) if self._uid else None;self.canvas.update()

    def _field(self):return next((field for field in self.designs if field.uid==self._uid),None)

    def _refresh_list(self):
        self.field_list.blockSignals(True);self.field_list.clear()
        for field in self.designs:
            item=QListWidgetItem(field.name+" · "+dict((value,label) for label,value in KINDS)[field.kind]);item.setData(Qt.ItemDataRole.UserRole,field.uid);self.field_list.addItem(item)
            if field.uid==self._uid:self.field_list.setCurrentItem(item)
        self.field_list.blockSignals(False);self._sync_canvas()
        self.up_button.setEnabled(bool(self._uid));self.down_button.setEnabled(bool(self._uid));self.delete_button.setEnabled(bool(self._uid))

    def _list_selected(self,item,previous):
        if item:self._select_field(item.data(Qt.ItemDataRole.UserRole))

    def _select_field(self,uid,placement_index=0):
        if self._uid and (uid!=self._uid or placement_index!=self._placement) and not self.apply_properties():self._refresh_list();return False
        field=next((field for field in self.designs if field.uid==uid),None)
        if field is None:return False
        self._uid=uid;self._placement=min(placement_index,len(field.placements)-1)
        self.page_combo.setCurrentIndex(field.placements[self._placement].page_index)
        self._refresh_list();self._load_properties();return True

    def _load_properties(self):
        field=self._field();self.properties.setEnabled(field is not None)
        if field is None:self._sync_canvas();return
        self._loading=True
        self.name_edit.setText(field.name);self.label_edit.setText(field.label);self.required_check.setChecked(field.required);self.readonly_check.setChecked(field.read_only)
        self.value_edit.setText(str(field.value) if field.kind!="checkbox" else "");self.checkbox_initial.setChecked(bool(field.value) if field.kind=="checkbox" else False)
        self.options_edit.setPlainText("\n".join(value+"="+label for value,label in field.options));self.options_edit.setEnabled(field.kind in ("choice","radio"))
        self.value_edit.setEnabled(field.kind!="checkbox");self.checkbox_initial.setVisible(field.kind=="checkbox")
        self.multiline_check.setChecked(field.multiline);self.multiline_check.setEnabled(field.kind=="text")
        self.max_length_spin.setValue(field.max_length or 0);self.max_length_spin.setEnabled(field.kind=="text")
        self.placement_combo.clear()
        for index,place in enumerate(field.placements):self.placement_combo.addItem((dict(field.options).get(place.option,place.option)+" · " if place.option else "")+f"Seite {place.page_index+1}",index)
        self.placement_combo.setCurrentIndex(self._placement);self._load_geometry();self._loading=False;self._sync_canvas()

    def _load_geometry(self):
        field=self._field()
        if not field:return
        place=field.placements[self._placement];self.placement_page.setValue(place.page_index+1)
        rotation=self.workspace_rotation if place.page_index==self.initial_page else 0
        x,y,right,bottom=_rotate_rect(place.rect,rotation)
        for spin,value in zip(self.geometry,(x,y,right-x,bottom-y)):spin.setValue(value*100)
        self._loaded_geometry=(self.placement_page.value(),tuple(spin.value() for spin in self.geometry))

    def _placement_changed(self,index):
        if self._loading or index<0:return
        if not self.apply_properties():
            self.placement_combo.blockSignals(True);self.placement_combo.setCurrentIndex(self._placement);self.placement_combo.blockSignals(False);return
        field=self._field();self._placement=min(index,len(field.placements)-1)
        self.page_combo.setCurrentIndex(field.placements[self._placement].page_index);self._load_properties()

    def _page_changed(self):self._render_timer.start(0)

    def apply_properties(self):
        field=self._field()
        if self._loading or field is None:return True
        try:
            options=[]
            if field.kind in ("choice","radio"):
                for line in self.options_edit.toPlainText().splitlines():
                    if not line.strip():continue
                    value,separator,label=line.partition("=")
                    options.append((value.strip(),label.strip() if separator else value.strip()))
            placements=list(field.placements)
            x,y,w,h=[spin.value()/100 for spin in self.geometry];page=self.placement_page.value()-1
            rotation=self.workspace_rotation if page==self.initial_page else 0
            # Spinbox roundoff must not turn an edge at 100% into an invalid box.
            rect=_rotate_rect((x,y,min(1,x+w),min(1,y+h)),(-rotation)%360)
            if self._loaded_geometry!=(self.placement_page.value(),tuple(spin.value() for spin in self.geometry)):
                placements[self._placement]=replace(placements[self._placement],page_index=page,rect=rect)
            if field.kind=="radio":
                keys={option[0] for option in options};placements=[place for place in placements if place.option in keys]
                for index,(value,label) in enumerate(options):
                    if not any(place.option==value for place in placements):
                        top=min(.85,.18+index*.085);placements.append(FieldPlacement(page,(.12,top,.65,top+.055),value))
            value=self.checkbox_initial.isChecked() if field.kind=="checkbox" else self.value_edit.text()
            updated=replace(field,name=self.name_edit.text().strip(),label=self.label_edit.text(),options=tuple(options),placements=tuple(placements),
                value=value,required=self.required_check.isChecked(),read_only=self.readonly_check.isChecked(),
                multiline=self.multiline_check.isChecked() if field.kind=="text" else False,
                max_length=(self.max_length_spin.value() or None) if field.kind=="text" else None)
            if any(item.uid!=field.uid and item.name==updated.name for item in self.designs):raise PdfWorkbenchError("Dieser Feldname ist bereits vergeben.")
            self.designs[self.designs.index(field)]=updated;self._placement=min(self._placement,len(updated.placements)-1)
            self.error.clear();self._refresh_list();self._load_properties();return True
        except (PdfWorkbenchError,ValueError,TypeError) as error:
            self.error.setText(str(error));return False

    def add_field(self):
        if not self.apply_properties():return None
        kind=self.type_combo.currentData();base={"text":"Text","checkbox":"Auswahl","choice":"Dropdown","radio":"Optionen"}[kind]
        index=1;names={field.name for field in self.designs}
        while f"{base}{index}" in names:index+=1
        page=self.page_combo.currentData();top=.16+(len(self.designs)%6)*.10
        options=(("option1","Option 1"),("option2","Option 2")) if kind in ("choice","radio") else ()
        placements=(FieldPlacement(page,(.12,top,.66,top+.055)),)
        if kind=="radio":placements=tuple(FieldPlacement(page,(.12,top+index*.075,.66,top+index*.075+.055),value) for index,(value,label) in enumerate(options))
        field=FormDesign(uuid4().hex,f"{base}{index}",base,kind,placements,options,False if kind=="checkbox" else "",tab_order=len(self.designs))
        self.designs.append(field);self._uid=field.uid;self._placement=0;self._refresh_list();self._load_properties();self.name_edit.setFocus();self.name_edit.selectAll();return field.uid

    def delete_field(self):
        field=self._field()
        if field is None:return
        index=self.designs.index(field);self.designs.remove(field)
        self._uid=self.designs[min(index,len(self.designs)-1)].uid if self.designs else None;self._placement=0
        self.error.clear();self._reorder();self._refresh_list();self._load_properties()

    def _reorder(self):self.designs=[replace(field,tab_order=index) for index,field in enumerate(self.designs)]

    def _move(self,delta):
        if not self.apply_properties():return
        field=self._field()
        if field is None:return
        old=self.designs.index(field);new=old+delta
        if 0<=new<len(self.designs):self.designs.insert(new,self.designs.pop(old));self._reorder();self._refresh_list()

    def _rect_changed(self,uid,index,rect):
        if not self._select_field(uid,index):return
        field=self._field();placements=list(field.placements)
        try:
            placements[index]=replace(placements[index],rect=rect);self.designs[self.designs.index(field)]=replace(field,placements=tuple(placements))
            self._load_geometry();self._sync_canvas()
        except PdfWorkbenchError as error:self.error.setText(str(error))

    def accept(self):
        if not self.apply_properties():return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:self.result_source=apply_form_designs(self.source,self.designs)
        except (PdfWorkbenchError,ValueError,TypeError) as error:self.error.setText(str(error));return
        finally:QApplication.restoreOverrideCursor()
        super().accept()

    @classmethod
    def get_source(cls,parent,source,*,page_index=0,workspace_rotation=0):
        dialog=cls(source,parent,page_index=page_index,workspace_rotation=workspace_rotation)
        result = dialog.result_source if dialog.exec()==QDialog.DialogCode.Accepted else None
        dialog.deleteLater()
        return result
