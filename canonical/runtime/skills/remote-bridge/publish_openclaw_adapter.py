#!/usr/bin/env python3
"""Publish remote-bridge dual-route adapter into OpenClaw workspace skills.

Source of truth: this directory (canonical or installed runtime copy).
Destination default: ~/.openclaw/workspace/skills/aas-remote-bridge

Does not print or read secret values. Copies only skill scripts and SKILL.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent


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


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def plan_copies(src_root: Path, dest: Path) -> list[dict[str, Path]]:
    """Return list of {src, dest} mappings to publish."""
    skill_md = src_root / "openclaw-adapter" / "SKILL.md"
    if not skill_md.is_file():
        # Allow running from a dest that already has openclaw-adapter missing
        # only when called from full runtime tree.
        raise FileNotFoundError(f"missing adapter SKILL.md: {skill_md}")
    pairs = [
        (skill_md, dest / "SKILL.md"),
        (src_root / "dispatch_aas.py", dest / "scripts" / "dispatch_aas.py"),
        (
            src_root / "sync_remote_bridge_paths.py",
            dest / "scripts" / "sync_remote_bridge_paths.py",
        ),
        (src_root / "remote_bridge.py", dest / "vendor" / "remote_bridge.py"),
        (
            src_root / "sync_remote_bridge_paths.py",
            dest / "vendor" / "sync_remote_bridge_paths.py",
        ),
    ]
    out: list[dict[str, Path]] = []
    for src, dst in pairs:
        if not src.is_file():
            raise FileNotFoundError(f"missing source file: {src}")
        out.append({"src": src, "dest": dst})
    return out


def publish(*, dest: Path, dry_run: bool = False) -> dict[str, Any]:
    pairs = plan_copies(_HERE, dest)
    actions: list[dict[str, Any]] = []
    for pair in pairs:
        src: Path = pair["src"]
        dst: Path = pair["dest"]
        src_hash = _sha256(src)
        if dst.is_file() and _sha256(dst) == src_hash:
            actions.append(
                {
                    "path": str(dst),
                    "action": "noop",
                    "reason": "identical",
                    "sha256": src_hash[:12],
                }
            )
            continue
        action = "create" if not dst.exists() else "update"
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            # scripts should be executable-ish on POSIX
            if dst.suffix == ".py":
                try:
                    mode = dst.stat().st_mode
                    dst.chmod(mode | 0o111)
                except OSError:
                    pass
        actions.append(
            {
                "path": str(dst),
                "action": action if not dry_run else f"would_{action}",
                "sha256": src_hash[:12],
            }
        )
    # Drop a small provenance marker (no secrets).
    marker = dest / ".aas-published.json"
    payload = {
        "schema": "aas.openclaw_aas_remote_bridge.publish.v1",
        "source_root": str(_HERE),
        "dest": str(dest),
        "files": [
            {"path": a["path"], "action": a["action"], "sha256_12": a.get("sha256")}
            for a in actions
        ],
    }
    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"ok": True, "dry_run": dry_run, "dest": str(dest), "actions": actions}


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
    try:
        result = publish(dest=dest, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        err = {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:300]}
        print(json.dumps(err, indent=2, sort_keys=True))
        return 1
    # Always print JSON audit (no secret values); --json kept for CLI symmetry.
    print(json.dumps(result, indent=2, sort_keys=True))
    _ = args.json
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
