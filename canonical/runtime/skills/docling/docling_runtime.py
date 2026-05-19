#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
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
                        f"OCR.space configuration is not supported in Phase 1 ({path}:{dotted}); "
                        "future OCR.space adapters must be explicit and use OCR Engine 3."
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
