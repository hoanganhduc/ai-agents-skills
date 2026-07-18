"""Offline, credential-free tests for the Hetzner routing lane, the local self-preservation
veto (Phase A), and the router-wide GPU policy (plan §5.1). No network call and no
provisioning: the planner cascade, the EUR budget gate, the local veto, the watchdog, and the
GPU-lane cascade are all exercised with injected load / liveness / resource snapshots. Mirrors
the subprocess / temp-workspace pattern of test_research_compute_bootstrap.
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
from research_compute import budget_ledger, hetzner_backend, planner  # noqa: E402
from research_compute import config as rc_config  # noqa: E402
from research_compute import github_actions_backend as gha  # noqa: E402

# The hcloud lifecycle driver ships under the skill runtime dir; add it to the path so the
# offline driver tests can import it directly (mirrors how the wrapper wires sys.path).
SKILL_DIR = RUNTIME_SOURCE_ROOT / "skills" / "hetzner-research-compute"
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))
import hetzner_driver  # noqa: E402
import hetzner_audit  # noqa: E402
import hetzner_reaper  # noqa: E402


CONFIG_TOML = """\
install_id = "test-install"
platform = "linux"
broker_state_root = "state"
routing_order = ["local", "modal", "hetzner", "gha"]

[local]
danger_load_frac = 0.5
session_headroom_frac = 0.15
soft = 0.4
hard = 0.55
local_wall_budget_h = 2.0

[hetzner]
enabled = true
location = "nbg1"
allowed_locations = ["nbg1", "hel1", "sin"]
image = "ubuntu-24.04"
monthly_eur_cap = 0.0
max_eur_per_job = 3.0
max_eur_per_day = 3.0
max_server_hours = 6.0
max_concurrent_servers = 2
gpu_server_types = []

[hetzner.server_types.cpx22]
vcpu = 2
ram_gb = 4
arch = "x86"
eur_per_hour = 0.0180

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
"""

# A GitHub Actions lane on top of the base config: a private repo whose worst case is 200
# Linux-equivalent minutes (10% of the 2000 included) so the cumulative-cap arithmetic is
# easy to read (cap = 60% * 2000 = 1200).
GHA_CONFIG_TOML = CONFIG_TOML + """
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
timeout_minutes = 200
"""

CPX22 = {"name": "cpx22", "vcpu": 2, "ram_gb": 4, "arch": "x86", "eur_per_hour": 0.0180}
CPX62 = {"name": "cpx62", "vcpu": 16, "ram_gb": 32, "arch": "x86", "eur_per_hour": 0.1550}
CCX63 = {"name": "ccx63", "vcpu": 48, "ram_gb": 192, "arch": "x86", "eur_per_hour": 1.3678}

# GPU-routing config variants (plan §5.1). The reorder puts the GPU-incapable lanes
# (Hetzner has no GPU; GitHub Actions GPU is off) BEFORE Modal, so a GPU walk must skip both
# to reach Modal -- proving they are GPU-inadequate. The gpu-on variant opts GitHub Actions
# into paid larger-runner GPU.
GPU_REORDER_CONFIG_TOML = GHA_CONFIG_TOML.replace(
    'routing_order = ["local", "modal", "hetzner", "gha"]',
    'routing_order = ["local", "hetzner", "gha", "modal"]')
GHA_GPU_CONFIG_TOML = GHA_CONFIG_TOML.replace(
    "max_usage_fraction = 0.60\n", "max_usage_fraction = 0.60\ngpu_enabled = true\n")


def _with_liveness_defaults(resources: dict | None) -> dict:
    """Every offline `plan` test carries a liveness snapshot so the planner is fully
    deterministic and NEVER touches the network across the subprocess boundary: the Hetzner
    probe reads resources['liveness']['hetzner'] (default usable) instead of calling the
    real hcloud API, and Modal / GHA liveness is injected per scenario. Any lane a test
    passes in overrides the default."""
    snap = dict(resources or {})
    liveness = dict(snap.get("liveness", {}))
    liveness.setdefault("hetzner", {"usable": True, "reason": "injected-usable"})
    snap["liveness"] = liveness
    return snap


def _make_workspace(tmp: Path, resources: dict | None = None, *, config_toml: str = CONFIG_TOML) -> Path:
    ws = tmp / "ws"
    (ws / "config").mkdir(parents=True)
    (ws / "config" / "research-compute.toml").write_text(config_toml, encoding="utf-8")
    (ws / ".codex_resources.json").write_text(
        json.dumps(_with_liveness_defaults(resources)), encoding="utf-8")
    return ws


def _plan(ws: Path, job: dict, *, token: bool = True) -> dict:
    (ws / "job.json").write_text(json.dumps(job), encoding="utf-8")
    env = dict(os.environ)
    env["OPENCLAW_WORKSPACE"] = str(ws)
    env["PYTHONPATH"] = os.pathsep.join([str(WORKSPACE), env.get("PYTHONPATH", "")])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("HCLOUD_TOKEN", None)
    if token:
        env["HCLOUD_TOKEN"] = "dummy-token-for-offline-test"
    proc = subprocess.run(
        [sys.executable, "-m", "research_compute", "plan", str(ws / "job.json")],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _config(tmp: Path) -> rc_config.BrokerConfig:
    cfg = tmp / "research-compute.toml"
    cfg.write_text(CONFIG_TOML, encoding="utf-8")
    return rc_config.load_config(cfg)


def _gha_config(tmp: Path) -> rc_config.BrokerConfig:
    tmp.mkdir(parents=True, exist_ok=True)
    cfg = tmp / "research-compute.toml"
    cfg.write_text(GHA_CONFIG_TOML, encoding="utf-8")
    return rc_config.load_config(cfg)


def _config_text(tmp: Path, text: str) -> rc_config.BrokerConfig:
    tmp.mkdir(parents=True, exist_ok=True)
    cfg = tmp / "research-compute.toml"
    cfg.write_text(text, encoding="utf-8")
    return rc_config.load_config(cfg)


class HetznerRoutingTests(unittest.TestCase):
    def test_a_imports_clean(self) -> None:
        for mod in (planner, rc_config, hetzner_backend, budget_ledger):
            self.assertIsNotNone(mod)

    HEAVY_CPU_JOB = {"task_family": "enumeration",
                     "constraints": {"cpu": 12, "memory_mb": 8192, "parallelism": 12,
                                     "core_hours": 40, "resource_class": "cpu"}}

    def test_b_cpu_job_prefers_modal_when_available(self) -> None:
        """New routing order local>modal>hetzner>gha: a CPU-heavy job that exceeds local
        goes to Modal FIRST when Modal is account-usable, even though Hetzner is also
        available (Modal is now the preferred first offload tier)."""
        resources = {"liveness": {"modal": {"ready": True, "usable": True},
                                  "hetzner": {"usable": True}}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            out = _plan(ws, self.HEAVY_CPU_JOB, token=True)
            self.assertTrue(out["plan"]["decision"].startswith("modal_"))

    def test_b_cpu_job_routes_to_hetzner_when_modal_unavailable(self) -> None:
        """The Modal-out-of-credits fallthrough fix: with Modal unavailable, a CPU-heavy
        job falls through order-driven to HETZNER (not straight to GHA), with a EUR cost
        estimate and no provisioning. The routing trail proves Hetzner was reached and GHA
        was never consulted."""
        resources = {"liveness": {"modal": {"ready": True, "usable": False},
                                  "hetzner": {"usable": True}}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            out = _plan(ws, self.HEAVY_CPU_JOB, token=True)
            plan = out["plan"]
            self.assertEqual(plan["decision"], "hetzner")
            self.assertEqual(plan["server_type"], "cpx62")
            self.assertEqual(plan["budget_unit"], "eur")
            self.assertGreater(plan["estimated_cost_eur"], 0.0)
            self.assertLess(plan["estimated_cost_eur"], 3.0)
            trail = plan["routing_trail"]
            self.assertEqual(trail[0]["backend"], "modal")
            self.assertFalse(trail[0]["available"])
            self.assertEqual(trail[-1]["backend"], "hetzner")
            self.assertNotIn("gha", [t["backend"] for t in trail])
            # No provisioning artifacts: only planning state exists.
            self.assertFalse((ws / "state" / "hetzner-reservations.jsonl").exists())

    def test_b_falls_to_modal_without_token(self) -> None:
        """With no HCLOUD_TOKEN Hetzner is unavailable; a Modal-usable host keeps the CPU
        job on Modal (the first offload tier)."""
        resources = {"liveness": {"modal": {"ready": True, "usable": True}}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            out = _plan(ws, self.HEAVY_CPU_JOB, token=False)
            self.assertTrue(out["plan"]["decision"].startswith("modal_"))

    def test_b_hetzner_liveness_unusable_falls_through_to_gha(self) -> None:
        """A per-vendor liveness probe reporting the lane unusable makes it unavailable:
        with Modal out of credits AND Hetzner's account-usable probe failing (http_401),
        a CPU job falls through both, order-driven, to GitHub Actions (under its cap)."""
        resources = {"liveness": {"modal": {"ready": True, "usable": False},
                                  "hetzner": {"usable": False, "reason": "http_401"},
                                  "gha": {"used_this_cycle": 100, "included_minutes": 2000}}}
        job = {"task_family": "enumeration", "gha_target": "sweep",
               "constraints": {"cpu": 12, "memory_mb": 8192, "parallelism": 12,
                               "core_hours": 40, "resource_class": "cpu", "matrix_cells": 1}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources, config_toml=GHA_CONFIG_TOML)
            out = _plan(ws, job, token=True)
            plan = out["plan"]
            self.assertEqual(plan["decision"], "gha")
            trail = {t["backend"]: t for t in plan["routing_trail"]}
            self.assertFalse(trail["modal"]["available"])
            self.assertFalse(trail["hetzner"]["available"])
            self.assertTrue(trail["gha"]["available"])

    def test_b_gha_cap_under_60pct_allows_routing(self) -> None:
        """GHA lane available while cumulative usage + job worst-case <= 60% of included:
        total at 30% (600) + a 10% job (200) = 800 <= 1200 -> routes to GHA."""
        resources = {"liveness": {"modal": {"usable": False}, "hetzner": {"usable": False},
                                  "gha": {"used_this_cycle": 600, "included_minutes": 2000}}}
        job = {"task_family": "enumeration", "gha_target": "sweep",
               "constraints": {"cpu": 12, "memory_mb": 8192, "parallelism": 12,
                               "core_hours": 40, "resource_class": "cpu", "matrix_cells": 1}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources, config_toml=GHA_CONFIG_TOML)
            out = _plan(ws, job, token=False)
            self.assertEqual(out["plan"]["decision"], "gha")

    def test_b_gha_cap_over_60pct_refuses_and_falls_through(self) -> None:
        """Cumulative TOTAL cap, NOT per-task: total already at 55% (1100) + a 10% job
        (200) = 1300 > 1200 makes GHA unavailable, so the lane falls through. With no other
        lane usable the heavy job keeps the base Modal decision -- GHA was refused."""
        resources = {"liveness": {"modal": {"usable": False}, "hetzner": {"usable": False},
                                  "gha": {"used_this_cycle": 1100, "included_minutes": 2000}}}
        job = {"task_family": "enumeration", "gha_target": "sweep",
               "constraints": {"cpu": 12, "memory_mb": 8192, "parallelism": 12,
                               "core_hours": 40, "resource_class": "cpu", "matrix_cells": 1}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources, config_toml=GHA_CONFIG_TOML)
            out = _plan(ws, job, token=False)
            plan = out["plan"]
            self.assertNotEqual(plan["decision"], "gha")
            gha_entry = next(t for t in plan["routing_trail"] if t["backend"] == "gha")
            self.assertFalse(gha_entry["available"])
            self.assertIn("cap", gha_entry["reason"])

    def test_c_self_preservation_veto_falls_through_to_hetzner(self) -> None:
        """A job small enough to classify local, whose full-run load would breach the
        ceiling (w_needed > w_safe), is vetoed and re-routed order-driven. With Modal
        unavailable it lands on Hetzner; the routing trail records the local veto first and
        the chosen Hetzner lane last."""
        resources = {"cpu": {"logical_cores": 8, "physical_cores": 8},
                     "memory": {"total_gb": 16, "available_gb": 12},
                     "disk": {"available_gb": 100},
                     "gpu": {"total_gpus": 0, "available_backends": []},
                     "load": {"load_1m": 1.0},
                     "liveness": {"modal": {"usable": False}, "hetzner": {"usable": True}}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            job = {"task_family": "generic",
                   "constraints": {"cpu": 1, "memory_mb": 1024, "parallelism": 1,
                                   "core_hours": 10}}
            out = _plan(ws, job, token=True)
            plan = out["plan"]
            self.assertEqual(plan["decision"], "hetzner")
            self.assertIn("local_self_preservation_veto", plan["risk_flags"])
            trail = plan["routing_trail"]
            self.assertEqual(trail[0]["backend"], "local")
            self.assertFalse(trail[0]["adequate"])
            self.assertIn("self_preservation_veto", trail[0]["reason"])
            self.assertEqual(trail[0]["w_safe"], 1)
            self.assertEqual(trail[0]["w_needed"], 5)
            self.assertEqual(trail[-1]["backend"], "hetzner")

    def test_c_unfallable_secret_never_offloads(self) -> None:
        """Secret-locality data that cannot run local safely (w_safe < 1) is surfaced
        (rejected), never gambled locally AND never offloaded -- even though a Hetzner
        token is present here, secret data must not leave the host."""
        resources = {"cpu": {"logical_cores": 8}, "load": {"load_1m": 2.0}}  # w_safe = 0
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            job = {"task_family": "generic",
                   "constraints": {"cpu": 1, "memory_mb": 1024, "parallelism": 1,
                                   "core_hours": 10, "data_locality": "secret"}}
            out = _plan(ws, job, token=True)  # token present, yet secret must not offload
            plan = out["plan"]
            self.assertEqual(plan["decision"], "rejected")
            self.assertFalse(plan["accepted"])
            self.assertIn("unfallable_secret_local_unsafe", plan["risk_flags"])

    def test_c_secret_over_budget_runs_throttled_local(self) -> None:
        """Secret data that is load-safe but over the wall budget runs throttled-local
        (safe, not a gamble) rather than offloading."""
        resources = {"cpu": {"logical_cores": 8}, "load": {"load_1m": 1.0}}  # w_safe = 1
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            job = {"task_family": "generic",
                   "constraints": {"cpu": 1, "memory_mb": 1024, "parallelism": 1,
                                   "core_hours": 10, "data_locality": "secret"}}
            out = _plan(ws, job, token=True)
            plan = out["plan"]
            self.assertEqual(plan["decision"], "local_cpu")
            self.assertIn("local_over_wall_budget", plan["risk_flags"])
            self.assertEqual(plan["local_workers"], 1)

    def test_c_forced_local_keeps_watchdog_armed(self) -> None:
        """Phase A deviation 3 (plan section 6): an explicit backend=local override skips the
        PRE-LAUNCH veto but keeps the RUNTIME load-watchdog armed, so a forced-local run can
        still abort and offload on a load breach and cannot trip the host's auto-restart. The
        load here (3.5 on 8 cores) would fail the pre-launch veto, yet the forced run is
        accepted WITH watchdog_armed set."""
        resources = {"cpu": {"logical_cores": 8}, "load": {"load_1m": 3.5}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            job = {"task_family": "generic", "policy": {"backend": "local"},
                   "constraints": {"cpu": 4, "parallelism": 4, "core_hours": 100}}
            out = _plan(ws, job, token=False)
            plan = out["plan"]
            self.assertEqual(plan["decision"], "local_cpu")
            self.assertTrue(plan["accepted"])
            self.assertTrue(plan["forced_local"])
            self.assertTrue(plan["watchdog_armed"])
            self.assertGreaterEqual(plan["local_workers"], 1)

    def test_d_budget_gate_fail_closed_over_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            state = Path(tmp) / "state"
            # CCX63 worst case = 1.3678 * ceil(6) + 0.01 = 8.22 EUR > 3.0 per-job cap.
            with self.assertRaises(hetzner_backend.HetznerBudgetError):
                hetzner_backend.budget_gate(job_id="j-over", server_spec=CCX63,
                                            config=config, state_root=state)
            # Fail-closed: nothing was reserved.
            self.assertEqual(budget_ledger.outstanding(state, "hetzner"), 0.0)

    def test_d_budget_gate_reserves_eur_within_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            state = Path(tmp) / "state"
            res = hetzner_backend.budget_gate(job_id="j-ok", server_spec=CPX62,
                                              config=config, state_root=state)
            self.assertTrue(res["ok"])
            self.assertGreater(res["reserved"], 0.0)
            ledger = state / "hetzner-reservations.jsonl"
            self.assertTrue(ledger.exists())
            row = json.loads(ledger.read_text().splitlines()[0])
            self.assertEqual(row["unit"], "eur")
            # A second reservation that would exceed the daily cap is refused.
            with self.assertRaises(hetzner_backend.HetznerBudgetError):
                for i in range(20):
                    hetzner_backend.budget_gate(job_id=f"j-day-{i}", server_spec=CPX62,
                                                config=config, state_root=state)

    def test_d_concurrent_server_cap_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            state = Path(tmp) / "state"
            with self.assertRaises(hetzner_backend.HetznerBudgetError):
                hetzner_backend.budget_gate(job_id="j-many", server_spec=CPX62,
                                            config=config, state_root=state, count=5)

    def test_e_config_parses_hetzner_and_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            self.assertTrue(config.hetzner_enabled)
            self.assertEqual(config.routing_order, ["local", "modal", "hetzner", "gha"])
            self.assertEqual(config.gha_max_usage_fraction, 0.60)
            self.assertIn("cpx62", config.hetzner_server_types)
            self.assertEqual(config.hetzner_max_eur_per_job, 3.0)
            self.assertEqual(config.hetzner_location, "nbg1")
            self.assertEqual(config.local_danger_load_frac, 0.5)
            self.assertEqual(config.local_session_headroom_frac, 0.15)
            self.assertEqual(config.local_soft_load_frac, 0.4)
            self.assertEqual(config.local_hard_load_frac, 0.55)
            self.assertEqual(config.local_wall_budget_h, 2.0)

    def test_e_config_default_routing_order_modal_before_hetzner(self) -> None:
        # Kaggle inserted at position 2 (free CPU ahead of the paid lanes); Modal still
        # precedes Hetzner. The Hetzner scenarios above pin routing_order explicitly, so this
        # only tracks the shipped default.
        self.assertEqual(rc_config.BrokerConfig.__dataclass_fields__["routing_order"].default_factory(),
                         ["local", "kaggle", "modal", "hetzner", "gha"])

    def test_e_config_defaults_refresh_to_current_generation(self) -> None:
        """Plan section 12: the shipped defaults are the current orderable generation -- the
        default region is nbg1, the allow-list is [nbg1, hel1, sin] (fsn1 dropped), and the
        default rate card is the x86 lineup cpx22/cpx62/ccx63 (ARM cax* removed)."""
        fields = rc_config.BrokerConfig.__dataclass_fields__
        self.assertEqual(fields["hetzner_location"].default, "nbg1")
        self.assertEqual(fields["hetzner_allowed_locations"].default_factory(), ["nbg1", "hel1", "sin"])
        self.assertNotIn("fsn1", fields["hetzner_allowed_locations"].default_factory())
        self.assertEqual(set(hetzner_backend.DEFAULT_SERVER_TYPES), {"cpx22", "cpx62", "ccx63"})
        self.assertNotIn("cax41", hetzner_backend.DEFAULT_SERVER_TYPES)

    def test_e_example_config_parses_current_generation(self) -> None:
        """The shipped example config parses and carries the refreshed [hetzner] defaults."""
        example = WORKSPACE / "config" / "research-compute.example.toml"
        cfg = rc_config.load_config(example)
        self.assertEqual(cfg.hetzner_location, "nbg1")
        self.assertEqual(cfg.hetzner_allowed_locations, ["nbg1", "hel1", "sin"])
        self.assertEqual(set(cfg.hetzner_server_types), {"cpx22", "cpx62", "ccx63"})
        self.assertNotIn("fsn1", cfg.hetzner_allowed_locations)


class OrderablePlacementTests(unittest.TestCase):
    """Pure, network-free tests for the availability-driven placement picker (plan section 12).
    select_orderable_placement walks the cheapest adequate types first and, within each, the
    allowed locations in preference order, returning the first orderable (type, location) and
    falling back across combos on a stock-out. The availability map is injected directly."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = _config(Path(self._tmp.name))  # allowed_locations = [nbg1, hel1, sin]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # A 12-way / 8 GB job: cpx22 (2 vCPU) is too small, so cpx62 then ccx63 are adequate.
    JOB = {"core_hours": 40, "parallelism": 12, "peak_ram_gb": 8.0, "gpu": False}

    def test_picks_cheapest_adequate_orderable_combo(self) -> None:
        avail = {"nbg1": ["cpx22", "cpx62", "ccx63"]}
        spec, loc, reason = hetzner_backend.select_orderable_placement(
            self.JOB, config=self.config, availability=avail)
        self.assertEqual(spec["name"], "cpx62")  # cheapest adequate (cpx22 excluded: too small)
        self.assertEqual(loc, "nbg1")
        self.assertEqual(reason, "orderable")

    def test_location_fallback_on_stockout(self) -> None:
        # cpx62 is out in nbg1 but orderable in hel1 -> keep the cheap type, fall to hel1.
        avail = {"nbg1": ["cpx22"], "hel1": ["cpx62", "ccx63"]}
        spec, loc, _ = hetzner_backend.select_orderable_placement(
            self.JOB, config=self.config, availability=avail)
        self.assertEqual((spec["name"], loc), ("cpx62", "hel1"))

    def test_type_fallback_when_cheapest_stocked_out_everywhere(self) -> None:
        # cpx62 is out across the whole allow-list; ccx63 is orderable -> fall to the costlier type.
        avail = {"nbg1": ["cpx22", "ccx63"], "hel1": ["cpx22"], "sin": ["cpx22"]}
        spec, loc, _ = hetzner_backend.select_orderable_placement(
            self.JOB, config=self.config, availability=avail)
        self.assertEqual((spec["name"], loc), ("ccx63", "nbg1"))

    def test_full_stockout_returns_none(self) -> None:
        # Only cpx22 (too small) is orderable anywhere -> no orderable adequate server.
        avail = {"nbg1": ["cpx22"], "hel1": ["cpx22"], "sin": ["cpx22"]}
        spec, loc, reason = hetzner_backend.select_orderable_placement(
            self.JOB, config=self.config, availability=avail)
        self.assertIsNone(spec)
        self.assertIsNone(loc)
        self.assertIn("out of stock", reason)

    def test_respects_allowlist_ignoring_unlisted_regions(self) -> None:
        # cpx62 is orderable only in a US region NOT on the allow-list -> not selectable.
        avail = {"ash": ["cpx62", "ccx63"]}
        spec, _, _ = hetzner_backend.select_orderable_placement(
            self.JOB, config=self.config, availability=avail)
        self.assertIsNone(spec)


class LocalVetoAndWatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = _config(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_veto_accepts_safe_throttled_local(self) -> None:
        resources = {"cpu": {"logical_cores": 16}, "load": {"load_1m": 0.2}}
        estimate = {"core_hours": 2.0, "parallelism": 4}
        veto = planner.local_self_preservation_probe(estimate, config=self.config, resources=resources)
        self.assertTrue(veto["adequate"])
        self.assertGreaterEqual(veto["w_eff"], 1)
        self.assertLessEqual(veto["w_eff"], veto["w_safe"])

    def test_veto_rejects_when_no_safe_worker(self) -> None:
        resources = {"cpu": {"logical_cores": 4}, "load": {"load_1m": 2.0}}
        estimate = {"core_hours": 1.0, "parallelism": 1}
        veto = planner.local_self_preservation_probe(estimate, config=self.config, resources=resources)
        self.assertFalse(veto["adequate"])
        self.assertLess(veto["w_safe"], 1)

    def test_watchdog_aborts_and_checkpoints_on_hard_breach(self) -> None:
        resources = {"cpu": {"logical_cores": 8}}  # hard = 0.55 * 8 = 4.4
        marks: list[int] = []
        verdict = planner.run_local_watched(
            w_eff=2, config=self.config, resources=resources,
            load_source=lambda: 5.0, checkpoint=lambda: marks.append(1) or "ckpt")
        self.assertEqual(verdict["status"], planner.WATCH_ABORT_FALLBACK)
        self.assertEqual(verdict["checkpoint"], "ckpt")
        self.assertTrue(marks)

    def test_forced_local_watchdog_aborts_on_load_breach(self) -> None:
        """The armed watchdog a forced-local plan carries aborts and offloads (checkpoint +
        ABORT_FALLBACK) on a hard load breach, so even a forced-local run cannot shut the box
        down. Uses the forced worker count (w_eff=4) with a breaching load reading."""
        resources = {"cpu": {"logical_cores": 8}}  # hard = 0.55 * 8 = 4.4
        verdict = planner.run_local_watched(
            w_eff=4, config=self.config, resources=resources,
            load_source=lambda: 6.0, checkpoint=lambda: "ckpt")
        self.assertEqual(verdict["status"], planner.WATCH_ABORT_FALLBACK)
        self.assertEqual(verdict["checkpoint"], "ckpt")

    def test_watchdog_completes_under_ceiling(self) -> None:
        resources = {"cpu": {"logical_cores": 8}}
        steps = iter([True, True, False])
        verdict = planner.run_local_watched(
            w_eff=2, config=self.config, resources=resources,
            load_source=lambda: 0.5, run_step=lambda: next(steps))
        self.assertEqual(verdict["status"], planner.WATCH_OK)

    def test_provision_and_teardown_guarded(self) -> None:
        prev = os.environ.pop("HCLOUD_TOKEN", None)
        try:
            with self.assertRaises(hetzner_backend.HetznerError):
                hetzner_backend.provision(server_spec=CPX62, config=self.config, job_id="j")
            skipped = hetzner_backend.teardown(config=self.config)
            self.assertTrue(skipped["skipped"])
        finally:
            if prev is not None:
                os.environ["HCLOUD_TOKEN"] = prev


class LivenessAndCapTests(unittest.TestCase):
    """In-process, network-free tests for the per-vendor account-usable liveness probes
    (plan §6.1), the order-driven cascade, and the GitHub Actions cumulative 60% minutes
    cap. Every real probe hook is replaced or injected, so no external call ever runs."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = _config(Path(self._tmp.name))
        self._prev_hz = hetzner_backend.ACCOUNT_LIVENESS_PROBE
        self._prev_modal = planner.MODAL_LIVENESS_PROBE
        self._prev_usage = gha.USAGE_PROBE
        self._prev_token = os.environ.pop("HCLOUD_TOKEN", None)

    def tearDown(self) -> None:
        hetzner_backend.ACCOUNT_LIVENESS_PROBE = self._prev_hz
        planner.MODAL_LIVENESS_PROBE = self._prev_modal
        gha.USAGE_PROBE = self._prev_usage
        if self._prev_token is not None:
            os.environ["HCLOUD_TOKEN"] = self._prev_token
        else:
            os.environ.pop("HCLOUD_TOKEN", None)
        self._tmp.cleanup()

    # -- Hetzner account-usable liveness --------------------------------------

    def test_hetzner_liveness_injection_short_circuits_hook(self) -> None:
        calls: list[int] = []
        hetzner_backend.ACCOUNT_LIVENESS_PROBE = lambda config, **k: calls.append(1) or {"usable": True}
        ok, reason = hetzner_backend.account_usable(
            self.config, {"liveness": {"hetzner": {"usable": False, "reason": "http_403"}}})
        self.assertFalse(ok)
        self.assertEqual(reason, "http_403")
        self.assertEqual(calls, [])  # injection wins; the real hook never runs

    def test_hetzner_liveness_uses_hook_without_injection(self) -> None:
        os.environ["HCLOUD_TOKEN"] = "dummy-offline"
        hetzner_backend.ACCOUNT_LIVENESS_PROBE = lambda config, **k: {"usable": False, "reason": "http_401"}
        ok, reason = hetzner_backend.account_usable(self.config, None)
        self.assertFalse(ok)
        self.assertEqual(reason, "http_401")

    def test_hetzner_probe_unavailable_when_liveness_unusable(self) -> None:
        os.environ["HCLOUD_TOKEN"] = "dummy-offline"
        hetzner_backend.ACCOUNT_LIVENESS_PROBE = lambda config, **k: {"usable": False, "reason": "http_401"}
        estimate = {"core_hours": 40, "parallelism": 12, "peak_ram_gb": 8.0,
                    "gpu": False, "data_locality": ""}
        blocked = hetzner_backend.probe(estimate, config=self.config, resources=None)
        self.assertFalse(blocked["available"])
        self.assertFalse(blocked["account_usable"])
        self.assertIn("http_401", blocked["reason"])
        # A usable (injected) verdict flips the same job back to available + adequate.
        usable = hetzner_backend.probe(estimate, config=self.config,
                                       resources={"liveness": {"hetzner": {"usable": True}}})
        self.assertTrue(usable["available"])
        self.assertTrue(usable["adequate"])

    # -- Modal account-usable liveness ----------------------------------------

    def test_modal_lane_available_injection_and_hook(self) -> None:
        # Injected out-of-credits -> unavailable regardless of readiness.
        ok, _ = planner.modal_lane_available(
            self.config, {"liveness": {"modal": {"ready": True, "usable": False}}}, modal_ready=True)
        self.assertFalse(ok)
        # Hook path: an out-of-credits verdict from the wired hook.
        planner.MODAL_LIVENESS_PROBE = lambda config: (False, "no payment method on file")
        ok2, reason2 = planner.modal_lane_available(self.config, None, modal_ready=True)
        self.assertFalse(ok2)
        self.assertIn("payment", reason2)
        # Not ready -> unavailable without consulting the hook.
        ok3, _ = planner.modal_lane_available(self.config, None, modal_ready=False)
        self.assertFalse(ok3)

    # -- order-driven cascade -------------------------------------------------

    def test_cascade_is_driven_by_routing_order(self) -> None:
        hz_ok = {"available": True, "adequate": True,
                 "server_spec": {"name": "cpx62"}, "reason": "available"}
        gha_ok = lambda: (True, "within usage cap")
        # local>modal>hetzner>gha with Modal down -> Hetzner (the fallthrough fix).
        _, backend, _ = planner.select_remote_lane(
            order=["local", "modal", "hetzner", "gha"], modal_decision="modal_cpu",
            gpu_signal=False, hz=hz_ok, hz_in_order=True, modal_ok=(False, "out"), gha_ok=gha_ok)
        self.assertEqual(backend, "hetzner")
        # Reordered local>hetzner>modal>gha -> Hetzner wins even with Modal up: the walk
        # follows routing_order, it is not hard-coded.
        _, backend2, _ = planner.select_remote_lane(
            order=["local", "hetzner", "modal", "gha"], modal_decision="modal_cpu",
            gpu_signal=False, hz=hz_ok, hz_in_order=True, modal_ok=(True, "ok"), gha_ok=gha_ok)
        self.assertEqual(backend2, "hetzner")

    # -- GitHub Actions cumulative 60% minutes cap ----------------------------

    def test_gha_cap_cumulative_not_per_task(self) -> None:
        cfg = _gha_config(Path(self._tmp.name) / "gha")
        repo_cfg = dict(cfg.gha_repos["sweep"])  # worst case = 200 min (10% of 2000; cap 1200)
        ok_lo, _ = gha.usage_cap_ok(repo_cfg=repo_cfg, config=cfg, cells=1,
            resources={"liveness": {"gha": {"used_this_cycle": 600, "included_minutes": 2000}}})
        self.assertTrue(ok_lo)   # 30% total + 10% job = 800 <= 1200
        ok_hi, detail = gha.usage_cap_ok(repo_cfg=repo_cfg, config=cfg, cells=1,
            resources={"liveness": {"gha": {"used_this_cycle": 1100, "included_minutes": 2000}}})
        self.assertFalse(ok_hi)  # 55% total + 10% job = 1300 > 1200 (cumulative, not per-task)
        self.assertIn("cap", detail["reason"])

    def test_gha_cap_boundary_via_usage_probe_hook(self) -> None:
        cfg = _gha_config(Path(self._tmp.name) / "gha2")
        repo_cfg = {"repo": "o/r", "timeout_minutes": 30, "runner_os": "linux"}  # worst = 30
        gha.USAGE_PROBE = lambda owner, config: {"used_this_cycle": 1170.0, "included_minutes": 2000.0}
        ok_eq, _ = gha.usage_cap_ok(repo_cfg=repo_cfg, config=cfg, cells=1)  # 1170+30 = 1200 <= 1200
        self.assertTrue(ok_eq)
        gha.USAGE_PROBE = lambda owner, config: {"used_this_cycle": 1171.0, "included_minutes": 2000.0}
        ok_over, _ = gha.usage_cap_ok(repo_cfg=repo_cfg, config=cfg, cells=1)  # 1201 > 1200
        self.assertFalse(ok_over)


class GpuRoutingTests(unittest.TestCase):
    """Offline, credential-free GPU-routing tests (plan §5.1) over the existing lanes. These
    exercise the router-wide GPU policy across the full matrix {CPU, GPU} x {auto-signal,
    explicit-request} x {available, unavailable -> fallthrough}. gpu_requested =
    auto_gpu_signal OR policy.gpu, and an explicit request ALWAYS wins. Every run injects a
    liveness + resource snapshot, so the subprocess planner is deterministic and NEVER touches
    the network. The CPU auto-signal corners (Modal available -> Modal; Modal down -> Hetzner;
    both down -> GHA) are covered by HetznerRoutingTests; the CPU explicit-request corners are
    added here so the whole matrix is exercised."""

    # Auto-signalled GPU: an "embedding" task-family marker triggers auto_gpu_signal with no
    # explicit request.
    AUTO_GPU_JOB = {"task_family": "embedding",
                    "constraints": {"cpu": 2, "memory_mb": 4096, "parallelism": 2,
                                    "core_hours": 4}}
    # Explicit GPU on a job the estimate would classify as a CPU offload (heavy enumeration):
    # proves an explicit request wins over a CPU auto-signal.
    EXPLICIT_GPU_ON_CPU_JOB = {"task_family": "enumeration", "policy": {"gpu": True},
                               "constraints": {"cpu": 12, "memory_mb": 8192, "parallelism": 12,
                                               "core_hours": 40, "resource_class": "cpu"}}
    # A plain CPU job: no GPU marker and no explicit request.
    PLAIN_CPU_JOB = {"task_family": "enumeration",
                     "constraints": {"cpu": 12, "memory_mb": 8192, "parallelism": 12,
                                     "core_hours": 40, "resource_class": "cpu"}}

    @staticmethod
    def _resources(*, gpus: int = 0, modal_usable: bool = True) -> dict:
        return {"gpu": {"total_gpus": gpus},
                "liveness": {"modal": {"ready": True, "usable": modal_usable},
                             "hetzner": {"usable": True}}}

    # -- GPU x explicit-request ------------------------------------------------

    def test_explicit_gpu_with_local_gpu_routes_local_gpu(self) -> None:
        """Explicit GPU + a local GPU present -> local-GPU (first GPU-capable lane in order)."""
        res = self._resources(gpus=1, modal_usable=True)
        job = {"task_family": "generic", "policy": {"gpu": True},
               "constraints": {"cpu": 2, "memory_mb": 2048, "parallelism": 2, "core_hours": 2}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res)
            plan = _plan(ws, job, token=True)["plan"]
            self.assertEqual(plan["decision"], "local_gpu")
            self.assertEqual(plan["backend"], "local")
            self.assertTrue(plan["gpu"])
            self.assertEqual(plan["routing_trail"][0]["backend"], "local")
            self.assertTrue(plan["routing_trail"][0]["gpu_capable"])

    def test_explicit_gpu_without_local_gpu_routes_modal_gpu(self) -> None:
        """Explicit GPU, no local GPU -> Modal-GPU (local skipped: no GPU)."""
        res = self._resources(gpus=0, modal_usable=True)
        job = {"task_family": "generic", "policy": {"gpu": True},
               "constraints": {"cpu": 2, "memory_mb": 2048, "parallelism": 2, "core_hours": 2}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res)
            plan = _plan(ws, job, token=True)["plan"]
            self.assertEqual(plan["decision"], "modal_gpu")
            trail = {t["backend"]: t for t in plan["routing_trail"]}
            self.assertFalse(trail["local"]["available"])
            self.assertTrue(trail["modal"]["available"])

    def test_explicit_gpu_overrides_cpu_auto_signal(self) -> None:
        """The explicit-wins case: a heavy CPU-classified job with policy.gpu=True routes to a
        GPU lane (modal_gpu), NOT the CPU offload it would otherwise get."""
        res = self._resources(gpus=0, modal_usable=True)
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res)
            plan = _plan(ws, self.EXPLICIT_GPU_ON_CPU_JOB, token=True)["plan"]
            self.assertEqual(plan["decision"], "modal_gpu")
            self.assertNotEqual(plan["decision"], "modal_cpu")

    def test_explicit_gpu_no_lane_available_is_rejected(self) -> None:
        """GPU x unavailable: explicit GPU, no local GPU, Modal down -> no GPU-capable lane
        (Hetzner has none, GHA GPU off) -> rejected, never silently run on CPU."""
        res = self._resources(gpus=0, modal_usable=False)
        job = {"task_family": "generic", "policy": {"gpu": True}, "gha_target": "sweep",
               "constraints": {"cpu": 2, "memory_mb": 2048, "parallelism": 2, "core_hours": 2,
                               "matrix_cells": 1}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res, config_toml=GHA_CONFIG_TOML)
            plan = _plan(ws, job, token=True)["plan"]
            self.assertEqual(plan["decision"], "rejected")
            self.assertFalse(plan["accepted"])
            self.assertIn("no_gpu_lane_available", plan["risk_flags"])

    # -- GPU x auto-signal -----------------------------------------------------

    def test_auto_gpu_signal_without_local_gpu_routes_modal_gpu(self) -> None:
        """Auto-signal alone (no explicit request) triggers GPU: an embedding job with no
        local GPU -> Modal-GPU."""
        res = self._resources(gpus=0, modal_usable=True)
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res)
            plan = _plan(ws, self.AUTO_GPU_JOB, token=True)["plan"]
            self.assertEqual(plan["decision"], "modal_gpu")

    def test_auto_gpu_signal_with_local_gpu_routes_local_gpu(self) -> None:
        """Auto-signalled GPU + a local GPU -> local-GPU, same as the explicit case."""
        res = self._resources(gpus=2, modal_usable=True)
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res)
            plan = _plan(ws, self.AUTO_GPU_JOB, token=True)["plan"]
            self.assertEqual(plan["decision"], "local_gpu")

    def test_gpu_skips_hetzner_and_gha_then_lands_on_modal(self) -> None:
        """With routing_order reordered so the GPU-incapable lanes come first, a GPU job must
        SKIP Hetzner (no GPU) and GitHub Actions (GPU off) before landing on Modal-GPU --
        proving both are GPU-inadequate, order-driven."""
        res = self._resources(gpus=0, modal_usable=True)
        job = dict(self.AUTO_GPU_JOB); job["gha_target"] = "sweep"
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res, config_toml=GPU_REORDER_CONFIG_TOML)
            plan = _plan(ws, job, token=True)["plan"]
            self.assertEqual(plan["decision"], "modal_gpu")
            trail = {t["backend"]: t for t in plan["routing_trail"]}
            self.assertFalse(trail["hetzner"]["gpu_capable"])
            self.assertEqual(trail["hetzner"]["reason"], "hetzner_no_gpu")
            self.assertFalse(trail["gha"]["gpu_capable"])
            self.assertEqual(trail["gha"]["reason"], "gha_gpu_disabled")
            self.assertTrue(trail["modal"]["available"])

    def test_auto_gpu_modal_down_falls_through_to_rejected(self) -> None:
        """GPU x auto-signal x unavailable: an auto-GPU job with Modal down and no local GPU
        walks the whole order (Hetzner skipped: no GPU; GHA skipped: GPU off) and is rejected;
        the trail records every GPU-incapable lane."""
        res = self._resources(gpus=0, modal_usable=False)
        job = dict(self.AUTO_GPU_JOB); job["gha_target"] = "sweep"
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res, config_toml=GHA_CONFIG_TOML)
            plan = _plan(ws, job, token=True)["plan"]
            self.assertEqual(plan["decision"], "rejected")
            self.assertIn("no_gpu_lane_available", plan["risk_flags"])
            trail = {t["backend"]: t for t in plan["routing_trail"]}
            self.assertFalse(trail["modal"]["available"])
            self.assertEqual(trail["hetzner"]["reason"], "hetzner_no_gpu")
            self.assertEqual(trail["gha"]["reason"], "gha_gpu_disabled")

    # -- CPU: never given a GPU + explicit-request corners ---------------------

    def test_cpu_job_is_never_given_gpu(self) -> None:
        """A CPU job (no GPU marker, no explicit request) is never given a GPU, even when a
        local GPU is present: it stays on the CPU cascade (Modal-CPU here)."""
        res = self._resources(gpus=4, modal_usable=True)  # local GPU present but unused
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res)
            plan = _plan(ws, self.PLAIN_CPU_JOB, token=True)["plan"]
            self.assertEqual(plan["decision"], "modal_cpu")
            self.assertNotIn("gpu", plan["decision"])

    def test_cpu_explicit_modal_override_available(self) -> None:
        """CPU x explicit-request x available: an explicit backend=modal override forces a
        would-be-local job to Modal-CPU (never a GPU lane)."""
        res = self._resources(gpus=1, modal_usable=True)
        job = {"task_family": "generic", "policy": {"backend": "modal"},
               "constraints": {"cpu": 1, "memory_mb": 1024, "parallelism": 1, "core_hours": 1}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res)
            plan = _plan(ws, job, token=True)["plan"]
            self.assertEqual(plan["decision"], "modal_cpu")

    def test_cpu_explicit_hetzner_override_unavailable_rejected(self) -> None:
        """CPU x explicit-request x unavailable: an explicit backend=hetzner override with no
        HCLOUD_TOKEN (lane unavailable) is rejected rather than silently re-routed."""
        res = self._resources(gpus=0, modal_usable=True)
        job = {"task_family": "enumeration", "policy": {"backend": "hetzner"},
               "constraints": {"cpu": 12, "memory_mb": 8192, "parallelism": 12, "core_hours": 40}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res)
            plan = _plan(ws, job, token=False)["plan"]
            self.assertEqual(plan["decision"], "rejected")
            self.assertIn("hetzner_unavailable", plan["risk_flags"])


class GpuLaneUnitTests(unittest.TestCase):
    """In-process, network-free unit tests for the GPU-lane cascade (plan §5.1) and the
    gha.gpu_enabled config toggle. select_gpu_lane is a pure function, so these need no
    subprocess or resource file."""

    def test_gpu_lane_walk_is_order_driven(self) -> None:
        class Cfg:  # only gha_gpu_enabled is consulted
            gha_gpu_enabled = False
        default = ["local", "modal", "hetzner", "gha"]
        # No local GPU, Modal up -> Modal-GPU (local skipped).
        dec, be, _ = planner.select_gpu_lane(order=default, local_gpu=False, config=Cfg(),
                                             modal_ok=(True, "ok"), gha_ok=lambda: (True, "cap"))
        self.assertEqual((dec, be), ("modal_gpu", "modal"))
        # A local GPU wins the walk (first GPU-capable lane).
        dec2, be2, _ = planner.select_gpu_lane(order=default, local_gpu=True, config=Cfg(),
                                               modal_ok=(True, "ok"), gha_ok=lambda: (True, "cap"))
        self.assertEqual((dec2, be2), ("local_gpu", "local"))
        # No local GPU and Modal down -> no GPU lane (Hetzner has none, GHA GPU off) -> None.
        dec3, be3, _ = planner.select_gpu_lane(order=default, local_gpu=False, config=Cfg(),
                                               modal_ok=(False, "out"), gha_ok=lambda: (True, "cap"))
        self.assertEqual((dec3, be3), (None, None))

    def test_gpu_lane_hetzner_always_skipped_and_gha_toggle(self) -> None:
        class Off:
            gha_gpu_enabled = False
        class On:
            gha_gpu_enabled = True
        # Reorder so Hetzner + GHA precede Modal, with Modal down, to force the walk past both.
        order = ["local", "hetzner", "gha", "modal"]
        dec_off, be_off, trail_off = planner.select_gpu_lane(
            order=order, local_gpu=False, config=Off(),
            modal_ok=(False, "out"), gha_ok=lambda: (True, "cap"))
        self.assertEqual((dec_off, be_off), (None, None))  # gha off + modal down -> no lane
        hz = next(t for t in trail_off if t["backend"] == "hetzner")
        self.assertFalse(hz["gpu_capable"])
        self.assertEqual(hz["reason"], "hetzner_no_gpu")
        gha_off = next(t for t in trail_off if t["backend"] == "gha")
        self.assertEqual(gha_off["reason"], "gha_gpu_disabled")
        # Flip gha.gpu_enabled on: GHA becomes GPU-capable and is chosen (Modal still down).
        dec_on, be_on, trail_on = planner.select_gpu_lane(
            order=order, local_gpu=False, config=On(),
            modal_ok=(False, "out"), gha_ok=lambda: (True, "within usage cap"))
        self.assertEqual((dec_on, be_on), ("gha", "gha"))
        gha_on = next(t for t in trail_on if t["backend"] == "gha")
        self.assertTrue(gha_on["gpu_capable"])
        self.assertTrue(gha_on["available"])
        # Even with GHA GPU on, Hetzner is still never GPU-capable.
        self.assertFalse(next(t for t in trail_on if t["backend"] == "hetzner")["gpu_capable"])

    def test_config_parses_gha_gpu_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(_config_text(Path(tmp) / "base", CONFIG_TOML).gha_gpu_enabled)
            self.assertFalse(_config_text(Path(tmp) / "off", GHA_CONFIG_TOML).gha_gpu_enabled)
            self.assertTrue(_config_text(Path(tmp) / "on", GHA_GPU_CONFIG_TOML).gha_gpu_enabled)


DRIVER_TOKEN = "SECRET-HCLOUD-TOKEN-offline-do-not-log"


def _make_bundle(tmp: Path, manifest: dict) -> Path:
    bundle = tmp / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bundle


class _FakeRunner:
    """Records every external command and NEVER provisions. Answers the read-only availability
    queries (`server-type list`, `datacenter list`) from a canned catalog (cpx22/cpx62/ccx63 all
    orderable in nbg1 by default; override `datacenters` / `server_types` per test) and a canned
    server list (so IP resolution works), and returns success for everything else; `fail_on`
    simulates a mid-run failure, `echo_token` proves output redaction."""

    SERVER_TYPES = [{"id": 22, "name": "cpx22"}, {"id": 62, "name": "cpx62"}, {"id": 63, "name": "ccx63"}]
    DATACENTERS = [{"id": 4, "name": "nbg1-dc3", "location": {"name": "nbg1"},
                    "server_types": {"available": [22, 62, 63], "supported": [22, 62, 63]}}]

    def __init__(self, ip: str = "203.0.113.9", fail_on: str | None = None, echo_token: bool = False,
                 datacenters: list | None = None, server_types: list | None = None):
        self.calls: list[dict] = []
        self.ip = ip
        self.fail_on = fail_on
        self.echo_token = echo_token
        self.datacenters = self.DATACENTERS if datacenters is None else datacenters
        self.server_types = self.SERVER_TYPES if server_types is None else server_types

    def __call__(self, argv, *, env, timeout):
        joined = " ".join(argv)
        self.calls.append({"argv": list(argv), "joined": joined,
                           "env_has_token": bool(env.get("HCLOUD_TOKEN"))})
        if self.fail_on and self.fail_on in joined:
            return {"returncode": 1, "stdout": "", "stderr": "simulated failure"}
        if "datacenter" in argv and "list" in argv:
            return {"returncode": 0, "stdout": json.dumps(self.datacenters), "stderr": ""}
        if "server-type" in argv and "list" in argv:
            return {"returncode": 0, "stdout": json.dumps(self.server_types), "stderr": ""}
        if "server" in argv and "list" in argv:
            servers = [{"id": 4242, "name": "ai-agents-skills-jobX", "status": "running",
                        "public_net": {"ipv4": {"ip": self.ip}}}]
            return {"returncode": 0, "stdout": json.dumps(servers), "stderr": ""}
        stdout = f"leaked {env.get('HCLOUD_TOKEN')} value" if self.echo_token else "ok"
        return {"returncode": 0, "stdout": stdout, "stderr": ""}


def _iso(epoch: float) -> str:
    """hcloud-style ISO 8601 UTC timestamp for a given epoch (reaper age tests)."""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


class _ReaperRunner:
    """Records commands and NEVER provisions. Returns a configurable server list for
    `server list` and success for `server delete`, recording the deleted ids, so the reaper's
    delete set can be asserted exactly. Reused for the reconcile-before-create guard."""

    def __init__(self, servers: list[dict]):
        self.servers = servers
        self.calls: list[dict] = []
        self.deleted: list[str] = []

    def __call__(self, argv, *, env, timeout):
        self.calls.append({"argv": list(argv), "joined": " ".join(argv),
                           "env_has_token": bool(env.get("HCLOUD_TOKEN"))})
        if "list" in argv:
            return {"returncode": 0, "stdout": json.dumps(self.servers), "stderr": ""}
        if "delete" in argv:
            self.deleted.append(argv[-1])
            return {"returncode": 0, "stdout": "ok", "stderr": ""}
        return {"returncode": 0, "stdout": "ok", "stderr": ""}


class HetznerDriverTests(unittest.TestCase):
    """Offline, credential-free tests for the hcloud lifecycle driver. Every external
    command is intercepted, so no server is ever provisioned; the token guard, budget gate,
    labels, dry-run path, redaction, and guaranteed teardown are all exercised offline."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = _config(self.tmp)
        self.state = self.tmp / "state"
        self._prev_runner = hetzner_driver.COMMAND_RUNNER
        # preflight consults hetzner_backend.probe -> the account-usable liveness hook;
        # replace it so the offline driver tests never make a real hcloud API call.
        self._prev_liveness = hetzner_backend.ACCOUNT_LIVENESS_PROBE
        hetzner_backend.ACCOUNT_LIVENESS_PROBE = lambda config, **k: {"usable": True, "reason": "test"}
        self._prev_token = os.environ.pop("HCLOUD_TOKEN", None)

    def tearDown(self) -> None:
        hetzner_driver.COMMAND_RUNNER = self._prev_runner
        hetzner_backend.ACCOUNT_LIVENESS_PROBE = self._prev_liveness
        if self._prev_token is not None:
            os.environ["HCLOUD_TOKEN"] = self._prev_token
        else:
            os.environ.pop("HCLOUD_TOKEN", None)
        self._tmp.cleanup()

    def _bundle(self, **over) -> Path:
        manifest = {"job_id": "jobX", "core_hours": 40, "parallelism": 12,
                    "memory_mb": 8192, "arch": "x86"}
        manifest.update(over)
        return _make_bundle(self.tmp, manifest)

    def test_preflight_plans_without_provisioning(self) -> None:
        runner = _FakeRunner()
        hetzner_driver.COMMAND_RUNNER = runner
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        out = hetzner_driver.preflight(job_dir=self._bundle(), config=self.config, state_root=self.state)
        self.assertEqual(out["server_type"], "cpx62")
        self.assertEqual(out["region"], "nbg1")
        self.assertEqual(out["budget_verdict"], "auto_approve")
        self.assertFalse(out["provisioned"])
        self.assertLess(out["est_cost_eur"], 3.0)
        # preflight availability-checks the live datacenter list (read-only): only list queries,
        # never a create / delete, and it reserves nothing.
        joined = [c["joined"] for c in runner.calls]
        self.assertTrue(any("datacenter list" in j for j in joined))
        self.assertTrue(all("create" not in j and "delete" not in j for j in joined))
        self.assertFalse((self.state / "hetzner-reservations.jsonl").exists())  # reserves nothing

    def test_parse_availability_maps_ids_and_unions_datacenters(self) -> None:
        """parse_availability resolves numeric server-type ids to names and unions the orderable
        set across the datacenters in a location."""
        server_types = json.dumps(
            [{"id": 22, "name": "cpx22"}, {"id": 62, "name": "cpx62"}, {"id": 63, "name": "ccx63"}])
        datacenters = json.dumps([
            {"name": "nbg1-dc3", "location": {"name": "nbg1"}, "server_types": {"available": [22, 62]}},
            {"name": "nbg1-dc4", "location": {"name": "nbg1"}, "server_types": {"available": [63]}},
            {"name": "hel1-dc2", "location": {"name": "hel1"}, "server_types": {"available": [62]}},
        ])
        avail = hetzner_driver.parse_availability(server_types, datacenters)
        self.assertEqual(set(avail["nbg1"]), {"cpx22", "cpx62", "ccx63"})  # unioned across DCs
        self.assertEqual(avail["hel1"], ["cpx62"])

    def test_fetch_availability_uses_runner_readonly(self) -> None:
        """fetch_availability builds the {location: [orderable types]} map from the live
        datacenter list through the mockable runner, issuing only read-only list calls."""
        runner = _FakeRunner()
        hetzner_driver.COMMAND_RUNNER = runner
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        avail = hetzner_driver.fetch_availability(self.config)
        self.assertIn("cpx62", avail.get("nbg1", []))
        self.assertTrue(all("create" not in c["joined"] and "delete" not in c["joined"]
                            for c in runner.calls))

    def test_preflight_falls_back_to_next_location_on_stockout(self) -> None:
        """A stock-out of the cheapest adequate type in the preferred region degrades gracefully:
        cpx62 is out in nbg1 but orderable in hel1, so preflight reports (cpx62, hel1)."""
        runner = _FakeRunner(datacenters=[
            {"name": "nbg1-dc3", "location": {"name": "nbg1"}, "server_types": {"available": [22]}},
            {"name": "hel1-dc2", "location": {"name": "hel1"}, "server_types": {"available": [22, 62, 63]}},
        ])
        hetzner_driver.COMMAND_RUNNER = runner
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        out = hetzner_driver.preflight(job_dir=self._bundle(), config=self.config, state_root=self.state)
        self.assertEqual(out["server_type"], "cpx62")
        self.assertEqual(out["region"], "hel1")  # fell back from the stocked-out nbg1
        self.assertFalse(out["provisioned"])

    def test_preflight_falls_back_to_next_type_when_cheapest_stocked_out(self) -> None:
        """When the cheapest adequate type is out across the whole allow-list, preflight falls
        back to the next (costlier) orderable type and flags it for human confirmation."""
        runner = _FakeRunner(datacenters=[
            {"name": "nbg1-dc3", "location": {"name": "nbg1"}, "server_types": {"available": [22, 63]}},
            {"name": "hel1-dc2", "location": {"name": "hel1"}, "server_types": {"available": [22, 63]}},
        ])
        hetzner_driver.COMMAND_RUNNER = runner
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        out = hetzner_driver.preflight(job_dir=self._bundle(), config=self.config, state_root=self.state)
        self.assertEqual(out["server_type"], "ccx63")
        self.assertEqual(out["region"], "nbg1")
        self.assertEqual(out["budget_verdict"], "needs_human_confirmation")  # ccx63 worst-case > cap

    def test_preflight_reports_no_orderable_server_on_full_stockout(self) -> None:
        """A full stock-out (only the too-small cpx22 orderable) makes the lane report no
        orderable server instead of planning an unprovisionable type."""
        runner = _FakeRunner(datacenters=[
            {"name": "nbg1-dc3", "location": {"name": "nbg1"}, "server_types": {"available": [22]}},
        ])
        hetzner_driver.COMMAND_RUNNER = runner
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        out = hetzner_driver.preflight(job_dir=self._bundle(), config=self.config, state_root=self.state)
        self.assertIsNone(out["server_type"])
        self.assertFalse(out["available"])
        self.assertEqual(out["budget_verdict"], "no_orderable_server")

    def test_up_provisions_in_fallback_location_on_regional_stockout(self) -> None:
        """Regional stock-out degrades gracefully on the provisioning path: cpx62 is out in nbg1
        but orderable in hel1, so up provisions cpx62 in hel1 (the create carries --location hel1)."""
        runner = _FakeRunner(datacenters=[
            {"name": "nbg1-dc3", "location": {"name": "nbg1"}, "server_types": {"available": [22]}},
            {"name": "hel1-dc2", "location": {"name": "hel1"}, "server_types": {"available": [22, 62, 63]}},
        ])
        hetzner_driver.COMMAND_RUNNER = runner
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        out = hetzner_driver.up(job_dir=self._bundle(), config=self.config, state_root=self.state, confirm=True)
        self.assertTrue(out["provisioned"])
        self.assertEqual(out["server_type"], "cpx62")
        self.assertEqual(out["location"], "hel1")  # fell back from the stocked-out nbg1
        create = next(c for c in runner.calls if "create" in c["joined"])
        self.assertIn("--location hel1", create["joined"])

    def test_up_budget_gate_still_guards_costlier_fallback_type(self) -> None:
        """The type fallback stays under the fail-closed budget gate: with cpx62 stocked out and
        only ccx63 orderable, up resolves ccx63 but the gate refuses it (worst-case over the
        per-job cap) before any create."""
        runner = _FakeRunner(datacenters=[
            {"name": "nbg1-dc3", "location": {"name": "nbg1"}, "server_types": {"available": [22, 63]}},
        ])
        hetzner_driver.COMMAND_RUNNER = runner
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        with self.assertRaises(hetzner_backend.HetznerBudgetError):
            hetzner_driver.up(job_dir=self._bundle(), config=self.config, state_root=self.state, confirm=True)
        self.assertTrue(all("create" not in c["joined"] for c in runner.calls))  # gated before create

    def test_up_dry_run_no_reservation_no_call_no_token_on_argv(self) -> None:
        runner = _FakeRunner()
        hetzner_driver.COMMAND_RUNNER = runner
        out = hetzner_driver.up(job_dir=self._bundle(), config=self.config, state_root=self.state, dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertFalse(out["provisioned"])
        self.assertEqual(out["command"][:3], ["hcloud", "server", "create"])
        joined = " ".join(out["command"])
        self.assertNotIn(DRIVER_TOKEN, joined)
        self.assertIn("managed-by=ai-agents-skills", joined)
        self.assertIn("job-id=jobX", joined)
        self.assertEqual(runner.calls, [])
        self.assertFalse((self.state / "hetzner-reservations.jsonl").exists())

    def test_up_refuses_without_token_and_without_confirm(self) -> None:
        hetzner_driver.COMMAND_RUNNER = _FakeRunner()
        with self.assertRaises(hetzner_driver.HetznerDriverError):  # no token
            hetzner_driver.up(job_dir=self._bundle(), config=self.config, state_root=self.state, confirm=True)
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        with self.assertRaises(hetzner_driver.HetznerDriverError):  # token but no confirm
            hetzner_driver.up(job_dir=self._bundle(), config=self.config, state_root=self.state, confirm=False)
        self.assertFalse((self.state / "hetzner-reservations.jsonl").exists())  # fail-closed: no reservation

    def test_up_confirm_reserves_budget_and_keeps_token_off_argv(self) -> None:
        runner = _FakeRunner()
        hetzner_driver.COMMAND_RUNNER = runner
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        out = hetzner_driver.up(job_dir=self._bundle(), config=self.config, state_root=self.state, confirm=True)
        self.assertTrue(out["provisioned"])
        self.assertEqual(out["server_type"], "cpx62")
        self.assertEqual(out["location"], "nbg1")
        self.assertGreater(out["reservation"]["reserved"], 0.0)
        self.assertTrue((self.state / "hetzner-reservations.jsonl").exists())
        create = next(c for c in runner.calls if "create" in c["joined"])
        self.assertNotIn(DRIVER_TOKEN, create["joined"])  # token never on argv
        self.assertTrue(create["env_has_token"])          # token travels via env
        self.assertIn("managed-by=ai-agents-skills", create["joined"])
        self.assertIn("job-id=jobX", create["joined"])

    def test_run_hcloud_passes_token_via_env_and_redacts_output(self) -> None:
        runner = _FakeRunner(echo_token=True)
        hetzner_driver.COMMAND_RUNNER = runner
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        res = hetzner_driver.run_hcloud(["version"])
        self.assertNotIn(DRIVER_TOKEN, res["stdout"])  # redacted on surfaced output
        self.assertIn("<REDACTED_HCLOUD_TOKEN>", res["stdout"])
        self.assertNotIn(DRIVER_TOKEN, runner.calls[-1]["joined"])
        self.assertTrue(runner.calls[-1]["env_has_token"])

    def test_run_hcloud_refuses_without_token(self) -> None:
        hetzner_driver.COMMAND_RUNNER = _FakeRunner()
        with self.assertRaises(hetzner_driver.HetznerDriverError):
            hetzner_driver.run_hcloud(["version"])

    def test_down_dry_run_and_confirm_paths(self) -> None:
        runner = _FakeRunner()
        hetzner_driver.COMMAND_RUNNER = runner
        dry = hetzner_driver.down(config=self.config, job_id="jobX", dry_run=True)
        self.assertTrue(dry["dry_run"])
        self.assertEqual(dry["selector"], "job-id=jobX")
        self.assertEqual(runner.calls, [])
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        out = hetzner_driver.down(config=self.config, state_root=self.state, job_id="jobX", confirm=True)
        self.assertTrue(out["destroyed"])
        self.assertTrue(any("delete" in c["joined"] for c in runner.calls))

    def test_down_refuses_without_confirm(self) -> None:
        hetzner_driver.COMMAND_RUNNER = _FakeRunner()
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        with self.assertRaises(hetzner_driver.HetznerDriverError):
            hetzner_driver.down(config=self.config, job_id="jobX", confirm=False)

    def test_oneshot_dry_run_shows_full_sequence_without_calls(self) -> None:
        runner = _FakeRunner()
        hetzner_driver.COMMAND_RUNNER = runner
        out = hetzner_driver.oneshot(job_dir=self._bundle(), config=self.config, state_root=self.state, dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["sequence"], ["up", "push", "run", "wait", "fetch", "down"])
        self.assertIn("trap", out["teardown"])
        self.assertEqual(runner.calls, [])

    def test_oneshot_tears_down_even_when_a_step_fails(self) -> None:
        # Fail the detached run step (ssh ... run.sh); teardown must still DELETE the server.
        runner = _FakeRunner(fail_on="run.sh")
        hetzner_driver.COMMAND_RUNNER = runner
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        with self.assertRaises(hetzner_driver.HetznerDriverError):
            hetzner_driver.oneshot(job_dir=self._bundle(), config=self.config, state_root=self.state,
                                   confirm=True, dest=self.tmp / "out")
        self.assertTrue(any("delete" in c["joined"] for c in runner.calls))  # guaranteed teardown ran
        self.assertTrue(all(DRIVER_TOKEN not in c["joined"] for c in runner.calls))  # token never on argv

    # -- Phase C: cloud-init dead-man's-switch --------------------------------

    def test_render_cloud_init_shutdown_backstop_no_token(self) -> None:
        """The rendered dead-man's-switch has the boot-relative shutdown AND the systemd
        RuntimeMaxSec backstop, no unrendered placeholders, and NO token on the server."""
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        ci = hetzner_driver.render_cloud_init(self.config, 6.0)  # max_server_hours = 6
        self.assertIn("shutdown -h +360", ci)     # 6h -> 360 min, boot-relative
        self.assertIn("RuntimeMaxSec=21600", ci)  # 6h -> 21600 s backstop
        self.assertNotIn("{{", ci)                 # fully rendered
        self.assertNotIn(DRIVER_TOKEN, ci)
        self.assertNotIn("HCLOUD_TOKEN", ci)       # no token variable planted on the server

    def test_up_dry_run_reports_dead_mans_switch(self) -> None:
        runner = _FakeRunner()
        hetzner_driver.COMMAND_RUNNER = runner
        out = hetzner_driver.up(job_dir=self._bundle(), config=self.config,
                                state_root=self.state, dry_run=True)
        self.assertTrue(out["dead_mans_switch"])
        self.assertEqual(out["cloud_init_shutdown_minutes"], 360)
        self.assertIn("--user-data-from-file", out["command"])
        self.assertEqual(runner.calls, [])  # dry-run makes no call and provisions nothing

    def test_up_user_data_override_disables_auto_switch(self) -> None:
        runner = _FakeRunner()
        hetzner_driver.COMMAND_RUNNER = runner
        ud = self.tmp / "operator-cloud-init.yaml"
        ud.write_text("#cloud-config\n", encoding="utf-8")
        out = hetzner_driver.up(job_dir=self._bundle(), config=self.config, state_root=self.state,
                                dry_run=True, user_data=str(ud))
        self.assertFalse(out["dead_mans_switch"])  # operator-supplied user-data wins
        self.assertIn(str(ud), " ".join(out["command"]))

    # -- Phase C: reconcile-before-create runaway-loop guard ------------------

    def test_up_reconcile_before_create_aborts_over_concurrent_cap(self) -> None:
        """Two live tagged servers already exist and max_concurrent_servers is 2, so one more
        would exceed the cap: `up` aborts BEFORE reserving budget or creating (fail-closed)."""
        runner = _ReaperRunner([
            {"id": 1, "name": "a", "status": "running", "labels": {"managed-by": "ai-agents-skills"}},
            {"id": 2, "name": "b", "status": "running", "labels": {"managed-by": "ai-agents-skills"}},
        ])
        hetzner_driver.COMMAND_RUNNER = runner
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        with self.assertRaises(hetzner_driver.HetznerDriverError):
            hetzner_driver.up(job_dir=self._bundle(), config=self.config,
                              state_root=self.state, confirm=True)
        self.assertFalse((self.state / "hetzner-reservations.jsonl").exists())  # no reservation
        self.assertTrue(all("create" not in c["joined"] for c in runner.calls))  # no create

    # -- Phase C: audit log ---------------------------------------------------

    def test_up_confirm_writes_provision_audit(self) -> None:
        runner = _FakeRunner()
        hetzner_driver.COMMAND_RUNNER = runner
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        hetzner_driver.up(job_dir=self._bundle(), config=self.config, state_root=self.state, confirm=True)
        records = hetzner_audit.read(self.state)
        prov = [r for r in records if r["event"] == "provision"]
        self.assertTrue(prov)
        self.assertEqual(prov[0]["server_type"], "cpx62")
        self.assertGreater(prov[0]["est_eur"], 0.0)
        self.assertTrue(prov[0]["dead_mans_switch"])
        self.assertNotIn(DRIVER_TOKEN, (self.state / hetzner_audit.AUDIT_FILENAME).read_text())

    def test_down_confirm_writes_destroy_audit(self) -> None:
        runner = _FakeRunner()
        hetzner_driver.COMMAND_RUNNER = runner
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        hetzner_driver.down(config=self.config, state_root=self.state, job_id="jobX", confirm=True)
        records = hetzner_audit.read(self.state)
        self.assertTrue(any(r["event"] == "destroy" for r in records))

    def test_audit_redacts_token_even_if_embedded(self) -> None:
        """Belt-and-braces redaction: even a record that embedded the token is redacted before
        the write, so the token never reaches disk."""
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN
        hetzner_audit.append(self.state, {"event": "provision", "leak": f"x {DRIVER_TOKEN} y"})
        raw = (self.state / hetzner_audit.AUDIT_FILENAME).read_text()
        self.assertNotIn(DRIVER_TOKEN, raw)
        self.assertIn("<REDACTED_HCLOUD_TOKEN>", raw)
        self.assertEqual(hetzner_audit.read(self.state)[0]["event"], "provision")


class HetznerReaperTests(unittest.TestCase):
    """Offline, credential-free tests for the detached reaper (plan section 6, Arm 2). Every
    hcloud call is intercepted through the driver's COMMAND_RUNNER, so no server is ever
    provisioned; the reap predicate, the delete set, dry-run, the kill switch, and redacted
    audit are all exercised with a mocked server list."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = _config(self.tmp)
        self.state = self.tmp / "state"
        self._prev_runner = hetzner_driver.COMMAND_RUNNER
        self._prev_token = os.environ.pop("HCLOUD_TOKEN", None)
        os.environ["HCLOUD_TOKEN"] = DRIVER_TOKEN

    def tearDown(self) -> None:
        hetzner_driver.COMMAND_RUNNER = self._prev_runner
        if self._prev_token is not None:
            os.environ["HCLOUD_TOKEN"] = self._prev_token
        else:
            os.environ.pop("HCLOUD_TOKEN", None)
        self._tmp.cleanup()

    def _servers(self, now: float) -> list[dict]:
        base = {"managed-by": "ai-agents-skills", "ttl": "6h"}
        return [
            {"id": 1, "name": "keep", "status": "running", "created": _iso(now - 60),
             "labels": {**base, "job-id": "J-active"}},
            {"id": 2, "name": "off", "status": "off", "created": _iso(now - 60),
             "labels": {**base, "job-id": "J-active"}},
            {"id": 3, "name": "old", "status": "running", "created": _iso(now - 7 * 3600),
             "labels": {**base, "job-id": "J-active"}},
            {"id": 4, "name": "orphan", "status": "running", "created": _iso(now - 60),
             "labels": {**base, "job-id": "J-ghost"}},
        ]

    def test_reaper_deletes_expired_poweredoff_orphans_keeps_active(self) -> None:
        now = 2_000_000.0
        runner = _ReaperRunner(self._servers(now))
        hetzner_driver.COMMAND_RUNNER = runner
        budget_ledger.reserve(self.state, "hetzner", "J-active", 1.0, "eur")  # J-active is live
        out = hetzner_reaper.reap(config=self.config, state_root=self.state, now=now)
        self.assertEqual(sorted(runner.deleted), ["2", "3", "4"])  # off / past-TTL / orphan
        self.assertNotIn("1", runner.deleted)                       # active job kept
        self.assertEqual(out["scanned"], 4)
        reasons = {d["server"]: d["reasons"] for d in out["deleted"]}
        self.assertIn("powered_off", reasons["2"])
        self.assertIn("past_ttl", reasons["3"])
        self.assertIn("orphaned", reasons["4"])

    def test_reaper_dry_run_deletes_nothing(self) -> None:
        now = 2_000_000.0
        runner = _ReaperRunner(self._servers(now))
        hetzner_driver.COMMAND_RUNNER = runner
        out = hetzner_reaper.reap(config=self.config, state_root=self.state, now=now, dry_run=True)
        self.assertEqual(runner.deleted, [])
        self.assertTrue(all("delete" not in c["joined"] for c in runner.calls))
        self.assertTrue(out["planned"])  # it still reports what WOULD be reaped

    def test_reaper_stale_heartbeat(self) -> None:
        now = 2_000_000.0
        servers = [{"id": 9, "name": "hb", "status": "running", "created": _iso(now - 60),
                    "labels": {"managed-by": "ai-agents-skills", "job-id": "J-active",
                               "ttl": "6h", "heartbeat": str(now - 3600)}}]
        runner = _ReaperRunner(servers)
        hetzner_driver.COMMAND_RUNNER = runner
        budget_ledger.reserve(self.state, "hetzner", "J-active", 1.0, "eur")
        out = hetzner_reaper.reap(config=self.config, state_root=self.state, now=now,
                                  heartbeat_max_seconds=900.0)
        self.assertEqual(runner.deleted, ["9"])
        self.assertIn("stale_heartbeat", out["deleted"][0]["reasons"])

    def test_reaper_orphan_check_off_without_ledger(self) -> None:
        """Without a state root the reaper cannot know the active-jobs set, so it does NOT
        treat every server as orphaned; it still enforces TTL / powered-off."""
        now = 2_000_000.0
        runner = _ReaperRunner(self._servers(now))
        hetzner_driver.COMMAND_RUNNER = runner
        out = hetzner_reaper.reap(config=self.config, state_root=None, now=now)
        self.assertNotIn("1", runner.deleted)  # not deleted as orphan
        self.assertNotIn("4", runner.deleted)  # orphan check disabled without a ledger
        self.assertEqual(sorted(runner.deleted), ["2", "3"])  # only off + past-TTL

    def test_kill_switch_deletes_all_tagged(self) -> None:
        now = 2_000_000.0
        runner = _ReaperRunner(self._servers(now))
        hetzner_driver.COMMAND_RUNNER = runner
        budget_ledger.reserve(self.state, "hetzner", "J-active", 1.0, "eur")  # active is killed too
        out = hetzner_reaper.kill_switch(config=self.config, state_root=self.state)
        self.assertEqual(sorted(runner.deleted), ["1", "2", "3", "4"])
        self.assertEqual(len(out["killed"]), 4)

    def test_reaper_writes_redacted_audit(self) -> None:
        now = 2_000_000.0
        runner = _ReaperRunner(self._servers(now))
        hetzner_driver.COMMAND_RUNNER = runner
        hetzner_reaper.reap(config=self.config, state_root=self.state, now=now)
        records = hetzner_audit.read(self.state)
        self.assertTrue(records)
        self.assertTrue(all(r["event"] == "reap" for r in records))
        self.assertNotIn(DRIVER_TOKEN, (self.state / hetzner_audit.AUDIT_FILENAME).read_text())

    def test_reaper_requires_token(self) -> None:
        os.environ.pop("HCLOUD_TOKEN", None)
        hetzner_driver.COMMAND_RUNNER = _ReaperRunner([])
        with self.assertRaises(hetzner_driver.HetznerDriverError):
            hetzner_reaper.reap(config=self.config, state_root=self.state)

    def test_reaper_main_dry_run_smoke(self) -> None:
        """The detached entrypoint (what systemd/cron invoke) runs a dry-run pass and returns
        0 without deleting anything."""
        import contextlib
        import io

        runner = _ReaperRunner(self._servers(2_000_000.0))
        hetzner_driver.COMMAND_RUNNER = runner
        cfg = self.tmp / "research-compute.toml"  # written by _config in setUp
        with contextlib.redirect_stdout(io.StringIO()):  # main() is a CLI: keep its JSON off the suite
            rc = hetzner_reaper.main(["--config", str(cfg), "reap", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertEqual(runner.deleted, [])


if __name__ == "__main__":
    unittest.main()
