"""Compact, keyboard-accessible crop/fit tools with a cancellable PDF preview."""
from __future__ import annotations

import threading

from PyQt6.QtCore import QByteArray,QBuffer,QIODevice,QPointF,QRectF,QSize,Qt,QThread,QTimer,pyqtSignal
from PyQt6.QtGui import QColor,QImage,QPainter,QPen,QTransform
from PyQt6.QtPdf import QPdfDocument,QPdfDocumentRenderOptions
from PyQt6.QtWidgets import (QCheckBox,QComboBox,QDialog,QDialogButtonBox,QDoubleSpinBox,QFormLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QScrollArea,QSizePolicy,QVBoxLayout,QWidget)

from ..core import OperationCancelled,PdfWorkbenchError
from ..features import parse_page_selection
from ..form_rendering import form_render_data
from ..page_geometry import PageGeometryOptions,PageGeometryRequest,page_display_size,prepare_page_geometry
from ..page_marks import target_pages
from .pdf_dialogs import heading
from .theme import color,semantic_label

PT_PER_MM=72/25.4


class GeometryPreview(QWidget):
    cropChanged=pyqtSignal(tuple)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.image=QImage();self.result_image=QImage();self.crop=(0.,0.,1.,1.);self.cropping=True;self.show_result=False
        self._drag=None;self.setMinimumSize(220,170);self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus);self.setMouseTracking(True)
        self.setAccessibleName('Zuschnittrahmen der PDF-Seite')
        self.setAccessibleDescription('Rahmen mit Maus ziehen oder die vier beschrifteten Randfelder verwenden. Pfeiltasten verschieben den Rahmen, Umschalt vergrößert den Schritt.')

    def paper_rect(self):
        image=self.result_image if self.show_result else self.image
        if image.isNull():return QRectF()
        bounds=self.rect().adjusted(14,14,-14,-14)
        size=image.size().scaled(bounds.size(),Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF(bounds.center().x()-size.width()/2,bounds.center().y()-size.height()/2,size.width(),size.height())

    def selection_rect(self):
        paper=self.paper_rect();l,t,r,b=self.crop
        return QRectF(paper.left()+l*paper.width(),paper.top()+t*paper.height(),(r-l)*paper.width(),(b-t)*paper.height())

    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),color('canvas'))
        paper=self.paper_rect()
        if paper.isEmpty():return
        p.fillRect(paper.translated(3,3),color('shadow'));p.fillRect(paper,QColor('white'))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform);p.drawImage(paper,self.result_image if self.show_result else self.image)
        if not self.cropping or self.show_result:return
        selection=self.selection_rect();shade=QColor(9,27,28,125)
        for rect in (QRectF(paper.left(),paper.top(),paper.width(),selection.top()-paper.top()),QRectF(paper.left(),selection.bottom(),paper.width(),paper.bottom()-selection.bottom()),
                     QRectF(paper.left(),selection.top(),selection.left()-paper.left(),selection.height()),QRectF(selection.right(),selection.top(),paper.right()-selection.right(),selection.height())):p.fillRect(rect,shade)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen=QPen(color('accent'),2);p.setPen(pen);p.drawRect(selection)
        for point in (selection.topLeft(),selection.topRight(),selection.bottomLeft(),selection.bottomRight()):
            p.fillRect(QRectF(point.x()-4,point.y()-4,8,8),QColor('white'));p.drawRect(QRectF(point.x()-4,point.y()-4,8,8))
        if self.hasFocus():
            p.setPen(QPen(color('accent'),1,Qt.PenStyle.DotLine));p.drawRect(self.rect().adjusted(3,3,-4,-4))

    def _relative(self,point):
        paper=self.paper_rect()
        return (max(0.,min(1.,(point.x()-paper.left())/paper.width())),max(0.,min(1.,(point.y()-paper.top())/paper.height())))

    def mousePressEvent(self,event):
        if event.button()!=Qt.MouseButton.LeftButton or not self.cropping or self.show_result or self.paper_rect().isEmpty():return
        self.setFocus();selection=self.selection_rect();point=event.position();l,t,r,b=self.crop
        edge=''
        if abs(point.x()-selection.left())<10:edge+='l'
        elif abs(point.x()-selection.right())<10:edge+='r'
        if abs(point.y()-selection.top())<10:edge+='t'
        elif abs(point.y()-selection.bottom())<10:edge+='b'
        if not edge and selection.contains(point):edge='move'
        if not edge:
            if not self.paper_rect().contains(point):return
            edge='new'
        self._drag=(edge,self._relative(point),self.crop)

    def mouseMoveEvent(self,event):
        if self._drag is None:return
        edge,start,old=self._drag;x,y=self._relative(event.position());l,t,r,b=old
        if edge=='move':
            dx=max(-l,min(1-r,x-start[0]));dy=max(-t,min(1-b,y-start[1]));l+=dx;r+=dx;t+=dy;b+=dy
        elif edge=='new':l,r=sorted((start[0],x));t,b=sorted((start[1],y))
        else:
            if 'l' in edge:l=min(x,r-.01)
            if 'r' in edge:r=max(x,l+.01)
            if 't' in edge:t=min(y,b-.01)
            if 'b' in edge:b=max(y,t+.01)
        if r-l>.005 and b-t>.005:
            self.crop=(l,t,r,b);self.cropChanged.emit(self.crop);self.update()

    def mouseReleaseEvent(self,event):self._drag=None
    def keyPressEvent(self,event):
        if not self.cropping or self.show_result:return super().keyPressEvent(event)
        delta=.02 if event.modifiers()&Qt.KeyboardModifier.ShiftModifier else .002
        movement={Qt.Key.Key_Left:(-delta,0),Qt.Key.Key_Right:(delta,0),Qt.Key.Key_Up:(0,-delta),Qt.Key.Key_Down:(0,delta)}
        if event.key() not in movement:return super().keyPressEvent(event)
        l,t,r,b=self.crop;dx,dy=movement[event.key()];dx=max(-l,min(1-r,dx));dy=max(-t,min(1-b,dy))
        self.crop=(l+dx,t+dy,r+dx,b+dy);self.cropChanged.emit(self.crop);self.update()


class GeometryPreviewWorker(QThread):
    result=pyqtSignal(int,QImage,QImage,tuple,tuple,int)
    failed=pyqtSignal(int,str)
    def __init__(self,sources,pages,request,uid,generation,parent,original=None):
        super().__init__(parent);self.sources,self.pages,self.request=sources,pages,request;self.uid,self.generation=uid,generation
        self.cancel_event=threading.Event();self.original=original
    def render(self,source,page):
        document=QPdfDocument(None)
        try:
            buffer=QBuffer();buffer.setData(QByteArray(form_render_data(source,self.cancel_event.is_set)));buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            document.load(buffer)
            if document.status()!=QPdfDocument.Status.Ready:raise PdfWorkbenchError('Die PDF-Vorschau konnte nicht geöffnet werden.')
            size=document.pagePointSize(page.page_index).toSize().scaled(QSize(720,720),Qt.AspectRatioMode.KeepAspectRatio)
            flags=QPdfDocumentRenderOptions();flags.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
            image=document.render(page.page_index,size,flags)
            if image.isNull():raise PdfWorkbenchError('Für diese PDF-Seite ist keine Vorschau verfügbar.')
            return image.transformed(QTransform().rotate(page.rotation)) if page.rotation else image
        finally:document.close()
    def prepared(self,options):
        result=prepare_page_geometry(self.sources,self.pages,options,self.request.target_ids,self.cancel_event.is_set,preview_uid=self.uid)
        page=next(p for p in result.pages if p.uid==self.uid);source=next(s for s in result.sources if s.source_id==page.source_id)
        return result,page,source
    def run(self):
        try:
            original_page=next(p for p in self.pages if p.uid==self.uid)
            old_size=page_display_size(self.sources[original_page.source_id],original_page)
            original=self.original
            if original is None:
                _,page,source=self.prepared(PageGeometryOptions(crop_margins=(0,0,0,0)))
                original=self.render(source,page)
            if self.cancel_event.is_set():return
            result,page,source=self.prepared(self.request.options);image=self.render(source,page)
            size=page_display_size(source,page)
            if not self.cancel_event.is_set():self.result.emit(self.generation,original,image,old_size,size,result.flattened_annotation_count)
        except OperationCancelled:pass
        except Exception as error:
            if not self.cancel_event.is_set():self.failed.emit(self.generation,str(error))


class PageGeometryDialog(QDialog):
    def __init__(self,parent,workspace,selected_ids=(),*,mode='crop'):
        super().__init__(parent)
        if mode not in ('crop','resize'):raise ValueError('Unknown geometry mode')
        self.mode=mode;self.sources=dict(workspace.sources);self.pages=tuple(workspace.pages)
        self.selected_ids=tuple(uid for uid in selected_ids if uid in {p.uid for p in self.pages})
        self._generation=0;self.worker=None;self._ready_request=None;self._closing=False;self._old_size=None;self._cached_original=None
        self.setWindowTitle('Seiten zuschneiden' if mode=='crop' else 'Seitengröße anpassen');self.resize(780,550);self.setMinimumSize(680,460)
        layout=QVBoxLayout(self);layout.setContentsMargins(20,18,20,16);layout.setSpacing(10)
        heading(layout,'Den Ausschnitt bestimmen.' if mode=='crop' else 'Platz für den Inhalt.',
                'Sichtbare Ränder präzise wählen. Originale bleiben erhalten.' if mode=='crop' else 'Inhalt proportional auf eine neue Seite einpassen. Die Änderung ist rückgängig machbar.')
        body=QHBoxLayout();body.setSpacing(16);layout.addLayout(body,1)
        settings=QWidget();settings_layout=QVBoxLayout(settings);settings_layout.setContentsMargins(0,0,6,0);settings_layout.setSpacing(10)
        form=QFormLayout();form.setSpacing(9);form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.scope=QComboBox();self.scope.addItem('Alle Arbeitsseiten','all');self.scope.addItem(f'Ausgewählte Seiten ({len(self.selected_ids)})','selected');self.scope.addItem('Seitenbereich …','range');self.scope.setCurrentIndex(1 if self.selected_ids else 0)
        self._row(form,'Anwenden auf',self.scope)
        self.range_edit=QLineEdit();self.range_edit.setPlaceholderText('z. B. 1-3, 5');self.range_label=self._row(form,'Seitenbereich',self.range_edit)
        self.margin_controls=[]
        for name in ('Links','Oben','Rechts','Unten'):
            control=self._mm(0,5000,0);self.margin_controls.append(control)
            if mode=='crop':self._row(form,name,control)
        self.reset=QPushButton('Ränder zurücksetzen');self.reset.clicked.connect(self._reset_crop)
        if mode=='crop':form.addRow(self.reset)
        self.paper=QComboBox();self.paper.addItem('A4 · 210 × 297 mm',(595.275590551,841.88976378));self.paper.addItem('Letter · 216 × 279 mm',(612.,792.));self.paper.addItem('Eigene Größe',None)
        self.landscape=QCheckBox('Querformat');self.target_width=self._mm(6.35,5080,210);self.target_height=self._mm(6.35,5080,297)
        self.margin=self._mm(0,5000,0);self.position=QComboBox()
        for label,value in (('Mitte','center'),('Oben links','top-left'),('Oben mittig','top-center'),('Oben rechts','top-right'),('Mittig links','center-left'),('Mittig rechts','center-right'),('Unten links','bottom-left'),('Unten mittig','bottom-center'),('Unten rechts','bottom-right')):self.position.addItem(label,value)
        self.upscale=QCheckBox('Kleine Seiten vergrößern');self.upscale.setChecked(True)
        if mode=='resize':
            self._row(form,'Papierformat',self.paper);form.addRow(self.landscape)
            self.target_width_label=self._row(form,'Breite',self.target_width);self.target_height_label=self._row(form,'Höhe',self.target_height)
            self._row(form,'Innenrand',self.margin);self._row(form,'Inhaltsposition',self.position);form.addRow(self.upscale)
        settings_layout.addLayout(form)
        hint=QLabel('Rahmen ziehen oder vier Ränder in Millimetern eingeben.' if mode=='crop' else 'Seiten werden ohne Verzerrung eingepasst. Freie Fläche bleibt weiß; bestehende Seiteninhalte werden gemeinsam skaliert.')
        hint.setWordWrap(True);hint.setObjectName('muted');settings_layout.addWidget(hint);settings_layout.addStretch()
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QScrollArea.Shape.NoFrame);scroll.setWidget(settings);scroll.setMinimumWidth(300);body.addWidget(scroll,1)
        column=QVBoxLayout();column.setSpacing(7)
        self.preview_page=QComboBox();label=QLabel('Vorschauseite');label.setBuddy(self.preview_page);self.preview_page.setAccessibleName('Arbeitsseite für die Geometrievorschau');column.addWidget(label);column.addWidget(self.preview_page)
        self.preview=GeometryPreview();self.preview.cropping=mode=='crop';column.addWidget(self.preview,1)
        self.result_toggle=QCheckBox('Ergebnis anzeigen');self.result_toggle.setChecked(mode=='resize');self.result_toggle.setVisible(mode=='crop');column.addWidget(self.result_toggle)
        self.preview_caption=QLabel('Vorschau wird vorbereitet …');self.preview_caption.setWordWrap(True);self.preview_caption.setObjectName('muted');column.addWidget(self.preview_caption)
        body.addLayout(column,1)
        warning=QLabel('Zuschneiden verbirgt Inhalte und entfernt sie nicht sicher. Angeschnittene eigene Objekte bleiben als PDF-Anmerkungen erhalten und sind hier nicht mehr direkt bearbeitbar.' if mode=='crop' else 'Eigene Textobjekte bleiben bei 4–288 pt editierbar. Kleinere oder größere Ergebnisse werden ausdrücklich abgewiesen.')
        warning.setObjectName('muted');warning.setWordWrap(True);layout.addWidget(warning)
        self.error=QLabel();self.error.setWordWrap(True);semantic_label(self.error,'error');self.error.setAccessibleName('Hinweis zur Seitengeometrie');layout.addWidget(self.error)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel)
        self.apply_button=self.buttons.button(QDialogButtonBox.StandardButton.Apply);self.apply_button.setText('Auf Seiten anwenden');self.apply_button.setObjectName('primary');self.apply_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Abbrechen');self.apply_button.clicked.connect(self.accept);self.buttons.rejected.connect(self.reject);layout.addWidget(self.buttons)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(180);self.timer.timeout.connect(self._start_preview)
        for control in (*self.margin_controls,self.target_width,self.target_height,self.margin):control.valueChanged.connect(self._schedule)
        for control in (self.landscape,self.upscale):control.toggled.connect(self._schedule)
        self.paper.currentIndexChanged.connect(self._paper_changed);self.position.currentIndexChanged.connect(self._schedule)
        self.range_edit.textChanged.connect(self._range_changed);self.scope.currentIndexChanged.connect(self._scope_changed)
        self.preview_page.currentIndexChanged.connect(self._page_changed);self.preview.cropChanged.connect(self._crop_changed);self.result_toggle.toggled.connect(self._toggle_result)
        self._scope_changed()
        if mode=='resize':self._paper_changed()
        else:self.margin_controls[0].setFocus()

    def _mm(self,low,high,value):
        control=QDoubleSpinBox();control.setRange(low,high);control.setDecimals(2);control.setSingleStep(1);control.setSuffix(' mm');control.setValue(value);return control
    def _row(self,form,text,control):
        label=QLabel(text);label.setBuddy(control);control.setAccessibleName(text);form.addRow(label,control);return label
    def _targets(self):
        if self.scope.currentData()=='all':return tuple(p.uid for p in self.pages)
        if self.scope.currentData()=='selected':return self.selected_ids
        return tuple(self.pages[i].uid for i in parse_page_selection(self.range_edit.text(),len(self.pages)))
    def _refresh_pages(self):
        old=self.preview_page.currentData();self.preview_page.blockSignals(True);self.preview_page.clear()
        try:
            for index,page in target_pages(self.pages,self._targets()):self.preview_page.addItem(f'Seite {index+1} · {self.sources[page.source_id].label}',page.uid)
            index=self.preview_page.findData(old)
            if index>=0:self.preview_page.setCurrentIndex(index)
        except PdfWorkbenchError:pass
        self.preview_page.blockSignals(False)
        if old!=self.preview_page.currentData():self._cached_original=None;self._old_size=None
    def _scope_changed(self):
        ranged=self.scope.currentData()=='range';self.range_edit.setVisible(ranged);self.range_label.setVisible(ranged);self._refresh_pages();self._schedule()
    def _range_changed(self):self._refresh_pages();self._schedule()
    def _page_changed(self):self._cached_original=None;self._old_size=None;self._schedule()
    def _paper_changed(self):
        custom=self.paper.currentData() is None
        for widget in (self.target_width_label,self.target_width,self.target_height_label,self.target_height):widget.setVisible(custom)
        self.landscape.setVisible(not custom);self._schedule()
    def _reset_crop(self):
        for control in self.margin_controls:control.setValue(0)
    def _crop_changed(self,rect):
        if self._old_size is None:return
        w,h=self._old_size;l,t,r,b=rect
        for control,value in zip(self.margin_controls,(l*w,t*h,(1-r)*w,(1-b)*h)):
            control.blockSignals(True);control.setValue(value/PT_PER_MM);control.blockSignals(False)
        self._schedule()
    def _sync_crop(self):
        if self._old_size is None:return
        w,h=self._old_size;l,t,r,b=(c.value()*PT_PER_MM for c in self.margin_controls)
        self.preview.crop=(min(1,l/w),min(1,t/h),max(0,1-r/w),max(0,1-b/h));self.preview.update()
    def _toggle_result(self,checked):
        self.preview.show_result=checked;self.preview.update()
        motion=getattr(self.parent(),'motion',None)
        if motion is not None:motion.feedback(self.preview)
    @property
    def request(self):
        if self.mode=='crop':options=PageGeometryOptions(crop_margins=tuple(c.value()*PT_PER_MM for c in self.margin_controls))
        else:
            size=self.paper.currentData()
            if size is None:size=(self.target_width.value()*PT_PER_MM,self.target_height.value()*PT_PER_MM)
            elif self.landscape.isChecked():size=tuple(reversed(size))
            options=PageGeometryOptions(target_size=size,margin=self.margin.value()*PT_PER_MM,position=self.position.currentData(),allow_upscale=self.upscale.isChecked())
        ids=self._targets();target_pages(self.pages,ids);return PageGeometryRequest(options,ids)
    def _schedule(self,*_):
        if self._closing:return
        self._generation+=1;self._ready_request=None;self.apply_button.setEnabled(False);self.error.clear();self._sync_crop()
        self.preview_caption.setText('Vorschau wird aktualisiert …')
        if self.worker:self.worker.cancel_event.set()
        self.timer.start()
    def _start_preview(self):
        if self._closing or self.worker:return
        try:
            request=self.request;uid=self.preview_page.currentData()
            if uid is None:raise PdfWorkbenchError('Bitte eine vorhandene Vorschauseite wählen.')
        except Exception as error:self.error.setText(str(error));self.preview_caption.setText('Einstellungen bitte prüfen.');return
        self.worker=GeometryPreviewWorker(self.sources,self.pages,request,uid,self._generation,self,self._cached_original)
        self.worker.result.connect(lambda *args:self._preview_ready(*args,request));self.worker.failed.connect(self._failed);self.worker.finished.connect(self._finished);self.worker.start()
    def _preview_ready(self,generation,original,image,old_size,size,demoted,request):
        if self._closing or generation!=self._generation:return
        self._cached_original=original;self._old_size=old_size;self.preview.image=original;self.preview.result_image=image;self.preview.show_result=self.result_toggle.isChecked();self._sync_crop();self.preview.update();self._ready_request=request
        caption=f'Echte PDF-Vorschau · {size[0]/PT_PER_MM:.1f} × {size[1]/PT_PER_MM:.1f} mm · {len(request.target_ids)} Seite(n)'
        if demoted:caption+=f'\n{demoted} eigene Objekte werden normale PDF-Anmerkungen.'
        self.preview_caption.setText(caption)
    def _failed(self,generation,error):
        if self._closing or generation!=self._generation:return
        self.error.setText(error);self.preview_caption.setText('Einstellungen bitte prüfen.')
    def _finished(self):
        worker,self.worker=self.worker,None;worker.deleteLater()
        if self._closing:super().reject();return
        self.apply_button.setEnabled(self._ready_request is not None)
        if self._ready_request is None and not self.error.text() and not self.timer.isActive():self.timer.start(0)
    def accept(self):
        if self._ready_request is not None and self.worker is None and not self.timer.isActive():super().accept()
    def reject(self):
        self._closing=True;self.timer.stop()
        if self.worker:self.worker.cancel_event.set();self.buttons.setEnabled(False);self.preview_caption.setText('Vorschau wird beendet …')
        else:super().reject()
    def closeEvent(self,event):
        if self.worker:self.reject();event.ignore()
        else:super().closeEvent(event)
    @classmethod
    def get_request(cls,parent,workspace,selected_ids=(),*,mode='crop'):
        dialog=cls(parent,workspace,selected_ids,mode=mode);result=dialog._ready_request if dialog.exec()==QDialog.DialogCode.Accepted else None
        dialog.deleteLater();return result
