"""The credential-bearing launch gate of ``run_skill.sh``.

A credential-bearing command starts only when the launcher that is running is
owner-controlled: owned by the invoking user or root, no group or other write
bit, not a symlink, a single hard link unless root owns it, and every ancestor
directory up to the runtime root the same (``trusted_metadata``).  Both the
installed layout (``<runtime>/run_skill.sh``) and the source checkout
(``<runtime>/runners/run_skill.sh``) pass.  No path pattern and no root
ownership is required anywhere.

The foreign-owner refusal (a launcher owned by another user) is not producible
by an unprivileged test: such a test cannot create a file it does not own, and
``trusted_metadata`` reads the owner from ``stat``.  That arm is stated here and
not tested.  Every fixture is created with an explicit mode under ``umask 077``
because the host umask decides what ``mkdir`` and ``open`` would otherwise leave
behind (0002 on the machine this was written on gives group-writable trees).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import os_child_env

REPO = Path(__file__).resolve().parents[1]
RUN_SKILL = REPO / "canonical" / "runtime" / "runners" / "run_skill.sh"
SECRET_LOADER = REPO / "canonical" / "runtime" / "runners" / "load_secret_env.py"
GATE_FUNCTIONS = (
    "trusted_metadata",
    "trusted_runtime_file_chain",
    "trusted_credential_launcher",
)
RETIRED_NAMES = (
    "trusted_credential_runtime_generation",
    "root_owned_metadata",
    "credential_runtime_enforcement",
    "allow_managed_selector_advisory",
    "/usr/local/libexec",
    "python-closure",
)
COMMAND_REL = "skills/axiom-axle-mcp/run_axiom_axle_mcp.sh"
COMMAND_BODY = "#!/usr/bin/env bash\necho launched\n"
LAUNCHER_REFUSAL = "credential-bearing launch requires an owner-controlled launcher"
COMMAND_CHAIN_REFUSAL = (
    "credential-bearing launch requires an owner-controlled managed command chain"
)


def _function_source(text: str, name: str) -> str:
    """Return one top-level bash function, ``name() {`` through its closing ``}``."""
    start = text.index(f"{name}() {{")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _gate_source() -> str:
    text = RUN_SKILL.read_text(encoding="utf-8")
    return "".join(_function_source(text, name) for name in GATE_FUNCTIONS)


def _mkdir(path: Path, mode: int = 0o755) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)
    return path


def _write(path: Path, text: str, mode: int) -> Path:
    _mkdir(path.parent)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


class _OwnerControlledFixtures(unittest.TestCase):
    def setUp(self) -> None:
        previous = os.umask(0o077)
        self.addCleanup(os.umask, previous)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.root.chmod(0o700)


@unittest.skipIf(os.name == "nt", "the credential gate is POSIX-only")
class CredentialLauncherGateTests(_OwnerControlledFixtures):
    """The three gate functions, extracted verbatim and decided in place."""

    def _decide(self, *, script_path: Path, runtime_parent_real: Path, runtime_real: Path) -> str:
        script = (
            "set -uo pipefail\n"
            f"script_path={shlex.quote(str(script_path))}\n"
            f"runtime_parent_real={shlex.quote(str(runtime_parent_real))}\n"
            f"runtime_real={shlex.quote(str(runtime_real))}\n"
            + _gate_source()
            + "if trusted_credential_launcher; then echo accept; else echo refuse; fi\n"
        )
        completed = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout.strip()

    def _installed_layout(self) -> tuple[Path, Path]:
        runtime = _mkdir(self.root / "runtime")
        launcher = _write(runtime / "run_skill.sh", "#!/bin/bash -p\n", 0o755)
        return runtime, launcher

    def _source_layout(self) -> tuple[Path, Path, Path]:
        runtime = _mkdir(self.root / "checkout" / "runtime")
        runners = _mkdir(runtime / "runners")
        launcher = _write(runners / "run_skill.sh", "#!/bin/bash -p\n", 0o755)
        return runtime, runners, launcher

    def test_the_retired_generation_gate_is_gone(self) -> None:
        text = RUN_SKILL.read_text(encoding="utf-8")
        for name in RETIRED_NAMES:
            self.assertNotIn(name, text)
        for name in GATE_FUNCTIONS:
            self.assertIn(f"{name}() {{", text)
        self.assertIn(LAUNCHER_REFUSAL, text)

    def test_the_installed_layout_is_accepted(self) -> None:
        runtime, launcher = self._installed_layout()
        verdict = self._decide(script_path=launcher, runtime_parent_real=runtime, runtime_real=runtime)
        self.assertEqual(verdict, "accept")

    def test_the_source_checkout_layout_is_accepted(self) -> None:
        runtime, runners, launcher = self._source_layout()
        verdict = self._decide(script_path=launcher, runtime_parent_real=runners, runtime_real=runtime)
        self.assertEqual(verdict, "accept")

    def test_a_group_writable_launcher_is_refused(self) -> None:
        runtime, launcher = self._installed_layout()
        launcher.chmod(0o775)
        verdict = self._decide(script_path=launcher, runtime_parent_real=runtime, runtime_real=runtime)
        self.assertEqual(verdict, "refuse")

    def test_a_group_writable_runtime_root_is_refused(self) -> None:
        runtime, launcher = self._installed_layout()
        runtime.chmod(0o775)
        verdict = self._decide(script_path=launcher, runtime_parent_real=runtime, runtime_real=runtime)
        self.assertEqual(verdict, "refuse")

    def test_a_group_writable_runners_directory_is_refused(self) -> None:
        runtime, runners, launcher = self._source_layout()
        runners.chmod(0o775)
        verdict = self._decide(script_path=launcher, runtime_parent_real=runners, runtime_real=runtime)
        self.assertEqual(verdict, "refuse")

    def test_a_symlinked_launcher_is_refused(self) -> None:
        runtime = _mkdir(self.root / "runtime")
        real = _write(self.root / "elsewhere" / "run_skill.sh", "#!/bin/bash -p\n", 0o755)
        link = runtime / "run_skill.sh"
        link.symlink_to(real)
        verdict = self._decide(script_path=link, runtime_parent_real=runtime, runtime_real=runtime)
        self.assertEqual(verdict, "refuse")

    def test_a_hard_linked_launcher_is_refused(self) -> None:
        runtime, launcher = self._installed_layout()
        os.link(launcher, self.root / "run_skill.copy")
        self.assertEqual(launcher.stat().st_nlink, 2)
        verdict = self._decide(script_path=launcher, runtime_parent_real=runtime, runtime_real=runtime)
        self.assertEqual(verdict, "refuse")

    def test_a_launcher_outside_the_runtime_is_refused(self) -> None:
        runtime = _mkdir(self.root / "runtime")
        elsewhere = _mkdir(self.root / "elsewhere")
        launcher = _write(elsewhere / "run_skill.sh", "#!/bin/bash -p\n", 0o755)
        verdict = self._decide(script_path=launcher, runtime_parent_real=elsewhere, runtime_real=runtime)
        self.assertEqual(verdict, "refuse")

    def test_a_missing_launcher_is_refused(self) -> None:
        runtime = _mkdir(self.root / "runtime")
        verdict = self._decide(
            script_path=runtime / "run_skill.sh",
            runtime_parent_real=runtime,
            runtime_real=runtime,
        )
        self.assertEqual(verdict, "refuse")


@unittest.skipIf(os.name == "nt", "the credential gate is POSIX-only")
@unittest.skipUnless(os.path.isfile("/usr/bin/python3"), "the credential branch needs /usr/bin/python3")
class CredentialLauncherCallSiteTests(_OwnerControlledFixtures):
    """The whole launcher, executed directly so its ``#!/bin/bash -p`` applies.

    The command is an armed credential-bearing wrapper replaced by a stub that
    prints ``launched``; rc 0 with that line proves the branch ran through the
    gate, the command-chain check, the attested Python and the secret loader.
    """

    def _stage(self, runtime: Path, *, source_layout: bool) -> Path:
        _mkdir(runtime)
        runners = _mkdir(runtime / "runners") if source_layout else runtime
        launcher = runners / "run_skill.sh"
        shutil.copy2(RUN_SKILL, launcher)
        launcher.chmod(0o755)
        loader = runners / "load_secret_env.py"
        shutil.copy2(SECRET_LOADER, loader)
        loader.chmod(0o644)
        workspace = runtime if source_layout else _mkdir(runtime / "workspace")
        _mkdir(workspace / "skills")
        _write(workspace / COMMAND_REL, COMMAND_BODY, 0o755)
        return launcher

    def _stage_installed(self) -> tuple[Path, Path]:
        runtime = self.root / ".local" / "share" / "ai-agents-skills" / "runtime"
        return runtime, self._stage(runtime, source_layout=False)

    def _launch(self, launcher: Path) -> subprocess.CompletedProcess[str]:
        env = {
            **os_child_env(),
            "HOME": str(self.root),
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        return subprocess.run(
            [str(launcher), COMMAND_REL],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(self.root),
            timeout=60,
            check=False,
        )

    def test_an_owner_controlled_installed_launcher_runs_the_command(self) -> None:
        _runtime, launcher = self._stage_installed()
        completed = self._launch(launcher)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("launched", completed.stdout)

    def test_an_owner_controlled_source_checkout_runs_the_command(self) -> None:
        launcher = self._stage(self.root / "checkout" / "runtime", source_layout=True)
        completed = self._launch(launcher)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("launched", completed.stdout)

    def test_a_group_writable_launcher_is_refused_before_the_command(self) -> None:
        _runtime, launcher = self._stage_installed()
        launcher.chmod(0o775)
        completed = self._launch(launcher)
        self.assertEqual(completed.returncode, 127, completed.stderr)
        self.assertIn(LAUNCHER_REFUSAL, completed.stderr)
        self.assertNotIn("launched", completed.stdout)

    def test_a_symlinked_launcher_is_refused_before_the_command(self) -> None:
        runtime, launcher = self._stage_installed()
        real = runtime / "run_skill.real"
        launcher.rename(real)
        launcher.symlink_to(real)
        completed = self._launch(launcher)
        self.assertEqual(completed.returncode, 127, completed.stderr)
        self.assertIn(LAUNCHER_REFUSAL, completed.stderr)
        self.assertNotIn("launched", completed.stdout)

    def test_a_group_writable_command_chain_is_refused(self) -> None:
        runtime, launcher = self._stage_installed()
        (runtime / "workspace" / "skills").chmod(0o775)
        completed = self._launch(launcher)
        self.assertEqual(completed.returncode, 127, completed.stderr)
        self.assertIn(COMMAND_CHAIN_REFUSAL, completed.stderr)
        self.assertNotIn("launched", completed.stdout)


if __name__ == "__main__":
    unittest.main()
