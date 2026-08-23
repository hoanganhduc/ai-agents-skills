"""Regressions for the LaTeX annotator's \\todo labels.

Both defects pinned here live in the two functions that render an issue title
into a `\\todo` box, and both were invisible in a bare checkout: the skill's
``requirements.txt`` declares pylatexenc, but ``latex_annotator`` falls back to
an identity ``utf8tolatex`` when it is absent, and that fallback hides the
escaping defect completely.

* ``build_verifier_addition_todo`` read the issue's title and never used it.
  The other two renderers of the same ``additional_issues`` record print it
  (``pdf_annotator.py`` and ``zotero_note.py``), so the annotated LaTeX PDF --
  the skill's primary output -- was the one artifact of the three that showed a
  verifier-added issue with no title.
* ``build_reviewer_todo`` escaped the title, embedded it in the label, then
  escaped the label again. ``utf8tolatex`` is not idempotent, so a title reading
  "Erdős bound is off by 50%" reached the PDF as literal macro text.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_never_matching_predicates import RUNTIME_SKILLS, load_module


SKILL_DIR = RUNTIME_SKILLS / "annotated-review"


def _load(name: str):
    return load_module(name, SKILL_DIR / f"{name}.py", extra_syspath=SKILL_DIR)


class VerifierAdditionKeepsItsTitleTests(unittest.TestCase):
    """A verifier addition reaches every artifact with its title attached."""

    # ASCII-only, so the assertions hold whether or not pylatexenc is installed.
    TITLE = "Lemma 3.2 assumes a connectivity Theorem 1 never establishes"
    ISSUE = {
        "severity": "major",
        "type": "unsupported",
        "title": TITLE,
        "body": "The induction step needs G-v connected.",
        "quote": "By induction on the number of vertices",
        "line_start": 42,
        "line_end": 44,
    }

    def setUp(self) -> None:
        self.latex = _load("latex_annotator")

    def test_the_todo_box_carries_the_title(self) -> None:
        self.assertIn(self.TITLE, self.latex.build_verifier_addition_todo(self.ISSUE))

    def test_the_marker_and_severity_are_still_there(self) -> None:
        todo = self.latex.build_verifier_addition_todo(self.ISSUE)
        self.assertIn("+ VERIFIER ADDITION", todo)
        self.assertIn("MAJOR", todo)
        self.assertIn(self.ISSUE["body"], todo)

    def test_the_title_survives_into_the_annotated_tex(self) -> None:
        tex = "\n".join([
            r"\documentclass{article}",
            r"\begin{document}",
            r"By induction on the number of vertices, the claim holds.",
            r"\end{document}",
        ])
        out = self.latex.annotate_file(tex, [], {}, {}, [self.ISSUE])
        self.assertIn(self.TITLE, out)

    def test_the_companion_html_renders_the_same_title(self) -> None:
        """The cross-check: two artifacts built from one record must agree."""

        note = _load("zotero_note")
        html = note.build_note_html(
            {
                "meta": {"title": "A paper"},
                "annotations": [],
                "verification": {"results": [], "additional_issues": [self.ISSUE]},
            },
            paper_title="A paper",
        )
        self.assertIn(self.TITLE, html)

    def test_the_pdf_renderer_still_prints_it(self) -> None:
        """pdf_annotator needs PyMuPDF to import, so its contract is read from
        source: it is the third renderer of this record and the reason the LaTeX
        one dropping the title was a disagreement rather than a house style."""

        source = (SKILL_DIR / "pdf_annotator.py").read_text(encoding="utf-8")
        self.assertIn('f"[VERIFIER ADDITION', source)
        self.assertIn('f"{title}\\n\\n{body}"', source)


class ReviewerTodoEscapesExactlyOnceTests(unittest.TestCase):
    """The title is escaped where the label is emitted, and nowhere else."""

    TITLE = "50% of a_b cases"
    ANN = {
        "severity": "major",
        "type": "unsupported",
        "title": TITLE,
        "body": "See section 3.",
        "line_start": 42,
        "line_end": 44,
    }

    def setUp(self) -> None:
        self.latex = _load("latex_annotator")
        # A stand-in with the property that matters: escaping is not idempotent,
        # exactly like pylatexenc's utf8tolatex. Patching it keeps the test
        # honest in a checkout where the declared dependency is not installed,
        # where the module's fallback escaper is the identity and would let a
        # double escape pass unnoticed.
        self.latex.utf8tolatex = self._escape

    @staticmethod
    def _escape(text: str, non_ascii_only: bool = False, **_kwargs) -> str:
        return text.replace("\\", "{\\textbackslash}").replace("%", "{\\%}").replace("_", "{\\_}")

    def test_the_stand_in_is_not_idempotent(self) -> None:
        once = self._escape(self.TITLE)
        self.assertNotEqual(once, self._escape(once))

    def test_the_title_appears_escaped_once(self) -> None:
        todo = self.latex.build_reviewer_todo(self.ANN)
        self.assertIn(self._escape(self.TITLE), todo)

    def test_no_escape_of_an_escape_reaches_the_output(self) -> None:
        todo = self.latex.build_reviewer_todo(self.ANN)
        self.assertNotIn("{\\{\\textbackslash}", todo)
        self.assertNotIn(self._escape(self._escape(self.TITLE)), todo)

    def test_the_verifier_addition_label_is_escaped_once_too(self) -> None:
        todo = self.latex.build_verifier_addition_todo(self.ANN)
        self.assertIn(self._escape(self.TITLE), todo)
        self.assertNotIn(self._escape(self._escape(self.TITLE)), todo)

    def test_the_real_escaper_is_the_one_being_modelled(self) -> None:
        """When the declared dependency is present, pin the property on it."""

        try:
            from pylatexenc.latexencode import utf8tolatex
        except ImportError:  # pragma: no cover - dependency is optional here
            self.skipTest("pylatexenc not installed")
        once = utf8tolatex(self.TITLE, non_ascii_only=False)
        self.assertNotEqual(once, utf8tolatex(once, non_ascii_only=False))


if __name__ == "__main__":
    unittest.main()


class AnnotationsOnIncludedFilesReachThePdfTests(unittest.TestCase):
    r"""A comment the annotator could not write must not pass as a done review.

    The root .tex write is unguarded, so it stops the run. Every other file --
    the \input sections a real manuscript is made of -- went through a write
    wrapped in ``except OSError: pass``. The annotated tree still carried the
    preamble and the metadata box, so the PDF compiled and read as a complete
    review with a reviewer's comment missing from it.
    """

    REVIEW = {
        "meta": {"title": "A Note on Tight Bounds", "reviewer": "R1"},
        "annotations": [{
            "file": "sec1.tex", "quote": "The bound is tight for all",
            "severity": "major", "type": "correctness",
            "title": "Counterexample at n=4",
            "body": "The bound fails for n=4; see the construction below.",
            "line_start": 3, "line_end": 3,
        }],
    }

    def _paper(self, *, read_only_section, stray=False):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base, True)
        src = base / "paper"
        src.mkdir()
        (src / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\n"
            "\\input{sec1}\n\\end{document}\n", encoding="utf-8")
        (src / "sec1.tex").write_text(
            "\\section{Bounds}\n\nThe bound is tight for all $n \\ge 3$.\n\nMore.\n",
            encoding="utf-8")
        if stray:
            (src / "notes.tex").write_text(
                "\\section{Notes}\n\nPlain text only.\n", encoding="utf-8")
            os.chmod(src / "notes.tex", stat.S_IRUSR | stat.S_IRGRP)
        if read_only_section:
            os.chmod(src / "sec1.tex", stat.S_IRUSR | stat.S_IRGRP)
        return str(src)

    def test_a_writable_section_gets_its_annotation(self):
        out = _load("latex_annotator").annotate_tree(
            self._paper(read_only_section=False), self.REVIEW)
        self.assertIn("Counterexample at n=4",
                      (Path(out) / "sec1.tex").read_text(encoding="utf-8"))

    def test_a_section_that_cannot_be_written_stops_the_run(self):
        with self.assertRaises(OSError):
            _load("latex_annotator").annotate_tree(
                self._paper(read_only_section=True), self.REVIEW)

    def test_an_unwritable_file_with_nothing_to_inject_is_left_alone(self):
        """The fix must not fail a review over a stray read-only .tex."""
        out = _load("latex_annotator").annotate_tree(
            self._paper(read_only_section=False, stray=True), self.REVIEW)
        self.assertIn("Counterexample at n=4",
                      (Path(out) / "sec1.tex").read_text(encoding="utf-8"))


class PrecompileStdoutStaysParseableTests(unittest.TestCase):
    """Success was the one precompile outcome a caller could not parse.

    The error branch prints the envelope alone, so json.loads() works on it.
    The success branch printed the envelope and then the bare path, so
    json.loads() raised "Extra data: line 2" on exactly the runs that worked.
    A stubbed toolchain keeps this end-to-end without a LaTeX install.
    """

    def _toolchain(self, base, *, succeed):
        bin_dir = base / "bin"
        bin_dir.mkdir()
        if os.name == "nt":
            for name in ("pdflatex", "lualatex", "xelatex"):
                (bin_dir / f"{name}.cmd").write_text(
                    "@echo off\r\nexit /b 0\r\n", encoding="utf-8"
                )
            implementation = base / "latexmk_stub.py"
            implementation.write_text(
                (
                    "from pathlib import Path\n"
                    "import sys\n"
                    "Path(sys.argv[-1]).with_suffix('.pdf').write_bytes(b'%PDF-1.4')\n"
                )
                if succeed
                else "import sys\nprint('! LaTeX Error: stub failure')\nsys.exit(1)\n",
                encoding="utf-8",
            )
            (bin_dir / "latexmk.cmd").write_text(
                "@echo off\r\nchcp 65001 >nul\r\n"
                f'"{sys.executable}" "{implementation}" %*\r\n'
                "exit /b %ERRORLEVEL%\r\n",
                encoding="utf-8",
            )
            return bin_dir
        for name in ("pdflatex", "lualatex", "xelatex"):
            (bin_dir / name).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        body = ('#!/bin/sh\nfor a in "$@"; do :; done\n'
                'printf %%PDF-1.4 > "${a%.tex}.pdf"\nexit 0\n' if succeed
                else '#!/bin/sh\necho "! LaTeX Error: stub failure" \nexit 1\n')
        (bin_dir / "latexmk").write_text(body, encoding="utf-8")
        for f in bin_dir.iterdir():
            os.chmod(f, 0o755)
        return bin_dir

    def _run(self, succeed):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base, True)
        src = base / "paper"
        src.mkdir()
        (src / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nHi.\n\\end{document}\n",
            encoding="utf-8")
        bin_dir = self._toolchain(base, succeed=succeed)
        env = {**os.environ,
               "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
               "PYTHONDONTWRITEBYTECODE": "1"}
        return subprocess.run(
            [sys.executable, str(SKILL_DIR / "review.py"),
             "--precompile-only", "--source", str(src)],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=300)

    def test_a_successful_precompile_prints_one_json_object(self):
        proc = self._run(succeed=True)
        payload = json.loads(proc.stdout)      # raised "Extra data: line 2"
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["pdf"].endswith(".pdf"), payload)

    def test_a_failed_precompile_still_prints_one_json_object(self):
        proc = self._run(succeed=False)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "error")

class AutoFixRetryPdfSurvivesALogWriteFailureTests(unittest.TestCase):
    """A pdf the auto-fix retry produced is returned even if the log append fails.

    ``compile_latex`` appended the retry transcript to ``compile.log`` and then
    checked for the pdf, both inside one ``try``. An append that raises --
    ENOSPC, a read-only mount, a quota -- therefore jumped straight over the
    existence check to ``except Exception: pass``, and the function reported the
    original LaTeX error while the repaired pdf sat next to the .tex file. The
    log records the retry; it does not decide it.

    latexmk is absent on ordinary CI images, so ``shutil.which`` and
    ``subprocess.run`` are stubbed here; everything else is the real
    ``compile_latex``, and the stub only makes the retry succeed, which is the
    case the defect threw away.
    """

    ENOSPC = 28

    def _fixture(self, tmp: Path) -> None:
        (tmp / "paper.tex").write_text(
            "\\documentclass{article}\\begin{document}x\\end{document}\n",
            encoding="utf-8",
        )

    def _compile(self, tmp: Path, *, retry_emits_pdf: bool, opener):
        review = _load("review")
        calls = {"n": 0}

        def fake_run(cmd, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2 and retry_emits_pdf:
                (tmp / "paper.pdf").write_bytes(b"%PDF-1.5 fixture\n")
            return subprocess.CompletedProcess(cmd, 0, "log text\n", "")

        with mock.patch("subprocess.run", fake_run), mock.patch.object(
            review.shutil, "which", lambda name: f"/usr/bin/{name}"
        ), mock.patch.object(
            review, "extract_compile_error", lambda path: "! Undefined control sequence."
        ), mock.patch.object(
            review, "attempt_autofix", lambda *args: "removed a bad macro"
        ), mock.patch.object(
            review, "extract_latex_warnings", lambda text: []
        ), mock.patch.object(review, "open", opener, create=True):
            return review.compile_latex(str(tmp), "paper.tex"), calls["n"]

    def _failing_append(self, tmp: Path):
        real_open = open

        def opener(path, mode="r", *args, **kwargs):
            if "a" in mode and str(path) == str(tmp / "compile.log"):
                raise OSError(self.ENOSPC, "No space left on device")
            return real_open(path, mode, *args, **kwargs)

        return opener

    def test_a_full_disk_on_the_log_does_not_discard_the_retry_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            self._fixture(tmp)

            (pdf, error, warnings), runs = self._compile(
                tmp, retry_emits_pdf=True, opener=self._failing_append(tmp)
            )

            self.assertEqual(runs, 2)
            self.assertTrue((tmp / "paper.pdf").is_file())
            self.assertEqual(pdf, os.path.join(str(tmp), "paper.pdf"))
            self.assertIsNone(error)
            self.assertEqual(warnings, ["Auto-fix applied: removed a bad macro"])

    def test_the_retry_pdf_is_returned_when_the_log_append_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            self._fixture(tmp)

            (pdf, error, _warnings), runs = self._compile(
                tmp, retry_emits_pdf=True, opener=open
            )

            self.assertEqual(runs, 2)
            self.assertEqual(pdf, os.path.join(str(tmp), "paper.pdf"))
            self.assertIsNone(error)
            self.assertIn(
                "AUTO-FIX RETRY",
                (tmp / "compile.log").read_text(encoding="utf-8"),
            )

    def test_a_retry_that_produces_no_pdf_still_reports_the_error(self) -> None:
        # Anchors the two above: the fixture reports a pdf because the retry
        # made one, not because the check was loosened.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            self._fixture(tmp)

            (pdf, error, warnings), runs = self._compile(
                tmp, retry_emits_pdf=False, opener=self._failing_append(tmp)
            )

            self.assertEqual(runs, 2)
            self.assertIsNone(pdf)
            self.assertEqual(error, "! Undefined control sequence.")
            self.assertEqual(warnings, [])
