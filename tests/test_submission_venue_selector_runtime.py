from __future__ import annotations

import json
import os
import subprocess
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer.ai_agents_skills.manifest import REPO_ROOT, load_manifests
from installer.ai_agents_skills.render import render_instruction_block, render_reference_skill_md
from installer.ai_agents_skills.runtime_smoke import runtime_command_target
from tests.test_never_matching_predicates import load_module


RUNTIME_DIR = REPO_ROOT / "canonical" / "runtime" / "skills" / "submission-venue-selector"
SCRIPT = RUNTIME_DIR / "submission_venue_selector.py"


def run_selector(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
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

    def test_provider_report_does_not_claim_network_without_a_privacy_gate(self) -> None:
        """`provider_status.json` must not authorize what the next command refuses.

        `provider_records` derived `network_allowed` from `--allow-network` and
        `--allow-provider` alone, while `ensure_network_allowed` additionally
        requires an ok privacy guard in the workspace. So `providers` recorded
        `provider_status: "ok"` and `network_allowed: true` for a workspace whose
        very next `resolve` exited 2 with "network access requires a prior ok
        privacy-gate in this workspace". One workspace, two artifacts, opposite
        answers to the same question.
        """

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = self.write_sample_draft(root)
            run_dir = root / "venue-run"
            run_selector("init", "--dir", str(run_dir), "--draft", str(draft))
            run_selector("extract", "--dir", str(run_dir), "--draft", str(draft))
            self.assertFalse((run_dir / "guards.jsonl").exists())

            reported = run_selector(
                "providers", "--dir", str(run_dir),
                "--allow-network", "--allow-provider", "openalex",
            )
            row = next(
                entry
                for entry in last_json(reported.stdout)["providers"]
                if entry["provider"] == "openalex"
            )
            self.assertFalse(row["privacy_gate_ok"])
            self.assertFalse(row["network_allowed"])
            self.assertEqual(row["provider_status"], "skipped")

            # ... and that is what the workspace actually does.
            refused = run_selector(
                "resolve", "--dir", str(run_dir),
                "--allow-network", "--allow-provider", "openalex", check=False,
            )
            self.assertEqual(refused.returncode, 2)
            self.assertIn("privacy-gate", refused.stderr)

    def test_provider_report_claims_network_once_the_gate_is_ok(self) -> None:
        """The control: the new predicate is not hardwired to refuse."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = self.write_sample_draft(root)
            run_dir = root / "venue-run"
            run_selector("init", "--dir", str(run_dir), "--draft", str(draft))
            run_selector("extract", "--dir", str(run_dir), "--draft", str(draft))
            run_selector("privacy-gate", "--dir", str(run_dir), "--draft", str(draft), "--allow-network")

            reported = run_selector(
                "providers", "--dir", str(run_dir),
                "--allow-network", "--allow-provider", "openalex",
            )
            row = next(
                entry
                for entry in last_json(reported.stdout)["providers"]
                if entry["provider"] == "openalex"
            )
            self.assertTrue(row["privacy_gate_ok"])
            self.assertTrue(row["network_allowed"])
            self.assertEqual(row["provider_status"], "ok")

    def test_permission_flags_for_absent_capabilities_are_refused(self) -> None:
        """A gate that grants nothing must say so rather than parse cleanly.

        `--allow-downloads`, `--allow-zotero-mutation`, and
        `--allow-unpaywall-email` reached `add_argument` and were never read
        anywhere in the tree. No code path in the module downloads, mutates
        Zotero, or sends an Unpaywall email -- the skill's own routing boundary
        sends all three elsewhere -- so passing one used to succeed and grant
        nothing, and a caller could not tell that apart from "enabled, and there
        was nothing to do".
        """

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "venue-run"
            for flag, routed_to in (
                ("--allow-downloads", "getscipapers-requester"),
                ("--allow-zotero-mutation", "zotero"),
                ("--allow-unpaywall-email", "unpaywall"),
            ):
                with self.subTest(flag=flag):
                    completed = run_selector(
                        "providers", "--dir", str(run_dir), flag, check=False
                    )
                    self.assertEqual(completed.returncode, 2, completed.stdout)
                    self.assertIn(flag, completed.stderr)
                    self.assertIn(routed_to, completed.stderr)

            # The control: the same command without the flag still works.
            ok = run_selector("providers", "--dir", str(run_dir))
            self.assertTrue(last_json(ok.stdout)["providers"])

    def test_cache_and_force_flags_are_refused(self) -> None:
        """Four more flags that parsed cleanly and controlled nothing.

        `--cache-dir`, `--refresh-cache` and `--no-cache` describe a provider
        cache this runtime does not have: `.cache` appears once in the module,
        in `command_purge`, which deletes a directory nothing creates. Passing
        `--refresh-cache` and `--no-cache` together used to succeed, even though
        the two ask for opposite things. `--force` was the worst of the four --
        it reads as an override of the evidence gate `validate` enforces, and a
        caller who reached for it had no way to learn it was discarded.
        """

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "venue-run"
            for flag, extra in (
                ("--cache-dir", [str(Path(tmp) / "cache")]),
                ("--refresh-cache", []),
                ("--no-cache", []),
                ("--force", []),
            ):
                with self.subTest(flag=flag):
                    completed = run_selector(
                        "providers", "--dir", str(run_dir), flag, *extra, check=False
                    )
                    self.assertEqual(completed.returncode, 2, completed.stdout)
                    self.assertIn(flag, completed.stderr)
                    self.assertIn("not supported by this skill", completed.stderr)

            # Contradictory pair: refusing the first is enough to stop the run.
            both = run_selector(
                "providers", "--dir", str(run_dir), "--refresh-cache", "--no-cache",
                check=False,
            )
            self.assertEqual(both.returncode, 2, both.stdout)

            # The control: the same command without the flags still works, and
            # the run it produces holds no cache for those flags to have meant.
            ok = run_selector("providers", "--dir", str(run_dir))
            self.assertTrue(last_json(ok.stdout)["providers"])
            self.assertEqual([p.name for p in run_dir.rglob(".cache")], [])

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
            "skills/submission-venue-selector/run_submission_venue_selector.ps1",
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



class ScoreRawAndNormalizedTests(unittest.TestCase):
    """A score row's `raw_score` is the ordinal sum; `normalized_score` scales it.

    The criteria inside the same row already use that pair honestly -- `raw_score` is
    an overlap count, a comparator count, or the string `not_scored`, and
    `ordinal_score` is the 0-4 anchor.  The row itself published `normalized` under
    both names, so `raw_score` carried a 0-1 fraction under the one key that one
    nesting level down means the opposite.
    """

    def _scores(self) -> list[dict[str, object]]:
        helper = SubmissionVenueSelectorRuntimeTests(
            "test_smoke_output_declares_offline_no_mutation_contract"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = helper.write_sample_draft(root)
            fixture = helper.write_recent_fixture(
                root, evidence_level="abstract_inspected", per_venue=3
            )
            run_dir = root / "venue-run"
            run_selector(
                "run", "--dir", str(run_dir), "--draft", str(draft),
                "--offline", "--fixture-dir", str(fixture),
            )
            rows = [
                json.loads(line)
                for line in (run_dir / "scores.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        self.assertTrue(rows, "the fixture run produced no score rows")
        return rows

    def test_raw_score_is_the_sum_of_the_anchored_ordinals(self) -> None:
        for score in self._scores():
            with self.subTest(venue=score["venue_id"]):
                scored = [c for c in score["criteria"] if c["raw_score"] != "not_scored"]
                self.assertEqual(
                    score["raw_score"],
                    sum(c["ordinal_score"] for c in scored),
                )

    def test_normalized_score_is_that_sum_over_its_maximum(self) -> None:
        for score in self._scores():
            with self.subTest(venue=score["venue_id"]):
                scored = [c for c in score["criteria"] if c["raw_score"] != "not_scored"]
                self.assertEqual(
                    score["normalized_score"],
                    round(score["raw_score"] / (4 * len(scored)), 4),
                )

    def test_the_two_are_not_the_same_number(self) -> None:
        """The regression, stated directly: one key per quantity."""

        for score in self._scores():
            with self.subTest(venue=score["venue_id"]):
                self.assertNotEqual(
                    score["raw_score"],
                    score["normalized_score"],
                    "raw_score is publishing the normalized value",
                )

    def test_the_two_live_on_different_scales(self) -> None:
        for score in self._scores():
            with self.subTest(venue=score["venue_id"]):
                self.assertIsInstance(score["raw_score"], int)
                self.assertGreater(score["raw_score"], 1)
                self.assertLessEqual(score["normalized_score"], 1.0)

    def test_the_criterion_level_pair_is_unchanged(self) -> None:
        """The control: criterion `raw_score` was already the measurement, and the
        `not_scored` sentinel the report gate reads must survive."""

        for score in self._scores():
            with self.subTest(venue=score["venue_id"]):
                by_id = {c["criterion_id"]: c for c in score["criteria"]}
                self.assertEqual(
                    by_id["presentation_discourse_alignment"]["raw_score"], "not_scored"
                )
                self.assertIsNone(
                    by_id["presentation_discourse_alignment"]["ordinal_score"]
                )
                self.assertIsInstance(by_id["comparator_pattern_fit"]["raw_score"], int)

@unittest.skipUnless(os.name == "posix", "creation modes and umask are POSIX")
class WorkspaceObjectsArePrivateAtCreationTests(unittest.TestCase):
    """Every workspace object is private from the instant it exists.

    The workspace holds an unpublished manuscript draft, the claims extracted
    from it, and the venue evidence gathered for it. ``ensure_workspace``,
    ``write_json`` and ``write_jsonl`` created each object under the caller's
    umask and narrowed it a statement later, so under the ordinary 022 the
    workspace existed at 0755 and every file at 0644 first, and a reader who
    opened one inside that window kept a descriptor the chmod cannot revoke.
    Both chmods also swallowed their own failure, so a filesystem that refuses
    them left the draft world-readable and reported nothing.
    """

    def setUp(self) -> None:
        self.module = load_module(
            "submission_venue_selector", SCRIPT, extra_syspath=RUNTIME_DIR
        )
        # 022 is what the defect needs to show; under 077 every mode below is
        # already private and the test would pass against the old code.
        previous_umask = os.umask(0o022)
        self.addCleanup(os.umask, previous_umask)
        workspace_root = tempfile.TemporaryDirectory()
        self.addCleanup(workspace_root.cleanup)
        self.root = Path(workspace_root.name)

    @staticmethod
    def _mode(path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def test_objects_are_created_private_rather_than_narrowed_afterwards(self) -> None:
        workspace = self.root / "venue-run"
        # With chmod turned into a no-op, the mode left on disk is the mode the
        # object was created with, which is the property under test.
        with mock.patch.object(Path, "chmod"):
            self.module.ensure_workspace(workspace)
            self.module.write_json(workspace / "draft.json", {"title": "unpublished"})
            self.module.write_jsonl(workspace / "papers.jsonl", [{"doi": "10.1/x"}])

        self.assertEqual(self._mode(workspace), 0o700)
        self.assertEqual(self._mode(workspace / "draft.json"), 0o600)
        self.assertEqual(self._mode(workspace / "papers.jsonl"), 0o600)

    def test_a_refused_chmod_is_reported_rather_than_swallowed(self) -> None:
        workspace = self.root / "denied"
        with mock.patch.object(
            Path, "chmod", side_effect=PermissionError(13, "Permission denied")
        ):
            with self.assertRaises(PermissionError):
                self.module.ensure_workspace(workspace)
            with self.assertRaises(PermissionError):
                self.module.write_json(workspace / "draft.json", {"title": "x"})
            with self.assertRaises(PermissionError):
                self.module.write_jsonl(workspace / "papers.jsonl", [{"doi": "10.1/x"}])

    def test_a_file_an_earlier_run_left_readable_is_still_narrowed(self) -> None:
        # O_CREAT carries no mode for a file that already exists, so the chmod
        # is what rescues a file an earlier version of this module left open.
        workspace = self.root / "legacy"
        self.module.ensure_workspace(workspace)
        stale = workspace / "draft.json"
        stale.write_text("{}\n", encoding="utf-8")
        stale.chmod(0o644)

        self.module.write_json(stale, {"title": "unpublished"})

        self.assertEqual(self._mode(stale), 0o600)


@unittest.skipUnless(os.name == "posix", "creation modes and umask are POSIX")
class WorkspaceIsPrivateWhicheverVerbCreatesItTests(unittest.TestCase):
    """The run workspace is 0700 no matter which verb brings it into being.

    ``ensure_workspace`` states the intent -- the directory holding an
    unpublished draft is owner-private -- but only ``init`` and ``plan`` call
    it. Every other verb reaches the workspace through ``write_json`` or
    ``write_jsonl``, whose ``path.parent.mkdir`` honoured the umask, so a
    workspace first touched by ``providers``, ``venues``, ``score``,
    ``validate``, ``purge``, ``expand`` or ``recent`` was created 0755 and
    stayed there until an ``init`` or ``plan`` happened to run in it.
    """

    def setUp(self) -> None:
        self.module = load_module("svs_workspace_parent_mode", SCRIPT)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        previous = os.umask(0o022)
        self.addCleanup(os.umask, previous)

    @staticmethod
    def _mode(path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def test_write_json_creates_the_workspace_private(self) -> None:
        workspace = self.root / "fresh-json"
        self.assertFalse(workspace.exists())

        self.module.write_json(workspace / "provider_status.json", {"providers": []})

        self.assertEqual(self._mode(workspace), 0o700)
        self.assertEqual(self._mode(workspace / "provider_status.json"), 0o600)

    def test_write_jsonl_creates_the_workspace_private(self) -> None:
        workspace = self.root / "fresh-jsonl"
        self.assertFalse(workspace.exists())

        self.module.write_jsonl(workspace / "venues.jsonl", [{"venue_id": "V1"}])

        self.assertEqual(self._mode(workspace), 0o700)
        self.assertEqual(self._mode(workspace / "venues.jsonl"), 0o600)

    def test_a_verb_that_never_calls_ensure_workspace_still_creates_it_private(self) -> None:
        # End to end, through the CLI: providers --check is a preflight verb,
        # so running it against a directory that does not exist yet is an
        # ordinary thing to do, and it does not go through ensure_workspace.
        workspace = self.root / "preflight"

        run_selector("providers", "--dir", str(workspace))

        self.assertEqual(self._mode(workspace), 0o700)

    def test_an_existing_workspace_keeps_the_mode_it_already_has(self) -> None:
        # The complement: mkdir's mode applies at creation only, so a directory
        # the operator chose to share is not silently re-narrowed by a write.
        # Narrowing an existing directory stays ensure_workspace's job.
        workspace = self.root / "existing"
        workspace.mkdir(mode=0o750)

        self.module.write_json(workspace / "run_status.json", {"stage": "score"})

        self.assertEqual(self._mode(workspace), 0o750)

    def test_the_umask_is_really_022_for_these_cases(self) -> None:
        # Anchor: with a 077 umask every directory would be 0700 anyway and the
        # tests above would pass against the unfixed source.
        probe = self.root / "umask-probe"
        probe.mkdir()
        self.assertEqual(self._mode(probe), 0o755)


if __name__ == "__main__":
    unittest.main()
