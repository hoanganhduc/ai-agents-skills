"""Readiness requires independent controller records bound to actual inputs."""
from __future__ import annotations
import copy
import contextlib
import hashlib
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parents[1] / "canonical/runtime/skills/lax-formalization"
sys.path.insert(0, str(RUNTIME))
import workflow_check as workflow
from lax_formalization import digest
from lax_executor import executor_identity, source_inventory
from public_source import write_private_json


class PaperMetadataTests(unittest.TestCase):
    def registry(self):
        return {"schema_version": "paper-versions.v1", "papers": [
            {"id": "preprint-v1", "kind": "arxiv", "title": "Example", "version": "v1",
             "url": "https://arxiv.org/abs/2601.00001v1", "sha256": "a" * 64},
            {"id": "journal", "kind": "journal", "title": "Extended example",
             "url": "https://doi.org/10.0000/example", "version": "published"}]}

    def test_new_bibliographic_entry_does_not_inherit_formal_acceptance(self):
        result = workflow.render_papers(self.registry())
        self.assertEqual(result.count("not-reviewed"), 2)
        self.assertNotIn("accepted", result)

    def test_unversioned_arxiv_or_fake_acceptance_is_refused(self):
        registry = self.registry(); registry["papers"][0]["url"] = "https://arxiv.org/abs/2601.00001"
        with self.assertRaises(ValueError): workflow.render_papers(registry)
        registry = self.registry(); registry["papers"][0]["correspondence"] = "accepted"
        with self.assertRaises(ValueError): workflow.render_papers(registry)

    def test_missing_hash_or_producer_never_renders_acceptance(self):
        review = {"schema_version": "paper-correspondence.v1", "policy_version": workflow.POLICY,
                  "status": "accepted", "reviewer_run": "reviewer", "paper_id": "journal",
                  "paper_version": "published", "source_commit": "a" * 40, "scope_digest": "b" * 64}
        self.assertNotIn("accepted for commit", workflow.render_papers(self.registry(), [review]))


class PublicBundleTests(unittest.TestCase):
    def test_extra_checksum_listed_file_is_refused_before_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ["source.zip", "metadata.json", "provenance.json", "verification-summary.json",
                         "dependency-inventory.json", "REPRODUCE.md", "SHA256SUMS", "host-debug.txt"]:
                (root / name).write_text("SYNTHETIC_PRIVATE_SENTINEL", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unreviewed-bundle-file"):
                workflow.public_bundle_check(root)

    def test_nested_diagnostics_cannot_replace_typed_public_fields(self):
        from lax_formalization import ENVIRONMENT, MATHLIB_SHA, VERSION
        h = "a" * 64
        receipt = {"schema_version":"lax-verification.v1", "backend":"lax", "status":"passed",
            "machine_status":"passed", "closure_status":"closed", "closure":{"closed":True,"open":[],"witnesses":[]},
            "semantic_status":"pending", "publication_status":"local", "publication_enabled":False,
            "submission_id":"lax-123", "submission_folder":"submission", "targets":["Lax123.Target.target"],
            "source":{"repository":"https://github.com/example/paper","commit":"a"*40,"tree":"b"*40},
            "source_digest":h,"scope_digest":h,"challenge_digest":h,"request_digest":h,
            "database_commit":"c"*40,"database_digest":h,"dependency_digests":{},
            "tools":{"lax":VERSION,"environment":ENVIRONMENT,"mathlib":MATHLIB_SHA,"image":"sha256:"+h,
                     "spec_sha256":h,"package_sha256":h,"executor_sha256":h,
                     "trusted_inputs":{k:h for k in ["elan_launchers","inspector","toolchain","warm"]}},
            "capture":{"files":[],"bytes":0,"sha256":h}, "phases":[],
            "coverage":{"concepts":["Lax123.Target"],"proofs":["Lax123Proofs.Target"]},
            "verified_proofs":[],"limitations":[]}
        for kind in ["tool-key", "coverage-shape"]:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); r = copy.deepcopy(receipt)
                if kind == "tool-key": r["tools"]["debug_note"] = "SYNTHETIC_PRIVATE_SENTINEL"
                else: r["coverage"]["concepts"] = {"debug_note":"SYNTHETIC_PRIVATE_SENTINEL"}
                for name in ["source.zip", "metadata.json", "provenance.json", "dependency-inventory.json", "REPRODUCE.md", "SHA256SUMS"]:
                    (root / name).write_text("{}", encoding="utf-8")
                (root / "verification-summary.json").write_text(json.dumps(r), encoding="utf-8")
                class Loader:
                    def create_module(self, spec): return None
                    def exec_module(self, module): module.validate_bundle = lambda _: {"ok": True}
                import importlib.util
                fake_spec = importlib.util.spec_from_loader("bundle_validator_fixture", Loader())
                with patch.object(workflow.importlib.util, "spec_from_file_location", return_value=fake_spec):
                    expected = "unreviewed-nested-receipt-fields" if kind == "tool-key" else "invalid-public-name-list"
                    with self.assertRaisesRegex(ValueError, expected): workflow.public_bundle_check(root)


@unittest.skipIf(os.name == "nt", "POSIX controller admission and Git qualification")
class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.control = self.root / "control"; self.control.mkdir(mode=0o700)
        self.source = self.root / "research-source"; self.source.mkdir()
        self.project = self.root / "public"; self.project.mkdir()
        for args in [["init", "-q"], ["config", "user.name", "Fixture"], ["config", "user.email", "fixture" + "@" + "example.invalid"],
                     ["remote", "add", "origin", "https://github.com/example/paper"]]: self.git(*args)
        (self.project / "submission/concepts").mkdir(parents=True)
        (self.project / "submission/concepts/Target.lean").write_text("axiom target : True\n", encoding="utf-8")
        (self.project / "README.md").write_text("Public fixture\n", encoding="utf-8")
        self.git("add", "."); self.git("commit", "-qm", "public source")
        self.commit = self.git("rev-parse", "HEAD")
        (self.control / "paper.txt").write_text("Target: True\n", encoding="utf-8")
        self.paper_hash = hashlib.sha256((self.control / "paper.txt").read_bytes()).hexdigest()
        self.inventory = source_inventory(self.project)
        from lax_formalization import tree_inventory
        concept_hash = tree_inventory(self.project / "submission/concepts")["sha256"]
        self.report = {"schema_version": "lax-verification.v1", "backend": "lax", "status": "passed",
            "machine_status": "passed", "closure_status": "closed", "semantic_status": "accepted",
            "source": {"repository": "https://github.com/example/paper", "commit": self.commit},
            "source_digest": self.inventory["sha256"], "scope_digest": digest(["Lax123.Target.target"]),
            "challenge_digest": concept_hash, "targets": ["Lax123.Target.target"], "dependency_digests": {},
            "tools": {"executor_sha256": executor_identity()}, "submission_id": "lax-123"}
        self.job = {"schema_version": "lax-workflow-job.v1", "job_id": "fixture", "operation": "from-existing-lean",
            "execution": "prepare-local", "project_root": str(self.project), "submission": "submission",
            "source_repo": str(self.source),
            "public_origin": "https://github.com/example/paper", "source_commit": self.commit,
            "targets": ["Lax123.Target.target"], "producer_run": "producer", "paper_mode": "link-only",
            "paper": {"id": "paper-v1", "version": "v1", "path": "paper.txt", "sha256": self.paper_hash},
            "verification": "verification.json", "correspondence": "correspondence.json",
            "privacy_review": "privacy.json", "public_inventory": "inventory.json"}
        self.correspondence = {"schema_version": "paper-correspondence.v1", "policy_version": workflow.POLICY,
            "status": "accepted", "reviewer_run": "reviewer", "producer_run": "producer",
            "paper_id": "paper-v1", "paper_version": "v1", "paper_sha256": self.paper_hash,
            "source_commit": self.commit, "source_digest": self.inventory["sha256"],
            "challenge_digest": concept_hash, "scope_digest": self.report["scope_digest"], "dependency_digests": {}}
        self.privacy = {"schema_version": "public-review.v1", "status": "accepted", "reviewer_run": "privacy-reviewer",
            "producer_run": "producer", "source_commit": self.commit, "source_digest": self.inventory["sha256"],
            "history_tip": self.commit, "public_origin": self.job["public_origin"], "policy_version": workflow.POLICY}
        self.save()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.project), *args], encoding="utf-8").strip()

    def save(self):
        for name, data in [("job.json", self.job), ("verification.json", self.report),
                           ("correspondence.json", self.correspondence), ("privacy.json", self.privacy),
                           ("inventory.json", self.inventory)]: write_private_json(self.control / name, data)

    def test_correct_bound_controller_records_produce_local_readiness_only(self):
        r = workflow.check_readiness(self.control / "job.json")
        self.assertEqual(r["status"], "local_ready")
        self.assertFalse(r["publication_enabled"])

    def test_stale_paper_scope_review_or_pending_machine_status_never_ready(self):
        cases = [("paper_sha256", "b" * 64), ("scope_digest", "c" * 64),
                 ("reviewer_run", "producer"), ("status", "pending")]
        baseline = copy.deepcopy(self.correspondence)
        for key, value in cases:
            with self.subTest(key=key):
                self.correspondence = {**baseline, key: value}; self.save()
                self.assertEqual(workflow.check_readiness(self.control / "job.json")["status"], "blocked")
        self.correspondence = baseline; self.report["semantic_status"] = "pending"; self.save()
        self.assertEqual(workflow.check_readiness(self.control / "job.json")["status"], "blocked")

    def test_candidate_owned_review_is_refused(self):
        self.job["correspondence"] = str(self.project / "fake-review.json"); self.save()
        (self.project / "fake-review.json").write_text(json.dumps(self.correspondence), encoding="utf-8")
        self.assertEqual(workflow.check_readiness(self.control / "job.json")["status"], "blocked")

    def test_original_source_cannot_own_the_controller_records(self):
        self.job["source_repo"] = str(self.control); self.save()
        self.assertEqual(workflow.check_readiness(self.control / "job.json")["status"], "blocked")

    def test_existing_source_mode_cannot_omit_original_source_boundary(self):
        self.job.pop("source_repo"); self.save()
        self.assertEqual(workflow.check_readiness(self.control / "job.json")["status"], "blocked")

    def test_malformed_git_config_never_leaks_paths_or_traceback(self):
        (self.project / ".git/config").write_text("[invalid\n", encoding="utf-8")
        r = subprocess.run([sys.executable, "-B", str(RUNTIME / "workflow_check.py"), "readiness",
                            "--job", str(self.control / "job.json"), "--out", str(self.control / "result.json")],
                           capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(r.stdout)["status"], "blocked")
        self.assertEqual(r.stderr, "")

    def test_readme_new_commit_does_not_reuse_old_evidence(self):
        (self.project / "README.md").write_text("New journal link\n", encoding="utf-8")
        self.git("add", "."); self.git("commit", "-qm", "bibliographic metadata")
        self.assertEqual(workflow.check_readiness(self.control / "job.json")["status"], "blocked")
        self.assertEqual(self.git("show", self.commit + ":README.md"), "Public fixture")

    def test_paper_build_cannot_be_skipped_when_tex_is_requested(self):
        self.job["paper_mode"] = "embedded-tex"; self.save()
        self.assertEqual(workflow.check_readiness(self.control / "job.json")["status"], "blocked")

    def test_cli_does_not_overwrite_paper_or_any_existing_output(self):
        paper = self.control / "paper.txt"; before = paper.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            code = workflow.main(["readiness", "--job", str(self.control / "job.json"), "--out", str(paper)])
        self.assertEqual(code, 1)
        self.assertEqual(paper.read_bytes(), before)

    def test_public_ci_summary_never_copies_private_fields_or_marks_semantics_accepted(self):
        self.report["phases"] = [{"log": "PRIVATE_SENTINEL /" + "home/<USER>/private"}]
        result = workflow.public_summary(self.report)
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))
        self.assertEqual(result["semantic_status"], "pending")
        self.report["machine_status"] = "failed"
        with self.assertRaises(ValueError): workflow.public_summary(self.report)


if __name__ == "__main__": unittest.main()
