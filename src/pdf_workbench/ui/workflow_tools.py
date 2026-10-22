"""MainWindow mixin. Build the menu once, update controls with busy/page state."""
from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QFileDialog

from ..batch import process_batch
from ..workflows import (export_images, export_internal_links, export_table_of_contents,
                         export_text, find_page_candidates, prepare_scans)
from .theme import file_dialog_options
from .workflow_dialogs import (BatchDialog, CandidateReviewDialog, ContentsDialog,
    DuplexDialog, InternalLinksDialog, ScanDialog, ScopeDialog)


class WorkflowTools:
    def _build_workflow_menu(self, menu):
        self.workflow_actions = []
        for text, method in (("Scans aufbereiten …", self.prepare_scans),
                             ("Leere und doppelte Seiten prüfen …", self.review_pages),
                             ("Vorder-/Rückseiten verschachteln …", self.interleave_duplex),
                             ("Seiten als PNG/JPEG exportieren …", self.export_images),
                             ("Text exportieren …", self.export_text),
                             ("Klickbares Inhaltsverzeichnis exportieren …", self.export_toc),
                             ("Interne Links exportieren …", self.export_internal_links)):
            action = menu.addAction(text); action.triggered.connect(method); self.workflow_actions.append(action)
        menu.addSeparator(); self.batch_action = menu.addAction("Stapelverarbeitung und Vorlagen …"); self.batch_action.triggered.connect(self.process_batch)

    def update_workflow_controls(self, busy, has_pages):
        for action in getattr(self, "workflow_actions", ()): action.setEnabled(not busy and has_pages)
        if hasattr(self, "batch_action"): self.batch_action.setEnabled(not busy)

    def _workflow_pages(self, scope="all"):
        selected = set(self.selected_ids()) if scope == "selected" else None
        return tuple(page for page in self.workspace.pages if selected is None or page.uid in selected)

    def _workflow_save(self, title, name, file_filter="PDF-Dateien (*.pdf)", suffix=".pdf"):
        path, _ = QFileDialog.getSaveFileName(self, title, name, file_filter, options=file_dialog_options())
        return path + suffix if path and not path.lower().endswith(suffix) else path

    def export_images(self, checked=False):
        if self.job or not self.workspace.pages: return
        dialog = ScopeDialog(self, "Seiten als Bilder exportieren", len(self.workspace.pages), len(self.selected_ids()), image=True)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        directory = QFileDialog.getExistingDirectory(self, "Zielordner für Seitenbilder", options=file_dialog_options())
        if not directory: return
        sources = dict(self.workspace.sources); pages = self._workflow_pages(dialog.scope.currentData())
        options = dict(image_format=dialog.format.currentData(), dpi=dialog.dpi.value(), quality=dialog.quality.value(), prefix=dialog.prefix.text().strip())
        self.start_job(lambda cancel, progress: export_images(sources, pages, directory, cancel=cancel, progress=progress, **options),
                       lambda paths: self.set_status(f"{len(paths)} Seitenbilder gespeichert.", "success"))

    def export_text(self, checked=False):
        if self.job or not self.workspace.pages: return
        dialog = ScopeDialog(self, "Text exportieren", len(self.workspace.pages), len(self.selected_ids()))
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        path = self._workflow_save("Text als neue Datei speichern", "Dokument.txt", "Textdateien (*.txt)", ".txt")
        if not path: return
        sources = dict(self.workspace.sources); pages = self._workflow_pages(dialog.scope.currentData())
        self.start_job(lambda cancel, progress: export_text(sources, pages, path, cancel=cancel, progress=progress),
                       lambda output: self.set_status(f"Text gespeichert: {output.name}", "success"))

    def review_pages(self, checked=False):
        if self.job or not self.workspace.pages: return
        sources = dict(self.workspace.sources); pages = tuple(self.workspace.pages)
        def success(candidates):
            if not candidates: self.set_status("Keine leeren oder visuell gleichen Seiten gefunden.", "success"); return
            dialog = CandidateReviewDialog(self, candidates)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                ids = dialog.selected_ids; self.workspace.delete(ids); self.refresh()
                self.set_status(f"{len(ids)} geprüfte Seiten entfernt. Rückgängig ist verfügbar.", "success")
        self.start_job(lambda cancel, progress: find_page_candidates(sources, pages, cancel=cancel, progress=progress), success)

    def prepare_scans(self, checked=False):
        if self.job or not self.workspace.pages: return
        dialog = ScanDialog(self, len(self.workspace.pages), len(self.selected_ids()))
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        sources = dict(self.workspace.sources); pages = tuple(self.workspace.pages); targets = tuple(page.uid for page in self._workflow_pages(dialog.scope.currentData())); options = dialog.options
        def success(result):
            self.workspace.apply_page_replacements(result.sources, result.pages); self.refresh(selected=targets)
            self.set_status(f"{result.processed} Scanseiten als Bildseiten übernommen. Rückgängig ist verfügbar.", "success")
            if result.warnings: self.show_error("Hinweise zur Scanausrichtung", "\n".join(result.warnings[:20]) + ("\nWeitere Hinweise wurden gekürzt." if len(result.warnings) > 20 else ""))
        self.start_job(lambda cancel, progress: prepare_scans(sources, pages, options, targets, cancel, progress), success)

    def interleave_duplex(self, checked=False):
        if self.job or not self.workspace.pages: return
        dialog = DuplexDialog(self, self.workspace.pages)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        self.workspace._set(dialog.ordered_pages); self.refresh(); self.set_status("Vorder- und Rückseiten verschachtelt. Rückgängig ist verfügbar.", "success")

    def export_toc(self, checked=False):
        if self.job or not self.workspace.pages: return
        if not self.workspace.bookmarks: self.set_status("Bitte zuerst Lesezeichen für das Inhaltsverzeichnis anlegen."); return
        dialog = ContentsDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        path = self._workflow_save("PDF mit Inhaltsverzeichnis speichern", "Dokument-mit-Inhalt.pdf")
        if not path: return
        sources = dict(self.workspace.sources); pages = tuple(self.workspace.pages); bookmarks = self.workspace.bookmarks; title = dialog.title.text().strip()
        self.start_job(lambda cancel, progress: export_table_of_contents(sources, pages, path, bookmarks, title=title, cancel=cancel, progress=progress),
                       lambda output: self.set_status(f"PDF mit klickbarem Inhaltsverzeichnis gespeichert: {output.name}", "success"))

    def export_internal_links(self, checked=False):
        if self.job or not self.workspace.pages: return
        dialog = InternalLinksDialog(self, self.workspace)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        path = self._workflow_save("PDF mit internen Links speichern", "Dokument-mit-Links.pdf")
        if not path: return
        sources = dict(self.workspace.sources); pages = tuple(self.workspace.pages); bookmarks = self.workspace.bookmarks; links = tuple(dialog.links)
        self.start_job(lambda cancel, progress: export_internal_links(sources, pages, path, links, bookmarks=bookmarks, cancel=cancel, progress=progress),
                       lambda output: self.set_status(f"PDF mit {len(links)} internen Links gespeichert: {output.name}", "success"))

    def process_batch(self, checked=False):
        if self.job: return
        dialog = BatchDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        paths = dialog.paths; directory = dialog.directory.text(); preset = dialog.preset
        def success(result):
            self.set_status(f"{len(result.outputs)} PDFs mit Vorlage „{preset.name}“ gespeichert.", "success")
            if result.warnings: self.show_error("Hinweise zur Stapelverarbeitung", "\n".join(result.warnings[:20]))
        self.start_job(lambda cancel, progress: process_batch(paths, directory, preset, cancel=cancel, progress=progress), success)
