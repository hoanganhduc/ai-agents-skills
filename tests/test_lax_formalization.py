"""Nonpublishing Lax admission, closure, source and executor regressions."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
from pathlib import Path
import sys
sys.dont_write_bytecode = True
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "canonical/runtime/skills/lax-formalization"
sys.path.insert(0, str(RUNTIME))
spec = importlib.util.spec_from_file_location("lax_formalization", RUNTIME / "lax_formalization.py")
assert spec and spec.loader
lax = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lax
spec.loader.exec_module(lax)


class ClosureTests(unittest.TestCase):
    def proof(self, name, conclusion, assumptions=(), **extra):
        return {"id": name, "conclusion": conclusion, "assumptions": list(assumptions),
                "state": "registered", "independently_verified": True, **extra}

    def test_registered_unproved_is_not_grounded(self):
        r = lax.proof_closure(["A"], [])
        self.assertFalse(r["closed"])
        self.assertEqual(r["open"], ["A"])

    def test_unfounded_cycle_remains_open(self):
        r = lax.proof_closure(["A"], [self.proof("pa", "A", ["B"]), self.proof("pb", "B", ["A"])])
        self.assertFalse(r["closed"])

    def test_cycle_with_foundation_closes(self):
        r = lax.proof_closure(["A"], [self.proof("pa", "A", ["B"]), self.proof("pb", "B", ["A"]), self.proof("base", "B")])
        self.assertTrue(r["closed"])
        self.assertIn("base", r["witnesses"])

    def test_unverified_published_proof_is_not_evidence(self):
        self.assertFalse(lax.proof_closure(["A"], [self.proof("p", "A", independently_verified=False)])["closed"])

    def test_select_registered_alternative_to_draft(self):
        r = lax.proof_closure(["A"], [self.proof("a-draft", "A", state="draft"), self.proof("z-stable", "A")])
        self.assertEqual(r["witnesses"], ["z-stable"])

    def test_local_candidate_can_ground_own_claim(self):
        r = lax.proof_closure(["A"], [self.proof("p", "A", state="local")])
        self.assertTrue(r["closed"])

    def test_missing_assumption_is_not_ignored(self):
        r = lax.proof_closure(["A"], [self.proof("p", "A", ["missing"])])
        self.assertFalse(r["closed"])
        self.assertIn("missing", r["open"])

    def test_reject_duplicate_proof_identity(self):
        with self.assertRaises(ValueError):
            lax.proof_closure(["A"], [self.proof("p", "A"), self.proof("p", "B")])


class FileBoundaryTests(unittest.TestCase):
    def test_fingerprint_changes_with_source(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "X.lean").write_text("theorem t : True := trivial", encoding="utf-8")
            first = lax.tree_inventory(root)
            (root / "X.lean").write_text("theorem t : False := sorry", encoding="utf-8")
            self.assertNotEqual(first["sha256"], lax.tree_inventory(root)["sha256"])

    def test_fingerprint_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "x").symlink_to("/etc/passwd")
            with self.assertRaises(ValueError): lax.tree_inventory(root)

    def archive(self, names):
        b = io.BytesIO()
        with tarfile.open(fileobj=b, mode="w") as t:
            for name in names:
                info = tarfile.TarInfo(name); info.size = 1
                t.addfile(info, io.BytesIO(b"x"))
        b.seek(0); return b

    def test_archive_escape_refused_before_extraction(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError): lax.extract_archive(self.archive(["../x"]), Path(d))
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_archive_duplicate_and_case_collision_refused(self):
        for names in [["x", "x"], ["A.lean", "a.lean"]]:
            with self.subTest(names=names), tempfile.TemporaryDirectory() as d:
                with self.assertRaises(ValueError): lax.extract_archive(self.archive(names), Path(d))

    def test_archive_size_bound(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError): lax.extract_archive(self.archive(["a", "b"]), Path(d), max_bytes=1)

    def test_normal_archive(self):
        with tempfile.TemporaryDirectory() as d:
            lax.extract_archive(self.archive(["a/x.lean"]), Path(d))
            self.assertEqual((Path(d) / "a/x.lean").read_bytes(), b"x")


class CatalogTests(unittest.TestCase):
    def test_catalog_search_never_certifies_registration(self):
        with tempfile.TemporaryDirectory() as d:
            rec = Path(d) / "lax-1"; rec.mkdir()
            (rec / "record.json").write_text(json.dumps({"id": "lax-1", "state": "registered", "source": {"repository": "https://github.com/example/repo", "commit": "a"*40, "folder": "."}}), encoding="utf-8")
            (rec / "build-output.json").write_text(json.dumps({"id": "lax-1", "inputs": {"manifest": {"leanVersion": "v4.33.0", "mathlibVersion": lax.MATHLIB_SHA, "title": "Finite patterns"}}, "concepts": [{"id": "Lax1.Pattern", "title": "patterns", "statements": [{"id": "Lax1.Pattern.t", "signature": "t : True"}]}], "proofs": []}), encoding="utf-8")
            r = lax.search_catalog(Path(d), "patterns", "v4.33.0")
            self.assertEqual(len(r["candidates"]), 1)
            self.assertEqual(r["candidates"][0]["verification_status"], "not_verified")

    def test_unavailable_catalog_not_global_absence(self):
        r = lax.search_catalog(Path("/nonexistent/lax-fixture"), "x", "v4.33.0")
        self.assertEqual(r["coverage_status"], "unavailable")
        self.assertNotEqual(r.get("recommendation"), "formalize-new")


class ExecutorPolicyTests(unittest.TestCase):
    def test_candidate_git_fsmonitor_cannot_execute(self):
        import lax_executor as ex
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); root = base / "repo"; root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            marker = base / "executed"
            hook = base / "fsmonitor"
            hook.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nprintf "token\\0"\n', encoding="utf-8")
            hook.chmod(0o755)
            subprocess.run(["git", "-C", str(root), "config", "core.fsmonitor", str(hook)], check=True)
            with self.assertRaisesRegex(ValueError, "unqualified local Git"):
                ex.git(root, "status", "--porcelain")
            self.assertFalse(marker.exists(), "candidate fsmonitor executed on host")

    def test_candidate_clean_filter_is_refused_before_status(self):
        import lax_executor as ex
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "filter.host.clean", "not-a-real-command"], check=True)
            (root / ".gitattributes").write_text("* filter=host\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unqualified local Git"):
                ex.git(root, "status", "--porcelain")

    def test_fingerprint_cache_never_replaces_content_pin(self):
        import lax_executor as ex
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            cfg={"elan_home":str(root/'elan'),"warm_root":str(root/'warm'),"tools_root":str(root/'tools')}
            paths=[root/'elan/toolchains/leanprover--lean4---v4.33.0',root/'elan/bin',
                   root/'warm/v4.33.0-db584cd6d46c',root/'tools']
            for p in paths:p.mkdir(parents=True);(p/'input').write_text('original', encoding="utf-8")
            before=ex.trusted_inputs(cfg)
            self.assertEqual(ex.trusted_inputs(cfg),before)
            (paths[-1]/'input').write_text('replaced', encoding="utf-8")
            self.assertNotEqual(ex.trusted_inputs(cfg),before)
            (paths[-1]/'.git').mkdir();(paths[-1]/'.git/hidden').write_text('hidden', encoding="utf-8")
            (paths[-1]/'linked').symlink_to('.git/hidden')
            with self.assertRaisesRegex(ValueError,'excluded metadata'):ex.trusted_inputs(cfg)

    def test_ancestor_repository_is_not_admitted(self):
        import lax_executor as ex
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);subprocess.run(['git','init','-q',str(root)],check=True)
            child=root/'child';child.mkdir()
            with self.assertRaisesRegex(ValueError,'ancestor discovery'):ex.git(child,'status','--porcelain')

    def test_github_checkout_gc_setting_is_admitted_but_not_arbitrary_gc(self):
        import lax_executor as ex
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);subprocess.run(['git','init','-q',str(root)],check=True)
            subprocess.run(['git','-C',str(root),'config','gc.auto','0'],check=True)
            self.assertEqual(ex.git(root,'status','--porcelain'),'')
            subprocess.run(['git','-C',str(root),'config','gc.auto','1'],check=True)
            with self.assertRaisesRegex(ValueError,'unqualified local Git'):ex.git(root,'status','--porcelain')

    def test_capture_cannot_shadow_concept_or_mathlib(self):
        import lax_executor as ex
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "lib/Mathlib").mkdir(parents=True)
            (root / "lib/Mathlib/Logic.olean").write_bytes(b"forged")
            with self.assertRaises(ValueError):
                ex.admit_capture(root, {"rootModule": "Lax1Proofs", "modules": ["Lax1Proofs.Result"]}, root)

    def test_container_boundary_has_no_network_or_engine_mount(self):
        args = lax.container_options("test-123", "sha256:" + "a"*64, [], writable=False)
        self.assertIn("--network=none", args)
        self.assertIn("--read-only", args)
        self.assertIn("--cap-drop=ALL", args)
        self.assertFalse(any("docker.sock" in x for x in args))
        self.assertFalse(any(x == "--privileged" for x in args))

    def test_publication_commands_fail(self):
        for verb in ["submit", "register", "publish", "upload", "login"]:
            with self.subTest(verb=verb):
                self.assertEqual(lax.main([verb]), 2)


if __name__ == "__main__":
    unittest.main()
