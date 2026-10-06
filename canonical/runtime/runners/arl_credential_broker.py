#!/usr/bin/env python3
"""Credential broker for autonomous-research-loop.

Only this credential broker parses provider/compute authorities.  The ARL
orchestrator receives an opaque local capability and remains credential-blind;
each provider or compute subprocess receives only its selected projection.
"""

from __future__ import annotations

import argparse
import ctypes
import io
import hashlib
import hmac
import json
import os
import pwd
import re
import secrets
import shutil
import socketserver
import stat
import struct
import subprocess
import sys
import tempfile
import threading

from pathlib import Path
from typing import Any, Mapping

PROVIDER_POINTER = "AAS_PROVIDER_SECRETS_FILE"
COMPUTE_POINTER = "AAS_COMPUTE_SECRETS_FILE"
BROKER_SOCKET_ENV = "AAS_ARL_BROKER_SOCKET"
BROKER_TOKEN_ENV = "AAS_ARL_BROKER_TOKEN"
BROKER_PROXY_ENV = "AAS_ARL_COMPUTE_PROXY"
MAX_MESSAGE_BYTES = 32 * 1024 * 1024

PROVIDER_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN", "COPILOT_GITHUB_TOKEN",
        "COPILOT_PROVIDER_API_KEY", "COPILOT_PROVIDER_BEARER_TOKEN",
        "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "GH_TOKEN", "GITHUB_TOKEN",
        "GOOGLE_API_KEY", "GROK_API_KEY", "KIMI_API_KEY", "MOONSHOT_API_KEY",
        "OPENAI_API_KEY", "OPENCODE_API_KEY", "XAI_API_KEY",
    }
)
PROVIDER_KEY_MAP: dict[str, frozenset[str]] = {
    "anthropic": frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}),
    "claude": frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}),
    "codex": frozenset({"OPENAI_API_KEY"}),
    "codewhale": frozenset({"DEEPSEEK_API_KEY"}),
    "deepseek": frozenset({"DEEPSEEK_API_KEY"}),
    "antigravity": frozenset({"GEMINI_API_KEY", "GOOGLE_API_KEY"}),
    "gemini": frozenset({"GEMINI_API_KEY", "GOOGLE_API_KEY"}),
    "google": frozenset({"GEMINI_API_KEY", "GOOGLE_API_KEY"}),
    "grok": frozenset({"GROK_API_KEY", "XAI_API_KEY"}),
    "xai": frozenset({"GROK_API_KEY", "XAI_API_KEY"}),
    "copilot": frozenset({"COPILOT_GITHUB_TOKEN", "COPILOT_PROVIDER_API_KEY", "COPILOT_PROVIDER_BEARER_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"}),
    "kimi": frozenset({"KIMI_API_KEY", "MOONSHOT_API_KEY"}),
    "moonshot": frozenset({"KIMI_API_KEY", "MOONSHOT_API_KEY"}),
    "openai": frozenset({"OPENAI_API_KEY"}),
    "opencode": frozenset({"OPENCODE_API_KEY"}),
}
COMPUTE_KEYS = frozenset(
    {
        "HCLOUD_TOKEN", "HCLOUD_SSH_KEYS", "KAGGLE_API_TOKEN",
        "KAGGLE_CONFIG_DIR", "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET",
    }
)
COMPUTE_KEY_MAP: dict[str, frozenset[str]] = {
    "hetzner": frozenset({"HCLOUD_TOKEN", "HCLOUD_SSH_KEYS"}),
    "kaggle": frozenset({"KAGGLE_API_TOKEN", "KAGGLE_CONFIG_DIR"}),
    "modal": frozenset({"MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"}),
}
COMPUTE_PROJECTION_KEYS = frozenset().union(*COMPUTE_KEY_MAP.values())
PROVIDER_CONFIG_ENV = frozenset(
    {
        "CLAUDE_CONFIG_DIR", "CODEWHALE_HOME", "CODEX_HOME",
        "GEMINI_CONFIG_DIR", "GROK_CONFIG_DIR", "KIMI_CONFIG_DIR",
        "OPENCODE_CONFIG_DIR",
    }
)
PROVIDER_CONFIG_MAP: dict[str, frozenset[str]] = {
    "claude": frozenset({"CLAUDE_CONFIG_DIR"}),
    "codex": frozenset({"CODEX_HOME"}),
    "codewhale": frozenset({"CODEWHALE_HOME"}),
    "deepseek": frozenset({"CODEWHALE_HOME"}),
    "antigravity": frozenset({"GEMINI_CONFIG_DIR"}),
    "gemini": frozenset({"GEMINI_CONFIG_DIR"}),
    "google": frozenset({"GEMINI_CONFIG_DIR"}),
    "grok": frozenset({"GROK_CONFIG_DIR"}),
    "xai": frozenset({"GROK_CONFIG_DIR"}),
    "kimi": frozenset({"KIMI_CONFIG_DIR"}),
    "moonshot": frozenset({"KIMI_CONFIG_DIR"}),
    "opencode": frozenset({"OPENCODE_CONFIG_DIR"}),
}
PROVIDER_DEFAULT_CONFIG: dict[str, tuple[str, str]] = {
    "claude": ("CLAUDE_CONFIG_DIR", ".claude"),
    "codex": ("CODEX_HOME", ".codex"),
    "codewhale": ("CODEWHALE_HOME", ".codewhale"),
    "deepseek": ("CODEWHALE_HOME", ".codewhale"),
    "antigravity": ("GEMINI_CONFIG_DIR", ".gemini"),
    "gemini": ("GEMINI_CONFIG_DIR", ".gemini"),
    "google": ("GEMINI_CONFIG_DIR", ".gemini"),
    "grok": ("GROK_CONFIG_DIR", ".grok"),
    "xai": ("GROK_CONFIG_DIR", ".grok"),
    "kimi": ("KIMI_CONFIG_DIR", ".kimi"),
    "moonshot": ("KIMI_CONFIG_DIR", ".kimi"),
    "opencode": ("OPENCODE_CONFIG_DIR", ".config/opencode"),
}
# Providers that create session/lock state inside their config directory and
# therefore cannot run against a read-only bind of it.  These get a private
# writable directory in the synthetic home, seeded with only the listed
# regular files from the real config.  The grok CLI additionally ignores
# GROK_CONFIG_DIR and always resolves $HOME/<relative>, which the seeded
# target satisfies because the synthetic home is the child HOME.  Claude
# writes shell snapshots, session transcripts, and credential refreshes into
# CLAUDE_CONFIG_DIR during tool-using primary runs.
PROVIDER_SEED_STATE: dict[str, tuple[str, ...]] = {
    "claude": (".credentials.json", "settings.json", ".claude.json"),
    "grok": ("auth.json", "config.toml", "settings.json"),
    "xai": ("auth.json", "config.toml", "settings.json"),
}
SECRET_POINTERS = frozenset(
    {
        PROVIDER_POINTER, COMPUTE_POINTER, "AAS_SECRETS_FILE",
        "OPENCLAW_SECRETS_FILE", "AAS_SKILL_SECRETS_FILE",
        "AAS_CALIBRE_SECRETS_FILE", "AAS_ZOTERO_SECRETS_FILE",
        "AAS_FILE_DELIVERY_SECRETS_FILE", "REMOTE_BRIDGE_SECRETS_FILE",
        "SEND_EMAIL_SECRETS_FILE",
    }
)
CHILD_BASE_KEYS = frozenset(
    {
        "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
        "PATH", "SHELL", "TERM", "NO_COLOR", "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS", "AAS_RUNTIME_ROOT", "AAS_RUNTIME_WORKSPACE",
        "OPENCLAW_WORKSPACE", "AAS_RUNTIME_PYTHON", "AAS_RUNTIME_COMMAND_FD",
        "AAS_RUNTIME_COMMAND_PATH", "PYTHONDONTWRITEBYTECODE", "PYTHONUTF8",
        "PYTHONIOENCODING", "AAS_REMOTE_STRICT_NOTIFY_CHANNEL",
        "AAS_ALLOW_RAW_NOTIFY_CMD", "AAS_RUNTIME_PYTHON_PREFIX", "AAS_SKILL_VENV",
    }
)


def _runtime_root() -> Path:
    configured = Path(os.environ.get("AAS_RUNTIME_ROOT", ""))
    if configured.is_absolute() and configured.is_dir():
        return configured.resolve()
    return Path(__file__).resolve().parents[1]


def _skills_root(runtime_root: Path) -> Path:
    """Installed runtimes carry workspace/skills; a source checkout carries skills."""
    installed = runtime_root / "workspace" / "skills"
    return installed if installed.is_dir() else runtime_root / "skills"


def _attested_interpreter() -> Path:
    """Real path of the attested interpreter.  AAS_RUNTIME_PYTHON is the launcher's
    bound descriptor (/proc/self/fd/N); it is valid in this process but not in a
    subprocess.run child, which closes every inherited descriptor (close_fds=True,
    no pass_fds) before execve.  The child therefore execs the validated real path."""
    candidate = os.environ.get("AAS_RUNTIME_PYTHON") or "/usr/bin/python3"
    real = Path(os.path.realpath(candidate))
    if not re.fullmatch(r"/usr/bin/python3(\.[0-9]+)?", str(real)) or not os.path.samefile(candidate, "/usr/bin/python3"):
        raise ValueError("compute driver interpreter is not the attested system Python")
    return real


def _skill_python_argv0(interpreter: Path) -> tuple[str, str]:
    """(argv0, child PATH).  Same two facts the wrappers re-check; the launcher did full admission."""
    prefix = os.environ.get("AAS_RUNTIME_PYTHON_PREFIX")
    if not prefix:
        return str(interpreter), "/usr/bin:/bin"
    root = Path(prefix); cfg = root / "pyvenv.cfg"; py = root / "bin" / "python"
    if (not root.is_absolute() or cfg.is_symlink() or not cfg.is_file()
            or not py.is_symlink() or not os.path.samefile(py, interpreter)):
        raise ValueError("skill Python venv is not admissible")
    return str(py), f"{root}/bin:/usr/bin:/bin"


def _owner_controlled_regular(info: os.stat_result) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid in {0, os.getuid()}
        and not info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        and (info.st_nlink == 1 or info.st_uid == 0)
    )


def _load_module_file(path: Path, name: str) -> Any:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if not _owner_controlled_regular(before):
            raise RuntimeError(f"untrusted broker dependency: {path.name}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65_536)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(fd)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise RuntimeError(f"broker dependency changed while reading: {path.name}")
    finally:
        os.close(fd)
    module = type(sys)(name)
    module.__file__ = str(path)
    exec(compile(b"".join(chunks), str(path), "exec"), module.__dict__)
    return module


def _set_nondumpable() -> None:
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE
            raise OSError(ctypes.get_errno(), "could not make credential broker nondumpable")


def _recv_exact(stream: Any, count: int) -> bytes:
    value = stream.read(count)
    if value is None or len(value) != count:
        raise ValueError("incomplete broker message")
    return value


def _safe_environment(source: Mapping[str, str]) -> dict[str, str]:
    child = {
        key: str(value)
        for key, value in source.items()
        if key in CHILD_BASE_KEYS or key.startswith("AAS_AUTOLOOP_") or key.startswith("AAS_FORCE_LOOP_") or key.startswith("LC_")
    }
    child["PATH"] = "/usr/bin:/bin"
    child.pop("PYTHONPATH", None)
    child.pop("PYTHONHOME", None)
    return child


def _fd_numbers(environment: Mapping[str, str]) -> tuple[int, ...]:
    values: set[int] = set()
    for name in ("AAS_RUNTIME_COMMAND_FD", "AAS_RUNTIME_PYTHON"):
        value = str(environment.get(name) or "")
        match = re.fullmatch(r"/(?:proc/self|dev)/fd/(\d+)", value)
        if name == "AAS_RUNTIME_COMMAND_FD" and value.isdigit():
            values.add(int(value))
        elif match:
            values.add(int(match.group(1)))
    return tuple(sorted(values))


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _toml_parser():
    """The TOML parser, resolved at use rather than at import.

    Only the Modal authority is TOML; provider projections, Hetzner env files and
    Kaggle JSON are not. Importing at module scope made tomli a hard requirement
    for every broker verb on the declared Python 3.10 floor, so a host without it
    could not start the broker at all.
    """
    try:
        import tomllib  # Python 3.11+

        return tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
        try:
            import tomli

            return tomli
        except ModuleNotFoundError:
            raise RuntimeError(
                "reading the Modal authority needs a TOML parser: "
                "install tomli (pip install --user 'tomli>=2') on Python 3.10, "
                "or run on Python 3.11+ where tomllib is in the standard library"
            ) from None


def _load_modal_authority() -> dict[str, str]:
    """Read CSR's native 0600 ~/.modal.toml into an in-memory projection."""

    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    authority = home / ".modal.toml"
    if not authority.exists():
        return {}
    descriptor = os.open(authority, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.getuid()}
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size > 65_536
        ):
            raise RuntimeError("Modal authority is not the exact private CSR file")
        payload = bytearray()
        while len(payload) <= 65_536:
            chunk = os.read(descriptor, 65_537 - len(payload))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(payload) > 65_536 or any(
            getattr(before, field) != getattr(after, field) for field in fields
        ):
            raise RuntimeError("Modal authority changed while being read")
    finally:
        os.close(descriptor)
    try:
        document = _toml_parser().loads(bytes(payload).decode("utf-8"))
    finally:
        for index in range(len(payload)):
            payload[index] = 0
    profiles = [
        value
        for value in document.values()
        if isinstance(value, dict)
        and isinstance(value.get("token_id"), str)
        and str(value.get("token_id") or "").strip() == value.get("token_id")
        and isinstance(value.get("token_secret"), str)
        and str(value.get("token_secret") or "").strip() == value.get("token_secret")
        and value.get("token_id")
        and value.get("token_secret")
    ]
    selected = document.get("default")
    if not (
        isinstance(selected, dict)
        and selected.get("token_id")
        and selected.get("token_secret")
    ):
        if len(profiles) != 1:
            raise RuntimeError("Modal authority has no unambiguous credential profile")
        selected = profiles[0]
    return {
        "MODAL_TOKEN_ID": str(selected["token_id"]),
        "MODAL_TOKEN_SECRET": str(selected["token_secret"]),
    }


class CredentialState:
    def __init__(
        self,
        runtime_root: Path,
        providers: dict[str, str],
        compute: dict[str, str],
        provider_config: dict[str, str],
        parent_token: str,
        socket_path: str,
        private_root: Path,
    ) -> None:
        self.runtime_root = runtime_root
        self.providers = providers
        self.compute = compute
        self.provider_config = provider_config
        self.parent_token = parent_token
        self.socket_path = socket_path
        self.private_root = private_root
        authority_name = os.environ.get("AAS_ARL_FORMAL_AUTHORITY_ROOT")
        self.formal_authority_root = Path(authority_name) if authority_name else Path.home() / ".local/share/ai-agents-skills/host-authority"
        if not self.formal_authority_root.is_absolute() or self.formal_authority_root.resolve().is_relative_to(runtime_root.resolve()):
            raise ValueError("formal authority must be a stable host data directory outside runtime code")
        self.capabilities: dict[str, tuple[frozenset[str], Path]] = {}
        self.formal_registrations: dict[str, dict[str, Any]] = {}
        self.formal_locks: dict[str, threading.Lock] = {}
        self.formal_checkpoint_locks: dict[str, threading.Lock] = {}
        self.formal_step_capabilities: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.skill_dir = _skills_root(runtime_root) / "autonomous-research-loop-runtime"
        sys.path.insert(0, str(self.skill_dir))
        import autonomous_research_loop_runtime as runtime  # type: ignore
        import panel_parent  # type: ignore
        self.runtime = runtime
        self.panel = panel_parent
        from provider_resources import register_host_formal_authority_root
        register_host_formal_authority_root(self.formal_authority_root)
        self.proxy = self.skill_dir / "arl_compute_proxy.py"

    def _secret_projection(self, provider: str) -> dict[str, str]:
        normalized = provider.strip().lower().replace("_", "-")
        keys = PROVIDER_KEY_MAP.get(normalized)
        if keys is None:
            raise ValueError("provider has no credential projection contract")
        return {key: self.providers[key] for key in keys if self.providers.get(key)}

    def _config_projection(self, provider: str) -> dict[str, str]:
        keys = PROVIDER_CONFIG_MAP.get(provider, frozenset())
        return {
            key: self.provider_config[key]
            for key in keys
            if self.provider_config.get(key)
        }

    def _prepare_config_projection(
        self, provider: str, child_home: Path
    ) -> tuple[dict[str, str], dict[str, str]]:
        projected: dict[str, str] = {}
        mounts: dict[str, str] = {}
        real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        default = PROVIDER_DEFAULT_CONFIG.get(provider)
        if default is None:
            return projected, mounts
        env_name, relative = default
        source_text = self.provider_config.get(env_name, "")
        source = Path(source_text) if source_text else real_home / relative
        if not source.exists():
            return projected, mounts
        if not source.is_absolute() or source.is_symlink():
            raise ValueError("selected provider config path is unsafe")
        target = child_home / relative
        projected[env_name] = str(target)
        seed_names = PROVIDER_SEED_STATE.get(provider)
        if seed_names is not None:
            target.mkdir(mode=0o700, parents=True)
            for name in seed_names:
                seed_source = source / name
                try:
                    info = os.lstat(seed_source)
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("provider seed state file is unsafe")
                shutil.copy2(seed_source, target / name)
                os.chmod(target / name, 0o600)
            return projected, mounts
        mounts[str(target)] = str(source)
        return projected, mounts

    def _filter_process_evidence(
        self, rc: int, stdout: str, stderr: str, evidence: dict[str, Any] | None,
        *, extra_secrets: tuple[str, ...] = (),
    ) -> tuple[int, str, str, dict[str, Any] | None]:
        """Filter original bytes as well as text before anything leaves the broker."""
        import base64
        from provider_resources import validate_process_evidence, withhold_process_evidence

        values = tuple(value for value in (*self.providers.values(), *self.compute.values(), *extra_secrets) if value)
        if evidence is None:
            if any(value in stdout or value in stderr for value in values):
                return 126, "", "broker blocked provider output containing credential material\n", None
            rc, stdout, stderr = self._block_secret_output(rc, stdout, stderr)
            return rc, stdout, stderr, None
        if validate_process_evidence(evidence):
            return 126, "", "broker blocked malformed process evidence\n", None
        encoded = json.dumps(evidence, ensure_ascii=False)
        blocked = any(value in stdout or value in stderr or value in encoded for value in values)
        for name in ("stdout", "stderr"):
            stream = evidence[name]
            if stream["state"] in {"captured", "partial"}:
                raw = base64.b64decode(stream["base64"], validate=True)
                blocked = blocked or any(value.encode("utf-8") in raw for value in values)
        if blocked:
            evidence = withhold_process_evidence(evidence, "credential_material")
            for name in ("capture_error", "cleanup_error", "execution_error"):
                value = evidence.get(name)
                if isinstance(value, str) and any(secret in value for secret in values):
                    evidence[name] = "[withheld diagnostic]"
            return 126, "", "broker blocked provider output containing credential material\n", evidence
        return rc, stdout, stderr, evidence

    def formal_register(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Parent-token-only registration; workers cannot mint this authority."""
        import remote_formal
        from compute_policy import require_execution_policy
        if set(request) - {"operation", "run_dir", "project", "pin"}:
            raise ValueError("unrecognized formal registration fields")
        pin = request.get("pin")
        if not isinstance(pin, dict) or pin.get("execution_backend") != "kaggle-cpu":
            raise ValueError("host formal registration requires a pinned Kaggle CPU executor")
        run_dir = Path(str(request.get("run_dir") or ""))
        project = Path(str(request.get("project") or ""))
        if not run_dir.is_absolute() or not project.is_absolute() or not run_dir.is_dir() or not project.is_dir():
            raise ValueError("host formal registration requires exact existing roots")
        compute_binding = require_execution_policy(run_dir, "kaggle")
        if self.formal_authority_root.resolve().is_relative_to(project.resolve()):
            raise ValueError("formal authority cannot be inside the candidate project")
        bound = remote_formal.request_snapshot(Path(str(pin.get("remote_request") or "")),
            str(pin.get("remote_request_sha256") or ""), project)
        for value in (bound["bootstrap_script"], bound["compute_config"], bound["state_root"], pin["remote_request"]):
            if Path(value).resolve().is_relative_to(self.formal_authority_root.resolve()):
                raise ValueError("formal request inputs cannot reference host signing authority")
        if bound["submission_authorized"] is not True:
            raise ValueError("remote formal submission is not authorized")
        # Only immutable host registration data crosses this boundary; no CLI,
        # environment, validator choice or credential pointer is accepted.
        authority_id = hashlib.sha256(self._formal_authority_key(create=True)).hexdigest()
        registration = {"run_dir": str(run_dir.resolve()), "project": str(project.resolve()),
            "authority_id": authority_id,
            "pin": {name: pin[name] for name in ("execution_backend", "remote_request", "remote_request_sha256")}}
        fingerprint = hashlib.sha256(json.dumps(registration, sort_keys=True).encode()).hexdigest()
        # Policy changes never reset the persisted attempt account.
        registration["compute_policy_binding"] = compute_binding
        with self.lock:
            for identity, value in self.formal_registrations.items():
                if value["fingerprint"] == fingerprint:
                    if value["registration"].get("compute_policy_binding") != compute_binding:
                        raise ValueError("compute policy changed during this host registration")
                    return {"ok": True, "registration_id": identity}
            identity = secrets.token_hex(24)
            anchor_path = self.formal_authority_root / ("state-" + fingerprint + ".json")
            state_path = Path(bound["state_root"]) / "state.json"
            if not anchor_path.exists():
                if state_path.exists():
                    raise ValueError("preexisting remote state has no host admission anchor")
                from state_transaction import commit_transaction, RevisionConflict
                try:
                    commit_transaction(self.formal_authority_root, json_files={anchor_path.name: {
                        "schema_version": "remote_formal_authority.v1", "fingerprint": fingerprint,
                        "state_path": str(state_path), "state_sha256": None, "mirrors": {}}},
                        expected_absent=[anchor_path.name])
                except RevisionConflict:
                    pass  # Another host created the same authority; never replace it.
            path = self.private_root / ("formal-" + identity + ".json")
            with path.open("x", encoding="utf-8") as stream:
                json.dump(registration, stream)
            path.chmod(0o600)
            self.formal_registrations[identity] = {"fingerprint": fingerprint, "path": path,
                "registration": registration, "anchor_path": anchor_path, "state_path": state_path}
            self.formal_locks[identity] = threading.Lock()
            self.formal_checkpoint_locks[identity] = threading.Lock()
        return {"ok": True, "registration_id": identity}

    def _read_formal_anchor(self, registered: dict[str, Any]) -> tuple[dict[str, Any], str]:
        import remote_formal
        raw = remote_formal.regular_bytes(registered["anchor_path"], private=True)
        anchor = json.loads(raw)
        if (anchor.get("schema_version") != "remote_formal_authority.v1"
                or anchor.get("fingerprint") != registered["fingerprint"]
                or anchor.get("state_path") != str(registered["state_path"])):
            raise ValueError("formal authority checkpoint identity mismatch")
        return anchor, remote_formal.digest(raw)

    def _commit_formal_anchor(self, registered: dict[str, Any], anchor: dict[str, Any], *, expected_hash: str) -> None:
        from state_transaction import commit_transaction
        path = registered["anchor_path"]
        commit_transaction(self.formal_authority_root, json_files={path.name: anchor},
                           expected_hashes={path.name: expected_hash})

    def _repair_formal_mirrors(self, registered: dict[str, Any], anchor: dict[str, Any]) -> None:
        """Restore only authoritative checkpoint postimages, never adopt disk data."""
        import remote_formal
        from state_transaction import commit_transaction
        root = registered["state_path"].parent
        files = {}
        for name, entry in anchor.get("mirrors", {}).items():
            path = Path(name)
            relative = path.relative_to(root)
            raw = (remote_formal.canonical(entry["payload"]) if entry["kind"] == "receipt"
                   else (json.dumps(entry["payload"], indent=2, sort_keys=True) + "\n").encode())
            if remote_formal.digest(raw) != entry["sha256"]:
                raise ValueError("protected formal checkpoint is corrupt")
            try:
                current = remote_formal.regular_bytes(path, private=True)
            except FileNotFoundError:
                current = None
            if current != raw:
                files[relative] = raw
        if files:
            commit_transaction(root, binary_files=files)

    def formal_checkpoint(self, request: Mapping[str, Any], token: str) -> dict[str, Any]:
        """Scoped controller callback: protected postimage commits before its mirror."""
        import remote_formal
        if set(request) - {"operation", "path", "payload", "kind", "expected_sha256"}:
            raise ValueError("unrecognized formal checkpoint fields")
        with self.lock:
            capability = self.formal_step_capabilities.get(token)
            if capability is None:
                raise ValueError("formal step capability is invalid or expired")
            identity = capability["registration_id"]
            registered = self.formal_registrations[identity]
            checkpoint_lock = self.formal_checkpoint_locks[identity]
        # This is deliberately not the outer advance_lock held while waiting
        # for the controller: checkpoint callbacks run on another server thread.
        with checkpoint_lock:
            with self.lock:
                if self.formal_step_capabilities.get(token) is not capability:
                    raise ValueError("formal step capability expired before checkpoint admission")
            anchor, anchor_hash = self._read_formal_anchor(registered)
            if (anchor.get("in_flight") or {}).get("step_id") != capability["step_id"]:
                raise ValueError("formal checkpoint belongs to an obsolete step")
            registration = registered["registration"]
            bound = remote_formal.request_snapshot(Path(registration["pin"]["remote_request"]),
                registration["pin"]["remote_request_sha256"], Path(registration["project"]))
            path = Path(str(request.get("path") or ""))
            payload = request.get("payload")
            kind = request.get("kind")
            if not path.is_absolute() or not isinstance(payload, dict) or kind not in {"state", "intent", "receipt"}:
                raise ValueError("invalid formal checkpoint")
            root = registered["state_path"].parent
            path.relative_to(root)
            mirrors = anchor.setdefault("mirrors", {})
            previous = mirrors.get(str(path))
            if request.get("expected_sha256") != (previous["sha256"] if previous else None):
                raise ValueError("formal checkpoint preimage changed")
            prior_state = (mirrors.get(str(registered["state_path"])) or {}).get("payload") or {"attempts": {}}
            attempts = prior_state["attempts"]
            if kind == "state":
                if path != registered["state_path"] or payload.get("schema_version") != "remote_lean_state.v1" or not isinstance(payload.get("attempts"), dict):
                    raise ValueError("invalid registered formal state")
                proposed = payload["attempts"]
                if len(proposed) > bound["max_attempts"] or not set(attempts) <= set(proposed):
                    raise ValueError("formal checkpoint cannot reset attempt accounting")
                if len(set(proposed) - set(attempts)) > 1:
                    raise ValueError("a formal step can reserve only one new attempt")
                transitions = {"preparing": {"preparing", "submitting", "incomplete"},
                    "submitting": {"submitting", "submitted", "incomplete"},
                    "submitted": {"submitted", "passed", "incomplete"},
                    "passed": {"passed"}, "incomplete": {"incomplete"}}
                for key, row in proposed.items():
                    old = attempts.get(key)
                    if not isinstance(row, dict) or row.get("request_sha256") != registration["pin"]["remote_request_sha256"]:
                        raise ValueError("formal checkpoint request identity mismatch")
                    if re.fullmatch(r"[a-f0-9]{64}", str(row.get("input_digest"))) is None:
                        raise ValueError("formal checkpoint has no exact input digest")
                    if row == old:
                        continue
                    if row.get("purpose_key") != capability["purpose_key"]:
                        raise ValueError("formal checkpoint changes another host purpose")
                    if old is None:
                        if row.get("state") != "preparing" or re.fullmatch(r"[a-f0-9]{32}", str(row.get("attempt_id"))) is None:
                            raise ValueError("new formal attempt must begin preparing")
                        from compute_policy import require_execution_policy
                        require_execution_policy(Path(registration["run_dir"]), "kaggle",
                            expected=registration["compute_policy_binding"])
                        if row.get("compute_policy_binding") != registration["compute_policy_binding"]:
                            raise ValueError("formal attempt omits the registered compute policy binding")
                    else:
                        if row.get("state") not in transitions.get(old.get("state"), set()):
                            raise ValueError("formal checkpoint stage regression")
                        for field in ("attempt_id", "purpose_key", "input_digest", "request_sha256", "created_at", "original_submission_failure",
                                      "bundle_sha256", "submission_intent", "accepted_identity", "receipt_sha256", "compute_policy_binding"):
                            if field in old and row.get(field) != old[field]:
                                raise ValueError("formal checkpoint changes original attempt evidence")
                    expected_key = remote_formal.digest(remote_formal.canonical([row["request_sha256"], row["purpose_key"], row["input_digest"]]))
                    if key != expected_key:
                        raise ValueError("formal attempt key disagrees with bound input")
                    if row.get("state") == "passed":
                        receipt_path = root / "attempts" / row["attempt_id"] / "receipt.json"
                        if (mirrors.get(str(receipt_path)) or {}).get("sha256") != row.get("receipt_sha256"):
                            raise ValueError("formal completion lacks a protected receipt checkpoint")
                    if row.get("state") == "submitted":
                        intent = (mirrors.get(row.get("submission_intent")) or {}).get("payload") or {}
                        if intent.get("state") != "submitted" or intent.get("accepted_identity") != row.get("accepted_identity"):
                            raise ValueError("formal submission lacks a protected accepted-identity checkpoint")
            else:
                owners = [row for row in attempts.values() if row.get("purpose_key") == capability["purpose_key"]]
                if kind == "intent":
                    kaggle_driver, _ = remote_formal.kaggle_modules()
                    owners = [row for row in owners if path == kaggle_driver._submission_intent_path(root,
                        job_id="lean-" + row["attempt_id"], round_idx=0, chunk_idx=0)]
                    if len(owners) != 1 or owners[0].get("state") not in {"submitting", "submitted"}:
                        raise ValueError("formal submission checkpoint has no exact reserved owner")
                    if payload.get("schema") != "ai-agents-skills.kaggle-submission-intent.v2" or payload.get("bundle_sha256") != owners[0].get("bundle_sha256"):
                        raise ValueError("formal submission checkpoint identity mismatch")
                    if payload.get("state") not in {"acceptance_unknown", "submitted"}:
                        raise ValueError("unsupported formal submission stage")
                    expected_kernel = kaggle_driver.kernel_ref("lean-" + owners[0]["attempt_id"], 0, 0, username=bound["owner"])
                    if payload.get("kernel") != expected_kernel or payload.get("gpu") is not False or payload.get("enable_internet") is not bound["enable_internet"]:
                        raise ValueError("formal submission contradicts host execution policy")
                    if previous is None and payload.get("state") != "acceptance_unknown":
                        raise ValueError("formal submission must begin with an uncertainty fence")
                    if previous is None:
                        from compute_policy import require_execution_policy
                        require_execution_policy(Path(registration["run_dir"]), "kaggle",
                            expected=registration["compute_policy_binding"])
                    if previous is not None:
                        prior = previous["payload"]
                        for field in ("schema", "attempt_id", "kernel", "bundle_sha256", "gpu", "enable_internet", "submitted_source", "provider_response"):
                            if field in prior and payload.get(field) != prior[field]:
                                raise ValueError("formal submission checkpoint changes bound identity")
                        if prior.get("state") == "submitted" and payload.get("state") != "submitted":
                            raise ValueError("formal submission stage cannot regress")
                        prior_events = prior.get("events") or []
                        if not isinstance(payload.get("events"), list) or payload["events"][:len(prior_events)] != prior_events:
                            raise ValueError("formal submission history cannot be rewritten")
                    if payload.get("state") == "submitted" and kaggle_driver._bind_saved_response(payload) != payload.get("accepted_identity"):
                        raise ValueError("formal accepted identity differs from its saved provider response")
                else:
                    owners = [row for row in owners if path == root / "attempts" / row["attempt_id"] / "receipt.json"]
                    if len(owners) != 1 or owners[0].get("state") != "submitted" or payload.get("attempt_id") != owners[0]["attempt_id"]:
                        raise ValueError("formal receipt checkpoint has no exact submitted owner")
            raw = remote_formal.canonical(payload) if kind == "receipt" else (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("formal checkpoint exceeds its bounded metadata size")
            if any(value and value in raw.decode() for value in (*self.providers.values(), *self.compute.values(), token)):
                raise ValueError("formal checkpoint contains credential material")
            mirrors[str(path)] = {"kind": kind, "payload": payload, "sha256": remote_formal.digest(raw)}
            if kind == "state":
                anchor["state_sha256"] = remote_formal.digest(raw)
            self._commit_formal_anchor(registered, anchor, expected_hash=anchor_hash)
            self._repair_formal_mirrors(registered, anchor)
            return {"ok": True, "sha256": remote_formal.digest(raw)}

    def _formal_authority_key(self, *, create: bool) -> bytes:
        """Durable host key; its location is selected by this runtime, not data."""
        root = self.formal_authority_root
        new_root = False
        if create:
            try:
                root.mkdir(mode=0o700)
                new_root = True
            except FileExistsError:
                pass
        if root.is_symlink() or any(parent.is_symlink() for parent in root.parents):
            raise ValueError("formal host authority traverses a link")
        info = root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("formal host authority directory is not private")
        path = root / "remote-formal.key"
        if create:
            if not new_root and not path.exists() and any(root.iterdir()):
                raise ValueError("formal host authority key is missing; explicit recovery required")
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(secrets.token_bytes(32))
                    stream.flush()
                    os.fsync(stream.fileno())
        import remote_formal
        key = remote_formal.regular_bytes(path, private=True, limit=32)
        if len(key) != 32:
            raise ValueError("invalid formal host authority key")
        return key

    def _sign_formal_admission(self, result: dict[str, Any], registration: dict[str, Any], purpose_key: str) -> None:
        import remote_formal
        path = Path(result["receipt_path"])
        receipt = remote_formal.inspect_receipt_content(path, result["receipt_sha256"], project=Path(registration["project"]))
        if receipt["request_sha256"] != registration["pin"]["remote_request_sha256"]:
            raise ValueError("remote admission does not match the host registration")
        if receipt.get("purpose_key") != purpose_key or receipt.get("run_dir") != registration["run_dir"]:
            raise ValueError("remote admission does not match its host purpose/run")
        key = self._formal_authority_key(create=True)
        message = {"schema_version": "remote_lean_authentication.v1", "receipt_sha256": result["receipt_sha256"],
            "receipt_path": str(path.absolute()), "project": str(Path(registration["project"]).resolve()),
            "authority_id": hashlib.sha256(key).hexdigest()}
        message["hmac_sha256"] = hmac.new(key, remote_formal.canonical(message), hashlib.sha256).hexdigest()
        self.panel._secure_write_text(path.with_suffix(".host-auth.json"), json.dumps(message, sort_keys=True) + "\n")

    def formal_validate(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Read-only master-capability verification of durable host admission."""
        import remote_formal
        if set(request) - {"operation", "receipt_path", "receipt_sha256", "project"}:
            raise ValueError("unrecognized formal validation fields")
        path = Path(str(request.get("receipt_path") or ""))
        project = Path(str(request.get("project") or ""))
        if not path.is_absolute() or not project.is_absolute() or path.resolve().is_relative_to(project.resolve()):
            raise ValueError("invalid host receipt location")
        key = self._formal_authority_key(create=False)
        document = json.loads(remote_formal.regular_bytes(path.with_suffix(".host-auth.json"), private=True, limit=4096))
        signature = document.pop("hmac_sha256", None)
        expected = {"schema_version": "remote_lean_authentication.v1", "receipt_sha256": request.get("receipt_sha256"),
            "receipt_path": str(path.absolute()), "project": str(project.resolve()), "authority_id": hashlib.sha256(key).hexdigest()}
        if document != expected or not isinstance(signature, str) or not hmac.compare_digest(signature,
                hmac.new(key, remote_formal.canonical(document), hashlib.sha256).hexdigest()):
            raise ValueError("receipt was not admitted by this host authority")
        if remote_formal.digest(remote_formal.regular_bytes(path, private=True)) != document["receipt_sha256"]:
            raise ValueError("authenticated receipt bytes changed")
        return {"ok": True, "authenticated": True, "authority_id": document["authority_id"]}

    def formal_advance(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Run only the fixed formal controller with a Kaggle-only projection."""
        import remote_formal
        from compute_policy import require_execution_policy
        if set(request) - {"operation", "registration_id", "purpose_key"}:
            raise ValueError("unrecognized formal advance fields")
        identity = str(request.get("registration_id") or "")
        purpose = request.get("purpose_key")
        if not isinstance(purpose, str) or re.fullmatch(r"[a-f0-9]{64}", purpose) is None:
            raise ValueError("invalid host formal purpose identity")
        with self.lock:
            registered = self.formal_registrations.get(identity)
            advance_lock = self.formal_locks.get(identity)
        if registered is None or advance_lock is None:
            raise ValueError("host formal registration is absent")
        with advance_lock:
            registration = registered["registration"]
            pin = registration["pin"]
            require_execution_policy(Path(registration["run_dir"]), "kaggle",
                expected=registration["compute_policy_binding"])
            remaining = self.panel._remaining_panel_wall_budget(Path(registration["run_dir"]))
            if remaining == 0:
                return {"ok": True, "result": {"status": "verification_pending", "execution_backend": "kaggle-cpu",
                    "detail": "host_wall_budget_exhausted"}}
            controller_timeout = min(900, remaining) if remaining is not None else 900
            if hashlib.sha256(self._formal_authority_key(create=False)).hexdigest() != registration["authority_id"]:
                raise ValueError("registered formal authority changed")
            remote_formal.request_snapshot(Path(pin["remote_request"]), pin["remote_request_sha256"], Path(registration["project"]))
            if json.loads(remote_formal.regular_bytes(registered["path"], private=True)) != registration:
                raise ValueError("broker formal registration changed")
            from state_transaction import recover_transactions
            recover_transactions(self.formal_authority_root)
            anchor, anchor_hash = self._read_formal_anchor(registered)
            if anchor.get("cleanup_unverified"):
                return {"ok": True, "result": {"status": "incomplete", "execution_backend": "kaggle-cpu",
                    "detail": "resource_cleanup_unverified", "reconciliation_required": True}}
            if anchor.get("in_flight") and anchor["in_flight"].get("resource_scope"):
                owner = anchor["in_flight"].get("broker_pid")
                if type(owner) is not int or owner <= 0:
                    raise ValueError("interrupted formal controller has no host owner")
                if owner != os.getpid():
                    try:
                        os.kill(owner, 0)
                    except ProcessLookupError:
                        pass
                    else:
                        return {"ok": True, "result": {"status": "verification_pending",
                            "execution_backend": "kaggle-cpu", "detail": "controller_active_elsewhere"}}
                if self.panel.cleanup_resource_scope(anchor["in_flight"]["resource_scope"]) is not None:
                    raise ValueError("interrupted formal controller cleanup is unverified")
            self._repair_formal_mirrors(registered, anchor)
            if anchor.get("in_flight"):
                anchor["recovered_step"] = anchor.pop("in_flight")
                self._commit_formal_anchor(registered, anchor, expected_hash=anchor_hash)
                anchor, anchor_hash = self._read_formal_anchor(registered)
            state_path = registered["state_path"]
            current_hash = remote_formal.digest(remote_formal.regular_bytes(state_path, private=True)) if state_path.exists() else None
            if (anchor.get("fingerprint") != registered["fingerprint"] or anchor.get("state_path") != str(state_path)
                    or anchor.get("state_sha256") != current_hash):
                raise ValueError("remote state changed outside its host-admitted controller step")
            child_registration = {**registration, "expected_state_sha256": current_hash}
            child_path_file = self.private_root / ("formal-step-" + secrets.token_hex(16) + ".json")
            self.panel._secure_write_text(child_path_file, json.dumps(child_registration))
            script = self.skill_dir / "remote_formal.py"
            if script.is_symlink() or not _owner_controlled_regular(script.stat()):
                raise ValueError("exact host formal controller is unavailable")
            interpreter = _attested_interpreter()
            argv0, child_path = _skill_python_argv0(interpreter)
            env = _safe_environment(os.environ)
            env["PATH"] = child_path
            env["HOME"] = str(self.private_root)
            for key in COMPUTE_KEY_MAP["kaggle"]:
                if self.compute.get(key):
                    env[key] = self.compute[key]
            # The controller can read selected host files and perform Kaggle
            # calls, but cannot see signing authority or host launch-control
            # sockets. Its broker socket grants only this step's checkpoints.
            command = self.panel.trusted_local_containment_command(
                [argv0, str(script), str(child_path_file), purpose], cwd=self.private_root)
            command, controller_limits, controller_scope = self.panel.resource_limited_command(
                command, controller_timeout, role="panel")
            env = self.panel.resource_control_environment(env)
            # This fence survives a parent crash. Never adopt an unacknowledged
            # post-image merely because its worker-authored hashes agree.
            anchor["in_flight"] = {"purpose_key": purpose, "step_id": secrets.token_hex(16), "broker_pid": os.getpid(),
                                   "resource_scope": controller_scope, "resource_limits": controller_limits}
            self._commit_formal_anchor(registered, anchor, expected_hash=anchor_hash)
            step_token = secrets.token_urlsafe(32)
            with self.lock:
                self.formal_step_capabilities[step_token] = {"registration_id": identity,
                    "purpose_key": purpose, "step_id": anchor["in_flight"]["step_id"]}
            env[BROKER_SOCKET_ENV] = self.socket_path
            env[BROKER_TOKEN_ENV] = step_token
            try:
                require_execution_policy(Path(registration["run_dir"]), "kaggle",
                    expected=registration["compute_policy_binding"])
                captured = self.panel._default_runner(command, env, str(self.private_root), controller_timeout,
                    output_limit_bytes=1024 * 1024, scope_unit=controller_scope)
            finally:
                with self.formal_checkpoint_locks[identity]:
                    with self.lock:
                        self.formal_step_capabilities.pop(step_token, None)
            rc, stdout, stderr, evidence = self._filter_process_evidence(*captured, captured.process_evidence,
                extra_secrets=(step_token,))
            anchor, anchor_hash = self._read_formal_anchor(registered)
            if rc != 0:
                anchor.setdefault("original_failures", []).append({"returncode": rc, "process_evidence": evidence})
                anchor["cleanup_unverified"] = bool(evidence and evidence.get("cleanup_error"))
                if not anchor["cleanup_unverified"]:
                    anchor["interrupted_step"] = anchor.pop("in_flight")
                self._commit_formal_anchor(registered, anchor, expected_hash=anchor_hash)
                return {"ok": True, "result": {"status": "verification_pending", "execution_backend": "kaggle-cpu",
                    "detail": "controller_interrupted_resume_status_first"}}
            result = json.loads(stdout)
            if not isinstance(result, dict):
                raise ValueError("invalid host formal controller result")
            after_hash = remote_formal.digest(remote_formal.regular_bytes(state_path, private=True)) if state_path.exists() else None
            if result.pop("controller_state_sha256", "missing") != after_hash or anchor["state_sha256"] != after_hash:
                raise ValueError("controller result does not bind its final state")
            anchor.pop("in_flight", None)
            self._commit_formal_anchor(registered, anchor, expected_hash=anchor_hash)
            if result.get("status") == "passed":
                self._sign_formal_admission(result, registration, purpose)
            return {"ok": True, "result": result}

    def _block_secret_output(self, rc: int, stdout: str, stderr: str) -> tuple[int, str, str]:
        values = tuple(value for value in (*self.providers.values(), *self.compute.values()) if value)
        if any(value in stdout or value in stderr for value in values):
            return 126, "", "broker blocked provider output containing credential material\n"
        return rc, stdout, stderr

    def primary(self, request: Mapping[str, Any]) -> dict[str, Any]:
        provider = str(request.get("provider") or "").strip().lower().replace("_", "-")
        run_args = request.get("run_args")
        if (
            not isinstance(run_args, list)
            or not run_args
            or not all(isinstance(item, str) for item in run_args)
            or bool(request.get("use_shell"))
        ):
            raise ValueError("invalid primary command")
        supplied_env = request.get("child_env")
        if not isinstance(supplied_env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in supplied_env.items()):
            raise ValueError("invalid primary environment")
        forbidden = (
            PROVIDER_KEYS
            | COMPUTE_PROJECTION_KEYS
            | PROVIDER_CONFIG_ENV
            | SECRET_POINTERS
            | {BROKER_TOKEN_ENV}
        )
        if forbidden.intersection(supplied_env):
            raise ValueError("orchestrator attempted to supply credential authority")
        cwd = Path(str(request.get("cwd") or ""))
        if not cwd.is_absolute() or not cwd.is_dir():
            raise ValueError("invalid primary working directory")
        supplied_attestation = request.get("executable_attestation")
        if not isinstance(supplied_attestation, dict):
            raise ValueError("brokered provider requires executable attestation")
        attestation = self.runtime.revalidate_provider_executable_attestation(
            supplied_attestation,
            forbidden_roots=(cwd,),
        )
        if (
            str(attestation.get("provider") or "") != provider
            or str(run_args[0]) != str(attestation.get("executable_path") or "")
        ):
            raise ValueError("primary command is not the selected attested provider")
        lanes_raw = request.get("compute_lanes") or []
        if not isinstance(lanes_raw, list) or not all(isinstance(item, str) for item in lanes_raw):
            raise ValueError("invalid compute lane policy")
        lanes = frozenset(lanes_raw) & COMPUTE_KEY_MAP.keys()
        child_env = dict(supplied_env)
        child_env.update(self._secret_projection(provider))
        child_home = self.private_root / f"provider-home-{secrets.token_hex(12)}"
        child_home.mkdir(mode=0o700)
        config_environment, config_mounts = self._prepare_config_projection(
            provider, child_home
        )
        child_env.update(config_environment)
        child_env["HOME"] = str(child_home)
        child_env["AAS_ARL_BROKER_STRICT_FS"] = "1"
        child_env["AAS_ARL_BROKER_DEPENDENCY_ROOT"] = str(
            attestation.get("dependency_root") or ""
        )
        child_env["AAS_ARL_BROKER_CONFIG_MOUNTS"] = json.dumps(
            config_mounts, separators=(",", ":")
        )
        capability = ""
        if lanes:
            capability = secrets.token_urlsafe(32)
            with self.lock:
                self.capabilities[capability] = (lanes, cwd.resolve())
            child_env[BROKER_SOCKET_ENV] = self.socket_path
            child_env[BROKER_TOKEN_ENV] = capability
            child_env[BROKER_PROXY_ENV] = str(self.proxy)
        output = io.StringIO()
        metadata: dict[str, Any] = {}
        try:
            rc, timed_out, cleanup_error = self.runtime.run_primary_subprocess(
                run_args,
                use_shell=bool(request.get("use_shell")),
                child_env=child_env,
                cwd=cwd,
                timeout_s=int(request.get("timeout_s") or 1),
                output=output,
                provider=provider,
                enforce_mode=bool(request.get("enforce_mode")),
                trusted_local=bool(request.get("trusted_local")),
                stdin_text=str(request["stdin_text"]) if request.get("stdin_text") is not None else None,
                resource_metadata=metadata,
            )
        finally:
            if capability:
                with self.lock:
                    self.capabilities.pop(capability, None)
        rc, stdout, stderr, evidence = self._filter_process_evidence(
            int(rc), output.getvalue(), "", metadata.pop("process_evidence", None),
            extra_secrets=(capability,))
        if evidence is not None:
            metadata["process_evidence"] = evidence
        return {"ok": True, "returncode": rc, "timed_out": bool(timed_out), "cleanup_error": cleanup_error, "stdout": stdout, "stderr": stderr, "resource_metadata": metadata}

    def panel_run(self, request: Mapping[str, Any]) -> dict[str, Any]:
        provider = str(request.get("provider") or "").strip().lower().replace("_", "-")
        command = request.get("command")
        supplied_env = request.get("environment")
        if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
            raise ValueError("invalid panel command")
        if not isinstance(supplied_env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in supplied_env.items()):
            raise ValueError("invalid panel environment")
        if (
            PROVIDER_KEYS
            | COMPUTE_PROJECTION_KEYS
            | PROVIDER_CONFIG_ENV
            | SECRET_POINTERS
            | {BROKER_TOKEN_ENV}
        ).intersection(supplied_env):
            raise ValueError("panel orchestrator attempted to supply credential authority")
        supplied_attestation = request.get("executable_attestation")
        if not isinstance(supplied_attestation, dict):
            raise ValueError("brokered panel requires executable attestation")
        attestation = self.panel.revalidate_provider_executable_attestation(
            supplied_attestation,
            forbidden_roots=(Path(str(request.get("cwd") or "")),),
        )
        if (
            str(attestation.get("provider") or "") != provider
            or command[0] != str(attestation.get("executable_path") or "")
        ):
            raise ValueError("panel command is not the selected attested provider")
        env = dict(supplied_env)
        env.update(self._secret_projection(provider))
        child_home = self.private_root / f"panel-home-{secrets.token_hex(12)}"
        child_home.mkdir(mode=0o700)
        config_environment, config_mounts = self._prepare_config_projection(
            provider, child_home
        )
        env.update(config_environment)
        env["HOME"] = str(child_home)
        bound_command = self.panel.interpreter_bound_provider_command(command)
        execution_command = self.panel.brokered_provider_containment_command(
            bound_command,
            cwd=Path(str(request.get("cwd") or "")),
            dependency_root=Path(str(attestation["dependency_root"])),
            synthetic_home=child_home,
            config_mounts=config_mounts,
            broker_socket=None,
        )
        execution_command, limits, scope = self.panel.resource_limited_command(
            execution_command,
            int(request.get("timeout_s") or 1),
            role="panel",
        )
        env = self.panel.resource_control_environment(env)
        captured_result = self.panel._default_runner(
            execution_command,
            env,
            str(request.get("cwd") or ""),
            int(request.get("timeout_s") or 1),
            stdin_text=str(request["stdin_text"]) if request.get("stdin_text") is not None else None,
            output_limit_bytes=int(limits["output_max_bytes"]),
            scope_unit=scope,
        )
        rc, stdout, stderr = captured_result
        rc, stdout, stderr, evidence = self._filter_process_evidence(
            int(rc), str(stdout), str(stderr), getattr(captured_result, "process_evidence", None)
        )
        return {"ok": True, "returncode": rc, "stdout": stdout, "stderr": stderr,
                "process_evidence": evidence, "resource_scope": scope,
                "resource_limits": limits}

    def compute_run(self, request: Mapping[str, Any], token: str) -> dict[str, Any]:
        with self.lock:
            capability = self.capabilities.get(token)
        if capability is None:
            raise ValueError("compute capability is invalid or expired")
        allowed_lanes, allowed_root = capability
        lane = str(request.get("lane") or "").strip().lower()
        if lane not in allowed_lanes or lane not in COMPUTE_KEY_MAP:
            raise ValueError("compute lane is not authorized")
        arguments = request.get("arguments")
        if not isinstance(arguments, list) or not all(isinstance(item, str) and "\x00" not in item for item in arguments):
            raise ValueError("invalid compute arguments")
        cwd = Path(str(request.get("cwd") or ""))
        if not cwd.is_absolute() or not cwd.is_dir() or not _within(cwd.resolve(), allowed_root):
            raise ValueError("compute working directory is outside the authorized project")
        driver = _skills_root(self.runtime_root) / f"{lane}-research-compute" / f"{lane}_research_compute.py"
        if not driver.is_file() or driver.is_symlink():
            raise ValueError("exact compute driver is unavailable")
        info = driver.stat()
        if not _owner_controlled_regular(info):
            raise ValueError("exact compute driver is untrusted")
        interpreter = _attested_interpreter()
        argv0, child_path = _skill_python_argv0(interpreter)
        env = _safe_environment(os.environ)
        env["PATH"] = child_path
        prefix = os.environ.get("AAS_RUNTIME_PYTHON_PREFIX")
        if prefix:
            env["AAS_RUNTIME_PYTHON_PREFIX"] = prefix
        else:
            env.pop("AAS_RUNTIME_PYTHON_PREFIX", None)
        env["CODEX_CALLER_CWD"] = str(cwd)
        compute_home = self.private_root / f"compute-home-{lane}-{secrets.token_hex(12)}"
        compute_home.mkdir(mode=0o700)
        env["HOME"] = str(compute_home)
        lane_values = dict(self.compute)
        if lane == "modal":
            lane_values.update(_load_modal_authority())
        for key in COMPUTE_KEY_MAP[lane]:
            if lane_values.get(key):
                env[key] = lane_values[key]
        completed = subprocess.run(
            [argv0, str(driver), *arguments],
            executable=str(interpreter),
            cwd=str(cwd), env=env, text=True, encoding="utf-8", errors="replace", capture_output=True,
            timeout=86_400, check=False,
        )
        rc, stdout, stderr = self._block_secret_output(completed.returncode, completed.stdout, completed.stderr)
        if any(
            value and (value in stdout or value in stderr)
            for value in lane_values.values()
        ):
            rc, stdout, stderr = (
                126,
                "",
                "broker blocked compute output containing credential material\n",
            )
        return {"ok": True, "returncode": rc, "stdout": stdout, "stderr": stderr}


class BrokerHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            length = struct.unpack("!I", _recv_exact(self.rfile, 4))[0]
            if length > MAX_MESSAGE_BYTES:
                raise ValueError("broker request is oversized")
            request = json.loads(_recv_exact(self.rfile, length).decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("broker request must be an object")
            token = str(request.pop("token", ""))
            operation = str(request.get("operation") or "")
            state: CredentialState = self.server.credential_state  # type: ignore[attr-defined]
            if operation == "compute":
                response = state.compute_run(request, token)
            elif operation == "formal_checkpoint":
                response = state.formal_checkpoint(request, token)
            else:
                if not secrets.compare_digest(token, state.parent_token):
                    raise ValueError("broker capability is invalid")
                if operation == "primary":
                    response = state.primary(request)
                elif operation == "panel":
                    response = state.panel_run(request)
                elif operation == "formal_register":
                    response = state.formal_register(request)
                elif operation == "formal_advance":
                    response = state.formal_advance(request)
                elif operation == "formal_validate":
                    response = state.formal_validate(request)
                else:
                    raise ValueError("unknown broker operation")
        except Exception as exc:  # noqa: BLE001 - broker boundary returns no traceback
            response = {"ok": False, "error": str(exc)[:1000]}
        encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_MESSAGE_BYTES:
            encoded = b'{"ok":false,"error":"broker response is oversized"}'
        self.wfile.write(struct.pack("!I", len(encoded)) + encoded)


class BrokerServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", required=True)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    arguments = list(args.arguments)
    if arguments and arguments[0] == "--":
        arguments = arguments[1:]
    runtime_root = _runtime_root()
    loader_path = runtime_root / "runners" / "load_secret_env.py"
    if not loader_path.is_file():
        loader_path = runtime_root / "load_secret_env.py"
    loader = _load_module_file(loader_path, "aas_exact_secret_loader")
    secret_error = getattr(loader, "SecretEnvError", ValueError)
    try:
        providers = loader.load_pointer_secret_env(PROVIDER_POINTER, allowed_keys=PROVIDER_KEYS)
        compute = loader.load_pointer_secret_env(COMPUTE_POINTER, allowed_keys=COMPUTE_KEYS)
    except (secret_error, OSError) as exc:
        # Fail closed with a diagnostic instead of an uncaught traceback when a
        # pointed secret file does not satisfy the strict schema. Loader errors
        # name keys and line numbers, never values.
        print(f"arl-credential-broker: secret env rejected: {exc}", file=sys.stderr)
        return 2
    provider_config = {
        key: str(os.environ.get(key) or "")
        for key in PROVIDER_CONFIG_ENV
        if str(os.environ.get(key) or "")
    }
    for key in PROVIDER_KEYS | COMPUTE_PROJECTION_KEYS | PROVIDER_CONFIG_ENV | SECRET_POINTERS:
        os.environ.pop(key, None)
    _set_nondumpable()
    socket_dir = Path(tempfile.mkdtemp(prefix="aas-arl-broker-"))
    os.chmod(socket_dir, 0o700)
    socket_path = str(socket_dir / "broker.sock")
    parent_token = secrets.token_urlsafe(32)
    state = CredentialState(
        runtime_root,
        providers,
        compute,
        provider_config,
        parent_token,
        socket_path,
        socket_dir,
    )
    server = BrokerServer(socket_path, BrokerHandler)
    server.credential_state = state  # type: ignore[attr-defined]
    os.chmod(socket_path, 0o600)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    child_env = _safe_environment(os.environ)
    child_env[BROKER_SOCKET_ENV] = socket_path
    child_env[BROKER_TOKEN_ENV] = parent_token
    child_env[BROKER_PROXY_ENV] = str(state.proxy)
    entry = str(args.entry)
    pass_fds = _fd_numbers(child_env)
    try:
        completed = subprocess.run(
            [entry, *arguments], env=child_env, pass_fds=pass_fds, check=False
        )
        return int(completed.returncode)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        shutil.rmtree(socket_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
