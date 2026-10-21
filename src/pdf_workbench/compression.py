"""Local PDF compression with measured sizes and conservative image handling.

Lossless mode only compresses page streams and merges identical objects. The
other presets may resample ordinary RGB/gray images and use JPEG compression.
Masks, indexed/ICC/CMYK color, custom decode arrays and inline images are left
alone. Text, vectors, form appearances and Workbench annotation assets are not
rasterized. No preset can promise that every input will become smaller.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pypdf import PdfWriter

if TYPE_CHECKING:
    from .core import CancelCheck, Progress


@dataclass(frozen=True)
class CompressionOptions:
    preset: Literal["lossless", "balanced", "small"] = "lossless"

    def __post_init__(self) -> None:
        if self.preset not in ("lossless", "balanced", "small"):
            raise ValueError("Unbekannte Komprimierungsstufe.")


@dataclass(frozen=True)
class CompressionReport:
    baseline_bytes: int
    output_bytes: int
    images_changed: int = 0
    images_skipped: int = 0

    @property
    def saved_bytes(self) -> int:
        return self.baseline_bytes - self.output_bytes

    @property
    def saved_percent(self) -> float:
        return self.saved_bytes / self.baseline_bytes * 100 if self.baseline_bytes else 0.0


def _serialize(writer: PdfWriter) -> bytes:
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def _image_paths(container, ancestors=(), visited=None):
    """Enumerate XObjects without decoding inline or unsupported image data."""
    visited = set() if visited is None else visited
    identity = id(container)
    if identity in visited:
        return
    visited.add(identity)
    resources = container.get("/Resources")
    if not resources:
        return
    objects = resources.get_object().get("/XObject")
    if not objects:
        return
    for name, reference in objects.get_object().items():
        obj = reference.get_object()
        path = (*ancestors, name)
        if obj.get("/Subtype") == "/Image":
            yield path, obj
        elif obj.get("/Subtype") == "/Form":
            yield from _image_paths(obj, path, visited)


def _ordinary_image(obj) -> bool:
    # Replacing an image would discard additional dictionary semantics. Keep
    # those images intact instead of guessing how masks/color profiles behave.
    supported_keys = {"/Type", "/Subtype", "/Width", "/Height", "/ColorSpace",
                      "/BitsPerComponent", "/Filter", "/DecodeParms", "/Length"}
    return (set(obj).issubset(supported_keys)
            and obj.get("/ColorSpace") in ("/DeviceRGB", "/DeviceGray")
            and obj.get("/BitsPerComponent") == 8
            and 0 < int(obj.get("/Width", 0)) * int(obj.get("/Height", 0)) <= 40_000_000)


def compress_writer(writer: PdfWriter, options: CompressionOptions,
                    cancel: CancelCheck | None = None,
                    progress: Progress | None = None) -> tuple[bytes, CompressionReport]:
    """Return the smaller measured candidate; never touch the input file."""
    from .core import _check_cancel
    _check_cancel(cancel)
    baseline = _serialize(writer)
    changed = skipped = 0
    seen = set()
    for index, page in enumerate(writer.pages):
        _check_cancel(cancel)
        page.compress_content_streams(level=9)
        if options.preset != "lossless":
            # This cap is intentionally in pixels, not a claimed DPI: an image
            # can be used at several scales or through transformed form objects.
            maximum, quality = ((2200, 82) if options.preset == "balanced" else (1400, 65))
            for path, obj in _image_paths(page):
                _check_cancel(cancel)
                reference = getattr(obj, "indirect_reference", None)
                identity = (reference.idnum, reference.generation) if reference else id(obj)
                if identity in seen:
                    continue
                seen.add(identity)
                if not reference or not _ordinary_image(obj):
                    skipped += 1
                    continue
                original = writer._objects[reference.idnum - 1]
                try:
                    image = page.images[list(path)]
                    pixels = image.image
                    if pixels is None or pixels.mode not in ("RGB", "L"):
                        skipped += 1
                        continue
                    from PIL import Image
                    resized = pixels.copy()
                    resized.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
                    image.replace(resized, quality=quality, optimize=True)
                    replacement = reference.get_object()
                    # pypdf stores an image's encoded payload in _data. Only
                    # accept a smaller stream; restore the exact prior object
                    # otherwise. No source reader objects are modified.
                    if len(replacement._data) < len(original._data):
                        changed += 1
                    else:
                        writer._objects[reference.idnum - 1] = original
                except (ImportError, ValueError, TypeError, OSError, KeyError, NotImplementedError):
                    writer._objects[reference.idnum - 1] = original
                    skipped += 1
                _check_cancel(cancel)
        if progress:
            progress(85 + int((index + 1) / len(writer.pages) * 9),
                     f"Seite {index + 1} von {len(writer.pages)} komprimieren …")
    _check_cancel(cancel)
    writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    _check_cancel(cancel)
    candidate = _serialize(writer)
    _check_cancel(cancel)
    if len(candidate) >= len(baseline):
        return baseline, CompressionReport(len(baseline), len(baseline), 0, skipped)
    return candidate, CompressionReport(len(baseline), len(candidate), changed, skipped)
