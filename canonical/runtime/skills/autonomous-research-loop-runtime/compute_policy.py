#!/usr/bin/env python3
"""Compute policy text shared by every agent an ARL loop launches.

Why this module exists
----------------------
The heavy-compute rules live in the autonomous-research-loop skill body and in
``canonical/instructions/compute-offload-routing.md``. Neither reaches the agents
that actually run compute:

* ``iteration_prompt`` (the drive primary contract) said nothing about compute,
  so a loop could only enforce lanes by hand-wiring a suffix into a per-loop
  ``AAS_AUTOLOOP_CMD_<PROVIDER>`` wrapper.
* Panel agents are worse off. ``panel_parent`` builds their brief from
  ``recovery.md`` and the iteration artifacts alone, does not honour the command
  override, and never shows ``loop_state.standing_orders``. A panel reviewer is
  therefore structurally unable to learn the loop's compute rules.

The observed failure: a result-review agent launched a detached 90-minute local
search that outlived the iteration it belonged to, kept a core busy for its full
timeout on a saturated 4-core host, and produced nothing anyone collected. A
backend allowlist in the broker cannot prevent that, because the job never
reached the broker.

This module renders one block, injected into both prompt builders.

Contract: never raise and never block prompt construction. A loop with no
standing orders still gets the standing rules; an unreadable ``loop_state.json``
degrades to the standing rules alone.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

# Kept short on purpose: it is prepended to every panel brief and iteration
# contract, and panel briefs are truncated at max_chars.
STANDING_RULES = (
    "## Compute policy (binding)\n"
    "\n"
    "1. Do not launch detached or backgrounded heavy compute. No `nohup`, no "
    "trailing `&`, no bare `timeout ... &`. Your session ends when this "
    "iteration or review ends; a detached job outlives it, holds a core for its "
    "whole timeout, and no gate ever collects its output.\n"
    "2. Route heavy computation (enumeration, exhaustive or prescribed-endpoint "
    "search, censuses, sweeps, certificate suites) through the compute broker "
    "rather than running it inline. Use `run plan` as the **routing** decision "
    "boundary and let its adequacy and self-preservation policy choose among "
    "permitted lanes. `run plan` does **not** execute Hetzner or Kaggle work.\n"
    "3. After a plan selects (or the loop allowlists) Hetzner or Kaggle, execute "
    "via the **lane skill**: Hetzner free `preflight` then `oneshot --confirm` "
    "(up→push→run→wait→fetch→down with guaranteed teardown); Kaggle free "
    "`preflight` then `run --confirm`. Prefer disjoint dual-lane shards when "
    "both lanes are allowed and the work partitions. Host-verify fetched "
    "`out/` / runner logs before banking; never bank agent summary alone.\n"
    "4. Ensure lane credentials are in the **process environment** before "
    "lifecycle verbs: Hetzner needs `HCLOUD_TOKEN` set (env only; never print, "
    "never argv, never an `hcloud` context file). If it is unset, use an "
    "installation- or loop-owned secret loader — do not invent a store path in "
    "prompt prose. Kaggle needs its API token path as the kaggle skill documents. "
    "Note: `doctor`/`preflight` may run without a Hetzner token, but "
    "`available`/`api_unreachable` without a loaded token is not a final "
    "infrastructure verdict.\n"
    "5. Treat user-named compute resources as a strict allowlist. When the loop "
    "names lanes, encode them as `policy.backends` on the job and use only "
    "those. Do not fall through to local or to an unlisted lane while any "
    "listed lane is still available. If all listed lanes appear exhausted, "
    "re-run **same-bundle lane preflight** from a token-injected environment "
    "and record the fields (`adequate`, `available`, `budget_verdict`, "
    "`reason`) before banking a multi-iteration infrastructure blocker. That "
    "recheck is diagnostic; it does not widen the allowlist or authorize local "
    "heavy substitute.\n"
    "6. Local compute is for work that finishes in about a minute on one core: "
    "smoke tests, bundle validation, reading a result a lane already produced. "
    "Run it under the loop's guard or the throttled local queue, never bare.\n"
    "7. A refusal from a guard or the broker is an instruction, not a result. It "
    "means partition the work and send it remote (or recheck lane preflight per "
    "rule 5); it never means retry heavy work unthrottled locally.\n"
    "8. Interpret preflight carefully: `ok: true` is not availability. Use "
    "`available`, `adequate`, `within_auto_approve`, and `budget_verdict` "
    "together. Prefer `oneshot` over ad-hoc up/run without teardown; if a run "
    "crashes mid-lifecycle use the lane `down --orphans --confirm` path. "
    "See `skills/hetzner-research-compute/references/agent-loop-integration.md`."
)


def _read_regular_text(path: Path, *, max_bytes: int = 2_000_000) -> str:
    """Read one regular file without following a leaf symlink."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            raise OSError(f"unsafe or oversized compute-policy input: {path}")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            payload = handle.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise OSError(f"oversized compute-policy input: {path}")
        return payload.decode("utf-8")
    finally:
        os.close(fd)


def _standing_orders_compute_from_state(state: Any) -> str:
    """Render compute standing orders from an already trusted document."""

    if not isinstance(state, dict):
        return ""
    orders = state.get("standing_orders")
    if not isinstance(orders, dict):
        return ""
    value = orders.get("compute")
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, (list, tuple)):
        text = "\n".join(f"- {str(v).strip()}" for v in value if str(v).strip())
    elif isinstance(value, dict):
        allowed = value.get("allowed_services") or value.get("backends") or []
        forbidden = value.get("forbidden_services") or []
        if isinstance(allowed, str):
            allowed = [allowed]
        if isinstance(forbidden, str):
            forbidden = [forbidden]
        text = (
            "Structured allowlist: "
            + (", ".join(str(item) for item in allowed if str(item)) or "none specified")
            + "; forbidden: "
            + (", ".join(str(item) for item in forbidden if str(item)) or "none")
            + "."
        )
    else:
        return ""
    return text[:2000]


def _goal_focus_compute_policy_from_plan(value: Any) -> str:
    """Render the canonical reviewed plan policy from a trusted document."""

    if not isinstance(value, dict) or not isinstance(value.get("compute_policy"), dict):
        return ""
    policy = value["compute_policy"]
    allowed = policy.get("allowed_services") or []
    forbidden = policy.get("forbidden_services") or []
    if isinstance(allowed, str):
        allowed = [allowed]
    if isinstance(forbidden, str):
        forbidden = [forbidden]
    return (
        "Authoritative current_plan allowlist: "
        + (", ".join(str(item) for item in allowed if str(item)) or "no plan-specific restriction")
        + "; forbidden: "
        + (", ".join(str(item) for item in forbidden if str(item)) or "none")
        + ". Report each actual service in the staged record, or explicitly report no compute."
    )[:2000]


def compute_policy_block_from_documents(
    loop_state: Any = None, current_plan: Any = None
) -> str:
    """Render a panel-safe compute block from host-opened documents."""

    parts = [STANDING_RULES]
    goal_focus_policy = _goal_focus_compute_policy_from_plan(current_plan)
    orders = _standing_orders_compute_from_state(loop_state)
    if goal_focus_policy:
        parts.append("### Goal-Focus compute policy\n\n" + goal_focus_policy)
    if orders:
        parts.append(
            "### Standing orders for this loop (loop_state.standing_orders.compute)"
            "\n\n" + orders
        )
    return "\n\n".join(parts)


def compute_policy_block(run_dir: Path | str | None = None) -> str:
    """Render the compute block for an agent prompt.

    Returns a leading-newline-separated section, or the standing rules alone
    when no loop-specific orders exist. Safe to concatenate unconditionally.
    """
    if run_dir is not None:
        state: Any = None
        plan: Any = None
        try:
            state_path = Path(run_dir) / "loop_state.json"
            state = json.loads(_read_regular_text(state_path))
        except Exception:  # noqa: BLE001 — prompt construction must never fail
            state = None
        try:
            plan_path = Path(run_dir) / "current_plan.json"
            plan = json.loads(_read_regular_text(plan_path))
        except Exception:  # noqa: BLE001 — prompt construction must never fail
            plan = None
        return compute_policy_block_from_documents(state, plan)
    return compute_policy_block_from_documents()


def compute_policy_addon(run_dir: Path | str | None = None) -> str:
    """Same block, prefixed for appending to an existing prompt string."""
    return "\n\n" + compute_policy_block(run_dir)
