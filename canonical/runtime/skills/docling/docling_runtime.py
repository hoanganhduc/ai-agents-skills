#!/usr/bin/env python3
from __future__ import annotations

import base64
import html
import io
import json
import os
import re
import sys
from pathlib import Path
from urllib import parse, request
from urllib.parse import urlparse


class DoclingRuntimeError(ValueError):
    """User-facing runtime configuration or policy error."""


DEFAULT_OPTIONS = {
    "pipeline": "standard",
    "ocr_mode": "auto",
    "ocr_engine": "auto",
    "ocr_lang": ["en"],
    "force_full_page_ocr": False,
    "tables": True,
    "table_mode": "accurate",
    "cell_matching": True,
    "document_timeout": None,
    "max_num_pages": None,
    "max_file_size": None,
    "page_range": None,
    "artifacts_path": None,
    "num_threads": None,
    "device": None,
}

BUILTIN_PRESETS = {
    "local-accurate": {
        "pipeline": "standard",
        "ocr_mode": "auto",
        "ocr_engine": "auto",
        "table_mode": "accurate",
        "cell_matching": True,
    },
    "local-fast": {
        "pipeline": "standard",
        "ocr_mode": "auto",
        "ocr_engine": "auto",
        "table_mode": "fast",
        "cell_matching": False,
    },
    "scan-heavy": {
        "pipeline": "standard",
        "ocr_mode": "always",
        "force_full_page_ocr": True,
        "ocr_engine": "auto",
        "table_mode": "accurate",
        "cell_matching": True,
    },
    "born-digital": {
        "pipeline": "standard",
        "ocr_mode": "auto",
        "force_full_page_ocr": False,
        "table_mode": "accurate",
    },
    "vlm-local": {
        "pipeline": "vlm",
        "table_mode": "accurate",
        "requires_artifacts": True,
    },
}

ALLOWED_TOP_LEVEL_KEYS = {"schema_version", "default_preset", "defaults", "presets"}
ALLOWED_OPTION_KEYS = set(DEFAULT_OPTIONS) | {"requires_artifacts"}
LOCAL_OCR_ENGINES = {"auto", "easyocr", "ocrmac", "rapidocr", "tesseract", "tesserocr"}
OCR_FALLBACKS = {"none", "ocrspace"}
DEFAULT_OCR_QUALITY_THRESHOLD = 0.55
DEFAULT_OCR_MIN_CHARS_PER_PAGE = 120
DEFAULT_OCR_MIN_ALNUM_RATIO = 0.35
DEFAULT_OCR_MAX_REPLACEMENT_RATIO = 0.02
DEFAULT_OCRSPACE_MAX_PAGES = 10
DEFAULT_OCRSPACE_DPI = 200
DEFAULT_OCRSPACE_TIMEOUT = 60.0
OCRSPACE_KEY_ENVS = ("OCRSPACE_API_KEY", "OCR_SPACE_API_KEY", "OCRSPACE_KEY", "OCR_SPACE_KEY")
OCRSPACE_ENDPOINT = "https://api.ocr.space/parse/image"
OCRSPACE_LANGUAGE_MAP = {
    "en": "eng",
    "eng": "eng",
    "fr": "fre",
    "fra": "fre",
    "fre": "fre",
    "de": "ger",
    "deu": "ger",
    "ger": "ger",
    "es": "spa",
    "it": "ita",
    "pt": "por",
    "vi": "vie",
    "zh": "chs",
    "zh-cn": "chs",
    "zh-tw": "cht",
}
REMOTE_SCHEMES = {"http", "https", "ftp", "ftps", "s3", "gs", "az", "file"}
REMOTE_REFERENCE_RE = re.compile(
    r"""(?ix)
    (?:src|href)\s*=\s*["']\s*(?:https?|ftp)://
    |
    \]\(\s*(?:https?|ftp)://
    """
)

REMOTE_CONFIG_KEYS = {
    "api_key",
    "apikey",
    "api-token",
    "api_token",
    "base64image",
    "base_url",
    "endpoint",
    "enable_remote_services",
    "headers",
    "iscreatesearchablepdf",
    "isoverlayrequired",
    "kserve",
    "ocrapi",
    "ocrengine",
    "ocrspace",
    "ocr_space",
    "password",
    "provider",
    "secret",
    "token",
    "url",
}


def add_common_arguments(parser) -> None:
    parser.add_argument("--config", help="Optional local Docling TOML config.")
    parser.add_argument("--preset", help="Builtin or config preset name.")
    parser.add_argument(
        "--allow-openclaw-config",
        action="store_true",
        help="Also discover OPENCLAW_WORKSPACE/config/docling.toml for legacy installs.",
    )
    parser.add_argument("--pipeline", choices=["standard", "auto", "vlm"], default=None)
    parser.add_argument("--ocr-mode", choices=["never", "auto", "always"], default=None)
    parser.add_argument("--ocr-engine", choices=sorted(LOCAL_OCR_ENGINES), default=None)
    parser.add_argument("--ocr-lang", action="append", help="OCR language code; repeat for multiple languages.")
    parser.add_argument("--force-full-page-ocr", action="store_true", default=None)
    parser.add_argument("--tables", dest="tables", action="store_true", default=None)
    parser.add_argument("--no-tables", dest="tables", action="store_false")
    parser.add_argument("--table-mode", choices=["fast", "accurate"], default=None)
    parser.add_argument("--cell-matching", dest="cell_matching", action="store_true", default=None)
    parser.add_argument("--no-cell-matching", dest="cell_matching", action="store_false")
    parser.add_argument("--document-timeout", type=float, default=None)
    parser.add_argument("--max-num-pages", type=int, default=None)
    parser.add_argument("--max-file-size", type=int, default=None)
    parser.add_argument("--page-range", default=None, help="Inclusive page range, for example 1-8.")
    parser.add_argument("--artifacts-path", default=None, help="Local Docling model artifact directory.")
    parser.add_argument("--num-threads", type=int, default=None)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda", "mps"], default=None)


def add_quality_arguments(parser) -> None:
    parser.add_argument("--ocr-quality-threshold", type=float, default=DEFAULT_OCR_QUALITY_THRESHOLD)
    parser.add_argument("--ocr-min-chars-per-page", type=int, default=DEFAULT_OCR_MIN_CHARS_PER_PAGE)
    parser.add_argument("--ocr-min-alnum-ratio", type=float, default=DEFAULT_OCR_MIN_ALNUM_RATIO)
    parser.add_argument("--ocr-max-replacement-ratio", type=float, default=DEFAULT_OCR_MAX_REPLACEMENT_RATIO)


def add_remote_ocr_arguments(parser) -> None:
    parser.add_argument("--ocr-fallback", choices=sorted(OCR_FALLBACKS), default="none")
    parser.add_argument(
        "--allow-remote-ocr",
        action="store_true",
        help="Allow explicit upload of selected local PDF pages to the configured remote OCR fallback.",
    )
    parser.add_argument("--ocrspace-max-pages", type=int, default=DEFAULT_OCRSPACE_MAX_PAGES)
    parser.add_argument("--ocrspace-dpi", type=int, default=DEFAULT_OCRSPACE_DPI)
    parser.add_argument("--ocrspace-timeout", type=float, default=DEFAULT_OCRSPACE_TIMEOUT)
    parser.add_argument("--ocrspace-language", default=None)
    parser.add_argument("--ocr-audit-output")
    add_quality_arguments(parser)


def resolve_runtime_options(args) -> dict:
    config = _load_config(args)
    options = dict(DEFAULT_OPTIONS)

    selected_preset = (
        getattr(args, "preset", None)
        or os.environ.get("AAS_DOCLING_PRESET")
        or os.environ.get("DOCLING_PRESET")
        or config.get("default_preset")
    )
    if selected_preset:
        options.update(_resolve_preset(selected_preset, config))

    options.update(config.get("defaults", {}))
    if selected_preset and selected_preset in config.get("presets", {}):
        options.update(config["presets"][selected_preset])

    _apply_env_overrides(options)
    _apply_cli_overrides(options, args)
    _normalize_options(options)
    _validate_options(options)
    _force_local_only_environment()
    return options


def validate_local_source(source: str) -> str:
    if _looks_remote(source):
        raise DoclingRuntimeError(f"remote source is not allowed for Docling runtime: {_redact(source)}")

    path = Path(source).expanduser()
    if path.exists() and path.is_file() and path.suffix.lower() in {".html", ".htm", ".md"}:
        try:
            sample = path.read_text(encoding="utf-8")[:1_000_000]
        except UnicodeDecodeError:
            sample = ""
        if REMOTE_REFERENCE_RE.search(sample):
            raise DoclingRuntimeError(
                f"local document contains remote references; inline or remove remote assets first: {path}"
            )
    return str(path)


def validate_output_path(output: str, *, overwrite: bool = False) -> Path:
    if _looks_remote(output):
        raise DoclingRuntimeError(f"remote output path is not allowed: {_redact(output)}")
    path = Path(output).expanduser()
    if path.exists():
        if path.is_symlink():
            raise DoclingRuntimeError(f"refusing to write through symlink output path: {path}")
        if path.is_dir():
            raise DoclingRuntimeError(f"output path is a directory: {path}")
        if not overwrite:
            raise DoclingRuntimeError(f"output path already exists; pass --overwrite to replace it: {path}")

    existing_parent = path.parent
    while not existing_parent.exists() and existing_parent != existing_parent.parent:
        existing_parent = existing_parent.parent
    if existing_parent.exists() and existing_parent.is_symlink():
        raise DoclingRuntimeError(f"refusing output path with symlinked parent: {existing_parent}")
    return path


def write_text_output(output: str, text: str, *, overwrite: bool = False) -> None:
    path = validate_output_path(output, overwrite=overwrite)
    mode = "w" if overwrite else "x"
    with path.open(mode, encoding="utf-8") as handle:
        handle.write(text)


def build_docling_converter(options: dict):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, VlmPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    if options["pipeline"] == "auto":
        return DocumentConverter()

    if options["pipeline"] == "vlm":
        from docling.pipeline.vlm_pipeline import VlmPipeline

        pipeline_options = VlmPipelineOptions(document_timeout=options.get("document_timeout"))
        _apply_common_pipeline_options(pipeline_options, options)
        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=pipeline_options,
                    pipeline_cls=VlmPipeline,
                )
            }
        )

    pipeline_options = PdfPipelineOptions(
        do_ocr=options["ocr_mode"] != "never",
        do_table_structure=bool(options["tables"]),
        document_timeout=options.get("document_timeout"),
    )
    _apply_common_pipeline_options(pipeline_options, options)
    _apply_table_options(pipeline_options, options)
    _apply_ocr_options(pipeline_options, options)
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)})


def conversion_kwargs(options: dict) -> dict:
    kwargs = {}
    for key in ("max_num_pages", "max_file_size", "page_range"):
        value = options.get(key)
        if value is not None:
            kwargs[key] = value
    return kwargs


def convert_with_options(converter, source: str, options: dict):
    return converter.convert(source, **conversion_kwargs(options))


def document_text(document) -> str:
    if hasattr(document, "export_to_text"):
        text = document.export_to_text()
        if text is not None:
            return str(text)
    texts = []
    for item in getattr(document, "texts", []) or []:
        value = getattr(item, "text", None)
        if value:
            texts.append(str(value))
    return "\n".join(texts)


def document_page_count(document) -> int:
    if hasattr(document, "num_pages"):
        try:
            value = getattr(document, "num_pages")
            if callable(value):
                value = value()
            return max(1, int(value))
        except Exception:
            return 1
    return 1


def document_quality_report(document, args) -> dict:
    return evaluate_ocr_quality(
        document_text(document),
        pages=document_page_count(document),
        threshold=float(args.ocr_quality_threshold),
        min_chars_per_page=int(args.ocr_min_chars_per_page),
        min_alnum_ratio=float(args.ocr_min_alnum_ratio),
        max_replacement_ratio=float(args.ocr_max_replacement_ratio),
    )


def evaluate_ocr_quality(
    text: str,
    *,
    pages: int,
    threshold: float = DEFAULT_OCR_QUALITY_THRESHOLD,
    min_chars_per_page: int = DEFAULT_OCR_MIN_CHARS_PER_PAGE,
    min_alnum_ratio: float = DEFAULT_OCR_MIN_ALNUM_RATIO,
    max_replacement_ratio: float = DEFAULT_OCR_MAX_REPLACEMENT_RATIO,
) -> dict:
    page_count = max(1, int(pages or 1))
    threshold = _ratio(threshold, "ocr_quality_threshold")
    min_alnum_ratio = _ratio(min_alnum_ratio, "ocr_min_alnum_ratio")
    max_replacement_ratio = _ratio(max_replacement_ratio, "ocr_max_replacement_ratio")
    min_chars_per_page = _positive_int(min_chars_per_page, "ocr_min_chars_per_page")

    text = text or ""
    stripped = text.strip()
    nonspace_chars = [char for char in stripped if not char.isspace()]
    nonspace_count = len(nonspace_chars)
    alnum_count = sum(1 for char in nonspace_chars if char.isalnum())
    replacement_count = stripped.count("\ufffd")
    control_count = sum(1 for char in stripped if ord(char) < 32 and char not in "\n\r\t")
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'_-]{1,}", stripped)

    chars_per_page = len(stripped) / page_count
    words_per_page = len(words) / page_count
    alnum_ratio = (alnum_count / nonspace_count) if nonspace_count else 0.0
    replacement_ratio = (replacement_count / max(1, len(stripped))) if stripped else 0.0
    control_ratio = (control_count / max(1, len(stripped))) if stripped else 0.0

    score_parts = [
        min(1.0, chars_per_page / min_chars_per_page),
        min(1.0, words_per_page / 20.0),
        min(1.0, alnum_ratio / min_alnum_ratio) if min_alnum_ratio else 1.0,
        max(0.0, 1.0 - (replacement_ratio / max_replacement_ratio)) if max_replacement_ratio else 1.0,
        max(0.0, 1.0 - (control_ratio / 0.02)),
    ]
    score = round(sum(score_parts) / len(score_parts), 4)
    reasons = []
    if not stripped:
        reasons.append("no extracted text")
    if chars_per_page < min_chars_per_page:
        reasons.append("low characters per page")
    if words_per_page < 20:
        reasons.append("low words per page")
    if alnum_ratio < min_alnum_ratio:
        reasons.append("low alphanumeric ratio")
    if replacement_ratio > max_replacement_ratio:
        reasons.append("high replacement-character ratio")
    if control_ratio > 0.02:
        reasons.append("high control-character ratio")

    return {
        "schema_version": "docling-ocr-quality.v1",
        "status": "ok" if score >= threshold and not reasons else "degraded",
        "passes": score >= threshold and not reasons,
        "score": score,
        "threshold": threshold,
        "pages": page_count,
        "characters": len(stripped),
        "characters_per_page": round(chars_per_page, 2),
        "words": len(words),
        "words_per_page": round(words_per_page, 2),
        "alnum_ratio": round(alnum_ratio, 4),
        "replacement_ratio": round(replacement_ratio, 4),
        "control_ratio": round(control_ratio, 4),
        "reasons": reasons,
        "thresholds": {
            "min_chars_per_page": min_chars_per_page,
            "min_alnum_ratio": min_alnum_ratio,
            "max_replacement_ratio": max_replacement_ratio,
        },
    }


def validate_remote_ocr_args(args) -> None:
    fallback = getattr(args, "ocr_fallback", "none")
    if fallback == "none":
        return
    if fallback not in OCR_FALLBACKS:
        raise DoclingRuntimeError(f"unsupported OCR fallback: {fallback}")
    if not getattr(args, "allow_remote_ocr", False):
        raise DoclingRuntimeError(f"OCR fallback {fallback} requires --allow-remote-ocr")
    if fallback == "ocrspace" and not ocrspace_key_env():
        joined = ", ".join(OCRSPACE_KEY_ENVS)
        raise DoclingRuntimeError(f"OCR.space fallback requires one of these environment variables: {joined}")
    _positive_int(getattr(args, "ocrspace_max_pages", DEFAULT_OCRSPACE_MAX_PAGES), "ocrspace_max_pages")
    dpi = _positive_int(getattr(args, "ocrspace_dpi", DEFAULT_OCRSPACE_DPI), "ocrspace_dpi")
    if dpi < 72 or dpi > 400:
        raise DoclingRuntimeError("ocrspace_dpi must be between 72 and 400")
    timeout = float(getattr(args, "ocrspace_timeout", DEFAULT_OCRSPACE_TIMEOUT))
    if timeout <= 0:
        raise DoclingRuntimeError("ocrspace_timeout must be positive")


def maybe_write_audit(args, audit: dict) -> None:
    output = getattr(args, "ocr_audit_output", None)
    if not output:
        return
    write_text_output(output, json.dumps(audit, ensure_ascii=False, indent=2) + "\n", overwrite=True)


def ocrspace_key_env() -> str | None:
    for name in OCRSPACE_KEY_ENVS:
        if os.environ.get(name):
            return name
    return None


def _ocrspace_key() -> str:
    env_name = ocrspace_key_env()
    if not env_name:
        joined = ", ".join(OCRSPACE_KEY_ENVS)
        raise DoclingRuntimeError(f"OCR.space API key not found; set one of: {joined}")
    return os.environ[env_name]


def run_ocrspace_fallback(source: str, options: dict, args, *, local_quality: dict | None, local_error: dict | None) -> dict:
    source_path = Path(source)
    if source_path.suffix.lower() != ".pdf":
        raise DoclingRuntimeError("OCR.space fallback currently supports local PDF sources only")
    pages = _ocrspace_pages(source_path, options, int(args.ocrspace_max_pages))
    language = _ocrspace_language(args, options)
    timeout = float(args.ocrspace_timeout)
    key = _ocrspace_key()
    page_results = []
    for page_number, image_bytes in render_pdf_pages(source_path, pages, int(args.ocrspace_dpi)):
        response = call_ocrspace_image(image_bytes, key=key, language=language, timeout=timeout)
        parsed_text, response_summary = parse_ocrspace_response(response)
        page_results.append({
            "page": page_number,
            "text": parsed_text,
            "response": response_summary,
        })
    return {
        "schema_version": "docling-ocr-fallback.v1",
        "provider": "ocrspace",
        "engine": 3,
        "language": language,
        "uploaded_pages": pages,
        "key_env": ocrspace_key_env(),
        "local_quality": local_quality,
        "local_error": local_error,
        "pages": page_results,
    }


def render_pdf_pages(source: Path, pages: list[int], dpi: int):
    try:
        import pypdfium2
    except Exception as exc:
        raise DoclingRuntimeError("OCR.space fallback requires pypdfium2 to render PDF pages") from exc
    try:
        document = pypdfium2.PdfDocument(str(source))
    except Exception as exc:
        raise DoclingRuntimeError(f"failed to open PDF for OCR.space fallback: {source}") from exc
    try:
        scale = dpi / 72.0
        for page_number in pages:
            page = document[page_number - 1]
            bitmap = None
            try:
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                yield page_number, buffer.getvalue()
            finally:
                if bitmap is not None:
                    close_bitmap = getattr(bitmap, "close", None)
                    if callable(close_bitmap):
                        close_bitmap()
                close_page = getattr(page, "close", None)
                if callable(close_page):
                    close_page()
    finally:
        document.close()


def call_ocrspace_image(image_bytes: bytes, *, key: str, language: str, timeout: float) -> dict:
    payload = parse.urlencode({
        "base64Image": "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii"),
        "language": language,
        "isOverlayRequired": "false",
        "OCREngine": "3",
    }).encode("utf-8")
    req = request.Request(
        OCRSPACE_ENDPOINT,
        data=payload,
        headers={
            "apikey": key,
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "ai-agents-skills-docling-ocrspace/1.0",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read(5_000_000)
            status = response.status
    except Exception as exc:
        raise DoclingRuntimeError(f"OCR.space request failed: {type(exc).__name__}: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as exc:
        raise DoclingRuntimeError(f"OCR.space returned non-JSON response with HTTP status {status}") from exc
    data["_http_status"] = status
    return data


def parse_ocrspace_response(payload: dict) -> tuple[str, dict]:
    parsed_results = payload.get("ParsedResults") or []
    if payload.get("IsErroredOnProcessing"):
        message = payload.get("ErrorMessage") or payload.get("ErrorDetails") or "OCR.space processing error"
        if isinstance(message, list):
            message = "; ".join(str(item) for item in message)
        raise DoclingRuntimeError(f"OCR.space processing failed: {str(message)[:300]}")
    texts = []
    for item in parsed_results:
        if isinstance(item, dict):
            texts.append(str(item.get("ParsedText") or ""))
    summary = {
        "http_status": payload.get("_http_status"),
        "ocr_exit_code": payload.get("OCRExitCode"),
        "is_errored_on_processing": payload.get("IsErroredOnProcessing"),
        "processing_time_ms": payload.get("ProcessingTimeInMilliseconds"),
        "parsed_results_count": len(parsed_results) if isinstance(parsed_results, list) else None,
        "parsed_text_lengths": [len(text) for text in texts],
    }
    return "\n".join(text.rstrip() for text in texts if text).strip(), summary


def render_ocr_fallback_output(fallback: dict, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(fallback, ensure_ascii=False, indent=2)
    if output_format == "html":
        sections = []
        for item in fallback["pages"]:
            sections.append(
                f"<section><h2>OCR.space page {item['page']}</h2><pre>"
                + html.escape(item.get("text", ""))
                + "</pre></section>"
            )
        return "\n".join(sections)
    separator = "\n\n" if output_format == "md" else "\n\f\n"
    chunks = []
    for item in fallback["pages"]:
        text = item.get("text", "").strip()
        if output_format == "md":
            chunks.append(f"## OCR.space page {item['page']}\n\n{text}")
        else:
            chunks.append(text)
    return separator.join(chunks).rstrip() + "\n"


def _ocrspace_pages(source: Path, options: dict, max_pages: int) -> list[int]:
    try:
        import pypdfium2
    except Exception as exc:
        raise DoclingRuntimeError("OCR.space fallback requires pypdfium2 to inspect PDF pages") from exc
    try:
        document = pypdfium2.PdfDocument(str(source))
    except Exception as exc:
        raise DoclingRuntimeError(f"failed to open PDF for OCR.space fallback: {source}") from exc
    try:
        total_pages = len(document)
    finally:
        document.close()
    if total_pages <= 0:
        raise DoclingRuntimeError("PDF has no pages for OCR.space fallback")
    start, end = 1, total_pages
    if options.get("page_range"):
        start, end = options["page_range"]
        if start > total_pages:
            raise DoclingRuntimeError(
                f"page_range starts at page {start}, but PDF has only {total_pages} pages"
            )
        end = min(end, total_pages)
    if options.get("max_num_pages"):
        end = min(end, start + int(options["max_num_pages"]) - 1)
    pages = list(range(start, end + 1))
    if not pages:
        raise DoclingRuntimeError("OCR.space fallback selected no PDF pages to upload")
    if len(pages) > max_pages:
        raise DoclingRuntimeError(
            f"OCR.space fallback would upload {len(pages)} pages; "
            f"raise --ocrspace-max-pages above {max_pages} or set --page-range"
        )
    return pages


def _ocrspace_language(args, options: dict) -> str:
    requested = getattr(args, "ocrspace_language", None)
    if requested:
        return str(requested)
    langs = options.get("ocr_lang") or ["en"]
    first = str(langs[0]).lower()
    return OCRSPACE_LANGUAGE_MAP.get(first, first)


def run_cli(callback) -> int:
    try:
        callback()
    except DoclingRuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def discover_config_path(args) -> Path | None:
    explicit = getattr(args, "config", None)
    if explicit:
        return Path(explicit).expanduser()

    for env_name in ("AAS_DOCLING_CONFIG", "DOCLING_CONFIG"):
        value = os.environ.get(env_name)
        if value:
            return Path(value).expanduser()

    workspace = os.environ.get("AAS_RUNTIME_WORKSPACE")
    if workspace:
        path = Path(workspace).expanduser() / "config" / "docling.toml"
        if path.exists():
            return path

    if getattr(args, "allow_openclaw_config", False):
        legacy_workspace = os.environ.get("OPENCLAW_WORKSPACE")
        if legacy_workspace:
            path = Path(legacy_workspace).expanduser() / "config" / "docling.toml"
            if path.exists():
                return path

    return None


def _load_config(args) -> dict:
    path = discover_config_path(args)
    if not path:
        return {}
    if _looks_remote(str(path)):
        raise DoclingRuntimeError(f"remote Docling config path is not allowed: {_redact(str(path))}")
    if not path.exists():
        raise DoclingRuntimeError(f"Docling config does not exist: {path}")
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - used on Python 3.10
        import tomli as tomllib

    with path.open("rb") as handle:
        config = tomllib.load(handle)
    if not isinstance(config, dict):
        raise DoclingRuntimeError(f"Docling config must be a TOML table: {path}")
    _validate_config_schema(config, path)
    return config


def _resolve_preset(name: str, config: dict) -> dict:
    if name in config.get("presets", {}):
        return dict(config["presets"][name])
    if name in BUILTIN_PRESETS:
        return dict(BUILTIN_PRESETS[name])
    raise DoclingRuntimeError(f"unknown Docling preset: {name}")


def _validate_config_schema(config: dict, path: Path) -> None:
    _reject_remote_config(config, path)
    unknown_top_level = set(config) - ALLOWED_TOP_LEVEL_KEYS
    if unknown_top_level:
        joined = ", ".join(sorted(unknown_top_level))
        raise DoclingRuntimeError(f"unsupported Docling config keys in {path}: {joined}")
    for section_name in ("defaults",):
        section = config.get(section_name, {})
        if section is not None and not isinstance(section, dict):
            raise DoclingRuntimeError(f"Docling config section [{section_name}] must be a table")
        _reject_unknown_options(section or {}, f"{path}:[{section_name}]")
    presets = config.get("presets", {})
    if presets is not None and not isinstance(presets, dict):
        raise DoclingRuntimeError("Docling config [presets] must be a table")
    for name, preset in (presets or {}).items():
        if not isinstance(preset, dict):
            raise DoclingRuntimeError(f"Docling preset {name!r} must be a table")
        _reject_unknown_options(preset, f"{path}:[presets.{name}]")


def _reject_unknown_options(options: dict, where: str) -> None:
    unknown = set(options) - ALLOWED_OPTION_KEYS
    if unknown:
        joined = ", ".join(sorted(unknown))
        raise DoclingRuntimeError(f"unsupported Docling options in {where}: {joined}")


def _reject_remote_config(value, path: Path, trail: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            normalized = _normalize_key(key_text)
            if normalized in REMOTE_CONFIG_KEYS:
                dotted = ".".join((*trail, key_text))
                if "ocrspace" in normalized or "ocr_space" in normalized or normalized == "ocrengine":
                    raise DoclingRuntimeError(
                        f"OCR.space configuration is not supported in Docling config ({path}:{dotted}); "
                        "use explicit CLI fallback flags: --ocr-fallback ocrspace --allow-remote-ocr."
                    )
                raise DoclingRuntimeError(f"remote or secret-bearing Docling config key is not allowed: {path}:{dotted}")
            _reject_remote_config(child, path, (*trail, key_text))
    elif isinstance(value, str):
        lower = value.lower()
        if "ocr.space" in lower or "ocrspace" in lower or lower.startswith(("http://", "https://", "ftp://")):
            dotted = ".".join(trail) or "<value>"
            raise DoclingRuntimeError(f"remote Docling config value is not allowed: {path}:{dotted}")


def _apply_env_overrides(options: dict) -> None:
    if os.environ.get("DOCLING_ARTIFACTS_PATH"):
        options["artifacts_path"] = os.environ["DOCLING_ARTIFACTS_PATH"]
    if os.environ.get("DOCLING_NUM_THREADS"):
        options["num_threads"] = _positive_int(os.environ["DOCLING_NUM_THREADS"], "DOCLING_NUM_THREADS")
    if os.environ.get("DOCLING_DEVICE"):
        options["device"] = os.environ["DOCLING_DEVICE"]


def _apply_cli_overrides(options: dict, args) -> None:
    legacy_ocr = getattr(args, "legacy_ocr", None)
    if legacy_ocr is not None:
        options["ocr_mode"] = "always" if legacy_ocr else "never"

    for key in DEFAULT_OPTIONS:
        value = getattr(args, key, None)
        if value is not None:
            options[key] = value


def _normalize_options(options: dict) -> None:
    options["pipeline"] = str(options.get("pipeline", "standard")).lower()
    options["ocr_mode"] = str(options.get("ocr_mode", "auto")).lower()
    options["ocr_engine"] = str(options.get("ocr_engine", "auto")).lower()
    options["table_mode"] = str(options.get("table_mode", "accurate")).lower()
    options["ocr_lang"] = _normalize_langs(options.get("ocr_lang"))
    if options.get("page_range") is not None:
        options["page_range"] = _parse_page_range(options["page_range"])
    for key in ("max_num_pages", "max_file_size", "num_threads"):
        if options.get(key) is not None:
            options[key] = _positive_int(options[key], key)
    if options.get("document_timeout") is not None:
        timeout = float(options["document_timeout"])
        if timeout <= 0:
            raise DoclingRuntimeError("document_timeout must be positive")
        options["document_timeout"] = timeout
    if options.get("artifacts_path"):
        path = Path(str(options["artifacts_path"])).expanduser()
        options["artifacts_path"] = str(path)


def _validate_options(options: dict) -> None:
    if options["pipeline"] not in {"standard", "auto", "vlm"}:
        raise DoclingRuntimeError(f"unsupported Docling pipeline: {options['pipeline']}")
    if options["ocr_mode"] not in {"never", "auto", "always"}:
        raise DoclingRuntimeError(f"unsupported OCR mode: {options['ocr_mode']}")
    if options["ocr_engine"] not in LOCAL_OCR_ENGINES:
        raise DoclingRuntimeError(f"unsupported local OCR engine: {options['ocr_engine']}")
    if options["table_mode"] not in {"fast", "accurate"}:
        raise DoclingRuntimeError(f"unsupported table mode: {options['table_mode']}")
    if options.get("device") and options["device"] not in {"auto", "cpu", "cuda", "mps"}:
        raise DoclingRuntimeError(f"unsupported Docling device: {options['device']}")
    if options.get("requires_artifacts") or options["pipeline"] == "vlm":
        artifact_path = options.get("artifacts_path")
        if not artifact_path:
            raise DoclingRuntimeError("vlm-local requires DOCLING_ARTIFACTS_PATH or artifacts_path")
        if not Path(artifact_path).exists():
            raise DoclingRuntimeError(f"Docling artifacts_path does not exist: {artifact_path}")


def _apply_common_pipeline_options(pipeline_options, options: dict) -> None:
    if options.get("artifacts_path") and hasattr(pipeline_options, "artifacts_path"):
        pipeline_options.artifacts_path = Path(options["artifacts_path"])
    if hasattr(pipeline_options, "enable_remote_services"):
        pipeline_options.enable_remote_services = False
    if hasattr(pipeline_options, "allow_external_plugins"):
        pipeline_options.allow_external_plugins = False
    accelerator = getattr(pipeline_options, "accelerator_options", None)
    if accelerator is not None:
        if options.get("num_threads") is not None and hasattr(accelerator, "num_threads"):
            accelerator.num_threads = options["num_threads"]
        if options.get("device") is not None and hasattr(accelerator, "device"):
            accelerator.device = options["device"]


def _apply_table_options(pipeline_options, options: dict) -> None:
    table_options = getattr(pipeline_options, "table_structure_options", None)
    if table_options is None:
        return
    if hasattr(table_options, "do_cell_matching"):
        table_options.do_cell_matching = bool(options["cell_matching"])
    if hasattr(table_options, "mode"):
        from docling.datamodel.pipeline_options import TableFormerMode

        table_options.mode = TableFormerMode.ACCURATE if options["table_mode"] == "accurate" else TableFormerMode.FAST


def _apply_ocr_options(pipeline_options, options: dict) -> None:
    if not getattr(pipeline_options, "do_ocr", False):
        return
    from docling.datamodel.pipeline_options import (
        EasyOcrOptions,
        OcrAutoOptions,
        OcrMacOptions,
        RapidOcrOptions,
        TesseractCliOcrOptions,
        TesseractOcrOptions,
    )

    kwargs = {
        "lang": _engine_langs(options["ocr_engine"], options["ocr_lang"]),
        "force_full_page_ocr": options["ocr_mode"] == "always" or bool(options["force_full_page_ocr"]),
    }
    engine = options["ocr_engine"]
    if engine == "auto":
        pipeline_options.ocr_options = OcrAutoOptions(**kwargs)
    elif engine == "easyocr":
        pipeline_options.ocr_options = EasyOcrOptions(download_enabled=False, **kwargs)
    elif engine == "rapidocr":
        pipeline_options.ocr_options = RapidOcrOptions(**kwargs)
    elif engine == "tesseract":
        pipeline_options.ocr_options = TesseractCliOcrOptions(**kwargs)
    elif engine == "tesserocr":
        pipeline_options.ocr_options = TesseractOcrOptions(**kwargs)
    elif engine == "ocrmac":
        pipeline_options.ocr_options = OcrMacOptions(**kwargs)


def _force_local_only_environment() -> None:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_DATASETS_OFFLINE", "1")


def _normalize_langs(value) -> list[str]:
    if value is None:
        return ["en"]
    if isinstance(value, str):
        raw = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple)):
        raw = []
        for item in value:
            if isinstance(item, str) and "," in item:
                raw.extend(part.strip() for part in item.split(","))
            else:
                raw.append(str(item).strip())
    else:
        raise DoclingRuntimeError("ocr_lang must be a string or list of strings")
    langs = [item for item in raw if item]
    return langs or ["en"]


def _engine_langs(engine: str, langs: list[str]) -> list[str]:
    if engine in {"tesseract", "tesserocr"}:
        mapping = {
            "en": "eng",
            "fr": "fra",
            "de": "deu",
            "es": "spa",
            "it": "ita",
            "pt": "por",
            "vi": "vie",
            "zh": "chi_sim",
        }
        return [mapping.get(lang, lang) for lang in langs]
    if engine == "rapidocr":
        mapping = {"en": "english", "zh": "chinese"}
        return [mapping.get(lang, lang) for lang in langs]
    if engine == "ocrmac":
        mapping = {"en": "en-US", "fr": "fr-FR", "de": "de-DE", "es": "es-ES"}
        return [mapping.get(lang, lang) for lang in langs]
    return langs


def _parse_page_range(value) -> tuple[int, int]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        start, end = value
    else:
        text = str(value).strip()
        separator = "-" if "-" in text else ":" if ":" in text else ","
        parts = [part.strip() for part in text.split(separator)]
        if len(parts) != 2:
            raise DoclingRuntimeError("page_range must be formatted as start-end")
        start, end = parts
    start_int = _positive_int(start, "page_range start")
    end_int = _positive_int(end, "page_range end")
    if end_int < start_int:
        raise DoclingRuntimeError("page_range end must be greater than or equal to start")
    return (start_int, end_int)


def _positive_int(value, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DoclingRuntimeError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise DoclingRuntimeError(f"{name} must be positive")
    return parsed


def _ratio(value, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise DoclingRuntimeError(f"{name} must be a number") from exc
    if parsed < 0 or parsed > 1:
        raise DoclingRuntimeError(f"{name} must be between 0 and 1")
    return parsed


def _looks_remote(value: str) -> bool:
    text = str(value)
    parsed = urlparse(text)
    if parsed.scheme.lower() in REMOTE_SCHEMES:
        return True
    return text.startswith("\\\\") or text.startswith("//")


def _normalize_key(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _redact(value: str) -> str:
    text = str(value)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)=([^&\s]+)", r"\1=<redacted>", text)
    return re.sub(r"(?i)(https?://)[^/\s]+", r"\1<remote-host>", text)
