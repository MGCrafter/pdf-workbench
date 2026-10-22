"""Main-window integration for advanced content and certificate tools."""
from pathlib import Path

from PyQt6.QtWidgets import QFileDialog

from ..core import PdfWorkbenchError
from .theme import file_dialog_options


class AdvancedTools:
    def _build_advanced_menu(self, menu):
        self.redact_action = self._action(menu, "Inhalte dauerhaft schwärzen …", None, self.redact_pdf, "lock")
        self.existing_text_action = self._action(menu, "Bestehenden PDF-Text bearbeiten …", None, self.edit_existing_text, "document")
        menu.addSeparator()
        self.certificate_sign_action = self._action(menu, "Mit Zertifikat signierte PDF speichern …", None, self.sign_pdf_certificate, "lock")
        self.certificate_verify_action = self._action(menu, "Zertifikatssignaturen einer Datei prüfen …", None, self.verify_pdf_signatures, "document")
        menu.addSeparator()
        self.pdfa_action = self._action(menu, "Validierte PDF/A-2b speichern …", None, self.export_pdfa, "export")

    def update_advanced_controls(self, busy, has_pages):
        for name in ("redact_action", "existing_text_action", "certificate_sign_action", "pdfa_action"):
            action = getattr(self, name, None)
            if action is not None: action.setEnabled(has_pages and not busy)
        action = getattr(self, "certificate_verify_action", None)
        if action is not None: action.setEnabled(not busy)

    def _advanced_ready(self):
        return not self.job and bool(self.workspace.pages) and not self.preview.selection_pending

    def _advanced_destination(self, title, suggestion):
        filename, _ = QFileDialog.getSaveFileName(self, title, suggestion, "PDF-Dateien (*.pdf)",
            options=QFileDialog.Option.DontConfirmOverwrite | file_dialog_options())
        if not filename: return None
        path = Path(filename)
        return path if path.suffix else path.with_suffix(".pdf")

    def redact_pdf(self):
        if not self._advanced_ready(): return
        from .advanced_dialogs import RedactionDialog
        from ..advanced_pdf import export_redacted_pdf
        try: request = RedactionDialog.get_request(self, self.workspace, self._active_uid)
        except PdfWorkbenchError as error:
            self.show_error("Die Schwärzung konnte nicht vorbereitet werden.", str(error)); return
        if request is None: return
        destination = self._advanced_destination("Neue dauerhaft geschwärzte Bild-PDF speichern", "geschwaerzt.pdf")
        if destination is None: return
        sources, pages = dict(self.workspace.sources), tuple(self.workspace.pages)
        def task(cancel, progress):
            return export_redacted_pdf(sources, pages, destination, request.regions, confirmed=True,
                                       dpi=request.dpi, cancel=cancel, progress=progress)
        def success(result):
            self.set_status(f"Geschwärzte Bild-PDF gespeichert: {result.path} · {result.regions} Bereich(e). Originale, Projekte und Sicherungen bleiben ungeschwärzt und müssen getrennt geschützt werden.", "warning")
        self.start_job(task, success)

    def edit_existing_text(self):
        if not self._advanced_ready() or self._active_uid not in self._page_by_uid: return
        from .advanced_dialogs import ExistingTextDialog
        from ..advanced_pdf import prepare_text_replacement
        page = self._page_by_uid[self._active_uid]; source = self.workspace.sources[page.source_id]
        try: request = ExistingTextDialog.get_request(self, source, page.page_index)
        except PdfWorkbenchError as error:
            self.show_error("Der bestehende Text konnte nicht gelesen werden.", str(error)); return
        if request is None: return
        sources, pages = dict(self.workspace.sources), tuple(self.workspace.pages)
        selected, active = self.selected_ids(), self._active_uid
        def task(cancel, progress):
            return prepare_text_replacement(sources, pages, page.uid, request[0], request[1], cancel, progress)
        def success(result):
            self.workspace.apply_page_replacements(result.sources, result.pages); self.refresh(selected, active)
            self.set_status("Bestehender Text wurde ersetzt. Als neue PDF speichern; Rückgängig ist verfügbar. Originale und Projektverlauf enthalten weiterhin den alten Text.")
        self.start_job(task, success)

    def sign_pdf_certificate(self):
        if not self._advanced_ready(): return
        from .advanced_dialogs import CertificateDialog
        from ..security_signatures import sign_pdf_certificate
        request = CertificateDialog.get_request(self)
        if request is None: return
        destination = self._advanced_destination("Neue mit Zertifikat signierte PDF speichern", "signiert.pdf")
        if destination is None: return
        sources, pages, bookmarks = dict(self.workspace.sources), tuple(self.workspace.pages), self.workspace.bookmarks
        # The mutable container is cleared even if signing fails or is cancelled;
        # jobs do not retain the certificate passphrase after completion.
        secrets = list(request); request = None
        def task(cancel, progress):
            try:
                return sign_pdf_certificate(sources, pages, destination, secrets[0], secrets[1], reason=secrets[2],
                                            cancel=cancel, progress=progress, bookmarks=bookmarks)
            finally: secrets.clear()
        def success(result):
            self.set_status(f"Signierte PDF gespeichert: {result.path} · Kryptografische Integrität bestätigt. Zertifikatsvertrauen und Sperrstatus wurden nicht bestätigt.", "warning")
        self.start_job(task, success)

    def verify_pdf_signatures(self):
        if self.job: return
        from .advanced_dialogs import SignatureReportDialog, SignatureVerificationDialog
        from ..security_signatures import verify_pdf_signatures
        request = SignatureVerificationDialog.get_request(self)
        if request is None: return
        path, roots = request
        def task(cancel, progress): return verify_pdf_signatures(path, trust_roots=roots, cancel=cancel, progress=progress)
        def success(statuses):
            def show_report():
                dialog = SignatureReportDialog(self, statuses, path); dialog.exec(); dialog.deleteLater()
            self._after_job_callback = show_report
            self.set_status(f"{len(statuses)} Zertifikatssignatur(en) offline geprüft. Vertrauenskette und Integrität stehen im Prüfbericht.")
        self.start_job(task, success)

    def export_pdfa(self):
        if not self._advanced_ready(): return
        from .advanced_dialogs import PdfaDialog
        from ..pdfa import export_pdfa
        request = PdfaDialog.get_request(self)
        if request is None: return
        destination = self._advanced_destination("Neue mit veraPDF validierte PDF/A-2b speichern", "archiv-pdfa.pdf")
        if destination is None: return
        sources, pages, bookmarks = dict(self.workspace.sources), tuple(self.workspace.pages), self.workspace.bookmarks
        def task(cancel, progress):
            return export_pdfa(sources, pages, destination, ghostscript=request[0], verapdf=request[1],
                               cancel=cancel, progress=progress, bookmarks=bookmarks)
        def success(result): self.set_status(f"PDF/A-2b gespeichert: {result.path} · Tatsächliche Konformität mit veraPDF bestätigt. Bitte die Darstellung vor der Archivierung prüfen.")
        self.start_job(task, success)


# Keep the explicit name available to integrations written during parallel work.
AdvancedToolsMixin = AdvancedTools
