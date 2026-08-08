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


def _pid_start_token(pid: int) -> str:
    """Return the kernel start-time of a PID, or "" where it is unavailable.

    A bare liveness probe cannot tell a live supervisor from a recycled PID,
    so the pidfile carries this token and stop refuses to signal a mismatch.
    """
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="ignore")
        # The comm field may contain spaces and parentheses; split after the
        # last ")" so field indices stay stable.
        return stat_line.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return ""


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


def _open_loop_tree_file(path: Path, extra_flags: int) -> int:
    """Open a loop-tree runtime file without following a planted link."""
    if os.name == "nt":  # pragma: no cover - PowerShell owns native Windows launch
        return os.open(str(path), os.O_WRONLY | os.O_CREAT | extra_flags, 0o600)
    directory = os.open(
        str(path.parent),
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        fd = os.open(
            path.name,
            os.O_WRONLY | os.O_CREAT | extra_flags | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory,
        )
    finally:
        os.close(directory)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or int(info.st_uid) != os.geteuid():
        os.close(fd)
        raise ValueError(f"loop runtime file is not an owner-owned regular file: {path}")
    return fd


def _write_pidfile(loop_dir: Path, pid: int) -> None:
    fd = _open_loop_tree_file(pid_path(loop_dir), os.O_TRUNC)
    try:
        os.write(fd, f"{pid} {_pid_start_token(pid)}\n".encode("utf-8"))
    finally:
        os.close(fd)


def _read_pidfile(loop_dir: Path) -> tuple[int, str]:
    """Return (pid, start token) from the pidfile; (0, "") when unusable."""
    pf = pid_path(loop_dir)
    if not pf.is_file():
        return 0, ""
    try:
        parts = pf.read_text(encoding="utf-8").split()
        return (int(parts[0]) if parts else 0), (parts[1] if len(parts) > 1 else "")
    except (OSError, ValueError):
        return 0, ""


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


def _iter_cmdlines() -> list[tuple[int, str, tuple[str, ...]]]:
    """Return (pid, cmdline, argv fields) triples portably."""
    results: list[tuple[int, str, tuple[str, ...]]] = []
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
            fields = tuple(f for f in raw.decode(errors="ignore").split("\0") if f)
            cmd = " ".join(fields)
            if cmd:
                results.append((int(entry.name), cmd, fields))
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
                        results.append((pid, cmd, tuple(cmd.split())))
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
                    results.append((int(pid_s), cmd, tuple(cmd.split())))
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
        f"--loop-tag {loop}",
        "arl_drive_supervisor",
        "autonomous_research_loop_runtime.py",
        "force_loop_cli.py",
    )
    pids: list[int] = []
    for pid, cmd, fields in _iter_cmdlines():
        # Identity is an exact argv field: a substring test makes .../run1
        # match the unrelated campaign .../run10.
        if loop not in fields:
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
        old, token = _read_pidfile(loop_dir)
        # An empty token means the pidfile came from LAUNCH_supervisor.sh, which
        # records the bare PID; fall back to the liveness-only test there.
        if old > 0 and _pid_alive(old) and (not token or token == _pid_start_token(old)):
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
    pid = _read_pidfile(loop_dir)[0] or None
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
        # bind_child_command opens argv[0] with O_NOFOLLOW; a symlinked
        # sys.executable (the common venv shape) would raise ELOOP there.
        os.path.realpath(sys.executable),
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
    # The supervisor takes its loop from LOOP_DIR and reads no positional
    # arguments, so --loop-tag is inert; it exists so the descriptor-bound
    # child stays matchable by find_loop_pids when the pidfile is lost.
    return ["/bin/bash", str(supervisor), "--loop-tag", str(loop_dir)]


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


def _ownership_fault(
    info: os.stat_result, where: Path, *, directory: bool
) -> str:
    """Describe why ``where`` is not owner-controlled, or return an empty string.

    The binding gate rejects any path an unprivileged third party could swap
    under the driver. Each rejection names the path, the observed mode, and the
    remedy so a failed ``force-loop start`` is actionable without a stat walk.
    """

    mode = stat.S_IMODE(info.st_mode)
    kind = "directory" if directory else "regular file"
    if directory and not stat.S_ISDIR(info.st_mode):
        return f"{where} is not a directory"
    if not directory and not stat.S_ISREG(info.st_mode):
        return f"{where} is not a regular file"
    if int(info.st_uid) not in {0, os.geteuid()}:
        return (
            f"{where} is a {kind} owned by uid {int(info.st_uid)}, "
            f"not root or the calling uid {os.geteuid()}"
        )
    sticky_public_dir = (
        directory and int(info.st_uid) == 0 and bool(info.st_mode & stat.S_ISVTX)
    )
    if mode & 0o022 and not sticky_public_dir:
        return (
            f"{where} is a group- or world-writable {kind} (mode {mode:04o}); "
            f"run 'chmod go-w {where}'"
        )
    if not directory and int(info.st_uid) != 0 and int(info.st_nlink) != 1:
        return (
            f"{where} has {int(info.st_nlink)} hard links, so another link "
            "could replace its contents"
        )
    return ""


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
    walked = Path(absolute.anchor or os.sep)
    try:
        for component in absolute.parts[1:-1]:
            child = os.open(component, parent_flags, dir_fd=parent)
            walked = walked / component
            info = os.fstat(child)
            # Name the offending ancestor and its mode: the operator cannot act
            # on "somewhere above this command is writable by someone else".
            fault = _ownership_fault(info, walked, directory=True)
            if fault:
                os.close(child)
                raise ValueError(f"child command ancestor {fault}")
            os.close(parent)
            parent = child
        fd = os.open(absolute.name, flags, dir_fd=parent)
    finally:
        os.close(parent)
    info = os.fstat(fd)
    fault = _ownership_fault(info, absolute, directory=False)
    if fault:
        os.close(fd)
        raise ValueError(f"child command {fault}")
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
    # argv references descriptors by embedded /proc/self/fd or /dev/fd paths,
    # so tuple order is free; sort for a deterministic record on every
    # platform (macOS dup ordering differs) — Popen's pass_fds is a set-like
    # allowlist, not positional.
    return BoundChildCommand(bound_argv, tuple(sorted(descriptors)))


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
        with open(_open_loop_tree_file(log, os.O_APPEND), "a", encoding="utf-8") as log_handle:
            proc = subprocess.Popen(
                argv,
                cwd=str(loop_dir.parent),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                pass_fds=pass_fds,
            )
            _write_pidfile(loop_dir, proc.pid)
            try:
                return int(proc.wait())
            finally:
                # An interrupted wait must not destroy the only handle to a
                # child that is still running.
                if not _pid_alive(proc.pid):
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
    # The detached child does NOT inherit this lock: flock is per open-file
    # description and the parent releases it after spawn so replace/stop can
    # work. Exclusion for this backend is cmd_start's already-running
    # precondition plus the pidfile, not the lock.
    try:
        log = log_path(loop_dir)
        log_handle = open(_open_loop_tree_file(log, os.O_APPEND), "a", encoding="utf-8")
        proc = subprocess.Popen(
            argv,
            cwd=str(loop_dir.parent),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            pass_fds=pass_fds,
        )
        _write_pidfile(loop_dir, proc.pid)
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
