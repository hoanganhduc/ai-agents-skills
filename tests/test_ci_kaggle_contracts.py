from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer.ai_agents_skills.agents import detect_agents, target_for
from installer.ai_agents_skills.apply import apply_plan
from installer.ai_agents_skills.lifecycle import rollback, uninstall
from installer.ai_agents_skills.lifecycle_matrix import fake_root_for_shape, root_snapshot
from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.planner import build_plan
from installer.ai_agents_skills.render import render_reference_skill_md
from installer.ai_agents_skills.verify import verify


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ci_test_checkout", REPO / ".github" / "scripts" / "ci_test_checkout.py"
)
assert SPEC is not None and SPEC.loader is not None
CI_CHECKOUT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CI_CHECKOUT)


class CiCheckoutTests(unittest.TestCase):
    def test_local_invocation_refuses_before_creating_a_checkout(self) -> None:
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), mock.patch.object(
            CI_CHECKOUT.tempfile, "TemporaryDirectory"
        ) as temporary, mock.patch.object(CI_CHECKOUT, "git_output") as git:
            self.assertEqual(CI_CHECKOUT.main(["--", "make", "test"]), 2)
            temporary.assert_not_called()
            git.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "native Windows refusal")
    def test_windows_refuses_even_in_github_actions(self) -> None:
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), mock.patch.object(
            CI_CHECKOUT.tempfile, "TemporaryDirectory"
        ) as temporary:
            self.assertEqual(CI_CHECKOUT.main(["--", "make", "test"]), 2)
            temporary.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "POSIX CI launcher")
    def test_child_receives_same_sha_python_and_literal_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp).resolve()
            command = ["make", "test", "ARGS=space and ; literal"]
            with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), mock.patch.object(
                CI_CHECKOUT.tempfile, "TemporaryDirectory"
            ) as temporary, mock.patch.object(
                CI_CHECKOUT, "git_output", side_effect=["", "a" * 40, "", "", "a" * 40]
            ) as git, mock.patch.object(
                CI_CHECKOUT.subprocess, "run", return_value=subprocess.CompletedProcess(command, 7)
            ) as child:
                temporary.return_value.__enter__.return_value = str(private)
                self.assertEqual(CI_CHECKOUT.main(["--", *command]), 7)
                self.assertEqual(git.call_args_list[2].args[1:4], ("clone", "--no-hardlinks", "--no-checkout"))
                self.assertEqual(git.call_args_list[3].args[1:], ("checkout", "--detach", "a" * 40))
                self.assertEqual(child.call_args.args[0], command)
                self.assertEqual(child.call_args.kwargs["cwd"], private / "repo")
                env = child.call_args.kwargs["env"]
                self.assertEqual(env["AAS_PYTHON"], sys.executable)
                self.assertEqual(env.get("HOME"), os.environ.get("HOME"))
                git.side_effect = ["", "a" * 40, "", "", "a" * 40]
                child.return_value = subprocess.CompletedProcess(command, -15)
                self.assertEqual(CI_CHECKOUT.main(["--", *command]), 143)

    @unittest.skipUnless(os.name == "posix", "POSIX CI launcher")
    def test_dirty_source_refuses_before_creating_a_checkout(self) -> None:
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), mock.patch.object(
            CI_CHECKOUT.tempfile, "TemporaryDirectory"
        ) as temporary, mock.patch.object(
            CI_CHECKOUT, "git_output", side_effect=subprocess.CalledProcessError(1, ["git", "diff"])
        ), mock.patch.object(CI_CHECKOUT.subprocess, "run") as child:
            self.assertEqual(CI_CHECKOUT.main(["--", "make", "test"]), 1)
            temporary.assert_not_called()
            child.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "POSIX CI launcher")
    def test_sha_mismatch_prevents_test_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), mock.patch.object(
                CI_CHECKOUT.tempfile, "TemporaryDirectory"
            ) as temporary, mock.patch.object(
                CI_CHECKOUT, "git_output", side_effect=["", "a" * 40, "", "", "b" * 40]
            ), mock.patch.object(CI_CHECKOUT.subprocess, "run") as child:
                temporary.return_value.__enter__.return_value = tmp
                self.assertEqual(CI_CHECKOUT.main(["--", "make", "test"]), 1)
                child.assert_not_called()


class ReferenceSourceTests(unittest.TestCase):
    def test_reference_names_existing_source_inside_and_outside_target_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            target_home = base / "target"
            target_home.mkdir()
            for parent in (target_home, base / "shared"):
                with self.subTest(parent=parent.name):
                    source = parent / "checkout" / "canonical" / "skills" / "example" / "SKILL.md"
                    source.parent.mkdir(parents=True)
                    source.write_text("# Test skill\n", encoding="utf-8")
                    text = render_reference_skill_md(
                        "example", {"description": "Test skill"}, "deepseek", source,
                        home_root=target_home,
                    )
                    displayed = next(line[3:-1] for line in text.splitlines() if line.startswith("- `"))
                    referenced = target_home / displayed[2:] if displayed.startswith("~/") else Path(displayed)
                    self.assertEqual(referenced.resolve(), source.resolve())
                    self.assertTrue(referenced.is_file())
                    self.assertEqual(displayed.startswith("~/"), parent == target_home)


@unittest.skipUnless(os.name == "posix", "installer mutation is POSIX-only")
class ClcParentLifecycleTests(unittest.TestCase):
    def test_config_parents_round_trip_with_and_without_existing_config(self) -> None:
        manifests = load_manifests()
        for shape in ("linux", "macos", "windows", "wsl"):
            for existing in (False, True):
                for removal in ("uninstall", "rollback"):
                    with self.subTest(shape=shape, existing=existing, removal=removal), tempfile.TemporaryDirectory() as tmp:
                        root = fake_root_for_shape(Path(tmp).resolve(), shape)
                        target_for(root, "chatgpt-local-coder").home.mkdir(parents=True)
                        if shape == "windows":
                            config = root / "AppData" / "Roaming" / "chatgpt-local-coder" / "config.json"
                        elif shape == "macos":
                            config = root / "Library" / "Application Support" / "chatgpt-local-coder" / "config.json"
                        else:
                            config = root / ".config" / "chatgpt-local-coder" / "config.json"
                        if existing:
                            config.parent.mkdir(parents=True)
                            config.write_text(json.dumps({"keep": [1, 2]}, indent=2) + "\n", encoding="utf-8")
                        baseline = root_snapshot(root, include_installer_state=False)
                        plan = build_plan(
                            root, manifests, ["zotero"],
                            detect_agents(root, ["chatgpt-local-coder"], platform=shape),
                            platform=shape,
                        )
                        result = apply_plan(root, plan, dry_run=False)
                        self.assertTrue(json.loads(config.read_text(encoding="utf-8"))["skills"]["scanHostOnly"])
                        self.assertEqual(verify(root)["status"], "ok")
                        if removal == "uninstall":
                            uninstall(root, dry_run=False)
                        else:
                            rollback(root, run_id=result["run_id"], dry_run=False)
                        self.assertEqual(verify(root)["status"], "no-managed-artifacts")
                        self.assertEqual(root_snapshot(root, include_installer_state=False), baseline)

    def test_actual_parent_drift_after_planning_is_still_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target_for(root, "chatgpt-local-coder").home.mkdir()
            plan = build_plan(
                root, load_manifests(), ["zotero"],
                detect_agents(root, ["chatgpt-local-coder"], platform="windows"),
                platform="windows",
            )
            shared_parent = root / "AppData"
            shared_parent.mkdir()
            shared_parent.chmod(0o755)
            with self.assertRaisesRegex(ValueError, "managed parent permissions changed after planning"):
                apply_plan(root, plan, dry_run=False)
