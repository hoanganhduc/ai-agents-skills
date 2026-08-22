from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.manifest import REPO_ROOT


SCRIPT = REPO_ROOT / "canonical" / "runtime" / "skills" / "digest-bridge" / "digest_bridge.py"

DIGEST = """# Research digest

## 1. A paper the bridge should hand off
- Link: https://arxiv.org/abs/2401.01234
- Relevance: 9

## 2. Another one
- Link: https://doi.org/10.1145/3372297.3417231
- Relevance: 7
"""

# The helper is present but cannot do the work: what an unconfigured
# getscipapers install looks like from here.
BROKEN_HELPER = """import sys
print("getscipapers is not configured", file=sys.stderr)
sys.exit(1)
"""

WORKING_HELPER = """import json
import sys

if sys.argv[1] == "build-manifest":
    print(json.dumps({"kind": "auto", "entries": 2}))
else:
    print(json.dumps({"watch": "created"}))
sys.exit(0)
"""


class ManifestFailureKeepsThePapersTests(unittest.TestCase):
    """`request` used to bank papers whose handoff had failed.

    digest-bridge/SKILL.md calls `request` "the transition into manifest/watch
    creation", and the state file is the ledger of what has already made that
    transition. `cmd_request` wrote every identifier into it before it had any
    evidence the handoff worked, so a getscipapers that could not run still
    produced `"ok": true, "requested_count": 2` with a null manifest -- and the
    retry after fixing getscipapers then reported "No new papers to request",
    because the ledger already listed them. The papers were dropped for good.
    """

    def _workspace(self, tmp: Path, helper_body: str) -> tuple[Path, Path]:
        """Lay out the installed shape: sibling skill dirs under one root.

        `digest_bridge.py` resolves the getscipapers helper relative to its own
        location, so the two skills have to be siblings for the handoff to be
        reachable at all -- the runtime installs them as
        `workspace/skills/digest-bridge/` and `workspace/skills/getscipapers_requester/`.
        """

        workspace = tmp / "workspace"
        skills = tmp / "skills"
        (skills / "digest-bridge").mkdir(parents=True)
        (skills / "getscipapers_requester").mkdir(parents=True)
        shutil.copy(SCRIPT, skills / "digest-bridge" / "digest_bridge.py")
        (skills / "getscipapers_requester" / "gsp_openclaw_helper.py").write_text(
            helper_body, encoding="utf-8"
        )
        digest = workspace / "data" / "research" / "alerts" / "digests" / "latest-digest.md"
        digest.parent.mkdir(parents=True)
        digest.write_text(DIGEST, encoding="utf-8")
        return skills, workspace

    def _run(self, skills: Path, workspace: Path, *args: str):
        env = dict(
            os.environ,
            AAS_RUNTIME_WORKSPACE=str(workspace),
            PYTHONDONTWRITEBYTECODE="1",
        )
        proc = subprocess.run(
            [sys.executable, "-B", str(skills / "digest-bridge" / "digest_bridge.py"), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=env,
            check=False,
        )
        return proc, json.loads(proc.stdout)

    def _state(self, workspace: Path):
        path = workspace / "data" / "research" / "digest-bridge-state.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def test_a_failed_handoff_records_nothing_and_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), BROKEN_HELPER)

            proc, payload = self._run(
                skills, workspace, "request", "--source", "research", "--watch"
            )

            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error_code"], "manifest_failed")
            self.assertNotIn("requested_count", payload)
            self.assertIsNone(self._state(workspace))

    def test_the_papers_survive_for_the_retry(self) -> None:
        """The point of not banking them: the second run still finds them."""

        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), BROKEN_HELPER)
            self._run(skills, workspace, "request", "--source", "research")

            _, scanned = self._run(skills, workspace, "scan", "--source", "research")
            self.assertEqual(scanned["new_papers"], 2, scanned)
            self.assertEqual(scanned["already_requested"], 0, scanned)

            # Now getscipapers works, and the retry hands them off.
            (skills / "getscipapers_requester" / "gsp_openclaw_helper.py").write_text(
                WORKING_HELPER, encoding="utf-8"
            )
            proc, payload = self._run(skills, workspace, "request", "--source", "research")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(payload["requested_count"], 2, payload)

    def test_the_reason_the_handoff_failed_reaches_the_operator(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), BROKEN_HELPER)
            proc, _ = self._run(skills, workspace, "request", "--source", "research")
            self.assertIn("getscipapers is not configured", proc.stderr)

    def test_a_successful_handoff_still_banks_and_dedups(self) -> None:
        """The control: the ledger has to keep working, or every run re-requests."""

        with tempfile.TemporaryDirectory() as raw:
            skills, workspace = self._workspace(Path(raw), WORKING_HELPER)

            proc, first = self._run(
                skills, workspace, "request", "--source", "research", "--watch"
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(first["ok"])
            self.assertEqual(first["requested_count"], 2)
            self.assertEqual(first["manifest"], {"kind": "auto", "entries": 2})
            self.assertEqual(
                [w["status"] for w in first["watches"]], ["created", "created"]
            )
            self.assertEqual(
                self._state(workspace)["requested"],
                ["2401.01234", "10.1145/3372297.3417231"],
            )

            _, second = self._run(skills, workspace, "request", "--source", "research")
            self.assertEqual(second["message"], "No new papers to request")


if __name__ == "__main__":
    unittest.main()
