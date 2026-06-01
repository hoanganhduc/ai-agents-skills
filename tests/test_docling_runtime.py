from __future__ import annotations

import builtins
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
DOCLING_DIR = ROOT / "canonical" / "runtime" / "skills" / "docling"


def load_docling_runtime():
    spec = importlib.util.spec_from_file_location(
        "docling_runtime_under_test",
        DOCLING_DIR / "docling_runtime.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def no_bytecode_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for key in (
        "AAS_DOCLING_CONFIG",
        "AAS_DOCLING_PRESET",
        "AAS_RUNTIME_WORKSPACE",
        "DOCLING_ARTIFACTS_PATH",
        "DOCLING_CONFIG",
        "DOCLING_DEVICE",
        "DOCLING_NUM_THREADS",
        "DOCLING_PRESET",
        "OCRSPACE_API_KEY",
        "OCR_SPACE_API_KEY",
        "OCRSPACE_KEY",
        "OCR_SPACE_KEY",
        "OPENCLAW_WORKSPACE",
    ):
        env.pop(key, None)
    return env


class DoclingRuntimeTests(unittest.TestCase):
    def test_runtime_helper_imports_without_docling_import(self) -> None:
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "docling" or name.startswith("docling."):
                raise AssertionError(f"unexpected docling import: {name}")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", guarded_import):
            module = load_docling_runtime()

        self.assertIn("auto", module.LOCAL_OCR_ENGINES)

    def test_convert_help_does_not_require_docling_import(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(DOCLING_DIR / "docling_convert.py"), "--help"],
            check=False,
            text=True,
            capture_output=True,
            timeout=30,
            env=no_bytecode_env(),
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--ocr-mode", completed.stdout)
        self.assertIn("--config", completed.stdout)
        self.assertIn("--ocr-fallback", completed.stdout)

    def test_quality_help_does_not_require_docling_import(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(DOCLING_DIR / "docling_quality.py"), "--help"],
            check=False,
            text=True,
            capture_output=True,
            timeout=30,
            env=no_bytecode_env(),
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--ocr-quality-threshold", completed.stdout)

    def test_ocrspace_smoke_help_does_not_require_docling_import(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(DOCLING_DIR / "docling_ocrspace_smoke.py"), "--help"],
            check=False,
            text=True,
            capture_output=True,
            timeout=30,
            env=no_bytecode_env(),
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--allow-remote-ocr", completed.stdout)

    def test_remote_source_rejected_before_conversion(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(DOCLING_DIR / "docling_convert.py"),
                "--source",
                "https://example.com/paper.pdf",
            ],
            check=False,
            text=True,
            capture_output=True,
            timeout=30,
            env=no_bytecode_env(),
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("remote source is not allowed", completed.stderr)
        self.assertIn("https://<remote-host>/paper.pdf", completed.stderr)
        self.assertNotIn("example.com", completed.stderr)

    def test_ocrspace_config_is_rejected_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "docling.toml"
            config.write_text(
                """
schema_version = 1
[defaults]
ocrspace = "secret-api-value"
OCREngine = 3
""".strip()
                + "\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(DOCLING_DIR / "docling_convert.py"),
                    "--source",
                    str(root / "paper.pdf"),
                    "--config",
                    str(config),
                ],
                check=False,
                text=True,
                capture_output=True,
                timeout=30,
                env=no_bytecode_env(),
            )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("OCR.space configuration is not supported in Docling config", completed.stderr)
        self.assertIn("--ocr-fallback ocrspace --allow-remote-ocr", completed.stderr)
        self.assertNotIn("secret-api-value", completed.stderr)

    def test_ocrspace_config_rejection_works_without_tomli(self) -> None:
        module = load_docling_runtime()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "docling.toml"
            config.write_text(
                """
schema_version = 1
[defaults]
ocrspace = "secret-api-value"
""".strip()
                + "\n",
                encoding="utf-8",
            )
            args = SimpleNamespace(config=str(config), allow_openclaw_config=False)
            with patch.dict(sys.modules, {"tomllib": None, "tomli": None}):
                with self.assertRaises(module.DoclingRuntimeError) as caught:
                    module._load_config(args)

        message = str(caught.exception)
        self.assertIn("OCR.space configuration is not supported in Docling config", message)
        self.assertIn("--ocr-fallback ocrspace --allow-remote-ocr", message)
        self.assertNotIn("secret-api-value", message)

    def test_ocrspace_fallback_requires_explicit_remote_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "paper.pdf"
            source.write_bytes(b"%PDF-1.4\n")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(DOCLING_DIR / "docling_convert.py"),
                    "--source",
                    str(source),
                    "--ocr-fallback",
                    "ocrspace",
                ],
                check=False,
                text=True,
                capture_output=True,
                timeout=30,
                env=no_bytecode_env(),
            )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("requires --allow-remote-ocr", completed.stderr)

    def test_ocrspace_fallback_requires_key_env_without_printing_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "paper.pdf"
            source.write_bytes(b"%PDF-1.4\n")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(DOCLING_DIR / "docling_convert.py"),
                    "--source",
                    str(source),
                    "--ocr-fallback",
                    "ocrspace",
                    "--allow-remote-ocr",
                ],
                check=False,
                text=True,
                capture_output=True,
                timeout=30,
                env=no_bytecode_env(),
            )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("OCR.space fallback requires", completed.stderr)
        self.assertNotIn("secret-api-value", completed.stderr)

    def test_ocrspace_smoke_requires_explicit_remote_opt_in(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(DOCLING_DIR / "docling_ocrspace_smoke.py"),
            ],
            check=False,
            text=True,
            capture_output=True,
            timeout=30,
            env=no_bytecode_env(),
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("requires --allow-remote-ocr", completed.stderr)

    def test_convert_rejects_existing_output_before_docling_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "paper.pdf"
            output = root / "out.md"
            source.write_bytes(b"%PDF-1.4\n")
            output.write_text("existing", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(DOCLING_DIR / "docling_convert.py"),
                    "--source",
                    str(source),
                    "--output",
                    str(output),
                ],
                check=False,
                text=True,
                capture_output=True,
                timeout=30,
                env=no_bytecode_env(),
            )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("--overwrite", completed.stderr)

    def test_ocr_quality_evaluator_flags_low_text(self) -> None:
        module = load_docling_runtime()
        report = module.evaluate_ocr_quality("???", pages=2)
        self.assertEqual(report["status"], "degraded")
        self.assertFalse(report["passes"])
        self.assertIn("low characters per page", report["reasons"])

    def test_ocr_quality_evaluator_accepts_reasonable_text(self) -> None:
        module = load_docling_runtime()
        text = "This is a normal extracted paragraph with enough words and numbers 123. " * 20
        report = module.evaluate_ocr_quality(text, pages=1)
        self.assertEqual(report["status"], "ok")
        self.assertTrue(report["passes"])

    def test_ocrspace_response_parsing_and_rendering(self) -> None:
        module = load_docling_runtime()
        text, summary = module.parse_ocrspace_response({
            "_http_status": 200,
            "IsErroredOnProcessing": False,
            "OCRExitCode": 1,
            "ParsedResults": [{"ParsedText": "Hello page"}],
        })
        self.assertEqual(text, "Hello page")
        self.assertEqual(summary["parsed_text_lengths"], [10])
        rendered = module.render_ocr_fallback_output({"pages": [{"page": 1, "text": text}]}, "md")
        self.assertIn("OCR.space page 1", rendered)
        self.assertIn("Hello page", rendered)

    def test_validate_remote_ocr_args_accepts_env_key_name_only(self) -> None:
        module = load_docling_runtime()
        args = SimpleNamespace(
            ocr_fallback="ocrspace",
            allow_remote_ocr=True,
            ocrspace_max_pages=1,
            ocrspace_dpi=200,
            ocrspace_timeout=10,
        )
        with patch.dict(os.environ, {"OCRSPACE_API_KEY": "secret"}, clear=False):
            module.validate_remote_ocr_args(args)
            self.assertEqual(module.ocrspace_key_env(), "OCRSPACE_API_KEY")

    def test_output_path_requires_overwrite_for_existing_file(self) -> None:
        module = load_docling_runtime()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out.md"
            output.write_text("old", encoding="utf-8")

            with self.assertRaisesRegex(module.DoclingRuntimeError, "--overwrite"):
                module.validate_output_path(str(output))

            self.assertEqual(module.validate_output_path(str(output), overwrite=True), output)

    @unittest.skipIf(os.name == "nt", "POSIX shell wrapper test")
    def test_posix_wrapper_uses_aas_runtime_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docling_dir = Path(tmp) / "skills" / "docling"
            docling_dir.mkdir(parents=True)
            shutil.copy2(DOCLING_DIR / "run_docling.sh", docling_dir / "run_docling.sh")
            (docling_dir / "doctor.py").write_text(
                "import json, sys\nprint(json.dumps({'executable': sys.executable, 'args': sys.argv[1:]}))\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AAS_RUNTIME_PYTHON"] = sys.executable
            env.pop("DOCLING_PYTHON", None)
            completed = subprocess.run(
                ["bash", str(docling_dir / "run_docling.sh"), "doctor", "arg1", "arg2"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(Path(payload["executable"]).resolve(), Path(sys.executable).resolve())
        self.assertEqual(payload["args"], ["arg1", "arg2"])


if __name__ == "__main__":
    unittest.main()
