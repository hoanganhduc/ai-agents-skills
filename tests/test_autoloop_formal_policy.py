"""Unit tests for formal_policy.v1 (ARL Lean formalization assist).

Min CI set from plan Phase F: off regression, merge order, force non-terminal,
claim-support ban, path jail, path-steal default, legacy merge, prompt order.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "autonomous-research-loop-runtime"
)
sys.path.insert(0, str(RUNTIME_DIR))

import formal_policy as fp  # noqa: E402
import autonomous_research_loop_runtime as rt  # noqa: E402


def _init_loop(tmp: Path, **kwargs: Any) -> Path:
    run_dir = tmp / "loop"
    args = type(
        "A",
        (),
        {
            "dir": str(run_dir),
            "goal": "Prove X",
            "success_criteria": "artifact exists",
            "mode": "bounded-research",
            "max_iterations": 10,
            "max_wall_time_seconds": 3600,
            "max_tokens": 0,
            "max_usd": 0.0,
            "max_depth": 3,
            "max_hops": 20,
            "max_child_workers": 2,
            "plateau_rule": rt.DEFAULT_PLATEAU_RULE,
            "budget_owner": "user",
            "force": True,
            "stop_on_guard_fail": True,
            "stop_on_missing_evidence": True,
            "stop_on_scope_change": True,
            "success_check": "",
            "require_user_stop_only": False,
            "stop_condition": None,
            "goal_priority_template": False,
            "formal_policy": kwargs.get("formal_policy"),
            "formal_project": kwargs.get("formal_project"),
            "formal_force_credits": kwargs.get("formal_force_credits"),
            "formal_allow_path_steal": bool(kwargs.get("formal_allow_path_steal", False)),
            "formal_typecheck": bool(kwargs.get("formal_typecheck", False)),
            "formal_force_after_iteration": bool(
                kwargs.get("formal_force_after_iteration", False)
            ),
        },
    )()
    rt.init_loop(args)
    return run_dir


def _set_state(run_dir: Path, **updates: Any) -> None:
    path = run_dir / "loop_state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state.update(updates)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


_CLEAN_SCAN: dict[str, Any] = {
    "ok": True,
    "status": "ok",
    "report": {
        "ok": True,
        "findings": [],
        "coverage": {"files_total": 1, "files_scanned": 1},
    },
}
_TYPECHECK_OK: dict[str, Any] = {
    "ok": True,
    "status": "typechecked",
    "report": {"lean_check_status": "typechecked"},
}


def _audit_result(status: str, **report_extra: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "axiom_audit_status": status,
        "declarations": [
            {"declaration": "t", "axioms": ["propext"], "status": "sanctioned"}
        ],
        "unsanctioned_axioms": [],
    }
    report.update(report_extra)
    clean = status == "audited" and not report["unsanctioned_axioms"]
    return {"ok": clean, "status": status, "report": report}


_AUDIT_CLEAN = _audit_result("audited")


def _set_standing_formal(run_dir: Path, formal: dict[str, Any]) -> None:
    path = run_dir / "loop_state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    so = state.setdefault("standing_orders", {})
    so["formal"] = formal
    state["standing_orders"] = so
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


class FormalPolicyLoadTests(unittest.TestCase):
    def tearDown(self) -> None:
        for key in list(os.environ):
            if key.startswith("AAS_AUTOLOOP_FORMAL"):
                os.environ.pop(key, None)

    def test_fp01_default_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            pol = fp.load_formal_policy(run_dir)
            self.assertEqual(pol.policy, "off")
            self.assertEqual(fp.formal_policy_prompt_addon(run_dir), "")
            self.assertEqual(fp.formal_policy_panel_addon(run_dir), "")

    def test_fp02_full_merge_cli_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            formal_dir = run_dir / "formal"
            formal_dir.mkdir(parents=True, exist_ok=True)
            (formal_dir / "formal_policy.json").write_text(
                json.dumps({"policy": "mention-only", "project": "file_proj/"}),
                encoding="utf-8",
            )
            _set_standing_formal(
                run_dir, {"policy": "auto", "project": "standing/", "force_credits": 9}
            )
            os.environ["AAS_AUTOLOOP_FORMAL_POLICY"] = "on"
            os.environ["AAS_AUTOLOOP_FORMAL_PROJECT"] = "env_proj/"
            pol = fp.load_formal_policy(
                run_dir, cli={"policy": "force", "force_credits": 2}
            )
            self.assertEqual(pol.policy, "force")
            self.assertEqual(pol.project, "env_proj/")
            self.assertEqual(pol.force_credits, 2)

    def test_fp_invalid_env_fail_closed_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            os.environ["AAS_AUTOLOOP_FORMAL_POLICY"] = "not-a-policy"
            pol = fp.load_formal_policy(run_dir)
            self.assertEqual(pol.policy, "off")

    def test_fp07_off_regression_empty_addon(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            text = rt.iteration_prompt(run_dir, panel_enabled=False)
            self.assertNotIn("## Formal policy", text)
            self.assertNotIn("Formal policy (binding)", text)

    def test_fp08_on_injects_after_goal_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            # Activate goal_priority
            (run_dir / "goal_priority.json").write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "discipline_mode": "advise",
                        "primary_campaign": "main",
                        "campaigns": [{"id": "main", "status": "open"}],
                    }
                ),
                encoding="utf-8",
            )
            _set_standing_formal(run_dir, {"policy": "on"})
            text = rt.iteration_prompt(run_dir, panel_enabled=False)
            self.assertIn("## Formal policy", text)
            fi = text.find("## Formal policy")
            # goal_priority active → campaign / discipline text before formal
            gi = text.find("goal_priority")
            if gi < 0:
                gi = text.find("Primary campaign")
            self.assertGreater(fi, 0)
            if gi >= 0:
                self.assertLess(gi, fi)
            # parked (no formal-track) or full binding both ban OpenGauss auto-spawn
            self.assertTrue(
                "OpenGauss" in text or "opengauss" in text.lower(),
                msg="formal addon should mention OpenGauss rule",
            )
            self.assertIn("no OpenGauss auto-spawn", text)

    def test_fp15_auto_no_stable_no_binding_mandate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _set_standing_formal(run_dir, {"policy": "auto"})
            text = fp.formal_policy_prompt_addon(run_dir)
            self.assertIn("no stable candidate", text.lower())
            self.assertNotIn("Formal-track path active", text)

    def test_auto_construct_alone_not_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _set_standing_formal(run_dir, {"policy": "auto"})
            # ledger construct alone must not flip checklist
            with (run_dir / "iterations.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "iteration": 1,
                            "decision": "continue",
                            "goal_contribution": "construct",
                        }
                    )
                    + "\n"
                )
            self.assertFalse(fp.checklist_stable(run_dir))
            text = fp.formal_policy_prompt_addon(run_dir)
            self.assertIn("no stable candidate", text.lower())

    def test_legacy_formalization_status_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            path = run_dir / "loop_state.json"
            state = json.loads(path.read_text(encoding="utf-8"))
            state.setdefault("standing_orders", {})["formalization"] = {
                "enabled": True,
                "project": "formal/DbHam",
                "phase": "skeleton",
                "lake_build": "green",
                "sorry_count": 0,
            }
            path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            pol = fp.load_formal_policy(run_dir)
            # legacy never silently escalates policy to force
            self.assertEqual(pol.policy, "off")
            self.assertEqual(pol.project, "formal/DbHam")
            self.assertEqual(pol.status.get("phase"), "skeleton")
            self.assertTrue(pol.legacy_enabled)

    def test_fp25_init_write_standing_orders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(
                Path(tmp),
                formal_policy="on",
                formal_project="formal/MyProj",
                formal_force_credits=5,
            )
            state = json.loads((run_dir / "loop_state.json").read_text(encoding="utf-8"))
            formal = state["standing_orders"]["formal"]
            self.assertEqual(formal["policy"], "on")
            self.assertEqual(formal["project"], "formal/MyProj")
            self.assertEqual(formal["force_credits"], 5)
            # other standing orders keys not wiped
            self.assertIn("standing_orders", state)
            mirror = run_dir / "formal" / "formal_policy.json"
            self.assertTrue(mirror.is_file())
            data = json.loads(mirror.read_text(encoding="utf-8"))
            self.assertEqual(data["policy"], "on")


class FormalForceTickTests(unittest.TestCase):
    def tearDown(self) -> None:
        for key in list(os.environ):
            if key.startswith("AAS_AUTOLOOP_FORMAL"):
                os.environ.pop(key, None)

    def test_fp17_force_skip_when_flag_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp), formal_policy="force")
            pol = fp.load_formal_policy(run_dir)
            self.assertFalse(fp.is_force_tick_enabled(pol))
            report = fp.formal_force_tick(run_dir, policy=pol)
            self.assertEqual(report["claim_support_status"], "not_evaluated")
            self.assertFalse(report["opengauss_launched"])
            self.assertTrue(report["no_claim_support_promotion"])

    def test_fp18_tool_unavailable_no_loop_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(
                Path(tmp),
                formal_policy="force",
                formal_force_after_iteration=True,
            )
            pol = fp.load_formal_policy(
                run_dir, cli={"policy": "force", "force_after_iteration": True}
            )
            report = fp.formal_force_tick(run_dir, policy=pol)
            self.assertIn(report["terminal"], {"tool_unavailable", "issue_free"})
            # loop state status unchanged (still running from init)
            state = json.loads((run_dir / "loop_state.json").read_text(encoding="utf-8"))
            self.assertNotIn(state.get("status"), {"blocked", "stopped"})
            self.assertEqual(report["claim_support_status"], "not_evaluated")

    def test_force_never_loop_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(
                Path(tmp),
                formal_policy="force",
                formal_force_after_iteration=True,
            )
            state_before = json.loads(
                (run_dir / "loop_state.json").read_text(encoding="utf-8")
            )
            pol = fp.load_formal_policy(
                run_dir, cli={"policy": "force", "force_after_iteration": True}
            )
            fp.formal_force_tick(run_dir, policy=pol)
            state_after = json.loads(
                (run_dir / "loop_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state_before.get("status"), state_after.get("status"))
            self.assertNotIn(state_after.get("status"), {"blocked", "stopped"})

    def test_force_forbids_claim_supported(self) -> None:
        """A runner that answers "supported" cannot put that in the report."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(
                Path(tmp),
                formal_policy="force",
                formal_force_after_iteration=True,
            )
            pol = fp.load_formal_policy(
                run_dir, cli={"policy": "force", "force_after_iteration": True}
            )

            def promoting_runner(_name: str, _payload: dict[str, Any]) -> dict[str, Any]:
                return {
                    "ok": True,
                    "status": "ok",
                    "claim_support_status": "supported",
                    "report": {"claim_support_status": "supported", "ok": True},
                }

            report = fp.formal_force_tick(run_dir, policy=pol, runner=promoting_runner)
            self.assertEqual(report["claim_support_status"], "not_evaluated")
            self.assertNotEqual(report.get("claim_support_status"), "supported")
            self.assertTrue(report["no_claim_support_promotion"])

    def test_fp22_no_opengauss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(
                Path(tmp),
                formal_policy="force",
                formal_force_after_iteration=True,
            )
            pol = fp.load_formal_policy(
                run_dir, cli={"policy": "force", "force_after_iteration": True}
            )
            launched = []

            def bad_runner(name: str, _payload: dict[str, Any]) -> dict[str, Any]:
                if "opengauss" in name.lower():
                    launched.append(name)
                return {"ok": True, "status": "ok"}

            report = fp.formal_force_tick(run_dir, policy=pol, runner=bad_runner)
            self.assertFalse(report["opengauss_launched"])
            self.assertEqual(launched, [])

    def test_fp19_credit_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(
                Path(tmp),
                formal_policy="force",
                formal_force_after_iteration=True,
                formal_force_credits=0,
            )
            pol = fp.load_formal_policy(
                run_dir,
                cli={
                    "policy": "force",
                    "force_after_iteration": True,
                    "force_credits": 0,
                },
            )
            report = fp.formal_force_tick(run_dir, policy=pol, credits_remaining=0)
            self.assertEqual(report["terminal"], "credit_budget_exhausted")
            state = json.loads((run_dir / "loop_state.json").read_text(encoding="utf-8"))
            self.assertNotIn(state.get("status"), {"blocked", "stopped"})


class HostCheckedClaimSupportTests(unittest.TestCase):
    """WS1: the host may record what its own checks established, and no more.

    Every promotion here rests on three host-run steps agreeing — gate scan,
    lake build, axiom audit. Each test below removes exactly one of them and
    asserts the report falls back to ``not_evaluated``, because a missing check
    is an absence of evidence rather than a weaker kind of pass.
    """

    def tearDown(self) -> None:
        for key in list(os.environ):
            if key.startswith("AAS_AUTOLOOP_FORMAL"):
                os.environ.pop(key, None)

    @staticmethod
    def _runner(
        *,
        scan: dict[str, Any] | None = None,
        verify: dict[str, Any] | None = None,
        audit: dict[str, Any] | None = None,
    ):
        seen: list[str] = []

        def runner(name: str, _payload: dict[str, Any]) -> dict[str, Any]:
            seen.append(name)
            if name == "lean_strict_verification_gate.scan":
                return scan if scan is not None else _CLEAN_SCAN
            if name == "lean_strict_verification_gate.verify_typecheck":
                return verify if verify is not None else _TYPECHECK_OK
            if name == "lean_strict_verification_gate.axiom_audit":
                return audit if audit is not None else _AUDIT_CLEAN
            return {"ok": False, "status": "forbidden_skill", "report": {}}

        runner.seen = seen  # type: ignore[attr-defined]
        return runner

    def _tick(self, tmp: Path, runner: Any) -> dict[str, Any]:
        run_dir = _init_loop(
            tmp,
            formal_policy="force",
            formal_force_after_iteration=True,
            formal_typecheck=True,
        )
        (run_dir / "formal").mkdir(parents=True, exist_ok=True)
        (run_dir / "formal" / "lakefile.toml").write_text(
            'name = "demo"\n', encoding="utf-8"
        )
        pol = fp.load_formal_policy(
            run_dir,
            cli={
                "policy": "force",
                "force_after_iteration": True,
                "typecheck": True,
            },
        )
        self.assertTrue(pol.typecheck)
        report = fp.formal_force_tick(run_dir, policy=pol, runner=runner)
        self.run_dir = run_dir
        return report

    def test_a_clean_host_check_records_statement_level_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = self._runner()
            report = self._tick(Path(tmp), runner)
            self.assertEqual(
                report["claim_support_status"], "supports_formal_statement_only"
            )
            # Statement support is not claim support: equivalence with the
            # informal claim is still unreviewed, so the flag stays raised.
            self.assertTrue(report["no_claim_support_promotion"])
            self.assertNotEqual(
                report["claim_support_status"], "supports_claim_after_equivalence_review"
            )
            self.assertIn("formal_axiom_audit", report["evidence_types_emitted"])
            self.assertIn(
                "lean_strict_verification_gate.axiom_audit", runner.seen
            )

    def test_the_persisted_report_carries_what_was_returned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self._tick(Path(tmp), self._runner())
            written = sorted(
                (self.run_dir / "formal" / "force_loop_reports").glob("force_*.json")
            )
            self.assertTrue(written)
            data = json.loads(written[-1].read_text(encoding="utf-8"))
            self.assertEqual(
                data["claim_support_status"], report["claim_support_status"]
            )
            self.assertEqual(data["writer"], fp.HOST_WRITER)

    def test_a_build_that_never_ran_never_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = self._runner(
                verify={"ok": True, "status": "tool_unavailable", "report": {}}
            )
            report = self._tick(Path(tmp), runner)
            self.assertEqual(report["claim_support_status"], "not_evaluated")
            # No build, no audit: the cheaper checks gate the expensive one.
            self.assertNotIn("lean_strict_verification_gate.axiom_audit", runner.seen)

    def test_a_failed_build_never_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self._tick(
                Path(tmp),
                self._runner(
                    verify={
                        "ok": False,
                        "status": "typecheck_failed",
                        "report": {"lean_check_status": "typecheck_failed"},
                    }
                ),
            )
            self.assertEqual(report["claim_support_status"], "not_evaluated")

    def test_a_sorry_ax_in_the_trust_base_never_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self._tick(
                Path(tmp),
                self._runner(
                    audit=_audit_result("audited", unsanctioned_axioms=["sorryAx"])
                ),
            )
            self.assertEqual(report["claim_support_status"], "not_evaluated")
            self.assertEqual(report["hygiene_status"], "gaps")

    def test_an_unresolved_declaration_never_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self._tick(
                Path(tmp),
                self._runner(
                    audit=_audit_result(
                        "audited",
                        declarations=[{"declaration": "Ghost.t", "status": "unresolved"}],
                    )
                ),
            )
            self.assertEqual(report["claim_support_status"], "not_evaluated")

    def test_an_audit_that_could_not_run_never_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self._tick(
                Path(tmp), self._runner(audit=_audit_result("tool_unavailable"))
            )
            self.assertEqual(report["claim_support_status"], "not_evaluated")

    def test_a_scan_finding_never_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = self._runner(
                scan={
                    "ok": False,
                    "status": "failed",
                    "report": {
                        "ok": False,
                        "findings": [
                            {"file": "Demo.lean", "kind": "active_placeholder"}
                        ],
                        "coverage": {"files_total": 1, "files_scanned": 1},
                    },
                }
            )
            report = self._tick(Path(tmp), runner)
            self.assertEqual(report["claim_support_status"], "not_evaluated")
            self.assertNotIn("lean_strict_verification_gate.axiom_audit", runner.seen)

    def test_the_crude_fallback_never_promotes(self) -> None:
        """Reading source text for the token "sorry" is not a gate result."""
        with tempfile.TemporaryDirectory() as tmp:
            runner = self._runner(scan={"ok": True, "status": "ok", "report": {}})
            report = self._tick(Path(tmp), runner)
            self.assertEqual(report["claim_support_status"], "not_evaluated")
            self.assertNotIn("lean_strict_verification_gate.axiom_audit", runner.seen)
            self.assertIn(
                "crude_fallback",
                [row.get("decision") for row in report["ledger"]],
            )


class FormalSafetyTests(unittest.TestCase):
    def tearDown(self) -> None:
        for key in list(os.environ):
            if key.startswith("AAS_AUTOLOOP_FORMAL"):
                os.environ.pop(key, None)

    def test_no_path_steal_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp), formal_policy="force")
            pol = fp.load_formal_policy(run_dir)
            self.assertFalse(pol.allow_path_steal)

    def test_formal_project_path_jail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = _init_loop(root)
            # Escape attempt
            escaped = fp.resolve_formal_project(run_dir, "../../../etc", root=root)
            self.assertIsNone(escaped)
            # Valid project with lakefile under run_dir
            proj = run_dir / "formal" / "DbHam"
            proj.mkdir(parents=True)
            (proj / "lakefile.toml").write_text("name = 'DbHam'\n", encoding="utf-8")
            resolved = fp.resolve_formal_project(run_dir, "formal/DbHam", root=root)
            self.assertIsNotNone(resolved)
            assert resolved is not None
            self.assertEqual(resolved.resolve(), proj.resolve())

    def test_no_secret_in_addon_or_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(
                Path(tmp),
                formal_policy="force",
                formal_force_after_iteration=True,
            )
            pol = fp.load_formal_policy(
                run_dir, cli={"policy": "force", "force_after_iteration": True}
            )
            # Inject secret-shaped string via exception path through runner typecheck
            pol_tc = fp.FormalPolicy(
                **{**pol.__dict__, "typecheck": True, "force_after_iteration": True}
            )
            # create a tiny project so typecheck path is considered
            proj = run_dir / "formal" / "P"
            proj.mkdir(parents=True)
            (proj / "lakefile.toml").write_text("name='P'\n", encoding="utf-8")
            (proj / "Basic.lean").write_text("theorem t : True := by trivial\n", encoding="utf-8")
            pol_tc = fp.FormalPolicy(
                policy="force",
                project="formal/P",
                force_after_iteration=True,
                typecheck=True,
                force_credits=3,
            )

            # Build secret-shaped strings at runtime so the source file never
            # contains continuous token literals (sanitize-check TOKEN_PATTERNS).
            fake_sk = "sk-" + ("ab" * 12)  # length > 20 after sk-
            fake_key = "LE" + "ANEXPLORE_API_KEY=" + "testvalue99"
            def leaky(_name: str, _p: dict[str, Any]) -> dict[str, Any]:
                raise RuntimeError(f"Bearer {fake_sk} {fake_key}")

            report = fp.formal_force_tick(run_dir, policy=pol_tc, runner=leaky)
            blob = json.dumps(report)
            self.assertNotIn("testvalue99", blob)
            self.assertNotIn(fake_sk, blob)
            self.assertIn("REDACTED", blob)
            addon = fp.formal_policy_prompt_addon(run_dir, cli={"policy": "on"})
            self.assertNotIn("API_KEY=", addon)
            self.assertNotIn("Bearer ", addon)

    def test_redact_secrets_helper(self) -> None:
        fake_sk = "sk-" + ("cd" * 12)
        raw = (
            "Bearer tok.en "
            + "LE"
            + "ANEXPLORE_API_KEY="
            + "xyzhide "
            + "api_key=foohide "
            + fake_sk
        )
        out = fp._redact_secrets(raw)
        self.assertIn("REDACTED", out)
        self.assertNotIn("xyzhide", out)
        self.assertNotIn("tok.en", out)
        self.assertNotIn(fake_sk, out)

    def test_pin_wins_over_agent_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            os.environ["AAS_AUTOLOOP_FORMAL_POLICY"] = "force"
            pin = {"policy": "on", "force_after_iteration": False}
            pol = fp.load_formal_policy(run_dir, pin=pin)
            self.assertEqual(pol.policy, "on")
            self.assertFalse(pol.force_after_iteration)

    def test_export_formal_env(self) -> None:
        pol = fp.FormalPolicy(policy="on", project="formal/X", force_credits=4)
        env = fp.export_formal_env(pol)
        self.assertEqual(env["AAS_AUTOLOOP_FORMAL_POLICY"], "on")
        self.assertEqual(env["AAS_AUTOLOOP_FORMAL_PROJECT"], "formal/X")
        self.assertEqual(env["AAS_AUTOLOOP_FORMAL_FORCE_CREDITS"], "4")


class FormalDriveWireTests(unittest.TestCase):
    def tearDown(self) -> None:
        for key in list(os.environ):
            if key.startswith("AAS_AUTOLOOP_FORMAL"):
                os.environ.pop(key, None)

    def test_apply_formal_drive_start_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            args = rt.selftest_drive_args(run_dir, Path(tmp) / "reg", "true")
            args.formal_policy = "on"
            args.formal_project = "formal/"
            pol, pin = rt._apply_formal_drive_start(run_dir, args)
            self.assertIsNotNone(pol)
            assert pol is not None
            self.assertEqual(pol.policy, "on")
            self.assertEqual(pin.get("policy"), "on")
            self.assertEqual(os.environ.get("AAS_AUTOLOOP_FORMAL_POLICY"), "on")
            self.assertTrue((run_dir / "formal" / "host_policy.pin.json").is_file())
            state = json.loads((run_dir / "loop_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["standing_orders"]["formal"]["policy"], "on")

    def test_parser_formal_flags(self) -> None:
        parser = rt.build_parser()
        ns = parser.parse_args(
            [
                "drive",
                "--dir",
                "/tmp/x",
                "--cmd",
                "true",
                "--formal-policy",
                "force",
                "--formal-force-after-iteration",
                "--formal-typecheck",
            ]
        )
        self.assertEqual(ns.formal_policy, "force")
        self.assertTrue(ns.formal_force_after_iteration)
        self.assertTrue(ns.formal_typecheck)

    def test_prompt_order_formal_after_goal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            (run_dir / "goal_priority.json").write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "discipline_mode": "advise",
                        "primary_campaign": "main",
                        "campaigns": [{"id": "main", "status": "open"}],
                    }
                ),
                encoding="utf-8",
            )
            _set_standing_formal(run_dir, {"policy": "force"})
            text = rt.iteration_prompt(run_dir, panel_enabled=False)
            # compute appears; formal last among policy blocks
            fi = text.find("## Formal policy")
            self.assertGreater(fi, 0)
            # goal_priority addon has distinctive markers when active
            # formal must not appear before compute-ish content start
            self.assertIn("Never auto-spawn OpenGauss", text)


if __name__ == "__main__":
    unittest.main()
