"""Main-window project lifecycle, crash recovery and opt-in update installation.

Background services are explicitly enabled by the application entry point, so
embedded windows and test harnesses never inspect a user's recovery files.
"""
from dataclasses import replace
from pathlib import Path
import time

from PyQt6.QtCore import QStandardPaths, QTimer
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from .. import __version__
from ..core import Workspace
from ..projects import (ProjectSnapshot, ProjectPasswordRequired, IncorrectProjectPassword,
    RecoveryStore, list_recoveries, read_project, write_project, snapshot_requires_password)
from .project_dialogs import ProjectPasswordDialog, RecoveryDialog
from .theme import file_dialog_options


class WorkspaceSession:
    def _init_session(self):
        self._project_path = None; self._project_identity = None; self._project_password = None
        self._project_saved_key = None; self._saved_bookmarks = ()
        self._services_enabled = False; self._recovery_store = None; self._autosave_job = None
        self._autosave_last_key = None; self._discard_recovery_pending = False
        self._update_check_job = None; self._update_release = None
        self._skip_discard_once = False; self._update_restart_plan = None; self._pending_update_plan = None
        self._autosave_timer = QTimer(self); self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(2500); self._autosave_timer.timeout.connect(self._autosave)

    def _content_key(self):
        return tuple(self.workspace.pages), self.workspace.bookmarks

    def _is_dirty(self):
        if self._project_saved_key is not None: return self._content_key() != self._project_saved_key
        return self._content_key() != (self.saved_state, self._saved_bookmarks)

    def _project_snapshot(self):
        return ProjectSnapshot(self.workspace.snapshot(), tuple(page.uid for page in self.workspace.pages if page.uid in self.selected_ids()),
            self._active_uid, self.preview.zoom_mode, self.preview.zoom_factor,
            str(self._project_path) if self._project_path else None)

    def _session_changed(self):
        if self._services_enabled and not self._closing_requested and self._project_snapshot() != self._autosave_last_key:
            self._autosave_timer.start()

    def enable_background_services(self, *, data_dir=None, prompt_recovery=True, check_updates=True):
        if self._services_enabled: return
        self._services_enabled = True
        self._session_directory = Path(data_dir or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        self._recovery_directory = self._session_directory / "recovery"
        try: self._recovery_store = RecoveryStore(self._recovery_directory)
        except Exception as error: self.set_status(f"Automatische Sicherung nicht verfügbar: {error}", "warning")
        self.autosave_action.setChecked(self.theme.settings.value("projects/autosave", True, type=bool))
        self.update_controls()
        if prompt_recovery: QTimer.singleShot(0, self.recover_project)
        if check_updates: QTimer.singleShot(2000, self._check_update_background)
        self._session_changed()

    def _set_autosave(self, enabled):
        self.theme.settings.setValue("projects/autosave", enabled); self.theme.settings.sync()
        if enabled: self._session_changed()
        else: self._autosave_timer.stop()

    def _autosave(self):
        if not self._services_enabled or self._closing_requested or not self.autosave_action.isChecked() or not self._recovery_store: return
        if self.job is not None or self._autosave_job is not None:
            self._autosave_timer.start(); return
        snapshot = self._project_snapshot()
        if snapshot == self._autosave_last_key: return
        if not self.workspace.pages and not self.workspace.can_undo: return
        password = self._project_password
        if snapshot_requires_password(snapshot) and not password:
            self.set_status("Autosave pausiert: Bitte das Projekt mit Passwort speichern, damit entsperrte PDFs geschützt bleiben.", "warning"); return
        from ..app import Job
        store = self._recovery_store
        job = Job(lambda cancel, progress: store.save(snapshot, password=password, cancel=cancel), self)
        self._autosave_job = job
        job.succeeded.connect(lambda result: setattr(self, "_autosave_last_key", snapshot))
        job.failed.connect(lambda error: self.set_status(f"Automatische Sicherung fehlgeschlagen: {error}", "warning"))
        job.finished.connect(self._autosave_finished); job.start()

    def _autosave_finished(self):
        job, self._autosave_job = self._autosave_job, None
        if job: job.deleteLater()
        if self._discard_recovery_pending:
            self._discard_recovery_pending = False
            self._discard_own_recovery()
            self._autosave_last_key = None
        if self._closing_requested: self._rendering_closed()
        else: self._session_changed()

    def save_project(self, checked=False, *, save_as=False, protect=False, after_save=None):
        if self.job or self._closing_requested: return
        snapshot = self._project_snapshot(); key = self._content_key()
        path = None if save_as else self._project_path
        expected = None if save_as else self._project_identity
        if path is None:
            filename, _ = QFileDialog.getSaveFileName(self, "Arbeitsbereich als Projekt speichern", "arbeitsbereich.pdfwb", "PDF-Workbench-Projekt (*.pdfwb)", options=QFileDialog.Option.DontConfirmOverwrite | file_dialog_options())
            if not filename: return
            path = Path(filename)
            if not path.suffix: path = path.with_suffix(".pdfwb")
        password = self._project_password
        if protect or (snapshot_requires_password(snapshot) and not password):
            password = ProjectPasswordDialog.ask(self, saving=True)
            if password is None: return
        snapshot = replace(snapshot, project_path=str(path))
        def task(cancel, progress): return write_project(snapshot, path, password=password, expected=expected, cancel=cancel, progress=progress)
        def success(identity):
            if password and not self._project_password:
                # Remove earlier plaintext recovery generations after the
                # protected manual project has been committed successfully.
                if self._autosave_job:
                    self._autosave_job.cancel_event.set(); self._discard_recovery_pending = True
                else: self._discard_own_recovery()
                self._autosave_last_key = None
            self._project_path = Path(path).absolute(); self._project_identity = identity; self._project_password = password
            self._project_saved_key = key; self._update_title(); self._session_changed()
            self.set_status(f"Projekt gespeichert: {path} · PDFs und Rückgängig-Schritte sind eingebettet.")
            if after_save: self._after_job_callback = after_save
        self.start_job(task, success)

    def open_project(self, checked=False, *, path=None, recovery=False, password=None, incorrect=False, confirmed=False, after_open=None):
        if self.job or self._closing_requested: return
        if path is None:
            filename, _ = QFileDialog.getOpenFileName(self, "Projekt öffnen", "", "PDF-Workbench-Projekt (*.pdfwb)", options=file_dialog_options())
            if not filename: return
            path = Path(filename)
        if not confirmed and not self.confirm_discard(after_save=lambda: self.open_project(path=path, recovery=recovery, confirmed=True, after_open=after_open)): return
        def task(cancel, progress):
            try: return read_project(path, password=password, cancel=cancel)
            except (ProjectPasswordRequired, IncorrectProjectPassword) as error: return error
        def success(loaded):
            if isinstance(loaded, (ProjectPasswordRequired, IncorrectProjectPassword)):
                def retry():
                    value = ProjectPasswordDialog.ask(self, filename=Path(path).name, incorrect=isinstance(loaded, IncorrectProjectPassword))
                    if value is not None: self.open_project(path=path, recovery=recovery, password=value, confirmed=True, after_open=after_open)
                self._after_job_callback = retry; return
            state = loaded.snapshot
            self.workspace.restore_state(state.state)
            self._reset_project_session()
            self._project_path = None if recovery else loaded.path
            self._project_identity = None if recovery else loaded.identity
            self._project_password = password
            self._project_saved_key = None if recovery else self._content_key()
            if recovery: self.saved_state = (); self._saved_bookmarks = ()
            self._form_field_cache.clear(); self.render_broker.invalidate(clear_cache=True)
            self.refresh(state.selected_ids, state.active_uid)
            if state.zoom_mode == "width": self.preview.fit_width()
            elif state.zoom_mode == "page": self.preview.fit_page()
            else: self.preview.set_zoom(state.zoom_factor)
            self.set_status("Automatische Sicherung wiederhergestellt. Bitte als neues Projekt speichern." if recovery else f"Projekt geöffnet: {loaded.path}")
            if loaded.recovered_from: self.set_status(f"Vorige Projektversion wiederhergestellt: {loaded.recovered_from}. Bitte erneut speichern.", "warning")
            if after_open: self._after_job_callback = after_open
        self.start_job(task, success)

    def recover_project(self):
        if self.job or self._closing_requested or not self._services_enabled: return
        try: entries = list_recoveries(self._recovery_directory)
        except Exception as error: self.show_error("Sicherungen konnten nicht gelesen werden.", str(error)); return
        if not entries: return
        entry = RecoveryDialog.pick(self, entries, self._recovery_directory)
        if entry: self.open_project(path=entry.path, recovery=True)

    def _reset_project_session(self):
        self._autosave_timer.stop()
        if self._autosave_job:
            self._autosave_job.cancel_event.set(); self._discard_recovery_pending = True
        else: self._discard_own_recovery()
        self._project_path = None; self._project_identity = None; self._project_password = None
        self._project_saved_key = None; self._saved_bookmarks = (); self._autosave_last_key = None

    def _discard_own_recovery(self):
        if self._recovery_store:
            try: self._recovery_store.discard()
            except Exception as error:
                self.set_status(f"Alte automatische Sicherungen konnten nicht entfernt werden: {self._recovery_store.directory} · {error}", "warning")

    def _confirm_session_discard(self, after_save=None):
        if self._skip_discard_once:
            self._skip_discard_once = False; return True
        if not self._is_dirty(): return True
        answer = QMessageBox.question(self, "Arbeitsbereich speichern?",
            "Die aktuelle Arbeit enthält ungespeicherte Änderungen. Als Projekt speichern, einschließlich Objekten, Lesezeichen und Rückgängig-Schritten?\n\nDie Originaldateien bleiben erhalten.",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            self.save_project(after_save=after_save); return False
        return answer == QMessageBox.StandardButton.Discard

    def _stop_background_services(self):
        self._autosave_timer.stop()
        for job in (self._autosave_job, self._update_check_job):
            if job: job.cancel_event.set()

    def _background_closed(self):
        return self._autosave_job is None and self._update_check_job is None

    def _finish_session(self):
        if self._pending_update_plan is not None:
            from ..update_installer import discard_install
            try: discard_install(self._pending_update_plan)
            except Exception as error: self.set_status(f"Update-Vorbereitung konnte nicht entfernt werden: {error}", "warning")
            self._pending_update_plan = None
        try:
            if self._recovery_store: self._recovery_store.close(discard=True)
        except Exception as error:
            self.set_status(f"Automatische Sicherungen bleiben erhalten: {error}", "warning")
        finally:
            self._recovery_store = None; self._project_password = None

    def _check_update_background(self):
        from ..updates import CHECK_ENABLED_KEY, LAST_CHECK_KEY, check_latest
        settings = self.theme.settings
        if self._closing_requested or self._update_check_job or not settings.value(CHECK_ENABLED_KEY, True, type=bool): return
        try: last = float(settings.value(LAST_CHECK_KEY, 0))
        except (ValueError, TypeError): last = 0
        if 0 <= time.time() - last < 86400: return
        settings.setValue(LAST_CHECK_KEY, time.time()); settings.sync()
        from ..app import Job
        job = Job(lambda cancel, progress: check_latest(__version__, cancel=cancel), self); self._update_check_job = job
        def success(release):
            if release and not self._closing_requested:
                self._update_release = release; self.update_action.setText(f"Version {release.version} installieren …")
                self.set_status(f"Version {release.version} verfügbar · unter Hilfe → Programm aktualisieren.")
        job.succeeded.connect(success)
        job.finished.connect(self._update_check_finished); job.start()

    def _update_check_finished(self):
        job, self._update_check_job = self._update_check_job, None
        if job: job.deleteLater()
        if self._closing_requested: self._rendering_closed()

    def update_program(self):
        if self.job or self._closing_requested: return
        if self._pending_update_plan is not None:
            self._install_update(self._pending_update_plan); return
        from .update_dialog import UpdateDialog
        directory = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)) / "updates"
        plan = UpdateDialog.get_plan(self, __version__, directory, self.theme.settings, self._update_release)
        if plan is not None: self._install_update(plan)

    def _install_update(self, plan):
        self._pending_update_plan = plan
        self.update_action.setText("Vorbereitetes Update installieren …")
        # A restart must never quietly drop a workspace, even after a PDF export.
        has_session = bool(self.workspace.pages or self.workspace.can_undo or self.workspace.can_redo)
        if has_session and (self._project_path is None or self._is_dirty()):
            answer = QMessageBox.question(self, "Vor dem Update speichern?",
                "Der Neustart schließt diesen Arbeitsbereich. Als Projekt speichern und danach automatisch wieder öffnen?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)
            if answer == QMessageBox.StandardButton.Save:
                self.save_project(after_save=lambda: self._install_update(plan)); return
            if answer != QMessageBox.StandardButton.Discard:
                from ..update_installer import discard_install
                discard_install(plan); self._pending_update_plan = None
                self.update_action.setText("Programm aktualisieren …"); return
            restart_args = ()
        else: restart_args = (str(self._project_path),) if self._project_path else ()
        plan = replace(plan, restart_args=restart_args)
        try:
            from ..update_installer import launch_install
            launch_install(plan)
        except Exception as error:
            self.show_error("Das Update konnte nicht gestartet werden.", str(error)); return
        self._pending_update_plan = None; self._update_restart_plan = plan; self._skip_discard_once = True; self.close()
