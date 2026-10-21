"""Configure a local OCR export and explicitly acquire optional language packs."""
from dataclasses import dataclass
from pathlib import Path
import threading

from PyQt6.QtCore import QSettings, QThread, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                            QFormLayout, QHBoxLayout, QLabel, QLineEdit, QProgressBar,
                            QPushButton, QVBoxLayout)

from ..core import OperationCancelled
from ..ocr import (LANGUAGE_PACKS, OcrEngine, OcrError, default_cache_dir,
                   cached_languages, discover_engine, download_language)
from .theme import file_dialog_options, semantic_label


@dataclass(frozen=True)
class OcrRequest:
    scope: str
    engine: OcrEngine
    languages: tuple[str, ...]
    destination: Path
    cache_dir: Path
    dpi: int = 300
    timeout: float = 120


class LanguageDownload(QThread):
    progressed = pyqtSignal(int, str)
    completed = pyqtSignal(str, str)

    def __init__(self, languages, cache_dir, parent):
        super().__init__(parent)
        self.languages, self.cache_dir = languages, cache_dir
        self.cancelled = threading.Event()

    def run(self):
        try:
            for index, code in enumerate(self.languages):
                download_language(code, self.cache_dir, self.cancelled.is_set,
                                  lambda value, text, n=index: self.progressed.emit((n*100+value)//len(self.languages), text))
            self.completed.emit("Sprachpakete sind bereit.", "success")
        except OperationCancelled:
            self.completed.emit("Download abgebrochen. Bereits fertige Pakete bleiben verfügbar.", "warning")
        except Exception as error:
            self.completed.emit(str(error), "error")


class EngineProbe(QThread):
    completed = pyqtSignal(object, str)

    def __init__(self, executable, cache_dir, parent):
        super().__init__(parent)
        self.executable, self.cache_dir = executable, cache_dir
        self.cancelled = threading.Event()

    def run(self):
        try:
            engine = discover_engine(self.executable, self.cache_dir, cancel=self.cancelled.is_set)
            self.completed.emit(engine, "")
        except OperationCancelled:
            self.completed.emit(None, "Prüfung abgebrochen.")
        except Exception as error:
            self.completed.emit(None, str(error))


class OcrDialog(QDialog):
    def __init__(self, parent=None, page_count=0, selected_count=0, *, cache_dir=None, executable=None, settings=None):
        super().__init__(parent)
        self.setWindowTitle("Durchsuchbare PDF erstellen")
        self.resize(570, 480)
        self.page_count, self.selected_count = page_count, selected_count
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.settings = settings if settings is not None else QSettings("PDF Workbench", "PDF Workbench")
        selected_executable = executable if executable is not None else self.settings.value("ocr/executable", "")
        self.engine = None; self.request = None; self.download_job = None; self.engine_job = None
        self._close_when_idle = False
        self.available_languages = set(cached_languages(self.cache_dir))
        layout = QVBoxLayout(self)
        intro = QLabel("Texterkennung mit lokalem Tesseract. Es wird eine zusätzliche PDF mit unsichtbarer Textschicht gespeichert. Die vorhandenen Seiteninhalte und Originaldateien bleiben erhalten. Die neue Datei wird ohne Passwortschutz gespeichert.")
        intro.setWordWrap(True); layout.addWidget(intro)
        form = QFormLayout()
        self.scope = QComboBox(); self.scope.setAccessibleName("Seiten für die Texterkennung")
        self.scope.addItem(f"Alle Seiten ({page_count})", "all")
        if selected_count: self.scope.addItem(f"Ausgewählte Seiten ({selected_count})", "selected")
        form.addRow("Seiten", self.scope)
        engine_line = QHBoxLayout()
        self.engine_path = QLineEdit(str(selected_executable or "")); self.engine_path.setPlaceholderText("Tesseract automatisch suchen")
        self.engine_path.setAccessibleName("Lokales Tesseract-Programm")
        self.choose_engine_button = QPushButton("Wählen …"); self.choose_engine_button.clicked.connect(self.choose_engine)
        self.check_button = QPushButton("Prüfen"); self.check_button.clicked.connect(self.refresh_engine)
        engine_line.addWidget(self.engine_path, 1); engine_line.addWidget(self.choose_engine_button); engine_line.addWidget(self.check_button)
        form.addRow("Tesseract", engine_line)
        self.engine_status = QLabel(); self.engine_status.setWordWrap(True)
        form.addRow("Status", self.engine_status)
        languages = QHBoxLayout()
        self.deu = QCheckBox("Deutsch (ca. 1,5 MB)"); self.deu.setChecked(True)
        self.eng = QCheckBox("Englisch (ca. 4,1 MB)"); self.eng.setChecked(True)
        languages.addWidget(self.deu); languages.addWidget(self.eng)
        form.addRow("Sprachen", languages)
        self.language_status = QLabel(); self.language_status.setWordWrap(True); form.addRow("Sprachpakete", self.language_status)
        self.download_button = QPushButton("Fehlende ausgewählte Sprachpakete herunterladen")
        self.download_button.clicked.connect(self.start_download)
        form.addRow("", self.download_button)
        output_line = QHBoxLayout()
        self.output = QLineEdit(); self.output.setPlaceholderText("Neuen Dateinamen wählen …")
        self.output.setAccessibleName("Neue durchsuchbare PDF-Datei")
        choose_output = QPushButton("Speichern unter …"); choose_output.clicked.connect(self.choose_output)
        output_line.addWidget(self.output, 1); output_line.addWidget(choose_output)
        form.addRow("Ausgabe", output_line)
        layout.addLayout(form)
        hint = QLabel("Seiten mit vorhandener Textschicht werden übersprungen. Der Export läuft offline; nur der ausdrücklich gestartete Sprachpaket-Download benötigt Internet. Tesseract selbst wird nicht automatisch installiert.")
        hint.setWordWrap(True); hint.setObjectName("muted"); layout.addWidget(hint)
        self.progress = QProgressBar(); self.progress.hide(); layout.addWidget(self.progress)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Durchsuchbare PDF erstellen")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject); layout.addWidget(self.buttons)
        self.deu.toggled.connect(self.validate); self.eng.toggled.connect(self.validate)
        self.output.textChanged.connect(self.validate)
        self.engine_path.textEdited.connect(self.invalidate_engine)
        self.refresh_engine()

    def languages(self):
        return tuple(code for code, checkbox in (("deu", self.deu), ("eng", self.eng)) if checkbox.isChecked())

    def invalidate_engine(self):
        self.engine = None; self.engine_status.setText("Geänderten Programmpfad zuerst prüfen.")
        self.validate()

    def refresh_engine(self):
        if self.download_job or self.engine_job: return
        self.engine = None
        self.available_languages = set(cached_languages(self.cache_dir))
        self.engine_status.setText("Lokales Tesseract wird geprüft …"); semantic_label(self.engine_status, "muted")
        self.engine_job = EngineProbe(self.engine_path.text().strip() or None, self.cache_dir, self)
        self.engine_job.completed.connect(self.engine_checked)
        self.engine_job.finished.connect(self.engine_check_finished)
        self.validate(); self.engine_job.start()

    def engine_checked(self, engine, error):
        self.engine = engine
        if error:
            self.engine_status.setText(error); semantic_label(self.engine_status, "error")
        elif engine:
            self.available_languages.update(engine.languages)
            self.engine_status.setText(engine.version + ("" if engine.pdf_font else " · pdf.ttf fehlt in der Installation"))
            semantic_label(self.engine_status, "normal" if engine.pdf_font else "error")
            if self.engine_path.text().strip():
                self.settings.setValue("ocr/executable", str(engine.executable))
            else:
                self.settings.remove("ocr/executable")
            self.settings.sync()
        else:
            self.engine_status.setText("Tesseract ist nicht installiert oder nicht im Suchpfad. Eine lokale Installation kann über Wählen ausgewählt werden.")
            semantic_label(self.engine_status, "muted")

    def engine_check_finished(self):
        job, self.engine_job = self.engine_job, None
        job.deleteLater()
        self.validate()
        if self._close_when_idle: super().reject()

    def validate(self, *_):
        languages = self.languages()
        available = self.available_languages
        missing = [LANGUAGE_PACKS[code]["name"] for code in languages if code not in available]
        self.language_status.setText("Fehlt: " + ", ".join(missing) if missing else ("Ausgewählte Pakete sind vorhanden." if languages else "Bitte mindestens eine Sprache auswählen."))
        semantic_label(self.language_status, "warning" if missing or not languages else "normal")
        busy = self.download_job is not None or self.engine_job is not None
        self.download_button.setEnabled(bool(missing) and not busy)
        self.check_button.setEnabled(not busy)
        self.choose_engine_button.setEnabled(not busy)
        self.engine_path.setEnabled(not busy); self.deu.setEnabled(not busy); self.eng.setEnabled(not busy)
        destination = Path(self.output.text()).expanduser() if self.output.text().strip() else None
        valid_destination = bool(destination and destination.suffix.lower() == ".pdf" and destination.parent.is_dir() and not destination.exists() and not destination.is_symlink())
        ready = bool(self.page_count and self.engine and self.engine.pdf_font and languages and not missing and valid_destination and not busy)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(ready)

    def choose_engine(self):
        if self.download_job or self.engine_job: return
        path, _ = QFileDialog.getOpenFileName(self, "Lokales Tesseract-Programm wählen", "", "Alle Dateien (*)", options=file_dialog_options())
        if path: self.engine_path.setText(path); self.refresh_engine()

    def choose_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Zusätzliche durchsuchbare PDF speichern", "durchsuchbar.pdf", "PDF-Dateien (*.pdf)", options=QFileDialog.Option.DontConfirmOverwrite | file_dialog_options())
        if path:
            destination = Path(path)
            if not destination.suffix: destination = destination.with_suffix(".pdf")
            self.output.setText(str(destination))
            if destination.exists() or destination.is_symlink():
                self.status.setText("Diese Datei existiert bereits. Bitte einen neuen Namen wählen."); semantic_label(self.status, "error")

    def start_download(self):
        if self.download_job or self.engine_job: return
        available = self.available_languages
        missing = tuple(code for code in self.languages() if code not in available)
        if not missing: return
        self.download_job = LanguageDownload(missing, self.cache_dir, self)
        self.download_job.progressed.connect(self.download_progress)
        self.download_job.completed.connect(self.download_completed)
        self.download_job.finished.connect(self.download_finished)
        self.progress.setValue(0); self.progress.show()
        self.status.setText("Sprachpaket-Download wurde gestartet. Abbrechen stoppt den Download.")
        semantic_label(self.status, "normal")
        self.validate(); self.download_job.start()

    def download_progress(self, value, text):
        self.progress.setValue(value); self.status.setText(text)

    def download_completed(self, text, tone):
        self.status.setText(text); semantic_label(self.status, tone)

    def download_finished(self):
        job, self.download_job = self.download_job, None
        self.progress.hide(); job.deleteLater()
        if self._close_when_idle: super().reject()
        else: self.refresh_engine()

    def accept(self):
        self.validate()
        if not self.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled(): return
        self.request = OcrRequest(self.scope.currentData(), self.engine, self.languages(),
                                  Path(self.output.text()).expanduser().absolute(), self.cache_dir)
        super().accept()

    def reject(self):
        if self.download_job:
            self._close_when_idle = True
            self.download_job.cancelled.set(); self.status.setText("Download wird abgebrochen …")
            return
        if self.engine_job:
            self._close_when_idle = True
            self.engine_job.cancelled.set(); self.status.setText("Prüfung wird abgebrochen …")
            return
        super().reject()

    def closeEvent(self, event):
        if self.download_job or self.engine_job:
            self.reject(); event.ignore()
        else: super().closeEvent(event)

    @staticmethod
    def get_request(parent, page_count, selected_count):
        dialog = OcrDialog(parent, page_count, selected_count)
        return dialog.request if dialog.exec() == QDialog.DialogCode.Accepted else None
