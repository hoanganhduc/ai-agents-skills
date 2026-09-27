"""Partial Lax installs must carry the verifier used by the executor."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from installer.ai_agents_skills.agents import detect_agents
from installer.ai_agents_skills.apply import apply_plan
from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.planner import build_plan
from installer.ai_agents_skills.runtime import resolve_runtime_skills
from installer.ai_agents_skills.selectors import artifact_dependency_skills


class LaxInstallDependencyTests(unittest.TestCase):
    def test_standalone_runtime_carries_strict_gate(self):
        manifests = load_manifests()
        selected = resolve_runtime_skills(
            ["lax-formalization"], manifests["runtime"], "auto"
        )
        self.assertIn("lean-strict-verification-gate", selected)

    @unittest.skipIf(os.name == "nt", "native Windows mutation is disabled")
    def test_workflow_partial_install_executes_new_helpers_from_installed_tree(self):
        manifests = load_manifests(); artifacts = [("template", "lax-paper-workflow")]
        skills = sorted(artifact_dependency_skills(artifacts, manifests))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); (root / ".codex").mkdir(); runtime = root / "runtime"
            plan = build_plan(root, manifests, skills, detect_agents(root), artifacts=artifacts,
                              runtime_profile="auto", runtime_root=runtime, install_mode="copy")
            apply_plan(root, plan, dry_run=False)
            self.assertTrue((root / ".codex/templates/lax-paper-workflow.md").is_file())
            folder = runtime / "workspace/skills/lax-formalization"
            for name in ["public_source.py", "workflow_check.py"]:
                r = subprocess.run([sys.executable, "-I", "-B", "-c",
                    "import sys,runpy; sys.path.insert(0,sys.argv[1]); p=sys.argv[1]+'/'+sys.argv[2]; "
                    "sys.argv=[p,'--help']; runpy.run_path(p,run_name='__main__')", str(folder), name],
                    cwd=root, capture_output=True, encoding="utf-8", timeout=30)
                self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((folder / "paper-template/paper-versions.json").is_file())

    @unittest.skipIf(os.name == "nt", "native Windows mutation is disabled")
    def test_template_only_install_can_identify_executor(self):
        manifests = load_manifests()
        artifacts = [("template", "lax-paper-artifact")]
        skills = sorted(artifact_dependency_skills(artifacts, manifests))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".codex").mkdir()
            runtime = root / "runtime"
            plan = build_plan(
                root, manifests, skills, detect_agents(root), artifacts=artifacts,
                runtime_profile="auto", runtime_root=runtime, install_mode="copy",
            )
            apply_plan(root, plan, dry_run=False)
            executor = runtime / "workspace/skills/lax-formalization"
            # A separate process in the installed tree prevents canonical source
            # imports from hiding a missing runtime file. No Docker/network needed.
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c",
                 "import sys; sys.path.insert(0, sys.argv[1]); "
                 "import lax_executor; print(lax_executor.executor_identity())",
                 str(executor)],
                cwd=root, capture_output=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertRegex(result.stdout.strip(), r"^[0-9a-f]{64}$")
            self.assertTrue(
                (root / ".codex/skills/lean-strict-verification-gate/SKILL.md").is_file()
            )


if __name__ == "__main__":
    unittest.main()
