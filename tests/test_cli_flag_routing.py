"""What the command line says has to be what the command receives.

Every case here is a flag that parsed cleanly, exited zero, and then acted on a
different value than the one written -- a root that stayed relative, a tilde
that stayed literal, a subcommand option the top level had already claimed, or
a requested target the plan quietly declined to serve.
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.ai_agents_skills import cli
from installer.ai_agents_skills.manifest import REPO_ROOT
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



FLAG = re.compile(r"(?<![\w-])(--[a-z0-9][a-z0-9-]{1,40})")


def flags_a_runtime_accepts(runtime: Path) -> set:
    """Every `--flag` literal the runtime names, in Python or in its shell runners."""

    accepted = set()
    for path in runtime.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and node.value.startswith("--") and " " not in node.value:
                accepted.add(node.value)
    for path in list(runtime.rglob("*.sh")) + list(runtime.rglob("*.bat")):
        try:
            accepted.update(FLAG.findall(path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return accepted


def documented_own_runner_flags(skill: str) -> list:
    """(flag, line) for each flag on a fenced command invoking this skill's runner.

    Anchoring on the skill's own `run_*.sh` is what makes this checkable: skill
    docs also show other skills' runners and `make ... ARGS=` lines, and those
    flags belong to a different parser.
    """

    doc = REPO_ROOT / "canonical" / "skills" / skill / "SKILL.md"
    own = re.compile(r"skills/" + re.escape(skill) + r"/run_[a-z0-9_]+\.(?:sh|bat)")
    found = []
    for block in re.findall(r"```[a-z]*\n(.*?)```", doc.read_text(encoding="utf-8"), flags=re.S):
        for line in block.splitlines():
            if own.search(line):
                found.extend((flag, line.strip()) for flag in FLAG.findall(line))
    return found


class DocumentedFlagsExistTests(unittest.TestCase):
    """A flag a SKILL.md tells the agent to type has to exist in the parser.

    The agent reads the skill and types what it shows. argparse answers an
    undeclared flag with exit 2 before the command body is entered, so the lane
    the doc was describing never runs -- and the failure surfaces as a usage
    error, which reads like the agent mistyped rather than like the doc is wrong.
    """

    def test_every_documented_flag_is_one_the_runtime_accepts(self) -> None:
        skills_dir = REPO_ROOT / "canonical" / "skills"
        undeclared = []
        checked = 0
        for doc in sorted(skills_dir.glob("*/SKILL.md")):
            skill = doc.parent.name
            runtime = REPO_ROOT / "canonical" / "runtime" / "skills" / skill
            if not runtime.is_dir():
                continue
            documented = documented_own_runner_flags(skill)
            if not documented:
                continue
            accepted = flags_a_runtime_accepts(runtime)
            for flag, line in documented:
                checked += 1
                if flag not in accepted:
                    undeclared.append(f"{skill}: {flag}\n    {line}")
        self.assertEqual(undeclared, [], "\n".join(undeclared))
        # A zero result is only evidence if the scan reached something.
        self.assertGreater(checked, 40, "the flag scan matched almost nothing")


def _load_cal():
    """Import calibre's cal.py against calibre's own `lib`, not whoever ran first.

    calibre and zotero each ship a top-level `lib` package, and their module
    names overlap (cache, config, doctor, filetype, gdrive, parallel, renamer,
    verifier). Each runtime runs in its own process in real use, so the clash is
    invisible there -- but the suite runs in one process, and whichever runtime a
    test imported first owns `lib` for every test after it. Loading cal.py on the
    ambient path passes alone and fails under `unittest discover` with
    `cannot import name 'remove_from_cache' from 'lib.cache'` pointing at
    zotero's copy. Prepend calibre's runtime and hand `lib` back afterwards.
    """

    runtime = REPO_ROOT / "canonical" / "runtime" / "skills" / "calibre"
    saved_path = list(sys.path)
    saved_modules = {
        name: module for name, module in sys.modules.items()
        if name == "lib" or name.startswith("lib.")
    }
    for name in saved_modules:
        del sys.modules[name]
    sys.path.insert(0, str(runtime))
    try:
        spec = importlib.util.spec_from_file_location(
            "_cal_under_test", runtime / "cal.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved_path
        for name in [n for n in sys.modules if n == "lib" or n.startswith("lib.")]:
            del sys.modules[name]
        sys.modules.update(saved_modules)


class CalibreSyncProgressTests(unittest.TestCase):
    """calibre/SKILL.md documents `sync --progress` and the shape it emits.

        bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
            skills/calibre/run_cal.sh sync [--force] [--progress]

        - Use `sync --progress` when pulling `metadata.db` may take time. Progress is
          emitted as JSON lines on stderr so stdout remains the final JSON result.

    The subparser declared only `--force`, so the documented command exited 2 --
    on exactly the slow pull the guidance was written for -- and `_progress`
    wrote a bare string, which no reader tailing stderr can parse.
    """

    def setUp(self) -> None:
        self.cal = _load_cal()

    def test_the_documented_command_parses(self) -> None:
        parsed = self.cal.build_parser().parse_args(["sync", "--force", "--progress"])
        self.assertTrue(parsed.force)
        self.assertTrue(parsed.progress)

    def test_sync_without_the_flag_still_parses(self) -> None:
        parsed = self.cal.build_parser().parse_args(["sync"])
        self.assertFalse(parsed.progress)

    def _emit(self, enabled: bool) -> str:
        self.cal._set_progress(enabled)
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            self.cal._progress("metadata.db: downloading 40% (4.0 MB / 10.0 MB)")
        return buffer.getvalue()

    def test_progress_lines_are_json(self) -> None:
        line = self._emit(True).rstrip("\n")
        self.assertEqual(
            json.loads(line),
            {"status": "progress",
             "message": "metadata.db: downloading 40% (4.0 MB / 10.0 MB)"},
        )

    def test_one_line_per_message(self) -> None:
        self.assertEqual(self._emit(True).count("\n"), 1)

    def test_it_stays_quiet_until_asked(self) -> None:
        self.assertEqual(self._emit(False), "")

    def test_the_stream_is_stderr_so_stdout_stays_the_result(self) -> None:
        self.cal._set_progress(True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.cal._progress("metadata.db: checking Google Drive")
        self.assertEqual(out.getvalue(), "")

    def test_cmd_sync_arms_the_stream_from_the_flag(self) -> None:
        """The flag has to reach the toggle, not merely parse."""

        class Args:
            force = False
            progress = True

        self.cal._set_progress(False)
        with patch.object(self.cal, "load_config", side_effect=RuntimeError("stop here")):
            with self.assertRaises(RuntimeError):
                self.cal.cmd_sync(Args())
        self.assertTrue(self.cal._PROGRESS_ENABLED)



class DocumentedLauncherResolvesTests(unittest.TestCase):
    """A documented launch command has to name a launcher that exists.

    `AAS_RUNTIME_ROOT` is assigned in exactly two places in the tree --
    `runners/run_skill.sh` and `runners/run_skill.ps1` -- where the launcher
    exports it for its own children. No installer writes it into an agent's
    environment, so a doc line spelled `bash "$AAS_RUNTIME_ROOT/run_skill.sh"`
    asks the reader to already hold the value the launcher itself produces. In
    the fresh shell an agent actually has, it expanded to `/run_skill.sh` and
    exited 127 before any skill argument was read.
    """

    # Launcher spellings only. Runtime code that reads a bare $AAS_RUNTIME_ROOT
    # runs as a child of run_skill.sh and is correct as it stands.
    POSIX_UNRESOLVED = '"$AAS_RUNTIME_ROOT/run_skill'
    WINDOWS_UNRESOLVED = ('"$env:AAS_RUNTIME_ROOT\\run_skill',
                          '"$env:AAS_RUNTIME_ROOT\\run_python')
    SUFFIXES = {".md", ".py", ".sh", ".ps1"}

    def _sources(self):
        roots = [REPO_ROOT / "canonical", REPO_ROOT / "docs"]
        files = [REPO_ROOT / "README.md", REPO_ROOT / "installer" / "ai_agents_skills" / "docs.py"]
        for root in roots:
            files += [f for f in sorted(root.rglob("*"))
                      if f.is_file() and f.suffix in self.SUFFIXES]
        return files

    def _offenders(self, needles) -> list[str]:
        found = []
        for path in self._sources():
            text = path.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if any(needle in line for needle in needles):
                    found.append(f"{path.relative_to(REPO_ROOT)}:{i}")
        return found

    def test_no_documented_posix_launcher_is_unresolved(self) -> None:
        self.assertEqual(self._offenders((self.POSIX_UNRESOLVED,)), [])

    def test_no_documented_windows_launcher_is_unresolved(self) -> None:
        self.assertEqual(self._offenders(self.WINDOWS_UNRESOLVED), [])

    def test_the_scan_reaches_the_files_it_claims_to(self) -> None:
        """A zero result is only evidence if the scan is not vacuous."""

        sources = self._sources()
        self.assertGreater(len(sources), 100, len(sources))
        resolved = sum(
            "${AAS_RUNTIME_ROOT:-" in f.read_text(encoding="utf-8", errors="replace")
            for f in sources
        )
        self.assertGreater(resolved, 20, resolved)

    def test_the_guard_fires_on_the_form_it_forbids(self) -> None:
        """The predicate itself, against the line the fix removed."""

        bad = 'bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/calibre/run_cal.sh sync'
        self.assertIn(self.POSIX_UNRESOLVED, bad)
        good = ('bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}'
                '/run_skill.sh" skills/calibre/run_cal.sh sync')
        self.assertNotIn(self.POSIX_UNRESOLVED, good)

    @unittest.skipIf(os.name == "nt", "POSIX parameter expansion")
    def test_the_two_forms_expand_differently_in_a_shell_with_it_unset(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "AAS_RUNTIME_ROOT"}
        env.setdefault("HOME", str(Path.home()))

        def expand(word: str) -> str:
            return subprocess.run(
                ["bash", "-c", f'printf "%s" "{word}"'],
                capture_output=True, text=True, encoding="utf-8", env=env, check=True,
            ).stdout

        self.assertEqual(expand("$AAS_RUNTIME_ROOT/run_skill.sh"), "/run_skill.sh")
        self.assertEqual(
            expand("${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh"),
            f"{env['HOME']}/.local/share/ai-agents-skills/runtime/run_skill.sh",
        )

    def test_the_documented_default_is_the_one_the_installer_would_pick(self) -> None:
        """Docs and installer must not drift on where the runtime lands."""

        from installer.ai_agents_skills.runtime import default_runtime_root

        class _Agent:
            def __init__(self, name: str) -> None:
                self.name = name

        chosen = default_runtime_root(
            Path("/h"), [_Agent("claude"), _Agent("codex")], platform="linux"
        )
        self.assertEqual(
            str(chosen), "/h/.local/share/ai-agents-skills/runtime", chosen
        )


if __name__ == "__main__":
    unittest.main()
