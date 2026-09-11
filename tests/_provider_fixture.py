"""Attested provider executables for the autonomous-loop tests.

The attestation gate refuses a binary whose parent directory is writable by
anyone else, so the fixture tree has to sit beside the real home rather than in
the system temporary directory. The cost of that placement is that a run killed
before ``cleanup`` leaves a directory in the home: six such trees, dated across
two weeks, were found there. Construction therefore sweeps the stale ones first,
which makes the leak self-healing instead of cumulative.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path

FIXTURE_PREFIX = ".aas-provider-fixture-"
# Long enough that a concurrent suite never sweeps a live sibling's tree.
STALE_AFTER_SECONDS = 24 * 60 * 60


def sweep_stale_fixtures(parent: Path, now: float | None = None) -> list[Path]:
    """Remove fixture trees abandoned by an earlier run, newest ones untouched."""

    moment = time.time() if now is None else now
    removed: list[Path] = []
    try:
        entries = sorted(parent.iterdir())
    except OSError:  # pragma: no cover - unreadable home
        return removed
    for entry in entries:
        if not entry.name.startswith(FIXTURE_PREFIX):
            continue
        if entry.is_symlink() or not entry.is_dir():
            continue
        try:
            age = moment - entry.stat().st_mtime
        except OSError:  # pragma: no cover - vanished mid-sweep
            continue
        if age < STALE_AFTER_SECONDS:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed.append(entry)
    return removed


class ProviderAttestationFixture:
    """A private tree of shim executables plus the environment that attests them."""

    def __init__(self, families: dict[str, str]) -> None:
        safe_parent = Path(os.path.realpath(Path.home()))
        sweep_stale_fixtures(safe_parent)
        self._temporary = tempfile.TemporaryDirectory(
            prefix=FIXTURE_PREFIX, dir=safe_parent
        )
        self.root = Path(self._temporary.name)
        self.paths: dict[str, Path] = {}
        self.environment: dict[str, str] = {}
        python = str(Path(os.path.realpath(sys.executable)))
        for provider, family in families.items():
            dependency_root = self.root / "providers" / provider
            dependency_root.mkdir(parents=True, mode=0o700)
            if os.name == "posix":
                (self.root / "providers").chmod(0o700)
                dependency_root.chmod(0o700)
            suffix = ".exe" if os.name == "nt" else ""
            path = dependency_root / f"{provider}{suffix}"
            if os.name == "nt":  # pragma: no cover - Windows CI fixture
                path.write_bytes(Path(python).read_bytes())
            else:
                path.write_text(
                    f"#!/bin/sh\nexec {shlex.quote(python)} \"$@\"\n",
                    encoding="utf-8",
                )
                path.chmod(0o700)
            digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            key = provider.upper()
            self.paths[provider] = path
            self.environment.update(
                {
                    f"AAS_AUTOLOOP_ATTESTED_BIN_{key}": str(path),
                    f"AAS_AUTOLOOP_ATTESTED_SHA256_{key}": digest,
                    f"AAS_AUTOLOOP_ATTESTED_UPSTREAM_{key}": family,
                    f"AAS_AUTOLOOP_ATTESTED_MODEL_{key}": f"{provider}-test-model",
                    f"AAS_AUTOLOOP_ATTESTED_DEPENDENCY_ROOT_{key}": str(
                        dependency_root
                    ),
                }
            )

    def cleanup(self) -> None:
        self._temporary.cleanup()

    def __enter__(self) -> "ProviderAttestationFixture":
        return self

    def __exit__(self, *_: object) -> None:
        self.cleanup()
