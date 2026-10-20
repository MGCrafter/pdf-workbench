"""Bounded, immutable image snapshots. Only the existing Qt codec is needed."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import threading
import weakref

from .annotation_errors import AnnotationError

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 4_000_000
MAX_IMAGE_DIMENSION = 8192
_assets: weakref.WeakValueDictionary[str, ImageAsset] = weakref.WeakValueDictionary()
_lock = threading.RLock()


@dataclass(frozen=True)
class ImageAsset:
    """Canonical PNG, never a filename. Use load_image_asset to construct one."""
    data: bytes = field(repr=False)
    width: int
    height: int
    has_alpha: bool
    sha256: str

    def __post_init__(self):
        if type(self.data) is not bytes or not self.data or len(self.data) > MAX_IMAGE_BYTES:
            raise AnnotationError("Das Bild darf höchstens 8 MiB groß sein.")
        if (type(self.width) is not int or type(self.height) is not int or
                not 0 < self.width <= MAX_IMAGE_DIMENSION or not 0 < self.height <= MAX_IMAGE_DIMENSION or
                self.width * self.height > MAX_IMAGE_PIXELS or type(self.has_alpha) is not bool):
            raise AnnotationError("Das Bild darf höchstens 4 Millionen Pixel und 8192 Pixel je Kante haben.")
        if self.sha256 != hashlib.sha256(self.data).hexdigest():
            raise AnnotationError("Die Bildprüfsumme stimmt nicht mit den Bilddaten überein.")


def _decode(data: bytes):
    from PyQt6.QtCore import QByteArray, QBuffer, QIODevice
    from PyQt6.QtGui import QImage, QImageReader
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setDecideFormatFromContent(True)
    if bytes(reader.format()).lower() not in (b"png", b"jpeg", b"jpg"):
        raise AnnotationError("Bitte verwende ein PNG- oder JPEG-Bild.")
    size = reader.size()
    if (not size.isValid() or size.width() > MAX_IMAGE_DIMENSION or size.height() > MAX_IMAGE_DIMENSION or
            size.width() * size.height() > MAX_IMAGE_PIXELS):
        raise AnnotationError("Das Bild darf höchstens 4 Millionen Pixel und 8192 Pixel je Kante haben.")
    reader.setAutoTransform(True)  # Apply JPEG EXIF orientation before making the snapshot.
    image = reader.read()
    if image.isNull():
        raise AnnotationError("Das Bild konnte nicht vollständig gelesen werden.")
    if image.width() * image.height() > MAX_IMAGE_PIXELS:
        raise AnnotationError("Das Bild hat zu viele Pixel.")
    # Straight alpha and explicit sRGB make PDF RGB/SMask independent of the decoder's native layout.
    from PyQt6.QtGui import QColorSpace
    if image.colorSpace().isValid():
        image = image.convertedToColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return image


def load_image_asset(data: bytes) -> ImageAsset:
    """Decode PNG/JPEG, honor EXIF, remove metadata, freeze and deduplicate PNG bytes."""
    if not isinstance(data, (bytes, bytearray, memoryview)) or not 0 < len(data) <= MAX_IMAGE_BYTES:
        raise AnnotationError("Das Bild darf höchstens 8 MiB groß sein.")
    image = _decode(bytes(data))
    from PyQt6.QtCore import QByteArray, QBuffer, QIODevice
    from PyQt6.QtGui import QImage
    # A fresh image strips EXIF/text/DPI metadata; pixels have already been transformed.
    raw = image.constBits().asstring(image.sizeInBytes())
    clean = QImage(raw, image.width(), image.height(), image.bytesPerLine(), QImage.Format.Format_RGBA8888).copy()
    clean.setDotsPerMeterX(3780)
    clean.setDotsPerMeterY(3780)
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not clean.save(buffer, "PNG"):
        raise AnnotationError("Das Bild konnte nicht als PNG gespeichert werden.")
    canonical = bytes(encoded)
    if len(canonical) > MAX_IMAGE_BYTES:
        raise AnnotationError("Das normalisierte Bild überschreitet 8 MiB.")
    digest = hashlib.sha256(canonical).hexdigest()
    alpha = any(raw[index] != 255 for index in range(3, len(raw), 4))
    with _lock:
        existing = _assets.get(digest)
        if existing is not None:
            return existing
        result = ImageAsset(canonical, clean.width(), clean.height(), alpha, digest)
        _assets[digest] = result
        return result


def image_pixels(asset: ImageAsset) -> tuple[bytes, bytes | None]:
    """Revalidate the immutable snapshot before constructing PDF RGB/SMask streams."""
    image = _decode(asset.data)
    if (image.width(), image.height()) != (asset.width, asset.height):
        raise AnnotationError("Die Bildabmessungen stimmen nicht mit dem Modell überein.")
    raw = image.constBits().asstring(image.sizeInBytes())
    alpha = raw[3::4]
    has_alpha = any(value != 255 for value in alpha)
    if has_alpha != asset.has_alpha:
        raise AnnotationError("Die Bildtransparenz stimmt nicht mit dem Modell überein.")
    rgb = bytearray(asset.width * asset.height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = raw[0::4], raw[1::4], raw[2::4]
    return bytes(rgb), alpha if has_alpha else None


def restore_image_asset(data: bytes) -> ImageAsset:
    """Read a private PNG snapshot without changing its encoded bytes/hash."""
    if type(data) is not bytes or not 0 < len(data) <= MAX_IMAGE_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AnnotationError("Der eingebettete Bild-Snapshot ist ungültig.")
    digest = hashlib.sha256(data).hexdigest()
    with _lock:
        existing = _assets.get(digest)
        if existing is not None:
            return existing
    image = _decode(data)
    raw = image.constBits().asstring(image.sizeInBytes())
    result = ImageAsset(data, image.width(), image.height(), any(v != 255 for v in raw[3::4]), digest)
    with _lock:
        existing = _assets.get(digest)
        if existing is not None:
            return existing
        _assets[digest] = result
    return result
