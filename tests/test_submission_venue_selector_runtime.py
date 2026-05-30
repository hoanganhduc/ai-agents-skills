from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.manifest import REPO_ROOT, load_manifests
from installer.ai_agents_skills.runtime_smoke import runtime_command_target


RUNTIME_DIR = REPO_ROOT / "canonical" / "runtime" / "skills" / "submission-venue-selector"
SCRIPT = RUNTIME_DIR / "submission_venue_selector.py"


def run_selector(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if check and completed.returncode != 0:
        raise AssertionError(f"selector failed\nstdout={completed.stdout}\nstderr={completed.stderr}")
    return completed


def last_json(stdout: str) -> dict[str, object]:
    decoder = json.JSONDecoder()
    value: object | None = None
    index = 0
    while index < len(stdout):
        while index < len(stdout) and stdout[index].isspace():
            index += 1
        if index >= len(stdout):
            break
        parsed, index = decoder.raw_decode(stdout, index)
        value = parsed
    assert isinstance(value, dict)
    return value


class SubmissionVenueSelectorRuntimeTests(unittest.TestCase):
    def write_sample_draft(self, root: Path) -> Path:
        draft = root / "draft.tex"
        draft.write_text(
            "\n".join(
                [
                    "This unpublished draft contains SECRET-CODE-NAME-ALPHA in the introduction.",
                    "@article{smith2021, title={Graph recoloring in sparse graphs}, author={Smith, A.}, year={2021}, journal={Journal of Graph Theory}, doi={10.1000/jgt.1}}",
                    "@inproceedings{doe2022, title={Reconfiguration algorithms for colorings}, author={Doe, B.}, year={2022}, booktitle={Proceedings of Symposium on Discrete Algorithms}}",
                ]
            ),
            encoding="utf-8",
        )
        return draft

    def test_offline_run_creates_valid_dossier_without_raw_draft_leak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = self.write_sample_draft(root)
            run_dir = root / "venue-run"
            completed = run_selector("run", "--dir", str(run_dir), "--draft", str(draft), "--offline")
            payload = last_json(completed.stdout)

            self.assertEqual(payload["status"], "ready-with-caveats")
            for name in (
                "draft.json",
                "references.jsonl",
                "papers.jsonl",
                "venues.jsonl",
                "venue_profiles.jsonl",
                "scores.jsonl",
                "delivery.json",
                "recommendation.md",
            ):
                self.assertTrue((run_dir / name).is_file(), name)

            serialized = "\n".join(path.read_text(encoding="utf-8") for path in run_dir.iterdir() if path.is_file())
            self.assertNotIn("SECRET-CODE-NAME-ALPHA", serialized)
            self.assertNotIn(str(draft), serialized)

    def test_validate_fails_incomplete_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "venue-run"
            run_dir.mkdir()
            completed = run_selector("validate", "--dir", str(run_dir), check=False)
            payload = last_json(completed.stdout)

            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(payload["status"], "not-ready")
            self.assertTrue(payload["findings"])

    def test_smoke_output_declares_offline_no_mutation_contract(self) -> None:
        completed = run_selector("smoke")
        payload = last_json(completed.stdout)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["smoke_mode"], "offline")
        self.assertFalse(payload["network_required"])
        self.assertFalse(payload["live_api_attempted"])
        self.assertFalse(payload["package_install_attempted"])
        self.assertFalse(payload["config_written"])
        self.assertFalse(payload["real_secrets_read"])
        self.assertFalse(payload["downloads_attempted"])
        self.assertFalse(payload["mutations_attempted"])

    def test_runtime_manifest_selects_all_os_command_targets(self) -> None:
        manifests = load_manifests()

        self.assertEqual(
            runtime_command_target(manifests, "submission-venue-selector", "linux"),
            "skills/submission-venue-selector/run_submission_venue_selector.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "submission-venue-selector", "macos"),
            "skills/submission-venue-selector/run_submission_venue_selector.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "submission-venue-selector", "wsl"),
            "skills/submission-venue-selector/run_submission_venue_selector.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "submission-venue-selector", "windows"),
            "skills/submission-venue-selector/run_submission_venue_selector.bat",
        )
        self.assertEqual(
            runtime_command_target(manifests, "submission-venue-selector", "windows", "run_skill.ps1"),
            "skills/submission-venue-selector/run_submission_venue_selector.ps1",
        )


if __name__ == "__main__":
    unittest.main()
