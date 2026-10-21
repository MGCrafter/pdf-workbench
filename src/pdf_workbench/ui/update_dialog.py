"""A deliberate download/restart flow; closing never starts an installation."""
from __future__ import annotations

import sys
import threading
import time

from PyQt6.QtCore import QSettings,QThread,QTimer,pyqtSignal
from PyQt6.QtWidgets import QCheckBox,QDialog,QDialogButtonBox,QLabel,QProgressBar,QPushButton,QVBoxLayout

from ..core import OperationCancelled
from ..updates import CHECK_ENABLED_KEY,LAST_CHECK_KEY,check_latest,download_update
from ..update_installer import discard_install,prepare_install
from .pdf_dialogs import heading,size_text
from .theme import semantic_label


class UpdateWorker(QThread):
    result=pyqtSignal(object)
    failed=pyqtSignal(str)
    progress=pyqtSignal(int,str)

    def __init__(self,current_version,cache_dir,release=None,parent=None):
        super().__init__(parent);self.current_version=current_version;self.cache_dir=cache_dir;self.release=release;self.cancel_event=threading.Event()

    def run(self):
        try:
            if self.release is None:self.result.emit(check_latest(self.current_version,cancel=self.cancel_event.is_set))
            else:
                downloaded=download_update(self.release,self.cache_dir,self.cancel_event.is_set,self.progress.emit)
                self.progress.emit(-1,"Download geprüft. Der neue Programmstart wird vorbereitet …")
                self.result.emit(prepare_install(downloaded,cancel=self.cancel_event.is_set))
        except OperationCancelled:pass
        except Exception as error:self.failed.emit(str(error))


class UpdateDialog(QDialog):
    def __init__(self,parent,current_version,cache_dir,settings=None,release=None):
        super().__init__(parent);self.current_version=current_version;self.cache_dir=cache_dir;self.settings=settings if settings is not None else QSettings()
        self.release=release;self.plan=None;self.worker=None;self._closing=False;self._accepted=False;self._checking=False
        self.setWindowTitle("Programm aktualisieren");self.resize(540,420)
        layout=QVBoxLayout(self);layout.setContentsMargins(24,22,24,20);layout.setSpacing(14)
        heading(layout,"Immer auf dem aktuellen Stand.","Neue Versionen kommen direkt aus dem offiziellen GitHub-Release. Herunterladen und Neustart bleiben Ihre Entscheidung.")
        self.version_label=QLabel(f"Installiert: {current_version}");self.version_label.setObjectName("sectionTitle");layout.addWidget(self.version_label)
        self.status=QLabel("Neue Version wird gesucht …");self.status.setWordWrap(True);layout.addWidget(self.status)
        self.detail=QLabel();self.detail.setWordWrap(True);self.detail.setObjectName("muted");layout.addWidget(self.detail)
        self.progress=QProgressBar();self.progress.setAccessibleName("Fortschritt des Programmupdates");self.progress.setRange(0,0);self.progress.setFixedHeight(7);self.progress.setTextVisible(False);layout.addWidget(self.progress)
        self.automatic=QCheckBox("Einmal täglich automatisch nach Updates suchen");self.automatic.setChecked(self.settings.value(CHECK_ENABLED_KEY,True,type=bool));self.automatic.toggled.connect(self._preference);layout.addWidget(self.automatic)
        self.error=QLabel();self.error.setWordWrap(True);self.error.setAccessibleName("Hinweis zum Programmupdate");semantic_label(self.error,"error");layout.addWidget(self.error);layout.addStretch()
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Schließen")
        self.action=QPushButton("Erneut prüfen");self.action.setObjectName("primary");self.action.clicked.connect(self._action);self.buttons.addButton(self.action,QDialogButtonBox.ButtonRole.ActionRole)
        self.buttons.rejected.connect(self.reject);layout.addWidget(self.buttons)
        if self.release is None:QTimer.singleShot(0,self.check)
        else:self._show_release()

    def _preference(self,enabled):self.settings.setValue(CHECK_ENABLED_KEY,enabled);self.settings.sync()

    def _action(self):
        if self.plan is not None:self.accept()
        elif self.release is not None:self.download()
        else:self.check()

    def _busy(self):
        self.action.setEnabled(False);self.error.clear();self.progress.setRange(0,0);self.progress.show();self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")

    def check(self):
        if self.worker is not None or self._closing:return
        self._checking=True;self._busy();self.status.setText("Neue Version wird gesucht …")
        self.worker=UpdateWorker(self.current_version,self.cache_dir,parent=self);self._connect()

    def download(self):
        if self.worker is not None or self.release is None or self._closing:return
        if not getattr(sys,"frozen",False):return
        self._checking=False;self._busy();self.status.setText("Update wird heruntergeladen und geprüft …")
        self.worker=UpdateWorker(self.current_version,self.cache_dir,self.release,self);self._connect()

    def _connect(self):
        self.worker.result.connect(self._result);self.worker.failed.connect(self._failed);self.worker.progress.connect(self._progress);self.worker.finished.connect(self._finished);self.worker.start()

    def _progress(self,value,message):
        if value<0:self.progress.setRange(0,0)
        else:self.progress.setRange(0,100);self.progress.setValue(value)
        self.status.setText(message)

    def _result(self,result):
        if self._checking:
            self.settings.setValue(LAST_CHECK_KEY,time.time());self.settings.sync();self.release=result
            self._show_release()
        else:
            self.plan=result
            self.status.setText("Update geprüft und zum Neustart bereit.")
            self.detail.setText("Vor dem Neustart können Sie die aktuelle Arbeit speichern oder abbrechen. Die vorige Programmkopie bleibt als Sicherung erhalten.")
            self.action.setText("Neustarten und installieren …")

    def _show_release(self):
        if self.release is None:
            self.status.setText("Sie verwenden die aktuelle stabile Version.");self.detail.setText("Beim nächsten automatischen Prüfen wird nur eine kleine Release-Anfrage gesendet.");self.action.setText("Erneut prüfen")
        else:
            self.status.setText(f"Version {self.release.version} ist verfügbar.")
            self.detail.setText(f"{size_text(self.release.asset.size)} · passendes Paket für {self.release.platform.capitalize()}\nDer Download wird mit beiden veröffentlichten SHA-256-Angaben geprüft.")
            self.action.setText("Herunterladen und vorbereiten")
            if not getattr(sys,"frozen",False):self.detail.setText(self.detail.text()+"\nEin Quellcode-Checkout wird nicht aktualisiert. Bitte das portable Programmpaket manuell verwenden.")
        self.progress.hide();self.action.setEnabled(self.release is None or bool(getattr(sys,"frozen",False)))

    def _failed(self,message):
        if self._checking:self.settings.setValue(LAST_CHECK_KEY,time.time());self.settings.sync()
        self.error.setText(message);self.status.setText("Das Update konnte nicht abgeschlossen werden.")

    def _finished(self):
        worker,self.worker=self.worker,None;worker.deleteLater();self.progress.hide()
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Schließen")
        if self._closing:
            self._discard()
            super().reject();return
        self.action.setEnabled(self.plan is not None or self.release is None or bool(getattr(sys,"frozen",False)))

    def accept(self):
        if self.plan is not None and self.worker is None:self._accepted=True;super().accept()

    def reject(self):
        self._closing=True
        if self.worker is not None:
            self.worker.cancel_event.set();self.action.setEnabled(False);self.status.setText("Update wird abgebrochen …")
        else:
            if not self._accepted:self._discard()
            super().reject()

    def _discard(self):
        if self.plan is None:return
        try:discard_install(self.plan)
        except Exception as error:self.error.setText(f"Die temporäre Vorbereitung konnte nicht entfernt werden: {error}")
        self.plan=None

    def closeEvent(self,event):
        if self.worker is not None:self.reject();event.ignore()
        else:super().closeEvent(event)

    @classmethod
    def get_plan(cls,parent,current_version,cache_dir,settings=None,release=None):
        dialog=cls(parent,current_version,cache_dir,settings,release)
        try:return dialog.plan if dialog.exec()==QDialog.DialogCode.Accepted else None
        finally:dialog.deleteLater()
