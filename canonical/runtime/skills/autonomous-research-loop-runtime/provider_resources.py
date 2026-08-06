#!/usr/bin/env python3
"""Mandatory resource bounds for trusted-local provider subprocesses."""

from __future__ import annotations

import os
import errno
import json
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, NamedTuple, Sequence


class ProviderResourceError(RuntimeError):
    """Raised before spawn when provider resource limits cannot be enforced."""


class ProviderResourceCleanupError(ProviderResourceError):
    """Raised when a resource preflight scope cannot be proven dead."""


class ProviderProcessError(ProviderResourceError):
    """Raised when provider execution fails and carries cleanup verification."""

    def __init__(self, message: str, *, cleanup_error: str | None = None) -> None:
        super().__init__(message)
        self.cleanup_error = cleanup_error


class BoundedProcessResult(NamedTuple):
    """Result of one resource-scoped provider process with bounded capture."""

    return_code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    oversized: bool
    capture_error: str | None
    cleanup_error: str | None


RESOURCE_ENV = {
    "memory_mib": "AAS_AUTOLOOP_RESOURCE_MEMORY_MIB",
    "swap_mib": "AAS_AUTOLOOP_RESOURCE_SWAP_MIB",
    "address_space_mib": "AAS_AUTOLOOP_RESOURCE_ADDRESS_SPACE_MIB",
    "cpu_seconds": "AAS_AUTOLOOP_RESOURCE_CPU_SECONDS",
    "cpu_quota_percent": "AAS_AUTOLOOP_RESOURCE_CPU_QUOTA_PERCENT",
    "tasks_max": "AAS_AUTOLOOP_RESOURCE_MAX_PROCESSES",
    "open_files_max": "AAS_AUTOLOOP_RESOURCE_OPEN_FILES",
    "file_size_mib": "AAS_AUTOLOOP_RESOURCE_FILE_SIZE_MIB",
    "output_mib": "AAS_AUTOLOOP_RESOURCE_OUTPUT_MIB",
}

DEFAULTS = {
    "memory_mib": 4096,
    "swap_mib": 0,
    "address_space_mib": 65536,
    "cpu_quota_percent": 100,
    "tasks_max": 128,
    "open_files_max": 1024,
    "file_size_mib": 4096,
    "output_mib": 16,
}

BOUNDS = {
    "memory_mib": (1024, 262144),
    "swap_mib": (0, 262144),
    "address_space_mib": (2048, 524288),
    "cpu_seconds": (1, 172800),
    "cpu_quota_percent": (10, 6400),
    "tasks_max": (16, 4096),
    "open_files_max": (64, 65536),
    "file_size_mib": (2, 4096),
    "output_mib": (1, 16),
}


def _bounded_integer(
    name: str,
    default: int,
    source: Mapping[str, str],
) -> int:
    raw = str(source.get(RESOURCE_ENV[name]) or default).strip()
    try:
        value = int(raw, 10)
    except ValueError as exc:
        raise ProviderResourceError(
            f"{RESOURCE_ENV[name]} must be a base-10 integer"
        ) from exc
    lower, upper = BOUNDS[name]
    if not lower <= value <= upper:
        raise ProviderResourceError(
            f"{RESOURCE_ENV[name]} must be between {lower} and {upper}"
        )
    return value


def provider_resource_limits(
    wall_time_seconds: int,
    *,
    role: str = "primary",
    environ: Mapping[str, str] | None = None,
) -> dict[str, int]:
    """Return validated mandatory limits for one provider process tree."""

    if isinstance(wall_time_seconds, bool):
        raise ProviderResourceError("provider wall timeout must be an integer")
    try:
        wall_time = int(wall_time_seconds)
    except (TypeError, ValueError) as exc:
        raise ProviderResourceError("provider wall timeout must be an integer") from exc
    if wall_time < 1 or wall_time > 172800:
        raise ProviderResourceError(
            "provider wall timeout must be between 1 and 172800 seconds"
        )
    normalized_role = str(role or "").strip().lower().replace("_", "-")
    if normalized_role not in {"primary", "panel"}:
        raise ProviderResourceError("provider resource role must be primary or panel")
    source = os.environ if environ is None else environ
    role_defaults = dict(DEFAULTS)
    if normalized_role == "panel":
        role_defaults.update({"memory_mib": 3072, "tasks_max": 64})
    values = {
        name: _bounded_integer(name, default, source)
        for name, default in role_defaults.items()
    }
    cpu_default = max(30, min(172800, wall_time + 60))
    cpu_seconds = _bounded_integer("cpu_seconds", cpu_default, source)
    if values["address_space_mib"] <= values["memory_mib"]:
        raise ProviderResourceError(
            "AAS_AUTOLOOP_RESOURCE_ADDRESS_SPACE_MIB must exceed "
            "AAS_AUTOLOOP_RESOURCE_MEMORY_MIB"
        )
    if values["file_size_mib"] <= values["output_mib"]:
        raise ProviderResourceError(
            "AAS_AUTOLOOP_RESOURCE_FILE_SIZE_MIB must exceed "
            "AAS_AUTOLOOP_RESOURCE_OUTPUT_MIB"
        )
    mib = 1024 * 1024
    return {
        "wall_time_seconds": wall_time,
        "runtime_scope_seconds": min(172815, wall_time + 15),
        "memory_max_bytes": values["memory_mib"] * mib,
        "memory_swap_max_bytes": values["swap_mib"] * mib,
        "address_space_bytes": values["address_space_mib"] * mib,
        "cpu_time_seconds": cpu_seconds,
        "cpu_quota_percent": values["cpu_quota_percent"],
        "tasks_max": values["tasks_max"],
        "open_files_max": values["open_files_max"],
        "file_size_max_bytes": values["file_size_mib"] * mib,
        "output_max_bytes": values["output_mib"] * mib,
        "core_size_max_bytes": 0,
    }


def _trusted_host_binary(candidates: Sequence[Path], label: str) -> str:
    if not sys.platform.startswith("linux"):
        raise ProviderResourceError(
            f"trusted-local resource enforcement requires Linux {label}"
        )
    for candidate in candidates:
        try:
            info = os.lstat(candidate)
        except OSError:
            continue
        if (
            stat.S_ISREG(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and info.st_uid == 0
            and not info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            and os.access(candidate, os.X_OK)
        ):
            return str(candidate)
    raise ProviderResourceError(
        f"trusted-local resource enforcement requires a trusted {label} binary"
    )


def _trusted_python3() -> str:
    """Resolve a root-owned system Python without trusting a symlink or PATH."""

    candidates: list[Path] = []
    for alias in (Path("/usr/bin/python3"), Path("/bin/python3")):
        try:
            resolved = alias.resolve(strict=True)
        except OSError:
            continue
        if resolved not in candidates:
            candidates.append(resolved)
    return _trusted_host_binary(tuple(candidates), "Python 3")


_RESOURCE_GATE_SCRIPT = r"""
import json
import os
import resource
import sys
from pathlib import Path, PurePosixPath

def fail():
    sys.stderr.write("trusted-local resource gate rejected provider spawn\n")
    raise SystemExit(125)

try:
    expected = json.loads(sys.argv[1])
    if sys.argv[2] != "--" or len(sys.argv) < 4:
        fail()
    command = sys.argv[3:]
    if not command[0].startswith("/"):
        fail()

    entries = []
    for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines():
        hierarchy, controllers, cgroup_path = line.split(":", 2)
        if hierarchy == "0" and controllers == "":
            entries.append(cgroup_path)
    if len(entries) != 1:
        fail()
    relative = PurePosixPath(entries[0])
    if not relative.is_absolute() or ".." in relative.parts:
        fail()
    base = Path("/sys/fs/cgroup").resolve(strict=True)
    leaf = (base / str(relative).lstrip("/")).resolve(strict=True)
    if leaf != base and base not in leaf.parents:
        fail()
    if leaf.name != expected["scope_unit"]:
        fail()

    def read(name):
        return (leaf / name).read_text(encoding="utf-8").strip()

    memory_max = int(read("memory.max"))
    memory_swap_max = int(read("memory.swap.max"))
    pids_max = int(read("pids.max"))
    cpu_quota_text, cpu_period_text = read("cpu.max").split()
    if cpu_quota_text == "max":
        fail()
    cpu_quota = int(cpu_quota_text)
    cpu_period = int(cpu_period_text)
    limits = expected["limits"]
    if not (0 < memory_max <= limits["memory_max_bytes"]):
        fail()
    if not (0 <= memory_swap_max <= limits["memory_swap_max_bytes"]):
        fail()
    if not (0 < pids_max <= limits["tasks_max"]):
        fail()
    if cpu_period < 1 or cpu_quota < 1:
        fail()
    if cpu_quota * 100 != limits["cpu_quota_percent"] * cpu_period:
        fail()

    expected_rlimits = {
        resource.RLIMIT_AS: limits["address_space_bytes"],
        resource.RLIMIT_CPU: limits["cpu_time_seconds"],
        resource.RLIMIT_NOFILE: limits["open_files_max"],
        resource.RLIMIT_FSIZE: limits["file_size_max_bytes"],
        resource.RLIMIT_CORE: limits["core_size_max_bytes"],
    }
    for resource_id, expected_value in expected_rlimits.items():
        if resource.getrlimit(resource_id) != (expected_value, expected_value):
            fail()
except (IndexError, KeyError, OSError, TypeError, ValueError):
    fail()

os.execve(command[0], command, dict(os.environ))
""".strip()


_CONTROL_DIRECTORY_MASKS = (
    Path("/run/avahi-daemon"),
    Path("/run/cups"),
    Path("/run/dbus"),
    Path("/run/containerd"),
    Path("/run/libvirt"),
    Path("/run/lxd"),
    Path("/run/pcscd"),
    Path("/run/podman"),
    Path("/run/screen"),
    Path("/run/tailscale"),
)
_CONTROL_SOCKET_MASKS = (
    Path("/run/docker.sock"),
    Path("/run/lxd-installer.socket"),
    Path("/run/rpcbind.sock"),
    Path("/run/snapd-snap.socket"),
    Path("/run/snapd.socket"),
)


def provider_control_plane_mask_args() -> list[str]:
    """Hide host process-launch control planes from a trusted-local child.

    The outer host process must reach the user manager to create the resource
    scope.  The provider inside bubblewrap must not be able to reconnect to
    that manager (or a container manager) and ask it to launch work outside
    the scope.  These masks are not a hostile-provider filesystem sandbox;
    they preserve the explicitly trusted host view while making the cgroup
    descendant boundary meaningful.
    """

    if not sys.platform.startswith("linux"):
        raise ProviderResourceError(
            "trusted-local resource enforcement requires Linux control-plane masks"
        )
    runtime_dir = Path("/run/user") / str(os.getuid())
    try:
        runtime_info = os.lstat(runtime_dir)
    except OSError as exc:
        raise ProviderResourceError(
            "trusted-local resource enforcement requires the user runtime directory"
        ) from exc
    if stat.S_ISLNK(runtime_info.st_mode) or not stat.S_ISDIR(runtime_info.st_mode):
        raise ProviderResourceError(
            "trusted-local user runtime directory is not a real directory"
        )
    args = ["--tmpfs", str(runtime_dir)]
    tmux_dir = Path("/tmp") / f"tmux-{os.getuid()}"
    try:
        tmux_info = os.lstat(tmux_dir)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ProviderResourceError(
            "trusted-local tmux control-plane inspection failed"
        ) from exc
    else:
        if (
            stat.S_ISLNK(tmux_info.st_mode)
            or not stat.S_ISDIR(tmux_info.st_mode)
            or tmux_info.st_uid != os.getuid()
            or tmux_info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        ):
            raise ProviderResourceError(
                "trusted-local tmux control directory has an unsafe identity"
            )
        args.extend(["--tmpfs", str(tmux_dir)])
    for path in _CONTROL_DIRECTORY_MASKS:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ProviderResourceError(
                "trusted-local control-plane mask inspection failed"
            ) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ProviderResourceError(
                "trusted-local control-plane directory has an unsafe type"
            )
        args.extend(["--tmpfs", str(path)])
    for path in _CONTROL_SOCKET_MASKS:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ProviderResourceError(
                "trusted-local control-plane mask inspection failed"
            ) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISSOCK(info.st_mode):
            raise ProviderResourceError(
                "trusted-local control-plane socket has an unsafe type"
            )
        args.extend(["--ro-bind", "/dev/null", str(path)])
    return args


def trusted_local_containment_command(
    command: Sequence[str],
    *,
    cwd: Path,
) -> list[str]:
    """Wrap a trusted CLI in the required PID/mount containment boundary."""

    if (
        not command
        or not isinstance(command[0], str)
        or not command[0]
        or not all(isinstance(item, str) for item in command)
    ):
        raise ProviderResourceError("trusted-local command must be a non-empty argv")
    try:
        canonical_cwd = cwd.resolve(strict=True)
        cwd_info = os.lstat(canonical_cwd)
    except OSError as exc:
        raise ProviderResourceError(
            "trusted-local working directory must be a real directory"
        ) from exc
    if stat.S_ISLNK(cwd_info.st_mode) or not stat.S_ISDIR(cwd_info.st_mode):
        raise ProviderResourceError(
            "trusted-local working directory must be a real directory"
        )
    bwrap = _trusted_host_binary(
        (Path("/usr/bin/bwrap"), Path("/bin/bwrap")), "bubblewrap"
    )
    return [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-cgroup",
        "--bind",
        "/",
        "/",
        *provider_control_plane_mask_args(),
        "--tmpfs",
        "/sys/fs/cgroup",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--chdir",
        str(canonical_cwd),
        "--",
        *command,
    ]


def brokered_provider_containment_command(
    command: Sequence[str],
    *,
    cwd: Path,
    dependency_root: Path,
    synthetic_home: Path,
    config_mounts: Mapping[str, str] | None = None,
    broker_socket: Path | None = None,
) -> list[str]:
    """Build an allowlist-only provider filesystem view.

    Unlike the compatibility trusted-local boundary, this shape never binds
    the host root or user home.  It exposes the selected dependency closure,
    project, system runtime, optional selected config sources, and broker
    socket only.
    """

    if (
        not command
        or not isinstance(command[0], str)
        or not command[0]
        or not all(isinstance(item, str) for item in command)
    ):
        raise ProviderResourceError("brokered provider command must be non-empty")
    try:
        canonical_cwd = cwd.resolve(strict=True)
        canonical_dependency = dependency_root.resolve(strict=True)
        canonical_home = synthetic_home.resolve(strict=True)
    except OSError as exc:
        raise ProviderResourceError("brokered provider mount is unavailable") from exc
    if not all(path.is_dir() and not path.is_symlink() for path in (canonical_cwd, canonical_dependency, canonical_home)):
        raise ProviderResourceError("brokered provider mounts must be real directories")
    def within(candidate: Path, root: Path) -> bool:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False

    real_home = Path.home().resolve()
    if canonical_cwd == real_home or within(real_home, canonical_cwd):
        raise ProviderResourceError("brokered project root must not expose the user home")
    if canonical_dependency == Path("/") or canonical_dependency == real_home or within(
        real_home, canonical_dependency
    ):
        raise ProviderResourceError(
            "brokered dependency root must not expose the host root or user home"
        )

    sensitive_names = {".git-credentials", ".pypirc", ".npmrc", ".netrc"}
    inspected = 0
    for directory, directories, files in os.walk(canonical_cwd, followlinks=False):
        inspected += len(directories) + len(files)
        if inspected > 250_000:
            raise ProviderResourceError(
                "project credential-shadow scan exceeds the safety bound"
            )
        relative_dir = Path(directory).relative_to(canonical_cwd)
        if "force_loop_pin_backups" in relative_dir.parts:
            raise ProviderResourceError(
                "project contains a forbidden force-loop backup shadow"
            )
        for name in [*directories, *files]:
            if (
                name in sensitive_names
                or name == "force_loop.env"
                or name.startswith(".env")
                or name.startswith(".dev.vars")
            ):
                raise ProviderResourceError(
                    "project contains a credential-capable file excluded from provider mounts"
                )
    git_config = canonical_cwd / ".git" / "config"
    if git_config.is_symlink():
        raise ProviderResourceError("project git config must not be a symlink")
    if git_config.is_file():
        try:
            if git_config.stat().st_size > 1_000_000:
                raise ProviderResourceError("project git config exceeds safety bound")
            config_text = git_config.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError) as exc:
            raise ProviderResourceError("project git config could not be inspected") from exc
        lowered = config_text.lower()
        if (
            "[credential" in lowered
            or "helper =" in lowered
            or re.search(r"https?://[^/\s:@]+:[^@\s]+@", config_text)
        ):
            raise ProviderResourceError(
                "project git config contains credential-capable authority"
            )
    bwrap = _trusted_host_binary((Path("/usr/bin/bwrap"),), "bubblewrap")
    args = [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-cgroup",
    ]
    created: set[Path] = {Path("/")}

    def ensure_parents(destination: Path) -> None:
        parents = list(destination.parents)
        for parent in reversed(parents[:-1]):
            if parent != Path("/") and parent not in created:
                args.extend(["--dir", str(parent)])
                created.add(parent)

    def bind(source: Path, destination: Path, *, read_only: bool) -> None:
        ensure_parents(destination)
        args.extend(["--ro-bind" if read_only else "--bind", str(source), str(destination)])
        created.add(destination)

    args.extend(["--tmpfs", "/tmp", "--dir", "/var", "--tmpfs", "/var/tmp"])
    created.update({Path("/tmp"), Path("/var"), Path("/var/tmp")})

    for system_path in (Path("/usr"), Path("/bin"), Path("/sbin"), Path("/lib"), Path("/lib64")):
        if system_path.exists():
            bind(system_path.resolve(strict=True), system_path, read_only=True)
    # Do not expose all of /etc.  Bind only the non-secret runtime material
    # needed for dynamic linking, DNS, TLS verification, and timezone data.
    for system_path in (
        Path("/etc/ld.so.cache"),
        Path("/etc/resolv.conf"),
        Path("/etc/hosts"),
        Path("/etc/nsswitch.conf"),
        Path("/etc/gai.conf"),
        Path("/etc/services"),
        Path("/etc/localtime"),
        Path("/etc/ssl/certs"),
        Path("/etc/ssl/openssl.cnf"),
    ):
        if system_path.exists():
            bind(system_path.resolve(strict=True), system_path, read_only=True)
    bind(canonical_dependency, canonical_dependency, read_only=True)
    bind(canonical_cwd, canonical_cwd, read_only=False)
    bind(canonical_home, canonical_home, read_only=False)
    for target_text, source_text in sorted((config_mounts or {}).items()):
        source = Path(source_text).resolve(strict=True)
        target = Path(target_text)
        if not target.is_absolute() or not within(target, canonical_home):
            raise ProviderResourceError("provider config target escapes synthetic home")
        info = os.lstat(source)
        if stat.S_ISLNK(info.st_mode) or not (
            stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
        ):
            raise ProviderResourceError("selected provider config has unsafe type")
        if info.st_uid not in {0, os.getuid()} or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ProviderResourceError("selected provider config has unsafe ownership")
        bind(source, target, read_only=True)
    if broker_socket is not None:
        socket_path = broker_socket.resolve(strict=True)
        if not stat.S_ISSOCK(os.lstat(socket_path).st_mode):
            raise ProviderResourceError("compute broker endpoint is not a socket")
        bind(socket_path, socket_path, read_only=True)
    ensure_parents(Path("/sys/fs/cgroup"))
    args.extend(
        [
            "--tmpfs", "/sys/fs/cgroup",
            "--proc", "/proc",
            "--dev", "/dev",
            "--setenv", "HOME", str(canonical_home),
            "--chdir", str(canonical_cwd),
            "--",
            *command,
        ]
    )
    return args


def interpreter_bound_provider_command(command: Sequence[str]) -> list[str]:
    """Pin Node entry scripts to a trusted system interpreter.

    Provider package scripts commonly use ``#!/usr/bin/env node``.  A
    tool-enabled trusted-local primary needs its ordinary PATH for research
    tools, but that PATH must not decide the interpreter that establishes the
    attested provider identity.
    """

    if not command or not isinstance(command[0], str) or not command[0]:
        raise ProviderResourceError("provider command must be a non-empty argv")
    executable = Path(command[0])
    if executable.suffix.lower() != ".js":
        return [str(item) for item in command]
    node = _trusted_host_binary(
        (Path("/usr/bin/node"), Path("/bin/node")), "Node.js interpreter"
    )
    return [node, *[str(item) for item in command]]


def resource_limited_command(
    command: Sequence[str],
    wall_time_seconds: int,
    *,
    role: str,
    environ: Mapping[str, str] | None = None,
) -> tuple[list[str], dict[str, int], str]:
    """Wrap a command in a mandatory systemd scope and inherited POSIX limits."""

    if (
        not command
        or not isinstance(command[0], str)
        or not command[0]
        or not all(isinstance(item, str) for item in command)
    ):
        raise ProviderResourceError("provider command must be a non-empty argv")
    normalized_role = str(role or "").strip().lower().replace("_", "-")
    limits = provider_resource_limits(
        wall_time_seconds, role=normalized_role, environ=environ
    )
    systemd_run = _trusted_host_binary(
        (Path("/usr/bin/systemd-run"), Path("/bin/systemd-run")), "systemd-run"
    )
    prlimit = _trusted_host_binary(
        (Path("/usr/bin/prlimit"), Path("/bin/prlimit")), "prlimit"
    )
    env_binary = _trusted_host_binary(
        (Path("/usr/bin/env"), Path("/bin/env")), "env"
    )
    python = _trusted_python3()
    scope_unit = (
        f"aas-arl-{normalized_role}-{os.getpid()}-{uuid.uuid4().hex[:12]}.scope"
    )
    gate_expectation = json.dumps(
        {
            "schema_version": "provider_resource_gate.v1",
            "scope_unit": scope_unit,
            "limits": limits,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    wrapped = [
        systemd_run,
        "--user",
        "--scope",
        "--quiet",
        "--collect",
        "--no-ask-password",
        "--expand-environment=no",
        f"--unit={scope_unit}",
        "--property=KillMode=control-group",
        f"--property=MemoryMax={limits['memory_max_bytes']}",
        f"--property=MemorySwapMax={limits['memory_swap_max_bytes']}",
        f"--property=TasksMax={limits['tasks_max']}",
        f"--property=CPUQuota={limits['cpu_quota_percent']}%",
        f"--property=RuntimeMaxSec={limits['runtime_scope_seconds']}",
        "--property=OOMPolicy=kill",
        env_binary,
        "-u",
        "XDG_RUNTIME_DIR",
        "-u",
        "DBUS_SESSION_BUS_ADDRESS",
        prlimit,
        f"--as={limits['address_space_bytes']}",
        f"--cpu={limits['cpu_time_seconds']}",
        f"--nofile={limits['open_files_max']}",
        f"--fsize={limits['file_size_max_bytes']}",
        f"--core={limits['core_size_max_bytes']}",
        "--",
        python,
        "-I",
        "-S",
        "-c",
        _RESOURCE_GATE_SCRIPT,
        gate_expectation,
        "--",
        *command,
    ]
    return wrapped, limits, scope_unit


def resource_control_environment(
    child_environment: Mapping[str, str],
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Add only the user-manager endpoints needed by the outer scope client."""

    source = os.environ if environ is None else environ
    child = {str(key): str(value) for key, value in child_environment.items()}
    for name in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
        value = str(source.get(name) or "")
        if not value:
            raise ProviderResourceError(
                f"trusted-local resource enforcement requires {name}"
            )
        child[name] = value
    return child


def preflight_resource_backend(
    wall_time_seconds: int,
    *,
    role: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, int]:
    """Exercise the real scope/limit backend without launching a provider."""

    source = os.environ if environ is None else environ
    python = _trusted_python3()
    probe_script = """
from pathlib import Path

cgroup = Path('/proc/self/cgroup').read_text(encoding='utf-8').strip()
if cgroup != '0::/':
    raise SystemExit(125)
if (Path('/sys/fs/cgroup') / 'memory.max').exists():
    raise SystemExit(125)
""".strip()
    contained = trusted_local_containment_command(
        [python, "-I", "-S", "-c", probe_script], cwd=Path("/")
    )
    command, limits, scope_unit = resource_limited_command(
        contained, wall_time_seconds, role=role, environ=source
    )
    base_environment = {
        name: str(source[name])
        for name in ("HOME", "LANG", "LC_ALL", "LC_CTYPE", "USER")
        if str(source.get(name) or "")
    }
    execution_environment = resource_control_environment(
        base_environment, environ=source
    )
    cleanup_error: str | None = None
    execution_error: Exception | None = None
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            env=execution_environment,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        execution_error = exc
    finally:
        cleanup_error = cleanup_resource_scope(scope_unit)
    if cleanup_error is not None:
        raise ProviderResourceCleanupError(
            "trusted-local resource scope cleanup failed during preflight"
        )
    if execution_error is not None:
        raise ProviderResourceError(
            "trusted-local resource backend preflight failed"
        ) from execution_error
    if completed.returncode != 0:
        raise ProviderResourceError(
            "trusted-local resource backend preflight failed"
        )
    if completed.stdout.strip():
        raise ProviderResourceError(
            "trusted-local resource backend preflight produced unexpected output"
        )
    return limits


_SCOPE_UNIT_RE = re.compile(r"^aas-arl-(?:primary|panel)-[0-9]+-[0-9a-f]{12}\.scope$")


def cleanup_resource_scope(scope_unit: str) -> str | None:
    """Kill any remaining descendants in an exact host-created provider scope."""

    if _SCOPE_UNIT_RE.fullmatch(str(scope_unit or "")) is None:
        return "provider resource scope identity is invalid"
    try:
        systemctl = _trusted_host_binary(
            (Path("/usr/bin/systemctl"), Path("/bin/systemctl")), "systemctl"
        )
    except ProviderResourceError as exc:
        return str(exc)
    try:
        observed = subprocess.run(
            [
                systemctl,
                "--user",
                "show",
                "--property=LoadState",
                "--property=ActiveState",
                scope_unit,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if observed.returncode != 0:
            return "provider resource scope inspection failed"
        properties = {
            key: value
            for line in observed.stdout.splitlines()
            if "=" in line
            for key, value in [line.split("=", 1)]
        }
        if properties.get("LoadState") == "not-found":
            return None
        if properties.get("LoadState") != "loaded":
            return "provider resource scope inspection failed"
        if properties.get("ActiveState") in {"inactive", "failed"}:
            return None
        killed = subprocess.run(
            [
                systemctl,
                "--user",
                "kill",
                "--kill-whom=all",
                "--signal=KILL",
                scope_unit,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        # A scope that finishes deactivating between the inspection above and
        # this kill makes ``systemctl kill`` exit non-zero for a unit that is
        # already gone, which is the ordinary case right after the kernel OOM
        # killer tears one down.  A failed kill is therefore inconclusive on
        # its own; only the observed unit state decides.  The poll below still
        # fails closed, and it keeps the two outcomes distinct: a kill that was
        # refused while the scope kept running is a cleanup failure, while a
        # kill that was accepted and did not take effect is a survival.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            check = subprocess.run(
                [
                    systemctl,
                    "--user",
                    "show",
                    "--property=LoadState",
                    "--property=ActiveState",
                    scope_unit,
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if check.returncode != 0:
                return "provider resource scope inspection failed"
            state = {
                key: value
                for line in check.stdout.splitlines()
                if "=" in line
                for key, value in [line.split("=", 1)]
            }
            if state.get("LoadState") == "not-found" or state.get(
                "ActiveState"
            ) in {"inactive", "failed"}:
                return None
            time.sleep(0.05)
        if killed.returncode != 0:
            return "provider resource scope cleanup failed"
        return "provider resource scope survived cleanup"
    except (OSError, subprocess.TimeoutExpired):
        return "provider resource scope cleanup failed"


def _terminate_bounded_process_group(process: subprocess.Popen[bytes]) -> str | None:
    """Terminate the exact host-created process group and reap its leader."""

    cleanup_error: str | None = None
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        pass
    except PermissionError:
        cleanup_error = "provider process group could not be inspected"
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            except PermissionError:
                cleanup_error = cleanup_error or (
                    "provider process group could not be inspected"
                )
                break
            time.sleep(0.02)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                cleanup_error = cleanup_error or (
                    "provider process group could not be terminated"
                )
    if process.poll() is None:
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            cleanup_error = cleanup_error or (
                "provider process cleanup timed out"
            )
    return cleanup_error


def run_bounded_resource_process(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    timeout_s: int,
    output_limit_bytes: int,
    scope_unit: str | None,
    stdin_text: str | None = None,
    merge_stderr: bool = False,
) -> BoundedProcessResult:
    """Run a wrapped provider without ever buffering more than its output cap.

    Pipes are drained incrementally by the host.  At the first combined byte
    beyond the cap, or at the wall deadline, the host terminates both the
    process group and the systemd scope before returning.  This avoids the
    multi-gigabyte temporary-file window that a post-exit size check would
    otherwise create.
    """

    if not sys.platform.startswith("linux"):
        raise ProviderResourceError(
            "bounded trusted-local provider execution requires Linux"
        )
    if output_limit_bytes < 1 or output_limit_bytes > 256 * 1024 * 1024:
        raise ProviderResourceError("provider output limit is invalid")
    if timeout_s < 1:
        raise ProviderResourceError("provider timeout is invalid")
    if scope_unit is not None and _SCOPE_UNIT_RE.fullmatch(str(scope_unit)) is None:
        raise ProviderResourceError("provider resource scope identity is invalid")
    try:
        input_data = (stdin_text or "").encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ProviderResourceError("provider prompt is not valid UTF-8") from exc
    selector = selectors.DefaultSelector()
    stdout_data = bytearray()
    stderr_data = bytearray()
    total_output = 0
    timed_out = False
    oversized = False
    capture_error: str | None = None
    cleanup_error: str | None = None
    cleanup_done = False
    post_cleanup_deadline: float | None = None
    input_offset = 0
    stdin_delivery_failed = False
    return_code: int | None = None
    process: subprocess.Popen[bytes] | None = None
    execution_error: Exception | None = None

    try:
        stderr_target: int | None = (
            subprocess.STDOUT if merge_stderr else subprocess.PIPE
        )
        try:
            process = subprocess.Popen(
                [str(item) for item in command],
                stdout=subprocess.PIPE,
                stderr=stderr_target,
                stdin=(
                    subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL
                ),
                env={str(key): str(value) for key, value in env.items()},
                cwd=str(cwd),
                start_new_session=True,
            )
        except Exception as exc:
            scope_error = (
                cleanup_resource_scope(scope_unit)
                if scope_unit is not None
                else None
            )
            if scope_error is not None:
                raise ProviderResourceError(
                    "provider spawn failed and resource scope cleanup was not verified"
                ) from exc
            raise

        def finish_cleanup() -> None:
            nonlocal cleanup_done, cleanup_error, post_cleanup_deadline
            if cleanup_done:
                return
            try:
                group_error = _terminate_bounded_process_group(process)
            except Exception:
                group_error = "provider process group cleanup failed"
            try:
                scope_error = (
                    cleanup_resource_scope(scope_unit)
                    if scope_unit is not None
                    else None
                )
            except Exception:
                scope_error = "provider resource scope cleanup failed"
            cleanup_error = cleanup_error or group_error or scope_error
            cleanup_done = True
            post_cleanup_deadline = time.monotonic() + 1.0

        assert process.stdout is not None

        def register_read(stream: Any, label: str) -> None:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)

        register_read(process.stdout, "stdout")
        if not merge_stderr:
            assert process.stderr is not None
            register_read(process.stderr, "stderr")
        if process.stdin is not None:
            os.set_blocking(process.stdin.fileno(), False)
            if input_data:
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                process.stdin.close()

        deadline = time.monotonic() + timeout_s
        while True:
            now = time.monotonic()
            if not cleanup_done and not oversized and now >= deadline:
                timed_out = True
            if not cleanup_done and (timed_out or oversized):
                finish_cleanup()
            polled = process.poll()
            if polled is not None and not cleanup_done:
                return_code = int(polled)
                finish_cleanup()

            output_registered = any(
                key.data in {"stdout", "stderr"}
                for key in selector.get_map().values()
            )
            if cleanup_done and not output_registered:
                if input_offset < len(input_data):
                    capture_error = capture_error or (
                        "provider exited before the complete prompt was delivered"
                    )
                break
            if (
                cleanup_done
                and post_cleanup_deadline is not None
                and now >= post_cleanup_deadline
            ):
                cleanup_error = cleanup_error or (
                    "provider output pipes survived resource cleanup"
                )
                for key in list(selector.get_map().values()):
                    if key.data not in {"stdout", "stderr"}:
                        continue
                    try:
                        selector.unregister(key.fileobj)
                    except Exception:
                        pass
                    try:
                        key.fileobj.close()
                    except Exception:
                        pass
                break

            wait_s = 0.05
            if not cleanup_done:
                wait_s = max(0.0, min(wait_s, deadline - now))
            for key, _events in selector.select(wait_s):
                stream = key.fileobj
                label = str(key.data)
                if label == "stdin":
                    try:
                        sent = os.write(stream.fileno(), input_data[input_offset:])
                        input_offset += sent
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        stdin_delivery_failed = True
                    except OSError as exc:
                        if exc.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                            continue
                        capture_error = capture_error or (
                            "provider stdin delivery failed"
                        )
                        stdin_delivery_failed = True
                    if stdin_delivery_failed or input_offset >= len(input_data):
                        selector.unregister(stream)
                        stream.close()
                    continue
                try:
                    chunk = os.read(stream.fileno(), 65536)
                except BlockingIOError:
                    continue
                except OSError:
                    chunk = b""
                    capture_error = capture_error or "provider output capture failed"
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                remaining = max(0, output_limit_bytes - total_output)
                if remaining:
                    target = stdout_data if label == "stdout" else stderr_data
                    target.extend(chunk[:remaining])
                total_output += len(chunk)
                if total_output > output_limit_bytes:
                    oversized = True
                    finish_cleanup()
        if process.poll() is None:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                cleanup_error = cleanup_error or "provider process cleanup timed out"
        if return_code is None and process.returncode is not None:
            return_code = int(process.returncode)
    except Exception as exc:
        execution_error = exc
    finally:
        try:
            if process is not None and not cleanup_done:
                try:
                    group_error = _terminate_bounded_process_group(process)
                except Exception:
                    group_error = "provider process group cleanup failed"
                try:
                    scope_error = (
                        cleanup_resource_scope(scope_unit)
                        if scope_unit is not None
                        else None
                    )
                except Exception:
                    scope_error = "provider resource scope cleanup failed"
                cleanup_error = cleanup_error or group_error or scope_error
                cleanup_done = True
        finally:
            for key in list(selector.get_map().values()):
                try:
                    selector.unregister(key.fileobj)
                except Exception:
                    pass
                try:
                    key.fileobj.close()
                except Exception:
                    pass
            selector.close()

    if execution_error is not None:
        if cleanup_error is not None:
            raise ProviderProcessError(
                "provider execution failed and cleanup was not verified",
                cleanup_error=cleanup_error,
            ) from execution_error
        raise execution_error

    if stdin_delivery_failed or input_offset < len(input_data):
        capture_error = capture_error or (
            "provider exited before the complete prompt was delivered"
        )
    if timed_out:
        return_code = 124
    elif oversized:
        return_code = 126
    elif return_code is None:
        return_code = 1
    return BoundedProcessResult(
        return_code,
        bytes(stdout_data),
        bytes(stderr_data),
        timed_out,
        oversized,
        capture_error,
        cleanup_error,
    )


def public_resource_limits(limits: Mapping[str, Any]) -> dict[str, int]:
    """Normalize limit metadata before persistence or progress emission."""

    return {str(key): int(value) for key, value in sorted(limits.items())}
