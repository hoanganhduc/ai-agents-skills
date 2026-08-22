"""Goal-priority (goal_priority.v1 / soft v2 fields) — opt-in path discipline.

Template docs: canonical/templates/goal-priority.md
Does not change loop stop conditions (enforcement.md).
Never writes loop_state.status. Never fail-closes append for vocabulary.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from state_transaction import iteration_ledger_paths  # type: ignore
except ImportError:  # pragma: no cover - package-relative install layouts
    from .state_transaction import iteration_ledger_paths  # type: ignore

SCHEMA_VERSION = "goal_priority.v1"


def _atomic_write_text(path: Path, text: str) -> None:
    """Replace a loop file whole, or leave the old one exactly as it was.

    ``write_text`` opens with mode ``"w"``, which truncates the destination
    before the first byte of the replacement is written. The three files this
    module rewrites are the loop's own state, its recovery record and its audit
    log, so a write that dies partway -- a full disk, a file-size limit, a
    killed supervisor -- left the loop holding a state file cut off mid-token.
    The caller was told the write failed and the loop was unrecoverable anyway.

    The runtime's own writer for ``loop_state.json`` has never done this, and
    says so: "Write atomically (temp file + os.replace) so a crash mid-write
    cannot truncate the destination and lose loop state." This is that, and it
    keeps the ``mkstemp`` + ``os.replace`` shape used elsewhere in the pack, so
    a symlink planted at the destination is replaced rather than followed.
    """

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


GENERIC_LOCAL_TAGS = [
    "finite_sample_only",
    "bookkeeping",
    "special_case_only",
    "uncertified_counterexample",
    "elegant_reduction",
    "local_refinement_only",
    "closed_campaign_sample",
]

# Closed vocabulary for advise+ validation (soft still accepts open strings).
CONTRIBUTION_VOCABULARY = frozenset(
    {
        "eliminate",
        "construct",
        "scope_lift",
        "bridge",
        "separate",
        "verify_trust",
        "replan",
        "formalize",
        "operational",
        "advance",
    }
)

DISCIPLINE_MODES = frozenset({"soft", "advise", "hard"})

# Contributions that count as real residual/goal progress for host streak
# (advise/hard). Ordered because the guidance this module emits is built from it.
# The two were maintained separately and drifted: the guidance named
# ``verify_trust`` among the labels to prefer over a bare ``advance``, while the
# streak counter had it in LOW_VALUE_CONTRIBUTIONS and so treated it exactly like
# the ``advance`` it was telling the agent to avoid -- three independent audits of
# three different residuals hit the cap and forced a replan. Meanwhile
# ``formalize`` counted as progress but went unmentioned. An independent audit is
# a gate discharged, the same as the formal gate beside it.
PROGRESS_CONTRIBUTIONS_ORDERED = (
    "eliminate",
    "construct",
    "scope_lift",
    "bridge",
    "separate",
    "verify_trust",
    "replan",
    "formalize",
)
PROGRESS_CONTRIBUTIONS = frozenset(PROGRESS_CONTRIBUTIONS_ORDERED)
# The one rendering of that list the agent ever reads, so it cannot drift again.
PREFERRED_CONTRIBUTIONS_TEXT = "/".join(PROGRESS_CONTRIBUTIONS_ORDERED)
# Low-value labels: same residual_id + these can count as local in advise/hard.
LOW_VALUE_CONTRIBUTIONS = frozenset({"advance", "operational", ""})

_ENV_ON = frozenset({"1", "on", "true", "yes"})
_ENV_OFF = frozenset({"0", "off", "false", "no"})


def default_goal_priority_config() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "enabled": False,
        "discipline_mode": "soft",
        "primary_campaign": "",
        "primary_objective": "",
        "campaign_registry": {},
        "closed_campaigns": [],
        "next_campaigns_ordered": [],
        "max_consecutive_local_without_goal_delta": 3,
        "local_without_goal_delta_tags": list(GENERIC_LOCAL_TAGS),
        "require_goal_contribution_in_ledger": True,
        "panel_rank_by_goal_ev": True,
        "host_signal_epoch_iteration": None,
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

    mode = str(cfg.get("discipline_mode") or "advise").strip().lower()
    if mode not in DISCIPLINE_MODES:
        warnings.append(
            f"goal_priority discipline_mode {mode!r} invalid; using advise"
        )
        mode = "advise"
    cfg["discipline_mode"] = mode

    epoch_raw = cfg.get("host_signal_epoch_iteration")
    if epoch_raw is not None and epoch_raw != "":
        epoch_n, epoch_ok = _safe_int(epoch_raw, 0, minimum=0)
        if not epoch_ok:
            warnings.append(
                "goal_priority host_signal_epoch_iteration invalid; ignoring"
            )
            cfg["host_signal_epoch_iteration"] = None
        else:
            cfg["host_signal_epoch_iteration"] = epoch_n

    # residual_inventory.json may supply epoch if config omitted
    inv = load_residual_inventory(Path(run_dir))
    cfg["_residual_inventory"] = inv
    if cfg.get("host_signal_epoch_iteration") is None and inv.get(
        "host_signal_epoch_iteration"
    ) is not None:
        cfg["host_signal_epoch_iteration"] = inv.get("host_signal_epoch_iteration")

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


def load_residual_inventory(run_dir: Path) -> dict[str, Any]:
    """Load optional residual_inventory.json (Ship 1). Malformed → empty + warning."""
    path = Path(run_dir) / "residual_inventory.json"
    out: dict[str, Any] = {
        "schema_version": "residual_inventory.v1",
        "host_signal_epoch_iteration": None,
        "leaves": [],
        "_warnings": [],
        "_present": False,
        "_hash": "",
    }
    if not path.is_file():
        return out
    out["_present"] = True
    try:
        raw = path.read_text(encoding="utf-8")
        out["_hash"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        out["_warnings"].append(f"residual_inventory.json unreadable: {exc}")
        return out
    if not isinstance(data, dict):
        out["_warnings"].append("residual_inventory.json is not a JSON object")
        return out
    if "host_signal_epoch_iteration" in data:
        n, ok = _safe_int(data.get("host_signal_epoch_iteration"), 0, minimum=0)
        out["host_signal_epoch_iteration"] = n if ok else None
    leaves = data.get("leaves")
    if leaves is None:
        leaves = []
    if not isinstance(leaves, list):
        out["_warnings"].append("residual_inventory leaves is not a list")
        leaves = []
    cleaned: list[dict[str, Any]] = []
    for item in leaves:
        if isinstance(item, dict) and str(item.get("id") or "").strip():
            cleaned.append(item)
    out["leaves"] = cleaned
    if data.get("schema_version"):
        out["schema_version"] = str(data.get("schema_version"))
    return out


def open_residual_leaves(run_dir: Path, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cfg = cfg or load_goal_priority(run_dir)
    inv = cfg.get("_residual_inventory") or load_residual_inventory(run_dir)
    return [
        leaf
        for leaf in (inv.get("leaves") or [])
        if str(leaf.get("status") or "open").lower() == "open"
    ]


def discipline_mode(cfg: dict[str, Any]) -> str:
    mode = str(cfg.get("discipline_mode") or "advise").strip().lower()
    return mode if mode in DISCIPLINE_MODES else "advise"


def contribution_is_generic_advance(value: Any) -> bool:
    return str(value or "").strip().lower() == "advance"


def read_iterations_jsonl(run_dir: Path) -> list[dict[str, Any]]:
    """Read the whole iteration ledger, spanning every rotated shard.

    Reading only ``iterations.jsonl`` would make every ledger scan here restart
    at the first rotation, so the shard order comes from one shared helper.
    """

    rows: list[dict[str, Any]] = []
    for path in iteration_ledger_paths(run_dir):
        if not path.is_file():
            continue
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


def _counts_as_local(
    row: dict[str, Any],
    *,
    require: bool,
    mode: str = "soft",
) -> bool:
    """Whether a ledger row counts as local-without-goal-delta for streak.

    soft: explicit flag or missing contribution (when require).
    advise/hard: also any low-value label, whatever residual_id the row names.

    The residual_id does not enter the verdict. This function used to compare each row
    against the newer one beside it and count a low-value label only when it continued
    the same residual, but the two clauses below that comparison already returned True
    for a low-value label with no residual_id and for one with any residual_id --
    between them, every low-value row -- so the comparison decided nothing. An
    exhaustive sweep of the input space found no row whose verdict it changed, and the
    comment on the last clause, "(no newer row to compare)", named a condition the
    clause did not test. Counting on any residual is what the streak is specified
    against: three bare `advance` iterations are three iterations without a goal delta
    whether or not they happen to name one residual.
    """
    flagged = row.get("local_without_goal_delta") is True
    if flagged:
        return True
    gc = str(row.get("goal_contribution") or "").strip().lower()
    if require and not gc:
        return True
    if mode in {"advise", "hard"}:
        if gc in PROGRESS_CONTRIBUTIONS:
            return False
        return gc in LOW_VALUE_CONTRIBUTIONS
    return False


def local_without_goal_delta_streak(run_dir: Path, cfg: dict[str, Any] | None = None) -> int:
    """Count consecutive tail iterations that count as local-without-goal-delta.

    Activation boundary: first record containing any of goal_contribution,
    campaign_id, or local_without_goal_delta. Pre-epoch rows stop the reverse
    count (do not extend streak into history before host_signal_epoch_iteration).
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
    mode = discipline_mode(cfg)
    epoch = cfg.get("host_signal_epoch_iteration")
    streak = 0
    for row in reversed(rows[start:]):
        try:
            n = int(row.get("iteration") or 0)
        except (TypeError, ValueError):
            n = 0
        if epoch is not None:
            try:
                if n < int(epoch):
                    break
            except (TypeError, ValueError):
                pass
        if _counts_as_local(row, require=require, mode=mode):
            streak += 1
        else:
            break
    return streak


def replan_required(run_dir: Path, cfg: dict[str, Any] | None = None) -> bool:
    cfg = cfg or load_goal_priority(run_dir)
    if not cfg.get("_active"):
        return False
    cap, _ = _safe_int(cfg.get("max_consecutive_local_without_goal_delta"), 3, minimum=1)
    if local_without_goal_delta_streak(run_dir, cfg) >= cap:
        return True
    # Hard: also replan when the committed path still targets only closed leaves
    # while open residual leaves remain.
    if discipline_mode(cfg) == "hard" and path_targets_closed_residual(run_dir, cfg):
        return True
    return False


def path_targets_closed_residual(
    run_dir: Path, cfg: dict[str, Any] | None = None
) -> bool:
    """True when recovery/next path cites closed residual ids but open leaves exist."""
    cfg = cfg or load_goal_priority(run_dir)
    open_leaves = open_residual_leaves(run_dir, cfg)
    if not open_leaves:
        return False
    inv = cfg.get("_residual_inventory") or load_residual_inventory(run_dir)
    closed_ids: set[str] = set()
    open_ids: set[str] = set()
    for leaf in inv.get("leaves") or []:
        if not isinstance(leaf, dict):
            continue
        lid = str(leaf.get("id") or "").strip()
        if not lid:
            continue
        status = str(leaf.get("status") or "open").lower()
        aliases = {lid}
        for a in leaf.get("recovery_aliases") or []:
            if str(a).strip():
                aliases.add(str(a).strip())
        if status == "closed":
            closed_ids |= aliases
        elif status == "open":
            open_ids |= aliases
    path_text = _current_path_text(run_dir).lower()
    if not path_text:
        return False
    cites_open = any(oid.lower() in path_text for oid in open_ids)
    cites_closed = any(cid.lower() in path_text for cid in closed_ids)
    # Steer if path still pushes closed strata and does not prioritize an open leaf.
    return cites_closed and not cites_open


def _current_path_text(run_dir: Path) -> str:
    parts: list[str] = []
    sp = Path(run_dir) / "loop_state.json"
    if sp.is_file():
        try:
            state = json.loads(sp.read_text(encoding="utf-8"))
            parts.append(str(state.get("next_preferred_path") or ""))
        except (OSError, json.JSONDecodeError):
            pass
    rp = Path(run_dir) / "recovery.md"
    if rp.is_file():
        try:
            for line in rp.read_text(encoding="utf-8").splitlines():
                if "Next safe action" in line or "next safe action" in line.lower():
                    parts.append(line)
        except OSError:
            pass
    return "\n".join(parts)


def _next_iteration_number(run_dir: Path) -> int:
    rows = read_iterations_jsonl(run_dir)
    last = 0
    for row in rows:
        try:
            last = max(last, int(row.get("iteration") or 0))
        except (TypeError, ValueError):
            continue
    sp = Path(run_dir) / "loop_state.json"
    if sp.is_file():
        try:
            state = json.loads(sp.read_text(encoding="utf-8"))
            last = max(last, int(state.get("last_iteration") or 0))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return last + 1


def propose_hard_replan_path(
    run_dir: Path, cfg: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Propose next_preferred_path text for hard mode (does not write)."""
    cfg = cfg or load_goal_priority(run_dir)
    nxt = _next_iteration_number(run_dir)
    open_leaves = open_residual_leaves(run_dir, cfg)
    # Prefer open leaves with higher goal_ev, then stable id order.
    ev_rank = {"high": 0, "medium": 1, "low": 2, "none": 3, "": 4}

    def leaf_key(leaf: dict[str, Any]) -> tuple[int, str]:
        return (
            ev_rank.get(str(leaf.get("goal_ev") or "").lower(), 4),
            str(leaf.get("id") or ""),
        )

    if open_leaves:
        leaf = sorted(open_leaves, key=leaf_key)[0]
        lid = str(leaf.get("id") or "open-leaf")
        camp = str(leaf.get("campaign_id") or cfg.get("primary_campaign") or "primary")
        desc = str(leaf.get("description") or lid)[:240]
        scope = str(leaf.get("scope_lock") or "encoding_only")
        path = (
            f"SINGLE PATH (iteration {nxt}, hard replan): execute residual leaf "
            f"`{lid}` under campaign `{camp}` — {desc}. "
            f"Scope lock: `{scope}` (encoding progress ≠ full GOAL-SC unless bridge). "
            f"Do not treat closed residual strata as sole primary. "
            f"Keep banked regressions. Do not ambient hosts, M3 waterfall, "
            f"random gadgets, or OpenGauss-primary."
        )
        return {
            "kind": "open_leaf",
            "residual_id": lid,
            "campaign_id": camp,
            "scope_lock": scope,
            "path": path,
            "reason": "open_residual_leaf_priority",
        }

    # No open leaves: pivot to next campaign after primary if available.
    primary = str(cfg.get("primary_campaign") or "").strip()
    ordered = [str(x).strip() for x in (cfg.get("next_campaigns_ordered") or []) if str(x).strip()]
    pick = ""
    for cid in ordered:
        if cid != primary:
            pick = cid
            break
    if not pick and ordered:
        pick = ordered[0]
    if not pick:
        pick = primary or "main"
    obj = _campaign_objective(cfg, pick) or str(cfg.get("primary_objective") or "")
    path = (
        f"SINGLE PATH (iteration {nxt}, hard replan): residual inventory has no "
        f"open leaves. Pivot to campaign `{pick}` — {obj[:200] or 'advance loop goal'}. "
        f"Do not restart closed encoding residual as sole primary without a new "
        f"mechanism. Keep regressions. No ambient hosts / M3 waterfall / OpenGauss-primary."
    )
    return {
        "kind": "campaign_pivot",
        "residual_id": "",
        "campaign_id": pick,
        "scope_lock": "",
        "path": path,
        "reason": "no_open_leaves_campaign_pivot",
    }


def apply_hard_path_discipline(run_dir: Path) -> dict[str, Any]:
    """Hard mode: rewrite next_preferred_path + recovery next-action when replan is required.

    Never writes loop_state.status, goal, success_criteria, or budget.
    Never fail-closes the loop. Soft/advise modes return applied=False.
    """
    run_dir = Path(run_dir)
    cfg = load_goal_priority(run_dir)
    result: dict[str, Any] = {
        "applied": False,
        "mode": discipline_mode(cfg),
        "replan_required": False,
        "reason": "",
        "path": "",
    }
    if not cfg.get("_active"):
        result["reason"] = "inactive"
        return result
    if discipline_mode(cfg) != "hard":
        result["reason"] = "not_hard_mode"
        return result
    need = replan_required(run_dir, cfg)
    result["replan_required"] = need
    if not need:
        result["reason"] = "replan_not_required"
        return result

    proposal = propose_hard_replan_path(run_dir, cfg)
    new_path = str(proposal.get("path") or "").strip()
    if not new_path:
        result["reason"] = "empty_proposal"
        return result

    # --- loop_state.next_preferred_path only ---
    state_path = run_dir / "loop_state.json"
    old_path = ""
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                result["reason"] = "loop_state_not_object"
                return result
            old_path = str(state.get("next_preferred_path") or "")
            # Never touch status / goal / success_criteria / budget fields.
            state["next_preferred_path"] = new_path
            # optional stamp for audit
            so = state.get("standing_orders")
            if not isinstance(so, dict):
                so = {}
                state["standing_orders"] = so
            gp_so = so.get("goal_priority")
            if not isinstance(gp_so, dict):
                gp_so = {}
                so["goal_priority"] = gp_so
            gp_so["last_hard_replan_path"] = new_path[:500]
            gp_so["last_hard_replan_reason"] = str(proposal.get("reason") or "")
            state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            _atomic_write_text(state_path, json.dumps(state, indent=2) + "\n")
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            result["reason"] = f"loop_state_write_failed:{exc}"
            return result

    # --- recovery.md Next safe action bullet only ---
    recovery_path = run_dir / "recovery.md"
    if recovery_path.is_file():
        try:
            lines = recovery_path.read_text(encoding="utf-8").splitlines()
            replaced = False
            new_lines: list[str] = []
            bullet = f"- **Next safe action:** {new_path}"
            for line in lines:
                if line.lstrip().startswith("- **Next safe action:**") or (
                    "Next safe action:" in line and line.lstrip().startswith("-")
                ):
                    new_lines.append(bullet)
                    replaced = True
                else:
                    new_lines.append(line)
            if not replaced:
                # Insert after Status / Last completed if possible
                inserted = False
                out2: list[str] = []
                for line in new_lines:
                    out2.append(line)
                    if not inserted and line.lstrip().startswith(
                        "- **Last completed iteration:**"
                    ):
                        out2.append(bullet)
                        inserted = True
                if not inserted:
                    out2.append(bullet)
                new_lines = out2
            _atomic_write_text(recovery_path, "\n".join(new_lines) + "\n")
        except OSError as exc:
            result["reason"] = f"recovery_write_failed:{exc}"
            # path was already updated in loop_state; still report partial
            result["applied"] = True
            result["path"] = new_path
            result["old_path"] = old_path[:300]
            result["partial"] = True
            return result

    # Audit log under driver_logs (best-effort)
    try:
        log_dir = run_dir / "driver_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        audit = {
            "schema_version": "goal_priority_hard_replan.v1",
            "applied": True,
            "reason": proposal.get("reason"),
            "kind": proposal.get("kind"),
            "residual_id": proposal.get("residual_id"),
            "campaign_id": proposal.get("campaign_id"),
            "path": new_path,
            "old_path": old_path[:500],
            "streak": local_without_goal_delta_streak(run_dir, cfg),
        }
        _atomic_write_text(
            log_dir / "goal_priority_hard_replan.json",
            json.dumps(audit, indent=2) + "\n",
        )
    except OSError:
        pass

    result.update(
        {
            "applied": True,
            "reason": str(proposal.get("reason") or "hard_replan"),
            "path": new_path,
            "old_path": old_path[:300],
            "kind": proposal.get("kind"),
            "residual_id": proposal.get("residual_id"),
            "campaign_id": proposal.get("campaign_id"),
        }
    )
    return result

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
    mode = discipline_mode(cfg)
    lines = [
        "",
        "## Goal-focused path discipline (goal_priority.v1 — active)",
        "Prefer primary paths that advance the loop goal / success criteria.",
        "Favor outcomes that kill, bridge, construct, verify trust, or replan",
        "over unbounded local samples that do not reduce goal uncertainty.",
        f"- Discipline mode: `{mode}`"
        + (
            " — **hard path steering active**: host may rewrite "
            "`next_preferred_path` / recovery next-action on REPLAN_REQUIRED "
            "(never writes loop_state.status)."
            if mode == "hard"
            else " (does not write loop_state.status)."
        ),
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
    open_leaves = open_residual_leaves(run_dir, cfg)
    if open_leaves:
        leaf_bits = []
        for leaf in open_leaves[:12]:
            lid = str(leaf.get("id") or "")
            sl = str(leaf.get("scope_lock") or "")
            leaf_bits.append(f"`{lid}`" + (f" [{sl}]" if sl else ""))
        lines.append("- Open residual leaves: " + ", ".join(leaf_bits))
    epoch = cfg.get("host_signal_epoch_iteration")
    if epoch is not None:
        lines.append(
            f"- Host-signal epoch iteration: {epoch} "
            "(rows before epoch are not host-counted toward local streak)."
        )
    lines.append(
        "- When appending, prefer ledger fields: "
        f"`--goal-contribution` (prefer {PREFERRED_CONTRIBUTIONS_TEXT} over bare "
        "advance), `--campaign-id`, "
        "optional `--residual-id` / `--scope-lock`, and if applicable "
        "`--local-without-goal-delta` / `--local-without-goal-delta-tag`."
    )
    lines.append(
        "- Scope note: `encoding_only` residual work is campaign progress, not "
        "full goal resolution, until bridge/transfer obligations are discharged."
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
                "**Never** use REPLAN_REQUIRED as authority for `--decision stop|blocked`;",
                "the headless driver owns stop conditions.",
            ]
        )
        if open_leaves:
            ids = ", ".join(f"`{leaf.get('id')}`" for leaf in open_leaves[:8])
            lines.append(f"Concrete open leaves to consider: {ids}.")
    lines.append(
        "This does **not** stop the loop (enforcement unchanged). Soft discipline only."
    )
    lines.append(
        "REPLAN_REQUIRED / goal_priority never authorizes `--decision stop|blocked`; "
        "the headless driver owns stop conditions."
    )
    lines.append("")
    return "\n".join(lines)


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
    inv = cfg.get("_residual_inventory") or {}
    warnings.extend(list(inv.get("_warnings") or []))
    if not cfg.get("_active"):
        return warnings
    mode = discipline_mode(cfg)
    if latest_record is not None and cfg.get("require_goal_contribution_in_ledger", True):
        if not str(latest_record.get("goal_contribution") or "").strip():
            warnings.append(
                "goal_priority active: latest iteration missing goal_contribution "
                "(use --goal-contribution)"
            )
        elif mode in {"advise", "hard"}:
            gc = str(latest_record.get("goal_contribution") or "").strip()
            if contribution_is_generic_advance(gc):
                warnings.append(
                    "goal_priority advise+: bare goal_contribution 'advance' is "
                    f"discouraged; prefer {PREFERRED_CONTRIBUTIONS_TEXT} when "
                    "accurate"
                )
            elif gc and gc not in CONTRIBUTION_VOCABULARY:
                # Soft open vocabulary still allowed; advise+ notes unknown labels.
                warnings.append(
                    f"goal_priority advise+: goal_contribution {gc!r} not in "
                    "recommended vocabulary (ok, still accepted; hard mode coerces)"
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
        "discipline_mode": "soft",
        "primary_campaign": "main",
        "primary_objective": "State what this campaign must produce for loop_state.goal",
        "campaign_registry": {
            "main": {
                "objective": "Advance the stated goal with a host-verifiable artifact",
                "entry_condition": "Always while this is primary",
                "non_goals": ["Unbounded local sampling without goal reduction"],
            }
        },
        "closed_campaigns": [
            {
                "id": "example-closed",
                "kind": "certified_host_classification",
                "forbid_as_sole_primary": True,
                "note": (
                    "Replace or delete: a finished campaign hard mode must not "
                    "re-target as sole primary."
                ),
            }
        ],
        "next_campaigns_ordered": ["main"],
        "max_consecutive_local_without_goal_delta": 3,
        "local_without_goal_delta_tags": list(GENERIC_LOCAL_TAGS),
        "require_goal_contribution_in_ledger": True,
        "panel_rank_by_goal_ev": True,
        "host_signal_epoch_iteration": None,
    }
    return json.dumps(example, indent=2) + "\n"


def example_residual_inventory_json() -> str:
    example = {
        "schema_version": "residual_inventory.v1",
        "host_signal_epoch_iteration": None,
        "leaves": [
            {
                "id": "example-leaf",
                "campaign_id": "main",
                "description": "Replace with a real open residual leaf",
                "status": "open",
                "scope_lock": "encoding_only",
                "goal_ev": "medium",
                "max_iterations_before_replan": None,
                "recovery_aliases": ["example-leaf"],
                "evidence_refs": [],
                "closed_by_iteration": None,
            }
        ],
    }
    return json.dumps(example, indent=2) + "\n"
