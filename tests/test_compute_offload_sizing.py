"""Sizing correctness across the offload lanes.

These cover the five defects found by running a real job on both lanes: a job must
not be admitted against hardware it does not fit, and the numbers the lanes report
before and after a run must describe what actually happens.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True  # never write __pycache__ into the canonical runtime tree

from installer.ai_agents_skills.runtime import RUNTIME_SOURCE_ROOT

WORKSPACE = RUNTIME_SOURCE_ROOT / "workspace"
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))
KAGGLE_SKILL = RUNTIME_SOURCE_ROOT / "skills" / "kaggle-research-compute"
if str(KAGGLE_SKILL) not in sys.path:
    sys.path.insert(0, str(KAGGLE_SKILL))

import kaggle_driver  # noqa: E402
from research_compute import hetzner_backend, kaggle_backend, modal_backend  # noqa: E402
from research_compute import planner  # noqa: E402
from research_compute.planner import build_estimate, plan_job  # noqa: E402


class _Cfg:
    routing_order = ["local", "kaggle", "modal", "hetzner", "gha"]
    per_job_cost_cap_usd = 50.0
    modal_environment = "main"
    deployment_alias = "test"
    gha_enabled = False
    gha_repos: dict = {}
    gha_gpu_enabled = False
    kaggle_enabled = True
    kaggle_kernel_cores = 4
    kaggle_kernel_ram_gb = 32.0
    kaggle_session_hours = 12.0
    kaggle_max_runs = 5
    kaggle_concurrency = 5
    kaggle_weekly_gpu_hours_cap = 18.0
    hetzner_enabled = False
    hetzner_max_eur_per_job = 3.0
    hetzner_max_eur_per_day = 3.0
    hetzner_max_server_hours = 6.0
    hetzner_max_concurrent_servers = 2
    hetzner_location = "nbg1"
    hetzner_allowed_locations = ["nbg1"]
    hetzner_server_types: dict = {}
    functions = modal_backend  # replaced per-test below


class _Functions:
    modal_cpu = "run_cpu_job"
    modal_highmem_cpu = "run_highmem_job"
    modal_gpu = "run_gpu_job"
    modal_sandbox_experimental = "run_sandbox_job"


_Cfg.functions = _Functions()


def _resources(**kwargs):
    base = {
        "cpu": {"logical_cores": 4},
        "memory": {"total_gb": 16},
        "disk": {"available_gb": 50},
        "gpu": {"total_gpus": 0},
        "liveness": {
            "modal": {"ready": True, "usable": True},
            "kaggle": {"usable": True, "reason": "injected-usable"},
            "hetzner": {"usable": True, "reason": "injected-usable"},
        },
    }
    base.update(kwargs)
    return base


def _offline_creds(empty_kaggle_dir: Path):
    return mock.patch.dict(
        os.environ,
        {
            "KAGGLE_API_TOKEN": "offline-token-for-test",
            "KAGGLE_CONFIG_DIR": str(empty_kaggle_dir),
        },
        clear=False,
    )


def _plan(job, *, modal_ready: bool = False):
    with tempfile.TemporaryDirectory() as tmp:
        legacy = {k: os.environ.pop(k) for k in ("KAGGLE_USERNAME", "KAGGLE_KEY") if k in os.environ}
        try:
            with _offline_creds(Path(tmp)):
                return plan_job(job, config=_Cfg(), resources=_resources(),
                                modal_ready=modal_ready)
        finally:
            os.environ.update(legacy)


class UnknownManifestKeyTests(unittest.TestCase):
    """An unrecognized top-level key must never be dropped silently: the resource
    block lands under `constraints`, and a job that spells it otherwise would
    otherwise be planned as though it requested nothing."""

    def test_unknown_top_level_key_is_rejected(self) -> None:
        job = {
            "job_id": "unknown-key",
            "task_family": "enumeration",
            "policy": {"backends": ["modal"]},
            # The natural wrong guess: the Kaggle *bundle* manifest uses a flat
            # resource block, so callers reach for `estimate` on the broker job.
            "estimate": {"cores": 64, "memory_mb": 1048576, "core_hours": 0.5},
            "payload": {"python_source": "def main():\n    return {}\n", "entrypoint": "main"},
        }
        plan = _plan(job)
        self.assertFalse(plan["accepted"], plan)
        self.assertIn("unknown_manifest_key", plan.get("risk_flags", []))
        joined = " ".join(plan.get("rejection_reasons", []) or []) + plan.get("reasoning_summary", "")
        self.assertIn("estimate", joined)
        self.assertIn("constraints", joined)

    def test_a_normalized_job_still_plans(self) -> None:
        """`normalize_job` stamps environment_name/deployment_alias/provenance onto the
        job before the CLI plans it, so the allowlist must admit them."""
        from research_compute.planner import normalize_job

        job = normalize_job({
            "task_family": "enumeration",
            "policy": {"backends": ["kaggle"]},
            "constraints": {"cores": 4, "memory_mb": 8192, "core_hours": 1.0},
            "payload": {"python_source": "def main():\n    return {}\n", "entrypoint": "main"},
        }, config=_Cfg())
        plan = _plan(job)
        self.assertTrue(plan["accepted"], plan)

    def test_known_keys_still_plan(self) -> None:
        job = {
            "job_id": "known-keys",
            "task_family": "enumeration",
            "task_type": "search",
            "template": "python_source",
            "template_version": "v1",
            "policy": {"backends": ["kaggle"]},
            "constraints": {"cores": 4, "memory_mb": 8192, "core_hours": 1.0},
            "payload": {"python_source": "def main():\n    return {}\n", "entrypoint": "main"},
        }
        plan = _plan(job)
        self.assertTrue(plan["accepted"], plan)
        self.assertNotIn("unknown_manifest_key", plan.get("risk_flags", []))


class ModalCapacityFitTests(unittest.TestCase):
    """Modal adequacy must compare the request against the DECLARED capacity of the
    function the decision maps to, not merely probe that the API is reachable."""

    def test_capacity_table_matches_deployed_decorators(self) -> None:
        """The table is the single source of truth modal_app.py builds from."""
        src = (WORKSPACE / "research_compute" / "modal_app.py").read_text(encoding="utf-8")
        self.assertIn("FUNCTION_CAPACITY", src, "modal_app must build decorators from the table")
        for name in ("run_cpu_job", "run_highmem_job", "run_gpu_job", "run_sandbox_job"):
            self.assertIn(name, modal_backend.FUNCTION_CAPACITY)

    def test_oversized_request_does_not_fit(self) -> None:
        ok, reason = modal_backend.capacity_fit(
            {"peak_ram_gb": 1024.0, "parallelism": 64}, "modal_highmem_cpu", _Cfg())
        self.assertFalse(ok)
        self.assertIn("1024", reason)

    def test_right_sized_request_fits(self) -> None:
        ok, _ = modal_backend.capacity_fit(
            {"peak_ram_gb": 6.0, "parallelism": 4}, "modal_cpu", _Cfg())
        self.assertTrue(ok)

    def test_oversubscribed_cores_are_reported_not_vetoed(self) -> None:
        """Modal's `cpu=` is a reserved floor, not a cap: more workers than cores runs
        slower but completes, so it belongs in the trail rather than in a veto."""
        ok, reason = modal_backend.capacity_fit(
            {"peak_ram_gb": 6.0, "parallelism": 12}, "modal_cpu", _Cfg())
        self.assertTrue(ok)
        self.assertIn("cores_oversubscribed=12>4", reason)

    def test_plan_rejects_job_larger_than_every_modal_function(self) -> None:
        job = {
            "job_id": "too-big",
            "task_family": "enumeration",
            "policy": {"backends": ["modal"]},
            "constraints": {"cores": 64, "memory_mb": 1048576, "core_hours": 0.5},
            "payload": {"python_source": "def main():\n    return {}\n", "entrypoint": "main"},
        }
        plan = _plan(job)
        modal_hops = [t for t in plan.get("routing_trail", []) if t.get("backend") == "modal"]
        self.assertTrue(modal_hops, plan)
        self.assertFalse(modal_hops[0]["adequate"], modal_hops)
        self.assertFalse(plan["accepted"], plan)

    def test_explicit_backend_override_still_checks_capacity(self) -> None:
        """`policy.backend` forces the lane but must not forge adequacy: an override that
        does not fit is an OOM after the job is accepted, which is the failure the
        capacity check exists to prevent."""
        job = {
            "job_id": "override-too-big",
            "task_family": "enumeration",
            "policy": {"backend": "modal"},
            "constraints": {"cores": 4, "memory_mb": 204800, "core_hours": 0.5},
            "payload": {"python_source": "def main():\n    return {}\n", "entrypoint": "main"},
        }
        plan = _plan(job, modal_ready=True)
        modal_hops = [t for t in plan.get("routing_trail", []) if t.get("backend") == "modal"]
        self.assertTrue(modal_hops, plan)
        self.assertFalse(modal_hops[0]["adequate"], modal_hops)
        self.assertIn("modal_capacity_exceeded", modal_hops[0]["reason"])
        self.assertFalse(plan["accepted"], plan)
        self.assertIn("modal_capacity_exceeded", plan["risk_flags"])

    def test_explicit_backend_override_that_fits_is_accepted(self) -> None:
        """The override arm must keep working for a job the function can actually hold."""
        job = {
            "job_id": "override-fits",
            "task_family": "enumeration",
            "policy": {"backend": "modal"},
            "constraints": {"cores": 4, "memory_mb": 4096, "core_hours": 0.5},
            "payload": {"python_source": "def main():\n    return {}\n", "entrypoint": "main"},
        }
        plan = _plan(job, modal_ready=True)
        modal_hops = [t for t in plan.get("routing_trail", []) if t.get("backend") == "modal"]
        self.assertTrue(modal_hops, plan)
        self.assertTrue(modal_hops[0]["adequate"], modal_hops)
        self.assertTrue(plan["accepted"], plan)


class KaggleUnitAccountingTests(unittest.TestCase):
    """The kernel writes checkpoints to $OUT (/kaggle/working/out) and `kaggle kernels
    output -p DEST` preserves that tree, so they arrive at <out_dir>/out/."""

    def test_units_done_sees_checkpoints_under_the_kernel_out_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            nested = out_dir / "out"
            nested.mkdir(parents=True)
            for i in range(5):
                (nested / f"unit-{i:04d}.json").write_text("{}", encoding="utf-8")
            self.assertEqual(kaggle_driver.units_done(out_dir), 5)

    def test_units_done_still_reads_a_flat_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            out_dir.mkdir(parents=True)
            (out_dir / "unit-0000.json").write_text("{}", encoding="utf-8")
            self.assertEqual(kaggle_driver.units_done(out_dir), 1)

    def test_units_done_does_not_double_count_a_refetched_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            (out_dir / "out").mkdir(parents=True)
            (out_dir / "unit-0000.json").write_text("{}", encoding="utf-8")
            (out_dir / "out" / "unit-0000.json").write_text("{}", encoding="utf-8")
            self.assertEqual(kaggle_driver.units_done(out_dir), 1)


class KaggleFanoutEstimateTests(unittest.TestCase):
    """`run` dispatches min(concurrency, remaining) kernels per round, so an estimate
    derived from core_hours alone under-reports a unit-parallel job."""

    def test_estimate_runs_accounts_for_total_units(self) -> None:
        got = kaggle_backend.estimate_runs(0.5, _Cfg(), total_units=5)
        self.assertEqual(got["est_kernels"], 5)
        self.assertEqual(got["est_rounds"], 1)

    def test_more_units_than_concurrency_needs_more_rounds(self) -> None:
        got = kaggle_backend.estimate_runs(0.5, _Cfg(), total_units=12)
        self.assertEqual(got["est_kernels"], 12)
        self.assertEqual(got["est_rounds"], 3)  # ceil(12 / concurrency 5)

    def test_core_hours_still_dominates_when_larger(self) -> None:
        # 4 cores x 12h = 48 core-h per kernel; 500 core-h needs 11 kernels.
        got = kaggle_backend.estimate_runs(500.0, _Cfg(), total_units=1)
        self.assertEqual(got["est_kernels"], 11)


    def test_broker_constraints_total_units_reaches_the_probe(self) -> None:
        """The broker job path builds its own estimate, so `constraints.total_units` has
        to survive into it or the fan-out arm is dead for every non-bundle job."""
        job = {
            "job_id": "unit-parallel",
            "task_family": "enumeration",
            "policy": {"backends": ["kaggle"]},
            "constraints": {"cores": 4, "memory_mb": 8192, "core_hours": 0.5,
                            "total_units": 12},
            "payload": {"python_source": "def main():\n    return {}\n", "entrypoint": "main"},
        }
        plan = _plan(job)
        self.assertEqual(plan["kernel_count"], 12, plan)
        self.assertEqual(plan["kernel_runs"], 3, plan)  # ceil(12 / concurrency 5)


class KagglePreflightHardwareTests(unittest.TestCase):
    """preflight is the designated know-before-you-run call, so it must state the
    hardware a kernel actually provides."""

    def _bundle(self, tmp: str) -> Path:
        job_dir = Path(tmp) / "bundle"
        job_dir.mkdir()
        (job_dir / "manifest.json").write_text(json.dumps({
            "job_id": "preflight-hw",
            "core_hours": 0.5,
            "cores": 4,
            "memory_mb": 8192,
            "total_units": 5,
        }), encoding="utf-8")
        (job_dir / "run.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        return job_dir

    def test_preflight_reports_kernel_hardware(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = self._bundle(tmp)
            legacy = {k: os.environ.pop(k) for k in ("KAGGLE_USERNAME", "KAGGLE_KEY") if k in os.environ}
            try:
                with _offline_creds(Path(tmp)):
                    got = kaggle_driver.preflight(job_dir=job_dir, config=_Cfg())
            finally:
                os.environ.update(legacy)
        self.assertEqual(got["kernel_cores"], 4)
        self.assertEqual(got["kernel_ram_gb"], 32.0)
        self.assertEqual(got["aggregate_cores"], 20)  # 4 x concurrency 5

    def test_preflight_kernel_count_matches_the_fanout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = self._bundle(tmp)
            legacy = {k: os.environ.pop(k) for k in ("KAGGLE_USERNAME", "KAGGLE_KEY") if k in os.environ}
            try:
                with _offline_creds(Path(tmp)):
                    got = kaggle_driver.preflight(job_dir=job_dir, config=_Cfg())
            finally:
                os.environ.update(legacy)
        self.assertEqual(got["total_units"], 5)
        self.assertEqual(got["est_kernels"], 5)



class DocumentedConstraintKeyTests(unittest.TestCase):
    """A manifest written the way the planner documents must size the same as one
    written the way the planner reads.

    `unknown_manifest_keys_error` and `canonical/templates/
    compute-offload-sizing-gate.md` both spell the width `constraints.cores`.
    `build_estimate` read only `parallelism`/`cpu`, so a 48-way sweep spelled as
    documented planned as a one-core job: Hetzner picked the cheapest 2-vCPU box
    and Modal reported capacity_ok with no oversubscription note.
    """

    WIDTH = 48
    BASE = {"memory_mb": 4096, "core_hours": 24}

    def _estimate(self, constraints):
        return build_estimate(
            parameters={},
            constraints=constraints,
            policy={"backends": ["hetzner"]},
            gpu_signal=False,
            runtime_sec=24 * 3600,
        )

    def test_every_documented_spelling_of_the_width_sizes_the_same(self) -> None:
        for key in planner.PARALLELISM_CONSTRAINT_KEYS:
            with self.subTest(key=key):
                est = self._estimate({key: self.WIDTH, **self.BASE})
                self.assertEqual(est["parallelism"], self.WIDTH)

    def test_the_width_reaches_the_lane_that_buys_the_hardware(self) -> None:
        """Hetzner sizes on `estimate["parallelism"]`; a 48-way job must not be
        planned onto a 2-vCPU server."""

        est = self._estimate({"cores": self.WIDTH, **self.BASE})
        spec, reason = hetzner_backend.select_server_spec(est, _Cfg())
        self.assertIsNotNone(spec, reason)
        self.assertGreaterEqual(int(spec["vcpu"]), self.WIDTH)

    def test_the_width_reaches_the_modal_oversubscription_note(self) -> None:
        est = self._estimate({"cores": self.WIDTH, **self.BASE})
        fits, reason = modal_backend.capacity_fit(est, "modal_cpu", _Cfg())
        self.assertTrue(fits, reason)
        self.assertIn("cores_oversubscribed", reason)

    def test_a_manifest_with_no_width_is_still_one_core(self) -> None:
        self.assertEqual(self._estimate(dict(self.BASE))["parallelism"], 1)

    def test_an_explicit_parallelism_outranks_a_core_count(self) -> None:
        """`parallelism` is the fan-out width proper; a manifest carrying both
        means the fan-out, not the reservation."""

        est = self._estimate({"parallelism": 4, "cores": 48, **self.BASE})
        self.assertEqual(est["parallelism"], 4)

    def test_the_rejection_message_names_only_keys_the_planner_reads(self) -> None:
        """The message tells a caller where resource requests belong. Every key it
        names has to be one the planner actually sizes on, or the instruction
        produces the silently-ignored manifest it exists to prevent."""

        message = planner.unknown_manifest_keys_error(
            {"job_id": "x", "estimate": {"cores": 4}}
        )
        self.assertIsNotNone(message)
        for key in planner.DOCUMENTED_CONSTRAINT_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, message)
        self.assertIn("cores", planner.PARALLELISM_CONSTRAINT_KEYS)



class ModalCapacityNoteInTrailTests(unittest.TestCase):
    """The cpu note has to reach the trail, on the path where the lane is TAKEN.

    `capacity_fit` calls its cpu note "sizing information the caller needs in the
    trail, not grounds to refuse the lane", and modal-research-compute/SKILL.md
    tells the agent to read `cores_oversubscribed=...` off "Modal's trail reason".
    All three call sites kept the capacity string only when the lane was refused,
    so an accepted 12x-over-subscribed plan reported `"reason": "available"` and
    the note was computed and thrown away.
    """

    # 48-way against run_cpu_job, which is deployed with cpu=4.0.
    WIDE = {"parallelism": 48, "memory_mb": 4096, "core_hours": 24}

    def _estimate(self, constraints):
        return build_estimate(
            parameters={},
            constraints=constraints,
            policy={"backends": ["modal"]},
            gpu_signal=False,
            runtime_sec=24 * 3600,
        )

    def _fit(self, constraints, *, gpu=False):
        est = dict(self._estimate(constraints))
        est["gpu"] = gpu
        return lambda decision: modal_backend.capacity_fit(est, decision, _Cfg())

    def test_an_accepted_cpu_hop_carries_the_oversubscription_note(self) -> None:
        _, _, trail = planner.select_remote_lane(
            order=["modal"], modal_decision="modal_cpu", gpu_signal=False,
            hz=None, hz_in_order=False, modal_ok=(True, "available"),
            modal_fit=self._fit(self.WIDE), gha_ok=lambda: (False, "off"))
        modal_hop = next(t for t in trail if t["backend"] == "modal")
        self.assertTrue(modal_hop["adequate"], modal_hop)
        self.assertIn("cores_oversubscribed=48>4", modal_hop["reason"])
        self.assertIn("available", modal_hop["reason"])

    def test_an_accepted_gpu_hop_carries_it_too(self) -> None:
        _, _, trail = planner.select_gpu_lane(
            order=["modal"], local_gpu=False, config=_Cfg(),
            modal_ok=(True, "available"), modal_fit=self._fit(self.WIDE, gpu=True),
            gha_ok=lambda: (False, "off"))
        modal_hop = next(t for t in trail if t["backend"] == "modal")
        self.assertTrue(modal_hop["adequate"], modal_hop)
        self.assertIn("cores_oversubscribed=48>8", modal_hop["reason"])

    def test_an_end_to_end_plan_reports_it(self) -> None:
        """The agent reads the plan, not the cascade; the note must survive to it."""

        job = {
            "job_id": "wide-sweep",
            "task_family": "enumeration",
            "policy": {"backends": ["modal"]},
            "constraints": dict(self.WIDE),
            "payload": {"python_source": "def main():\n    return {}\n",
                        "entrypoint": "main"},
        }
        plan = _plan(job, modal_ready=True)
        self.assertEqual(plan["decision"], "modal_cpu", plan)
        modal_hop = next(t for t in plan["routing_trail"] if t["backend"] == "modal")
        self.assertIn("cores_oversubscribed", modal_hop["reason"])

    def test_a_refused_hop_still_reports_the_capacity_verdict_alone(self) -> None:
        """The control: a refusal's decisive reason is the capacity verdict, not the
        liveness string it would otherwise be prefixed with."""

        too_big = {"parallelism": 1, "memory_mb": 65536, "core_hours": 1}
        _, _, trail = planner.select_remote_lane(
            order=["modal"], modal_decision="modal_cpu", gpu_signal=False,
            hz=None, hz_in_order=False, modal_ok=(True, "available"),
            modal_fit=self._fit(too_big), gha_ok=lambda: (False, "off"))
        modal_hop = next(t for t in trail if t["backend"] == "modal")
        self.assertFalse(modal_hop["adequate"], modal_hop)
        self.assertTrue(
            modal_hop["reason"].startswith("modal_capacity_exceeded"), modal_hop
        )

    def test_an_unchecked_hop_says_so_rather_than_reading_as_fitted(self) -> None:
        """No estimate supplied is reported, not silently asserted to fit."""

        _, _, trail = planner.select_remote_lane(
            order=["modal"], modal_decision="modal_cpu", gpu_signal=False,
            hz=None, hz_in_order=False, modal_ok=(True, "available"),
            modal_fit=None, gha_ok=lambda: (False, "off"))
        modal_hop = next(t for t in trail if t["backend"] == "modal")
        self.assertIn("modal_capacity_unchecked", modal_hop["reason"])

    def test_the_composer_keeps_both_halves_on_the_accepted_path(self) -> None:
        self.assertEqual(
            planner.modal_trail_reason("available", "modal_capacity_ok:x", True),
            "available; modal_capacity_ok:x",
        )
        self.assertEqual(
            planner.modal_trail_reason("available", "modal_capacity_exceeded:x", False),
            "modal_capacity_exceeded:x",
        )


if __name__ == "__main__":
    unittest.main()
