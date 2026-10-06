#!/usr/bin/env python3
"""Atomically add a provider to panel exclude_until_credit (effective store).

load_panel_config merges panel.json first, then standing_orders.panel (which
wins on key conflicts). This helper updates the effective store so the next
load_panel_config call excludes the provider.

Usage:
  python3 sync_panel_exclude.py --dir /path/to/loop --provider codex
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=False)
            handle.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _norm(name: str) -> str:
    return (name or "").strip().lower()


def _panel_json_excludes(panel_path: Path) -> list[Any]:
    """The exclusion list ``panel.json`` contributes to the merge."""

    if not panel_path.is_file():
        return []
    try:
        data = json.loads(panel_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    excl = data.get("exclude_until_credit")
    return excl if isinstance(excl, list) else []


def sync_exclude(run_dir: Path, provider: str) -> dict[str, Any]:
    """Persist one hard-credit exclusion without losing concurrent controls."""
    import hashlib
    from state_transaction import RevisionConflict, commit_transaction, _read_bytes_nofollow

    prov = _norm(provider)
    if not prov:
        return {"ok": False, "error": "empty provider"}
    run_dir = run_dir.expanduser().resolve()
    for _ in range(3):
        before: dict[str, bytes] = {}
        data: dict[str, dict[str, Any]] = {}
        try:
            for name in ("loop_state.json", "panel.json"):
                try:
                    raw = _read_bytes_nofollow(run_dir / name)
                except FileNotFoundError:
                    continue
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError(f"{name} must contain an object")
                before[name] = raw
                data[name] = value
            state = data.get("loop_state.json", {})
            so = state.get("standing_orders") or {}
            standing = so.get("panel")
            panel = data.get("panel.json", {})
            effective = dict(panel)
            if isinstance(standing, dict):
                effective.update(standing)
            raw_names = effective.get("exclude_until_credit", [])
            if not isinstance(raw_names, list):
                raise ValueError("exclude_until_credit must be a list")
            names = list(dict.fromkeys(_norm(str(n)) for n in raw_names if str(n).strip()))
            if prov not in names:
                names.append(prov)
            updates: dict[str, dict[str, Any]] = {}
            updated = []
            if isinstance(standing, dict):
                standing["exclude_until_credit"] = names
                updates["loop_state.json"] = state
                updated.append("standing_orders.panel")
            if "panel.json" in data or not isinstance(standing, dict):
                panel["exclude_until_credit"] = names
                updates["panel.json"] = panel
                updated.append("panel.json")
            commit_transaction(run_dir, json_files=updates,
                expected_hashes={name: hashlib.sha256(raw).hexdigest() for name, raw in before.items()},
                expected_absent=[name for name in ("loop_state.json", "panel.json") if name not in before])
            return {"ok": True, "provider": prov, "updated": updated}
        except RevisionConflict:
            continue
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": "concurrent exclusion changes; retry required"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True, help="loop directory")
    parser.add_argument("--provider", required=True, help="provider to exclude")
    args = parser.parse_args(argv)
    result = sync_exclude(Path(args.dir), args.provider)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
