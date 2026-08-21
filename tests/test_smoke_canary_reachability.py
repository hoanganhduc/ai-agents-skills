from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from installer.ai_agents_skills import runtime_smoke  # noqa: E402
from installer.ai_agents_skills.manifest import load_manifests  # noqa: E402

# Skills whose credential path cannot be exercised from a temporary smoke runtime,
# so no canary can be delivered to the process at all:
#
#   autonomous-research-loop-runtime -- any pointer sets ``arl_credential_broker=1``
#     and the broker refuses its own dependency outside an exact generation
#     ("untrusted broker dependency: load_secret_env.py").
#   lean-explore-mcp -- a populated secrets file makes the skill take the
#     credential-bearing branch, which refuses without an immutable
#     exact-generation helper.
#
# These are recorded rather than silently tolerated, and the second test below
# retires an entry automatically the moment its canary does become reachable.
UNREACHABLE_BY_DESIGN = {
    "autonomous-research-loop-runtime": "any secrets pointer activates the ARL credential broker",
    "lean-explore-mcp": "the credential branch requires an immutable exact-generation helper",
}


def declared_canaries(manifests: dict, skill: str) -> dict[str, str]:
    smoke = manifests["runtime"]["skills"][skill].get("smoke") or {}
    values = dict(smoke.get("env_canaries") or {})
    file_spec = runtime_smoke.secret_file_canary_spec(manifests, skill)
    if file_spec is not None:
        values.update(file_spec["values"])
    return values


def reachable_canaries(
    manifests: dict, skill: str
) -> tuple[dict[str, bool], subprocess.CompletedProcess]:
    """Launch the real runner with a stub command and report what it could see.

    The stub stands in for the skill so the answer is about delivery, not about
    what any particular skill happens to do with the value. Everything else is
    production: the real ``run_skill.sh``, the real command-relative path that
    selects its credential branch, and the real ``smoke_env``.

    The completed process comes back alongside the per-canary verdict because a
    launch the runner refused says nothing about delivery, and the two are
    indistinguishable from the canary map alone -- both leave every name absent.
    """
    declared = declared_canaries(manifests, skill)
    smoke = manifests["runtime"]["skills"][skill].get("smoke") or {}
    relative = (smoke.get("command") or {})["linux"].split("workspace/", 1)[1]
    file_spec = runtime_smoke.secret_file_canary_spec(manifests, skill)
    pointer_env = file_spec["pointer_env"] if file_spec else ""

    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        runtime = work / "runtime"
        command = runtime / "workspace" / relative
        command.parent.mkdir(parents=True)
        body = "#!/usr/bin/env bash\n"
        if pointer_env:
            # A pointer-style skill is handed the file, not the keys, so the value
            # arriving inside that file is delivery just as much as an export is.
            body += (
                f'pointer="${{{pointer_env}:-}}"\npayload=""\n'
                '[ -n "$pointer" ] && [ -r "$pointer" ] && payload="$(cat "$pointer")"\n'
            )
        for name, value in sorted(declared.items()):
            body += f'value="${{{name}:-}}"\n'
            if pointer_env:
                body += f'case "$payload" in *"{value}"*) value="{value}" ;; esac\n'
            body += f'printf "%s=%s\\n" {name} "${{value:-<absent>}}"\n'
        command.write_text(body, encoding="utf-8")
        command.chmod(0o755)

        runner = runtime / "run_skill.sh"
        shutil.copy2(ROOT / "canonical" / "runtime" / "runners" / "run_skill.sh", runner)
        runner.write_text(
            runner.read_text(encoding="utf-8").replace(
                "credential_runtime_enforcement=1", "credential_runtime_enforcement=0", 1
            ),
            encoding="utf-8",
        )
        runner.chmod(0o755)
        shutil.copy2(
            ROOT / "canonical" / "runtime" / "runners" / "load_secret_env.py",
            runtime / "load_secret_env.py",
        )
        current = command.parent
        while True:
            current.chmod(0o755)
            if current == runtime:
                break
            current = current.parent

        # A bare ``/usr/bin:/bin`` is minimal but not always supported: it offers
        # whatever ``python3`` the platform ships, and macOS ships 3.9 -- below the
        # 3.10 the project has required since ``installer/bootstrap.sh``.  The
        # noncredential branch then refuses the launch over the interpreter and the
        # skill never runs, which is not what this test is asking about.  Lead with
        # a supported interpreter, as any real runtime install has.
        interpreter_bin = work / "bin"
        interpreter_bin.mkdir()
        (interpreter_bin / "python3").symlink_to(sys.executable)

        env = runtime_smoke.smoke_env(manifests, skill, runtime / "workspace")
        env["PATH"] = f"{interpreter_bin}:/usr/bin:/bin"
        env["HOME"] = str(work)
        completed = subprocess.run(
            ["bash", str(runner), relative], env=env, text=True, capture_output=True, timeout=120
        )
        seen = dict(
            line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line
        )
    return {name: seen.get(name) == value for name, value in declared.items()}, completed


@unittest.skipIf(os.name == "nt", "run_skill.sh is not a native Windows target")
class SmokeCanaryReachabilityTests(unittest.TestCase):
    """A ``canary-not-leaked`` check must be able to fail.

    ``run_skill.sh`` unsets every known secret name before exec, and a
    credential-contract command additionally keeps only its retained names. So a
    canary planted in the environment reaches nothing, its check passes on every
    run, and the smoke reports leak coverage it never had. Measuring delivery is
    the only way to tell a passing check from an absent one.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifests = load_manifests()
        cls.skills = sorted(
            name
            for name in cls.manifests["runtime"]["skills"]
            if declared_canaries(cls.manifests, name)
            and (cls.manifests["runtime"]["skills"][name].get("smoke") or {}).get("command", {}).get("linux")
        )

    def test_the_canaries_were_found(self) -> None:
        self.assertGreaterEqual(len(self.skills), 18)

    def test_every_declared_canary_reaches_the_process(self) -> None:
        for skill in self.skills:
            if skill in UNREACHABLE_BY_DESIGN:
                continue
            with self.subTest(skill=skill):
                delivered, completed = reachable_canaries(self.manifests, skill)
                self.assertEqual(
                    completed.returncode,
                    0,
                    f"{skill} never reached the stub at all: the launcher exited "
                    f"{completed.returncode} -- {completed.stderr.strip() or '(no stderr)'}. "
                    "A refused launch is not evidence about delivery either way",
                )
                for name, reached in sorted(delivered.items()):
                    self.assertTrue(
                        reached,
                        f"{skill} declares canary {name}, but the launcher strips it before the "
                        "skill runs -- canary-not-leaked would pass no matter what the skill did",
                    )

    def test_a_refused_launch_is_not_reported_as_a_stripped_canary(self) -> None:
        """The two must stay distinguishable, because both leave every name absent.

        macOS ships Python 3.9 at ``/usr/bin/python3``, below the 3.10 the
        project has required since ``installer/bootstrap.sh``, and the
        noncredential branch refuses the launch over it.  Read through the
        canary map alone that refusal is indistinguishable from stripping, and
        it once surfaced as a claim that the launcher strips values it had never
        been handed the chance to pass along.
        """
        skill = next(
            name
            for name in self.skills
            if name not in UNREACHABLE_BY_DESIGN
            and runtime_smoke.secret_file_canary_spec(self.manifests, name) is None
        )
        with tempfile.TemporaryDirectory() as raw:
            unsupported = Path(raw) / "python3"
            unsupported.write_text('#!/bin/sh\nprintf "3.9\\n"\n', encoding="utf-8")
            unsupported.chmod(0o755)
            with patch.object(sys, "executable", str(unsupported)):
                delivered, completed = reachable_canaries(self.manifests, skill)

        self.assertFalse(any(delivered.values()), f"{skill} should reach nothing here")
        self.assertNotEqual(
            completed.returncode, 0, "a refusal has to be visible in the launch outcome"
        )
        self.assertIn("3.10 or newer", completed.stderr)

    def test_a_recorded_exemption_is_dropped_once_it_stops_applying(self) -> None:
        for skill, reason in sorted(UNREACHABLE_BY_DESIGN.items()):
            with self.subTest(skill=skill):
                self.assertIn(skill, self.skills, f"{skill} is exempted but declares no canary")
                self.assertFalse(
                    any(reachable_canaries(self.manifests, skill)[0].values()),
                    f"{skill} is now delivering its canary ({reason} no longer holds); "
                    "remove it from UNREACHABLE_BY_DESIGN",
                )


if __name__ == "__main__":
    unittest.main()
