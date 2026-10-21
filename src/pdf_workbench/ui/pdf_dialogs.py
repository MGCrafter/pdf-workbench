"""Measured compression, password protection and ordinary AcroForm editing."""
from __future__ import annotations

import threading

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QProgressBar, QPushButton,
    QScrollArea, QVBoxLayout, QWidget, QPlainTextEdit,
)

from .theme import semantic_label
from ..compression import CompressionOptions
from ..core import OperationCancelled, preview_compression
from ..forms import form_fields


def size_text(size):
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.2f} MB".replace(".", ",")
    return f"{size / 1024:.1f} KB".replace(".", ",")


def heading(layout, title, detail):
    label = QLabel(title); label.setObjectName("emptyTitle"); layout.addWidget(label)
    hint = QLabel(detail); hint.setWordWrap(True); hint.setObjectName("muted"); layout.addWidget(hint)


class CompressionWorker(QThread):
    result = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, sources, pages, options, parent):
        super().__init__(parent)
        self.sources, self.pages, self.options = sources, pages, options
        self.cancel_event = threading.Event()

    def run(self):
        try:
            self.result.emit(preview_compression(self.sources, self.pages, self.options, self.cancel_event.is_set))
        except OperationCancelled:
            pass
        except Exception as error:
            self.failed.emit(str(error))


class CompressionDialog(QDialog):
    def __init__(self, parent, sources, pages):
        super().__init__(parent)
        self.setWindowTitle("PDF komprimieren"); self.resize(540, 460)
        self.sources, self.pages, self.worker, self.report = sources, tuple(pages), None, None
        self._reject_pending = False
        layout = QVBoxLayout(self); layout.setContentsMargins(24,24,24,20); layout.setSpacing(16)
        heading(layout, "Kleiner speichern.", "Verglichen wird dieselbe aktuelle Seitenanordnung – einmal normal und einmal komprimiert. Ihre Originaldateien bleiben erhalten.")
        form = QFormLayout(); form.setSpacing(12)
        self.quality = QComboBox()
        for title, value in (("Verlustfrei · Text und Bilder erhalten", "lossless"),
                             ("Ausgewogen · Bilder bis 2.200 Pixel", "balanced"),
                             ("Klein · Bilder bis 1.400 Pixel", "small")):
            self.quality.addItem(title, value)
        self.quality.setAccessibleName("Qualität der PDF-Komprimierung")
        self.quality.currentIndexChanged.connect(self._quality_changed)
        form.addRow("Qualität", self.quality); layout.addLayout(form)
        self.warning = QLabel(); self.warning.setWordWrap(True); self.warning.setObjectName("muted"); layout.addWidget(self.warning)
        self.comparison = QLabel("Dateigrößen werden berechnet …")
        self.comparison.setObjectName("sectionTitle"); self.comparison.setWordWrap(True); layout.addWidget(self.comparison)
        self.details = QLabel(); self.details.setObjectName("muted"); self.details.setWordWrap(True); layout.addWidget(self.details)
        self.progress = QProgressBar(); self.progress.setRange(0,0); self.progress.setFixedHeight(5); self.progress.setTextVisible(False); layout.addWidget(self.progress)
        layout.addStretch()
        row = QHBoxLayout()
        self.preview_button = QPushButton("Größe vergleichen"); self.preview_button.clicked.connect(self.calculate); row.addWidget(self.preview_button); row.addStretch()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save); self.save_button.setText("Als neue PDF …"); self.save_button.setObjectName("primary"); self.save_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject); row.addWidget(self.buttons); layout.addLayout(row)
        self._quality_changed(); QTimer.singleShot(0, self.calculate)

    @property
    def options(self):
        return CompressionOptions(self.quality.currentData())

    def _quality_changed(self):
        self.report = None; self.save_button.setEnabled(False); semantic_label(self.details, "muted")
        self.warning.setText("Verlustfreie Optimierung ohne Neuberechnung der Bildpixel." if self.quality.currentData() == "lossless" else
                             "Bilder können sichtbar an Qualität verlieren. Text und Vektoren werden nicht gerastert. Prüfen Sie das Ergebnis vor der Weitergabe.")
        self.comparison.setText("Bereit für den Größenvergleich."); self.details.clear()

    def calculate(self):
        if self.worker: return
        semantic_label(self.details, "muted")
        self.worker = CompressionWorker(self.sources, self.pages, self.options, self)
        self.worker.result.connect(self._result); self.worker.failed.connect(self._failed); self.worker.finished.connect(self._finished)
        self.preview_button.setEnabled(False); self.quality.setEnabled(False); self.save_button.setEnabled(False)
        self.progress.show(); self.comparison.setText("Dateigrößen werden berechnet …"); self.worker.start()

    def _result(self, report):
        self.report = report
        self.comparison.setText(f"ca. {size_text(report.baseline_bytes)}  →  {size_text(report.output_bytes)}")
        benefit = f"{report.saved_percent:.1f} % kleiner".replace(".", ",") if report.saved_bytes else "Keine weitere Größenersparnis möglich."
        self.details.setText(f"{benefit}\n{report.images_changed} Bild(er) optimiert · {report.images_skipped} spezielle Bild(er) unverändert.\nDateiname und Metadaten können die endgültige Dateigröße geringfügig ändern. Die neue Datei bleibt unverschlüsselt; für Passwortschutz das separate Werkzeug verwenden.")
        self.details.setMinimumHeight(self.details.heightForWidth(max(280,self.width()-48)))

    def _failed(self, error):
        self.report = None; self.comparison.setText("Vergleich nicht möglich.")
        self.details.setText(error); semantic_label(self.details, "error")

    def _finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater(); self.progress.hide()
        self.preview_button.setEnabled(True); self.quality.setEnabled(True); self.save_button.setEnabled(self.report is not None)
        if self._reject_pending: super().reject()

    def reject(self):
        if self.worker:
            self._reject_pending = True; self.worker.cancel_event.set()
            self.comparison.setText("Vergleich wird abgebrochen …")
            self.buttons.setEnabled(False)
        else: super().reject()

    def accept(self):
        if self.worker is None and self.report is not None:
            super().accept()

    def closeEvent(self, event):
        if self.worker: self.reject(); event.ignore()
        else: super().closeEvent(event)

    @classmethod
    def get_options(cls, parent, sources, pages):
        dialog = cls(parent, sources, pages)
        return dialog.options if dialog.exec() == QDialog.DialogCode.Accepted else None


class PasswordDialog(QDialog):
    def __init__(self, parent, *, source_name=None, incorrect=False):
        super().__init__(parent)
        self.protect = source_name is None
        self.setWindowTitle("Mit Passwort schützen" if self.protect else "Geschützte PDF öffnen")
        self.resize(460, 290)
        layout = QVBoxLayout(self); layout.setContentsMargins(24,24,24,20); layout.setSpacing(14)
        heading(layout, "Ein Passwort für Ihre PDF." if self.protect else "PDF entsperren.",
                "Die neue PDF wird mit AES-256 verschlüsselt. Zum Öffnen wird dieses Passwort benötigt. Bewahren Sie es sicher auf." if self.protect else
                f"Öffnungspasswort für {source_name}. Das Passwort wird nur für diesen Import verwendet und nicht gespeichert.")
        form = QFormLayout(); form.setSpacing(12)
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setAccessibleName("Passwort")
        form.addRow("Passwort", self.password)
        self.confirm = QLineEdit(); self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm.setAccessibleName("Passwort wiederholen")
        if self.protect: form.addRow("Wiederholen", self.confirm)
        layout.addLayout(form)
        self.show_password = QCheckBox("Passwort anzeigen"); self.show_password.toggled.connect(self._show_password); layout.addWidget(self.show_password)
        self.error = QLabel("Das Passwort ist falsch. Bitte erneut versuchen." if incorrect else "")
        self.error.setWordWrap(True); semantic_label(self.error,"error"); layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Geschützte PDF speichern …" if self.protect else "PDF öffnen")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.password.setFocus()

    def _show_password(self, shown):
        for field in (self.password, self.confirm):
            field.setEchoMode(QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password)

    def accept(self):
        password = self.password.text()
        if self.protect and not password:
            self.error.setText("Bitte ein Passwort eingeben."); self.password.setFocus(); return
        if self.protect and len(password.encode("utf-8")) > 127:
            self.error.setText("Das Passwort darf höchstens 127 UTF-8-Bytes lang sein."); return
        if self.protect and password != self.confirm.text():
            self.error.setText("Die beiden Passwörter stimmen nicht überein."); self.confirm.setFocus(); return
        super().accept()

    @classmethod
    def get_password(cls, parent, *, source_name=None, incorrect=False):
        dialog = cls(parent, source_name=source_name, incorrect=incorrect)
        result = dialog.password.text() if dialog.exec() == QDialog.DialogCode.Accepted else None
        dialog.password.clear(); dialog.confirm.clear()
        return result


class FormDialog(QDialog):
    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Formular ausfüllen"); self.resize(570, 480)
        self.fields = form_fields(source); self.editors = {}
        layout = QVBoxLayout(self); layout.setContentsMargins(24,24,24,20); layout.setSpacing(14)
        heading(layout, "Formular ausfüllen.", f"{source.label}\nAlle Formularfelder dieser Quelldatei. Änderungen gelten auch für ihre duplizierten Seiten im Arbeitsbereich. Gespeichert wird erst beim PDF-Export.")
        area = QScrollArea(); area.setWidgetResizable(True); area.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget(); form = QFormLayout(body); form.setSpacing(14)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        for field in self.fields:
            if field.kind == "checkbox":
                editor = QCheckBox("Aktiviert"); editor.setChecked(bool(field.value))
            elif field.kind == "choice" and isinstance(field.value, tuple):
                editor = QListWidget(); editor.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
                for value, label in field.options:
                    item = QListWidgetItem(label); item.setData(Qt.ItemDataRole.UserRole,value); editor.addItem(item); item.setSelected(value in field.value)
                editor.setMaximumHeight(120)
            elif field.kind in ("choice", "radio"):
                editor = QComboBox()
                if not any(value == "" for value, _ in field.options):
                    editor.addItem("– Keine Auswahl –", "")
                for value, label in field.options: editor.addItem(label,value)
                index = editor.findData(field.value)
                if index < 0: editor.addItem(str(field.value),field.value); index=editor.count()-1
                editor.setCurrentIndex(index)
            elif field.multiline:
                editor = QPlainTextEdit(str(field.value)); editor.setMaximumHeight(112)
            else:
                editor = QLineEdit(str(field.value))
                if field.max_length: editor.setMaxLength(field.max_length)
            editor.setEnabled(not field.read_only); editor.setAccessibleName(field.label or field.name)
            editor.setToolTip(f"{field.name} · Seite(n) {', '.join(map(str,field.page_numbers))}" + (" · schreibgeschützt" if field.read_only else ""))
            form.addRow((field.label or field.name) + (" (schreibgeschützt)" if field.read_only else ""),editor)
            self.editors[field.name] = editor
        area.setWidget(body); layout.addWidget(area,1)
        hint = QLabel("Unterstützt werden Textfelder, Kontrollkästchen, Auswahllisten und Optionsgruppen. XFA, Skripte und Zertifikatssignaturen werden nicht unterstützt.")
        hint.setWordWrap(True); hint.setObjectName("muted"); layout.addWidget(hint)
        self.error = QLabel(); self.error.setWordWrap(True); semantic_label(self.error,"error"); layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Änderungen übernehmen"); buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def accept(self):
        for field in self.fields:
            editor = self.editors[field.name]
            if field.kind == "text" and field.max_length and not field.read_only:
                text = editor.toPlainText() if isinstance(editor,QPlainTextEdit) else editor.text()
                if len(text) > field.max_length:
                    self.error.setText(f"{field.label or field.name}: höchstens {field.max_length} Zeichen.")
                    editor.setFocus(); return
        super().accept()

    def values(self):
        values = {}
        for field in self.fields:
            if field.read_only: continue
            editor = self.editors[field.name]
            if isinstance(editor,QCheckBox): value=editor.isChecked()
            elif isinstance(editor,QComboBox): value=editor.currentData()
            elif isinstance(editor,QListWidget): value=tuple(item.data(Qt.ItemDataRole.UserRole) for item in editor.selectedItems())
            elif isinstance(editor,QPlainTextEdit): value=editor.toPlainText()
            else: value=editor.text()
            if value != field.value: values[field.name]=value
        return values

    @classmethod
    def get_values(cls,parent,source):
        dialog=cls(source,parent)
        return dialog.values() if dialog.exec()==QDialog.DialogCode.Accepted else None
