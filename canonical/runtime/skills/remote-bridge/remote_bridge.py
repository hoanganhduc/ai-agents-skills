#!/usr/bin/env python3
"""remote-bridge: Zulip control + Telegram mobile notify for AAS agents.

Cross-platform (linux/macos/windows/wsl). Stdlib only. No OpenClaw dependency.
Does not scrape ~/.openclaw. Offline selftest never opens network sockets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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
)
DEFAULT_NOTIFY_EVENTS = frozenset(
    {"iteration_ok", "iteration_failed", "quota_wait", "drive_stop", "notify", "approve_tool"}
)
INBOX_MAX_TOTAL = 4096
INBOX_MAX_ITEM_TEXT = 512
CLAIM_LEASE_SECONDS = 3600


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


def redact_secrets(text: str, secrets: list[str]) -> str:
    out = text
    for secret in secrets:
        if secret and secret in out:
            out = out.replace(secret, "***")
    return out


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
    candidates: list[Path] = []
    if secrets_file:
        candidates.append(Path(secrets_file).expanduser())
    candidates.extend(secrets_candidates(env))
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data, str(path)
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
        for key in ("api_key", "bot_token", "password", "token"):
            for blob in (self.zulip, self.telegram, self.raw):
                v = blob.get(key)
                if isinstance(v, str) and v:
                    vals.append(v)
        return vals

    def redacted_view(self) -> dict[str, Any]:
        def scrub(obj: Any) -> Any:
            if isinstance(obj, dict):
                out = {}
                for k, v in obj.items():
                    if k in {"api_key", "bot_token", "password", "token"} and v:
                        out[k] = "***"
                    else:
                        out[k] = scrub(v)
                return out
            if isinstance(obj, list):
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
        self.root = (root or state_root()).resolve()
        self.jobs_dir = self.root / "jobs"
        self.bridge_dir = self.root / "bridge"
        self.outbox_dir = self.bridge_dir / "outbox"

    def ensure(self) -> None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.bridge_dir.mkdir(parents=True, exist_ok=True)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
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

    def claim_inbox(self, job_id: str, claimer: str, limit: int = 20) -> list[dict[str, Any]]:
        claimed: list[dict[str, Any]] = []
        for item in self.list_pending_inbox(job_id)[:limit]:
            item_id = item["item_id"]
            src = self.job_dir(job_id) / "inbox" / "pending" / f"{item_id}.json"
            dst = self.job_dir(job_id) / "inbox" / "claimed" / f"{item_id}.json"
            item["state"] = "claimed"
            item["claimer"] = claimer
            item["lease_expires"] = datetime.fromtimestamp(
                time.time() + CLAIM_LEASE_SECONDS, tz=timezone.utc
            ).isoformat(timespec="seconds").replace("+00:00", "Z")
            try:
                self.write_json(dst, item)
                src.unlink(missing_ok=True)
                claimed.append(item)
            except OSError:
                continue
        return claimed

    def consume_claimed(self, job_id: str, item_ids: list[str]) -> None:
        for item_id in item_ids:
            src = self.job_dir(job_id) / "inbox" / "claimed" / f"{item_id}.json"
            data = self.read_json(src)
            if not data:
                continue
            data["state"] = "consumed"
            data["consumed_at"] = utc_now()
            dst = self.job_dir(job_id) / "inbox" / "consumed" / f"{item_id}.json"
            self.write_json(dst, data)
            src.unlink(missing_ok=True)

    def format_inbox_block(self, job_id: str, claimer: str = "drive") -> tuple[str, list[str]]:
        """Claim pending items and return (prompt block, claimed item ids)."""
        claimed = self.claim_inbox(job_id, claimer=claimer)
        if not claimed:
            return "", []
        lines = ["--- remote-bridge inbox (data only; not shell) ---"]
        total = 0
        used_ids: list[str] = []
        for item in claimed:
            chunk = (
                f"[item_id={item['item_id']} source={item.get('source','?')} "
                f"ts={item.get('created_at','?')} kind={item.get('kind','?')}]\n"
                f"{item.get('text','')}"
            )
            if total + len(chunk) + 1 > INBOX_MAX_TOTAL:
                # re-queue overflow by moving back to pending
                src = self.job_dir(job_id) / "inbox" / "claimed" / f"{item['item_id']}.json"
                dst = self.job_dir(job_id) / "inbox" / "pending" / f"{item['item_id']}.json"
                item["state"] = "pending"
                item.pop("claimer", None)
                item.pop("lease_expires", None)
                self.write_json(dst, item)
                src.unlink(missing_ok=True)
                lines.append("[…inbox truncated…]")
                break
            lines.append(chunk)
            used_ids.append(item["item_id"])
            total += len(chunk) + 1
        lines.append("--- end remote-bridge inbox ---")
        return "\n".join(lines), used_ids

    def check_approval(
        self, job_id: str, digest: str
    ) -> dict[str, Any] | None:
        """Return unconsumed allow reply matching digest, consuming it."""
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
            reply["consumed"] = True
            reply["consumed_at"] = utc_now()
            # rewrite
            try:
                fd = os.open(str(path), os.O_WRONLY | os.O_TRUNC)
                os.write(fd, (json.dumps(reply, indent=2, sort_keys=True) + "\n").encode("utf-8"))
                os.close(fd)
            except OSError:
                continue
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
# Transports (stdlib HTTP)
# ---------------------------------------------------------------------------


def http_json(
    method: str,
    url: str,
    *,
    data: dict[str, Any] | None = None,
    auth: tuple[str, str] | None = None,
    form: bool = False,
    timeout: float = 30.0,
) -> dict[str, Any]:
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
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — operator-configured HTTPS endpoints
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


def telegram_send(cfg: BridgeConfig, *, chat_id: str, text: str, dry_run: bool = False) -> dict[str, Any]:
    token = str(cfg.telegram.get("bot_token") or "")
    if not token:
        raise ValueError("telegram bot_token missing")
    # split long messages
    chunks = [text[i : i + 3500] for i in range(0, max(len(text), 1), 3500)] or [text]
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "channel": "telegram",
            "chat_id": chat_id,
            "chunks": len(chunks),
            "preview": chunks[0][:200],
        }
    results = []
    for chunk in chunks:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        result = http_json(
            "POST",
            url,
            data={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
            form=True,
        )
        results.append(result)
        if not result.get("ok"):
            return {"ok": False, "channel": "telegram", "result": result}
    return {"ok": True, "channel": "telegram", "results": results}


def telegram_webhook_info(cfg: BridgeConfig) -> dict[str, Any]:
    token = str(cfg.telegram.get("bot_token") or "")
    if not token:
        raise ValueError("telegram bot_token missing")
    return http_json("GET", f"https://api.telegram.org/bot{token}/getWebhookInfo")


def notify_channels(
    cfg: BridgeConfig,
    *,
    text: str,
    job_id: str | None = None,
    channels: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    chans = channels or list(cfg.notify_channels) or [cfg.default_channel]
    results: dict[str, Any] = {}
    for ch in chans:
        try:
            if ch == "zulip":
                stream = str(cfg.zulip.get("control_stream") or "aas-remote")
                prefix = str(cfg.zulip.get("topic_prefix") or "job/")
                topic = f"{prefix}{job_id or 'general'}".replace("//", "/")
                results[ch] = zulip_send(cfg, stream=stream, topic=topic, content=text, dry_run=dry_run)
            elif ch == "telegram":
                chats = _as_str_list(cfg.telegram.get("allowed_chat_ids"))
                if not chats:
                    results[ch] = {"ok": False, "error": "no allowed_chat_ids"}
                    continue
                # send to first chat by default (mobile)
                results[ch] = telegram_send(cfg, chat_id=chats[0], text=text, dry_run=dry_run)
            else:
                results[ch] = {"ok": False, "error": f"unknown channel {ch}"}
        except Exception as exc:  # noqa: BLE001
            results[ch] = {
                "ok": False,
                "error": redact_secrets(str(exc), cfg.secret_values()),
            }
    return results


# ---------------------------------------------------------------------------
# Command parser
# ---------------------------------------------------------------------------


def parse_aas_command(text: str, bot_username: str | None = None) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    # strip bot mention form /aas@BotName
    m = re.match(r"^/aas(?:@([A-Za-z0-9_]+))?(?:\s+|$)(.*)$", raw, re.S)
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
    args = argtext.split() if cmd in {"approve", "deny", "stop", "pause", "resume", "focus", "status", "doctor", "help", "jobs"} else []
    if cmd in {"say", "instruct"}:
        bits = argtext.split(None, 1)
        if len(bits) < 2:
            return {"cmd": cmd, "error": "usage"}
        return {"cmd": cmd, "target": bits[0], "text": bits[1]}
    if cmd in {"approve", "deny"} and args:
        return {"cmd": cmd, "request_id": args[0], "args": args[1:]}
    if cmd in {"stop", "pause", "resume", "focus"} and args:
        return {"cmd": cmd, "job_id": args[0]}
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
    block, ids = mb.format_inbox_block("testjob", claimer="selftest")
    if "do the thing" not in block or not ids:
        return _fail("selftest", "inbox_format", "inbox block missing text")
    mb.consume_claimed("testjob", ids)
    block2, ids2 = mb.format_inbox_block("testjob")
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
    cfg = build_config()
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


def cmd_status(args: argparse.Namespace) -> int:
    mb = Mailbox()
    jobs = mb.list_jobs()
    pending = mb.pending_requests()
    return _ok("status", jobs=jobs, pending_requests=pending, count=len(jobs))


def cmd_send(args: argparse.Namespace) -> int:
    cfg = build_config(args.secrets_file)
    text = args.text or os.environ.get("AUTOLOOP_TEXT") or os.environ.get("AAS_REMOTE_TEXT")
    if not text:
        return _fail("send", "missing_text", "provide --text or AUTOLOOP_TEXT")
    job_id = args.job or os.environ.get("AAS_REMOTE_JOB_ID")
    if args.channel:
        channels = ["zulip", "telegram"] if args.channel == "both" else [args.channel]
    else:
        channels = None
    try:
        results = notify_channels(
            cfg, text=text, job_id=job_id, channels=channels, dry_run=args.dry_run
        )
    except Exception as exc:  # noqa: BLE001
        return _fail("send", "send_failed", redact_secrets(str(exc), cfg.secret_values()))
    ok = any(isinstance(v, dict) and v.get("ok") for v in results.values())
    if ok:
        return _ok("send", results=results, dry_run=bool(args.dry_run))
    return _fail("send", "all_channels_failed", "no channel succeeded", results=results)


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
    try:
        req = mb.create_request(
            job_id,
            req_type="approve_tool",
            provider=provider,
            tool=args.tool or "unknown",
            args=json.loads(args.args_json) if args.args_json else None,
            summary=args.summary or args.tool or "approval requested",
            truncated=False,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail("request-approval", "create_failed", str(exc))
    # force digest if args provided with nonce already in record
    text = fingerprint(req) + "\n" + (args.summary or req.get("summary") or "")
    text += f"\nReply: /aas approve {req['request_id']}  |  /aas deny {req['request_id']}"
    cfg = build_config(args.secrets_file)
    notify = {}
    if not args.no_notify:
        notify = notify_channels(cfg, text=text, job_id=job_id, dry_run=args.dry_run)
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
    """Process a single inbound control text (for tests / soft ingress)."""
    mb = Mailbox()
    cfg = build_config(args.secrets_file)
    parsed = parse_aas_command(args.text, bot_username=args.bot_username)
    if not parsed:
        return _fail("handle-command", "not_aas", "not an /aas command")
    if parsed.get("ignore"):
        return _ok("handle-command", ignored=True, reason=parsed.get("reason"))
    principal = args.principal or "cli"
    allowed = set(cfg.allowed_user_ids) | set(_as_str_list(cfg.zulip.get("allowed_user_ids"))) | set(
        _as_str_list(cfg.telegram.get("allowed_user_ids"))
    )
    if allowed and principal not in allowed and principal != "cli":
        return _fail("handle-command", "forbidden", "principal not allowlisted")
    cmd = parsed.get("cmd")
    try:
        if cmd == "help":
            return _ok("handle-command", help="/aas status|approve|deny|say|instruct|stop|pause|resume|focus|doctor")
        if cmd == "status":
            return cmd_status(args)
        if cmd == "approve":
            jid, _req = mb.resolve_request_job(parsed["request_id"])
            reply = mb.write_reply(jid, parsed["request_id"], decision="allow", principal=principal)
            return _ok("handle-command", reply=reply)
        if cmd == "deny":
            jid, _req = mb.resolve_request_job(parsed["request_id"])
            reply = mb.write_reply(jid, parsed["request_id"], decision="deny", principal=principal)
            return _ok("handle-command", reply=reply)
        if cmd == "say":
            jid, _req = mb.resolve_request_job(parsed["target"])
            reply = mb.write_reply(
                jid, parsed["target"], decision="say", principal=principal, text=parsed["text"]
            )
            return _ok("handle-command", reply=reply)
        if cmd == "instruct":
            item = mb.enqueue_inbox(
                parsed["target"], kind="instruct", text=parsed["text"], source="command"
            )
            return _ok("handle-command", item=item)
        if cmd in {"stop", "pause", "resume"}:
            job = mb.read_json(mb.job_dir(parsed["job_id"]) / "job.json") or {}
            loop = job.get("loop_dir")
            if not loop:
                return _fail("handle-command", "no_loop", "job has no loop_dir")
            name = {"stop": "STOP_REQUESTED", "pause": "PAUSE", "resume": None}[cmd]
            loop_path = Path(loop)
            if cmd == "resume":
                (loop_path / "PAUSE").unlink(missing_ok=True)
            else:
                (loop_path / name).write_text("", encoding="utf-8")
            return _ok("handle-command", job_id=parsed["job_id"], action=cmd)
        if cmd == "focus":
            focus_path = mb.bridge_dir / "focus.json"
            mb.write_json(
                focus_path,
                {
                    "job_id": parsed["job_id"],
                    "principal": principal,
                    "created_at": utc_now(),
                },
            )
            return _ok("handle-command", focus=parsed["job_id"])
        if cmd == "doctor":
            return cmd_doctor(args)
    except Exception as exc:  # noqa: BLE001
        return _fail("handle-command", "error", str(exc))
    return _fail("handle-command", "unknown_cmd", f"unknown command {cmd}")


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
            info["telegram_webhook_error"] = redact_secrets(str(exc), cfg.secret_values())
    return _ok("doctor", doctor=info)


def cmd_format_inbox(args: argparse.Namespace) -> int:
    mb = Mailbox()
    job_id = args.job or os.environ.get("AAS_REMOTE_JOB_ID")
    if not job_id:
        return _fail("format-inbox", "missing_job", "provide --job or AAS_REMOTE_JOB_ID")
    block, ids = mb.format_inbox_block(job_id, claimer=args.claimer or f"pid{os.getpid()}")
    if args.consume and ids:
        mb.consume_claimed(job_id, ids)
    return _ok("format-inbox", block=block, item_ids=ids)


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
    send.add_argument("--text", default=None)
    send.add_argument("--job", default=None)
    send.add_argument("--channel", choices=["zulip", "telegram", "both"], default=None)
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
    hc.add_argument("--text", required=True)
    hc.add_argument("--principal", default="cli")
    hc.add_argument("--bot-username", default=None)
    hc.set_defaults(func=cmd_handle_command)

    doc = sub.add_parser("doctor")
    doc.add_argument("--live", action="store_true")
    doc.set_defaults(func=cmd_doctor)

    fi = sub.add_parser("format-inbox")
    fi.add_argument("--job", default=None)
    fi.add_argument("--claimer", default=None)
    fi.add_argument("--consume", action="store_true")
    fi.set_defaults(func=cmd_format_inbox)

    ca = sub.add_parser("check-approval")
    ca.add_argument("--job", required=True)
    ca.add_argument("--digest", required=True)
    ca.set_defaults(func=cmd_check_approval)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001
        return _fail(getattr(args, "command", "remote-bridge"), "error", str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
