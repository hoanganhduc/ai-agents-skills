#!/usr/bin/env python3
"""Codex runtime wrapper for the vnthuquan package."""

from __future__ import annotations

import json
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WRAPPER_VERSION = "0.1.0"
DOWNLOADABLE_FORMATS = {"epub", "pdf", "text", "audio"}
DISCOVERY_FORMATS = {*DOWNLOADABLE_FORMATS, "image"}


def configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def nested_runner_env() -> dict[str, str]:
    env = subprocess_env()
    prefixes = ("CODEX_RUN_SKILL_ARG_", "VNTHUQUAN_RUN_ARG_", "CLAUDE_RUN_ARG_")
    exact = {
        "CODEX_RUN_SKILL_ARG_COUNT",
        "VNTHUQUAN_RUN_ARG_COUNT",
        "CLAUDE_RUN_ARG_COUNT",
        "CLAUDE_RUN_SCRIPT",
    }
    for key in list(env):
        if key in exact or any(key.startswith(prefix) for prefix in prefixes):
            env.pop(key, None)
    return env


HOME = Path.home()
TARGET = os.environ.get("VNTHUQUAN_TARGET", "local-codex")
ASSISTANT_HOME = Path(os.environ.get("VNTHUQUAN_ASSISTANT_HOME", str(HOME / ".codex"))).expanduser()
RUN_DIR = Path(os.environ.get("VNTHUQUAN_RUN_DIR", str(ASSISTANT_HOME / "runs" / "vnthuquan"))).expanduser()
STATE_DIR = Path(os.environ.get("VNTHUQUAN_STATE_DIR", str(ASSISTANT_HOME / "state" / "vnthuquan"))).expanduser()
CONFIG_PATH = STATE_DIR / "config.json"
ARCHIVE_PATH = STATE_DIR / "downloads.jsonl"
CACHE_PATH = STATE_DIR / "http-cache.json"
SOURCE_DIR = Path(os.environ.get("VNTHUQUAN_SOURCE_DIR", "~/vnthuquan")).expanduser()
DEFAULT_DOWNLOAD_DIR = Path(os.environ.get("VNTHUQUAN_DOWNLOAD_DIR", str(HOME / "Downloads" / "vnthuquan"))).expanduser()
DEFAULT_QUEUE_JOBS = "3"
CALIBRE_RUNNER = Path(
    os.environ.get("VNTHUQUAN_CALIBRE_RUNNER", str(ASSISTANT_HOME / "runtime" / "run_skill.sh"))
).expanduser()
CALIBRE_SCRIPT = os.environ.get("VNTHUQUAN_CALIBRE_SCRIPT", "skills/calibre/run_cal.sh")
CALIBRE_CACHE_PATH = Path(
    os.environ.get(
        "VNTHUQUAN_CALIBRE_CACHE_PATH",
        str(ASSISTANT_HOME / "runtime" / "workspace" / "data" / "calibre" / "cache" / "library.json"),
    )
).expanduser()
CALIBRE_TIMEOUT_SECONDS = 30
CALIBRE_WRITE_TIMEOUT_SECONDS = int(os.environ.get("VNTHUQUAN_CALIBRE_WRITE_TIMEOUT_SECONDS", "180"))
HELP_FLAGS = {"-h", "--help"}
DOWNLOAD_SELECTOR_OPTIONS = {"--title", "--url", "--id"}
QUEUE_SELECTOR_OPTIONS = {"--query", "--category", "--author-id", "--title", "--url", "--id"}
QUEUE_SCOPE_OPTIONS = {"--limit", "--pages"}
NATIVE_HELP_COMMANDS = {
    "archive",
    "categories",
    "completion",
    "config",
    "doctor",
    "download",
    "formats",
    "list",
    "mirrors",
    "search",
    "show",
    "validate",
}


class WrapperError(Exception):
    def __init__(self, message: str, code: str = "wrapper_error", exit_code: int = 1):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


def ensure_dirs() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def default_config() -> dict[str, Any]:
    return {
        "default_mirror": "http://vietnamthuquan.eu",
        "download_dir": str(DEFAULT_DOWNLOAD_DIR),
        "archive_path": str(ARCHIVE_PATH),
        "timeout": 30.0,
        "retries": 2,
        "retry_backoff_seconds": 0.5,
        "retry_jitter_seconds": 0.1,
        "cache_ttl_seconds": 300.0,
        "cache_path": str(CACHE_PATH),
        "request_interval_seconds": 0.4,
        "filename_template": "{title} - {author} - vnthuquan",
    }


def ensure_config() -> None:
    ensure_dirs()
    if CONFIG_PATH.exists():
        return
    CONFIG_PATH.write_text(json.dumps(default_config(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_vnthuquan() -> tuple[list[str], str, str | None]:
    if os.name == "nt":
        windows_exe = HOME / ".vnthuquan_venv" / "Scripts" / "vnthuquan.exe"
        if windows_exe.is_file():
            return [str(windows_exe)], str(windows_exe), str(HOME / ".vnthuquan_venv" / "Scripts" / "python.exe")
        windows_python = HOME / ".vnthuquan_venv" / "Scripts" / "python.exe"
        if windows_python.is_file():
            return [str(windows_python), "-m", "vnthuquan"], f"{windows_python} -m vnthuquan", str(windows_python)
        python = shutil.which("python") or shutil.which("py")
        if python:
            return [python, "-m", "vnthuquan"], f"{python} -m vnthuquan", python
        raise WrapperError("vnthuquan command not found", "missing_executable", 127)

    candidates = [
        HOME / ".local" / "bin" / "vnthuquan",
        HOME / ".vnthuquan_venv" / "bin" / "vnthuquan",
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return [str(candidate)], str(candidate), shutil.which("python3")
    if SOURCE_DIR.is_dir():
        python = shutil.which("python3") or shutil.which("python")
        if python:
            return [python, "-m", "vnthuquan"], f"{python} -m vnthuquan", python
    raise WrapperError("vnthuquan command not found", "missing_executable", 127)


def run_pkg(args: list[str], *, json_mode: bool = True) -> tuple[int, str, str]:
    ensure_config()
    cmd, _, _ = resolve_vnthuquan()
    full = [*cmd, "--config", str(CONFIG_PATH), *args]
    if json_mode and "--json" not in full:
        full.append("--json")
    proc = subprocess.run(
        full,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        env=subprocess_env(),
    )
    return proc.returncode, proc.stdout, proc.stderr


def parse_json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WrapperError(f"Could not parse vnthuquan JSON output: {exc}", "bad_package_json", 3)
    if not isinstance(data, dict):
        raise WrapperError("vnthuquan JSON output was not an object", "bad_package_json", 3)
    return data


def package_version() -> str | None:
    try:
        cmd, _, _ = resolve_vnthuquan()
    except WrapperError:
        return None
    proc = subprocess.run(
        [*cmd, "--version"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        env=subprocess_env(),
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip().replace("vnthuquan ", "", 1)


def default_mirror() -> str | None:
    status, stdout, _ = run_pkg(["config", "show"], json_mode=True)
    if status != 0:
        return None
    data = parse_json(stdout)
    config = data.get("config") or {}
    return config.get("default_mirror")


def base_payload(command: str) -> dict[str, Any]:
    return {
        "target": TARGET,
        "command": command,
        "wrapper_version": WRAPPER_VERSION,
        "vnthuquan_version": package_version(),
    }


def emit_json(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", False) else int(payload.get("exit_code", 1))


def emit_text(payload: dict[str, Any]) -> int:
    if payload.get("ok"):
        command = payload.get("command")
        count = payload.get("count")
        if count is not None:
            print(f"{command}: {count} result(s)")
        elif command == "diagnose":
            ready = "ready" if payload.get("ready") else "not ready"
            print(f"vnthuquan {payload.get('vnthuquan_version')} ({ready})")
            print(f"executable: {payload.get('executable')}")
            print(f"config: {payload.get('config_path')}")
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"error: {payload.get('message')}", file=sys.stderr)
    return int(payload.get("exit_code", 1))


def finish(payload: dict[str, Any], json_out: bool) -> int:
    return emit_json(payload) if json_out else emit_text(payload)


def normalize_error(command: str, message: str, code: str, exit_code: int) -> dict[str, Any]:
    payload = base_payload(command)
    payload.update({"ok": False, "error_code": code, "message": message, "exit_code": exit_code})
    return payload


def require_success(command: str, args: list[str]) -> dict[str, Any]:
    status, stdout, stderr = run_pkg(args, json_mode=True)
    if status != 0:
        payload = normalize_error(command, stderr.strip() or stdout.strip(), "package_error", status)
        payload["package_stdout"] = stdout
        payload["package_stderr"] = stderr
        return payload
    data = parse_json(stdout)
    return data


def has_help(args: list[str]) -> bool:
    return any(arg in HELP_FLAGS for arg in args)


def has_option(args: list[str], option: str) -> bool:
    prefix = f"{option}="
    return any(arg == option or arg.startswith(prefix) for arg in args)


def consume_flag(args: list[str], flag: str) -> tuple[list[str], bool]:
    cleaned = [arg for arg in args if arg != flag]
    return cleaned, len(cleaned) != len(args)


def consume_option_value(args: list[str], option: str) -> tuple[list[str], str | None]:
    cleaned: list[str] = []
    value: str | None = None
    index = 0
    prefix = f"{option}="
    while index < len(args):
        arg = args[index]
        if arg == option:
            if index + 1 >= len(args):
                raise WrapperError(f"{option} requires a value", "usage", 2)
            value = args[index + 1]
            index += 2
            continue
        if arg.startswith(prefix):
            value = arg[len(prefix) :]
            index += 1
            continue
        cleaned.append(arg)
        index += 1
    return cleaned, value


def append_option_if_missing(args: list[str], option: str, value: str | None = None) -> list[str]:
    if has_option(args, option):
        return args
    if value is None:
        return [*args, option]
    return [*args, option, value]


def merge_batch_split_format_values(args: list[str]) -> list[str]:
    merged: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--format" and index + 1 < len(args):
            values = [args[index + 1]]
            index += 2
            while index < len(args) and args[index].lower() in DISCOVERY_FORMATS:
                values.append(args[index])
                index += 1
            merged.extend(["--format", ",".join(values)])
            continue
        merged.append(arg)
        index += 1
    return merged


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def default_queue_manifest_path() -> Path:
    return RUN_DIR / f"queue-{timestamp_slug()}.json"


def archive_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_run_json(prefix: str, payload: dict[str, Any]) -> str:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    path = RUN_DIR / f"{prefix}-{timestamp_slug()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path)


def append_state_jsonl(name: str, payload: dict[str, Any]) -> str:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / name
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return str(path)


def queue_summary_from_results(results: Any) -> dict[str, int]:
    if not isinstance(results, list):
        return {"total": 0, "succeeded": 0, "failed": 0, "skipped": 0}
    succeeded = 0
    failed = 0
    skipped = 0
    for item in results:
        if not isinstance(item, dict):
            failed += 1
            continue
        if item.get("skipped"):
            skipped += 1
        elif item.get("ok") is False or item.get("errors"):
            failed += 1
        elif item.get("path") or item.get("ok") is True:
            succeeded += 1
        else:
            failed += 1
    return {"total": len(results), "succeeded": succeeded, "failed": failed, "skipped": skipped}


def native_help(command: str, rest: list[str], json_out: bool) -> int:
    status, stdout, stderr = run_pkg([command, *rest], json_mode=False)
    if status != 0:
        return finish(normalize_error(command, stderr.strip() or stdout.strip(), "package_error", status), json_out)
    print(stdout, end="")
    return 0


def parse_optional_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def calibre_display_command(args: list[str]) -> list[str]:
    if os.name == "nt":
        ps_runner = CALIBRE_RUNNER.with_suffix(".ps1")
        if ps_runner.is_file():
            return ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_runner), CALIBRE_SCRIPT, *args]
        return ["cmd.exe", "/c", str(CALIBRE_RUNNER), CALIBRE_SCRIPT, *args]
    return ["bash", str(CALIBRE_RUNNER), CALIBRE_SCRIPT, *args]


def run_calibre(args: list[str], *, timeout: int = CALIBRE_TIMEOUT_SECONDS) -> dict[str, Any]:
    cmd = calibre_display_command(args)
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=timeout,
            env=nested_runner_env(),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timeout": True,
            "timeout_seconds": timeout,
            "command": cmd,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "command": cmd,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "json": parse_optional_json(proc.stdout),
    }


def normalize_text(value: str) -> str:
    value = value.replace("Đ", "D").replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", value)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return stripped.casefold()


def text_tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^0-9a-z]+", normalize_text(value)) if len(token) > 1}


def load_archive_record(path: Path) -> dict[str, Any] | None:
    if not ARCHIVE_PATH.is_file():
        return None
    try:
        target = path.resolve()
    except OSError:
        target = path
    matched: dict[str, Any] | None = None
    for line in ARCHIVE_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        output = record.get("output_path")
        if not isinstance(output, str):
            continue
        try:
            output_path = Path(output).expanduser().resolve()
        except OSError:
            output_path = Path(output).expanduser()
        if output_path == target:
            matched = record
    return matched


def calibre_cache_candidates(title: str, author: str | None, limit: int) -> dict[str, Any]:
    if not CALIBRE_CACHE_PATH.is_file():
        return {"ok": False, "source": str(CALIBRE_CACHE_PATH), "message": "Calibre cache not found", "results": []}
    try:
        cache = json.loads(CALIBRE_CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"ok": False, "source": str(CALIBRE_CACHE_PATH), "message": f"Could not parse cache: {exc}", "results": []}
    items = cache.get("items") if isinstance(cache, dict) else cache
    if not isinstance(items, list):
        return {"ok": False, "source": str(CALIBRE_CACHE_PATH), "message": "Calibre cache has no items list", "results": []}
    title_norm = normalize_text(title)
    title_tokens = text_tokens(title)
    author_norm = normalize_text(author or "")
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_title = normalize_text(str(item.get("title") or ""))
        item_authors = " ".join(str(author_value) for author_value in item.get("authors", []) if author_value)
        item_author_norm = normalize_text(item_authors)
        score = 0
        item_tokens = text_tokens(str(item.get("title") or ""))
        if title_norm and title_norm == item_title:
            score += 100
        elif title_tokens and item_tokens:
            overlap = len(title_tokens & item_tokens) / max(len(title_tokens), 1)
            if len(title_tokens) <= 2 and title_tokens == item_tokens:
                score += 80
            elif len(title_tokens) > 2 and overlap >= 0.8:
                score += 75
            elif len(title_tokens) > 2 and overlap >= 0.6 and author_norm and author_norm in item_author_norm:
                score += 60
        if score and author_norm and author_norm in item_author_norm:
            score += 25
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    results = [
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "authors": item.get("authors"),
            "year": item.get("year"),
            "formats": item.get("formats"),
            "score": score,
        }
        for score, item in scored[:limit]
    ]
    return {
        "ok": True,
        "source": str(CALIBRE_CACHE_PATH),
        "cache_updated": cache.get("updated") if isinstance(cache, dict) else None,
        "results": results,
        "count": len(results),
    }


def diagnose() -> dict[str, Any]:
    ensure_dirs()
    try:
        cmd, executable, python = resolve_vnthuquan()
        version = package_version()
        ready = bool(version)
    except WrapperError as exc:
        cmd, executable, python = [], None, shutil.which("python3")
        version = None
        ready = False
        error = str(exc)
    else:
        error = None
    if ready:
        ensure_config()
    payload = base_payload("diagnose")
    payload.update(
        {
            "ok": ready,
            "platform": sys.platform,
            "platform_detail": platform.platform(),
            "executable": executable,
            "resolved_command": cmd,
            "python": python,
            "assistant_home": str(ASSISTANT_HOME),
            "run_dir": str(RUN_DIR),
            "state_dir": str(STATE_DIR),
            "config_path": str(CONFIG_PATH),
            "config_scope": "wrapper-managed",
            "config_exists": CONFIG_PATH.exists(),
            "archive_path": str(ARCHIVE_PATH),
            "archive_scope": "wrapper-managed",
            "cache_path": str(CACHE_PATH),
            "cache_scope": "wrapper-managed",
            "source_dir": str(SOURCE_DIR),
            "calibre_runner": str(CALIBRE_RUNNER),
            "calibre_script": CALIBRE_SCRIPT,
            "calibre_cache_path": str(CALIBRE_CACHE_PATH),
            "default_mirror": default_mirror() if ready else None,
            "ready": ready,
        }
    )
    if error:
        payload["message"] = error
        payload["error_code"] = "missing_executable"
        payload["exit_code"] = 127
    return payload


def doctor() -> dict[str, Any]:
    diag = diagnose()
    payload = base_payload("doctor")
    payload["local_ready"] = bool(diag.get("ready"))
    payload["diagnose"] = diag
    if not diag.get("ready"):
        payload.update({"ok": False, "message": "diagnose failed", "exit_code": 127})
        return payload
    data = require_success("doctor", ["doctor", "--resources"])
    if data.get("ok") is False and "error_code" in data:
        return data
    payload.update(
        {
            "ok": bool(data.get("ok", False)),
            "live_site_ready": bool(data.get("ok", False)),
            "package_payload": data,
        }
    )
    return payload


def mirrors(args: list[str]) -> dict[str, Any]:
    if not args:
        return normalize_error("mirrors", "missing mirrors subcommand", "usage", 2)
    args, yes = consume_flag(args, "--yes")
    subcommand = args[0]
    if subcommand in {"use", "reset"} and not yes:
        return normalize_error("mirrors", f"mirrors {subcommand} requires --yes", "confirmation_required", 2)
    if subcommand not in {"list", "check", "use", "reset"}:
        return normalize_error("mirrors", f"unsupported mirrors subcommand: {subcommand}", "usage", 2)
    data = require_success("mirrors", ["mirrors", *args])
    if data.get("ok") is False and "error_code" in data:
        return data
    payload = base_payload("mirrors")
    payload["ok"] = bool(data.get("ok", True))
    payload["subcommand"] = subcommand
    payload["wrapper_consumed_flags"] = ["--yes"] if yes else []
    payload["default_mirror"] = default_mirror()
    mirrors_value = data.get("mirrors", [])
    if subcommand == "check":
        normalized = []
        for item in mirrors_value:
            if isinstance(item, dict):
                converted = dict(item)
                elapsed = converted.pop("elapsed_seconds", None)
                converted["latency_ms"] = round(float(elapsed) * 1000) if elapsed is not None else None
                normalized.append(converted)
        mirrors_value = normalized
    payload["mirrors"] = mirrors_value
    payload["count"] = len(mirrors_value) if isinstance(mirrors_value, list) else None
    payload["package_payload"] = data
    return payload


def config(args: list[str]) -> dict[str, Any]:
    if not args:
        return normalize_error("config", "missing config subcommand", "usage", 2)
    args, yes = consume_flag(args, "--yes")
    subcommand = args[0]
    if subcommand in {"set", "unset"} and not yes:
        return normalize_error("config", f"config {subcommand} requires --yes", "confirmation_required", 2)
    if subcommand not in {"path", "show", "set", "unset"}:
        return normalize_error("config", f"unsupported config subcommand: {subcommand}", "usage", 2)
    if subcommand == "path":
        payload = base_payload("config")
        payload.update(
            {
                "ok": True,
                "subcommand": "path",
                "config_path": str(CONFIG_PATH),
                "config_scope": "wrapper-managed",
            }
        )
        return payload
    if subcommand in {"set", "unset"}:
        data = require_success("config", ["config", *args])
        if data.get("ok") is False and "error_code" in data:
            return data
        payload = base_payload("config")
        payload.update(
            {
                "ok": bool(data.get("ok", True)),
                "subcommand": subcommand,
                "config_path": str(CONFIG_PATH),
                "config_scope": "wrapper-managed",
                "wrapper_consumed_flags": ["--yes"] if yes else [],
                "package_payload": data,
            }
        )
        return payload
    data = require_success("config", ["config", "show"])
    if data.get("ok") is False and "error_code" in data:
        return data
    payload = base_payload("config")
    payload.update(
        {
            "ok": True,
            "subcommand": "show",
            "config_path": str(CONFIG_PATH),
            "config_scope": "wrapper-managed",
            "config": data.get("config", {}),
            "package_payload": data,
        }
    )
    return payload


def categories(args: list[str]) -> dict[str, Any]:
    if not args:
        return normalize_error("categories", "missing categories subcommand", "usage", 2)
    subcommand = args[0]
    data = require_success("categories", ["categories", *args])
    if data.get("ok") is False and "error_code" in data:
        return data
    payload = base_payload("categories")
    payload.update({"ok": bool(data.get("ok", True)), "subcommand": subcommand, "package_payload": data})
    if "categories" in data:
        payload["categories"] = data["categories"]
        payload["count"] = len(data["categories"])
    if "category" in data:
        payload["category"] = data["category"]
    return payload


def formats() -> dict[str, Any]:
    data = require_success("formats", ["formats", "list"])
    if data.get("ok") is False and "error_code" in data:
        return data
    formats_value = data.get("formats", [])
    slugs = [item.get("slug") for item in formats_value if isinstance(item, dict)]
    payload = base_payload("formats")
    payload.update(
        {
            "ok": bool(data.get("ok", True)),
            "formats": formats_value,
            "count": len(formats_value),
            "downloadable": [slug for slug in slugs if slug in DOWNLOADABLE_FORMATS],
            "discovery_only": [slug for slug in slugs if slug and slug not in DOWNLOADABLE_FORMATS],
            "package_payload": data,
        }
    )
    return payload


def list_cmd(args: list[str]) -> dict[str, Any]:
    if not args:
        return normalize_error("list", "missing list subcommand", "usage", 2)
    subcommand = args[0]
    data = require_success("list", ["list", *args])
    if data.get("ok") is False and "error_code" in data:
        return data
    payload = base_payload("list")
    payload.update({"ok": bool(data.get("ok", True)), "subcommand": subcommand, "package_payload": data})
    for key in ("results", "books", "authors", "items"):
        if key in data and isinstance(data[key], list):
            payload["results"] = data[key]
            payload["count"] = len(data[key])
            break
    return payload


def display_query(args: list[str]) -> str:
    options_with_values = {
        "--title",
        "--author",
        "--author-id",
        "--category",
        "--field",
        "--format",
        "--limit",
        "--page",
        "--jobs",
        "--index",
        "--mirror",
        "--print",
    }
    flag_only = {"--all", "--exact", "--assets", "--links"}
    values: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in options_with_values:
            skip_next = True
            continue
        if arg in flag_only or arg.startswith("--"):
            continue
        values.append(arg)
    return " ".join(values)


def search(args: list[str]) -> dict[str, Any]:
    data = require_success("search", ["search", *args])
    if data.get("ok") is False and "error_code" in data:
        return data
    results = data.get("results", [])
    payload = base_payload("search")
    payload.update(
        {
            "ok": bool(data.get("ok", True)),
            "query": display_query(args),
            "results": results,
            "count": len(results) if isinstance(results, list) else None,
            "package_payload": data,
        }
    )
    return payload


def show(args: list[str]) -> dict[str, Any]:
    data = require_success("show", ["show", *args])
    if data.get("ok") is False and "error_code" in data:
        return data
    payload = base_payload("show")
    payload.update({"ok": bool(data.get("ok", True)), "package_payload": data})
    for key, value in data.items():
        if key not in {"ok"}:
            payload[key] = value
    return payload


def download(args: list[str]) -> dict[str, Any]:
    args, yes = consume_flag(args, "--yes")
    execute = has_option(args, "--execute")
    dry_run = has_option(args, "--dry-run")
    overwrite = has_option(args, "--overwrite")
    no_archive = has_option(args, "--no-archive")

    if execute and not yes:
        return normalize_error("download", "download --execute requires --yes", "confirmation_required", 2)
    if execute and overwrite and not yes:
        return normalize_error("download", "download --overwrite requires --yes", "confirmation_required", 2)
    if execute and no_archive:
        return normalize_error("download", "download execution must keep the wrapper-managed archive", "archive_required", 2)
    if has_option(args, "--all") and execute:
        return normalize_error("download", "use queue plus execute-queue for --all downloads", "usage", 2)

    raw_args = ["download", *args]
    if not execute:
        raw_args = append_option_if_missing(raw_args, "--dry-run")
        dry_run = True
    else:
        raw_args = append_option_if_missing(raw_args, "--archive-path", str(ARCHIVE_PATH))

    data = require_success("download", raw_args)
    if data.get("ok") is False and "error_code" in data:
        return data

    path_value = data.get("path")
    path = Path(path_value).expanduser() if isinstance(path_value, str) else None
    payload = base_payload("download")
    payload.update(
        {
            "ok": bool(data.get("ok", True)),
            "dry_run": bool(dry_run and not execute),
            "executed": bool(execute),
            "path": path_value,
            "path_exists": path.is_file() if path else False,
            "sha256": archive_sha256(path) if path else None,
            "plan": data.get("plan"),
            "validation": data.get("validation"),
            "manifest_path": data.get("manifest_path"),
            "archive_path": data.get("archive_path") or (str(ARCHIVE_PATH) if execute else None),
            "warnings": data.get("warnings", []),
            "errors": data.get("errors", []),
            "wrapper_consumed_flags": ["--yes"] if yes else [],
            "forwarded_args": raw_args,
            "package_payload": data,
        }
    )
    return payload


def queue(args: list[str]) -> dict[str, Any]:
    args, yes = consume_flag(args, "--yes")
    if has_option(args, "--execute"):
        return normalize_error("queue", "queue creates a dry-run manifest only; use execute-queue to download", "usage", 2)
    if has_option(args, "--from-manifest"):
        return normalize_error("queue", "use execute-queue for an existing manifest", "usage", 2)
    if not any(has_option(args, option) for option in QUEUE_SELECTOR_OPTIONS):
        return normalize_error("queue", "queue requires --query, --category, --author-id, --title, --url, or --id", "usage", 2)
    listing_queue = any(has_option(args, option) for option in {"--query", "--category", "--author-id"})
    if listing_queue and not any(has_option(args, option) for option in QUEUE_SCOPE_OPTIONS):
        return normalize_error("queue", "listing queues require --limit or --pages to bound the crawl", "usage", 2)

    args, manifest_value = consume_option_value(args, "--manifest")
    manifest_path = Path(manifest_value).expanduser() if manifest_value else default_queue_manifest_path()
    if manifest_path.exists() and not yes:
        return normalize_error("queue", f"manifest already exists and requires --yes to overwrite: {manifest_path}", "confirmation_required", 2)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    raw_args = ["download", *args]
    raw_args = append_option_if_missing(raw_args, "--all")
    raw_args = append_option_if_missing(raw_args, "--dry-run")
    raw_args = append_option_if_missing(raw_args, "--manifest", str(manifest_path))

    data = require_success("queue", raw_args)
    if data.get("ok") is False and "error_code" in data:
        return data
    queue_data = data.get("queue") if isinstance(data.get("queue"), dict) else {}
    items = queue_data.get("items", []) if isinstance(queue_data, dict) else []
    payload = base_payload("queue")
    payload.update(
        {
            "ok": bool(data.get("ok", True)),
            "dry_run": True,
            "manifest_path": data.get("manifest_path") or str(manifest_path),
            "manifest_exists": manifest_path.is_file(),
            "manifest_sha256": archive_sha256(manifest_path),
            "count": len(items) if isinstance(items, list) else None,
            "items": items,
            "source": queue_data.get("source") if isinstance(queue_data, dict) else None,
            "wrapper_consumed_flags": ["--yes"] if yes else [],
            "forwarded_args": raw_args,
            "package_payload": data,
        }
    )
    return payload


def execute_queue(args: list[str]) -> dict[str, Any]:
    args, yes = consume_flag(args, "--yes")
    if not yes:
        return normalize_error("execute-queue", "execute-queue requires --yes", "confirmation_required", 2)
    if not args:
        return normalize_error("execute-queue", "missing queue manifest path", "usage", 2)
    if has_option(args, "--dry-run"):
        return normalize_error("execute-queue", "use queue for dry-run manifest creation", "usage", 2)
    if has_option(args, "--no-archive"):
        return normalize_error("execute-queue", "queue execution must keep the wrapper-managed archive", "archive_required", 2)

    args, from_manifest_value = consume_option_value(args, "--from-manifest")
    if from_manifest_value:
        manifest = from_manifest_value
    else:
        manifest = args[0]
        args = args[1:]
    manifest_path = Path(manifest).expanduser()
    if not manifest_path.is_file():
        return normalize_error("execute-queue", f"queue manifest not found: {manifest_path}", "missing_manifest", 2)

    raw_args = ["download", "--from-manifest", str(manifest_path), *args]
    raw_args = append_option_if_missing(raw_args, "--execute")
    raw_args = append_option_if_missing(raw_args, "--archive-path", str(ARCHIVE_PATH))
    raw_args = append_option_if_missing(raw_args, "--jobs", DEFAULT_QUEUE_JOBS)

    data = require_success("execute-queue", raw_args)
    if data.get("ok") is False and "error_code" in data:
        return data
    results = data.get("results", [])
    summary = queue_summary_from_results(results)
    payload = base_payload("execute-queue")
    payload.update(
        {
            "ok": bool(data.get("ok", True)),
            "executed": True,
            "manifest_path": str(manifest_path),
            "manifest_sha256": archive_sha256(manifest_path),
            "archive_path": str(ARCHIVE_PATH),
            "summary": summary,
            "total": summary["total"],
            "succeeded": summary["succeeded"],
            "failed": summary["failed"],
            "skipped": summary["skipped"],
            "results": results,
            "wrapper_consumed_flags": ["--yes"],
            "forwarded_args": raw_args,
            "package_payload": data,
        }
    )
    payload["result_path"] = write_run_json("queue-result", payload)
    return payload


def requeue_failed(args: list[str]) -> dict[str, Any]:
    args, yes = consume_flag(args, "--yes")
    if not args:
        return normalize_error("requeue-failed", "missing queue result JSON path", "usage", 2)
    result_path = Path(args[0]).expanduser()
    if not result_path.is_file():
        return normalize_error("requeue-failed", f"queue result JSON not found: {result_path}", "missing_result", 2)
    args = args[1:]
    args, manifest_value = consume_option_value(args, "--manifest")
    if args:
        return normalize_error("requeue-failed", f"unsupported arguments: {' '.join(args)}", "usage", 2)
    manifest_path = Path(manifest_value).expanduser() if manifest_value else RUN_DIR / f"retry-queue-{timestamp_slug()}.json"
    if manifest_path.exists() and not yes:
        return normalize_error(
            "requeue-failed",
            f"manifest already exists and requires --yes to overwrite: {manifest_path}",
            "confirmation_required",
            2,
        )
    try:
        result_data = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return normalize_error("requeue-failed", f"could not parse queue result JSON: {exc}", "bad_result_json", 3)
    results = result_data.get("results")
    if not isinstance(results, list):
        package_payload = result_data.get("package_payload")
        if isinstance(package_payload, dict):
            results = package_payload.get("results")
    if not isinstance(results, list):
        return normalize_error("requeue-failed", "queue result JSON has no results list", "bad_result_json", 3)

    failed_items: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        failed = result.get("ok") is False or bool(result.get("errors"))
        if not failed or result.get("skipped"):
            continue
        plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
        selector = plan.get("selector") if isinstance(plan.get("selector"), dict) else result.get("selector")
        if not isinstance(selector, dict):
            continue
        output_path = plan.get("output_path") if isinstance(plan.get("output_path"), str) else None
        failed_items.append(
            {
                "selector": selector,
                "format": plan.get("format") or result.get("format") or "epub",
                "out_dir": str(Path(output_path).parent) if output_path else None,
                "index": result.get("index"),
                "exact": bool(result.get("exact", False)),
                "filename_template": result.get("filename_template"),
            }
        )

    manifest = {
        "items": failed_items,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "version": 1,
        "source": {"kind": "requeue-failed", "result_path": str(result_path)},
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload = base_payload("requeue-failed")
    payload.update(
        {
            "ok": True,
            "manifest_path": str(manifest_path),
            "manifest_exists": manifest_path.is_file(),
            "manifest_sha256": archive_sha256(manifest_path),
            "count": len(failed_items),
            "items": failed_items,
            "source_result_path": str(result_path),
            "wrapper_consumed_flags": ["--yes"] if yes else [],
        }
    )
    return payload


def validate_cmd(args: list[str]) -> dict[str, Any]:
    if not has_option(args, "--format"):
        args = ["--format", "auto", *args]
    data = require_success("validate", ["validate", *args])
    if data.get("ok") is False and "error_code" in data:
        return data
    payload = base_payload("validate")
    payload.update({"ok": bool(data.get("ok", True)), "package_payload": data})
    for key, value in data.items():
        if key not in {"ok"}:
            payload[key] = value
    return payload


def add_to_calibre(args: list[str]) -> dict[str, Any]:
    args, yes = consume_flag(args, "--yes")
    args, dry_flag = consume_flag(args, "--dry-run")
    args, execute = consume_flag(args, "--execute")
    args, duplicates_reviewed = consume_flag(args, "--duplicates-reviewed")
    args, allow_duplicate = consume_flag(args, "--allow-duplicate")
    args, title_override = consume_option_value(args, "--title")
    args, author_override = consume_option_value(args, "--author")
    args, tag_value = consume_option_value(args, "--tag")
    args, limit_value = consume_option_value(args, "--limit")
    if dry_flag and execute:
        return normalize_error(
            "add-to-calibre",
            "choose either --dry-run or --execute, not both",
            "usage",
            2,
        )
    consumed_flags = []
    for flag, present in (
        ("--yes", yes),
        ("--dry-run", dry_flag),
        ("--execute", execute),
        ("--duplicates-reviewed", duplicates_reviewed),
        ("--allow-duplicate", allow_duplicate),
    ):
        if present:
            consumed_flags.append(flag)
    try:
        duplicate_limit = int(limit_value) if limit_value else 5
    except ValueError:
        return normalize_error("add-to-calibre", "--limit must be an integer", "usage", 2)
    if not args:
        return normalize_error("add-to-calibre", "missing file path", "usage", 2)
    path = Path(args[0]).expanduser()
    if len(args) > 1:
        return normalize_error("add-to-calibre", f"unsupported arguments: {' '.join(args[1:])}", "usage", 2)
    if not path.is_file():
        return normalize_error("add-to-calibre", f"file not found: {path}", "missing_file", 2)
    suffix = path.suffix.lower()
    if suffix not in {".epub", ".pdf"}:
        return normalize_error(
            "add-to-calibre",
            "Calibre handoff accepts only EPUB/PDF; text/audio archives need a conversion workflow first",
            "unsupported_format",
            2,
        )

    validation = validate_cmd([str(path)])
    if not validation.get("ok") or not validation.get("validation", {}).get("ok"):
        payload = normalize_error("add-to-calibre", "file validation failed before Calibre handoff", "validation_failed", 2)
        payload["validation"] = validation
        return payload

    archive_record = load_archive_record(path)
    validation_data = validation.get("validation", {})
    title = title_override or (archive_record or {}).get("title") or validation_data.get("metadata_title") or path.stem
    author = author_override or (archive_record or {}).get("author") or validation_data.get("metadata_creator") or "Unknown"
    fmt = suffix.lstrip(".")

    doctor = run_calibre(["doctor"], timeout=CALIBRE_TIMEOUT_SECONDS)
    doctor_json = doctor.get("json") if isinstance(doctor.get("json"), dict) else {}
    doctor_ok = bool(doctor.get("ok") and doctor_json.get("status") == "ok")
    if not doctor_ok:
        payload = normalize_error("add-to-calibre", "Calibre doctor failed or timed out", "calibre_unavailable", 2)
        payload["calibre_doctor"] = doctor
        payload["validation"] = validation
        return payload

    duplicate_search_args = ["search", title, "--limit", str(duplicate_limit)]
    duplicate_search_command = calibre_display_command(duplicate_search_args)
    duplicates = calibre_cache_candidates(title, author, duplicate_limit)
    tags = "vnthuquan"
    if tag_value:
        tags = f"{tags},{tag_value}"
    write_args = [
        "add",
        str(path),
        "--title",
        str(title),
        "--author",
        str(author),
        "--tag",
        tags,
    ]
    dry_run_args = [
        *write_args,
        "--dry-run",
    ]
    add_result = run_calibre(dry_run_args, timeout=CALIBRE_TIMEOUT_SECONDS)
    add_json = add_result.get("json")
    add_ok = bool(add_result.get("ok") and isinstance(add_json, dict) and add_json.get("status") == "dry_run")
    dry_run_defaulted = not dry_flag and not execute
    payload = base_payload("add-to-calibre")
    payload.update(
        {
            "ok": add_ok,
            "dry_run": not execute,
            "dry_run_defaulted": dry_run_defaulted,
            "executed": False,
            "write_attempted": False,
            "path": str(path),
            "format": fmt,
            "metadata": {"title": title, "author": author, "tags": tags},
            "validation": validation_data,
            "archive_record": archive_record,
            "calibre_doctor": doctor_json or doctor,
            "duplicate_search_command": duplicate_search_command,
            "duplicate_candidates": duplicates,
            "calibre_preflight_command": calibre_display_command(dry_run_args),
            "calibre_preflight_result": add_json or add_result,
            "calibre_add_command": calibre_display_command(dry_run_args),
            "calibre_add_result": add_json or add_result,
            "wrapper_consumed_flags": consumed_flags,
            "write_gate": {
                "execute_requested": execute,
                "confirmation_required": execute,
                "confirmed": yes,
                "duplicates_reviewed": duplicates_reviewed,
                "allow_duplicate": allow_duplicate,
                "duplicate_count": duplicates.get("count") if isinstance(duplicates, dict) else None,
            },
        }
    )
    if not add_ok:
        payload.update({"message": "Calibre dry-run add failed", "error_code": "calibre_add_failed", "exit_code": 2})
        return payload

    if not execute:
        return payload

    payload.update(
        {
            "ok": False,
            "dry_run": False,
            "calibre_add_command": calibre_display_command(write_args),
            "calibre_write_command": calibre_display_command(write_args),
        }
    )
    if not yes:
        payload.update(
            {
                "message": "Calibre write requires --execute --yes after reviewing the dry-run result",
                "error_code": "confirmation_required",
                "exit_code": 2,
            }
        )
        return payload
    if not isinstance(duplicates, dict) or not duplicates.get("ok"):
        payload.update(
            {
                "message": "Calibre duplicate cache is unavailable; run Calibre sync/search before executing a write",
                "error_code": "duplicate_review_unavailable",
                "exit_code": 2,
            }
        )
        return payload
    if not duplicates_reviewed:
        payload.update(
            {
                "message": "Calibre write requires --duplicates-reviewed after checking duplicate candidates",
                "error_code": "duplicate_review_required",
                "exit_code": 2,
            }
        )
        return payload
    duplicate_count = int(duplicates.get("count") or 0)
    if duplicate_count and not allow_duplicate:
        payload.update(
            {
                "message": "duplicate candidates found; pass --allow-duplicate only after confirming this should be a separate Calibre entry",
                "error_code": "duplicate_candidates_found",
                "exit_code": 2,
            }
        )
        return payload

    write_result = run_calibre(write_args, timeout=CALIBRE_WRITE_TIMEOUT_SECONDS)
    write_json = write_result.get("json")
    write_ok = bool(write_result.get("ok") and isinstance(write_json, dict) and write_json.get("status") == "ok")
    result_record = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target": TARGET,
        "path": str(path),
        "format": fmt,
        "metadata": {"title": title, "author": author, "tags": tags},
        "duplicate_candidates": duplicates,
        "calibre_write_result": write_json or write_result,
        "ok": write_ok,
    }
    result_path = write_run_json("calibre-add-result", result_record)
    log_path = append_state_jsonl("calibre-writes.jsonl", {**result_record, "result_path": result_path})
    payload.update(
        {
            "ok": write_ok,
            "executed": write_ok,
            "write_attempted": True,
            "calibre_write_result": write_json or write_result,
            "calibre_write_result_path": result_path,
            "calibre_write_log_path": log_path,
            "recovery_notes": [
                "Do not retry a failed Calibre write automatically.",
                "Run the Calibre skill doctor/sync workflow before retrying.",
                "Review duplicate candidates again before any second execute attempt.",
            ],
        }
    )
    if isinstance(write_json, dict):
        payload["calibre_id"] = write_json.get("id")
        payload["calibre_drive_path"] = write_json.get("drive_path")
        payload["calibre_title"] = write_json.get("title")
        payload["calibre_authors"] = write_json.get("authors")
        payload["calibre_formats"] = write_json.get("formats")
    if not write_ok:
        payload.update({"message": "Calibre write failed", "error_code": "calibre_write_failed", "exit_code": 2})
    return payload


def archive(args: list[str]) -> dict[str, Any]:
    if not args:
        return normalize_error("archive", "missing archive subcommand", "usage", 2)
    subcommand = args[0]
    if subcommand == "path":
        payload = base_payload("archive")
        payload.update({"ok": True, "subcommand": "path", "archive_path": str(ARCHIVE_PATH)})
        return payload
    if subcommand == "list":
        raw_args = ["archive", "list", "--archive-path", str(ARCHIVE_PATH), *args[1:]]
        data = require_success("archive", raw_args)
        if data.get("ok") is False and "error_code" in data:
            return data
        records = data.get("records", [])
        payload = base_payload("archive")
        payload.update(
            {
                "ok": bool(data.get("ok", True)),
                "subcommand": "list",
                "archive_path": str(ARCHIVE_PATH),
                "records": records,
                "count": len(records) if isinstance(records, list) else None,
                "package_payload": data,
            }
        )
        return payload
    return normalize_error("archive", f"unsupported archive subcommand: {subcommand}", "usage", 2)


def completion(args: list[str], json_out: bool) -> int:
    if not args:
        return finish(normalize_error("completion", "missing shell", "usage", 2), json_out)
    status, stdout, stderr = run_pkg(["completion", *args], json_mode=json_out)
    if status != 0:
        return finish(normalize_error("completion", stderr.strip() or stdout.strip(), "package_error", status), json_out)
    if json_out:
        data = parse_json(stdout)
        payload = base_payload("completion")
        payload.update({"ok": bool(data.get("ok", True)), "shell": args[0], "package_payload": data})
        return finish(payload, True)
    print(stdout, end="")
    return 0


def phase_not_implemented(command: str) -> dict[str, Any]:
    return normalize_error(
        command,
        f"{command} is gated for a later implementation phase",
        "phase_not_implemented",
        2,
    )


def command_help_text(command: str) -> str | None:
    if command == "queue":
        return """vnthuquan Codex wrapper queue

Usage:
  run_vnthuquan.sh queue --query QUERY --limit N [--format epub|pdf|text|audio] [--json]
  run_vnthuquan.sh queue --category CATEGORY --pages N [--format epub|pdf|text|audio] [--json]

Creates a dry-run queue manifest under ~/.codex/runs/vnthuquan by default.
Use execute-queue MANIFEST --yes to execute it.
"""
    if command == "execute-queue":
        return """vnthuquan Codex wrapper execute-queue

Usage:
  run_vnthuquan.sh execute-queue MANIFEST --yes [--jobs N|auto] [--progress] [--json]

Executes a queue manifest with the wrapper-managed archive path.
"""
    if command == "requeue-failed":
        return """vnthuquan Codex wrapper requeue-failed

Usage:
  run_vnthuquan.sh requeue-failed QUEUE_RESULT_JSON [--manifest PATH] [--json]

Creates a retry manifest containing failed queue items from an execute-queue
result log.
"""
    if command == "diagnose":
        return "Usage: run_vnthuquan.sh diagnose [--json]\n"
    if command == "archive":
        return "Usage: run_vnthuquan.sh archive path|list [--json]\n"
    if command == "add-to-calibre":
        return """vnthuquan Codex wrapper add-to-calibre

Usage:
  run_vnthuquan.sh add-to-calibre PATH [--dry-run] [--title TITLE] [--author AUTHOR] [--json]
  run_vnthuquan.sh add-to-calibre PATH --execute --yes --duplicates-reviewed [--allow-duplicate] [--json]

EPUB/PDF files are validated, checked against the local Calibre cache for
duplicates, and preflighted with `cal add ... --dry-run`. Real Calibre writes
require --execute --yes --duplicates-reviewed. If duplicate candidates exist,
--allow-duplicate is also required.
"""
    return None


def help_text() -> str:
    return f"""vnthuquan assistant wrapper (Phase 5, target: {TARGET})

Usage:
  run_vnthuquan.sh <command> [args...] [--json]

Read/discovery commands:
  diagnose
  doctor
  mirrors list|check
  config path|show
  categories list|show
  formats
  list ...
  search ...
  show ...
  archive path|list
  completion bash|zsh|fish

Write-capable commands:
  mirrors use|reset --yes
  config set|unset --yes
  download ... --dry-run
  download ... --execute --yes
  queue ... --limit N
  execute-queue MANIFEST --yes
  requeue-failed QUEUE_RESULT_JSON
  validate PATH
  add-to-calibre PATH --dry-run
  add-to-calibre PATH --execute --yes --duplicates-reviewed
"""


def main(argv: list[str]) -> int:
    ensure_dirs()
    json_out = False
    cleaned: list[str] = []
    for arg in argv:
        if arg == "--json":
            json_out = True
        else:
            cleaned.append(arg)
    if not cleaned or cleaned[0] in {"-h", "--help", "help"}:
        print(help_text())
        return 0
    if cleaned[0] == "--version":
        print(f"vnthuquan-wrapper {WRAPPER_VERSION}")
        print(f"vnthuquan {package_version()}")
        return 0
    command, rest = cleaned[0], cleaned[1:]
    if command in {"search", "list"}:
        rest = merge_batch_split_format_values(rest)
    if has_help(rest):
        if command in NATIVE_HELP_COMMANDS:
            return native_help(command, rest, json_out)
        command_help = command_help_text(command)
        if command_help is not None:
            print(command_help, end="")
            return 0
    try:
        if command == "diagnose":
            payload = diagnose()
        elif command == "doctor":
            payload = doctor()
        elif command == "mirrors":
            payload = mirrors(rest)
        elif command == "config":
            payload = config(rest)
        elif command == "categories":
            payload = categories(rest)
        elif command == "formats":
            payload = formats()
        elif command == "list":
            payload = list_cmd(rest)
        elif command == "search":
            payload = search(rest)
        elif command == "show":
            payload = show(rest)
        elif command == "download":
            payload = download(rest)
        elif command == "queue":
            payload = queue(rest)
        elif command == "execute-queue":
            payload = execute_queue(rest)
        elif command == "requeue-failed":
            payload = requeue_failed(rest)
        elif command == "validate":
            payload = validate_cmd(rest)
        elif command == "archive":
            payload = archive(rest)
        elif command == "completion":
            return completion(rest, json_out)
        elif command == "add-to-calibre":
            payload = add_to_calibre(rest)
        else:
            payload = normalize_error(command, f"unknown command: {command}", "usage", 2)
    except WrapperError as exc:
        payload = normalize_error(command, str(exc), exc.code, exc.exit_code)
    return finish(payload, json_out)


if __name__ == "__main__":
    configure_utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
