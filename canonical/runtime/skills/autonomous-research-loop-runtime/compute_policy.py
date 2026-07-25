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
    "rather than running it inline. Use `run plan` as the decision boundary and "
    "let its adequacy and self-preservation policy choose the lane.\n"
    "3. Treat user-named compute resources as a strict allowlist. When the loop "
    "names lanes, encode them as `policy.backends` on the job and use only "
    "those. Do not fall through to local or to an unlisted lane while any "
    "listed lane is still available; if all listed lanes are exhausted, report "
    "that instead of substituting a lane nobody asked for.\n"
    "4. Local compute is for work that finishes in about a minute on one core: "
    "smoke tests, bundle validation, reading a result a lane already produced. "
    "Run it under the loop's guard or the throttled local queue, never bare.\n"
    "5. A refusal from a guard or the broker is an instruction, not a result. It "
    "means partition the work and send it remote; it never means retry locally."
)


def _standing_orders_compute(run_dir: Path) -> str:
    """Free-text compute standing orders a loop declares once in loop_state.

    Read from ``standing_orders.compute``; accepts a string or a list of
    strings. This is the general form of a per-loop CLAUDE.md workaround: the
    loop names its lanes once and every agent, primary and panel, sees them.
    """
    state_path = Path(run_dir) / "loop_state.json"
    try:
        state: Any = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return ""
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
    else:
        return ""
    return text[:2000]


def compute_policy_block(run_dir: Path | str | None = None) -> str:
    """Render the compute block for an agent prompt.

    Returns a leading-newline-separated section, or the standing rules alone
    when no loop-specific orders exist. Safe to concatenate unconditionally.
    """
    parts = [STANDING_RULES]
    if run_dir is not None:
        try:
            orders = _standing_orders_compute(Path(run_dir))
        except Exception:  # noqa: BLE001 — prompt construction must never fail
            orders = ""
        if orders:
            parts.append(
                "### Standing orders for this loop (loop_state.standing_orders.compute)"
                "\n\n" + orders
            )
    return "\n\n".join(parts)


def compute_policy_addon(run_dir: Path | str | None = None) -> str:
    """Same block, prefixed for appending to an existing prompt string."""
    return "\n\n" + compute_policy_block(run_dir)
