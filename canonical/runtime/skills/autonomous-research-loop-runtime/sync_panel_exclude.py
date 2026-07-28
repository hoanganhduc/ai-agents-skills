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


def sync_exclude(run_dir: Path, provider: str) -> dict[str, Any]:
    prov = _norm(provider)
    if not prov:
        return {"ok": False, "error": "empty provider"}
    run_dir = run_dir.expanduser().resolve()
    state_path = run_dir / "loop_state.json"
    panel_path = run_dir / "panel.json"
    updated: list[str] = []

    # Prefer standing_orders.panel when present (it overwrites panel.json).
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": f"loop_state read: {exc}"}
        if not isinstance(state, dict):
            return {"ok": False, "error": "loop_state not an object"}
        so = state.get("standing_orders")
        if not isinstance(so, dict):
            so = {}
            state["standing_orders"] = so
        panel = so.get("panel")
        if isinstance(panel, dict):
            excl = panel.get("exclude_until_credit")
            if not isinstance(excl, list):
                excl = []
            names = [_norm(str(x)) for x in excl if str(x).strip()]
            if prov not in names:
                names.append(prov)
            panel["exclude_until_credit"] = names
            so["panel"] = panel
            _atomic_write_json(state_path, state)
            updated.append("standing_orders.panel")
            # Mirror into panel.json when it exists so both stay aligned.
            if panel_path.is_file():
                try:
                    pdata = json.loads(panel_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pdata = {}
                if not isinstance(pdata, dict):
                    pdata = {}
                pexcl = pdata.get("exclude_until_credit")
                if not isinstance(pexcl, list):
                    pexcl = []
                pnames = [_norm(str(x)) for x in pexcl if str(x).strip()]
                if prov not in pnames:
                    pnames.append(prov)
                pdata["exclude_until_credit"] = pnames
                _atomic_write_json(panel_path, pdata)
                updated.append("panel.json")
            return {"ok": True, "provider": prov, "updated": updated}

    # No standing_orders.panel: update or create panel.json.
    pdata: dict[str, Any] = {}
    if panel_path.is_file():
        try:
            loaded = json.loads(panel_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                pdata = loaded
        except (OSError, json.JSONDecodeError):
            pdata = {}
    excl = pdata.get("exclude_until_credit")
    if not isinstance(excl, list):
        excl = []
    names = [_norm(str(x)) for x in excl if str(x).strip()]
    if prov not in names:
        names.append(prov)
    pdata["exclude_until_credit"] = names
    _atomic_write_json(panel_path, pdata)
    updated.append("panel.json")
    return {"ok": True, "provider": prov, "updated": updated}


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
