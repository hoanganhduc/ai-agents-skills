"""Guarded browser Print-to-PDF orchestration and strict stdlib verification.

The browser path delegates to :mod:`u2s.cdp`, so PDF printing uses the same
``Fetch`` request interception, URL/IP re-validation, timeout, process-tree
reaping, and profile cleanup as PNG capture. Verification is deliberately
conservative: a bounded artifact with a PDF header, classic cross-reference
table, trailer-rooted catalog, and internally consistent positive page tree can
be declared only ``STRUCTURALLY_VALID``. Structural inspection cannot establish
that rendered pages are visually nonblank, so the final verdict remains
``UNVERIFIED``. Unsupported or ambiguous PDF structures fail closed.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat as stat_module
from dataclasses import dataclass
from pathlib import Path

from . import __version__ as RUNTIME_VERSION
from . import capture, cdp, detect, naming, security

VERIFIED = "VERIFIED"
UNVERIFIED = "UNVERIFIED"
STRUCTURALLY_VALID = "STRUCTURALLY_VALID"
PASS = "PASS"
FAIL = "FAIL"

DEFAULT_MAX_PDF_BYTES = cdp.MAX_PDF_BYTES
MAX_PDF_OBJECTS = 200_000
MAX_PAGE_TREE_DEPTH = 128
MAX_PDF_PAGES = 50_000
MAX_CONTENT_STREAMS = 50_000
MAX_CONTENT_STREAM_BYTES = 64 * 1024 * 1024
MAX_PDF_DECIMAL_DIGITS = 20


@dataclass
class PrintPdfRequest:
    url: str
    out_path: str = ""
    media: str = "print"
    print_background: bool = True
    prefer_css_page_size: bool = True
    consent: bool = True
    wait_ms: int = 800
    timeout_ms: int = 30000
    max_bytes: int = DEFAULT_MAX_PDF_BYTES
    allow_private: bool = False
    allow_file_urls: bool = False
    same_origin_only: bool = False
    browser: str | None = None
    no_sandbox: bool = False


@dataclass
class PdfVerificationResult:
    final_verdict: str
    status: str
    checks: dict[str, str]
    bytes: int | None
    page_count: int | None
    sha256: str | None
    detail: str
    max_bytes: int

    @property
    def ok(self) -> bool:
        return self.final_verdict == VERIFIED

    @property
    def structurally_valid(self) -> bool:
        return self.status == STRUCTURALLY_VALID

    def to_dict(self) -> dict:
        return {
            "final_verdict": self.final_verdict,
            "status": self.status,
            "structurally_valid": self.structurally_valid,
            "checks": self.checks,
            "bytes": self.bytes,
            "page_count": self.page_count,
            "sha256": self.sha256,
            "detail": self.detail,
            "max_bytes": self.max_bytes,
        }


def _allocate_out_path(request: PrintPdfRequest, admission: security.AdmissionResult) -> str:
    if request.out_path:
        return request.out_path
    host = admission.host or "local-file"
    return str(naming.allocate_output(host).with_suffix(".pdf"))


def _resolve_browser(request: PrintPdfRequest) -> detect.BrowserInfo:
    # Reuse the capture runtime's explicit override semantics; the constructed
    # request is inert and does not navigate or launch anything.
    return capture.resolve_browser(
        capture.CaptureRequest(url=request.url, browser=request.browser)
    )


def run_print_pdf(request: PrintPdfRequest) -> dict:
    """Print one URL to PDF through the existing guarded CDP navigation path."""
    if request.media not in {"print", "screen"}:
        return {"status": cdp.BLOCKED_INPUT, "reason": "media must be 'print' or 'screen'"}
    if request.max_bytes <= 0 or request.max_bytes > cdp.MAX_PDF_BYTES:
        return {
            "status": cdp.BLOCKED_INPUT,
            "reason": f"PDF byte limit must be in 1..{cdp.MAX_PDF_BYTES}",
        }

    admission = security.validate_target_url(
        request.url,
        allow_private=request.allow_private,
        allow_file_urls=request.allow_file_urls,
    )
    browser = _resolve_browser(request)
    if browser.status != "available" or not browser.path:
        return {
            "status": cdp.BLOCKED_ENVIRONMENT,
            "reason": "no browser available; run doctor",
            "runtime_version": RUNTIME_VERSION,
            "same_origin_only": bool(request.same_origin_only),
            "origin_policy": (
                cdp.ORIGIN_POLICY_SCHEME_HOST_PORT
                if request.same_origin_only
                else "none"
            ),
            "url": security.redact_url(request.url),
        }
    browser.version = detect.probe_browser_version(browser.path)

    auto_no_sandbox, sandbox_reason = capture.detect_sandbox_disable()
    no_sandbox = request.no_sandbox or auto_no_sandbox
    if request.no_sandbox and not auto_no_sandbox:
        sandbox_reason = "explicit --no-sandbox flag"
    out_path = _allocate_out_path(request, admission)
    pin = capture.resolver_pin(admission)

    common = {
        "url": security.redact_url(request.url),
        "browser": browser.to_dict(),
        "runtime_version": RUNTIME_VERSION,
        "resolver_pin": pin,
        "private_targets_allowed": admission.private_targets_allowed,
        "file_urls_allowed": bool(request.allow_file_urls),
        "same_origin_only": bool(request.same_origin_only),
        "origin_policy": (
            cdp.ORIGIN_POLICY_SCHEME_HOST_PORT
            if request.same_origin_only
            else "none"
        ),
        "sandbox": "disabled" if no_sandbox else "enabled",
        "media": request.media,
        "print_background": request.print_background,
        "prefer_css_page_size": request.prefer_css_page_size,
        "max_bytes": request.max_bytes,
    }
    if sandbox_reason:
        common["sandbox_reason"] = sandbox_reason

    outcome = cdp.run_cdp_print_pdf(
        cdp.CdpPrintPdfRequest(
            browser_path=browser.path,
            url=request.url,
            out_path=out_path,
            media=request.media,
            print_background=request.print_background,
            prefer_css_page_size=request.prefer_css_page_size,
            consent=request.consent,
            wait_ms=request.wait_ms,
            timeout_ms=request.timeout_ms,
            max_bytes=request.max_bytes,
            no_sandbox=no_sandbox,
            allow_private=request.allow_private,
            allow_file_urls=request.allow_file_urls,
            same_origin_only=request.same_origin_only,
            resolver_pin=pin,
        )
    )
    if outcome.get("status"):
        return {**common, **outcome}

    payload = {**common, "status": "PDF_PRINTED", **outcome}
    try:
        if not Path(out_path).is_file():
            raise OSError("PDF artifact is missing")
        naming.write_result_sidecar(Path(out_path), payload)
    except OSError:
        return {
            **payload,
            "status": "BLOCKED_OUTPUT",
            "reason": "result sidecar could not be written",
        }
    return payload


class PdfStructureError(ValueError):
    """The bounded bytes do not form the conservative PDF subset we accept."""


_STARTXREF_RE = re.compile(rb"startxref\s+(\d+)\s+%%EOF\s*\Z")
_XREF_ENTRY_RE = re.compile(rb"(\d{10})\s+(\d{5})\s+([nf])(?:\s|\Z)")
_OBJECT_HEADER_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj\b")
_REF_RE = re.compile(rb"(\d+)\s+(\d+)\s+R\b")


def _bounded_decimal(raw: bytes, label: str) -> int:
    """Parse one PDF decimal token without entering unbounded big-int work."""

    if not raw or len(raw) > MAX_PDF_DECIMAL_DIGITS or not raw.isdigit():
        raise PdfStructureError(
            f"{label} exceeds the {MAX_PDF_DECIMAL_DIGITS}-digit numeric limit"
        )
    return int(raw)


def _read_line(data: bytes, position: int) -> tuple[bytes, int]:
    if position >= len(data):
        return b"", len(data)
    end = data.find(b"\n", position)
    if end < 0:
        return data[position:].rstrip(b"\r"), len(data)
    return data[position:end].rstrip(b"\r"), end + 1


def _parse_classic_xref(pdf_bytes: bytes) -> tuple[dict[tuple[int, int], bytes], tuple[int, int]]:
    """Parse one non-incremental classic xref and return objects + trailer root.

    XRef streams, hybrid references, and incremental updates are intentionally
    rejected. Chromium ``Page.printToPDF`` currently emits the classic form; a
    future unsupported form must become ``UNVERIFIED``, never a false pass.
    """
    match = _STARTXREF_RE.search(pdf_bytes)
    if match is None:
        raise PdfStructureError("missing terminal startxref/%%EOF")
    xref_offset = _bounded_decimal(match.group(1), "startxref offset")
    if xref_offset < 0 or xref_offset >= len(pdf_bytes):
        raise PdfStructureError("startxref offset is outside the file")

    line, position = _read_line(pdf_bytes, xref_offset)
    if line.strip() != b"xref":
        raise PdfStructureError("xref stream or invalid classic xref")

    entries: dict[tuple[int, int], int] = {}
    xref_rows = 0
    while True:
        if position >= len(pdf_bytes):
            raise PdfStructureError("xref has no trailer")
        line, position = _read_line(pdf_bytes, position)
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == b"trailer":
            break
        header = re.fullmatch(rb"(\d+)\s+(\d+)", stripped)
        if header is None:
            raise PdfStructureError("invalid xref subsection header")
        first = _bounded_decimal(header.group(1), "xref first object")
        count = _bounded_decimal(header.group(2), "xref subsection count")
        xref_rows += count
        if count < 0 or xref_rows > MAX_PDF_OBJECTS:
            raise PdfStructureError("invalid xref subsection size")
        for index in range(count):
            if position >= len(pdf_bytes):
                raise PdfStructureError("truncated xref subsection")
            entry_line, position = _read_line(pdf_bytes, position)
            entry = _XREF_ENTRY_RE.fullmatch(entry_line.strip())
            if entry is None:
                raise PdfStructureError("invalid xref entry")
            if entry.group(3) == b"n":
                ref = (
                    first + index,
                    _bounded_decimal(entry.group(2), "xref generation"),
                )
                if ref in entries:
                    raise PdfStructureError("duplicate live xref entry")
                entries[ref] = _bounded_decimal(entry.group(1), "xref object offset")

    trailer_end = pdf_bytes.find(b"startxref", position)
    if trailer_end < 0 or trailer_end != match.start():
        raise PdfStructureError("trailer is not followed by startxref")
    trailer = _pdf_code_bytes(pdf_bytes[position:trailer_end])
    if re.search(rb"/(?:Prev|XRefStm)\b", trailer):
        raise PdfStructureError("incremental or hybrid xref is unsupported")
    root_match = re.search(rb"/Root\s+(\d+)\s+(\d+)\s+R\b", trailer)
    if root_match is None:
        raise PdfStructureError("trailer has no catalog root")
    root = (
        _bounded_decimal(root_match.group(1), "catalog object number"),
        _bounded_decimal(root_match.group(2), "catalog generation"),
    )

    live_offsets = sorted((offset, ref) for ref, offset in entries.items())
    if len({offset for offset, _ in live_offsets}) != len(live_offsets):
        raise PdfStructureError("duplicate live object offsets")
    objects: dict[tuple[int, int], bytes] = {}
    for index, (offset, ref) in enumerate(live_offsets):
        if offset < len(b"%PDF-1.0") or offset >= xref_offset:
            raise PdfStructureError("live object offset is outside the body")
        boundary = live_offsets[index + 1][0] if index + 1 < len(live_offsets) else xref_offset
        if boundary <= offset or boundary > xref_offset:
            raise PdfStructureError("invalid object offset ordering")
        segment = pdf_bytes[offset:boundary]
        object_header = _OBJECT_HEADER_RE.match(segment)
        if object_header is None:
            raise PdfStructureError("xref does not point to an indirect object")
        actual_ref = (
            _bounded_decimal(object_header.group(1), "object number"),
            _bounded_decimal(object_header.group(2), "object generation"),
        )
        if actual_ref != ref:
            raise PdfStructureError("xref/object identity mismatch")
        end = segment.rfind(b"endobj")
        if end < object_header.end():
            raise PdfStructureError("indirect object has no endobj")
        if segment[end + len(b"endobj") :].strip():
            raise PdfStructureError("unexpected bytes after endobj")
        objects[ref] = segment[object_header.end() : end]

    if root not in objects:
        raise PdfStructureError("catalog root object is missing")
    return objects, root


def _pdf_code_bytes(data: bytes) -> bytes:
    """Blank PDF comments and string bodies while preserving token offsets.

    Structural regexes must never accept ``/Type`` or page-tree references that
    occur only inside a comment, literal string, or hex string.  This small
    lexer covers the bounded classic-xref subset accepted by this verifier; it
    is not a general PDF parser.
    """
    cleaned = bytearray(data)
    index = 0
    length = len(data)
    while index < length:
        byte = data[index]
        if byte == ord("%"):
            end = index
            while end < length and data[end] not in (10, 13):
                cleaned[end] = 32
                end += 1
            index = end
            continue
        if byte == ord("("):
            depth = 1
            cleaned[index] = 32
            index += 1
            while index < length and depth:
                byte = data[index]
                cleaned[index] = 32
                if byte == ord("\\"):
                    index += 1
                    if index < length:
                        cleaned[index] = 32
                elif byte == ord("("):
                    depth += 1
                elif byte == ord(")"):
                    depth -= 1
                index += 1
            continue
        if byte == ord("<") and index + 1 < length and data[index + 1] == ord("<"):
            index += 2
            continue
        if byte == ord("<"):
            cleaned[index] = 32
            index += 1
            while index < length:
                byte = data[index]
                cleaned[index] = 32
                index += 1
                if byte == ord(">"):
                    break
            continue
        index += 1
    return bytes(cleaned)


def _dictionary_prefix(body: bytes) -> bytes:
    # Page-tree keys live in the object dictionary before any stream data. This
    # avoids treating compressed content bytes as structural tokens. Lexically
    # blank comments/strings before scanning so they cannot forge structure.
    code = _pdf_code_bytes(body)
    marker = re.search(rb"\bstream(?:\r?\n|\r)", code)
    return code[: marker.start()] if marker else code


def _object_type(body: bytes) -> bytes | None:
    match = re.search(rb"/Type\s*/([A-Za-z]+)\b", _dictionary_prefix(body))
    return match.group(1) if match else None


def _validated_direct_stream_has_content(
    body: bytes, *, max_stream_bytes: int = MAX_CONTENT_STREAM_BYTES
) -> tuple[bool, int]:
    """Validate a direct-length stream and report whether its bytes are nonblank."""

    prefix = _dictionary_prefix(body)
    length_match = re.search(rb"/Length\s+(\d+)\b", prefix)
    if length_match is None:
        raise PdfStructureError("content stream lacks a direct Length")
    declared_length = _bounded_decimal(length_match.group(1), "stream Length")
    if declared_length > max_stream_bytes:
        raise PdfStructureError("content stream bytes exceed the aggregate work limit")
    code = _pdf_code_bytes(body)
    stream_match = re.search(rb"\bstream(?:\r\n|\n|\r)", code)
    if stream_match is None:
        raise PdfStructureError("content object has no stream body")
    data_start = stream_match.end()
    data_end = data_start + declared_length
    if data_end > len(body):
        raise PdfStructureError("content stream Length exceeds its object")
    suffix = body[data_end:]
    end_match = re.match(rb"(?:\r\n|\n|\r)?endstream\b", suffix)
    if end_match is None:
        raise PdfStructureError("content stream Length does not align with endstream")
    stream_bytes = body[data_start:data_end]
    has_content = bool(stream_bytes) and any(
        byte not in b"\x00\t\n\x0c\r " for byte in stream_bytes
    )
    return has_content, declared_length


def _parse_positive_page_count(pdf_bytes: bytes) -> tuple[int, int]:
    objects, root = _parse_classic_xref(pdf_bytes)
    catalog = _dictionary_prefix(objects[root])
    if _object_type(catalog) != b"Catalog":
        raise PdfStructureError("trailer root is not a catalog")
    pages_match = re.search(rb"/Pages\s+(\d+)\s+(\d+)\s+R\b", catalog)
    if pages_match is None:
        raise PdfStructureError("catalog has no page-tree reference")
    pages_root = (
        _bounded_decimal(pages_match.group(1), "page-tree object number"),
        _bounded_decimal(pages_match.group(2), "page-tree generation"),
    )

    visited: set[tuple[int, int]] = set()
    content_cache: dict[tuple[int, int], bool] = {}
    content_bytes_scanned = 0
    content_pages = 0
    page_visits = 0

    def walk(
        ref: tuple[int, int], parent: tuple[int, int] | None, depth: int = 0
    ) -> int:
        nonlocal content_bytes_scanned, content_pages, page_visits
        if depth > MAX_PAGE_TREE_DEPTH:
            raise PdfStructureError("page tree exceeds the depth limit")
        if ref in visited:
            raise PdfStructureError("page tree contains a cycle or duplicate child")
        body = objects.get(ref)
        if body is None:
            raise PdfStructureError("page tree references a missing object")
        visited.add(ref)
        prefix = _dictionary_prefix(body)
        kind = _object_type(prefix)
        if kind == b"Page":
            page_visits += 1
            if page_visits > MAX_PDF_PAGES:
                raise PdfStructureError("PDF page count exceeds the work limit")
            parent_match = re.search(rb"/Parent\s+(\d+)\s+(\d+)\s+R\b", prefix)
            if parent is None or parent_match is None:
                raise PdfStructureError("page leaf has no parent")
            actual_parent = (
                _bounded_decimal(parent_match.group(1), "page parent object number"),
                _bounded_decimal(parent_match.group(2), "page parent generation"),
            )
            if actual_parent != parent:
                raise PdfStructureError("page leaf parent does not match page tree")
            contents_match = re.search(rb"/Contents\s+(\d+)\s+(\d+)\s+R\b", prefix)
            if contents_match is not None:
                contents_ref = (
                    _bounded_decimal(
                        contents_match.group(1), "content-stream object number"
                    ),
                    _bounded_decimal(
                        contents_match.group(2), "content-stream generation"
                    ),
                )
                contents_body = objects.get(contents_ref)
                if contents_body is None:
                    raise PdfStructureError("page references a missing content stream")
                if contents_ref not in content_cache:
                    if len(content_cache) >= MAX_CONTENT_STREAMS:
                        raise PdfStructureError("content stream count exceeds the work limit")
                    remaining = MAX_CONTENT_STREAM_BYTES - content_bytes_scanned
                    has_content, stream_bytes = _validated_direct_stream_has_content(
                        contents_body, max_stream_bytes=max(0, remaining)
                    )
                    content_bytes_scanned += stream_bytes
                    content_cache[contents_ref] = has_content
                if content_cache[contents_ref]:
                    content_pages += 1
            return 1
        if kind != b"Pages":
            raise PdfStructureError("page tree child is neither Page nor Pages")

        count_match = re.search(rb"/Count\s+(\d+)\b", prefix)
        kids_match = re.search(rb"/Kids\s*\[(.*?)\]", prefix, re.DOTALL)
        if count_match is None or kids_match is None:
            raise PdfStructureError("Pages node lacks Count or Kids")
        declared = _bounded_decimal(count_match.group(1), "page Count")
        if declared > MAX_PDF_PAGES:
            raise PdfStructureError("PDF page count exceeds the work limit")
        refs = [
            (
                _bounded_decimal(match.group(1), "Kids object number"),
                _bounded_decimal(match.group(2), "Kids generation"),
            )
            for match in _REF_RE.finditer(kids_match.group(1))
        ]
        residue = _REF_RE.sub(b"", kids_match.group(1)).strip()
        if residue:
            raise PdfStructureError("Kids array contains unsupported tokens")
        computed = sum(walk(child, ref, depth + 1) for child in refs)
        if computed != declared:
            raise PdfStructureError("declared page count does not match page tree")
        return computed

    page_count = walk(pages_root, None)
    if page_count <= 0:
        raise PdfStructureError("PDF page count is not positive")
    return page_count, content_pages


_PDF_NAME_RE = re.compile(rb"/([^\x00\t\n\x0c\r ()<>\[\]{}/%]+)")
_PDF_NAME_ESCAPE_RE = re.compile(rb"#([0-9A-Fa-f]{2})")
_ACTIVE_CONTENT_NAMES = {
    b"OpenAction",
    b"AA",
    b"JS",
    b"JavaScript",
    b"Launch",
    b"EmbeddedFile",
    b"Filespec",
    b"RichMedia",
    b"XFA",
}


def _decoded_pdf_names(data: bytes) -> list[bytes]:
    """Return complete PDF name tokens with ``#xx`` escapes decoded."""

    names: list[bytes] = []
    for match in _PDF_NAME_RE.finditer(data):
        raw = match.group(1)
        decoded = _PDF_NAME_ESCAPE_RE.sub(
            lambda escape: bytes((int(escape.group(1), 16),)), raw
        )
        names.append(decoded)
    return names


def _has_active_content(pdf_bytes: bytes) -> bool:
    objects, _root = _parse_classic_xref(pdf_bytes)
    return any(
        name in _ACTIVE_CONTENT_NAMES
        for body in objects.values()
        for name in _decoded_pdf_names(_dictionary_prefix(body))
    )


def verify_pdf(pdf_bytes: bytes, *, max_bytes: int = DEFAULT_MAX_PDF_BYTES) -> PdfVerificationResult:
    """Verify bounded PDF bytes; fail closed on every unsupported structure."""
    size = len(pdf_bytes)
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    checks = {
        "signature": PASS if re.match(rb"%PDF-\d\.\d", pdf_bytes) else FAIL,
        "bounded_size": (
            PASS
            if 0 < max_bytes <= cdp.MAX_PDF_BYTES and 0 < size <= max_bytes
            else FAIL
        ),
        "page_count": FAIL,
        "content_evidence": FAIL,
        "active_content_absent": FAIL,
        "sha256": PASS if len(digest) == 64 else FAIL,
    }
    detail_parts: list[str] = []
    page_count: int | None = None

    if checks["signature"] == FAIL:
        detail_parts.append("missing or invalid PDF signature")
    if checks["bounded_size"] == FAIL:
        detail_parts.append(
            f"PDF size/limit is invalid (bytes={size}, max_bytes={max_bytes}, hard_cap={cdp.MAX_PDF_BYTES})"
        )
    if checks["signature"] == PASS and checks["bounded_size"] == PASS:
        try:
            page_count, content_pages = _parse_positive_page_count(pdf_bytes)
            checks["page_count"] = PASS
            if content_pages > 0:
                checks["content_evidence"] = PASS
            else:
                detail_parts.append("PDF pages contain no non-empty direct content stream")
            if not _has_active_content(pdf_bytes):
                checks["active_content_absent"] = PASS
            else:
                detail_parts.append("PDF contains an active-content or embedded-file action")
        except PdfStructureError as exc:
            detail_parts.append(str(exc))

    structurally_valid = all(value == PASS for value in checks.values())
    if structurally_valid:
        detail_parts.append(
            "structural validation cannot establish that rendered pages are visually nonblank"
        )
    return PdfVerificationResult(
        final_verdict=UNVERIFIED,
        status=STRUCTURALLY_VALID if structurally_valid else UNVERIFIED,
        checks=checks,
        bytes=size,
        page_count=page_count,
        sha256=digest,
        detail="; ".join(detail_parts),
        max_bytes=max_bytes,
    )


def verify_pdf_file(path: str | Path, *, max_bytes: int = DEFAULT_MAX_PDF_BYTES) -> PdfVerificationResult:
    """Read at most the configured bound and verify a local PDF artifact."""
    source = Path(path)
    if max_bytes <= 0 or max_bytes > cdp.MAX_PDF_BYTES:
        return PdfVerificationResult(
            final_verdict=UNVERIFIED,
            status=cdp.BLOCKED_INPUT,
            checks={"signature": FAIL, "bounded_size": FAIL, "page_count": FAIL, "content_evidence": FAIL, "active_content_absent": FAIL, "sha256": FAIL},
            bytes=None,
            page_count=None,
            sha256=None,
            detail=f"PDF byte limit must be in 1..{cdp.MAX_PDF_BYTES}",
            max_bytes=max_bytes,
        )
    descriptor: int | None = None
    try:
        if source.is_symlink():
            raise OSError("PDF path may not be a symlink")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(source, flags)
        metadata = os.fstat(descriptor)
        if not stat_module.S_ISREG(metadata.st_mode):
            raise OSError("PDF path is not a regular file")
        if metadata.st_size > max_bytes:
            return PdfVerificationResult(
                final_verdict=UNVERIFIED,
                status=UNVERIFIED,
                checks={"signature": FAIL, "bounded_size": FAIL, "page_count": FAIL, "content_evidence": FAIL, "active_content_absent": FAIL, "sha256": FAIL},
                bytes=metadata.st_size,
                page_count=None,
                sha256=None,
                detail=f"PDF size {metadata.st_size} exceeds byte limit {max_bytes}",
                max_bytes=max_bytes,
            )
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    except OSError as exc:
        return PdfVerificationResult(
            final_verdict=UNVERIFIED,
            status=cdp.BLOCKED_INPUT,
            checks={"signature": FAIL, "bounded_size": FAIL, "page_count": FAIL, "content_evidence": FAIL, "active_content_absent": FAIL, "sha256": FAIL},
            bytes=None,
            page_count=None,
            sha256=None,
            detail=str(exc),
            max_bytes=max_bytes,
        )
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(data) > max_bytes:
        return PdfVerificationResult(
            final_verdict=UNVERIFIED,
            status=UNVERIFIED,
            checks={"signature": FAIL, "bounded_size": FAIL, "page_count": FAIL, "content_evidence": FAIL, "active_content_absent": FAIL, "sha256": FAIL},
            bytes=len(data),
            page_count=None,
            sha256=None,
            detail=f"PDF grew beyond byte limit {max_bytes} while being read",
            max_bytes=max_bytes,
        )
    return verify_pdf(data, max_bytes=max_bytes)
