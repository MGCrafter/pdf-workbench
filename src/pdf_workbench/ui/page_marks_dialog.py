"""Keyboard-accessible page markings with an asynchronous, real-PDF preview."""
from __future__ import annotations

import threading

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QTransform
from PyQt6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions
from PyQt6.QtWidgets import (
    QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QVBoxLayout, QWidget,
)

from ..core import OperationCancelled, PdfWorkbenchError
from ..features import parse_page_selection
from ..form_rendering import form_render_data
from ..page_marks import (
    PageMarksOptions, PageMarksRequest, PageNumberOptions, WatermarkOptions,
    prepare_page_marks, target_pages,
)
from .pdf_dialogs import heading
from .theme import color, semantic_label


class MarkPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.setMinimumSize(220, 160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Vorschau der sichtbaren PDF-Seite mit Markierung")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), color("canvas"))
        if self.image.isNull(): return
        bounds = self.rect().adjusted(12, 12, -12, -12)
        size = self.image.size().scaled(bounds.size(), Qt.AspectRatioMode.KeepAspectRatio)
        paper = QRectF(bounds.center().x()-size.width()/2, bounds.center().y()-size.height()/2, size.width(), size.height())
        painter.fillRect(paper.translated(3, 3), color("shadow"))
        painter.fillRect(paper, QColor("white"))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(paper, self.image)


class MarkPreviewWorker(QThread):
    result = pyqtSignal(int, QImage)
    failed = pyqtSignal(int, str)

    def __init__(self, sources, pages, request, uid, generation, parent):
        super().__init__(parent)
        self.sources, self.pages, self.request = sources, pages, request
        self.uid, self.generation = uid, generation
        self.cancel_event = threading.Event()

    def run(self):
        document = None
        try:
            result = prepare_page_marks(self.sources, self.pages, self.request.options, self.request.target_ids,
                                        self.cancel_event.is_set, preview_uid=self.uid)
            page = next(page for page in result.pages if page.uid == self.uid)
            source = next(source for source in result.sources if source.source_id == page.source_id)
            document = QPdfDocument(None)
            buffer = QBuffer(); buffer.setData(QByteArray(form_render_data(source,self.cancel_event.is_set))); buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            document.load(buffer)
            if document.status() != QPdfDocument.Status.Ready:
                raise PdfWorkbenchError("Die markierte PDF konnte nicht als Vorschau geöffnet werden.")
            if self.cancel_event.is_set(): return
            size = document.pagePointSize(page.page_index).toSize().scaled(QSize(660, 660), Qt.AspectRatioMode.KeepAspectRatio)
            options = QPdfDocumentRenderOptions()
            options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
            image = document.render(page.page_index, size, options)
            if image.isNull(): raise PdfWorkbenchError("Für diese PDF-Seite ist keine Vorschau verfügbar.")
            if page.rotation: image = image.transformed(QTransform().rotate(page.rotation))
            if not self.cancel_event.is_set(): self.result.emit(self.generation, image)
        except OperationCancelled:
            pass
        except Exception as error:
            if not self.cancel_event.is_set(): self.failed.emit(self.generation, str(error))
        finally:
            if document is not None: document.close()


class PageMarksDialog(QDialog):
    def __init__(self, parent, workspace, selected_ids=(), *, mode="watermark"):
        super().__init__(parent)
        if mode not in ("watermark", "numbering"): raise ValueError("Unknown page marking mode")
        self.mode = mode
        self.sources, self.pages = dict(workspace.sources), tuple(workspace.pages)
        self.selected_ids = tuple(uid for uid in selected_ids if uid in {page.uid for page in self.pages})
        self._generation, self.worker, self._ready_request, self._closing = 0, None, None, False
        self.setWindowTitle("Wasserzeichen" if mode == "watermark" else "Kopf- und Fußzeilen")
        self.resize(760, 520)
        self.setMinimumSize(660, 440)
        layout = QVBoxLayout(self); layout.setContentsMargins(20, 18, 20, 16); layout.setSpacing(12)
        heading(layout, "Ein Zeichen auf jeder Seite." if mode == "watermark" else "Seiten mit Orientierung.",
                "Die Vorschau zeigt neue PDF-Inhalte. Originale bleiben erhalten; die Änderung lässt sich gemeinsam rückgängig machen.")
        body = QHBoxLayout(); body.setSpacing(16); layout.addLayout(body, 1)
        settings = QWidget(); settings_layout = QVBoxLayout(settings); settings_layout.setContentsMargins(0,0,6,0); settings_layout.setSpacing(12)
        form = QFormLayout(); form.setSpacing(10); form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.scope = QComboBox()
        self.scope.addItem("Alle Arbeitsseiten", "all")
        self.scope.addItem(f"Ausgewählte Seiten ({len(self.selected_ids)})", "selected")
        self.scope.addItem("Seitenbereich …", "range")
        self.scope.setCurrentIndex(1 if self.selected_ids else 0)
        self.range_edit = QLineEdit(); self.range_edit.setPlaceholderText("z. B. 1-3, 5")
        self._row(form, "Anwenden auf", self.scope)
        self.range_label = self._row(form, "Seitenbereich", self.range_edit)
        self.text = QLineEdit("ENTWURF" if mode == "watermark" else "Seite {page} von {pages}")
        self._row(form, "Wasserzeichentext" if mode == "watermark" else "Textvorlage", self.text)
        self.position = QComboBox()
        positions = (("Mitte", "center"),) if mode == "watermark" else ()
        positions += (("Oben links", "top-left"), ("Oben mittig", "top-center"), ("Oben rechts", "top-right"),
                      ("Unten links", "bottom-left"), ("Unten mittig", "bottom-center"), ("Unten rechts", "bottom-right"))
        for label, value in positions: self.position.addItem(label, value)
        self.position.setCurrentIndex(0 if mode == "watermark" else 4)
        self._row(form, "Position", self.position)
        self.font_size = QDoubleSpinBox(); self.font_size.setRange(4, 288); self.font_size.setDecimals(1); self.font_size.setSuffix(" pt"); self.font_size.setValue(48 if mode == "watermark" else 10)
        self._row(form, "Schriftgröße", self.font_size)
        self.color_edit = QLineEdit("#1F524F"); self.color_edit.setMaxLength(7)
        self.color_button = QPushButton("Wählen …"); self.color_button.setAccessibleName("Farbe für die PDF-Markierung wählen"); self.color_button.clicked.connect(self._choose_color)
        color_row = QWidget(); color_layout = QHBoxLayout(color_row); color_layout.setContentsMargins(0,0,0,0); color_layout.setSpacing(6); color_layout.addWidget(self.color_edit, 1); color_layout.addWidget(self.color_button)
        label = QLabel("Farbe (Hex)"); label.setBuddy(self.color_edit); form.addRow(label, color_row); self.color_edit.setAccessibleName("Markierungsfarbe als Hexwert")
        self.margin = QDoubleSpinBox(); self.margin.setRange(0, 288); self.margin.setDecimals(1); self.margin.setSuffix(" pt"); self.margin.setValue(24 if mode == "watermark" else 18)
        self._row(form, "Rand", self.margin)
        self.opacity = QSpinBox(); self.opacity.setRange(1,100); self.opacity.setValue(20); self.opacity.setSuffix(" %")
        self.angle = QSpinBox(); self.angle.setRange(-180,180); self.angle.setValue(45); self.angle.setSuffix(" °")
        self.start = QSpinBox(); self.start.setRange(0,1_000_000_000); self.start.setValue(1)
        self.counting = QComboBox(); self.counting.addItem("Nach Dokumentposition", "document"); self.counting.addItem("Nur markierte Seiten zählen", "targets")
        if mode == "watermark":
            self._row(form, "Deckkraft", self.opacity); self._row(form, "Drehung", self.angle)
        else:
            self._row(form, "Startnummer", self.start); self._row(form, "Zählweise", self.counting)
        settings_layout.addLayout(form)
        help_text = "Sichtbarer Inhalt, kein Schutz vor Kopieren oder Entfernen. Für größere Texte die Schrift verkleinern." if mode == "watermark" else "{page} = Seitennummer · {pages} = Seitenanzahl. Dokumentposition zählt alle Arbeitsseiten, die zweite Zählweise nur markierte Seiten. Vorhandene Inhalte können überlagert werden."
        hint = QLabel(help_text); hint.setWordWrap(True); hint.setObjectName("muted"); settings_layout.addWidget(hint); settings_layout.addStretch()
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.Shape.NoFrame); scroll.setWidget(settings); scroll.setMinimumWidth(310); body.addWidget(scroll, 1)
        preview_column = QVBoxLayout(); preview_column.setSpacing(8)
        self.preview_page = QComboBox(); self.preview_page.setAccessibleName("Arbeitsseite für die Markierungsvorschau")
        preview_label = QLabel("Vorschauseite"); preview_label.setBuddy(self.preview_page); preview_column.addWidget(preview_label); preview_column.addWidget(self.preview_page)
        self.preview = MarkPreview(); preview_column.addWidget(self.preview,1)
        self.preview_caption = QLabel("Vorschau wird vorbereitet …"); self.preview_caption.setWordWrap(True); self.preview_caption.setObjectName("muted"); preview_column.addWidget(self.preview_caption)
        body.addLayout(preview_column,1)
        self.error = QLabel(); self.error.setWordWrap(True); self.error.setAccessibleName("Hinweis zur Seitenmarkierung"); semantic_label(self.error, "error"); layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = self.buttons.button(QDialogButtonBox.StandardButton.Apply); self.apply_button.setText("Auf Seiten anwenden"); self.apply_button.setObjectName("primary"); self.apply_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.apply_button.clicked.connect(self.accept); self.buttons.rejected.connect(self.reject); layout.addWidget(self.buttons)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.setInterval(180); self.timer.timeout.connect(self._start_preview)
        for control in (self.text,self.range_edit,self.color_edit): control.textChanged.connect(self._schedule)
        for control in (self.font_size,self.margin,self.opacity,self.angle,self.start): control.valueChanged.connect(self._schedule)
        for control in (self.position,self.counting,self.preview_page): control.currentIndexChanged.connect(self._schedule)
        self.scope.currentIndexChanged.connect(self._scope_changed)
        self.range_edit.textChanged.connect(self._refresh_pages)
        self._scope_changed(); self.text.setFocus()

    def _row(self, form, text, control):
        label = QLabel(text); label.setBuddy(control); control.setAccessibleName(text); form.addRow(label, control); return label

    def _choose_color(self):
        chosen = QColorDialog.getColor(QColor(self.color_edit.text()), self, "Farbe der Markierung", QColorDialog.ColorDialogOption.DontUseNativeDialog)
        if chosen.isValid(): self.color_edit.setText(chosen.name())

    def _targets(self):
        scope = self.scope.currentData()
        if scope == "all": return tuple(page.uid for page in self.pages)
        if scope == "selected": return self.selected_ids
        return tuple(self.pages[index].uid for index in parse_page_selection(self.range_edit.text(), len(self.pages)))

    def _scope_changed(self):
        ranged = self.scope.currentData() == "range"
        self.range_edit.setVisible(ranged); self.range_label.setVisible(ranged)
        self._refresh_pages(); self._schedule()

    def _refresh_pages(self):
        old = self.preview_page.currentData()
        self.preview_page.blockSignals(True); self.preview_page.clear()
        try:
            for index,page in target_pages(self.pages,self._targets()): self.preview_page.addItem(f"Seite {index+1} · {self.sources[page.source_id].label}", page.uid)
            selected = self.preview_page.findData(old)
            if selected >= 0: self.preview_page.setCurrentIndex(selected)
        except PdfWorkbenchError:
            pass
        self.preview_page.blockSignals(False)

    @property
    def request(self):
        value = self.color_edit.text()
        if len(value) != 7 or not value.startswith("#") or not QColor(value).isValid():
            raise PdfWorkbenchError("Bitte eine Farbe als #RRGGBB eingeben, zum Beispiel #1F524F.")
        qcolor = QColor(value); rgb = (qcolor.redF(),qcolor.greenF(),qcolor.blueF())
        if self.mode == "watermark":
            options = PageMarksOptions(watermark=WatermarkOptions(self.text.text(),self.font_size.value(),rgb,self.opacity.value()/100,self.angle.value(),self.position.currentData(),self.margin.value()))
        else:
            options = PageMarksOptions(numbering=PageNumberOptions(self.text.text(),self.start.value(),self.position.currentData(),self.font_size.value(),rgb,self.margin.value(),self.counting.currentData()))
        ids = self._targets(); target_pages(self.pages,ids)
        return PageMarksRequest(options,ids)

    def _schedule(self, *_):
        if self._closing: return
        self._generation += 1; self._ready_request = None; self.apply_button.setEnabled(False); self.error.clear()
        self.preview_caption.setText("Vorschau wird aktualisiert …")
        if self.worker: self.worker.cancel_event.set()
        self.timer.start()

    def _start_preview(self):
        if self._closing or self.worker: return
        try:
            request = self.request
            uid = self.preview_page.currentData()
            if uid is None: raise PdfWorkbenchError("Bitte eine vorhandene Vorschauseite wählen.")
        except Exception as error:
            self.error.setText(str(error)); self.preview_caption.setText("Einstellungen bitte prüfen."); return
        self.worker = MarkPreviewWorker(self.sources,self.pages,request,uid,self._generation,self)
        self.worker.result.connect(lambda generation,image:self._preview_ready(generation,image,request))
        self.worker.failed.connect(self._failed); self.worker.finished.connect(self._finished); self.worker.start()

    def _preview_ready(self,generation,image,request):
        if self._closing or generation != self._generation: return
        self.preview.image = image; self.preview.update(); self._ready_request = request
        count = len(request.target_ids)
        self.preview_caption.setText(f"Echte PDF-Vorschau · {count} Seite(n) werden markiert.")

    def _failed(self,generation,error):
        if self._closing or generation != self._generation: return
        self.error.setText(error); self.preview_caption.setText("Einstellungen bitte prüfen.")

    def _finished(self):
        worker, self.worker = self.worker, None; worker.deleteLater()
        if self._closing:
            super().reject(); return
        self.apply_button.setEnabled(self._ready_request is not None)
        if self._ready_request is None and not self.error.text() and not self.timer.isActive(): self.timer.start(0)

    def accept(self):
        if self._ready_request is not None and self.worker is None and not self.timer.isActive(): super().accept()

    def reject(self):
        self._closing = True; self.timer.stop()
        if self.worker:
            self.worker.cancel_event.set(); self.buttons.setEnabled(False); self.preview_caption.setText("Vorschau wird beendet …")
        else: super().reject()

    def closeEvent(self,event):
        if self.worker: self.reject(); event.ignore()
        else: super().closeEvent(event)

    @classmethod
    def get_request(cls,parent,workspace,selected_ids=(),*,mode="watermark"):
        dialog = cls(parent,workspace,selected_ids,mode=mode)
        return dialog._ready_request if dialog.exec() == QDialog.DialogCode.Accepted else None
