"""Project passwords and explicit selection of inactive recovery snapshots."""
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout)

from ..projects import discard_recovery
from .pdf_dialogs import heading
from .theme import semantic_label


class ProjectPasswordDialog(QDialog):
    def __init__(self, parent=None, *, saving=False, filename="", incorrect=False):
        super().__init__(parent)
        self.setWindowTitle("Projekt schützen" if saving else "Geschütztes Projekt öffnen")
        self.resize(490, 290)
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 22, 24, 20); layout.setSpacing(14)
        heading(layout, "Arbeit sicher aufbewahren." if saving else "Projekt entsperren.",
            "Das Passwort schützt alle eingebetteten PDFs, Objekte und Rückgängig-Schritte. Ohne Passwort kann das Projekt nicht wiederhergestellt werden."
            if saving else f"Passwort für {filename}. Es wird nur für diese Sitzung im Arbeitsspeicher behalten.")
        form = QFormLayout(); self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setAccessibleName("Projektpasswort"); form.addRow("Passwort", self.password)
        self.confirmation = QLineEdit(); self.confirmation.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirmation.setAccessibleName("Projektpasswort wiederholen")
        if saving: form.addRow("Wiederholen", self.confirmation)
        layout.addLayout(form)
        self.error = QLabel("Das Passwort stimmt nicht oder die Datei ist beschädigt." if incorrect else "")
        self.error.setWordWrap(True); semantic_label(self.error, "error"); layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Schützen" if saving else "Öffnen")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.saving = saving; self.password.setFocus()

    def accept(self):
        value = self.password.text()
        if not value or len(value.encode("utf-8")) > 1024:
            self.error.setText("Bitte ein Passwort mit 1 bis 1.024 UTF-8-Bytes eingeben."); return
        if self.saving and value != self.confirmation.text():
            self.error.setText("Die beiden Passwörter stimmen nicht überein."); return
        super().accept()

    @classmethod
    def ask(cls, parent=None, **options):
        dialog = cls(parent, **options)
        try: return dialog.password.text() if dialog.exec() == QDialog.DialogCode.Accepted else None
        finally:
            dialog.password.clear(); dialog.confirmation.clear(); dialog.deleteLater()


class RecoveryDialog(QDialog):
    def __init__(self, parent, entries, directory):
        super().__init__(parent); self.entries = tuple(entries); self.directory = directory
        self.setWindowTitle("Arbeitsbereich wiederherstellen"); self.resize(550, 380)
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 22, 24, 20); layout.setSpacing(14)
        heading(layout, "Hier können Sie weitermachen.",
            "Diese Sicherungen stammen aus beendeten Sitzungen. Auch die vorige Generation bleibt auswählbar, falls die neueste beschädigt ist.")
        self.list = QListWidget(); self.list.setAccessibleName("Verfügbare automatische Sicherungen")
        for entry in self.entries:
            date = datetime.fromtimestamp(entry.modified_ns / 1e9).strftime("%d.%m.%Y · %H:%M:%S")
            item = QListWidgetItem(date + (" · passwortgeschützt" if entry.encrypted else " · lokal"))
            item.setToolTip(str(entry.path)); self.list.addItem(item)
        if self.entries: self.list.setCurrentRow(0)
        layout.addWidget(self.list, 1)
        self.error = QLabel(); self.error.setWordWrap(True); semantic_label(self.error, "error"); layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Open).setText("Wiederherstellen")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Später")
        delete = QPushButton("Sitzung verwerfen"); buttons.addButton(delete, QDialogButtonBox.ButtonRole.ActionRole)
        delete.clicked.connect(self.discard); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def discard(self):
        row = self.list.currentRow()
        if row < 0: return
        session = self.entries[row].session_id
        try: discard_recovery(self.directory, session)
        except Exception as error: self.error.setText(str(error)); return
        for index in reversed(range(len(self.entries))):
            if self.entries[index].session_id == session: self.list.takeItem(index)
        self.entries = tuple(entry for entry in self.entries if entry.session_id != session)

    @classmethod
    def pick(cls, parent, entries, directory):
        dialog = cls(parent, entries, directory)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.list.currentRow() >= 0:
                return dialog.entries[dialog.list.currentRow()]
            return None
        finally: dialog.deleteLater()
