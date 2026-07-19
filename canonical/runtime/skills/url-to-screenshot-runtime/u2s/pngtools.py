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
import time
import zlib

from .limits import (
    MAX_CAPTURE_PIXELS as MAX_PNG_PIXELS,
    MAX_PNG_CHUNKS,
    MAX_PNG_DECODE_SECONDS,
    MAX_PNG_DECOMPRESSED_BYTES,
    MAX_PNG_DIMENSION,
    MAX_PNG_FILE_BYTES,
    PNG_COLOR_BITS,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

class PngInputBlocked(ValueError):
    """A PNG exceeds a resource or structural input boundary."""


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
    """Bounded structural and exact composited-color view of one PNG."""

    __slots__ = (
        "width",
        "height",
        "byte_length",
        "bytes_per_pixel",
        "color_counts",
        "analyzed_pixels",
    )

    def __init__(
        self,
        width: int,
        height: int,
        byte_length: int,
        bytes_per_pixel: int,
        color_counts: list[int],
        analyzed_pixels: int,
    ):
        self.width = width
        self.height = height
        self.byte_length = byte_length
        self.bytes_per_pixel = bytes_per_pixel
        self.color_counts = color_counts
        self.analyzed_pixels = analyzed_pixels


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


def _expected_scanline_bytes(width: int, height: int, bpp: int) -> int:
    if width <= 0 or height <= 0:
        raise PngInputBlocked(f"invalid PNG dimensions {width}x{height}")
    if width > MAX_PNG_DIMENSION or height > MAX_PNG_DIMENSION:
        raise PngInputBlocked(
            f"PNG dimensions {width}x{height} exceed the {MAX_PNG_DIMENSION}-pixel dimension limit"
        )
    pixels = width * height
    if pixels > MAX_PNG_PIXELS:
        raise PngInputBlocked(
            f"PNG dimensions {width}x{height} exceed the {MAX_PNG_PIXELS}-pixel limit"
        )
    expected = height * (1 + width * bpp)
    if expected > MAX_PNG_DECOMPRESSED_BYTES:
        raise PngInputBlocked(
            f"PNG decompressed scanlines exceed {MAX_PNG_DECOMPRESSED_BYTES} bytes"
        )
    return expected


def _unfilter_line(
    line: bytearray,
    previous: bytearray,
    filter_type: int,
    bpp: int,
) -> None:
    """Reverse one PNG filter using only the current and previous rows."""

    if filter_type == 0:  # None
        return
    if filter_type == 1:  # Sub
        for index in range(bpp, len(line)):
            line[index] = (line[index] + line[index - bpp]) & 0xFF
        return
    if filter_type == 2:  # Up
        for index in range(len(line)):
            line[index] = (line[index] + previous[index]) & 0xFF
        return
    if filter_type == 3:  # Average
        for index in range(len(line)):
            left = line[index - bpp] if index >= bpp else 0
            line[index] = (line[index] + ((left + previous[index]) >> 1)) & 0xFF
        return
    if filter_type == 4:  # Paeth
        for index in range(len(line)):
            left = line[index - bpp] if index >= bpp else 0
            upper_left = previous[index - bpp] if index >= bpp else 0
            line[index] = (
                line[index] + _paeth(left, previous[index], upper_left)
            ) & 0xFF
        return
    raise ValueError(f"unsupported scanline filter {filter_type}")


class _ScanlineAnalyzer:
    """Incrementally decode, unfilter, and analyze with bounded working memory."""

    _INPUT_CHUNK = 64 * 1024
    _OUTPUT_CHUNK = 1024 * 1024

    def __init__(self, width: int, height: int, bpp: int):
        self.width = width
        self.height = height
        self.bpp = bpp
        self.row_bytes = width * bpp
        self.scanline_bytes = self.row_bytes + 1
        self.expected_bytes = _expected_scanline_bytes(width, height, bpp)
        self.decoder = zlib.decompressobj()
        self.pending = bytearray()
        self.previous = bytearray(self.row_bytes)
        self.decoded_bytes = 0
        self.row_index = 0
        self.deadline = time.monotonic() + MAX_PNG_DECODE_SECONDS
        self.color_counts = [0] * (1 << (3 * PNG_COLOR_BITS))
        self.analyzed_pixels = 0

    def _count_visible_pixels(self, line: bytearray) -> None:
        shift = 8 - PNG_COLOR_BITS
        for offset in range(0, len(line), self.bpp):
            red, green, blue = line[offset], line[offset + 1], line[offset + 2]
            if self.bpp == 4:
                alpha = line[offset + 3]
                red = (red * alpha + 255 * (255 - alpha) + 127) // 255
                green = (green * alpha + 255 * (255 - alpha) + 127) // 255
                blue = (blue * alpha + 255 * (255 - alpha) + 127) // 255
            bucket = (
                (red >> shift) << (2 * PNG_COLOR_BITS)
                | (green >> shift) << PNG_COLOR_BITS
                | (blue >> shift)
            )
            self.color_counts[bucket] += 1
            self.analyzed_pixels += 1

    def _accept_output(self, output: bytes) -> None:
        self.decoded_bytes += len(output)
        if self.decoded_bytes > self.expected_bytes:
            raise PngInputBlocked(
                f"PNG decompressed data exceeds expected scanline size {self.expected_bytes}"
            )
        self.pending.extend(output)
        while len(self.pending) >= self.scanline_bytes:
            if time.monotonic() > self.deadline:
                raise PngInputBlocked(
                    f"PNG decode exceeded the {MAX_PNG_DECODE_SECONDS:g}-second limit"
                )
            if self.row_index >= self.height:
                raise PngInputBlocked("PNG contains excess scanlines")
            filter_type = self.pending[0]
            line = bytearray(self.pending[1 : self.scanline_bytes])
            del self.pending[: self.scanline_bytes]
            _unfilter_line(line, self.previous, filter_type, self.bpp)
            self._count_visible_pixels(line)
            self.previous = line
            self.row_index += 1

    def feed(self, payload: memoryview) -> None:
        for start in range(0, len(payload), self._INPUT_CHUNK):
            remaining: bytes | memoryview = payload[start : start + self._INPUT_CHUNK]
            while remaining:
                if self.decoder.eof:
                    raise PngInputBlocked("PNG compressed stream contains trailing data")
                try:
                    output = self.decoder.decompress(remaining, self._OUTPUT_CHUNK)
                except zlib.error as exc:
                    raise ValueError(f"invalid compressed IDAT: {exc}") from exc
                remaining = self.decoder.unconsumed_tail
                self._accept_output(output)
                if self.decoder.unused_data:
                    raise PngInputBlocked("PNG compressed stream contains trailing data")

    def finish(self) -> tuple[list[int], int]:
        if not self.decoder.eof:
            raise ValueError("truncated compressed IDAT stream")
        if self.decoded_bytes != self.expected_bytes:
            raise ValueError(
                f"PNG decompressed data is {self.decoded_bytes} bytes; "
                f"expected {self.expected_bytes}"
            )
        if self.pending or self.row_index != self.height:
            raise ValueError("truncated PNG scanlines")
        expected_pixels = self.width * self.height
        if self.analyzed_pixels != expected_pixels:
            raise ValueError("PNG visual analysis accounting failed")
        return self.color_counts, self.analyzed_pixels


def _read_png(data: bytes) -> PngInfo:
    """Parse a bounded 8-bit RGB/RGBA PNG into compact scanline views.

    Raises ``ValueError`` on a non-PNG, an unsupported color type/bit depth, or a
    truncated stream. Supports 8-bit truecolor (type 2) and truecolor+alpha
    (type 6) with all five standard scanline filters, which covers both the
    engine's own synthesized PNGs (filter 0, type 2) and real Chromium captures
    (adaptive filtering, sometimes type 6).
    """
    if len(data) > MAX_PNG_FILE_BYTES:
        raise PngInputBlocked(
            f"PNG byte length {len(data)} exceeds limit {MAX_PNG_FILE_BYTES}"
        )
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG (bad signature)")
    offset = len(PNG_SIGNATURE)
    width = height = bit_depth = color_type = None
    analyzer: _ScanlineAnalyzer | None = None
    chunk_index = 0
    idat_seen = False
    idat_ended = False
    iend_seen = False
    plte_seen = False
    data_view = memoryview(data)
    while offset + 8 <= len(data):
        if chunk_index >= MAX_PNG_CHUNKS:
            raise PngInputBlocked(f"PNG exceeds the {MAX_PNG_CHUNKS}-chunk limit")
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        tag = data[offset + 4 : offset + 8]
        if any(not (65 <= value <= 90 or 97 <= value <= 122) for value in tag):
            raise ValueError("invalid PNG chunk type")
        if not 65 <= tag[2] <= 90:
            raise ValueError("invalid PNG reserved chunk-type bit")
        chunk_start = offset + 8
        chunk_end = chunk_start + length
        if chunk_end + 4 > len(data):
            raise ValueError("truncated PNG chunk")
        payload = data_view[chunk_start:chunk_end]
        expected_crc = struct.unpack(">I", data[chunk_end : chunk_end + 4])[0]
        actual_crc = zlib.crc32(tag)
        actual_crc = zlib.crc32(payload, actual_crc) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ValueError(f"invalid {tag.decode('ascii', 'replace')} CRC")
        if tag == b"IHDR":
            if chunk_index != 0 or width is not None:
                raise ValueError("IHDR must be the first and only IHDR chunk")
            if len(payload) != 13:
                raise ValueError("invalid IHDR length")
            width, height, bit_depth, color_type = struct.unpack(">IIBB", payload[:10])
            compression, filtering, interlace = payload[10], payload[11], payload[12]
            if compression != 0 or filtering != 0 or interlace != 0:
                raise ValueError("unsupported PNG compression, filter, or interlace method")
            if bit_depth != 8 or color_type not in _BPP:
                raise ValueError(
                    f"unsupported PNG (bit_depth={bit_depth}, color_type={color_type})"
                )
            analyzer = _ScanlineAnalyzer(width, height, _BPP[color_type])
        elif tag == b"IDAT":
            if analyzer is None:
                raise ValueError("IDAT appears before IHDR")
            if idat_ended:
                raise ValueError("IDAT chunks must be consecutive")
            idat_seen = True
            analyzer.feed(payload)
        elif tag == b"IEND":
            if len(payload) != 0:
                raise ValueError("invalid IEND length")
            if not idat_seen:
                raise ValueError("IEND appears before IDAT")
            iend_seen = True
            offset = chunk_end + 4
            break
        elif tag == b"PLTE":
            if (
                plte_seen
                or idat_seen
                or len(payload) == 0
                or len(payload) > 768
                or len(payload) % 3
            ):
                raise ValueError("invalid PLTE chunk")
            plte_seen = True
        elif 65 <= tag[0] <= 90:
            raise ValueError(f"unsupported critical PNG chunk {tag!r}")
        if idat_seen and tag != b"IDAT":
            idat_ended = True
        offset = chunk_end + 4  # skip the trailing CRC
        chunk_index += 1
    if width is None or height is None:
        raise ValueError("missing IHDR")
    if not idat_seen:
        raise ValueError("missing IDAT")
    if not iend_seen:
        raise ValueError("missing IEND")
    if offset != len(data):
        raise ValueError("trailing bytes after IEND")
    if analyzer is None or bit_depth is None or color_type is None:
        raise ValueError("missing PNG decoder state")
    color_counts, analyzed_pixels = analyzer.finish()
    return PngInfo(
        width,
        height,
        len(data),
        _BPP[color_type],
        color_counts,
        analyzed_pixels,
    )


def read_png(data: bytes) -> PngInfo:
    """Decode a PNG while translating allocation failures into a blocked input."""

    try:
        return _read_png(data)
    except MemoryError as exc:
        raise PngInputBlocked("PNG decode exceeded available memory") from exc
