from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.manifest import REPO_ROOT, load_manifests
from installer.ai_agents_skills.render import render_instruction_block, render_reference_skill_md
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
                    "@inproceedings{doe2022, title={Reconfiguration algorithms for colorings}, author={Doe, B.}, year={2022}, booktitle={Proceedings of Symposium on Discrete Algorithms}, doi={10.1000/soda.2}}",
                ]
            ),
            encoding="utf-8",
        )
        return draft

    def write_recent_fixture(self, root: Path, evidence_level: str = "abstract_inspected", per_venue: int = 3) -> Path:
        fixture = root / "fixtures"
        fixture.mkdir()
        rows = []
        venues = [
            ("Journal of Graph Theory", "jgt", "graph recoloring"),
            ("Proceedings of Symposium on Discrete Algorithms", "soda", "reconfiguration algorithms"),
        ]
        for venue_name, slug, topic in venues:
            for index in range(1, per_venue + 1):
                rows.append(
                    {
                        "venue_name": venue_name,
                        "title": f"Recent {topic} comparator {index}",
                        "year": "2025",
                        "doi": f"10.1000/recent-{slug}-{index}",
                        "provider": "fixture",
                        "provider_work_id": f"fixture:{slug}-2025-{index}",
                        "venue_source_id": f"fixture:{slug}",
                        "sampling_method": "fixture-provider-cache",
                        "evidence_level": evidence_level,
                        "abstract_available": evidence_level in {"abstract_inspected", "full_text_inspected"},
                        "full_text_status": "available" if evidence_level == "full_text_inspected" else "not_requested",
                        "article_type": "research-article",
                        "exclusion_status": "included",
                        "topic_distance_rationale": "same or adjacent graph reconfiguration topic",
                        "inspection_scope": evidence_level,
                        "topic_similarity": 0.8,
                        "matched_terms": topic.split(),
                    }
                )
        with (fixture / "recent_papers.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        return fixture

    def test_offline_run_is_not_deliverable_without_comparator_evidence_or_raw_draft_leak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = self.write_sample_draft(root)
            run_dir = root / "venue-run"
            completed = run_selector("run", "--dir", str(run_dir), "--draft", str(draft), "--offline", check=False)
            payload = last_json(completed.stdout)

            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(payload["status"], "not-ready")
            for name in (
                "draft.json",
                "references.jsonl",
                "papers.jsonl",
                "venues.jsonl",
                "venue_profiles.jsonl",
                "scores.jsonl",
                "scorecards.jsonl",
                "base_rate_sources.jsonl",
                "chance_estimates.jsonl",
                "delivery.json",
                "recommendation.md",
            ):
                self.assertTrue((run_dir / name).is_file(), name)

            serialized = "\n".join(path.read_text(encoding="utf-8") for path in run_dir.iterdir() if path.is_file())
            self.assertNotIn("SECRET-CODE-NAME-ALPHA", serialized)
            self.assertNotIn(str(draft), serialized)
            self.assertIn("incomplete analysis", (run_dir / "recommendation.md").read_text(encoding="utf-8"))
            self.assertIn("Estimated acceptance chance", (run_dir / "recommendation.md").read_text(encoding="utf-8"))
            delivery = json.loads((run_dir / "delivery.json").read_text(encoding="utf-8"))
            self.assertEqual(delivery["delivery_status"], "not-ready")

    def test_metadata_only_fixture_cannot_support_ready_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = self.write_sample_draft(root)
            fixture = self.write_recent_fixture(root, evidence_level="metadata_only", per_venue=3)
            run_dir = root / "venue-run"
            completed = run_selector(
                "run",
                "--dir",
                str(run_dir),
                "--draft",
                str(draft),
                "--offline",
                "--fixture-dir",
                str(fixture),
                check=False,
            )
            payload = last_json(completed.stdout)

            self.assertNotEqual(payload["status"], "ready")
            scores = [
                json.loads(line)
                for line in (run_dir / "scores.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(all(score["fit_band"] != "strong fit" for score in scores))
            estimates = [
                json.loads(line)
                for line in (run_dir / "chance_estimates.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(all("-" in estimate["display_interval"] for estimate in estimates))
            self.assertTrue(all(estimate["confidence"] == "low" for estimate in estimates))

    def test_fixture_comparator_evidence_can_support_ready_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = self.write_sample_draft(root)
            fixture = self.write_recent_fixture(root, evidence_level="abstract_inspected", per_venue=3)
            run_dir = root / "venue-run"
            completed = run_selector(
                "run",
                "--dir",
                str(run_dir),
                "--draft",
                str(draft),
                "--offline",
                "--fixture-dir",
                str(fixture),
            )
            payload = last_json(completed.stdout)

            self.assertEqual(payload["status"], "ready")
            recent_rows = [
                json.loads(line)
                for line in (run_dir / "recent_papers.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(recent_rows)
            self.assertTrue(all(row["source_ids"] for row in recent_rows))
            self.assertTrue(all(row["query_id"] for row in recent_rows))
            self.assertTrue(all(row["evidence_ids"] for row in recent_rows))
            self.assertTrue(all(row["article_type"] for row in recent_rows))
            self.assertTrue(all(row["topic_distance_rationale"] for row in recent_rows))
            self.assertTrue(all(row["provider"] != "offline" for row in recent_rows))
            scores = [
                json.loads(line)
                for line in (run_dir / "scores.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            for score in scores:
                recent_criteria = [c for c in score["criteria"] if c["criterion_id"] == "comparator_pattern_fit"]
                self.assertEqual(len(recent_criteria), 1)
                self.assertTrue(recent_criteria[0]["evidence_ids"])
                self.assertIn(score["fit_band"], {"strong fit", "plausible fit"})
            report = (run_dir / "recommendation.md").read_text(encoding="utf-8")
            self.assertIn("Estimated acceptance chance if submitted as-is", report)
            self.assertIn("heuristic estimates, not predictions", report)

    def test_network_requires_privacy_gate_and_explicit_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = self.write_sample_draft(root)
            run_dir = root / "venue-run"
            run_selector("init", "--dir", str(run_dir), "--draft", str(draft))
            run_selector("extract", "--dir", str(run_dir), "--draft", str(draft))

            no_guard = run_selector(
                "resolve",
                "--dir",
                str(run_dir),
                "--allow-network",
                "--allow-provider",
                "openalex",
                check=False,
            )
            self.assertNotEqual(no_guard.returncode, 0)
            self.assertIn("privacy-gate", no_guard.stderr)

            run_selector("privacy-gate", "--dir", str(run_dir), "--draft", str(draft), "--allow-network")
            no_provider = run_selector("resolve", "--dir", str(run_dir), "--allow-network", check=False)
            self.assertNotEqual(no_provider.returncode, 0)
            self.assertIn("--allow-provider", no_provider.stderr)

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

    def test_docs_and_generated_adapters_expose_no_shallow_shortlist_gate(self) -> None:
        manifests = load_manifests()
        spec = manifests["skills"]["skills"]["submission-venue-selector"]
        skill_source = REPO_ROOT / "canonical" / "skills" / "submission-venue-selector" / "SKILL.md"
        paths = [
            skill_source,
            skill_source.parent / "references" / "report-contract.md",
            skill_source.parent / "references" / "scoring-rubric.md",
            skill_source.parent / "references" / "provider-policy.md",
            skill_source.parent / "references" / "privacy-and-network-policy.md",
            skill_source.parent / "agents" / "openai.yaml",
        ]
        for path in paths:
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertIn("comparator", text.lower())

        adapter = render_reference_skill_md("submission-venue-selector", spec, "codex", skill_source)
        instruction_block = render_instruction_block("submission-venue-selector", spec)
        self.assertIn("comparator-paper evidence", adapter)
        self.assertIn("not-ready", adapter)
        self.assertIn("comparator-paper evidence", instruction_block)
        self.assertIn("not-ready", instruction_block)


if __name__ == "__main__":
    unittest.main()
