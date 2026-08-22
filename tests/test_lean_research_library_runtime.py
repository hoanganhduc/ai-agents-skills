from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "canonical" / "runtime" / "skills" / "lean-research-library" / "lean_research_library.py"
)
spec = importlib.util.spec_from_file_location("lean_research_library", MODULE_PATH)
assert spec is not None and spec.loader is not None
lrl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lrl)


def make_library(root: Path) -> Path:
    """A minimal HoangMathLib-shaped checkout."""
    module = root / "HoangMathLib"
    staging = module / "Mathlib" / "Combinatorics"
    staging.mkdir(parents=True)
    (root / "lakefile.toml").write_text(
        'name = "hoangmathlib"\nrev = "v4.32.2"\n', encoding="utf-8"
    )
    (staging / "Good.lean").write_text(
        "import Mathlib.Combinatorics.SimpleGraph.Clique\n"
        "theorem SimpleGraph.good_lemma : True := trivial\n",
        encoding="utf-8",
    )
    (staging / "Bad.lean").write_text(
        "import HoangMathLib.Reconfig.Basic\n"
        "theorem SimpleGraph.bad_lemma : True := sorry\n",
        encoding="utf-8",
    )
    research = module / "Reconfig"
    research.mkdir(parents=True)
    (research / "Basic.lean").write_text("-- research\n", encoding="utf-8")
    return root


def run_cli(argv: list[str], expect_code: int = 0) -> dict:
    buffer = StringIO()
    with redirect_stdout(buffer):
        code = lrl.main(argv)
    assert code == expect_code, f"exit {code} != {expect_code}"
    return json.loads(buffer.getvalue())


class DoctorTests(unittest.TestCase):
    def test_unconfigured_doctor_is_ok_with_guidance(self):
        with patch.dict("os.environ", {"AAS_LEAN_LIBRARY_ROOT": ""}, clear=False), \
             patch.object(lrl, "config_path", return_value=Path("/nonexistent/config.json")):
            payload = run_cli(["doctor"])
        self.assertEqual(payload["status"], "ok")
        self.assertFalse(payload["library_configured"])
        self.assertTrue(payload["guidance"])
        self.assertTrue(payload["no_auto_install"])
        self.assertFalse(payload["installs_attempted"])
        self.assertFalse(payload["network_required"])
        self.assertIn(payload["tool_status"]["lean"]["status"], {"available", "tool_unavailable"})


class StatusAndSearchTests(unittest.TestCase):
    def test_status_classifies_staging_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_library(Path(tmp))
            with patch.dict("os.environ", {"AAS_LEAN_LIBRARY_ROOT": str(root)}, clear=False):
                payload = run_cli(["status"])
        self.assertEqual(payload["pinned_mathlib"], "v4.32.2")
        by_file = {Path(f["file"]).name: f for f in payload["staged_files"]}
        self.assertTrue(by_file["Good.lean"]["sorry_free"])
        self.assertTrue(by_file["Good.lean"]["import_discipline_ok"])
        self.assertFalse(by_file["Bad.lean"]["sorry_free"])
        self.assertFalse(by_file["Bad.lean"]["import_discipline_ok"])
        ready = [Path(f).name for f in payload["ready_to_upstream"]]
        self.assertEqual(ready, ["Good.lean"])

    def test_offline_search_prefers_library_and_flags_closed_deps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_library(Path(tmp))
            cfg = {"library_root": str(root), "closed_deps": True,
                   "peer_satellites": [str(root)]}
            with patch.object(lrl, "load_config", return_value=cfg):
                payload = run_cli(["search", "--query", "good_lemma", "--offline"])
        self.assertEqual(payload["recommendation"], "use-library")
        self.assertEqual(payload["buckets"]["peer_satellite"], [])
        self.assertTrue(any("closed-deps" in n for n in payload["notes"]))

    def test_phrase_in_module_docstring_finds_the_files_decls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_library(Path(tmp))
            target = Path(tmp) / "HoangMathLib" / "Mathlib" / "Combinatorics" / "Good.lean"
            target.write_text(
                "/-! Lemmas: a subset of an independent set is independent. -/\n"
                + "\n" * 60
                + "import Mathlib.Combinatorics.SimpleGraph.Clique\n"
                "theorem SimpleGraph.far_away_lemma : True := trivial\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"AAS_LEAN_LIBRARY_ROOT": str(root)}, clear=False):
                payload = run_cli(["search", "--query", "subset of an independent set", "--offline"])
        self.assertEqual(payload["recommendation"], "use-library")
        names = [h["name"] for h in payload["buckets"]["library"]]
        self.assertIn("SimpleGraph.far_away_lemma", names)

    def test_mathlib_hit_wins_over_library_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_library(Path(tmp))
            cfg = {"library_root": str(root)}
            fake_loogle = {"hits": [{"name": "Mathlib.hit", "module": "Mathlib.Order.Basic", "type": "T"}]}
            with patch.object(lrl, "load_config", return_value=cfg), \
                 patch.object(lrl, "http_json", return_value=fake_loogle):
                payload = run_cli(["search", "--query", "good_lemma"])
        self.assertEqual(payload["recommendation"], "use-mathlib")

    def test_statement_only_sources_never_reach_mathlib_bucket(self):
        fake = {"hits": [{"name": "conj", "module": "FormalConjectures.Open.X", "type": "T"}]}
        with patch.object(lrl, "load_config", return_value={}), \
             patch.object(lrl, "http_json", return_value=fake):
            payload = run_cli(["search", "--query", "anything"])
        self.assertEqual(payload["buckets"]["mathlib"], [])
        elsewhere = payload["buckets"]["elsewhere"]
        self.assertTrue(elsewhere and elsewhere[0]["statement_only"])
        self.assertEqual(payload["recommendation"], "formalize-new")


class GateTests(unittest.TestCase):
    def test_intake_never_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "New.lean"
            source.write_text("theorem fresh_result : True := trivial\n", encoding="utf-8")
            before = sorted(Path(tmp).rglob("*"))
            payload = run_cli(["intake", "--file", str(source)])
            after = sorted(Path(tmp).rglob("*"))
        self.assertEqual(payload["status"], "proposals-ready")
        self.assertEqual(payload["writes_performed"], [])
        self.assertEqual(before, after)
        self.assertIn("APPROVAL", payload["user_gate"].upper())
        self.assertIn("useful beyond the immediate task", payload["intake_criterion"])

    def test_stage_blocks_discipline_violations_and_default_is_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_library(Path(tmp))
            source = Path(tmp) / "candidate.lean"
            source.write_text(
                "import HoangMathLib.Reconfig.Basic\ntheorem t : True := sorry\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"AAS_LEAN_LIBRARY_ROOT": str(root)}, clear=False):
                blocked = run_cli(["stage", "--file", str(source),
                                   "--target", "Mathlib/Combinatorics/T.lean", "--apply"], expect_code=1)
                clean = Path(tmp) / "clean.lean"
                clean.write_text(
                    "import Mathlib.Combinatorics.SimpleGraph.Clique\n"
                    "theorem t2 : True := trivial\n", encoding="utf-8",
                )
                dry = run_cli(["stage", "--file", str(clean),
                               "--target", "Mathlib/Combinatorics/U.lean"])
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["writes_performed"], [])
        self.assertFalse((Path(root) / "HoangMathLib" / "Mathlib" / "Combinatorics" / "T.lean").exists())
        self.assertEqual(dry["status"], "dry-run")
        self.assertEqual(dry["writes_performed"], [])

    def test_publish_refuses_production_without_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".zenodo.json").write_text(
                json.dumps({"upload_type": "software", "title": "t",
                            "creators": [{"name": "Hoang, Duc A."}]}),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"ZENODO_TOKEN": "fake"}, clear=False):
                payload = run_cli(["artifact", "publish", "--dir", tmp,
                                   "--mode", "api", "--production"], expect_code=1)
        self.assertEqual(payload["status"], "refused")
        self.assertIn("UNDELETABLE", payload["reason"])

    def test_publish_blocks_on_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".zenodo.json").write_text(
                '{"title": "<PAPER-TITLE>"}', encoding="utf-8"
            )
            payload = run_cli(["artifact", "publish", "--dir", tmp, "--mode", "api"], expect_code=1)
        self.assertEqual(payload["status"], "blocked")

    def test_stage_rejects_traversal_and_absolute_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_library(Path(tmp))
            source = Path(tmp) / "ok.lean"
            source.write_text("theorem t : True := trivial\n", encoding="utf-8")
            outside = Path(tmp) / "outside.lean"
            with patch.dict("os.environ", {"AAS_LEAN_LIBRARY_ROOT": str(root)}, clear=False):
                for target in (str(outside), "../../escape.lean",
                               "Mathlib/../../escape2.lean", "Mathlib\\Combinatorics\\Win.lean"):
                    payload = run_cli(["stage", "--file", str(source),
                                       "--target", target, "--apply"],
                                      expect_code=1 if target != "Mathlib\\Combinatorics\\Win.lean" else 0)
                    if target == "Mathlib\\Combinatorics\\Win.lean":
                        # backslash form is normalized INTO the staging tree and validated
                        self.assertEqual(payload["tree"], "staging-mirror")
                    else:
                        self.assertEqual(payload["status"], "error")
            self.assertFalse(outside.exists())
            self.assertFalse((Path(tmp) / "escape.lean").exists())

    def test_stage_refuses_silent_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_library(Path(tmp))
            source = Path(tmp) / "ok.lean"
            source.write_text(
                "import Mathlib.Combinatorics.SimpleGraph.Clique\ntheorem t : True := trivial\n",
                encoding="utf-8")
            with patch.dict("os.environ", {"AAS_LEAN_LIBRARY_ROOT": str(root)}, clear=False):
                payload = run_cli(["stage", "--file", str(source),
                                   "--target", "Mathlib/Combinatorics/Good.lean", "--apply"],
                                  expect_code=1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("overwrite", payload["error"])

    def test_bump_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_library(Path(tmp))
            before = (root / "lakefile.toml").read_text(encoding="utf-8")
            fake_releases = [{"tag_name": "v4.33.0"}, {"tag_name": "v4.33.0-rc1"}, {"tag_name": "v4.32.2"}]
            with patch.dict("os.environ", {"AAS_LEAN_LIBRARY_ROOT": str(root)}, clear=False), \
                 patch.object(lrl, "http_json", return_value=fake_releases):
                payload = run_cli(["bump"])
            after = (root / "lakefile.toml").read_text(encoding="utf-8")
        self.assertEqual(payload["status"], "dry-run")
        self.assertEqual(payload["target"], "v4.33.0")
        self.assertNotIn("v4.33.0-rc1", payload["stable_ladder"])
        self.assertEqual(payload["writes_performed"], [])
        self.assertEqual(before, after)



class ArtifactScaffoldTests(unittest.TestCase):
    """`artifact new` must scaffold, never copy whatever the agent is standing in.

    `template_root` is an optional config key -- `doctor` lists it under "optional
    keys" and a stock host reports `library_configured: false`. It used to be read
    as `Path(str(cfg.get("template_root", "")))`, and `Path("")` is `Path(".")`,
    whose `is_dir()` is always true. So on every default install the copytree
    branch ran against the current working directory: `artifact new --dir D` from
    a paper repo filled D with that repo and still reported `source:
    "template_root"` with an empty note, i.e. claimed the full CI verification
    ladder. Nothing exercised the function, so nothing caught it.
    """

    def _scaffold(self, cfg, *, from_dir, out):
        """Call the payload with `from_dir` as the process's working directory."""

        previous = os.getcwd()
        os.chdir(from_dir)
        try:
            return lrl.artifact_new_payload(
                cfg=cfg, paper="demo", directory=out, library_rev=""
            )
        finally:
            os.chdir(previous)

    def _populated_cwd(self, root: Path) -> Path:
        """A paper repo an agent would plausibly be standing in."""

        project = root / "paper-repo"
        (project / "sections").mkdir(parents=True)
        (project / "main.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
        (project / "sections" / "intro.tex").write_text("intro\n", encoding="utf-8")
        (project / "private-notes.md").write_text("unpublished\n", encoding="utf-8")
        return project

    def test_an_unconfigured_template_uses_the_embedded_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "artifact"
            payload = self._scaffold({}, from_dir=self._populated_cwd(root), out=out)
        self.assertEqual(payload["status"], "scaffolded")
        self.assertEqual(payload["source"], "embedded-minimal")
        self.assertIn("lacks the CI verification ladder", payload["note"])

    def test_an_unconfigured_template_does_not_copy_the_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "artifact"
            self._scaffold({}, from_dir=self._populated_cwd(root), out=out)
            written = {p.relative_to(out).as_posix() for p in out.rglob("*")}
        self.assertEqual(
            written,
            {
                ".gitignore",
                "Artifact",
                "Artifact/Results.lean",
                "Artifact.lean",
                "decls.txt",
                "lakefile.toml",
                "lean-toolchain",
            },
        )
        for leaked in ("main.tex", "private-notes.md", "sections/intro.tex"):
            self.assertNotIn(leaked, written)

    def test_an_empty_template_root_is_treated_as_absent(self) -> None:
        """Explicitly empty and whitespace-only are the same absence as no key."""

        for value in ("", "   ", None):
            with self.subTest(template_root=value):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    out = root / "artifact"
                    payload = self._scaffold(
                        {"template_root": value},
                        from_dir=self._populated_cwd(root),
                        out=out,
                    )
                    self.assertEqual(payload["source"], "embedded-minimal")
                    self.assertTrue((out / "lakefile.toml").is_file())

    def test_a_configured_template_is_still_copied(self) -> None:
        """The control: a real clone must keep working, and report an empty note."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / "lean-paper-artifact-template"
            (template / ".github" / "workflows").mkdir(parents=True)
            (template / "README.md").write_text("# template\n", encoding="utf-8")
            (template / ".github" / "workflows" / "verify.yml").write_text(
                "name: verify\n", encoding="utf-8"
            )
            out = root / "artifact"
            payload = self._scaffold(
                {"template_root": str(template)},
                from_dir=self._populated_cwd(root),
                out=out,
            )
            written = {p.relative_to(out).as_posix() for p in out.rglob("*")}
        self.assertEqual(payload["source"], "template_root")
        self.assertEqual(payload["note"], "")
        self.assertIn("README.md", written)
        self.assertIn(".github/workflows/verify.yml", written)
        self.assertNotIn("main.tex", written)

    def test_a_configured_template_drops_git_and_lake(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / "template"
            (template / ".git").mkdir(parents=True)
            (template / ".lake").mkdir()
            (template / ".git" / "config").write_text("[core]\n", encoding="utf-8")
            (template / ".lake" / "build").write_text("stale\n", encoding="utf-8")
            (template / "README.md").write_text("# t\n", encoding="utf-8")
            out = root / "artifact"
            self._scaffold(
                {"template_root": str(template)},
                from_dir=self._populated_cwd(root),
                out=out,
            )
            written = {p.relative_to(out).as_posix() for p in out.rglob("*")}
        self.assertEqual(written, {"README.md"})

    def test_a_configured_template_that_is_missing_falls_back_with_the_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "artifact"
            payload = self._scaffold(
                {"template_root": str(root / "not-cloned-yet")},
                from_dir=self._populated_cwd(root),
                out=out,
            )
        self.assertEqual(payload["source"], "embedded-minimal")
        self.assertIn("clone", payload["note"])

    def test_a_non_empty_target_is_refused_before_anything_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "artifact"
            out.mkdir()
            (out / "keep.txt").write_text("mine\n", encoding="utf-8")
            payload = self._scaffold({}, from_dir=self._populated_cwd(root), out=out)
            written = {p.relative_to(out).as_posix() for p in out.rglob("*")}
        self.assertEqual(payload["status"], "error")
        self.assertIn("is not empty", payload["error"])
        self.assertEqual(written, {"keep.txt"})

    def test_the_repo_creation_command_is_only_ever_a_proposal(self) -> None:
        """The skill promises propose-only; the payload must not run gh itself."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "artifact"
            payload = self._scaffold({}, from_dir=self._populated_cwd(root), out=out)
        self.assertIn("REQUIRED", payload["user_gate"])
        self.assertTrue(
            any(c.startswith("gh repo create ") for c in payload["proposed_commands"]),
            payload["proposed_commands"],
        )
        self.assertEqual(payload["writes_performed"], [str(out)])


if __name__ == "__main__":
    unittest.main()
