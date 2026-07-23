#!/usr/bin/env python3
"""Sync remote-bridge secrets + mailbox state between host and OpenClaw workspace.

Why: OpenClaw sandbox cannot bind-mount ~/.config. Secrets/state live in the
workspace for sandbox agents, while host ARL notify uses ~/.config and
~/.local/share. This keeps both sides aligned on update.

Default paths (overridable via env):

  Host secrets:  $REMOTE_BRIDGE_HOST_SECRETS
                 or ~/.config/remote-bridge/secrets.json
  Workspace:     $REMOTE_BRIDGE_WORKSPACE_SECRETS
                 or ~/.openclaw/workspace/secrets/remote-bridge/secrets.json
  Host state:    $AAS_REMOTE_BRIDGE_HOST_STATE
                 or ~/.local/share/ai-agents-skills/remote-bridge
  Workspace:     $AAS_REMOTE_BRIDGE_WORKSPACE_STATE
                 or ~/.openclaw/workspace/.remote-bridge-state

Rules:
  - secrets.json: newer mtime wins (content-equal short-circuits)
  - state trees: per-file newer wins under jobs/ and bridge/ (and top-level json)
  - never prints secret values
  - no-op success when one side is missing
  - disable with AAS_REMOTE_BRIDGE_SYNC=0
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any


def _home() -> Path:
    return Path.home()


def default_paths() -> dict[str, Path]:
    home = _home()
    # Prefer explicit env; fall back to standard host + OpenClaw workspace.
    openclaw_ws = Path(
        os.environ.get("OPENCLAW_WORKSPACE")
        or os.environ.get("AAS_OPENCLAW_WORKSPACE")
        or (home / ".openclaw" / "workspace")
    )
    return {
        "host_secrets": Path(
            os.environ.get("REMOTE_BRIDGE_HOST_SECRETS")
            or (home / ".config" / "remote-bridge" / "secrets.json")
        ),
        "ws_secrets": Path(
            os.environ.get("REMOTE_BRIDGE_WORKSPACE_SECRETS")
            or (openclaw_ws / "secrets" / "remote-bridge" / "secrets.json")
        ),
        "host_state": Path(
            os.environ.get("AAS_REMOTE_BRIDGE_HOST_STATE")
            or (home / ".local" / "share" / "ai-agents-skills" / "remote-bridge")
        ),
        "ws_state": Path(
            os.environ.get("AAS_REMOTE_BRIDGE_WORKSPACE_STATE")
            or (openclaw_ws / ".remote-bridge-state")
        ),
    }


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _same_bytes(a: Path, b: Path) -> bool:
    try:
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def sync_secrets_file(src_a: Path, src_b: Path) -> dict[str, Any]:
    """Copy newer secrets.json onto the older path. Returns a small audit dict."""
    a_exists, b_exists = src_a.is_file(), src_b.is_file()
    if not a_exists and not b_exists:
        return {"action": "skip", "reason": "neither_secrets_present"}
    if a_exists and not b_exists:
        src_b.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_a, src_b)
        try:
            os.chmod(src_b, 0o600)
        except OSError:
            pass
        return {"action": "copy", "direction": "a_to_b", "bytes": src_a.stat().st_size}
    if b_exists and not a_exists:
        src_a.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_b, src_a)
        try:
            os.chmod(src_a, 0o600)
        except OSError:
            pass
        return {"action": "copy", "direction": "b_to_a", "bytes": src_b.stat().st_size}
    if _same_bytes(src_a, src_b):
        # Keep mtimes aligned lightly if equal content
        return {"action": "noop", "reason": "identical"}
    ma, mb = _mtime(src_a), _mtime(src_b)
    if ma >= mb:
        shutil.copy2(src_a, src_b)
        try:
            os.chmod(src_b, 0o600)
        except OSError:
            pass
        return {"action": "copy", "direction": "a_to_b", "winner": "a"}
    shutil.copy2(src_b, src_a)
    try:
        os.chmod(src_a, 0o600)
    except OSError:
        pass
    return {"action": "copy", "direction": "b_to_a", "winner": "b"}


def _iter_state_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # skip caches / pyc
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".git"}]
        base = Path(dirpath)
        for name in filenames:
            if name.endswith((".pyc", ".tmp", ".swp")):
                continue
            out.append(base / name)
    return out


def sync_state_trees(host: Path, workspace: Path) -> dict[str, Any]:
    """Per-file newer-wins sync under state roots (jobs/, bridge/, top-level)."""
    if not host.exists() and not workspace.exists():
        return {"action": "skip", "reason": "neither_state_present", "copied": 0}
    host.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)

    # Map relative path -> (side, absolute)
    files: dict[str, dict[str, Path]] = {}
    for side, root in (("host", host), ("ws", workspace)):
        for f in _iter_state_files(root):
            rel = str(f.relative_to(root))
            files.setdefault(rel, {})[side] = f

    copied = 0
    directions: dict[str, int] = {"host_to_ws": 0, "ws_to_host": 0}
    for rel, sides in files.items():
        h = sides.get("host")
        w = sides.get("ws")
        if h and not w:
            dest = workspace / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(h, dest)
            copied += 1
            directions["host_to_ws"] += 1
            continue
        if w and not h:
            dest = host / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(w, dest)
            copied += 1
            directions["ws_to_host"] += 1
            continue
        if not h or not w:
            continue
        if _same_bytes(h, w):
            continue
        if _mtime(h) >= _mtime(w):
            shutil.copy2(h, w)
            copied += 1
            directions["host_to_ws"] += 1
        else:
            shutil.copy2(w, h)
            copied += 1
            directions["ws_to_host"] += 1
    return {"action": "sync", "copied": copied, "directions": directions}


def sync_once(*, quiet: bool = True) -> dict[str, Any]:
    if os.environ.get("AAS_REMOTE_BRIDGE_SYNC", "1").strip().lower() in {
        "0",
        "false",
        "off",
        "no",
    }:
        return {"ok": True, "skipped": True, "reason": "disabled_by_env"}
    paths = default_paths()
    started = time.time()
    result: dict[str, Any] = {
        "ok": True,
        "schema": "remote_bridge_path_sync.v1",
        "paths": {k: str(v) for k, v in paths.items()},
        "secrets": {},
        "state": {},
    }
    try:
        result["secrets"] = sync_secrets_file(paths["host_secrets"], paths["ws_secrets"])
        result["state"] = sync_state_trees(paths["host_state"], paths["ws_state"])
    except Exception as exc:  # noqa: BLE001 — sync is best-effort
        result["ok"] = False
        result["error"] = type(exc).__name__
        result["error_detail"] = str(exc)[:200]
    result["duration_ms"] = int((time.time() - started) * 1000)
    if not quiet:
        print(json.dumps(result, indent=2, sort_keys=True))
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sync remote-bridge secrets/state host↔workspace")
    ap.add_argument("--json", action="store_true", help="print audit JSON (default)")
    ap.add_argument("--quiet", action="store_true", help="no stdout")
    args = ap.parse_args(argv)
    # sync_once always computes; only CLI prints once
    result = sync_once(quiet=True)
    if not args.quiet:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
