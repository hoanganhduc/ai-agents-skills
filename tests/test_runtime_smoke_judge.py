from __future__ import annotations

import contextlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.ai_agents_skills import runtime_smoke as smoke
from installer.ai_agents_skills import post_install_smoke
from installer.ai_agents_skills.cli import build_parser


def contract(**overrides):
    return {"command": {"linux": "workspace/skills/example/run.sh"}, "args": [],
            "expect": {"stdout_json": [{"path": "status", "equals": "ok"}]},
            "requires_python_modules": [], **overrides}


def manifests_for(cases=None, *, offline=None, live=None):
    spec = {"smoke_coverage": {"status": "offline-smoke" if offline else "venv-smoke"}}
    if cases is not None:
        spec["functional_smoke"] = cases
    if offline is not None:
        spec["smoke"] = offline
    if live is not None:
        spec["live_check"] = live
    return {"runtime": {"skills": {"example": spec}}}


class RuntimeSmokeJudgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.smoke_dir = self.workspace / "runtime-smoke"
        self.smoke_dir.mkdir(mode=0o700)
        self.completed = subprocess.CompletedProcess([], 0, json.dumps({
            "status": "ok", "items": [{"name": "a", "ok": True}, {"name": "b", "ok": True}],
            "count": 2, "other": 2, "nullable": None, "tags": ["x", "y"], "object": {"key": 1},
        }), "line one\nready\n")

    def judge(self, expect, completed=None):
        return smoke.judge_expect(expect, completed or self.completed, self.smoke_dir)

    def test_judge_expect_operators(self):
        assertions = [
            {"path": "", "type": "object"}, {"path": "status", "equals": "ok"},
            {"path": "items[*].ok", "equals": True}, {"path": "items[1].name", "regex": "^b$"},
            {"path": "nullable", "exists": True}, {"path": "missing", "absent": True},
            {"path": "tags", "min_len": 2}, {"path": "object", "min_len": 1},
            {"path": "status", "min_len": 2}, {"path": "count", "equals_path": "other"},
            {"path": "tags", "contains": "x"}, {"path": "object", "contains": "key"},
            {"path": "count", "regex": "^2$"}, {"path": "count", "type": "integer"},
            {"path": "count", "minimum": 2}, {"path": "items[*].name", "set_equals": ["b", "a"]},
        ]
        self.assertEqual(self.judge({"exit_code": [0, 1], "stdout_regex": ["status", "items"],
                                    "stderr_regex": "^ready$", "stdout_not_regex": "secret",
                                    "stderr_not_regex": ["password", "exception"], "stdout_json": assertions}), [])
        for assertion in assertions:
            with self.subTest(assertion=assertion):
                bad = dict(assertion)
                op = next(key for key in bad if key != "path")
                if op == "absent":
                    bad["path"] = "status"
                else:
                    bad["path"] = "not.present"
                self.assertTrue(self.judge({"stdout_json": [bad]}))
        changed = json.loads(self.completed.stdout)
        changed["items"][1]["ok"] = False
        self.assertTrue(self.judge({"stdout_json": [{"path": "items[*].ok", "equals": True}]},
                                  subprocess.CompletedProcess([], 0, json.dumps(changed), "")))

    def test_json_types_do_not_accept_bool_as_integer_or_number(self):
        for value, name in [(None, "null"), (True, "boolean"), ({}, "object"), ([], "array"),
                            (2.5, "number"), (2, "integer"), ("x", "string")]:
            completed = subprocess.CompletedProcess([], 0, json.dumps(value), "")
            self.assertEqual(self.judge({"stdout_json": [{"path": "", "type": name}]}, completed), [])
        boolean = subprocess.CompletedProcess([], 0, "true", "")
        for op in [{"type": "integer"}, {"type": "number"}, {"minimum": 1}, {"equals": 1}]:
            self.assertTrue(self.judge({"stdout_json": [{"path": "", **op}]}, boolean))

    def test_set_equals_preserves_scenario_set_with_duplicates_and_order(self):
        expect = {"stdout_json": [{"path": "[*].name", "set_equals": ["a", "b"]}]}
        for values, passed in [(["b", "a", "a"], True), (["a"], False), (["a", "b", "c"], False)]:
            completed = subprocess.CompletedProcess([], 0, json.dumps([{"name": name} for name in values]), "")
            self.assertEqual(not self.judge(expect, completed), passed)

    def test_exit_code_and_alternatives_pair_payload_with_exit(self):
        expect = {"alternatives": [{"exit_code": 0, "stdout_regex": "ok"},
                                    {"exit_code": [1], "stdout_regex": "not-ready"}]}
        self.assertEqual(self.judge(expect, subprocess.CompletedProcess([], 1, "not-ready", "")), [])
        self.assertTrue(self.judge(expect, subprocess.CompletedProcess([], 0, "not-ready", "")))
        self.assertTrue(self.judge({}, subprocess.CompletedProcess([], 1, "", "")))
        self.assertEqual(self.judge({"exit_code": 1}, subprocess.CompletedProcess([], 1, "", "")), [])

    def test_files_types_regex_json_and_containment(self):
        (self.smoke_dir / "data").write_text('{"ready":true}\n', encoding="utf-8")
        (self.smoke_dir / "folder").mkdir()
        expect = {"files": ["data", {"path": "data", "type": "file"},
                             {"path": "folder", "type": "directory"}, {"path": "data", "regex": "^.*ready.*$"},
                             {"path": "data", "json": [{"path": "ready", "equals": True}]}]}
        self.assertEqual(self.judge(expect), [])
        (self.root / "outside").write_text("sensitive", encoding="utf-8")
        (self.smoke_dir / "escape").symlink_to(self.root / "outside")
        for path in ["../outside", str(self.root / "outside"), "escape", "no-file"]:
            self.assertTrue(self.judge({"files": [path]}), path)
        self.assertTrue(self.judge({"files": [{"path": "folder", "type": "file"}]}))
        self.assertTrue(self.judge({"files": [{"path": "data", "type": "directory"}]}))
        (self.smoke_dir / "data").write_text("not json", encoding="utf-8")
        self.assertTrue(self.judge(expect))

    def test_invalid_json_and_all_regex_failures_are_reported(self):
        result = self.judge({"stdout_regex": ["one", "two"], "stderr_not_regex": "ready", "stdout_json": []},
                            subprocess.CompletedProcess([], 0, "not-json", "ready"))
        self.assertEqual(result, ["stdout_regex:one", "stdout_regex:two", "stderr_not_regex:ready", "stdout_json:invalid-json"])

    def test_smoke_timeout(self):
        manifest = manifests_for(offline=contract(timeout_seconds=300))
        self.assertEqual(smoke.smoke_timeout(manifest, "example", None), 300)
        self.assertEqual(smoke.smoke_timeout(manifest, "example", 30), 30)
        self.assertEqual(smoke.smoke_timeout(manifest, "example", 900), 300)
        self.assertEqual(smoke.smoke_timeout(manifests_for(offline=contract()), "example", None), 60)
        self.assertEqual(smoke.smoke_timeout(manifests_for(offline=contract()), "example", 15), 15)

    def test_cli_smoke_timeouts_and_installed_opt_ins(self):
        parser = build_parser()
        self.assertIsNone(parser.parse_args(["runtime-smoke"]).timeout)
        args = parser.parse_args(["installed-runtime-smoke", "--require-functional", "--live"])
        self.assertIsNone(args.timeout)
        self.assertTrue(args.require_functional)
        self.assertTrue(args.live)

    def test_smoke_env_drops_interpreter_overrides(self):
        names = ["AAS_RUNTIME_PYTHON", "AAS_SKILL_VENV", "AAS_RUNTIME_PYTHON_PREFIX",
                 "DOCLING_PYTHON", "PYTHONPATH", "VIRTUAL_ENV"]
        xdg = ["XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"]
        with patch.dict(os.environ, {name: "/hostile" for name in names + xdg}):
            env = smoke.smoke_env(manifests_for(offline=contract()), "example", self.workspace)
        self.assertTrue(all(name not in env for name in names + xdg))
        self.assertEqual(env["HOME"], str(self.smoke_dir / "home"))
        self.assertEqual(stat.S_IMODE(Path(env["HOME"]).stat().st_mode), 0o700)
        self.assertFalse((Path(env["HOME"]) / ".agents_skills_venv").exists())

    def test_smoke_path_entries_lead_with_the_running_interpreter(self):
        """The pinned PATH is only useful if a supported interpreter is on it.

        ``/usr/bin:/bin`` alone offers the platform python3: macOS ships a 3.9
        stub that run_skill.sh refuses at its 3.10 floor, and a CI Linux
        python3 is not the interpreter the smoke dependencies were installed
        into. Both made every skill fail on a runner while passing here.
        """

        with patch.object(sys, "executable", "/opt/py311/bin/python"):
            self.assertEqual(smoke.smoke_path_entries(), ["/opt/py311/bin", "/usr/bin", "/bin"])
        with patch.object(sys, "executable", "/usr/bin/python3"):
            self.assertEqual(smoke.smoke_path_entries(), ["/usr/bin", "/bin"])

    @unittest.skipIf(os.name == "nt", "smoke_env pins PATH on POSIX only")
    def test_smoke_env_path_can_launch_a_supported_interpreter(self):
        env = smoke.smoke_env(manifests_for(offline=contract()), "example", self.workspace)
        entries = env["PATH"].split(os.pathsep)

        self.assertEqual(entries[-2:], ["/usr/bin", "/bin"])
        self.assertIn(os.path.dirname(sys.executable), entries)
        interpreter = Path(entries[0]) / "python3"
        self.assertTrue(interpreter.exists(), f"{entries[0]} carries no python3")
        reported = subprocess.run(
            [str(interpreter), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True, check=True, text=True, encoding="utf-8",
        ).stdout.strip()
        self.assertGreaterEqual(tuple(int(part) for part in reported.split(".")), (3, 10), reported)

    def test_fixtures_expand_content_copy_sources_and_refuse_symlink_escape(self):
        source = self.root / "source"
        source.mkdir()
        (source / "input").write_text("copied", encoding="utf-8")
        case = contract(fixtures=[{"to": "nested/content", "content": "{workspace}|{smoke_dir}|{home}|{skill_venv}"},
                                  {"to": "copy", "copy_from": "input"}])
        with patch.object(smoke, "RUNTIME_SOURCE_ROOT", source):
            smoke.materialize_smoke_fixtures(case, self.workspace, skill_venv="/private/venv")
        destination = self.smoke_dir / "nested" / "content"
        self.assertEqual(
            destination.read_text(encoding="utf-8"),
            f"{self.workspace}|{self.smoke_dir}|{self.smoke_dir / 'home'}|/private/venv",
        )
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(destination.parent.stat().st_mode), 0o700)
        self.assertEqual((self.smoke_dir / "copy").read_text(encoding="utf-8"), "copied")
        (self.smoke_dir / "escape").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            smoke.materialize_smoke_fixtures(contract(fixtures=[{"to": "escape/leak", "content": "no"}]), self.workspace)
        self.assertFalse((self.root / "leak").exists())

    def test_functional_cases_without_modules_run_on_an_absent_venv(self):
        manifest = manifests_for({"first": contract(), "needs": contract(requires_python_modules=["missing_module"]),
                                  "last": contract()})
        with patch.object(smoke, "run_smoke_case", return_value={"status": "ok"}) as run:
            result = smoke.run_functional_smoke_cases(manifest, skills=["example"], runtime_root=self.root,
                    workspace=self.workspace, platform="linux", venv={"status": "absent", "path": "/absent"})
        self.assertEqual([call.kwargs["case_name"] for call in run.call_args_list], ["first", "last"])
        self.assertEqual(result["results"][1]["missing_modules"], ["missing_module"])
        self.assertTrue(all(call.kwargs["skill_venv"] is None for call in run.call_args_list))
        self.assertTrue(all(call.kwargs["inject_canaries"] is False for call in run.call_args_list))
        with patch.object(smoke, "run_smoke_case") as run:
            result = smoke.run_functional_smoke_cases(manifest, skills=["example"], runtime_root=self.root,
                    workspace=self.workspace, platform="linux", venv={"status": "refused", "reason": "group writable"})
        run.assert_not_called()
        self.assertEqual([row["status"] for row in result["results"]], ["failed"] * 3)

    def test_admitted_functional_case_probes_modules_in_venv(self):
        manifest = manifests_for({"needs": contract(requires_python_modules=["module_one", "module_two"])})
        probe_results = [subprocess.CompletedProcess([], 0, "", ""), subprocess.CompletedProcess([], 1, "", "")]
        with patch.object(smoke, "run_smoke_process", autospec=True, side_effect=probe_results) as probe, patch.object(smoke, "run_smoke_case") as run:
            result = smoke.run_functional_smoke_cases(manifest, skills=["example"], runtime_root=self.root,
                    workspace=self.workspace, platform="linux", venv={"status": "admitted", "path": "/private/venv"})
        self.assertEqual(probe.call_args_list[0].args[0][:2], ["/private/venv/bin/python", "-I"])
        self.assertEqual(probe.call_args.kwargs["env"]["AAS_SKILL_VENV"], "/private/venv")
        self.assertEqual(result["results"][0]["missing_modules"], ["module_two"])
        run.assert_not_called()
        with patch.object(smoke, "run_smoke_process", autospec=True, return_value=subprocess.CompletedProcess([], 0, "", "")), \
                patch.object(smoke, "run_smoke_case", return_value={"status": "ok"}) as run:
            result = smoke.run_functional_smoke_cases(manifest, skills=["example"], runtime_root=self.root,
                    workspace=self.workspace, platform="linux", venv={"status": "admitted", "path": "/private/venv"})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(run.call_args.kwargs["skill_venv"], "/private/venv")
        self.assertFalse(run.call_args.kwargs["inject_canaries"])

    def test_functional_no_key_case_never_inherits_t2_canaries(self):
        t2 = contract(env_canaries={"EXAMPLE_API_KEY": "ambient-canary"}, secret_file_canaries={
            "pointer_env": "AAS_SKILL_SECRETS_FILE", "values": {"EXAMPLE_API_KEY": "file-canary"}})
        manifest = manifests_for({"no-key": contract()}, offline=t2)
        with patch.dict(os.environ, {"AAS_SKILL_SECRETS_FILE": "/operator/key", "EXAMPLE_API_KEY": "operator"}), \
                patch.object(smoke, "run_smoke_process", autospec=True, return_value=self.completed) as run:
            result = smoke.run_functional_smoke_cases(manifest, skills=["example"], runtime_root=self.root,
                    workspace=self.workspace, platform="linux", venv={"status": "absent", "path": "/absent"})
        self.assertEqual(result["status"], "ok", result)
        env = run.call_args.kwargs["env"]
        self.assertNotIn("AAS_SKILL_SECRETS_FILE", env)
        self.assertNotIn("EXAMPLE_API_KEY", env)
        self.assertFalse(list(self.smoke_dir.glob("*.smoke-canary.*")))

    def test_run_case_judges_nonzero_exit_and_canary_outside_expect(self):
        case = contract(expect={"exit_code": 1, "stdout_regex": "not-ready"}, env_canaries={"EXAMPLE_TOKEN": "canary-value"})
        manifest = manifests_for(offline=case)
        runner = {"name": "run_skill.sh", "argv": ["/trusted/run_skill.sh"]}
        with patch.object(smoke, "run_smoke_process", autospec=True, return_value=subprocess.CompletedProcess([], 1, "not-ready", "")):
            result = smoke.run_smoke_case(manifest, skill="example", runner=runner, workspace=self.workspace,
                                          platform="linux", timeout=None)
        self.assertEqual(result["status"], "ok", result)
        with patch.object(smoke, "run_smoke_process", autospec=True, return_value=subprocess.CompletedProcess([], 1, "not-ready", "canary-value")):
            result = smoke.run_smoke_case(manifest, skill="example", runner=runner, workspace=self.workspace,
                                          platform="linux", timeout=None)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any(check["name"] == "canary-not-leaked:EXAMPLE_TOKEN" and not check["ok"] for check in result["checks"]))

    def test_live_uses_real_runtime_real_environment_and_no_fixtures(self):
        config = self.workspace / "skills" / "example" / "config.json"
        config.parent.mkdir(mode=0o700, parents=True)
        config.write_text("{}", encoding="utf-8")
        required_config = "{workspace}/skills/example/config.json"
        case = contract(requires={"pointer_env": ["AAS_SKILL_SECRETS_FILE"],
                                  "config_files": [required_config], "network": True},
                        fixtures=[{"to": "must-not-create", "content": "no"}])
        manifest = manifests_for(live={"read-only": case})
        with patch.dict(os.environ, {"HOME": str(self.root / "operator"), "AAS_SKILL_SECRETS_FILE": "/operator/secret"}), \
                patch.object(smoke, "run_smoke_process", autospec=True, return_value=self.completed) as run:
            result = smoke.run_live_checks(manifest, skills=["example"], runtime_root=self.root, platform="linux")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(run.call_args.args[0][0], str(self.root / "run_skill.sh"))
        self.assertEqual(run.call_args.kwargs["env"]["HOME"], str(self.root / "operator"))
        self.assertEqual(run.call_args.kwargs["env"]["AAS_SKILL_SECRETS_FILE"], "/operator/secret")
        self.assertFalse((self.smoke_dir / "must-not-create").exists())
        config.unlink()
        with patch.dict(os.environ, {}, clear=True), patch.object(smoke, "run_smoke_process") as run:
            result = smoke.run_live_checks(manifest, skills=["example"], runtime_root=self.root, platform="linux")
        run.assert_not_called()
        self.assertEqual(result["results"][0]["missing_requirements"], ["AAS_SKILL_SECRETS_FILE", required_config])

    def test_skill_venv_resolves_before_synthetic_home_and_reports_refusal(self):
        prefix = self.root / "venv"
        prefix.mkdir()
        with patch.dict(os.environ, {"AAS_SKILL_VENV": str(prefix)}), \
                patch.object(smoke.skill_python, "attested_base_python", return_value=Path("/usr/bin/python3")), \
                patch.object(smoke.skill_python, "admit_skill_venv", return_value=(False, "group writable")) as admit:
            result = smoke.skill_venv_row()
        self.assertEqual(result, {"status": "refused", "path": str(prefix), "reason": "group writable"})
        admit.assert_called_once_with(str(prefix), attested_python=Path("/usr/bin/python3"))
        with patch.dict(os.environ, {"AAS_SKILL_VENV": str(self.root / "absent")}):
            self.assertEqual(smoke.skill_venv_row()["status"], "absent")

    def test_venv_only_selection_creates_no_t2_row(self):
        manifest = manifests_for({"needs": contract(requires_python_modules=["module_one"])})
        with contextlib.ExitStack() as stack:
            for name, result in [("detect_agents", []), ("build_plan", {}), ("apply_plan", {"actions": []}),
                                 ("verify", {"status": "ok"}), ("skill_venv_row", {"status": "refused", "reason": "operator venv"})]:
                stack.enter_context(patch.object(smoke, name, return_value=result))
            run = stack.enter_context(patch.object(smoke, "run_smoke_case"))
            result = smoke.run_runtime_smoke(manifest, skills={"example"}, platform="linux")
        run.assert_not_called()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["results"], [])
        self.assertTrue(result["coverage"][0]["has_smoke_contract"])
        self.assertEqual(result["skill_venv"]["status"], "refused")
        self.assertNotIn("live", result)

    def test_credential_canary_requires_127_and_restores_mode_on_timeout(self):
        launcher = self.root / "run_skill.sh"
        launcher.write_text("#!/bin/sh\n", encoding="utf-8")
        launcher.chmod(0o700)
        manifest = manifests_for(offline=contract())
        # The command is a real credential-bearing selector; no provider is run.
        candidate = {"status": "ok", "skill": "example", "command_target": "skills/send-email/run_send_email.sh",
                     "args": ["--help"], "timeout_seconds": 60}
        def negative(*args, **kwargs):
            self.assertTrue(launcher.stat().st_mode & stat.S_IWGRP)
            return subprocess.CompletedProcess([], 127, "", "owner-controlled launcher")
        with patch.object(smoke, "run_smoke_process", autospec=True, side_effect=negative):
            result = smoke.credential_launch_canary(self.root, "linux", [candidate], manifests=manifest)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual([row["kind"] for row in result["results"]], ["positive", "negative"])
        self.assertEqual(stat.S_IMODE(launcher.stat().st_mode), 0o700)
        with patch.object(smoke, "run_smoke_process", autospec=True, side_effect=subprocess.TimeoutExpired([], 1)):
            result = smoke.credential_launch_canary(self.root, "linux", [candidate], manifests=manifest)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(stat.S_IMODE(launcher.stat().st_mode), 0o700)
        self.assertEqual(smoke.credential_launch_canary(self.root, "windows", [], manifests=manifest)["status"], "not-applicable")
        self.assertEqual(smoke.credential_launch_canary(self.root, "linux", [], manifests=manifest)["status"], "skipped")

    @unittest.skipUnless(os.name == "posix", "POSIX runtime fixture")
    def test_installed_functional_verdict_and_complete_credential_coverage(self):
        from installer.ai_agents_skills.agents import detect_agents
        from installer.ai_agents_skills.apply import apply_plan
        from installer.ai_agents_skills.manifest import load_manifests
        from installer.ai_agents_skills.planner import build_plan

        manifest = load_manifests()
        spec = manifest["runtime"]["skills"]["send-email"]
        manifest["runtime"]["skills"] = {"send-email": spec}
        spec["functional_smoke"]["missing-module"] = {
            **spec["functional_smoke"]["accounts"], "requires_python_modules": ["missing_fixture_module"],
        }
        (self.root / ".codex").mkdir(mode=0o700)
        apply_plan(self.root, build_plan(self.root, manifest, ["send-email"], detect_agents(self.root, ["codex"]),
                                        platform="linux"), dry_run=False)
        with patch.object(smoke, "skill_venv_row", return_value={"status": "absent", "path": "/test/absent"}):
            result = smoke.run_installed_runtime_smoke(self.root, manifest, skills={"send-email"}, platform="linux")
            required = smoke.run_installed_runtime_smoke(self.root, manifest, skills={"send-email"},
                                                        platform="linux", require_functional=True)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual([row["status"] for row in result["functional"]["results"]], ["ok", "skipped"])
        self.assertEqual(required["status"], "failed", required)
        self.assertEqual(required["unknown_coverage_count"], 0)
        self.assertEqual(required["missing_managed_runtime_count"], 0)
        with patch.object(smoke, "skill_venv_row", return_value={"status": "refused", "reason": "group writable"}):
            refused = smoke.run_installed_runtime_smoke(self.root, manifest, skills={"send-email"}, platform="linux")
        self.assertEqual(refused["status"], "failed", refused)
        self.assertEqual([row["status"] for row in refused["functional"]["results"]], ["failed", "failed"])
        with patch.object(smoke, "skill_venv_row", return_value={"status": "absent", "path": "/test/absent"}), \
                patch.object(smoke, "credential_launch_canary", return_value={"status": "skipped", "results": []}):
            required = smoke.run_installed_runtime_smoke(self.root, manifest, platform="linux", require_complete_coverage=True)
        self.assertEqual(required["status"], "failed", required)
        self.assertEqual(required["unknown_coverage_count"], 0)
        self.assertEqual(required["missing_managed_runtime_count"], 0)

    def test_deep_fallback_runs_and_judges_each_contract_even_if_files_are_stale(self):
        contracts = {"init": contract(args=["init"], timeout_seconds=300, expect={"stdout_json": [{"path": "schema_version", "equals": 2}],
                                                          "files": [{"path": "deep/file", "type": "file"}]}),
                     "validate": contract(args=["validate"], timeout_seconds=90, expect={"stdout_json": [{"path": "status", "equals": "ok"}]})}
        manifest = {"runtime": {"skills": {"deep-research-workflow": {"functional_smoke": contracts}}}}
        (self.smoke_dir / "deep").mkdir()
        (self.smoke_dir / "deep" / "file").write_text("stale", encoding="utf-8")
        processes = [subprocess.CompletedProcess([], 2, "", "invalid choice: 'selftest'"),
                     subprocess.CompletedProcess([], 0, '{"schema_version":1}', ""),
                     subprocess.CompletedProcess([], 0, '{"status":"ok"}', "")]
        with patch.object(smoke, "run_smoke_process", autospec=True, side_effect=processes) as run:
            completed, checks = smoke.run_deep_research_workflow_smoke(["launcher", "deep"], workspace=self.workspace,
                    timeout=60, env={}, args=["selftest"], manifests=manifest)
        self.assertEqual([call.kwargs["args"] for call in run.call_args_list], [["selftest"], ["init"], ["validate"]])
        self.assertEqual([call.kwargs["timeout"] for call in run.call_args_list], [60, 300, 90])
        self.assertEqual([check["ok"] for check in checks if check["name"] == "output-validation"], [False, True])
        self.assertEqual(completed.returncode, 0)

    def test_post_install_smoke_accepts_new_sections(self):
        report = smoke.installed_runtime_smoke_report(status="ok", unknown_coverage_count=0,
                missing_managed_runtime_count=0, credential_launch={"status": "ok", "results": []},
                skill_venv={"status": "admitted", "path": "/private/venv"},
                functional={"status": "ok", "results": []}, live={"status": "skipped", "results": []})
        with contextlib.ExitStack() as stack:
            for name in ["verify_state", "smoke_state", "run_opencode_native_smoke", "run_antigravity_native_smoke",
                         "run_grok_native_smoke", "run_kimi_native_smoke"]:
                stack.enter_context(patch.object(post_install_smoke, name, return_value={"status": "ok"}))
            stack.enter_context(patch.object(post_install_smoke, "run_installed_runtime_smoke", return_value=report))
            stack.enter_context(patch.object(post_install_smoke, "write_report_and_state_summary"))
            result = post_install_smoke.run_post_install_smoke(self.root, {}, {"run_id": "test-smoke", "actions": [{}]})
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["runtime_smoke"], report)


if __name__ == "__main__":
    unittest.main()
