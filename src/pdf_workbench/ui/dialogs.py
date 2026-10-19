"""Small native dialogs for page ranges and safe separate-page export."""
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QVBoxLayout,
)

from ..core import PdfWorkbenchError
from ..features import parse_page_selection


class PageRangeDialog(QDialog):
    def __init__(self, parent, page_count):
        super().__init__(parent)
        self.page_count = page_count
        self.selected_indices = []
        self.setWindowTitle("Seitenbereich auswählen")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 24, 24, 20); layout.setSpacing(14)
        label = QLabel(f"Seiten der aktuellen Reihenfolge · {page_count} insgesamt")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText("z. B. 1-3, 5, 8-10")
        self.range_edit.setAccessibleName("Seitennummern und Bereiche")
        self.range_edit.setAccessibleDescription("Kommagetrennte Seitennummern und inklusive Bereiche, beginnend mit 1.")
        label.setBuddy(self.range_edit)
        line = QHBoxLayout(); line.addWidget(self.range_edit)
        all_button = QPushButton("Alle")
        all_button.clicked.connect(lambda: self.range_edit.setText(f"1-{page_count}" if page_count > 1 else "1"))
        line.addWidget(all_button); layout.addLayout(line)
        self.error_label = QLabel("Bereiche beziehen sich auf die aktuelle Reihenfolge, nicht auf die Originaldateien.")
        self.error_label.setWordWrap(True); self.error_label.setObjectName("muted")
        layout.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Auswählen")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.range_edit.textChanged.connect(self.validate)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.range_edit.setFocus()

    def validate(self, text):
        try:
            self.selected_indices = parse_page_selection(text, self.page_count)
            self.error_label.setText(f"{len(self.selected_indices)} Seite(n) werden ausgewählt.")
            self.error_label.setStyleSheet("color: #526278;")
            valid = True
        except PdfWorkbenchError as error:
            self.selected_indices = []
            self.error_label.setText(str(error)); self.error_label.setStyleSheet("color: #a52b2b;")
            valid = False
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(valid)

    @staticmethod
    def get_selection(parent, page_count):
        dialog = PageRangeDialog(parent, page_count)
        return dialog.selected_indices if dialog.exec() == QDialog.DialogCode.Accepted else None


class SeparateExportDialog(QDialog):
    def __init__(self, parent, page_count):
        super().__init__(parent)
        self.destination = None
        self.setWindowTitle("Auswahl als einzelne PDFs speichern")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 24, 24, 20); layout.setSpacing(14)
        intro = QLabel(f"{page_count} ausgewählte Seite(n) werden als nummerierte PDF-Dateien in einem neuen Unterordner gespeichert.")
        intro.setWordWrap(True); layout.addWidget(intro)
        form = QFormLayout(); form.setSpacing(10)
        self.parent_edit = QLineEdit(str(Path.home() / "Documents" if (Path.home() / "Documents").is_dir() else Path.home()))
        self.parent_edit.setAccessibleName("Übergeordneter Zielordner")
        parent_line = QHBoxLayout(); parent_line.addWidget(self.parent_edit)
        browse = QPushButton("Wählen …"); browse.clicked.connect(self.browse)
        parent_line.addWidget(browse)
        form.addRow("Zielordner", parent_line)
        self.folder_edit = QLineEdit("PDF-Einzelseiten")
        self.folder_edit.setAccessibleName("Name des neuen Unterordners")
        form.addRow("Neuer Unterordner", self.folder_edit)
        layout.addLayout(form)
        self.error_label = QLabel(); self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Einzelseiten speichern")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.parent_edit.textChanged.connect(self.validate); self.folder_edit.textChanged.connect(self.validate)
        self.validate()

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "Übergeordneten Zielordner wählen", self.parent_edit.text())
        if path:
            self.parent_edit.setText(path)

    def validate(self, *_):
        parent = Path(self.parent_edit.text()).expanduser()
        name = self.folder_edit.text().strip()
        error = None
        if not parent.is_dir():
            error = "Bitte einen vorhandenen übergeordneten Ordner wählen."
        elif not name or name in (".", "..") or any(char in name for char in '/\\:\0'):
            error = "Bitte einen einfachen Namen für den neuen Unterordner angeben."
        elif (parent / name).exists() or (parent / name).is_symlink():
            error = "Dieser Unterordner existiert bereits. Bitte einen neuen Namen wählen."
        self.destination = None if error else parent / name
        self.error_label.setText(error or str(self.destination))
        self.error_label.setStyleSheet("color: #a52b2b;" if error else "color: #526278;")
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(error is None)

    @staticmethod
    def get_destination(parent, page_count):
        dialog = SeparateExportDialog(parent, page_count)
        return dialog.destination if dialog.exec() == QDialog.DialogCode.Accepted else None
