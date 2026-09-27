"""Source selection is read-only, explicit and independent of old Git history."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

RUNTIME = Path(__file__).resolve().parents[1] / "canonical/runtime/skills/lax-formalization"
sys.path.insert(0, str(RUNTIME))
import public_source as public
from tex_sanitize import sanitize_tex


class TexSanitizerTests(unittest.TestCase):
    def test_private_comments_removed_without_adding_spaces(self):
        result = sanitize_tex("A% private exchange\nB\n% confidential\nC\\% shown\n")
        self.assertEqual(result["text"], "A%\nB\n%\nC\\% shown\n")
        self.assertEqual(result["removed_comments"], 2)

    def test_literal_percent_and_lax_markers_preserved(self):
        text = "\\verb|x%y|\n\\begin{verbatim}\nx%y\n\\end{verbatim}\n% lax begin Lax123.Test\nX\n% lax end\n"
        self.assertEqual(sanitize_tex(text)["text"], text)

    def test_hidden_notes_are_removed_and_ambiguous_input_refused(self):
        result = sanitize_tex("A\n\\begin{comment}\nsecret\n\\end{comment}\nB\n")
        self.assertNotIn("secret", result["text"])
        for text in [r"\catcode`\%=12", r"\input{\privatefile}", r"\todo{secret}",
                     "^^25 private", "% lax beginning bad", r"\verb|unterminated",
                     "\\iffalse% \\fi\nPRIVATE_SENTINEL\n\\fi\nPublic\n",
                     "A\\begin{comment}\nsecret\\end{comment}still secret\n",
                     "\\lstinline|x%y|\n", "\\mintinline{tex}|x%y|\n"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                sanitize_tex(text)

    def test_license_comment_requires_explicit_review(self):
        text = "% Copyright example contributor\nX\n"
        with self.assertRaises(ValueError): sanitize_tex(text)
        self.assertEqual(sanitize_tex(text, keep_comment_lines=[1])["text"], text)


@unittest.skipIf(os.name == "nt", "fresh export currently qualified on POSIX")
class PublicSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "private-source"; self.source.mkdir()
        self.control = self.root / "control"; self.control.mkdir(mode=0o700)
        (self.source / "Useful.lean").write_text("import Mathlib\ntheorem keep : True := trivial\n", encoding="utf-8")
        (self.source / "Broken.lean").write_text("private unrelated invalid code", encoding="utf-8")
        (self.source / "paper.tex").write_text("Hello% private conversation\nworld\n", encoding="utf-8")
        (self.source / ".git").mkdir(); (self.source / ".git/private-history").write_text("old secret", encoding="utf-8")
        self.out = self.root / "public-repo"

    def plan(self, extra=()):
        files = [{"source": "Useful.lean", "target": "Useful.lean", "transform": "copy",
                  "reason": "selected target", "provenance": "original author; Apache-2.0"}, *extra]
        p = public.plan_selection(self.source, self.out, files, job_id="fixture",
                                  public_origin="https://github.com/example/paper")
        path = self.control / "plan.json"
        public.write_private_json(path, p)
        public.approve_plan(path, "independent-review-run")
        return path

    def test_reuses_only_selected_source_and_does_not_modify_original(self):
        before = (self.source / "Useful.lean").read_bytes()
        p = self.plan([{"source": "paper.tex", "target": "paper/main.tex", "transform": "tex",
                        "reason": "paper source", "provenance": "original author"}])
        self.assertEqual(public.export_plan(p)["status"], "dry-run")
        self.assertFalse(self.out.exists())
        result = public.export_plan(p, apply=True)
        self.assertEqual(result["status"], "exported")
        self.assertEqual((self.out / "Useful.lean").read_bytes(), before)
        self.assertNotEqual((self.out / "Useful.lean").stat().st_ino, (self.source / "Useful.lean").stat().st_ino)
        self.assertNotIn("private conversation", (self.out / "paper/main.tex").read_text(encoding="utf-8"))
        self.assertFalse((self.out / ".git").exists())
        self.assertFalse((self.out / "Broken.lean").exists())
        self.assertEqual((self.source / "Useful.lean").read_bytes(), before)
        self.assertIn("private conversation", (self.source / "paper.tex").read_text(encoding="utf-8"))

    def test_changed_input_and_unreviewed_plan_never_create_destination(self):
        p = self.plan()
        (self.source / "Useful.lean").write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError): public.export_plan(p, apply=True)
        self.assertFalse(self.out.exists())
        p = self.plan(); payload = json.loads(p.read_text(encoding="utf-8")); payload.pop("approval")
        public.write_private_json(p, payload)
        with self.assertRaises(ValueError): public.export_plan(p, apply=True)
        self.assertFalse(self.out.exists())

    def test_destination_collision_and_private_paths_refused(self):
        p = self.plan(); self.out.mkdir(); (self.out / "user.txt").write_text("preserve", encoding="utf-8")
        with self.assertRaises(ValueError): public.export_plan(p, apply=True)
        self.assertEqual((self.out / "user.txt").read_text(encoding="utf-8"), "preserve")
        for target in ["../escape", ".git/config", "A/../../escape", "A\\escape", "AUX.txt"]:
            with self.subTest(target=target), self.assertRaises(ValueError):
                public.plan_selection(self.source, self.root / "new", [{"source": "Useful.lean", "target": target,
                    "transform": "copy", "reason": "target", "provenance": "author"}], job_id="x",
                    public_origin="https://github.com/example/paper")

    def test_symlink_and_git_history_cannot_be_selected(self):
        (self.source / "link.lean").symlink_to(self.source / "Useful.lean")
        for source in ["link.lean", ".git/private-history"]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.plan([{"source": source, "target": "Bad.lean", "transform": "copy",
                            "reason": "bad", "provenance": "author"}])

    def test_import_scan_is_only_a_candidate_list(self):
        result = public.inspect_source(self.source)
        self.assertEqual(result["analysis_status"], "static-candidates-only")
        entry = next(f for f in result["files"] if f["path"] == "Useful.lean")
        self.assertEqual(entry["imports"], ["Mathlib"])
        self.assertFalse(any(f["path"].startswith(".git/") for f in result["files"]))

    def test_real_git_inventory_classifies_without_running_hooks(self):
        source = self.root / "git-source"; source.mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        (source / "A.lean").write_text("theorem t : True := trivial\n", encoding="utf-8")
        (source / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(source), "add", "A.lean", ".gitignore"], check=True)
        (source / "ignored.txt").write_text("ignored", encoding="utf-8")
        (source / "untracked.txt").write_text("untracked", encoding="utf-8")
        entries = {e["path"]: e for e in public.inspect_source(source)["files"]}
        self.assertEqual(entries["A.lean"]["git_status"], "tracked")
        self.assertEqual(entries["ignored.txt"]["git_status"], "ignored")
        self.assertEqual(entries["untracked.txt"]["git_status"], "untracked")
        subprocess.run(["git", "-C", str(source), "config", "core.fsmonitor", "touch forbidden"], check=True)
        with self.assertRaises(ValueError): public.inspect_source(source)
        self.assertFalse((source / "forbidden").exists())

    def test_transitive_module_candidates_exclude_unused_broken_module(self):
        (self.source / "Useful.lean").write_text("import Support\nimport Mathlib\ntheorem keep : True := helper\n", encoding="utf-8")
        (self.source / "Support.lean").write_text("import Base\ntheorem helper : True := base\n", encoding="utf-8")
        (self.source / "Base.lean").write_text("theorem base : True := trivial\n", encoding="utf-8")
        result = public.dependency_candidates(self.source, ["Useful"])
        self.assertEqual(result["required_files"], ["Base.lean", "Support.lean", "Useful.lean"])
        self.assertEqual(result["external_imports"], ["Mathlib"])


if __name__ == "__main__": unittest.main()
