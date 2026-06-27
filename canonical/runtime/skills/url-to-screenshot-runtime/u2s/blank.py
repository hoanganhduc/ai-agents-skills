"""Dependency-light blank/near-uniform output detection.

A capture is "blank" when its byte length is below a floor, or when a decimated
sample of its pixels is overwhelmingly a single dominant color. Detection runs on
raw decompressed PNG scanlines via ``u2s.pngtools`` (stdlib ``zlib``), so it never
needs Pillow. Used both post-capture in the engine and in the offline selftest on
in-memory synthesized PNGs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import pngtools

# Below this many bytes a capture cannot carry meaningful content (a 1x1 PNG is
# ~69 bytes). Real Chromium captures are kilobytes; this floor only catches a
# degenerate near-empty output.
MIN_BYTES_FLOOR = 100
# Fraction of sampled pixels sharing the single most common color that marks blank.
DOMINANT_COLOR_BLANK_THRESHOLD = 0.985
# Cap on sampled pixels so very large captures stay cheap.
MAX_SAMPLES = 20000

BLANK_OUTPUT = "BLANK_OUTPUT"


@dataclass
class BlankMetrics:
    is_blank: bool
    reason: str
    width: int = 0
    height: int = 0
    byte_length: int = 0
    dominant_color_fraction: float = 0.0
    decode_error: str = ""

    def to_dict(self) -> dict:
        return {
            "is_blank": self.is_blank,
            "reason": self.reason,
            "width": self.width,
            "height": self.height,
            "bytes": self.byte_length,
            "dominant_color_fraction": round(self.dominant_color_fraction, 6),
            "decode_error": self.decode_error,
        }


def is_blank(png_bytes: bytes) -> BlankMetrics:
    """Classify ``png_bytes`` as blank or not, with structured metrics."""
    byte_length = len(png_bytes)
    if byte_length < MIN_BYTES_FLOOR:
        return BlankMetrics(
            is_blank=True,
            reason="below-byte-floor",
            byte_length=byte_length,
            dominant_color_fraction=1.0,
        )
    try:
        info = pngtools.read_png(png_bytes)
    except ValueError as exc:
        # Undecodable output is treated as blank/unverifiable, never as a pass.
        return BlankMetrics(
            is_blank=True,
            reason="decode-failed",
            byte_length=byte_length,
            decode_error=str(exc),
        )

    counts: dict[tuple[int, int, int], int] = {}
    sampled = 0
    total_pixels = info.width * info.height
    step = max(1, total_pixels // MAX_SAMPLES)
    flat_index = 0
    for row in info.rows:
        for pixel in row:
            if flat_index % step == 0:
                counts[pixel] = counts.get(pixel, 0) + 1
                sampled += 1
            flat_index += 1
    if sampled == 0:
        return BlankMetrics(
            is_blank=True,
            reason="no-samples",
            width=info.width,
            height=info.height,
            byte_length=byte_length,
            dominant_color_fraction=1.0,
        )
    dominant = max(counts.values())
    fraction = dominant / sampled
    blank = fraction >= DOMINANT_COLOR_BLANK_THRESHOLD
    return BlankMetrics(
        is_blank=blank,
        reason="near-uniform-color" if blank else "ok",
        width=info.width,
        height=info.height,
        byte_length=byte_length,
        dominant_color_fraction=fraction,
    )
