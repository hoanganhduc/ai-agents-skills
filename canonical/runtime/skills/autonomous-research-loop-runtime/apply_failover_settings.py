#!/usr/bin/env python3
"""Write or merge {loop}/failover.json from an example / CLI fields.

Usage:
  python3 apply_failover_settings.py --dir /path/to/loop --from-json failover.example.json
  python3 apply_failover_settings.py --dir /path/to/loop --research-title "TS_k acyclicity" \\
      --primary-order grok,claude,deepseek
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dir", required=True)
    p.add_argument("--from-json", help="seed JSON path")
    p.add_argument("--research-title")
    p.add_argument("--job-slug")
    p.add_argument("--primary-order", help="comma-separated providers")
    p.add_argument("--max-quota-waits", type=int)
    p.add_argument("--force", action="store_true", help="overwrite existing file")
    args = p.parse_args(argv)

    run_dir = Path(args.dir).expanduser().resolve()
    out = run_dir / "failover.json"
    data: dict[str, Any] = {"schema_version": "failover.v1"}
    if args.from_json:
        src = Path(args.from_json).expanduser().resolve()
        data.update(json.loads(src.read_text(encoding="utf-8")))
    elif out.is_file() and not args.force:
        data.update(json.loads(out.read_text(encoding="utf-8")))
    if args.research_title:
        data["research_title"] = args.research_title
    if args.job_slug:
        data["job_slug"] = args.job_slug
    if args.primary_order:
        data["primary_order"] = [
            x.strip() for x in args.primary_order.split(",") if x.strip()
        ]
    if args.max_quota_waits is not None:
        data["max_quota_waits_per_primary"] = int(args.max_quota_waits)
    if out.is_file() and not args.force and not args.from_json and not (
        args.research_title or args.job_slug or args.primary_order or args.max_quota_waits is not None
    ):
        print(json.dumps({"ok": False, "error": "exists; pass --force or fields"}, indent=2))
        return 1
    _atomic_write_json(out, data)
    print(json.dumps({"ok": True, "path": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
