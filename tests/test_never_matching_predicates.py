"""Regressions for predicates that could never match.

Every test here pins a defect of one shape: a filter, guard, or safety rule
written so that no input could ever satisfy it. Such a predicate does not fail
loudly — it reports success on everything, so the subsystem looks healthy while
doing nothing. The seven fixes span five subsystems, and they are collected in
one file so the shape stays visible rather than dissolving into per-skill suites.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from tests.test_zotero_webdav_metadata import load_zot_module


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SKILLS = ROOT / "canonical" / "runtime" / "skills"
SAFETY_SCRIPT = ROOT / "canonical" / "skills" / "self-improving-agent" / "scripts" / "check_command_safety.sh"


def load_module(name: str, path: Path, extra_syspath: Path | None = None):
    """Import a skill module by path without leaving a __pycache__ behind.

    The runtime inventory test rejects stray .pyc files under canonical/, so
    bytecode writing stays off for the duration of the import. extra_syspath
    covers skills whose modules import siblings by bare name.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    if extra_syspath is not None:
        sys.path.insert(0, str(extra_syspath))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
        if extra_syspath is not None:
            sys.path.remove(str(extra_syspath))
    return module


class DoclingWordFloorTest(unittest.TestCase):
    """The words-per-page floor counted only ASCII-shaped words.

    Kana, Han, Hangul, Cyrillic, Greek, Arabic, and Thai text scored zero words,
    so the floor was unsatisfiable and clean extractions of those documents were
    reported as degraded no matter how good the OCR was.
    """

    def setUp(self) -> None:
        self.module = load_module("docling_runtime_predicates", RUNTIME_SKILLS / "docling" / "docling_runtime.py")

    def test_non_latin_pages_can_satisfy_the_word_floor(self) -> None:
        samples = {
            "chinese": "这是一段中文文本用来测试字数统计功能是否正确工作" * 20,
            "japanese": "これは日本語のテキストです文字数の計算を確認します" * 20,
            "korean": "이것은 한국어 텍스트입니다 단어 수를 계산합니다" * 20,
            "russian": "Это русский текст для проверки подсчёта слов" * 20,
            "greek": "Αυτό είναι ελληνικό κείμενο για τον έλεγχο των λέξεων" * 20,
            "arabic": "هذا نص عربي لاختبار عدد الكلمات في الصفحة" * 20,
            "thai": "นี่คือข้อความภาษาไทยสำหรับทดสอบการนับคำ" * 20,
        }
        for language, text in samples.items():
            with self.subTest(language=language):
                report = self.module.evaluate_ocr_quality(text, pages=1)
                self.assertTrue(report["passes"], f"{language} extraction wrongly flagged as degraded")

    def test_ascii_counting_is_unchanged(self) -> None:
        text = "The quick brown fox jumps over the lazy dog. " * 30
        self.assertEqual(self.module.count_words(text), 270)

    def test_degraded_extractions_are_still_rejected(self) -> None:
        for label, text in (("empty", "   "), ("replacement", "\ufffd" * 400), ("control", "\x00\x01\x02" * 200)):
            with self.subTest(case=label):
                self.assertFalse(self.module.evaluate_ocr_quality(text, pages=4)["passes"])

    def test_zero_floor_disables_the_word_check(self) -> None:
        report = self.module.evaluate_ocr_quality("word " * 400, pages=1, min_words_per_page=0)
        self.assertNotIn("words per page", " ".join(report["reasons"]))


@contextmanager
def stub_docling():
    """Install a minimal docling stand-in so converter wiring is testable offline.

    docling is a heavyweight optional dependency and is absent from the test
    interpreter, so a skipUnless guard here would itself never fire. Every
    docling import in build_docling_converter is function-local, which makes
    sys.modules injection enough to exercise the real branching.
    """

    class InputFormat:
        PDF = "input-format-pdf"
        IMAGE = "input-format-image"

    class Recorder:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    names = {
        "docling.datamodel.base_models": {"InputFormat": InputFormat},
        "docling.datamodel.pipeline_options": {"PdfPipelineOptions": Recorder, "VlmPipelineOptions": Recorder},
        "docling.document_converter": {"DocumentConverter": Recorder, "PdfFormatOption": Recorder},
        "docling.pipeline.vlm_pipeline": {"VlmPipeline": Recorder},
        "docling.backend.image_backend": {"ImageDocumentBackend": Recorder},
    }
    packages = ["docling", "docling.datamodel", "docling.pipeline", "docling.backend"]

    saved = {key: sys.modules.get(key) for key in list(names) + packages}
    try:
        for package in packages:
            sys.modules[package] = types.ModuleType(package)
        for path, members in names.items():
            module = types.ModuleType(path)
            for key, value in members.items():
                setattr(module, key, value)
            sys.modules[path] = module
        yield InputFormat
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


class DoclingImageFormatTest(unittest.TestCase):
    """format_options was keyed on PDF alone.

    Docling looks the options up by input format, so for the jpg/png/tiff inputs
    this skill advertises every configured option — OCR mode, engine, languages,
    tables, threads, device, timeout — was silently discarded.
    """

    BASE_OPTIONS = {
        "ocr_mode": "auto",
        "ocr_engine": "auto",
        "ocr_lang": None,
        "force_full_page_ocr": False,
        "tables": True,
        "table_mode": "fast",
        "cell_matching": False,
        "num_threads": 2,
        "device": "cpu",
        "artifacts_path": None,
        "document_timeout": 30.0,
    }

    def format_options_for(self, pipeline: str) -> tuple:
        module = load_module("docling_runtime_converter", RUNTIME_SKILLS / "docling" / "docling_runtime.py")
        with stub_docling() as input_format:
            converter = module.build_docling_converter({**self.BASE_OPTIONS, "pipeline": pipeline})
        return converter.kwargs["format_options"], input_format

    def test_both_pipelines_configure_image_inputs(self) -> None:
        for pipeline in ("standard", "vlm"):
            with self.subTest(pipeline=pipeline):
                format_options, input_format = self.format_options_for(pipeline)
                self.assertIn(input_format.PDF, format_options)
                self.assertIn(input_format.IMAGE, format_options, "image inputs would ignore every option")

    def test_image_inputs_get_an_image_native_backend(self) -> None:
        format_options, input_format = self.format_options_for("standard")
        self.assertIn("backend", format_options[input_format.IMAGE].kwargs)


class ZoteroDjvuTest(unittest.TestCase):
    """DjVu scans were filed as artwork.

    DjVu resolves to image/vnd.djvu, so the broad "image/" prefix claimed it
    first and no scanned book could ever reach the document branch. The fix
    depends on dict ordering, which this test pins.
    """

    def setUp(self) -> None:
        self.module = load_zot_module()

    def test_djvu_content_types_map_to_document(self) -> None:
        for content_type in ("image/vnd.djvu", "image/x-djvu", "application/djvu"):
            with self.subTest(content_type=content_type):
                metadata = self.module._extract_file_metadata("scan.djvu", content_type)
                self.assertEqual(metadata["itemType"], "document")

    def test_real_images_are_still_artwork(self) -> None:
        self.assertEqual(self.module._extract_file_metadata("cover.png", "image/png")["itemType"], "artwork")
        self.assertEqual(self.module._extract_file_metadata("book.epub", "application/epub+zip")["itemType"], "book")


class LeanForeignPatternTest(unittest.TestCase):
    """A shared leading \\b made the @extern half of the pattern unreachable.

    \\b before a group demands a word character adjacent to the match start, and
    "@" is not one, so "@extern" was never flagged regardless of context.
    """

    def setUp(self) -> None:
        module = load_module(
            "lean_gate_predicates",
            RUNTIME_SKILLS / "lean-strict-verification-gate" / "lean_strict_verification_gate.py",
        )
        self.pattern = module.SAFETY_PATTERNS["foreign"]

    def test_extern_declarations_are_flagged(self) -> None:
        for source in ('@extern "c" def f', "  @extern", "@my_extern", "foreign import f"):
            with self.subTest(source=source):
                self.assertIsNotNone(self.pattern.search(source))

    def test_unrelated_text_is_not_flagged(self) -> None:
        for source in ("no match here", "external tool", "-- discuss foreign policy"):
            with self.subTest(source=source):
                self.assertIsNone(self.pattern.search(source))


class LatexInjectionTest(unittest.TestCase):
    r"""The injection required a newline after \begin{document}.

    \begin{document}\maketitle and \begin{document}% are common, and for those
    the entire review header — line numbers, metadata box, todo list — was
    skipped without a word of warning.
    """

    def setUp(self) -> None:
        self.module = load_module(
            "latex_annotator_predicates",
            RUNTIME_SKILLS / "annotated-review" / "latex_annotator.py",
            extra_syspath=RUNTIME_SKILLS / "annotated-review",
        )
        source = (RUNTIME_SKILLS / "annotated-review" / "latex_annotator.py").read_text(encoding="utf-8")
        self.counts = {key: 0 for key in re.findall(r"[avt]c\['(\w+)'\]", source)}

    def inject(self, tex: str) -> str:
        return self.module.inject_document_start(
            tex, {"title": "Paper"}, None, None, self.counts, self.counts, self.counts
        )

    def test_every_preamble_idiom_is_annotated(self) -> None:
        idioms = (
            "\\begin{document}\n\\maketitle\n",
            "\\begin{document}\\maketitle\n",
            "\\begin{document}% comment\n",
            "\\begin{document}",
        )
        for tex in idioms:
            with self.subTest(idiom=tex):
                self.assertIn("\\linenumbers", self.inject(tex))

    def test_following_content_is_preserved(self) -> None:
        result = self.inject("\\begin{document}\\maketitle\n")
        self.assertIn("\\maketitle", result)

    def test_fragments_without_a_document_are_untouched(self) -> None:
        fragment = "\\section{Intro}\nText.\n"
        self.assertEqual(self.inject(fragment), fragment)


class SafetyScriptPortabilityTest(unittest.TestCase):
    """The safety rules used GNU-only regex escapes.

    \\s and \\b are GNU extensions. Under the POSIX ERE that BSD and macOS grep
    provide they degrade — \\s to a literal "s", \\b to a backspace byte — so
    every rule in this script matched nothing and "rm -rf /" was waved through.
    """

    def setUp(self) -> None:
        self.rules = re.findall(r"grep -q(?:i?)E '(.*)'", SAFETY_SCRIPT.read_text(encoding="utf-8"))

    def test_the_rules_were_found(self) -> None:
        self.assertGreaterEqual(len(self.rules), 7)

    def test_no_rule_uses_a_gnu_only_escape(self) -> None:
        for rule in self.rules:
            with self.subTest(rule=rule):
                self.assertNotIn(r"\s", rule, "use [[:space:]] so the rule survives POSIX grep")
                self.assertNotIn(r"\b", rule, "use an explicit non-word class so the rule survives POSIX grep")


class DeepResearchFallbackTest(unittest.TestCase):
    """The legacy-runtime fallback was gated on a string argparse never emits.

    An installed runtime predating the selftest subcommand fails with
    "invalid choice: 'selftest'", not "invalid command: selftest", so the guard
    returned False on the one input it existed to recognise and the whole
    fallback path was unreachable.
    """

    ARGPARSE_STDERR = (
        "usage: run_deep_research_workflow [-h] {doctor,init,validate} ...\n"
        "run_deep_research_workflow: error: argument command: invalid choice: "
        "'selftest' (choose from 'doctor', 'init', 'validate')\n"
    )

    def setUp(self) -> None:
        from installer.ai_agents_skills.runtime_smoke import is_deep_research_selftest_unsupported

        self.unsupported = is_deep_research_selftest_unsupported

    def result(self, returncode: int, stderr: str = "", stdout: str = ""):
        import subprocess

        return subprocess.CompletedProcess(args=["selftest"], returncode=returncode, stdout=stdout, stderr=stderr)

    def test_argparse_rejection_is_recognised(self) -> None:
        self.assertTrue(self.unsupported(self.result(2, stderr=self.ARGPARSE_STDERR)))

    def test_older_wording_is_still_recognised(self) -> None:
        self.assertTrue(self.unsupported(self.result(2, stderr="error: invalid command: selftest")))

    def test_success_and_unrelated_failures_are_not_mistaken_for_it(self) -> None:
        self.assertFalse(self.unsupported(self.result(0, stdout="{}")))
        self.assertFalse(self.unsupported(self.result(1, stderr="Traceback: PermissionError")))
        self.assertFalse(self.unsupported(self.result(2, stderr="invalid choice: 'doctor'")))


class SmokeCanaryTest(unittest.TestCase):
    """A canary proves nothing unless it is injected, scanned, and unskippable.

    The scan used to sit inside validate_smoke_output, below an early return taken
    on any non-zero exit, and it read only the parsed JSON payload. So it was
    absent from every failure path and blind to stderr — the two places an
    environment value actually escapes. Deriving the checks from the manifest and
    appending them outside the validator makes declaring a canary the only step
    needed to wire one up, and makes skipping one impossible.
    """

    def setUp(self) -> None:
        from installer.ai_agents_skills import runtime_smoke

        self.smoke = runtime_smoke
        self.manifest = json.loads((ROOT / "manifest" / "runtime.yaml").read_text(encoding="utf-8"))
        self.manifests = {"runtime": self.manifest}

    def declared(self, skill: str) -> dict:
        return (self.manifest["skills"][skill].get("smoke") or {}).get("env_canaries") or {}

    def with_canaries(self):
        return [name for name in self.manifest["skills"] if self.declared(name)]

    def run_case(self, skill: str, returncode: int, stdout: str, stderr: str) -> dict:
        """Drive run_smoke_case over a fabricated child result.

        The point is the wiring, not the skill: patching the subprocess call lets
        a crash, a garbled payload, and a timeout each be replayed exactly.
        """
        completed = subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)
        with mock.patch.object(self.smoke, "run_smoke_process", return_value=completed):
            return self.smoke.run_smoke_case(
                self.manifests,
                skill=skill,
                runner={"name": "run_skill.sh", "argv": ["/nonexistent/run_skill.sh"]},
                workspace=Path(tempfile.gettempdir()),
                platform="linux",
                timeout=5,
            )

    def test_every_secret_forbidding_skill_declares_a_canary(self) -> None:
        for name, spec in self.manifest["skills"].items():
            smoke = spec.get("smoke") or {}
            if (smoke.get("safety") or {}).get("real_secrets") != "forbidden":
                continue
            with self.subTest(skill=name):
                self.assertTrue(
                    smoke.get("env_canaries"),
                    f"{name} forbids real secrets but injects nothing that would prove it",
                )

    def test_the_canaries_were_found(self) -> None:
        self.assertGreaterEqual(len(self.with_canaries()), 18)

    def test_a_declared_canary_produces_a_check(self) -> None:
        for name in self.with_canaries():
            with self.subTest(skill=name):
                checks = self.smoke.canary_checks(self.manifests, name, "", "")
                self.assertEqual(len(checks), len(self.declared(name)))
                self.assertTrue(all(check["ok"] for check in checks))

    def test_a_leak_on_either_stream_is_caught(self) -> None:
        for name in self.with_canaries():
            leaked = sorted(self.declared(name).values())[0]
            with self.subTest(skill=name):
                on_stdout = self.smoke.canary_checks(self.manifests, name, f'{{"v": "{leaked}"}}', "")
                on_stderr = self.smoke.canary_checks(self.manifests, name, "", f"Traceback: env={leaked}")
                self.assertFalse(all(c["ok"] for c in on_stdout), "a canary on stdout must fail the smoke")
                self.assertFalse(all(c["ok"] for c in on_stderr), "a canary on stderr must fail the smoke")

    def test_the_scan_survives_the_paths_that_used_to_skip_it(self) -> None:
        skill = "graph-verifier"
        leaked = sorted(self.declared(skill).values())[0]
        paths = {
            "non-zero exit": self.run_case(skill, 1, "", f"Traceback (most recent call last): KEY={leaked}"),
            "unparseable stdout": self.run_case(skill, 0, f"not json at all {leaked}", ""),
        }
        for label, result in paths.items():
            with self.subTest(path=label):
                names = [check["name"] for check in result["checks"]]
                self.assertTrue(
                    any(name.startswith("canary-not-leaked") for name in names),
                    f"the canary scan disappeared on the {label} path",
                )
                self.assertFalse(all(check["ok"] for check in result["checks"]))
                self.assertEqual(result["status"], "failed")

    def test_the_scan_survives_a_timeout(self) -> None:
        skill = "graph-verifier"
        leaked = sorted(self.declared(skill).values())[0]
        expired = subprocess.TimeoutExpired(cmd=["run_skill.sh"], timeout=1, output=f"partial {leaked}", stderr="")
        with mock.patch.object(self.smoke, "run_smoke_process", side_effect=expired):
            result = self.smoke.run_smoke_case(
                self.manifests,
                skill=skill,
                runner={"name": "run_skill.sh", "argv": ["/nonexistent/run_skill.sh"]},
                workspace=Path(tempfile.gettempdir()),
                platform="linux",
                timeout=1,
            )
        leaked_checks = [c for c in result["checks"] if c["name"].startswith("canary-not-leaked")]
        self.assertTrue(leaked_checks, "a hung skill that dumped a canary must still be caught")
        self.assertFalse(all(check["ok"] for check in leaked_checks))


class SmokeSelftestBranchTest(unittest.TestCase):
    """Three skills declared full safety contracts and were checked only for exit 0.

    Each emits a structured selftest report the harness never read, so a run whose
    internal checks had all failed still passed the smoke as long as the process
    exited cleanly. An empty report has to count as a failure for the same reason:
    a selftest that asserts nothing likewise reports nothing failing.
    """

    GOOD = {
        "manim-math-animation": {"ok": True, "passed": 7, "total": 7, "failures": []},
        "slides-to-video": {"ok": True, "passed": 9, "total": 9, "failures": []},
        "send-email": {"ok": True, "command": "selftest", "passed": 15, "failed": 0},
    }
    BAD = {
        "manim-math-animation": [
            {"ok": False, "passed": 6, "total": 7, "failures": [{"check": "scenegen"}]},
            {"ok": True, "passed": 0, "total": 0, "failures": []},
        ],
        "slides-to-video": [
            {"ok": False, "passed": 8, "total": 9, "failures": [{"check": "ffmpeg-argv"}]},
            {"ok": True, "passed": 0, "total": 0, "failures": []},
        ],
        "send-email": [
            {"ok": False, "command": "selftest", "passed": 14, "failed": 1},
            {"ok": True, "command": "selftest", "passed": 0, "failed": 0},
        ],
    }

    def setUp(self) -> None:
        from installer.ai_agents_skills import runtime_smoke

        self.smoke = runtime_smoke

    def validate(self, skill: str, payload: dict) -> list[dict]:
        completed = subprocess.CompletedProcess(
            args=["selftest"], returncode=0, stdout=json.dumps(payload), stderr=""
        )
        return self.smoke.validate_smoke_output({}, skill, completed, ["selftest"])

    def test_a_healthy_report_passes(self) -> None:
        for skill, payload in self.GOOD.items():
            with self.subTest(skill=skill):
                checks = self.validate(skill, payload)
                self.assertGreater(len(checks), 1, f"{skill} is still checked for nothing but exit 0")
                self.assertTrue(all(check["ok"] for check in checks))

    def test_a_failing_or_empty_report_is_rejected(self) -> None:
        for skill, payloads in self.BAD.items():
            for index, payload in enumerate(payloads):
                with self.subTest(skill=skill, payload=index):
                    checks = self.validate(skill, payload)
                    self.assertFalse(
                        all(check["ok"] for check in checks),
                        f"{skill} accepted a selftest report it should have rejected",
                    )


if __name__ == "__main__":
    unittest.main()
