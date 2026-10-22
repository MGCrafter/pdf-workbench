"""Copy or move immutable pages between independent document workspaces."""
from dataclasses import replace
from uuid import uuid4

from .core import PdfWorkbenchError, Workspace


def transfer_pages(origin, destination, selected_ids, *, move=False):
    """Validate both results before committing either workspace; one undo each."""
    if origin is destination:
        raise PdfWorkbenchError("Bitte einen anderen Dokument-Tab wählen.")
    selected_ids = set(selected_ids)
    pages = [page for page in origin.pages if page.uid in selected_ids]
    if not pages or {page.uid for page in pages} != selected_ids:
        raise PdfWorkbenchError("Bitte vorhandene Seiten zum Übertragen auswählen.")
    target = Workspace(); target.restore_state(destination.snapshot())
    sources = {page.source_id: origin.sources[page.source_id] for page in pages}
    for source_id, source in sources.items():
        if source_id in target.sources and target.sources[source_id] != source:
            raise PdfWorkbenchError("Eine PDF-Quellenkennung ist im Ziel bereits anders belegt.")
    mapping = {page.uid: uuid4().hex for page in pages}
    copies = [replace(page, uid=mapping[page.uid]) for page in pages]

    def bookmarks(tree):
        result = []
        for item in tree:
            children = bookmarks(item.children)
            page_uid = mapping.get(item.page_uid)
            if page_uid is not None or children:
                result.append(replace(item, uid=uuid4().hex, page_uid=page_uid, children=children))
        return tuple(result)

    target.sources.update(sources)
    target._set([*target.pages, *copies], (*target.bookmarks, *bookmarks(origin.bookmarks)))
    target_state = target.snapshot()
    source_state = None
    if move:
        source = Workspace(); source.restore_state(origin.snapshot())
        source._set([page for page in source.pages if page.uid not in selected_ids])
        source_state = source.snapshot()
    destination.restore_state(target_state)
    if source_state is not None:
        origin.restore_state(source_state)
    return tuple(page.uid for page in copies)
