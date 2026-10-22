"""PDF operations, independent of the graphical toolkit.

Sources are immutable logical snapshots of base bytes and editable annotations. Every export is a new file; neither an
input path nor an existing output can be overwritten, including through aliases.
"""

from __future__ import annotations

import io
import math
import os
import stat
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from uuid import uuid4

from pypdf import PdfReader, PdfWriter

from . import __version__
from .annotations import AnnotationError, PageAnnotation, add_pdf_annotations, extract_own_annotations
from .compression import CompressionOptions, CompressionReport, compress_writer
from .bookmarks import (SourceBookmark, WorkspaceBookmark, bookmarks_for_sources,
                        bookmark_location, delete_bookmark, insert_bookmark, move_bookmark, replace_bookmark,
                        prune_bookmarks, read_source_bookmarks, source_to_workspace,
                        validate_bookmarks, validate_source_bookmarks, write_bookmarks)


class PdfWorkbenchError(Exception):
    """An error suitable for displaying to a user."""


class OperationCancelled(PdfWorkbenchError):
    """The operation was cancelled before committing its output."""


class PasswordRequiredError(PdfWorkbenchError):
    """An encrypted input requires a password supplied by the user."""


class IncorrectPasswordError(PdfWorkbenchError):
    """The supplied password did not unlock the encrypted input."""


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
    was_encrypted: bool = False
    original_size: int = 0
    bookmarks: tuple[SourceBookmark, ...] = ()
    bookmark_warnings: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self,"bookmarks",tuple(self.bookmarks))
        object.__setattr__(self,"bookmark_warnings",tuple(self.bookmark_warnings))

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


@dataclass(frozen=True)
class WorkspaceState:
    """Complete immutable session, including sources retained for undo/protection."""
    sources: tuple[SourceDocument,...]
    pages: tuple[PageRef,...]
    bookmarks: tuple[WorkspaceBookmark,...] = ()
    undo: tuple[tuple[PageRef,...],...] = ()
    redo: tuple[tuple[PageRef,...],...] = ()
    undo_bookmarks: tuple[tuple[WorkspaceBookmark,...],...] = ()
    redo_bookmarks: tuple[tuple[WorkspaceBookmark,...],...] = ()


def load_source(path: str | Path, cancel: CancelCheck | None = None, *,
                password: str | None = None) -> SourceDocument:
    """Read a complete snapshot; reject currently unsupported PDF features."""
    source_path = Path(path).expanduser().resolve()
    try:
        chunks = []
        with source_path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                _check_cancel(cancel)
                chunks.append(chunk)
        data = b"".join(chunks)
        original_size = len(data)
        _check_cancel(cancel)
        reader = PdfReader(io.BytesIO(data), strict=False)
        was_encrypted = reader.is_encrypted
        if was_encrypted:
            if password is None:
                raise PasswordRequiredError("Diese PDF ist verschlüsselt. Bitte gib das Öffnungspasswort ein.")
            try:
                unlocked = reader.decrypt(password)
            except Exception:
                raise PdfWorkbenchError("Die Verschlüsselung dieser PDF konnte nicht geöffnet werden.") from None
            if not unlocked:
                raise IncorrectPasswordError("Das Passwort ist falsch. Bitte versuche es erneut.")
            _check_cancel(cancel)
        root = reader.trailer["/Root"]
        from .forms import prepare_form_appearances, validate_forms
        validate_forms(reader)
        if root.get("/Perms"):
            raise PdfWorkbenchError("PDFs mit digitalen Zertifikatssignaturen werden derzeit nicht unterstützt.")
        normalized_form = prepare_form_appearances(reader)
        if normalized_form is not None:
            data = normalized_form
            reader = PdfReader(io.BytesIO(data), strict=False)
        if not len(reader.pages):
            raise PdfWorkbenchError("Die PDF enthält keine Seiten.")
        sizes = []
        for page in reader.pages:
            _check_cancel(cancel)
            size = (float(page.mediabox.width), float(page.mediabox.height))
            if any(not math.isfinite(value) or value <= 0 for value in size):
                raise PdfWorkbenchError("Diese PDF enthält eine Seite mit ungültigen Abmessungen (MediaBox).")
            sizes.append(size)
        page_annotations, stripped = extract_own_annotations(reader)
        bookmarks, bookmark_warnings = read_source_bookmarks(reader,cancel)
        if stripped or was_encrypted:
            _check_cancel(cancel)
            base = PdfWriter()
            base.clone_document_from_reader(reader)
            stream = io.BytesIO()
            base.write(stream)
            data = stream.getvalue()
        _check_cancel(cancel)
        return SourceDocument(uuid4().hex, source_path, data, len(sizes), tuple(sizes), page_annotations,
                              was_encrypted, original_size, bookmarks, bookmark_warnings)
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
        self.bookmarks: tuple[WorkspaceBookmark,...] = ()
        self._undo_bookmarks: list[tuple[WorkspaceBookmark,...]] = []
        self._redo_bookmarks: list[tuple[WorkspaceBookmark,...]] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def _set(self, pages: Sequence[PageRef], bookmarks=None) -> None:
        bookmarks=(prune_bookmarks(self.bookmarks,{page.uid for page in pages}) if bookmarks is None else tuple(bookmarks))
        validate_bookmarks(bookmarks,{page.uid for page in pages})
        if list(pages) == self.pages and bookmarks==self.bookmarks:
            return
        self._undo.append(tuple(self.pages))
        self._undo_bookmarks.append(self.bookmarks)
        self._undo = self._undo[-50:]
        self._undo_bookmarks = self._undo_bookmarks[-50:]
        self._redo.clear()
        self._redo_bookmarks.clear()
        self.pages = list(pages)
        self.bookmarks = bookmarks

    def add_sources(self, sources: Iterable[SourceDocument]) -> list[str]:
        new_pages = []; imported=[];additions={}
        for source in sources:
            previous=additions.get(source.source_id,self.sources.get(source.source_id))
            if previous is not None and previous!=source:raise PdfWorkbenchError("Eine vorhandene PDF-Quelle darf nicht verändert werden.")
            additions[source.source_id]=source
            source_pages=[PageRef(uuid4().hex, source.source_id, i, annotations=(
                source.page_annotations[i] if i < len(source.page_annotations) else ()
            )) for i in range(source.page_count)]
            new_pages.extend(source_pages);imported.extend(source_to_workspace(source.bookmarks,source_pages))
        bookmarks=(*self.bookmarks,*imported);validate_bookmarks(bookmarks,{p.uid for p in [*self.pages,*new_pages]})
        self.sources.update(additions)
        self._set([*self.pages, *new_pages],bookmarks)
        return [page.uid for page in new_pages]

    def set_bookmarks(self, bookmarks: Sequence[WorkspaceBookmark]) -> None:
        self._set(self.pages,bookmarks)

    def add_bookmark(self,title,page_uid=None,*,parent_uid=None,index=None) -> str:
        item=WorkspaceBookmark(uuid4().hex,title,page_uid)
        self.set_bookmarks(insert_bookmark(self.bookmarks,item,parent_uid=parent_uid,index=index));return item.uid

    def update_bookmark(self,uid,*,title=None,page_uid=Ellipsis) -> None:
        found=bookmark_location(self.bookmarks,uid)
        if found is None:raise PdfWorkbenchError("Das Lesezeichen wurde nicht gefunden.")
        item=found[2];item=replace(item,title=item.title if title is None else title,
                                 page_uid=item.page_uid if page_uid is Ellipsis else page_uid)
        self.set_bookmarks(replace_bookmark(self.bookmarks,uid,item))

    def delete_bookmark(self,uid,*,keep_children=False) -> None:
        self.set_bookmarks(delete_bookmark(self.bookmarks,uid,keep_children=keep_children))

    def move_bookmark(self,uid,*,parent_uid=None,index=None) -> None:
        self.set_bookmarks(move_bookmark(self.bookmarks,uid,parent_uid=parent_uid,index=index))

    def snapshot(self,include_history=True) -> WorkspaceState:
        return WorkspaceState(tuple(self.sources.values()),tuple(self.pages),self.bookmarks,
            tuple(self._undo) if include_history else (),tuple(self._redo) if include_history else (),
            tuple(self._undo_bookmarks) if include_history else (),tuple(self._redo_bookmarks) if include_history else ())

    def restore_state(self,state: WorkspaceState,*,clear_history=False) -> None:
        """Validate every snapshot before replacing any part of the live session."""
        if not isinstance(state,WorkspaceState):raise PdfWorkbenchError("Der Projektzustand ist ungültig.")
        sources={}
        for source in state.sources:
            if (not isinstance(source,SourceDocument) or not isinstance(source.source_id,str) or not source.source_id
                    or source.source_id in sources or not isinstance(source.data,bytes)
                    or type(source.page_count) is not int or source.page_count<1
                    or len(source.page_sizes)!=source.page_count):
                raise PdfWorkbenchError("Eine Projektquelle ist ungültig oder mehrfach vorhanden.")
            for size in source.page_sizes:
                if len(size)!=2 or any(not math.isfinite(n) or n<=0 for n in size):raise PdfWorkbenchError("Ein Projektseitenformat ist ungültig.")
            validate_source_bookmarks(source.bookmarks,source.page_count)
            sources[source.source_id]=source
        def validate(pages,bookmarks):
            ids=set()
            for page in pages:
                if (not isinstance(page,PageRef) or not isinstance(page.uid,str) or not page.uid or page.uid in ids
                        or page.source_id not in sources or type(page.page_index) is not int
                        or not 0<=page.page_index<sources[page.source_id].page_count
                        or type(page.rotation) is not int or page.rotation%90):raise PdfWorkbenchError("Eine Projektseite ist ungültig.")
                ids.add(page.uid)
            validate_bookmarks(bookmarks,ids)
        if (len(state.undo)!=len(state.undo_bookmarks) or len(state.redo)!=len(state.redo_bookmarks)
                or len(state.undo)>50 or len(state.redo)>50):raise PdfWorkbenchError("Der Projektverlauf ist ungültig.")
        validate(state.pages,state.bookmarks)
        for pages,bookmarks in (*zip(state.undo,state.undo_bookmarks),*zip(state.redo,state.redo_bookmarks)):validate(pages,bookmarks)
        self.sources=sources;self.pages=list(state.pages);self.bookmarks=tuple(state.bookmarks)
        self._undo=[] if clear_history else list(state.undo);self._redo=[] if clear_history else list(state.redo)
        self._undo_bookmarks=[] if clear_history else list(state.undo_bookmarks)
        self._redo_bookmarks=[] if clear_history else list(state.redo_bookmarks)

    def replace_source(self, source_id: str, new_source: SourceDocument) -> None:
        """Replace an immutable snapshot while keeping undoable page identity."""
        old_source = self.sources.get(source_id)
        if old_source is None or new_source.page_count != old_source.page_count:
            raise PdfWorkbenchError("Das neue Dokument muss dieselbe Seitenanzahl besitzen.")
        if new_source.source_id in self.sources and self.sources[new_source.source_id] != new_source:
            raise PdfWorkbenchError("Die neue Dokumentkennung wird bereits verwendet.")
        if new_source.source_id == source_id and new_source != old_source:
            raise PdfWorkbenchError("Ein bearbeitetes Dokument benötigt eine neue Kennung.")
        self.sources[new_source.source_id] = new_source
        self._set([replace(page, source_id=new_source.source_id)
                   if page.source_id == source_id else page for page in self.pages])

    def apply_page_replacements(self, new_sources: Sequence[SourceDocument], pages: Sequence[PageRef]) -> None:
        """Commit immutable per-page edits atomically, with one undo step."""
        pages = tuple(pages)
        if (any(not isinstance(page, PageRef) for page in pages)
                or tuple(page.uid for page in pages) != tuple(page.uid for page in self.pages)):
            raise PdfWorkbenchError("Die bearbeiteten Seiten müssen ihre Kennungen und Reihenfolge behalten.")
        additions = {}
        for source in new_sources:
            if not isinstance(source, SourceDocument):
                raise PdfWorkbenchError("Eine bearbeitete PDF-Quelle ist ungültig.")
            previous = additions.get(source.source_id, self.sources.get(source.source_id))
            if previous is not None and previous != source:
                raise PdfWorkbenchError("Eine vorhandene PDF-Quelle darf nicht verändert werden.")
            additions[source.source_id] = source
        candidates = self.sources | additions
        for page in pages:
            source = candidates.get(page.source_id)
            if (source is None or type(page.page_index) is not int
                    or not 0 <= page.page_index < source.page_count
                    or type(page.rotation) is not int or page.rotation % 90):
                raise PdfWorkbenchError("Eine bearbeitete Seite verweist auf eine ungültige PDF-Quelle.")
        self.sources.update(additions)
        self._set(pages)

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
            self._redo_bookmarks.append(self.bookmarks)
            self.pages = list(self._undo.pop())
            self.bookmarks=self._undo_bookmarks.pop()

    def redo(self) -> None:
        if self._redo:
            self._undo.append(tuple(self.pages))
            self._undo_bookmarks.append(self.bookmarks)
            self.pages = list(self._redo.pop())
            self.bookmarks=self._redo_bookmarks.pop()


def _protect_destination(target: Path, sources: Mapping[str, SourceDocument]) -> None:
    # lexists also detects dangling symlinks, which must never be followed.
    if os.path.lexists(target):
        raise PdfWorkbenchError("Die Zieldatei existiert bereits. Bitte wähle einen neuen Dateinamen.")
    resolved = target.resolve()
    if any(resolved == source.path.resolve() for source in sources.values()):
        raise PdfWorkbenchError("Eine Quelldatei darf nicht überschrieben werden. Wähle einen neuen Namen.")


def _discard_partial(target: Path, identity: tuple[int, int] | None) -> str:
    """Clean up a fallback output only while it is still the file we opened."""
    try:
        current = target.lstat()
        if (identity is None or not identity[1] or not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino) != identity):
            return f"\nDas Ziel wurde inzwischen verändert. Eine fremde Datei bleibt erhalten; der Verbleib der eigenen Teildatei ist unbekannt. Letzter Pfad: {target}"
        target.unlink()
    except FileNotFoundError:
        return f"\nDas Ziel wurde inzwischen entfernt oder verschoben. Letzter Pfad der eigenen Teildatei: {target}"
    except OSError as error:
        return f"\nNicht vollständig bereinigt: {target} ({error})"
    return ""


def _build_writer(sources: Mapping[str, SourceDocument], pages: Sequence[PageRef],
                  title: str, cancel: CancelCheck | None = None,
                  progress: Progress | None = None, *, bookmarks=None) -> PdfWriter:
    from .forms import copy_page_fields, page_has_fields
    if not pages:
        raise PdfWorkbenchError("Bitte wähle mindestens eine Seite aus.")
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
        reader = readers[page.source_id]
        source_page = reader.pages[page.page_index]
        identity = (page.source_id, page.page_index)
        if identity in imported_pages:
            writer.reset_translation(reader)
        # Cloning form parents and their Kids can accidentally embed pages the
        # user excluded. Rebuild supported fields from the selected widgets.
        added = (writer.add_page(source_page, excluded_keys=("/Annots",))
                 if page_has_fields(source_page) else writer.add_page(source_page))
        copy_page_fields(writer, added, reader, source_page, namespace=page.source_id)
        imported_pages.add(identity)
        add_pdf_annotations(writer, added, source_page, page.annotations, page.rotation)
        if page.rotation:
            added.rotate(page.rotation)
        if progress:
            progress(int((index + 1) / len(pages) * 85),
                     f"Seite {index + 1} von {len(pages)} vorbereiten …")
    write_bookmarks(writer,pages,bookmarks_for_sources(sources,pages) if bookmarks is None else bookmarks,cancel)
    writer.add_metadata({"/Producer": f"PDF Workbench {__version__} (pypdf)", "/Title": title})
    return writer


def _encrypted_bytes(data: bytes, password: str) -> bytes:
    try:
        valid = isinstance(password, str) and bool(password) and len(password.encode("utf-8")) <= 127
    except UnicodeError:
        valid = False
    if not valid:
        raise PdfWorkbenchError("Bitte verwende ein Passwort mit 1 bis 127 UTF-8-Bytes.")
    try:
        writer = PdfWriter(clone_from=io.BytesIO(data))
        writer.encrypt(password, algorithm="AES-256")
        stream = io.BytesIO()
        writer.write(stream)
        return stream.getvalue()
    except Exception:
        # Do not let an encryption provider or password-validation error put
        # the supplied secret into logs or a user-visible exception.
        raise PdfWorkbenchError("Der AES-256-Passwortschutz konnte nicht erzeugt werden.") from None


def _serialize_output(writer: PdfWriter, options: CompressionOptions | None,
                      password: str | None, cancel: CancelCheck | None,
                      progress: Progress | None) -> tuple[bytes, CompressionReport | None]:
    _check_cancel(cancel)
    baseline = None
    if options is not None and password is not None:
        stream = io.BytesIO()
        writer.write(stream)
        baseline = _encrypted_bytes(stream.getvalue(), password)
    if options is not None:
        data, report = compress_writer(writer, options, cancel, progress)
    else:
        stream = io.BytesIO()
        writer.write(stream)
        data, report = stream.getvalue(), None
    _check_cancel(cancel)
    if password is not None:
        data = _encrypted_bytes(data, password)
        if report is not None and baseline is not None:
            if len(data) >= len(baseline):
                data = baseline
                report = replace(report, images_changed=0)
            report = replace(report, baseline_bytes=len(baseline), output_bytes=len(data))
    _check_cancel(cancel)
    return data, report


def preview_compression(sources: Mapping[str, SourceDocument], pages: Sequence[PageRef],
                        options: CompressionOptions, cancel: CancelCheck | None = None,
                        progress: Progress | None = None, *, bookmarks=None) -> CompressionReport:
    """Measure an in-memory export; creates no temporary or destination file."""
    try:
        writer = _build_writer(sources, pages, "Komprimierungsvorschau", cancel, progress,bookmarks=bookmarks)
        _, report = _serialize_output(writer, options, None, cancel, progress)
        assert report is not None
        return report
    except PdfWorkbenchError:
        raise
    except Exception as exc:
        raise PdfWorkbenchError(f"Komprimierung konnte nicht vorbereitet werden: {exc}") from exc


def export_pdf(
    sources: Mapping[str, SourceDocument],
    pages: Sequence[PageRef],
    destination: str | Path,
    cancel: CancelCheck | None = None,
    progress: Progress | None = None,
    *,
    password: str | None = None,
    optimization: CompressionOptions | None = None,
    compression_report: Callable[[CompressionReport], None] | None = None,
    bookmarks: Sequence[WorkspaceBookmark] | None = None,
    publication_record: Callable | None = None,
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
    created_identity = None
    try:
        writer = _build_writer(sources, pages, target.stem, cancel, progress,bookmarks=bookmarks)
        data, report = _serialize_output(writer, optimization, password, cancel, progress)
        if publication_record is not None:
            # Batch callers need the identity and expected immutable bytes
            # before any public completion callback can change the stage file.
            checked = PdfReader(io.BytesIO(data))
            if password is not None and not checked.decrypt(password):
                raise PdfWorkbenchError("Der Passwortschutz der gespeicherten PDF konnte nicht geprüft werden.")
            if len(checked.pages) != len(pages):
                raise PdfWorkbenchError("Die PDF konnte nicht vollständig gespeichert werden.")
            from .workflows import publish_bytes
            record = publish_bytes(data, target, sources, cancel)
            publication_record(record)
            if compression_report and report is not None: compression_report(report)
            if progress: progress(100, "PDF gespeichert")
            return target
        descriptor, name = tempfile.mkstemp(prefix=".pdf-workbench-", suffix=".tmp", dir=target.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _check_cancel(cancel)
        # Parse the generated file before publishing it to the chosen path.
        with temporary.open("rb") as stream:
            checked = PdfReader(stream)
            if password is not None and not checked.decrypt(password):
                raise PdfWorkbenchError("Der Passwortschutz der gespeicherten PDF konnte nicht geprüft werden.")
            if len(checked.pages) != len(pages):
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
                identity = os.fstat(output.fileno())
                created_identity = identity.st_dev, identity.st_ino
                with temporary.open("rb") as source_stream:
                    while chunk := source_stream.read(1024 * 1024):
                        _check_cancel(cancel)
                        output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        if compression_report and report is not None:
            compression_report(report)
        if progress:
            progress(100, "PDF gespeichert")
        return target
    except Exception as exc:
        cleanup = _discard_partial(target, created_identity) if created_target else ""
        if isinstance(exc, OperationCancelled):
            raise OperationCancelled(("Vorgang abgebrochen." if cleanup else str(exc)) + cleanup) from exc
        if isinstance(exc, (AnnotationError, PdfWorkbenchError)):
            raise PdfWorkbenchError(str(exc) + cleanup) from exc
        raise PdfWorkbenchError(f"PDF konnte nicht gespeichert werden: {exc}{cleanup}") from exc
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
