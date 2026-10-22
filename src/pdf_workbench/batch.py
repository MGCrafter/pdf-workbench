"""Reusable validated batch recipes; originals and existing outputs are retained."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile

from .compression import CompressionOptions
from .core import (PdfWorkbenchError, Workspace, _build_writer, _serialize_output,
                   _check_cancel, _protect_destination, load_source)
from .page_marks import (PageMarksOptions, PageMarksRequest, PageNumberOptions,
                         WatermarkOptions, apply_page_marks)
from .workflows import ScanOptions, _unchanged, discard_published, prepare_scans, publish_bytes
from pypdf import PdfReader


@dataclass(frozen=True)
class BatchPreset:
    name: str = "Meine Vorlage"
    compression: str | None = "lossless"
    rotation: int = 0
    watermark: WatermarkOptions | None = None
    numbering: PageNumberOptions | None = None
    scan: ScanOptions | None = None
    ocr_languages: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 80 or any(ord(c) < 32 for c in self.name):
            raise PdfWorkbenchError("Ein Vorlagenname benötigt 1 bis 80 Zeichen ohne Steuerzeichen.")
        if self.compression is not None: CompressionOptions(self.compression)
        if type(self.rotation) is not int or self.rotation not in (0, 90, 180, 270):
            raise PdfWorkbenchError("Die Stapeldrehung muss 0, 90, 180 oder 270 Grad betragen.")
        if (not isinstance(self.watermark, (WatermarkOptions, type(None)))
                or not isinstance(self.numbering, (PageNumberOptions, type(None)))
                or not isinstance(self.scan, (ScanOptions, type(None)))):
            raise PdfWorkbenchError("Die Stapelvorlage enthält ungültige Optionen.")
        object.__setattr__(self, "ocr_languages", tuple(self.ocr_languages))
        if len(set(self.ocr_languages)) != len(self.ocr_languages) or any(code not in ("deu", "eng") for code in self.ocr_languages):
            raise PdfWorkbenchError("Für Stapel-OCR bitte Deutsch und/oder Englisch wählen.")

    def to_json(self):
        return json.dumps({"format": "pdf-workbench-batch", "version": 1, "preset": asdict(self)}, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, data):
        try:
            if not isinstance(data, str) or len(data.encode("utf-8")) > 64 * 1024: raise ValueError
            value = json.loads(data)
            if set(value) != {"format", "version", "preset"} or value["format"] != "pdf-workbench-batch" or type(value["version"]) is not int or value["version"] != 1:
                raise ValueError
            preset = dict(value["preset"])
            for key, constructor in (("watermark", WatermarkOptions), ("numbering", PageNumberOptions), ("scan", ScanOptions)):
                if preset.get(key) is not None: preset[key] = constructor(**preset[key])
            return cls(**preset)
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            raise PdfWorkbenchError("Die Datei enthält keine gültige PDF-Workbench-Stapelvorlage (Version 1).") from error


@dataclass(frozen=True)
class BatchResult:
    outputs: tuple[Path, ...]
    warnings: tuple[str, ...] = ()


def save_preset(preset, path):
    if not isinstance(preset, BatchPreset): raise PdfWorkbenchError("Die Stapelvorlage ist ungültig.")
    return publish_bytes(preset.to_json().encode("utf-8"), path).path


def load_preset(path):
    with Path(path).open("rb") as stream:
        data = stream.read(64 * 1024 + 1)
    try: return BatchPreset.from_json(data.decode("utf-8"))
    except UnicodeError as error: raise PdfWorkbenchError("Die Stapelvorlage ist nicht UTF-8-kodiert.") from error


@contextmanager
def _staging_directory(destination, warnings):
    """Remove registered own files only; retain foreign additions or edits."""
    directory = Path(tempfile.mkdtemp(prefix=".pdf-workbench-batch-", dir=destination))
    records = []
    try: yield directory, records.append
    finally:
        discard_published(records)
        try: directory.rmdir()
        except OSError:
            warnings.append(f"Zwischenzeitlich veränderte oder fremde temporäre Dateien bleiben erhalten: {directory}")


def process_batch(paths, directory, preset=BatchPreset(), *, cancel=None, progress=None, engine=None):
    """Prepare the entire batch privately before publishing any result.

    Failure removes only byte-identical own outputs. If another application has
    changed an output during cancellation, it is preserved and named in the
    raised error. No directory wildcard cleanup is ever used.
    """
    if not isinstance(preset, BatchPreset): raise PdfWorkbenchError("Die Stapelvorlage ist ungültig.")
    inputs = tuple(Path(path).expanduser().absolute() for path in paths)
    destination = Path(directory).expanduser().absolute()
    if not inputs or not destination.is_dir(): raise PdfWorkbenchError("Bitte PDFs und einen vorhandenen Zielordner auswählen.")
    if len(inputs) > 1000: raise PdfWorkbenchError("Ein Stapel darf höchstens 1.000 PDFs enthalten.")
    outputs = tuple(destination / (path.stem + "-bearbeitet.pdf") for path in inputs)
    if len({os.path.normcase(str(path)) for path in outputs}) != len(outputs):
        raise PdfWorkbenchError("Mehrere PDFs haben denselben Dateinamen. Bitte diese getrennt verarbeiten.")
    for path in outputs: _protect_destination(path, {})
    originals = {path.resolve() for path in inputs}
    if any(path.resolve() in originals for path in outputs): raise PdfWorkbenchError("Eine Stapelausgabe würde eine Quelldatei überschreiben.")
    records = []; warnings = []
    try:
        with _staging_directory(destination, warnings) as (staging, register_record):
            staged_outputs = []
            for ordinal, path in enumerate(inputs):
                _check_cancel(cancel)
                workspace = Workspace(); workspace.add_sources((load_source(path, cancel),))
                def step(value, message):
                    if progress: progress(round((ordinal + value / 100) * 90 / len(inputs)), f"{path.name}: {message}")
                if preset.rotation: workspace.rotate((page.uid for page in workspace.pages), preset.rotation)
                if preset.scan:
                    result = prepare_scans(workspace.sources, workspace.pages, preset.scan, cancel=cancel, progress=step)
                    workspace.apply_page_replacements(result.sources, result.pages)
                    warnings.extend(f"{path.name}: {warning}" for warning in result.warnings)
                # OCR precedes new watermarks/numbers: new visible text must not
                # make an otherwise image-only page skip recognition.
                if preset.ocr_languages:
                    from .ocr import export_searchable_pdf
                    ocr_path = staging / f"ocr-{ordinal}.pdf"
                    ocr_records = []
                    def ocr_published(record):
                        register_record(record); ocr_records.append(record)
                    export_searchable_pdf(workspace.sources, workspace.pages, ocr_path, engine=engine,
                                          languages=preset.ocr_languages, cancel=cancel, progress=step, bookmarks=workspace.bookmarks,
                                          publication_record=ocr_published)
                    if len(ocr_records) != 1: raise PdfWorkbenchError("Die temporäre OCR-Ausgabe wurde nicht vollständig bestätigt.")
                    ocr_record = ocr_records[0]
                    checked = PdfReader(ocr_path)
                    if checked.is_encrypted or len(checked.pages) != len(workspace.pages) or not _unchanged(ocr_record):
                        raise PdfWorkbenchError("Die temporäre OCR-Ausgabe wurde verändert oder ist unvollständig.")
                    replacement = Workspace(); replacement.add_sources((load_source(ocr_path, cancel),)); workspace = replacement
                if preset.watermark is not None or preset.numbering is not None:
                    apply_page_marks(workspace, PageMarksRequest(PageMarksOptions(preset.watermark, preset.numbering)), cancel, step)
                output = staging / f"result-{ordinal}.pdf"
                # Capture our immutable expected bytes before file creation.
                # Registering a disk file after export_pdf's public completion
                # callback could otherwise adopt a foreign mutation as ours.
                writer = _build_writer(workspace.sources, workspace.pages, path.stem, cancel, step, bookmarks=workspace.bookmarks)
                data, _ = _serialize_output(writer, CompressionOptions(preset.compression) if preset.compression else None,
                                            None, cancel, step)
                checked = PdfReader(io.BytesIO(data))
                if checked.is_encrypted or len(checked.pages) != len(workspace.pages):
                    raise PdfWorkbenchError("Die temporäre Stapelausgabe ist unvollständig.")
                record = publish_bytes(data, output, workspace.sources, cancel)
                register_record(record)
                staged_outputs.append(record)
                step(100, "PDF gespeichert")
            _check_cancel(cancel)
            for index, (staged, target) in enumerate(zip(staged_outputs, outputs)):
                data = staged.path.read_bytes()
                if len(data) != staged.size or hashlib.sha256(data).hexdigest() != staged.digest:
                    raise PdfWorkbenchError(f"Eine temporäre Stapelausgabe wurde zwischenzeitlich verändert und bleibt erhalten: {staged.path}")
                records.append(publish_bytes(data, target, cancel=cancel))
                if progress: progress(90 + round((index + 1) * 10 / len(outputs)), f"PDF {index + 1} von {len(outputs)} gespeichert …")
            _check_cancel(cancel)
        return BatchResult(outputs, tuple(warnings))
    except BaseException as error:
        retained = discard_published(records)
        temporary_hints = [warning for warning in warnings if "temporäre Dateien bleiben erhalten" in warning]
        if retained or temporary_hints:
            from .core import OperationCancelled
            detail = ("\nZwischenzeitlich veränderte Dateien bleiben erhalten: " + ", ".join(retained)) if retained else ""
            if temporary_hints: detail += "\n" + "\n".join(temporary_hints)
            if isinstance(error, OperationCancelled): raise OperationCancelled(str(error) + detail) from error
            raise PdfWorkbenchError(str(error) + detail) from error
        raise
