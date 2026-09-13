from __future__ import annotations

import json
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_secret_entrypoints import _bash_supports_descriptor_binding


REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "canonical/runtime"
SKILL = "lean-explore-mcp"
COMMAND = f"skills/{SKILL}/run_lean_explore_mcp.sh"
HELPER = RUNTIME / "skills" / SKILL / "lean_explore_mcp.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("lean_explore_helper_test", HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LeanExploreConfigTargetTests(unittest.TestCase):
    def test_command_targets_follow_native_platform(self) -> None:
        helper = load_helper()

        self.assertEqual(
            helper._runtime_command_targets("nt"),
            (
                "run_skill.ps1",
                "skills/lean-explore-mcp/run_lean_explore_mcp.ps1",
            ),
        )
        self.assertEqual(
            helper._runtime_command_targets("posix"),
            (
                "run_skill.sh",
                "skills/lean-explore-mcp/run_lean_explore_mcp.sh",
            ),
        )


def stage_lean_runtime(root: Path) -> Path:
    runtime = root / "runtime"
    skill_dir = runtime / "workspace" / "skills" / SKILL
    skill_dir.mkdir(parents=True)
    for name in ("run_skill.sh", "load_secret_env.py"):
        destination = runtime / name
        shutil.copy2(RUNTIME / "runners" / name, destination)
        destination.chmod(0o755 if name.endswith(".sh") else 0o644)
    for name in ("run_lean_explore_mcp.sh", "lean_explore_mcp.py"):
        destination = skill_dir / name
        shutil.copy2(RUNTIME / "skills" / SKILL / name, destination)
        destination.chmod(0o755 if name.endswith(".sh") else 0o644)
    for directory in (runtime, runtime / "workspace", skill_dir.parent, skill_dir):
        directory.chmod(0o755)
    return runtime / "run_skill.sh"


@unittest.skipUnless(
    sys.platform == "linux" and Path("/usr/bin/python3").is_file()
    and _bash_supports_descriptor_binding(),
    "admitted venv launches require Linux, system Python, and bash >= 4.4",
)
class LeanExploreVenvServeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, self.previous_umask)
        home = self.root / "home"
        home.mkdir(mode=0o700)
        self.env = {
            "HOME": str(home),
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.launcher = stage_lean_runtime(self.root)

    def _venv(self) -> Path:
        venv = self.root / "venv"
        completed = subprocess.run(
            ["/usr/bin/python3", "-I", "-m", "venv", "--without-pip", str(venv)],
            env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return venv

    def _serve(self, venv: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        if venv is not None:
            env["AAS_SKILL_VENV"] = str(venv)
        return subprocess.run(
            ["/bin/bash", str(self.launcher), COMMAND, "serve", "--backend", "api"],
            env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )

    def test_serve_refuses_without_prefix(self) -> None:
        completed = self._serve()
        self.assertEqual(completed.returncode, 78, completed.stderr)
        self.assertIn(
            'LeanExplore MCP serve requires the admitted skill Python venv; run: '
            'make provision-skill-python ARGS="--skills lean-explore-mcp --apply"',
            completed.stderr,
        )

    def test_serve_refuses_without_exactly_one_dist_info(self) -> None:
        venv = self._venv()
        for count in (0, 2):
            with self.subTest(dist_info_count=count):
                if count:
                    site = next((venv / "lib").glob("python3.*/site-packages"))
                    (site / "lean_explore-1.2.1.dist-info").mkdir()
                    second = venv / "lib/python3.999/site-packages/lean_explore-1.2.1.dist-info"
                    second.mkdir(parents=True)
                completed = self._serve(venv)
                self.assertEqual(completed.returncode, 78, completed.stderr)
                self.assertIn(
                    "LeanExplore MCP serve requires exactly one lean_explore-1.2.1.dist-info "
                    f"in the skill Python venv (found {count})",
                    completed.stderr,
                )

    def test_serve_adapter_refuses_outside_a_venv(self) -> None:
        completed = subprocess.run(
            ["/usr/bin/python3", "-I", str(HELPER), "serve", "--backend", "api"],
            env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(completed.returncode, 78, completed.stderr)
        self.assertIn(
            "LeanExplore MCP adapter refused to start: "
            "LeanExplore MCP serve requires the admitted skill Python venv",
            completed.stderr,
        )

    def test_serve_adapter_reaches_the_api_key_check(self) -> None:
        venv = self._venv()
        site = next((venv / "lib").glob("python3.*/site-packages"))
        package = site / "lean_explore"
        mcp = package / "mcp"
        mcp.mkdir(parents=True)
        for path in (package / "__init__.py", mcp / "__init__.py", mcp / "tools.py"):
            path.write_text("", encoding="utf-8")
        (mcp / "app.py").write_text("mcp_app = object()\n", encoding="utf-8")
        dist = site / "lean_explore-1.2.1.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: lean-explore\nVersion: 1.2.1\n",
            encoding="utf-8",
        )
        completed = self._serve(venv)
        self.assertEqual(completed.returncode, 78, completed.stderr)
        self.assertIn("LEANEXPLORE_API_KEY is required for the api backend", completed.stderr)

    def test_wrapper_parses(self) -> None:
        completed = subprocess.run(
            ["/bin/bash", "-n", str(RUNTIME / COMMAND)],
            env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_generated_api_config_projects_private_key_through_launcher(self) -> None:
        venv = self._venv()
        site = next((venv / "lib").glob("python3.*/site-packages"))
        package = site / "lean_explore"
        mcp = package / "mcp"
        mcp.mkdir(parents=True)
        for path in (package / "__init__.py", mcp / "__init__.py", mcp / "tools.py"):
            path.write_text("", encoding="utf-8")
        canary = "LEANEXPLORE-GENERATED-CONFIG-CANARY"
        (package / "api.py").write_text(
            "import os\n"
            "class ApiClient:\n"
            "    def __init__(self, *, api_key):\n"
            f"        if api_key != {canary!r}:\n"
            "            raise RuntimeError('configured key did not reach adapter')\n"
            "        if any(name in os.environ for name in "
            "('LEANEXPLORE_API_KEY', 'AAS_SKILL_SECRETS_FILE')):\n"
            "            raise RuntimeError('credential environment was not scrubbed')\n",
            encoding="utf-8",
        )
        (mcp / "app.py").write_text(
            "class App:\n"
            "    def run(self, *, transport):\n"
            "        if transport != 'stdio':\n"
            "            raise RuntimeError('unexpected transport')\n"
            "        print('{\"configured\": true}')\n"
            "mcp_app = App()\n",
            encoding="utf-8",
        )
        dist = site / "lean_explore-1.2.1.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: lean-explore\nVersion: 1.2.1\n",
            encoding="utf-8",
        )
        secrets = self.root / "lean-explore.env"
        secrets.write_text(f"LEANEXPLORE_API_KEY={canary}\n", encoding="utf-8")
        secrets.chmod(0o600)
        snippet = subprocess.run(
            [str(self.launcher), COMMAND, "config-snippet", "--backend", "api"],
            env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(snippet.returncode, 0, snippet.stderr)
        command = json.loads(snippet.stdout)["local_stdio_mcp_config"]["mcpServers"]["lean-explore"]
        env = {**self.env, "AAS_SKILL_VENV": str(venv), **command["env"]}
        if "AAS_SKILL_SECRETS_FILE" in command["env"]:
            env["AAS_SKILL_SECRETS_FILE"] = str(secrets)
        completed = subprocess.run(
            [command["command"], *command["args"]],
            env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {"configured": True})
        self.assertNotIn(canary, snippet.stdout + snippet.stderr + completed.stdout + completed.stderr)

    def test_curated_api_config_uses_the_secret_file_pointer(self) -> None:
        template = REPO / "canonical/templates/sample-arl-headless-driver-with-formal/curated_mcp.claude.example.json"
        command = json.loads(template.read_text(encoding="utf-8"))["mcpServers"]["lean-explore"]
        self.assertEqual(command["command"], "<ABSOLUTE_RUNTIME_ROOT>/run_skill.sh")
        self.assertEqual(command["args"], [COMMAND, "serve", "--backend", "api"])
        self.assertEqual(command["env"], {
            "AAS_SKILL_SECRETS_FILE": "<ABSOLUTE_OWNER_CONTROLLED_LEANEXPLORE_ENV_FILE>",
        })


if __name__ == "__main__":
    unittest.main()
