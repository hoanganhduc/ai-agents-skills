from __future__ import annotations

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from installer.ai_agents_skills.sanitize import has_sensitive_material, sanitize_text
from tools import sanitization_check


class SanitizationTests(unittest.TestCase):
    # One live sample per entry in sanitize.TOKEN_PATTERNS, plus the two home
    # substitutions that have no other coverage.  The fixture used to supply
    # four of the seven token patterns and assert only that "<REDACTED_SECRET>"
    # appeared *somewhere*, which any one of the four satisfied -- so deleting
    # the ``sk-``, ``github_pat_`` or PEM pattern left the suite fully green
    # while ``has_sensitive_material`` stopped flagging those secrets, and that
    # function is both the gate that refuses to install a runtime source
    # (runtime.py) and the only check ``make sanitize-check`` performs.
    SECRET_SAMPLES = (
        ("github oauth", "gho_" + "abcdefghijklmnopqrstuvwxyz123456"),
        ("github pat", "github_pat_" + "B" * 40),
        ("openai style", "sk-" + "A" * 40),
        ("aws access key", "AKIA" + "A" * 16),
        ("aws session key", "ASIA" + "B" * 16),
        ("google api key", "AIza" + "A" * 35),
        ("slack bot token", "xoxb-" + "1" * 24),
        (
            "pem private key",
            "-----BEGIN PRIVATE KEY-----\nQUJD\n-----END PRIVATE KEY-----",
        ),
        (
            "openssh private key",
            "-----BEGIN OPENSSH PRIVATE KEY-----\nQUJD\n-----END OPENSSH PRIVATE KEY-----",
        ),
    )
    PERSONAL_SAMPLES = (
        ("linux home", "/home/exampleuser/project", "<LINUX_HOME>"),
        ("wsl windows home", "/windows/Users/exampleuser/.codex", "<WINDOWS_HOME>"),
        ("mounted windows home", "/mnt/c/Users/exampleuser/.codex", "<WINDOWS_HOME>"),
        ("native windows home", "C:\\Users\\exampleuser\\.codex", "<WINDOWS_HOME>"),
        ("email", "person@example.com", "<EMAIL>"),
    )

    def test_every_secret_pattern_is_redacted_and_detected(self) -> None:
        """Each sample is asserted on its own, so no pattern rides on another.

        Both halves matter and fail independently: redaction is what keeps the
        secret out of a published artifact, and ``has_sensitive_material`` is
        what refuses to install the source in the first place.
        """

        for label, sample in self.SECRET_SAMPLES:
            with self.subTest(secret=label):
                result = sanitize_text(f"value={sample}\n", canonical_name="sample-skill")
                self.assertNotIn(sample, result)
                self.assertIn("<REDACTED_SECRET>", result)
                self.assertTrue(has_sensitive_material(sample))

    def test_every_personal_path_shape_is_replaced(self) -> None:
        for label, sample, placeholder in self.PERSONAL_SAMPLES:
            with self.subTest(personal=label):
                result = sanitize_text(f"path={sample}\n", canonical_name="sample-skill")
                self.assertNotIn(sample, result)
                self.assertIn(placeholder, result)

    def test_the_fixture_covers_every_declared_token_pattern(self) -> None:
        """Guards against a pattern being added with no sample beside it.

        Every entry in ``TOKEN_PATTERNS`` has to be the one that fires for at
        least one sample, which is what the four-of-seven fixture could not say.
        """

        from installer.ai_agents_skills.sanitize import TOKEN_PATTERNS

        matched = {
            index
            for index, pattern in enumerate(TOKEN_PATTERNS)
            for _, sample in self.SECRET_SAMPLES
            if pattern.search(sample)
        }
        self.assertEqual(
            sorted(matched),
            list(range(len(TOKEN_PATTERNS))),
            "TOKEN_PATTERNS entries with no sample in SECRET_SAMPLES",
        )

    def test_sanitize_replaces_personal_paths_and_tokens(self) -> None:
        text = "".join(f"p{i}={sample}\n" for i, (_, sample, _) in enumerate(self.PERSONAL_SAMPLES))
        text += "".join(f"s{i}={sample}\n" for i, (_, sample) in enumerate(self.SECRET_SAMPLES))
        result = sanitize_text(text, canonical_name="sample-skill")
        for _, sample in self.SECRET_SAMPLES:
            self.assertNotIn(sample, result)
        for _, sample, placeholder in self.PERSONAL_SAMPLES:
            self.assertNotIn(sample, result)
            self.assertIn(placeholder, result)
        self.assertIn("<REDACTED_SECRET>", result)

    def test_sanitize_normalizes_frontmatter_name(self) -> None:
        text = "---\nname: legacy_name\ndescription: test\n---\n\n# Test\n"
        self.assertIn("name: canonical-name", sanitize_text(text, "canonical-name"))

    def test_sensitive_material_detector_ignores_placeholders(self) -> None:
        self.assertFalse(has_sensitive_material("<LINUX_HOME> <WINDOWS_HOME> <EMAIL>"))
        self.assertFalse(has_sensitive_material("inspect `/windows/Users/...` from Linux"))
        self.assertTrue(has_sensitive_material("/home/exampleuser/file"))
        self.assertTrue(has_sensitive_material("/windows/Users/exampleuser/.codex"))

    def test_sanitization_check_skips_local_virtualenvs(self) -> None:
        self.assertIn(".venv", sanitization_check.SKIP_DIRS)

    def test_sanitization_check_skips_codex_run_artifacts(self) -> None:
        self.assertTrue(
            sanitization_check.should_skip_path(Path(".codex/runs/agent_group_discuss/repo_review/final.md"))
        )
        self.assertFalse(sanitization_check.should_skip_path(Path("docs/source/installation.md")))

    def test_sanitization_check_skips_what_git_ignores(self) -> None:
        ignored = frozenset({Path(".claude")})
        self.assertTrue(sanitization_check.should_skip_path(Path(".claude/settings.local.json"), ignored))
        # An untracked file that is not ignored is the next commit, so it stays
        # in scope.
        self.assertFalse(sanitization_check.should_skip_path(Path("docs/source/installation.md"), ignored))

    def test_git_ignored_prefixes_are_empty_outside_a_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sanitization_check.git_ignored_prefixes(Path(tmp)), frozenset())

    def test_git_ignored_prefixes_reads_the_repository_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            if subprocess.run(["git", "init", "-q", str(root)], check=False).returncode != 0:
                self.skipTest("git unavailable")
            (root / ".gitignore").write_text("secrets/\n", encoding="utf-8")
            (root / "secrets").mkdir()
            (root / "secrets" / "local.json").write_text("{}\n", encoding="utf-8")
            (root / "kept.md").write_text("# kept\n", encoding="utf-8")
            ignored = sanitization_check.git_ignored_prefixes(root)
            self.assertTrue(sanitization_check.should_skip_path(Path("secrets/local.json"), ignored))
            self.assertFalse(sanitization_check.should_skip_path(Path("kept.md"), ignored))

    def test_an_ignored_path_that_is_not_utf8_still_reads_as_ignored(self) -> None:
        """The prefix has to round-trip whatever bytes the filesystem holds.

        Decoding git's output with `replace` turns an undecodable byte into
        U+FFFD, so the prefix stops matching the name `rglob` yields and the
        ignored file is scanned after all.
        """
        if os.name != "posix":
            self.skipTest("non-UTF-8 filenames are a POSIX-only case")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            if subprocess.run(["git", "init", "-q", str(root)], check=False).returncode != 0:
                self.skipTest("git unavailable")
            (root / ".gitignore").write_text("*-tmp/\n", encoding="utf-8")
            name = os.fsdecode(b"caf\xe9-tmp")
            try:
                (root / name).mkdir()
            except OSError:
                # APFS and other UTF-8-enforcing filesystems reject the name
                # outright, so there is nothing to round-trip there.
                self.skipTest("filesystem refuses non-UTF-8 filenames")
            (root / name / "note.md").write_text("# note\n", encoding="utf-8")
            ignored = sanitization_check.git_ignored_prefixes(root)
            self.assertTrue(
                sanitization_check.should_skip_path(Path(name) / "note.md", ignored)
            )


if __name__ == "__main__":
    unittest.main()
