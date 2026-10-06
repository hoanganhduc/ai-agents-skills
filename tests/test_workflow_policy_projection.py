"""Sanctioned nonsecret formal controls survive the strict launcher boundary."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "canonical/runtime"
VALUES = {
    "AAS_AUTOLOOP_FORMAL_POLICY": "on", "AAS_AUTOLOOP_FORMAL_TYPECHECK": "1",
    "AAS_AUTOLOOP_FORMAL_PROJECT": "/reviewed/project",
    "AAS_AUTOLOOP_LAX_REQUEST": "/host/optional-lax.json",
    "AAS_AUTOLOOP_FORMAL_EXECUTION_BACKEND": "kaggle-cpu",
    "AAS_AUTOLOOP_FORMAL_REMOTE_REQUEST": "/host/remote.json",
    "AAS_AUTOLOOP_FORMAL_FORCE_CREDITS": "0",
    "AAS_AUTOLOOP_FORMAL_FORCE": "0", "AAS_AUTOLOOP_FORMAL_ALLOW_PATH_STEAL": "0",
    "AAS_AUTOLOOP_FORMAL_TYPECHECK_TIMEOUT": "80",
    "AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local",
}


@unittest.skipUnless(sys.platform.startswith("linux"), "descriptor-bound POSIX launcher test")
class FormalProjectionTests(unittest.TestCase):
    def test_controls_with_no_compute_provider_or_both_pointers(self):
        for pointers in ((), ("AAS_COMPUTE_SECRETS_FILE",), ("AAS_PROVIDER_SECRETS_FILE",), ("AAS_COMPUTE_SECRETS_FILE", "AAS_PROVIDER_SECRETS_FILE")):
            with self.subTest(pointers=pointers), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                runtime = root / "runtime"
                pack = runtime / "workspace/skills/autonomous-research-loop-runtime"
                shutil.copytree(RUNTIME / "skills/autonomous-research-loop-runtime", pack,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                for name in ("run_skill.sh", "load_secret_env.py", "arl_credential_broker.py"):
                    shutil.copyfile(RUNTIME / "runners" / name, runtime / name)
                script = pack / "run_autonomous_research_loop.sh"
                keys = [*VALUES, "OPENAI_API_KEY", "HCLOUD_TOKEN", "AAS_AUTOLOOP_ARBITRARY_NEW_KEY", "AAS_RUNTIME_COMMAND_FD"]
                script.write_text("#!/bin/bash\nexec /usr/bin/python3 - <<'PY'\nimport json,os\nprint(json.dumps({key:os.environ.get(key) for key in " + repr(keys) + "}))\nPY\n", encoding="utf-8")
                for path in [runtime, *runtime.rglob("*")]:
                    if not path.is_symlink():
                        path.chmod(0o755 if path.is_dir() or path.suffix == ".sh" else 0o644)
                home = root / "home"; home.mkdir(mode=0o700)
                env = {"HOME": str(home), "PATH": "/usr/bin:/bin", **VALUES,
                       "OPENAI_API_KEY": "ambient-provider-fixture", "HCLOUD_TOKEN": "ambient-compute-fixture",
                       "AAS_RUNTIME_COMMAND_FD": "999", "AAS_AUTOLOOP_ARBITRARY_NEW_KEY": "unselected"}
                for name in pointers:
                    pointer = root / (name + ".env"); pointer.write_text("", encoding="utf-8"); pointer.chmod(0o600)
                    env[name] = str(pointer)
                completed = subprocess.run([str(runtime / "run_skill.sh"), "skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh"],
                    env=env, capture_output=True, encoding="utf-8", timeout=30)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                data = json.loads(completed.stdout)
                self.assertEqual({name:data[name] for name in VALUES}, VALUES)
                self.assertIsNone(data["OPENAI_API_KEY"])
                self.assertIsNone(data["HCLOUD_TOKEN"])
                self.assertNotEqual(data["AAS_RUNTIME_COMMAND_FD"], "999")
                if not pointers:
                    self.assertIsNone(data["AAS_AUTOLOOP_ARBITRARY_NEW_KEY"])

    def test_powershell_uses_same_explicit_control_names(self):
        text = (RUNTIME / "runners/run_skill.ps1").read_text(encoding="utf-8")
        for name in VALUES:
            self.assertIn('"' + name + '"', text)


class RemoteRuntimeClosureTests(unittest.TestCase):
    def test_selected_remote_runtime_has_its_deferred_repository_dependencies(self):
        from installer.ai_agents_skills.manifest import load_manifests
        from installer.ai_agents_skills.runtime import resolve_runtime_skills
        runtime = load_manifests()["runtime"]
        for skill in ("autonomous-research-loop-runtime", "deep-research-workflow"):
            closure = resolve_runtime_skills([skill], runtime, "auto")
            self.assertIn("kaggle-research-compute", closure)
            self.assertIn("lean-strict-verification-gate", closure)
            self.assertNotIn("lax-formalization", closure)
