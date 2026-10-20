"""Convert bounded PNG/JPEG snapshots into ordinary PDF document pages."""
from __future__ import annotations

from dataclasses import dataclass
import io
import math
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QRectF, QSizeF
from PyQt6.QtGui import QImageReader, QPageLayout, QPageSize, QPainter, QPdfWriter
from pypdf import PdfReader

from .core import OperationCancelled, PdfWorkbenchError, SourceDocument


@dataclass(frozen=True)
class ImagePageOptions:
    paper: str = "A4"
    orientation: str = "auto"
    margin_mm: float = 10.0

    def __post_init__(self):
        if self.paper not in ("A4", "Letter", "image"):
            raise ValueError("Unbekanntes Seitenformat")
        if self.orientation not in ("auto", "portrait", "landscape"):
            raise ValueError("Unbekannte Seitenausrichtung")
        if not math.isfinite(self.margin_mm) or not 0 <= self.margin_mm <= 40:
            raise ValueError("Der Rand muss zwischen 0 und 40 mm liegen.")


def load_image_source(path, options=None, cancel=None):
    options = options or ImagePageOptions()
    path = Path(path).expanduser().resolve()
    if cancel and cancel():
        raise OperationCancelled()
    try:
        with path.open("rb") as stream:
            data = stream.read(32 * 1024 * 1024 + 1)
    except OSError as error:
        raise PdfWorkbenchError(f"Bild konnte nicht gelesen werden: {error}") from error
    if not data or len(data) > 32 * 1024 * 1024:
        raise PdfWorkbenchError("Das Bild ist leer oder größer als 32 MB.")
    source_buffer = QBuffer()
    source_buffer.setData(QByteArray(data))
    source_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(source_buffer)
    reader.setAutoTransform(True)
    if bytes(reader.format()).lower() not in (b"png", b"jpeg", b"jpg"):
        raise PdfWorkbenchError("Bitte eine PNG- oder JPEG-Datei auswählen.")
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > 16_000_000:
        raise PdfWorkbenchError("Das Bild ist ungültig oder hat mehr als 16 Millionen Bildpunkte.")
    image = reader.read()
    source_buffer.close()
    if image.isNull():
        raise PdfWorkbenchError("Das Bild konnte nicht vollständig gelesen werden: " + reader.errorString())
    if cancel and cancel():
        raise OperationCancelled()
    if options.paper == "image":
        # A stable physical size also handles images without usable DPI metadata.
        width, height = image.width() * 72 / 150, image.height() * 72 / 150
    else:
        width, height = (595.2756, 841.8898) if options.paper == "A4" else (612., 792.)
    landscape = options.orientation == "landscape" or (options.orientation == "auto" and image.width() > image.height())
    if landscape != (width > height):
        width, height = height, width
    margin = options.margin_mm * 72 / 25.4
    if width <= margin * 2 or height <= margin * 2:
        raise PdfWorkbenchError("Der gewählte Rand lässt keinen Platz für das Bild. Bitte den Rand verkleinern.")
    pdf_bytes = QByteArray()
    output = QBuffer(pdf_bytes)
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QPdfWriter(output)
    writer.setResolution(72)
    writer.setCreator("PDF Workbench")
    writer.setTitle(path.stem)
    layout = QPageLayout(QPageSize(QSizeF(width, height), QPageSize.Unit.Point), QPageLayout.Orientation.Portrait,
                         QMarginsF(options.margin_mm, options.margin_mm, options.margin_mm, options.margin_mm), QPageLayout.Unit.Millimeter)
    writer.setPageLayout(layout)
    if writer.width() < 1 or writer.height() < 1:
        del writer
        output.close()
        raise PdfWorkbenchError("Das Bildformat ist für eine PDF-Seite zu klein. Bitte A4 oder Letter verwenden.")
    painter = QPainter(writer)
    if not painter.isActive():
        raise PdfWorkbenchError("Die PDF-Seite für das Bild konnte nicht angelegt werden.")
    try:
        available = QRectF(0, 0, writer.width(), writer.height())
        factor = min(available.width() / image.width(), available.height() / image.height())
        target = QRectF((available.width() - image.width() * factor) / 2,
                        (available.height() - image.height() * factor) / 2,
                        image.width() * factor, image.height() * factor)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target, image)
    finally:
        painter.end()
    del painter
    del writer
    output.close()
    data = bytes(pdf_bytes)
    if cancel and cancel():
        raise OperationCancelled()
    pdf = PdfReader(io.BytesIO(data))
    if len(pdf.pages) != 1 or any(not math.isfinite(float(value)) or float(value) <= 0
                                  for value in (pdf.pages[0].mediabox.width, pdf.pages[0].mediabox.height)):
        raise PdfWorkbenchError("Das Bild konnte nicht als gültige PDF-Seite übernommen werden. Bitte A4 oder Letter verwenden.")
    page = pdf.pages[0]
    return SourceDocument(uuid4().hex, path, data, 1,
                          ((float(page.mediabox.width), float(page.mediabox.height)),))
