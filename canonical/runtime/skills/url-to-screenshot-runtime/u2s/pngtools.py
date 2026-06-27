"""In-memory PNG synthesis and decode using the standard library only.

This is the single source of all PNG test bytes for both the offline
``selftest`` and the unit tests, so no binary fixture is ever committed. Only
``zlib`` and ``struct`` are used; nothing here touches the filesystem.

A synthesized PNG is a minimal but valid 8-bit RGB (color type 2) stream:
signature + IHDR + a single zlib-compressed IDAT (each scanline prefixed with a
filter-type-0 byte) + IEND. The reader parses IHDR for dimensions and
decompresses IDAT back to raw RGB scanlines, which is enough for the
blank-detector and the verify gate to run without Pillow.
"""

from __future__ import annotations

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def make_png(width: int, height: int, pixels: list[tuple[int, int, int]]) -> bytes:
    """Build a valid 8-bit RGB PNG from a flat ``width*height`` pixel list.

    ``pixels`` is row-major; pixel ``(x, y)`` is at index ``y * width + x``.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    if len(pixels) != width * height:
        raise ValueError(f"expected {width * height} pixels, got {len(pixels)}")
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0 (None) for this scanline
        for x in range(width):
            r, g, b = pixels[y * width + x]
            raw.extend((r & 0xFF, g & 0xFF, b & 0xFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    return PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def make_uniform_png(width: int, height: int, rgb: tuple[int, int, int] = (255, 255, 255)) -> bytes:
    """A solid single-color page (used to exercise the blank/near-uniform path)."""
    return make_png(width, height, [rgb] * (width * height))


def make_two_color_png(
    width: int,
    height: int,
    background: tuple[int, int, int] = (255, 255, 255),
    foreground: tuple[int, int, int] = (10, 20, 200),
    fill_fraction: float = 0.35,
) -> bytes:
    """A non-blank page: a foreground band over a background (a 'golden' page)."""
    pixels: list[tuple[int, int, int]] = []
    fg_rows = max(1, int(round(height * fill_fraction)))
    for y in range(height):
        row_color = foreground if y < fg_rows else background
        pixels.extend([row_color] * width)
    return make_png(width, height, pixels)


def make_tiny_png(rgb: tuple[int, int, int] = (255, 255, 255)) -> bytes:
    """A 1x1 page; its byte length is below any sane capture floor."""
    return make_uniform_png(1, 1, rgb)


class PngInfo:
    """Lightweight decoded view of a synthesized/real PNG."""

    __slots__ = ("width", "height", "byte_length", "rows")

    def __init__(self, width: int, height: int, byte_length: int, rows: list[list[tuple[int, int, int]]]):
        self.width = width
        self.height = height
        self.byte_length = byte_length
        self.rows = rows


# Bytes-per-pixel for the color types this reader supports (8-bit).
_BPP = {2: 3, 6: 4}  # RGB, RGBA


def _paeth(a: int, b: int, c: int) -> int:
    """PNG Paeth predictor (a=left, b=above, c=upper-left)."""
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter_scanlines(raw: bytes, width: int, height: int, bpp: int) -> list[bytearray]:
    """Reverse the per-scanline PNG filters (types 0-4) into raw byte rows.

    Real browsers emit adaptive filtering (Sub/Up/Average/Paeth), not only
    filter 0, so this reverses all five standard filter types per the PNG spec.
    Returns one ``bytearray`` of length ``width*bpp`` per row.
    """
    stride = width * bpp
    rows: list[bytearray] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        if pos >= len(raw):
            raise ValueError("truncated IDAT scanlines")
        filter_type = raw[pos]
        pos += 1
        line = bytearray(raw[pos : pos + stride])
        if len(line) != stride:
            raise ValueError("truncated scanline")
        pos += stride
        if filter_type == 0:  # None
            pass
        elif filter_type == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        else:
            raise ValueError(f"unsupported scanline filter {filter_type}")
        rows.append(line)
        prev = line
    return rows


def read_png(data: bytes) -> PngInfo:
    """Parse IHDR + IDAT of an 8-bit RGB/RGBA PNG into rows of (r, g, b) tuples.

    Raises ``ValueError`` on a non-PNG, an unsupported color type/bit depth, or a
    truncated stream. Supports 8-bit truecolor (type 2) and truecolor+alpha
    (type 6) with all five standard scanline filters, which covers both the
    engine's own synthesized PNGs (filter 0, type 2) and real Chromium captures
    (adaptive filtering, sometimes type 6).
    """
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG (bad signature)")
    offset = len(PNG_SIGNATURE)
    width = height = bit_depth = color_type = None
    idat = bytearray()
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        tag = data[offset + 4 : offset + 8]
        chunk_start = offset + 8
        chunk_end = chunk_start + length
        if chunk_end > len(data):
            raise ValueError("truncated PNG chunk")
        payload = data[chunk_start:chunk_end]
        if tag == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", payload[:10])
        elif tag == b"IDAT":
            idat.extend(payload)
        elif tag == b"IEND":
            break
        offset = chunk_end + 4  # skip the trailing CRC
    if width is None or height is None:
        raise ValueError("missing IHDR")
    if bit_depth != 8 or color_type not in _BPP:
        raise ValueError(f"unsupported PNG (bit_depth={bit_depth}, color_type={color_type})")
    bpp = _BPP[color_type]
    raw = zlib.decompress(bytes(idat))
    byte_rows = _unfilter_scanlines(raw, width, height, bpp)
    rows: list[list[tuple[int, int, int]]] = []
    for line in byte_rows:
        rows.append([(line[i], line[i + 1], line[i + 2]) for i in range(0, width * bpp, bpp)])
    return PngInfo(width, height, len(data), rows)
