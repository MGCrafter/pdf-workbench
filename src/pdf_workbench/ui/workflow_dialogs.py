"""Native German dialogs for scan, batch and final-export workflows."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QSettings, QSize, Qt
from PyQt6.QtGui import QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from ..batch import BatchPreset, load_preset, save_preset
from ..core import PdfWorkbenchError
from ..features import parse_page_selection
from ..page_marks import PageNumberOptions, WatermarkOptions
from ..workflows import InternalLink, ScanOptions, duplex_order, pdf_engine, workspace_pdf
from .page_geometry_dialog import GeometryPreview
from .pdf_dialogs import heading
from .theme import file_dialog_options, semantic_label


def spin(low, high, value, suffix=""):
    field = QSpinBox(); field.setRange(low, high); field.setValue(value); field.setSuffix(suffix)
    return field


def row(form, text, field):
    label = QLabel(text); label.setBuddy(field); field.setAccessibleName(text); form.addRow(label, field)


def hint(layout, text):
    label = QLabel(text); label.setWordWrap(True); label.setObjectName("muted"); layout.addWidget(label)
    return label


class WorkflowDialog(QDialog):
    def __init__(self, parent, title, detail):
        super().__init__(parent); self.setWindowTitle(title); self.resize(590, 480)
        self.body = QVBoxLayout(self); self.body.setContentsMargins(22, 20, 22, 18); self.body.setSpacing(12)
        heading(self.body, title, detail)

    def finish(self, action):
        self.error = QLabel(); self.error.setWordWrap(True); semantic_label(self.error, "error"); self.body.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok); self.ok.setText(action); self.ok.setObjectName("primary")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject); self.body.addWidget(self.buttons)

    def accept(self):
        try: self.validate()
        except (PdfWorkbenchError, ValueError, OSError) as error:
            self.error.setText(str(error)); return
        super().accept()

    def validate(self): pass


class ScopeDialog(WorkflowDialog):
    def __init__(self, parent, title, count, selected, *, image=False):
        super().__init__(parent, title, "Die sichtbare Arbeitsreihenfolge einschließlich Drehungen und eigenen Objekten wird verwendet. Originale bleiben erhalten.")
        form = QFormLayout(); self.body.addLayout(form)
        self.scope = QComboBox(); self.scope.addItem(f"Alle Arbeitsseiten ({count})", "all")
        if selected: self.scope.addItem(f"Ausgewählte Seiten ({selected})", "selected"); self.scope.setCurrentIndex(1)
        row(form, "Seiten", self.scope)
        if image:
            self.format = QComboBox(); self.format.addItem("PNG · verlustfrei", "png"); self.format.addItem("JPEG · kleinere Bilder", "jpeg")
            self.dpi = spin(72, 600, 150, " dpi"); self.quality = spin(1, 100, 90, " %"); self.prefix = QLineEdit("Seite")
            row(form, "Bildformat", self.format); row(form, "Auflösung", self.dpi); row(form, "JPEG-Qualität", self.quality); row(form, "Dateipräfix", self.prefix)
            self.format.currentIndexChanged.connect(lambda: self.quality.setEnabled(self.format.currentData() == "jpeg")); self.quality.setEnabled(False)
            hint(self.body, "Jede Seite wird als eigenes Bild gespeichert. Vorhandene Dateinamen werden zurückgewiesen. Bei großen Seiten bitte weniger dpi wählen.")
        else:
            hint(self.body, "Exportiert vorhandenen Text als UTF-8. Scans brauchen zuerst OCR. Seiten werden durch Seitenumbrüche getrennt; Textlesereihenfolge und Tabellen können von der PDF-Ansicht abweichen.")
        self.body.addStretch(); self.finish("Ziel auswählen …")


class ScanDialog(WorkflowDialog):
    def __init__(self, parent, count, selected):
        super().__init__(parent, "Scans aufbereiten", "Ausgewählte Seiten werden als neue Bildseiten übernommen. Die Änderung ist als ein Schritt rückgängig machbar.")
        form = QFormLayout(); self.body.addLayout(form)
        self.scope = QComboBox(); self.scope.addItem(f"Alle Arbeitsseiten ({count})", "all")
        if selected: self.scope.addItem(f"Ausgewählte Seiten ({selected})", "selected"); self.scope.setCurrentIndex(1)
        self.dpi = spin(72, 600, 200, " dpi"); self.rotation = QComboBox()
        for value in (0, 90, 180, 270): self.rotation.addItem(f"{value}°", value)
        self.deskew = QCheckBox("Schiefe Textzeilen begradigen (bis 5°)"); self.deskew.setChecked(True)
        self.cleanup = QCheckBox("Graustufen und Hintergrund aufhellen")
        self.orientation = QCheckBox("Textausrichtung lokal mit Tesseract erkennen")
        row(form, "Seiten", self.scope); row(form, "Auflösung", self.dpi); row(form, "Zusätzlich drehen", self.rotation)
        for field in (self.deskew, self.cleanup, self.orientation): form.addRow(field)
        hint(self.body, "Beim Rastern gehen auswählbarer Text, Formularfelder, Links und editierbare Objekte auf diesen Seiten in das Bild über. OCR kann danach neu ausgeführt werden. Hintergrundbereinigung verändert Farben und kann schwache Details entfernen; Ergebnis kontrollieren.")
        hint(self.body, "Automatische Ausrichtung benötigt eine vorhandene lokale Tesseract-Installation mit osd.traineddata. Unsichere Erkennung wird übersprungen und gemeldet. Es werden keine Modelle heruntergeladen.")
        self.body.addStretch(); self.finish("Bildseiten übernehmen")

    @property
    def options(self): return ScanOptions(self.dpi.value(), self.deskew.isChecked(), self.cleanup.isChecked(), self.orientation.isChecked(), self.rotation.currentData())


class CandidateReviewDialog(WorkflowDialog):
    def __init__(self, parent, candidates):
        super().__init__(parent, "Leere und doppelte Seiten prüfen", "Nur manuell markierte Kandidaten werden entfernt. Die Löschung ist rückgängig machbar.")
        self.resize(790, 590); self.candidates = tuple(candidates)
        hint(self.body, "Leerseiten sind eine Schätzung. Doppelte Seiten sehen bei 90 dpi gleich aus; feine oder unsichtbare Inhalte können abweichen. Vorschauen und Originalansicht vor der Auswahl prüfen.")
        self.list = QListWidget(); self.list.setIconSize(QSize(90, 118)); self.list.setAccessibleName("Seitenkandidaten zur manuellen Kontrolle")
        for candidate in candidates:
            item = QListWidgetItem(f"Seite {candidate.page_number} · {candidate.reason}\nDunkle Bildpunkte: {candidate.ink_fraction * 100:.3f} %")
            item.setData(Qt.ItemDataRole.UserRole, candidate.page_uid); item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked); item.setIcon(QIcon(QPixmap.fromImage(QImage.fromData(candidate.preview_png))))
            self.list.addItem(item)
        self.body.addWidget(self.list, 1); self.finish("Markierte Seiten entfernen")
        self.list.itemChanged.connect(self._selection_changed); self._selection_changed()

    @property
    def selected_ids(self):
        return tuple(self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                     if self.list.item(i).checkState() == Qt.CheckState.Checked)

    def _selection_changed(self, *_):
        count = len(self.selected_ids); self.ok.setEnabled(count > 0); self.ok.setText(f"{count} markierte Seiten entfernen")


class DuplexDialog(WorkflowDialog):
    def __init__(self, parent, pages):
        super().__init__(parent, "Vorder- und Rückseiten verschachteln", "Zwei Scanfolgen in eine gemeinsame Lesereihenfolge bringen. Alle Arbeitsseiten müssen genau einmal enthalten sein.")
        self.pages = tuple(pages); half = (len(pages) + 1) // 2
        form = QFormLayout(); self.body.addLayout(form)
        self.fronts = QLineEdit("1" if half == 1 else f"1-{half}")
        self.backs = QLineEdit("" if half == len(pages) else str(half + 1) if half + 1 == len(pages) else f"{half + 1}-{len(pages)}")
        self.reverse = QCheckBox("Rückseiten wurden in umgekehrter Reihenfolge gescannt")
        self.rotation = QComboBox()
        for value in (0, 90, 180, 270): self.rotation.addItem(f"{value}°", value)
        row(form, "Vorderseiten", self.fronts); row(form, "Rückseiten", self.backs); form.addRow(self.reverse); row(form, "Rückseiten zusätzlich drehen", self.rotation)
        self.preview = QLabel(); self.preview.setWordWrap(True); self.body.addWidget(self.preview)
        hint(self.body, "Seitenbereiche beziehen sich auf die aktuelle Arbeitsreihenfolge, z. B. 1-5, 8. Eine letzte Vorderseite ohne Rückseite ist erlaubt. Die gesamte Änderung bleibt ein Undo-Schritt.")
        self.body.addStretch(); self.finish("Reihenfolge übernehmen")
        for field in (self.fronts, self.backs): field.textChanged.connect(self._preview)
        self.reverse.toggled.connect(self._preview); self.rotation.currentIndexChanged.connect(self._preview); self._preview()

    def _ids(self, field):
        if not field.text().strip(): return ()
        return tuple(self.pages[index].uid for index in parse_page_selection(field.text(), len(self.pages)))

    @property
    def ordered_pages(self):
        return duplex_order(self.pages, self._ids(self.fronts), self._ids(self.backs), reverse_backs=self.reverse.isChecked(), backs_rotation=self.rotation.currentData())

    def validate(self): self.ordered_pages

    def _preview(self):
        try:
            lookup = {page.uid: index + 1 for index, page in enumerate(self.pages)}
            order = self.ordered_pages; self.preview.setText("Neue Reihenfolge: " + " → ".join(str(lookup[page.uid]) for page in order[:80]) + (" …" if len(order) > 80 else ""))
            self.error.clear(); self.ok.setEnabled(True)
        except PdfWorkbenchError as error: self.error.setText(str(error)); self.ok.setEnabled(False)


class ContentsDialog(WorkflowDialog):
    def __init__(self, parent):
        super().__init__(parent, "Klickbares Inhaltsverzeichnis exportieren", "Aus den aktuellen Lesezeichen entstehen neue Inhaltsseiten vor der aktuellen Arbeitsreihenfolge.")
        form = QFormLayout(); self.title = QLineEdit("Inhaltsverzeichnis"); row(form, "Titel", self.title); self.body.addLayout(form)
        hint(self.body, "Kapitel und Unterkapitel bleiben gegliedert. Einträge mit Seitenziel sind anklickbar und zeigen die Seitenzahl der neuen PDF. Das Inhaltsverzeichnis wird nur im neuen Export erzeugt.")
        self.body.addStretch(); self.finish("Neue PDF speichern …")


class InternalLinksDialog(WorkflowDialog):
    def __init__(self, parent, workspace):
        super().__init__(parent, "Interne Links exportieren", "Linkbereiche anlegen, vor dem Export bearbeiten oder entfernen. Die Ziele beziehen sich auf die aktuelle Arbeitsreihenfolge.")
        self.resize(890, 670); self.setMinimumSize(740, 550)
        self.sources = dict(workspace.sources); self.pages = tuple(workspace.pages); self.links = []; self._syncing = False
        columns = QHBoxLayout(); self.body.addLayout(columns, 1)
        editor_panel = QWidget(); editor = QVBoxLayout(editor_panel); editor.setContentsMargins(0, 0, 6, 0); editor.setSpacing(10)
        self.editor_scroll = QScrollArea(); self.editor_scroll.setWidgetResizable(True); self.editor_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.editor_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); self.editor_scroll.setMinimumWidth(315)
        self.editor_scroll.setAccessibleName("Linkeinstellungen, bei kleinen Fenstern vertikal scrollbar")
        self.editor_scroll.setWidget(editor_panel); columns.addWidget(self.editor_scroll, 1)
        self.list = QListWidget(); self.list.setMinimumHeight(90); self.list.setMaximumHeight(150)
        self.list.setAccessibleName("Interne Links vor dem PDF-Export"); editor.addWidget(self.list)
        form = QFormLayout(); self.source = QComboBox(); self.target = QComboBox()
        for index, page in enumerate(self.pages):
            for field in (self.source, self.target): field.addItem(f"Seite {index + 1}", page.uid)
        for field in (self.source, self.target): field.setMinimumHeight(40)
        if len(self.pages) > 1: self.target.setCurrentIndex(1)
        row(form, "Link auf Seite", self.source); row(form, "Zielseite", self.target)
        self.coords = []
        for name, value in (("Links", 10), ("Oben", 10), ("Rechts", 90), ("Unten", 20)):
            field = QDoubleSpinBox(); field.setMinimumHeight(40); field.setDecimals(1); field.setRange(0, 100); field.setValue(value); field.setSuffix(" %")
            row(form, name, field); self.coords.append(field); field.valueChanged.connect(self._coordinates_changed)
        editor.addLayout(form)
        actions = QHBoxLayout(); self.new = QPushButton("Neuer Link"); self.apply = QPushButton("Link hinzufügen"); self.remove = QPushButton("Entfernen")
        for button in (self.new, self.apply, self.remove): button.setMinimumHeight(36); actions.addWidget(button)
        self.remove.setEnabled(False)
        editor.addLayout(actions); editor.addStretch()
        right = QVBoxLayout(); self.preview = GeometryPreview(); self.preview.setAccessibleName("Linkbereich auf der sichtbaren PDF-Seite")
        self.preview.crop = tuple(field.value() / 100 for field in self.coords); right.addWidget(self.preview, 1)
        hint(right, "Bereich mit der Maus ziehen oder die vier prozentualen Grenzen eingeben. Der Link ist unsichtbar und wird als anklickbarer Bereich gespeichert.")
        columns.addLayout(right, 1)
        hint(self.body, "Diese Linkliste gehört zu diesem Exportdialog. Beim erneuten Import oder späteren Umordnen wird kein vollständiger Erhalt bestehender PDF-Links versprochen. Prüfen Sie Links im externen PDF-Reader.")
        self.finish("PDF mit Links speichern …"); self.ok.setEnabled(False)
        self.new.clicked.connect(self._new); self.apply.clicked.connect(self._apply); self.remove.clicked.connect(self._remove)
        self.list.currentRowChanged.connect(self._selected); self.source.currentIndexChanged.connect(self._load_preview)
        self.preview.cropChanged.connect(self._rectangle_changed); self._load_preview()

    def _load_preview(self):
        try:
            ref = self.pages[self.source.currentIndex()]; fitz = pdf_engine()
            with fitz.open(stream=workspace_pdf(self.sources, (ref,)), filetype="pdf") as document:
                page = document[0]; scale = min(720 / max(page.rect.width, page.rect.height), 2)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, annots=True)
                self.preview.image = QImage.fromData(pixmap.tobytes("png")); self.preview.update()
            self.error.clear()
        except Exception as error: self.error.setText(str(error)); self.preview.image = QImage(); self.preview.update()

    def _coordinates_changed(self):
        if self._syncing: return
        self.preview.crop = tuple(field.value() / 100 for field in self.coords); self.preview.update()

    def _rectangle_changed(self, rectangle):
        self._syncing = True
        for field, value in zip(self.coords, rectangle): field.setValue(value * 100)
        self._syncing = False

    def _new(self):
        self.list.setCurrentRow(-1); self.apply.setText("Link hinzufügen")

    def _apply(self):
        try:
            link = InternalLink(self.source.currentData(), self.target.currentData(), tuple(field.value() / 100 for field in self.coords))
            index = self.list.currentRow()
            if index < 0:
                if len(self.links) >= 5000: raise PdfWorkbenchError("Höchstens 5.000 Links sind möglich.")
                self.links.append(link)
            else: self.links[index] = link
            self._refresh(index); self.error.clear()
        except PdfWorkbenchError as error: self.error.setText(str(error))

    def _selected(self, index):
        self.remove.setEnabled(index >= 0); self.apply.setText("Änderung übernehmen" if index >= 0 else "Link hinzufügen")
        if index < 0: return
        link = self.links[index]; self.source.setCurrentIndex(self.source.findData(link.page_uid)); self.target.setCurrentIndex(self.target.findData(link.target_uid))
        self._rectangle_changed(link.rect); self._coordinates_changed()

    def _remove(self):
        index = self.list.currentRow()
        if index >= 0: self.links.pop(index); self._refresh(-1)

    def _refresh(self, selected):
        self.list.blockSignals(True); self.list.clear()
        lookup = {page.uid: index + 1 for index, page in enumerate(self.pages)}
        for link in self.links: self.list.addItem(f"Seite {lookup[link.page_uid]} → Seite {lookup[link.target_uid]}")
        self.list.setCurrentRow(selected); self.list.blockSignals(False); self.ok.setEnabled(bool(self.links)); self._selected(selected)

    def validate(self):
        if not self.links: raise PdfWorkbenchError("Bitte zuerst einen Link hinzufügen.")


class BatchDialog(WorkflowDialog):
    def __init__(self, parent, *, settings=None):
        super().__init__(parent, "Stapelverarbeitung", "Mehrere lokale PDFs mit derselben wiederverwendbaren Vorlage als neue Dateien speichern.")
        self.resize(730, 720); self.settings = settings if settings is not None else QSettings("PDF Workbench", "PDF Workbench")
        self.tabs = QTabWidget(); self.body.addWidget(self.tabs, 1)
        files_page = QWidget(); files_layout = QVBoxLayout(files_page); self.tabs.addTab(files_page, "Dateien und Ziel")
        self.files = QListWidget(); self.files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection); self.files.setAccessibleName("PDF-Dateien für die Stapelverarbeitung"); files_layout.addWidget(self.files, 1)
        buttons = QHBoxLayout(); add = QPushButton("PDFs hinzufügen …"); remove = QPushButton("Aus Liste entfernen"); buttons.addWidget(add); buttons.addWidget(remove); files_layout.addLayout(buttons)
        add.clicked.connect(self._add_files); remove.clicked.connect(self._remove_files)
        directory_row = QHBoxLayout(); self.directory = QLineEdit(); self.directory.setAccessibleName("Stapel-Zielordner"); browse = QPushButton("Zielordner …"); browse.clicked.connect(self._browse)
        directory_row.addWidget(self.directory, 1); directory_row.addWidget(browse); files_layout.addLayout(directory_row)
        hint(files_layout, "Ausgaben heißen <Originalname>-bearbeitet.pdf. Gleichnamige Eingaben und bereits vorhandene Ausgaben werden zurückgewiesen. Passwortgeschützte Eingaben bitte einzeln öffnen und als neue PDF speichern.")
        options_page = QWidget(); options_layout = QVBoxLayout(options_page); scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.Shape.NoFrame); scroll.setWidget(options_page); self.tabs.addTab(scroll, "Vorlage und Arbeitsschritte")
        preset_row = QHBoxLayout(); self.saved = QComboBox(); self.saved.setAccessibleName("Gespeicherte Stapelvorlagen"); self.saved.addItem("Gespeicherte Vorlage auswählen", None)
        self._presets = {}
        for name, value in self._stored_presets().items():
            try: self._presets[name] = BatchPreset.from_json(value); self.saved.addItem(name, name)
            except PdfWorkbenchError: pass
        self.save = QPushButton("Vorlage merken"); preset_row.addWidget(self.saved, 1); preset_row.addWidget(self.save); options_layout.addLayout(preset_row)
        form = QFormLayout(); self.name = QLineEdit("Meine Vorlage"); row(form, "Vorlagenname", self.name)
        self.compression = QComboBox()
        for text, value in (("Keine", None), ("Verlustfrei", "lossless"), ("Ausgewogen", "balanced"), ("Klein", "small")): self.compression.addItem(text, value)
        self.compression.setCurrentIndex(1); row(form, "Komprimierung", self.compression)
        self.rotation = QComboBox()
        for value in (0, 90, 180, 270): self.rotation.addItem(f"{value}°", value)
        row(form, "Alle Seiten drehen", self.rotation)
        self.watermark = QCheckBox("Wasserzeichen hinzufügen"); self.watermark_text = QLineEdit("ENTWURF")
        form.addRow(self.watermark); row(form, "Wasserzeichentext", self.watermark_text)
        self.numbering = QCheckBox("Seitennummern hinzufügen"); self.numbering_text = QLineEdit("Seite {page} von {pages}"); self.start = spin(0, 1000000000, 1)
        form.addRow(self.numbering); row(form, "Nummerierungsvorlage", self.numbering_text); row(form, "Startnummer je PDF", self.start)
        self.scan = QCheckBox("Als Scan-Bildseiten aufbereiten"); self.dpi = spin(72, 600, 200, " dpi"); self.deskew = QCheckBox("Begradigen"); self.deskew.setChecked(True)
        self.cleanup = QCheckBox("Graustufen / Hintergrund aufhellen"); self.orientation = QCheckBox("Automatische Textausrichtung (lokales Tesseract/OSD)")
        form.addRow(self.scan); row(form, "Scanauflösung", self.dpi)
        for field in (self.deskew, self.cleanup, self.orientation): form.addRow(field)
        self.ocr_de = QCheckBox("OCR Deutsch"); self.ocr_en = QCheckBox("OCR Englisch")
        form.addRow(self.ocr_de); form.addRow(self.ocr_en); options_layout.addLayout(form)
        hint(options_layout, "Scanaufbereitung rastert Text, Felder, Links und Objekte; die Original-PDF bleibt erhalten. OCR benötigt vorhandenes Tesseract mit den gewählten Sprachdaten und pdf.ttf; es gibt hier keinen automatischen Download. OCR läuft vor neuen Wasserzeichen und Nummern. Bildkomprimierung kann Details reduzieren.")
        preset_files = QHBoxLayout(); load = QPushButton("Vorlage importieren …"); export = QPushButton("Vorlage exportieren …"); preset_files.addWidget(load); preset_files.addWidget(export); options_layout.addLayout(preset_files)
        load.clicked.connect(self._import); export.clicked.connect(self._export); self.save.clicked.connect(self._save); self.saved.currentIndexChanged.connect(self._load_saved)
        for check, fields in ((self.watermark, (self.watermark_text,)), (self.numbering, (self.numbering_text, self.start)), (self.scan, (self.dpi, self.deskew, self.cleanup, self.orientation))):
            check.toggled.connect(lambda enabled, fields=fields: [field.setEnabled(enabled) for field in fields])
            for field in fields: field.setEnabled(check.isChecked())
        self.finish("Stapel als neue PDFs verarbeiten")

    def _stored_presets(self):
        import json
        try:
            value = json.loads(str(self.settings.value("batch/presets-v1", "{}")))
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError): return {}

    @property
    def paths(self): return tuple(self.files.item(i).text() for i in range(self.files.count()))

    @property
    def preset(self):
        loaded = getattr(self, "_loaded_preset", None)
        watermark = (replace(loaded.watermark, text=self.watermark_text.text())
                     if loaded and loaded.watermark else WatermarkOptions(self.watermark_text.text())) if self.watermark.isChecked() else None
        numbering = (replace(loaded.numbering, template=self.numbering_text.text(), start_number=self.start.value())
                     if loaded and loaded.numbering else PageNumberOptions(self.numbering_text.text(), self.start.value())) if self.numbering.isChecked() else None
        scan = ScanOptions(self.dpi.value(), self.deskew.isChecked(), self.cleanup.isChecked(), self.orientation.isChecked(),
                           loaded.scan.rotation if loaded and loaded.scan else 0) if self.scan.isChecked() else None
        return BatchPreset(self.name.text().strip(), self.compression.currentData(), self.rotation.currentData(),
                           watermark, numbering, scan,
                           tuple(code for check, code in ((self.ocr_de, "deu"), (self.ocr_en, "eng")) if check.isChecked()))

    def _set_preset(self, preset):
        self.name.setText(preset.name); self.compression.setCurrentIndex(self.compression.findData(preset.compression)); self.rotation.setCurrentIndex(self.rotation.findData(preset.rotation))
        self.watermark.setChecked(preset.watermark is not None); self.numbering.setChecked(preset.numbering is not None); self.scan.setChecked(preset.scan is not None)
        if preset.watermark: self.watermark_text.setText(preset.watermark.text)
        if preset.numbering: self.numbering_text.setText(preset.numbering.template); self.start.setValue(preset.numbering.start_number)
        if preset.scan:
            self.dpi.setValue(preset.scan.dpi); self.deskew.setChecked(preset.scan.deskew); self.cleanup.setChecked(preset.scan.clean_background); self.orientation.setChecked(preset.scan.auto_orientation)
        self.ocr_de.setChecked("deu" in preset.ocr_languages); self.ocr_en.setChecked("eng" in preset.ocr_languages)
        # Preserve advanced imported mark options even when their simple fields
        # are edited: preset creation below replaces only exposed properties.
        self._loaded_preset = preset

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "PDFs zum Stapel hinzufügen", "", "PDF-Dateien (*.pdf)", options=file_dialog_options())
        existing = set(self.paths)
        for path in files:
            if path not in existing: self.files.addItem(path); existing.add(path)

    def _remove_files(self):
        for item in self.files.selectedItems(): self.files.takeItem(self.files.row(item))

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Stapel-Zielordner", self.directory.text(), options=file_dialog_options())
        if path: self.directory.setText(path)

    def _save(self):
        import json
        try:
            preset = self.preset; self._presets[preset.name] = preset
            self.settings.setValue("batch/presets-v1", json.dumps({name: item.to_json() for name, item in self._presets.items()}, ensure_ascii=False))
            if self.saved.findData(preset.name) < 0: self.saved.addItem(preset.name, preset.name)
            self.saved.setCurrentIndex(self.saved.findData(preset.name)); self.error.clear()
        except (PdfWorkbenchError, ValueError) as error: self.error.setText(str(error))

    def _load_saved(self):
        name = self.saved.currentData()
        if name in self._presets: self._set_preset(self._presets[name])

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Stapelvorlage importieren", "", "Stapelvorlagen (*.json)", options=file_dialog_options())
        if path:
            try: self._set_preset(load_preset(path)); self.error.clear()
            except (PdfWorkbenchError, OSError) as error: self.error.setText(str(error))

    def _export(self):
        try: preset = self.preset
        except (PdfWorkbenchError, ValueError) as error: self.error.setText(str(error)); return
        path, _ = QFileDialog.getSaveFileName(self, "Stapelvorlage exportieren", "Stapelvorlage.json", "Stapelvorlagen (*.json)", options=file_dialog_options())
        if path:
            try: save_preset(preset, path if path.lower().endswith(".json") else path + ".json"); self.error.clear()
            except (PdfWorkbenchError, OSError) as error: self.error.setText(str(error))

    def validate(self):
        self.preset
        if not self.paths: raise PdfWorkbenchError("Bitte mindestens eine PDF hinzufügen.")
        if not self.directory.text().strip() or not Path(self.directory.text()).is_dir(): raise PdfWorkbenchError("Bitte einen vorhandenen Zielordner auswählen.")
