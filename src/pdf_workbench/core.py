"""PDF operations, independent of the graphical toolkit.

Sources are immutable logical snapshots of base bytes and editable annotations. Every export is a new file; neither an
input path nor an existing output can be overwritten, including through aliases.
"""

from __future__ import annotations

import io
import math
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from uuid import uuid4

from pypdf import PdfReader, PdfWriter

from . import __version__
from .annotations import AnnotationError, PageAnnotation, add_pdf_annotations, extract_own_annotations


class PdfWorkbenchError(Exception):
    """An error suitable for displaying to a user."""


class OperationCancelled(PdfWorkbenchError):
    """The operation was cancelled before committing its output."""


CancelCheck = Callable[[], bool]
Progress = Callable[[int, str], None]


def _check_cancel(cancel: CancelCheck | None) -> None:
    if cancel and cancel():
        raise OperationCancelled("Vorgang abgebrochen. Es wurde keine Datei gespeichert.")


@dataclass(frozen=True)
class SourceDocument:
    source_id: str
    path: Path
    data: bytes
    page_count: int
    page_sizes: tuple[tuple[float, float], ...]
    page_annotations: tuple[tuple[PageAnnotation, ...], ...] = ()

    @property
    def label(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class PageRef:
    uid: str
    source_id: str
    page_index: int
    rotation: int = 0
    annotations: tuple[PageAnnotation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "annotations", tuple(self.annotations))
        if any(not isinstance(item, PageAnnotation) for item in self.annotations):
            raise PdfWorkbenchError("Eine Seitenanmerkung ist ungültig.")


def load_source(path: str | Path, cancel: CancelCheck | None = None) -> SourceDocument:
    """Read a complete snapshot; reject currently unsupported PDF features."""
    source_path = Path(path).expanduser().resolve()
    try:
        chunks = []
        with source_path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                _check_cancel(cancel)
                chunks.append(chunk)
        data = b"".join(chunks)
        _check_cancel(cancel)
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise PdfWorkbenchError(
                "Verschlüsselte PDFs werden derzeit nicht unterstützt. "
                "Bitte verwende eine unverschlüsselte Kopie, die du bearbeiten darfst."
            )
        root = reader.trailer["/Root"]
        acroform = root.get("/AcroForm")
        if acroform:
            fields = acroform.get_object()
            if fields.get("/Fields") or fields.get("/XFA"):
                raise PdfWorkbenchError(
                    "Diese PDF enthält interaktive Formularfelder oder digitale Signaturen. "
                    "Derzeit werden nur normale Dokumentseiten verarbeitet."
                )
        if root.get("/Perms"):
            raise PdfWorkbenchError("PDFs mit digitalen Zertifikatssignaturen werden derzeit nicht unterstützt.")
        if not len(reader.pages):
            raise PdfWorkbenchError("Die PDF enthält keine Seiten.")
        sizes = []
        for page in reader.pages:
            _check_cancel(cancel)
            # Orphaned widgets can exist without a document-level form tree.
            annotations = page.get("/Annots", [])
            if hasattr(annotations, "get_object"):
                annotations = annotations.get_object()
            for annotation in annotations:
                if annotation.get_object().get("/Subtype") == "/Widget":
                    raise PdfWorkbenchError(
                        "Diese PDF enthält Formularfelder. Sie werden derzeit nicht unterstützt."
                    )
            size = (float(page.mediabox.width), float(page.mediabox.height))
            if any(not math.isfinite(value) or value <= 0 for value in size):
                raise PdfWorkbenchError("Diese PDF enthält eine Seite mit ungültigen Abmessungen (MediaBox).")
            sizes.append(size)
        page_annotations, stripped = extract_own_annotations(reader)
        if stripped:
            _check_cancel(cancel)
            base = PdfWriter()
            base.clone_document_from_reader(reader)
            stream = io.BytesIO()
            base.write(stream)
            data = stream.getvalue()
        _check_cancel(cancel)
        return SourceDocument(uuid4().hex, source_path, data, len(sizes), tuple(sizes), page_annotations)
    except (PdfWorkbenchError, OperationCancelled):
        raise
    except OSError as exc:
        raise PdfWorkbenchError(f"Datei konnte nicht gelesen werden: {exc}") from exc
    except Exception as exc:
        raise PdfWorkbenchError(f"Keine lesbare PDF-Datei: {exc}") from exc


class Workspace:
    """Editable order with bounded undo history; sources themselves never change."""

    def __init__(self) -> None:
        self.sources: dict[str, SourceDocument] = {}
        self.pages: list[PageRef] = []
        self._undo: list[tuple[PageRef, ...]] = []
        self._redo: list[tuple[PageRef, ...]] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def _set(self, pages: Sequence[PageRef]) -> None:
        if list(pages) == self.pages:
            return
        self._undo.append(tuple(self.pages))
        self._undo = self._undo[-50:]
        self._redo.clear()
        self.pages = list(pages)

    def add_sources(self, sources: Iterable[SourceDocument]) -> list[str]:
        new_pages = []
        for source in sources:
            self.sources[source.source_id] = source
            new_pages.extend(PageRef(uuid4().hex, source.source_id, i, annotations=(
                source.page_annotations[i] if i < len(source.page_annotations) else ()
            )) for i in range(source.page_count))
        self._set([*self.pages, *new_pages])
        return [page.uid for page in new_pages]

    def reorder(self, ordered_ids: Sequence[str]) -> None:
        if len(ordered_ids) != len(self.pages) or set(ordered_ids) != {p.uid for p in self.pages}:
            raise PdfWorkbenchError("Die Seitenreihenfolge ist ungültig.")
        lookup = {page.uid: page for page in self.pages}
        self._set([lookup[uid] for uid in ordered_ids])

    def move(self, selected_ids: Iterable[str], direction: int) -> None:
        if direction not in (-1, 1):
            raise ValueError("direction must be -1 or 1")
        selected = set(selected_ids)
        pages = list(self.pages)
        indices = range(1, len(pages)) if direction == -1 else range(len(pages) - 2, -1, -1)
        for i in indices:
            j = i + direction
            if pages[i].uid in selected and pages[j].uid not in selected:
                pages[i], pages[j] = pages[j], pages[i]
        self._set(pages)

    def rotate(self, selected_ids: Iterable[str], degrees: int) -> None:
        if degrees % 90:
            raise ValueError("rotation must be a multiple of 90")
        selected = set(selected_ids)
        self._set([
            replace(page, rotation=(page.rotation + degrees) % 360)
            if page.uid in selected else page for page in self.pages
        ])

    def delete(self, selected_ids: Iterable[str]) -> None:
        selected = set(selected_ids)
        self._set([page for page in self.pages if page.uid not in selected])

    def duplicate(self, selected_ids: Iterable[str]) -> list[str]:
        """Copy each selected page directly after it, as one undoable edit."""
        selected = set(selected_ids)
        pages = []
        new_ids = []
        for page in self.pages:
            pages.append(page)
            if page.uid in selected:
                copied = replace(page, uid=uuid4().hex, annotations=tuple(
                    replace(annotation, uid=uuid4().hex) for annotation in page.annotations
                ))
                pages.append(copied)
                new_ids.append(copied.uid)
        self._set(pages)
        return new_ids

    def _annotation_page(self, page_uid: str) -> tuple[int, PageRef]:
        for index, page in enumerate(self.pages):
            if page.uid == page_uid:
                return index, page
        raise PdfWorkbenchError("Die ausgewählte Arbeitsseite wurde nicht gefunden.")

    def add_annotation(self, page_uid: str, annotation: PageAnnotation) -> None:
        index, page = self._annotation_page(page_uid)
        if not isinstance(annotation, PageAnnotation):
            raise PdfWorkbenchError("Die Anmerkung ist ungültig.")
        if any(item.uid == annotation.uid for item in page.annotations):
            raise PdfWorkbenchError("Eine Anmerkung mit dieser Kennung existiert bereits.")
        pages = list(self.pages)
        pages[index] = replace(page, annotations=(*page.annotations, annotation))
        self._set(pages)

    def update_annotation(self, page_uid: str, annotation: PageAnnotation) -> None:
        index, page = self._annotation_page(page_uid)
        if not isinstance(annotation, PageAnnotation):
            raise PdfWorkbenchError("Die Anmerkung ist ungültig.")
        if not any(item.uid == annotation.uid for item in page.annotations):
            raise PdfWorkbenchError("Die zu bearbeitende Anmerkung wurde nicht gefunden.")
        pages = list(self.pages)
        pages[index] = replace(page, annotations=tuple(
            annotation if item.uid == annotation.uid else item for item in page.annotations
        ))
        self._set(pages)

    def remove_annotation(self, page_uid: str, annotation_uid: str) -> None:
        index, page = self._annotation_page(page_uid)
        if not any(item.uid == annotation_uid for item in page.annotations):
            raise PdfWorkbenchError("Die zu entfernende Anmerkung wurde nicht gefunden.")
        pages = list(self.pages)
        pages[index] = replace(page, annotations=tuple(item for item in page.annotations if item.uid != annotation_uid))
        self._set(pages)

    def undo(self) -> None:
        if self._undo:
            self._redo.append(tuple(self.pages))
            self.pages = list(self._undo.pop())

    def redo(self) -> None:
        if self._redo:
            self._undo.append(tuple(self.pages))
            self.pages = list(self._redo.pop())


def _protect_destination(target: Path, sources: Mapping[str, SourceDocument]) -> None:
    # lexists also detects dangling symlinks, which must never be followed.
    if os.path.lexists(target):
        raise PdfWorkbenchError("Die Zieldatei existiert bereits. Bitte wähle einen neuen Dateinamen.")
    resolved = target.resolve()
    if any(resolved == source.path.resolve() for source in sources.values()):
        raise PdfWorkbenchError("Eine Quelldatei darf nicht überschrieben werden. Wähle einen neuen Namen.")


def export_pdf(
    sources: Mapping[str, SourceDocument],
    pages: Sequence[PageRef],
    destination: str | Path,
    cancel: CancelCheck | None = None,
    progress: Progress | None = None,
) -> Path:
    """Write selected pages in their current order, safely creating a fresh PDF.

    A same-directory temporary file is fully written and checked first. A hard
    link commits it without replacing another file; filesystems without hard
    links use exclusive creation and remove partial output on failure.
    """
    if not pages:
        raise PdfWorkbenchError("Bitte wähle mindestens eine Seite aus.")
    target = Path(destination).expanduser().absolute()
    if target.suffix.lower() != ".pdf":
        raise PdfWorkbenchError("Bitte verwende die Dateiendung .pdf.")
    _protect_destination(target, sources)
    _check_cancel(cancel)
    temporary: Path | None = None
    created_target = False
    try:
        writer = PdfWriter()
        readers: dict[str, PdfReader] = {}
        imported_pages: set[tuple[str, int]] = set()
        for index, page in enumerate(pages):
            _check_cancel(cancel)
            source = sources.get(page.source_id)
            if source is None or not 0 <= page.page_index < source.page_count or page.rotation % 90:
                raise PdfWorkbenchError("Eine ausgewählte Seite ist ungültig.")
            if page.source_id not in readers:
                readers[page.source_id] = PdfReader(io.BytesIO(source.data))
            identity = (page.source_id, page.page_index)
            if identity in imported_pages:
                # add_page creates an independent page dictionary, but its
                # clone cache otherwise shares annotations and their /P link.
                writer.reset_translation(readers[page.source_id])
            added = writer.add_page(readers[page.source_id].pages[page.page_index])
            imported_pages.add(identity)
            add_pdf_annotations(writer, added, readers[page.source_id].pages[page.page_index], page.annotations, page.rotation)
            if page.rotation:
                added.rotate(page.rotation)
            if progress:
                progress(int((index + 1) / len(pages) * 85), f"Seite {index + 1} von {len(pages)} vorbereiten …")
        writer.add_metadata({"/Producer": f"PDF Workbench {__version__} (pypdf)", "/Title": target.stem})
        descriptor, name = tempfile.mkstemp(prefix=".pdf-workbench-", suffix=".tmp", dir=target.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            writer.write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        _check_cancel(cancel)
        # Parse the generated file before publishing it to the chosen path.
        with temporary.open("rb") as stream:
            if len(PdfReader(stream).pages) != len(pages):
                raise PdfWorkbenchError("Die PDF konnte nicht vollständig gespeichert werden.")
        _check_cancel(cancel)
        _protect_destination(target, sources)
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise PdfWorkbenchError("Die Zieldatei wurde inzwischen angelegt. Bitte wähle einen neuen Namen.") from exc
        except OSError:
            # FAT and some network filesystems do not implement hard links.
            with target.open("xb") as output:
                created_target = True
                with temporary.open("rb") as source_stream:
                    while chunk := source_stream.read(1024 * 1024):
                        _check_cancel(cancel)
                        output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        if progress:
            progress(100, "PDF gespeichert")
        return target
    except AnnotationError as exc:
        if created_target:
            target.unlink(missing_ok=True)
        raise PdfWorkbenchError(str(exc)) from exc
    except (PdfWorkbenchError, OperationCancelled):
        if created_target:
            target.unlink(missing_ok=True)
        raise
    except Exception as exc:
        if created_target:
            target.unlink(missing_ok=True)
        raise PdfWorkbenchError(f"PDF konnte nicht gespeichert werden: {exc}") from exc
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
