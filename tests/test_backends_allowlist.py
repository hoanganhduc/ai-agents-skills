"""Strict user-requested multi-backend allowlist (policy.backends)."""
from __future__ import annotations

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

from research_compute.planner import backends_allowlist_value, plan_job  # noqa: E402


class BackendsAllowlistUnitTests(unittest.TestCase):
    def test_parse_backends_ok(self) -> None:
        got, err = backends_allowlist_value({"backends": ["kaggle", "hetzner"]})
        self.assertIsNone(err)
        self.assertEqual(got, ["kaggle", "hetzner"])

    def test_parse_preferred_alias(self) -> None:
        got, err = backends_allowlist_value({"preferred_backends": ["Hetzner", "Kaggle"]})
        self.assertIsNone(err)
        self.assertEqual(got, ["hetzner", "kaggle"])

    def test_mutually_exclusive_with_backend(self) -> None:
        got, err = backends_allowlist_value({"backend": "kaggle", "backends": ["hetzner"]})
        self.assertIsNone(got)
        self.assertIn("mutually exclusive", err or "")

    def test_reject_unknown_and_duplicates(self) -> None:
        _, err = backends_allowlist_value({"backends": ["kaggle", "aws"]})
        self.assertIn("Unsupported", err or "")
        _, err2 = backends_allowlist_value({"backends": ["kaggle", "kaggle"]})
        self.assertIn("duplicate", err2 or "")

    def test_reject_empty(self) -> None:
        _, err = backends_allowlist_value({"backends": []})
        self.assertIn("empty", err or "")


class BackendsAllowlistPlanTests(unittest.TestCase):
    """plan_job integration using lightweight config/resource stubs.

    Kaggle and Hetzner probes require token *presence* even when liveness is injected
    (account_usable is injection-first; available still gates on token_present). Offline
    tests therefore set dummy env tokens and pin KAGGLE_CONFIG_DIR away from a real
    ~/.kaggle/access_token so CI and developer boxes behave the same.
    """

    class _Cfg:
        routing_order = ["local", "kaggle", "modal", "hetzner", "gha"]
        per_job_cost_cap_usd = 50.0
        modal_environment = "main"
        deployment_alias = "test"
        gha_enabled = False
        gha_repos = {}
        gha_gpu_enabled = False
        # Kaggle lane (free CPU)
        kaggle_enabled = True
        kaggle_kernel_cores = 4
        kaggle_kernel_ram_gb = 32.0
        kaggle_session_hours = 12.0
        kaggle_max_runs = 5
        kaggle_concurrency = 5
        kaggle_weekly_gpu_hours_cap = 18.0
        # Hetzner lane (budget + catalog required for available=True)
        hetzner_enabled = True
        hetzner_max_eur_per_job = 3.0
        hetzner_max_eur_per_day = 3.0
        hetzner_max_server_hours = 6.0
        hetzner_max_concurrent_servers = 2
        hetzner_location = "nbg1"
        hetzner_allowed_locations = ["nbg1", "hel1", "sin"]
        hetzner_server_types = {
            "cpx22": {"vcpu": 2, "ram_gb": 4, "arch": "x86", "eur_per_hour": 0.018},
            "cpx62": {"vcpu": 16, "ram_gb": 32, "arch": "x86", "eur_per_hour": 0.155},
        }

    def _resources(self, **kwargs):
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

    def _offline_creds(self, empty_kaggle_dir: Path):
        """Dummy tokens + isolated Kaggle config dir; clear legacy username/key pair."""
        return mock.patch.dict(
            os.environ,
            {
                "KAGGLE_API_TOKEN": "offline-token-for-test",
                "HCLOUD_TOKEN": "offline-token-for-test",
                "KAGGLE_CONFIG_DIR": str(empty_kaggle_dir),
            },
            clear=False,
        )

    def test_allowlist_omits_local_selects_kaggle_first(self) -> None:
        """Heavy-ish job with allowlist [kaggle, hetzner] must not choose local."""
        job = {
            "task_family": "enumeration",
            "policy": {"backends": ["kaggle", "hetzner"]},
            "constraints": {
                "cpu": 2,
                "memory_mb": 2048,
                "parallelism": 2,
                "core_hours": 1,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            # Clear legacy env vars that would otherwise confuse token_present()
            legacy = {
                k: os.environ.pop(k)
                for k in ("KAGGLE_USERNAME", "KAGGLE_KEY")
                if k in os.environ
            }
            try:
                with self._offline_creds(Path(tmp)):
                    # Light enough that auto path would prefer local_cpu without allowlist.
                    plan = plan_job(job, config=self._Cfg(), resources=self._resources())
            finally:
                os.environ.update(legacy)
        self.assertTrue(plan["accepted"], plan)
        self.assertIn(plan["decision"], ("kaggle", "hetzner"))
        self.assertNotEqual(plan.get("backend"), "local")
        self.assertEqual(plan.get("user_backends_allowlist"), ["kaggle", "hetzner"])
        self.assertTrue(plan.get("strict_backends_allowlist"))
        trail = [t["backend"] for t in plan.get("routing_trail", [])]
        self.assertNotIn("modal", trail)
        self.assertNotIn("gha", trail)

    def test_allowlist_exhausted_rejects_not_local(self) -> None:
        """When all listed remotes fail, do not fall back to local or modal."""
        job = {
            "task_family": "enumeration",
            "policy": {"backends": ["kaggle", "hetzner"]},
            "constraints": {
                "cpu": 12,
                "memory_mb": 8192,
                "parallelism": 12,
                "core_hours": 40,
            },
        }
        res = self._resources(
            liveness={
                "modal": {"ready": True, "usable": True},
                "kaggle": {"usable": False, "reason": "injected-unusable"},
                "hetzner": {"usable": False, "reason": "injected-unusable"},
            }
        )
        # Tokens present so the probe reaches the injected liveness=unusable path
        # (not the earlier no_token short-circuit). Modal remains usable but unlisted.
        with tempfile.TemporaryDirectory() as tmp:
            with self._offline_creds(Path(tmp)):
                plan = plan_job(job, config=self._Cfg(), resources=res)
        self.assertFalse(plan["accepted"])
        self.assertEqual(plan["decision"], "rejected")
        self.assertIn("backends_allowlist_exhausted", plan["risk_flags"])
        # Must not silently accept local/modal despite them being "available"
        self.assertNotIn(plan.get("backend"), ("local", "modal"))

    def test_invalid_allowlist_rejected(self) -> None:
        job = {
            "task_family": "generic",
            "policy": {"backends": "kaggle"},  # wrong type
            "constraints": {"cpu": 1, "memory_mb": 1024, "parallelism": 1, "core_hours": 1},
        }
        plan = plan_job(job, config=self._Cfg(), resources=self._resources())
        self.assertFalse(plan["accepted"])
        self.assertIn("invalid_backends_allowlist", plan["risk_flags"])


if __name__ == "__main__":
    unittest.main()
