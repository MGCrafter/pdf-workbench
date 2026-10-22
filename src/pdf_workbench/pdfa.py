"""Local PDF/A-2b conversion, committed only after real veraPDF validation."""
from __future__ import annotations

from dataclasses import dataclass
import io
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

from pypdf import PdfReader

from .advanced_pdf import publish_new_pdf, workspace_pdf_bytes
from .core import PdfWorkbenchError, _check_cancel


@dataclass(frozen=True)
class PdfaValidation:
    compliant: bool
    flavour: str
    report: str
    failed_rules: int = 0


@dataclass(frozen=True)
class PdfaResult:
    path: Path
    validation: PdfaValidation


def discover_ghostscript(executable=None):
    if executable:
        path = Path(executable).expanduser()
        if path.is_file(): return str(path.absolute())
        found = shutil.which(str(executable))
        if found: return found
    else:
        for name in ("gs", "gswin64c", "gswin32c"):
            found = shutil.which(name)
            if found: return found
    raise PdfWorkbenchError("Ghostscript wurde nicht gefunden. Bitte ein lokal installiertes Ghostscript-Konsolenprogramm auswählen.")


def discover_verapdf(executable=None):
    if executable:
        path = Path(executable).expanduser()
        if path.is_file(): return str(path.absolute())
        found = shutil.which(str(executable))
        if found: return found
    else:
        for name in ("verapdf", "verapdf.bat"):
            found = shutil.which(name)
            if found: return found
    raise PdfWorkbenchError("veraPDF wurde nicht gefunden. Bitte die lokal installierte veraPDF-CLI auswählen. Ohne echte Validierung wird keine PDF/A-Datei gespeichert.")


def _command(executable, *arguments):
    path = Path(executable)
    if path.suffix.lower() == ".jar":
        java = shutil.which("java")
        if not java: raise PdfWorkbenchError("Für veraPDF wird eine lokale Java-Laufzeit benötigt.")
        return [java, "-jar", str(path), *map(str, arguments)]
    if path.suffix.lower() in (".bat", ".cmd"):
        # Run the installed Java CLI directly: this avoids cmd.exe interpreting
        # PDF paths or user names as shell syntax on Windows.
        jars = tuple(path.parent.glob("bin/cli-*.jar"))
        java = shutil.which("java")
        if len(jars) != 1 or not java:
            raise PdfWorkbenchError("Bitte die veraPDF-CLI mit Java installieren oder ihre ausführbare CLI-JAR auswählen.")
        return [java, "-jar", str(jars[0]), *map(str, arguments)]
    return [str(executable), *map(str, arguments)]


def _run(command, cancel=None, timeout=240):
    _check_cancel(cancel)
    # File handles avoid pipe deadlocks and unbounded in-memory process output.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            raise PdfWorkbenchError("Das lokale PDF/A-Hilfsprogramm konnte nicht gestartet werden.") from None
        start = time.monotonic()
        try:
            while process.poll() is None:
                _check_cancel(cancel)
                if time.monotonic() - start > timeout:
                    raise PdfWorkbenchError("Das lokale PDF/A-Hilfsprogramm hat die Zeitgrenze überschritten.")
                try: process.wait(timeout=.1)
                except subprocess.TimeoutExpired: pass
            _check_cancel(cancel)
            stdout.seek(0); stderr.seek(0)
            result = stdout.read(8 * 1024 * 1024 + 1)
            errors = stderr.read(1024 * 1024)
            if len(result) > 8 * 1024 * 1024:
                raise PdfWorkbenchError("Der PDF/A-Prüfbericht ist zu groß. Es wurde keine Datei gespeichert.")
            return process.returncode, result.decode("utf-8", "replace"), errors.decode("utf-8", "replace")
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired: process.kill(); process.wait()


def parse_verapdf_report(report):
    """Accept only an unambiguous, complete one-file PDF/A-2b success report."""
    try:
        root = ET.fromstring(report)
        elements = list(root.iter())
        reports = [node for node in elements if node.tag.rsplit("}", 1)[-1] == "validationReport"]
        jobs = [node for node in elements if node.tag.rsplit("}", 1)[-1] == "job"]
        exceptions = [node for node in elements if "exception" in node.tag.rsplit("}", 1)[-1].lower() and (node.text or "").strip()]
        if len(jobs) != 1 or len(reports) != 1 or exceptions: raise ValueError()
        result = reports[0]
        profile = result.get("profileName", "")
        if profile != "PDF/A-2b validation profile" or result.get("jobEndStatus") != "normal": raise ValueError()
        details = next((node for node in result.iter() if node.tag.rsplit("}", 1)[-1] == "details"), None)
        if details is None or int(details.get("passedRules", "0")) < 1: raise ValueError()
        failures = int(details.attrib["failedRules"])
        failed_checks = int(details.attrib["failedChecks"])
        if failures < 0 or failed_checks < 0 or result.get("isCompliant") not in ("true", "false"): raise ValueError()
        summaries = [node for node in elements if node.tag.rsplit("}", 1)[-1] == "batchSummary"]
        if len(summaries) != 1: raise ValueError()
        summary = summaries[0]
        if summary.get("totalJobs") != "1" or any(summary.get(key) != "0" for key in ("failedToParse", "encrypted", "outOfMemory", "veraExceptions")): raise ValueError()
        totals = [node for node in summary if node.tag.rsplit("}", 1)[-1] == "validationReports"]
        if len(totals) != 1 or totals[0].get("failedJobs") != "0" or (totals[0].text or "").strip() != "1": raise ValueError()
        compliant = result.get("isCompliant") == "true" and failures == 0 and failed_checks == 0
        if totals[0].get("compliant") != str(int(compliant)) or totals[0].get("nonCompliant") != str(int(not compliant)): raise ValueError()
        return PdfaValidation(compliant, "2b", report, failures)
    except (ET.ParseError, KeyError, TypeError, ValueError):
        raise PdfWorkbenchError("veraPDF hat keinen vollständigen eindeutigen PDF/A-2b-Prüfbericht geliefert. Es wurde keine Datei gespeichert.") from None


def validate_pdfa(path, *, verapdf=None, cancel=None):
    executable = discover_verapdf(verapdf)
    code, report, _ = _run(_command(executable, "--format", "xml", "--flavour", "2b", "--maxfailuresdisplayed", "10", str(Path(path).absolute())), cancel)
    if code != 0:
        raise PdfWorkbenchError("veraPDF konnte die Datei nicht vollständig prüfen. Es wurde keine PDF/A-Datei gespeichert.")
    return parse_verapdf_report(report)


def _icc_bytes(icc_profile):
    if icc_profile:
        try: data = Path(icc_profile).expanduser().read_bytes()
        except OSError: raise PdfWorkbenchError("Das RGB-Farbprofil konnte nicht gelesen werden.") from None
    else:
        from PIL import ImageCms
        data = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    if len(data) < 128 or data[16:20] != b"RGB " or data[36:40] != b"acsp":
        raise PdfWorkbenchError("Bitte ein gültiges RGB-ICC-Farbprofil verwenden.")
    return data


def export_pdfa(sources, pages, destination, *, ghostscript=None, verapdf=None,
                icc_profile=None, cancel=None, progress=None, bookmarks=None):
    """Convert the current workspace to 2b and require independent validation.

    Ghostscript may flatten interactive content, substitute fonts, change colors
    and discard unsupported structures. Existing signatures are not preserved.
    Originals and project history are never modified by this export.
    """
    gs = discover_ghostscript(ghostscript); validator = discover_verapdf(verapdf)
    data = workspace_pdf_bytes(sources, pages, cancel, bookmarks=bookmarks)
    icc = _icc_bytes(icc_profile)
    with tempfile.TemporaryDirectory(prefix="pdfwb-pdfa-") as folder:
        directory = Path(folder)
        source, converted = directory / "input.pdf", directory / "converted.pdf"
        profile, definition = directory / "srgb.icc", directory / "definition.ps"
        source.write_bytes(data); profile.write_bytes(icc)
        # A hex literal prevents all PostScript path injection, including on
        # Windows. The definition is authored by us, not loaded from the PDF.
        encoded_profile = os.fsencode(profile).hex()
        definition.write_text("%!PS\n/ICCProfile <" + encoded_profile + "> def\n"
            "[/_objdef {icc_PDFA} /type /stream /OBJ pdfmark\n"
            "[{icc_PDFA} << /N 3 >> /PUT pdfmark\n"
            "[{icc_PDFA} ICCProfile (r) file /PUT pdfmark\n"
            "[/_objdef {OutputIntent_PDFA} /type /dict /OBJ pdfmark\n"
            "[{OutputIntent_PDFA} << /Type /OutputIntent /S /GTS_PDFA1 /DestOutputProfile {icc_PDFA} "
            "/OutputConditionIdentifier (sRGB) >> /PUT pdfmark\n"
            "[{Catalog} << /OutputIntents [ {OutputIntent_PDFA} ] >> /PUT pdfmark\n", encoding="ascii")
        if progress: progress(35, "Ghostscript erstellt PDF/A-2b …")
        arguments = ["-dSAFER", "-dBATCH", "-dNOPAUSE", "-dPDFA=2", "-dPDFACompatibilityPolicy=2",
                     "-dAutoRotatePages=/None", "-sDEVICE=pdfwrite", "-sColorConversionStrategy=RGB",
                     "-sProcessColorModel=DeviceRGB", "-dCompatibilityLevel=1.7",
                     f"--permit-file-read={profile}", f"-sOutputFile={converted}", str(definition), str(source)]
        code, _, _ = _run(_command(gs, *arguments), cancel)
        if code != 0 or not converted.is_file():
            raise PdfWorkbenchError("Ghostscript konnte diese PDF nicht vollständig nach PDF/A-2b konvertieren. Es wurde keine Datei gespeichert.")
        before_validation = converted.lstat()
        converted_data = converted.read_bytes(); _check_cancel(cancel)
        checked = PdfReader(io.BytesIO(converted_data))
        if checked.is_encrypted or len(checked.pages) != len(pages):
            raise PdfWorkbenchError("Die PDF/A-Konvertierung hat die Seiten nicht vollständig erhalten. Es wurde keine Datei gespeichert.")
        if progress: progress(75, "veraPDF prüft die tatsächliche PDF/A-2b-Konformität …")
        validation = validate_pdfa(converted, verapdf=validator, cancel=cancel)
        if not validation.compliant:
            raise PdfWorkbenchError(f"Die Konvertierung erfüllt PDF/A-2b nicht ({validation.failed_rules} verletzte Regeln). Es wurde keine Datei gespeichert.")
        # The bytes committed below must be precisely the bytes veraPDF read.
        # Reject replacements, in-place edits and touched validation input.
        after_validation = converted.lstat()
        if (not stat.S_ISREG(after_validation.st_mode)
                or (before_validation.st_dev, before_validation.st_ino, before_validation.st_size, before_validation.st_mtime_ns)
                != (after_validation.st_dev, after_validation.st_ino, after_validation.st_size, after_validation.st_mtime_ns)
                or converted.read_bytes() != converted_data):
            raise PdfWorkbenchError("Die PDF wurde während der PDF/A-Prüfung verändert. Es wurde keine Datei gespeichert.")
        path = publish_new_pdf(converted_data, destination, sources, cancel, expected_pages=len(pages))
    if progress: progress(100, "PDF/A-2b gespeichert und mit veraPDF validiert")
    return PdfaResult(path, validation)
