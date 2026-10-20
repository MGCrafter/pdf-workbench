"""Image page import settings; actual conversion runs in the import worker."""
from PyQt6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QLabel, QListWidget, QPushButton, QVBoxLayout
from ..image_import import ImagePageOptions
from .theme import file_dialog_options


class ImagePagesDialog(QDialog):
    def __init__(self, parent=None, files=()):
        super().__init__(parent); self.setWindowTitle('Bilder zu PDF-Seiten'); self.resize(490, 400)
        layout = QVBoxLayout(self)
        hint = QLabel('Jedes Bild wird eine eigene Seite. Danach kannst du die Seiten im Arbeitsbereich sortieren und als PDF speichern.'); hint.setWordWrap(True); layout.addWidget(hint)
        self.files = list(files)
        self.list = QListWidget(); self.list.setAccessibleName('Bilder für neue PDF-Seiten'); layout.addWidget(self.list)
        self.choose_button = QPushButton('Bilder auswählen …'); self.choose_button.clicked.connect(self.choose); layout.addWidget(self.choose_button)
        form = QFormLayout()
        self.paper = QComboBox()
        for label, value in [('A4', 'A4'), ('Letter', 'Letter'), ('Bildformat (150 dpi)', 'image')]: self.paper.addItem(label, value)
        self.orientation = QComboBox()
        for label, value in [('Automatisch', 'auto'), ('Hochformat', 'portrait'), ('Querformat', 'landscape')]: self.orientation.addItem(label, value)
        self.margin = QDoubleSpinBox(); self.margin.setRange(0, 40); self.margin.setValue(10); self.margin.setSuffix(' mm')
        form.addRow('Seitenformat', self.paper); form.addRow('Ausrichtung', self.orientation); form.addRow('Rand', self.margin); layout.addLayout(form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Seiten hinzufügen')
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject); layout.addWidget(self.buttons); self.update_files()

    def choose(self):
        files, _ = QFileDialog.getOpenFileNames(self, 'Bilder auswählen', '', 'Bilder (*.png *.jpg *.jpeg)', options=file_dialog_options())
        if files: self.files = list(dict.fromkeys(files)); self.update_files()

    def update_files(self):
        self.list.clear(); self.list.addItems(self.files)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.files))

    @classmethod
    def get_request(cls, parent=None, files=()):
        dialog = cls(parent, files)
        if dialog.exec() != QDialog.DialogCode.Accepted: return None
        return dialog.files, ImagePageOptions(dialog.paper.currentData(), dialog.orientation.currentData(), dialog.margin.value())
