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
        self.assertIn("OCR.space configuration is not supported in Phase 1", completed.stderr)
        self.assertIn("OCR Engine 3", completed.stderr)
        self.assertNotIn("secret-api-value", completed.stderr)

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
