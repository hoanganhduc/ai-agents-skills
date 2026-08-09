#!/usr/bin/env python3
"""Force-loop CLI: bootstrap, apply-defaults, start, stop, replace, status, drain, smoke.

Cross-platform default path for scripted unattended ARL drive.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets as random_secrets
import shutil
import stat
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

PACK_DIR = Path(__file__).resolve().parent
RUNTIME_PARENT = PACK_DIR.parent  # autonomous-research-loop-runtime/

if str(PACK_DIR) not in sys.path:
    sys.path.insert(0, str(PACK_DIR))

# Local imports (same directory)
from apply_force_loop_defaults import (  # noqa: E402
    apply_compute_policy,
    apply_defaults,
    verify_effective,
)
from force_loop_process import (  # noqa: E402
    bind_child_command,
    build_drive_command,
    build_supervisor_command,
    run_foreground,
    run_posix_detach,
    select_backend,
    status_snapshot,
    stop_loop_processes,
    systemd_user_available,
)
from load_loop_env import (  # noqa: E402
    COMPUTE_LANE_KEYS,
    EnvLoadError,
    apply_to_environ,
    load_env_file,
)


COMPUTE_SECRETS_ENV = "AAS_COMPUTE_SECRETS_FILE"
POLICY_FILE_ENV = "AAS_FORCE_LOOP_POLICY_FILE"
SECRETS_PROJECTION_ENV = "AAS_FORCE_LOOP_SECRETS_PROJECTED"
COMPUTE_SECRET_KEYS = frozenset(
    {
        "HCLOUD_TOKEN",
        "HCLOUD_SSH_KEYS",
        "KAGGLE_API_TOKEN",
        "KAGGLE_CONFIG_DIR",
        "MODAL_TOKEN_ID",
        "MODAL_TOKEN_SECRET",
    }
)
PROVIDER_SECRETS_ENV = "AAS_PROVIDER_SECRETS_FILE"
PROVIDER_SECRET_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "COPILOT_GITHUB_TOKEN",
        "COPILOT_PROVIDER_API_KEY",
        "COPILOT_PROVIDER_BEARER_TOKEN",
        "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GOOGLE_API_KEY",
        "GROK_API_KEY",
        "KIMI_API_KEY",
        "MOONSHOT_API_KEY",
        "OPENAI_API_KEY",
        "OPENCODE_API_KEY",
        "XAI_API_KEY",
    }
)
PROVIDER_KEY_MAP: dict[str, frozenset[str]] = {
    "anthropic": frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}),
    "antigravity": frozenset({"GEMINI_API_KEY", "GOOGLE_API_KEY"}),
    "claude": frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}),
    "codex": frozenset({"OPENAI_API_KEY"}),
    "copilot": frozenset({"COPILOT_GITHUB_TOKEN", "COPILOT_PROVIDER_API_KEY", "COPILOT_PROVIDER_BEARER_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"}),
    "deepseek": frozenset({"DEEPSEEK_API_KEY"}),
    "gemini": frozenset({"GEMINI_API_KEY", "GOOGLE_API_KEY"}),
    "google": frozenset({"GEMINI_API_KEY", "GOOGLE_API_KEY"}),
    "grok": frozenset({"GROK_API_KEY", "XAI_API_KEY"}),
    "xai": frozenset({"GROK_API_KEY", "XAI_API_KEY"}),
    "kimi": frozenset({"KIMI_API_KEY", "MOONSHOT_API_KEY"}),
    "moonshot": frozenset({"KIMI_API_KEY", "MOONSHOT_API_KEY"}),
    "openai": frozenset({"OPENAI_API_KEY"}),
    "opencode": frozenset({"OPENCODE_API_KEY"}),
}
BASE_ENV_KEYS = frozenset(
    {
        "AAS_RUNTIME_PYTHON", "AAS_RUNTIME_ROOT", "AAS_RUNTIME_WORKSPACE",
        "AAS_ARL_BROKER_SOCKET", "AAS_ARL_BROKER_TOKEN", "AAS_ARL_COMPUTE_PROXY",
        "AAS_REMOTE_STRICT_NOTIFY_CHANNEL",
        "AUTOLOOP_DISABLE", "CLAUDE_CONFIG_DIR", "CODEWHALE_HOME", "CODEX_HOME",
        "GEMINI_CONFIG_DIR", "GROK_CONFIG_DIR", "HOME", "KIMI_CONFIG_DIR",
        "LANG", "LC_ALL", "LOGNAME", "OPENCODE_CONFIG_DIR", "TZ", "USER",
        "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR",
    }
)
STRICT_NOTIFY_CHANNEL_ENV = "AAS_REMOTE_STRICT_NOTIFY_CHANNEL"
STRICT_NOTIFY_CHANNELS = frozenset({"zulip", "telegram"})
SYSTEMD_ENV_MAX_BYTES = 262_144
SYSTEMD_ENV_DIR_NAME = "aas-force-loop"
SYSTEMD_ENV_FIXED_KEYS = frozenset(
    {
        "AAS_ARL_BROKER_SOCKET",
        "AAS_ARL_BROKER_TOKEN",
        "AAS_ARL_COMPUTE_PROXY",
        "AAS_REMOTE_STRICT_NOTIFY_CHANNEL",
        "AAS_RUNTIME_PYTHON",
        "AAS_RUNTIME_ROOT",
        "AAS_RUNTIME_WORKSPACE",
        "AUTOLOOP_DISABLE",
        "CLAUDE_CONFIG_DIR",
        "CODEWHALE_HOME",
        "CODEX_HOME",
        "DBUS_SESSION_BUS_ADDRESS",
        "DRIVE_EXTRA_ARGS",
        "FAILOVER_JSON",
        "GEMINI_CONFIG_DIR",
        "GROK_CONFIG_DIR",
        "HOME",
        "KIMI_CONFIG_DIR",
        "LANG",
        "LC_ALL",
        "LOGNAME",
        "LOOP_DIR",
        "OPENCODE_CONFIG_DIR",
        "PATH",
        "PROJECT_ROOT",
        "PYTHONPATH",
        "RUNTIME_PY",
        "SHELL",
        "SUPERVISOR",
        "SYNC_PANEL_PY",
        "TZ",
        "USER",
        "VIRTUAL_ENV",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
    }
)
SYSTEMD_ENV_PREFIXES = ("AAS_AUTOLOOP_",)
SYSTEMD_CLIENT_ENV_KEYS = frozenset(
    {
        "DBUS_SESSION_BUS_ADDRESS",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOGNAME",
        "PATH",
        "TZ",
        "USER",
        "XDG_RUNTIME_DIR",
    }
)
SYSTEMD_CLIENT_ENV_PREFIXES = ("LC_", "SYSTEMD_")
SYSTEMD_SAFE_PATH = re.compile(r"/[A-Za-z0-9_./-]+\Z")
# DRIVE_EXTRA_ARGS crosses into the supervisor as one space-joined string that
# bash re-splits and expands, so only word-splitting-stable characters pass.
UNSAFE_DRIVE_EXTRA = re.compile(r"[^A-Za-z0-9_@%+=:,./-]")


def _python() -> str:
    return os.environ.get("AAS_RUNTIME_PYTHON") or sys.executable


def _runtime_py() -> Path:
    return RUNTIME_PARENT / "autonomous_research_loop_runtime.py"


def _run_runtime(args: list[str], *, env: dict[str, str] | None = None) -> int:
    cmd = [_python(), str(_runtime_py()), *args]
    proc = subprocess.run(cmd, env=env or os.environ.copy(), check=False)
    return int(proc.returncode)


def _run_runtime_json(
    args: list[str], *, env: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    """Run a runtime command and return its exit status with its JSON report.

    The report is advisory: an unparsable payload still yields the exit status,
    so a caller never fails only because the runtime changed its output shape.
    """

    cmd = [_python(), str(_runtime_py()), *args]
    try:
        proc = subprocess.run(
            cmd,
            env=env or os.environ.copy(),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return 1, {}
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    try:
        report = json.loads(proc.stdout)
    except (ValueError, TypeError):
        report = {}
    return int(proc.returncode), report if isinstance(report, dict) else {}


def _policy_file_from_args(args: argparse.Namespace) -> Path:
    value = str(getattr(args, "policy_file", None) or os.environ.get(POLICY_FILE_ENV) or "")
    if not value:
        raise SystemExit(
            f"an explicit --policy-file or {POLICY_FILE_ENV} is required"
        )
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SystemExit("force-loop policy file must be absolute")
    return Path(os.path.abspath(path))


def cmd_apply_defaults(args: argparse.Namespace) -> int:
    policy_file = _policy_file_from_args(args)
    try:
        result = apply_defaults(
            Path(args.loop),
            profile=args.profile,
            research_title=args.research_title,
            backup=not args.no_backup,
            policy_file=policy_file,
        )
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def cmd_bootstrap(args: argparse.Namespace) -> int:
    loop = Path(args.loop).expanduser().resolve()
    root = Path(args.root).expanduser().resolve() if args.root else loop.parent
    policy_file = _policy_file_from_args(args)
    loop.mkdir(parents=True, exist_ok=True)

    need_init = not (loop / "loop_state.json").is_file()
    if need_init:
        if not (args.goal and args.success_criteria):
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "loop missing; pass both --goal and --success-criteria for init",
                    },
                    indent=2,
                )
            )
            return 2
        init_args = [
            "init",
            "--dir",
            str(loop),
            "--goal",
            args.goal,
            "--success-criteria",
            args.success_criteria,
            "--goal-focus-mode",
            "enforce",
        ]
        if args.profile == "formal":
            init_args.extend(["--formal-policy", "on"])
            if args.formal_project:
                init_args.extend(["--formal-project", args.formal_project])
        max_iterations = args.max_iterations
        max_wall_time = args.max_wall_time_seconds
        if args.profile == "formal":
            # Formal campaigns outlive the runtime's smoke-scale init defaults
            # (5 iterations / 1 h); a paper-formalization run needs room to
            # reach a terminal state without tripping the budget stop.
            if max_iterations is None:
                max_iterations = 40
            if max_wall_time is None:
                max_wall_time = 259200
        if max_iterations is not None:
            init_args.extend(["--max-iterations", str(max_iterations)])
        if max_wall_time is not None:
            init_args.extend(["--max-wall-time-seconds", str(max_wall_time)])
        if not (loop / "compute_policy.json").is_file():
            # Goal Focus resolves current_plan.compute_policy during init; a pin
            # written afterwards leaves the plan allowlist empty and permanently
            # fails the plan compute-policy check.
            try:
                apply_compute_policy(loop, args.profile)
            except ValueError as exc:
                print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
                return 2
        rc = _run_runtime(init_args)
        if rc != 0:
            print(json.dumps({"ok": False, "error": "init failed", "rc": rc}, indent=2))
            return rc

    try:
        result = apply_defaults(
            loop,
            profile=args.profile,
            research_title=args.research_title,
            backup=not args.no_backup,
            policy_file=policy_file,
        )
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 2
    smoke = _smoke_checks(loop, args.profile, root=root, live=False, policy_file=policy_file)
    out = {
        "ok": bool(result.get("ok") and smoke.get("ok")),
        "bootstrap": result,
        "smoke": smoke,
        "next": [
            f"force-loop start --loop {loop} --root {root} --policy-file {policy_file}",
        ],
    }
    print(json.dumps(out, indent=2))
    return 0 if out["ok"] else 1


def _open_directory_nofollow(path: Path) -> int:
    """Open an absolute POSIX directory path without following any link."""

    absolute = Path(os.path.abspath(path))
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = os.open(absolute.anchor or os.sep, flags)
    try:
        for component in absolute.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


def _load_managed_secrets(
    pointer_env: str,
    *,
    allowed_keys: frozenset[str],
    environ: dict[str, str],
) -> dict[str, str]:
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        raise EnvLoadError(
            "Windows secret pointers must be loaded by run_force_loop.ps1"
        )
    runtime_root = next(
        (parent for parent in PACK_DIR.parents if parent.name == "runtime"),
        None,
    )
    candidates: list[Path] = []
    if runtime_root is not None:
        candidates.extend(
            [
                runtime_root / "load_secret_env.py",
                runtime_root / "runners" / "load_secret_env.py",
            ]
        )
    loader_path = next((path for path in candidates if path.is_file()), None)
    if loader_path is None:
        raise EnvLoadError("managed secret loader is unavailable")
    parent_fd: int | None = None
    loader_fd: int | None = None
    try:
        parent_fd = _open_directory_nofollow(loader_path.parent)
        loader_fd = os.open(
            loader_path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        before = os.fstat(loader_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or int(before.st_uid) not in {0, os.geteuid()}
            or stat.S_IMODE(before.st_mode) & 0o022
            or (int(before.st_uid) != 0 and int(before.st_nlink) != 1)
            or int(before.st_size) > 1_000_000
        ):
            raise EnvLoadError("managed secret loader is not owner-controlled")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(loader_fd, 65_536)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(loader_fd)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise EnvLoadError("managed secret loader changed while reading")
        module = types.ModuleType("aas_force_loop_secret_env_loader")
        module.__file__ = str(loader_path)
        exec(compile(b"".join(chunks), str(loader_path), "exec"), module.__dict__)
        return module.load_pointer_secret_env(
            pointer_env,
            allowed_keys=allowed_keys,
            environ=environ,
        )
    except Exception as exc:
        error_type = getattr(locals().get("module"), "SecretEnvError", ())
        if error_type and isinstance(exc, error_type):
            raise EnvLoadError(str(exc)) from exc
        if isinstance(exc, EnvLoadError):
            raise
        raise EnvLoadError("managed secret loader failed") from exc
    finally:
        if loader_fd is not None:
            os.close(loader_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def _load_start_env(loop: Path, policy_file: Path) -> dict[str, str]:
    """Build a scrubbed non-secret environment from one protected host policy."""
    # Foreground/systemd parity: admit the same non-secret operator prefixes
    # the systemd backend passes through (transport, attestation pins, egress
    # consents, compute-workspace pin); policy-file keys still override below.
    env = {
        key: str(value)
        for key, value in os.environ.items()
        if key in BASE_ENV_KEYS
        or key.startswith("LC_")
        or any(key.startswith(prefix) for prefix in SYSTEMD_ENV_PREFIXES)
    }
    if os.name == "nt" and (
        os.environ.get(COMPUTE_SECRETS_ENV) or os.environ.get(PROVIDER_SECRETS_ENV)
    ):  # pragma: no cover - exercised on Windows CI
        raise SystemExit(
            "env load failed: Windows secret pointers must be loaded by "
            "run_force_loop.ps1"
        )
    try:
        legacy = loop / "driver" / "force_loop.env"
        if legacy.exists() or legacy.is_symlink():
            raise EnvLoadError(
                "legacy loop-local driver/force_loop.env is forbidden; use the host policy file"
            )
        merged = load_env_file(policy_file, forbidden_root=loop)
        apply_to_environ(merged, env, override=True)
        strict_notify_channel = _normalize_strict_notify_channel(env)
        if strict_notify_channel:
            env[STRICT_NOTIFY_CHANNEL_ENV] = strict_notify_channel
        else:
            env.pop(STRICT_NOTIFY_CHANNEL_ENV, None)
    except EnvLoadError as exc:
        raise SystemExit(f"env load failed: {exc}") from exc
    env["PATH"] = "/usr/bin:/bin"
    env["SHELL"] = "/bin/bash"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["LOOP_DIR"] = str(loop)
    return env


def _provider_name(provider: str | None) -> str:
    """Return the bare provider name, dropping any ``name:model`` suffix."""
    value = str(provider or "").strip().lower()
    if not value:
        return ""
    return re.split(r"[:/]", value, maxsplit=1)[0]


def _provider_keys(provider: str | None) -> frozenset[str]:
    name = _provider_name(provider)
    if not name:
        return frozenset()
    return PROVIDER_KEY_MAP.get(name, frozenset())


def _compute_keys(policy: dict[str, str]) -> frozenset[str]:
    raw = str(policy.get("AAS_FORCE_LOOP_COMPUTE_LANES") or "")
    if not raw:
        return frozenset()
    selected: set[str] = set()
    for lane in raw.split(","):
        name = lane.strip().lower()
        if not name or name not in COMPUTE_LANE_KEYS:
            raise EnvLoadError("host policy contains an unsupported compute lane")
        selected.update(COMPUTE_LANE_KEYS[name])
    return frozenset(selected)


def _projected_credentials(selected: frozenset[str]) -> dict[str, str]:
    """Return the selected subset of what run_force_loop.ps1 already loaded.

    The native Windows launcher owns credential reading, so it publishes the
    key names it exported and Python admits nothing else: an ambient token that
    the launcher's scrub missed cannot pose as a projected credential.
    """
    if SECRETS_PROJECTION_ENV not in os.environ:
        raise EnvLoadError(
            "Windows credentials must be projected by run_force_loop.ps1"
        )
    # An empty manifest means the launcher ran and the pointer file carried
    # nothing for the selected lanes/provider, which is what POSIX does too;
    # an absent manifest means the launcher never ran.
    names = [
        name
        for name in str(os.environ[SECRETS_PROJECTION_ENV]).strip().split(",")
        if name
    ]
    if names != sorted(set(names)):
        raise EnvLoadError("projected credential manifest must be sorted and unique")
    known = COMPUTE_SECRET_KEYS | PROVIDER_SECRET_KEYS
    values: dict[str, str] = {}
    for name in names:
        if name not in known:
            raise EnvLoadError("projected credential manifest names an unsupported key")
        if name not in os.environ:
            raise EnvLoadError("projected credential manifest is missing a declared key")
        if name in selected:
            values[name] = str(os.environ[name])
    return values


def _load_selected_credentials(
    env: dict[str, str],
    *,
    provider: str | None,
) -> dict[str, str]:
    """Load only policy/provider-selected credentials after argv binding."""
    for key in COMPUTE_SECRET_KEYS | PROVIDER_SECRET_KEYS:
        env.pop(key, None)
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        try:
            selected = _compute_keys(env) | _provider_keys(provider)
            if provider and _provider_name(provider) not in PROVIDER_KEY_MAP:
                raise EnvLoadError("selected provider has no credential projection contract")
            if selected:
                apply_to_environ(_projected_credentials(selected), env)
        except EnvLoadError as exc:
            raise SystemExit(f"env load failed: {exc}") from exc
        return env
    compute_pointer = str(os.environ.get(COMPUTE_SECRETS_ENV) or "")
    provider_pointer = str(os.environ.get(PROVIDER_SECRETS_ENV) or "")
    try:
        compute_keys = _compute_keys(env)
        provider_keys = _provider_keys(provider)
        if provider and _provider_name(provider) not in PROVIDER_KEY_MAP:
            raise EnvLoadError("selected provider has no credential projection contract")
        if compute_pointer and not compute_keys:
            raise EnvLoadError("compute secret pointer requires policy-selected compute lanes")
        if provider_pointer and not provider_keys:
            raise EnvLoadError("provider secret pointer requires an explicit supported provider")
        # The managed loader rejects a whole file that carries any key outside
        # ``allowed_keys``; admit the full lane/provider vocabulary there and
        # narrow the projection to the selected subset below.
        if compute_keys:
            pointer_env = {COMPUTE_SECRETS_ENV: compute_pointer}
            loaded = _load_managed_secrets(
                COMPUTE_SECRETS_ENV,
                allowed_keys=COMPUTE_SECRET_KEYS,
                environ=pointer_env,
            )
            apply_to_environ({key: value for key, value in loaded.items() if key in compute_keys}, env)
        if provider_keys:
            pointer_env = {PROVIDER_SECRETS_ENV: provider_pointer}
            loaded = _load_managed_secrets(
                PROVIDER_SECRETS_ENV,
                allowed_keys=PROVIDER_SECRET_KEYS,
                environ=pointer_env,
            )
            apply_to_environ({key: value for key, value in loaded.items() if key in provider_keys}, env)
    except EnvLoadError as exc:
        raise SystemExit(f"env load failed: {exc}") from exc
    return env


def _normalize_strict_notify_channel(env: dict[str, str]) -> str:
    value = str(env.get(STRICT_NOTIFY_CHANNEL_ENV) or "").strip().lower()
    if value and value not in STRICT_NOTIFY_CHANNELS:
        raise EnvLoadError(
            f"{STRICT_NOTIFY_CHANNEL_ENV} must be zulip, telegram, or empty"
        )
    return value


def _systemd_environment_values(
    env: dict[str, str],
    *,
    loop: Path,
    root: Path,
) -> dict[str, str]:
    source = dict(env)
    source["LOOP_DIR"] = str(loop)
    source["PROJECT_ROOT"] = str(root)
    strict_notify_channel = _normalize_strict_notify_channel(source)
    if strict_notify_channel:
        source[STRICT_NOTIFY_CHANNEL_ENV] = strict_notify_channel
    else:
        source.pop(STRICT_NOTIFY_CHANNEL_ENV, None)
    return {
        key: str(value)
        for key, value in source.items()
        if value
        and (
            key in SYSTEMD_ENV_FIXED_KEYS
            or any(key.startswith(prefix) for prefix in SYSTEMD_ENV_PREFIXES)
        )
    }


def _quote_systemd_environment_value(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise EnvLoadError("systemd environment values must not contain controls")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _open_private_systemd_environment_directory(env: dict[str, str]) -> tuple[Path, int]:
    runtime_value = str(env.get("XDG_RUNTIME_DIR") or "")
    if not runtime_value:
        runtime_value = f"/run/user/{os.geteuid()}"
    if runtime_value != runtime_value.strip():
        raise EnvLoadError("XDG_RUNTIME_DIR has surrounding whitespace")
    runtime_dir = Path(runtime_value)
    if not runtime_dir.is_absolute():
        raise EnvLoadError("XDG_RUNTIME_DIR must name an absolute path")
    runtime_dir = Path(os.path.abspath(runtime_dir))
    base_fd: int | None = None
    child_fd: int | None = None
    try:
        base_fd = _open_directory_nofollow(runtime_dir)
        base_info = os.fstat(base_fd)
        if (
            int(base_info.st_uid) != int(os.geteuid())
            or stat.S_IMODE(base_info.st_mode) & 0o077
        ):
            raise EnvLoadError("XDG_RUNTIME_DIR must be current-user private")
        try:
            os.mkdir(SYSTEMD_ENV_DIR_NAME, mode=0o700, dir_fd=base_fd)
        except FileExistsError:
            pass
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        child_fd = os.open(SYSTEMD_ENV_DIR_NAME, flags, dir_fd=base_fd)
        child_info = os.fstat(child_fd)
        if (
            not stat.S_ISDIR(child_info.st_mode)
            or int(child_info.st_uid) != int(os.geteuid())
            or stat.S_IMODE(child_info.st_mode) & 0o077
        ):
            raise EnvLoadError("systemd environment directory must be owner-private")
        return runtime_dir / SYSTEMD_ENV_DIR_NAME, child_fd
    except EnvLoadError:
        if child_fd is not None:
            os.close(child_fd)
        raise
    except OSError as exc:
        if child_fd is not None:
            os.close(child_fd)
        raise EnvLoadError("could not prepare private systemd environment") from exc
    finally:
        if base_fd is not None:
            os.close(base_fd)


def _write_systemd_environment_file(
    env: dict[str, str],
    *,
    unit: str,
    loop: Path,
    root: Path,
) -> Path:
    values = _systemd_environment_values(env, loop=loop, root=root)
    lines: list[str] = []
    for key in sorted(values):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise EnvLoadError("systemd environment contains an invalid key")
        lines.append(f"{key}={_quote_systemd_environment_value(values[key])}")
    payload = (("\n".join(lines) + "\n") if lines else "").encode("utf-8")
    if len(payload) > SYSTEMD_ENV_MAX_BYTES:
        raise EnvLoadError("systemd environment file is oversized")

    directory, directory_fd = _open_private_systemd_environment_directory(env)
    file_fd: int | None = None
    filename = ""
    try:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        for _attempt in range(10):
            filename = f"{unit}-{random_secrets.token_hex(8)}.env"
            try:
                file_fd = os.open(filename, flags, 0o600, dir_fd=directory_fd)
                break
            except FileExistsError:
                continue
        if file_fd is None:
            raise EnvLoadError("could not allocate private systemd environment file")
        os.fchmod(file_fd, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(file_fd, view)
            if written <= 0:
                raise OSError("short systemd environment write")
            view = view[written:]
        os.fsync(file_fd)
        info = os.fstat(file_fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or int(getattr(info, "st_nlink", 1)) != 1
            or int(info.st_uid) != int(os.geteuid())
            or stat.S_IMODE(info.st_mode) & 0o077
            or int(info.st_size) != len(payload)
        ):
            raise EnvLoadError("systemd environment file failed private-file checks")
    except (OSError, EnvLoadError):
        if filename:
            try:
                os.unlink(filename, dir_fd=directory_fd)
            except OSError:
                pass
        raise
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(directory_fd)
    path = directory / filename
    if not SYSTEMD_SAFE_PATH.fullmatch(str(path)):
        try:
            path.unlink()
        except OSError:
            pass
        raise EnvLoadError("systemd environment path contains unsupported characters")
    return path


def _systemd_client_environment(env: dict[str, str]) -> dict[str, str]:
    return {
        key: str(value)
        for key, value in env.items()
        if value
        and (
            key in SYSTEMD_CLIENT_ENV_KEYS
            or any(key.startswith(prefix) for prefix in SYSTEMD_CLIENT_ENV_PREFIXES)
        )
    }


def _write_pinned_failover(loop: Path, provider: str) -> Path:
    """Persist a single-primary failover config so rotation cannot override --provider."""
    source = loop / "failover.json"
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"failover config is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("failover config must be a JSON object")
    data = dict(data)
    data["primary_order"] = [provider]
    data.pop("primary_fallback", None)
    data["max_quota_waits_per_primary"] = int(data.get("max_quota_waits_per_primary", 3) or 3)
    destination = loop / "driver" / "failover.pinned.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    return destination


def cmd_start(args: argparse.Namespace) -> int:
    loop = Path(args.loop).expanduser().resolve()
    root = Path(args.root).expanduser().resolve() if args.root else loop.parent
    policy_file = _policy_file_from_args(args)
    if not loop.is_dir():
        print(json.dumps({"ok": False, "error": f"loop not found: {loop}"}, indent=2))
        return 2

    # A second driver on one loop tree races every state transaction; refuse
    # unless the operator has confirmed the survivors are stale.
    running = status_snapshot(loop)
    if (running.get("pidfile_alive") or running.get("matched_pids")) and not args.force:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "loop already has live driver processes; run stop/replace or pass --force",
                    "process": running,
                },
                indent=2,
                default=str,
            )
        )
        return 10

    # Ensure pins present before start.
    errors = verify_effective(loop, args.profile, policy_file)
    if errors and not args.skip_defaults_check:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "defaults missing; run bootstrap or apply-defaults",
                    "errors": errors,
                },
                indent=2,
            )
        )
        return 1

    try:
        backend = select_backend(args.backend, detach=bool(args.detach))
        if os.environ.get("AAS_ARL_BROKER_SOCKET") and backend != "foreground":
            raise SystemExit(
                "credential-brokered force-loop start requires --backend foreground; "
                "detached backends cannot outlive the exact-generation broker"
            )
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 2

    env = _load_start_env(loop, policy_file)
    env["PROJECT_ROOT"] = str(root)
    if not env.get("AAS_RUNTIME_ROOT"):
        # Best-effort: installed layout is runtime/workspace/skills/<skill>
        # When running from the git tree, leave unset (drive still works).
        candidate = RUNTIME_PARENT.parent.parent.parent
        if (candidate / "workspace").is_dir() or (candidate / "run_skill.sh").is_file():
            env["AAS_RUNTIME_ROOT"] = str(candidate)

    extra: list[str] = []
    if args.panel:
        extra.extend(["--panel", args.panel])
    if args.profile == "formal":
        extra.extend(["--formal-policy", "on"])
        if args.formal_typecheck:
            extra.append("--formal-typecheck")
    if args.drive_extra:
        extra.extend(args.drive_extra)

    supervisor = build_supervisor_command(
        pack_parent=RUNTIME_PARENT,
        loop_dir=loop,
        project_root=root,
    )
    # The supervisor is only a supervisor when it has a failover config; without
    # one it exits 2 before any drive iteration, so fall back to direct drive.
    use_supervisor = bool(
        supervisor and not args.drive_only and (loop / "failover.json").is_file()
    )
    if use_supervisor:
        argv = supervisor
        env["LOOP_DIR"] = str(loop)
        env["PROJECT_ROOT"] = str(root)
        env["RUNTIME_PY"] = str(_runtime_py())
        env["SYNC_PANEL_PY"] = str(RUNTIME_PARENT / "sync_panel_exclude.py")
        if args.provider:
            # The supervisor owns provider selection; a --provider on the drive
            # argv would silently outrank every rotation it performs.
            env["FAILOVER_JSON"] = str(_write_pinned_failover(loop, args.provider))
        if extra:
            # Supervisor reads DRIVE_EXTRA_ARGS as shell-ish; pass via env space-joined
            # only safe flags we control (no secrets).
            rejected = [item for item in extra if UNSAFE_DRIVE_EXTRA.search(item)]
            if rejected:
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "error": "drive extras must survive supervisor word splitting",
                            "rejected": rejected,
                        },
                        indent=2,
                    )
                )
                return 2
            env["DRIVE_EXTRA_ARGS"] = " ".join(extra)
    else:
        if args.provider:
            extra[:0] = ["--provider", args.provider]
        argv = build_drive_command(
            runtime_py=_runtime_py(),
            loop_dir=loop,
            project_root=root,
            extra_args=extra,
        )

    print(
        json.dumps(
            {
                "ok": True,
                "backend": backend,
                "failover_supervisor": use_supervisor,
                "argv_preview": argv[:6] + (["…"] if len(argv) > 6 else []),
                "loop": str(loop),
            },
            indent=2,
        )
    )

    if backend == "systemd_user" and (
        args.provider
        or os.environ.get(COMPUTE_SECRETS_ENV)
        or os.environ.get(PROVIDER_SECRETS_ENV)
        or env.get("AAS_FORCE_LOOP_COMPUTE_LANES")
    ):
        print(json.dumps({"ok": False, "error": "systemd_user does not support descriptor-bound credential handoff"}, indent=2))
        return 2
    try:
        binding = bind_child_command(argv)
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": f"child binding failed: {exc}"}, indent=2))
        return 2
    try:
        _load_selected_credentials(env, provider=args.provider)
        if backend == "foreground":
            rc = run_foreground(
                binding.argv, loop_dir=loop, env=env, pass_fds=binding.pass_fds
            )
        elif backend == "posix_detach":
            rc = run_posix_detach(
                binding.argv, loop_dir=loop, env=env, pass_fds=binding.pass_fds
            )
        else:
            rc = None
        if rc is not None:
            if rc == 10:
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "error": "supervisor lock is held by another driver on this loop",
                            "lock": str(loop / "driver" / "supervisor.lock"),
                        },
                        indent=2,
                    )
                )
            return rc
    finally:
        binding.close()
    if backend == "systemd_user":
        return _start_systemd_user(argv, loop=loop, root=root, env=env)
    print(json.dumps({"ok": False, "error": f"backend not implemented: {backend}"}, indent=2))
    return 2


def _start_systemd_user(
    argv: list[str],
    *,
    loop: Path,
    root: Path,
    env: dict[str, str],
) -> int:
    unit_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", loop.name).strip(".-") or "loop"
    unit = f"aas-force-loop-{unit_slug}"[:100]
    systemd_run_binary = shutil.which("systemd-run", path="/usr/bin:/bin")
    rm_binary = shutil.which("rm", path="/usr/bin:/bin")
    if (
        not systemd_run_binary
        or not SYSTEMD_SAFE_PATH.fullmatch(systemd_run_binary)
        or not rm_binary
        or not SYSTEMD_SAFE_PATH.fullmatch(rm_binary)
    ):
        print(
            json.dumps(
                {
                    "ok": False,
                    "unit": unit,
                    "rc": 2,
                    "error": "trusted systemd submission tools are unavailable",
                },
                indent=2,
            )
        )
        return 2
    try:
        environment_file = _write_systemd_environment_file(
            env,
            unit=unit,
            loop=loop,
            root=root,
        )
    except EnvLoadError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "unit": unit,
                    "rc": 2,
                    "error": str(exc),
                },
                indent=2,
            )
        )
        return 2

    # The transient unit reads a private bounded EnvironmentFile. Only its path,
    # never token values, crosses the systemd-run argv boundary.
    cmd = [
        systemd_run_binary,
        "--user",
        "--quiet",
        "--collect",
        "--no-ask-password",
        "--expand-environment=no",
        f"--unit={unit}",
        "--service-type=exec",
        f"--working-directory={root}",
        "--property=KillMode=control-group",
        "--property=TimeoutStopSec=20s",
        f"--property=EnvironmentFile={environment_file}",
        f"--property=ExecStopPost={rm_binary} -f -- {environment_file}",
        *argv,
    ]
    client_env = _systemd_client_environment(env)
    client_env["PATH"] = "/usr/bin:/bin"
    try:
        proc = subprocess.run(
            cmd,
            env=client_env,
            check=False,
        )
    except OSError as exc:
        try:
            environment_file.unlink()
        except OSError:
            pass
        print(
            json.dumps(
                {
                    "ok": False,
                    "unit": unit,
                    "rc": 2,
                    "error": f"systemd-run failed: {exc}",
                },
                indent=2,
            )
        )
        return 2
    if proc.returncode != 0:
        try:
            environment_file.unlink()
        except OSError:
            pass
    print(
        json.dumps(
            {"ok": proc.returncode == 0, "unit": unit, "rc": proc.returncode},
            indent=2,
        )
    )
    return int(proc.returncode)


def cmd_stop(args: argparse.Namespace) -> int:
    loop = Path(args.loop).expanduser().resolve()
    stopped = stop_loop_processes(loop)
    snap = status_snapshot(loop)
    survivors = bool(snap.get("pidfile_alive") or snap.get("matched_pids"))
    print(
        json.dumps(
            {
                "ok": not survivors,
                "stopped": stopped,
                "loop": str(loop),
                "process": snap,
            },
            indent=2,
            default=str,
        )
    )
    return 1 if survivors else 0


def cmd_replace(args: argparse.Namespace) -> int:
    stop_rc = cmd_stop(args)
    if stop_rc != 0:
        return stop_rc
    return cmd_start(args)


def cmd_status(args: argparse.Namespace) -> int:
    loop = Path(args.loop).expanduser().resolve()
    policy_file = _policy_file_from_args(args)
    snap = status_snapshot(loop)
    # Best-effort goal-focus status
    gf: dict[str, Any] = {}
    if _runtime_py().is_file():
        try:
            proc = subprocess.run(
                [_python(), str(_runtime_py()), "goal-focus", "status", "--dir", str(loop)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if proc.stdout.strip():
                try:
                    gf = json.loads(proc.stdout)
                except json.JSONDecodeError:
                    gf = {"raw_preview": proc.stdout[:500]}
        except (subprocess.TimeoutExpired, OSError) as exc:
            gf = {"error": str(exc)}
    errors = verify_effective(loop, args.profile, policy_file)
    out = {
        "process": snap,
        "defaults_ok": not errors,
        "defaults_errors": errors,
        "goal_focus": gf,
        "systemd_user_available": systemd_user_available(),
    }
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_drain(args: argparse.Namespace) -> int:
    """Thin wrappers around goal-focus recover/status — no reclaim reimplementation."""
    loop = Path(args.loop).expanduser().resolve()
    actions: list[dict[str, Any]] = []

    def _gf(extra: list[str]) -> dict[str, Any]:
        try:
            proc = subprocess.run(
                [_python(), str(_runtime_py()), "goal-focus", *extra, "--dir", str(loop)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {"argv": extra, "rc": 1, "result": {"status": "failed", "error": str(exc)}}
        body: Any
        try:
            body = json.loads(proc.stdout) if proc.stdout.strip() else {"stdout": proc.stdout}
        except json.JSONDecodeError:
            body = {"stdout": proc.stdout, "stderr": proc.stderr}
        return {"argv": extra, "rc": proc.returncode, "result": body}

    actions.append(_gf(["status"]))

    if args.cancel_dispatch_id:
        actions.append(
            _gf(
                [
                    "recover-dispatch",
                    "--cancel",
                    "--dispatch-id",
                    args.cancel_dispatch_id,
                ]
            )
        )
    elif args.recover_dispatch:
        actions.append(_gf(["recover-dispatch"]))

    if args.recover_quarantine:
        q = ["recover-quarantine"]
        if args.release_fingerprint:
            q.extend(["--release", "--candidate-fingerprint", args.release_fingerprint])
        actions.append(_gf(q))

    snap = status_snapshot(loop)
    if args.cancel_dead_pids and snap.get("matched_pids"):
        # Only cancel if pidfile claims dead owner — still via recover-dispatch when id known.
        actions.append(
            {
                "note": "matched pids present; use --cancel-dispatch-id with exact id from status",
                "matched_pids": snap.get("matched_pids"),
                "pidfile_alive": snap.get("pidfile_alive"),
            }
        )

    # `drain` is a recovery command: a failed recover-* leaves the loop stuck,
    # so its exit status must follow the recovery calls, not the wrapper.
    recovery_ok = all(
        int(action.get("rc", 0)) == 0
        for action in actions
        if str((action.get("argv") or [""])[0]).startswith("recover-")
    )
    print(
        json.dumps(
            {"ok": recovery_ok, "actions": actions, "process": snap},
            indent=2,
            default=str,
        )
    )
    return 0 if recovery_ok else 1


def _goal_focus_validate_errors(loop: Path) -> list[str]:
    """Run `goal-focus validate` and return its errors, prefixed for the caller."""
    try:
        proc = subprocess.run(
            [_python(), str(_runtime_py()), "goal-focus", "validate", "--dir", str(loop)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [f"goal-focus: validate could not run: {exc}"]
    if proc.returncode == 0:
        return []
    body: Any = {}
    try:
        body = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        body = {}
    detail: list[str] = []
    if isinstance(body, dict):
        detail = [str(item) for item in (body.get("errors") or [])]
        if not detail and body.get("error"):
            detail = [str(body["error"])]
    if not detail:
        detail = [f"validate exited {proc.returncode}"]
    return [f"goal-focus: {item}" for item in detail]


def _detect_notify_channels() -> list[str] | None:
    """Notify channels with usable remote-bridge credentials; None when detection fails.

    Runs in a subprocess so a broken runtime import cannot take down the CLI.
    """
    snippet = (
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import autonomous_research_loop_runtime as rt\n"
        "print(json.dumps(rt.detect_configured_notify_channels()))\n"
    )
    try:
        proc = subprocess.run(
            [_python(), "-c", snippet, str(RUNTIME_PARENT)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        channels = json.loads(proc.stdout.strip().splitlines()[-1])
    except ValueError:
        return None
    if not isinstance(channels, list):
        return None
    return [str(channel) for channel in channels]


def _smoke_checks(
    loop: Path,
    profile: str,
    *,
    root: Path | None = None,
    live: bool = False,
    policy_file: Path | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not _runtime_py().is_file():
        errors.append(f"runtime missing: {_runtime_py()}")
    defaults_errors = (
        verify_effective(loop, profile, policy_file) if loop.is_dir() else ["loop missing"]
    )
    errors.extend(defaults_errors)

    # Pins can all be present while Goal Focus still refuses the plan (mode
    # authority, compute allowlist); a smoke that skips this reports ok on a
    # loop whose first gate call fails.
    if loop.is_dir() and (loop / "loop_state.json").is_file() and _runtime_py().is_file():
        errors.extend(_goal_focus_validate_errors(loop))

    # Notify preflight: a campaign whose terminal notice cannot be delivered
    # runs to completion with nobody watching, so consented egress without a
    # usable channel is an error, not a warning.
    egress_allowed = (
        str(os.environ.get("AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS") or "").strip().lower()
        == "allow"
    )
    channels = _detect_notify_channels() if egress_allowed else None
    if not egress_allowed:
        notify_status = "blocked_no_consent"
        warnings.append(
            "AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS is not 'allow'; "
            "external notify delivery stays blocked for this loop"
        )
    elif channels is None:
        notify_status = "unknown"
        warnings.append(
            "notify channel detection failed; external notify delivery is unverified"
        )
    elif not channels:
        notify_status = "no_channel"
        errors.append(
            "external notify egress is allowed but no notify channel has usable "
            "credentials (zulip/telegram); configure remote-bridge secrets or "
            "unset AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS"
        )
    else:
        notify_status = "ready:" + ",".join(channels)

    # Env loader self-check
    try:
        from load_loop_env import parse_env_text

        parse_env_text("AAS_AUTOLOOP_NOTIFY=auto\n")
        try:
            parse_env_text("BAD=$(whoami)\n")
            errors.append("env loader accepted unsafe value")
        except EnvLoadError:
            pass
    except Exception as exc:  # pragma: no cover
        errors.append(f"env loader import failed: {exc}")

    backend = select_backend(None)
    out: dict[str, Any] = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "notify_status": notify_status,
        "default_backend": backend,
        "systemd_user_available": systemd_user_available(),
        "platform": sys.platform,
        "profile": profile,
        "loop": str(loop),
    }
    if live and root is not None and not errors:
        # Optional: runtime selftest.  A green rc can still hide skipped drive
        # checks, so surface that verdict instead of only the exit status.
        rc, report = _run_runtime_json(["selftest"])
        out["runtime_selftest_rc"] = rc
        drive_checks = str(report.get("drive_checks") or "")
        if drive_checks:
            out["runtime_selftest_drive_checks"] = drive_checks
        if drive_checks == "skipped":
            out["warnings"] = list(out["warnings"]) + [
                "runtime selftest skipped its driver drive checks: "
                + (str(report.get("drive_skip_reason") or "no reason reported"))
            ]
        if rc != 0:
            out["ok"] = False
            out["errors"] = list(out["errors"]) + ["runtime selftest failed"]
    return out


def cmd_smoke(args: argparse.Namespace) -> int:
    loop = Path(args.loop).expanduser().resolve() if args.loop else Path.cwd()
    root = Path(args.root).expanduser().resolve() if args.root else loop.parent
    out = _smoke_checks(
        loop,
        args.profile,
        root=root,
        live=bool(args.live),
        policy_file=_policy_file_from_args(args),
    )
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="force-loop",
        description="Default scripted force-loop kit (cross-platform)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add_loop(sp: argparse.ArgumentParser, *, required: bool = True) -> None:
        sp.add_argument("--loop", required=required, help="loop directory")
        sp.add_argument("--root", default=None, help="project root (default: parent of loop)")
        sp.add_argument(
            "--profile",
            default="formal",
            choices=["formal", "general"],
        )
        sp.add_argument(
            "--policy-file",
            default=None,
            help=f"absolute protected host policy (or {POLICY_FILE_ENV})",
        )

    b = sub.add_parser("bootstrap", help="init if needed + apply defaults + smoke")
    add_loop(b)
    b.add_argument("--goal", default=None, help="required when the loop is not initialised yet")
    b.add_argument(
        "--success-criteria",
        default=None,
        help="required when the loop is not initialised yet",
    )
    b.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="init iteration budget; formal profile defaults to 40 (runtime default 5)",
    )
    b.add_argument(
        "--max-wall-time-seconds",
        type=int,
        default=None,
        help="init wall-time budget; formal profile defaults to 259200 (runtime default 3600)",
    )
    b.add_argument("--research-title", default=None)
    b.add_argument("--formal-project", default=None)
    b.add_argument("--no-backup", action="store_true")
    b.set_defaults(func=cmd_bootstrap)

    a = sub.add_parser("apply-defaults", help="write enforce/hard/notify/compute/formal pins")
    add_loop(a)
    a.add_argument("--research-title", default=None)
    a.add_argument("--no-backup", action="store_true")
    a.set_defaults(func=cmd_apply_defaults)

    s = sub.add_parser("start", help="start supervisor/drive (foreground default)")
    add_loop(s)
    s.add_argument("--backend", default=None, help="foreground|posix_detach|systemd_user|auto")
    s.add_argument("--detach", action="store_true", help="use posix_detach when backend=auto")
    s.add_argument("--provider", default=None)
    s.add_argument("--panel", default=None, help="on|off|auto")
    s.add_argument("--drive-only", action="store_true", help="skip bash supervisor even on POSIX")
    s.add_argument(
        "--formal-typecheck",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="pass --formal-typecheck when profile=formal (default: on)",
    )
    s.add_argument("--skip-defaults-check", action="store_true")
    s.add_argument(
        "--force",
        action="store_true",
        help="start even when live driver processes already match this loop",
    )
    s.add_argument("drive_extra", nargs="*", help="extra args after -- passed to drive")
    s.set_defaults(func=cmd_start)

    st = sub.add_parser("stop", help="stop matching supervisor/drive processes")
    add_loop(st)
    st.set_defaults(func=cmd_stop)

    r = sub.add_parser("replace", help="stop then start")
    add_loop(r)
    r.add_argument("--backend", default=None)
    r.add_argument("--detach", action="store_true")
    r.add_argument("--provider", default=None)
    r.add_argument("--panel", default=None)
    r.add_argument("--drive-only", action="store_true")
    r.add_argument(
        "--formal-typecheck",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    r.add_argument("--skip-defaults-check", action="store_true")
    r.add_argument("--force", action="store_true")
    r.add_argument("drive_extra", nargs="*")
    r.set_defaults(func=cmd_replace)

    u = sub.add_parser("status", help="process + defaults + goal-focus status")
    add_loop(u)
    u.set_defaults(func=cmd_status)

    d = sub.add_parser("drain", help="wrap goal-focus status/recover (no reclaim reimplementation)")
    add_loop(d)
    d.add_argument("--recover-dispatch", action="store_true")
    d.add_argument("--cancel-dispatch-id", default=None)
    d.add_argument("--recover-quarantine", action="store_true")
    d.add_argument("--release-fingerprint", default=None)
    d.add_argument("--cancel-dead-pids", action="store_true")
    d.set_defaults(func=cmd_drain)

    sm = sub.add_parser("smoke", help="offline default + env checks")
    add_loop(sm, required=False)
    sm.add_argument("--live", action="store_true", help="also run runtime selftest")
    sm.set_defaults(func=cmd_smoke)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
