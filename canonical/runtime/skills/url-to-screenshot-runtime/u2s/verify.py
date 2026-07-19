"""Artifact-truth verification gate (analog of ``tikz-draw approve``).

``capture`` produces a PNG but never declares success. ``verify`` is the only
thing allowed to declare a real screenshot done: ``final_verdict == "VERIFIED"``
only when file/decode/dimensions/not-blank/consent sub-checks all PASS; any other
state yields a structured ``BLOCKED_*`` / ``UNVERIFIED`` verdict and the failing
sub-check. "The file exists" or "Chromium exited 0" never constitutes success.

Runs entirely on PNG bytes; no browser, no network. Used by the offline selftest
on synthesized golden and blank PNGs.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field

from . import blank as blank_mod
from . import pngtools

VERIFIED = "VERIFIED"
UNVERIFIED = "UNVERIFIED"

PASS = "PASS"
FAIL = "FAIL"
SKIPPED = "SKIPPED"
BLOCKED_INPUT = "BLOCKED_INPUT"
MAX_PNG_FILE_BYTES = pngtools.MAX_PNG_FILE_BYTES


@dataclass
class VerifyResult:
    final_verdict: str
    checks: dict[str, str] = field(default_factory=dict)
    detail: dict[str, object] = field(default_factory=dict)
    status: str = ""

    @property
    def ok(self) -> bool:
        return self.final_verdict == VERIFIED

    def to_dict(self) -> dict:
        result = {
            "final_verdict": self.final_verdict,
            "checks": self.checks,
            "detail": self.detail,
        }
        if self.status:
            result["status"] = self.status
        return result


def read_png_file(path: str | os.PathLike[str]) -> bytes:
    """Read one stable, bounded regular file without following a symlink."""

    before = os.lstat(path)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise pngtools.PngInputBlocked("PNG path must be a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise pngtools.PngInputBlocked("PNG path must open as a regular file")
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise pngtools.PngInputBlocked("PNG path changed while it was opened")
        if opened.st_size > MAX_PNG_FILE_BYTES:
            raise pngtools.PngInputBlocked(
                f"PNG byte length {opened.st_size} exceeds limit {MAX_PNG_FILE_BYTES}"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(opened.st_size + 1)
        after = os.fstat(descriptor)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(after, field) != getattr(opened, field) for field in stable_fields):
            raise pngtools.PngInputBlocked("PNG file changed while it was read")
        if len(data) != opened.st_size:
            raise pngtools.PngInputBlocked("PNG file changed while it was read")
        return data
    finally:
        os.close(descriptor)


def verify_png(
    png_bytes: bytes,
    *,
    expected_width: int | None = None,
    expected_height: int | None = None,
    consent_removed: bool | None = None,
) -> VerifyResult:
    """Verify a captured PNG against the success criteria.

    ``consent_removed`` is ``None`` when consent handling was not requested
    (the sub-check is SKIPPED), ``True`` when an overlay was removed and the page
    is expected to be non-blank, ``False`` when consent was off.
    """
    checks: dict[str, str] = {}
    detail: dict[str, object] = {}

    # file / bytes
    if len(png_bytes) > MAX_PNG_FILE_BYTES:
        checks["file"] = FAIL
        detail["bytes"] = len(png_bytes)
        detail["limit"] = MAX_PNG_FILE_BYTES
        return VerifyResult(UNVERIFIED, checks, detail, BLOCKED_INPUT)
    if len(png_bytes) < blank_mod.MIN_BYTES_FLOOR:
        checks["file"] = FAIL
        detail["bytes"] = len(png_bytes)
        return VerifyResult(UNVERIFIED, checks, detail)
    checks["file"] = PASS
    detail["bytes"] = len(png_bytes)
    detail["sha256"] = hashlib.sha256(png_bytes).hexdigest()

    # decode
    try:
        info = pngtools.read_png(png_bytes)
    except ValueError as exc:
        checks["decode"] = FAIL
        detail["decode_error"] = str(exc)
        return VerifyResult(UNVERIFIED, checks, detail, BLOCKED_INPUT)
    checks["decode"] = PASS
    detail["width"] = info.width
    detail["height"] = info.height

    # dimensions
    dims_ok = True
    if expected_width is not None and info.width != expected_width:
        dims_ok = False
    if expected_height is not None and info.height != expected_height:
        dims_ok = False
    checks["dimensions"] = PASS if dims_ok else FAIL
    if not dims_ok:
        detail["expected"] = {"width": expected_width, "height": expected_height}
        return VerifyResult(UNVERIFIED, checks, detail)

    # not-blank
    metrics = blank_mod.metrics_from_info(info)
    detail["blank"] = metrics.to_dict()
    if metrics.is_blank:
        checks["not_blank"] = FAIL
        return VerifyResult(UNVERIFIED, checks, detail)
    checks["not_blank"] = PASS

    # consent
    if consent_removed is None or consent_removed is False:
        checks["consent"] = SKIPPED
    else:
        # Consent overlay was removed; the page must not have blanked as a result.
        checks["consent"] = PASS if not metrics.is_blank else FAIL
        if metrics.is_blank:
            return VerifyResult(UNVERIFIED, checks, detail)

    return VerifyResult(VERIFIED, checks, detail)
