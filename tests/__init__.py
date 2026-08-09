"""Test package for shared test helpers."""

from __future__ import annotations

import functools
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

# The autonomous-research-loop runtime rejects any loop or artifact path that
# crosses a symlink, so tests must hand it a fully resolved directory. On macOS
# the default temp directory is ``/var/folders/...`` and ``/var`` is a symlink to
# ``/private/var``, which makes every ``tempfile`` path trip that check. Resolve
# the process-wide temp directory once, at package import time, so ``mkdtemp``
# and ``TemporaryDirectory`` yield link-free paths on every platform. ``TMPDIR``
# is exported as well because ``tempfile`` reads it first, which keeps child
# processes spawned by tests on the same resolved directory.
tempfile.tempdir = os.path.realpath(tempfile.gettempdir())
os.environ["TMPDIR"] = tempfile.tempdir

# Tests that run a runtime dispatcher as a subprocess make it import modules out
# of the canonical tree, and the import leaves ``__pycache__`` directories
# behind. The runtime inventory check denies any source it did not enrol, so the
# second run of the suite on the same checkout fails on the first run's
# bytecode. Disable it for every child the suite spawns; the parent already
# holds its own bytecode from before this module was imported.
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

# The parent needs the same treatment for a different reason: several modules
# load canonical sources directly through ``spec_from_file_location``, which
# happens after this package is imported and writes ``__pycache__`` beside the
# source. A full run hides it, because whichever module sets this first covers
# every module collected after it. Running one file on its own does not, so the
# next inventory check fails on bytecode the previous command left behind.
sys.dont_write_bytecode = True

# Windows CPython cannot seed its hash randomization without ``SYSTEMROOT`` and
# aborts with ``_Py_HashRandomization_Init: failed to get random numbers`` before
# reaching ``main``. Tests that hand a child an explicit ``env=`` therefore have
# to carry the OS identity forward, or the child exits 1 on Windows no matter
# what the code under test would have decided. These are plain OS configuration
# values, never secret material, so forwarding them does not weaken the
# cross-lane isolation those tests assert.
_OS_CHILD_ENV_KEYS = ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP")


def os_child_env() -> dict[str, str]:
    """Return the minimum OS identity a fresh interpreter needs in order to start."""
    return {key: os.environ[key] for key in _OS_CHILD_ENV_KEYS if key in os.environ}


# ``run_python.ps1`` demands a pinned, signature-verified interpreter the moment
# it sees a secret pointer or ``LEANEXPLORE_API_KEY`` in its environment. Tests
# that exercise interpreter discovery hand it a stub or a temp-tree interpreter,
# so a developer shell exporting any of these turns that group red for a reason
# unrelated to the behaviour under test. A full-suite run happened to hide it,
# because an unrelated module pops the LeanExplore key at import time. Scrub the
# triggers from the child environment instead; the credential gate keeps its own
# coverage in the tests that set these deliberately.
_SECRET_TRIGGER_KEYS = (
    "AAS_CALIBRE_SECRETS_FILE",
    "AAS_COMPUTE_SECRETS_FILE",
    "AAS_FILE_DELIVERY_SECRETS_FILE",
    "AAS_PROVIDER_SECRETS_FILE",
    "AAS_RUNTIME_REQUIRE_TRUSTED",
    "AAS_SKILL_SECRETS_FILE",
    "AAS_ZOTERO_SECRETS_FILE",
    "LEANEXPLORE_API_KEY",
    "REMOTE_BRIDGE_SECRETS_FILE",
    "SEND_EMAIL_SECRETS_FILE",
)


def runner_child_env() -> dict[str, str]:
    """Return the process environment without the runner's secret-bearing triggers."""
    env = os.environ.copy()
    for key in _SECRET_TRIGGER_KEYS:
        env.pop(key, None)
    return env


@functools.lru_cache(maxsize=1)
def _state_dacl_guard_refusal() -> str:
    """Return why ``private_path_guard`` refuses a temp state directory, or ``""``.

    The broker guards every state directory with ``private_path_guard``, which
    denies any ACE outside owner/SYSTEM/Administrators/TrustedInstaller. Whether
    a ``tempfile`` directory satisfies that is a property of the host, not of
    the platform: a profile carrying an orphaned ACE from a former domain fails,
    a clean one passes. Ask the guard itself rather than assuming, so these
    tests run wherever they can and report the real reason where they cannot.
    """
    if os.name != "nt":
        return ""

    from installer.ai_agents_skills.runtime import RUNTIME_SOURCE_ROOT

    workspace = str(RUNTIME_SOURCE_ROOT / "workspace")
    if workspace not in sys.path:
        sys.path.insert(0, workspace)
    from research_compute.windows_acl import private_path_guard

    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "state"
        probe.mkdir(mode=0o700)
        try:
            with private_path_guard(probe, directory=True):
                pass
        except OSError as exc:
            return str(exc) or exc.__class__.__name__
    return ""


def state_dacl_skip() -> Callable[[Any], Any]:
    """Return a decorator that skips only where the host's ACLs force it."""
    refusal = _state_dacl_guard_refusal()
    return unittest.skipIf(
        bool(refusal),
        f"runner temp state dirs trip the strict windows_acl gate: {refusal!r}",
    )
