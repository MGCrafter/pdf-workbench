"""Native, accessible dialogs for redaction, text replacement and certificates."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading

from PyQt6.QtCore import QSettings, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPlainTextEdit,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ..advanced_pdf import RedactionRegion, existing_text_runs, mupdf, workspace_pdf_bytes
from ..core import OperationCancelled, PdfWorkbenchError
from ..pdfa import discover_ghostscript, discover_verapdf
from .page_geometry_dialog import GeometryPreview
from .pdf_dialogs import heading
from .theme import color, file_dialog_options, semantic_label


def _hint(text):
    widget = QLabel(text); widget.setWordWrap(True); widget.setObjectName("muted")
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def _buttons(layout, accept_label, accept, reject):
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Save).setText(accept_label)
    buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
    buttons.accepted.connect(accept); buttons.rejected.connect(reject); layout.addWidget(buttons)
    return buttons


class RedactionPreview(GeometryPreview):
    def __init__(self, parent=None):
        super().__init__(parent); self.regions = (); self.crop = (.1, .1, .6, .3)
        self.setAccessibleName("Schwärzungsrahmen der sichtbaren PDF-Seite")
        self.setAccessibleDescription("Rahmen an den Griffen ziehen. Pfeiltasten verschieben ihn. Die vier Prozentfelder ermöglichen dieselbe Bedienung per Tastatur. Bereich hinzufügen übernimmt den Rahmen.")

    def paintEvent(self, event):
        painter = QPainter(self); painter.fillRect(self.rect(), color("canvas"))
        paper = self.paper_rect()
        if paper.isEmpty(): return
        painter.fillRect(paper, QColor("white")); painter.drawImage(paper, self.image)
        from PyQt6.QtCore import QRectF
        def box(values):
            l,t,r,b = values
            return QRectF(paper.left()+l*paper.width(), paper.top()+t*paper.height(), (r-l)*paper.width(), (b-t)*paper.height())
        for region in self.regions: painter.fillRect(box(region), QColor("black"))
        selection = self.selection_rect()
        painter.fillRect(selection, QColor(180, 40, 40, 65)); painter.setPen(QPen(color("accent"), 2)); painter.drawRect(selection)
        for point in (selection.topLeft(), selection.topRight(), selection.bottomLeft(), selection.bottomRight()):
            painter.fillRect(QRectF(point.x()-4, point.y()-4, 8, 8), QColor("white"))
            painter.drawRect(QRectF(point.x()-4, point.y()-4, 8, 8))
        if self.hasFocus():
            painter.setPen(QPen(color("accent"), 1, Qt.PenStyle.DotLine)); painter.drawRect(self.rect().adjusted(3,3,-4,-4))


class RedactionPreviewWorker(QThread):
    completed = pyqtSignal(str, QImage)
    failed = pyqtSignal(str, str)

    def __init__(self, sources, page, parent):
        super().__init__(parent); self.sources, self.page = sources, page
        self.cancelled = threading.Event()

    def run(self):
        try:
            data = workspace_pdf_bytes(self.sources, (self.page,), self.cancelled.is_set, bookmarks=())
            fitz = mupdf()
            with fitz.open(stream=data, filetype="pdf") as document:
                page = document[0]; scale = min(1.5, 850/max(page.rect.width, page.rect.height))
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
                image = QImage.fromData(pix.tobytes("png"), "PNG")
            if not self.cancelled.is_set(): self.completed.emit(self.page.uid, image)
        except OperationCancelled: pass
        except Exception as error:
            if not self.cancelled.is_set(): self.failed.emit(self.page.uid, str(error))


@dataclass(frozen=True)
class RedactionRequest:
    regions: tuple[RedactionRegion, ...]
    dpi: int


class RedactionDialog(QDialog):
    def __init__(self, parent, workspace, active_uid=None):
        super().__init__(parent); self.setWindowTitle("Inhalte dauerhaft schwärzen"); self.resize(920, 690)
        self.sources, self.pages = dict(workspace.sources), tuple(workspace.pages)
        self.regions = []; self.worker = None; self._closing = False; self._ready_uid = None
        layout = QVBoxLayout(self); layout.setContentsMargins(20,18,20,16); layout.setSpacing(12)
        heading(layout, "Sensible Inhalte entfernen.", "Markierte Bereiche werden aus der neuen Datei entfernt. Alle sichtbaren Seiten werden als Bild-PDF neu aufgebaut, einschließlich ihrer sichtbaren Anmerkungen.")
        body = QHBoxLayout(); layout.addLayout(body, 1)
        controls = QWidget(); settings = QVBoxLayout(controls); settings.setContentsMargins(0,0,8,0)
        form = QFormLayout(); self.page_box = QComboBox(); self.page_box.setAccessibleName("Seite für den Schwärzungsbereich")
        for index, page in enumerate(self.pages): self.page_box.addItem(f"Seite {index+1}", page.uid)
        if active_uid:
            index = self.page_box.findData(active_uid)
            if index >= 0: self.page_box.setCurrentIndex(index)
        form.addRow("Seite", self.page_box)
        self.coordinates = []
        for name, value in zip(("Links", "Oben", "Rechts", "Unten"), (.1,.1,.6,.3)):
            spin = QDoubleSpinBox(); spin.setRange(0,100); spin.setDecimals(2); spin.setSingleStep(.5); spin.setSuffix(" %")
            spin.setValue(value*100); spin.setAccessibleName(f"Schwärzungsrahmen {name.lower()} in Prozent")
            self.coordinates.append(spin); form.addRow(name, spin); spin.valueChanged.connect(self._coordinates_changed)
        self.dpi = QSpinBox(); self.dpi.setRange(100,400); self.dpi.setValue(200); self.dpi.setSuffix(" dpi"); form.addRow("Bildauflösung", self.dpi)
        settings.addLayout(form)
        self.add_button = QPushButton("Bereich hinzufügen"); self.add_button.clicked.connect(self.add_region); settings.addWidget(self.add_button)
        settings.addWidget(_hint("Der rote Rahmen ist die Auswahl. Hinzugefügte Bereiche erscheinen schwarz und stehen in der Liste."))
        self.region_list = QListWidget(); self.region_list.setAccessibleName("Dauerhaft zu schwärzende Bereiche"); self.region_list.setMinimumHeight(110); settings.addWidget(self.region_list, 1)
        self.remove_button = QPushButton("Markierten Bereich entfernen"); self.remove_button.clicked.connect(self.remove_region); settings.addWidget(self.remove_button)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(controls); scroll.setMinimumWidth(300); body.addWidget(scroll)
        self.preview = RedactionPreview(); self.preview.setMinimumSize(250,240); body.addWidget(self.preview, 1)
        self.preview.cropChanged.connect(self._frame_changed)
        warning = _hint("Die neue PDF ist unverschlüsselt und enthält keine Textsuche, Formulare, Links, Lesezeichen, Zertifikatssignaturen, Kommentare oder Originalmetadaten. Sichtbare Inhalte außerhalb der Bereiche bleiben als Pixel erhalten. Originale, offene Projekte, Undo-Historie und automatische Sicherungen enthalten weiterhin die ungeschwärzten Daten und müssen getrennt geschützt werden.")
        semantic_label(warning, "warning"); layout.addWidget(warning)
        self.confirm = QCheckBox("Ich habe alle Bereiche geprüft und möchte eine dauerhaft geschwärzte Bild-PDF erstellen.")
        self.confirm.toggled.connect(self.validate); layout.addWidget(self.confirm)
        self.status = _hint(""); layout.addWidget(self.status)
        self.buttons = _buttons(layout, "Geschwärzte PDF speichern …", self.accept, self.reject)
        self.page_box.currentIndexChanged.connect(self.render_page)
        self.region_list.currentRowChanged.connect(self.validate)
        self.render_page(); self.validate()

    def _coordinates_changed(self):
        values = tuple(spin.value()/100 for spin in self.coordinates)
        if values[0] < values[2] and values[1] < values[3]:
            self.preview.crop = values; self.preview.update()
        self.validate()

    def _frame_changed(self, values):
        for spin, value in zip(self.coordinates, values):
            spin.blockSignals(True); spin.setValue(value*100); spin.blockSignals(False)
        self.validate()

    def render_page(self):
        self._ready_uid = None; self.preview.image = QImage(); self.preview.update()
        self.status.setText("Seitenvorschau wird geladen …"); self.validate()
        if self.worker:
            self.worker.cancelled.set(); return
        if self._closing: return
        uid = self.page_box.currentData(); page = next(p for p in self.pages if p.uid == uid)
        self.worker = RedactionPreviewWorker(self.sources, page, self)
        self.worker.completed.connect(self._rendered); self.worker.failed.connect(self._failed); self.worker.finished.connect(self._finished); self.worker.start()

    def _rendered(self, uid, image):
        if uid != self.page_box.currentData(): return
        self._ready_uid = uid; self.preview.image = image; self.status.clear(); self._update_regions(); self.validate()

    def _failed(self, uid, error):
        if uid == self.page_box.currentData(): self.status.setText(error); semantic_label(self.status, "error")

    def _finished(self):
        uid = self.worker.page.uid; cancelled = self.worker.cancelled.is_set()
        self.worker.deleteLater(); self.worker = None
        if self._closing: super().reject()
        elif cancelled or uid != self.page_box.currentData(): self.render_page()
        self.validate()

    def _update_regions(self):
        uid = self.page_box.currentData()
        self.preview.regions = tuple(r.rect for r in self.regions if r.page_uid == uid); self.preview.update()

    def add_region(self):
        values = tuple(spin.value()/100 for spin in self.coordinates)
        try: region = RedactionRegion(self.page_box.currentData(), values)
        except PdfWorkbenchError as error: self.status.setText(str(error)); return
        self.regions.append(region); page = self.page_box.currentIndex()+1
        self.region_list.addItem(f"Seite {page}: {values[0]*100:g}/{values[1]*100:g} bis {values[2]*100:g}/{values[3]*100:g} %")
        self.region_list.setCurrentRow(len(self.regions)-1); self.confirm.setChecked(False); self._update_regions(); self.validate()

    def remove_region(self):
        row = self.region_list.currentRow()
        if 0 <= row < len(self.regions):
            self.regions.pop(row); self.region_list.takeItem(row); self.confirm.setChecked(False); self._update_regions(); self.validate()

    def validate(self):
        if not hasattr(self, "buttons"): return
        values = tuple(spin.value()/100 for spin in self.coordinates)
        self.add_button.setEnabled(self._ready_uid == self.page_box.currentData() and values[0] < values[2] and values[1] < values[3])
        self.remove_button.setEnabled(self.region_list.currentRow() >= 0)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(bool(self.regions) and self.confirm.isChecked() and self.worker is None)

    def accept(self):
        if self.regions and self.confirm.isChecked() and not self.worker: super().accept()

    def reject(self):
        if self.worker:
            self._closing = True; self.worker.cancelled.set(); self.buttons.setEnabled(False)
        else: super().reject()

    def closeEvent(self, event):
        if self.worker: self.reject(); event.ignore()
        else: super().closeEvent(event)

    @classmethod
    def get_request(cls, parent, workspace, active_uid=None):
        dialog = cls(parent, workspace, active_uid)
        result = RedactionRequest(tuple(dialog.regions), dialog.dpi.value()) if dialog.exec() == QDialog.DialogCode.Accepted else None
        dialog.deleteLater(); return result


class ExistingTextDialog(QDialog):
    def __init__(self, parent, source, page_index):
        super().__init__(parent); self.setWindowTitle("Bestehenden PDF-Text bearbeiten"); self.resize(680,570)
        self.runs = existing_text_runs(source, page_index); self.request = None
        layout = QVBoxLayout(self); layout.setContentsMargins(24,20,24,20); layout.setSpacing(12)
        heading(layout, "Eine Textzeile ersetzen.", "Der ursprüngliche Text wird aus dem Seiteninhalt entfernt und durch den neuen Text ersetzt. Schrift, Größe und Farbe bleiben bei unterstützten Zeilen erhalten.")
        self.list = QListWidget(); self.list.setAccessibleName("Bestehende PDF-Textzeilen")
        for run in self.runs: self.list.addItem(("" if run.supported else "Nicht unterstützt · ") + run.text)
        layout.addWidget(self.list, 1)
        form = QFormLayout(); self.original = QPlainTextEdit(); self.original.setReadOnly(True); self.original.setMaximumHeight(80); self.original.setAccessibleName("Ursprünglicher PDF-Text")
        form.addRow("Bisher", self.original)
        self.replacement = QLineEdit(); self.replacement.setMaxLength(2000); self.replacement.setAccessibleName("Neuer Text für die PDF-Textzeile"); form.addRow("Neuer Text", self.replacement)
        layout.addLayout(form); self.details = _hint(""); layout.addWidget(self.details)
        layout.addWidget(_hint("Unterstützt werden isolierte waagerechte Zeilen in Helvetica, Times und Courier mit westlichen Zeichen. Neue Texte müssen in die bisherige Breite passen. Seiten mit Formularfeldern sowie eingebettete Schriften, interne Links, Überlappungen und besondere Textdarstellungen werden zurückgewiesen. Kein Absatzumbruch. Der alte Text bleibt in Originaldateien und der Projekt-/Undo-Historie erhalten; dieses Werkzeug ersetzt keine Schwärzung."))
        self.buttons = _buttons(layout, "Text ersetzen", self.accept, self.reject)
        self.list.currentRowChanged.connect(self.selected); self.replacement.textChanged.connect(self.validate)
        self.list.setCurrentRow(0); self.selected()

    def selected(self):
        row = self.list.currentRow()
        if not 0 <= row < len(self.runs):
            self.original.clear(); self.details.setText("Diese Seite enthält keine sichtbaren PDF-Textzeilen. Reine Scans können hier nicht als Text bearbeitet werden.")
        else:
            run = self.runs[row]; self.original.setPlainText(run.text); self.replacement.setText(run.text)
            self.details.setText(f"{run.font} · {run.font_size:g} pt" if run.supported else run.reason)
        self.validate()

    def validate(self):
        row = self.list.currentRow()
        valid = 0 <= row < len(self.runs) and self.runs[row].supported and bool(self.replacement.text().strip()) and self.replacement.text() != self.runs[row].text
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(valid)

    def accept(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.runs) and self.runs[row].supported and self.replacement.text().strip():
            self.request = self.runs[row].run_id, self.replacement.text(); super().accept()

    @classmethod
    def get_request(cls, parent, source, page_index):
        dialog = cls(parent, source, page_index)
        result = dialog.request if dialog.exec() == QDialog.DialogCode.Accepted else None
        dialog.deleteLater(); return result


class CertificateDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent); self.setWindowTitle("Mit Zertifikat signieren"); self.resize(630,420); self.request = None
        layout = QVBoxLayout(self); layout.setContentsMargins(24,20,24,20); layout.setSpacing(14)
        heading(layout, "Die neue PDF digital signieren.", "Ein eigenes PFX-/P12-Zertifikat mit privatem Schlüssel signiert die vollständige neue PDF kryptografisch. Die Signatur ist unsichtbar und wird anschließend lokal auf Integrität geprüft.")
        form = QFormLayout(); line = QHBoxLayout(); self.certificate = QLineEdit(); self.certificate.setAccessibleName("PKCS12-Zertifikatsdatei")
        choose = QPushButton("Wählen …"); choose.clicked.connect(self.choose_certificate); line.addWidget(self.certificate, 1); line.addWidget(choose); form.addRow("PFX / P12", line)
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.EchoMode.Password); self.password.setAccessibleName("Zertifikatspasswort"); form.addRow("Passwort", self.password)
        self.reason = QLineEdit(); self.reason.setMaxLength(1000); self.reason.setAccessibleName("Optionaler Signaturgrund"); form.addRow("Grund (optional)", self.reason); layout.addLayout(form)
        layout.addWidget(_hint("Die neue PDF ist unverschlüsselt. Zertifikat und Passwort bleiben lokal. Es gibt keine Online-Sperrprüfung und keinen vertrauenswürdigen Zeitstempel. Zertifikatsbesitz bestätigt keine vertrauenswürdige Identität oder qualifizierte Signatur. Änderungen oder ein erneuter Export der signierten Datei können die Signatur ungültig machen. Bestehende Signaturen werden beim Arbeitsbereichsexport nicht erhalten."))
        self.status = _hint(""); layout.addWidget(self.status); layout.addStretch()
        self.buttons = _buttons(layout, "Neue signierte PDF speichern …", self.accept, self.reject)
        self.certificate.textChanged.connect(self.validate); self.validate()

    def choose_certificate(self):
        path, _ = QFileDialog.getOpenFileName(self, "Eigenes Signaturzertifikat wählen", "", "PKCS12-Zertifikate (*.pfx *.p12)", options=file_dialog_options())
        if path: self.certificate.setText(path)

    def validate(self):
        valid = bool(self.certificate.text().strip()) and Path(self.certificate.text().strip()).expanduser().is_file()
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(valid)
        self.status.setText("" if valid else "Bitte eine vorhandene PFX- oder P12-Datei wählen.")

    def accept(self):
        if self.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled():
            self.request = (Path(self.certificate.text().strip()).expanduser(), self.password.text(), self.reason.text())
            self.password.clear(); super().accept()

    def reject(self):
        self.password.clear(); super().reject()

    @classmethod
    def get_request(cls, parent):
        dialog = cls(parent); result = dialog.request if dialog.exec() == QDialog.DialogCode.Accepted else None
        dialog.request = None; dialog.password.clear(); dialog.deleteLater(); return result


class SignatureReportDialog(QDialog):
    def __init__(self, parent, statuses, path):
        super().__init__(parent); self.setWindowTitle("Zertifikatssignaturen prüfen"); self.resize(730,600)
        layout = QVBoxLayout(self); heading(layout, "Lokale Signaturprüfung.", str(path))
        text = QPlainTextEdit(); text.setReadOnly(True); text.setAccessibleName("Ergebnisse der Offline-Zertifikatssignaturprüfung")
        report = []
        for index, status in enumerate(statuses):
            report.append(f"Signatur {index+1}: {status.field_name}\n{status.signer}\n"
                f"Kryptografische Integrität: {'bestanden' if status.integrity_ok else 'NICHT bestanden'}\n"
                f"Vertrauenskette: {'gegen gewählte Vertrauenszertifikate bestätigt' if status.trusted else 'nicht bestätigt'}\n"
                f"Abdeckung: {status.coverage}\nSpätere Dateirevision: {'ja' if status.modified_after_signing else 'nein'}\n\n{status.details}")
        text.setPlainText("\n\n────────────────────────────\n\n".join(report) if report else "Keine eingebetteten Zertifikatssignaturen gefunden.")
        layout.addWidget(text, 1); layout.addWidget(_hint("Offline-Prüfung ohne Online-Sperrlisten und ohne Nachweis einer qualifizierten Signatur. Eine intakte Signatur bestätigt die unveränderten signierten Bytes. Vertrauen muss über selbst gewählte Vertrauenszertifikate geprüft werden; eine unbestätigte Vertrauenskette bedeutet nicht automatisch eine beschädigte Datei."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); buttons.button(QDialogButtonBox.StandardButton.Close).setText("Schließen"); buttons.rejected.connect(self.reject); layout.addWidget(buttons)


class SignatureVerificationDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent); self.setWindowTitle("Zertifikatssignaturen prüfen"); self.resize(650,360)
        self.request = None; self.trust_roots = ()
        layout = QVBoxLayout(self); layout.setContentsMargins(24,20,24,20); layout.setSpacing(14)
        heading(layout, "Die signierte Originaldatei prüfen.", "Die Datei wird direkt gelesen. Eine Änderung oder ein Arbeitsbereichsexport würde vorhandene Signaturen verändern. Alle Prüfungen bleiben offline.")
        form = QFormLayout(); row = QHBoxLayout(); self.document = QLineEdit(); self.document.setAccessibleName("Original-PDF für die Signaturprüfung")
        choose = QPushButton("Wählen …"); choose.clicked.connect(self.choose_pdf); row.addWidget(self.document,1); row.addWidget(choose); form.addRow("Signierte PDF",row)
        rootrow = QHBoxLayout(); self.roots = _hint("Keine · Identität bleibt unbestätigt")
        rootbutton = QPushButton("Vertrauenszertifikate …"); rootbutton.clicked.connect(self.choose_roots)
        clear = QPushButton("Leeren"); clear.clicked.connect(self.clear_roots)
        rootrow.addWidget(rootbutton); rootrow.addWidget(clear); form.addRow("Vertrauen (optional)",rootrow); form.addRow("",self.roots); layout.addLayout(form)
        layout.addWidget(_hint("Nur selbst geprüfte Stammzertifikate als PEM/DER auswählen. Die eingebettete Signatur liefert allein keinen Vertrauensanker. Sperrstatus und qualifizierte Signaturen werden nicht bestätigt."))
        layout.addStretch(); self.buttons = _buttons(layout, "Datei offline prüfen", self.accept, self.reject)
        self.document.textChanged.connect(self.validate); self.validate()

    def choose_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "Signierte Original-PDF wählen", "", "PDF-Dateien (*.pdf)", options=file_dialog_options())
        if path: self.document.setText(path)

    def choose_roots(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Selbst geprüfte Vertrauenszertifikate wählen", "", "Zertifikate (*.pem *.crt *.cer *.der);;Alle Dateien (*)", options=file_dialog_options())
        if paths:
            self.trust_roots = tuple(Path(path) for path in paths)
            self.roots.setText(f"{len(paths)} Zertifikat(e): " + ", ".join(Path(path).name for path in paths))

    def clear_roots(self):
        self.trust_roots = (); self.roots.setText("Keine · Identität bleibt unbestätigt")

    def validate(self):
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(bool(self.document.text().strip()) and Path(self.document.text().strip()).expanduser().is_file())

    def accept(self):
        if self.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled():
            self.request = Path(self.document.text().strip()).expanduser(), self.trust_roots; super().accept()

    @classmethod
    def get_request(cls, parent):
        dialog = cls(parent); result = dialog.request if dialog.exec() == QDialog.DialogCode.Accepted else None
        dialog.deleteLater(); return result


class PdfaDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent); self.setWindowTitle("Validierte PDF/A erstellen"); self.resize(650,440); self.request = None
        self.settings = QSettings("PDF Workbench", "PDF Workbench")
        layout = QVBoxLayout(self); layout.setContentsMargins(24,20,24,20); layout.setSpacing(14)
        heading(layout, "Ein geprüftes Archivexemplar.", "Die aktuelle Seitenanordnung wird mit lokalem Ghostscript nach PDF/A-2b konvertiert und anschließend mit der echten veraPDF-CLI geprüft. Nur ein bestandenes Ergebnis wird gespeichert.")
        form = QFormLayout(); self.paths = []
        for label, key in (("Ghostscript", "ghostscript"), ("veraPDF-CLI", "verapdf")):
            row = QHBoxLayout(); field = QLineEdit(str(self.settings.value(f"pdfa/{key}", ""))); field.setPlaceholderText("Automatisch im Suchpfad finden")
            field.setAccessibleName(f"Lokales {label}-Programm"); self.paths.append(field); row.addWidget(field, 1)
            button = QPushButton("Wählen …"); button.clicked.connect(lambda checked=False, f=field, l=label: self.choose_program(f,l)); row.addWidget(button); form.addRow(label, row)
        layout.addLayout(form)
        layout.addWidget(_hint("Die neue PDF ist unverschlüsselt. Ghostscript und veraPDF werden nicht heruntergeladen. veraPDF benötigt Java. Unter Windows kann die installierte verapdf.bat gewählt werden; die zugehörige Java-CLI wird direkt gestartet. Die Konvertierung kann Schriftarten, Farben, interaktive Formulare, Kommentare, Links und Lesezeichen ändern oder reduzieren; bestehende Zertifikatssignaturen bleiben nicht erhalten. PDF/A-2b bescheinigt keine Barrierefreiheit. Originale und Projekte bleiben erhalten."))
        self.status = _hint(""); semantic_label(self.status, "error"); layout.addWidget(self.status); layout.addStretch()
        self.buttons = _buttons(layout, "PDF/A-2b erstellen und prüfen …", self.accept, self.reject)

    def choose_program(self, field, label):
        path, _ = QFileDialog.getOpenFileName(self, f"Lokales {label}-Programm wählen", "", "Alle Dateien (*)", options=file_dialog_options())
        if path: field.setText(path)

    def accept(self):
        try:
            gs = discover_ghostscript(self.paths[0].text().strip() or None)
            vera = discover_verapdf(self.paths[1].text().strip() or None)
        except PdfWorkbenchError as error:
            self.status.setText(str(error)); return
        self.request = gs, vera
        self.settings.setValue("pdfa/ghostscript", self.paths[0].text().strip()); self.settings.setValue("pdfa/verapdf", self.paths[1].text().strip())
        super().accept()

    @classmethod
    def get_request(cls, parent):
        dialog = cls(parent); result = dialog.request if dialog.exec() == QDialog.DialogCode.Accepted else None
        dialog.deleteLater(); return result
