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
