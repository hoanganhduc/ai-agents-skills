#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from docling_runtime import (
    DoclingRuntimeError,
    ocrspace_key_env,
    render_ocr_fallback_output,
    run_ocrspace_fallback,
    validate_remote_ocr_args,
)


SMOKE_LINES = [
    "AAS OCRSPACE SMOKE TEST 2026",
    "This synthetic PDF page verifies remote OCR fallback.",
]


def _run() -> tuple[int, dict]:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-remote-ocr",
        action="store_true",
        help="Required: allow upload of the generated synthetic smoke-test page to OCR.space.",
    )
    parser.add_argument("--ocrspace-dpi", type=int, default=200)
    parser.add_argument("--ocrspace-timeout", type=float, default=60.0)
    parser.add_argument("--ocrspace-language", default="eng")
    parser.add_argument(
        "--expected-token",
        action="append",
        help="Case-insensitive token expected in OCR output; repeat for multiple tokens. Default: SMOKE.",
    )
    args = parser.parse_args()

    remote_args = SimpleNamespace(
        ocr_fallback="ocrspace",
        allow_remote_ocr=args.allow_remote_ocr,
        ocrspace_max_pages=1,
        ocrspace_dpi=args.ocrspace_dpi,
        ocrspace_timeout=args.ocrspace_timeout,
        ocrspace_language=args.ocrspace_language,
    )
    validate_remote_ocr_args(remote_args)

    with tempfile.TemporaryDirectory(prefix="aas-docling-ocrspace-smoke-") as tmp:
        pdf_path = Path(tmp) / "ocrspace-smoke.pdf"
        write_synthetic_pdf(pdf_path)
        fallback = run_ocrspace_fallback(
            str(pdf_path),
            {"page_range": (1, 1), "max_num_pages": 1, "ocr_lang": [args.ocrspace_language]},
            remote_args,
            local_quality={"status": "forced-smoke", "passes": False},
            local_error=None,
        )

    text = render_ocr_fallback_output(fallback, "text")
    expected_tokens = args.expected_token or ["SMOKE"]
    upper_text = text.upper()
    token_checks = [
        {"token": token, "found": token.upper() in upper_text}
        for token in expected_tokens
    ]
    page_text_lengths = [
        {"page": item["page"], "characters": len(item.get("text", ""))}
        for item in fallback["pages"]
    ]
    ok = all(item["found"] for item in token_checks) and any(item["characters"] > 0 for item in page_text_lengths)
    result = {
        "schema_version": "docling-ocrspace-smoke.v1",
        "status": "ok" if ok else "degraded",
        "provider": fallback["provider"],
        "engine": fallback["engine"],
        "language": fallback["language"],
        "uploaded_pages": fallback["uploaded_pages"],
        "key_env": ocrspace_key_env(),
        "page_text_lengths": page_text_lengths,
        "expected_token_checks": token_checks,
    }
    return (0 if ok else 2), result


def write_synthetic_pdf(path: Path) -> None:
    stream_lines = ["BT", "/F1 34 Tf", "72 700 Td"]
    for index, line in enumerate(SMOKE_LINES):
        if index:
            stream_lines.append("0 -52 Td")
        stream_lines.append(f"({escape_pdf_text(line)}) Tj")
    stream_lines.append("ET")
    stream = "\n".join(stream_lines).encode("ascii")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode("ascii"))
        data.extend(obj)
        data.extend(b"\nendobj\n")
    xref_start = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    data.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode("ascii")
    )
    path.write_bytes(bytes(data))


def escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def main() -> int:
    try:
        returncode, payload = _run()
    except DoclingRuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
