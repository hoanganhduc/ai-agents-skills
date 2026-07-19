"""Dependency-light blank/near-uniform output detection.

A capture is "blank" when its byte length is below a floor, or when a decimated
its composited pixels are overwhelmingly a single dominant color. Detection runs on
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
# Fraction of analyzed composited pixels in one quantized color bin that marks blank.
DOMINANT_COLOR_BLANK_THRESHOLD = 0.985
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


def metrics_from_info(info: pngtools.PngInfo) -> BlankMetrics:
    """Classify one already-decoded PNG from its exact composited-color counts."""

    analyzed = info.analyzed_pixels
    if analyzed == 0:
        return BlankMetrics(
            is_blank=True,
            reason="no-pixels",
            width=info.width,
            height=info.height,
            byte_length=info.byte_length,
            dominant_color_fraction=1.0,
        )
    dominant = max(info.color_counts)
    fraction = dominant / analyzed
    blank = fraction >= DOMINANT_COLOR_BLANK_THRESHOLD
    return BlankMetrics(
        is_blank=blank,
        reason="near-uniform-color" if blank else "ok",
        width=info.width,
        height=info.height,
        byte_length=info.byte_length,
        dominant_color_fraction=fraction,
    )


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
    return metrics_from_info(info)
