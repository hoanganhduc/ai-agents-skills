"""Test package for shared test helpers."""

from __future__ import annotations

import os
import tempfile

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
