"""Cancellable, snapshot-based search without blocking document editing."""
import threading

from PyQt6.QtCore import QEvent, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QToolButton
from ..core import OperationCancelled
from ..search import SearchResults, search_workspace


class SearchWorker(QThread):
    completed = pyqtSignal(int, object, str)

    def __init__(self, serial, sources, pages, query, parent):
        super().__init__(parent)
        self.serial, self.sources, self.pages, self.query = serial, sources, pages, query
        self.cancel_event = threading.Event()

    def run(self):
        try:
            result = search_workspace(self.sources, self.pages, self.query, self.cancel_event.is_set)
            self.completed.emit(self.serial, result, '')
        except OperationCancelled:
            pass
        except Exception as error:
            self.completed.emit(self.serial, SearchResults(()), str(error))


class SearchBar(QFrame):
    hit_selected = pyqtSignal(object)
    cleared = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, snapshot, parent=None):
        super().__init__(parent)
        self.snapshot = snapshot
        self.worker = None
        self.serial = 0
        self.results = SearchResults(())
        self.index = -1
        self._closing = False
        self._pages = ()
        self.setObjectName('toolbar')
        layout = QHBoxLayout(self); layout.setContentsMargins(6, 4, 6, 4)
        self.editor = QLineEdit(); self.editor.setPlaceholderText('PDF und eigene Textfelder durchsuchen …')
        self.editor.setMaxLength(256); self.editor.setAccessibleName('Suchtext')
        self.count = QLabel(''); self.count.setObjectName('muted')
        self.previous = QToolButton(); self.previous.setText('↑'); self.previous.setToolTip('Vorheriger Treffer (Umschalt+Eingabe)')
        self.next = QToolButton(); self.next.setText('↓'); self.next.setToolTip('Nächster Treffer (Eingabe)')
        self.close_button = QToolButton(); self.close_button.setText('×'); self.close_button.setToolTip('Suche schließen (Esc)')
        for button in (self.previous, self.next, self.close_button): button.setAccessibleName(button.toolTip()); button.setMinimumSize(30, 30)
        layout.addWidget(self.editor, 1); layout.addWidget(self.count)
        for button in (self.previous, self.next, self.close_button): layout.addWidget(button)
        self.previous.clicked.connect(lambda: self.advance(-1)); self.next.clicked.connect(lambda: self.advance(1))
        self.close_button.clicked.connect(self.dismiss)
        self.editor.installEventFilter(self)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.setInterval(200)
        self.timer.timeout.connect(self._launch)
        self.editor.textChanged.connect(self.restart)
        self._update_count(); self.hide()

    def open(self):
        self.show(); self.editor.setFocus(); self.editor.selectAll()
        if self.editor.text().strip() and not self.results.hits: self.restart()

    def dismiss(self):
        self.hide(); self.timer.stop(); self.serial += 1
        if self.worker: self.worker.cancel_event.set()
        self.results = SearchResults(()); self.index = -1; self._update_count(); self.cleared.emit()

    def workspace_changed(self, pages):
        pages = tuple(pages)
        if self._pages != pages:
            self._pages = pages
            if self.isVisible(): self.restart()

    def restart(self, *_):
        self.serial += 1; self.results = SearchResults(()); self.index = -1
        self.cleared.emit()
        if self.worker: self.worker.cancel_event.set()
        self.count.setText('Suche …' if self.editor.text().strip() else '')
        self.previous.setEnabled(False); self.next.setEnabled(False)
        self.timer.start()

    def _launch(self):
        if self.worker or self._closing or not self.isVisible(): return
        if not self.editor.text().strip(): self._update_count(); return
        sources, pages = self.snapshot()
        worker = SearchWorker(self.serial, sources, pages, self.editor.text(), self)
        self.worker = worker
        worker.completed.connect(self._completed)
        worker.finished.connect(self._finished)
        worker.start()

    def _completed(self, serial, result, error):
        if serial != self.serial or self._closing or not self.isVisible(): return
        self.results = result; self.index = -1
        if error:
            self.count.setText('Suche fehlgeschlagen'); self.count.setToolTip(error)
        elif result.hits: self.advance(1)
        else: self._update_count()

    def _finished(self):
        worker, self.worker = self.worker, None
        stale = worker.serial != self.serial
        worker.deleteLater()
        if self._closing: self.closed.emit()
        elif stale and self.isVisible(): self._launch()

    def advance(self, direction):
        if not self.results.hits: return
        self.index = (self.index + direction) % len(self.results.hits)
        self._update_count(); self.hit_selected.emit(self.results.hits[self.index])

    def _update_count(self):
        count = len(self.results.hits)
        self.count.setText(f'{self.index + 1} / {count}' + ('+' if self.results.truncated else '') if count else 'Keine Treffer' if self.editor.text().strip() else '')
        self.count.setToolTip(self.results.hits[self.index].context if count and self.index >= 0 else '')
        self.previous.setEnabled(bool(count)); self.next.setEnabled(bool(count))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape: self.dismiss(); return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.advance(-1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1); return True
        return super().eventFilter(watched, event)

    @property
    def is_closed(self): return self._closing and self.worker is None

    def shutdown(self):
        self._closing = True; self.timer.stop(); self.serial += 1
        if self.worker: self.worker.cancel_event.set()
        else: self.closed.emit()
