from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import textwrap
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


if __name__ == "__main__":
    unittest.main()
