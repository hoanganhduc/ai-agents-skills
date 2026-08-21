from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

RUN_SKILL = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_skill.sh"
PIN = "0" * 40
COMPONENT_PREFIX = "/usr/local/libexec/coding-system/components/ai-agents-skills"

# Root ownership cannot be produced by an unprivileged test, so the harness below
# stubs exactly that one predicate and leaves every other decision the gate makes
# -- the component path shape, the manifest, the symlink refusal, the containment
# walk -- running against a real tree.  Without this the gate has no test at all
# that can distinguish it from an unconditional refusal: both smoke harnesses
# rewrite ``credential_runtime_enforcement`` to 0 before any case runs.
_STUB = """
root_owned_metadata() {
  local candidate="$1" expected_type="$2"
  if [ "$expected_type" = file ]; then [ -f "$candidate" ]; else [ -d "$candidate" ]; fi
}
"""


def _gate_source() -> str:
    text = RUN_SKILL.read_text(encoding="utf-8")
    start = text.index("trusted_credential_runtime_generation() {")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


@unittest.skipIf(os.name == "nt", "run_skill.sh is not a native Windows target")
class CredentialRuntimeGateTests(unittest.TestCase):
    """The gate that refuses a credential launch from an untrusted runtime copy."""

    def _component(self, tmp: Path, *, pin: str = PIN) -> tuple[Path, Path, Path]:
        """Build a component generation whose paths mirror the real prefix."""
        component_root = tmp / pin
        runtime = component_root / "canonical" / "runtime"
        command = runtime / "workspace" / "skills" / "send-email" / "run_send_email.sh"
        command.parent.mkdir(parents=True)
        command.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        (component_root / "manifest").mkdir(parents=True)
        (component_root / "manifest" / "credential-runtime.json").write_text("{}\n", encoding="utf-8")
        return component_root, runtime, command

    def test_a_well_formed_generation_is_accepted(self) -> None:
        # Without this case the suite could not tell the gate apart from a
        # function that refuses unconditionally.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _, runtime, command = self._component(tmp)
            self.assertEqual(self._decide_in_place(tmp, runtime, command), "accept")

    def _decide_in_place(self, tmp: Path, runtime: Path, command: Path) -> str:
        """Decide with the gate's literal prefix pointed at the temporary tree."""
        gate = _gate_source().replace(COMPONENT_PREFIX, str(tmp))
        script = (
            "set -uo pipefail\n"
            + _STUB
            + f'runtime_real="{runtime}"\n'
            + f'workspace_real="{runtime / "workspace"}"\n'
            + f'command_path="{command}"\n'
            + gate
            + "if trusted_credential_runtime_generation; then echo accept; else echo refuse; fi\n"
        )
        completed = subprocess.run(
            ["bash", "-c", script], check=False, text=True, capture_output=True, timeout=30
        )
        return completed.stdout.strip()

    def test_a_same_uid_runtime_copy_is_refused(self) -> None:
        # The production case: an ordinary install under the user's own home.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            runtime = tmp / "home" / ".local" / "share" / "ai-agents-skills" / "runtime"
            command = runtime / "workspace" / "skills" / "send-email" / "run_send_email.sh"
            command.parent.mkdir(parents=True)
            command.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            self.assertEqual(self._decide_in_place(tmp, runtime, command), "refuse")

    def test_a_missing_component_manifest_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            component_root, runtime, command = self._component(tmp)
            (component_root / "manifest" / "credential-runtime.json").unlink()
            self.assertEqual(self._decide_in_place(tmp, runtime, command), "refuse")

    def test_a_symlink_anywhere_in_the_generation_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            component_root, runtime, command = self._component(tmp)
            real = component_root / "canonical" / "real-runtime"
            shutil.move(str(runtime), str(real))
            runtime.symlink_to(real)
            self.assertEqual(self._decide_in_place(tmp, runtime, command), "refuse")

    def test_a_command_outside_the_generation_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _, runtime, _ = self._component(tmp)
            outside = tmp / "elsewhere" / "run_send_email.sh"
            outside.parent.mkdir(parents=True)
            outside.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            self.assertEqual(self._decide_in_place(tmp, runtime, outside), "refuse")

    def test_a_generation_whose_directory_name_is_not_a_pin_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            _, runtime, command = self._component(tmp, pin="latest")
            self.assertEqual(self._decide_in_place(tmp, runtime, command), "refuse")


@unittest.skipIf(os.name == "nt", "run_skill.sh is not a native Windows target")
class CredentialRuntimeGateCallSiteTests(unittest.TestCase):
    """The launch path that consults the gate, with enforcement left switched on.

    Every other runner test rewrites ``credential_runtime_enforcement`` to 0 in its
    ephemeral copy, so nothing else in the suite ever reaches this branch.
    """

    def test_an_ordinary_install_refuses_a_credential_bearing_launch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            runtime = root / ".local" / "share" / "ai-agents-skills" / "runtime"
            command = runtime / "workspace" / "skills" / "axiom-axle-mcp" / "run_axiom_axle_mcp.sh"
            command.parent.mkdir(parents=True)
            command.write_text("#!/usr/bin/env bash\necho launched\n", encoding="utf-8")
            command.chmod(0o755)
            runner = runtime / "run_skill.sh"
            shutil.copy2(RUN_SKILL, runner)
            runner.chmod(0o755)
            self.assertIn(
                "credential_runtime_enforcement=1",
                runner.read_text(encoding="utf-8"),
                "the runner under test must keep enforcement on",
            )

            completed = subprocess.run(
                ["bash", str(runner), "skills/axiom-axle-mcp/run_axiom_axle_mcp.sh"],
                check=False,
                text=True,
                capture_output=True,
                timeout=60,
            )

            self.assertEqual(completed.returncode, 127, completed.stderr)
            self.assertIn(
                "credential-bearing launch requires a root-owned exact AAS component generation",
                completed.stderr,
            )
            self.assertNotIn("launched", completed.stdout)


if __name__ == "__main__":
    unittest.main()
