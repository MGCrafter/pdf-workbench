"""Read-only comparison of visible PDF pages and extracted text.

QtPdf objects belong to the calling thread. The GUI calls this service from a
worker; callers need an existing Qt application. Raster comparisons deliberately
have a documented resolution limit, rather than claiming byte-level equality.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
import hashlib
import io
import math
import re
import unicodedata

from PIL import Image, ImageChops
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QSize
from PyQt6.QtGui import QImage
from PyQt6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions

from .core import CancelCheck, OperationCancelled, PageRef, PdfWorkbenchError, Progress, SourceDocument, _build_writer
from .form_rendering import form_render_data


@dataclass(frozen=True)
class ComparisonOptions:
    max_pages: int = 300
    render_edge: int = 1000
    max_text_characters: int = 100_000
    max_text_changes: int = 60

    def __post_init__(self):
        if (type(self.max_pages) is not int or not 1 <= self.max_pages <= 500
                or type(self.render_edge) is not int or not 256 <= self.render_edge <= 1600
                or type(self.max_text_characters) is not int or not 1000 <= self.max_text_characters <= 200_000
                or type(self.max_text_changes) is not int or not 1 <= self.max_text_changes <= 200):
            raise PdfWorkbenchError("Ungültige Grenzen für den Dokumentvergleich.")


@dataclass(frozen=True)
class TextChange:
    kind: str  # added, removed, changed
    before: str
    after: str


@dataclass(frozen=True)
class PageComparison:
    left_index: int | None
    right_index: int | None
    status: str  # unchanged, changed, added, removed
    text_changes: tuple[TextChange, ...] = ()
    visual_regions: tuple[tuple[float, float, float, float], ...] = ()
    visual_difference: float = 0.0
    size_changed: bool = False
    text_changes_truncated: bool = False
    moved_to: int | None = None
    moved_from: int | None = None


@dataclass(frozen=True)
class ComparisonResult:
    pages: tuple[PageComparison, ...]
    left_page_count: int
    right_page_count: int
    render_edge: int
    left_source: SourceDocument | None = field(default=None, repr=False, compare=False)
    right_source: SourceDocument | None = field(default=None, repr=False, compare=False)
    left_page_sizes: tuple[tuple[float, float], ...] = ()
    right_page_sizes: tuple[tuple[float, float], ...] = ()

    def count(self, status: str) -> int:
        return sum(page.status == status for page in self.pages)


@dataclass(frozen=True)
class _Fingerprint:
    text: str
    words: frozenset[str]
    visual_hash: str
    thumbnail: bytes
    points: tuple[float, float]

    @property
    def key(self):
        return self.visual_hash, self.text, self.points


def _cancel(check):
    if check and check():
        raise OperationCancelled("Dokumentvergleich abgebrochen. Keine Dateien verändert.")


def _render_source(source, cancel):
    """Restore imported editable appearances into an immutable render snapshot.

    load_source strips our annotations into page_annotations so the workspace
    can edit them. Comparing source.data alone would silently omit those marks.
    """
    if any(source.page_annotations):
        pages=tuple(PageRef(f"compare-{index}",source.source_id,index,annotations=(
            source.page_annotations[index] if index < len(source.page_annotations) else ()))
            for index in range(source.page_count))
        writer=_build_writer({source.source_id:source},pages,"Dokumentvergleich",cancel)
        _cancel(cancel)
        stream=io.BytesIO();writer.write(stream)
        _cancel(cancel)
        source=replace(source,data=stream.getvalue(),page_annotations=())
    # QtPdf ignores live Widget appearances even with RenderFlag.Annotations.
    # Flatten only this render copy; actual source/export widgets stay live.
    data=form_render_data(source,cancel)
    return replace(source,data=data) if data is not source.data else source


class _Document:
    def __init__(self, source):
        self.document = QPdfDocument(None)
        self.buffer = QBuffer()
        self.buffer.setData(QByteArray(source.data))
        self.buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.document.load(self.buffer)
        if self.document.status() != QPdfDocument.Status.Ready or self.document.pageCount() != source.page_count:
            self.close()
            raise PdfWorkbenchError(f"{source.label} konnte für den Vergleich nicht gerendert werden.")

    def close(self):
        self.document.close()
        self.buffer.close()

    def points(self, index):
        size = self.document.pagePointSize(index)
        result = size.width(), size.height()
        if any(not math.isfinite(n) or n <= 0 for n in result):
            raise PdfWorkbenchError(f"Ungültiges Seitenformat auf Seite {index + 1}.")
        return result

    def render(self, index, edge):
        width, height = self.points(index)
        factor = edge / max(width, height)
        size = QSize(max(1, round(width * factor)), max(1, round(height * factor)))
        options = QPdfDocumentRenderOptions()
        options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)
        image = self.document.render(index, size, options).convertToFormat(QImage.Format.Format_RGBA8888)
        if image.isNull():
            raise PdfWorkbenchError(f"Seite {index + 1} konnte für den Vergleich nicht gerendert werden.")
        rgba = Image.frombytes("RGBA", (image.width(), image.height()),
                               image.constBits().asstring(image.sizeInBytes()), "raw", "RGBA", image.bytesPerLine())
        return Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")


def _fingerprints(document, source, options, cancel, progress, offset, total):
    result = []
    for index in range(source.page_count):
        _cancel(cancel)
        text = unicodedata.normalize("NFC", document.document.getAllText(index).text())
        if index < len(source.page_annotations):
            # QtPdf's page-text extraction does not include FreeText appearances.
            # These are explicit text objects, so no image OCR is needed.
            text += "\n" + "\n".join(annotation.text for annotation in source.page_annotations[index] if annotation.kind == "text")
        if len(text) > options.max_text_characters:
            raise PdfWorkbenchError(f"Seite {index + 1} in {source.label} enthält zu viel Text für den Vergleich. Bitte kleinere Dokumente verwenden.")
        text = " ".join(text.split())
        image = document.render(index, options.render_edge)
        _cancel(cancel)
        visual_hash = hashlib.sha256(image.tobytes()).hexdigest()
        # Bound alignment storage independently of the rasterization resolution.
        thumbnail = image.resize((8, 8), Image.Resampling.BOX).tobytes()
        words = frozenset(re.findall(r"\w+", text.casefold())[:256])
        result.append(_Fingerprint(text, words, visual_hash, thumbnail, document.points(index)))
        if progress:
            progress(round(60 * (offset + index + 1) / total), f"Seiten lesen · {offset + index + 1}/{total}")
    return result


def _pair_cost(left, right):
    if left.key == right.key:
        return 0.0
    union = len(left.words | right.words)
    text_similarity = len(left.words & right.words) / union if union else 0.0
    if left.text and left.text == right.text:
        text_similarity = 1.0
    visual_similarity = 1 - sum(abs(a - b) for a, b in zip(left.thumbnail, right.thumbnail)) / (192 * 255)
    return .45 + 1.1 * (1 - max(text_similarity, visual_similarity * .8))


def _align_block(left, right, left_start, right_start, cancel):
    n, m = len(left), len(right)
    if not n:
        return [(None, right_start + j) for j in range(m)]
    if not m:
        return [(left_start + i, None) for i in range(n)]
    costs = [[0.0] * (m + 1) for _ in range(n + 1)]
    directions = [bytearray(m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        costs[i][0], directions[i][0] = i * .9, 1
    for j in range(1, m + 1):
        costs[0][j], directions[0][j] = j * .9, 2
    for i in range(1, n + 1):
        _cancel(cancel)
        for j in range(1, m + 1):
            candidates = (costs[i - 1][j - 1] + _pair_cost(left[i - 1], right[j - 1]),
                          costs[i - 1][j] + .9, costs[i][j - 1] + .9)
            direction = min(range(3), key=candidates.__getitem__)
            costs[i][j], directions[i][j] = candidates[direction], direction
    result = []
    i, j = n, m
    while i or j:
        direction = directions[i][j]
        if direction == 0:
            i -= 1; j -= 1; result.append((left_start + i, right_start + j))
        elif direction == 1:
            i -= 1; result.append((left_start + i, None))
        else:
            j -= 1; result.append((None, right_start + j))
    return list(reversed(result))


def _align(left, right, cancel):
    matcher = SequenceMatcher(None, [page.key for page in left], [page.key for page in right], autojunk=False)
    pairs = []
    for tag, a, b, c, d in matcher.get_opcodes():
        _cancel(cancel)
        if tag == "equal":
            pairs.extend(zip(range(a, b), range(c, d)))
        else:
            pairs.extend(_align_block(left[a:b], right[c:d], a, c, cancel))
    return pairs


def _text_changes(before, after, limit, cancel=None):
    if before == after:
        return (), False
    old, new = before.split(), after.split()
    if len(old) + len(new) > 12_000:
        # Avoid quadratic matching on pathological long/repeated word streams.
        # Preserve the full changed span but disclose that fine grouping is cut.
        prefix = 0
        while prefix < min(len(old),len(new)) and old[prefix] == new[prefix]:
            if prefix % 256 == 0: _cancel(cancel)
            prefix += 1
        end_old,end_new = len(old),len(new)
        while end_old > prefix and end_new > prefix and old[end_old-1] == new[end_new-1]:
            if end_old % 256 == 0: _cancel(cancel)
            end_old -= 1; end_new -= 1
        a,b = " ".join(old[prefix:end_old])," ".join(new[prefix:end_new])
        kind = "changed" if a and b else "removed" if a else "added"
        return (TextChange(kind,a,b),), True
    matcher = SequenceMatcher(None, old, new, autojunk=len(old) + len(new) > 2000)
    result = []
    truncated = False
    for kind, a, b, c, d in matcher.get_opcodes():
        _cancel(cancel)
        if kind == "equal":
            continue
        if len(result) == limit:
            truncated = True; break
        result.append(TextChange({"replace": "changed", "delete": "removed", "insert": "added"}[kind],
                                 " ".join(old[a:b]), " ".join(new[c:d])))
    return tuple(result), truncated


def _visual_regions(left, right):
    if left.size != right.size:
        return ((0.0, 0.0, 1.0, 1.0),), 1.0
    difference = ImageChops.difference(left, right)
    r, g, b = difference.split()
    mask = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda value: 255 if value > 16 else 0)
    if not mask.getbbox():
        return (), 0.0
    fraction = mask.histogram()[255] / (mask.width * mask.height)
    cells = set()
    step = max(8, math.ceil(max(mask.size) / 40))
    for y in range(0, mask.height, step):
        for x in range(0, mask.width, step):
            if mask.crop((x, y, min(x + step, mask.width), min(y + step, mask.height))).getbbox():
                cells.add((x // step, y // step))
    regions = []
    while cells:
        seed = cells.pop(); group = [seed]; pending = [seed]
        while pending:
            x, y = pending.pop()
            for neighbor in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if neighbor in cells:
                    cells.remove(neighbor); group.append(neighbor); pending.append(neighbor)
        regions.append((max(0, min(x for x, _ in group) * step - 3) / mask.width,
                        max(0, min(y for _, y in group) * step - 3) / mask.height,
                        min(mask.width, (max(x for x, _ in group) + 1) * step + 3) / mask.width,
                        min(mask.height, (max(y for _, y in group) + 1) * step + 3) / mask.height))
    if len(regions) > 40:
        regions = [(min(a[0] for a in regions), min(a[1] for a in regions),
                    max(a[2] for a in regions), max(a[3] for a in regions))]
    return tuple(sorted(regions, key=lambda rect: (rect[1], rect[0]))), fraction


def compare_documents(left: SourceDocument, right: SourceDocument, cancel: CancelCheck | None = None,
                      progress: Progress | None = None, *, options: ComparisonOptions | None = None) -> ComparisonResult:
    """Compare immutable source snapshots without writing files.

    Matching respects page order. Exact pages anchor inserted/deleted sequences;
    moves are represented by linked removed/added entries. No OCR or metadata,
    attachment, accessibility-tree or signature validation is performed.
    """
    options = options or ComparisonOptions()
    _cancel(cancel)
    if any(not 0 < source.page_count <= options.max_pages for source in (left, right)):
        raise PdfWorkbenchError(f"Der Vergleich unterstützt höchstens {options.max_pages} Seiten pro PDF. Bitte kleinere Dokumente verwenden.")
    render_left,render_right = _render_source(left,cancel),_render_source(right,cancel)
    first = _Document(render_left)
    second = None
    try:
        second = _Document(render_right)
        total = left.page_count + right.page_count
        old = _fingerprints(first, left, options, cancel, progress, 0, total)
        new = _fingerprints(second, right, options, cancel, progress, left.page_count, total)
        if progress: progress(62, "Seiten zuordnen …")
        pairs = _align(old, new, cancel)
        result = []
        for position, (a, b) in enumerate(pairs):
            _cancel(cancel)
            if a is None:
                page = PageComparison(None, b, "added")
            elif b is None:
                page = PageComparison(a, None, "removed")
            elif old[a].key == new[b].key:
                page = PageComparison(a, b, "unchanged")
            else:
                changes, truncated = _text_changes(old[a].text, new[b].text, options.max_text_changes, cancel)
                _cancel(cancel)
                before, after = first.render(a, options.render_edge), second.render(b, options.render_edge)
                regions, difference = _visual_regions(before, after)
                size_changed = old[a].points != new[b].points
                if size_changed: regions = ((0.0, 0.0, 1.0, 1.0),)
                page = PageComparison(a, b, "changed", changes, regions, difference, size_changed, truncated)
            result.append(page)
            if progress: progress(65 + round(35 * (position + 1) / len(pairs)), f"Änderungen prüfen · {position + 1}/{len(pairs)}")
        removed = defaultdict(list)
        for index, page in enumerate(result):
            if page.status == "removed": removed[old[page.left_index].key].append(index)
        for index, page in enumerate(result):
            if page.status != "added": continue
            candidates = removed[new[page.right_index].key]
            if candidates:
                previous = candidates.pop(0)
                result[index] = replace(page, moved_from=result[previous].left_index)
                result[previous] = replace(result[previous], moved_to=page.right_index)
        _cancel(cancel)
        return ComparisonResult(tuple(result), left.page_count, right.page_count, options.render_edge,render_left,render_right,
                                tuple(page.points for page in old),tuple(page.points for page in new))
    except (OperationCancelled, PdfWorkbenchError):
        raise
    except Exception as error:
        raise PdfWorkbenchError(f"Dokumentvergleich nicht möglich: {error}") from error
    finally:
        if second is not None: second.close()
        first.close()
