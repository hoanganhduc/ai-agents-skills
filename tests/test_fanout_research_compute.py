"""Offline, credential-free tests for the multi-backend parallel fan-out scheduler (v2).

No network call and no dispatch: the pure allocator, the partial-failure reassignment, the
merge/vacuity guard, the rail-computing adapter (probes are injection-first offline), the
fan-out gate, and the CLI `fanout-plan` entry point are all exercised deterministically with
injected load / liveness / resource snapshots. Mirrors the subprocess / temp-workspace and
in-process hook-injection patterns of test_hetzner_research_compute and
test_kaggle_research_compute.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # never write __pycache__ into the canonical runtime tree

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.runtime import RUNTIME_SOURCE_ROOT

WORKSPACE = RUNTIME_SOURCE_ROOT / "workspace"
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

# (a) imports clean -- a failure here fails the whole module, which is the signal.
from research_compute import fanout  # noqa: E402
from research_compute import config as rc_config  # noqa: E402


# A config that enables every lane plus fan-out, with an easy-to-read rate card so the rail
# arithmetic in the adapter tests is checkable by hand.
CONFIG_TOML = """\
install_id = "test-install"
platform = "linux"
broker_state_root = "state"
routing_order = ["local", "kaggle", "modal", "hetzner", "gha"]
per_job_cost_cap_usd = 5.0

[local]
danger_load_frac = 0.5
session_headroom_frac = 0.15
soft = 0.4
hard = 0.55
local_wall_budget_h = 2.0

[fanout]
enabled = true
speed_cost_weight = 0.5
min_chunks = 8
usd_per_eur = 1.08
local_usd_per_core_hour = 0.006
modal_usd_per_core_hour = 0.10
modal_slots = 16
gha_slots = 20
local_startup_seconds = 5.0
kaggle_startup_seconds = 300.0
modal_startup_seconds = 45.0
hetzner_startup_seconds = 180.0
gha_startup_seconds = 60.0

[hetzner]
enabled = true
location = "nbg1"
allowed_locations = ["nbg1", "hel1", "sin"]
image = "ubuntu-24.04"
max_eur_per_job = 3.0
max_eur_per_day = 3.0
max_server_hours = 6.0
max_concurrent_servers = 2
gpu_server_types = []

[hetzner.server_types.cpx62]
vcpu = 16
ram_gb = 32
arch = "x86"
eur_per_hour = 0.1550

[hetzner.server_types.ccx63]
vcpu = 48
ram_gb = 192
arch = "x86"
eur_per_hour = 1.3678

[kaggle]
enabled = true
weekly_gpu_hours_cap = 18.0
max_runs = 5
concurrency = 5
session_hours = 12.0
kernel_cores = 4
kernel_ram_gb = 32.0

[gha]
enabled = true
included_minutes = 2000
max_usage_fraction = 0.60

[gha.repos.sweep]
repo = "owner/PrivateResearchRepo"
ref = "main"
workflow = "experiment.yml"
runtime = "python"
experiment = "sweep"
runner_os = "linux"
timeout_minutes = 60
"""


def _profile(name, *, slots, chunk_seconds=3600.0, cost_per_chunk=0.0, startup_seconds=0.0,
             max_chunks=10**6, available=True, unit="usd"):
    return fanout.LaneProfile(
        name=name, available=available, slots=slots, chunk_seconds=chunk_seconds,
        cost_per_chunk=cost_per_chunk, startup_seconds=startup_seconds, max_chunks=max_chunks,
        native_cost_unit=unit, native_cost_per_chunk=cost_per_chunk)


def _config(tmp: Path) -> rc_config.BrokerConfig:
    cfg = tmp / "research-compute.toml"
    cfg.write_text(CONFIG_TOML, encoding="utf-8")
    return rc_config.load_config(cfg)


class AllocatorKnobTests(unittest.TestCase):
    """The core knob behaviour on pure lane profiles: a cost-leaning knob picks free/cheap
    lanes and refuses paid ones; a speed-leaning knob recruits paid lanes to cut makespan;
    intermediate weights blend monotonically."""

    # A free lane (Kaggle-like, cost 0) and a paid lane (Hetzner-like), both with ample rail.
    def _lanes(self):
        return [
            _profile("kaggle", slots=8, startup_seconds=300, cost_per_chunk=0.0, unit="free"),
            _profile("hetzner", slots=8, startup_seconds=180, cost_per_chunk=0.10, unit="eur"),
        ]

    def test_cost_leaning_uses_free_only_refuses_paid(self) -> None:
        alloc = fanout.allocate(self._lanes(), 80, speed_cost_weight=0.0)
        self.assertEqual(alloc.counts.get("hetzner", 0), 0)  # paid lane refused
        self.assertEqual(alloc.counts.get("kaggle"), 80)     # free lane absorbs all
        self.assertEqual(alloc.total_cost, 0.0)
        self.assertTrue(alloc.feasible)

    def test_speed_leaning_recruits_paid_to_cut_makespan(self) -> None:
        cheap = fanout.allocate(self._lanes(), 80, speed_cost_weight=0.0)
        fast = fanout.allocate(self._lanes(), 80, speed_cost_weight=1.0)
        self.assertGreater(fast.counts.get("hetzner", 0), 0)          # paid recruited
        self.assertLess(fast.makespan_seconds, cheap.makespan_seconds)  # makespan cut
        self.assertGreater(fast.total_cost, cheap.total_cost)          # at a cost

    def test_intermediate_weights_blend_monotonically(self) -> None:
        lanes = self._lanes()
        allocs = [fanout.allocate(lanes, 80, speed_cost_weight=w) for w in (0.0, 0.25, 0.5, 0.75, 1.0)]
        makespans = [a.makespan_seconds for a in allocs]
        costs = [a.total_cost for a in allocs]
        # More speed weight never increases makespan and never decreases cost.
        for lo, hi in zip(makespans, makespans[1:]):
            self.assertGreaterEqual(lo + 1e-9, hi)
        for lo, hi in zip(costs, costs[1:]):
            self.assertLessEqual(lo - 1e-9, hi)

    def test_allocator_is_deterministic(self) -> None:
        lanes = self._lanes()
        a = fanout.allocate(lanes, 57, speed_cost_weight=0.5)
        b = fanout.allocate(lanes, 57, speed_cost_weight=0.5)
        self.assertEqual(a.counts, b.counts)
        self.assertEqual(a.makespan_seconds, b.makespan_seconds)

    def test_all_chunks_assigned_when_capacity_suffices(self) -> None:
        for w in (0.0, 0.5, 1.0):
            alloc = fanout.allocate(self._lanes(), 123, speed_cost_weight=w)
            self.assertEqual(sum(alloc.counts.values()), 123)
            self.assertEqual(alloc.shortfall, 0)


class FreeLaneFirstTests(unittest.TestCase):
    def test_free_lane_fills_before_paid_lane(self) -> None:
        """Cost-leaning fill order: with a free lane (Kaggle) and paid lanes (local costs;
        Hetzner/Modal cost), a cost-leaning split puts everything on the free lane and leaves
        every costed lane -- including the cheap-but-nonzero local -- at zero."""
        lanes = [
            _profile("kaggle", slots=4, cost_per_chunk=0.0, unit="free"),
            _profile("local", slots=4, cost_per_chunk=0.006, unit="usd"),
            _profile("hetzner", slots=8, cost_per_chunk=0.02, unit="eur"),
            _profile("modal", slots=16, cost_per_chunk=0.10, unit="usd"),
        ]
        alloc = fanout.allocate(lanes, 40, speed_cost_weight=0.0)
        self.assertEqual(alloc.counts, {"kaggle": 40})
        self.assertEqual(alloc.total_cost, 0.0)

    def test_routing_order_breaks_ties_among_equal_lanes(self) -> None:
        """Two lanes identical in every numeric field but different names: a single chunk goes
        to the earlier routing-order lane (kaggle before gha)."""
        lanes = [
            _profile("gha", slots=4, cost_per_chunk=0.0, unit="minutes"),
            _profile("kaggle", slots=4, cost_per_chunk=0.0, unit="free"),
        ]
        alloc = fanout.allocate(lanes, 1, speed_cost_weight=0.0)
        self.assertEqual(alloc.counts, {"kaggle": 1})

    def test_free_capacity_short_spills_to_cheapest_paid(self) -> None:
        """When the free lane's rail cannot cover M, a cost-leaning split spills the remainder
        to the CHEAPEST paid lane, not an arbitrary one."""
        lanes = [
            _profile("kaggle", slots=4, cost_per_chunk=0.0, max_chunks=30, unit="free"),
            _profile("local", slots=4, cost_per_chunk=0.006, unit="usd"),
            _profile("modal", slots=16, cost_per_chunk=0.10, unit="usd"),
        ]
        alloc = fanout.allocate(lanes, 50, speed_cost_weight=0.0)
        self.assertEqual(alloc.counts.get("kaggle"), 30)   # free lane maxed first
        self.assertEqual(alloc.counts.get("local"), 20)    # cheapest paid takes the rest
        self.assertEqual(alloc.counts.get("modal", 0), 0)  # dearer paid untouched


class HardRailTests(unittest.TestCase):
    """Every hard rail is respected even under a speed-leaning (weight -> 1) knob: the knob may
    lean on more allowed capacity but can never assign a lane beyond its max_chunks ceiling."""

    def test_speed_knob_never_breaches_any_max_chunks(self) -> None:
        lanes = [
            _profile("kaggle", slots=4, cost_per_chunk=0.0, max_chunks=10, unit="free"),
            _profile("local", slots=2, cost_per_chunk=0.006, max_chunks=4, unit="usd"),
            _profile("hetzner", slots=16, cost_per_chunk=0.02, max_chunks=20, unit="eur"),
            _profile("modal", slots=16, cost_per_chunk=0.10, max_chunks=15, unit="usd"),
            _profile("gha", slots=20, cost_per_chunk=0.0, max_chunks=7, unit="minutes"),
        ]
        alloc = fanout.allocate(lanes, 1000, speed_cost_weight=1.0)  # demand >> capacity
        for lane in lanes:
            self.assertLessEqual(alloc.counts.get(lane.name, 0), lane.max_chunks)
        # Capacity is 10+4+20+15+7 = 56; the rest is an honest, flagged shortfall.
        self.assertEqual(sum(alloc.counts.values()), 56)
        self.assertFalse(alloc.feasible)
        self.assertEqual(alloc.shortfall, 1000 - 56)

    def test_zero_rail_or_unavailable_lane_never_used(self) -> None:
        lanes = [
            _profile("kaggle", slots=4, cost_per_chunk=0.0, max_chunks=100, unit="free"),
            _profile("hetzner", slots=16, cost_per_chunk=0.02, max_chunks=0, unit="eur"),  # rail 0
            _profile("modal", slots=16, cost_per_chunk=0.10, available=False, unit="usd"),  # down
        ]
        alloc = fanout.allocate(lanes, 40, speed_cost_weight=1.0)
        self.assertEqual(alloc.counts.get("hetzner", 0), 0)
        self.assertEqual(alloc.counts.get("modal", 0), 0)
        self.assertEqual(alloc.counts.get("kaggle"), 40)


class RailAdapterTests(unittest.TestCase):
    """build_lane_profiles turns each lane's (injection-first, offline) probe verdict + config
    into a LaneProfile whose max_chunks encodes the hard rail. Tokens are set so the enabled
    lanes' presence checks pass; every liveness verdict is injected, so no network runs."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = _config(Path(self._tmp.name))
        self._prev = {k: os.environ.get(k) for k in ("HCLOUD_TOKEN", "KAGGLE_API_TOKEN")}
        os.environ["HCLOUD_TOKEN"] = "dummy-offline-token"
        os.environ["KAGGLE_API_TOKEN"] = "dummy-offline-token"

    def tearDown(self) -> None:
        for key, value in self._prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    # A CPU job: 100 chunks, 1 core-hour/chunk (core_hours 100 over 100 chunks -> ccs 3600).
    def _cpu_job(self, **constraints):
        base = {"cpu": 16, "memory_mb": 8192, "parallelism": 16, "core_hours": 100, "chunks": 100}
        base.update(constraints)
        return {"task_family": "enumeration", "constraints": base}

    def _resources(self, **liveness):
        live = {"modal": {"ready": True, "usable": True},
                "hetzner": {"usable": True},
                "kaggle": {"usable": True}}
        live.update(liveness)
        return {"cpu": {"logical_cores": 8}, "memory": {"total_gb": 32, "available_gb": 24},
                "disk": {"available_gb": 100}, "gpu": {"total_gpus": 0},
                "load": {"load_1m": 0.2}, "liveness": live}

    def _lane(self, profiles, name):
        return next((p for p in profiles if p.name == name), None)

    def test_chunk_core_seconds_from_total_and_count(self) -> None:
        self.assertEqual(fanout.chunk_core_seconds(self._cpu_job(), 100), 3600.0)

    def test_local_load_cap_and_cost_included(self) -> None:
        """Local slots = w_safe (the self-preservation load-cap) and max_chunks is bounded by
        the wall budget; local is NOT free -- its per-core-hour cost enters the objective."""
        profiles = fanout.build_lane_profiles(
            self._cpu_job(), config=self.config, resources=self._resources(),
            modal_ready=True, total_chunks=100)
        local = self._lane(profiles, "local")
        # w_safe = floor(0.5*8 - 0.2 - 0.15*8) = 2; max = floor(2 * 2.0h * 3600 / 3600) = 4.
        self.assertEqual(local.slots, 2)
        self.assertEqual(local.max_chunks, 4)
        self.assertAlmostEqual(local.cost_per_chunk, 0.006, places=6)  # local costs money
        self.assertFalse(local.is_free)

    def test_kaggle_cpu_is_free_and_quota_free(self) -> None:
        profiles = fanout.build_lane_profiles(
            self._cpu_job(), config=self.config, resources=self._resources(),
            modal_ready=True, total_chunks=100)
        kaggle = self._lane(profiles, "kaggle")
        self.assertTrue(kaggle.available)
        self.assertEqual(kaggle.cost_per_chunk, 0.0)      # free lane
        self.assertEqual(kaggle.max_chunks, 100)          # no CPU quota rail
        self.assertEqual(kaggle.slots, 5 * 4)             # concurrency * kernel cores

    def test_hetzner_eur_per_day_envelope_bounds_max_chunks(self) -> None:
        """The <= EUR3/day auto-approve envelope becomes a max_chunks ceiling: a 48-core
        ccx63 (parallelism 48) is sized so its share's EUR stays within EUR3."""
        job = self._cpu_job(parallelism=48)
        profiles = fanout.build_lane_profiles(
            job, config=self.config, resources=self._resources(),
            modal_ready=True, total_chunks=100)
        hetzner = self._lane(profiles, "hetzner")
        # ccx63: eur/core-hour = 1.3678/48; eur/chunk = that * 1h; floor(3.0 / eur_per_chunk).
        eur_per_chunk = (1.3678 / 48)
        self.assertEqual(hetzner.max_chunks, int(3.0 // eur_per_chunk))
        self.assertLessEqual(hetzner.max_chunks * eur_per_chunk, 3.0 + 1e-9)

    def test_hetzner_outstanding_eur_reduces_headroom(self) -> None:
        """Already-reserved EUR (injected) shrinks the daily headroom, so the rail ceiling drops."""
        job = self._cpu_job(parallelism=48)
        res = self._resources()
        res["outstanding"] = {"hetzner": 2.0}  # only EUR1 of the EUR3/day cap remains
        profiles = fanout.build_lane_profiles(
            job, config=self.config, resources=res, modal_ready=True, total_chunks=100)
        hetzner = self._lane(profiles, "hetzner")
        eur_per_chunk = (1.3678 / 48)
        self.assertEqual(hetzner.max_chunks, int(1.0 // eur_per_chunk))

    def test_modal_per_job_usd_cap_bounds_max_chunks(self) -> None:
        profiles = fanout.build_lane_profiles(
            self._cpu_job(), config=self.config, resources=self._resources(),
            modal_ready=True, total_chunks=100)
        modal = self._lane(profiles, "modal")
        self.assertTrue(modal.available)
        # per_job_cost_cap_usd 5.0 / (1h * 0.10 usd/core-h) = 50 chunks (50 * 0.10 = 5.00 <= cap).
        self.assertEqual(modal.max_chunks, 50)
        self.assertLessEqual(modal.max_chunks * modal.cost_per_chunk, 5.0 + 1e-9)

    def test_gha_60pct_cap_bounds_max_chunks(self) -> None:
        """The GitHub Actions cumulative 60% minutes cap becomes a max_chunks ceiling."""
        job = self._cpu_job()
        job["gha_target"] = "sweep"
        res = self._resources(gha={"used_this_cycle": 600, "included_minutes": 2000})
        profiles = fanout.build_lane_profiles(
            job, config=self.config, resources=res, modal_ready=True, total_chunks=100)
        gha = self._lane(profiles, "gha")
        self.assertTrue(gha.available)
        # cap = 0.60*2000 = 1200; remaining = 1200-600 = 600; minutes/chunk = 1h*60 = 60 -> 10.
        self.assertEqual(gha.max_chunks, 10)
        self.assertEqual(gha.cost_per_chunk, 0.0)  # prepaid minutes: marginal objective cost 0

    def test_gha_over_cap_is_unavailable(self) -> None:
        job = self._cpu_job()
        job["gha_target"] = "sweep"
        res = self._resources(gha={"used_this_cycle": 1180, "included_minutes": 2000})
        profiles = fanout.build_lane_profiles(
            job, config=self.config, resources=res, modal_ready=True, total_chunks=100)
        gha = self._lane(profiles, "gha")
        self.assertFalse(gha.available)  # 1180 + 60 worst-case > 1200 cap

    def test_gpu_job_uses_kaggle_quota_and_skips_hetzner(self) -> None:
        """A GPU fan-out: Kaggle's weekly GPU-hour quota bounds its share, and Hetzner (no
        on-demand GPU) is not offered at all."""
        job = self._cpu_job(parallelism=4)
        job["policy"] = {"gpu": True}
        res = self._resources(kaggle={"usable": True, "gpu_hours_used_this_week": 17.0})
        res["gpu"] = {"total_gpus": 0}
        profiles = fanout.build_lane_profiles(
            job, config=self.config, resources=res, modal_ready=True, total_chunks=100)
        self.assertIsNone(self._lane(profiles, "hetzner"))  # no GPU on Hetzner -> skipped
        kaggle = self._lane(profiles, "kaggle")
        self.assertTrue(kaggle.available)
        # cap 18h - used 17h = 1h remaining; 1 core-hour/chunk -> 1 chunk.
        self.assertEqual(kaggle.max_chunks, 1)

    def test_speed_knob_over_rails_from_probes_stays_within_caps(self) -> None:
        """End-to-end rail safety: at weight 1 over probe-derived profiles, every lane's
        assigned share stays within its computed max_chunks."""
        job = self._cpu_job()
        job["gha_target"] = "sweep"
        res = self._resources(gha={"used_this_cycle": 600, "included_minutes": 2000})
        profiles = fanout.build_lane_profiles(
            job, config=self.config, resources=res, modal_ready=True, total_chunks=100)
        alloc = fanout.allocate(profiles, 100, speed_cost_weight=1.0)
        by_name = {p.name: p for p in profiles}
        for name, count in alloc.counts.items():
            self.assertLessEqual(count, by_name[name].max_chunks)


class ReassignTests(unittest.TestCase):
    """Partial-failure reassignment: a failed/stalled lane's UNFINISHED chunks are reassigned
    to healthy lanes with no duplication or loss (chunks are resumable)."""

    def _lanes(self):
        return [
            _profile("kaggle", slots=8, cost_per_chunk=0.0, max_chunks=1000, unit="free"),
            _profile("hetzner", slots=8, cost_per_chunk=0.02, max_chunks=1000, unit="eur"),
            _profile("modal", slots=16, cost_per_chunk=0.10, max_chunks=1000, unit="usd"),
        ]

    def test_failed_lane_chunks_reassigned_fully(self) -> None:
        lanes = self._lanes()
        alloc = fanout.allocate(lanes, 100, speed_cost_weight=1.0)
        assignment = fanout.chunk_ranges(alloc.counts)
        self.assertGreater(len(assignment.get("hetzner", [])), 0)
        result = fanout.reassign(
            assignment=assignment, completed_chunks=set(), failed_lanes={"hetzner"},
            lanes=lanes, speed_cost_weight=1.0)
        self.assertTrue(result["all_covered"])
        self.assertEqual(result["uncovered"], [])
        self.assertEqual(sorted(result["covered"]), sorted(assignment["hetzner"]))
        self.assertNotIn("hetzner", result["reassigned"])  # never back onto the failed lane

    def test_only_unfinished_chunks_move_no_rework(self) -> None:
        lanes = self._lanes()
        assignment = {"kaggle": [0, 1, 2, 3], "hetzner": [4, 5, 6, 7]}
        completed = {4, 5}  # hetzner finished 4,5 before stalling; 6,7 are unfinished
        result = fanout.reassign(
            assignment=assignment, completed_chunks=completed, failed_lanes={"hetzner"},
            lanes=lanes, speed_cost_weight=0.5)
        self.assertEqual(result["lost_chunks"], [6, 7])   # only the unfinished ones
        self.assertEqual(sorted(result["covered"]), [6, 7])
        self.assertTrue(result["all_covered"])

    def test_no_duplication_across_all_chunks(self) -> None:
        lanes = self._lanes()
        alloc = fanout.allocate(lanes, 60, speed_cost_weight=1.0)
        assignment = fanout.chunk_ranges(alloc.counts)
        result = fanout.reassign(
            assignment=assignment, completed_chunks=set(), failed_lanes={"modal"},
            lanes=lanes, speed_cost_weight=1.0)
        # Surviving chunks + reassigned chunks cover 0..59 exactly once.
        survivors = [c for name, ids in assignment.items() if name != "modal" for c in ids]
        moved = [c for ids in result["reassigned"].values() for c in ids]
        covered = sorted(survivors + moved)
        self.assertEqual(covered, list(range(60)))
        self.assertEqual(len(covered), len(set(covered)))  # no duplicate

    def test_reassign_reports_uncovered_when_headroom_exhausted(self) -> None:
        lanes = [
            _profile("kaggle", slots=8, cost_per_chunk=0.0, max_chunks=10, unit="free"),
            _profile("hetzner", slots=8, cost_per_chunk=0.02, max_chunks=10, unit="eur"),
        ]
        assignment = {"kaggle": list(range(10)), "hetzner": list(range(10, 20))}
        result = fanout.reassign(
            assignment=assignment, completed_chunks=set(), failed_lanes={"hetzner"},
            lanes=lanes, speed_cost_weight=0.5)
        # Kaggle is already at its rail (10/10) -> the 10 lost chunks cannot be absorbed.
        self.assertFalse(result["all_covered"])
        self.assertEqual(result["uncovered"], list(range(10, 20)))


class MergeTests(unittest.TestCase):
    """Aggregation with the bundle's vacuity guard preserved."""

    def test_merge_dedups_and_reports_coverage(self) -> None:
        outputs = {
            "kaggle": [{"chunk": 0, "result": [1]}, {"chunk": 1, "result": [2]}],
            "hetzner": [{"chunk": 1, "result": [2]}, {"chunk": 2, "result": [3]}],  # chunk 1 dup
        }
        merged = fanout.merge_partials(outputs, expected_chunks=4)
        self.assertEqual(merged["chunk_ids"], [0, 1, 2])
        self.assertEqual(merged["duplicates"], [1])
        self.assertEqual(merged["missing"], [3])
        self.assertFalse(merged["complete"])

    def test_merge_complete_when_all_present(self) -> None:
        outputs = {"kaggle": [{"chunk": i, "result": [i]} for i in range(3)]}
        merged = fanout.merge_partials(outputs, expected_chunks=3)
        self.assertTrue(merged["complete"])
        self.assertEqual(merged["missing"], [])

    def test_vacuity_guard_rejects_all_empty(self) -> None:
        outputs = {"kaggle": [{"chunk": 0, "result": None}, {"chunk": 1, "status": "empty"}]}
        with self.assertRaises(fanout.MergeVacuityError):
            fanout.merge_partials(outputs)

    def test_vacuity_guard_passes_with_one_non_vacuous(self) -> None:
        outputs = {"kaggle": [{"chunk": 0, "result": None}, {"chunk": 1, "result": [42]}]}
        merged = fanout.merge_partials(outputs)
        self.assertFalse(merged["vacuous"])
        self.assertEqual(merged["non_vacuous_chunk_ids"], [1])


class FanoutGateTests(unittest.TestCase):
    """should_fanout: opt-in, and only for LARGE divisible jobs; small jobs stay single-lane."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = _config(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_large_divisible_job_uses_fanout(self) -> None:
        use, reason = fanout.should_fanout(
            {"constraints": {"chunks": 64}}, config=self.config)
        self.assertTrue(use)
        self.assertIn("64", reason)

    def test_small_job_falls_back_to_single_lane(self) -> None:
        use, reason = fanout.should_fanout(
            {"constraints": {"chunks": 3}}, config=self.config)
        self.assertFalse(use)
        self.assertIn("single-lane", reason)

    def test_non_divisible_job_not_fanned_out(self) -> None:
        use, _ = fanout.should_fanout({"constraints": {"cpu": 8}}, config=self.config)
        self.assertFalse(use)

    def test_disabled_flag_disables_fanout(self) -> None:
        # Rebuild a config with fanout off.
        text = CONFIG_TOML.replace("[fanout]\nenabled = true", "[fanout]\nenabled = false")
        path = Path(self._tmp.name) / "off.toml"
        path.write_text(text, encoding="utf-8")
        off = rc_config.load_config(path)
        use, reason = fanout.should_fanout({"constraints": {"chunks": 64}}, config=off)
        self.assertFalse(use)
        self.assertEqual(reason, "fanout_disabled")


class ConfigTests(unittest.TestCase):
    def test_config_parses_fanout_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _config(Path(tmp))
            self.assertTrue(cfg.fanout_enabled)
            self.assertEqual(cfg.fanout_speed_cost_weight, 0.5)
            self.assertEqual(cfg.fanout_min_chunks, 8)
            self.assertEqual(cfg.fanout_usd_per_eur, 1.08)
            self.assertEqual(cfg.fanout_local_usd_per_core_hour, 0.006)
            self.assertEqual(cfg.fanout_modal_usd_per_core_hour, 0.10)
            self.assertEqual(cfg.fanout_modal_slots, 16)
            self.assertEqual(cfg.fanout_gha_slots, 20)

    def test_config_defaults_fanout_disabled(self) -> None:
        fields = rc_config.BrokerConfig.__dataclass_fields__
        self.assertFalse(fields["fanout_enabled"].default)
        self.assertEqual(fields["fanout_speed_cost_weight"].default, 0.5)

    def test_example_config_parses_fanout(self) -> None:
        example = WORKSPACE / "config" / "research-compute.example.toml"
        cfg = rc_config.load_config(example)
        self.assertFalse(cfg.fanout_enabled)  # off by default in the shipped example
        self.assertEqual(cfg.fanout_speed_cost_weight, 0.5)
        self.assertEqual(cfg.fanout_min_chunks, 8)


def _fanout_plan(ws: Path, job: dict) -> dict:
    (ws / "job.json").write_text(json.dumps(job), encoding="utf-8")
    env = dict(os.environ)
    env["OPENCLAW_WORKSPACE"] = str(ws)
    env["PYTHONPATH"] = os.pathsep.join([str(WORKSPACE), env.get("PYTHONPATH", "")])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["HCLOUD_TOKEN"] = "dummy-token-for-offline-test"
    env["KAGGLE_API_TOKEN"] = "dummy-token-for-offline-test"
    proc = subprocess.run(
        [sys.executable, "-m", "research_compute", "fanout-plan", str(ws / "job.json")],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


class CliFanoutPlanTests(unittest.TestCase):
    """The CLI `fanout-plan` entry point is distinct from `plan` and returns the allocation."""

    def _make_ws(self, tmp: Path) -> Path:
        ws = tmp / "ws"
        (ws / "config").mkdir(parents=True)
        (ws / "config" / "research-compute.toml").write_text(CONFIG_TOML, encoding="utf-8")
        resources = {"cpu": {"logical_cores": 8}, "memory": {"total_gb": 32, "available_gb": 24},
                     "disk": {"available_gb": 100}, "gpu": {"total_gpus": 0},
                     "load": {"load_1m": 0.2},
                     "liveness": {"modal": {"ready": True, "usable": True},
                                  "hetzner": {"usable": True}, "kaggle": {"usable": True},
                                  "gha": {"used_this_cycle": 300, "included_minutes": 2000}}}
        (ws / ".codex_resources.json").write_text(json.dumps(resources), encoding="utf-8")
        return ws

    def test_cli_fanout_plan_large_job(self) -> None:
        job = {"task_family": "enumeration", "gha_target": "sweep",
               "policy": {"speed_cost_weight": 1.0},
               "constraints": {"cpu": 16, "memory_mb": 8192, "parallelism": 16,
                               "core_hours": 100, "chunks": 100}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._make_ws(Path(tmp))
            out = _fanout_plan(ws, job)
            fan = out["fanout"]
            self.assertTrue(fan["use_fanout"])
            self.assertTrue(fan["accepted"])
            self.assertEqual(fan["chunks"], 100)
            self.assertEqual(fan["speed_cost_weight"], 1.0)
            # A speed-leaning split recruits more than one lane and covers all 100 chunks.
            self.assertGreaterEqual(len(fan["allocation"]["counts"]), 2)
            self.assertEqual(sum(fan["allocation"]["counts"].values()), 100)
            covered = sorted(c for ids in fan["chunk_assignment"].values() for c in ids)
            self.assertEqual(covered, list(range(100)))

    def test_cli_fanout_plan_small_job_defers_to_single_lane(self) -> None:
        job = {"task_family": "enumeration",
               "constraints": {"cpu": 4, "core_hours": 2, "chunks": 3}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._make_ws(Path(tmp))
            out = _fanout_plan(ws, job)
            fan = out["fanout"]
            self.assertFalse(fan["use_fanout"])
            self.assertEqual(fan["fallback"], "single_lane_router")

    def test_cli_cost_vs_speed_knob_changes_split(self) -> None:
        base = {"task_family": "enumeration", "gha_target": "sweep",
                "constraints": {"cpu": 16, "memory_mb": 8192, "parallelism": 16,
                                "core_hours": 100, "chunks": 100}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._make_ws(Path(tmp))
            cheap = _fanout_plan(ws, {**base, "policy": {"speed_cost_weight": 0.0}})["fanout"]
            fast = _fanout_plan(ws, {**base, "policy": {"speed_cost_weight": 1.0}})["fanout"]
            self.assertLessEqual(cheap["allocation"]["total_cost_usd"], fast["allocation"]["total_cost_usd"])
            self.assertLessEqual(fast["allocation"]["makespan_seconds"], cheap["allocation"]["makespan_seconds"])


if __name__ == "__main__":
    unittest.main()
