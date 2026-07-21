"""Offline, credential-free tests for the Kaggle routing lane and the kernel-lifecycle driver.

No network call and NO live Kaggle call (Kaggle ToS): the planner cascade, the free-CPU /
weekly-GPU-hour-cap probe, the GPU budget gate, the multi-run resume loop, and the concurrent
fan-out are all exercised with injected liveness / resource snapshots, a mocked `kaggle` CLI,
and a mocked kagglehub-validate hook. Auth is the new single Kaggle API token
(KAGGLE_API_TOKEN, or ~/.kaggle/access_token), NOT the legacy KAGGLE_USERNAME + KAGGLE_KEY pair;
KAGGLE_CONFIG_DIR is pinned to a temp dir so no real access_token file is ever read. Mirrors the
subprocess / temp-workspace pattern of test_hetzner_research_compute.

Matrix covered (plan §8.2): Kaggle x {CPU, GPU} x {auto-signal, explicit-request} x
{available, GPU-quota-exhausted -> fall-through}, plus the multi-run resume loop (a 2-run job
resumes from checkpoint), the ~5-concurrent fan-out, and the new routing order (a CPU job now
prefers Kaggle over the paid lanes; local still first).
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # never write __pycache__ into the canonical runtime tree

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from installer.ai_agents_skills.runtime import RUNTIME_SOURCE_ROOT

WORKSPACE = RUNTIME_SOURCE_ROOT / "workspace"
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

# (a) imports clean -- a failure here fails the whole module, which is the signal.
from research_compute import budget_ledger, kaggle_backend, planner  # noqa: E402
from research_compute import config as rc_config  # noqa: E402

# The kernel-lifecycle driver ships under the skill runtime dir; add it to the path so the
# offline driver tests can import it directly (mirrors how the wrapper wires sys.path).
SKILL_DIR = RUNTIME_SOURCE_ROOT / "skills" / "kaggle-research-compute"
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))
import kaggle_driver  # noqa: E402


# Base config: the new default order with Kaggle at position 2, Kaggle enabled, and a GHA lane
# so the fall-through corners can be observed. The GHA repo worst case is 200 minutes (10% of
# the 2000 included) so the cumulative-cap arithmetic stays readable.
CONFIG_TOML = """\
install_id = "test-install"
platform = "linux"
broker_state_root = "state"
routing_order = ["local", "kaggle", "modal", "hetzner", "gha"]

[local]
danger_load_frac = 0.5
session_headroom_frac = 0.15
soft = 0.4
hard = 0.55
local_wall_budget_h = 2.0

[kaggle]
enabled = true
weekly_gpu_hours_cap = 18.0
max_runs = 5
concurrency = 5
session_hours = 12.0
kernel_cores = 4
kernel_ram_gb = 32.0

[hetzner]
enabled = true
location = "nbg1"
allowed_locations = ["nbg1", "hel1", "sin"]
max_eur_per_job = 3.0
max_eur_per_day = 3.0
max_server_hours = 6.0
max_concurrent_servers = 2

[hetzner.server_types.cpx62]
vcpu = 16
ram_gb = 32
arch = "x86"
eur_per_hour = 0.1550

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
max_cpu = 16
max_memory_mb = 16384
"""

# Kaggle-disabled variant: Kaggle absent from routing_order entirely, to prove the lane is
# skipped (kaggle_not_in_routing_order) and a CPU job falls to Modal.
NO_KAGGLE_CONFIG_TOML = CONFIG_TOML.replace(
    'routing_order = ["local", "kaggle", "modal", "hetzner", "gha"]',
    'routing_order = ["local", "modal", "hetzner", "gha"]')


def _with_liveness_defaults(resources: dict | None) -> dict:
    """Every offline `plan` test carries a liveness snapshot so the planner is fully
    deterministic and NEVER touches the network across the subprocess boundary: the Kaggle
    probe reads resources['liveness']['kaggle'] (default usable) instead of calling the real
    kaggle CLI, and Modal / Hetzner / GHA liveness is injected per scenario. Any lane a test
    passes in overrides the default."""
    snap = dict(resources or {})
    liveness = dict(snap.get("liveness", {}))
    liveness.setdefault("kaggle", {"usable": True, "reason": "injected-usable"})
    liveness.setdefault("hetzner", {"usable": True, "reason": "injected-usable"})
    if "gha" in liveness:
        gha_liveness = dict(liveness["gha"])
        gha_liveness.setdefault("authenticated", True)
        gha_liveness.setdefault("repo_private", True)
        liveness["gha"] = gha_liveness
    snap["liveness"] = liveness
    return snap


def _make_workspace(tmp: Path, resources: dict | None = None, *, config_toml: str = CONFIG_TOML) -> Path:
    ws = tmp / "ws"
    (ws / "config").mkdir(parents=True)
    (ws / "config" / "research-compute.toml").write_text(config_toml, encoding="utf-8")
    (ws / ".codex_resources.json").write_text(
        json.dumps(_with_liveness_defaults(resources)), encoding="utf-8")
    return ws


def _run_broker(ws: Path, command: str, job: dict, *, creds: bool = True) -> dict:
    """Run the real router in a subprocess. The API token is set only in the env (never argv);
    NO live Kaggle call is made because the Kaggle probe reads the injected liveness snapshot.
    KAGGLE_CONFIG_DIR points at the temp workspace (which has no access_token) so the box's real
    ~/.kaggle/access_token is never read and only KAGGLE_API_TOKEN controls token presence."""
    (ws / "job.json").write_text(json.dumps(job), encoding="utf-8")
    env = dict(os.environ)
    env["OPENCLAW_WORKSPACE"] = str(ws)
    env["PYTHONPATH"] = os.pathsep.join([str(WORKSPACE), env.get("PYTHONPATH", "")])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["KAGGLE_CONFIG_DIR"] = str(ws)
    for legacy in ("KAGGLE_USERNAME", "KAGGLE_KEY", "KAGGLE_API_TOKEN"):
        env.pop(legacy, None)
    if creds:
        env["KAGGLE_API_TOKEN"] = "offline-token-for-test"
    proc = subprocess.run(
        [sys.executable, "-m", "research_compute", command, str(ws / "job.json")],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _plan(ws: Path, job: dict, *, creds: bool = True) -> dict:
    return _run_broker(ws, "plan", job, creds=creds)


def _config(tmp: Path, text: str = CONFIG_TOML) -> rc_config.BrokerConfig:
    tmp.mkdir(parents=True, exist_ok=True)
    cfg = tmp / "research-compute.toml"
    cfg.write_text(text, encoding="utf-8")
    return rc_config.load_config(cfg)


HEAVY_CPU_JOB = {"task_family": "enumeration",
                 "constraints": {"cpu": 12, "memory_mb": 8192, "parallelism": 12,
                                 "core_hours": 40, "resource_class": "cpu"}}


class KaggleRoutingTests(unittest.TestCase):
    """End-to-end routing through the real subprocess planner (plan §8.2 CPU corners)."""

    def test_a_imports_clean(self) -> None:
        for mod in (planner, rc_config, kaggle_backend, budget_ledger, kaggle_driver):
            self.assertIsNotNone(mod)

    def test_cpu_job_prefers_kaggle_over_paid_lanes(self) -> None:
        """New routing order local>kaggle>modal>hetzner>gha: a CPU-heavy job that exceeds
        local goes to KAGGLE FIRST (free CPU) even though Modal AND Hetzner are also
        available -- Kaggle is the preferred first offload tier."""
        resources = {"liveness": {"kaggle": {"usable": True},
                                  "modal": {"ready": True, "usable": True},
                                  "hetzner": {"usable": True}}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            plan = _plan(ws, HEAVY_CPU_JOB, creds=True)["plan"]
            self.assertEqual(plan["decision"], "kaggle")
            self.assertEqual(plan["budget_unit"], "free")
            trail = plan["routing_trail"]
            self.assertEqual(trail[0]["backend"], "kaggle")
            self.assertTrue(trail[0]["available"])
            # Modal was never consulted -- Kaggle won at position 2, ahead of the paid lanes.
            self.assertNotIn("modal", [t["backend"] for t in trail])

    def test_submit_selected_kaggle_reports_lane_driver_handoff(self) -> None:
        """The umbrella submit path must not mislabel a Kaggle plan as local execution."""
        resources = {"liveness": {"kaggle": {"usable": True},
                                  "modal": {"ready": True, "usable": True}}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            result = _run_broker(ws, "submit", HEAVY_CPU_JOB, creds=True)
            self.assertEqual(result["status"], "external_driver_required")
            self.assertEqual(result["plan"]["backend"], "kaggle")
            self.assertIn("kaggle-research-compute", result["message"])

    def test_cpu_local_still_first(self) -> None:
        """Local remains position 1: a small job whose full-run load stays under the ceiling
        runs local, never Kaggle."""
        resources = {"cpu": {"logical_cores": 16}, "memory": {"total_gb": 32},
                     "disk": {"available_gb": 100}, "gpu": {"total_gpus": 0},
                     "load": {"load_1m": 0.2},
                     "liveness": {"kaggle": {"usable": True}, "modal": {"usable": True}}}
        job = {"task_family": "generic",
               "constraints": {"cpu": 1, "memory_mb": 512, "parallelism": 1, "core_hours": 0.5}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            plan = _plan(ws, job, creds=True)["plan"]
            self.assertEqual(plan["decision"], "local_cpu")

    def test_cpu_kaggle_unavailable_without_creds_falls_to_modal(self) -> None:
        """With no Kaggle credentials the lane is unavailable; the CPU job falls through
        order-driven to Modal (the next available lane)."""
        resources = {"liveness": {"modal": {"ready": True, "usable": True}}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            plan = _plan(ws, HEAVY_CPU_JOB, creds=False)["plan"]
            self.assertTrue(plan["decision"].startswith("modal_"))
            trail = {t["backend"]: t for t in plan["routing_trail"]}
            self.assertFalse(trail["kaggle"]["available"])
            self.assertTrue(trail["modal"]["available"])

    def test_cpu_kaggle_liveness_unusable_falls_to_modal(self) -> None:
        """Credentials present but the account-usable probe fails (auth failure): the lane is
        unavailable and the CPU job falls through to Modal."""
        resources = {"liveness": {"kaggle": {"usable": False, "reason": "kaggle_auth_failed"},
                                  "modal": {"ready": True, "usable": True}}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            plan = _plan(ws, HEAVY_CPU_JOB, creds=True)["plan"]
            self.assertTrue(plan["decision"].startswith("modal_"))
            trail = {t["backend"]: t for t in plan["routing_trail"]}
            self.assertFalse(trail["kaggle"]["available"])

    def test_cpu_kaggle_not_in_order_is_skipped(self) -> None:
        """With Kaggle absent from routing_order the lane is skipped entirely and a CPU job
        falls to Modal (the pre-Kaggle behaviour, order-driven)."""
        resources = {"liveness": {"modal": {"ready": True, "usable": True}}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources, config_toml=NO_KAGGLE_CONFIG_TOML)
            plan = _plan(ws, HEAVY_CPU_JOB, creds=True)["plan"]
            self.assertTrue(plan["decision"].startswith("modal_"))
            self.assertNotIn("kaggle", [t["backend"] for t in plan["routing_trail"]])

    def test_explicit_kaggle_override_bypasses_routing_order_for_cpu(self) -> None:
        """An explicit override selects Kaggle even when it is absent from routing_order."""
        resources = {"liveness": {"kaggle": {"usable": True},
                                  "modal": {"ready": True, "usable": True}}}
        job = {**HEAVY_CPU_JOB, "policy": {"backend": "kaggle"}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(
                Path(tmp), resources=resources, config_toml=NO_KAGGLE_CONFIG_TOML
            )
            plan = _plan(ws, job, creds=True)["plan"]
            self.assertTrue(plan["accepted"])
            self.assertEqual(plan["decision"], "kaggle")
            self.assertEqual(plan["backend"], "kaggle")
            self.assertEqual(
                [entry["backend"] for entry in plan["routing_trail"]], ["kaggle"]
            )

    def test_explicit_kaggle_override_selects_gpu_when_requested(self) -> None:
        resources = {"gpu": {"total_gpus": 1},
                     "liveness": {"kaggle": {"usable": True},
                                  "modal": {"ready": True, "usable": True}}}
        job = {
            "task_family": "generic",
            "policy": {"backend": "kaggle", "gpu": True},
            "constraints": {
                "cpu": 2,
                "memory_mb": 2048,
                "parallelism": 2,
                "core_hours": 4,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            plan = _plan(ws, job, creds=True)["plan"]
            self.assertTrue(plan["accepted"])
            self.assertEqual(plan["decision"], "kaggle_gpu")
            self.assertEqual(plan["backend"], "kaggle")
            self.assertTrue(plan["within_weekly_gpu_cap"])

    def test_explicit_kaggle_override_fails_closed_when_unavailable(self) -> None:
        resources = {"liveness": {
            "kaggle": {"usable": False, "reason": "kaggle_auth_failed"},
            "modal": {"ready": True, "usable": True},
        }}
        job = {**HEAVY_CPU_JOB, "policy": {"backend": "kaggle"}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            plan = _plan(ws, job, creds=True)["plan"]
            self.assertFalse(plan["accepted"])
            self.assertEqual(plan["decision"], "rejected")
            self.assertIn("kaggle_unavailable", plan["risk_flags"])
            self.assertNotIn("modal", [t["backend"] for t in plan["routing_trail"]])

    def test_explicit_kaggle_gpu_pin_fails_when_weekly_cap_is_exhausted(self) -> None:
        resources = {"gpu": {"total_gpus": 0}, "liveness": {
            "kaggle": {"usable": True, "gpu_hours_used_this_week": 18.0},
            "modal": {"ready": True, "usable": True},
        }}
        job = {
            "task_family": "generic",
            "policy": {"backend": "kaggle", "gpu": True},
            "constraints": {"cpu": 2, "memory_mb": 2048, "parallelism": 2,
                            "core_hours": 4},
        }
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            plan = _plan(ws, job, creds=True)["plan"]
            self.assertFalse(plan["accepted"])
            self.assertEqual(plan["decision"], "rejected")
            self.assertIn("kaggle_unavailable", plan["risk_flags"])
            self.assertIn("cap", plan["routing_trail"][0]["reason"])
            self.assertNotIn("modal", [entry["backend"] for entry in plan["routing_trail"]])

    def test_explicit_kaggle_cpu_pin_fails_when_one_kernel_is_inadequate(self) -> None:
        resources = {"liveness": {
            "kaggle": {"usable": True},
            "modal": {"ready": True, "usable": True},
        }}
        job = {
            "task_family": "enumeration",
            "policy": {"backend": "kaggle"},
            "constraints": {"cpu": 12, "memory_mb": 64 * 1024, "parallelism": 12,
                            "core_hours": 40, "resource_class": "highmem_cpu"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            plan = _plan(ws, job, creds=True)["plan"]
            self.assertFalse(plan["accepted"])
            self.assertEqual(plan["decision"], "rejected")
            self.assertIn("kaggle_unavailable", plan["risk_flags"])
            self.assertFalse(plan["routing_trail"][0]["adequate"])

    def test_explicit_kaggle_pin_does_not_probe_unrelated_providers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            selected_kaggle = {
                "backend": "kaggle",
                "available": True,
                "adequate": True,
                "reason": "available",
                "est_runs": 1,
                "est_kernels": 1,
                "concurrency": 1,
                "session_hours": 12.0,
                "gpu_hours_est": 0.0,
                "gpu_hours_cap": 18.0,
                "within_gpu_cap": True,
            }
            with mock.patch.object(
                planner.kaggle_backend, "probe", return_value=selected_kaggle
            ), mock.patch.object(
                planner.hetzner_backend,
                "probe",
                side_effect=AssertionError("unrelated Hetzner probe"),
            ), mock.patch.object(
                planner,
                "modal_lane_available",
                side_effect=AssertionError("unrelated Modal probe"),
            ):
                plan = planner.plan_job(
                    {**HEAVY_CPU_JOB, "policy": {"backend": "kaggle"}},
                    config=config,
                    resources={},
                    modal_ready=True,
                )
            self.assertEqual(plan["decision"], "kaggle")
            self.assertEqual(
                [entry["backend"] for entry in plan["routing_trail"]], ["kaggle"]
            )

    def test_cpu_highmem_inadequate_falls_through_to_paid_lane(self) -> None:
        """A job needing more than one kernel's ~32 GB is INADEQUATE for Kaggle and falls
        through; with Modal down it lands on Hetzner (a bigger box), proving Kaggle adequacy
        gates on RAM."""
        resources = {"liveness": {"kaggle": {"usable": True},
                                  "modal": {"ready": True, "usable": False},
                                  "hetzner": {"usable": True}}}
        job = {"task_family": "enumeration",
               "constraints": {"cpu": 12, "memory_mb": 64 * 1024, "parallelism": 12,
                               "core_hours": 40, "resource_class": "highmem_cpu"}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            plan = _plan(ws, job, creds=True)["plan"]
            trail = {t["backend"]: t for t in plan["routing_trail"]}
            self.assertFalse(trail["kaggle"]["adequate"])
            self.assertNotEqual(plan["decision"], "kaggle")

    def test_local_veto_falls_through_to_kaggle(self) -> None:
        """A job small enough to classify local, whose full-run load would breach the ceiling,
        is vetoed and re-routed order-driven -- landing on KAGGLE (position 2), ahead of the
        paid lanes. The trail records the local veto first and Kaggle chosen."""
        resources = {"cpu": {"logical_cores": 8}, "memory": {"total_gb": 16},
                     "disk": {"available_gb": 100}, "gpu": {"total_gpus": 0},
                     "load": {"load_1m": 1.0},
                     "liveness": {"kaggle": {"usable": True},
                                  "modal": {"usable": True}, "hetzner": {"usable": True}}}
        job = {"task_family": "generic",
               "constraints": {"cpu": 1, "memory_mb": 1024, "parallelism": 1, "core_hours": 10}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=resources)
            plan = _plan(ws, job, creds=True)["plan"]
            self.assertEqual(plan["decision"], "kaggle")
            self.assertIn("local_self_preservation_veto", plan["risk_flags"])
            trail = plan["routing_trail"]
            self.assertEqual(trail[0]["backend"], "local")
            self.assertEqual(trail[-1]["backend"], "kaggle")


class KaggleGpuRoutingTests(unittest.TestCase):
    """GPU routing matrix (plan §8.2): {auto-signal, explicit-request} x {within-cap ->
    Kaggle-GPU, quota-exhausted -> fall through to Modal}. gpu_hours_used_this_week is injected
    to drive the weekly cap deterministically. cap = 18.0; the jobs below estimate GPU-hours
    from core_hours (core_hours / (parallelism handled elsewhere) -> here core_hours is the
    proxy)."""

    AUTO_GPU_JOB = {"task_family": "embedding",
                    "constraints": {"cpu": 2, "memory_mb": 4096, "parallelism": 2,
                                    "core_hours": 4}}
    EXPLICIT_GPU_ON_CPU_JOB = {"task_family": "enumeration", "policy": {"gpu": True},
                               "constraints": {"cpu": 12, "memory_mb": 8192, "parallelism": 12,
                                               "core_hours": 4, "resource_class": "cpu"}}

    @staticmethod
    def _res(*, gpus: int = 0, modal_usable: bool = True, gpu_hours_used: float = 0.0) -> dict:
        return {"gpu": {"total_gpus": gpus},
                "liveness": {"kaggle": {"usable": True, "gpu_hours_used_this_week": gpu_hours_used},
                             "modal": {"ready": True, "usable": modal_usable},
                             "hetzner": {"usable": True}}}

    def test_explicit_gpu_within_cap_routes_kaggle_gpu(self) -> None:
        """Explicit GPU + within the weekly cap + no local GPU -> Kaggle-GPU (free, ahead of
        Modal-GPU)."""
        res = self._res(gpus=0, modal_usable=True, gpu_hours_used=0.0)
        job = {"task_family": "generic", "policy": {"gpu": True},
               "constraints": {"cpu": 2, "memory_mb": 2048, "parallelism": 2, "core_hours": 4}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res)
            plan = _plan(ws, job, creds=True)["plan"]
            self.assertEqual(plan["decision"], "kaggle_gpu")
            self.assertEqual(plan["backend"], "kaggle")
            trail = {t["backend"]: t for t in plan["routing_trail"]}
            self.assertTrue(trail["kaggle"]["gpu_capable"])
            self.assertTrue(trail["kaggle"]["available"])
            # Modal-GPU was never reached: Kaggle won at position 2.
            self.assertNotIn("modal", trail)

    def test_explicit_gpu_quota_exhausted_falls_to_modal_gpu(self) -> None:
        """Explicit GPU but the weekly GPU-hour cap is exhausted (17.9 used + 4 est > 18) ->
        Kaggle unavailable -> falls through to Modal-GPU."""
        res = self._res(gpus=0, modal_usable=True, gpu_hours_used=17.9)
        job = {"task_family": "generic", "policy": {"gpu": True},
               "constraints": {"cpu": 2, "memory_mb": 2048, "parallelism": 2, "core_hours": 4}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res)
            plan = _plan(ws, job, creds=True)["plan"]
            self.assertEqual(plan["decision"], "modal_gpu")
            trail = {t["backend"]: t for t in plan["routing_trail"]}
            self.assertFalse(trail["kaggle"]["available"])
            self.assertIn("cap", trail["kaggle"]["reason"])
            self.assertTrue(trail["modal"]["available"])

    def test_auto_gpu_within_cap_routes_kaggle_gpu(self) -> None:
        """Auto-signalled GPU (embedding marker) within the cap, no local GPU -> Kaggle-GPU."""
        res = self._res(gpus=0, modal_usable=True, gpu_hours_used=1.0)
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res)
            plan = _plan(ws, self.AUTO_GPU_JOB, creds=True)["plan"]
            self.assertEqual(plan["decision"], "kaggle_gpu")

    def test_auto_gpu_quota_exhausted_falls_to_modal_gpu(self) -> None:
        """Auto-signalled GPU with the weekly cap exhausted -> falls through to Modal-GPU."""
        res = self._res(gpus=0, modal_usable=True, gpu_hours_used=18.0)
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res)
            plan = _plan(ws, self.AUTO_GPU_JOB, creds=True)["plan"]
            self.assertEqual(plan["decision"], "modal_gpu")

    def test_explicit_gpu_overrides_cpu_signal_to_kaggle_gpu(self) -> None:
        """Explicit-wins: a heavy CPU-classified job with policy.gpu=True routes to Kaggle-GPU,
        NOT the CPU offload it would otherwise get."""
        res = self._res(gpus=0, modal_usable=True, gpu_hours_used=0.0)
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res)
            plan = _plan(ws, self.EXPLICIT_GPU_ON_CPU_JOB, creds=True)["plan"]
            self.assertEqual(plan["decision"], "kaggle_gpu")

    def test_local_gpu_still_first_over_kaggle(self) -> None:
        """A local GPU keeps GPU work local (position 1); Kaggle is skipped."""
        res = self._res(gpus=1, modal_usable=True, gpu_hours_used=0.0)
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res)
            plan = _plan(ws, self.AUTO_GPU_JOB, creds=True)["plan"]
            self.assertEqual(plan["decision"], "local_gpu")

    def test_gpu_kaggle_exhausted_and_modal_down_rejected(self) -> None:
        """GPU x unavailable: Kaggle cap exhausted, Modal down, no local GPU -> no GPU lane
        (Hetzner has none, GHA GPU off) -> rejected, never silently run on CPU."""
        res = self._res(gpus=0, modal_usable=False, gpu_hours_used=18.0)
        job = {"task_family": "generic", "policy": {"gpu": True}, "gha_target": "sweep",
               "constraints": {"cpu": 2, "memory_mb": 2048, "parallelism": 2, "core_hours": 4,
                               "matrix_cells": 1}}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(Path(tmp), resources=res)
            plan = _plan(ws, job, creds=True)["plan"]
            self.assertEqual(plan["decision"], "rejected")
            self.assertIn("no_gpu_lane_available", plan["risk_flags"])


class KaggleConfigAndProbeTests(unittest.TestCase):
    """In-process unit tests for the config parse, the free-CPU / weekly-GPU-cap probe, the GPU
    budget gate, and the weekly usage ledger."""

    def setUp(self) -> None:
        # Pin KAGGLE_CONFIG_DIR to an empty temp dir so the box's real ~/.kaggle/access_token is
        # never read; token presence is then driven purely by KAGGLE_API_TOKEN in each test.
        self._env_tmp = tempfile.TemporaryDirectory()
        self._prev_env = {k: os.environ.get(k) for k in ("KAGGLE_CONFIG_DIR", "KAGGLE_API_TOKEN")}
        os.environ["KAGGLE_CONFIG_DIR"] = self._env_tmp.name
        os.environ.pop("KAGGLE_API_TOKEN", None)

    def tearDown(self) -> None:
        for key, prev in self._prev_env.items():
            if prev is not None:
                os.environ[key] = prev
            else:
                os.environ.pop(key, None)
        self._env_tmp.cleanup()

    def test_config_parses_kaggle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp))
            self.assertTrue(config.kaggle_enabled)
            self.assertEqual(config.routing_order, ["local", "kaggle", "modal", "hetzner", "gha"])
            self.assertEqual(config.kaggle_weekly_gpu_hours_cap, 18.0)
            self.assertEqual(config.kaggle_max_runs, 5)
            self.assertEqual(config.kaggle_concurrency, 5)
            self.assertEqual(config.kaggle_session_hours, 12.0)

    def test_config_default_routing_order_kaggle_second(self) -> None:
        self.assertEqual(rc_config.BrokerConfig.__dataclass_fields__["routing_order"].default_factory(),
                         ["local", "kaggle", "modal", "hetzner", "gha"])

    def _cfg(self, tmp: Path):
        return _config(tmp)

    def test_probe_cpu_available_free_and_ungated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(Path(tmp))
            os.environ["KAGGLE_API_TOKEN"] = "tok"
            try:
                est = {"core_hours": 40, "parallelism": 12, "peak_ram_gb": 8.0, "gpu": False}
                res = {"liveness": {"kaggle": {"usable": True}}}
                probe = kaggle_backend.probe(est, config=cfg, resources=res)
                self.assertTrue(probe["available"])
                self.assertTrue(probe["adequate"])
                self.assertEqual(probe["kind"], "cpu")
                self.assertEqual(probe["est_cost"], 0.0)
                self.assertEqual(probe["est_cost_unit"], "free")
                # CPU is quota-free: a huge injected GPU usage does not gate a CPU job.
                res2 = {"liveness": {"kaggle": {"usable": True, "gpu_hours_used_this_week": 999}}}
                self.assertTrue(kaggle_backend.probe(est, config=cfg, resources=res2)["available"])
            finally:
                os.environ.pop("KAGGLE_API_TOKEN", None)

    def test_probe_gpu_within_and_over_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(Path(tmp))
            os.environ["KAGGLE_API_TOKEN"] = "tok"
            try:
                est = {"core_hours": 4, "parallelism": 2, "peak_ram_gb": 8.0, "gpu": True}
                within = {"liveness": {"kaggle": {"usable": True, "gpu_hours_used_this_week": 10.0}}}
                self.assertTrue(kaggle_backend.probe(est, config=cfg, resources=within)["available"])
                over = {"liveness": {"kaggle": {"usable": True, "gpu_hours_used_this_week": 15.0}}}
                probe = kaggle_backend.probe(est, config=cfg, resources=over)  # 15 + 4 > 18
                self.assertFalse(probe["available"])
                self.assertFalse(probe["within_gpu_cap"])
                self.assertIn("cap", probe["reason"])
            finally:
                os.environ.pop("KAGGLE_API_TOKEN", None)

    def test_probe_highmem_inadequate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(Path(tmp))
            os.environ["KAGGLE_API_TOKEN"] = "tok"
            try:
                est = {"core_hours": 40, "parallelism": 12, "peak_ram_gb": 64.0, "gpu": False}
                probe = kaggle_backend.probe(est, config=cfg, resources={"liveness": {"kaggle": {"usable": True}}})
                self.assertFalse(probe["adequate"])
                self.assertIn("exceeds kernel RAM", probe["reason"])
            finally:
                os.environ.pop("KAGGLE_API_TOKEN", None)

    def test_probe_disabled_and_no_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(Path(tmp))
            os.environ.pop("KAGGLE_API_TOKEN", None)  # setUp already pinned KAGGLE_CONFIG_DIR empty
            est = {"core_hours": 40, "parallelism": 12, "peak_ram_gb": 8.0, "gpu": False}
            probe = kaggle_backend.probe(est, config=cfg, resources={"liveness": {"kaggle": {"usable": True}}})
            self.assertFalse(probe["available"])  # no API token
            self.assertIn("no_kaggle_api_token", probe["reason"])

    def test_token_present_env_and_access_token_file(self) -> None:
        """token_present() resolves the new token env-first (KAGGLE_API_TOKEN), then falls back to
        ~/.kaggle/access_token (honoring KAGGLE_CONFIG_DIR). The legacy username+key is ignored."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "kaggle-cfg"; cfg_dir.mkdir()
            prev = os.environ.get("KAGGLE_CONFIG_DIR")
            os.environ["KAGGLE_CONFIG_DIR"] = str(cfg_dir)
            os.environ.pop("KAGGLE_API_TOKEN", None)
            try:
                self.assertFalse(kaggle_backend.token_present())  # neither env nor file
                (cfg_dir / "access_token").write_text("file-token-value\n", encoding="utf-8")
                self.assertTrue(kaggle_backend.token_present())  # file fallback
                self.assertEqual(kaggle_backend.read_token(), "file-token-value")
                os.environ["KAGGLE_API_TOKEN"] = "env-token-value"
                self.assertEqual(kaggle_backend.read_token(), "env-token-value")  # env wins
                # Legacy username+key alone must NOT count as present.
                os.environ.pop("KAGGLE_API_TOKEN", None)
                (cfg_dir / "access_token").unlink()
                os.environ["KAGGLE_USERNAME"] = "u"; os.environ["KAGGLE_KEY"] = "k"
                self.assertFalse(kaggle_backend.token_present())
            finally:
                os.environ.pop("KAGGLE_USERNAME", None); os.environ.pop("KAGGLE_KEY", None)
                os.environ.pop("KAGGLE_API_TOKEN", None)
                if prev is not None:
                    os.environ["KAGGLE_CONFIG_DIR"] = prev

    def test_account_usable_validates_via_kagglehub_hook(self) -> None:
        """With no injected liveness, account_usable() validates the token through the mockable
        kagglehub hook (kagglehub.whoami()); no live call is made."""
        cfg = object()
        os.environ["KAGGLE_API_TOKEN"] = "tok"
        prev = kaggle_backend.KAGGLEHUB_VALIDATE
        kaggle_backend.KAGGLEHUB_VALIDATE = lambda config: {"usable": True, "username": "who", "reason": "kagglehub_validated"}
        try:
            usable, reason = kaggle_backend.account_usable(cfg, resources=None)
            self.assertTrue(usable)
            self.assertEqual(reason, "kagglehub_validated")
        finally:
            kaggle_backend.KAGGLEHUB_VALIDATE = prev
            os.environ.pop("KAGGLE_API_TOKEN", None)

    def test_gpu_budget_gate_fail_closed_over_cap_and_reserves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(Path(tmp))
            state = Path(tmp) / "state"; state.mkdir()
            est = {"core_hours": 4, "gpu": True}
            # Pre-fill the ledger near the cap so the next reservation is refused.
            kaggle_backend.reserve_gpu_hours(state, job_id="prior", gpu_hours=16.0)
            with self.assertRaises(kaggle_backend.KaggleBudgetError):
                kaggle_backend.gpu_budget_gate(job_id="j-over", estimate=est, config=cfg, state_root=state)
            # A fresh state reserves within the cap.
            state2 = Path(tmp) / "state2"; state2.mkdir()
            res = kaggle_backend.gpu_budget_gate(job_id="j-ok", estimate=est, config=cfg, state_root=state2)
            self.assertTrue(res["ok"])
            self.assertGreater(res["reserved_gpu_hours"], 0.0)
            self.assertTrue((state2 / "kaggle-gpu-usage.jsonl").exists())

    def test_gpu_ledger_weekly_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"; state.mkdir()
            now = time.time()
            # An entry 8 days old is outside the 7-day window and must not count.
            kaggle_backend.reserve_gpu_hours(state, job_id="old", gpu_hours=10.0, now=now - 8 * 24 * 3600)
            kaggle_backend.reserve_gpu_hours(state, job_id="new", gpu_hours=3.0, now=now - 3600)
            self.assertAlmostEqual(kaggle_backend.gpu_hours_used_this_week(state, now=now), 3.0)

    def test_account_usable_injection_short_circuits_hook(self) -> None:
        called = {"n": 0}

        def _boom(config, **k):  # must never run when injection is present
            called["n"] += 1
            return {"usable": True, "username": "x", "reason": "hook"}

        prev = kaggle_backend.KAGGLEHUB_VALIDATE
        kaggle_backend.KAGGLEHUB_VALIDATE = _boom
        try:
            usable, reason = kaggle_backend.account_usable(
                object(), {"liveness": {"kaggle": {"usable": False, "reason": "injected"}}})
            self.assertFalse(usable)
            self.assertEqual(reason, "injected")
            self.assertEqual(called["n"], 0)
        finally:
            kaggle_backend.KAGGLEHUB_VALIDATE = prev


class KaggleLaneUnitTests(unittest.TestCase):
    """Pure-function tests for the cascades' Kaggle branches (no subprocess, no resources)."""

    def test_remote_lane_prefers_kaggle_when_in_order(self) -> None:
        order = ["local", "kaggle", "modal", "hetzner", "gha"]
        kg = {"available": True, "adequate": True, "reason": "available",
              "est_runs": 1, "est_kernels": 1, "concurrency": 5, "session_hours": 12.0}
        hz = {"available": True, "adequate": True, "reason": "available", "server_spec": {}}
        dec, be, trail = planner.select_remote_lane(
            order=order, modal_decision="modal_cpu", gpu_signal=False,
            hz=hz, hz_in_order=True, kg=kg, kg_in_order=True,
            modal_ok=(True, "ok"), gha_ok=lambda: (True, "cap"))
        self.assertEqual((dec, be), ("kaggle", "kaggle"))
        self.assertEqual(trail[0]["backend"], "kaggle")

    def test_remote_lane_kaggle_unavailable_falls_to_modal(self) -> None:
        order = ["local", "kaggle", "modal", "hetzner", "gha"]
        kg = {"available": False, "adequate": True, "reason": "no_kaggle_credentials"}
        hz = {"available": True, "adequate": True, "reason": "available", "server_spec": {}}
        dec, be, _ = planner.select_remote_lane(
            order=order, modal_decision="modal_cpu", gpu_signal=False,
            hz=hz, hz_in_order=True, kg=kg, kg_in_order=True,
            modal_ok=(True, "ok"), gha_ok=lambda: (True, "cap"))
        self.assertEqual((dec, be), ("modal_cpu", "modal"))

    def test_gpu_lane_kaggle_before_modal_and_exhausted_skips(self) -> None:
        class Cfg:
            gha_gpu_enabled = False
        order = ["local", "kaggle", "modal", "hetzner", "gha"]
        # Within cap -> Kaggle-GPU wins ahead of Modal.
        kg_ok = {"available": True, "reason": "available", "est_runs": 1, "est_kernels": 1,
                 "concurrency": 5, "session_hours": 12.0, "gpu_hours_est": 4.0, "gpu_hours_cap": 18.0,
                 "within_gpu_cap": True}
        dec, be, _ = planner.select_gpu_lane(
            order=order, local_gpu=False, config=Cfg(), kg=kg_ok, kg_in_order=True,
            modal_ok=(True, "ok"), gha_ok=lambda: (True, "cap"))
        self.assertEqual((dec, be), ("kaggle_gpu", "kaggle"))
        # Exhausted -> Kaggle skipped -> Modal-GPU.
        kg_over = {"available": False, "reason": "gpu cap"}
        dec2, be2, trail2 = planner.select_gpu_lane(
            order=order, local_gpu=False, config=Cfg(), kg=kg_over, kg_in_order=True,
            modal_ok=(True, "ok"), gha_ok=lambda: (True, "cap"))
        self.assertEqual((dec2, be2), ("modal_gpu", "modal"))
        self.assertFalse(next(t for t in trail2 if t["backend"] == "kaggle")["available"])


def _make_bundle(tmp: Path, manifest: dict, *, with_run: bool = True) -> Path:
    bundle = tmp / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if with_run:
        (bundle / "run.sh").write_text("echo run\n", encoding="utf-8")
    return bundle


DRIVER_TOKEN = "kgtok_offline_do_not_log_placeholder"


class _FakeRunner:
    """Records every `kaggle` command and NEVER pushes a real kernel. `kernels status` returns
    complete; `kernels output` writes `units_per_output` fresh unit checkpoints into the -p
    dest (so the resume loop makes real progress); `echo_token` proves output redaction."""

    def __init__(self, *, units_per_output: int = 1, echo_token: bool = False):
        self.calls: list[dict] = []
        self.units_per_output = units_per_output
        self.echo_token = echo_token
        self._counter = 0

    def __call__(self, argv, *, env, timeout):
        joined = " ".join(argv)
        self.calls.append({"argv": list(argv), "joined": joined,
                           "env_has_token": bool(env.get("KAGGLE_API_TOKEN"))})
        if "status" in argv:
            return {"returncode": 0, "stdout": 'kernel has status "complete"', "stderr": ""}
        if "output" in argv:
            dest = Path(argv[argv.index("-p") + 1])
            dest.mkdir(parents=True, exist_ok=True)
            for _ in range(self.units_per_output):
                self._counter += 1
                (dest / f"unit-{self._counter:04d}.json").write_text("{}", encoding="utf-8")
            return {"returncode": 0, "stdout": "ok", "stderr": ""}
        stdout = f"leaked {env.get('KAGGLE_API_TOKEN')} value" if self.echo_token else "ok"
        return {"returncode": 0, "stdout": stdout, "stderr": ""}


class KaggleDriverTests(unittest.TestCase):
    """Offline, credential-free tests for the kernel-lifecycle driver. Every `kaggle` command is
    intercepted, so NO kernel is ever pushed and NO live Kaggle call is made; the credential
    guard, dry-run path, redaction, the multi-run resume loop, and the concurrent fan-out are
    all exercised offline."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config = _config(self.tmp)
        self.state = self.tmp / "state"; self.state.mkdir()
        # Pin KAGGLE_CONFIG_DIR to an empty dir so the box's real ~/.kaggle/access_token is never
        # read; presence is driven purely by KAGGLE_API_TOKEN (set by _creds()).
        self.cfg_dir = self.tmp / "kaggle-cfg"; self.cfg_dir.mkdir()
        self._prev_runner = kaggle_driver.COMMAND_RUNNER
        self._prev_validate = kaggle_backend.KAGGLEHUB_VALIDATE
        # Mock the kagglehub-validate hook: valid token -> authenticated username "tester". No
        # live call; the driver resolves the kernel/dataset owner from this.
        kaggle_backend.KAGGLEHUB_VALIDATE = lambda config, **k: {
            "usable": True, "username": "tester", "reason": "test"}
        self._prev_env = {k: os.environ.get(k)
                          for k in ("KAGGLE_CONFIG_DIR", "KAGGLE_API_TOKEN", "KAGGLE_USERNAME", "KAGGLE_KEY")}
        os.environ["KAGGLE_CONFIG_DIR"] = str(self.cfg_dir)
        for legacy in ("KAGGLE_API_TOKEN", "KAGGLE_USERNAME", "KAGGLE_KEY"):
            os.environ.pop(legacy, None)

    def tearDown(self) -> None:
        kaggle_driver.COMMAND_RUNNER = self._prev_runner
        kaggle_backend.KAGGLEHUB_VALIDATE = self._prev_validate
        for name, prev in self._prev_env.items():
            if prev is not None:
                os.environ[name] = prev
            else:
                os.environ.pop(name, None)
        self._tmp.cleanup()

    def _bundle(self, **over) -> Path:
        manifest = {"job_id": "jobX", "core_hours": 40, "parallelism": 12,
                    "memory_mb": 8192, "total_units": 6}
        manifest.update(over)
        return _make_bundle(self.tmp, manifest)

    def _creds(self) -> None:
        os.environ["KAGGLE_API_TOKEN"] = DRIVER_TOKEN

    def test_preflight_plans_without_pushing(self) -> None:
        runner = _FakeRunner()
        kaggle_driver.COMMAND_RUNNER = runner
        self._creds()
        out = kaggle_driver.preflight(job_dir=self._bundle(), config=self.config, state_root=self.state)
        self.assertEqual(out["kind"], "cpu")
        self.assertEqual(out["budget_verdict"], "free_cpu")
        self.assertEqual(out["total_units"], 6)
        self.assertFalse(out["provisioned"])
        self.assertEqual(out["cost"], "free")
        self.assertEqual(runner.calls, [])  # planning pushes nothing

    def test_run_dry_run_shows_fanout_and_loop_no_calls(self) -> None:
        runner = _FakeRunner()
        kaggle_driver.COMMAND_RUNNER = runner
        out = kaggle_driver.run(job_dir=self._bundle(), config=self.config,
                                state_root=self.state, dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["concurrency"], 5)
        self.assertEqual(len(out["first_round_kernels"]), 5)  # min(concurrency, total_units=6)
        self.assertFalse(out["provisioned"])
        self.assertEqual(runner.calls, [])  # dry-run submits nothing

    def test_push_refuses_without_creds_and_without_confirm(self) -> None:
        runner = _FakeRunner()
        kaggle_driver.COMMAND_RUNNER = runner
        with self.assertRaises(kaggle_driver.KaggleDriverError):
            kaggle_driver.push(job_dir=self._bundle(), config=self.config)  # no creds
        self._creds()
        with self.assertRaises(kaggle_driver.KaggleDriverError):
            kaggle_driver.push(job_dir=self._bundle(), config=self.config)  # no confirm
        self.assertEqual(runner.calls, [])

    def test_push_dry_run_no_call_no_creds_on_argv(self) -> None:
        runner = _FakeRunner()
        kaggle_driver.COMMAND_RUNNER = runner
        out = kaggle_driver.push(job_dir=self._bundle(), config=self.config, dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertFalse(out["enable_internet"])
        self.assertEqual(runner.calls, [])

    def test_run_multi_run_resume_loop_two_rounds(self) -> None:
        """The resume crux (plan §8.7): a 6-unit job with a 5-kernel round finishes unit 6 in a
        SECOND round, resuming from the round-1 checkpoints (a checkpoint dataset is created)."""
        runner = _FakeRunner(units_per_output=1)  # 5 units after round 0, 6 after round 1
        kaggle_driver.COMMAND_RUNNER = runner
        self._creds()
        out = kaggle_driver.run(job_dir=self._bundle(total_units=6), config=self.config,
                                state_root=self.state, confirm=True, dest=self.tmp / "res")
        self.assertEqual(out["status"], "completed")
        self.assertEqual(out["rounds_used"], 2)
        self.assertEqual(out["kernels_total"], 6)  # 5 in round 0 + 1 in round 1
        self.assertEqual(out["units_done"], 6)
        # Round 1 re-attached checkpoints via a checkpoint dataset (create then reference).
        self.assertTrue(any("datasets create" in c["joined"] for c in runner.calls))

    def test_run_concurrent_fanout_up_to_five(self) -> None:
        """The fan-out (plan §8): a 5-unit job pushes exactly 5 concurrent kernels in ONE round,
        each a distinct chunk index -- respecting the ~5 concurrency cap."""
        runner = _FakeRunner(units_per_output=1)
        kaggle_driver.COMMAND_RUNNER = runner
        self._creds()
        out = kaggle_driver.run(job_dir=self._bundle(total_units=5), config=self.config,
                                state_root=self.state, confirm=True, dest=self.tmp / "res")
        self.assertEqual(out["status"], "completed")
        self.assertEqual(out["rounds_used"], 1)
        push_calls = [c for c in runner.calls if "kernels push" in c["joined"]]
        self.assertEqual(len(push_calls), 5)

    def test_run_single_round_when_fits(self) -> None:
        runner = _FakeRunner(units_per_output=1)
        kaggle_driver.COMMAND_RUNNER = runner
        self._creds()
        out = kaggle_driver.run(job_dir=self._bundle(total_units=3), config=self.config,
                                state_root=self.state, confirm=True, dest=self.tmp / "res")
        self.assertEqual(out["rounds_used"], 1)
        self.assertEqual(out["kernels_total"], 3)
        # A single round needs no checkpoint dataset (no resume).
        self.assertFalse(any("datasets" in c["joined"] for c in runner.calls))

    def test_run_bounded_by_max_runs(self) -> None:
        """The loop is bounded: a large job that cannot finish within max_runs rounds stops and
        reports incomplete_max_runs rather than pushing kernels forever."""
        runner = _FakeRunner(units_per_output=1)
        kaggle_driver.COMMAND_RUNNER = runner
        self._creds()
        out = kaggle_driver.run(job_dir=self._bundle(total_units=100), config=self.config,
                                state_root=self.state, confirm=True, dest=self.tmp / "res",
                                max_runs=2)
        self.assertEqual(out["status"], "incomplete_max_runs")
        self.assertEqual(out["rounds_used"], 2)

    def test_run_gpu_reserves_weekly_gpu_hours(self) -> None:
        runner = _FakeRunner(units_per_output=3)  # finish a 3-unit GPU job in one round
        kaggle_driver.COMMAND_RUNNER = runner
        self._creds()
        out = kaggle_driver.run(job_dir=self._bundle(total_units=3, gpu=True, core_hours=4),
                                config=self.config, state_root=self.state, confirm=True,
                                dest=self.tmp / "res")
        self.assertEqual(out["kind"], "gpu")
        self.assertEqual(out["status"], "completed")
        self.assertIsNotNone(out["gpu_reservation"])
        self.assertTrue((self.state / "kaggle-gpu-usage.jsonl").exists())
        self.assertGreater(kaggle_backend.gpu_hours_used_this_week(self.state), 0.0)

    def test_run_gpu_refused_when_weekly_cap_exhausted(self) -> None:
        runner = _FakeRunner()
        kaggle_driver.COMMAND_RUNNER = runner
        self._creds()
        kaggle_backend.reserve_gpu_hours(self.state, job_id="prior", gpu_hours=18.0)  # exhaust
        with self.assertRaises(kaggle_driver.KaggleDriverError):
            kaggle_driver.run(job_dir=self._bundle(total_units=3, gpu=True, core_hours=4),
                              config=self.config, state_root=self.state, confirm=True,
                              dest=self.tmp / "res")
        self.assertFalse(any("kernels push" in c["joined"] for c in runner.calls))  # never pushed

    def test_run_token_redaction_and_env_only(self) -> None:
        runner = _FakeRunner(units_per_output=3, echo_token=True)
        kaggle_driver.COMMAND_RUNNER = runner
        self._creds()
        out = kaggle_driver.run(job_dir=self._bundle(total_units=3), config=self.config,
                                state_root=self.state, confirm=True, dest=self.tmp / "res")
        blob = json.dumps(out)
        self.assertNotIn(DRIVER_TOKEN, blob)  # token never surfaced
        # The API token travelled only in the env, never on argv.
        self.assertTrue(all(DRIVER_TOKEN not in c["joined"] for c in runner.calls))
        self.assertTrue(all(c["env_has_token"] for c in runner.calls))

    def test_run_refuses_without_confirm(self) -> None:
        runner = _FakeRunner()
        kaggle_driver.COMMAND_RUNNER = runner
        self._creds()
        with self.assertRaises(kaggle_driver.KaggleDriverError):
            kaggle_driver.run(job_dir=self._bundle(), config=self.config, state_root=self.state)
        self.assertEqual(runner.calls, [])

    def test_status_wait_fetch_offline(self) -> None:
        runner = _FakeRunner()
        kaggle_driver.COMMAND_RUNNER = runner
        self._creds()
        ref = "tester/aas-jobx-r0-c0"
        self.assertEqual(kaggle_driver.status(kernel=ref, config=self.config)["status"], "complete")
        self.assertEqual(kaggle_driver.wait(kernel=ref, config=self.config)["status"], "complete")
        dest = self.tmp / "out"
        fetched = kaggle_driver.fetch(kernel=ref, config=self.config, dest=dest)
        self.assertEqual(fetched["fetched_to"], str(dest))

    def test_doctor_offline_no_network(self) -> None:
        out = kaggle_driver.doctor(self.config)
        self.assertTrue(out["kaggle_enabled"])
        self.assertIn("reaper", out)
        self.assertIn("none needed", out["reaper"])
        self.assertEqual(out["network_probe"], "skipped (doctor performs no network calls)")
        self.assertIn("KAGGLE_API_TOKEN", out["confirm_gate"])

    def test_bootstrap_validates_and_primes_via_kagglehub(self) -> None:
        """bootstrap validates/primes via the mockable kagglehub hook: with a token present it
        reports the account usable + the resolved username. No live call is made."""
        self._creds()
        out = kaggle_driver.bootstrap(self.config)
        self.assertTrue(out["api_token_present"])
        self.assertTrue(out["account"]["usable"])
        self.assertEqual(out["account"]["username"], "tester")
        # doctor still stays offline.
        self.assertEqual(out["doctor"]["network_probe"], "skipped (doctor performs no network calls)")

    def test_bootstrap_without_token_reports_absent(self) -> None:
        out = kaggle_driver.bootstrap(self.config)  # setUp cleared the token
        self.assertFalse(out["api_token_present"])
        self.assertFalse(out["account"]["usable"])
        self.assertEqual(out["account"]["reason"], "no_kaggle_api_token")


if __name__ == "__main__":
    unittest.main()
