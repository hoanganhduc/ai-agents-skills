"""Hetzner Cloud lifecycle driver for the research-compute Hetzner lane.

This is the hcloud lifecycle CLI referenced by the hetzner-research-compute skill. It
rents a disposable server, runs a portable job bundle on it, fetches the results, and
DESTROYS the server. Planning verbs (bootstrap, doctor, preflight) are free and never
touch a server; lifecycle verbs (up, push, run, status, wait, fetch, down, oneshot) may
hold a paid server and require HCLOUD_TOKEN plus an explicit confirm.

Guardrails (treat the agent itself as the adversary):
  * HCLOUD_TOKEN is read from the environment and injected into the hcloud subprocess env,
    NEVER on argv (/proc/<pid>/cmdline is world-readable), NEVER logged, NEVER on a server,
    NEVER written to an `hcloud context` file. A redaction filter covers surfaced output.
  * Every server carries managed-by / install-scope / job-id / owner / ttl labels so only
    the creating runtime can find and tear it down.
  * A fail-closed budget gate reserves the pessimistic worst case before any create
    (reuses research_compute.hetzner_backend.budget_gate).
  * `oneshot` guarantees teardown on every exit path (a finally block plus signal handlers,
    the equivalent of `trap 'down' EXIT INT TERM HUP`).

Offline safety: every external command goes through the module-level COMMAND_RUNNER hook,
which tests replace so no server is ever provisioned. `--dry-run` on up / down / oneshot
prints the exact planned hcloud command with no reservation and no provisioning.

Phase C guardrails now built (plan section 6): every supported `up` auto-attaches the managed
cloud-init dead-man's-switch (Arm 1); custom user-data is rejected because it would bypass that
control. The driver enforces a reconcile-before-create runaway-loop guard; `down --all` /
`down --orphans` and the standalone
hetzner_reaper (Arms 2 and 3) delete servers; provision and destroy events are written to the
append-only redacted audit log; an audit failure is reported without skipping reconciliation
or later deletions.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import re
import shlex
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import hetzner_audit
from research_compute import budget_ledger, hetzner_backend
from research_compute.config import default_config_path, load_config, workspace_root

MANAGED_BY = "ai-agents-skills"
INSTALL_SCOPE_LABEL = "install-scope"
REMOTE_DIR = "/root/job-bundle"
# Server addresses are recycled, so every job provisions its own Ed25519 host key and pins that
# exact key in a private, per-job known_hosts file. Trust-on-first-use is never used.
SSH_NULL_CONFIG = "NUL" if os.name == "nt" else "/dev/null"
SSH_BASE_OPTS = [
    "-F", SSH_NULL_CONFIG,
    "-o", "StrictHostKeyChecking=yes",
    "-o", "GlobalKnownHostsFile=" + SSH_NULL_CONFIG,
    "-o", "UpdateHostKeys=no",
    "-o", "CheckHostIP=yes",
    "-o", "BatchMode=yes",
    "-o", "IdentitiesOnly=yes",
    "-o", "ConnectTimeout=30",
]
# How long push waits for cloud-init to finish starting sshd (module-level so tests can shorten it).
SSH_READY_TIMEOUT = 240.0
SSH_READY_INTERVAL = 5.0
TOOL_PIN_ENV = {
    "hcloud": "AAS_HETZNER_HCLOUD_BIN",
    "ssh": "AAS_HETZNER_SSH_BIN",
    "scp": "AAS_HETZNER_SCP_BIN",
    "rsync": "AAS_HETZNER_RSYNC_BIN",
    "ssh-keygen": "AAS_HETZNER_SSH_KEYGEN_BIN",
}
SUBPROCESS_ENV_KEYS = frozenset(
    {
        "HOME",
        "LANG",
        "LOGNAME",
        "SSH_AUTH_SOCK",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "TZ",
        "USER",
        "USERPROFILE",
        "WINDIR",
    }
)
BUNDLE_COMPONENT_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}\Z")
BUNDLE_MAX_FILES = 10_000
BUNDLE_MAX_FILE_BYTES = 256 * 1024 * 1024
BUNDLE_MAX_TOTAL_BYTES = 1024 * 1024 * 1024
BUNDLE_MAX_DEPTH = 16
BUNDLE_SECRET_POINTER_ENVS = (
    "AAS_COMPUTE_SECRETS_FILE",
    "AAS_PROVIDER_SECRETS_FILE",
    "AAS_SKILL_SECRETS_FILE",
    "AAS_SECRETS_FILE",
    "OPENCLAW_SECRETS_FILE",
)
PROTECTED_SECRET_IDENTITIES_ENV = "AAS_PROTECTED_SECRET_FILE_IDS"
REAPER_LEASE_MAX_AGE_SECONDS = 15 * 60
BUNDLE_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
BUNDLE_DIGEST_LABEL_HIGH = "bundle-sha256-high"
BUNDLE_DIGEST_LABEL_LOW = "bundle-sha256-low"
SSH_KEY_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@:+-]{0,127}\Z")


class HetznerDriverError(RuntimeError):
    pass


@dataclass(frozen=True)
class BundleSnapshot:
    path: Path
    manifest: dict[str, Any]
    digest: str


@dataclass(frozen=True)
class HostIdentity:
    private_key: str
    public_key: str


# --- token + redaction (env-only, never argv, never logged) -------------------

def _token() -> str | None:
    return os.environ.get("HCLOUD_TOKEN") or None


def token_present() -> bool:
    return bool(_token())


def _owner() -> str:
    """Best-effort owner label from the environment; never a hard-coded identity."""
    return os.environ.get("HETZNER_OWNER") or os.environ.get("AAS_OWNER") or MANAGED_BY


def _redact(text: str | None, token: str | None = None) -> str:
    token = token or _token()
    text = text or ""
    if token:
        text = text.replace(token, "<REDACTED_HCLOUD_TOKEN>")
    return text


def _project_identity(config: Any) -> str:
    value = str(getattr(config, "hetzner_project_identity", None) or "")
    if (
        not value
        or value != value.strip()
        or len(value) > 128
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise HetznerDriverError(
            "[hetzner].project_identity must be a non-empty exact project identifier"
        )
    return value


def _reaper_config_digest(config: Any) -> str:
    payload = {
        "install_scope": install_scope(config),
        "project_identity": _project_identity(config),
        "scheduler_id": str(getattr(config, "hetzner_reaper_scheduler_id", None) or ""),
        "max_server_hours": float(getattr(config, "hetzner_max_server_hours", 0.0)),
        "max_concurrent_servers": int(
            getattr(config, "hetzner_max_concurrent_servers", 0) or 0
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _parse_lease_time(value: Any, *, field: str) -> float:
    if not isinstance(value, str) or not value:
        raise HetznerDriverError(f"durable reaper lease {field} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HetznerDriverError(f"durable reaper lease {field} is invalid") from exc
    if parsed.tzinfo is None:
        raise HetznerDriverError(f"durable reaper lease {field} must include a timezone")
    return parsed.timestamp()


def _require_root_protected_parent_chain(path: Path, *, label: str) -> None:
    """Require every directory from ``path`` through ``/`` to be root-controlled.

    Checking only the immediate parent is insufficient: an agent that can rename that
    parent through a writable ancestor can replace an otherwise root-owned lease tree.
    """
    if os.name != "posix" or not path.is_absolute():
        raise HetznerDriverError(f"{label} parent chain requires an absolute POSIX path")
    current = path
    while True:
        try:
            info = current.stat(follow_symlinks=False)
        except OSError as exc:
            raise HetznerDriverError(f"{label} parent chain cannot be inspected") from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or int(info.st_uid) != 0
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise HetznerDriverError(
                f"{label} parent chain must contain only root-owned, "
                "non-group/world-writable directories"
            )
        if current.parent == current:
            return
        current = current.parent


def _verify_durable_reaper_lease(config: Any) -> dict[str, Any]:
    """Verify short-lived, scheduler-bound evidence that the agent cannot self-assert."""
    configured = str(getattr(config, "hetzner_reaper_lease_file", None) or "")
    scheduler_id = str(getattr(config, "hetzner_reaper_scheduler_id", None) or "")
    if not configured or not Path(configured).is_absolute() or not scheduler_id:
        raise HetznerDriverError(
            "live provisioning requires absolute reaper_lease_file and reaper_scheduler_id"
        )
    lease_path = Path(configured)
    _reject_symlink_components(lease_path, label="durable reaper lease")
    if os.name != "posix":
        raise HetznerDriverError("durable reaper lease verification requires POSIX")
    if os.geteuid() == 0:
        raise HetznerDriverError(
            "live provisioning as root is disabled because reaper evidence must be outside "
            "the agent authority"
        )
    _require_root_protected_parent_chain(
        lease_path.parent, label="durable reaper lease"
    )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(lease_path, flags)
    except OSError as exc:
        raise HetznerDriverError("durable reaper lease cannot be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        if (
            int(before.st_uid) != 0
            or stat.S_IMODE(before.st_mode) & 0o022
            or not stat.S_ISREG(before.st_mode)
            or int(before.st_nlink) != 1
            or int(before.st_size) > 64 * 1024
        ):
            raise HetznerDriverError(
                "durable reaper lease must be root-owned, regular, single-link, "
                "bounded, and not group/world writable"
            )
        chunks: list[bytes] = []
        remaining = int(before.st_size)
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise HetznerDriverError("durable reaper lease cannot be read safely") from exc
    finally:
        os.close(descriptor)
    if (
        len(raw) != int(before.st_size)
        or _stable_stat(before) != _stable_stat(after)
    ):
        raise HetznerDriverError("durable reaper lease changed while being read")
    try:
        lease = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HetznerDriverError("durable reaper lease is not valid UTF-8 JSON") from exc
    if not isinstance(lease, dict) or lease.get("version") != 1:
        raise HetznerDriverError("durable reaper lease version is unsupported")
    scheduler = lease.get("scheduler")
    if (
        not isinstance(scheduler, dict)
        or scheduler.get("kind") not in {"systemd", "cron"}
        or scheduler.get("id") != scheduler_id
        or scheduler.get("active") is not True
    ):
        raise HetznerDriverError("durable reaper lease is not bound to the active scheduler")
    if lease.get("project_identity") != _project_identity(config):
        raise HetznerDriverError("durable reaper lease project identity mismatch")
    if lease.get("install_scope") != install_scope(config):
        raise HetznerDriverError("durable reaper lease install scope mismatch")
    if lease.get("config_digest") != _reaper_config_digest(config):
        raise HetznerDriverError("durable reaper lease config digest mismatch")
    now = time.time()
    issued_at = _parse_lease_time(lease.get("issued_at"), field="issued_at")
    expires_at = _parse_lease_time(lease.get("expires_at"), field="expires_at")
    max_age = min(
        REAPER_LEASE_MAX_AGE_SECONDS,
        int(getattr(config, "hetzner_reaper_lease_max_age_seconds", 900) or 0),
    )
    if max_age <= 0 or issued_at > now + 30 or now - issued_at > max_age or expires_at <= now:
        raise HetznerDriverError("durable reaper lease is stale or expired")
    if expires_at - issued_at > max_age + 30 or now - float(before.st_mtime) > max_age:
        raise HetznerDriverError("durable reaper lease freshness exceeds the configured bound")
    return {
        "project_identity": lease["project_identity"],
        "install_scope": lease["install_scope"],
        "scheduler": {"kind": scheduler["kind"], "id": scheduler["id"]},
        "issued_at": lease["issued_at"],
        "expires_at": lease["expires_at"],
        "config_digest": lease["config_digest"],
    }


REAPER_LEASE_VERIFIER: Callable[[Any], dict[str, Any]] = _verify_durable_reaper_lease


def _require_durable_reaper_for_live_provisioning(config: Any) -> dict[str, Any]:
    """Keep paid creates closed until protected detached-reaper evidence is current."""
    if os.name == "nt":
        raise HetznerDriverError(
            "live Hetzner provisioning is disabled on native Windows because this release "
            "does not install or attest a durable Task Scheduler reaper; use WSL/Linux for "
            "up/oneshot. Planning and recovery/teardown verbs remain available on Windows."
        )
    return REAPER_LEASE_VERIFIER(config)


def runtime_workspace() -> Path:
    """Resolve the broker data workspace (config, state, and scope identity).

    The managed runner exports ``AAS_RUNTIME_WORKSPACE``, but an immutable
    exact-pin generation exports its own read-only tree there, which carries no
    broker configuration, cannot hold broker state, and moves on every
    republish (which would rotate the derived install scope).  Trust the
    runner's selection only when it names a real broker data workspace;
    otherwise fall back to ``workspace_root()``, whose environment the
    entrypoint has already normalized the same way for every lane.
    """
    selected = os.environ.get("AAS_RUNTIME_WORKSPACE")
    if selected:
        path = Path(selected).expanduser()
        if not path.is_absolute():
            raise HetznerDriverError("AAS_RUNTIME_WORKSPACE must name an absolute path")
        if default_config_path(path).is_file():
            return path.resolve()
    return workspace_root()


# --- command runner (single mockable hook for hcloud + ssh/scp/rsync) ---------

def _trusted_tool_path(tool: str, *, required: bool = True) -> str | None:
    env_name = TOOL_PIN_ENV.get(tool)
    if env_name is None:
        raise HetznerDriverError(f"unsupported external command {tool!r}")
    value = str(os.environ.get(env_name) or "")
    if not value:
        if required:
            raise HetznerDriverError(
                f"{env_name} is not pinned; launch through the managed Hetzner wrapper"
            )
        return None
    if value != value.strip() or not Path(value).is_absolute():
        raise HetznerDriverError(f"{env_name} must name an unpadded absolute path")
    try:
        resolved = Path(value).resolve(strict=True)
    except OSError as exc:
        raise HetznerDriverError(f"{env_name} does not name an existing executable") from exc
    if not resolved.is_file() or (os.name != "nt" and not os.access(resolved, os.X_OK)):
        raise HetznerDriverError(f"{env_name} does not name an executable file")
    return str(resolved)


def _safe_subprocess_path() -> str:
    if os.name == "nt":
        system_root = str(os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or "")
        if not system_root:
            return ""
        return os.pathsep.join(
            [
                str(Path(system_root) / "System32" / "OpenSSH"),
                str(Path(system_root) / "System32"),
                system_root,
            ]
        )
    return "/usr/bin:/bin"


def _minimal_subprocess_env(tool: str) -> dict[str, str]:
    env = {
        key: str(value)
        for key, value in os.environ.items()
        if value
        and (
            key in SUBPROCESS_ENV_KEYS
            or key.startswith("LC_")
        )
    }
    env["PATH"] = _safe_subprocess_path()
    if tool == "hcloud":
        token = _token()
        if token:
            env["HCLOUD_TOKEN"] = token
    return env


def _pinned_argv(argv: list[str]) -> list[str]:
    if not argv:
        raise HetznerDriverError("external command is empty")
    tool = str(argv[0])
    pinned = _trusted_tool_path(tool)
    command = [pinned, *[str(part) for part in argv[1:]]]
    if tool == "rsync" and "-e" in command:
        option_index = command.index("-e")
        if option_index + 1 >= len(command):
            raise HetznerDriverError("rsync -e is missing its SSH transport")
        transport = shlex.split(command[option_index + 1])
        if not transport or Path(transport[0]).name not in {"ssh", "ssh.exe"}:
            raise HetznerDriverError("rsync transport must be the pinned SSH client")
        command[option_index + 1] = shlex.join([_trusted_tool_path("ssh"), *transport[1:]])
    if tool == "scp":
        command[1:1] = ["-S", _trusted_tool_path("ssh")]
    return command


def _default_command_runner(argv: list[str], *, env: dict[str, str], timeout: float) -> dict[str, Any]:  # pragma: no cover - real subprocess path is never exercised offline
    import subprocess

    proc = subprocess.run(
        _pinned_argv(argv),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


# Tests replace this to guarantee no external command ever runs offline.
COMMAND_RUNNER: Callable[..., dict[str, Any]] = _default_command_runner


def _run(argv: list[str], *, timeout: float = 120.0, needs_token: bool = False) -> dict[str, Any]:
    """Run a pinned external command with a minimal tool-specific environment."""
    if needs_token and not token_present():
        raise HetznerDriverError("HCLOUD_TOKEN is not set; refusing to run a Hetzner command")
    if not argv or argv[0] not in TOOL_PIN_ENV:
        raise HetznerDriverError("refusing unsupported external command")
    env = _minimal_subprocess_env(str(argv[0]))
    token = _token()
    result = COMMAND_RUNNER(list(argv), env=env, timeout=timeout)
    result["stdout"] = _redact(result.get("stdout", ""), token)
    result["stderr"] = _redact(result.get("stderr", ""), token)
    if int(result.get("returncode", 1)) != 0:
        raise HetznerDriverError(
            f"command failed ({' '.join(argv)}): {result['stderr'].strip() or result['stdout'].strip()}"
        )
    return result


def run_hcloud(args: list[str], **kwargs: Any) -> dict[str, Any]:
    return _run(["hcloud", *args], needs_token=True, **kwargs)


# --- labels + selectors -------------------------------------------------------

def install_scope(config: Any) -> str:
    """Stable non-sensitive label derived from install id plus installed workspace identity."""
    install_id = getattr(config, "install_id", None)
    if not isinstance(install_id, str) or not install_id or install_id != install_id.strip():
        raise HetznerDriverError(
            "research-compute config must contain a non-empty, unpadded install_id"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in install_id):
        raise HetznerDriverError("research-compute install_id contains a control character")
    # Bootstrap historically used the host name as install_id. Two runtimes on one host can
    # therefore share that value, so bind it to the resolved runtime workspace as well. The
    # label exposes only the digest, never either source value.
    runtime_identity = os.path.normcase(str(runtime_workspace()))
    digest = hashlib.sha256(
        f"{install_id}\0{runtime_identity}".encode("utf-8")
    ).hexdigest()[:32]
    return f"v1-{digest}"


def managed_selector(config: Any, *, job_id: str | None = None) -> str:
    parts = [f"managed-by={MANAGED_BY}", f"{INSTALL_SCOPE_LABEL}={install_scope(config)}"]
    if job_id is not None:
        parts.append(f"job-id={job_id}")
    return ",".join(parts)


def managed_account_selector() -> str:
    """Project-wide selector for every server created by ai-agents-skills.

    Install-scoped selectors remain the identity boundary for normal job operations.  Billing
    safety operations deliberately use this broader stable label because an install directory
    move or reinstall changes the derived install scope and must not make a paid server
    invisible to the runaway guard, detached reaper, or emergency kill switch.
    """
    return f"managed-by={MANAGED_BY}"


def server_is_managed(server: dict[str, Any]) -> bool:
    if not isinstance(server, dict):
        return False
    labels = server.get("labels") or {}
    return isinstance(labels, dict) and labels.get("managed-by") == MANAGED_BY


def project_scope_label(config: Any) -> str:
    return "v1-" + hashlib.sha256(_project_identity(config).encode("utf-8")).hexdigest()[:32]


def server_in_project_scope(server: dict[str, Any], config: Any) -> bool:
    labels = server.get("labels") if isinstance(server, dict) else None
    return (
        isinstance(labels, dict)
        and labels.get("managed-by") == MANAGED_BY
        and labels.get("project-scope") == project_scope_label(config)
    )


def parse_server_records(payload: str | None, *, context: str) -> list[dict[str, Any]]:
    """Parse hcloud server inventory without turning malformed evidence into an empty set."""
    try:
        records = json.loads(payload or "")
    except json.JSONDecodeError as exc:
        raise HetznerDriverError(f"could not parse {context}: {exc}") from exc
    if not isinstance(records, list):
        raise HetznerDriverError(f"{context} must be a JSON list")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise HetznerDriverError(
                f"{context} record {index} must be a JSON object"
            )
    return records


def server_in_install_scope(
    server: dict[str, Any], config: Any, *, job_id: str | None = None
) -> bool:
    if not isinstance(server, dict):
        return False
    labels = server.get("labels") or {}
    if not isinstance(labels, dict):
        return False
    return (
        labels.get("managed-by") == MANAGED_BY
        and labels.get(INSTALL_SCOPE_LABEL) == install_scope(config)
        and (job_id is None or labels.get("job-id") == job_id)
    )


def server_labels(job_id: str, ttl_hours: float, config: Any,
                  *, bundle_digest: str | None = None) -> dict[str, str]:
    labels = {
        "managed-by": MANAGED_BY,
        "project-scope": project_scope_label(config),
        INSTALL_SCOPE_LABEL: install_scope(config),
        "job-id": job_id,
        "owner": _owner(),
        "ttl": f"{int(max(1, round(ttl_hours)))}h",
    }
    if bundle_digest:
        digest = _canonical_bundle_digest(bundle_digest, field="bundle digest")
        labels[BUNDLE_DIGEST_LABEL_HIGH] = digest[:32]
        labels[BUNDLE_DIGEST_LABEL_LOW] = digest[32:]
    return labels


def _canonical_bundle_digest(value: str | None, *, field: str) -> str:
    digest = str(value or "")
    if not BUNDLE_DIGEST_RE.fullmatch(digest):
        raise HetznerDriverError(f"{field} must be an exact lowercase SHA-256 digest")
    return digest


def _require_bundle_approval(
    bundle: BundleSnapshot,
    approved_bundle_sha256: str | None,
    *,
    operation: str,
) -> str:
    approved = _canonical_bundle_digest(
        approved_bundle_sha256,
        field=f"{operation} --bundle-sha256 approval",
    )
    if not hmac.compare_digest(approved, bundle.digest):
        raise HetznerDriverError(
            f"{operation} bundle differs from the exact preflight-approved SHA-256"
        )
    return approved


def _server_bundle_digest(server: dict[str, Any]) -> str | None:
    labels = server.get("labels") if isinstance(server, dict) else None
    if not isinstance(labels, dict):
        return None
    high = labels.get(BUNDLE_DIGEST_LABEL_HIGH)
    low = labels.get(BUNDLE_DIGEST_LABEL_LOW)
    if not isinstance(high, str) or not isinstance(low, str):
        return None
    digest = high + low
    return digest if BUNDLE_DIGEST_RE.fullmatch(digest) else None


def _require_server_bundle_digest(server: dict[str, Any], expected: str) -> None:
    actual = _server_bundle_digest(server)
    if actual is None or not hmac.compare_digest(actual, expected):
        raise HetznerDriverError(
            "managed server labels do not bind the exact approved bundle SHA-256"
        )


def _label_args(labels: dict[str, str]) -> list[str]:
    args: list[str] = []
    for key, value in labels.items():
        args += ["--label", f"{key}={value}"]
    return args


def _server_name(job_id: str) -> str:
    return f"{MANAGED_BY}-{job_id}"


# Hetzner requires a server name to be a valid hostname (RFC 1123 label rules), so an
# underscore -- ordinary in a job id -- is rejected at create time.
_SERVER_NAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\Z")


def _check_server_name(job_id: str) -> None:
    """Refuse a job id that cannot name a server, before anything is spent on it.

    Hetzner rejects the create with `invalid input in field 'name'`, which costs nothing
    by itself -- but the budget gate has already reserved the worst case by then, and a
    reservation is released only when a server is destroyed. No server, no release: the
    reservation holds part of the daily cap for good."""
    name = _server_name(job_id)
    if len(name) > 63 or not _SERVER_NAME_RE.match(name):
        raise HetznerDriverError(
            f"job_id {job_id!r} cannot name a Hetzner server ({name!r}): the name must be a "
            f"valid hostname of at most 63 characters -- letters, digits, dots and hyphens "
            f"only, starting and ending alphanumeric. Use hyphens instead of underscores.")


def _new_job_id() -> str:
    # Hyphens, not underscores: this id names a server (see _check_server_name).
    return f"hz-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"


# --- manifest + estimate ------------------------------------------------------

def _read_manifest(job_dir: str | Path) -> dict[str, Any]:
    path = Path(job_dir).expanduser() / "manifest.json"
    if not path.is_file():
        raise HetznerDriverError(f"job bundle manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _stable_stat(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
    )


def _reject_symlink_components(path: Path, *, label: str) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise HetznerDriverError(f"{label} cannot be inspected: {current}") from exc
        is_reparse = bool(
            int(getattr(info, "st_file_attributes", 0))
            & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        )
        if stat.S_ISLNK(info.st_mode) or is_reparse:
            raise HetznerDriverError(f"{label} contains a link/reparse component: {current}")


def _require_posix_owned_unwritable(path: Path, *, label: str) -> None:
    """Reject source authority that another unprivileged account can replace."""
    if os.name != "posix":  # Native Windows upload is disabled below.
        return
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise HetznerDriverError(f"{label} cannot be inspected: {path}") from exc
    allowed_owners = {0, os.geteuid()}
    if int(info.st_uid) not in allowed_owners:
        raise HetznerDriverError(f"{label} is not owned by root or the current operator: {path}")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise HetznerDriverError(f"{label} is group/world writable: {path}")


def _require_bundle_authority_chain(approved: Path, source: Path) -> None:
    """Attest every operator-controlled directory from bundle_root through the job."""
    current = approved
    _require_posix_owned_unwritable(current, label="Hetzner bundle authority")
    for component in source.relative_to(approved).parts:
        current /= component
        _require_posix_owned_unwritable(current, label="Hetzner bundle authority")


def _approved_bundle_source(job_dir: str | Path, config: Any) -> Path:
    configured_root = str(getattr(config, "hetzner_bundle_root", None) or "")
    if not configured_root or not Path(configured_root).is_absolute():
        raise HetznerDriverError(
            "Hetzner bundle upload requires an absolute operator-approved "
            "[hetzner].bundle_root"
        )
    approved = Path(os.path.abspath(Path(configured_root).expanduser()))
    supplied = Path(job_dir).expanduser()
    if not supplied.is_absolute():
        supplied = Path.cwd() / supplied
    source = Path(os.path.abspath(supplied))
    _reject_symlink_components(approved, label="Hetzner bundle root")
    _reject_symlink_components(source, label="Hetzner job bundle")
    if not approved.is_dir() or not source.is_dir():
        raise HetznerDriverError("Hetzner bundle root and job bundle must be directories")
    approved = approved.resolve(strict=True)
    source = source.resolve(strict=True)
    try:
        source.relative_to(approved)
    except ValueError as exc:
        raise HetznerDriverError(
            f"Hetzner job bundle is outside approved bundle_root: {source}"
        ) from exc
    if source == approved:
        raise HetznerDriverError("Hetzner job bundle must be a child of bundle_root")
    _require_bundle_authority_chain(approved, source)

    for pointer_name in BUNDLE_SECRET_POINTER_ENVS:
        pointer_value = str(os.environ.get(pointer_name) or "")
        if not pointer_value:
            continue
        pointer = Path(pointer_value).expanduser()
        if not pointer.is_absolute():
            raise HetznerDriverError(f"{pointer_name} must be absolute")
        try:
            pointer = pointer.resolve(strict=True)
        except OSError as exc:
            raise HetznerDriverError(f"{pointer_name} cannot be inspected") from exc
        if pointer == source or source in pointer.parents or pointer in source.parents:
            raise HetznerDriverError(
                f"Hetzner bundle overlaps protected authority {pointer_name}"
            )
    return source


def _copy_bundle_snapshot(source: Path, target: Path) -> None:
    counters = {"files": 0, "bytes": 0}
    protected_identities = {
        value
        for value in str(os.environ.get(PROTECTED_SECRET_IDENTITIES_ENV) or "").split(",")
        if re.fullmatch(r"[0-9]+:[0-9]+", value)
    }
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    source_descriptor = os.open(source, directory_flags)
    target.mkdir(mode=0o700)

    def copy_directory(source_fd: int, destination: Path, depth: int) -> None:
        if depth > BUNDLE_MAX_DEPTH:
            raise HetznerDriverError("Hetzner bundle exceeds the maximum directory depth")
        directory_before = os.fstat(source_fd)
        if os.name == "posix":
            if int(directory_before.st_uid) not in {0, os.geteuid()}:
                raise HetznerDriverError("Hetzner bundle directory has an untrusted owner")
            if stat.S_IMODE(directory_before.st_mode) & 0o022:
                raise HetznerDriverError("Hetzner bundle directory is group/world writable")
        with os.scandir(source_fd) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                name = entry.name
                if not BUNDLE_COMPONENT_RE.fullmatch(name):
                    raise HetznerDriverError(
                        f"Hetzner bundle contains an unsafe path component: {name!r}"
                    )
                if name.lower().endswith(".env") or name.lower() in {
                    "credentials",
                    "credentials.json",
                    "secrets.json",
                }:
                    raise HetznerDriverError(
                        f"Hetzner bundle contains a forbidden authority-like file: {name}"
                    )
                entry_info = entry.stat(follow_symlinks=False)
                destination_entry = destination / name
                if stat.S_ISDIR(entry_info.st_mode):
                    if os.name == "posix":
                        if int(entry_info.st_uid) not in {0, os.geteuid()}:
                            raise HetznerDriverError(
                                f"Hetzner bundle entry has an untrusted owner: {name}"
                            )
                        if stat.S_IMODE(entry_info.st_mode) & 0o022:
                            raise HetznerDriverError(
                                f"Hetzner bundle entry is group/world writable: {name}"
                            )
                    child_fd = os.open(name, directory_flags, dir_fd=source_fd)
                    try:
                        opened = os.fstat(child_fd)
                        if (entry_info.st_dev, entry_info.st_ino) != (
                            opened.st_dev,
                            opened.st_ino,
                        ):
                            raise HetznerDriverError(
                                f"Hetzner bundle directory changed while opening: {name}"
                            )
                        destination_entry.mkdir(mode=0o700)
                        copy_directory(child_fd, destination_entry, depth + 1)
                    finally:
                        os.close(child_fd)
                    continue
                if not stat.S_ISREG(entry_info.st_mode):
                    raise HetznerDriverError(
                        f"Hetzner bundle contains a link or special file: {name}"
                    )
                if os.name == "posix":
                    if int(entry_info.st_uid) not in {0, os.geteuid()}:
                        raise HetznerDriverError(
                            f"Hetzner bundle entry has an untrusted owner: {name}"
                        )
                    if stat.S_IMODE(entry_info.st_mode) & 0o022:
                        raise HetznerDriverError(
                            f"Hetzner bundle entry is group/world writable: {name}"
                        )
                if int(entry_info.st_nlink) != 1:
                    raise HetznerDriverError(
                        f"Hetzner bundle contains a hard-linked file: {name}"
                    )
                if f"{int(entry_info.st_dev)}:{int(entry_info.st_ino)}" in protected_identities:
                    raise HetznerDriverError(
                        f"Hetzner bundle overlaps a protected secret authority: {name}"
                    )
                if int(entry_info.st_size) > BUNDLE_MAX_FILE_BYTES:
                    raise HetznerDriverError(f"Hetzner bundle file is too large: {name}")
                counters["files"] += 1
                counters["bytes"] += int(entry_info.st_size)
                if counters["files"] > BUNDLE_MAX_FILES:
                    raise HetznerDriverError("Hetzner bundle contains too many files")
                if counters["bytes"] > BUNDLE_MAX_TOTAL_BYTES:
                    raise HetznerDriverError("Hetzner bundle exceeds the total size limit")

                file_flags = (
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_BINARY", 0)
                )
                file_fd = os.open(name, file_flags, dir_fd=source_fd)
                try:
                    before = os.fstat(file_fd)
                    if (
                        not stat.S_ISREG(before.st_mode)
                        or int(before.st_nlink) != 1
                        or (entry_info.st_dev, entry_info.st_ino)
                        != (before.st_dev, before.st_ino)
                    ):
                        raise HetznerDriverError(
                            f"Hetzner bundle file changed while opening: {name}"
                        )
                    chunks: list[bytes] = []
                    remaining = int(before.st_size)
                    while remaining:
                        chunk = os.read(file_fd, min(65_536, remaining))
                        if not chunk:
                            raise HetznerDriverError(
                                f"Hetzner bundle file was truncated while reading: {name}"
                            )
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    if os.read(file_fd, 1):
                        raise HetznerDriverError(
                            f"Hetzner bundle file grew while reading: {name}"
                        )
                    after = os.fstat(file_fd)
                    if _stable_stat(before) != _stable_stat(after):
                        raise HetznerDriverError(
                            f"Hetzner bundle file changed while reading: {name}"
                        )
                finally:
                    os.close(file_fd)
                mode = 0o700 if stat.S_IMODE(entry_info.st_mode) & 0o111 else 0o600
                target_fd = os.open(
                    destination_entry,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    mode,
                )
                try:
                    payload = b"".join(chunks)
                    offset = 0
                    while offset < len(payload):
                        offset += os.write(target_fd, payload[offset:])
                    os.fsync(target_fd)
                finally:
                    os.close(target_fd)
        directory_after = os.fstat(source_fd)
        if _stable_stat(directory_before) != _stable_stat(directory_after):
            raise HetznerDriverError("Hetzner bundle directory changed while snapshotting")

    try:
        copy_directory(source_descriptor, target, 0)
    finally:
        os.close(source_descriptor)


def _bundle_digest(snapshot: Path) -> str:
    """Digest the exact staged inode tree, including relative paths and execution modes."""
    digest = hashlib.sha256()
    for path in sorted(snapshot.rglob("*"), key=lambda item: item.relative_to(snapshot).as_posix()):
        relative = path.relative_to(snapshot).as_posix()
        info = path.stat(follow_symlinks=False)
        kind = b"d" if stat.S_ISDIR(info.st_mode) else b"f"
        digest.update(kind + b"\0" + relative.encode("utf-8") + b"\0")
        digest.update(f"{stat.S_IMODE(info.st_mode):04o}\0{int(info.st_size)}\0".encode("ascii"))
        if kind == b"f":
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65_536), b""):
                    digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


@contextmanager
def _validated_bundle_snapshot(
    job_dir: str | Path,
    config: Any,
    *,
    expected_job_id: str | None = None,
) -> Iterator[BundleSnapshot]:
    if os.name != "posix":  # pragma: no cover - native Windows
        raise HetznerDriverError(
            "Hetzner bundle upload is disabled on native Windows in this release; "
            "use preflight for planning and WSL/Linux for up, push, or oneshot"
        )
    source = _approved_bundle_source(job_dir, config)
    with tempfile.TemporaryDirectory(prefix="aas-hetzner-bundle-") as temporary:
        snapshot = Path(temporary) / "bundle"
        _copy_bundle_snapshot(source, snapshot)
        manifest_path = snapshot / "manifest.json"
        run_path = snapshot / "run.sh"
        if not manifest_path.is_file() or not run_path.is_file():
            raise HetznerDriverError(
                "Hetzner bundle requires regular manifest.json and run.sh files"
            )
        if not os.access(run_path, os.X_OK):
            raise HetznerDriverError("Hetzner bundle run.sh must be executable")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise HetznerDriverError("Hetzner bundle manifest is invalid UTF-8 JSON") from exc
        if not isinstance(manifest, dict):
            raise HetznerDriverError("Hetzner bundle manifest must be a JSON object")
        manifest_job_id = str(manifest.get("job_id") or "")
        if expected_job_id is not None and manifest_job_id != str(expected_job_id):
            raise HetznerDriverError(
                "Hetzner bundle manifest job_id does not match the requested job"
            )
        yield BundleSnapshot(snapshot, manifest, _bundle_digest(snapshot))


# --- root SSH access ----------------------------------------------------------

def _configured_ssh_key_names() -> list[str]:
    """Return the explicit credential-authority key allowlist, never a project-wide fallback."""
    raw = str(os.environ.get("HCLOUD_SSH_KEYS") or "")
    if not raw:
        raise HetznerDriverError(
            "refusing to provision without explicit HCLOUD_SSH_KEYS allowlist"
        )
    names = [part.strip() for part in raw.split(",")]
    if any(not name or not SSH_KEY_NAME_RE.fullmatch(name) for name in names):
        raise HetznerDriverError(
            "HCLOUD_SSH_KEYS must be a comma-separated list of exact safe key names"
        )
    if len(names) != len(set(names)):
        raise HetznerDriverError("HCLOUD_SSH_KEYS contains a duplicate key name")
    return names


def list_ssh_key_names() -> list[str]:
    """Validate the explicit SSH-key allowlist against the current Hetzner project.

    The stock Ubuntu image has no password login.  Every live create therefore requires
    exact names selected by ``HCLOUD_SSH_KEYS`` and proves those names exist in the current
    project immediately before the budget reservation.  The project inventory is never
    used as an implicit attach-all authority.
    """
    selected = _configured_ssh_key_names()
    try:
        result = run_hcloud(["ssh-key", "list", "-o", "noheader", "-o", "columns=name"], timeout=30.0)
    except HetznerDriverError as exc:
        raise HetznerDriverError(f"could not list the project SSH keys: {exc}") from exc
    project_names = [
        line.strip()
        for line in (result.get("stdout") or "").splitlines()
        if line.strip()
    ]
    if len(project_names) != len(set(project_names)):
        raise HetznerDriverError("Hetzner project SSH-key names are ambiguous")
    missing = [name for name in selected if name not in set(project_names)]
    if missing:
        raise HetznerDriverError(
            "HCLOUD_SSH_KEYS names are absent from the current Hetzner project: "
            + ", ".join(missing)
        )
    return selected


def estimate_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Backend-agnostic estimate read from the portable job bundle manifest."""
    core_hours = manifest.get("core_hours") or manifest.get("est_core_hours") or 0.0
    parallelism = manifest.get("parallelism") or manifest.get("cores") or 1
    peak_ram_gb = manifest.get("peak_ram_gb")
    if peak_ram_gb in (None, 0, 0.0):
        peak_ram_gb = float(int(manifest.get("memory_mb", 0) or 0)) / 1024.0
    return {
        "core_hours": float(core_hours),
        "parallelism": max(1, int(parallelism)),
        "peak_ram_gb": float(peak_ram_gb or 0.0),
        "gpu": bool(manifest.get("gpu")),
        "arch": (str(manifest.get("arch") or "").lower() or None),
    }


def _check_allowlists(server_spec: dict[str, Any], location: str | None, config: Any) -> None:
    allowed_locations = list(getattr(config, "hetzner_allowed_locations", []) or [])
    if allowed_locations and location and location not in allowed_locations:
        raise HetznerDriverError(f"location '{location}' is not in allowed_locations {allowed_locations}")
    allowed_types = set(hetzner_backend.server_catalog(config))
    if server_spec.get("name") not in allowed_types:
        raise HetznerDriverError(
            f"server type '{server_spec.get('name')}' is not in the configured allow-list {sorted(allowed_types)}"
        )


# --- live datacenter availability + orderable placement (plan section 12) ------
#
# The durable fix for a stock-out: Hetzner ARM has been unorderable everywhere and individual
# regions run dry, so a hard-coded (type, location) can fail to provision. preflight and up
# query the live datacenter list through the mockable COMMAND_RUNNER, build a
# {location: [orderable type names]} map, and pick the cheapest adequate type in the
# most-preferred orderable region, falling back across the allow-list on a stock-out. Offline
# tests inject the map directly or through a fake runner; the real hcloud calls are read-only
# (they create no server), and the real subprocess path is gated in _default_command_runner.

def parse_availability(server_types_json: str, datacenters_json: str) -> dict[str, list[str]]:
    """Build {location: [orderable server-type names]} from `hcloud server-type list` (to map
    numeric ids to names) and `hcloud datacenter list` (per-datacenter available ids, unioned
    across the datacenters in a location). Pure and offline-testable."""
    try:
        server_types = json.loads(server_types_json or "[]")
        datacenters = json.loads(datacenters_json or "[]")
    except json.JSONDecodeError as exc:
        raise HetznerDriverError(f"could not parse hcloud availability output: {exc}") from exc
    id_to_name: dict[Any, str] = {}
    for entry in server_types if isinstance(server_types, list) else []:
        if isinstance(entry, dict) and entry.get("id") is not None and entry.get("name"):
            id_to_name[entry["id"]] = str(entry["name"])
    availability: dict[str, list[str]] = {}
    for datacenter in datacenters if isinstance(datacenters, list) else []:
        if not isinstance(datacenter, dict):
            continue
        location = ((datacenter.get("location") or {}).get("name")) or datacenter.get("name")
        available_ids = ((datacenter.get("server_types") or {}).get("available")) or []
        names = availability.setdefault(str(location), [])
        for sid in available_ids:
            name = id_to_name.get(sid)
            if name and name not in names:
                names.append(name)
    return availability


def fetch_availability(config: Any) -> dict[str, list[str]]:
    """Query the live Hetzner datacenter list and return a {location: [orderable type names]}
    map. Two read-only hcloud calls through the mockable COMMAND_RUNNER; free and provisions
    nothing. Requires HCLOUD_TOKEN (every hcloud API call is authenticated)."""
    server_types = run_hcloud(["server-type", "list", "-o", "json"])
    datacenters = run_hcloud(["datacenter", "list", "-o", "json"])
    return parse_availability(server_types["stdout"], datacenters["stdout"])


def resolve_placement(*, estimate: dict[str, Any], config: Any,
                      availability: dict[str, list[str]] | None = None,
                      location: str | None = None) -> tuple[dict[str, Any] | None, str | None, str]:
    """Resolve the (server_spec, location) to provision, availability-checked against the live
    datacenter list. An explicit `location` pins the region (the operator's choice wins) and
    only the cheapest adequate type is chosen; otherwise the cheapest adequate orderable
    (type, location) is picked from the allow-list, falling back on a stock-out. `availability`
    may be injected (offline tests); when omitted and a token is present it is fetched live. On
    no token or a fetch failure it degrades to the cheapest adequate type in the pinned/default
    location (no availability data). Returns (spec | None, location | None, reason)."""
    if location is not None:
        spec, reason = hetzner_backend.select_server_spec(estimate, config)
        return (spec, location, "operator_pinned_location") if spec is not None else (None, None, reason)
    if availability is None and token_present():
        try:
            availability = fetch_availability(config)
        except HetznerDriverError:
            availability = None
    if availability is not None:
        return hetzner_backend.select_orderable_placement(estimate, config=config, availability=availability)
    # No availability data (no token / fetch failed): cheapest adequate type, default region.
    spec, reason = hetzner_backend.select_server_spec(estimate, config)
    default_loc = getattr(config, "hetzner_location", None)
    if spec is None:
        return None, None, reason
    return spec, default_loc, "no_availability_data_using_default_location"


# --- cloud-init dead-man's-switch + reconcile guard + audit (plan section 6) ---

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def cloud_init_shutdown_minutes(ttl_hours: float) -> int:
    """Compute-cap for the dead-man's-switch: the server halts this many minutes after boot
    (boot-relative, so a wrong clock cannot defeat it). At least one minute."""
    return max(1, int(round(float(ttl_hours) * 60.0)))


def _generate_host_identity() -> HostIdentity:
    """Generate one ephemeral server host identity through the pinned OpenSSH tool."""
    with tempfile.TemporaryDirectory(prefix="aas-hetzner-hostkey-") as temporary:
        os.chmod(temporary, 0o700)
        key_path = Path(temporary) / "ssh_host_ed25519_key"
        _run([
            "ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", str(key_path)
        ], timeout=30.0)
        try:
            private_key = key_path.read_text(encoding="ascii")
            public_key = key_path.with_suffix(".pub").read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as exc:
            raise HetznerDriverError("ssh-keygen did not produce a readable Ed25519 key pair") from exc
        fields = public_key.split()
        if (
            not private_key.startswith("-----BEGIN OPENSSH PRIVATE KEY-----\n")
            or len(fields) < 2
            or fields[0] != "ssh-ed25519"
            or not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", fields[1])
        ):
            raise HetznerDriverError("ssh-keygen produced an invalid Ed25519 host identity")
        return HostIdentity(private_key=private_key, public_key=f"{fields[0]} {fields[1]}")


def render_cloud_init(config: Any, ttl_hours: float, *, host_identity: HostIdentity) -> str:
    """Render the cloud-init dead-man's-switch (plan section 6, Arm 1) from
    assets/cloud-init.yaml: a boot-relative `shutdown -h +MAX` plus a systemd RuntimeMaxSec
    backstop that powers the box off at the cap. NO token is placed on the server -- a server
    can only power itself OFF, never delete itself, so the detached reaper deletes the
    powered-off box (the billing stopper). `ttl_hours` is the configured max_server_hours."""
    minutes = cloud_init_shutdown_minutes(ttl_hours)
    seconds = minutes * 60
    template = (ASSETS_DIR / "cloud-init.yaml").read_text(encoding="utf-8")
    # cc_ssh's `ssh_keys` directive takes plain YAML text, not base64; the private key is
    # indented to sit inside the template's block scalar under the two-space mapping key.
    indented_private = "\n".join(
        f"    {line}" for line in host_identity.private_key.splitlines()
    )
    return (template
            .replace("{{MAX_SECONDS_PLUS}}", str(seconds + 120))
            .replace("{{MAX_SECONDS}}", str(seconds))
            .replace("{{MAX_MINUTES}}", str(minutes))
            .replace("{{SSH_HOST_PRIVATE_KEY_INDENTED}}", indented_private)
            .replace("{{SSH_HOST_PUBLIC_KEY}}", host_identity.public_key))


def _write_temp_cloud_init(rendered: str) -> str:
    """Write the rendered dead-man's-switch to a temp file for `--user-data-from-file` and
    return its path. It carries no secret, but `up` deletes it right after the create anyway."""
    import tempfile

    handle = tempfile.NamedTemporaryFile("w", suffix="-cloud-init.yaml", delete=False, encoding="utf-8")
    try:
        handle.write(rendered)
    finally:
        handle.close()
    return handle.name


def count_managed_servers(config: Any) -> int:
    """Number of live AAS-managed servers in the project (runaway-loop guard input).

    The project-wide count is intentionally independent of mutable local install paths.  It is
    conservative when multiple AAS installs share a Hetzner project, but it cannot false-green
    after a restore while an older-scope server is still billing.
    """
    # Validate the local identity even though the safety selector is project-wide.  A malformed
    # restoration config must still fail closed before any paid provision operation.
    install_scope(config)
    result = run_hcloud(
        ["server", "list", "--selector", managed_account_selector(), "-o", "json"]
    )
    servers = parse_server_records(
        result.get("stdout"),
        context="hcloud managed-server inventory",
    )
    return len([server for server in servers if server_is_managed(server)])


def reconcile_before_create(config: Any, *, adding: int = 1) -> dict[str, Any]:
    """Runaway-loop guard (plan section 6): before any create, count the LIVE tagged servers
    and abort if creating `adding` more would exceed max_concurrent_servers. It counts what
    Hetzner actually reports, so it stops a looping/crashing agent even when the local
    reservation ledger is stale."""
    max_concurrent = int(getattr(config, "hetzner_max_concurrent_servers", 0) or 0)
    existing = count_managed_servers(config)
    if max_concurrent and existing + max(1, int(adding)) > max_concurrent:
        raise HetznerDriverError(
            f"reconcile-before-create: {existing} live tagged server(s) + {adding} would exceed "
            f"max_concurrent_servers {max_concurrent} (runaway-loop guard)")
    return {"existing": existing, "adding": int(adding), "max_concurrent": max_concurrent}


def _audit(state_root: Path | None, event: dict[str, Any]) -> None:
    """Append a redacted provision/destroy audit record (plan section 6) to the append-only
    JSONL log. A no-op when no ledger root is available."""
    hetzner_audit.append(state_root, event, token=_token())


# --- planning verbs (free; no server) -----------------------------------------

def doctor(config: Any) -> dict[str, Any]:
    """Offline readiness snapshot. Reuses the backend doctor and adds a driver note."""
    out = dict(hetzner_backend.doctor(config))
    out["driver"] = "hetzner_driver"
    out["confirm_gate"] = "lifecycle verbs require HCLOUD_TOKEN and --confirm"
    return out


def bootstrap(config: Any | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "hcloud_cli_available": _trusted_tool_path("hcloud", required=False) is not None,
        "token_present": token_present(),
    }
    result["doctor"] = doctor(config) if config is not None else {"error": "config not found"}
    if not result["hcloud_cli_available"]:
        result["hint"] = "install the hcloud CLI: https://github.com/hetznercloud/cli"
    return result


def preflight(*, job_dir: str | Path, config: Any, state_root: Path | None = None,
              availability: dict[str, list[str]] | None = None,
              approved_bundle_sha256: str | None = None) -> dict[str, Any]:
    """The plan the router consumes: server type, region, est wall-h, est EUR, arch, and the
    budget verdict. Availability-checks the live datacenter list (plan section 12) and reports
    the cheapest adequate ORDERABLE (type, region), falling back across the allow-list on a
    stock-out. The only external calls are the read-only availability query; no reservation and
    no provisioning. Cost/worst-case are computed from the resolved (possibly fallen-back) spec
    so the plan reflects what would actually be ordered."""
    with _validated_bundle_snapshot(job_dir, config) as bundle:
        result = _preflight_snapshot(
            bundle=bundle,
            config=config,
            state_root=state_root,
            availability=availability,
        )
        if approved_bundle_sha256 is not None:
            _require_bundle_approval(
                bundle,
                approved_bundle_sha256,
                operation="preflight",
            )
            result["bundle_approval_verified"] = True
        result["required_bundle_sha256"] = bundle.digest
        return result


def _preflight_snapshot(*, bundle: BundleSnapshot, config: Any,
                        state_root: Path | None = None,
                        availability: dict[str, list[str]] | None = None) -> dict[str, Any]:
    manifest = bundle.manifest
    estimate = estimate_from_manifest(manifest)
    spec, region, place_reason = resolve_placement(estimate=estimate, config=config, availability=availability)
    probe = hetzner_backend.probe(estimate, config=config, resources=None, state_root=state_root)

    max_hours = float(getattr(config, "hetzner_max_server_hours", 0.0))
    per_job = float(getattr(config, "hetzner_max_eur_per_job", 0.0))
    core_hours = float(estimate.get("core_hours") or 0.0)
    vcpu = int(spec["vcpu"]) if spec else 0
    est_wall_h = hetzner_backend.estimate_wall_hours(core_hours, vcpu) if spec else 0.0
    est_cost = hetzner_backend.estimate_cost_eur(spec, est_wall_h) if spec else 0.0
    worst_case = hetzner_backend.worst_case_eur(spec, max_hours) if spec else 0.0
    within_auto_approve = bool(spec) and worst_case <= per_job

    try:
        _check_server_name(str(manifest.get("job_id") or _new_job_id()))
        nameable = True
    except HetznerDriverError as exc:
        nameable, name_reason = False, str(exc)

    if not nameable:
        verdict = "invalid_job_id"
    elif spec is None:
        verdict = "no_orderable_server"
    elif not probe["available"]:
        verdict = "blocked"
    elif within_auto_approve:
        verdict = "auto_approve"
    else:
        verdict = "needs_human_confirmation"

    return {
        "backend": "hetzner",
        "job_id": manifest.get("job_id"),
        "bundle_digest": bundle.digest,
        "server_type": spec["name"] if spec else None,
        "server_arch": spec["arch"] if spec else None,
        "region": region,
        "est_wall_h": round(est_wall_h, 3),
        "est_cost_eur": round(est_cost, 4),
        "worst_case_eur": round(worst_case, 4),
        "adequate": spec is not None,
        "available": bool(probe["available"] and spec is not None and nameable),
        "within_auto_approve": within_auto_approve,
        "budget_verdict": verdict,
        "reason": name_reason if not nameable else (place_reason if spec is None else probe["reason"]),
        "provisioned": False,
    }


# --- lifecycle verbs (may hold a paid server) ---------------------------------

def _release_reservation_if_no_server(state_root: Path, job_id: str, config: Any) -> bool:
    """Release this job's worst-case reservation when its create left no server behind.

    `hcloud` can also fail *after* the server exists (a timeout reading the response, say),
    so the release is conditional: a job that still has a server keeps its reservation,
    because that machine is billing. Anything unexpected -- an unreadable server list, a
    ledger write error -- also keeps it, since over-reserving only costs headroom while
    under-reserving lets the daily cap be overspent."""
    try:
        result = run_hcloud(
            ["server", "list", "--selector", managed_selector(config, job_id=job_id), "-o", "json"]
        )
        servers = parse_server_records(
            result.get("stdout"),
            context="hcloud reservation-reconcile inventory",
        )
        if any(server_in_install_scope(server, config, job_id=job_id) for server in servers):
            return False
        budget_ledger.reconcile(Path(state_root), "hetzner", job_id)
        return True
    except Exception:  # noqa: BLE001 - never mask the create failure being handled
        return False


def up(*, job_dir: str | Path, config: Any, state_root: Path, confirm: bool = False,
       dry_run: bool = False, image: str | None = None, location: str | None = None,
       user_data: str | None = None, approved_bundle_sha256: str | None = None,
       _bundle: BundleSnapshot | None = None) -> dict[str, Any]:
    """Create one labelled server, budget-gated. `--dry-run` prints the planned command with no
    reservation, no availability query, and no create (fully offline). A real create requires
    HCLOUD_TOKEN and --confirm; it availability-checks the live datacenter list (plan section 12)
    and provisions the cheapest adequate ORDERABLE (type, region), falling back across the
    allow-list on a stock-out. An operator --location pins the region. Custom user-data is
    intentionally unsupported because it would both upload an arbitrary host file and bypass
    the mandatory billing dead-man switch."""
    if user_data is not None:
        raise HetznerDriverError(
            "custom --user-data is disabled; every Hetzner create must use the managed "
            "dead-man-switch cloud-init"
        )
    if _bundle is None:
        with _validated_bundle_snapshot(job_dir, config) as bundle:
            return up(
                job_dir=job_dir, config=config, state_root=state_root, confirm=confirm,
                dry_run=dry_run, image=image, location=location, user_data=user_data,
                approved_bundle_sha256=approved_bundle_sha256,
                _bundle=bundle,
            )
    approved_digest = _require_bundle_approval(
        _bundle,
        approved_bundle_sha256,
        operation="up",
    )
    manifest = dict(_bundle.manifest)
    job_id = str(manifest.get("job_id") or _new_job_id())
    _check_server_name(job_id)  # before the gate: an unnameable job must reserve nothing
    estimate = estimate_from_manifest(manifest)
    spec, adequacy_reason = hetzner_backend.select_server_spec(estimate, config)
    if spec is None:
        raise HetznerDriverError(f"no adequate Hetzner server for this job: {adequacy_reason}")

    explicit_location = location
    image = image or getattr(config, "hetzner_image", None) or "ubuntu-24.04"
    ttl_hours = float(getattr(config, "hetzner_max_server_hours", 1.0))
    labels = server_labels(job_id, ttl_hours, config, bundle_digest=approved_digest)
    name = _server_name(job_id)

    def _create_args(chosen_spec: dict[str, Any], chosen_location: str | None,
                     ssh_keys: list[str] | None = None) -> list[str]:
        create = ["server", "create", "--name", name, "--type", chosen_spec["name"], "--image", image]
        if chosen_location:
            create += ["--location", chosen_location]
        for key_name in ssh_keys or []:
            create += ["--ssh-key", key_name]
        return create + _label_args(labels)

    # Dead-man's-switch (plan section 6, Arm 1): every server gets a boot-relative shutdown
    # cloud-init. Caller-selected user-data is rejected above. No token is sent to the server.
    arm_dead_mans_switch = True

    if dry_run:
        # Offline: cheapest adequate type + the pinned/default region (the live availability-check
        # runs only on a real, confirmed create).
        location = explicit_location or getattr(config, "hetzner_location", None)
        _check_allowlists(spec, location, config)
        ssh_keys = _configured_ssh_key_names()
        worst_case = hetzner_backend.worst_case_eur(spec, float(getattr(config, "hetzner_max_server_hours", 0.0)))
        udf_display = "<rendered dead-mans-switch cloud-init>"
        return {
            "dry_run": True, "provisioned": False, "job_id": job_id, "server_name": name,
            "server_type": spec["name"], "server_arch": spec["arch"], "location": location,
            "image": image, "labels": labels,
            "bundle_digest": _bundle.digest,
            "command": [
                "hcloud",
                *_create_args(spec, location, ssh_keys=ssh_keys),
                "--user-data-from-file",
                udf_display,
            ],
            "dead_mans_switch": arm_dead_mans_switch,
            "cloud_init_shutdown_minutes": cloud_init_shutdown_minutes(ttl_hours) if arm_dead_mans_switch else None,
            "worst_case_eur": round(worst_case, 4),
            "would_reserve": worst_case <= float(getattr(config, "hetzner_max_eur_per_job", 0.0)),
        }

    reaper_evidence = _require_durable_reaper_for_live_provisioning(config)

    if not token_present():
        raise HetznerDriverError("refusing to provision: HCLOUD_TOKEN is not set")
    if not confirm:
        raise HetznerDriverError("refusing to provision: explicit confirm is required (plan->submit confirm gate)")

    # Runaway-loop guard BEFORE any spend or availability query: live tagged servers vs the cap.
    reconcile = reconcile_before_create(config)

    # Live availability-check (plan section 12): pick an orderable (type, region), falling back
    # across the allow-list on a stock-out, so a stocked-out type/region degrades gracefully
    # instead of failing to provision. An operator --location pins the region.
    spec, location, place_reason = resolve_placement(
        estimate=estimate, config=config, location=explicit_location)
    if spec is None:
        raise HetznerDriverError(f"no orderable Hetzner server for this job: {place_reason}")
    _check_allowlists(spec, location, config)

    # Root SSH access, resolved BEFORE the reservation: a keyless server is unreachable, so
    # refusing here costs nothing, while refusing after the gate would strand a reservation.
    ssh_keys = list_ssh_key_names()
    if not ssh_keys:
        raise HetznerDriverError(
            "refusing to provision: the Hetzner project has no SSH key, so root login on the "
            "new server would be refused (add one with `hcloud ssh-key create`, or pin the "
            "names to use with HCLOUD_SSH_KEYS)")

    # Complete every local-only setup step before reserving budget. A local host-key or
    # cloud-init staging failure cannot create a server and therefore must not strand a
    # reservation in the daily ledger.
    host_identity = _generate_host_identity()
    temp_cloud_init = _write_temp_cloud_init(
        render_cloud_init(config, ttl_hours, host_identity=host_identity)
    )
    try:
        # Fail-closed budget gate + worst-case reservation BEFORE any create (with the
        # resolved spec), but only after the local staging above has succeeded.
        reservation = hetzner_backend.budget_gate(
            job_id=job_id, server_spec=spec, config=config, state_root=Path(state_root)
        )
        create_args = _create_args(spec, location, ssh_keys=ssh_keys)
        create_args += ["--user-data-from-file", temp_cloud_init, "-o", "json"]
        try:
            result = run_hcloud(create_args)
        except BaseException:
            # The reservation bought a server that may not exist. Releasing it is what keeps
            # a failed create from eroding the daily cap for good, since the only other
            # release path runs per destroyed server.
            _release_reservation_if_no_server(Path(state_root), job_id, config)
            raise
    finally:
        # Never leave the rendered cloud-init lying around (it carries no secret, but tidy).
        try:
            os.unlink(temp_cloud_init)
        except OSError:
            pass

    created: dict[str, Any] | None = None
    try:
        created = _created_server_record(
            result,
            config=config,
            job_id=job_id,
            expected_bundle_digest=approved_digest,
        )
        server_id = str(created.get("id") or created.get("name"))
        server_ip = _server_ip(
            job_id,
            config,
            expected_bundle_digest=approved_digest,
        )
        known_hosts = _write_known_hosts(
            config=config,
            state_root=Path(state_root),
            job_id=job_id,
            ip=server_ip,
            public_key=host_identity.public_key,
        )
    except BaseException:
        # A server without an exact local host pin cannot be contacted safely. If its identity is
        # known, delete that exact server immediately; otherwise retain the reservation so the
        # detached reaper sees the billing risk.
        if created is not None and (created.get("id") or created.get("name")):
            target = str(created.get("id") or created.get("name"))
            try:
                run_hcloud(["server", "delete", target])
                budget_ledger.reconcile(Path(state_root), "hetzner", job_id, None)
                _audit(Path(state_root), {
                    "event": hetzner_audit.EVENT_DESTROY,
                    "server": target,
                    "job_id": job_id,
                    "labels": created.get("labels"),
                    "reason": "post-create SSH host-pin failure",
                })
            except Exception:  # noqa: BLE001 - preserve the primary pin/identity failure
                pass
        raise

    _audit(Path(state_root), {
        "event": hetzner_audit.EVENT_PROVISION, "job_id": job_id, "server_name": name,
        "server_type": spec["name"], "server_arch": spec["arch"], "location": location,
        "labels": labels, "est_eur": reservation.get("worst_case"), "real_eur": None,
        "reason": "up", "dead_mans_switch": arm_dead_mans_switch,
        "server_id": server_id, "bundle_digest": _bundle.digest,
    })
    return {
        "provisioned": True, "job_id": job_id, "server_name": name, "server_type": spec["name"],
        "server_arch": spec["arch"], "location": location, "image": image, "labels": labels,
        "dead_mans_switch": arm_dead_mans_switch, "reconcile": reconcile,
        "reaper_evidence": reaper_evidence,
        "reservation": reservation, "hcloud_stdout": result["stdout"],
        "server_id": server_id, "server_ip": server_ip,
        "known_hosts": str(known_hosts), "bundle_digest": _bundle.digest,
    }


def _exact_server_id(server: dict[str, Any]) -> str:
    value = server.get("id") if isinstance(server, dict) else None
    if isinstance(value, bool):
        value = None
    text = str(value or "")
    if not text.isdigit() or int(text) <= 0:
        raise HetznerDriverError("managed server record lacks an exact numeric server ID")
    return text


def describe_server_exact(server_id: str, config: Any) -> dict[str, Any]:
    """Refetch an exact immutable server ID and re-establish the project trust boundary."""
    target = str(server_id)
    if not target.isdigit() or int(target) <= 0:
        raise HetznerDriverError("server refetch requires an exact numeric server ID")
    result = run_hcloud(["server", "describe", target, "-o", "json"])
    try:
        server = json.loads(result.get("stdout") or "")
    except json.JSONDecodeError as exc:
        raise HetznerDriverError("could not parse exact server description") from exc
    if not isinstance(server, dict) or _exact_server_id(server) != target:
        raise HetznerDriverError("exact server refetch returned a different identity")
    if not server_in_project_scope(server, config):
        raise HetznerDriverError(
            "exact server refetch is outside the configured managed project scope"
        )
    return server


def _job_server(
    job_id: str,
    config: Any,
    *,
    expected_bundle_digest: str | None = None,
) -> dict[str, Any]:
    """Resolve exactly one current-install server for a job and optionally bind its bundle."""
    result = run_hcloud(
        ["server", "list", "--selector", managed_selector(config, job_id=job_id), "-o", "json"]
    )
    servers = parse_server_records(
        result.get("stdout"),
        context="hcloud job server inventory",
    )
    matches = [
        server
        for server in servers
        if server_in_install_scope(server, config, job_id=job_id)
    ]
    if len(matches) != 1:
        raise HetznerDriverError(
            f"job-id={job_id} must resolve to exactly one managed server; found {len(matches)}"
        )
    server = matches[0]
    _exact_server_id(server)
    if expected_bundle_digest is not None:
        _require_server_bundle_digest(server, expected_bundle_digest)
    return server


def _server_ipv4(server: dict[str, Any]) -> str:
    ip = (((server or {}).get("public_net") or {}).get("ipv4") or {}).get("ip")
    try:
        return str(ipaddress.IPv4Address(str(ip)))
    except ipaddress.AddressValueError as exc:
        raise HetznerDriverError(
            "hcloud returned an invalid public IPv4 literal for the managed server"
        ) from exc


def _server_ip(
    job_id: str,
    config: Any,
    *,
    expected_bundle_digest: str | None = None,
) -> str:
    """Resolve the public IPv4 of exactly one labelled server."""
    return _server_ipv4(
        _job_server(
            job_id,
            config,
            expected_bundle_digest=expected_bundle_digest,
        )
    )


def _state_root_for(config: Any, state_root: Path | None) -> Path:
    return Path(state_root) if state_root is not None else Path(config.state_root(runtime_workspace()))


def _known_hosts_path(config: Any, job_id: str, state_root: Path | None = None) -> Path:
    return _state_root_for(config, state_root) / "hetzner-ssh" / f"{job_id}.known_hosts"


def _write_known_hosts(*, config: Any, state_root: Path, job_id: str,
                       ip: str, public_key: str) -> Path:
    address = str(ipaddress.IPv4Address(ip))
    fields = public_key.split()
    if len(fields) != 2 or fields[0] != "ssh-ed25519":
        raise HetznerDriverError("refusing to pin a non-Ed25519 or malformed host key")
    directory = _known_hosts_path(config, job_id, state_root).parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        os.chmod(directory, 0o700)
        _require_posix_owned_unwritable(directory, label="Hetzner SSH pin directory")
    target = directory / f"{job_id}.known_hosts"
    temp = directory / f".{job_id}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    # OpenSSH looks up the plain host form for default-port connections; the
    # bracketed ``[host]:port`` form matches only non-standard ports, so pinning
    # it would fail strict checking on every port-22 connection.
    payload = f"{address} {fields[0]} {fields[1]}\n".encode("ascii")
    fd = os.open(
        temp,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(temp, target)
        if os.name == "posix":
            os.chmod(target, 0o600)
            _require_posix_owned_unwritable(target, label="Hetzner SSH host pin")
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
    return target


def _ssh_options(*, config: Any, state_root: Path | None, job_id: str, ip: str) -> list[str]:
    path = _known_hosts_path(config, job_id, state_root)
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise HetznerDriverError(f"missing per-job SSH host pin for {job_id}") from exc
    if not stat.S_ISREG(info.st_mode) or int(info.st_nlink) != 1:
        raise HetznerDriverError(f"unsafe per-job SSH host pin for {job_id}")
    if os.name == "posix":
        _require_posix_owned_unwritable(path.parent, label="Hetzner SSH pin directory")
        _require_posix_owned_unwritable(path, label="Hetzner SSH host pin")
    expected_prefix = f"{str(ipaddress.IPv4Address(ip))} ssh-ed25519 "
    try:
        line = path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise HetznerDriverError(f"cannot read per-job SSH host pin for {job_id}") from exc
    if line.count("\n") != 1 or not line.startswith(expected_prefix):
        raise HetznerDriverError(f"per-job SSH host pin does not match {ip}")
    return [*SSH_BASE_OPTS, "-o", f"UserKnownHostsFile={path}"]


def _created_server_record(
    result: dict[str, Any],
    *,
    config: Any,
    job_id: str,
    expected_bundle_digest: str,
) -> dict[str, Any]:
    """Resolve one exact created server identity from create output or a scoped inventory."""
    candidate: Any = None
    try:
        payload = json.loads(str(result.get("stdout") or ""))
        candidate = payload.get("server") if isinstance(payload, dict) and "server" in payload else payload
    except json.JSONDecodeError:
        candidate = None
    if (
        isinstance(candidate, dict)
        and (candidate.get("id") or candidate.get("name"))
        and server_in_install_scope(candidate, config, job_id=job_id)
    ):
        _require_server_bundle_digest(candidate, expected_bundle_digest)
        return candidate
    inventory = run_hcloud(
        ["server", "list", "--selector", managed_selector(config, job_id=job_id), "-o", "json"]
    )
    records = [
        server for server in parse_server_records(
            inventory.get("stdout"), context="post-create exact server inventory"
        )
        if (server.get("id") or server.get("name"))
        and server_in_install_scope(server, config, job_id=job_id)
    ]
    if len(records) != 1:
        raise HetznerDriverError(
            f"create succeeded but exact server identity is ambiguous ({len(records)} matches)"
        )
    _require_server_bundle_digest(records[0], expected_bundle_digest)
    return records[0]


def wait_for_ssh(ip: str, *, ssh_options: list[str], timeout: float | None = None,
                 interval: float | None = None) -> None:
    """Poll until root SSH is accepted, losing no work to the cloud-init boot race.

    hcloud reports a server as `running` as soon as the VM boots, which is well before
    cloud-init has started sshd; a push issued at that moment fails with connection
    refused and, under `oneshot`, takes the whole run down with it."""
    timeout = SSH_READY_TIMEOUT if timeout is None else timeout
    interval = SSH_READY_INTERVAL if interval is None else interval
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while True:
        try:
            _run(["ssh", *ssh_options, f"root@{ip}", "true"], timeout=25.0)
            return
        except Exception as exc:  # noqa: BLE001 - any transport failure is worth retrying
            last = exc
        if time.monotonic() >= deadline:
            raise HetznerDriverError(f"SSH not ready on {ip} within {timeout:.0f}s: {last}")
        time.sleep(interval)


def push(*, job_id: str, job_dir: str | Path, config: Any, confirm: bool = False,
         dry_run: bool = False, state_root: Path | None = None,
         approved_bundle_sha256: str | None = None,
         _bundle: BundleSnapshot | None = None) -> dict[str, Any]:
    """Copy an approved, link-free immutable snapshot of the job bundle over SSH."""
    if _bundle is None:
        with _validated_bundle_snapshot(
            job_dir,
            config,
            expected_job_id=job_id,
        ) as bundle:
            return push(
                job_id=job_id, job_dir=job_dir, config=config, confirm=confirm,
                dry_run=dry_run, state_root=state_root,
                approved_bundle_sha256=approved_bundle_sha256,
                _bundle=bundle,
            )
    approved_digest = _require_bundle_approval(
        _bundle,
        approved_bundle_sha256,
        operation="push",
    )
    if str(_bundle.manifest.get("job_id") or "") != str(job_id):
        raise HetznerDriverError("Hetzner bundle manifest job_id does not match the requested job")
    local = f"{str(_bundle.path).rstrip('/')}/"
    if dry_run:
        display_options = [*SSH_BASE_OPTS, "-o", "UserKnownHostsFile=<per-job-known-hosts>"]
        return {
            "dry_run": True,
            "job_id": job_id,
            "bundle_digest": _bundle.digest,
            "command": [
                "rsync",
                "-az",
                "-e",
                shlex.join(["ssh", *display_options]),
                "<validated-bundle-snapshot>/",
                f"root@<server-ip>:{REMOTE_DIR}/",
            ],
        }
    if not confirm:
        raise HetznerDriverError("refusing to push: explicit confirm is required")
    initial_server = _job_server(
        job_id,
        config,
        expected_bundle_digest=approved_digest,
    )
    server_id = _exact_server_id(initial_server)
    ip = _server_ipv4(initial_server)
    ssh_options = _ssh_options(
        config=config, state_root=state_root, job_id=job_id, ip=ip
    )
    wait_for_ssh(ip, ssh_options=ssh_options)
    # Refetch the immutable provider ID immediately before upload.  A mutable label/name
    # selector result from before the SSH wait is not upload authority.
    latest_server = describe_server_exact(server_id, config)
    if not server_in_install_scope(latest_server, config, job_id=job_id):
        raise HetznerDriverError(
            "server left the expected install/job scope before bundle upload"
        )
    _require_server_bundle_digest(latest_server, approved_digest)
    if _server_ipv4(latest_server) != ip:
        raise HetznerDriverError("server IPv4 changed before bundle upload")
    argv = [
        "rsync",
        "-az",
        "-e",
        shlex.join(["ssh", *ssh_options]),
        local,
        f"root@{ip}:{REMOTE_DIR}/",
    ]
    _run(argv, timeout=600.0)
    return {
        "job_id": job_id, "pushed_to": f"{REMOTE_DIR}/", "server_ip_known": True,
        "bundle_digest": approved_digest,
        "server_id": server_id,
    }


def run(*, job_id: str, config: Any, confirm: bool = False, dry_run: bool = False,
        state_root: Path | None = None) -> dict[str, Any]:
    """Start the bundle detached at full cores on the server."""
    remote_cmd = f"cd {REMOTE_DIR} && CORES=$(nproc) nohup bash run.sh > run.log 2>&1 & echo started"
    if dry_run:
        display_options = [*SSH_BASE_OPTS, "-o", "UserKnownHostsFile=<per-job-known-hosts>"]
        return {"dry_run": True, "job_id": job_id,
                "command": ["ssh", *display_options, "root@<server-ip>", remote_cmd]}
    if not confirm:
        raise HetznerDriverError("refusing to run: explicit confirm is required")
    ip = _server_ip(job_id, config)
    ssh_options = _ssh_options(config=config, state_root=state_root, job_id=job_id, ip=ip)
    _run(["ssh", *ssh_options, f"root@{ip}", remote_cmd])
    return {"job_id": job_id, "started": True}


def status(*, job_id: str, config: Any) -> dict[str, Any]:
    """Server state for this job (hcloud list by label). Free of remote side effects."""
    result = run_hcloud(
        ["server", "list", "--selector", managed_selector(config, job_id=job_id), "-o", "json"]
    )
    servers = parse_server_records(
        result.get("stdout"),
        context="hcloud status inventory",
    )
    return {"job_id": job_id, "servers": [
        {"name": s.get("name"), "status": s.get("status")}
        for s in servers if server_in_install_scope(s, config, job_id=job_id)]}


def wait(*, job_id: str, config: Any, timeout: float | None = None, interval: float = 20.0,
         max_polls: int = 100000, state_root: Path | None = None) -> dict[str, Any]:
    """Poll for the bundle's result marker over SSH until it appears or the wall cap hits."""
    import time

    marker = f"{REMOTE_DIR}/RESULTS.json"
    ip = _server_ip(job_id, config)
    ssh_options = _ssh_options(config=config, state_root=state_root, job_id=job_id, ip=ip)
    start = time.time()
    for poll in range(int(max_polls)):
        try:
            _run(["ssh", *ssh_options, f"root@{ip}", f"test -f {marker}"])
            return {"job_id": job_id, "status": "completed", "polls": poll + 1}
        except HetznerDriverError:
            pass
        if timeout is not None and time.time() - start > timeout:
            return {"job_id": job_id, "status": "timeout", "polls": poll + 1}
        time.sleep(interval)
    return {"job_id": job_id, "status": "timeout", "polls": int(max_polls)}


def fetch(*, job_id: str, config: Any, dest: str | Path | None = None,
          salvage: bool = False, state_root: Path | None = None) -> dict[str, Any]:
    """Copy results (and the resumable out/ tree) back and verify they are well formed.
    On failure/timeout paths, `salvage=True` fetches checkpoints before teardown.

    Records a `fetch` audit event on success, which is what `down` consults before it
    destroys a job's server. The record is written only after the `out/` copy returns,
    so a collection that failed leaves no record and the teardown interlock still bites."""
    dest_dir = Path(dest).expanduser() if dest else Path.cwd() / "hetzner-results" / job_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    ip = _server_ip(job_id, config)
    ssh_options = _ssh_options(config=config, state_root=state_root, job_id=job_id, ip=ip)
    _run(["scp", *ssh_options, "-r", f"root@{ip}:{REMOTE_DIR}/out", str(dest_dir)], timeout=600.0)
    result_ok = False
    try:
        _run(["scp", *ssh_options, f"root@{ip}:{REMOTE_DIR}/RESULTS.json", str(dest_dir)], timeout=120.0)
        results_path = dest_dir / "RESULTS.json"
        if results_path.is_file():
            json.loads(results_path.read_text(encoding="utf-8"))  # verify well formed
            result_ok = True
    except (HetznerDriverError, json.JSONDecodeError):
        if not salvage:
            raise
    _audit(state_root, {
        "event": hetzner_audit.EVENT_FETCH, "job_id": job_id, "dest": str(dest_dir),
        "results_present": result_ok, "salvage": salvage,
    })
    return {"job_id": job_id, "fetched_to": str(dest_dir), "results_present": result_ok,
            "salvage": salvage}


def _fetch_recorded(state_root: Path, job_id: str) -> bool:
    """Has `fetch` successfully copied this job's results back at least once?

    An unreadable audit log counts as "no fetch": the interlock fails closed toward
    keeping the data, because both the explicit override and the billing kill switches
    stay open regardless, and the cloud-init dead-man's-switch still caps the server."""
    try:
        records = hetzner_audit.read(Path(state_root))
    except Exception:  # noqa: BLE001 - a corrupt log must never crash teardown
        return False
    return any(r.get("event") == hetzner_audit.EVENT_FETCH and str(r.get("job_id")) == str(job_id)
               for r in records)


def _destruction_digest(records: list[dict[str, Any]], *, project_identity: str) -> tuple[int, str]:
    targets = sorted(_exact_server_id(server) for server in records)
    payload = json.dumps(
        {"project_identity": project_identity, "targets": targets},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return len(targets), hashlib.sha256(payload).hexdigest()


def _project_delete_confirmation(project_identity: str, count: int, digest: str) -> str:
    return f"DELETE-AAS-HETZNER project={project_identity} count={count} digest={digest[:12]}"


def down(*, config: Any, state_root: Path | None = None, job_id: str | None = None,
         server_id: str | None = None, all_tagged: bool = False, orphans: bool = False,
         confirm: bool = False, dry_run: bool = False,
         allow_unfetched: bool = False,
         project_confirmation: str | None = None) -> dict[str, Any]:
    """DESTROY servers -- the only thing that stops Hetzner billing. Selects by job-id, by
    server-id, `--all` (kill switch: every managed server), or `--orphans` (current-install
    servers whose job id is absent from an authoritative reservation ledger).

    Job-id teardown additionally requires that the job's results were fetched, because the
    server holds the only copy and deletion is irreversible. `allow_unfetched` overrides it.
    Only explicit `--all` is project-wide unconditional deletion. Orphan cleanup fails closed
    when local active-job evidence is missing, malformed, or changes during deletion."""
    selector_count = sum((bool(job_id), bool(server_id), bool(all_tagged), bool(orphans)))
    if selector_count != 1:
        raise HetznerDriverError(
            "down requires exactly one of job_id, server_id, all_tagged, or orphans"
        )
    if all_tagged or orphans:
        # Emergency/billing-safety operations must find servers from older or moved runtime
        # scopes as well as the current one.  Job-id and server-id operations stay exact-scope.
        install_scope(config)
        selector = managed_account_selector()
        mode = "all" if all_tagged else "orphans"
    elif job_id:
        selector = managed_selector(config, job_id=job_id)
        mode = "job"
    elif server_id:
        selector = None
        mode = "server"
    else:  # pragma: no cover - exact selector count above guards this
        raise HetznerDriverError("down selector is unavailable")

    active_job_ids: set[str] | None = None
    if mode == "orphans":
        if state_root is None:
            raise HetznerDriverError(
                "down --orphans requires an authoritative Hetzner reservation ledger"
            )
        active_job_ids = budget_ledger.authoritative_reserved_job_ids(
            Path(state_root),
            "hetzner",
        )
        if active_job_ids is None:
            raise HetznerDriverError(
                "down --orphans cannot verify authoritative active-job state"
            )

    if dry_run and mode != "all":
        listed = ["hcloud", "server", "list", "--selector", selector, "-o", "json"] if selector \
            else ["hcloud", "server", "describe", str(server_id)]
        delete = ["hcloud", "server", "delete", "<server-id>" if selector else str(server_id)]
        return {"dry_run": True, "mode": mode, "selector": selector,
                "list_command": listed, "delete_command": delete, "destroyed": [],
                "predicate": (
                    "current-install job-id absent from authoritative ledger"
                    if mode == "orphans" else None
                )}

    if not token_present():
        raise HetznerDriverError("refusing to destroy: HCLOUD_TOKEN is not set")
    if not confirm and not (dry_run and mode == "all"):
        raise HetznerDriverError("refusing to destroy: explicit confirm is required")
    if mode == "job" and not allow_unfetched and state_root is not None \
            and not _fetch_recorded(Path(state_root), str(job_id)):
        raise HetznerDriverError(
            f"refusing to destroy job {job_id}: no successful fetch is recorded, so this "
            f"would delete the only copy of the results. Run `fetch {job_id}` first (or "
            f"`oneshot`, which fetches before it tears down), or pass --allow-unfetched "
            f"to destroy anyway. Use --orphans/--all to stop billing unconditionally.")

    records: list[dict[str, Any]] = []
    if selector is None:
        server = describe_server_exact(str(server_id), config)
        if not server_in_install_scope(server, config):
            raise HetznerDriverError(
                f"refusing to destroy server {server_id}: it is not labelled for this install"
            )
        records = [server]
    else:
        result = run_hcloud(["server", "list", "--selector", selector, "-o", "json"])
        servers = parse_server_records(
            result.get("stdout"),
            context=f"hcloud {mode} teardown inventory",
        )
        records = [
            server for server in servers
            if (server.get("id") or server.get("name"))
            and (
                server_in_install_scope(server, config, job_id=str(job_id))
                if mode == "job"
                else (
                    server_in_install_scope(server, config)
                    and str((server.get("labels") or {}).get("job-id") or "")
                    not in (active_job_ids or set())
                    if mode == "orphans"
                    else server_in_project_scope(server, config)
                )
            )
        ]

    destruction_count: int | None = None
    destruction_digest: str | None = None
    required_confirmation: str | None = None
    project_identity: str | None = None
    if mode == "all":
        # Broad deletion is bound to protected, current reaper evidence and an exact target-set
        # confirmation. The read-only inventory remains available before a lease exists so an
        # operator can inspect the set; only the live destructive branch consumes evidence.
        project_identity = _project_identity(config)
        destruction_count, destruction_digest = _destruction_digest(
            records, project_identity=project_identity
        )
        required_confirmation = _project_delete_confirmation(
            project_identity, destruction_count, destruction_digest
        )
        if dry_run:
            return {
                "dry_run": True,
                "mode": mode,
                "selector": selector,
                "project_identity": project_identity,
                "target_count": destruction_count,
                "target_digest": destruction_digest,
                "required_confirmation": required_confirmation,
                "destroyed": [],
            }
        REAPER_LEASE_VERIFIER(config)
        if project_confirmation != required_confirmation:
            raise HetznerDriverError(
                "project-wide deletion requires the exact inventory-bound confirmation: "
                + required_confirmation
            )

    destroyed: list[str] = []
    errors: list[dict[str, str]] = []
    for server in records:
        try:
            target = _exact_server_id(server)
            latest_server = describe_server_exact(target, config)
            if mode == "job" and not server_in_install_scope(
                latest_server,
                config,
                job_id=str(job_id),
            ):
                raise HetznerDriverError(
                    "server left the requested install/job scope before deletion"
                )
            if mode == "server" and not server_in_install_scope(latest_server, config):
                raise HetznerDriverError(
                    "server left the requested install scope before deletion"
                )
            if mode == "all" and not server_in_project_scope(latest_server, config):
                raise HetznerDriverError(
                    "server left the confirmed managed project scope before deletion"
                )
        except Exception as exc:  # noqa: BLE001 - continue through later billable servers
            errors.append({
                "server": str(server.get("id") or ""),
                "stage": "refetch",
                "error": _redact(str(exc)),
            })
            continue
        if mode == "orphans":
            latest_active = budget_ledger.authoritative_reserved_job_ids(
                Path(state_root),
                "hetzner",
            )
            if latest_active is None:
                raise HetznerDriverError(
                    "down --orphans lost authoritative active-job state during deletion"
                )
            if not server_in_install_scope(latest_server, config):
                continue
            server_job_id = str(
                (latest_server.get("labels") or {}).get("job-id") or ""
            )
            if server_job_id in latest_active:
                continue
            if not server_job_id:
                raise HetznerDriverError(
                    "down --orphans cannot delete a freshly described server without job-id"
                )
        try:
            run_hcloud(["server", "delete", target])
        except Exception as exc:  # noqa: BLE001 - continue through later billable servers
            errors.append({"server": target, "stage": "delete", "error": _redact(str(exc))})
            continue
        destroyed.append(target)
        server = latest_server
        labels = server.get("labels") or {}
        reconcile_job = labels.get("job-id") or (job_id if mode == "job" else None)
        try:
            _audit(state_root, {
                "event": hetzner_audit.EVENT_DESTROY, "server": target,
                "name": server.get("name"), "mode": mode, "reason": f"down {mode}",
                "job_id": reconcile_job, "labels": labels or None, "real_eur": None,
            })
        except Exception as exc:  # noqa: BLE001 - reconciliation must still run
            errors.append({"server": target, "stage": "audit", "error": _redact(str(exc))})
        # A foreign/legacy scope can reuse a job id that exists in this install's ledger.  Audit
        # its deletion locally, but never mutate the current ledger for that foreign identity.
        if (
            state_root is not None
            and reconcile_job
            and server_in_install_scope(server, config)
        ):
            try:
                budget_ledger.reconcile(Path(state_root), "hetzner", str(reconcile_job), None)
            except Exception as exc:  # noqa: BLE001 - report and continue through the set
                errors.append({
                    "server": target, "stage": "reconcile", "error": _redact(str(exc))
                })
    return {
        "mode": mode, "selector": selector, "destroyed": destroyed, "errors": errors,
        "project_identity": project_identity,
        "target_count": destruction_count,
        "target_digest": destruction_digest,
    }


def _install_teardown_signals(teardown: Callable[[str], Any]) -> dict[Any, Any]:
    """Install SIGINT/SIGTERM/SIGHUP handlers that run teardown then re-raise, so the
    `finally` in `oneshot` always fires. This is the Python equivalent of a shell
    `trap 'down' EXIT INT TERM HUP`. No-op off the main thread."""
    import signal

    installed: dict[Any, Any] = {}

    def _handler(signum: int, _frame: Any) -> None:
        teardown(f"signal-{signum}")
        raise KeyboardInterrupt(f"terminated by signal {signum}")

    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            installed[sig] = signal.signal(sig, _handler)
        except (ValueError, OSError):  # not the main thread / unsupported
            pass
    return installed


def _restore_signals(installed: dict[Any, Any]) -> None:
    import signal

    for sig, old in installed.items():
        try:
            signal.signal(sig, old)
        except (ValueError, OSError):
            pass


def oneshot(*, job_dir: str | Path, config: Any, state_root: Path, confirm: bool = False,
            dry_run: bool = False, dest: str | Path | None = None,
            timeout: float | None = None,
            approved_bundle_sha256: str | None = None,
            _bundle: BundleSnapshot | None = None) -> dict[str, Any]:
    """up -> push -> run -> wait -> fetch -> down, with teardown GUARANTEED on every exit
    path (finally + signal handlers == `trap 'down' EXIT INT TERM HUP`). Failure and
    timeout paths salvage checkpoints before destroy so the run is resumable."""
    if _bundle is None:
        with _validated_bundle_snapshot(job_dir, config) as bundle:
            return oneshot(
                job_dir=job_dir, config=config, state_root=state_root, confirm=confirm,
                dry_run=dry_run, dest=dest, timeout=timeout,
                approved_bundle_sha256=approved_bundle_sha256,
                _bundle=bundle,
            )
    approved_digest = _require_bundle_approval(
        _bundle,
        approved_bundle_sha256,
        operation="oneshot",
    )
    manifest = _bundle.manifest
    job_id = str(manifest.get("job_id") or _new_job_id())

    if dry_run:
        return {
            "dry_run": True, "job_id": job_id,
            "bundle_digest": _bundle.digest,
            "sequence": ["up", "push", "run", "wait", "fetch", "down"],
            "up": up(
                job_dir=job_dir, config=config, state_root=state_root, dry_run=True,
                approved_bundle_sha256=approved_digest,
                _bundle=_bundle,
            ),
            "down": down(config=config, job_id=job_id, dry_run=True),
            "teardown": "guaranteed on every exit (finally + signal handlers == trap 'down' EXIT INT TERM HUP)",
        }

    _require_durable_reaper_for_live_provisioning(config)

    if not token_present():
        raise HetznerDriverError("refusing to run oneshot: HCLOUD_TOKEN is not set")
    if not confirm:
        raise HetznerDriverError("refusing to run oneshot: explicit confirm is required")

    torn_down = {"done": False, "running": False, "result": None, "server_id": None}

    def _teardown(_reason: str) -> Any:
        if torn_down["done"]:
            return torn_down["result"]
        if torn_down["running"]:
            raise HetznerDriverError("Hetzner teardown is already in progress")
        torn_down["running"] = True
        # allow_unfetched: teardown here is guaranteed by contract, and the fetch/salvage
        # step above already ran. The interlock guards hand-composed teardowns, not this one.
        try:
            selector = (
                {"server_id": str(torn_down["server_id"])}
                if torn_down["server_id"] is not None
                else {"job_id": job_id}
            )
            result = down(
                config=config, state_root=state_root, confirm=True,
                allow_unfetched=True, **selector,
            )
        except BaseException:
            # A signal path can enter teardown before the outer finally. Keep the latch open
            # when that attempt throws so the finally path can make the required retry.
            torn_down["running"] = False
            raise
        torn_down["result"] = result
        torn_down["done"] = True
        torn_down["running"] = False
        return result

    installed = _install_teardown_signals(_teardown)
    steps: dict[str, Any] = {}
    try:
        steps["up"] = up(
            job_dir=job_dir, config=config, state_root=state_root, confirm=True,
            approved_bundle_sha256=approved_digest,
            _bundle=_bundle,
        )
        torn_down["server_id"] = steps["up"].get("server_id")
        if not torn_down["server_id"]:
            raise HetznerDriverError("up did not return an exact created server identity")
        steps["push"] = push(
            job_id=job_id, job_dir=job_dir, config=config, confirm=True,
            state_root=state_root, approved_bundle_sha256=approved_digest,
            _bundle=_bundle,
        )
        steps["run"] = run(job_id=job_id, config=config, confirm=True, state_root=state_root)
        steps["wait"] = wait(
            job_id=job_id, config=config, timeout=timeout, state_root=state_root
        )
        steps["fetch"] = fetch(job_id=job_id, config=config, dest=dest, state_root=state_root,
                               salvage=steps["wait"].get("status") != "completed")
        outcome = "completed" if steps["wait"].get("status") == "completed" else steps["wait"].get("status")
    except BaseException:
        # Salvage checkpoints before the guaranteed teardown, best-effort.
        try:
            steps["fetch"] = fetch(job_id=job_id, config=config, dest=dest, salvage=True,
                                   state_root=state_root)
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        try:
            steps["down"] = _teardown("exit")
        finally:
            _restore_signals(installed)
    if steps["down"].get("errors"):
        raise HetznerDriverError(
            "Hetzner teardown completed with errors: "
            + json.dumps(steps["down"]["errors"], sort_keys=True)
        )
    return {"job_id": job_id, "status": outcome, "steps": steps}


# --- CLI ----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hetzner-research-compute",
        description="Hetzner Cloud lifecycle driver for the research-compute Hetzner lane.",
    )
    parser.add_argument("--config", default=None, help="Path to research-compute.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("bootstrap", help="Check the hcloud CLI + token and report doctor (no provisioning)")
    sub.add_parser("doctor", help="Offline lane / token / hcloud / caps readiness")

    pf = sub.add_parser("preflight", help="Emit the Hetzner plan for a job bundle (no server)")
    pf.add_argument("--job", required=True, help="Path to a portable job-bundle directory")
    pf.add_argument(
        "--bundle-sha256",
        default=None,
        help="optionally verify this exact lowercase digest against the preflight snapshot",
    )
    pf.add_argument("--json", action="store_true", help="(accepted for parity; output is always JSON)")

    up_p = sub.add_parser("up", help="Create a labelled server (budget-gated)")
    up_p.add_argument("--job", required=True)
    up_p.add_argument(
        "--bundle-sha256",
        required=True,
        help="exact full digest emitted by preflight for the approved bundle",
    )
    up_p.add_argument("--confirm", action="store_true")
    up_p.add_argument("--dry-run", action="store_true")
    up_p.add_argument("--image", default=None)
    up_p.add_argument("--location", default=None)
    up_p.add_argument(
        "--user-data",
        default=None,
        help="unsupported: custom user-data is rejected so the managed dead-man switch cannot be bypassed",
    )

    push_p = sub.add_parser("push", help="Copy the job bundle to the server")
    push_p.add_argument("job_id")
    push_p.add_argument("--job", required=True)
    push_p.add_argument(
        "--bundle-sha256",
        required=True,
        help="exact full digest approved for this server and upload",
    )
    push_p.add_argument("--confirm", action="store_true")
    push_p.add_argument("--dry-run", action="store_true")

    run_p = sub.add_parser("run", help="Start the bundle detached at full cores")
    run_p.add_argument("job_id")
    run_p.add_argument("--confirm", action="store_true")
    run_p.add_argument("--dry-run", action="store_true")

    status_p = sub.add_parser("status", help="Server state for a job")
    status_p.add_argument("job_id")

    wait_p = sub.add_parser("wait", help="Poll until the run finishes or the wall cap hits")
    wait_p.add_argument("job_id")
    wait_p.add_argument("--timeout", type=float, default=None)

    fetch_p = sub.add_parser("fetch", help="Copy results back and verify they are well formed")
    fetch_p.add_argument("job_id")
    fetch_p.add_argument("--dest", default=None)

    down_p = sub.add_parser("down", help="DESTROY servers (the only thing that stops billing)")
    down_p.add_argument("job_id", nargs="?", default=None)
    down_p.add_argument("--server-id", default=None)
    down_p.add_argument("--all", dest="all_tagged", action="store_true")
    down_p.add_argument("--orphans", action="store_true")
    down_p.add_argument("--confirm", action="store_true")
    down_p.add_argument(
        "--confirm-project-wide",
        dest="project_confirmation",
        default=None,
        help="exact inventory-bound phrase emitted by `down --all --dry-run`",
    )
    down_p.add_argument("--dry-run", action="store_true")
    down_p.add_argument("--allow-unfetched", action="store_true",
                        help="destroy a job's server even though no fetch is recorded "
                             "(discards the only copy of the results)")

    one_p = sub.add_parser("oneshot", help="up->push->run->wait->fetch->down, teardown guaranteed")
    one_p.add_argument("--job", required=True)
    one_p.add_argument(
        "--bundle-sha256",
        required=True,
        help="exact full digest emitted by preflight for the approved bundle",
    )
    one_p.add_argument("--confirm", action="store_true")
    one_p.add_argument("--dry-run", action="store_true")
    one_p.add_argument("--dest", default=None)
    one_p.add_argument("--timeout", type=float, default=None)
    return parser


def _load(args: argparse.Namespace) -> tuple[Any | None, Path]:
    root = runtime_workspace()
    config_path = Path(args.config).expanduser().resolve() if args.config else default_config_path(root)
    state_root = config_path.parent.parent / "memories" / "research-compute"
    config: Any | None = None
    if config_path.exists():
        config = load_config(config_path)
        state_root = config.state_root(root)
    return config, state_root


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config, state_root = _load(args)
        if args.command == "bootstrap":
            result = bootstrap(config)
        else:
            if config is None:
                raise HetznerDriverError("research-compute.toml not found; run the broker bootstrap first")
            Path(state_root).mkdir(parents=True, exist_ok=True)
            if args.command == "doctor":
                result = doctor(config)
            elif args.command == "preflight":
                result = preflight(
                    job_dir=args.job,
                    config=config,
                    state_root=Path(state_root),
                    approved_bundle_sha256=args.bundle_sha256,
                )
            elif args.command == "up":
                result = up(job_dir=args.job, config=config, state_root=Path(state_root),
                            confirm=args.confirm, dry_run=args.dry_run, image=args.image,
                            location=args.location, user_data=args.user_data,
                            approved_bundle_sha256=args.bundle_sha256)
            elif args.command == "push":
                result = push(job_id=args.job_id, job_dir=args.job, config=config,
                              confirm=args.confirm, dry_run=args.dry_run,
                              state_root=Path(state_root),
                              approved_bundle_sha256=args.bundle_sha256)
            elif args.command == "run":
                result = run(job_id=args.job_id, config=config, confirm=args.confirm, dry_run=args.dry_run)
            elif args.command == "status":
                result = status(job_id=args.job_id, config=config)
            elif args.command == "wait":
                result = wait(job_id=args.job_id, config=config, timeout=args.timeout)
            elif args.command == "fetch":
                result = fetch(job_id=args.job_id, config=config, dest=args.dest,
                               state_root=Path(state_root))
            elif args.command == "down":
                result = down(config=config, state_root=Path(state_root), job_id=args.job_id,
                              server_id=args.server_id, all_tagged=args.all_tagged,
                              orphans=args.orphans, confirm=args.confirm, dry_run=args.dry_run,
                              allow_unfetched=args.allow_unfetched,
                              project_confirmation=args.project_confirmation)
            elif args.command == "oneshot":
                result = oneshot(job_dir=args.job, config=config, state_root=Path(state_root),
                                 confirm=args.confirm, dry_run=args.dry_run, dest=args.dest,
                                 timeout=args.timeout,
                                 approved_bundle_sha256=args.bundle_sha256)
            else:  # pragma: no cover - argparse guards this
                raise HetznerDriverError(f"unhandled command: {args.command}")
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": _redact(str(exc))}, indent=2))
        return 1
    ok = not bool(result.get("errors"))
    print(json.dumps({"ok": ok, **result}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
