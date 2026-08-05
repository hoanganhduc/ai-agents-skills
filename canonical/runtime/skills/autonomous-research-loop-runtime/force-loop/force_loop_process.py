#!/usr/bin/env python3
"""Portable process helpers for force-loop start/stop/status.

Backends:
  foreground   — default on all OS
  posix_detach — optional --detach on POSIX
  systemd_user — optional Linux only when systemctl --user works

Does not require systemd. Stop matching uses portable cmdline inspection
(/proc on Linux, ps fallback elsewhere) — never /proc-only public API.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Optional: reuse runtime pid_alive when imported as sibling package path.
def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            # Windows: OpenProcess + wait with 0 timeout.
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):  # type: ignore[attr-defined]
                    return int(exit_code.value) == STILL_ACTIVE
                return False
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def systemd_user_available() -> bool:
    if sys.platform != "linux":
        return False
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    # Accept running / degraded / offline (user bus may still work).
    out = (proc.stdout or "").strip().lower()
    return proc.returncode in {0, 1} or out in {
        "running",
        "degraded",
        "offline",
        "maintenance",
    }


def select_backend(
    requested: str | None,
    *,
    detach: bool = False,
) -> str:
    """Choose process backend. Default always foreground unless requested."""
    req = (requested or "").strip().lower()
    if not req or req == "auto":
        if detach and os.name != "nt":
            return "posix_detach"
        return "foreground"
    if req == "foreground":
        return "foreground"
    if req in {"posix_detach", "detach"}:
        if os.name == "nt":
            raise ValueError("posix_detach is not available on Windows; use foreground")
        return "posix_detach"
    if req in {"systemd", "systemd_user"}:
        if not systemd_user_available():
            raise ValueError("systemd_user backend requested but systemctl --user unavailable")
        return "systemd_user"
    raise ValueError(f"unknown backend {requested!r}")


def lock_path(loop_dir: Path) -> Path:
    return loop_dir / "driver" / "supervisor.lock"


def pid_path(loop_dir: Path) -> Path:
    return loop_dir / "driver" / "supervisor.pid"


def log_path(loop_dir: Path) -> Path:
    return loop_dir / "driver_logs" / "supervisor.out"


def _ensure_driver_dirs(loop_dir: Path) -> None:
    (loop_dir / "driver").mkdir(parents=True, exist_ok=True)
    (loop_dir / "driver_logs").mkdir(parents=True, exist_ok=True)


class SupervisorLock:
    """Exclusive lock on driver/supervisor.lock (fcntl / msvcrt)."""

    def __init__(self, loop_dir: Path) -> None:
        self.path = lock_path(loop_dir)
        self._handle: Any = None

    def acquire_nonblocking(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self._handle.close()
            self._handle = None
            return False

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "SupervisorLock":
        if not self.acquire_nonblocking():
            raise BlockingIOError(f"lock held: {self.path}")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def _iter_cmdlines() -> list[tuple[int, str]]:
    """Return (pid, cmdline) pairs portably."""
    results: list[tuple[int, str]] = []
    proc_root = Path("/proc")
    if proc_root.is_dir() and os.name != "nt":
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "cmdline").read_bytes()
            except OSError:
                continue
            if not raw:
                continue
            cmd = raw.replace(b"\0", b" ").decode(errors="ignore").strip()
            if cmd:
                results.append((int(entry.name), cmd))
        return results
    # ps fallback (macOS, some containers, Windows via ps if present)
    try:
        if os.name == "nt":
            proc = subprocess.run(
                [
                    "wmic",
                    "process",
                    "get",
                    "ProcessId,CommandLine",
                    "/FORMAT:CSV",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout:
                for line in proc.stdout.splitlines()[1:]:
                    # CSV: Node,CommandLine,ProcessId
                    parts = line.strip().split(",")
                    if len(parts) < 3:
                        continue
                    try:
                        pid = int(parts[-1])
                    except ValueError:
                        continue
                    cmd = ",".join(parts[1:-1]).strip()
                    if cmd:
                        results.append((pid, cmd))
                if results:
                    return results
        proc = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    pid_s, cmd = line.split(None, 1)
                    results.append((int(pid_s), cmd))
                except ValueError:
                    continue
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return results


def find_loop_pids(loop_dir: Path) -> list[int]:
    """PIDs whose cmdline references drive/supervisor for this loop abs path."""
    loop = str(loop_dir.resolve())
    needles = (
        f"drive --dir {loop}",
        f"--dir {loop}",
        "arl_drive_supervisor",
        "autonomous_research_loop_runtime.py",
        "force_loop_cli.py",
    )
    pids: list[int] = []
    for pid, cmd in _iter_cmdlines():
        if loop not in cmd:
            continue
        if any(n in cmd for n in needles):
            if pid == os.getpid():
                continue
            pids.append(pid)
    return sorted(set(pids))


def stop_loop_processes(loop_dir: Path, *, grace_seconds: float = 2.0) -> list[int]:
    """TERM then KILL matching PIDs; clear pidfile. Returns stopped pids."""
    stopped: list[int] = []
    pf = pid_path(loop_dir)
    if pf.is_file():
        try:
            old = int(pf.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            old = 0
        if old > 0 and _pid_alive(old):
            _terminate_pid(old)
            stopped.append(old)
        try:
            pf.unlink()
        except OSError:
            pass
    for pid in find_loop_pids(loop_dir):
        _terminate_pid(pid)
        stopped.append(pid)
    if stopped:
        time.sleep(grace_seconds)
        for pid in list(set(stopped)):
            if _pid_alive(pid):
                _kill_pid(pid)
    return sorted(set(stopped))


def _terminate_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _kill_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def status_snapshot(loop_dir: Path) -> dict[str, Any]:
    loop_dir = loop_dir.resolve()
    pf = pid_path(loop_dir)
    pid: int | None = None
    if pf.is_file():
        try:
            pid = int(pf.read_text(encoding="utf-8").strip() or "0") or None
        except ValueError:
            pid = None
    pids = find_loop_pids(loop_dir)
    lock = lock_path(loop_dir)
    return {
        "loop_dir": str(loop_dir),
        "pidfile": str(pf),
        "pidfile_pid": pid,
        "pidfile_alive": bool(pid and _pid_alive(pid)),
        "matched_pids": pids,
        "lock_path": str(lock),
        "lock_exists": lock.is_file(),
        "stop_flag": (loop_dir / "STOP_REQUESTED").is_file(),
        "pause_flag": (loop_dir / "PAUSE").is_file(),
        "systemd_user_available": systemd_user_available(),
        "default_backend": select_backend(None),
    }


def build_drive_command(
    *,
    runtime_py: Path,
    loop_dir: Path,
    project_root: Path,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build a portable drive argv (no shell)."""
    cmd = [
        sys.executable,
        str(runtime_py),
        "drive",
        "--dir",
        str(loop_dir),
        "--root",
        str(project_root),
    ]
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def build_supervisor_command(
    *,
    pack_parent: Path,
    loop_dir: Path,
    project_root: Path,
) -> list[str] | None:
    """POSIX supervisor script if present; else None (caller uses drive)."""
    supervisor = pack_parent / "arl_drive_supervisor.sh"
    if not supervisor.is_file() or os.name == "nt":
        return None
    return ["/bin/bash", str(supervisor)]


@dataclass
class BoundChildCommand:
    argv: list[str]
    pass_fds: tuple[int, ...]

    def close(self) -> None:
        for fd in self.pass_fds:
            try:
                os.close(fd)
            except OSError:
                pass


def _open_bound_regular(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    parent_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    parent = os.open(absolute.anchor or os.sep, parent_flags)
    try:
        for component in absolute.parts[1:-1]:
            child = os.open(component, parent_flags, dir_fd=parent)
            info = os.fstat(child)
            if (
                not stat.S_ISDIR(info.st_mode)
                or int(info.st_uid) not in {0, os.geteuid()}
                or (
                    stat.S_IMODE(info.st_mode) & 0o022
                    and not (int(info.st_uid) == 0 and info.st_mode & stat.S_ISVTX)
                )
            ):
                os.close(child)
                raise ValueError("child command ancestor is not owner-controlled")
            os.close(parent)
            parent = child
        fd = os.open(absolute.name, flags, dir_fd=parent)
    finally:
        os.close(parent)
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or int(info.st_uid) not in {0, os.geteuid()}
        or stat.S_IMODE(info.st_mode) & 0o022
        or (int(info.st_uid) != 0 and int(info.st_nlink) != 1)
    ):
        os.close(fd)
        raise ValueError("child command is not an owner-controlled regular file")
    os.set_inheritable(fd, True)
    return fd


def _fd_path(fd: int) -> str:
    for prefix in ("/proc/self/fd", "/dev/fd"):
        candidate = f"{prefix}/{fd}"
        if os.path.exists(candidate):
            return candidate
    raise ValueError("descriptor execution paths are unavailable")


def bind_child_command(argv: list[str]) -> BoundChildCommand:
    """Bind interpreter and script identities before credentials are loaded."""
    if not argv:
        raise ValueError("child command is empty")
    if os.name == "nt":  # pragma: no cover - PowerShell owns native binding
        return BoundChildCommand(list(argv), ())
    executable = argv[0]
    if executable == "/bin/bash":
        canonical = Path("/bin/bash").resolve()
        if canonical not in {Path("/bin/bash"), Path("/usr/bin/bash")}:
            raise ValueError("/bin/bash resolves outside the system tool root")
        executable_path = canonical
        executable_fd = _open_bound_regular(executable_path)
    else:
        executable_path = Path(executable)
        if not executable_path.is_absolute():
            raise ValueError("child interpreter must be an absolute path")
        if executable.startswith("/proc/self/fd/") or executable.startswith("/dev/fd/"):
            try:
                original_fd = int(executable.rsplit("/", 1)[1])
                executable_fd = os.dup(original_fd)
                os.set_inheritable(executable_fd, True)
            except (OSError, ValueError) as exc:
                raise ValueError("could not bind inherited child interpreter") from exc
        else:
            executable_fd = _open_bound_regular(executable_path)
    descriptors = [executable_fd]
    bound_argv = [_fd_path(executable_fd), *argv[1:]]
    if len(argv) > 1 and Path(argv[1]).is_absolute() and Path(argv[1]).suffix in {".py", ".sh"}:
        try:
            script_fd = _open_bound_regular(Path(argv[1]))
        except Exception:
            os.close(executable_fd)
            raise
        descriptors.append(script_fd)
        bound_argv[1] = _fd_path(script_fd)
    return BoundChildCommand(bound_argv, tuple(descriptors))


def run_foreground(
    argv: list[str],
    *,
    loop_dir: Path,
    env: dict[str, str] | None = None,
    pass_fds: tuple[int, ...] = (),
) -> int:
    """Run supervisor/drive in foreground while holding exclusive lock."""
    _ensure_driver_dirs(loop_dir)
    lock = SupervisorLock(loop_dir)
    if not lock.acquire_nonblocking():
        return 10  # lock held (same as LAUNCH_supervisor)
    try:
        log = log_path(loop_dir)
        with open(log, "a", encoding="utf-8") as log_handle:
            proc = subprocess.Popen(
                argv,
                cwd=str(loop_dir.parent),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                pass_fds=pass_fds,
            )
            pid_path(loop_dir).write_text(f"{proc.pid}\n", encoding="utf-8")
            try:
                return int(proc.wait())
            finally:
                try:
                    pid_path(loop_dir).unlink()
                except OSError:
                    pass
    finally:
        lock.release()


def run_posix_detach(
    argv: list[str],
    *,
    loop_dir: Path,
    env: dict[str, str] | None = None,
    pass_fds: tuple[int, ...] = (),
) -> int:
    """Start process with nohup-style detach + pidfile (POSIX)."""
    if os.name == "nt":
        raise ValueError("posix_detach unsupported on Windows")
    _ensure_driver_dirs(loop_dir)
    lock = SupervisorLock(loop_dir)
    if not lock.acquire_nonblocking():
        return 10
    # Keep lock in parent only briefly: child inherits then parent exits after spawn.
    # For detach, we release after spawn so replace/stop can work; lock file still
    # marks presence via pidfile.
    try:
        log = log_path(loop_dir)
        log_handle = open(log, "a", encoding="utf-8")
        proc = subprocess.Popen(
            argv,
            cwd=str(loop_dir.parent),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            pass_fds=pass_fds,
        )
        pid_path(loop_dir).write_text(f"{proc.pid}\n", encoding="utf-8")
        log_handle.close()
        print(f"detached pid {proc.pid}")
        print(f"log: {log}")
        return 0
    finally:
        lock.release()


__all__ = [
    "SupervisorLock",
    "build_drive_command",
    "build_supervisor_command",
    "bind_child_command",
    "find_loop_pids",
    "run_foreground",
    "run_posix_detach",
    "select_backend",
    "status_snapshot",
    "stop_loop_processes",
    "systemd_user_available",
]
