"""Small TrueType metrics reader and self-contained PDF text appearances.

The unchanged bundled Liberation Sans is embedded in full. No system-font
fallback or GUI/font service is involved. Complex-script shaping is outside this
first plain-text scope and is rejected explicitly rather than silently altered.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
import math
import struct
import unicodedata
from types import MappingProxyType

from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject
from .annotation_errors import AnnotationError

FONT_FILENAME = "LiberationSans-Regular.ttf"
MAX_TEXT_CHARS = 20_000


@dataclass(frozen=True)
class TextStyle:
    font_size: float = 12.0
    color: tuple[float, float, float] = (.08, .12, .20)
    alignment: str = "left"

    def __post_init__(self):
        if isinstance(self.font_size, bool) or not isinstance(self.font_size, (int, float)) or not math.isfinite(self.font_size) or not 4 <= self.font_size <= 288:
            raise AnnotationError("Die Schriftgröße muss zwischen 4 und 288 pt liegen.")
        if self.alignment not in ("left", "center", "right"):
            raise AnnotationError("Die Textausrichtung muss links, mittig oder rechts sein.")
        try:
            color = tuple(self.color)
            if len(color) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in color):
                raise ValueError
        except (TypeError, ValueError):
            raise AnnotationError("Die Farbe benötigt drei RGB-Werte zwischen 0 und 1.") from None
        object.__setattr__(self, "color", color)


@lru_cache(maxsize=1)
def font_metrics():
    data = files("pdf_workbench").joinpath("fonts", FONT_FILENAME).read_bytes()
    def u16(offset): return struct.unpack_from(">H", data, offset)[0]
    def i16(offset): return struct.unpack_from(">h", data, offset)[0]
    tables = {}
    for index in range(u16(4)):
        offset = 12 + index * 16
        tag, _, start, size = struct.unpack_from(">4sIII", data, offset)
        tables[tag] = (start, size)
    head, hhea, hmtx = (tables[t][0] for t in (b"head", b"hhea", b"hmtx"))
    units = u16(head + 18)
    glyph_count = u16(tables[b"maxp"][0] + 4)
    count = u16(hhea + 34)
    advances = [u16(hmtx + index*4) for index in range(count)]
    advances.extend([advances[-1]] * (glyph_count-count))
    cmap = {}
    base = tables[b"cmap"][0]
    # Prefer Unicode format 12; format 4 fills BMP coverage where necessary.
    for index in range(u16(base + 2)):
        platform, encoding, start = struct.unpack_from(">HHI", data, base+4+8*index)
        start += base
        if platform not in (0, 3):
            continue
        fmt = u16(start)
        if fmt == 12:
            groups = struct.unpack_from(">I", data, start+12)[0]
            for group in range(groups):
                low, high, glyph = struct.unpack_from(">III", data, start+16+group*12)
                for code in range(low, high+1):
                    cmap[code] = glyph + code-low
        elif fmt == 4:
            segments = u16(start+6)//2
            ends = start+14
            starts = ends+segments*2+2
            deltas = starts+segments*2
            ranges = deltas+segments*2
            for segment in range(segments):
                for code in range(u16(starts+segment*2), u16(ends+segment*2)+1):
                    delta, jump = i16(deltas+segment*2), u16(ranges+segment*2)
                    glyph = ((code+delta)&65535) if not jump else u16(ranges+segment*2+jump+2*(code-u16(starts+segment*2)))
                    if jump and glyph:
                        glyph = (glyph+delta)&65535
                    if glyph and code != 65535:
                        cmap.setdefault(code, glyph)
    return data, units, tuple(advances), MappingProxyType(cmap), tuple(i16(head+offset) for offset in (36, 38, 40, 42)), i16(hhea+4), i16(hhea+6)


def normalize_text(text: str) -> str:
    if not isinstance(text, str):
        raise AnnotationError("Der Textfeldinhalt muss Text sein.")
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    "))
    if not text.strip():
        raise AnnotationError("Bitte gib Text für das Textfeld ein.")
    if len(text) > MAX_TEXT_CHARS:
        raise AnnotationError("Ein Textfeld darf höchstens 20000 Zeichen enthalten.")
    cmap = font_metrics()[3]
    for char in text:
        if char == "\n":
            continue
        if ord(char) not in cmap or not cmap[ord(char)]:
            raise AnnotationError(f"Die mitgelieferte Schrift unterstützt {char!r} (U+{ord(char):04X}) nicht.")
        if unicodedata.category(char).startswith("C") or unicodedata.combining(char) or unicodedata.bidirectional(char) in ("R", "AL", "AN"):
            raise AnnotationError(f"Das Zeichen U+{ord(char):04X} benötigt eine derzeit nicht unterstützte Textdarstellung.")
    return text


def layout_text(text: str, width: float, height: float, size: float):
    """Return lines (text, measured width, baseline from bottom); reject overflow."""
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 for value in (width, height, size)):
        raise AnnotationError("Textfeld und Schriftgröße benötigen endliche positive Abmessungen.")
    _, units, advances, cmap, bbox, ascent, descent = font_metrics()
    # Some supported accents extend past hhea's nominal line metrics.
    ascent, descent = max(ascent, bbox[3]), min(descent, bbox[1])
    def measure(value):
        return sum(advances[cmap[ord(c)]] for c in value) * size / units
    lines = []
    for paragraph in text.split("\n"):
        line, measured = "", 0.0
        for char in paragraph:
            char_width = advances[cmap[ord(char)]] * size / units
            if char_width > width + .0001:
                raise AnnotationError("Das Textfeld ist für diese Schriftgröße zu schmal.")
            while measured + char_width > width + .0001 and line:
                boundary = line.rfind(" ")
                if boundary > 0 and line[:boundary].strip():
                    lines.append(line[:boundary])
                    line = line[boundary+1:]
                else:
                    lines.append(line)
                    line = ""
                measured = measure(line)
            line += char
            measured += char_width
        lines.append(line)
    leading = max(size * 1.2, (ascent-descent)*size/units)
    first = height - ascent * size / units
    if first + descent * size / units < -.001 or first - (len(lines)-1)*leading + descent*size/units < -.001:
        raise AnnotationError("Der Text passt nicht vollständig in das Textfeld. Bitte vergrößere es oder verkleinere die Schrift.")
    return tuple((line, measure(line), first-index*leading) for index, line in enumerate(lines))


def _stream(writer, data):
    stream = DecodedStreamObject()
    stream.set_data(data)
    return writer._add_object(stream.flate_encode())


def pdf_text(writer, text: str, style: TextStyle, width: float, height: float, user_unit: float = 1):
    """Return font resources and text operators in local bottom-left coordinates."""
    size = style.font_size / user_unit
    lines = layout_text(text, width, height, size)
    data, units, advances, cmap, bbox, ascent, descent = font_metrics()
    font_file = getattr(writer, "_pdf_workbench_font_file", None)
    if font_file is None:
        stream = DecodedStreamObject()
        stream.set_data(data)
        stream[NameObject("/Length1")] = NumberObject(len(data))
        font_file = writer._add_object(stream.flate_encode())
        writer._pdf_workbench_font_file = font_file
    scale = lambda value: FloatObject(value * 1000 / units)
    descriptor = DictionaryObject({NameObject("/Type"): NameObject("/FontDescriptor"), NameObject("/FontName"): NameObject("/LiberationSans"),
        NameObject("/Flags"): NumberObject(32), NameObject("/FontBBox"): ArrayObject(scale(v) for v in bbox),
        NameObject("/ItalicAngle"): NumberObject(0), NameObject("/Ascent"): scale(ascent), NameObject("/Descent"): scale(descent),
        NameObject("/CapHeight"): scale(ascent), NameObject("/StemV"): NumberObject(80), NameObject("/FontFile2"): font_file})
    chars = sorted(set(text) - {"\n"})
    codes = {char: index+1 for index, char in enumerate(chars)}
    glyphs = [0] + [cmap[ord(char)] for char in chars]
    glyph_map = _stream(writer, b"".join(struct.pack(">H", glyph) for glyph in glyphs))
    widths = ArrayObject([NumberObject(1), ArrayObject(scale(advances[glyph]) for glyph in glyphs[1:])])
    cid = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/CIDFontType2"),
        NameObject("/BaseFont"): NameObject("/LiberationSans"), NameObject("/CIDSystemInfo"): DictionaryObject({
            NameObject("/Registry"): TextStringObject("Adobe"), NameObject("/Ordering"): TextStringObject("Identity"), NameObject("/Supplement"): NumberObject(0)}),
        NameObject("/FontDescriptor"): writer._add_object(descriptor), NameObject("/CIDToGIDMap"): glyph_map, NameObject("/W"): widths})
    mappings = [f"<{codes[char]:04X}> <{char.encode('utf-16-be').hex().upper()}>" for char in chars]
    unicode = ["/CIDInit /ProcSet findresource begin", "12 dict begin begincmap", "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def", "/CMapName /WorkbenchUnicode def /CMapType 2 def", "1 begincodespacerange <0000> <FFFF> endcodespacerange"]
    for index in range(0, len(mappings), 100):
        batch = mappings[index:index+100]
        unicode += [f"{len(batch)} beginbfchar", *batch, "endbfchar"]
    unicode += ["endcmap CMapName currentdict /CMap defineresource pop end end"]
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type0"),
        NameObject("/BaseFont"): NameObject("/LiberationSans"), NameObject("/Encoding"): NameObject("/Identity-H"),
        NameObject("/DescendantFonts"): ArrayObject([writer._add_object(cid)]), NameObject("/ToUnicode"): _stream(writer, "\n".join(unicode).encode("ascii"))})
    resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F0"): writer._add_object(font)})})
    commands = ["BT", " ".join(str(v) for v in style.color)+" rg", f"/F0 {size:.7f} Tf"]
    for line, measured, baseline in lines:
        x = 0 if style.alignment == "left" else ((width-measured)/2 if style.alignment == "center" else width-measured)
        encoded = "".join(f"{codes[c]:04X}" for c in line)
        commands += [f"1 0 0 1 {x:.7f} {baseline:.7f} Tm", f"<{encoded}> Tj"]
    commands.append("ET")
    return resources, commands
