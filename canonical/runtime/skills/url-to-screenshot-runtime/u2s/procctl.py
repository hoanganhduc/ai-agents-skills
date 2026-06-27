"""Platform-split process launch + kill for the Chromium subprocess.

Import-time / platform safety is binding: every platform-specific API
(``os.killpg``/``signal.SIGKILL``/``os.setsid`` on POSIX;
``subprocess.CREATE_NEW_PROCESS_GROUP`` on Windows) is referenced ONLY inside an
``os.name``-guarded branch that does not execute on the other OS, never at module
top level. Therefore ``import u2s.procctl`` succeeds on every OS, and the offline
selftest can import the whole package on windows-latest and macos-latest CI.

``select_kill_strategy(os_name)`` is a pure function returning an inert
descriptor (a string label plus a lazily-bound callable) without touching the
absent platform API; it is unit-tested per ``os.name``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class KillStrategy:
    """Inert description of how a process tree is reaped on a given OS.

    ``name`` is a stable label (``"posix-killpg"`` / ``"windows-job-object"``).
    ``kill`` is a callable bound lazily; constructing the strategy never touches
    any platform-only symbol, so it is safe to build on any OS.
    """

    name: str
    kill: Callable[["subprocess.Popen"], None]


def _kill_posix(proc: "subprocess.Popen") -> None:
    # POSIX-only symbols are referenced only here, reached only when os.name == "posix".
    import signal

    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        pgid = None
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, OSError):
        return
    try:
        proc.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, OSError):
        return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass


def _kill_windows(proc: "subprocess.Popen") -> None:
    # Windows-only reaping; reached only when os.name == "nt".
    if proc.poll() is not None:
        return
    taskkill = shutil.which("taskkill")
    if taskkill:
        try:
            subprocess.run(
                [taskkill, "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
            )
        except (subprocess.SubprocessError, OSError):
            pass
    try:
        proc.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
        proc.wait(timeout=3)
    except (subprocess.SubprocessError, OSError):
        pass


def select_kill_strategy(os_name: str) -> KillStrategy:
    """Return the inert kill strategy for ``os_name`` (``"posix"`` or ``"nt"``).

    Pure: it binds a callable but never invokes a platform-only symbol, so it is
    safe to call on any OS for any ``os_name`` value.
    """
    if os_name == "nt":
        return KillStrategy(name="windows-job-object", kill=_kill_windows)
    return KillStrategy(name="posix-killpg", kill=_kill_posix)


def popen_kwargs(os_name: str) -> dict:
    """Process-group kwargs so the whole Chromium tree can be reaped together.

    POSIX: ``start_new_session=True`` (new session/process group).
    Windows: ``CREATE_NEW_PROCESS_GROUP`` (referenced only on ``nt``).
    """
    if os_name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def cleanup_profile_dir(path: Path, *, attempts: int = 5, delay: float = 0.1) -> bool:
    """Remove a temp profile dir, tolerating locked files (slow Windows reaps).

    Retries ``rmtree`` a few times, then does a final best-effort sweep ignoring
    errors. Returns ``True`` if the directory is gone afterward.
    """
    if not path.exists():
        return True
    for _ in range(max(1, attempts)):
        try:
            shutil.rmtree(path)
        except OSError:
            time.sleep(delay)
        if not path.exists():
            return True
    shutil.rmtree(path, ignore_errors=True)
    return not path.exists()
