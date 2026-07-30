#!/usr/bin/env python3
"""Inert revocation stub for retired remote-bridge path synchronization.

This filename remains in one compatibility release so managed upgrades replace
older executable newer-wins secrets/state copiers. Every entry point returns a
stable BLOCK result without inspecting or mutating either path.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _blocked() -> dict[str, Any]:
    return {
        "schema": "remote_bridge_path_sync.block.v1",
        "ok": False,
        "status": "blocked",
        "error_code": "bidirectional_sync_retired",
        "reason": (
            "host and workspace secrets/state are independent trust domains; "
            "bidirectional synchronization is retired"
        ),
        "paths_inspected": False,
        "paths_mutated": False,
    }


def default_paths() -> dict[str, Path]:
    """Compatibility API that exposes no host or workspace path."""

    return {}


def sync_secrets_file(_src_a: Path, _src_b: Path) -> dict[str, Any]:
    """Refuse the retired secrets-copy operation without reading either path."""

    return _blocked()


def sync_state_trees(_host: Path, _workspace: Path) -> dict[str, Any]:
    """Refuse the retired state-copy operation without reading either path."""

    return _blocked()


def sync_once(*, quiet: bool = True) -> dict[str, Any]:
    """Return the stable revocation result; never inspect environment paths."""

    result = _blocked()
    if not quiet:
        print(json.dumps(result, indent=2, sort_keys=True))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Retired remote-bridge path synchronization (always blocked)"
    )
    parser.add_argument("--json", action="store_true", help="print BLOCK result")
    parser.add_argument("--quiet", action="store_true", help="no stdout")
    args = parser.parse_args(argv)
    result = sync_once(quiet=True)
    if not args.quiet:
        print(json.dumps(result, indent=2, sort_keys=True))
    _ = args.json
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
