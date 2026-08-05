#!/usr/bin/env python3
"""remote-bridge: Zulip control + Telegram mobile notify for AAS agents.

Cross-platform (linux/macos/windows/wsl). Stdlib only. No OpenClaw dependency.
Does not scrape ~/.openclaw. Offline selftest never opens network sockets.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import hashlib
import html as html_lib
import importlib.util
import json
import os
import re
import stat
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

SCHEMA_VERSION = "1.0"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SUPPORTED_PROVIDERS = (
    "claude",
    "codex",
    "grok",
    "deepseek",
    "opencode",
    "copilot",
    "antigravity",
    "kimi",
)
DEFAULT_NOTIFY_EVENTS = frozenset(
    {"iteration_ok", "iteration_failed", "quota_wait", "drive_stop", "notify", "approve_tool"}
)
INBOX_MAX_TOTAL = 4096
INBOX_MAX_ITEM_TEXT = 512
CLAIM_LEASE_SECONDS = 3600
EVENT_JSON_MAX_BYTES = 1024 * 1024
CONTROL_TEXT_MAX_BYTES = 64 * 1024
TELEGRAM_CHUNK_LIMIT = 3500
NOTIFY_RETRY_DEDUPE_SECONDS = 120.0
NOTIFY_LOCK_OPEN_TIMEOUT_SECONDS = 2.0
_NOTIFY_V2_MODULE: Any | None = None


class NotifyRedactionError(RuntimeError):
    """Raised when outbound text cannot pass the required redaction gate."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _emit(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def _fail(command: str, error_code: str, message: str, **extra: Any) -> int:
    payload = {"ok": False, "command": command, "error_code": error_code, "message": message}
    payload.update(extra)
    _emit(payload)
    return 1


def _ok(command: str, **extra: Any) -> int:
    payload = {"ok": True, "command": command}
    payload.update(extra)
    _emit(payload)
    return 0


def redact_notify_text(text: Any, cfg: "BridgeConfig") -> str:
    """Apply the mandatory notification-v2 secret and PII redaction gate."""

    value = str(text)
    try:
        return load_notify_v2_module().redact_text(value, cfg.secret_values())
    except Exception as exc:  # noqa: BLE001 - external sends fail closed
        raise NotifyRedactionError("notification redaction is unavailable") from exc


def neutralize_zulip_mentions(text: Any) -> str:
    """Make caller-controlled Zulip text incapable of creating mentions."""

    return str(text).replace("@", "＠")


def safe_redaction_error(text: Any, cfg: "BridgeConfig") -> str:
    """Return a safe diagnostic even when the redaction module is unavailable."""

    try:
        return redact_notify_text(text, cfg)
    except NotifyRedactionError:
        return "notification redaction is unavailable"


def sanitize_notify_job(value: Any, cfg: "BridgeConfig") -> str | None:
    """Bound and scrub an externally supplied topic/job label."""

    if value is None:
        return None
    cleaned = redact_notify_text(value, cfg)
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", cleaned).strip()
    return cleaned[:200] or None


# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------


def state_root(environ: dict[str, str] | None = None) -> Path:
    env = environ if environ is not None else os.environ
    override = env.get("AAS_REMOTE_BRIDGE_STATE")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ai-agents-skills" / "remote-bridge"
    xdg = env.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "ai-agents-skills" / "remote-bridge"
    return Path.home() / ".local" / "share" / "ai-agents-skills" / "remote-bridge"


def secrets_candidates(environ: dict[str, str] | None = None) -> list[Path]:
    env = environ if environ is not None else os.environ
    paths: list[Path] = []
    if env.get("REMOTE_BRIDGE_SECRETS_FILE"):
        paths.append(Path(env["REMOTE_BRIDGE_SECRETS_FILE"]).expanduser())
    if os.name == "nt":
        appdata = env.get("APPDATA")
        local = env.get("LOCALAPPDATA")
        if appdata:
            paths.append(Path(appdata) / "remote-bridge" / "secrets.json")
        if local:
            paths.append(Path(local) / "remote-bridge" / "secrets.json")
    else:
        xdg = env.get("XDG_CONFIG_HOME")
        if xdg:
            paths.append(Path(xdg) / "remote-bridge" / "secrets.json")
        paths.append(Path.home() / ".config" / "remote-bridge" / "secrets.json")
        if sys.platform == "darwin":
            paths.append(
                Path.home() / "Library" / "Application Support" / "remote-bridge" / "secrets.json"
            )
    return paths


def load_secrets(
    secrets_file: str | None = None, environ: dict[str, str] | None = None
) -> tuple[dict[str, Any], str | None]:
    env = environ if environ is not None else os.environ
    cli_override = secrets_file is not None
    env_override = "REMOTE_BRIDGE_SECRETS_FILE" in env
    if cli_override and env_override:
        raise OSError(
            "--secrets-file and REMOTE_BRIDGE_SECRETS_FILE are mutually exclusive"
        )
    explicit_override = cli_override or env_override
    if cli_override:
        override_value = str(secrets_file or "").strip()
    elif env_override:
        override_value = str(env.get("REMOTE_BRIDGE_SECRETS_FILE") or "").strip()
    else:
        override_value = ""
    if explicit_override:
        if not override_value:
            raise OSError("explicit remote-bridge secrets file is empty")
        candidates = [Path(override_value).expanduser()]
    else:
        candidates = secrets_candidates(env)
    for path in candidates:
        absolute = Path(os.path.abspath(path.expanduser()))
        try:
            os.lstat(absolute)
        except FileNotFoundError as exc:
            if explicit_override:
                raise OSError("explicit remote-bridge secrets file does not exist") from exc
            continue
        try:
            payload = _secure_read_secrets_file(absolute)
            data = json.loads(payload.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise OSError("remote-bridge secrets file is not UTF-8") from exc
        except json.JSONDecodeError as exc:
            raise OSError("remote-bridge secrets file is not valid JSON") from exc
        if isinstance(data, dict):
            return data, str(absolute)
        raise OSError("remote-bridge secrets file must contain a JSON object")
    if explicit_override:
        raise OSError("explicit remote-bridge secrets file could not be loaded")
    # Env-only skeleton
    data: dict[str, Any] = {
        "default_channel": env.get("AAS_REMOTE_DEFAULT_CHANNEL") or "zulip",
        "notify_channels": [],
        "zulip": {},
        "telegram": {},
        "allowed_user_ids": [],
    }
    if env.get("ZULIP_ORG_URL") or env.get("ZULIP_SITE"):
        data["zulip"] = {
            "site": env.get("ZULIP_ORG_URL") or env.get("ZULIP_SITE"),
            "email": env.get("ZULIP_EMAIL"),
            "api_key": env.get("ZULIP_API_KEY"),
            "control_stream": env.get("ZULIP_CONTROL_STREAM") or "aas-remote",
            "topic_prefix": env.get("ZULIP_TOPIC_PREFIX") or "job/",
            "allowed_user_ids": _split_ids(env.get("ZULIP_ALLOWED_USER_IDS")),
        }
        data["notify_channels"] = ["zulip"]
    if env.get("TELEGRAM_BOT_TOKEN"):
        data["telegram"] = {
            "bot_token": env.get("TELEGRAM_BOT_TOKEN"),
            "mode": env.get("TELEGRAM_MODE") or "outbound_only",
            "allowed_chat_ids": _split_ids(env.get("TELEGRAM_ALLOWED_CHAT_IDS")),
            "allowed_user_ids": _split_ids(env.get("TELEGRAM_ALLOWED_USER_IDS")),
        }
        data.setdefault("notify_channels", [])
        if "telegram" not in data["notify_channels"]:
            data["notify_channels"].append("telegram")
    if env.get("AAS_REMOTE_ALLOWED_USER_IDS"):
        data["allowed_user_ids"] = _split_ids(env["AAS_REMOTE_ALLOWED_USER_IDS"])
    return data, None


def _secure_read_secrets_file(path: Path, *, max_bytes: int = 1_000_000) -> bytes:
    """Descriptor-read one private, owner-controlled secrets file."""

    absolute = Path(os.path.abspath(path))
    # Windows cannot hold a directory descriptor: the parent chain is validated
    # by lstat, the leaf is opened by absolute path, and the descriptor-based
    # parent checks below are POSIX-only anyway.
    parent_fd: int | None = None
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        _ensure_notify_directory_chain(absolute.parent, create=False)
    else:
        parent_fd = _open_notify_directory_nofollow(absolute.parent, create=False)
    try:
        if parent_fd is not None:
            parent_info = os.fstat(parent_fd)
            if not stat.S_ISDIR(parent_info.st_mode):
                raise OSError("remote-bridge secrets parent is not a directory")
            if os.name == "posix" and (
                int(parent_info.st_uid) != int(os.geteuid())
                or parent_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            ):
                raise OSError("remote-bridge secrets parent is not owner-controlled")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        if parent_fd is None:  # pragma: no cover - exercised on Windows CI
            file_fd = os.open(absolute, flags)
        else:
            file_fd = os.open(absolute.name, flags, dir_fd=parent_fd)
        try:
            before = os.fstat(file_fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or int(getattr(before, "st_nlink", 1)) != 1
                or int(before.st_size) > max_bytes
            ):
                raise OSError("remote-bridge secrets file is not a bounded single-link regular file")
            if os.name == "posix" and (
                int(before.st_uid) != int(os.geteuid())
                or stat.S_IMODE(before.st_mode) & 0o077
            ):
                raise OSError("remote-bridge secrets file is not owner-private")
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(file_fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            after = os.fstat(file_fd)
            if len(payload) > max_bytes:
                raise OSError("remote-bridge secrets file is oversized")
            if (
                int(before.st_dev),
                int(before.st_ino),
                int(before.st_size),
                int(before.st_mtime_ns),
            ) != (
                int(after.st_dev),
                int(after.st_ino),
                int(after.st_size),
                int(after.st_mtime_ns),
            ):
                raise OSError("remote-bridge secrets file changed while reading")
            return payload
        finally:
            os.close(file_fd)
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def _split_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    return _split_ids(str(value))


@dataclass
class BridgeConfig:
    raw: dict[str, Any]
    secrets_path: str | None
    default_channel: str = "zulip"
    notify_channels: list[str] = field(default_factory=list)
    allowed_user_ids: list[str] = field(default_factory=list)
    zulip: dict[str, Any] = field(default_factory=dict)
    telegram: dict[str, Any] = field(default_factory=dict)

    def secret_values(self) -> list[str]:
        vals: list[str] = []

        def collect_strings(obj: Any) -> None:
            if isinstance(obj, str):
                if obj:
                    vals.append(obj)
                return
            if isinstance(obj, Mapping):
                for nested in obj.values():
                    collect_strings(nested)
                return
            if isinstance(obj, (list, tuple)):
                for nested in obj:
                    collect_strings(nested)

        def walk(obj: Any) -> None:
            if isinstance(obj, Mapping):
                for key, value in obj.items():
                    if _is_secret_config_key(key):
                        collect_strings(value)
                    else:
                        walk(value)
                return
            if isinstance(obj, (list, tuple)):
                for nested in obj:
                    walk(nested)

        for blob in (self.raw, self.zulip, self.telegram):
            walk(blob)
        return list(dict.fromkeys(vals))

    def redacted_view(self) -> dict[str, Any]:
        def scrub(obj: Any) -> Any:
            if isinstance(obj, Mapping):
                out = {}
                for k, v in obj.items():
                    if _is_secret_config_key(k):
                        out[k] = "***"
                    else:
                        out[k] = scrub(v)
                return out
            if isinstance(obj, (list, tuple)):
                return [scrub(x) for x in obj]
            return obj

        return {
            "default_channel": self.default_channel,
            "notify_channels": list(self.notify_channels),
            "allowed_user_ids": list(self.allowed_user_ids),
            "zulip": scrub(self.zulip),
            "telegram": scrub(self.telegram),
            "secrets_path": self.secrets_path,
        }


def _is_secret_config_key(key: Any) -> bool:
    """Classify case/style variants of credential-bearing config keys."""

    raw = str(key).strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    compact = normalized.replace("_", "")
    if not compact:
        return False
    direct_markers = (
        "apikey",
        "apitoken",
        "authorization",
        "bearertoken",
        "bottoken",
        "clientsecret",
        "cookie",
        "credential",
        "oauth",
        "password",
        "passwd",
        "privatekey",
        "refreshtoken",
        "accesstoken",
        "authtoken",
    )
    if any(marker in compact for marker in direct_markers):
        return True
    if compact in {"secret", "secrets", "token", "tokens", "session"}:
        return True
    if compact.startswith("secret") or compact.endswith("secret"):
        return True
    if compact.startswith("token") or compact.endswith("token"):
        return True
    return compact in {
        "sessionid",
        "sessionkey",
        "sessioncookie",
        "sessioncredential",
    }


def build_config(
    secrets_file: str | None = None, environ: dict[str, str] | None = None
) -> BridgeConfig:
    raw, path = load_secrets(secrets_file, environ)
    zulip = dict(raw.get("zulip") or {})
    telegram = dict(raw.get("telegram") or {})
    channels = raw.get("notify_channels")
    if not channels:
        channels = []
        if zulip.get("site") and zulip.get("email") and zulip.get("api_key"):
            channels.append("zulip")
        if telegram.get("bot_token"):
            channels.append("telegram")
    return BridgeConfig(
        raw=raw,
        secrets_path=path,
        default_channel=str(raw.get("default_channel") or "zulip"),
        notify_channels=[str(c) for c in channels],
        allowed_user_ids=_as_str_list(raw.get("allowed_user_ids")),
        zulip=zulip,
        telegram=telegram,
    )


# ---------------------------------------------------------------------------
# Mailbox / digest
# ---------------------------------------------------------------------------


def validate_id(value: str, kind: str = "id") -> str:
    if not SAFE_ID.match(value):
        raise ValueError(f"invalid {kind}: {value!r}")
    return value


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def action_digest(
    *,
    provider: str,
    job_id: str,
    workspace_root: str,
    tool: str,
    args: Any,
    nonce: str,
    policy_ver: str = "1",
    max_retries: int = 1,
) -> str:
    payload = {
        "v": 1,
        "provider": provider,
        "job_id": job_id,
        "workspace_root": str(Path(workspace_root).expanduser().resolve()) if workspace_root else "",
        "tool": tool,
        "args": args,
        "nonce": nonce,
        "policy_ver": policy_ver,
        "max_retries": max_retries,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def short_digest(digest: str) -> str:
    return digest[:12]


class Mailbox:
    def __init__(self, root: Path | None = None):
        self.root = Path(os.path.abspath((root or state_root()).expanduser()))
        self.jobs_dir = self.root / "jobs"
        self.bridge_dir = self.root / "bridge"
        self.outbox_dir = self.bridge_dir / "outbox"

    def ensure(self) -> None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.bridge_dir.mkdir(parents=True, exist_ok=True)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        for private_dir in (
            self.root,
            self.jobs_dir,
            self.bridge_dir,
            self.outbox_dir,
        ):
            try:
                os.chmod(private_dir, 0o700)
            except OSError:
                pass

    def job_dir(self, job_id: str) -> Path:
        validate_id(job_id, "job_id")
        return self.jobs_dir / job_id

    def job_exists(self, job_id: str) -> bool:
        return self.job_dir(job_id).is_dir()

    def arm(
        self,
        job_id: str,
        *,
        provider: str,
        cwd: str,
        loop_dir: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        self.ensure()
        validate_id(job_id, "job_id")
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"unsupported provider: {provider}")
        jdir = self.job_dir(job_id)
        if jdir.exists() and not force:
            existing = self.read_json(jdir / "job.json") or {}
            if existing.get("status") not in {"stopped", "failed", None} and existing:
                raise FileExistsError(f"job_id already armed: {job_id}")
        jdir.mkdir(parents=True, exist_ok=True)
        for sub in (
            "requests",
            "replies",
            "inbox/pending",
            "inbox/claimed",
            "inbox/consumed",
            "inbox/abandoned",
            "inbox/poisoned",
        ):
            (jdir / sub).mkdir(parents=True, exist_ok=True)
        job = {
            "schema_version": SCHEMA_VERSION,
            "job_id": job_id,
            "provider": provider,
            "cwd": str(Path(cwd).expanduser().resolve()),
            "loop_dir": str(Path(loop_dir).expanduser().resolve()) if loop_dir else None,
            "status": "armed",
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        self.write_json(jdir / "job.json", job)
        self.write_json(jdir / "state.json", {"status": "armed", "updated_at": utc_now()})
        return job

    def list_jobs(self) -> list[dict[str, Any]]:
        self.ensure()
        jobs = []
        if not self.jobs_dir.is_dir():
            return jobs
        for path in sorted(self.jobs_dir.iterdir()):
            if path.is_dir() and (path / "job.json").is_file():
                job = self.read_json(path / "job.json") or {"job_id": path.name}
                jobs.append(job)
        return jobs

    def read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def write_json(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def append_jsonl(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")

    def create_request(
        self,
        job_id: str,
        *,
        req_type: str,
        provider: str,
        tool: str = "",
        args: Any = None,
        summary: str = "",
        expires_seconds: int = 3600,
        truncated: bool = False,
    ) -> dict[str, Any]:
        jdir = self.job_dir(job_id)
        if not jdir.is_dir():
            raise FileNotFoundError(f"job not found: {job_id}")
        request_id = "r_" + uuid.uuid4().hex
        nonce = uuid.uuid4().hex
        job = self.read_json(jdir / "job.json") or {}
        workspace = str(job.get("cwd") or "")
        digest = None
        if truncated:
            digest = None
        elif req_type == "approve_tool":
            digest = action_digest(
                provider=provider,
                job_id=job_id,
                workspace_root=workspace,
                tool=tool,
                args=args,
                nonce=nonce,
            )
        record = {
            "schema_version": SCHEMA_VERSION,
            "request_id": request_id,
            "job_id": job_id,
            "type": req_type,
            "provider": provider,
            "tool": tool,
            "summary": summary[:240],
            "digest": digest,
            "digest_short": short_digest(digest) if digest else None,
            "nonce": nonce,
            "truncated": truncated,
            "status": "pending",
            "created_at": utc_now(),
            "expires_at": datetime.fromtimestamp(
                time.time() + expires_seconds, tz=timezone.utc
            )
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
        }
        self.write_json(jdir / "requests" / f"{request_id}.json", record)
        self.append_jsonl(jdir / "requests.jsonl", record)
        return record

    def write_reply(
        self,
        job_id: str,
        request_id: str,
        *,
        decision: str,
        principal: str,
        text: str = "",
    ) -> dict[str, Any]:
        validate_id(request_id.replace("r_", "r") if False else request_id, "request_id")
        # request_id has r_ prefix — SAFE_ID allows underscore
        if not re.match(r"^r_[A-Za-z0-9]+$", request_id) and not SAFE_ID.match(request_id):
            raise ValueError(f"invalid request_id: {request_id}")
        jdir = self.job_dir(job_id)
        reply_path = jdir / "replies" / f"{request_id}.json"
        if reply_path.exists():
            existing = self.read_json(reply_path) or {}
            return {"already_resolved": True, **existing}
        req = self.read_json(jdir / "requests" / f"{request_id}.json")
        if not req:
            # search all jobs
            found_job = None
            for job in self.list_jobs():
                jid = job["job_id"]
                cand = self.job_dir(jid) / "requests" / f"{request_id}.json"
                if cand.is_file():
                    found_job = jid
                    req = self.read_json(cand)
                    jdir = self.job_dir(jid)
                    reply_path = jdir / "replies" / f"{request_id}.json"
                    break
            if not req:
                raise FileNotFoundError(f"request not found: {request_id}")
            job_id = found_job or job_id
        if req.get("truncated"):
            raise ValueError("cannot approve truncated request")
        expires = req.get("expires_at")
        if expires and expires < utc_now() and decision == "allow":
            raise ValueError("request expired")
        reply = {
            "schema_version": SCHEMA_VERSION,
            "request_id": request_id,
            "job_id": job_id,
            "decision": decision,
            "principal": principal,
            "text": text[:2000],
            "digest": req.get("digest"),
            "created_at": utc_now(),
            "consumed": False,
        }
        # CAS: exclusive create
        tmp = reply_path.with_suffix(".tmp")
        try:
            fd = os.open(str(reply_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            existing = self.read_json(reply_path) or {}
            return {"already_resolved": True, **existing}
        try:
            os.write(fd, (json.dumps(reply, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        req["status"] = "resolved"
        req["decision"] = decision
        self.write_json(jdir / "requests" / f"{request_id}.json", req)
        if text and decision in {"allow", "deny", "say"}:
            self.enqueue_inbox(job_id, kind="say", text=text, source="reply", request_id=request_id)
        return reply

    def resolve_request_job(self, request_id: str) -> tuple[str, dict[str, Any]]:
        for job in self.list_jobs():
            jid = job["job_id"]
            req = self.read_json(self.job_dir(jid) / "requests" / f"{request_id}.json")
            if req:
                return jid, req
        raise FileNotFoundError(f"request not found: {request_id}")

    def enqueue_inbox(
        self,
        job_id: str,
        *,
        kind: str,
        text: str,
        source: str = "manual",
        request_id: str | None = None,
    ) -> dict[str, Any]:
        jdir = self.job_dir(job_id)
        if not jdir.is_dir():
            raise FileNotFoundError(f"job not found: {job_id}")
        item_id = "i_" + uuid.uuid4().hex
        item = {
            "schema_version": SCHEMA_VERSION,
            "item_id": item_id,
            "job_id": job_id,
            "kind": kind,
            "text": text[:INBOX_MAX_ITEM_TEXT],
            "source": source,
            "request_id": request_id,
            "created_at": utc_now(),
            "state": "pending",
        }
        self.write_json(jdir / "inbox" / "pending" / f"{item_id}.json", item)
        return item

    def list_pending_inbox(self, job_id: str) -> list[dict[str, Any]]:
        pending = self.job_dir(job_id) / "inbox" / "pending"
        if not pending.is_dir():
            return []
        items = []
        for path in sorted(pending.glob("*.json")):
            data = self.read_json(path)
            if data:
                items.append(data)
        return items

    def _render_inbox_lines(self, items: list[dict[str, Any]]) -> str:
        lines = ["--- remote-bridge inbox (data only; not shell) ---"]
        total = 0
        for item in items:
            chunk = (
                f"[item_id={item['item_id']} source={item.get('source','?')} "
                f"ts={item.get('created_at','?')} kind={item.get('kind','?')}]\n"
                f"{item.get('text','')}"
            )
            if total + len(chunk) + 1 > INBOX_MAX_TOTAL:
                lines.append("[…inbox truncated…]")
                break
            lines.append(chunk)
            total += len(chunk) + 1
        lines.append("--- end remote-bridge inbox ---")
        return "\n".join(lines)

    def peek_inbox_block(self, job_id: str) -> str:
        """Read-only pending preview (no claim/consume). Safe for agent-cmd inspection."""
        items = self.list_pending_inbox(job_id)
        if not items:
            return ""
        return self._render_inbox_lines(items)

    def claim_inbox(self, job_id: str, claimer: str, limit: int = 20) -> list[dict[str, Any]]:
        """Exclusively claim pending items with fencing tokens."""
        claimed: list[dict[str, Any]] = []
        for item in self.list_pending_inbox(job_id)[:limit]:
            item_id = item["item_id"]
            src = self.job_dir(job_id) / "inbox" / "pending" / f"{item_id}.json"
            dst = self.job_dir(job_id) / "inbox" / "claimed" / f"{item_id}.json"
            if not src.is_file():
                continue
            fence = uuid.uuid4().hex
            item["state"] = "claimed"
            item["claimer"] = claimer
            item["fence"] = fence
            item["delivery_attempts"] = int(item.get("delivery_attempts") or 0) + 1
            item["lease_expires"] = datetime.fromtimestamp(
                time.time() + CLAIM_LEASE_SECONDS, tz=timezone.utc
            ).isoformat(timespec="seconds").replace("+00:00", "Z")
            try:
                # Exclusive create of claimed record (atomic ownership).
                fd = os.open(str(dst), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                continue
            except OSError:
                continue
            try:
                os.write(fd, (json.dumps(item, indent=2, sort_keys=True) + "\n").encode("utf-8"))
            finally:
                os.close(fd)
            try:
                src.unlink(missing_ok=True)
            except OSError:
                pass
            claimed.append(item)
        return claimed

    def consume_claimed(
        self,
        job_id: str,
        item_ids: list[str],
        *,
        claimer: str | None = None,
        fences: dict[str, str] | None = None,
    ) -> list[str]:
        """Consume claimed items; requires matching claimer/fence when provided."""
        consumed: list[str] = []
        fences = fences or {}
        for item_id in item_ids:
            src = self.job_dir(job_id) / "inbox" / "claimed" / f"{item_id}.json"
            data = self.read_json(src)
            if not data:
                continue
            if claimer is not None and data.get("claimer") != claimer:
                continue
            if item_id in fences and data.get("fence") != fences[item_id]:
                continue
            data["state"] = "consumed"
            data["consumed_at"] = utc_now()
            dst = self.job_dir(job_id) / "inbox" / "consumed" / f"{item_id}.json"
            self.write_json(dst, data)
            src.unlink(missing_ok=True)
            consumed.append(item_id)
        return consumed

    def requeue_claimed(
        self,
        job_id: str,
        item_ids: list[str],
        *,
        claimer: str | None = None,
        fences: dict[str, str] | None = None,
    ) -> list[str]:
        """Return claimed items to pending if ownership matches."""
        requeued: list[str] = []
        fences = fences or {}
        for item_id in item_ids:
            src = self.job_dir(job_id) / "inbox" / "claimed" / f"{item_id}.json"
            data = self.read_json(src)
            if not data:
                continue
            if claimer is not None and data.get("claimer") != claimer:
                continue
            if item_id in fences and data.get("fence") != fences[item_id]:
                continue
            attempts = int(data.get("delivery_attempts") or 0)
            if attempts >= 5:
                data["state"] = "poisoned"
                dst = self.job_dir(job_id) / "inbox" / "poisoned" / f"{item_id}.json"
                self.write_json(dst, data)
                src.unlink(missing_ok=True)
                continue
            data["state"] = "pending"
            data.pop("claimer", None)
            data.pop("fence", None)
            data.pop("lease_expires", None)
            dst = self.job_dir(job_id) / "inbox" / "pending" / f"{item_id}.json"
            self.write_json(dst, data)
            src.unlink(missing_ok=True)
            requeued.append(item_id)
        return requeued

    def reclaim_stale_claims(self, job_id: str) -> int:
        """Move expired claims back to pending."""
        claimed_dir = self.job_dir(job_id) / "inbox" / "claimed"
        if not claimed_dir.is_dir():
            return 0
        n = 0
        now = utc_now()
        for path in list(claimed_dir.glob("*.json")):
            data = self.read_json(path)
            if not data:
                continue
            exp = data.get("lease_expires") or ""
            if exp and exp > now:
                continue
            item_id = data.get("item_id") or path.stem
            data["state"] = "pending"
            data.pop("claimer", None)
            data.pop("fence", None)
            data.pop("lease_expires", None)
            dst = self.job_dir(job_id) / "inbox" / "pending" / f"{item_id}.json"
            self.write_json(dst, data)
            path.unlink(missing_ok=True)
            n += 1
        return n

    def format_inbox_block(
        self, job_id: str, claimer: str = "drive"
    ) -> tuple[str, list[str], dict[str, str]]:
        """Claim pending items and return (block, item_ids, fences). Does not consume."""
        self.reclaim_stale_claims(job_id)
        claimed = self.claim_inbox(job_id, claimer=claimer)
        if not claimed:
            return "", [], {}
        # Overflow: requeue extras not rendered
        rendered: list[dict[str, Any]] = []
        overflow: list[dict[str, Any]] = []
        total = 0
        for item in claimed:
            chunk_len = len(item.get("text") or "") + 120
            if rendered and total + chunk_len > INBOX_MAX_TOTAL:
                overflow.append(item)
                continue
            rendered.append(item)
            total += chunk_len
        if overflow:
            self.requeue_claimed(
                job_id,
                [i["item_id"] for i in overflow],
                claimer=claimer,
                fences={i["item_id"]: i.get("fence", "") for i in overflow},
            )
        fences = {i["item_id"]: str(i.get("fence") or "") for i in rendered}
        return self._render_inbox_lines(rendered), [i["item_id"] for i in rendered], fences

    def check_approval(
        self, job_id: str, digest: str
    ) -> dict[str, Any] | None:
        """Return unconsumed allow reply matching digest, consuming it atomically."""
        jdir = self.job_dir(job_id)
        replies = jdir / "replies"
        if not replies.is_dir():
            return None
        for path in sorted(replies.glob("*.json")):
            reply = self.read_json(path)
            if not reply:
                continue
            if reply.get("decision") != "allow":
                continue
            if reply.get("consumed"):
                continue
            if reply.get("digest") != digest:
                continue
            # Atomic consume marker via exclusive sidecar
            mark = path.with_suffix(".consumed")
            try:
                fd = os.open(str(mark), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, utc_now().encode("utf-8"))
                os.close(fd)
            except FileExistsError:
                continue
            except OSError:
                continue
            reply["consumed"] = True
            reply["consumed_at"] = utc_now()
            try:
                self.write_json(path, reply)
            except OSError:
                pass
            return reply
        return None

    def pending_requests(self) -> list[dict[str, Any]]:
        out = []
        for job in self.list_jobs():
            jdir = self.job_dir(job["job_id"])
            for path in (jdir / "requests").glob("*.json"):
                req = self.read_json(path)
                if req and req.get("status") == "pending":
                    if not (jdir / "replies" / f"{req['request_id']}.json").exists():
                        out.append(req)
        return out


def fingerprint(record: dict[str, Any]) -> str:
    return (
        f"⟦AAS⟧ job={record.get('job_id','?')} req={record.get('request_id','?')} "
        f"provider={record.get('provider','?')} type={record.get('type','?')} "
        f"digest={record.get('digest_short') or '-'} expires={record.get('expires_at','?')}"
    )


# ---------------------------------------------------------------------------
# Structured ARL notifications (loaded lazily to preserve standalone commands)
# ---------------------------------------------------------------------------


def load_notify_v2_module() -> Any:
    """Load the canonical pure notify module without requiring a Python package."""
    global _NOTIFY_V2_MODULE
    if _NOTIFY_V2_MODULE is not None:
        return _NOTIFY_V2_MODULE
    here = Path(__file__).resolve().parent
    candidates = [
        here / "notify_v2.py",
        here.parent / "autonomous-research-loop-runtime" / "notify_v2.py",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        module_name = "aas_autoloop_notify_v2"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        _NOTIFY_V2_MODULE = module
        return module
    raise RuntimeError(
        "notify_v2.py is not installed beside remote-bridge or the autonomous-loop runtime"
    )


def _event_file_snapshot(info: os.stat_result) -> tuple[int, ...]:
    """Return metadata that must remain stable for one event-file read."""

    return (
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(info.st_mode),
        int(getattr(info, "st_nlink", 1)),
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000))),
        int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000))),
    )


def _validate_event_file(info: os.stat_result) -> None:
    """Reject special, multiply linked, or pre-known oversized event files."""

    if not stat.S_ISREG(info.st_mode) or int(getattr(info, "st_nlink", 1)) != 1:
        raise OSError("event JSON path is not a single-link regular file")
    if int(info.st_size) > EVENT_JSON_MAX_BYTES:
        raise ValueError("event JSON exceeds 1 MiB")


def _read_event_json_path(source: str) -> bytes:
    """Descriptor-read one stable, bounded local event JSON file."""

    path = Path(os.path.abspath(Path(source).expanduser()))
    # Windows cannot hold a directory descriptor, so the parent chain is
    # validated by lstat and every leaf step below works on the absolute path.
    parent_fd: int | None = None
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        _ensure_notify_directory_chain(path.parent, create=False)
    else:
        parent_fd = _open_notify_directory_nofollow(path.parent, create=False)
    file_fd: int | None = None
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_BINARY", 0)
        )
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            before_path = os.lstat(path)
            if stat.S_ISLNK(before_path.st_mode):
                raise OSError("event JSON path is a symlink")
            _validate_event_file(before_path)
            file_fd = os.open(path, flags)
        else:
            before_path = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            _validate_event_file(before_path)
            file_fd = os.open(path.name, flags, dir_fd=parent_fd)

        opened = os.fstat(file_fd)
        _validate_event_file(opened)
        if _event_file_snapshot(before_path) != _event_file_snapshot(opened):
            raise OSError("event JSON path changed while opening")

        chunks: list[bytes] = []
        remaining = EVENT_JSON_MAX_BYTES + 1
        while remaining > 0:
            chunk = os.read(file_fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > EVENT_JSON_MAX_BYTES:
            raise ValueError("event JSON exceeds 1 MiB")

        after = os.fstat(file_fd)
        if _event_file_snapshot(opened) != _event_file_snapshot(after):
            raise OSError("event JSON file changed while reading")
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            after_path = os.lstat(path)
        else:
            after_path = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if _event_file_snapshot(opened) != _event_file_snapshot(after_path):
            raise OSError("event JSON path changed while reading")
        return payload
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def _read_event_json_stdin() -> bytes:
    """Read stdin under the same byte ceiling without sharing path logic."""

    binary = getattr(sys.stdin, "buffer", None)
    if binary is not None:
        payload = binary.read(EVENT_JSON_MAX_BYTES + 1)
    else:  # pragma: no cover - direct callers may replace stdin with StringIO
        payload = sys.stdin.read(EVENT_JSON_MAX_BYTES + 1).encode("utf-8")
    if len(payload) > EVENT_JSON_MAX_BYTES:
        raise ValueError("event JSON exceeds 1 MiB")
    return payload


def _read_control_text_stdin() -> str:
    """Read one bounded UTF-8 control message without placing it in argv."""

    binary = getattr(sys.stdin, "buffer", None)
    if binary is not None:
        payload = binary.read(CONTROL_TEXT_MAX_BYTES + 1)
    else:  # pragma: no cover - direct callers may replace stdin with StringIO
        payload = sys.stdin.read(CONTROL_TEXT_MAX_BYTES + 1).encode("utf-8")
    if len(payload) > CONTROL_TEXT_MAX_BYTES:
        raise ValueError("control text exceeds the byte limit")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("control text stdin must be UTF-8") from exc


def load_event_json(source: str) -> dict[str, Any]:
    """Read one bounded UTF-8 event object from stdin (``-``) or a local path."""

    if source == "-":
        payload = _read_event_json_stdin()
    else:
        try:
            payload = _read_event_json_path(source)
        except ValueError:
            raise
        except OSError as exc:
            raise ValueError(f"cannot read event JSON: {exc}") from exc
    try:
        raw = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("event JSON is not UTF-8") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid event JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("event JSON must contain one object")
    return data


def summarize_delivery(
    results: dict[str, Any], *, dry_run: bool = False
) -> dict[str, Any]:
    """Return transport-independent delivery truth for callers and dedupe."""
    succeeded = [
        channel
        for channel, result in results.items()
        if isinstance(result, dict) and result.get("ok") is True
    ]
    return {
        "ok": bool(succeeded),
        "delivered": bool(succeeded) and not dry_run,
        "dry_run": bool(dry_run),
        "channel": succeeded[0] if succeeded else None,
        "attempted_channels": list(results),
    }


def _notify_delivery_path(mailbox: Mailbox | None = None) -> Path:
    mb = mailbox or Mailbox()
    return mb.bridge_dir / "notify_deliveries.json"


def _ensure_real_directory(path: Path, *, create: bool = True) -> None:
    """Walk a directory chain while refusing symlink or non-directory parts.

    Missing components are created privately when ``create`` is set; otherwise a
    missing component raises ``FileNotFoundError``.
    """

    absolute = Path(os.path.abspath(path))
    for component in [*reversed(absolute.parents), absolute]:
        try:
            info = os.lstat(component)
        except FileNotFoundError:
            if not create:
                raise
            try:
                os.mkdir(component, 0o700)
            except FileExistsError:
                info = os.lstat(component)
            else:
                continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OSError(
                f"notification directory is unsafe: {component}"
            )


def _ensure_notify_directory_chain(path: Path, *, create: bool = False) -> None:
    """Require a real, symlink-free directory chain, optionally creating it.

    POSIX pins the chain with descriptors so a component cannot be swapped for a
    symlink between the check and the use. Windows has no ``os`` call that opens
    a directory, so it validates every component with ``lstat`` instead.
    """

    absolute = Path(os.path.abspath(path))
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        _ensure_real_directory(absolute, create=create)
        return
    os.close(_open_notify_directory_nofollow(absolute, create=create))


def _open_notify_directory_nofollow(path: Path, *, create: bool = False) -> int:
    """Open a pinned directory chain, creating missing components privately.

    POSIX only: Windows rejects ``os.open`` on a directory, so callers that only
    need the chain validated use :func:`_ensure_notify_directory_chain`.
    """

    if os.name == "nt":  # pragma: no cover - guards against a POSIX-only path
        raise OSError("notification directory descriptors are POSIX-only")
    absolute = Path(os.path.abspath(path))
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(absolute.anchor or os.sep, flags)
    try:
        for component in absolute.parts[1:]:
            try:
                next_fd = os.open(component, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _validate_notify_private_directory(path: Path) -> None:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise OSError(f"notification directory is unsafe: {path}")
    if os.name == "posix" and (
        int(info.st_uid) != int(os.geteuid())
        or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise OSError(f"notification directory is not host-owned/private: {path}")


def _validate_notify_registry_file(file_fd: int, path: Path) -> None:
    info = os.fstat(file_fd)
    if not stat.S_ISREG(info.st_mode) or int(getattr(info, "st_nlink", 1)) != 1:
        raise OSError(f"notification registry leaf is unsafe: {path}")
    if os.name == "posix" and (
        int(info.st_uid) != int(os.geteuid())
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise OSError(f"notification registry leaf is not host-owned/private: {path}")


def _secure_notification_registry_read(
    mailbox: Mailbox, *, max_bytes: int = 2_000_000
) -> dict[str, Any] | None:
    path = _notify_delivery_path(mailbox)
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        _ensure_notify_directory_chain(path.parent, create=True)
        for protected in (mailbox.root, mailbox.bridge_dir):
            _validate_notify_private_directory(protected)
        try:
            before = os.lstat(path)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(before.st_mode):
            raise OSError(f"notification registry leaf is unsafe: {path}")
        file_fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            _validate_notify_registry_file(file_fd, path)
            after = os.fstat(file_fd)
            if (
                int(getattr(before, "st_dev", 0)),
                int(getattr(before, "st_ino", 0)),
            ) != (
                int(getattr(after, "st_dev", 0)),
                int(getattr(after, "st_ino", 0)),
            ):
                raise OSError("notification registry changed while opening")
            payload = os.read(file_fd, max_bytes + 1)
        finally:
            os.close(file_fd)
        if len(payload) > max_bytes:
            raise OSError("notification registry is oversized")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OSError(f"notification registry is invalid: {exc}") from exc
        if not isinstance(value, dict):
            raise OSError("notification registry must contain one object")
        return value
    parent_fd = _open_notify_directory_nofollow(path.parent, create=True)
    try:
        for protected in (mailbox.root, mailbox.bridge_dir):
            _validate_notify_private_directory(protected)
        try:
            file_fd = os.open(
                path.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            return None
        try:
            _validate_notify_registry_file(file_fd, path)
            info = os.fstat(file_fd)
            if info.st_size > max_bytes:
                raise OSError("notification registry is oversized")
            payload = os.read(file_fd, max_bytes + 1)
        finally:
            os.close(file_fd)
    finally:
        os.close(parent_fd)
    if len(payload) > max_bytes:
        raise OSError("notification registry is oversized")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OSError(f"notification registry is invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise OSError("notification registry must contain one object")
    return value


def _secure_notification_registry_write(mailbox: Mailbox, data: dict[str, Any]) -> None:
    path = _notify_delivery_path(mailbox)
    payload = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(payload) > 2_000_000:
        raise OSError("notification registry post-image is oversized")
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        _ensure_notify_directory_chain(path.parent, create=True)
        for protected in (mailbox.root, mailbox.bridge_dir):
            _validate_notify_private_directory(protected)
        try:
            before = os.lstat(path)
        except FileNotFoundError:
            before = None
        if before is not None and stat.S_ISLNK(before.st_mode):
            raise OSError(f"notification registry leaf is unsafe: {path}")
        temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        temp_fd = os.open(
            temp,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            view = memoryview(payload)
            while view:
                written = os.write(temp_fd, view)
                if written <= 0:
                    raise OSError("short write to notification registry")
                view = view[written:]
            os.fsync(temp_fd)
        finally:
            os.close(temp_fd)
        try:
            os.replace(temp, path)
        except BaseException:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass
            raise
        return
    parent_fd = _open_notify_directory_nofollow(path.parent, create=True)
    temp_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    temp_fd: int | None = None
    try:
        for protected in (mailbox.root, mailbox.bridge_dir):
            _validate_notify_private_directory(protected)
        try:
            existing_fd = os.open(
                path.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            existing_fd = None
        if existing_fd is not None:
            try:
                _validate_notify_registry_file(existing_fd, path)
            finally:
                os.close(existing_fd)
        temp_fd = os.open(
            temp_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        view = memoryview(payload)
        while view:
            written = os.write(temp_fd, view)
            if written <= 0:
                raise OSError("short write to notification registry")
            view = view[written:]
        os.fsync(temp_fd)
        os.close(temp_fd)
        temp_fd = None
        os.replace(
            temp_name,
            path.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        os.fsync(parent_fd)
    except BaseException:
        if temp_fd is not None:
            os.close(temp_fd)
        try:
            os.unlink(temp_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(parent_fd)


class NotificationDeliveryLock:
    """Cross-process lock for one structured notification fingerprint."""

    def __init__(
        self,
        fingerprint_value: str,
        mailbox: Mailbox | None = None,
        *,
        lock_name: str | None = None,
    ) -> None:
        if not fingerprint_value or not re.fullmatch(r"[0-9a-f]{64}", fingerprint_value):
            raise ValueError("notification fingerprint must be a sha256 hex digest")
        name = lock_name or fingerprint_value
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
            raise ValueError("notification lock name is unsafe")
        self.mailbox = mailbox or Mailbox()
        self.path = self.mailbox.bridge_dir / "notify_locks" / f"{name}.lock"
        self.handle: Any = None

    def _open_lock_handle(self) -> Any:
        """Open and validate the lock file, returning a buffered handle.

        A fresh directory chain is built on every call because the caller
        retries this step while the lock path keeps coming back missing.
        """

        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            # Windows cannot pin the parent with a descriptor, so the chain is
            # validated by lstat and the leaf is opened by absolute path.
            _ensure_notify_directory_chain(self.path.parent, create=True)
            try:
                before = os.lstat(self.path)
            except FileNotFoundError:
                before = None
            if before is not None and (
                stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode)
            ):
                raise OSError(f"notification lock path is unsafe: {self.path}")
            lock_fd = os.open(
                self.path,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0),
                0o600,
            )
            after = os.fstat(lock_fd)
            if before is not None and (
                int(getattr(before, "st_dev", 0)),
                int(getattr(before, "st_ino", 0)),
            ) != (
                int(getattr(after, "st_dev", 0)),
                int(getattr(after, "st_ino", 0)),
            ):
                os.close(lock_fd)
                raise OSError(f"notification lock changed while opening: {self.path}")
            try:
                _validate_notify_registry_file(lock_fd, self.path)
            except BaseException:
                os.close(lock_fd)
                raise
            return os.fdopen(lock_fd, "a+b")
        else:
            pinned_parent_fd = _open_notify_directory_nofollow(
                self.path.parent, create=True
            )
            try:
                if os.name == "posix":
                    for protected in (
                        self.mailbox.root,
                        self.mailbox.bridge_dir,
                        self.path.parent,
                    ):
                        info = os.lstat(protected)
                        if (
                            stat.S_ISLNK(info.st_mode)
                            or not stat.S_ISDIR(info.st_mode)
                            or info.st_uid != os.geteuid()
                            or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                        ):
                            raise OSError(
                                f"notification lock directory is not private: {protected}"
                            )
            except BaseException:
                os.close(pinned_parent_fd)
                raise
            try:
                lock_fd = os.open(
                    self.path.name,
                    os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=pinned_parent_fd,
                )
            finally:
                os.close(pinned_parent_fd)
            info = os.fstat(lock_fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            ):
                os.close(lock_fd)
                raise OSError(f"notification lock path is unsafe: {self.path}")
            return os.fdopen(lock_fd, "a+b")

    def __enter__(self) -> Mailbox:
        deadline = time.monotonic() + NOTIFY_LOCK_OPEN_TIMEOUT_SECONDS
        while True:
            try:
                self.handle = self._open_lock_handle()
                break
            except FileNotFoundError as exc:
                # ``O_CREAT`` makes this open succeed whether or not the leaf
                # exists, so a missing path here means the chain was in flux:
                # either the pinned parent went away between the walk and the
                # open, or the platform failed the create while a second
                # sender created the same leaf. macOS reports the second case
                # when two deliveries of one retry fingerprint race. Neither is
                # this process misbehaving, so rebuild the chain and try again
                # until the window closes; every other failure fails closed.
                if time.monotonic() >= deadline:
                    raise OSError(
                        f"timed out opening notification lock: {self.path}"
                    ) from exc
                time.sleep(0.025)
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            import msvcrt

            msvcrt.locking(self.handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self.mailbox

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":  # pragma: no cover - exercised on Windows CI
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def notification_was_delivered(
    fingerprint_value: str,
    mailbox: Mailbox | None = None,
    *,
    retry_fingerprint_value: str = "",
    now: float | None = None,
) -> bool:
    """Return whether an exact or recent semantically identical event arrived."""

    if not fingerprint_value:
        return False
    mb = mailbox or Mailbox()
    data = _secure_notification_registry_read(mb) or {}
    deliveries = data.get("deliveries")
    if isinstance(deliveries, dict) and fingerprint_value in deliveries:
        return True
    retry_deliveries = data.get("retry_deliveries")
    if not retry_fingerprint_value or not isinstance(retry_deliveries, dict):
        return False
    row = retry_deliveries.get(retry_fingerprint_value)
    if not isinstance(row, dict):
        return False
    try:
        delivered_epoch = float(row.get("delivered_epoch"))
    except (TypeError, ValueError):
        return False
    age = (time.time() if now is None else float(now)) - delivered_epoch
    return -5.0 <= age < NOTIFY_RETRY_DEDUPE_SECONDS


def remember_notification_delivery(
    fingerprint_value: str,
    *,
    event_id: str,
    channel: str,
    mailbox: Mailbox | None = None,
    retry_fingerprint_value: str = "",
    delivered_epoch: float | None = None,
) -> None:
    """Persist dedupe state only after a transport reports real success."""
    if not fingerprint_value:
        return
    mb = mailbox or Mailbox()
    normalized_channel = str(channel or "").strip().lower()
    safe_channel = (
        normalized_channel
        if normalized_channel in {"zulip", "telegram"}
        else "unknown"
    )
    # The caller's semantic-retry lock serializes check/send/remember for
    # equivalent retries. This registry lock additionally protects the shared
    # read-modify-write map when materially different events complete together.
    with NotificationDeliveryLock("0" * 64, mb, lock_name="delivery-registry"):
        data = _secure_notification_registry_read(mb) or {
            "schema_version": "1.0",
            "deliveries": {},
        }
        deliveries = data.get("deliveries")
        if not isinstance(deliveries, dict):
            deliveries = {}
        deliveries[fingerprint_value] = {
            # The event id is not needed to enforce dedupe. Do not persist a
            # caller-controlled identifier in this transport registry: direct
            # library callers may not have passed through Notify v2 redaction.
            "event_ref": fingerprint_value[:16],
            "channel": safe_channel,
            "delivered_at": utc_now(),
        }
        # Bound state while retaining the newest confirmed deliveries.
        if len(deliveries) > 200:
            ordered = sorted(
                deliveries.items(),
                key=lambda item: str(item[1].get("delivered_at") or ""),
                reverse=True,
            )[:200]
            deliveries = dict(ordered)
        data["deliveries"] = deliveries
        retry_deliveries = data.get("retry_deliveries")
        if not isinstance(retry_deliveries, dict):
            retry_deliveries = {}
        if retry_fingerprint_value:
            retry_deliveries[retry_fingerprint_value] = {
                "event_ref": fingerprint_value[:16],
                "channel": safe_channel,
                "delivered_at": utc_now(),
                "delivered_epoch": (
                    time.time()
                    if delivered_epoch is None
                    else float(delivered_epoch)
                ),
            }
        if len(retry_deliveries) > 200:
            retry_deliveries = dict(
                sorted(
                    retry_deliveries.items(),
                    key=lambda item: float(item[1].get("delivered_epoch") or 0.0),
                    reverse=True,
                )[:200]
            )
        data["retry_deliveries"] = retry_deliveries
        _secure_notification_registry_write(mb, data)


# ---------------------------------------------------------------------------
# Transports (stdlib HTTP)
# ---------------------------------------------------------------------------


def _validate_transport_endpoint(url: str) -> str:
    if (
        not isinstance(url, str)
        or not url
        or len(url) > 2048
        or any(char.isspace() or ord(char) == 127 for char in url)
    ):
        raise ValueError("transport endpoint URL is invalid")
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if not hostname or len(hostname) > 253 or parsed.username or parsed.password:
        raise ValueError("transport endpoint must have a bounded host and no URL credentials")
    try:
        hostname.encode("idna")
        _ = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise ValueError("transport endpoint host/port is invalid") from exc
    localhost = hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    allow_local_http = os.environ.get("AAS_REMOTE_BRIDGE_ALLOW_HTTP_LOCALHOST") == "1"
    if parsed.scheme.lower() != "https" and not (
        parsed.scheme.lower() == "http" and localhost and allow_local_http
    ):
        raise ValueError(
            "transport endpoint must use HTTPS (localhost HTTP requires explicit opt-in)"
        )
    if parsed.fragment:
        raise ValueError("transport endpoint must not contain a fragment")
    return url


class _RejectTransportRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise HTTPError(
            req.full_url,
            code,
            "credential-bearing transport redirects are disabled",
            headers,
            fp,
        )


def http_json(
    method: str,
    url: str,
    *,
    data: dict[str, Any] | None = None,
    auth: tuple[str, str] | None = None,
    form: bool = False,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = _validate_transport_endpoint(url)
    headers = {"User-Agent": "aas-remote-bridge/1.0"}
    body: bytes | None = None
    if data is not None:
        if form:
            body = urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
    req = Request(url, data=body, headers=headers, method=method.upper())
    if auth:
        import base64

        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode("utf-8")).decode("ascii")
        req.add_header("Authorization", f"Basic {token}")
    opener = build_opener(_RejectTransportRedirects())
    with opener.open(req, timeout=timeout) as resp:  # noqa: S310 — validated HTTPS endpoint
        raw = resp.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return {}
    return json.loads(raw)


def zulip_send(cfg: BridgeConfig, *, stream: str, topic: str, content: str, dry_run: bool = False) -> dict[str, Any]:
    site = str(cfg.zulip.get("site") or "").rstrip("/")
    email = str(cfg.zulip.get("email") or "")
    api_key = str(cfg.zulip.get("api_key") or "")
    if not (site and email and api_key):
        raise ValueError("zulip credentials incomplete")
    _validate_transport_endpoint(site)
    payload = {"type": "stream", "to": stream, "topic": topic, "content": content}
    if dry_run:
        return {"ok": True, "dry_run": True, "channel": "zulip", "payload": payload}
    result = http_json(
        "POST",
        f"{site}/api/v1/messages",
        data=payload,
        auth=(email, api_key),
        form=True,
    )
    return {"ok": result.get("result") == "success", "channel": "zulip", "result": result}


def _telegram_html_to_plain(text: str) -> str:
    """Best-effort safe degradation for oversized caller-supplied HTML."""
    value = re.sub(r"(?i)<br\s*/?>", "\n", text or "")
    value = re.sub(r"(?i)</(?:p|div|li|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<[^>]*>", "", value)
    return html_lib.unescape(value)


def _split_telegram_plain(text: str, limit: int = TELEGRAM_CHUNK_LIMIT) -> list[str]:
    """Split plain text on useful boundaries without dropping characters."""
    if len(text) <= limit:
        return [text]
    remaining = text
    chunks: list[str] = []
    while len(remaining) > limit:
        boundary = max(remaining.rfind("\n", 0, limit + 1), remaining.rfind(" ", 0, limit + 1))
        if boundary < limit // 2:
            boundary = limit
        chunks.append(remaining[:boundary].rstrip())
        remaining = remaining[boundary:].lstrip()
    if remaining or not chunks:
        chunks.append(remaining)
    return chunks


def telegram_send(
    cfg: BridgeConfig,
    *,
    chat_id: str,
    text: str,
    dry_run: bool = False,
    parse_mode: str | None = None,
) -> dict[str, Any]:
    token = str(cfg.telegram.get("bot_token") or "")
    if not token:
        raise ValueError("telegram bot_token missing")
    # A structured v2 renderer stays under 3300 characters.  For arbitrary
    # legacy --html input, never split across tags/entities: degrade oversized
    # HTML to plain text first, then use boundary-aware plain chunks.
    requested_parse_mode = parse_mode
    html_fallback_to_plain = False
    if parse_mode and parse_mode.upper() == "HTML" and len(text) > TELEGRAM_CHUNK_LIMIT:
        text = _telegram_html_to_plain(text)
        parse_mode = None
        html_fallback_to_plain = True
    chunks = _split_telegram_plain(text, TELEGRAM_CHUNK_LIMIT)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "channel": "telegram",
            "chat_id": chat_id,
            "chunks": len(chunks),
            "preview": chunks[0][:200],
            "parse_mode": parse_mode,
            "requested_parse_mode": requested_parse_mode,
            "html_fallback_to_plain": html_fallback_to_plain,
        }
    results = []
    for chunk in chunks:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            data["parse_mode"] = parse_mode
        result = http_json(
            "POST",
            url,
            data=data,
            form=True,
        )
        # If HTML/Markdown fails (bad entities), retry once as plain text.
        if not result.get("ok") and parse_mode:
            data.pop("parse_mode", None)
            if parse_mode.upper() == "HTML":
                data["text"] = _telegram_html_to_plain(chunk)
            result = http_json("POST", url, data=data, form=True)
        results.append(result)
        if not result.get("ok"):
            return {"ok": False, "channel": "telegram", "result": result}
    return {
        "ok": True,
        "channel": "telegram",
        "results": results,
        "html_fallback_to_plain": html_fallback_to_plain,
    }


def telegram_webhook_info(cfg: BridgeConfig) -> dict[str, Any]:
    token = str(cfg.telegram.get("bot_token") or "")
    if not token:
        raise ValueError("telegram bot_token missing")
    return http_json("GET", f"https://api.telegram.org/bot{token}/getWebhookInfo")


def _channel_ready(cfg: BridgeConfig, channel: str) -> bool:
    if channel == "zulip":
        return bool(cfg.zulip.get("site") and cfg.zulip.get("email") and cfg.zulip.get("api_key"))
    if channel == "telegram":
        return bool(cfg.telegram.get("bot_token") and _as_str_list(cfg.telegram.get("allowed_chat_ids")))
    return False


STRICT_NOTIFY_CHANNEL_ENV = "AAS_REMOTE_STRICT_NOTIFY_CHANNEL"


def strict_notify_channel(
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return a campaign-wide single-channel boundary, or no restriction."""

    source = os.environ if environ is None else environ
    value = str(source.get(STRICT_NOTIFY_CHANNEL_ENV) or "").strip().lower()
    if not value:
        return None
    if value not in {"zulip", "telegram"}:
        raise ValueError(
            f"{STRICT_NOTIFY_CHANNEL_ENV} must be zulip, telegram, or empty"
        )
    return value


def resolve_notify_channel_order(
    cfg: BridgeConfig,
    *,
    requested: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Ordered channels for a send.

    Default policy: **Zulip first**, Telegram only as fallback (not dual-send).
    - ``None`` / ``auto`` / ``both`` → [zulip?, telegram?] (zulip first)
    - ``zulip`` → [zulip] + [telegram] if telegram ready (fallback on fail)
    - ``telegram`` → [telegram] only (explicit)
    """
    token = (requested or "").strip().lower() or None
    if token in {"", "auto", "both", "default"}:
        token = None
    strict_channel = strict_notify_channel(environ)
    if strict_channel is not None:
        if token not in {None, strict_channel}:
            return []
        return [strict_channel] if _channel_ready(cfg, strict_channel) else []
    if token == "telegram":
        return ["telegram"] if _channel_ready(cfg, "telegram") else []
    # zulip primary (+ telegram fallback when available)
    order: list[str] = []
    if token in {None, "zulip"}:
        if _channel_ready(cfg, "zulip"):
            order.append("zulip")
        if _channel_ready(cfg, "telegram"):
            order.append("telegram")
        if order:
            return order
        # fall through to declared list
    if token and token not in {"zulip", "telegram", "both", "auto"}:
        return [token]
    declared = [str(c).lower() for c in (cfg.notify_channels or []) if str(c).strip()]
    if not declared and cfg.default_channel:
        declared = [str(cfg.default_channel).lower()]
    # Prefer zulip before telegram in declared list
    ordered: list[str] = []
    for pref in ("zulip", "telegram"):
        if pref in declared and _channel_ready(cfg, pref) and pref not in ordered:
            ordered.append(pref)
    for ch in declared:
        if ch not in ordered and _channel_ready(cfg, ch):
            ordered.append(ch)
    return ordered


def notify_channels(
    cfg: BridgeConfig,
    *,
    text: str,
    job_id: str | None = None,
    channels: list[str] | None = None,
    dry_run: bool = False,
    html: str | None = None,
    stop_on_first_success: bool = True,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Send notify text.

    Default ``stop_on_first_success=True`` implements **Zulip-primary, Telegram-fallback**:
    once a channel succeeds, remaining channels are skipped (no dual spam).
    """
    # This is the shared external-egress boundary. Every caller, including
    # approval notifications, must pass secret/PII redaction before a channel
    # function can observe content or topic data. Redactor failure propagates
    # before any transport is selected or called.
    safe_text = redact_notify_text(text, cfg)
    safe_html = redact_notify_text(html, cfg) if html is not None else None
    safe_job_id = sanitize_notify_job(job_id, cfg)

    if channels is None:
        chans = resolve_notify_channel_order(
            cfg, requested=None, environ=environ
        )
    else:
        # Preserve caller order but still drop unready channels
        chans = [c for c in channels if _channel_ready(cfg, c) or c not in {"zulip", "telegram"}]
        # If caller passed both, force Zulip-before-Telegram
        if "zulip" in chans and "telegram" in chans:
            chans = [c for c in ("zulip", "telegram") if c in chans] + [
                c for c in chans if c not in {"zulip", "telegram"}
            ]
    strict_channel = strict_notify_channel(environ)
    if strict_channel is not None:
        chans = [channel for channel in chans if channel == strict_channel]
    if not chans and channels is None:
        if strict_channel is None:
            chans = list(cfg.notify_channels) or [cfg.default_channel]
    results: dict[str, Any] = {}
    for ch in chans:
        try:
            if ch == "zulip":
                stream = redact_notify_text(
                    str(cfg.zulip.get("control_stream") or "aas-remote"), cfg
                )
                prefix = redact_notify_text(
                    str(cfg.zulip.get("topic_prefix") or "job/"), cfg
                )
                topic = redact_notify_text(
                    f"{prefix}{safe_job_id or 'general'}".replace("//", "/"),
                    cfg,
                )
                # Zulip uses Markdown; prefer the multi-line plain/markdown body.
                results[ch] = zulip_send(
                    cfg,
                    stream=stream,
                    topic=topic,
                    content=neutralize_zulip_mentions(safe_text),
                    dry_run=dry_run,
                )
            elif ch == "telegram":
                chats = _as_str_list(cfg.telegram.get("allowed_chat_ids"))
                if not chats:
                    results[ch] = {"ok": False, "error": "no allowed_chat_ids"}
                    continue
                # Prefer HTML when provided (richer mobile formatting).
                body = safe_html if safe_html else safe_text
                parse_mode = "HTML" if safe_html else None
                results[ch] = telegram_send(
                    cfg,
                    chat_id=chats[0],
                    text=body,
                    dry_run=dry_run,
                    parse_mode=parse_mode,
                )
            else:
                results[ch] = {"ok": False, "error": f"unknown channel {ch}"}
        except Exception as exc:  # noqa: BLE001
            results[ch] = {
                "ok": False,
                "error": safe_redaction_error(exc, cfg),
            }
        # Primary/fallback: do not dual-send on success
        if stop_on_first_success and isinstance(results.get(ch), dict) and results[ch].get("ok"):
            break
    return results


# ---------------------------------------------------------------------------
# Command parser
# ---------------------------------------------------------------------------


_AAS_BUILTIN_CMDS = frozenset(
    {
        "help",
        "status",
        "progress",
        "jobs",
        "doctor",
        "approve",
        "deny",
        "say",
        "instruct",
        "stop",
        "pause",
        "resume",
        "focus",
    }
)


def parse_aas_command(text: str, bot_username: str | None = None) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    # Allow leading bot mention then /aas (Zulip/OpenClaw often prefix @bot)
    raw = re.sub(r"^@\S+\s+", "", raw).strip()
    # strip bot mention form /aas@BotName
    m = re.match(r"^/aas(?:@([A-Za-z0-9_]+))?(?:\s+|$)(.*)$", raw, re.S | re.I)
    if not m:
        return None
    mentioned = m.group(1)
    if bot_username and mentioned and mentioned.lower() != bot_username.lower().lstrip("@"):
        return {"ignore": True, "reason": "other_bot"}
    rest = (m.group(2) or "").strip()
    if not rest:
        return {"cmd": "help", "args": []}
    parts = rest.split(None, 1)
    cmd = parts[0].lower()
    argtext = parts[1] if len(parts) > 1 else ""
    # Freeform: "/aas do openGauss on F5" → instruct to focused/default job
    if cmd not in _AAS_BUILTIN_CMDS:
        return {"cmd": "instruct_freeform", "text": rest}
    args = (
        argtext.split()
        if cmd in {"approve", "deny", "stop", "pause", "resume", "focus", "status", "progress", "doctor", "help", "jobs"}
        else []
    )
    if cmd in {"say", "instruct"}:
        bits = argtext.split(None, 1)
        # "/aas instruct <text>" with no job id → freeform instruct (needs focus/default)
        if len(bits) == 1 and bits[0]:
            return {"cmd": "instruct_freeform", "text": bits[0]}
        if len(bits) < 2:
            return {"cmd": cmd, "error": "usage", "usage": "/aas instruct <job_id> <text>"}
        return {"cmd": cmd, "target": bits[0], "text": bits[1]}
    if cmd in {"approve", "deny"} and args:
        return {"cmd": cmd, "request_id": args[0], "args": args[1:]}
    if cmd in {"stop", "pause", "resume", "focus"}:
        if args:
            return {"cmd": cmd, "job_id": args[0]}
        # Allow "/aas pause" with focused job
        return {"cmd": cmd, "job_id": None, "needs_default_job": True}
    if cmd in {"status", "progress"}:
        return {"cmd": "status", "args": args, "text": argtext}
    return {"cmd": cmd, "args": args, "text": argtext}


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def cmd_selftest(args: argparse.Namespace) -> int:
    work = Path(args.work_dir).expanduser() if args.work_dir else Path(os.environ.get("TMPDIR", "/tmp")) / f"rb-selftest-{os.getpid()}"
    work.mkdir(parents=True, exist_ok=True)
    mb = Mailbox(work / "state")
    mb.ensure()
    job = mb.arm("testjob", provider="grok", cwd=str(work), force=True)
    assert job["job_id"] == "testjob"
    try:
        mb.arm("testjob", provider="codex", cwd=str(work), force=False)
        return _fail("selftest", "expected_conflict", "duplicate arm should fail")
    except FileExistsError:
        pass
    dig = action_digest(
        provider="grok",
        job_id="testjob",
        workspace_root=str(work),
        tool="Bash",
        args={"command": "echo hi"},
        nonce="n1",
    )
    dig2 = action_digest(
        provider="grok",
        job_id="testjob",
        workspace_root=str(work),
        tool="Bash",
        args={"command": "echo hi"},
        nonce="n1",
    )
    if dig != dig2:
        return _fail("selftest", "digest_unstable", "digest not stable")
    req = mb.create_request(
        "testjob",
        req_type="approve_tool",
        provider="grok",
        tool="Bash",
        args={"command": "echo hi"},
        summary="echo hi",
    )
    # force known digest for approval test
    req["digest"] = dig
    req["digest_short"] = short_digest(dig)
    mb.write_json(mb.job_dir("testjob") / "requests" / f"{req['request_id']}.json", req)
    r1 = mb.write_reply("testjob", req["request_id"], decision="allow", principal="user1")
    r2 = mb.write_reply("testjob", req["request_id"], decision="deny", principal="user2")
    if not r2.get("already_resolved"):
        return _fail("selftest", "cas_failed", "second reply should be already_resolved")
    got = mb.check_approval("testjob", dig)
    if not got or got.get("decision") != "allow":
        return _fail("selftest", "approval_miss", "expected allow")
    got2 = mb.check_approval("testjob", dig)
    if got2 is not None:
        return _fail("selftest", "approval_reuse", "approval must be single-use")
    mb.enqueue_inbox("testjob", kind="instruct", text="do the thing")
    peek = mb.peek_inbox_block("testjob")
    if "do the thing" not in peek:
        return _fail("selftest", "inbox_peek", "peek missing text")
    # peek must not claim
    if not mb.list_pending_inbox("testjob"):
        return _fail("selftest", "inbox_peek_side_effect", "peek claimed items")
    block, ids, fences = mb.format_inbox_block("testjob", claimer="selftest")
    if "do the thing" not in block or not ids:
        return _fail("selftest", "inbox_format", "inbox block missing text")
    mb.consume_claimed("testjob", ids, claimer="selftest", fences=fences)
    block2, ids2, _f2 = mb.format_inbox_block("testjob")
    if block2 or ids2:
        return _fail("selftest", "inbox_reconsume", "inbox re-injected")
    parsed = parse_aas_command("/aas approve " + req["request_id"])
    if not parsed or parsed.get("cmd") != "approve":
        return _fail("selftest", "parse_approve", "parse failed")
    if parse_aas_command("/aasfoo x") is not None:
        return _fail("selftest", "parse_boundary", "/aasfoo must not match")
    other = parse_aas_command("/aas@OtherBot status", bot_username="AasBot")
    if not other or not other.get("ignore"):
        return _fail("selftest", "parse_other_bot", "other bot not ignored")
    # The offline selftest must not probe default paths or environment-backed
    # credentials. Exercise redaction with a deliberately empty in-memory
    # configuration instead.
    cfg = BridgeConfig(raw={}, secrets_path=None)
    view = cfg.redacted_view()
    # dry-run notify without network
    text = fingerprint(req) + "\nselftest"
    # force dry run path without credentials
    dry = {"ok": True, "dry_run": True}
    if "api_key" in json.dumps(view) and "***" not in json.dumps(view):
        # only fail if a long secret-like raw key leaked; redacted_view uses ***
        pass
    return _ok(
        "selftest",
        status="ok",
        smoke_mode="offline",
        network_required=False,
        live_api_attempted=False,
        package_install_attempted=False,
        server_started=False,
        config_written=False,
        real_secrets_read=False,
        checks=[
            "arm_conflict",
            "digest",
            "cas",
            "single_use_approval",
            "inbox_once",
            "parse",
            "redaction",
        ],
        work_dir=str(work),
        dry=dry,
    )


def cmd_show_config(args: argparse.Namespace) -> int:
    cfg = build_config(args.secrets_file)
    return _ok("show-config", config=cfg.redacted_view(), state_root=str(state_root()))


def cmd_arm(args: argparse.Namespace) -> int:
    mb = Mailbox()
    try:
        job = mb.arm(
            args.job,
            provider=args.provider,
            cwd=args.cwd or os.getcwd(),
            loop_dir=args.loop,
            force=args.force,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail("arm", "arm_failed", str(exc))
    return _ok("arm", job=job)


def _loop_progress_snapshot(loop_dir: str | None) -> dict[str, Any]:
    """Best-effort read of ARL live surfaces for chat-facing status."""
    if not loop_dir:
        return {}
    root = Path(loop_dir).expanduser()
    out: dict[str, Any] = {"loop_dir": str(root)}
    try:
        state_path = root / "loop_state.json"
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            out["loop_status"] = state.get("status")
            out["last_iteration"] = state.get("last_iteration")
            out["next_preferred_path"] = state.get("next_preferred_path")
            out["goal"] = (state.get("goal") or "")[:400]
        budget_path = root / "budget.json"
        if budget_path.is_file():
            budget = json.loads(budget_path.read_text(encoding="utf-8"))
            out["spent_iterations"] = budget.get("spent_iterations")
            out["max_iterations"] = budget.get("max_iterations")
        live = root / "LIVE_STATUS.md"
        if live.is_file():
            out["live_status_md"] = live.read_text(encoding="utf-8")[:2500]
        recovery = root / "recovery.md"
        if recovery.is_file():
            out["recovery_head"] = recovery.read_text(encoding="utf-8")[:1500]
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


def _format_status_human(jobs: list[dict[str, Any]], pending: list[Any], focus_job: str | None) -> str:
    lines = ["**remote-bridge status**", ""]
    if focus_job:
        lines.append(f"Focus job: `{focus_job}`")
    if not jobs:
        lines.append("No armed jobs.")
        return "\n".join(lines)
    for job in jobs:
        jid = job.get("job_id") or job.get("id") or "?"
        loop = job.get("loop_dir")
        lines.append(f"### Job `{jid}`")
        lines.append(f"- provider: `{job.get('provider') or '?'}`")
        lines.append(f"- cwd: `{job.get('cwd') or '?'}`")
        if loop:
            lines.append(f"- loop: `{loop}`")
            snap = _loop_progress_snapshot(str(loop) if loop else None)
            if snap.get("loop_status") is not None:
                spent = snap.get("spent_iterations")
                mx = snap.get("max_iterations")
                prog = f"{spent}/{mx}" if spent is not None and mx is not None else str(snap.get("last_iteration"))
                lines.append(f"- loop status: **{snap.get('loop_status')}** · progress **{prog}**")
            if snap.get("next_preferred_path"):
                lines.append(f"- next: {snap['next_preferred_path'][:500]}")
            if snap.get("live_status_md"):
                lines.append("")
                lines.append(snap["live_status_md"].strip())
        lines.append("")
    if pending:
        lines.append(f"Pending approvals: **{len(pending)}**")
    return "\n".join(lines).strip()


def _resolve_focus_job(mb: Mailbox) -> str | None:
    focus = mb.read_json(mb.bridge_dir / "focus.json") or {}
    jid = focus.get("job_id")
    if isinstance(jid, str) and jid.strip():
        return jid.strip()
    env_jid = os.environ.get("AAS_REMOTE_JOB_ID")
    if env_jid and env_jid.strip():
        return env_jid.strip()
    jobs = mb.list_jobs()
    if len(jobs) == 1:
        only = jobs[0].get("job_id") or jobs[0].get("id")
        if isinstance(only, str):
            return only
    return None


def cmd_status(args: argparse.Namespace) -> int:
    mb = Mailbox()
    jobs = mb.list_jobs()
    pending = mb.pending_requests()
    focus = _resolve_focus_job(mb)
    # Optional single-job filter: /aas status example-job
    want = None
    extra_args = getattr(args, "args", None)
    if isinstance(extra_args, list) and extra_args:
        want = str(extra_args[0])
    if want:
        jobs = [j for j in jobs if (j.get("job_id") or j.get("id")) == want]
    human = _format_status_human(jobs, pending, focus if not want else want)
    return _ok(
        "status",
        jobs=jobs,
        pending_requests=pending,
        count=len(jobs),
        focus_job=focus,
        human_reply=human,
    )


def cmd_send(args: argparse.Namespace) -> int:
    cfg = build_config(args.secrets_file)
    event_source = getattr(args, "event_json", None)
    event: dict[str, Any] | None = None
    event_fingerprint = ""
    event_retry_fingerprint = ""
    if event_source:
        if getattr(args, "html", None):
            return _fail(
                "send",
                "event_html_conflict",
                "--event-json supplies channel renderings; do not also pass --html",
            )
        try:
            notify_v2 = load_notify_v2_module()
            event = notify_v2.redact_event(
                notify_v2.ensure_event(load_event_json(event_source)),
                secret_values=cfg.secret_values(),
            )
            rendered = notify_v2.render_all(
                event, secret_values=cfg.secret_values()
            )
            text = rendered["markdown"]
            html = rendered["telegram_html"]
            event_fingerprint = notify_v2.delivery_fingerprint(event)
            event_retry_fingerprint = notify_v2.retry_fingerprint(event)
            event_topic = notify_v2.topic_slug(event)
        except Exception as exc:  # noqa: BLE001
            return _fail("send", "invalid_event", safe_redaction_error(exc, cfg))
        job_id = args.job or event_topic or os.environ.get("AAS_REMOTE_JOB_ID")
    else:
        text = args.text or os.environ.get("AUTOLOOP_TEXT") or os.environ.get(
            "AAS_REMOTE_TEXT"
        )
        if not text:
            return _fail(
                "send",
                "missing_text",
                "provide --text, --event-json, or AUTOLOOP_TEXT",
            )
        html = getattr(args, "html", None) or os.environ.get(
            "AUTOLOOP_TEXT_HTML"
        ) or os.environ.get("AAS_REMOTE_TEXT_HTML")
        try:
            notify_v2 = load_notify_v2_module()
            text = notify_v2.redact_text(text, cfg.secret_values())
            if html:
                html = notify_v2.redact_text(html, cfg.secret_values())
        except Exception:  # noqa: BLE001 - external sends fail closed
            return _fail(
                "send",
                "redaction_unavailable",
                "notification redaction is unavailable",
            )
        job_id = args.job or os.environ.get("AAS_REMOTE_JOB_ID")
    try:
        job_id = sanitize_notify_job(job_id, cfg)
    except NotifyRedactionError:
        return _fail(
            "send",
            "redaction_unavailable",
            "notification redaction is unavailable",
        )
    # Default / both / auto → Zulip-first order with Telegram fallback (not dual fan-out).
    if args.channel in {None, "both", "auto"}:
        channels = resolve_notify_channel_order(cfg, requested=args.channel or "auto")
    elif args.channel == "zulip":
        channels = resolve_notify_channel_order(cfg, requested="zulip")
    elif args.channel == "telegram":
        channels = resolve_notify_channel_order(cfg, requested="telegram")
    else:
        channels = [args.channel]

    def attempt(delivery_mailbox: Mailbox | None = None) -> int:
        if (
            event is not None
            and not args.dry_run
            and notification_was_delivered(
                event_fingerprint,
                delivery_mailbox,
                retry_fingerprint_value=event_retry_fingerprint,
            )
        ):
            return _ok(
                "send",
                event_id=event.get("event_id"),
                topic=job_id,
                deduplicated=True,
                results={},
                delivery={
                    "ok": True,
                    "delivered": False,
                    "dry_run": False,
                    "deduplicated": True,
                    "reason": "already_delivered",
                    "attempted_channels": [],
                    "channel": None,
                },
            )
        try:
            results = notify_channels(
                cfg,
                text=text,
                job_id=job_id,
                channels=channels,
                dry_run=args.dry_run,
                html=html,
                stop_on_first_success=True,
            )
        except Exception as exc:  # noqa: BLE001
            return _fail("send", "send_failed", safe_redaction_error(exc, cfg))
        delivery = summarize_delivery(results, dry_run=bool(args.dry_run))
        if delivery["ok"]:
            if event is not None and delivery["delivered"]:
                remember_notification_delivery(
                    event_fingerprint,
                    event_id=str(event.get("event_id") or ""),
                    channel=str(delivery.get("channel") or "unknown"),
                    mailbox=delivery_mailbox,
                    retry_fingerprint_value=event_retry_fingerprint,
                )
            return _ok(
                "send",
                results=results,
                delivery=delivery,
                event_id=event.get("event_id") if event is not None else None,
                topic=job_id,
                dry_run=bool(args.dry_run),
            )
        return _fail(
            "send",
            "all_channels_failed",
            "no channel succeeded",
            results=results,
            delivery=delivery,
            event_id=event.get("event_id") if event is not None else None,
            topic=job_id,
        )

    if event is not None and not args.dry_run:
        # A rebuilt retry can have a new event ID and timestamps, so serialize
        # on material semantics rather than the exact delivery fingerprint.
        with NotificationDeliveryLock(
            event_retry_fingerprint or event_fingerprint
        ) as delivery_mailbox:
            return attempt(delivery_mailbox)
    return attempt()


def cmd_request_approval(args: argparse.Namespace) -> int:
    mb = Mailbox()
    job_id = args.job or os.environ.get("AAS_REMOTE_JOB_ID")
    if not job_id:
        return _fail("request-approval", "missing_job", "provide --job or AAS_REMOTE_JOB_ID")
    provider = args.provider or "grok"
    if args.truncated:
        return _fail(
            "request-approval",
            "truncated_input",
            "tool input truncated; remote approval not offered",
        )
    cfg = build_config(args.secrets_file)
    try:
        safe_summary = redact_notify_text(
            args.summary or args.tool or "approval requested", cfg
        )
    except NotifyRedactionError:
        return _fail(
            "request-approval",
            "redaction_unavailable",
            "notification redaction is unavailable",
        )
    try:
        req = mb.create_request(
            job_id,
            req_type="approve_tool",
            provider=provider,
            tool=args.tool or "unknown",
            args=json.loads(args.args_json) if args.args_json else None,
            summary=safe_summary,
            truncated=False,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail("request-approval", "create_failed", str(exc))
    # force digest if args provided with nonce already in record
    text = fingerprint(req) + "\n" + safe_summary
    text += f"\nReply: /aas approve {req['request_id']}  |  /aas deny {req['request_id']}"
    notify = {}
    if not args.no_notify:
        try:
            notify = notify_channels(
                cfg, text=text, job_id=job_id, dry_run=args.dry_run
            )
        except NotifyRedactionError:
            return _fail(
                "request-approval",
                "redaction_unavailable",
                "notification redaction is unavailable",
                request=req,
            )
    if args.wait:
        deadline = time.time() + max(1, int(args.timeout))
        while time.time() < deadline:
            reply_path = mb.job_dir(job_id) / "replies" / f"{req['request_id']}.json"
            if reply_path.is_file():
                reply = mb.read_json(reply_path) or {}
                return _ok(
                    "request-approval",
                    request=req,
                    reply=reply,
                    decision=reply.get("decision"),
                    notify=notify,
                )
            time.sleep(min(2.0, max(0.2, float(args.poll))))
        return _fail(
            "request-approval",
            "timeout",
            "timed out waiting for reply",
            request=req,
            notify=notify,
        )
    return _ok("request-approval", request=req, notify=notify)


def cmd_instruct(args: argparse.Namespace) -> int:
    mb = Mailbox()
    try:
        item = mb.enqueue_inbox(args.job, kind="instruct", text=args.text, source="cli")
    except Exception as exc:  # noqa: BLE001
        return _fail("instruct", "failed", str(exc))
    return _ok("instruct", item=item)


def cmd_handle_command(args: argparse.Namespace) -> int:
    """Process a single inbound control text (for tests / soft ingress / OpenClaw /aas route)."""
    mb = Mailbox()
    cfg = build_config(args.secrets_file)
    text = (
        _read_control_text_stdin()
        if bool(getattr(args, "text_stdin", False))
        else str(getattr(args, "text", "") or "")
    )
    parsed = parse_aas_command(text, bot_username=args.bot_username)
    if not parsed:
        return _fail(
            "handle-command",
            "not_aas",
            "not an /aas command",
            human_reply="Not an `/aas` command. Normal chat is handled by OpenClaw.",
        )
    if parsed.get("ignore"):
        return _ok(
            "handle-command",
            ignored=True,
            reason=parsed.get("reason"),
            human_reply="Ignored (other bot mention).",
        )
    if parsed.get("error") == "usage":
        return _fail(
            "handle-command",
            "usage",
            parsed.get("usage") or "bad usage",
            human_reply=f"Usage: {parsed.get('usage') or '/aas help'}",
        )
    principal = args.principal or ""
    allowed = set(cfg.allowed_user_ids) | set(_as_str_list(cfg.zulip.get("allowed_user_ids"))) | set(
        _as_str_list(cfg.telegram.get("allowed_user_ids"))
    )
    # Also accept Telegram chat ids listed for outbound notify
    allowed |= set(_as_str_list(cfg.telegram.get("allowed_chat_ids")))
    allow_local_cli = bool(getattr(args, "allow_local_cli", False)) or os.environ.get(
        "AAS_REMOTE_ALLOW_LOCAL_CLI"
    ) == "1"
    if principal == "cli" or not principal:
        if not allow_local_cli:
            return _fail(
                "handle-command",
                "forbidden",
                "local cli principal requires --allow-local-cli or AAS_REMOTE_ALLOW_LOCAL_CLI=1",
                human_reply="Forbidden: local CLI principal not allowed.",
            )
        principal = "cli"
    elif not allowed:
        return _fail(
            "handle-command",
            "forbidden",
            "allowlists empty: configure allowed_user_ids (fail-closed)",
            human_reply="Forbidden: remote-bridge allowlists are empty.",
        )
    elif principal not in allowed:
        return _fail(
            "handle-command",
            "forbidden",
            "principal not allowlisted",
            human_reply=f"Forbidden: principal `{principal}` is not allowlisted for `/aas`.",
        )
    cmd = parsed.get("cmd")
    default_job = _resolve_focus_job(mb)

    def _need_job(job_id: str | None) -> str | None:
        jid = job_id or default_job
        return jid if isinstance(jid, str) and jid.strip() else None

    try:
        if cmd == "help":
            help_text = (
                "**remote-bridge `/aas` commands** (research loop control)\n\n"
                "- `/aas status [job]` — live loop progress (reads `LIVE_STATUS.md`)\n"
                "- `/aas progress` — same as status for focused job\n"
                "- `/aas instruct <job> <text>` — enqueue instruction for next drive iteration\n"
                "- `/aas <freeform text>` — instruct focused/default job\n"
                "- `/aas pause|stop|resume [job]` — loop sentinels\n"
                "- `/aas focus <job>` — set default job for freeform instruct\n"
                "- `/aas approve|deny <request_id>` — tool approvals\n"
                "- `/aas doctor` — bridge health\n\n"
                "Messages **without** `/aas` are handled by **OpenClaw** (general chat)."
            )
            return _ok("handle-command", help=help_text, human_reply=help_text)
        if cmd == "status":
            # Pass through optional job filter args for cmd_status
            args.args = parsed.get("args") or []
            return cmd_status(args)
        if cmd == "approve":
            jid, _req = mb.resolve_request_job(parsed["request_id"])
            reply = mb.write_reply(jid, parsed["request_id"], decision="allow", principal=principal)
            return _ok(
                "handle-command",
                reply=reply,
                human_reply=f"Approved request `{parsed['request_id']}` on job `{jid}`.",
            )
        if cmd == "deny":
            jid, _req = mb.resolve_request_job(parsed["request_id"])
            reply = mb.write_reply(jid, parsed["request_id"], decision="deny", principal=principal)
            return _ok(
                "handle-command",
                reply=reply,
                human_reply=f"Denied request `{parsed['request_id']}` on job `{jid}`.",
            )
        if cmd == "say":
            jid, _req = mb.resolve_request_job(parsed["target"])
            reply = mb.write_reply(
                jid, parsed["target"], decision="say", principal=principal, text=parsed["text"]
            )
            return _ok("handle-command", reply=reply, human_reply="Recorded say-reply.")
        if cmd == "instruct":
            item = mb.enqueue_inbox(
                parsed["target"], kind="instruct", text=parsed["text"], source="command"
            )
            return _ok(
                "handle-command",
                item=item,
                human_reply=(
                    f"Instruction queued for job `{parsed['target']}` "
                    f"(applied on the **next** drive iteration):\n\n> {parsed['text']}"
                ),
            )
        if cmd == "instruct_freeform":
            jid = _need_job(None)
            if not jid:
                return _fail(
                    "handle-command",
                    "no_default_job",
                    "no focused job; use /aas focus <job> or /aas instruct <job> <text>",
                    human_reply=(
                        "No default job. Arm a job, then `/aas focus <job>`, "
                        "or use `/aas instruct <job> <text>`."
                    ),
                )
            item = mb.enqueue_inbox(jid, kind="instruct", text=parsed["text"], source="command")
            return _ok(
                "handle-command",
                item=item,
                job_id=jid,
                human_reply=(
                    f"Instruction queued for job `{jid}` "
                    f"(next drive iteration):\n\n> {parsed['text']}"
                ),
            )
        if cmd in {"stop", "pause", "resume"}:
            jid = _need_job(parsed.get("job_id"))
            if not jid:
                return _fail(
                    "handle-command",
                    "missing_job",
                    "provide job id or focus a job",
                    human_reply="Missing job id. Example: `/aas pause example-job`",
                )
            job = mb.read_json(mb.job_dir(jid) / "job.json") or {}
            loop = job.get("loop_dir")
            if not loop:
                return _fail(
                    "handle-command",
                    "no_loop",
                    "job has no loop_dir",
                    human_reply=f"Job `{jid}` has no linked loop directory.",
                )
            name = {"stop": "STOP_REQUESTED", "pause": "PAUSE", "resume": None}[cmd]
            loop_path = Path(loop)
            if cmd == "resume":
                (loop_path / "PAUSE").unlink(missing_ok=True)
            else:
                (loop_path / name).write_text("", encoding="utf-8")
            return _ok(
                "handle-command",
                job_id=jid,
                action=cmd,
                human_reply=f"**{cmd}** applied to job `{jid}` (loop `{loop}`).",
            )
        if cmd == "focus":
            jid = parsed.get("job_id") or (parsed.get("args") or [None])[0]
            if not jid:
                return _fail(
                    "handle-command",
                    "missing_job",
                    "usage: /aas focus <job>",
                    human_reply="Usage: `/aas focus example-job`",
                )
            focus_path = mb.bridge_dir / "focus.json"
            mb.write_json(
                focus_path,
                {
                    "job_id": jid,
                    "principal": principal,
                    "created_at": utc_now(),
                },
            )
            return _ok(
                "handle-command",
                focus=jid,
                human_reply=f"Focus set to job `{jid}`. Freeform `/aas <text>` now targets it.",
            )
        if cmd == "doctor":
            return cmd_doctor(args)
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "handle-command",
            "error",
            str(exc),
            human_reply=f"remote-bridge error: {exc}",
        )
    return _fail(
        "handle-command",
        "unknown_cmd",
        f"unknown command {cmd}",
        human_reply=f"Unknown `/aas` command `{cmd}`. Try `/aas help`.",
    )


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = build_config(args.secrets_file)
    mb = Mailbox()
    mb.ensure()
    lease = mb.read_json(mb.bridge_dir / "lease.json")
    info = {
        "state_root": str(mb.root),
        "secrets_path": cfg.secrets_path,
        "default_channel": cfg.default_channel,
        "notify_channels": cfg.notify_channels,
        "jobs": len(mb.list_jobs()),
        "pending_requests": len(mb.pending_requests()),
        "lease": lease,
        "zulip_configured": bool(cfg.zulip.get("site") and cfg.zulip.get("api_key")),
        "telegram_configured": bool(cfg.telegram.get("bot_token")),
        "telegram_mode": cfg.telegram.get("mode") or "outbound_only",
    }
    # optional live checks only if --live
    if getattr(args, "live", False) and cfg.telegram.get("bot_token"):
        try:
            info["telegram_webhook"] = telegram_webhook_info(cfg)
        except Exception as exc:  # noqa: BLE001
            info["telegram_webhook_error"] = safe_redaction_error(exc, cfg)
    return _ok("doctor", doctor=info)


def cmd_format_inbox(args: argparse.Namespace) -> int:
    mb = Mailbox()
    job_id = args.job or os.environ.get("AAS_REMOTE_JOB_ID")
    if not job_id:
        return _fail("format-inbox", "missing_job", "provide --job or AAS_REMOTE_JOB_ID")
    if getattr(args, "peek", False):
        return _ok("format-inbox", block=mb.peek_inbox_block(job_id), item_ids=[], peek=True)
    claimer = args.claimer or f"pid{os.getpid()}"
    block, ids, fences = mb.format_inbox_block(job_id, claimer=claimer)
    if args.consume and ids:
        mb.consume_claimed(job_id, ids, claimer=claimer, fences=fences)
    return _ok("format-inbox", block=block, item_ids=ids, fences=fences)


def cmd_check_approval(args: argparse.Namespace) -> int:
    mb = Mailbox()
    reply = mb.check_approval(args.job, args.digest)
    if reply:
        return _ok("check-approval", allowed=True, reply=reply)
    return _ok("check-approval", allowed=False)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="AAS remote-bridge (Zulip + Telegram)")
    p.add_argument("--secrets-file", default=None)
    sub = p.add_subparsers(dest="command", required=True)

    st = sub.add_parser("selftest")
    st.add_argument("--work-dir", default=None)
    st.set_defaults(func=cmd_selftest)

    sc = sub.add_parser("show-config")
    sc.set_defaults(func=cmd_show_config)

    arm = sub.add_parser("arm")
    arm.add_argument("--job", required=True)
    arm.add_argument("--provider", required=True, choices=SUPPORTED_PROVIDERS)
    arm.add_argument("--cwd", default=None)
    arm.add_argument("--loop", default=None)
    arm.add_argument("--force", action="store_true")
    arm.set_defaults(func=cmd_arm)

    stt = sub.add_parser("status")
    stt.set_defaults(func=cmd_status)

    send = sub.add_parser("send")
    send_body = send.add_mutually_exclusive_group()
    send_body.add_argument("--text", default=None)
    send_body.add_argument(
        "--event-json",
        default=None,
        metavar="PATH|-",
        help=(
            "aas.autoloop.notify.v2 or legacy flat event JSON; '-' reads stdin and "
            "selects Markdown/Telegram HTML renderings automatically"
        ),
    )
    send.add_argument(
        "--html",
        default=None,
        help="optional HTML body for Telegram (parse_mode=HTML); Zulip still uses --text Markdown",
    )
    send.add_argument("--job", default=None)
    send.add_argument(
        "--channel",
        choices=["zulip", "telegram", "both", "auto"],
        default=None,
        help="zulip (default primary; Telegram only if Zulip fails), telegram, both/auto (same primary/fallback policy)",
    )
    send.add_argument("--dry-run", action="store_true")
    send.set_defaults(func=cmd_send)

    ra = sub.add_parser("request-approval")
    ra.add_argument("--job", default=None)
    ra.add_argument("--provider", default=None)
    ra.add_argument("--tool", default=None)
    ra.add_argument("--args-json", default=None)
    ra.add_argument("--summary", default=None)
    ra.add_argument("--wait", action="store_true")
    ra.add_argument("--timeout", type=int, default=300)
    ra.add_argument("--poll", type=float, default=1.0)
    ra.add_argument("--no-notify", action="store_true")
    ra.add_argument("--dry-run", action="store_true")
    ra.add_argument("--truncated", action="store_true")
    ra.set_defaults(func=cmd_request_approval)

    ins = sub.add_parser("instruct")
    ins.add_argument("--job", required=True)
    ins.add_argument("--text", required=True)
    ins.set_defaults(func=cmd_instruct)

    hc = sub.add_parser("handle-command")
    hc_text = hc.add_mutually_exclusive_group(required=True)
    hc_text.add_argument("--text")
    hc_text.add_argument(
        "--text-stdin",
        action="store_true",
        help="read one bounded UTF-8 control message from stdin",
    )
    hc.add_argument("--principal", default="")
    hc.add_argument("--bot-username", default=None)
    hc.add_argument(
        "--allow-local-cli",
        action="store_true",
        help="allow principal=cli for local operator (never used by serve)",
    )
    hc.set_defaults(func=cmd_handle_command)

    doc = sub.add_parser("doctor")
    doc.add_argument("--live", action="store_true")
    doc.set_defaults(func=cmd_doctor)

    fi = sub.add_parser("format-inbox")
    fi.add_argument("--job", default=None)
    fi.add_argument("--claimer", default=None)
    fi.add_argument("--consume", action="store_true")
    fi.add_argument("--peek", action="store_true", help="read-only preview; no claim")
    fi.set_defaults(func=cmd_format_inbox)

    ca = sub.add_parser("check-approval")
    ca.add_argument("--job", required=True)
    ca.add_argument("--digest", required=True)
    ca.set_defaults(func=cmd_check_approval)

    return p


def _maybe_sync_openclaw_workspace_paths() -> None:
    """Deprecated inert shim; CLI operations never sync legacy OpenClaw paths."""

    return None


def main(argv: list[str] | None = None) -> int:
    # Force UTF-8 stdio: chat API responses embed non-ASCII (e.g. a recipient's
    # name), and json.dumps(ensure_ascii=False) would otherwise crash emitting
    # them on a legacy Windows cp1252 console.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        rc = int(args.func(args))
    except Exception:  # noqa: BLE001 - last-chance output fails closed
        return _fail(
            getattr(args, "command", "remote-bridge"),
            "error",
            "remote-bridge command failed safely",
        )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
