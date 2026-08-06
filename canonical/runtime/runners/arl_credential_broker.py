#!/usr/bin/env python3
"""Exact-generation credential broker for autonomous-research-loop.

Only this root-owned process parses provider/compute authorities.  The ARL
orchestrator receives an opaque local capability and remains credential-blind;
each provider or compute subprocess receives only its selected projection.
"""

from __future__ import annotations

import argparse
import ctypes
import io
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
import tomllib
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
    {"HCLOUD_TOKEN", "HCLOUD_SSH_KEYS", "KAGGLE_API_TOKEN", "KAGGLE_CONFIG_DIR"}
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
        "AAS_ALLOW_RAW_NOTIFY_CMD",
    }
)


def _runtime_root() -> Path:
    configured = Path(os.environ.get("AAS_RUNTIME_ROOT", ""))
    if configured.is_absolute() and configured.is_dir():
        return configured.resolve()
    return Path(__file__).resolve().parents[1]


def _load_module_file(path: Path, name: str) -> Any:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or before.st_nlink != 1
        ):
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
        document = tomllib.loads(bytes(payload).decode("utf-8"))
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
        self.capabilities: dict[str, tuple[frozenset[str], Path]] = {}
        self.lock = threading.Lock()
        self.skill_dir = runtime_root / "skills" / "autonomous-research-loop-runtime"
        sys.path.insert(0, str(self.skill_dir))
        import autonomous_research_loop_runtime as runtime  # type: ignore
        import panel_parent  # type: ignore
        self.runtime = runtime
        self.panel = panel_parent
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
        mounts[str(target)] = str(source)
        return projected, mounts

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
                run_dir=Path(str(request["run_dir"])) if request.get("run_dir") else None,
                evidence_dir=Path(str(request["evidence_dir"])) if request.get("evidence_dir") else None,
                executable_attestation=attestation,
                stdin_text=str(request["stdin_text"]) if request.get("stdin_text") is not None else None,
                resource_metadata=metadata,
            )
        finally:
            if capability:
                with self.lock:
                    self.capabilities.pop(capability, None)
        rc, stdout, stderr = self._block_secret_output(int(rc), output.getvalue(), "")
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
        rc, stdout, stderr = self.panel._default_runner(
            execution_command,
            env,
            str(request.get("cwd") or ""),
            int(request.get("timeout_s") or 1),
            stdin_text=str(request["stdin_text"]) if request.get("stdin_text") is not None else None,
            output_limit_bytes=int(limits["output_max_bytes"]),
            scope_unit=scope,
        )
        rc, stdout, stderr = self._block_secret_output(int(rc), str(stdout), str(stderr))
        return {"ok": True, "returncode": rc, "stdout": stdout, "stderr": stderr}

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
        driver = self.runtime_root / "skills" / f"{lane}-research-compute" / f"{lane}_research_compute.py"
        if not driver.is_file() or driver.is_symlink():
            raise ValueError("exact compute driver is unavailable")
        info = driver.stat()
        if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH) or info.st_nlink != 1:
            raise ValueError("exact compute driver is untrusted")
        env = _safe_environment(os.environ)
        env["PATH"] = "/usr/bin:/bin"
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
            ["/usr/bin/python3", str(driver), *arguments],
            cwd=str(cwd), env=env, text=True, capture_output=True,
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
            else:
                if not secrets.compare_digest(token, state.parent_token):
                    raise ValueError("broker capability is invalid")
                if operation == "primary":
                    response = state.primary(request)
                elif operation == "panel":
                    response = state.panel_run(request)
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
    providers = loader.load_pointer_secret_env(PROVIDER_POINTER, allowed_keys=PROVIDER_KEYS)
    compute = loader.load_pointer_secret_env(COMPUTE_POINTER, allowed_keys=COMPUTE_KEYS)
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
