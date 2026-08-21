"""What the command line says has to be what the command receives.

Every case here is a flag that parsed cleanly, exited zero, and then acted on a
different value than the one written -- a root that stayed relative, a tilde
that stayed literal, a subcommand option the top level had already claimed, or
a requested target the plan quietly declined to serve.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.ai_agents_skills import cli
from installer.ai_agents_skills.cli import (
    build_parser,
    install_root,
    is_real_system_root,
    normalize_global_flags,
    subcommand_flags_shadowing_a_global,
)

NATIVE_WINDOWS_MUTATION_SKIP = unittest.skipIf(
    os.name == "nt",
    "native Windows apply is dry-run-only until handle-bound mutation lands",
)


def parse(argv: list[str]):
    return build_parser().parse_args(normalize_global_flags(argv))


def run_cli(argv: list[str], cwd: Path | None = None) -> tuple[int, dict]:
    here = os.getcwd()
    if cwd is not None:
        os.chdir(cwd)
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = cli.main(argv)
    finally:
        os.chdir(here)
    text = buffer.getvalue()
    return code, json.loads(text[text.index("{"):])


def confirm_installs(test: unittest.TestCase) -> None:
    """Answer the install confirmation for the duration of one test.

    :func:`run_cli` calls :func:`cli.main` in this process, so the confirmation
    has to be readable from this process's environment.  Assigning it outright
    leaves it set for every later test in the session, and the tests elsewhere
    that prove an unconfirmed ``--apply`` refuses then read a confirmation they
    never gave and exit zero -- a leak that is invisible when this module runs
    alone and only appears once the whole suite runs in one process.
    """

    patcher = patch.dict(
        os.environ, {cli.INSTALL_CONFIRMATION_ENV: cli.INSTALL_CONFIRMATION_PHRASE}
    )
    patcher.start()
    test.addCleanup(patcher.stop)


class RootNormalizationTests(unittest.TestCase):
    def test_a_relative_root_is_anchored_at_parse_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            here = os.getcwd()
            os.chdir(tmp)
            try:
                self.assertEqual(
                    parse(["--root", "./sandbox", "list-skills"]).root,
                    Path(os.path.abspath("sandbox")),
                )
            finally:
                os.chdir(here)

    def test_a_tilde_root_is_the_home_the_appliers_write_to(self) -> None:
        # The appliers expanduser() the root and write into the real home, so a
        # caller-side check reading the literal '~' calls that home a sandbox.
        for spelling in (["--root", "~", "list-skills"], ["--root=~", "list-skills"]):
            with self.subTest(spelling=spelling):
                root = parse(spelling).root
                self.assertEqual(root, Path.home())
                self.assertTrue(is_real_system_root(root))

    def test_an_absolute_root_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(parse(["--root", tmp, "list-skills"]).root, Path(tmp))

    def test_a_symlinked_root_keeps_the_name_it_was_given(self) -> None:
        # abspath, not resolve: the agents are configured with the path the user
        # named, so collapsing it would record artifacts under a path no agent
        # reads.
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            real.mkdir()
            link = Path(tmp) / "link"
            link.symlink_to(real)
            self.assertEqual(install_root(str(link)), link)

    @NATIVE_WINDOWS_MUTATION_SKIP
    def test_an_uninstall_reaches_what_a_relative_install_recorded(self) -> None:
        """Records are re-read against the root of a later command.

        A relative root wrote relative artifact paths, which resolve against
        whatever directory the next command runs from.  Naming the very same
        directory absolutely then put every record "outside selected root": the
        uninstall skipped all of them, reported ``dry_run: false`` with an empty
        removal list, exited zero, and left every managed file installed.
        """
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "sandbox" / ".claude").mkdir(parents=True)
            confirm_installs(self)

            code, _ = run_cli(
                ["--json", "install", "--root", "./sandbox", "--skill", "sagemath",
                 "--apply", "--post-install-smoke", "off"],
                cwd=work,
            )
            self.assertEqual(code, 0)
            state = json.loads(
                (work / "sandbox" / ".ai-agents-skills" / "state.json").read_text(encoding="utf-8")
            )
            self.assertTrue(state["artifacts"])
            self.assertEqual(
                [item for item in state["artifacts"] if not Path(item["artifact"]).is_absolute()],
                [],
            )

            installed = work / "sandbox" / ".claude" / "skills" / "sagemath" / "SKILL.md"
            self.assertTrue(installed.exists())

            # The same directory, spelled absolutely, from a different cwd.
            code, report = run_cli(
                ["--json", "uninstall", "--root", str(work / "sandbox"), "--skill", "sagemath",
                 "--apply"],
                cwd=Path(__file__).resolve().parents[1],
            )
            self.assertEqual(code, 0)
            self.assertFalse(report["dry_run"])
            self.assertTrue(report["removed"])
            self.assertEqual(
                [item for item in report["actions"] if item.get("operation") == "skip-conflict"],
                [],
            )
            self.assertFalse(installed.exists())


class SubcommandFlagShadowingTests(unittest.TestCase):
    def test_no_subcommand_redeclares_a_top_level_flag(self) -> None:
        """argparse resolves the subcommand last, so a shared flag can only lose.

        A subparser that redeclares a global option shares its destination and
        overwrites whatever the top level parsed with its own default, and
        ``normalize_global_flags`` hoists those spellings past the subcommand
        anyway -- so such a flag reports its default whatever the user writes.
        """
        self.assertEqual(subcommand_flags_shadowing_a_global(build_parser()), {})

    def test_the_runtime_probe_receives_the_platform_it_was_given(self) -> None:
        for argv in (
            ["openclaw-runtime-probe", "--skill", "zotero", "--runtime-root", "/tmp/rt",
             "--platform", "windows", "--path-style", "windows-drive"],
            ["--platform", "windows", "openclaw-runtime-probe", "--skill", "zotero",
             "--runtime-root", "/tmp/rt", "--path-style", "windows-drive"],
        ):
            with self.subTest(argv=argv):
                args = parse(argv)
                self.assertEqual(args.platform, "windows")
                self.assertEqual(args.path_style, "windows-drive")

    def test_the_probe_still_defaults_to_linux(self) -> None:
        args = parse(["openclaw-runtime-probe", "--skill", "zotero", "--runtime-root", "/tmp/rt"])
        self.assertIsNone(args.platform)
        self.assertEqual(args.platform or "linux", "linux")

    def test_the_broker_serves_the_agent_it_was_given(self) -> None:
        for argv in (
            ["openclaw-broker", "--manifest", "m.json", "--runtime-root", "/tmp/rt",
             "--agent", "research"],
            ["--agent", "research", "openclaw-broker", "--manifest", "m.json",
             "--runtime-root", "/tmp/rt"],
        ):
            with self.subTest(argv=argv):
                self.assertEqual(parse(argv).agents, "research")

    def test_the_broker_refuses_a_multi_agent_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "m.json"
            manifest.write_text("{}", encoding="utf-8")
            code, report = run_cli(["--json", "openclaw-broker", "--manifest", str(manifest),
                                    "--runtime-root", tmp, "--agent", "one,two"])
            self.assertEqual(code, 1)
            self.assertIn("single agent", report["error"])


class SkippedTargetReportingTests(unittest.TestCase):
    def _blocked_home(self, work: Path) -> Path:
        home = work / "home"
        (home / ".claude").mkdir(parents=True)
        (work / "elsewhere").mkdir()
        # The planner refuses to write through a symlinked managed skill dir.
        (home / ".claude" / "skills").symlink_to(work / "elsewhere")
        return home

    @NATIVE_WINDOWS_MUTATION_SKIP
    def test_an_install_reports_the_target_it_did_not_serve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = self._blocked_home(Path(tmp))
            confirm_installs(self)
            code, report = run_cli([
                "--json", "install", "--root", str(home), "--agents", "claude",
                "--skill", "zotero", "--apply", "--post-install-smoke", "off",
            ])
            self.assertEqual(code, 0)
            self.assertEqual(report["actions"], [])
            self.assertEqual(
                [item["agent"] for item in report["skipped_agents"]], ["claude"]
            )

    @NATIVE_WINDOWS_MUTATION_SKIP
    def test_requiring_every_requested_target_asks_the_planners_question(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = self._blocked_home(Path(tmp))
            confirm_installs(self)
            code, report = run_cli([
                "--json", "install", "--root", str(home), "--agents", "claude",
                "--skill", "zotero", "--apply", "--require-all-requested-agents",
                "--post-install-smoke", "off",
            ])
            self.assertEqual(code, 1)
            self.assertEqual(report["status"], "error")
            self.assertIn("claude", report["error"])
            self.assertIn("symlink", report["error"])

    def test_a_healthy_target_is_not_reported_as_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            (home / ".claude").mkdir(parents=True)
            confirm_installs(self)
            code, report = run_cli([
                "--json", "install", "--root", str(home), "--agents", "claude",
                "--skill", "zotero", "--require-all-requested-agents",
            ])
            self.assertEqual(code, 0)
            self.assertNotIn("skipped_agents", report)
            self.assertTrue(report["actions"])


if __name__ == "__main__":
    unittest.main()
