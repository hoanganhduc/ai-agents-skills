"""Multi-backend parallel fan-out scheduler for the research broker (v2).

This is a SCHEDULER LAYER ON TOP of the per-lane probes/drivers (local veto, Kaggle,
Modal, Hetzner, GitHub Actions). It splits ONE divisible batch job of M independent,
resumable chunks across MULTIPLE lanes AT ONCE -- some chunks local, some free (Kaggle),
some paid (Hetzner/Modal) -- each lane sized to its spare capacity, to minimise makespan
(time until every chunk's result is back) while minimising cost. Small jobs still use the
single-lane router in ``planner.plan_job``; fan-out is for LARGE divisible jobs only.

The design has three clean layers:

  1. A PURE, deterministic allocator (:func:`allocate`) that takes normalised lane
     profiles + the chunk count M + a ``speed_cost_weight`` knob and returns an integer
     split. It performs NO IO and NO probing, so it is fully unit-testable and gives the
     same answer for the same inputs.
  2. An ADAPTER (:func:`build_lane_profiles`) that turns each lane's probe verdict + the
     broker config into a :class:`LaneProfile`, computing every HARD RAIL as a per-lane
     ``max_chunks`` ceiling (see below). The probes are injection-first offline, so this
     stays network-free in tests.
  3. AGGREGATION + FAULT-TOLERANCE (:func:`merge_partials`, :func:`reassign`): collect
     each lane's partial ``out/`` and merge with the bundle's vacuity guard; a stalled or
     failed lane's UNFINISHED chunks are reassigned to a healthy lane (chunks are
     resumable, so no work is lost).

Objective. A per-job knob ``speed_cost_weight`` in [0, 1] blends the two goals:

    objective = weight * norm(makespan) + (1 - weight) * norm(cost)

``weight = 0`` is cheapest (free/cheap lanes only, accept a slower finish); ``weight = 1``
is fastest (recruit paid lanes aggressively to cut makespan); values between blend the
two. The allocator water-fills chunks by cost-efficiency (free/cheap lanes first) and
balances finish-times, subject to the objective and every lane's capacity.

Hard rails (ceilings the knob can NEVER override). Each rail is expressed as a lane's
``max_chunks`` ceiling, so a speed-leaning knob uses more *allowed* capacity but can never
breach a cap:

  * per-lane budget caps (Hetzner daily EUR, Modal per-job USD),
  * the <= EUR 3/day auto-approve envelope (Hetzner ``max_eur_per_job``),
  * GitHub Actions' 60% cumulative-minutes cap,
  * Kaggle's quota (the weekly GPU-hour self-cap; CPU is free and quota-free), and
  * local's self-preservation load-cap (``w_safe`` from the veto) and wall budget.

Cost model. Local (Oracle Cloud) is NOT free -- its per-core-hour cost enters the
objective. Kaggle is the free lane (cost 0). Hetzner is billed in EUR and normalised into
the objective's USD cost term through ``fanout_usd_per_eur``; GitHub Actions minutes are
prepaid, so their marginal objective cost is 0 but their consumption is rail-limited.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from . import github_actions_backend, hetzner_backend, kaggle_backend, planner

# Lanes in the canonical routing order; used only as a deterministic tie-break so that,
# among equally-attractive lanes, the earlier (cheaper/free) lane fills first.
_LANE_ORDER = ("local", "kaggle", "modal", "hetzner", "gha")


class FanoutError(RuntimeError):
    pass


class MergeVacuityError(FanoutError):
    """Raised when a merge would produce a vacuous result (no non-empty chunk output),
    mirroring the portable bundle's non-vacuous banked-value guard."""


@dataclass(frozen=True)
class LaneProfile:
    """Normalised, unit-agnostic description of one lane for the pure allocator.

    All time is in seconds and all objective cost is in a single normalised USD-equivalent
    unit (Kaggle and GitHub Actions are 0). ``max_chunks`` is the HARD RAIL: the most
    chunks this lane may accept under its budget/quota/liveness. ``native_cost_unit`` /
    ``native_cost_per_chunk`` carry the real per-lane unit (eur / usd / minutes / free) for
    reporting and to prove which rail bounds the lane; the allocator never reads them.
    """

    name: str
    available: bool
    slots: int
    chunk_seconds: float
    cost_per_chunk: float
    startup_seconds: float
    max_chunks: int
    native_cost_unit: str = "usd"
    native_cost_per_chunk: float = 0.0
    rail_reason: str = ""

    @property
    def is_free(self) -> bool:
        return self.cost_per_chunk <= 0.0

    @property
    def usable(self) -> bool:
        return bool(self.available and self.slots > 0 and self.max_chunks > 0)

    def finish_seconds(self, chunks: int) -> float:
        """Wall-seconds until ``chunks`` chunks are all done on this lane (0 if unused).
        Continuous drain over ``slots`` parallel slots plus the fixed startup latency."""
        if chunks <= 0 or self.slots <= 0:
            return 0.0
        return float(self.startup_seconds) + (chunks / float(self.slots)) * float(self.chunk_seconds)

    def cost(self, chunks: int) -> float:
        return max(0, int(chunks)) * float(self.cost_per_chunk)


@dataclass
class Allocation:
    """Result of :func:`allocate`: the integer split plus its makespan/cost and the
    objective breakdown. ``counts`` maps a lane name to its chunk count (0 omitted).
    ``feasible`` is False when the lanes' combined ``max_chunks`` cannot cover M; the
    ``shortfall`` chunks are then left unassigned for the caller to surface."""

    counts: dict[str, int]
    makespan_seconds: float
    total_cost: float
    weight: float
    feasible: bool
    shortfall: int
    objective: float
    makespan_norm: float
    cost_norm: float
    per_lane: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "counts": dict(self.counts),
            "makespan_seconds": round(self.makespan_seconds, 3),
            "makespan_hours": round(self.makespan_seconds / 3600.0, 4),
            "total_cost_usd": round(self.total_cost, 4),
            "speed_cost_weight": self.weight,
            "feasible": self.feasible,
            "shortfall": self.shortfall,
            "objective": round(self.objective, 6),
            "makespan_norm": round(self.makespan_norm, 6),
            "cost_norm": round(self.cost_norm, 6),
            "per_lane": self.per_lane,
        }


# --------------------------------------------------------------------------------------
# Pure allocator (deterministic given lanes + M + weight; no IO, no probing)
# --------------------------------------------------------------------------------------

def _lane_rank(name: str) -> int:
    try:
        return _LANE_ORDER.index(name)
    except ValueError:  # pragma: no cover - unknown lane sorts last, stably
        return len(_LANE_ORDER)


class _LaneState:
    """Mutable per-lane accumulator used only inside the greedy fill."""

    __slots__ = ("profile", "count", "headroom")

    def __init__(self, profile: LaneProfile, *, count: int, headroom: int) -> None:
        self.profile = profile
        self.count = count
        self.headroom = headroom

    def marginal_finish(self) -> float:
        return self.profile.finish_seconds(self.count + 1)


def _references(lanes: list[LaneProfile], total: int) -> tuple[float, float]:
    """Fixed normalisation scales for the blended objective. ``t_ref`` is the slowest
    full-load finish (a stable upper bound on any lane's marginal finish); ``c_ref`` is the
    most expensive lane's per-chunk cost. Both only set the RELATIVE weighting between the
    time and cost terms, and both extremes (weight 0 / weight 1) are independent of the
    other's reference, so the knob's endpoints are robust to this choice."""
    t_ref = 0.0
    c_ref = 0.0
    for lane in lanes:
        if not lane.usable:
            continue
        share = min(total, lane.max_chunks)
        t_ref = max(t_ref, lane.finish_seconds(share))
        c_ref = max(c_ref, lane.cost_per_chunk)
    return (t_ref or 1.0), c_ref


def _priority(state: _LaneState, weight: float, t_ref: float, c_ref: float) -> float:
    """Blended marginal priority for placing the next chunk on ``state`` (lower is better).
    The time term is the lane's resulting finish (earliest-finish water-fill balances the
    lanes); the cost term is the lane's per-chunk cost. Normalising each by a fixed
    reference keeps the blend scale-invariant."""
    time_term = state.marginal_finish() / t_ref if t_ref > 0 else 0.0
    cost_term = state.profile.cost_per_chunk / c_ref if c_ref > 0 else 0.0
    return weight * time_term + (1.0 - weight) * cost_term


def _place_one(states: list[_LaneState], weight: float, t_ref: float, c_ref: float) -> _LaneState | None:
    """Pick the lane with headroom that minimises the blended marginal priority.
    Deterministic tie-breaks: cheaper per-chunk cost, then earlier finish, then routing
    order, then lane name."""
    best: _LaneState | None = None
    best_key: tuple[float, float, float, int, str] | None = None
    for state in states:
        if state.headroom <= 0:
            continue
        key = (
            _priority(state, weight, t_ref, c_ref),
            state.profile.cost_per_chunk,
            state.marginal_finish(),
            _lane_rank(state.profile.name),
            state.profile.name,
        )
        if best_key is None or key < best_key:
            best, best_key = state, key
    return best


def allocate(lanes: list[LaneProfile], total_chunks: int, *, speed_cost_weight: float = 0.5) -> Allocation:
    """Split ``total_chunks`` (M) across ``lanes`` to minimise the blended objective.

    Pure and deterministic: identical ``lanes``/``total_chunks``/``speed_cost_weight`` give
    an identical :class:`Allocation`. The heuristic is a greedy water-fill -- each chunk is
    placed on the lane that minimises ``weight * norm(finish) + (1 - weight) * norm(cost)``,
    subject to every lane's ``max_chunks`` rail. At ``weight = 0`` this fills the free/cheap
    lanes first and never recruits a paid lane while a cheaper one still has headroom; at
    ``weight = 1`` it balances finish-times across all usable lanes (including paid ones) to
    cut the makespan. No lane is ever assigned beyond its ``max_chunks`` ceiling, so the
    knob can lean on more *allowed* capacity but can never breach a hard rail."""
    weight = min(1.0, max(0.0, float(speed_cost_weight)))
    total = max(0, int(total_chunks))
    usable = [lane for lane in lanes if lane.usable]
    t_ref, c_ref = _references(usable, total) if usable else (1.0, 0.0)

    states = [_LaneState(lane, count=0, headroom=min(lane.max_chunks, total)) for lane in usable]
    placed = 0
    for _ in range(total):
        chosen = _place_one(states, weight, t_ref, c_ref)
        if chosen is None:  # every usable lane is at its rail ceiling
            break
        chosen.count += 1
        chosen.headroom -= 1
        placed += 1

    counts = {state.profile.name: state.count for state in states if state.count > 0}
    makespan = max((state.profile.finish_seconds(state.count) for state in states), default=0.0)
    total_cost = sum(state.profile.cost(state.count) for state in states)
    shortfall = total - placed

    makespan_norm = makespan / t_ref if t_ref > 0 else 0.0
    cost_norm = (total_cost / (c_ref * total)) if (c_ref > 0 and total > 0) else 0.0
    objective = weight * makespan_norm + (1.0 - weight) * cost_norm

    per_lane = [
        {
            "lane": state.profile.name,
            "chunks": state.count,
            "slots": state.profile.slots,
            "max_chunks": state.profile.max_chunks,
            "rail_bound": state.count >= state.profile.max_chunks and state.count > 0,
            "finish_seconds": round(state.profile.finish_seconds(state.count), 3),
            "cost_usd": round(state.profile.cost(state.count), 4),
            "native_cost_unit": state.profile.native_cost_unit,
            "native_cost": round(state.profile.native_cost_per_chunk * state.count, 4),
            "rail_reason": state.profile.rail_reason,
        }
        for state in states
        if state.count > 0
    ]

    return Allocation(
        counts=counts,
        makespan_seconds=makespan,
        total_cost=total_cost,
        weight=weight,
        feasible=shortfall == 0,
        shortfall=shortfall,
        objective=objective,
        makespan_norm=makespan_norm,
        cost_norm=cost_norm,
        per_lane=per_lane,
    )


def chunk_ranges(counts: dict[str, int]) -> dict[str, list[int]]:
    """Deterministically map chunk ids ``0..M-1`` to lanes as contiguous ranges, in routing
    order. Used to turn allocator counts into concrete per-lane chunk-id assignments that
    the reassignment logic can operate on."""
    assignment: dict[str, list[int]] = {}
    cursor = 0
    for name in sorted(counts, key=lambda item: (_lane_rank(item), item)):
        size = int(counts[name])
        if size <= 0:
            continue
        assignment[name] = list(range(cursor, cursor + size))
        cursor += size
    return assignment


# --------------------------------------------------------------------------------------
# Partial-failure reassignment (pure)
# --------------------------------------------------------------------------------------

def reassign(
    *,
    assignment: dict[str, list[int]],
    completed_chunks: set[int] | list[int],
    failed_lanes: set[str] | list[str],
    lanes: list[LaneProfile],
    speed_cost_weight: float = 0.5,
) -> dict[str, Any]:
    """Reassign a stalled/failed lane's UNFINISHED chunks to healthy lanes.

    Chunks are resumable, so only the chunks a failed lane had NOT completed are moved; no
    finished work is redone and no chunk is lost. The unfinished chunks are water-filled
    onto the still-healthy lanes with the same greedy used by :func:`allocate`, seeded with
    each healthy lane's already-assigned load so finish-times keep balancing, and bounded by
    each lane's remaining ``max_chunks`` headroom (the hard rails still hold during
    recovery). Returns ``{reassigned, lost_chunks, covered, uncovered, healthy_lanes}``;
    ``uncovered`` is non-empty only when the healthy lanes' combined headroom cannot absorb
    every unfinished chunk."""
    weight = min(1.0, max(0.0, float(speed_cost_weight)))
    completed = set(int(c) for c in completed_chunks)
    failed = set(str(f) for f in failed_lanes)
    by_name = {lane.name: lane for lane in lanes}

    lost = sorted(
        {int(cid) for name in failed for cid in assignment.get(name, []) if int(cid) not in completed}
    )

    healthy: list[_LaneState] = []
    for name, lane in by_name.items():
        if name in failed or not lane.usable:
            continue
        seed = len([c for c in assignment.get(name, []) if int(c) not in completed])
        headroom = max(0, lane.max_chunks - seed)
        healthy.append(_LaneState(lane, count=seed, headroom=headroom))

    t_ref, c_ref = _references([state.profile for state in healthy], len(lost) or 1) if healthy else (1.0, 0.0)

    reassigned: dict[str, list[int]] = {}
    uncovered: list[int] = []
    for cid in lost:
        chosen = _place_one(healthy, weight, t_ref, c_ref)
        if chosen is None:
            uncovered.append(cid)
            continue
        chosen.count += 1
        chosen.headroom -= 1
        reassigned.setdefault(chosen.profile.name, []).append(cid)

    covered = sorted(cid for ids in reassigned.values() for cid in ids)
    return {
        "reassigned": {name: sorted(ids) for name, ids in reassigned.items()},
        "lost_chunks": lost,
        "covered": covered,
        "uncovered": sorted(uncovered),
        "healthy_lanes": sorted(state.profile.name for state in healthy),
        "all_covered": not uncovered,
    }


# --------------------------------------------------------------------------------------
# Aggregation (merge partial out/ with the bundle's vacuity guard)
# --------------------------------------------------------------------------------------

def _chunk_id(record: dict[str, Any]) -> int | None:
    for key in ("chunk", "chunk_id", "index", "shard"):
        if key in record:
            try:
                return int(record[key])
            except (TypeError, ValueError):
                return None
    return None


def _is_vacuous(record: dict[str, Any]) -> bool:
    """A chunk record is vacuous when it carries no actual output. Mirrors the bundle
    merge's non-vacuous banked-value control: an empty ``result``/``value``/``results`` (or
    a zero ``count`` with no values) does not count as real work."""
    if record.get("status") in {"error", "failed", "empty"}:
        return True
    for key in ("result", "results", "value", "values", "output"):
        if key in record:
            payload = record[key]
            if payload in (None, "", [], {}, 0):
                continue
            return False
    return True


def merge_partials(
    lane_outputs: dict[str, list[dict[str, Any]]],
    *,
    expected_chunks: int | None = None,
    require_non_vacuous: bool = True,
) -> dict[str, Any]:
    """Merge each lane's partial ``out/`` records into one result set.

    ``lane_outputs`` maps a lane name to the list of per-chunk records it produced. Records
    are de-duplicated by chunk id (the first-seen wins, so a chunk reassigned after a lane
    failure does not double-count), sorted by chunk id, and checked for coverage against
    ``expected_chunks``. The bundle's vacuity guard is preserved: with
    ``require_non_vacuous`` set, a merge in which every record is vacuous raises
    :class:`MergeVacuityError` rather than banking an empty result."""
    merged: dict[int, dict[str, Any]] = {}
    duplicates: list[int] = []
    unidentified = 0
    for lane in sorted(lane_outputs):
        for record in lane_outputs[lane]:
            cid = _chunk_id(record)
            if cid is None:
                unidentified += 1
                continue
            if cid in merged:
                duplicates.append(cid)
                continue
            merged[cid] = {**record, "_lane": lane}

    chunk_ids = sorted(merged)
    non_vacuous = [cid for cid in chunk_ids if not _is_vacuous(merged[cid])]
    missing: list[int] = []
    if expected_chunks is not None:
        missing = [cid for cid in range(int(expected_chunks)) if cid not in merged]

    if require_non_vacuous and not non_vacuous:
        raise MergeVacuityError(
            f"refusing to bank a vacuous merge: {len(merged)} chunk record(s), none non-vacuous"
        )

    return {
        "merged": [merged[cid] for cid in chunk_ids],
        "chunk_ids": chunk_ids,
        "non_vacuous_chunk_ids": non_vacuous,
        "duplicates": sorted(duplicates),
        "unidentified": unidentified,
        "missing": missing,
        "complete": expected_chunks is not None and not missing,
        "vacuous": not non_vacuous,
    }


# --------------------------------------------------------------------------------------
# Adapter: probes + config -> LaneProfile (computes every hard rail as max_chunks)
# --------------------------------------------------------------------------------------

def _floor_div(numerator: float, denominator: float) -> int:
    if denominator <= 0:
        return 0
    return max(0, int(math.floor(numerator / denominator)))


def chunk_core_seconds(job: dict[str, Any], total_chunks: int) -> float:
    """Per-chunk cost in core-seconds of ONE slot. Uses an explicit per-chunk estimate when
    the manifest provides one (``payload.parameters.chunk_core_hours`` or
    ``constraints.chunk_core_hours``), else the job's total ``core_hours`` divided across M
    chunks, else a conservative floor of one core-minute."""
    payload = dict(job.get("payload", {}) or {})
    parameters = dict(payload.get("parameters", {}) or {})
    constraints = dict(job.get("constraints", {}) or {})
    per_chunk = constraints.get("chunk_core_hours") or parameters.get("chunk_core_hours")
    if per_chunk:
        return float(per_chunk) * 3600.0
    total_core_hours = float(constraints.get("core_hours") or parameters.get("core_hours") or 0.0)
    if total_core_hours > 0 and total_chunks > 0:
        return (total_core_hours / float(total_chunks)) * 3600.0
    return 60.0


def _local_profile(job: dict[str, Any], *, config: Any, resources: dict[str, Any] | None,
                   ccs: float) -> LaneProfile:
    """Local (Oracle Cloud) lane. Availability + parallel width come from the SAME
    self-preservation veto the single-lane router uses, so fan-out honours the load-cap:
    ``slots = w_safe`` and the wall budget bounds ``max_chunks``. Local is NOT free -- its
    per-core-hour cost enters the objective."""
    estimate = planner.build_estimate(
        parameters=dict(dict(job.get("payload", {}) or {}).get("parameters", {}) or {}),
        constraints=dict(job.get("constraints", {}) or {}),
        policy=dict(job.get("policy", {}) or {}),
        gpu_signal=False,
        runtime_sec=0,
    )
    veto = planner.local_self_preservation_probe(estimate, config=config, resources=resources)
    w_safe = max(0, int(veto.get("w_safe", 0)))
    secret = estimate.get("data_locality") == "secret"
    wall_budget_h = float(getattr(config, "local_wall_budget_h", 2.0))
    usd_per_core_hour = float(getattr(config, "fanout_local_usd_per_core_hour", 0.0))
    max_chunks = _floor_div(w_safe * wall_budget_h * 3600.0, ccs)
    available = w_safe >= 1
    return LaneProfile(
        name="local",
        available=available,
        slots=w_safe,
        chunk_seconds=ccs,
        cost_per_chunk=(ccs / 3600.0) * usd_per_core_hour,
        startup_seconds=float(getattr(config, "fanout_local_startup_seconds", 5.0)),
        max_chunks=0 if secret else max_chunks,
        native_cost_unit="usd",
        native_cost_per_chunk=(ccs / 3600.0) * usd_per_core_hour,
        rail_reason=(
            "secret_locality_not_offloadable" if secret else
            (veto.get("reason", "") if available else "local_self_preservation_veto")
        ),
    )


def _kaggle_profile(kg: dict[str, Any], *, config: Any, ccs: float, gpu: bool,
                    total_chunks: int) -> LaneProfile:
    """Kaggle lane -- the free tier. CPU is free and quota-free (``max_chunks`` unbounded up
    to M); GPU is bounded by the self-imposed weekly GPU-hour cap. ``slots`` is the total
    parallel cores (concurrency * kernel cores)."""
    slots = kaggle_backend.concurrency(config) * kaggle_backend.kernel_cores(config)
    # Fan-out availability is ACCOUNT-level (token valid + account usable + the chunk fits a
    # kernel), NOT the single-lane probe's whole-job quota verdict: the weekly GPU-hour quota
    # is enforced per SHARE through max_chunks below, so a lane can still take the chunks that
    # fit the remaining quota even when the whole job would not.
    available = bool(kg.get("account_usable")) and bool(kg.get("adequate", True))
    if gpu:
        cap = float(kg.get("gpu_hours_cap", 0.0) or 0.0)
        used = float(kg.get("gpu_hours_used_week", 0.0) or 0.0)
        remaining_h = max(0.0, cap - used)
        max_chunks = _floor_div(remaining_h * 3600.0, ccs)
        rail = f"kaggle_weekly_gpu_cap remaining {remaining_h:.1f}h"
    else:
        max_chunks = max(0, int(total_chunks))
        rail = "kaggle_cpu_free_quota_free"
    return LaneProfile(
        name="kaggle",
        available=available,
        slots=max(1, slots),
        chunk_seconds=ccs,
        cost_per_chunk=0.0,
        startup_seconds=float(getattr(config, "fanout_kaggle_startup_seconds", 300.0)),
        max_chunks=max_chunks,
        native_cost_unit="free",
        native_cost_per_chunk=0.0,
        rail_reason=rail,
    )


def _hetzner_profile(hz: dict[str, Any], *, config: Any, ccs: float,
                     outstanding_eur: float) -> LaneProfile:
    """Hetzner lane (paid, EUR). ``slots`` is the chosen server's vCPU; the daily EUR cap
    and the <= EUR 3/job auto-approve envelope AND the max-server-hours TTL each bound
    ``max_chunks``. EUR is normalised into the objective's USD cost via ``fanout_usd_per_eur``."""
    spec = hz.get("server_spec") or {}
    vcpu = int(spec.get("vcpu", 0) or 0)
    eur_per_hour = float(spec.get("eur_per_hour", 0.0) or 0.0)
    # Account-level availability (token valid + account usable + an adequate server type),
    # NOT the single-lane probe's whole-job EUR verdict: the daily EUR cap and the auto-approve
    # envelope are enforced per SHARE through max_chunks below.
    available = bool(hz.get("account_usable")) and bool(hz.get("adequate"))
    eur_per_core_hour = (eur_per_hour / vcpu) if vcpu > 0 else 0.0
    eur_per_chunk = (ccs / 3600.0) * eur_per_core_hour
    usd_per_eur = float(getattr(config, "fanout_usd_per_eur", 1.08))

    per_job = float(getattr(config, "hetzner_max_eur_per_job", 0.0))
    per_day = float(getattr(config, "hetzner_max_eur_per_day", 0.0))
    max_server_hours = float(getattr(config, "hetzner_max_server_hours", 0.0))
    day_headroom = max(0.0, min(per_job, per_day - outstanding_eur))
    by_budget = _floor_div(day_headroom, eur_per_chunk) if eur_per_chunk > 0 else 0
    by_ttl = _floor_div(max_server_hours * vcpu * 3600.0, ccs) if vcpu > 0 else 0
    max_chunks = min(by_budget, by_ttl)
    return LaneProfile(
        name="hetzner",
        available=available,
        slots=max(0, vcpu),
        chunk_seconds=ccs,
        cost_per_chunk=eur_per_chunk * usd_per_eur,
        startup_seconds=float(getattr(config, "fanout_hetzner_startup_seconds", 180.0)),
        max_chunks=max_chunks,
        native_cost_unit="eur",
        native_cost_per_chunk=eur_per_chunk,
        rail_reason=(
            f"eur_day_headroom {day_headroom:.2f} (auto-approve envelope EUR{per_job:.2f}); "
            f"budget_cap {by_budget}, ttl_cap {by_ttl}"
        ),
    )


def _modal_profile(modal_ok: tuple[bool, str], *, config: Any, ccs: float) -> LaneProfile:
    """Modal lane (paid, USD). ``slots`` is a configured parallel width; the per-job USD cap
    bounds ``max_chunks``."""
    available, _reason = modal_ok
    usd_per_core_hour = float(getattr(config, "fanout_modal_usd_per_core_hour", 0.10))
    usd_per_chunk = (ccs / 3600.0) * usd_per_core_hour
    per_job_cap = float(getattr(config, "per_job_cost_cap_usd", 0.0))
    max_chunks = _floor_div(per_job_cap, usd_per_chunk) if usd_per_chunk > 0 else 0
    return LaneProfile(
        name="modal",
        available=bool(available),
        slots=max(1, int(getattr(config, "fanout_modal_slots", 16))),
        chunk_seconds=ccs,
        cost_per_chunk=usd_per_chunk,
        startup_seconds=float(getattr(config, "fanout_modal_startup_seconds", 45.0)),
        max_chunks=max_chunks,
        native_cost_unit="usd",
        native_cost_per_chunk=usd_per_chunk,
        rail_reason=f"modal_per_job_usd_cap {per_job_cap:.2f}",
    )


def _gha_profile(gha_ok: bool, gha_detail: dict[str, Any], *, config: Any, ccs: float,
                 os_multiplier: float, outstanding_minutes: float) -> LaneProfile:
    """GitHub Actions lane. Minutes are prepaid, so the marginal objective cost is 0, but the
    cumulative 60% minutes cap bounds ``max_chunks`` -- the remaining allowed minutes divided
    by this job's minutes-per-chunk (timeout inflated by the runner OS multiplier)."""
    cap_minutes = float(gha_detail.get("cap_minutes", 0.0) or 0.0)
    used = float(gha_detail.get("used_this_cycle", 0.0) or 0.0)
    remaining = max(0.0, cap_minutes - used - outstanding_minutes)
    minutes_per_chunk = (ccs / 3600.0) * 60.0 * float(os_multiplier)
    max_chunks = _floor_div(remaining, minutes_per_chunk) if minutes_per_chunk > 0 else 0
    return LaneProfile(
        name="gha",
        available=bool(gha_ok),
        slots=max(1, int(getattr(config, "fanout_gha_slots", 20))),
        chunk_seconds=ccs,
        cost_per_chunk=0.0,
        startup_seconds=float(getattr(config, "fanout_gha_startup_seconds", 60.0)),
        max_chunks=max_chunks,
        native_cost_unit="minutes",
        native_cost_per_chunk=minutes_per_chunk,
        rail_reason=f"gha_60pct_cap remaining {remaining:.0f} of {cap_minutes:.0f} min",
    )


def build_lane_profiles(
    job: dict[str, Any],
    *,
    config: Any,
    resources: dict[str, Any] | None = None,
    modal_ready: bool = False,
    total_chunks: int,
    state_root: Any = None,
) -> list[LaneProfile]:
    """Turn each lane's probe verdict + the broker config into a :class:`LaneProfile`,
    computing every hard rail as that lane's ``max_chunks`` ceiling. Only lanes in
    ``routing_order`` are considered. All probes are injection-first offline (they read the
    liveness snapshot in ``resources``), so this is network-free in tests. GPU divisible
    fan-out uses the Kaggle GPU cap and skips Hetzner (no on-demand GPU)."""
    order = list(getattr(config, "routing_order", list(_LANE_ORDER)))
    constraints = dict(job.get("constraints", {}) or {})
    policy = dict(job.get("policy", {}) or {})
    gpu = bool(policy.get("gpu")) or bool(constraints.get("gpu"))
    ccs = chunk_core_seconds(job, total_chunks)
    parameters = dict(dict(job.get("payload", {}) or {}).get("parameters", {}) or {})
    estimate = planner.build_estimate(parameters=parameters, constraints=constraints,
                                      policy=policy, gpu_signal=gpu, runtime_sec=0)

    profiles: list[LaneProfile] = []
    for name in order:
        if name == "local":
            profiles.append(_local_profile(job, config=config, resources=resources, ccs=ccs))
        elif name == "kaggle":
            kg = kaggle_backend.probe(estimate, config=config, resources=resources, state_root=state_root)
            profiles.append(_kaggle_profile(kg, config=config, ccs=ccs, gpu=gpu, total_chunks=total_chunks))
        elif name == "modal":
            modal_ok = planner.modal_lane_available(config, resources, modal_ready)
            profiles.append(_modal_profile(modal_ok, config=config, ccs=ccs))
        elif name == "hetzner":
            if gpu:
                continue  # Hetzner Cloud has no on-demand GPU; a GPU fan-out skips it.
            hz = hetzner_backend.probe(estimate, config=config, resources=resources, state_root=state_root)
            outstanding_eur = _injected_outstanding(resources, "hetzner")
            profiles.append(_hetzner_profile(hz, config=config, ccs=ccs, outstanding_eur=outstanding_eur))
        elif name == "gha":
            gha_ok, gha_detail = _gha_cap(job, config=config, resources=resources, gpu=gpu)
            os_mult = _gha_os_multiplier(job, config)
            outstanding_min = _injected_outstanding(resources, "gha")
            profiles.append(_gha_profile(gha_ok, gha_detail, config=config, ccs=ccs,
                                         os_multiplier=os_mult, outstanding_minutes=outstanding_min))
    return profiles


def _injected_outstanding(resources: dict[str, Any] | None, backend: str) -> float:
    """Already-reserved budget for a backend, injectable offline via
    ``resources['outstanding'][backend]`` (keeps the adapter deterministic without touching
    the ledger); defaults to 0.0."""
    node = resources.get("outstanding") if isinstance(resources, dict) else None
    if isinstance(node, dict) and backend in node:
        try:
            return float(node[backend])
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _gha_repo_cfg(job: dict[str, Any], config: Any) -> dict[str, Any]:
    template = str(job.get("template", "") or "")
    target = str(job.get("gha_target", "") or template)
    return dict((getattr(config, "gha_repos", {}) or {}).get(target, {}))


def _gha_os_multiplier(job: dict[str, Any], config: Any) -> float:
    repo_cfg = _gha_repo_cfg(job, config)
    runner_os = str(repo_cfg.get("runner_os", "linux")).lower()
    return float(github_actions_backend.OS_MULTIPLIER.get(runner_os, 1.0))


def _gha_cap(job: dict[str, Any], *, config: Any, resources: dict[str, Any] | None,
             gpu: bool) -> tuple[bool, dict[str, Any]]:
    """GitHub Actions availability + the cap detail (which carries the remaining minutes).
    Registered + enabled + (GPU only when opted in) and under the cumulative 60% cap."""
    if not bool(getattr(config, "gha_enabled", False)):
        return False, {"reason": "gha_disabled"}
    if gpu and not bool(getattr(config, "gha_gpu_enabled", False)):
        return False, {"reason": "gha_gpu_disabled"}
    repo_cfg = _gha_repo_cfg(job, config)
    if not repo_cfg:
        return False, {"reason": "gha_target_not_registered"}
    cells = int(dict(job.get("constraints", {}) or {}).get("matrix_cells", 1) or 1)
    return github_actions_backend.usage_cap_ok(repo_cfg=repo_cfg, config=config, cells=cells,
                                               resources=resources)


# --------------------------------------------------------------------------------------
# Top-level fan-out planning (compose adapter + allocator)
# --------------------------------------------------------------------------------------

def job_chunk_count(job: dict[str, Any]) -> int:
    """The number of independent chunks M the divisible job declares."""
    constraints = dict(job.get("constraints", {}) or {})
    parameters = dict(dict(job.get("payload", {}) or {}).get("parameters", {}) or {})
    for source in (constraints, parameters):
        for key in ("chunks", "chunk_count", "shards", "num_chunks"):
            if source.get(key):
                try:
                    return max(0, int(source[key]))
                except (TypeError, ValueError):
                    continue
    return 0


def should_fanout(job: dict[str, Any], *, config: Any) -> tuple[bool, str]:
    """Whether this job should use multi-backend fan-out instead of the single-lane router.
    Fan-out is opt-in (``[fanout].enabled``), only for a LARGE divisible job whose declared
    chunk count reaches ``fanout_min_chunks``. Returns (use_fanout, reason)."""
    if not bool(getattr(config, "fanout_enabled", False)):
        return False, "fanout_disabled"
    m = job_chunk_count(job)
    if m <= 0:
        return False, "job_not_declared_divisible"
    minimum = int(getattr(config, "fanout_min_chunks", 8))
    if m < minimum:
        return False, f"too_small_for_fanout ({m} < {minimum}); use single-lane router"
    return True, f"divisible_batch_job with {m} chunks"


def plan_fanout(
    job: dict[str, Any],
    *,
    config: Any,
    resources: dict[str, Any] | None = None,
    modal_ready: bool = False,
    state_root: Any = None,
) -> dict[str, Any]:
    """Plan a multi-backend fan-out for a large divisible job.

    Composes :func:`build_lane_profiles` (probes + config -> lane profiles with hard-rail
    ceilings) and :func:`allocate` (the pure water-fill), then returns a JSON-able plan: the
    per-lane split, makespan, cost, feasibility, and the chunk-id ranges each lane owns. The
    ``speed_cost_weight`` knob is taken from the job policy (``policy.speed_cost_weight``),
    falling back to the config default. Does NOT dispatch -- execution reuses the existing
    per-lane drivers."""
    accepted_gate, gate_reason = should_fanout(job, config=config)
    m = job_chunk_count(job)
    policy = dict(job.get("policy", {}) or {})
    weight = policy.get("speed_cost_weight")
    if weight is None:
        weight = getattr(config, "fanout_speed_cost_weight", 0.5)
    weight = min(1.0, max(0.0, float(weight)))

    if not accepted_gate:
        return {
            "mode": "fanout",
            "accepted": False,
            "use_fanout": False,
            "chunks": m,
            "speed_cost_weight": weight,
            "reason": gate_reason,
            "fallback": "single_lane_router",
        }

    profiles = build_lane_profiles(job, config=config, resources=resources,
                                   modal_ready=modal_ready, total_chunks=m, state_root=state_root)
    allocation = allocate(profiles, m, speed_cost_weight=weight)
    assignment = chunk_ranges(allocation.counts)

    return {
        "mode": "fanout",
        "accepted": allocation.feasible,
        "use_fanout": True,
        "chunks": m,
        "speed_cost_weight": weight,
        "reason": gate_reason,
        "allocation": allocation.as_dict(),
        "chunk_assignment": assignment,
        "lanes": [
            {
                "lane": lane.name,
                "available": lane.available,
                "usable": lane.usable,
                "slots": lane.slots,
                "max_chunks": lane.max_chunks,
                "cost_per_chunk_usd": round(lane.cost_per_chunk, 6),
                "native_cost_unit": lane.native_cost_unit,
                "startup_seconds": lane.startup_seconds,
                "rail_reason": lane.rail_reason,
            }
            for lane in profiles
        ],
        "risk_flags": ([] if allocation.feasible else ["fanout_capacity_shortfall"]),
    }
