#!/usr/bin/env python3
"""Schema-validated writer for loop formal_policy (not sed).

Usage:
  python3 apply_formal_settings.py --dir <loop_dir> --policy on
  python3 apply_formal_settings.py --dir <loop_dir> --from-json formal_policy.example.json

Writes:
  - <loop>/formal/formal_policy.json
  - merges privileged keys into loop_state.standing_orders.formal (if present)

Never prints secrets. Does not spawn Lake or OpenGauss.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FORMAL_POLICIES = frozenset({"off", "mention-only", "auto", "on", "force"})
PRIVILEGED = (
    "policy",
    "project",
    "force_credits",
    "allow_path_steal",
    "typecheck",
    "force_after_iteration",
    "allow_create_skeleton",
)


def default_cfg() -> dict:
    return {
        "schema_version": "formal_policy.v1",
        "policy": "off",
        "project": "formal/",
        "force_credits": 3,
        "allow_path_steal": False,
        "typecheck": False,
        "force_after_iteration": False,
        "allow_create_skeleton": False,
        "notes": [],
        "status": {
            "phase": "",
            "lake_build": "",
            "sorry_count": None,
            "updated_at": "",
        },
    }


def normalize(raw: dict) -> dict:
    cfg = default_cfg()
    if "policy" in raw:
        p = str(raw.get("policy") or "").strip().lower()
        cfg["policy"] = p if p in FORMAL_POLICIES else "off"
    if raw.get("project") is not None:
        cfg["project"] = str(raw["project"]).strip() or "formal/"
    if "force_credits" in raw:
        try:
            cfg["force_credits"] = max(0, int(raw["force_credits"]))
        except (TypeError, ValueError):
            pass
    for bkey in (
        "allow_path_steal",
        "typecheck",
        "force_after_iteration",
        "allow_create_skeleton",
    ):
        if bkey in raw and isinstance(raw[bkey], bool):
            cfg[bkey] = raw[bkey]
    if isinstance(raw.get("notes"), list):
        cfg["notes"] = [str(x)[:200] for x in raw["notes"][:20]]
    if isinstance(raw.get("status"), dict):
        st = dict(cfg["status"])
        st.update({k: v for k, v in raw["status"].items() if v is not None})
        cfg["status"] = st
    return cfg


def merge_standing(run_dir: Path, cfg: dict) -> None:
    path = run_dir / "loop_state.json"
    if not path.is_file():
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(state, dict):
        return
    so = state.setdefault("standing_orders", {})
    if not isinstance(so, dict):
        so = {}
        state["standing_orders"] = so
    formal = so.get("formal") if isinstance(so.get("formal"), dict) else {}
    for key in PRIVILEGED:
        formal[key] = cfg[key]
    so["formal"] = formal
    state["standing_orders"] = so
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, help="loop directory")
    ap.add_argument("--from-json", default=None, help="optional JSON file overlay")
    ap.add_argument(
        "--policy",
        choices=sorted(FORMAL_POLICIES),
        default=None,
        help="override policy",
    )
    ap.add_argument("--project", default=None)
    ap.add_argument("--force-credits", type=int, default=None)
    ap.add_argument("--force-after-iteration", action="store_true")
    ap.add_argument("--typecheck", action="store_true")
    args = ap.parse_args()

    run_dir = Path(args.dir).expanduser().resolve()
    cfg = default_cfg()
    if args.from_json:
        try:
            raw = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg = normalize(raw)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: cannot read --from-json: {exc}", file=sys.stderr)
            return 2
    if args.policy is not None:
        cfg["policy"] = args.policy
    if args.project is not None:
        cfg["project"] = args.project
    if args.force_credits is not None:
        cfg["force_credits"] = max(0, int(args.force_credits))
    if args.force_after_iteration:
        cfg["force_after_iteration"] = True
    if args.typecheck:
        cfg["typecheck"] = True

    formal_dir = run_dir / "formal"
    formal_dir.mkdir(parents=True, exist_ok=True)
    out = formal_dir / "formal_policy.json"
    out.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    merge_standing(run_dir, cfg)
    print(f"wrote {out}")
    print(f"policy={cfg['policy']} project={cfg['project']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
