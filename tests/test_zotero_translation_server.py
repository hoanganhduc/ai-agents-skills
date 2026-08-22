from __future__ import annotations

import builtins
import contextlib
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SOURCE = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "zotero"
    / "scripts"
    / "start-translation-server.sh"
)
ARM64_IMAGE = (
    "zotero/translation-server@sha256:"
    "a80abfaaab0d84c8cc4b0ef79e4fde94b391420ee3a1e69d680fc89a18bff115"
)
AMD64_IMAGE = (
    "ghcr.io/hoanganhduc/translation-server@sha256:"
    "6bb209778e0403d81285404fc9ca5bd142f91e090d14a5541ac33018531c1329"
)
COMPOSE_IMAGE = "${ZOTERO_TS_IMAGE:?ZOTERO_TS_IMAGE must be set}"
DOCTOR_PATH = (
    REPO_ROOT / "canonical" / "runtime" / "skills" / "zotero" / "lib" / "doctor.py"
)


def load_doctor_module():
    spec = importlib.util.spec_from_file_location("canonical_zotero_doctor", DOCTOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


@unittest.skipIf(os.name == "nt", "Translation Server container startup is Linux-only")
class ZoteroTranslationServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.skill_dir = self.root / "zotero"
        self.script_dir = self.skill_dir / "scripts"
        self.fake_bin = self.root / "bin"
        self.script_dir.mkdir(parents=True)
        self.fake_bin.mkdir()
        self.script = self.script_dir / "start-translation-server.sh"
        self.script.write_bytes(SCRIPT_SOURCE.read_bytes())
        self.script.chmod(0o755)
        self.log = self.root / "calls.log"
        self._write_fake_commands()
        self.write_compose()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_executable(self, name: str, body: str) -> None:
        path = self.fake_bin / name
        path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _write_fake_commands(self) -> None:
        self._write_executable(
            "uname",
            """
            #!/usr/bin/env bash
            case "${1:-}" in
                -s) printf '%s\n' Linux ;;
                -m) printf '%s\n' "${ZOTERO_TEST_ARCH:-x86_64}" ;;
                *) exit 2 ;;
            esac
            """,
        )
        self._write_executable(
            "docker",
            """
            #!/usr/bin/env bash
            printf 'docker-image=%s\n' "${ZOTERO_TS_IMAGE:-unset}" >> "$ZOTERO_TEST_LOG"
            printf 'docker-args=' >> "$ZOTERO_TEST_LOG"
            printf '%s|' "$@" >> "$ZOTERO_TEST_LOG"
            printf '\n' >> "$ZOTERO_TEST_LOG"
            case " $* " in
                *" config --images "*)
                    printf '%s\n' "$ZOTERO_TS_IMAGE"
                    if [[ -n "${ZOTERO_TEST_EXTRA_RESOLVED_IMAGE:-}" ]]; then
                        printf '%s\n' "$ZOTERO_TEST_EXTRA_RESOLVED_IMAGE"
                    fi
                    ;;
            esac
            """,
        )
        self._write_executable(
            "curl",
            """
            #!/usr/bin/env bash
            printf 'curl\n' >> "$ZOTERO_TEST_LOG"
            printf '%s' "${ZOTERO_TEST_HTTP_CODE:-404}"
            """,
        )
        self._write_executable(
            "sleep",
            """
            #!/usr/bin/env bash
            printf 'sleep=%s\n' "${1:-}" >> "$ZOTERO_TEST_LOG"
            """,
        )

    def write_compose(self, image: str = COMPOSE_IMAGE, *, include_build: bool = False) -> None:
        build = "    build: .\n" if include_build else ""
        (self.skill_dir / "docker-compose.yml").write_text(
            "services:\n"
            "  translation-server:\n"
            f"    image: {image}\n"
            f"{build}"
            "    ports:\n"
            "      - '1969:1969'\n",
            encoding="utf-8",
        )

    def run_script(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.fake_bin}:{env['PATH']}",
                "ZOTERO_TEST_LOG": str(self.log),
                "ZOTERO_TS_HEALTH_ATTEMPTS": "2",
                "ZOTERO_TS_HEALTH_INTERVAL_SECONDS": "0",
                "ZOTERO_TS_HEALTH_REQUEST_TIMEOUT_SECONDS": "1",
            }
        )
        env.update(overrides)
        return subprocess.run(
            ["bash", str(self.script)],
            cwd=self.root,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )

    def read_log(self) -> str:
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def test_amd64_selects_locked_ghcr_image_and_starts(self) -> None:
        result = self.run_script(ZOTERO_TEST_ARCH="x86_64")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"docker-image={AMD64_IMAGE}", self.read_log())
        self.assertIn("docker-args=compose|-f|", self.read_log())
        self.assertIn("|up|-d|", self.read_log())
        self.assertIn("Translation Server is ready.", result.stdout)

    def test_arm64_selects_locked_official_image(self) -> None:
        result = self.run_script(ZOTERO_TEST_ARCH="aarch64")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"docker-image={ARM64_IMAGE}", self.read_log())

    def test_digest_only_override_is_exported_to_compose(self) -> None:
        override = "registry.example.test/team/zotero@sha256:" + ("1" * 64)
        result = self.run_script(ZOTERO_TS_IMAGE=override)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"docker-image={override}", self.read_log())

    def test_tagged_or_mutable_override_is_rejected_before_docker(self) -> None:
        for override in (
            "registry.example.test/team/zotero:latest",
            "registry.example.test/team/zotero:stable@sha256:" + ("1" * 64),
            "registry.example.test/team/zotero@sha256:" + ("A" * 64),
        ):
            with self.subTest(override=override):
                if self.log.exists():
                    self.log.unlink()
                result = self.run_script(ZOTERO_TS_IMAGE=override)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("@sha256", result.stderr)
                self.assertEqual(self.read_log(), "")

    def test_missing_or_mutable_compose_file_is_rejected(self) -> None:
        (self.skill_dir / "docker-compose.yml").unlink()
        missing = self.run_script()
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("missing", missing.stderr)
        self.assertEqual(self.read_log(), "")

        self.write_compose("ghcr.io/hoanganhduc/translation-server:latest")
        mutable = self.run_script()
        self.assertNotEqual(mutable.returncode, 0)
        self.assertIn("must be exactly", mutable.stderr)
        self.assertEqual(self.read_log(), "")

    def test_compose_build_declaration_is_rejected(self) -> None:
        self.write_compose(include_build=True)
        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not contain a build", result.stderr)
        self.assertEqual(self.read_log(), "")

    def test_multiple_compose_image_declarations_are_rejected(self) -> None:
        compose = self.skill_dir / "docker-compose.yml"
        compose.write_text(
            compose.read_text(encoding="utf-8")
            + "  unexpected-service:\n"
            + f"    image: {COMPOSE_IMAGE}\n",
            encoding="utf-8",
        )

        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly one image declaration", result.stderr)
        self.assertEqual(self.read_log(), "")

    def test_multiple_resolved_images_are_rejected_before_start(self) -> None:
        result = self.run_script(
            ZOTERO_TEST_EXTRA_RESOLVED_IMAGE="registry.example.test/other@sha256:" + ("2" * 64)
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("resolved an image other than", result.stderr)
        self.assertNotIn("|up|-d|", self.read_log())

    def test_health_check_is_bounded_and_timeout_fails(self) -> None:
        result = self.run_script(
            ZOTERO_TEST_HTTP_CODE="503",
            ZOTERO_TS_HEALTH_ATTEMPTS="3",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("after 3 attempts", result.stderr)
        self.assertEqual(self.read_log().count("curl\n"), 3)
        self.assertEqual(self.read_log().count("sleep=0\n"), 2)

    def test_health_bounds_reject_excessive_attempts_before_start(self) -> None:
        result = self.run_script(ZOTERO_TS_HEALTH_ATTEMPTS="61")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("integer from 1 to 60", result.stderr)
        self.assertNotIn("|up|-d|", self.read_log())



class ZoteroDoctorTranslationServerCheckTests(unittest.TestCase):
    """`zot doctor` must not report an installation fault as a healthy check.

    `_check_translation_server` put `import requests` inside its `try` and caught
    bare `Exception`, returning `ok: True` "Unreachable at <url>" for every cause.
    A missing `requests` -- a declared dependency in the skill's requirements.txt --
    and a malformed `translation_server` URL both landed there, so the one command
    whose job is to surface installation faults reported them as healthy.
    """

    def setUp(self) -> None:
        self.doctor = load_doctor_module()
        self.config = {"translation_server": "http://localhost:1969"}

    @contextlib.contextmanager
    def _requests(self, module):
        """Bind what `import requests` resolves to inside the check."""

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "requests":
                if module is None:
                    raise ModuleNotFoundError("No module named 'requests'")
                return module
            return real_import(name, *args, **kwargs)

        builtins.__import__ = fake_import
        try:
            yield
        finally:
            builtins.__import__ = real_import

    @staticmethod
    def _fake_requests(*, get):
        module = types.ModuleType("requests")

        class ConnectionError_(Exception):
            pass

        class Timeout(Exception):
            pass

        module.ConnectionError = ConnectionError_
        module.Timeout = Timeout
        module.get = get
        return module

    def test_missing_requests_fails_the_check_instead_of_claiming_unreachable(self) -> None:
        with self._requests(None):
            result = self.doctor._check_translation_server(self.config)

        self.assertFalse(result["ok"], result)
        self.assertIn("requests", result["message"])
        self.assertIn("requirements.txt", result["message"])
        self.assertNotIn("Unreachable at", result["message"])

    def test_an_unexpected_error_is_reported_rather_than_absorbed(self) -> None:
        """A malformed `translation_server` URL raises neither ConnectionError nor
        Timeout; it is a config fault and has to read as one."""

        def get(url, timeout=None):
            raise ValueError(f"No connection adapters were found for {url!r}")

        with self._requests(self._fake_requests(get=get)):
            result = self.doctor._check_translation_server(
                {"translation_server": "localhost:1969"}
            )

        self.assertFalse(result["ok"], result)
        self.assertIn("No connection adapters", result["message"])
        self.assertNotIn("Unreachable at", result["message"])

    def test_a_refused_connection_is_still_the_benign_optional_case(self) -> None:
        """The control. The server is optional, so a real refusal keeps `ok: True`
        and the fallback message the fix must not have changed."""

        module = self._fake_requests(get=None)

        def get(url, timeout=None):
            raise module.ConnectionError("connection refused")

        module.get = get
        with self._requests(module):
            result = self.doctor._check_translation_server(self.config)

        self.assertTrue(result["ok"], result)
        self.assertIn("Unreachable at http://localhost:1969", result["message"])
        self.assertIn("Direct DOI/arXiv/ISBN fallback", result["message"])

    def test_a_reachable_server_and_a_failing_one_keep_their_verdicts(self) -> None:
        for status, expected_ok in ((200, True), (404, True), (503, False)):
            with self.subTest(status=status):
                response = types.SimpleNamespace(status_code=status)
                module = self._fake_requests(get=lambda url, timeout=None: response)
                with self._requests(module):
                    result = self.doctor._check_translation_server(self.config)
                self.assertEqual(result["ok"], expected_ok, result)

if __name__ == "__main__":
    unittest.main()
