"""Page selection and safe exports of individual workspace pages."""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import unicodedata
from typing import Mapping, Sequence

from .core import (
    CancelCheck, OperationCancelled, PageRef, PdfWorkbenchError, Progress,
    SourceDocument, export_pdf,
)


def parse_page_selection(text: str, page_count: int) -> list[int]:
    """Parse one-based ranges and return unique zero-based workspace indices."""
    if page_count < 1:
        raise PdfWorkbenchError("Der Arbeitsbereich enthält keine Seiten.")
    if not text.strip():
        raise PdfWorkbenchError("Bitte gib mindestens eine Seitennummer ein.")
    selected: set[int] = set()
    normalized = text.replace("–", "-").replace("—", "-")
    for token in re.split(r"[,;]", normalized):
        match = re.fullmatch(r"\s*([0-9]+)\s*(?:-\s*([0-9]+)\s*)?", token)
        if not match:
            raise PdfWorkbenchError(f"Ungültige Seitenauswahl „{token.strip()}“. Beispiel: 1-3, 5.")
        try:
            first = int(match[1])
            last = int(match[2]) if match[2] is not None else first
        except ValueError as exc:
            raise PdfWorkbenchError("Eine Seitennummer ist zu lang oder ungültig.") from exc
        if first < 1 or last < 1:
            raise PdfWorkbenchError("Seitennummern beginnen bei 1; Seite 0 ist ungültig.")
        if first > page_count or last > page_count:
            raise PdfWorkbenchError(f"Die Auswahl liegt außerhalb des Arbeitsbereichs mit {page_count} Seiten.")
        if first > last:
            raise PdfWorkbenchError(f"Der Seitenbereich {first}-{last} ist absteigend. Bitte beginne mit der kleineren Nummer.")
        selected.update(range(first - 1, last))
    return sorted(selected)


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(value.st_mode):
        return None
    return value.st_dev, value.st_ino


def _filename(index: int, count: int, source: SourceDocument, page: PageRef) -> str:
    stem = unicodedata.normalize("NFKC", source.path.stem)
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", stem).strip(" .")[:40] or "Dokument"
    return f"{index + 1:0{max(3, len(str(count)))}d}_{stem}_Seite_{page.page_index + 1:03d}.pdf"


def export_pages_separately(
    sources: Mapping[str, SourceDocument],
    pages: Sequence[PageRef],
    destination_directory: str | Path,
    cancel: CancelCheck | None = None,
    progress: Progress | None = None,
) -> Path:
    """Create an exclusively new folder containing one PDF for each page.

    On failure, remove only files created by this operation whose identity is
    unchanged. Never recursively delete a directory or remove foreign files.
    """
    if not pages:
        raise PdfWorkbenchError("Bitte wähle mindestens eine Seite aus.")
    requested = Path(destination_directory).expanduser().absolute()
    if os.path.lexists(requested):
        raise PdfWorkbenchError(f"Der Zielordner existiert bereits: {requested}. Bitte wähle einen neuen Namen.")
    try:
        target = requested.resolve()
        if os.path.lexists(target):
            raise PdfWorkbenchError(f"Der Zielordner existiert bereits: {target}.")
        if any(target == source.path.resolve() for source in sources.values()):
            raise PdfWorkbenchError(f"Eine Quelldatei darf nicht als Zielordner verwendet werden: {target}.")
        filenames = []
        for index, page in enumerate(pages):
            source = sources.get(page.source_id)
            if source is None or not 0 <= page.page_index < source.page_count or page.rotation % 90:
                raise PdfWorkbenchError(f"Die ausgewählte Arbeitsseite {index + 1} ist ungültig.")
            filename = _filename(index, len(pages), source, page)
            output = target / filename
            if os.path.lexists(output) or any(output.resolve() == item.path.resolve() for item in sources.values()):
                raise PdfWorkbenchError(f"Das geplante Seitenziel ist bereits belegt oder eine Quelle: {output}.")
            filenames.append(filename)
        if cancel and cancel():
            raise OperationCancelled("Einzelexport abgebrochen. Es wurde kein Zielordner angelegt.")
        target.mkdir(exist_ok=False)
    except (PdfWorkbenchError, OperationCancelled):
        raise
    except OSError as exc:
        raise PdfWorkbenchError(f"Der neue Zielordner konnte nicht angelegt werden: {requested}: {exc}") from exc

    directory_identity = _identity(target)
    created: dict[Path, tuple[int, int]] = {}
    current: Path | None = None
    try:
        for index, (page, filename) in enumerate(zip(pages, filenames)):
            if _identity(target) != directory_identity:
                raise PdfWorkbenchError(f"Der Zielordner wurde während des Exports verändert: {target}.")
            if cancel and cancel():
                raise OperationCancelled("Einzelexport abgebrochen.")
            current = target / filename

            def page_progress(value: int, message: str) -> None:
                if value == 100:
                    identity = _identity(current)
                    if identity is not None:
                        created[current] = identity
                if progress:
                    progress(min(99, int((index * 100 + value) / len(pages))), f"Arbeitsseite {index + 1} von {len(pages)}: {message}")

            export_pdf(sources, [page], current, cancel=cancel, progress=page_progress)
            if cancel and cancel():
                raise OperationCancelled("Einzelexport abgebrochen.")
        if progress:
            progress(100, f"{len(pages)} Einzel-PDFs gespeichert")
        if cancel and cancel():
            raise OperationCancelled("Einzelexport abgebrochen.")
        return target
    except Exception as exc:
        cleanup_errors = []
        same_directory = _identity(target) == directory_identity
        for output, identity in created.items():
            if same_directory and _identity(output) == identity:
                try:
                    output.unlink()
                except OSError:
                    cleanup_errors.append(str(output))
            elif _identity(output) == identity:
                cleanup_errors.append(str(output))
        if same_directory:
            try:
                target.rmdir()  # Removes an empty directory only.
            except OSError:
                cleanup_errors.append(str(target))
        else:
            cleanup_errors.append(str(target))
        remaining = [str(output) for output, identity in created.items() if _identity(output) == identity]
        detail = f"Einzelexport {'abgebrochen' if isinstance(exc, OperationCancelled) else 'fehlgeschlagen'}"
        if current is not None:
            detail += f" bei {current}"
        if not same_directory:
            detail += f". Der Zielordner wurde verschoben oder ersetzt; der Verbleib der {len(created)} bereits erzeugten eigenen PDF-Datei(en) ist unbekannt"
            if created:
                detail += ". Zuletzt bekannte Pfade: " + "; ".join(str(path) for path in created)
        else:
            detail += f". {len(remaining)} eigene PDF-Datei(en) verblieben"
            if remaining:
                detail += ": " + "; ".join(remaining)
        detail += "."
        if cleanup_errors:
            detail += " Nicht vollständig bereinigt (fremde Dateien bleiben unverändert): " + "; ".join(dict.fromkeys(cleanup_errors)) + "."
        if not isinstance(exc, OperationCancelled):
            detail += f" Ursache: {exc}"
        error_type = OperationCancelled if isinstance(exc, OperationCancelled) else PdfWorkbenchError
        raise error_type(detail) from exc
