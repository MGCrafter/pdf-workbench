"""Snapshot-based PDF search. All Qt PDF objects belong to the calling worker."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import re

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
from PyQt6.QtPdf import QPdfDocument, QPdfSearchModel
from PyQt6 import sip

from .core import OperationCancelled, PdfWorkbenchError


@dataclass(frozen=True)
class SearchHit:
    page_uid: str
    rectangles: tuple[tuple[float, float, float, float], ...]
    context: str
    annotation_uid: str | None = None


@dataclass(frozen=True)
class SearchResults:
    hits: tuple[SearchHit, ...]
    truncated: bool = False


def _context(before: str, term: str, after: str) -> str:
    return " ".join((before[-60:] + term + after[:60]).split())


def search_workspace(sources, pages, query, cancel=None, progress=None, max_hits=2000):
    """Search ordered workspace pages, including duplicate pages and own text boxes.

    Rectangles use the same normalized source coordinates as annotations, before
    the extra workspace rotation. No QObject is returned to the GUI thread.
    """
    query = str(query).strip()
    if not query:
        return SearchResults(())
    if len(query) > 256:
        raise PdfWorkbenchError("Der Suchtext darf höchstens 256 Zeichen lang sein.")
    if not isinstance(max_hits, int) or isinstance(max_hits, bool) or not 1 <= max_hits <= 10000:
        raise ValueError("Invalid search result limit")
    pages = tuple(pages)
    documents = OrderedDict()
    cached = {}
    hits = []
    own_pattern = re.compile(re.escape(query), re.IGNORECASE)

    def check_cancel():
        if cancel and cancel():
            raise OperationCancelled("Suche abgebrochen.")

    def close(entry):
        document, model, buffer = entry
        sip.delete(model)
        document.close()
        sip.delete(document)
        buffer.close()
        sip.delete(buffer)

    def document_for(source):
        if source.source_id not in documents:
            buffer = QBuffer()
            buffer.setData(QByteArray(source.data))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            document = QPdfDocument(None)
            document.load(buffer)
            if document.status() != QPdfDocument.Status.Ready:
                document.close(); buffer.close()
                raise PdfWorkbenchError(f"{source.label}: PDF-Text konnte nicht gelesen werden.")
            model = QPdfSearchModel(None)
            model.setDocument(document)
            model.setSearchString(query)
            documents[source.source_id] = (document, model, buffer)
            while len(documents) > 3:
                _, entry = documents.popitem(last=False)
                close(entry)
        documents.move_to_end(source.source_id)
        return documents[source.source_id][:2]

    try:
        for position, page in enumerate(pages):
            check_cancel()
            if progress:
                progress(round(position * 100 / max(1, len(pages))), f"Seite {position + 1} durchsuchen …")
            key = (page.source_id, page.page_index)
            if key not in cached:
                document, model = document_for(sources[page.source_id])
                size = document.pagePointSize(page.page_index)
                width, height = size.width(), size.height()
                found = []
                if width > 0 and height > 0:
                    for link in model.resultsOnPage(page.page_index):
                        check_cancel()
                        rectangles = []
                        for rect in link.rectangles():
                            rect = rect.normalized()
                            left, top = max(0., rect.left() / width), max(0., rect.top() / height)
                            right, bottom = min(1., rect.right() / width), min(1., rect.bottom() / height)
                            if right > left and bottom > top:
                                rectangles.append((left, top, right, bottom))
                        if rectangles:
                            found.append((tuple(rectangles), _context(link.contextBefore(), query, link.contextAfter())))
                        if len(found) > max_hits:
                            break
                cached[key] = tuple(found)
            for rectangles, context in cached[key]:
                if len(hits) == max_hits:
                    return SearchResults(tuple(hits), True)
                hits.append(SearchHit(page.uid, rectangles, context))
            for annotation in page.annotations:
                if annotation.kind != "text":
                    continue
                for match in own_pattern.finditer(annotation.text):
                    check_cancel()
                    if len(hits) == max_hits:
                        return SearchResults(tuple(hits), True)
                    context = _context(annotation.text[:match.start()], match.group(), annotation.text[match.end():])
                    hits.append(SearchHit(page.uid, (annotation.rect,), context, annotation.uid))
        check_cancel()
        if progress:
            progress(100, f"{len(hits)} Treffer gefunden.")
        return SearchResults(tuple(hits))
    finally:
        for entry in documents.values():
            close(entry)
