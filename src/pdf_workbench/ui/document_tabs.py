"""Independent document tabs sharing the window's native PDF view and workers.

Tab switches never occur while an export or autosave owns the live session.
Each tab retains its own immutable workspace, project password and recovery store.
"""
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QSize, QTimer, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QHBoxLayout, QInputDialog, QMenu, QMessageBox, QTabBar, QToolButton

from ..core import Workspace, PdfWorkbenchError
from ..document_transfer import transfer_pages
from ..projects import RecoveryStore, ProjectSnapshot, snapshot_requires_password
from .theme import file_dialog_options
from .icons import icon


SESSION_FIELDS = (
    "saved_state", "_project_path", "_project_identity", "_project_password",
    "_project_saved_key", "_saved_bookmarks", "_recovery_store", "_autosave_last_key",
    "_pending_locked_files",
)


@dataclass
class DocumentTab:
    uid: str = field(default_factory=lambda: uuid4().hex)
    workspace: Workspace = field(default_factory=Workspace)
    session: dict = field(default_factory=dict)
    selected: tuple = ()
    active_uid: str | None = None
    zoom_mode: str = "page"
    zoom_factor: float = 1.0


class DocumentTabs:
    def _build_document_tabs(self, layout):
        row = QHBoxLayout(); row.setSpacing(6)
        self.document_tabbar = QTabBar()
        self.document_tabbar.setAccessibleName("Geöffnete Dokumente")
        self.document_tabbar.setDocumentMode(True)
        self.document_tabbar.setTabsClosable(False)
        self.document_tabbar.setExpanding(False)
        self.document_tabbar.setUsesScrollButtons(True)
        self.document_tabbar.setElideMode(Qt.TextElideMode.ElideRight)
        self.document_tabbar.currentChanged.connect(self.switch_document_tab)
        self.document_tabbar.tabCloseRequested.connect(self.close_document_tab)
        self.document_tabbar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.document_tabbar.customContextMenuRequested.connect(self._document_tab_menu)
        row.addWidget(self.document_tabbar, 1)
        self.add_tab_button = QToolButton(); self.add_tab_button.setText("+")
        self.add_tab_button.setAccessibleName("Neuer Dokument-Tab")
        self.add_tab_button.setToolTip("Neuer Dokument-Tab (Strg+T)")
        self.add_tab_button.setMinimumSize(36, 36)
        self.add_tab_button.clicked.connect(self.new_document_tab)
        row.addWidget(self.add_tab_button); layout.addLayout(row)

    def _init_document_tabs(self):
        self._document_tabs = [DocumentTab(workspace=self.workspace)]
        self._document_index = 0
        self._close_approved_tabs = {}
        self._update_discarded_tabs = {}
        self._pending_tab_operation = None
        self._tab_operation_timer = QTimer(self); self._tab_operation_timer.setInterval(30)
        self._tab_operation_timer.timeout.connect(self._resume_tab_operation)
        self._capture_document_tab()
        self.document_tabbar.blockSignals(True)
        self.document_tabbar.addTab("Dokument 1")
        self._add_document_close_button(0)
        self.document_tabbar.blockSignals(False)
        self._tab_shortcuts = []
        for key, step in (("Ctrl+Tab", 1), ("Ctrl+Shift+Tab", -1)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda delta=step: self._cycle_document_tab(delta))
            self._tab_shortcuts.append(shortcut)

    def _build_document_tab_menu(self, menu):
        self.new_tab_action = self._action(menu, "Neuer Dokument-Tab", "Ctrl+T", self.new_document_tab, "new")
        self.open_tab_action = self._action(menu, "Projekt in neuem Tab öffnen …", None, self.open_project_tab, "document")
        self.close_tab_action = self._action(menu, "Aktuellen Tab schließen", "Ctrl+W", lambda: self.close_document_tab(self._document_index))
        self.copy_tab_action = self._action(menu, "Auswahl in anderen Tab kopieren …", None, self.copy_pages_to_tab, "duplicate")
        self.move_tab_action = self._action(menu, "Auswahl in anderen Tab verschieben …", None, lambda: self.copy_pages_to_tab(move=True))

    def _capture_document_tab(self):
        if not hasattr(self, "_document_tabs"): return
        context = self._document_tabs[self._document_index]
        context.workspace = self.workspace
        context.session = {key: getattr(self, key) for key in SESSION_FIELDS}
        context.selected = tuple(self.selected_ids())
        context.active_uid = self._active_uid
        context.zoom_mode = self.preview.zoom_mode
        context.zoom_factor = self.preview.zoom_factor

    @staticmethod
    def _tab_dirty(context):
        current = (tuple(context.workspace.pages), context.workspace.bookmarks)
        saved = context.session.get("_project_saved_key")
        if saved is None:
            saved = (context.session.get("saved_state", ()), context.session.get("_saved_bookmarks", ()))
        return current != saved

    def _sync_document_tabs(self):
        if not hasattr(self, "_document_tabs"): return
        self._capture_document_tab()
        for index, context in enumerate(self._document_tabs):
            path = context.session.get("_project_path")
            name = Path(path).name if path else f"Dokument {index + 1}"
            self.document_tabbar.setTabText(index, ("* " if self._tab_dirty(context) else "") + name)
            self.document_tabbar.setTabToolTip(index, str(path) if path else f"{len(context.workspace.pages)} Seiten · eigener Arbeitsbereich")
            button = self.document_tabbar.tabButton(index, QTabBar.ButtonPosition.RightSide)
            if button: button.setAccessibleName(f"{name} schließen")

    def _add_document_close_button(self, index):
        uid = self._document_tabs[index].uid
        button = QToolButton(self.document_tabbar)
        button.setIcon(icon("close")); button.setIconSize(QSize(16, 16))
        button.setFixedSize(24, 24); button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setToolTip("Dokument-Tab schließen (Strg+W)")
        def close_tab():
            found = next((i for i, context in enumerate(self._document_tabs) if context.uid == uid), None)
            if found is not None: self.close_document_tab(found)
        button.clicked.connect(close_tab)
        self.document_tabbar.setTabButton(index, QTabBar.ButtonPosition.RightSide, button)

    def _queue_tab_operation(self, callback):
        if self.job or self._closing_requested: return False
        if self._autosave_job is not None:
            self._pending_tab_operation = callback
            self._tab_operation_timer.start()
            return False
        return True

    def _resume_tab_operation(self):
        if self._closing_requested or self.job:
            self._pending_tab_operation = None; self._tab_operation_timer.stop(); return
        if self._autosave_job is None:
            operation, self._pending_tab_operation = self._pending_tab_operation, None
            self._tab_operation_timer.stop()
            if operation: operation()

    def switch_document_tab(self, index):
        if not hasattr(self, "_document_tabs") or not 0 <= index < len(self._document_tabs): return
        if index == self._document_index: return
        if not self._queue_tab_operation(lambda: self.switch_document_tab(index)):
            self.document_tabbar.blockSignals(True); self.document_tabbar.setCurrentIndex(self._document_index); self.document_tabbar.blockSignals(False)
            return
        self._capture_document_tab(); self._autosave_timer.stop()
        self._document_index = index
        context = self._document_tabs[index]
        zoom_mode, zoom_factor = context.zoom_mode, context.zoom_factor
        self.workspace = context.workspace
        for key, value in context.session.items(): setattr(self, key, value)
        self._discard_recovery_pending = False
        self._form_field_cache.clear(); self.search_bar.dismiss()
        self.preview.set_tool("pan"); self.pan_button.setChecked(True)
        self.render_broker.invalidate(clear_cache=True)
        self.document_tabbar.blockSignals(True); self.document_tabbar.setCurrentIndex(index); self.document_tabbar.blockSignals(False)
        self._active_uid = context.active_uid
        self.refresh(context.selected, context.active_uid)
        if zoom_mode == "width": self.preview.fit_width()
        elif zoom_mode == "page": self.preview.fit_page()
        else: self.preview.set_zoom(zoom_factor)
        self._session_changed()

    def new_document_tab(self, checked=False):
        if not self._queue_tab_operation(self.new_document_tab): return None
        self._capture_document_tab()
        context = DocumentTab()
        context.session = dict(saved_state=(), _project_path=None, _project_identity=None,
            _project_password=None, _project_saved_key=None, _saved_bookmarks=(),
            _recovery_store=None, _autosave_last_key=None, _pending_locked_files=[])
        if self._services_enabled:
            try: context.session["_recovery_store"] = RecoveryStore(self._recovery_directory)
            except Exception as error: self.set_status(f"Automatische Sicherung nicht verfügbar: {error}", "warning")
        self._document_tabs.append(context)
        self.document_tabbar.blockSignals(True); self.document_tabbar.addTab(f"Dokument {len(self._document_tabs)}"); self.document_tabbar.blockSignals(False)
        index = len(self._document_tabs) - 1
        self._add_document_close_button(index)
        self.switch_document_tab(index)
        return index

    def close_document_tab(self, index):
        if not 0 <= index < len(self._document_tabs): return
        uid = self._document_tabs[index].uid
        def retry():
            found = next((i for i, tab in enumerate(self._document_tabs) if tab.uid == uid), None)
            if found is not None: self.close_document_tab(found)
        if not self._queue_tab_operation(retry): return
        self.switch_document_tab(index)
        # A modal save question runs its own event loop. Do not allow its
        # autosave timer to start a writer while this tab/store is being closed.
        self._autosave_timer.stop()
        if not self.confirm_discard(after_save=retry):
            self._session_changed()
            return
        self._autosave_timer.stop()
        store = self._recovery_store
        if store:
            try: store.close(discard=True)
            except Exception as error: self.set_status(f"Automatische Sicherung bleibt erhalten: {error}", "warning")
        if len(self._document_tabs) == 1:
            self._recovery_store = None
            self._reset_project_session()
            self.workspace = Workspace(); self.saved_state = (); self._active_uid = None
            self._pending_locked_files = []
            if self._services_enabled:
                try: self._recovery_store = RecoveryStore(self._recovery_directory)
                except Exception as error: self.set_status(f"Automatische Sicherung nicht verfügbar: {error}", "warning")
            self.refresh(())
            return
        context = self._document_tabs.pop(index)
        context.session["_project_password"] = None; context.session["_recovery_store"] = None
        button = self.document_tabbar.tabButton(index, QTabBar.ButtonPosition.RightSide)
        self.document_tabbar.blockSignals(True); self.document_tabbar.removeTab(index); self.document_tabbar.blockSignals(False)
        if button: button.deleteLater()
        self._document_index = -1
        # The closed context must not be captured into a surviving tab.
        self._activate_surviving_tab(min(index, len(self._document_tabs) - 1))

    def _activate_surviving_tab(self, index):
        self._document_index = index
        context = self._document_tabs[index]
        zoom_mode, zoom_factor = context.zoom_mode, context.zoom_factor
        self.workspace = context.workspace
        for key, value in context.session.items(): setattr(self, key, value)
        self._form_field_cache.clear(); self.search_bar.dismiss()
        self.render_broker.invalidate(clear_cache=True)
        self._active_uid = context.active_uid
        self.document_tabbar.blockSignals(True); self.document_tabbar.setCurrentIndex(index); self.document_tabbar.blockSignals(False)
        self.refresh(context.selected, context.active_uid)
        if zoom_mode == "width": self.preview.fit_width()
        elif zoom_mode == "page": self.preview.fit_page()
        else: self.preview.set_zoom(zoom_factor)

    def _cycle_document_tab(self, delta):
        self.switch_document_tab((self._document_index + delta) % len(self._document_tabs))

    def _document_tab_menu(self, point):
        index = self.document_tabbar.tabAt(point)
        if index < 0: return
        menu = QMenu(self)
        action = menu.addAction("Tab schließen")
        if menu.exec(self.document_tabbar.mapToGlobal(point)) is action:
            self.close_document_tab(index)

    def open_project_tab(self, checked=False, *, path=None):
        if self.job or self._closing_requested: return
        if path is None:
            from PyQt6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(self, "Projekt in neuem Tab öffnen", "", "PDF-Workbench-Projekt (*.pdfwb)", options=file_dialog_options())
            if not path: return
        if not self._queue_tab_operation(lambda: self.open_project_tab(path=path)): return
        if self.new_document_tab() is not None:
            self.open_project(path=path, confirmed=True)

    def copy_pages_to_tab(self, checked=False, *, move=False, target_index=None):
        if not self._queue_tab_operation(lambda: self.copy_pages_to_tab(move=move, target_index=target_index)): return
        selected = self.selected_ids()
        if not selected: return
        self._capture_document_tab()
        choices = [(i, self.document_tabbar.tabText(i)) for i in range(len(self._document_tabs)) if i != self._document_index]
        if target_index is None:
            labels = [f"{i + 1}: {name}" for i, name in choices] + ["Neuer Dokument-Tab"]
            value, accepted = QInputDialog.getItem(self, "Seiten verschieben" if move else "Seiten kopieren", "Ziel-Tab:", labels, editable=False)
            if not accepted: return
            if value == labels[-1]:
                original_index = self._document_index
                target_index = self.new_document_tab()
                self.switch_document_tab(original_index)
            else: target_index = choices[labels.index(value)][0]
        if target_index is None or not 0 <= target_index < len(self._document_tabs): return
        target = self._document_tabs[target_index]
        try: ids = transfer_pages(self.workspace, target.workspace, selected, move=move)
        except PdfWorkbenchError as error: self.show_error("Seiten konnten nicht übertragen werden.", str(error)); return
        target.selected = ids; target.active_uid = ids[0]
        # A dormant tab has no worker. Schedule its next autosave when activated.
        target.session["_autosave_last_key"] = None
        self.refresh(() if move else selected)
        self._sync_document_tabs()
        self.set_status(f"{len(ids)} Seiten in Tab {target_index + 1} {'verschoben' if move else 'kopiert'}. Rückgängig ist in beiden Tabs getrennt verfügbar.")

    def _update_document_tab_controls(self, busy):
        self.document_tabbar.setEnabled(not busy)
        self.add_tab_button.setEnabled(not busy)
        for action in (self.new_tab_action, self.open_tab_action, self.close_tab_action): action.setEnabled(not busy)
        for action in (self.copy_tab_action, self.move_tab_action): action.setEnabled(bool(self.selected_ids()) and not busy and not self.preview.selection_pending)

    def _confirm_all_document_tabs(self, after_save=None):
        if self._autosave_job is not None:
            self._queue_tab_operation(after_save or self.close)
            return False
        if self._skip_discard_once:
            self._skip_discard_once = False; return True
        self._capture_document_tab()
        original = self._document_index
        for index, context in enumerate(self._document_tabs):
            if self._close_approved_tabs.get(context.uid) == context.workspace.snapshot() or not self._tab_dirty(context): continue
            self.switch_document_tab(index)
            if self._document_index != index: return False
            self._autosave_timer.stop()
            if not self.confirm_discard(after_save=after_save):
                if not self.job: self._close_approved_tabs.clear()
                self._session_changed()
                return False
            self._close_approved_tabs[context.uid] = context.workspace.snapshot()
        self.switch_document_tab(original)
        return True

    def enable_background_services(self, **options):
        super().enable_background_services(**options)
        self._capture_document_tab()
        for context in self._document_tabs:
            if context.session.get("_recovery_store") is None:
                try: context.session["_recovery_store"] = RecoveryStore(self._recovery_directory)
                except Exception as error: self.set_status(f"Automatische Sicherung nicht verfügbar: {error}", "warning")
        self._session_changed()

    def _tab_snapshot(self, context):
        path = context.session.get("_project_path")
        return ProjectSnapshot(context.workspace.snapshot(), context.selected, context.active_uid,
            context.zoom_mode, context.zoom_factor, str(path) if path else None)

    def _session_changed(self):
        if self._autosave_suppression: return
        if not hasattr(self, "_document_tabs"): return super()._session_changed()
        self._capture_document_tab()
        if not self._services_enabled or self._closing_requested: return
        for context in self._document_tabs:
            if context.workspace.pages or context.workspace.can_undo:
                snapshot = self._tab_snapshot(context)
                if snapshot != context.session.get("_autosave_last_key"):
                    self._autosave_timer.start(); return

    def _autosave(self):
        if self._autosave_suppression: return
        if not hasattr(self, "_document_tabs") or len(self._document_tabs) == 1:
            return super()._autosave()
        if not self._services_enabled or self._closing_requested or not self.autosave_action.isChecked(): return
        if self.job or self._autosave_job:
            self._autosave_timer.start(); return
        self._capture_document_tab()
        for context in self._document_tabs:
            if not context.workspace.pages and not context.workspace.can_undo: continue
            snapshot = self._tab_snapshot(context)
            if snapshot == context.session.get("_autosave_last_key"): continue
            password = context.session.get("_project_password")
            if snapshot_requires_password(snapshot) and not password:
                if context is self._document_tabs[self._document_index]:
                    self.set_status("Autosave pausiert: Bitte das Projekt mit Passwort speichern, damit entsperrte PDFs geschützt bleiben.", "warning")
                continue
            store = context.session.get("_recovery_store")
            if store is None: continue
            from ..app import Job
            job = Job(lambda cancel, progress: store.save(snapshot, password=password, cancel=cancel), self)
            self._autosave_job = job
            def success(result, owner=context, saved=snapshot):
                owner.session["_autosave_last_key"] = saved
                if owner is self._document_tabs[self._document_index]: self._autosave_last_key = saved
            job.succeeded.connect(success)
            job.failed.connect(lambda error: self.set_status(f"Automatische Sicherung fehlgeschlagen: {error}", "warning"))
            job.finished.connect(self._autosave_finished); job.start()
            return

    def _close_document_tab_stores(self):
        if not hasattr(self, "_document_tabs"): return
        self._capture_document_tab()
        for context in self._document_tabs:
            store = context.session.get("_recovery_store")
            if store and store is not self._recovery_store:
                try: store.close(discard=True)
                except Exception as error: self.set_status(f"Automatische Sicherung bleibt erhalten: {error}", "warning")
            context.session["_project_password"] = None
            context.session["_recovery_store"] = None

    def restore_document_tabs(self, paths):
        """Reopen updater projects sequentially; each encrypted tab asks its password."""
        paths = tuple(paths)
        if not paths: return
        if not self._queue_tab_operation(lambda: self.restore_document_tabs(paths)): return
        if self.workspace.pages or self.workspace.can_undo or self._project_path:
            if self.new_document_tab() is None: return
        self.open_project(path=paths[0], confirmed=True,
            after_open=lambda: self.restore_document_tabs(paths[1:]))

    def _install_update(self, plan):
        if len(self._document_tabs) == 1: return super()._install_update(plan)
        if not self._queue_tab_operation(lambda: self._install_update(plan)): return
        from ..update_installer import discard_install, launch_install
        self._pending_update_plan = plan
        self.update_action.setText("Vorbereitetes Update installieren …")
        self._capture_document_tab()
        original = self._document_index
        for index, context in enumerate(self._document_tabs):
            has_session = bool(context.workspace.pages or context.workspace.can_undo or context.workspace.can_redo)
            if (not has_session or self._update_discarded_tabs.get(context.uid) == context.workspace.snapshot()
                    or (context.session.get("_project_path") is not None and not self._tab_dirty(context))): continue
            self.switch_document_tab(index)
            with self._suspend_autosave():
                answer = QMessageBox.question(self, "Dokument-Tabs vor dem Update speichern?",
                    f"Tab {index + 1} enthält einen ungespeicherten Arbeitsbereich. Als Projekt speichern und nach dem Neustart wieder öffnen?",
                    QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Save)
            if answer == QMessageBox.StandardButton.Save:
                self.save_project(after_save=lambda: self._install_update(plan))
                if self.job is None: self._update_discarded_tabs.clear()
                return
            if answer != QMessageBox.StandardButton.Discard:
                discard_install(plan); self._pending_update_plan = None
                self._update_discarded_tabs.clear(); self.update_action.setText("Programm aktualisieren …")
                self.switch_document_tab(original); return
            self._update_discarded_tabs[context.uid] = context.workspace.snapshot()
        self._capture_document_tab()
        args = tuple(str(context.session["_project_path"]) for context in self._document_tabs
            if context.session.get("_project_path") is not None
            and self._update_discarded_tabs.get(context.uid) != context.workspace.snapshot())
        plan = replace(plan, restart_args=args)
        try: launch_install(plan)
        except Exception as error:
            self._update_discarded_tabs.clear()
            self.show_error("Das Update konnte nicht gestartet werden.", str(error)); return
        self._pending_update_plan = None; self._update_restart_plan = plan
        self._skip_discard_once = True; self.close()
