"""Goal-priority (goal_priority.v1) — opt-in path discipline for ARL loops.

Template docs: canonical/templates/goal-priority.md
Does not change loop stop conditions (enforcement.md).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "goal_priority.v1"

GENERIC_LOCAL_TAGS = [
    "finite_sample_only",
    "bookkeeping",
    "special_case_only",
    "uncertified_counterexample",
    "elegant_reduction",
    "local_refinement_only",
    "closed_campaign_sample",
]

_ENV_ON = frozenset({"1", "on", "true", "yes"})
_ENV_OFF = frozenset({"0", "off", "false", "no"})


def default_goal_priority_config() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "enabled": False,
        "primary_campaign": "",
        "primary_objective": "",
        "campaign_registry": {},
        "closed_campaigns": [],
        "next_campaigns_ordered": [],
        "max_consecutive_local_without_goal_delta": 3,
        "local_without_goal_delta_tags": list(GENERIC_LOCAL_TAGS),
        "require_goal_contribution_in_ledger": True,
        "panel_rank_by_goal_ev": True,
    }


def _shallow_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if value is None:
            continue
        out[key] = value
    return out


def _safe_int(value: Any, default: int, *, minimum: int | None = None) -> tuple[int, bool]:
    """Return (coerced, ok). On failure returns default and ok=False."""
    try:
        if isinstance(value, bool):
            return default, False
        n = int(value)
    except (TypeError, ValueError):
        return default, False
    if minimum is not None and n < minimum:
        return default, False
    return n, True


def load_goal_priority(run_dir: Path) -> dict[str, Any]:
    """Load and merge goal_priority.json + standing_orders.goal_priority + env.

    Active when merged enabled is JSON boolean True (or env forces on with a
    config object present). Malformed layers warn and are skipped when possible.
    """
    cfg = default_goal_priority_config()
    warnings: list[str] = []
    saw_object = False
    file_had_enabled = False
    standing_had_enabled = False
    path = Path(run_dir) / "goal_priority.json"

    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                saw_object = True
                if "enabled" in data:
                    file_had_enabled = True
                else:
                    warnings.append(
                        "goal_priority.json present without explicit enabled key"
                    )
                cfg = _shallow_merge(cfg, data)
            else:
                warnings.append("goal_priority.json is not a JSON object; ignoring file layer")
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"goal_priority.json unreadable: {exc}; skipping file layer")

    state_path = Path(run_dir) / "loop_state.json"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            so = state.get("standing_orders") if isinstance(state, dict) else None
            gp = so.get("goal_priority") if isinstance(so, dict) else None
            if isinstance(gp, dict):
                saw_object = True
                if "enabled" in gp:
                    standing_had_enabled = True
                elif not file_had_enabled:
                    warnings.append(
                        "standing_orders.goal_priority present without explicit enabled key"
                    )
                cfg = _shallow_merge(cfg, gp)
        except (OSError, json.JSONDecodeError):
            pass

    # Normalize enabled to strict boolean True only when explicitly true
    if cfg.get("enabled") is not True and cfg.get("enabled") is not False:
        if saw_object and "enabled" in cfg:
            warnings.append(
                "goal_priority enabled is not a JSON boolean; treating as inactive "
                "unless env forces on"
            )
        cfg["enabled"] = False

    cap, cap_ok = _safe_int(
        cfg.get("max_consecutive_local_without_goal_delta"), 3, minimum=1
    )
    if not cap_ok:
        warnings.append(
            "goal_priority max_consecutive_local_without_goal_delta invalid; using 3"
        )
    cfg["max_consecutive_local_without_goal_delta"] = cap

    if not isinstance(cfg.get("campaign_registry"), dict):
        warnings.append("goal_priority campaign_registry is not an object; using {}")
        cfg["campaign_registry"] = {}
    if not isinstance(cfg.get("closed_campaigns"), list):
        warnings.append("goal_priority closed_campaigns is not a list; using []")
        cfg["closed_campaigns"] = []
    if not isinstance(cfg.get("next_campaigns_ordered"), list):
        warnings.append("goal_priority next_campaigns_ordered is not a list; using []")
        cfg["next_campaigns_ordered"] = []
    if not isinstance(cfg.get("local_without_goal_delta_tags"), list):
        cfg["local_without_goal_delta_tags"] = list(GENERIC_LOCAL_TAGS)

    env_flag = os.environ.get("AAS_AUTOLOOP_GOAL_PRIORITY", "").strip().lower()
    env_forced_on = False
    if env_flag in _ENV_ON:
        if not saw_object:
            warnings.append(
                "AAS_AUTOLOOP_GOAL_PRIORITY=on without goal_priority config; inert"
            )
            cfg["enabled"] = False
        else:
            cfg["enabled"] = True
            env_forced_on = True
    elif env_flag in _ENV_OFF:
        cfg["enabled"] = False

    active = cfg.get("enabled") is True

    # Drop misleading "inactive" claims when env forced on
    if env_forced_on and active:
        cleaned: list[str] = []
        for w in warnings:
            if "without explicit enabled" in w:
                cleaned.append(
                    w.replace("treating as inactive", "enabled by env").rstrip("; ")
                    + " (enabled by AAS_AUTOLOOP_GOAL_PRIORITY)"
                )
            else:
                cleaned.append(w)
        if not any("enabled by env" in w or "AAS_AUTOLOOP_GOAL_PRIORITY" in w for w in cleaned):
            cleaned.append("goal_priority enabled by AAS_AUTOLOOP_GOAL_PRIORITY")
        warnings = cleaned
    elif saw_object and not active and not file_had_enabled and not standing_had_enabled:
        if not any("without explicit enabled" in w for w in warnings):
            warnings.append(
                "goal_priority config present without enabled true; inactive"
            )

    cfg["_warnings"] = warnings
    cfg["_active"] = active
    cfg["_saw_object"] = saw_object
    return cfg


def is_goal_priority_active(run_dir: Path) -> bool:
    return bool(load_goal_priority(run_dir).get("_active"))


def read_iterations_jsonl(run_dir: Path) -> list[dict[str, Any]]:
    path = Path(run_dir) / "iterations.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except json.JSONDecodeError:
            continue
    return rows


def _has_goal_field(row: dict[str, Any]) -> bool:
    if "goal_contribution" in row:
        return True
    if "local_without_goal_delta" in row:
        return True
    if "campaign_id" in row:
        return True
    return False


def _counts_as_local(row: dict[str, Any], *, require: bool) -> bool:
    flagged = row.get("local_without_goal_delta") is True
    if flagged:
        return True
    if require:
        return not str(row.get("goal_contribution") or "").strip()
    return False


def local_without_goal_delta_streak(run_dir: Path, cfg: dict[str, Any] | None = None) -> int:
    """Count consecutive tail iterations that count as local-without-goal-delta.

    Activation boundary: first record containing any of goal_contribution,
    campaign_id, or local_without_goal_delta. All subsequent records count.
    """
    cfg = cfg or load_goal_priority(run_dir)
    if not cfg.get("_active"):
        return 0
    rows = read_iterations_jsonl(run_dir)
    if not rows:
        return 0
    start = None
    for i, row in enumerate(rows):
        if _has_goal_field(row):
            start = i
            break
    if start is None:
        return 0
    require = bool(cfg.get("require_goal_contribution_in_ledger", True))
    streak = 0
    for row in reversed(rows[start:]):
        if _counts_as_local(row, require=require):
            streak += 1
        else:
            break
    return streak


def replan_required(run_dir: Path, cfg: dict[str, Any] | None = None) -> bool:
    cfg = cfg or load_goal_priority(run_dir)
    if not cfg.get("_active"):
        return False
    cap, _ = _safe_int(cfg.get("max_consecutive_local_without_goal_delta"), 3, minimum=1)
    return local_without_goal_delta_streak(run_dir, cfg) >= cap


def closed_forbid_ids(cfg: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for item in cfg.get("closed_campaigns") or []:
        if not isinstance(item, dict):
            continue
        if item.get("forbid_as_sole_primary"):
            cid = str(item.get("id") or "").strip()
            if cid:
                out.append(cid)
    return out


def _campaign_objective(cfg: dict[str, Any], campaign_id: str) -> str:
    registry = cfg.get("campaign_registry") if isinstance(cfg.get("campaign_registry"), dict) else {}
    entry = registry.get(campaign_id)
    if isinstance(entry, dict):
        return str(entry.get("objective") or "")
    return ""


def goal_priority_prompt_addon(run_dir: Path, cfg: dict[str, Any] | None = None) -> str:
    """Text appended to the primary iteration prompt when goal_priority is active."""
    cfg = cfg or load_goal_priority(run_dir)
    if not cfg.get("_active"):
        return ""
    state: dict[str, Any] = {}
    sp = Path(run_dir) / "loop_state.json"
    if sp.is_file():
        try:
            state = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    goal = str(state.get("goal") or "")[:800]
    success = str(state.get("success_criteria") or "")[:800]
    primary = str(cfg.get("primary_campaign") or "")
    primary_obj = str(cfg.get("primary_objective") or "") or _campaign_objective(cfg, primary)
    closed = closed_forbid_ids(cfg)
    next_ids = [str(x) for x in (cfg.get("next_campaigns_ordered") or []) if str(x).strip()]
    streak = local_without_goal_delta_streak(run_dir, cfg)
    cap, _ = _safe_int(cfg.get("max_consecutive_local_without_goal_delta"), 3, minimum=1)
    lines = [
        "",
        "## Goal-focused path discipline (goal_priority.v1 — active)",
        "Prefer primary paths that advance the loop goal / success criteria.",
        "Favor outcomes that kill, bridge, construct, verify trust, or replan",
        "over unbounded local samples that do not reduce goal uncertainty.",
        f"- Goal: {goal or '(see loop_state.goal)'}",
        f"- Success criteria: {success or '(see loop_state.success_criteria)'}",
        f"- Primary campaign: `{primary or '(unset)'}` — {primary_obj or '(see campaign_registry)'}",
    ]
    if closed:
        lines.append(
            "- Closed (forbid as sole primary): " + ", ".join(f"`{c}`" for c in closed[:20])
        )
    if next_ids:
        bits: list[str] = []
        for cid in next_ids[:12]:
            obj = _campaign_objective(cfg, cid)
            bits.append(f"`{cid}`" + (f" ({obj[:80]})" if obj else ""))
        lines.append("- Next campaigns ordered: " + ", ".join(bits))
    lines.append(
        "- When appending, prefer ledger fields: "
        "`--goal-contribution`, `--campaign-id`, and if applicable "
        "`--local-without-goal-delta` / `--local-without-goal-delta-tag`."
    )
    lines.append(f"- Local-without-goal-delta streak: {streak}/{cap}.")
    if replan_required(run_dir, cfg):
        lines.extend(
            [
                "",
                "### REPLAN_REQUIRED",
                "Consecutive local-without-goal-delta (or missing contribution) hit the cap.",
                "Prefer not to continue the same local residual as sole primary.",
                "Replan to `next_campaigns_ordered` / primary_campaign objective, or update",
                "`goal_priority` / `next_preferred_path` with a goal-advancing path.",
            ]
        )
    lines.append(
        "This does **not** stop the loop (enforcement unchanged). Soft discipline only."
    )
    lines.append("")
    return "\n".join(lines)


def goal_priority_brief_block(run_dir: Path, cfg: dict[str, Any] | None = None) -> str:
    """Block for panel target brief — placed before long recovery excerpts."""
    cfg = cfg or load_goal_priority(run_dir)
    if not cfg.get("_active"):
        return ""
    rank = bool(cfg.get("panel_rank_by_goal_ev", True))
    header = (
        "# Goal-EV ranking (host parent — goal_priority active)\n\n"
        "Rank candidate next paths by contribution to the loop **goal**, not by "
        "local residual size alone. Do not recommend a closed campaign with "
        "`forbid_as_sole_primary` as the sole primary.\n"
        if rank
        else (
            "# Goal priority (host parent — active; ranking language off)\n\n"
            "Honor goal / closed-campaign / replan guidance below. "
            "`panel_rank_by_goal_ev` is false (no EV ranking language).\n"
        )
    )
    return header + goal_priority_prompt_addon(run_dir, cfg)


def campaign_match_line(run_dir: Path, cfg: dict[str, Any] | None = None) -> str:
    """One-line campaign match for result-review brief."""
    cfg = cfg or load_goal_priority(run_dir)
    if not cfg.get("_active"):
        return ""
    rows = read_iterations_jsonl(run_dir)
    latest_cid = ""
    if rows:
        latest_cid = str(rows[-1].get("campaign_id") or "").strip()
    primary = str(cfg.get("primary_campaign") or "").strip()
    next_ids = [str(x) for x in (cfg.get("next_campaigns_ordered") or [])]
    if not latest_cid and not primary:
        return "- Campaign match: (no campaign_id on latest ledger; primary unset)\n"
    if latest_cid and primary and latest_cid == primary:
        status = "matches primary_campaign"
    elif latest_cid and latest_cid in next_ids:
        status = "in next_campaigns_ordered"
    elif latest_cid and latest_cid in closed_forbid_ids(cfg):
        status = "WARNING: latest campaign_id is closed with forbid_as_sole_primary"
    elif latest_cid:
        status = "differs from primary_campaign"
    else:
        status = "missing on latest ledger"
    return (
        f"- Campaign match: latest=`{latest_cid or '(none)'}` "
        f"primary=`{primary or '(none)'}` — {status}\n"
    )


def collect_goal_priority_warnings(
    run_dir: Path, *, latest_record: dict[str, Any] | None = None
) -> list[str]:
    """Warnings for validate / append (never flip validate status by themselves)."""
    cfg = load_goal_priority(run_dir)
    warnings = list(cfg.get("_warnings") or [])
    if not cfg.get("_active"):
        return warnings
    if latest_record is not None and cfg.get("require_goal_contribution_in_ledger", True):
        if not str(latest_record.get("goal_contribution") or "").strip():
            warnings.append(
                "goal_priority active: latest iteration missing goal_contribution "
                "(use --goal-contribution)"
            )
    tag = None
    if latest_record is not None:
        tag = latest_record.get("local_without_goal_delta_tag")
    allowed = cfg.get("local_without_goal_delta_tags") or []
    if tag and allowed and str(tag) not in allowed:
        warnings.append(
            f"goal_priority: local_without_goal_delta_tag {tag!r} not in "
            "config advisory vocabulary (ok, open vocabulary)"
        )
    if replan_required(run_dir, cfg):
        warnings.append(
            "goal_priority: REPLAN_REQUIRED (local-without-goal-delta streak at cap)"
        )
    primary = str(cfg.get("primary_campaign") or "").strip()
    if primary and primary in closed_forbid_ids(cfg):
        warnings.append(
            f"goal_priority: primary_campaign {primary!r} is also closed with "
            "forbid_as_sole_primary"
        )
    return warnings


def example_goal_priority_json() -> str:
    example = {
        "schema_version": SCHEMA_VERSION,
        "enabled": False,
        "primary_campaign": "main",
        "primary_objective": "State what this campaign must produce for loop_state.goal",
        "campaign_registry": {
            "main": {
                "objective": "Advance the stated goal with a host-verifiable artifact",
                "entry_condition": "Always while this is primary",
                "non_goals": ["Unbounded local sampling without goal reduction"],
            }
        },
        "closed_campaigns": [],
        "next_campaigns_ordered": ["main"],
        "max_consecutive_local_without_goal_delta": 3,
        "local_without_goal_delta_tags": list(GENERIC_LOCAL_TAGS),
        "require_goal_contribution_in_ledger": True,
        "panel_rank_by_goal_ev": True,
    }
    return json.dumps(example, indent=2) + "\n"
