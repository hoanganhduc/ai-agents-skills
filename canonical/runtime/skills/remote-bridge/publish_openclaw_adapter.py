#!/usr/bin/env python3
"""Fail-closed placeholder for OpenClaw adapter publishing.

Source of truth: this directory (canonical or installed runtime copy).
Destination default: ~/.openclaw/workspace/skills/aas-remote-bridge

Publishing into a lower-trust workspace is intentionally disabled until a
descriptor-pinned, no-follow, recoverable publisher is implemented and
reviewed. This command never inspects or mutates the destination.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

def default_dest() -> Path:
    raw = os.environ.get("AAS_OPENCLAW_AAS_REMOTE_BRIDGE_DEST") or os.environ.get(
        "OPENCLAW_AAS_REMOTE_BRIDGE_DEST"
    )
    if raw:
        return Path(raw).expanduser()
    ws = Path(
        os.environ.get("OPENCLAW_WORKSPACE")
        or os.environ.get("AAS_OPENCLAW_WORKSPACE")
        or (Path.home() / ".openclaw" / "workspace")
    )
    return ws / "skills" / "aas-remote-bridge"


def publish(*, dest: Path, dry_run: bool = False) -> dict[str, Any]:
    """Return a stable BLOCK result without touching ``dest``."""

    return {
        "schema": "aas.openclaw_aas_remote_bridge.publish-block.v1",
        "ok": False,
        "status": "blocked",
        "error_code": "publisher_security_boundary_unavailable",
        "reason": (
            "OpenClaw adapter publishing is disabled until destination traversal "
            "and writes are descriptor-pinned, no-follow, recoverable, and reviewed"
        ),
        "dest": str(dest),
        "dry_run": bool(dry_run),
        "destination_inspected": False,
        "destination_mutated": False,
        "actions": [],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Publish aas-remote-bridge adapter into OpenClaw workspace"
    )
    ap.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="Destination skill dir (default: ~/.openclaw/workspace/skills/aas-remote-bridge)",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true", help="print audit JSON")
    args = ap.parse_args(argv)
    dest = args.dest.expanduser() if args.dest else default_dest()
    result = publish(dest=dest, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True))
    _ = args.json
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
