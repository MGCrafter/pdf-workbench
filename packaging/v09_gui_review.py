"""Native v0.9 dialog/job/tab acceptance, without network or real updates."""
from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import threading
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'packaging')]


def v09_acceptance(window, directory, app, wait_for, fixture, *, verapdf=None, ghostscript=None):
    from PyQt6.QtCore import QCoreApplication, QEvent, Qt
    from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QFileDialog, QMessageBox
    from pypdf import PdfReader
    from pdf_workbench.core import export_pdf
    from pdf_workbench.ui.advanced_dialogs import (CertificateDialog, ExistingTextDialog, PdfaDialog, RedactionDialog, SignatureReportDialog, SignatureVerificationDialog)
    from pdf_workbench.ui.workflow_dialogs import (BatchDialog, CandidateReviewDialog, ContentsDialog, DuplexDialog, InternalLinksDialog, ScanDialog, ScopeDialog)
    from v09_review import certificate

    checks = []
    directory = Path(directory).resolve()
    outdir = directory / 'v09'; outdir.mkdir(exist_ok=False)
    source = outdir / 'native-source.pdf'; fixture(source, ('A1', 'A2'))
    original = source.read_bytes()
    errors = []

    def complete():
        wait_for(lambda: window.job is None)
        if errors: raise AssertionError('Unexpected UI error: ' + repr(errors))

    def snapshot(dialog, name):
        dialog.show(); dialog.resize(800, 560); app.processEvents()
        assert dialog.grab().save(str(outdir / (name + '.png')))

    def accept(dialog, name):
        snapshot(dialog, name); dialog.accept(); return QDialog.DialogCode.Accepted

    with patch.object(window, 'show_error', side_effect=lambda title, detail: errors.append((title, detail))), patch('pdf_workbench.ui.workflow_dialogs.QSettings', return_value=window.theme.settings), patch('pdf_workbench.ui.advanced_dialogs.QSettings', return_value=window.theme.settings):
        # Save two independent sessions and exercise the actual menu/callback paths.
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Discard): window.new_workspace()
        window.import_files([str(source)]); complete()
        tab0 = window._document_index; first_ws = window.workspace; ids = tuple(p.uid for p in first_ws.pages)
        first_ws.rotate([ids[-1]], 90); window.refresh(ids, ids[-1]); window.preview.set_zoom(1.6)
        first_project = outdir / 'first.pdfwb'
        with patch('pdf_workbench.ui.workspace_session.QFileDialog.getSaveFileName', return_value=(str(first_project), '')): window.save_project()
        complete(); first_identity = window._project_identity
        window.new_tab_action.trigger(); tab1 = window._document_index
        assert tab1 != tab0 and not window.workspace.pages and window._project_path is None
        window.import_files([str(source)]); complete(); second_ws = window.workspace
        second_uid = second_ws.pages[0].uid; window.refresh([second_uid], second_uid)
        window.preview.set_zoom(.75)
        window.copy_pages_to_tab(target_index=tab0, move=True)
        assert len(second_ws.pages) == 1 and len(first_ws.pages) == 3
        second_ws.undo(); assert len(second_ws.pages) == 2
        window.switch_document_tab(tab0)
        assert window.workspace is first_ws and window._project_identity == first_identity and window._project_path == first_project
        assert abs(window.preview.zoom_factor - 1.6) < .001
        first_ws.undo(); assert len(first_ws.pages) == 2
        assert first_ws.pages[-1].rotation == 90
        window.switch_document_tab(tab1); assert window.workspace is second_ws and abs(window.preview.zoom_factor - .75) < .001
        checks.append('tabs isolate project identity, zoom, history and reversible cross-tab move')

        # The worker actually runs on QThread; tab guards must protect live state.
        release = threading.Event()
        def held(cancel, progress):
            while not release.wait(.01):
                if cancel(): return None
            return None
        window.start_job(held, lambda result: None)
        current = window._document_index; count = len(window._document_tabs)
        window.switch_document_tab(tab0); window.new_document_tab(); window.close_document_tab(tab0)
        assert window._document_index == current and len(window._document_tabs) == count
        release.set(); complete()
        checks.append('actual background job blocks tab switch/create/close')

        # The updater is mocked at its launch boundary; no app installation is touched.
        @dataclass(frozen=True)
        class Plan:
            restart_args: tuple = ()
        window._document_tabs[tab0].workspace.rotate([ids[0]], 90)
        with patch('pdf_workbench.update_installer.launch_install', side_effect=RuntimeError('deliberate review failure')), patch.object(window, 'show_error'), patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Discard) as question:
            window._install_update(Plan()); assert question.call_count >= 2
            window.switch_document_tab(tab0); window.workspace.rotate([ids[0]], 90); window.refresh()
            question.reset_mock(); window._install_update(Plan()); assert question.call_count >= 1
        window._pending_update_plan = None; window._update_discarded_tabs.clear(); window.update_action.setText('Programm aktualisieren …')
        checks.append('failed update launch never reuses stale discard approval after edits')

        # Close only own additional test tab; cancel must retain the target first.
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Cancel): window.close_document_tab(tab1)
        assert len(window._document_tabs) == count
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Discard): window.close_document_tab(tab1)
        assert len(window._document_tabs) == count - 1
        window.switch_document_tab(tab0)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Discard): window.new_workspace()
        window.import_files([str(source)]); complete(); uid = window.workspace.pages[0].uid
        window.refresh([uid], uid)
        checks.append('cancel target-tab close retains pages; confirmed close removes only target')

        def text_dialog(dialog):
            row = next(i for i, run in enumerate(dialog.runs) if run.text == 'A1')
            dialog.list.setCurrentRow(row); dialog.replacement.setText('Z1')
            return accept(dialog, 'existing-text')
        before_text = tuple(window.workspace.pages)
        with patch.object(ExistingTextDialog, 'exec', text_dialog): window.existing_text_action.trigger()
        complete(); changed = outdir / 'changed.pdf'; export_pdf(window.workspace.sources, window.workspace.pages, changed)
        assert 'Z1' in PdfReader(changed).pages[0].extract_text(); assert 'A1' not in PdfReader(changed).pages[0].extract_text()
        window.undo(); assert tuple(window.workspace.pages) == before_text
        checks.append('existing-text real dialog edits isolated source and undo remains available')

        def redaction_dialog(dialog):
            dialog.show(); wait_for(lambda: dialog.worker is None and dialog._ready_uid is not None)
            for spin, value in zip(dialog.coordinates, (5, 2, 95, 20)): spin.setValue(value)
            dialog.add_button.click(); dialog.confirm.setChecked(True); dialog.dpi.setValue(100)
            assert dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
            return accept(dialog, 'redaction')
        redacted = outdir / 'redacted.pdf'
        with patch.object(RedactionDialog, 'exec', redaction_dialog), patch.object(QFileDialog, 'getSaveFileName', return_value=(str(redacted), '')): window.redact_action.trigger()
        complete(); assert redacted.exists() and not PdfReader(redacted).pages[0].extract_text()
        checks.append('redaction real preview, explicit review checkbox and physical removal export')

        def images_dialog(dialog):
            dialog.scope.setCurrentIndex(dialog.scope.findData('all')); dialog.dpi.setValue(72); dialog.prefix.setText('Native')
            return accept(dialog, 'image-export')
        with patch.object(ScopeDialog, 'exec', images_dialog), patch.object(QFileDialog, 'getExistingDirectory', return_value=str(outdir)): window.export_images()
        complete(); assert len(list(outdir.glob('Native-*.png'))) == 2
        textfile = outdir / 'native.txt'
        with patch.object(ScopeDialog, 'exec', lambda dialog: accept(dialog, 'text-export')), patch.object(QFileDialog, 'getSaveFileName', return_value=(str(textfile), '')): window.export_text()
        complete(); assert 'A1' in textfile.read_text()
        checks.append('image and UTF8 text exports use real scope dialogs and jobs')

        def scan_dialog(dialog):
            dialog.dpi.setValue(72); dialog.deskew.setChecked(False); dialog.rotation.setCurrentIndex(1)
            return accept(dialog, 'scan')
        before = tuple(window.workspace.pages)
        with patch.object(ScanDialog, 'exec', scan_dialog): window.prepare_scans()
        complete(); assert window.workspace.pages[0] != before[0] and window.workspace.pages[1] == before[1]
        window.undo(); assert tuple(window.workspace.pages) == before
        checks.append('scan native settings, selected-page replacement and one undo')

        def duplex_dialog(dialog):
            dialog.rotation.setCurrentIndex(2); return accept(dialog, 'duplex')
        with patch.object(DuplexDialog, 'exec', duplex_dialog): window.interleave_duplex()
        assert window.workspace.pages[1].rotation == 180; window.undo()
        checks.append('duplex native preview and final back rotation')

        window.import_files([str(source)]); complete(); before_review = tuple(window.workspace.pages)
        def candidate_dialog(dialog):
            assert dialog.list.count() >= 2 and not dialog.selected_ids
            dialog.list.item(0).setCheckState(Qt.CheckState.Checked)
            return accept(dialog, 'candidate-review')
        with patch.object(CandidateReviewDialog, 'exec', candidate_dialog): window.review_pages(); complete()
        assert len(window.workspace.pages) == len(before_review) - 1
        window.undo(); assert tuple(window.workspace.pages) == before_review
        # Remove only the imported test duplicate in one normal workspace step.
        window.workspace.delete([page.uid for page in window.workspace.pages[2:]]); window.refresh()
        checks.append('manual blank/duplicate review starts unchecked and deletion has undo')

        window.workspace.add_bookmark('Kapitel Ä', window.workspace.pages[1].uid); window.refresh()
        toc = outdir / 'contents.pdf'
        with patch.object(ContentsDialog, 'exec', lambda dialog: accept(dialog, 'contents')), patch.object(QFileDialog, 'getSaveFileName', return_value=(str(toc), '')): window.export_toc()
        complete(); assert len(PdfReader(toc).pages) == 3
        linked = outdir / 'linked.pdf'
        def links_dialog(dialog):
            assert not dialog.preview.image.isNull(); dialog.apply.click(); assert len(dialog.links) == 1
            return accept(dialog, 'internal-links')
        with patch.object(InternalLinksDialog, 'exec', links_dialog), patch.object(QFileDialog, 'getSaveFileName', return_value=(str(linked), '')): window.export_internal_links()
        complete(); assert len(PdfReader(linked).pages[0].get('/Annots', [])) == 1
        checks.append('contents and internal-link actual dialogs create PDF navigation')

        def batch_dialog(dialog):
            dialog.files.addItem(str(source)); dialog.directory.setText(str(outdir)); dialog.name.setText('NativeReview'); dialog.rotation.setCurrentIndex(1)
            dialog._save(); assert dialog.saved.findData('NativeReview') >= 0
            snapshot(dialog, 'batch-files'); dialog.tabs.setCurrentIndex(1)
            return accept(dialog, 'batch-options')
        with patch.object(BatchDialog, 'exec', batch_dialog): window.batch_action.trigger()
        complete(); assert PdfReader(outdir / 'native-source-bearbeitet.pdf').pages[0].rotation == 90
        checks.append('batch native files/options, remembered template and actual export')

        pfx = outdir / 'test-only.pfx'; certificate(pfx, 'native-review-only')
        signed = outdir / 'signed.pdf'
        def certificate_dialog(dialog):
            dialog.certificate.setText(str(pfx)); dialog.password.setText('native-review-only'); dialog.reason.setText('Native acceptance only')
            result = accept(dialog, 'certificate'); assert not dialog.password.text(); return result
        with patch.object(CertificateDialog, 'exec', certificate_dialog), patch.object(QFileDialog, 'getSaveFileName', return_value=(str(signed), '')): window.certificate_sign_action.trigger()
        complete(); assert signed.exists() and b'/ByteRange' in signed.read_bytes()
        checks.append('certificate native password dialog, cleared secret and real PKCS12 signing')

        reports = []
        def verification_dialog(dialog):
            dialog.document.setText(str(signed)); return accept(dialog, 'signature-verification')
        def report_dialog(dialog):
            from PyQt6.QtWidgets import QPlainTextEdit
            text = dialog.findChild(QPlainTextEdit).toPlainText()
            reports.append(text); snapshot(dialog, 'signature-report'); dialog.reject(); return QDialog.DialogCode.Rejected
        with patch.object(SignatureVerificationDialog, 'exec', verification_dialog), patch.object(SignatureReportDialog, 'exec', report_dialog):
            window.certificate_verify_action.trigger(); complete(); wait_for(lambda: bool(reports))
        assert 'bestanden' in reports[0] and 'Vertrauenskette' in reports[0]
        checks.append('signature native original-file verification and integrity/trust report')

        if verapdf and ghostscript:
            archive = outdir / 'archive.pdf'
            def pdfa_dialog(dialog):
                dialog.paths[0].setText(ghostscript); dialog.paths[1].setText(verapdf)
                return accept(dialog, 'pdfa')
            with patch.object(PdfaDialog, 'exec', pdfa_dialog), patch.object(QFileDialog, 'getSaveFileName', return_value=(str(archive), '')): window.pdfa_action.trigger()
            complete(); assert archive.exists()
            checks.append('native PDF/A dialog and actual Ghostscript plus veraPDF validated export')
        else:
            dialog = PdfaDialog(window); dialog.paths[0].setText(str(outdir / 'missing-gs')); dialog.paths[1].setText(str(outdir / 'missing-validator'))
            dialog.accept(); assert dialog.request is None and dialog.status.text(); snapshot(dialog, 'pdfa-missing'); dialog.reject(); dialog.deleteLater()
            checks.append('PDF/A missing external tools guard shown; conversion not exercised here')

        # Native screenshots in both themes with a real rendered PDF and tabbar.
        window.resize(800, 560); window.refresh()
        wait_for(lambda: not window.preview.image.isNull())
        for theme in ('light', 'dark'):
            window.theme.set_preference(theme); app.processEvents(); window.grab().save(str(outdir / ('workspace-' + theme + '.png')))
        assert source.read_bytes() == original
        window.saved_state = tuple(window.workspace.pages); window._saved_bookmarks = window.workspace.bookmarks; window._project_saved_key = window._content_key()
        window._capture_document_tab()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); app.processEvents()
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True); parser.add_argument('--verapdf'); parser.add_argument('--ghostscript')
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=False)
    from PyQt6.QtCore import QSettings, qVersion
    from PyQt6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(args.output / 'settings'))
    from pdf_workbench.app import MainWindow
    from v09_review import fixture
    window = MainWindow(); window.show(); report = {'result': 'RUNNING', 'os': platform.platform(), 'python': sys.version, 'qt_platform': app.platformName(), 'qt_runtime': qVersion(), 'source_sha256': {str(p.relative_to(ROOT)).replace(os.sep, '/'): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT / 'src').rglob('*.py'))}}
    def wait_for(predicate, timeout=45):
        end = time.monotonic() + timeout
        while not predicate() and time.monotonic() < end: app.processEvents(); time.sleep(.005)
        app.processEvents()
        if not predicate(): raise AssertionError('Native v09 GUI timed out')
    start = time.monotonic()
    with (args.output / 'gui.log').open('w', encoding='utf-8') as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        try:
            report['checks'] = v09_acceptance(window, args.output, app, wait_for, fixture, verapdf=args.verapdf, ghostscript=args.ghostscript)
            report['result'] = 'PASS'
        except BaseException: report.update(result='FAIL', error=traceback.format_exc()); traceback.print_exc()
        finally:
            if window.job: window.job.cancel_event.set(); wait_for(lambda: window.job is None)
            with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Discard): window.close(); wait_for(lambda: not window.isVisible())
    report['elapsed_seconds'] = time.monotonic() - start
    (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f"{report['result']}: {args.output / 'result.json'}"); return 0 if report['result'] == 'PASS' else 1


if __name__ == '__main__': raise SystemExit(main())
