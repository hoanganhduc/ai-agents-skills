#!/usr/bin/env python3
"""Entrypoint for the Codex Modal research-compute runtime skill."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Relative location of the broker configuration inside a data workspace.
COMPUTE_CONFIG_RELATIVE = "config/research-compute.toml"


def _code_workspace_root(skill_dir: Path) -> Path:
    """Return the directory that holds the ``research_compute`` package.

    Installed runtimes keep the package at the runtime root beside ``skills/``.
    CSR's immutable exact-pin generation retains the canonical source layout,
    where the package lives one level deeper under ``workspace/``.
    """

    root = skill_dir.parent.parent
    if (root / "research_compute").is_dir():
        return root
    source_layout = root / "workspace"
    if (source_layout / "research_compute").is_dir():
        return source_layout
    return root


def _normalize_data_workspace_env() -> None:
    """Point config and state resolution at a usable broker data workspace.

    ``research_compute.config.workspace_root`` trusts ``CODEX_RUNTIME_WORKSPACE``
    and ``OPENCLAW_WORKSPACE``.  Launchers running from an immutable generation
    export the read-only runtime root there, which carries no broker
    configuration and cannot hold broker state.  An explicit
    ``AAS_AUTOLOOP_COMPUTE_WORKSPACE`` pin wins (the name traverses the ARL
    credential broker's child-environment filter); otherwise drop overrides
    that lack a broker configuration so resolution falls back to the working
    directory.
    """

    pin = os.environ.get("AAS_AUTOLOOP_COMPUTE_WORKSPACE")
    if pin:
        pinned = Path(pin)
        if not pinned.is_absolute() or not (pinned / COMPUTE_CONFIG_RELATIVE).is_file():
            print(
                "AAS_AUTOLOOP_COMPUTE_WORKSPACE does not name a broker data workspace",
                file=sys.stderr,
            )
            raise SystemExit(2)
        resolved = str(pinned.resolve())
        os.environ["CODEX_RUNTIME_WORKSPACE"] = resolved
        os.environ["OPENCLAW_WORKSPACE"] = resolved
        return
    for key in ("CODEX_RUNTIME_WORKSPACE", "OPENCLAW_WORKSPACE"):
        value = os.environ.get(key)
        if value and not (Path(value).expanduser() / COMPUTE_CONFIG_RELATIVE).is_file():
            del os.environ[key]


def main() -> int:
    skill_dir = Path(__file__).resolve().parent
    workspace_root = _code_workspace_root(skill_dir)
    if str(workspace_root) not in sys.path:
        sys.path.insert(0, str(workspace_root))
    _normalize_data_workspace_env()

    from research_compute.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
