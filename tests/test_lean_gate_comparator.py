"""The gate's ``comparator`` verb: its checks, the judge run and the run's pieces.

``comparator`` judges a solution project with ``lake comparator`` against a
challenge that ``challenge-freeze`` wrote. Before any judge runs, the verb
checks the host, admits the pinned record and the ``Challenge.lean`` beside
it, resolves and attests the toolchain the record names, checks the pinned
bwrap, and reads the solution's local import closure exactly once. It also
generates the workspace's ``lakefile.toml``, ``AASSolution.lean`` and
``comparator.json``. The run's pieces are tested on their own: the wrapper
program lake comparator runs in place of bwrap, the phase log it writes,
and the classification of a finished run. The run itself (state directory,
host guards, workspace, judge, integrity checks, receipt and cleanup) is
tested on Linux with a fake toolchain and a fake bwrap, and, where Lean
v4.35.0 and bubblewrap 0.12.0 or later are installed, with Lean itself.

Everything else runs on every platform, except the wrapper program itself,
which forks and runs bwrap and so is run only on POSIX hosts. The comparator
runs only on Linux, so the payload tests patch the platform and stand in for
the run, and admission, the closure, the generators, the wrapper's argument
check and the classification are also called directly. Mocks stand in for the
toolchain attestation and the bwrap probes, which would run tools, and a tool
that runs by mistake fails the test. A case that needs POSIX modes, a symlink
or a hard link is skipped with a stated reason on a host that cannot make it.
The gate module comes from ``test_lean_gate_scanner``, and the fake
toolchain and freeze helpers from ``test_lean_gate_challenge``, so one copy
of the gate is loaded.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import os
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest import mock

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import test_lean_gate_challenge as challenge_tests  # noqa: E402
import test_lean_gate_scanner as scanner  # noqa: E402

gate = scanner.gate
SKILL_MD = challenge_tests.SKILL_MD
DEFAULT_TOOLCHAIN = challenge_tests.DEFAULT_TOOLCHAIN
CHALLENGE_BYTES = challenge_tests.CHALLENGE.encode("utf-8")
CORE_ROOTS = ["Init", "Lake", "Lean", "Std"]
SANCTIONED = ["Classical.choice", "Quot.sound", "propext"]
SOLUTION = "theorem add_zero_right (n : Nat) : n + 0 = n := by\n  simp\n"
# The first line `lean --version` prints for a stable release.
BANNER = "Lean (version 4.35.0, aarch64-unknown-linux-gnu, commit 0123abcd, Release)"
STATUSES = (
    "not_run",
    "accepted",
    "rejected",
    "unsupported_toolchain",
    "tool_unavailable",
    "command_failed",
)
PAYLOAD_KEYS = {
    "schema_version",
    "ok",
    "mode",
    "input",
    "comparator_status",
    "comparator_rejection_kind",
    "comparator_detail",
    "findings",
    "challenge",
    "toolchain",
    "sandbox",
    "solution",
    "run",
    "receipt",
    "limitations",
}
SECTION_KEYS = {
    "challenge": {
        "record",
        "record_sha256",
        "challenge_sha256",
        "theorem_names",
        "permitted_axioms",
        "paranoid",
        "workspace",
    },
    "toolchain": {"spec", "dir", "lean_version", "files_sha256"},
    "sandbox": {"bwrap", "bwrap_version", "bwrap_sha256", "wrapper_sha256", "memory_limit"},
    "solution": {"modules", "files", "digest", "post_digest"},
    "run": {
        "run_id",
        "argv",
        "returncode",
        "seconds",
        "timeout_seconds",
        "phases",
        "stdout_tail",
        "stderr_tail",
        "workspace_kept",
    },
    "receipt": {"path", "sha256"},
}
LIMITATIONS = [
    "the comparator decides statement match, axiom use and kernel acceptance; whether "
    "the challenge states the intended claim is still a review step",
    "statement comparison is exact: an equivalent statement written differently is rejected",
    "a process running as the same user can change the toolchain, bwrap, the gate or a "
    "receipt; the digests detect a change made during a run, not one made before the "
    "freeze or after the receipt",
    "the solution's own build directory is writable inside the sandbox, so a solution can "
    "fill the disk; the disk floor and cleanup bound this only after the fact",
    "core profile: imports outside Init, Std, Lean and Lake are refused",
]
NO_MEMORY_LIMIT = "no memory limit was applied"
# The payload tests check what comes before the run; this refusal stands in
# for the run itself, which ComparatorRunTests covers on Linux.
STAND_IN_RUN = "the payload tests stand in for the run"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_record_bytes(record: Any) -> bytes:
    """The bytes challenge-freeze writes for ``record``."""

    return (
        json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode("ascii")


def _core_record(challenge: bytes = CHALLENGE_BYTES, **changes: Any) -> dict[str, Any]:
    """A core record as challenge-freeze writes it for ``challenge``, with ``changes``."""

    record: dict[str, Any] = {
        "schema_version": "aas_lean_challenge.v1",
        "challenge_sha256": _sha256(challenge),
        "challenge_module": "AASChallenge",
        "theorem_names": ["AASDemo.two_add_two", "add_zero_right"],
        "definition_names": [],
        "permitted_axioms": list(SANCTIONED),
        "paranoid": False,
        "toolchain": {"spec": DEFAULT_TOOLCHAIN, "files": {"bin/lake": "1" * 64, "bin/lean": "2" * 64}},
        "workspace": {"kind": "core"},
        "allowed_import_roots": list(CORE_ROOTS),
    }
    record.update(changes)
    return record


def _write_private(path: Path, data: bytes) -> Path:
    """Write ``data`` with mode 0600, as challenge-freeze writes its files."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o600)
    return path


def _write_record(
    directory: Path,
    record: Any,
    *,
    challenge: bytes | None = CHALLENGE_BYTES,
    data: bytes | None = None,
) -> tuple[Path, str]:
    """``challenge.json`` for ``record``, and ``Challenge.lean`` unless None, both 0600.

    ``data`` replaces the record's canonical bytes. Returns the record's path
    and the sha256 of its bytes.
    """

    raw = _canonical_record_bytes(record) if data is None else data
    path = _write_private(Path(directory) / "challenge.json", raw)
    if challenge is not None:
        _write_private(Path(directory) / "Challenge.lean", challenge)
    return path, _sha256(raw)


def _tree(root: Path, files: dict[str, str | bytes]) -> Path:
    """Write each POSIX relative path in ``files`` under ``root``."""

    for relative, content in files.items():
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    root.mkdir(parents=True, exist_ok=True)
    return root


@contextlib.contextmanager
def _linux() -> Any:
    """Look like Linux to the gate, run by a user that is not root.

    The record's owner rule compares a file's owner with os.geteuid(), so the
    patched value keeps the real effective uid where the host has a non-root
    one. Without one (Windows), or as root, it is 1000.
    """

    real = getattr(os, "geteuid", None)
    euid = real() if real is not None and real() != 0 else 1000
    with mock.patch.object(gate.sys, "platform", "linux"), mock.patch.object(
        gate.os, "geteuid", return_value=euid, create=True
    ):
        yield


def _skip_as_root(test: unittest.TestCase) -> None:
    if os.name == "posix" and os.geteuid() == 0:
        test.skipTest(
            "as root, the non-root euid the platform patch reports does not own the "
            "test's files, so the record's owner rule refuses them"
        )


def _symlink_or_skip(test: unittest.TestCase, link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:
        test.skipTest(f"this host cannot create a symlink: {exc}")


def _hard_link_or_skip(test: unittest.TestCase, link: Path, target: Path) -> None:
    try:
        os.link(target, link)
    except (OSError, NotImplementedError, AttributeError) as exc:
        test.skipTest(f"this host cannot create a hard link: {exc}")


def _assert_refused(
    test: unittest.TestCase,
    call: Callable[[], Any],
    status: str,
    kind: str,
    detail: str = "",
) -> Any:
    with test.assertRaises(gate.ComparatorRefusal) as caught:
        call()
    refusal = caught.exception
    test.assertEqual((refusal.status, refusal.kind), (status, kind), refusal.detail)
    test.assertIn(detail, refusal.detail)
    return refusal


def _comparator_section() -> str:
    """SKILL.md's "## Comparator" section, whitespace normalized."""

    text = SKILL_MD.read_text(encoding="utf-8")
    start = text.index("\n## Comparator\n")
    end = text.find("\n## ", start + 1)
    return " ".join(text[start : end if end != -1 else len(text)].split())


class ComparatorCliTests(unittest.TestCase):
    REQUIRED = {
        "--input": "project",
        "--challenge-record": "frozen/challenge.json",
        "--challenge-record-sha256": "a" * 64,
        "--solution-module": "Solution",
    }

    def _argv(self, *, drop: str = "", **values: str) -> list[str]:
        argv: list[str] = []
        for flag, value in self.REQUIRED.items():
            if flag != drop:
                argv += [flag, values.get(flag, value)]
        return argv

    def _main(self, *args: str) -> tuple[Any, list[dict[str, Any]], str]:
        emitted: list[dict[str, Any]] = []
        stderr = io.StringIO()
        with mock.patch.object(gate, "emit", side_effect=emitted.append), contextlib.redirect_stderr(
            stderr
        ):
            try:
                code = gate.main(["comparator", *args])
            except SystemExit as exc:
                code = exc.code
        return code, emitted, stderr.getvalue()

    def test_flags_exit_codes_and_usage(self) -> None:
        # A usage error exits 2, and its error line names the flag it is about
        # (the usage line above it names every flag).
        usage_errors = [(f"without {flag}", self._argv(drop=flag), flag) for flag in self.REQUIRED]
        for value in ("A" * 64, "a" * 63, "a" * 65, "g" * 64, " " + "a" * 63):
            usage_errors.append(
                (
                    f"sha {value[:3]!r}... of {len(value)} characters",
                    self._argv(**{"--challenge-record-sha256": value}),
                    "--challenge-record-sha256",
                )
            )
        for value in ("10GB", "0G", "10g", "", "10", "G", "-1G", "1.5G", "010G", "off "):
            usage_errors.append(
                (f"memory limit {value!r}", [*self._argv(), "--memory-limit", value], "--memory-limit")
            )
        usage_errors.append(("timeout 0", [*self._argv(), "--timeout", "0"], "--timeout"))
        for label, argv, flag in usage_errors:
            with self.subTest(case=label):
                code, emitted, stderr = self._main(*argv)
                self.assertEqual(code, 2, stderr)
                error_line = stderr.strip().splitlines()[-1]
                self.assertIn(flag, error_line)
                self.assertNotIn("invalid choice", stderr)
                self.assertEqual(emitted, [])

        # The argparse types on their own.
        self.assertEqual(gate.sha256_argument("0123456789abcdef" * 4), "0123456789abcdef" * 4)
        for value in ("off", "1K", "512M", "10G", "1234567G"):
            with self.subTest(memory_limit=value):
                self.assertEqual(gate.memory_limit_argument(value), value)
        for function, value in (
            (gate.sha256_argument, "F" * 64),
            (gate.memory_limit_argument, "0M"),
            (gate.memory_limit_argument, "10GB"),
        ):
            with self.subTest(function=function.__name__, value=value):
                with self.assertRaises(gate.argparse.ArgumentTypeError):
                    function(value)

        # Dispatch: the flags reach comparator_payload, and only an accepted
        # payload exits 0.
        accepted = {"ok": True, "comparator_status": "accepted"}
        with mock.patch.object(gate, "comparator_payload", return_value=accepted) as payload:
            code, emitted, _ = self._main(*self._argv(), "--solution-module", "Other")
        self.assertEqual((code, emitted), (0, [accepted]))
        self.assertEqual(payload.call_args.args, (Path("project"),))
        self.assertEqual(
            payload.call_args.kwargs,
            {
                "challenge_record": Path("frozen/challenge.json"),
                "challenge_record_sha256": "a" * 64,
                "solution_modules": ["Solution", "Other"],
                "timeout": 300,
                "paranoid": False,
                "keep_workspace": False,
                "memory_limit": "10G",
            },
        )
        with mock.patch.object(gate, "comparator_payload", return_value=accepted) as payload:
            code, _, _ = self._main(
                *self._argv(),
                "--timeout",
                "7",
                "--paranoid",
                "--keep-workspace",
                "--memory-limit",
                "512M",
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            {
                key: payload.call_args.kwargs[key]
                for key in ("timeout", "paranoid", "keep_workspace", "memory_limit")
            },
            {"timeout": 7, "paranoid": True, "keep_workspace": True, "memory_limit": "512M"},
        )
        for result in (
            {"ok": False, "comparator_status": "rejected"},
            {"ok": True, "comparator_status": "rejected"},
            {"ok": False, "comparator_status": "accepted"},
            {"ok": False, "comparator_status": "command_failed"},
        ):
            with self.subTest(result=result), mock.patch.object(
                gate, "comparator_payload", return_value=result
            ):
                code, emitted, _ = self._main(*self._argv())
                self.assertEqual((code, emitted), (1, [result]))

        # A refused run exits 1 with its payload; `off` parses and is recorded.
        with mock.patch.object(gate.sys, "platform", "darwin"):
            code, emitted, _ = self._main(*self._argv(), "--memory-limit", "off")
        self.assertEqual(code, 1)
        self.assertEqual(len(emitted), 1)
        refused = emitted[0]
        self.assertEqual(refused["mode"], "comparator")
        self.assertEqual(refused["comparator_status"], "tool_unavailable")
        self.assertEqual([item["kind"] for item in refused["findings"]], ["platform_unsupported"])
        self.assertEqual(refused["sandbox"]["memory_limit"], "off")
        self.assertEqual(refused["limitations"], [*LIMITATIONS, NO_MEMORY_LIMIT])

        section = _comparator_section()
        for phrase in (
            "run_lean_strict_verification_gate.sh comparator",
            "`--input`",
            "`--challenge-record`",
            "`--challenge-record-sha256`",
            "`--solution-module`",
            "`--timeout`",
            "`--paranoid`",
            "`--keep-workspace`",
            "`--memory-limit`",
            *(f"`{status}`" for status in STATUSES),
            "Only `accepted` and `rejected` are verdicts",
            "The verb exits 0 only for `accepted`, 1 for every other result, and 2 for a usage error.",
            "`host_comparator_receipt.v1`",
            "`comparator.lock`",
            "`--keep-workspace` keeps it and reports its path as `run.workspace_kept`",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)

    def test_section_documents_the_judgment(self) -> None:
        """SKILL.md's Comparator section names what a run can end with, and why.

        It gives each check before the run with its finding, each rejection
        kind with the message of lake comparator v4.35.0 it stands for, each
        finding of a run without a verdict, the wrapper, the payload's fixed
        limitations word for word, the compiler-trust residual that a hidden
        axiom leaves, and that doctor's has_comparator reads listings only.
        The phrases of the host checks, the limits, the receipt and the
        cleanup were documented with the run itself, so those subtests are
        regression checks.
        """

        section = _comparator_section()
        findings_before_the_run = (
            "platform_unsupported",
            "running_as_root",
            "input_invalid",
            "challenge_record_inside_input",
            "challenge_record_unreadable",
            "challenge_record_mismatch",
            "challenge_record_invalid",
            "challenge_unreadable",
            "challenge_mismatch",
            "unsupported_workspace",
            "toolchain_unavailable",
            "toolchain_changed",
            "sandbox_unavailable",
            "paranoid_kernel_missing",
            "solution_module_invalid",
            "solution_module_missing",
            "solution_file_unsafe",
            "solution_shadows_trusted_module",
            "solution_import_not_allowed",
            "solution_too_large",
            "state_dir_inside_input",
            "insufficient_disk",
            "memory_limit_unavailable",
            "wrapper_interpreter_unavailable",
            "host_busy",
            "workspace_setup_failed",
        )
        findings_without_a_verdict = (
            "phase_log_unreadable",
            "wrapper_refused",
            "comparator_exit",
            "comparator_could_not_start",
            "inconsistent_run",
            "unclassified_failure",
            "resolve_failed",
            "challenge_build_failed",
            "challenge_export_failed",
            "solution_build_aborted",
            "solution_export_failed",
            "external_kernel_disagreement",
            "kernel_failed",
            "sandbox_failed",
            "timeout",
            "output_limit",
            "comparator_failed",
            "receipt_write_failed",
            "cleanup_failed",
        )
        messages = (
            "`Challenge and solution theorem statement do not match: '<name>'`",
            "`Challenge and solution constant kind don't match: '<name>'`",
            "`Solution constant is not a theorem: '<name>'`",
            "`Solution constant is not a definition: '<name>'`",
            "`Const does not match between challenge and target '<name>'`",
            "`Const not found in solution '<name>'`",
            "`Const not found in solution: '<name>'`",
            "`Constant not found in solution '<name>'`",
            "`Illegal axiom detected: '<name>'`",
            "`<kernel> exited with <n>`",
            "`Child exited with 1`",
        )
        for phrase in (
            *(f"`{kind}`" for kind in gate.COMPARATOR_REJECTION_KINDS),
            *(f"`{kind}`" for kind in findings_before_the_run + findings_without_a_verdict),
            *messages,
            *(" ".join(line.split()) for line in gate.COMPARATOR_LIMITATIONS),
            NO_MEMORY_LIMIT,
            "`wrapper.json`",
            "`phases.jsonl`",
            "`--share-net`",
            "`--unshare-all`",
            "`axiom helper._native.native_decide.ax_1_1 : False`",
            "`1 = 2`",
            "`has_comparator` reads directory listings",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)


class ComparatorAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.input = _tree(self.base / "input", {"Solution.lean": SOLUTION})
        self.cases = 0

    def _directory(self) -> Path:
        self.cases += 1
        return self.base / f"case{self.cases}"

    def _admit(self, path: Path, sha256: str) -> Any:
        return gate.admit_challenge_record(path, sha256, self.input)

    def test_freeze_round_trip_is_admitted(self) -> None:
        home = self.base / "home"
        home.mkdir()
        elan = self.base / "elan"
        challenge_tests._comparator_toolchain(elan / "toolchains" / "leanprover--lean4---v4.35.0")
        # The source gets its own directory: freeze refuses a toolchain that
        # lies in the source's directory.
        statement = self.base / "statement"
        statement.mkdir()
        source = challenge_tests._write_source(statement)
        out = self.base / "frozen"
        with challenge_tests._readiness_environment(home, ELAN_HOME=str(elan)):
            frozen = challenge_tests._freeze(
                source, out, theorems=("add_zero_right", "AASDemo.two_add_two")
            )
        self.assertTrue(frozen["ok"], frozen)
        record, record_bytes, challenge_bytes = self._admit(
            Path(frozen["challenge_record"]), frozen["record_sha256"]
        )
        self.assertEqual(record_bytes, (out / "challenge.json").read_bytes())
        self.assertEqual(challenge_bytes, source.read_bytes())
        self.assertEqual(record, json.loads(record_bytes.decode("ascii")))
        self.assertEqual(record["theorem_names"], ["AASDemo.two_add_two", "add_zero_right"])

    def test_record_file_and_content_refusals(self) -> None:
        valid, valid_sha = _write_record(self._directory(), _core_record())
        self.assertEqual(
            self._admit(valid, valid_sha),
            (_core_record(), valid.read_bytes(), CHALLENGE_BYTES),
        )

        # The record file. Lying inside --input is checked first, by real path.
        with self.subTest(case="inside --input"):
            inside, inside_sha = _write_record(self.input / "frozen", _core_record())
            _assert_refused(
                self, lambda: self._admit(inside, inside_sha), "not_run", "challenge_record_inside_input"
            )
            _assert_refused(
                self,
                lambda: self._admit(self.input / "absent" / "challenge.json", inside_sha),
                "not_run",
                "challenge_record_inside_input",
            )
        with self.subTest(case="inside --input through a symlinked directory"):
            alias = self.base / "alias"
            _symlink_or_skip(self, alias, self.input)
            _assert_refused(
                self,
                lambda: self._admit(alias / "frozen" / "challenge.json", inside_sha),
                "not_run",
                "challenge_record_inside_input",
            )
        with self.subTest(case="a symlink"):
            link = self._directory()
            link.mkdir()
            _write_private(link / "Challenge.lean", CHALLENGE_BYTES)
            _symlink_or_skip(self, link / "challenge.json", valid)
            _assert_refused(
                self,
                lambda: self._admit(link / "challenge.json", valid_sha),
                "not_run",
                "challenge_record_unreadable",
                "symlink",
            )
        with self.subTest(case="missing"):
            _assert_refused(
                self,
                lambda: self._admit(self.base / "none" / "challenge.json", valid_sha),
                "not_run",
                "challenge_record_unreadable",
                "does not exist",
            )
        with self.subTest(case="a directory"):
            directory = self._directory() / "challenge.json"
            directory.mkdir(parents=True)
            _assert_refused(
                self,
                lambda: self._admit(directory, valid_sha),
                "not_run",
                "challenge_record_unreadable",
                "not a regular file",
            )
        with self.subTest(case="over 1 MiB"):
            big = b" " * (1024 * 1024) + b"{}\n"
            path, sha = _write_record(self._directory(), _core_record(), data=big)
            _assert_refused(
                self,
                lambda: self._admit(path, sha),
                "not_run",
                "challenge_record_unreadable",
                "input_too_large",
            )
        with self.subTest(case="a hard link"):
            path, sha = _write_record(self._directory(), _core_record())
            _hard_link_or_skip(self, self.base / "second-name.json", path)
            _assert_refused(
                self, lambda: self._admit(path, sha), "not_run", "challenge_record_unreadable", "2 hard links"
            )
        with self.subTest(case="mode 0644"):
            if os.name != "posix":
                self.skipTest("only POSIX has the mode bits the private-record rule reads")
            path, sha = _write_record(self._directory(), _core_record())
            path.chmod(0o644)
            _assert_refused(
                self, lambda: self._admit(path, sha), "not_run", "challenge_record_unreadable", "0600"
            )
        with self.subTest(case="owned by another user"):
            if os.name != "posix":
                self.skipTest("only POSIX has the file owner the private-record rule reads")
            other = os.geteuid() + 1
            with mock.patch.object(gate.os, "geteuid", return_value=other):
                _assert_refused(
                    self,
                    lambda: self._admit(valid, valid_sha),
                    "not_run",
                    "challenge_record_unreadable",
                    "not owned by",
                )
        with self.subTest(case="another sha256"):
            _assert_refused(
                self, lambda: self._admit(valid, "0" * 64), "not_run", "challenge_record_mismatch"
            )

        # The record's content: each case is written canonically unless it
        # tests the canonical form, so only its own rule can refuse it.
        indented = (json.dumps(_core_record(), indent=1, sort_keys=True) + "\n").encode("ascii")
        missing = _core_record()
        del missing["paranoid"]
        files = {"bin/lean": "2" * 64}
        content_cases: list[tuple[str, Any, bytes | None, str]] = [
            ("indented JSON", None, indented, "canonical"),
            ("not JSON", None, b"{\n", "not JSON"),
            ("not UTF-8", None, b"\xff\n", "not JSON"),
            ("an array", [], None, "keys"),
            ("an extra key", {**_core_record(), "extra": 1}, None, "keys"),
            ("a missing key", missing, None, "keys"),
            ("another schema", _core_record(schema_version="aas_lean_challenge.v2"), None, "schema_version"),
            ("another challenge module", _core_record(challenge_module="Challenge"), None, "challenge_module"),
            (
                "a challenge sha that is not hex",
                _core_record(challenge_sha256="g" * 64),
                None,
                "challenge_sha256",
            ),
            (
                "an uppercase challenge sha",
                _core_record(challenge_sha256=_sha256(CHALLENGE_BYTES).upper()),
                None,
                "challenge_sha256",
            ),
            ("no theorem", _core_record(theorem_names=[]), None, "theorem_names"),
            (
                "unsorted theorems",
                _core_record(theorem_names=["add_zero_right", "AASDemo.two_add_two"]),
                None,
                "theorem_names",
            ),
            (
                "a duplicated theorem",
                _core_record(theorem_names=["add_zero_right", "add_zero_right"]),
                None,
                "theorem_names",
            ),
            (
                "theorem names as a string",
                _core_record(theorem_names="add_zero_right"),
                None,
                "theorem_names",
            ),
            ("an invalid theorem name", _core_record(theorem_names=["1bad"]), None, "1bad"),
            ("a _root_ theorem name", _core_record(theorem_names=["_root_.foo"]), None, "_root_.foo"),
            ("a definition hole", _core_record(definition_names=["AASDemo.d"]), None, "definition_names"),
            (
                "sorryAx",
                _core_record(permitted_axioms=sorted([*SANCTIONED, "sorryAx"])),
                None,
                "sorryAx, which is never permitted",
            ),
            (
                "an unknown axiom",
                _core_record(permitted_axioms=sorted([*SANCTIONED, "myAxiom"])),
                None,
                "myAxiom",
            ),
            (
                "unsorted axioms",
                _core_record(permitted_axioms=["propext", "Classical.choice"]),
                None,
                "permitted_axioms",
            ),
            (
                "duplicated axioms",
                _core_record(permitted_axioms=["propext", "propext"]),
                None,
                "permitted_axioms",
            ),
            ("no axiom", _core_record(permitted_axioms=[]), None, "permitted_axioms"),
            ("paranoid as a number", _core_record(paranoid=0), None, "paranoid"),
            ("a toolchain string", _core_record(toolchain=DEFAULT_TOOLCHAIN), None, "toolchain"),
            (
                "a toolchain with an extra key",
                _core_record(toolchain={"spec": DEFAULT_TOOLCHAIN, "files": files, "store": ""}),
                None,
                "toolchain",
            ),
            (
                "a toolchain spec as a number",
                _core_record(toolchain={"spec": 4, "files": files}),
                None,
                "toolchain",
            ),
            (
                "a toolchain digest that is not hex",
                _core_record(toolchain={"spec": DEFAULT_TOOLCHAIN, "files": {"bin/lean": "z" * 64}}),
                None,
                "toolchain",
            ),
            (
                "toolchain files as a list",
                _core_record(toolchain={"spec": DEFAULT_TOOLCHAIN, "files": []}),
                None,
                "toolchain",
            ),
            ("a workspace string", _core_record(workspace="core"), None, "workspace"),
            ("a workspace kind as a number", _core_record(workspace={"kind": 1}), None, "workspace"),
            (
                "a mathlib workspace whose paranoid is a string",
                _core_record(workspace={"kind": "mathlib"}, paranoid="yes"),
                None,
                "paranoid",
            ),
            (
                "other import roots",
                _core_record(allowed_import_roots=[*CORE_ROOTS, "Mathlib"]),
                None,
                "allowed_import_roots",
            ),
        ]
        for label, record, data, detail in content_cases:
            with self.subTest(content=label):
                path, sha = _write_record(self._directory(), record, data=data)
                _assert_refused(
                    self, lambda: self._admit(path, sha), "not_run", "challenge_record_invalid", detail
                )
        for label, workspace, roots in (
            ("a mathlib workspace", {"kind": "mathlib"}, CORE_ROOTS),
            ("a mathlib workspace with its own roots", {"kind": "mathlib"}, [*CORE_ROOTS, "Mathlib"]),
            ("a core workspace with another key", {"kind": "core", "store": "x"}, CORE_ROOTS),
        ):
            with self.subTest(workspace=label):
                record = _core_record(workspace=workspace, allowed_import_roots=list(roots))
                path, sha = _write_record(self._directory(), record)
                _assert_refused(
                    self, lambda: self._admit(path, sha), "tool_unavailable", "unsupported_workspace"
                )
        for axiom in ("Lean.ofReduceBool", "helper._native.native_decide.ax_1_1"):
            with self.subTest(admitted_compiler_trust=axiom):
                record = _core_record(permitted_axioms=sorted([*SANCTIONED, axiom]))
                path, sha = _write_record(self._directory(), record)
                self.assertEqual(self._admit(path, sha)[0], record)

        # Challenge.lean beside the record.
        with self.subTest(challenge="missing"):
            path, sha = _write_record(self._directory(), _core_record(), challenge=None)
            _assert_refused(
                self, lambda: self._admit(path, sha), "not_run", "challenge_unreadable", "does not exist"
            )
        with self.subTest(challenge="a symlink"):
            directory = self._directory()
            path, sha = _write_record(directory, _core_record(), challenge=None)
            _symlink_or_skip(self, directory / "Challenge.lean", valid.parent / "Challenge.lean")
            _assert_refused(
                self, lambda: self._admit(path, sha), "not_run", "challenge_unreadable", "symlink"
            )
        with self.subTest(challenge="changed after the freeze"):
            path, sha = _write_record(
                self._directory(), _core_record(), challenge=CHALLENGE_BYTES + b"-- edited\n"
            )
            _assert_refused(self, lambda: self._admit(path, sha), "not_run", "challenge_mismatch")
        with self.subTest(challenge="over 1 MiB"):
            big = b"-- " + b"x" * (1024 * 1024) + b"\n"
            path, sha = _write_record(self._directory(), _core_record(big), challenge=big)
            _assert_refused(
                self, lambda: self._admit(path, sha), "not_run", "challenge_unreadable", "input_too_large"
            )
        with self.subTest(challenge="a hard link"):
            path, sha = _write_record(self._directory(), _core_record())
            _hard_link_or_skip(self, self.base / "second-challenge.lean", path.parent / "Challenge.lean")
            _assert_refused(
                self, lambda: self._admit(path, sha), "not_run", "challenge_unreadable", "2 hard links"
            )
        with self.subTest(challenge="mode 0644"):
            if os.name != "posix":
                self.skipTest("only POSIX has the mode bits the private-record rule reads")
            path, sha = _write_record(self._directory(), _core_record())
            (path.parent / "Challenge.lean").chmod(0o644)
            _assert_refused(
                self, lambda: self._admit(path, sha), "not_run", "challenge_unreadable", "0600"
            )


class ComparatorClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.cases = 0

    def _input(self, files: dict[str, str | bytes]) -> Path:
        self.cases += 1
        return _tree(self.base / f"input{self.cases}", files)

    def _closure(self, root: Path, modules: list[str]) -> dict[str, bytes]:
        return gate.solution_closure(root, modules, list(CORE_ROOTS))

    def test_walk_and_snapshot(self) -> None:
        files = {
            "Sol/Extra.lean": "theorem extra : True := trivial\n",
            "Sol/Main.lean": (
                "import Sol.Lemmas\n"
                "import Sol.Aux\n"
                "import Lean.Elab\n"
                "import Std.Data.HashMap\n"
                "/- import Sol.Ghost -/\n"
                "-- import Sol.Ghost\n"
                "theorem main_thm : True := trivial\n"
                'def note := "\nimport Sol.Ghost\n"\n'
            ),
            "Sol/Lemmas.lean": "public import Sol.Deep\nimport Sol.Main\nimport Init\n",
            "Sol/Aux.lean": "meta import Sol.Lemmas\nimport Lake\n",
            "Sol/Deep.lean": "import all Sol.Aux\n",
            "Sol/Ghost.lean": "theorem ghost : True := trivial\n",
            "Unrelated.lean": "theorem unrelated : True := trivial\n",
        }
        root = self._input(files)
        with mock.patch.object(
            gate, "stable_regular_file_bytes", wraps=gate.stable_regular_file_bytes
        ) as reads:
            closure = self._closure(root, ["Sol.Main", "Sol.Extra", "Sol.Main"])
        # Breadth first from the sorted named modules: a depth-first walk
        # would read Sol/Deep.lean before Sol/Aux.lean.
        expected = ["Sol/Extra.lean", "Sol/Main.lean", "Sol/Lemmas.lean", "Sol/Aux.lean", "Sol/Deep.lean"]
        self.assertEqual(list(closure), expected)
        for relative in expected:
            self.assertEqual(closure[relative], files[relative].encode("utf-8"))
        read_paths = [Path(call.args[0]) for call in reads.call_args_list]
        self.assertEqual(len(read_paths), len(expected))
        self.assertEqual(
            sorted(path.relative_to(root).as_posix() for path in read_paths), sorted(expected)
        )

    def test_refusals(self) -> None:
        trivial = "theorem t : True := trivial\n"

        # Named modules. Each file exists, so only the name rule refuses it.
        for module, relative in (
            ("1Bad", "1Bad.lean"),
            ("Sol..Main", "Sol/Main.lean"),
            ("", "Sol/Main.lean"),
            ("_root_.Sol", "_root_/Sol.lean"),
            ("Sol._root_", "Sol/_root_.lean"),
            ("AASChallenge", "AASChallenge.lean"),
            ("AASChallenge.Y", "AASChallenge/Y.lean"),
            ("AASSolution", "AASSolution.lean"),
            ("AASSolution.X", "AASSolution/X.lean"),
            ("lakefile", "lakefile.lean"),
            ("Lean.Mine", "Lean/Mine.lean"),
            ("Init", "Init.lean"),
            ("Std.X", "Std/X.lean"),
            ("Lake", "Lake.lean"),
        ):
            with self.subTest(named=module):
                root = self._input({relative: trivial})
                _assert_refused(
                    self, lambda: self._closure(root, [module]), "not_run", "solution_module_invalid"
                )
        with self.subTest(named="none"):
            root = self._input({"Sol/Main.lean": trivial})
            _assert_refused(self, lambda: self._closure(root, []), "not_run", "solution_module_invalid")

        # Imports. Each imported file that could exist does, so only the
        # import rule refuses it.
        for line, extra, detail in (
            ("import AASChallenge", {"AASChallenge.lean": trivial}, "AASChallenge"),
            ("import AASSolution", {"AASSolution.lean": trivial}, "AASSolution"),
            ("import AASSolution.X", {"AASSolution/X.lean": trivial}, "AASSolution.X"),
            # A copied lakefile.lean would win over the generated lakefile.toml.
            ("import lakefile", {"lakefile.lean": trivial}, "lakefile"),
            ("import Mathlib.Data", {}, "Mathlib.Data"),
            ("import 1bad", {}, "1bad"),
            ("import", {}, "<missing>"),
            ("import _root_.Sol.Other", {"_root_/Sol/Other.lean": trivial}, "_root_.Sol.Other"),
            ("import Sol.Absent", {}, "Sol.Absent"),
        ):
            with self.subTest(imports=line):
                root = self._input({"Sol/Main.lean": f"{line}\n{trivial}", **extra})
                _assert_refused(
                    self,
                    lambda: self._closure(root, ["Sol.Main"]),
                    "not_run",
                    "solution_import_not_allowed",
                    detail,
                )
        for line, shadow in (("import Lean.Elab", "Lean/Elab.lean"), ("import Init", "Init.lean")):
            with self.subTest(shadows=shadow):
                root = self._input({"Sol/Main.lean": f"{line}\n{trivial}", shadow: trivial})
                _assert_refused(
                    self,
                    lambda: self._closure(root, ["Sol.Main"]),
                    "not_run",
                    "solution_shadows_trusted_module",
                    shadow.replace(".lean", "").replace("/", "."),
                )

        for module in ("Sol.Missing", "Nope.Main"):
            with self.subTest(missing=module):
                root = self._input({"Sol/Main.lean": trivial})
                _assert_refused(
                    self, lambda: self._closure(root, [module]), "not_run", "solution_module_missing", module
                )

        outside = _tree(self.base / "outside", {"Main.lean": trivial, "Sol/Main.lean": trivial})
        with self.subTest(unsafe="a symlinked file"):
            root = self._input({"Sol/Other.lean": trivial})
            _symlink_or_skip(self, root / "Sol" / "Main.lean", outside / "Main.lean")
            _assert_refused(
                self, lambda: self._closure(root, ["Sol.Main"]), "not_run", "solution_file_unsafe", "symlink"
            )
        with self.subTest(unsafe="a symlinked imported file"):
            root = self._input({"Sol/Main.lean": f"import Sol.Link\n{trivial}"})
            _symlink_or_skip(self, root / "Sol" / "Link.lean", outside / "Main.lean")
            _assert_refused(
                self, lambda: self._closure(root, ["Sol.Main"]), "not_run", "solution_file_unsafe", "symlink"
            )
        with self.subTest(unsafe="a symlinked directory"):
            root = self._input({"Other.lean": trivial})
            _symlink_or_skip(self, root / "Sol", outside / "Sol")
            _assert_refused(
                self, lambda: self._closure(root, ["Sol.Main"]), "not_run", "solution_file_unsafe", "symlink"
            )
        with self.subTest(unsafe="a hard link"):
            root = self._input({"Sol/Main.lean": trivial})
            _hard_link_or_skip(self, self.base / "second-main.lean", root / "Sol" / "Main.lean")
            _assert_refused(
                self,
                lambda: self._closure(root, ["Sol.Main"]),
                "not_run",
                "solution_file_unsafe",
                "2 hard links",
            )
        with self.subTest(unsafe="a directory named like a module file"):
            root = self._input({"Sol/Main.lean/Inner.lean": trivial})
            _assert_refused(
                self,
                lambda: self._closure(root, ["Sol.Main"]),
                "not_run",
                "solution_file_unsafe",
                "not a regular file",
            )
        with self.subTest(unsafe="a lexer error"):
            root = self._input({"Sol/Main.lean": "/- never closed\n" + trivial})
            _assert_refused(
                self,
                lambda: self._closure(root, ["Sol.Main"]),
                "not_run",
                "solution_file_unsafe",
                "lexical_scan_incomplete",
            )
        with self.subTest(unsafe="invalid UTF-8"):
            root = self._input({"Sol/Main.lean": trivial.encode("utf-8") + b"-- \xff\n"})
            _assert_refused(
                self,
                lambda: self._closure(root, ["Sol.Main"]),
                "not_run",
                "solution_file_unsafe",
                "invalid_utf8",
            )

        # The caps, patched small. The walk refuses past a cap, and never truncates.
        three = {
            "Sol/Main.lean": "import Sol.A\nimport Sol.B\n",
            "Sol/A.lean": "-- a\n",
            "Sol/B.lean": "-- b\n",
        }
        total = sum(len(text.encode("utf-8")) for text in three.values())
        largest = max(len(text.encode("utf-8")) for text in three.values())
        for constant, limit, admitted in (
            ("COMPARATOR_SOLUTION_MAX_FILES", 2, False),
            ("COMPARATOR_SOLUTION_MAX_FILES", 3, True),
            ("COMPARATOR_SOLUTION_MAX_TOTAL_BYTES", total - 1, False),
            ("COMPARATOR_SOLUTION_MAX_TOTAL_BYTES", total, True),
            ("COMPARATOR_SOLUTION_MAX_FILE_BYTES", largest - 1, False),
            ("COMPARATOR_SOLUTION_MAX_FILE_BYTES", largest, True),
        ):
            with self.subTest(cap=constant, limit=limit):
                root = self._input(three)
                with mock.patch.object(gate, constant, limit):
                    if admitted:
                        self.assertEqual(sorted(self._closure(root, ["Sol.Main"])), sorted(three))
                    else:
                        _assert_refused(
                            self,
                            lambda: self._closure(root, ["Sol.Main"]),
                            "not_run",
                            "solution_too_large",
                        )


class ComparatorGeneratorTests(unittest.TestCase):
    def test_exact_bytes(self) -> None:
        header = (
            'name = "aas_comparator"\n'
            'version = "0.1.0"\n'
            "\n"
            "[[lean_lib]]\n"
            'name = "AASChallenge"\n'
            "\n"
            "[[lean_lib]]\n"
            'name = "AASSolution"\n'
        )
        for modules, roots, globs in (
            (["Solution"], '"AASSolution", "Solution"', '"AASSolution", "Solution"'),
            (
                ["Sol.Main", "Sol.Lemmas"],
                '"AASSolution", "Sol"',
                '"AASSolution", "Sol.Lemmas", "Sol.Main"',
            ),
            (
                ["Zeta.A", "Alpha", "Sol.Main", "Alpha"],
                '"AASSolution", "Alpha", "Sol", "Zeta"',
                '"AASSolution", "Alpha", "Sol.Main", "Zeta.A"',
            ),
            # A letter outside the BMP stays a raw UTF-8 character: TOML has no
            # surrogate-pair escape.
            (["Sol.\U0001d538"], '"AASSolution", "Sol"', '"AASSolution", "Sol.\U0001d538"'),
        ):
            with self.subTest(modules=modules):
                self.assertEqual(
                    gate.comparator_lakefile(modules),
                    header + f"roots = [{roots}]\n" + f"globs = [{globs}]\n",
                )
        self.assertEqual(
            gate.comparator_solution_source(["Sol.Main", "Sol.Lemmas", "Sol.Main"]),
            "import Sol.Lemmas\nimport Sol.Main\n",
        )
        self.assertEqual(
            gate.comparator_config(_core_record()),
            b'{"challenge_module":"AASChallenge","definition_names":[],'
            b'"permitted_axioms":["Classical.choice","Quot.sound","propext"],'
            b'"solution_module":"AASSolution",'
            b'"theorem_names":["AASDemo.two_add_two","add_zero_right"]}\n',
        )
        self.assertEqual(
            gate.files_digest({"b.lean": "1" * 64, "a.lean": "2" * 64}),
            _sha256(('{"a.lean":"' + "2" * 64 + '","b.lean":"' + "1" * 64 + '"}').encode("ascii")),
        )


class ComparatorPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.base = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.home = self.base / "home"
        self.home.mkdir()
        self.elan = self.base / "elan"
        self.toolchain = challenge_tests._comparator_toolchain(
            self.elan / "toolchains" / "leanprover--lean4---v4.35.0"
        )
        self.files = challenge_tests._toolchain_files(self.toolchain)
        self.state = self.base / "state"
        self.input = _tree(self.base / "input", {"Solution.lean": SOLUTION})
        self.bwrap = _write_private(
            self.base / "opt" / "bubblewrap-0.12.0" / "bin" / "bwrap", b"#!/bin/sh\nexit 97\n"
        )
        self._use_record(self._record())
        stack.enter_context(
            challenge_tests._readiness_environment(
                self.home, ELAN_HOME=str(self.elan), XDG_STATE_HOME=str(self.state)
            )
        )

    def _record(self, **changes: Any) -> dict[str, Any]:
        """A core record pinning the fake toolchain's files, with ``changes``."""

        changes.setdefault("toolchain", {"spec": DEFAULT_TOOLCHAIN, "files": dict(self.files)})
        return _core_record(**changes)

    def _use_record(self, record: dict[str, Any]) -> None:
        self.cases = getattr(self, "cases", 0) + 1
        self.record = record
        self.record_path, self.record_sha = _write_record(self.base / f"frozen{self.cases}", record)

    def _attested(self, **changes: Any) -> dict[str, Any]:
        result = {
            "status": "ok",
            "files": dict(self.record["toolchain"]["files"]),
            "lean_version": BANNER,
            "version": "4.35.0",
            "detail": "",
        }
        result.update(changes)
        return result

    def _bwrap(self, **changes: Any) -> dict[str, Any]:
        result = {
            "status": "available",
            "path": str(self.bwrap),
            "source": "env",
            "version": "0.12.0",
            "version_source": "probe",
            "supported": True,
            "detail": "",
        }
        result.update(changes)
        return result

    def _payload(
        self,
        *,
        input_path: Path | None = None,
        attested: dict[str, Any] | None = None,
        bwrap: dict[str, Any] | None = None,
        preflight: dict[str, Any] | None = None,
        linux: bool = True,
        **overrides: Any,
    ) -> dict[str, Any]:
        """Run comparator_payload with the tool probes mocked; ``self.calls`` holds the mocks.

        _comparator_run is a stand-in that refuses with STAND_IN_RUN.
        """

        arguments: dict[str, Any] = {
            "challenge_record": self.record_path,
            "challenge_record_sha256": self.record_sha,
            "solution_modules": ["Solution"],
            "timeout": 300,
            "paranoid": False,
            "keep_workspace": False,
            "memory_limit": "10G",
        }
        arguments.update(overrides)
        preflight = preflight or {
            "status": "ok",
            "command": [],
            "returncode": 0,
            "stderr": "",
            "detail": "",
        }
        patches = (
            ("admit_challenge_record", {"wraps": gate.admit_challenge_record}),
            ("resolve_comparator_toolchain", {"wraps": gate.resolve_comparator_toolchain}),
            ("toolchain_attestation", {"return_value": attested or self._attested()}),
            ("bwrap_status", {"return_value": bwrap or self._bwrap()}),
            ("bwrap_preflight", {"return_value": preflight}),
            ("solution_closure", {"wraps": gate.solution_closure}),
            ("comparator_state_root", {"wraps": gate.comparator_state_root}),
            (
                "_comparator_run",
                {
                    "side_effect": gate.ComparatorRefusal(
                        "command_failed", "comparator_failed", STAND_IN_RUN
                    )
                },
            ),
            ("run_bounded_command", {"side_effect": AssertionError("a tool ran")}),
        )
        self.calls: dict[str, Any] = {}
        with contextlib.ExitStack() as stack:
            if linux:
                stack.enter_context(_linux())
            for name, options in patches:
                self.calls[name] = stack.enter_context(mock.patch.object(gate, name, **options))
            self.calls["Popen"] = stack.enter_context(
                mock.patch.object(gate.subprocess, "Popen", side_effect=AssertionError("a tool ran"))
            )
            return gate.comparator_payload(input_path or self.input, **arguments)

    def _assert_closed(self, payload: dict[str, Any]) -> None:
        self.assertEqual(set(payload), PAYLOAD_KEYS, payload)
        for section, keys in SECTION_KEYS.items():
            self.assertEqual(set(payload[section]), keys, section)
        self.assertEqual(payload["schema_version"], "lean-strict-verification-gate.v1")
        self.assertEqual(payload["mode"], "comparator")
        self.assertEqual(payload["comparator_rejection_kind"], "")
        self.assertEqual(payload["comparator_detail"], "")
        self.assertEqual(payload["receipt"], {"path": "", "sha256": ""})
        self.assertFalse(self.calls["run_bounded_command"].called)
        self.assertFalse(self.calls["Popen"].called)

    def _assert_finding(self, payload: dict[str, Any], status: str, kind: str, detail: str = "") -> None:
        self._assert_closed(payload)
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(payload["comparator_status"], status, payload)
        self.assertEqual([item["kind"] for item in payload["findings"]], [kind], payload)
        self.assertIn(detail, payload["findings"][0]["detail"])

    def _assert_not_called(self, *names: str) -> None:
        for name in names:
            self.assertFalse(self.calls[name].called, f"{name} ran after the refusal")

    def _filled_challenge(self) -> dict[str, Any]:
        return {
            "record": str(self.record_path.absolute()),
            "record_sha256": self.record_sha,
            "challenge_sha256": self.record["challenge_sha256"],
            "theorem_names": self.record["theorem_names"],
            "permitted_axioms": self.record["permitted_axioms"],
            "paranoid": self.record["paranoid"],
            "workspace": self.record["workspace"],
        }

    def test_platform_and_root_come_first(self) -> None:
        for platform, euid, kind in (
            ("darwin", 501, "platform_unsupported"),
            ("win32", None, "platform_unsupported"),
            ("linux", 0, "running_as_root"),
            ("linux", None, "running_as_root"),
        ):
            with self.subTest(platform=platform, euid=euid):
                geteuid = (
                    mock.patch.object(gate.os, "geteuid", None, create=True)
                    if euid is None
                    else mock.patch.object(gate.os, "geteuid", return_value=euid, create=True)
                )
                with mock.patch.object(gate.sys, "platform", platform), geteuid:
                    payload = self._payload(linux=False)
                self._assert_finding(payload, "tool_unavailable", kind)
                self._assert_not_called("admit_challenge_record", "resolve_comparator_toolchain")
                self.assertEqual(payload["challenge"]["record"], "")
                self.assertEqual(payload["input"], str(self.input))

    def test_input_and_record_refusals(self) -> None:
        _skip_as_root(self)
        not_a_directory = self.input / "Solution.lean"
        for label, path in (("missing", self.base / "absent"), ("a file", not_a_directory)):
            with self.subTest(input=label):
                payload = self._payload(input_path=path)
                self._assert_finding(payload, "not_run", "input_invalid")
                self._assert_not_called("admit_challenge_record")
        with self.subTest(input="a symlink"):
            alias = self.base / "alias"
            _symlink_or_skip(self, alias, self.input)
            payload = self._payload(input_path=alias)
            self._assert_finding(payload, "not_run", "input_invalid", "symlink")
            self._assert_not_called("admit_challenge_record")

        payload = self._payload(challenge_record_sha256="0" * 64)
        self._assert_finding(payload, "not_run", "challenge_record_mismatch")
        self._assert_not_called("resolve_comparator_toolchain")
        self.assertEqual(payload["challenge"]["record"], "")

        self._use_record(self._record(workspace={"kind": "mathlib"}))
        payload = self._payload()
        self._assert_finding(payload, "tool_unavailable", "unsupported_workspace")
        self._assert_not_called("resolve_comparator_toolchain")

    def test_toolchain_refusals(self) -> None:
        _skip_as_root(self)
        # A v4.34.1 record, written by hand: no tool runs and no Lake starts.
        self._use_record(
            _core_record(toolchain={"spec": "leanprover/lean4:v4.34.1", "files": dict(self.files)})
        )
        payload = self._payload()
        self._assert_finding(payload, "unsupported_toolchain", "unsupported_toolchain", "v4.34.1")
        self._assert_not_called("toolchain_attestation", "bwrap_status")
        self.assertEqual(payload["challenge"], self._filled_challenge())
        self.assertEqual(
            payload["toolchain"], {"spec": "", "dir": "", "lean_version": "", "files_sha256": ""}
        )

        self._use_record(self._record())
        empty = self.base / "empty-elan"
        empty.mkdir()
        with mock.patch.dict(os.environ, {"ELAN_HOME": str(empty)}):
            payload = self._payload()
        self._assert_finding(payload, "tool_unavailable", "toolchain_unavailable", "not installed")
        self._assert_not_called("toolchain_attestation")

        for label, attested, status, kind in (
            (
                "an older banner",
                self._attested(status="unsupported_toolchain", detail="Lean 4.34.1 is older than v4.35.0"),
                "unsupported_toolchain",
                "unsupported_toolchain",
            ),
            (
                "a lean that fails",
                self._attested(status="command_failed", detail="lean --version exited 1"),
                "tool_unavailable",
                "toolchain_unavailable",
            ),
            (
                "files that cannot be hashed",
                self._attested(status="tool_unavailable", detail="bin/lean is a symlink"),
                "tool_unavailable",
                "toolchain_unavailable",
            ),
        ):
            with self.subTest(attestation=label):
                payload = self._payload(attested=attested)
                self._assert_finding(payload, status, kind, attested["detail"])
                self._assert_not_called("bwrap_status")

        changed = dict(self.files)
        changed["bin/lean"] = "0" * 64
        del changed["lib/lean/libleanshared.so"]
        changed["bin/extra"] = "1" * 64
        payload = self._payload(attested=self._attested(files=changed))
        self._assert_finding(
            payload,
            "tool_unavailable",
            "toolchain_changed",
            "bin/extra, bin/lean, lib/lean/libleanshared.so",
        )
        self._assert_not_called("bwrap_status")
        every = {name: "0" * 64 for name in self.files}
        payload = self._payload(attested=self._attested(files=every))
        self._assert_finding(payload, "tool_unavailable", "toolchain_changed")
        named = sorted(self.files)
        detail = payload["findings"][0]["detail"]
        self.assertIn(", ".join(named[:5]), detail)
        self.assertNotIn(named[5], detail)
        self.assertIn(f"and {len(named) - 5} more", detail)

        payload = self._payload(
            attested=self._attested(
                version="4.36.0", lean_version=BANNER.replace("4.35.0", "4.36.0")
            )
        )
        self._assert_finding(payload, "tool_unavailable", "toolchain_changed", "4.36.0")
        self._assert_not_called("bwrap_status")
        self.assertEqual(payload["toolchain"]["spec"], "")

    def test_sandbox_and_paranoid_refusals(self) -> None:
        _skip_as_root(self)
        filled_toolchain = {
            "spec": DEFAULT_TOOLCHAIN,
            "dir": os.path.realpath(self.toolchain),
            "lean_version": BANNER,
            "files_sha256": gate.files_digest(self.files),
        }
        empty_sandbox = {
            "bwrap": "",
            "bwrap_version": "",
            "bwrap_sha256": "",
            "wrapper_sha256": "",
            "memory_limit": "10G",
        }
        payload = self._payload(
            bwrap=self._bwrap(status="tool_unavailable", detail="bubblewrap 0.9.0 is older than 0.12.0")
        )
        self._assert_finding(payload, "tool_unavailable", "sandbox_unavailable", "0.9.0")
        self._assert_not_called("bwrap_preflight", "solution_closure")
        self.assertEqual(payload["toolchain"], filled_toolchain)
        self.assertEqual(payload["sandbox"], empty_sandbox)

        payload = self._payload(
            preflight={
                "status": "command_failed",
                "command": [],
                "returncode": 1,
                "stderr": "",
                "detail": "bwrap exited 1: the sandbox did not start",
            }
        )
        self._assert_finding(payload, "tool_unavailable", "sandbox_unavailable", "did not start")
        self._assert_not_called("solution_closure")

        payload = self._payload(bwrap=self._bwrap(path=str(self.base / "gone" / "bwrap")))
        self._assert_finding(payload, "tool_unavailable", "sandbox_unavailable", "missing_file")
        self._assert_not_called("solution_closure")

        kernels = {
            name: digest
            for name, digest in self.files.items()
            if "lean4lean" not in name and "con-ron" not in name
        }
        self._use_record(self._record(toolchain={"spec": DEFAULT_TOOLCHAIN, "files": kernels}))
        payload = self._payload(paranoid=True)
        self._assert_finding(payload, "tool_unavailable", "paranoid_kernel_missing", "lean4lean, con-ron")
        self._assert_not_called("solution_closure")
        payload = self._payload()
        self._assert_finding(payload, "command_failed", "comparator_failed", STAND_IN_RUN)
        self._use_record(
            self._record(paranoid=True, toolchain={"spec": DEFAULT_TOOLCHAIN, "files": kernels})
        )
        payload = self._payload()
        self._assert_finding(payload, "tool_unavailable", "paranoid_kernel_missing", "lean4lean, con-ron")
        self._assert_not_called("solution_closure")
        self.assertEqual(
            payload["sandbox"],
            {
                **empty_sandbox,
                "bwrap": str(self.bwrap),
                "bwrap_version": "0.12.0",
                "bwrap_sha256": _sha256(self.bwrap.read_bytes()),
            },
        )

        self._use_record(self._record())
        payload = self._payload(paranoid=True)
        self._assert_finding(payload, "command_failed", "comparator_failed", STAND_IN_RUN)
        self.assertTrue(self.calls["_comparator_run"].call_args.args[1].paranoid)

    def test_solution_and_state_refusals(self) -> None:
        _skip_as_root(self)
        payload = self._payload(solution_modules=["Missing"])
        self._assert_finding(payload, "not_run", "solution_module_missing", "Missing")
        self._assert_not_called("comparator_state_root", "_comparator_run")
        self.assertEqual(payload["solution"], {"modules": [], "files": {}, "digest": "", "post_digest": ""})
        self.assertEqual(payload["sandbox"]["bwrap"], str(self.bwrap))

        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.input)}):
            payload = self._payload()
        self._assert_finding(payload, "not_run", "state_dir_inside_input")
        self._assert_not_called("_comparator_run")
        self.assertEqual(payload["solution"]["files"], {"Solution.lean": _sha256(SOLUTION.encode("utf-8"))})

        xdg = self.base / "xdg"
        equal = _tree(xdg / "ai-agents-skills" / "comparator", {"Solution.lean": SOLUTION})
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
            payload = self._payload(input_path=equal)
        self._assert_finding(payload, "not_run", "state_dir_inside_input")
        self._assert_not_called("_comparator_run")

        with self.subTest(state="a symlink into --input"):
            alias = self.base / "state-alias"
            _symlink_or_skip(self, alias, self.input)
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(alias)}):
                payload = self._payload()
            self._assert_finding(payload, "not_run", "state_dir_inside_input")
            self._assert_not_called("_comparator_run")

    def test_check_order_and_stub(self) -> None:
        _skip_as_root(self)
        payload = self._payload()
        self._assert_finding(payload, "command_failed", "comparator_failed")
        self.assertEqual(payload["findings"][0]["detail"], STAND_IN_RUN)
        self.assertEqual(payload["input"], str(self.input))
        self.assertEqual(payload["challenge"], self._filled_challenge())
        self.assertEqual(
            payload["toolchain"],
            {
                "spec": DEFAULT_TOOLCHAIN,
                "dir": os.path.realpath(self.toolchain),
                "lean_version": BANNER,
                "files_sha256": _sha256(
                    json.dumps(self.files, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ),
            },
        )
        self.assertEqual(
            payload["sandbox"],
            {
                "bwrap": str(self.bwrap),
                "bwrap_version": "0.12.0",
                "bwrap_sha256": _sha256(self.bwrap.read_bytes()),
                "wrapper_sha256": "",
                "memory_limit": "10G",
            },
        )
        files = {"Solution.lean": _sha256(SOLUTION.encode("utf-8"))}
        self.assertEqual(
            payload["solution"],
            {
                "modules": ["Solution"],
                "files": files,
                "digest": _sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")),
                "post_digest": "",
            },
        )
        self.assertEqual(
            payload["run"],
            {
                "run_id": "",
                "argv": [],
                "returncode": None,
                "seconds": 0,
                "timeout_seconds": 300,
                "phases": [],
                "stdout_tail": "",
                "stderr_tail": "",
                "workspace_kept": "",
            },
        )
        self.assertEqual(payload["limitations"], LIMITATIONS)
        self.assertEqual(
            self.calls["toolchain_attestation"].call_args.args, (os.path.realpath(self.toolchain),)
        )
        self.assertEqual(self.calls["bwrap_status"].call_args.kwargs, {"probe": True})
        self.assertEqual(self.calls["bwrap_preflight"].call_args.args, (str(self.bwrap),))

        context = self.calls["_comparator_run"].call_args.args[1]
        self.assertEqual(context.input_root, Path(os.path.abspath(self.input)))
        self.assertEqual(context.record_path, self.record_path.absolute())
        self.assertEqual(context.record, self.record)
        self.assertEqual(context.record_bytes, self.record_path.read_bytes())
        self.assertEqual(context.challenge_path, self.record_path.absolute().parent / "Challenge.lean")
        self.assertEqual(context.challenge_bytes, CHALLENGE_BYTES)
        self.assertEqual(context.toolchain_dir, os.path.realpath(self.toolchain))
        self.assertEqual(context.toolchain_files, self.files)
        self.assertEqual(
            (context.bwrap, context.bwrap_sha256), (str(self.bwrap), _sha256(self.bwrap.read_bytes()))
        )
        self.assertEqual(context.modules, ["Solution"])
        self.assertEqual(context.closure, {"Solution.lean": SOLUTION.encode("utf-8")})
        self.assertEqual(context.state_root, self.state / "ai-agents-skills" / "comparator")
        self.assertEqual(
            (context.paranoid, context.timeout, context.keep_workspace, context.memory_limit),
            (False, 300, False, "10G"),
        )
        # Nothing is created before the run.
        self.assertFalse(os.path.lexists(self.state))

        payload = self._payload(memory_limit="off", timeout=9, keep_workspace=True)
        self._assert_finding(payload, "command_failed", "comparator_failed", STAND_IN_RUN)
        self.assertEqual(payload["sandbox"]["memory_limit"], "off")
        self.assertEqual(payload["run"]["timeout_seconds"], 9)
        self.assertEqual(payload["limitations"], [*LIMITATIONS, NO_MEMORY_LIMIT])
        context = self.calls["_comparator_run"].call_args.args[1]
        self.assertEqual((context.timeout, context.keep_workspace, context.memory_limit), (9, True, "off"))

    def test_an_unexpected_error_is_comparator_failed(self) -> None:
        _skip_as_root(self)
        with mock.patch.object(gate, "files_digest", side_effect=RuntimeError("x" * 600)):
            payload = self._payload()
        self._assert_finding(payload, "command_failed", "comparator_failed")
        self.assertEqual(payload["findings"][0]["detail"], ("RuntimeError: " + "x" * 600)[:500])

    def test_constants_pin_the_contract(self) -> None:
        # The cap tests patch these values, so their own values are pinned here.
        self.assertEqual(gate.COMPARATOR_PAYLOAD_KEYS, PAYLOAD_KEYS)
        self.assertEqual(list(gate.COMPARATOR_LIMITATIONS), LIMITATIONS)
        self.assertEqual(gate.COMPARATOR_PARANOID_KERNELS, challenge_tests.BUNDLED_KERNELS)
        self.assertEqual(
            gate.COMPARATOR_RESERVED_MODULES, ("AASChallenge", "AASSolution", "lakefile")
        )
        self.assertEqual(
            (gate.COMPARATOR_DEFAULT_TIMEOUT, gate.COMPARATOR_DEFAULT_MEMORY_LIMIT), (300, "10G")
        )
        self.assertEqual(gate.CHALLENGE_RECORD_MAX_BYTES, 1024 * 1024)
        self.assertEqual(
            (
                gate.COMPARATOR_SOLUTION_MAX_FILES,
                gate.COMPARATOR_SOLUTION_MAX_TOTAL_BYTES,
                gate.COMPARATOR_SOLUTION_MAX_FILE_BYTES,
            ),
            (240, 15 * 1024 * 1024, 16 * 1024 * 1024),
        )
        # The run tests patch these, so their own values are pinned here too.
        self.assertEqual(gate.COMPARATOR_MIN_FREE_BYTES, 2 * 1024 * 1024 * 1024)
        self.assertEqual(
            (
                gate.COMPARATOR_SYSTEMD_RUN,
                gate.COMPARATOR_SYSTEMCTL,
                gate.COMPARATOR_NICE,
                gate.COMPARATOR_ENV,
                gate.COMPARATOR_WRAPPER_INTERPRETER,
            ),
            (
                "/usr/bin/systemd-run",
                "/usr/bin/systemctl",
                "/usr/bin/nice",
                "/usr/bin/env",
                "/usr/bin/python3",
            ),
        )
        self.assertEqual((gate.COMPARATOR_RESOLVE_TIMEOUT, gate.COMPARATOR_LOCK_MAX_WAIT), (60, 600))
        self.assertEqual(gate.COMPARATOR_RECEIPT_SCHEMA, "host_comparator_receipt.v1")

    def test_state_root_follows_xdg_state_home(self) -> None:
        default = self.home / ".local" / "state" / "ai-agents-skills" / "comparator"
        for label, value, expected in (
            ("absolute", str(self.state), self.state / "ai-agents-skills" / "comparator"),
            ("relative", os.path.join("relative", "state"), default),
            ("empty", "", default),
            ("unset", None, default),
        ):
            with self.subTest(XDG_STATE_HOME=label):
                with challenge_tests._readiness_environment(self.home):
                    os.environ.pop("XDG_STATE_HOME", None)
                    if value is not None:
                        os.environ["XDG_STATE_HOME"] = value
                    self.assertEqual(gate.comparator_state_root(), expected)
                self.assertFalse(os.path.lexists(self.state))
                self.assertFalse(os.path.lexists(self.home / ".local"))


# The judge run's own pieces, which the run does not call yet: the wrapper
# program lake comparator runs in place of bwrap for each sandboxed step, the
# phase log it writes, and the classification of a finished run.

OKAY_LINE = "Your solution is okay!"
STEPS_BEFORE_KERNELS = [
    ("resolve", ""),
    ("build", "AASChallenge"),
    ("export", "AASChallenge"),
    ("build", "AASSolution"),
    ("export", "AASSolution"),
]
# The name lake comparator v4.35.0 gives each kernel in its messages.
KERNEL_NAMES = {
    "leanchecker": "Lean default",
    "leanchecker-paranoid": "Lean paranoid",
    "lean4lean": "lean4lean",
    "nanoda_bin": "nanoda",
    "con-leche": "con-leche",
    "con-ron": "con-ron",
}
PHASE_LOG_KEYS = {"phase", "target", "rc", "seconds", "dropped", "refused"}
REJECTION_KINDS = (
    "statement_mismatch",
    "kind_mismatch",
    "constant_mismatch",
    "missing_constant",
    "forbidden_axiom",
    "kernel_rejected",
    "build_failed",
)
WRAPPER_IMPORTS = {"json", "os", "sys", "time"}
# The bwrap options that open and close every step, as lake comparator v4.35.0
# builds them (Lake/CLI/Check.lean, buildSandboxArgs, as logged in the parent
# evidence n3-s0a-bwrap-calls.log).
BWRAP_COVERS = [
    "--ro-bind", "/", "/",
    "--tmpfs", "/home",
    "--tmpfs", "/root",
    "--tmpfs", "/run/user",
    "--tmpfs", "/tmp",
    "--dir", "/tmp/home",
    "--dev", "/dev",
    "--proc", "/proc",
    "--clearenv",
]
BWRAP_ISOLATION = [
    "--setenv", "HOME", "/tmp/home",
    "--unshare-all",
    "--die-with-parent",
    "--new-session",
]
# What lake comparator exports for a core challenge whose one theorem is comm.
EXPORT_TARGETS = [
    "Quot", "Quot.mk", "Quot.lift", "Quot.ind", "comm", "propext", "Quot.sound",
    "Classical.choice", "Nat.add", "Nat.sub", "Nat.mul", "Nat.pow", "Nat.gcd", "Nat.div",
    "Nat.mod", "Nat.beq", "Nat.ble", "Nat.land", "Nat.lor", "Nat.xor", "Nat.shiftLeft",
    "Nat.shiftRight", "String.ofList", "Char.ofNat", "List", "eagerReduce", "Nat", "String",
    "String.mk", "Char", "optParam", "autoParam", "semiOutParam", "outParam",
]
# The fake bwrap the wrapper program runs: it records its arguments in
# calls.jsonl beside it, then follows scenario.json: remove a path, swap a
# path for a symlink, kill itself with a signal, or exit with rc.
FAKE_BWRAP = """\
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls.jsonl"), "a", encoding="utf-8") as calls:
    calls.write(json.dumps(sys.argv[1:]) + "\\n")
with open(os.path.join(here, "scenario.json"), encoding="utf-8") as handle:
    scenario = json.load(handle)
if "remove" in scenario:
    os.unlink(scenario["remove"])
if "symlink" in scenario:
    link, target = scenario["symlink"]
    os.unlink(link)
    os.symlink(target, link)
if "signal" in scenario:
    os.kill(os.getpid(), scenario["signal"])
sys.exit(scenario.get("rc", 0))
"""
POSIX_WRAPPER = (
    os.name == "posix" and hasattr(os, "fork"),
    "the wrapper program forks and runs bwrap, which it does only on POSIX hosts",
)
POSIX_PATHS = (
    os.name == "posix",
    "the wrapper checks POSIX paths: lake comparator and its wrapper run only on Linux",
)
# A key test_parse_phase_log removes from a phase-log entry.
_DROP = object()


def _wrapper_namespace() -> dict[str, Any]:
    """COMPARATOR_WRAPPER_SOURCE run as the module aas_comparator_wrapper, not as a program."""

    namespace: dict[str, Any] = {"__name__": "aas_comparator_wrapper"}
    exec(compile(gate.COMPARATOR_WRAPPER_SOURCE, "aas_comparator_wrapper", "exec"), namespace)
    return namespace


def _phase(phase: str, target: str, rc: int = 0, refused: str = "") -> dict[str, Any]:
    """One parsed phase-log entry."""

    return {
        "phase": phase,
        "target": target,
        "rc": rc,
        "seconds": 0.25,
        "dropped": ["--share-net"] if phase == "resolve" else [],
        "refused": refused,
    }


def _phases(*rcs: int, paranoid: bool = False) -> list[dict[str, Any]]:
    """The first len(rcs) steps lake comparator takes, the i-th exiting with rcs[i]."""

    kernels = [*challenge_tests.BUNDLED_KERNELS, "leanchecker"] if paranoid else ["leanchecker"]
    steps = [*STEPS_BEFORE_KERNELS, *(("kernel", kernel) for kernel in kernels)]
    if len(rcs) > len(steps):
        raise ValueError("more exit codes than steps")
    return [_phase(phase, target, rc) for (phase, target), rc in zip(steps, rcs)]


class ComparatorWrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        # Real paths, as wrapper.json holds them: a temporary directory may lie
        # behind a symlink, as on macOS.
        self.base = Path(os.path.realpath(stack.enter_context(tempfile.TemporaryDirectory())))
        self.toolchain = self.base / "toolchains" / "leanprover--lean4---v4.35.0"
        (self.toolchain / "bin").mkdir(parents=True)
        for name in ("lake", "lean", "leanexport", "sh", *KERNEL_NAMES):
            (self.toolchain / "bin" / name).write_bytes(f"{name}\n".encode("utf-8"))
        (self.toolchain / "lib" / "lean").mkdir(parents=True)
        (self.toolchain / "lib" / "lean" / "tool").write_bytes(b"tool\n")
        self.run = self.base / "state" / "ai-agents-skills" / "comparator" / "runs" / "run-1"
        self.ws = self.run / "ws"
        (self.ws / ".lake").mkdir(parents=True)
        self.tmp = self.run / "tmp"
        self.tmp.mkdir()
        # lake comparator's temporary files under TMPDIR: nanoda's configuration
        # and the solution's export.
        self.kernel_config = self.tmp / "tmp.Kcfg0001"
        self.export = self.tmp / "tmp.Xprt0002"
        for path in (self.kernel_config, self.export):
            path.write_bytes(b"{}\n")
        self.outside = self.base / "outside"
        self.outside.mkdir()
        self.config = {
            "bwrap": str(self.base / "opt" / "bubblewrap-0.12.0" / "bin" / "bwrap"),
            "log": str(self.run / "phases.jsonl"),
            "toolchain_dir": str(self.toolchain),
            "run_dir": str(self.run),
            "writable": str(self.ws / ".lake"),
        }
        self.wrapper = _wrapper_namespace()

    def _tool(self, name: str) -> str:
        return str(self.toolchain / "bin" / name)

    def _argv(
        self,
        command: list[Any],
        *,
        readable: list[Any],
        writable: tuple[Any, ...] = (),
        tmpfs: tuple[str, ...] = (),
        env: list[tuple[str, str]] | tuple[()] = (),
        network: bool = False,
        cwd: Path | None = None,
    ) -> list[str]:
        """The wrapper's arguments for one step, in the order lake comparator builds them."""

        args = list(BWRAP_COVERS)
        for path in tmpfs:
            args += ["--tmpfs", path]
        for path in readable:
            args += ["--ro-bind", str(path), str(path)]
        for path in writable:
            args += ["--bind", str(path), str(path)]
        for name, value in env:
            args += ["--setenv", name, value]
        args += BWRAP_ISOLATION
        if network:
            args.append("--share-net")
        args += ["--chdir", str(cwd or self.ws)]
        return [*args, "--", *(str(part) for part in command)]

    def _upstream_shapes(self) -> list[tuple[str, list[str], tuple[str, str, list[str]]]]:
        """Each step of a paranoid core run: (label, arguments, (phase, target, dropped))."""

        lake = self.ws / ".lake"
        path = f"{self.toolchain}/bin:/usr/bin:/bin"
        build_env = [("PATH", path), ("LEAN_ABORT_ON_PANIC", "1")]
        export_env = [
            ("LEAN_ABORT_ON_PANIC", "1"),
            ("LEAN_PATH", f"{lake}/build/lib/lean:{self.toolchain}/lib/lean"),
            ("PATH", f"{lake}/build/bin:{path}"),
        ]
        guarded = ("/run", "/var")
        shapes = [
            (
                "resolve",
                self._argv(
                    [self._tool("lake"), "env"],
                    readable=[self.ws, self.toolchain, self.toolchain],
                    writable=(lake,),
                    env=build_env,
                    network=True,
                ),
                ("resolve", "", ["--share-net"]),
            )
        ]
        for module in ("AASChallenge", "AASSolution"):
            shapes.append(
                (
                    f"build {module}",
                    self._argv(
                        [self._tool("lake"), "build", module],
                        tmpfs=guarded,
                        readable=[self.ws, self.toolchain, self.toolchain],
                        writable=(lake,),
                        env=build_env,
                    ),
                    ("build", module, []),
                )
            )
            shapes.append(
                (
                    f"export {module}",
                    self._argv(
                        [self._tool("leanexport"), module, "--", *EXPORT_TARGETS],
                        tmpfs=guarded,
                        readable=[self.ws, lake, self.toolchain, self._tool("leanexport")],
                        env=export_env,
                    ),
                    ("export", module, []),
                )
            )
        for kernel, arguments in (
            ("leanchecker", ["--silent", "--from-export", self.export]),
            ("leanchecker-paranoid", ["--silent", "--from-export", self.export]),
            ("lean4lean", ["--import", self.export]),
            ("nanoda_bin", [self.kernel_config]),
            ("con-leche", [self.export]),
            ("con-ron", [self.export]),
        ):
            shapes.append(
                (
                    kernel,
                    self._argv(
                        [self._tool(kernel), *arguments],
                        tmpfs=guarded,
                        readable=[
                            self.kernel_config, self.export, self._tool(kernel), self.toolchain
                        ],
                        env=[("LEAN_ABORT_ON_PANIC", "1")],
                        cwd=self.tmp,
                    ),
                    ("kernel", kernel, []),
                )
            )
        return shapes

    def test_source_is_self_contained(self) -> None:
        source = gate.COMPARATOR_WRAPPER_SOURCE
        self.assertNotIn("#!", source)
        # The run's wrapper runs under the host's /usr/bin/python3, which may be
        # as old as 3.9 (os.waitstatus_to_exitcode).
        tree = ast.parse(source, feature_version=(3, 9))
        compile(source, "aas_comparator_wrapper", "exec")
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or ".")
        self.assertLessEqual(imported, WRAPPER_IMPORTS)
        # Only definitions at the top level, then main() behind the program guard.
        *body, guard = tree.body
        for node in body:
            self.assertIsInstance(
                node, (ast.Expr, ast.Import, ast.Assign, ast.ClassDef, ast.FunctionDef)
            )
            if isinstance(node, ast.Expr):
                self.assertIs(node, tree.body[0], "only the docstring may be a bare expression")
        self.assertEqual(
            ast.unparse(guard), "if __name__ == '__main__':\n    sys.exit(main())"
        )
        # os.fork does not exist on Windows, hence create=True.
        with mock.patch.object(
            os, "fork", side_effect=AssertionError("fork"), create=True
        ), mock.patch.object(
            os, "execv", side_effect=AssertionError("execv")
        ), contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(
            io.StringIO()
        ) as err:
            namespace = _wrapper_namespace()
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))
        self.assertEqual(namespace["__name__"], "aas_comparator_wrapper")
        self.assertTrue(callable(namespace["main"]))
        self.assertEqual(namespace["PHASE_LOG_KEYS"], gate.COMPARATOR_PHASE_LOG_KEYS)
        self.assertEqual(namespace["PHASE_LOG_MAX_LINES"], gate.COMPARATOR_PHASE_LOG_MAX_LINES)
        self.assertEqual(set(namespace["KERNELS"]), set(gate.COMPARATOR_KERNEL_NAMES))
        self.assertEqual(
            namespace["MODULES"], (gate.CHALLENGE_MODULE, gate.COMPARATOR_SOLUTION_MODULE)
        )
        # What the wrapper logs, the gate parses back.
        record = namespace["phase_record"]
        line = record("resolve", "", 0, 1.5, ["--share-net"], "")
        refusal = record("refused", "", 125, 0.0, [], "r\u00e9fus\u00e9")
        self.assertTrue(line.endswith(b"\n") and refusal.isascii())
        self.assertEqual(
            gate.parse_phase_log(line + refusal),
            [
                {
                    "phase": "resolve",
                    "target": "",
                    "rc": 0,
                    "seconds": 1.5,
                    "dropped": ["--share-net"],
                    "refused": "",
                },
                {
                    "phase": "refused",
                    "target": "",
                    "rc": 125,
                    "seconds": 0.0,
                    "dropped": [],
                    "refused": "r\u00e9fus\u00e9",
                },
            ],
        )

    def test_constants_pin_the_contract(self) -> None:
        self.assertEqual(gate.COMPARATOR_PHASE_LOG_KEYS, PHASE_LOG_KEYS)
        self.assertEqual(
            (gate.COMPARATOR_PHASE_LOG_MAX_LINES, gate.COMPARATOR_PHASE_LOG_MAX_BYTES),
            (64, 64 * 1024),
        )
        self.assertEqual(gate.COMPARATOR_KERNEL_NAMES, KERNEL_NAMES)
        self.assertEqual(gate.COMPARATOR_REJECTION_KINDS, REJECTION_KINDS)

    @unittest.skipUnless(*POSIX_PATHS)
    def test_accepts_the_upstream_shapes(self) -> None:
        validate = self.wrapper["validate_bwrap_argv"]
        shapes = self._upstream_shapes()
        self.assertEqual(len(shapes), 11)
        for label, argv, expected in shapes:
            with self.subTest(step=label):
                separator = argv.index("--")
                opts, cmd, phase, target, dropped = validate(list(argv), self.config)
                self.assertEqual((phase, target, dropped), expected)
                self.assertEqual(opts, [arg for arg in argv[:separator] if arg != "--share-net"])
                self.assertEqual(cmd, argv[separator + 1 :])
                self.assertNotIn("--share-net", opts)
        # Options are read by their number of values, so a "--" that is a value
        # does not end them.
        build = dict((label, argv) for label, argv, _ in shapes)["build AASSolution"]
        start = len(BWRAP_COVERS)
        argv = [*build[:start], "--setenv", "AAS_VALUE", "--", *build[start:]]
        opts, cmd, phase, target, dropped = validate(argv, self.config)
        self.assertEqual((phase, target, dropped), ("build", "AASSolution", []))
        self.assertEqual(opts[start : start + 3], ["--setenv", "AAS_VALUE", "--"])
        self.assertEqual(cmd, [self._tool("lake"), "build", "AASSolution"])

    @unittest.skipUnless(*POSIX_PATHS)
    def test_refuses(self) -> None:
        validate = self.wrapper["validate_bwrap_argv"]
        refused = self.wrapper["Refused"]
        build = dict((label, argv) for label, argv, _ in self._upstream_shapes())[
            "build AASSolution"
        ]
        separator = build.index("--")
        options, command = build[:separator], build[separator + 1 :]
        lake_dir = str(self.ws / ".lake")
        lake, leanexport = self._tool("lake"), self._tool("leanexport")

        def extra(*more: str) -> list[str]:
            return [*options, *more, "--", *command]

        def without(option: str) -> list[str]:
            return [*(arg for arg in options if arg != option), "--", *command]

        def step(*cmd: str) -> list[str]:
            return [*options, "--", *cmd]

        not_allowed = "is not an allowed bwrap option"
        not_lake = "is not the workspace's writable .lake"
        not_readable = "is not /, the toolchain or the run directory"
        not_a_tool = "is not a program in the toolchain's bin directory"
        not_a_step = "is not a step lake comparator runs"
        cases: list[tuple[str, list[str], str, dict[str, str] | None]] = [
            ("--symlink", extra("--symlink", "/etc/passwd", "/tmp/x"), not_allowed, None),
            ("--bind-try", extra("--bind-try", lake_dir, lake_dir), not_allowed, None),
            ("--cap-add", extra("--cap-add", "ALL"), not_allowed, None),
            ("no --", list(options), "do not end with --", None),
            ("no -- before the command", [*options, *command], not_allowed, None),
            ("arity underflow", ["--ro-bind", "/"], "needs 2 values", None),
            ("arity underflow at the end", [*options, "--chdir"], "needs 1 value", None),
            (
                "--bind with another destination",
                extra("--bind", lake_dir, lake_dir + "2"),
                not_lake,
                None,
            ),
            ("--bind of the workspace", extra("--bind", *[str(self.ws)] * 2), not_lake, None),
            ("--bind of the run's tmp", extra("--bind", *[str(self.tmp)] * 2), not_lake, None),
            ("--ro-bind /etc", extra("--ro-bind", "/etc", "/etc"), not_readable, None),
            (
                "--ro-bind with another destination",
                extra("--ro-bind", str(self.toolchain), "/opt/lean"),
                not_readable,
                None,
            ),
            (
                "--ro-bind beside the run",
                extra("--ro-bind", str(self.outside), str(self.outside)),
                not_readable,
                None,
            ),
            (
                "--ro-bind of a name that only starts like the toolchain",
                extra("--ro-bind", f"{self.toolchain}-other", f"{self.toolchain}-other"),
                not_readable,
                None,
            ),
            ("no command", [*options, "--"], "no command follows --", None),
            ("relative command", step("lake", "build", "AASSolution"), not_a_tool, None),
            # A relative path that leads into the toolchain's bin is still refused.
            (
                "relative path into bin",
                step(os.path.relpath(lake), "build", "AASSolution"),
                not_a_tool,
                None,
            ),
            ("library file", step(str(self.toolchain / "lib" / "lean" / "tool")), not_a_tool, None),
            ("system shell", step("/bin/sh", "-c", "true"), not_a_tool, None),
            ("lake env x", step(lake, "env", "x"), not_a_step, None),
            (
                "lake build of both",
                step(lake, "build", "AASChallenge", "AASSolution"),
                not_a_step,
                None,
            ),
            ("lake build Other", step(lake, "build", "Other"), not_a_step, None),
            ("lake alone", step(lake), not_a_step, None),
            ("lake resolve-deps", step(lake, "resolve-deps"), not_a_step, None),
            ("leanexport Other", step(leanexport, "Other", "--", "comm"), not_a_step, None),
            ("leanexport without --", step(leanexport, "AASChallenge", "comm"), not_a_step, None),
            ("leanexport alone", step(leanexport), not_a_step, None),
            ("lean", step(self._tool("lean"), "--version"), not_a_step, None),
            ("sh", step(self._tool("sh"), "-c", "true"), not_a_step, None),
        ]
        for option in ("--unshare-all", "--die-with-parent", "--clearenv", "--new-session"):
            cases.append((f"without {option}", without(option), f"missing: {option}", None))
        # A second toolchain whose lake is a directory and whose leanchecker is missing.
        other = self.base / "toolchains" / "other"
        (other / "bin" / "lake").mkdir(parents=True)
        other_config = dict(self.config, toolchain_dir=str(other))
        other_options = [arg.replace(str(self.toolchain), str(other)) for arg in options]
        for label, cmd in (
            ("a directory named lake", [str(other / "bin" / "lake"), "env"]),
            ("a missing leanchecker", [str(other / "bin" / "leanchecker")]),
        ):
            cases.append((label, [*other_options, "--", *cmd], not_a_tool, other_config))
        for label, argv, reason, config in cases:
            with self.subTest(case=label):
                with self.assertRaises(refused) as caught:
                    validate(argv, config or self.config)
                self.assertIn(reason, str(caught.exception))
        # Symlinks, where the host can make them: each is judged by its real path.
        escape = self.run / "escape"
        outside_checker = self.outside / "leanchecker"
        outside_checker.write_bytes(b"leanchecker\n")
        planted = self.toolchain / "bin" / "planted"
        (other / "bin" / "lean").write_bytes(b"lean\n")
        disguised = other / "bin" / "con-ron"
        for label, link, target, argv, reason, config in (
            (
                "--ro-bind of a symlink out",
                escape,
                self.outside,
                extra("--ro-bind", str(escape), str(escape)),
                not_readable,
                self.config,
            ),
            (
                "a bin symlink out",
                planted,
                outside_checker,
                step(str(planted), "--silent"),
                not_a_tool,
                self.config,
            ),
            (
                "a kernel's name on a symlink to lean",
                disguised,
                other / "bin" / "lean",
                [*other_options, "--", str(disguised), str(self.export)],
                not_a_step,
                other_config,
            ),
        ):
            with self.subTest(case=label):
                _symlink_or_skip(self, link, target)
                with self.assertRaises(refused) as caught:
                    validate(argv, config)
                self.assertIn(reason, str(caught.exception))
        with self.subTest(case="--bind of a .lake that is a symlink out"):
            real_lake = self.ws / ".lake-real"
            (self.ws / ".lake").rename(real_lake)
            try:
                _symlink_or_skip(self, self.ws / ".lake", self.outside)
                with self.assertRaises(refused) as caught:
                    validate(list(build), self.config)
                self.assertIn(not_lake, str(caught.exception))
            finally:
                if os.path.lexists(self.ws / ".lake"):
                    (self.ws / ".lake").unlink()
                real_lake.rename(self.ws / ".lake")

    # The wrapper program, run as lake comparator runs it.

    def _install(self, config: dict[str, Any] | None = None) -> Path:
        """The wrapper program and its wrapper.json, an empty phase log, and the fake bwrap."""

        program = self.run / "bwrap-wrapper"
        settings = self.run / "wrapper.json"
        self.log = Path(self.config["log"])
        # A case may have left a symlink in place of one of these.
        for path in (program, settings, self.log):
            if os.path.lexists(path):
                path.unlink()
        program.write_text(gate.COMPARATOR_WRAPPER_SOURCE, encoding="utf-8")
        program.chmod(0o700)
        settings.write_text(json.dumps(config or self.config), encoding="utf-8")
        settings.chmod(0o600)
        self.log.write_bytes(b"")
        self.log.chmod(0o600)
        self.fake = self.base / "fake"
        self.fake.mkdir(exist_ok=True)
        implementation = self.fake / "bwrap_impl.py"
        implementation.write_text(FAKE_BWRAP, encoding="utf-8")
        bwrap = Path(self.config["bwrap"])
        bwrap.parent.mkdir(parents=True, exist_ok=True)
        bwrap.write_text(
            "#!/bin/sh\n"
            f'exec {shlex.quote(sys.executable)} -I {shlex.quote(str(implementation))} "$@"\n',
            encoding="utf-8",
        )
        bwrap.chmod(0o700)
        return program

    def _call(
        self, program: Path, args: list[str], scenario: dict[str, Any] | None = None
    ) -> tuple[subprocess.CompletedProcess[bytes], list[list[str]]]:
        """Run the wrapper with ``args``; return the run and the fake bwrap's calls."""

        (self.fake / "scenario.json").write_text(json.dumps(scenario or {}), encoding="utf-8")
        calls = self.fake / "calls.jsonl"
        if calls.exists():
            calls.unlink()
        completed = subprocess.run(
            [sys.executable, "-I", str(program), *args],
            cwd=self.ws,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=60,
            check=False,
        )
        made = (
            [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
            if calls.exists()
            else []
        )
        # The wrapper never writes to stdout: in the export step, stdout is the export.
        self.assertEqual(completed.stdout, b"")
        return completed, made

    def _logged(self) -> list[dict[str, Any]]:
        entries = gate.parse_phase_log(self.log.read_bytes())
        self.assertIsNotNone(entries)
        return entries

    @unittest.skipUnless(*POSIX_WRAPPER)
    def test_main_runs_each_step_and_logs_it(self) -> None:
        program = self._install()
        resolve = self._upstream_shapes()[0][1]
        separator = resolve.index("--")
        completed, calls = self._call(program, resolve)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        kept = [arg for arg in resolve[:separator] if arg != "--share-net"]
        self.assertEqual(calls, [kept + resolve[separator:]])
        (entry,) = self._logged()
        self.assertGreaterEqual(entry.pop("seconds"), 0.0)
        self.assertEqual(
            entry,
            {"phase": "resolve", "target": "", "rc": 0, "dropped": ["--share-net"], "refused": ""},
        )
        # The step's exit code is the wrapper's; a signal is logged as its
        # negative number, and the wrapper exits with 128 plus the number.
        kernel = self._upstream_shapes()[5][1]
        for scenario, exit_code, rc in (
            ({"rc": 3}, 3, 3),
            ({"signal": int(signal.SIGKILL)}, 128 + int(signal.SIGKILL), -int(signal.SIGKILL)),
        ):
            with self.subTest(scenario=scenario):
                self.log.write_bytes(b"")
                completed, calls = self._call(program, kernel, scenario)
                self.assertEqual(completed.returncode, exit_code, completed.stderr)
                self.assertEqual(len(calls), 1)
                (entry,) = self._logged()
                self.assertEqual(
                    (entry["phase"], entry["target"], entry["rc"], entry["refused"]),
                    ("kernel", "leanchecker", rc, ""),
                )
        # A bwrap that cannot start is exit 127, logged; the child never returns.
        program = self._install(dict(self.config, bwrap=str(self.base / "no-such-bwrap")))
        completed, calls = self._call(program, kernel)
        self.assertEqual((completed.returncode, calls), (127, []), completed.stderr)
        (entry,) = self._logged()
        self.assertEqual((entry["phase"], entry["rc"]), ("kernel", 127))

    @unittest.skipUnless(*POSIX_WRAPPER)
    def test_main_logs_a_refused_step_and_never_runs_it(self) -> None:
        program = self._install()
        build = self._upstream_shapes()[3][1]
        separator = build.index("--")
        widened = [*build[:separator], "--bind", str(self.ws), str(self.ws), *build[separator:]]
        completed, calls = self._call(program, widened)
        self.assertEqual((completed.returncode, calls), (125, []))
        self.assertIn(b"refused", completed.stderr)
        (entry,) = self._logged()
        self.assertEqual(
            (entry["phase"], entry["target"], entry["rc"], entry["seconds"], entry["dropped"]),
            ("refused", "", 125, 0.0, []),
        )
        self.assertIn("is not the workspace's writable .lake", entry["refused"])
        # The logged reason is capped, so one refusal cannot fill the log.
        self.log.write_bytes(b"")
        completed, calls = self._call(program, ["--" + "x" * 1000, *build])
        self.assertEqual((completed.returncode, calls), (125, []))
        (entry,) = self._logged()
        self.assertEqual(len(entry["refused"]), 500)
        self.assertTrue(entry["refused"].startswith("'--xxx"))

    @unittest.skipUnless(*POSIX_WRAPPER)
    def test_main_needs_its_configuration(self) -> None:
        resolve = self._upstream_shapes()[0][1]
        relative = dict(self.config, log="phases.jsonl")
        wrong_type = dict(self.config, run_dir=["/"])
        extra = dict(self.config, store=str(self.base))
        missing = {key: value for key, value in self.config.items() if key != "writable"}
        for label, settings in (
            ("a missing key", json.dumps(missing)),
            ("an extra key", json.dumps(extra)),
            ("a relative path", json.dumps(relative)),
            ("a path that is not a string", json.dumps(wrong_type)),
            ("not JSON", "{"),
            ("a list", json.dumps([self.config])),
        ):
            with self.subTest(case=label):
                program = self._install()
                (self.run / "wrapper.json").write_text(settings, encoding="utf-8")
                completed, calls = self._call(program, resolve)
                self.assertEqual((completed.returncode, calls), (125, []))
                self.assertIn(b"wrapper.json", completed.stderr)
                self.assertEqual(self.log.read_bytes(), b"")
        with self.subTest(case="no wrapper.json"):
            program = self._install()
            (self.run / "wrapper.json").unlink()
            completed, calls = self._call(program, resolve)
            self.assertEqual((completed.returncode, calls), (125, []))
        with self.subTest(case="wrapper.json is a symlink"):
            program = self._install()
            elsewhere = self.outside / "wrapper.json"
            elsewhere.write_text(json.dumps(self.config), encoding="utf-8")
            (self.run / "wrapper.json").unlink()
            _symlink_or_skip(self, self.run / "wrapper.json", elsewhere)
            completed, calls = self._call(program, resolve)
            self.assertEqual((completed.returncode, calls), (125, []))
        with self.subTest(case="called through a symlink"):
            # wrapper.json is found beside the program's real path, not beside the link.
            program = self._install()
            aliases = self.base / "aliases"
            aliases.mkdir()
            alias = aliases / "bwrap-alias"
            _symlink_or_skip(self, alias, program)
            completed, calls = self._call(alias, resolve)
            self.assertEqual((completed.returncode, len(calls)), (0, 1), completed.stderr)

    @unittest.skipUnless(*POSIX_WRAPPER)
    def test_main_keeps_its_log_bounded_and_never_creates_or_follows_it(self) -> None:
        resolve = self._upstream_shapes()[0][1]
        line = self.wrapper["phase_record"]("kernel", "leanchecker", 0, 0.5, [], "")
        with self.subTest(case="64 lines already"):
            program = self._install()
            self.log.write_bytes(line * 64)
            completed, calls = self._call(program, resolve)
            self.assertEqual((completed.returncode, calls), (125, []))
            self.assertEqual(self.log.read_bytes(), line * 64)
        with self.subTest(case="63 lines already"):
            program = self._install()
            self.log.write_bytes(line * 63)
            completed, calls = self._call(program, resolve)
            self.assertEqual((completed.returncode, len(calls)), (0, 1), completed.stderr)
            self.assertEqual(len(self._logged()), 64)
        with self.subTest(case="no log"):
            program = self._install()
            self.log.unlink()
            completed, calls = self._call(program, resolve)
            self.assertEqual((completed.returncode, calls), (125, []))
            self.assertFalse(os.path.lexists(self.log))
        target = self.outside / "elsewhere.jsonl"
        with self.subTest(case="the log is a symlink"):
            program = self._install()
            target.write_bytes(b"")
            self.log.unlink()
            _symlink_or_skip(self, self.log, target)
            completed, calls = self._call(program, resolve)
            self.assertEqual((completed.returncode, calls), (125, []))
            self.assertEqual(target.read_bytes(), b"")
        with self.subTest(case="the log becomes a symlink during the step"):
            program = self._install()
            target.write_bytes(b"")
            completed, calls = self._call(
                program, resolve, {"symlink": [str(self.log), str(target)]}
            )
            self.assertEqual((completed.returncode, len(calls)), (125, 1))
            self.assertEqual(target.read_bytes(), b"")
        with self.subTest(case="the log is removed during the step"):
            program = self._install()
            completed, calls = self._call(program, resolve, {"remove": str(self.log)})
            self.assertEqual((completed.returncode, len(calls)), (125, 1))
            self.assertFalse(os.path.lexists(self.log))


class ComparatorClassificationTests(unittest.TestCase):
    ACCEPTED_STDOUT = (
        "Resolving dependencies\nBuilding AASChallenge\nExporting from AASChallenge\n"
        "Building AASSolution\nExporting from AASSolution\n"
        "Running Lean default kernel on solution\nLean default kernel accepts the solution\n"
        f"{OKAY_LINE}\n"
    )

    def _check(
        self,
        result: tuple[str, str, str, dict[str, str] | None],
        status: str,
        kind: str = "",
        detail: str = "",
        finding: str | None = None,
    ) -> None:
        self.assertEqual(result[:3], (status, kind, detail), result)
        if finding is None:
            self.assertIsNone(result[3])
            return
        self.assertEqual(set(result[3]), {"kind", "detail"})
        self.assertEqual(result[3]["kind"], finding, result)
        self.assertTrue(result[3]["detail"])
        self.assertLessEqual(len(result[3]["detail"]), 500)

    def test_table(self) -> None:
        classify = gate.classify_comparator_run

        def run(
            returncode: int,
            stderr: str = "",
            phases: list[dict[str, Any]] | None = None,
            *,
            stdout: str = "",
            paranoid: bool = False,
        ) -> tuple[Any, ...]:
            return (returncode, stdout, stderr, phases, paranoid)

        def rejected(kind: str, detail: str) -> tuple[str, str, str, None]:
            return ("rejected", kind, detail, None)

        def failed(finding: str, detail: str = "") -> tuple[str, str, str, str]:
            return ("command_failed", "", detail, finding)

        before_kernels = _phases(0, 0, 0, 0, 0)
        # Every message lake comparator's judgment gives, from Lake/Check/Compare.lean
        # and Lake/Check/Axioms.lean, after the steps that come before it.
        for message, kind, paranoid in (
            (
                "Challenge and solution theorem statement do not match: 'comm'",
                "statement_mismatch",
                False,
            ),
            ("Challenge and solution constant kind don't match: 'comm'", "kind_mismatch", False),
            ("Solution constant is not a theorem: 'comm'", "kind_mismatch", False),
            ("Solution constant is not a definition: 'n'", "kind_mismatch", False),
            (
                "Const does not match between challenge and target 'Nat.gcd'",
                "constant_mismatch",
                False,
            ),
            ("Const not found in solution 'Nat.gcd'", "missing_constant", False),
            ("Const not found in solution: 'comm'", "missing_constant", False),
            ("Constant not found in solution 'helper'", "missing_constant", False),
            ("Illegal axiom detected: 'sorryAx'", "forbidden_axiom", False),
            ("Illegal axiom detected: 'helper'", "forbidden_axiom", True),
        ):
            with self.subTest(message=message, paranoid=paranoid):
                arguments = run(
                    1, f"error: {message}\n", before_kernels, stdout="Building\n", paranoid=paranoid
                )
                result = classify(*arguments)
                self._check(result, *rejected(kind, message))
                self.assertIn(result[1], gate.COMPARATOR_REJECTION_KINDS)
        # Messages about the challenge, or about the run, are no verdict.
        for message, phases in (
            ("Const not found in challenge 'Nat.gcd'", before_kernels),
            ("Const not found in challenge: 'comm'", before_kernels),
            ("Challenge constant is not a definition: 'n'", before_kernels),
            ("`lake env` did not report the project's search path", _phases(0)),
        ):
            with self.subTest(message=message):
                result = classify(*run(1, f"error: {message}\n", phases))
                self._check(result, *failed("unclassified_failure", message))
                self.assertIn(message, result[3]["detail"])

        accepted = _phases(0, 0, 0, 0, 0, 0)
        paranoid_accepted = _phases(*[0] * 11, paranoid=True)
        okay = self.ACCEPTED_STDOUT
        sorry = "Illegal axiom detected: 'sorryAx'"
        long_message = "Illegal axiom detected: '" + "x" * 600 + "'"
        bwrap = "bwrap: Can't find source path /nowhere: No such file or directory\n"
        no_git = "`lake comparator` needs `git` on PATH to build inside the sandbox"
        could_not_start = "Error while interacting with Lean default kernel: no such file"
        # str.splitlines() splits at U+2028, and \d matches the Arabic-Indic digit one.
        separated = f"Illegal axiom detected: 'a{chr(0x2028)}b'"
        arabic_one = chr(0x661)
        refused_step = _phase("refused", "", 125, "--bind")
        child_1 = "error: Child exited with 1\n"
        child_134 = "error: Child exited with 134\n"
        inconsistent = failed("inconsistent_run")
        cases: list[tuple[str, tuple[Any, ...], tuple[str, str, str, str | None]]] = [
            ("no phase log", run(0, stdout=okay), failed("phase_log_unreadable")),
            (
                "no phase log after a judgment",
                run(1, f"error: {sorry}\n"),
                failed("phase_log_unreadable", sorry),
            ),
            (
                "a refused step",
                run(1, "error: Child exited with 125\n", [*_phases(0, 0, 0), refused_step]),
                failed("wrapper_refused", "Child exited with 125"),
            ),
            (
                "a refused step in a run that exited 0",
                run(0, "", [*before_kernels, refused_step, *accepted[5:]], stdout=okay),
                failed("wrapper_refused"),
            ),
            ("exit 3", run(3, "error: x\n", accepted), failed("comparator_exit")),
            ("signal 9", run(-9, "", _phases(0, 0, 0)), failed("comparator_exit")),
            (
                "exit 137",
                run(137, f"error: {sorry}\n", _phases(0, 0, 0, 0)),
                failed("comparator_exit"),
            ),
            (
                "exit 2",
                run(2, f"error: {no_git}\n", []),
                failed("comparator_could_not_start", no_git),
            ),
            ("accepted", run(0, "", accepted, stdout=okay), ("accepted", "", "", None)),
            (
                "accepted, paranoid",
                run(0, "", paranoid_accepted, stdout=okay, paranoid=True),
                ("accepted", "", "", None),
            ),
            # With exit 0 no trusted message exists, so a build's "error:" line is no detail.
            (
                "accepted with an error line",
                run(0, f"error: {sorry}\n", accepted, stdout=okay),
                ("accepted", "", "", None),
            ),
            (
                "exit 0 without the okay line",
                run(0, "", accepted, stdout=okay.replace(OKAY_LINE, "Done")),
                inconsistent,
            ),
            (
                "exit 0 with the okay line not last",
                run(0, "", accepted, stdout=okay + "Done\n"),
                inconsistent,
            ),
            (
                "exit 0 without the leanchecker step",
                run(0, "", before_kernels, stdout=okay),
                inconsistent,
            ),
            (
                "exit 0 with an extra step",
                run(0, "", [*accepted, *accepted[5:]], stdout=okay),
                inconsistent,
            ),
            (
                "exit 0 without the bundled kernels",
                run(0, "", accepted, stdout=okay, paranoid=True),
                inconsistent,
            ),
            (
                "exit 0 with unasked bundled kernels",
                run(0, "", paranoid_accepted, stdout=okay),
                inconsistent,
            ),
            (
                "exit 0 after a failed step",
                run(0, "", _phases(0, 0, 0, 0, 0, 1), stdout=okay),
                inconsistent,
            ),
            (
                "a forged okay line with exit 1",
                run(1, f"error: {sorry}\n", before_kernels, stdout=okay),
                rejected("forbidden_axiom", sorry),
            ),
            (
                "a partial line glued to the last one",
                run(1, f"XXerror: {sorry}\n", before_kernels),
                failed("unclassified_failure"),
            ),
            ("no message", run(1, "", before_kernels), failed("unclassified_failure")),
            (
                "blank lines after the message",
                run(1, f"error: {sorry}\n\n  \n", before_kernels),
                rejected("forbidden_axiom", sorry),
            ),
            (
                "trailing white space",
                run(1, f"error: {sorry} \r\n", before_kernels),
                rejected("forbidden_axiom", sorry),
            ),
            (
                "a line separator inside a name",
                run(1, f"error: {separated}\n", before_kernels),
                rejected("forbidden_axiom", separated),
            ),
            (
                "a judgment after the kernels ran",
                run(1, f"error: {sorry}\n", accepted),
                failed("inconsistent_run", sorry),
            ),
            (
                "a judgment before the solution's export",
                run(1, f"error: {sorry}\n", _phases(0, 0, 0, 0)),
                failed("inconsistent_run", sorry),
            ),
            (
                "steps out of order",
                run(1, child_1, [_phase("resolve", ""), _phase("build", "AASSolution", 1)]),
                failed("inconsistent_run", "Child exited with 1"),
            ),
            (
                "build failed",
                run(1, child_1, _phases(0, 0, 0, 1)),
                rejected("build_failed", "Child exited with 1"),
            ),
            (
                "build killed",
                run(1, "error: Child exited with 137\n", _phases(0, 0, 0, 137)),
                failed("solution_build_aborted", "Child exited with 137"),
            ),
            (
                "build failed, not last",
                run(1, child_1, _phases(0, 0, 0, 1, 0)),
                failed("solution_build_aborted", "Child exited with 1"),
            ),
            (
                "build failed with another message",
                run(1, "error: Child exited with 2\n", _phases(0, 0, 0, 1)),
                failed("solution_build_aborted", "Child exited with 2"),
            ),
            (
                "a build exit code that is not the message's",
                run(1, child_1, _phases(0, 0, 0, 2)),
                failed("solution_build_aborted", "Child exited with 1"),
            ),
            (
                "build failed with a glued message",
                run(1, "XX" + child_1, _phases(0, 0, 0, 1)),
                failed("solution_build_aborted"),
            ),
            (
                "build failed with a bwrap error",
                run(1, bwrap + child_1, _phases(0, 0, 0, 1)),
                failed("sandbox_failed", "Child exited with 1"),
            ),
            (
                "build failed with bwrap: inside a line",
                run(1, " bwrap: x\n" + child_1, _phases(0, 0, 0, 1)),
                rejected("build_failed", "Child exited with 1"),
            ),
            (
                "resolve panicked",
                run(1, child_134, _phases(134)),
                failed("resolve_failed", "Child exited with 134"),
            ),
            (
                "challenge build panicked",
                run(1, child_134, _phases(0, 134)),
                failed("challenge_build_failed", "Child exited with 134"),
            ),
            (
                "challenge export panicked",
                run(1, child_134, _phases(0, 0, 134)),
                failed("challenge_export_failed", "Child exited with 134"),
            ),
            (
                "challenge build failed with a bwrap error",
                run(1, bwrap + child_1, _phases(0, 1)),
                failed("challenge_build_failed", "Child exited with 1"),
            ),
            (
                "solution export failed",
                run(1, child_1, _phases(0, 0, 0, 0, 1)),
                failed("solution_export_failed", "Child exited with 1"),
            ),
            (
                "kernel rejected",
                run(1, "error: Lean default exited with 1\n", _phases(0, 0, 0, 0, 0, 1)),
                rejected("kernel_rejected", "Lean default exited with 1"),
            ),
            (
                "kernel rejected, paranoid",
                run(
                    1,
                    "error: lean4lean exited with 1\n",
                    _phases(0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1, paranoid=True),
                    paranoid=True,
                ),
                rejected("kernel_rejected", "lean4lean exited with 1"),
            ),
            (
                "kernel rejected after a bundled kernel panicked",
                run(
                    1,
                    "error: Lean paranoid exited with 134\n",
                    _phases(0, 0, 0, 0, 0, 134, 0, 0, 0, 0, 1, paranoid=True),
                    paranoid=True,
                ),
                rejected("kernel_rejected", "Lean paranoid exited with 134"),
            ),
            (
                "a bundled kernel rejects what Lean's kernel accepts",
                run(
                    1,
                    "error: nanoda exited with 1\n",
                    _phases(0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, paranoid=True),
                    paranoid=True,
                ),
                failed("external_kernel_disagreement", "nanoda exited with 1"),
            ),
            (
                "leanchecker exits 2",
                run(1, "error: Lean default exited with 2\n", _phases(0, 0, 0, 0, 0, 2)),
                failed("kernel_failed", "Lean default exited with 2"),
            ),
            (
                "a kernel name that is not the failing step's",
                run(1, "error: Lean paranoid exited with 1\n", _phases(0, 0, 0, 0, 0, 1)),
                failed("kernel_failed", "Lean paranoid exited with 1"),
            ),
            (
                "an exit code that is not the failing step's",
                run(1, "error: Lean default exited with 2\n", _phases(0, 0, 0, 0, 0, 1)),
                failed("kernel_failed", "Lean default exited with 2"),
            ),
            (
                "an exit code in other digits",
                run(
                    1, f"error: Lean default exited with {arabic_one}\n", _phases(0, 0, 0, 0, 0, 1)
                ),
                failed("kernel_failed", f"Lean default exited with {arabic_one}"),
            ),
            (
                "a kernel that could not start",
                run(1, f"error: {could_not_start}\n", _phases(0, 0, 0, 0, 0, 1)),
                failed("kernel_failed", could_not_start),
            ),
            (
                "kernels missing after a failure",
                run(
                    1,
                    "error: Lean paranoid exited with 1\n",
                    _phases(0, 0, 0, 0, 0, 1, paranoid=True),
                    paranoid=True,
                ),
                failed("inconsistent_run", "Lean paranoid exited with 1"),
            ),
            (
                "kernel rejected with a bwrap error",
                run(1, bwrap + "error: Lean default exited with 1\n", _phases(0, 0, 0, 0, 0, 1)),
                failed("sandbox_failed", "Lean default exited with 1"),
            ),
            # The bwrap rule only takes back the verdict a sandboxed step gave.
            (
                "a judgment with a bwrap line",
                run(1, bwrap + f"error: {sorry}\n", before_kernels),
                rejected("forbidden_axiom", sorry),
            ),
            (
                "a long message",
                run(1, f"error: {long_message}\n", before_kernels),
                rejected("forbidden_axiom", long_message[:500]),
            ),
            # _check also holds every finding's detail to 500 characters.
            (
                "a long unknown message",
                run(1, "error: " + "y" * 700 + "\n", before_kernels),
                failed("unclassified_failure", "y" * 500),
            ),
        ]
        for label, arguments, expected in cases:
            with self.subTest(case=label):
                self._check(classify(*arguments), *expected)
        # The findings say what the steps did.
        refused = classify(*run(1, "", [_phase("refused", "", 125, "--cap-add is not allowed")]))
        self.assertIn("--cap-add is not allowed", refused[3]["detail"])
        disagreement = classify(
            *run(
                1,
                "error: lean4lean exited with 1\n",
                _phases(0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, paranoid=True),
                paranoid=True,
            )
        )
        self.assertIn("lean4lean, con-ron", disagreement[3]["detail"])
        self.assertIn("signal 9", classify(*run(-9, "", []))[3]["detail"])
        self.assertIn("137", classify(*run(137, "", []))[3]["detail"])

    def test_parse_phase_log(self) -> None:
        record = _wrapper_namespace()["phase_record"]
        good = record("build", "AASSolution", 1, 2.5, [], "")
        entry = {
            "phase": "build",
            "target": "AASSolution",
            "rc": 1,
            "seconds": 2.5,
            "dropped": [],
            "refused": "",
        }
        self.assertEqual(gate.parse_phase_log(b""), [])
        self.assertEqual(gate.parse_phase_log(good), [entry])
        # The line cap and the byte cap, at and past their limits.
        line = record("kernel", "leanchecker", 0, 0.5, [], "")
        self.assertEqual(len(gate.parse_phase_log(line * 64)), 64)
        self.assertIsNone(gate.parse_phase_log(line * 65))
        plain = len(record("refused", "", 125, 0.0, [], ""))
        lines = 8
        padding = 64 * 1024 - lines * plain
        share = padding // lines
        sized = [record("refused", "", 125, 0.0, [], "r" * share) for _ in range(lines - 1)]
        sized.append(record("refused", "", 125, 0.0, [], "r" * (padding - (lines - 1) * share)))
        full = b"".join(sized)
        self.assertEqual(len(full), 64 * 1024)
        self.assertEqual(len(gate.parse_phase_log(full)), lines)
        one_more = full.replace(b'"refused":"r', b'"refused":"rr', 1)
        self.assertEqual(len(one_more), 64 * 1024 + 1)
        self.assertIsNone(gate.parse_phase_log(one_more))

        def line_with(**changes: Any) -> bytes:
            value = dict(entry)
            for key, changed in changes.items():
                if changed is _DROP:
                    del value[key]
                else:
                    value[key] = changed
            return (json.dumps(value) + "\n").encode("utf-8")

        bad = {
            "invalid UTF-8": good.replace(b'"refused":""', b'"refused":"\xff"'),
            "not JSON": b"not json\n",
            "deep nesting": b"[" * 60000 + b"\n",
            "an array": b"[]\n",
            "a string": b'"build"\n',
            "null": b"null\n",
            "an extra key": line_with(extra=1),
            "a missing key": line_with(refused=_DROP),
            "an unknown phase": line_with(phase="building"),
            "a phase that is not a string": line_with(phase=1),
            "a phase that is a list": line_with(phase=["build"]),
            "an unknown target": line_with(target="Other"),
            "a target that is not a string": line_with(target=None),
            "a target that is a list": line_with(target=["AASSolution"]),
            "rc as a string": line_with(rc="1"),
            "rc as a float": line_with(rc=1.0),
            "rc as a bool": line_with(rc=True),
            "seconds as an integer": line_with(seconds=2),
            "seconds as a string": line_with(seconds="2.5"),
            "negative seconds": line_with(seconds=-0.5),
            "NaN seconds": good.replace(b"2.5", b"NaN"),
            "infinite seconds": good.replace(b"2.5", b"Infinity"),
            "dropped as a string": line_with(dropped="--share-net"),
            "dropped holding a number": line_with(dropped=[1]),
            "refused as a number": line_with(refused=1),
            "an unterminated last line": good[:-1],
            "a blank line": good + b"\n" + good,
            "a good line, then a bad one": good + line_with(rc=None),
        }
        for label, data in bad.items():
            with self.subTest(case=label):
                self.assertLessEqual(len(data), 64 * 1024)
                self.assertIsNone(gate.parse_phase_log(data))


class ComparatorAttestationTimeoutTests(unittest.TestCase):
    """D13: the attestation's ``lean --version`` runs under one measured constant.

    run_bounded_command is captured, so nothing runs and this holds on every
    platform. The constant's value comes from a cold-start measurement: at
    least three times the slowest cold ``lean --version`` (0.73 s for Lean
    v4.35.0 on the aarch64 host, page cache dropped), rounded up, and never
    below 5 seconds. The value is unchanged from the literal it replaces.
    """

    def test_attestation_timeout_constant(self) -> None:
        self.assertEqual(gate.TOOLCHAIN_ATTESTATION_TIMEOUT_SECONDS, 5)
        with tempfile.TemporaryDirectory() as tmp:
            toolchain = challenge_tests._comparator_toolchain(
                Path(tmp) / "leanprover--lean4---v4.35.0"
            )
            timeouts: list[float] = []

            def answer(command: list[str], **options: Any) -> Any:
                timeouts.append(options["timeout"])
                return gate.BoundedCommandResult(0, BANNER + "\n", "", len(BANNER) + 1)

            def stall(command: list[str], **options: Any) -> Any:
                timeouts.append(options["timeout"])
                raise subprocess.TimeoutExpired(command, options["timeout"])

            # A value of its own, so a call site that kept a literal shows.
            with mock.patch.object(gate, "TOOLCHAIN_ATTESTATION_TIMEOUT_SECONDS", 7):
                with mock.patch.object(gate, "run_bounded_command", side_effect=answer):
                    attested = gate.toolchain_attestation(toolchain)
                self.assertEqual(attested["status"], "ok", attested)
                self.assertEqual(timeouts, [7])
                with mock.patch.object(gate, "run_bounded_command", side_effect=stall):
                    stalled = gate.toolchain_attestation(toolchain)
            self.assertEqual(timeouts, [7, 7])
            self.assertEqual(
                (stalled["status"], stalled["detail"]),
                ("command_failed", "lean --version: timeout after 7 seconds"),
            )


# The judge run on Linux. A fake toolchain stands in for Lean: its lake drives
# the gate's own wrapper program through /usr/bin/env -i, once per scripted
# step, with bwrap arguments shaped like lake comparator's, and its other
# programs exit as the scenario says. A fake bwrap answers the gate's probes,
# logs each step's arguments and runs the step's command.

LINUX_RUN = (
    sys.platform == "linux"
    and os.geteuid() != 0
    and os.access("/usr/bin/python3", os.X_OK)
    and os.access("/usr/bin/env", os.X_OK)
    and os.access("/bin/sh", os.X_OK),
    "a comparator run needs Linux and a user that is not root; its wrapper program "
    "needs /usr/bin/python3 and /usr/bin/env, and the fake tools need /bin/sh",
)
# What /bin/sh may add to the environment of a program it starts: dash adds
# PWD, and bash also SHLVL and _. Environment checks leave these out.
SHELL_VARIABLES = frozenset({"PWD", "SHLVL", "_"})
RECEIPT_KEYS = {
    "schema_version",
    "run_id",
    "created_at",
    "gate",
    "platform",
    "toolchain",
    "sandbox",
    "challenge",
    "solution",
    "run",
    "result",
}
RESULT_KEYS = ("comparator_status", "comparator_rejection_kind", "comparator_detail", "findings")
# The manifest the fake lake resolve-deps writes, as Lake writes one for a
# workspace without dependencies.
FAKE_MANIFEST = (
    '{"version": "1.1.0", "packagesDir": ".lake/packages", "packages": [], '
    '"name": "aas_comparator", "lakeDir": ".lake"}\n'
)
# The five steps before the kernels, as a scenario of the fake lake names them.
PRE_KERNEL_PLAN = [
    {"step": "resolve"},
    {"step": "build", "target": "AASChallenge"},
    {"step": "export", "target": "AASChallenge"},
    {"step": "build", "target": "AASSolution"},
    {"step": "export", "target": "AASSolution"},
]
# The arguments lake comparator v4.35.0 gives each kernel before the export's path.
KERNEL_ARGUMENTS = {
    "leanchecker": ["--silent", "--from-export"],
    "leanchecker-paranoid": ["--silent", "--from-export"],
    "lean4lean": ["--import"],
    "nanoda_bin": [],
    "con-leche": [],
    "con-ron": [],
}
# For each rejection kind: the steps that run, their exit codes and lake
# comparator's final message.
REJECTION_PLANS = {
    "statement_mismatch": (
        PRE_KERNEL_PLAN,
        {},
        "Challenge and solution theorem statement do not match: 'add_zero_right'",
    ),
    "kind_mismatch": (
        PRE_KERNEL_PLAN,
        {},
        "Challenge and solution constant kind don't match: 'add_zero_right'",
    ),
    "constant_mismatch": (
        PRE_KERNEL_PLAN,
        {},
        "Const does not match between challenge and target 'Nat.gcd'",
    ),
    "missing_constant": (PRE_KERNEL_PLAN, {}, "Const not found in solution: 'add_zero_right'"),
    "forbidden_axiom": (PRE_KERNEL_PLAN, {}, "Illegal axiom detected: 'sorryAx'"),
    "kernel_rejected": (
        [*PRE_KERNEL_PLAN, {"step": "kernel", "target": "leanchecker"}],
        {"kernel leanchecker": 1},
        "Lean default exited with 1",
    ),
    "build_failed": (PRE_KERNEL_PLAN[:4], {"build AASSolution": 1}, "Child exited with 1"),
}
# A fake toolchain program: argv[1] names it, and settings.json, scenario.json
# and calls.jsonl lie beside this file, outside the toolchain, so the record's
# pins and the run's stat walk never see them change.
FAKE_JUDGE = r'''"""A fake toolchain program for the comparator run tests; argv[1] names it.

lean --version prints the banner. lake resolve-deps writes a manifest. lake
comparator logs what the gate gave it, runs the scenario's steps through
COMPARATOR_BWRAP under /usr/bin/env -i, applies the scenario's hooks, then
prints the scenario's output and exits with its code. Any other call is one
sandboxed step, which exits with the code the scenario gives it.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as handle:
        return json.load(handle)


SETTINGS = load("settings.json")
PLAN = load("scenario.json")
TOOLCHAIN = SETTINGS["toolchain"]


def record(entry):
    with open(os.path.join(HERE, "calls.jsonl"), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


def umask():
    """The umask this program was started with."""
    current = os.umask(0)
    os.umask(current)
    return current


def environment():
    """The environment this program was started with, before Python added LC_CTYPE (PEP 538)."""
    with open("/proc/self/environ", "rb") as handle:
        items = handle.read().split(b"\0")
    pairs = [item.decode("utf-8", "surrogateescape").partition("=") for item in items if item]
    return {name: value for name, _, value in pairs}


def text(path):
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8", "replace")


def workspace(root):
    """Each entry below root outside its .lake: relative path to [text or None, mode]."""
    entries = {}
    for directory, directories, names in os.walk(root):
        if directory == root:
            directories[:] = [name for name in directories if name != ".lake"]
        for name in directories:
            path = os.path.join(directory, name)
            entries[os.path.relpath(path, root)] = [None, mode(path)]
        for name in names:
            path = os.path.join(directory, name)
            entries[os.path.relpath(path, root)] = [text(path), mode(path)]
    return entries


def bwrap_args(step, project):
    """The wrapper's arguments for one step, in the order lake comparator builds them."""
    kind, target = step["step"], step.get("target", "")
    bin_dir = os.path.join(TOOLCHAIN, "bin")
    path = ":".join([bin_dir, "/usr/bin", "/bin"])
    dot_lake = os.path.join(project, ".lake")
    guarded = ["--tmpfs", "/run", "--tmpfs", "/var"]
    readable = [
        "--ro-bind", project, project, "--ro-bind", TOOLCHAIN, TOOLCHAIN,
        "--ro-bind", TOOLCHAIN, TOOLCHAIN,
    ]
    writable = ["--bind", dot_lake, dot_lake]
    build_env = ["--setenv", "PATH", path, "--setenv", "LEAN_ABORT_ON_PANIC", "1"]
    network = []
    chdir = ["--chdir", project]
    if kind == "resolve":
        middle = readable + writable + build_env
        network = ["--share-net"]
        command = [os.path.join(bin_dir, "lake"), "env"]
    elif kind == "build":
        middle = guarded + readable + writable + build_env
        command = [os.path.join(bin_dir, "lake"), "build", target]
    elif kind == "export":
        exporter = os.path.join(bin_dir, "leanexport")
        middle = guarded + [
            "--ro-bind", project, project, "--ro-bind", dot_lake, dot_lake,
            "--ro-bind", TOOLCHAIN, TOOLCHAIN, "--ro-bind", exporter, exporter,
            "--setenv", "LEAN_ABORT_ON_PANIC", "1", "--setenv", "PATH", path,
        ]
        command = [exporter, target, "--", "add_zero_right", "propext"]
    else:
        kernel = os.path.join(bin_dir, target)
        descriptor, export = tempfile.mkstemp()
        os.close(descriptor)
        middle = guarded + [
            "--ro-bind", export, export, "--ro-bind", kernel, kernel,
            "--ro-bind", TOOLCHAIN, TOOLCHAIN, "--setenv", "LEAN_ABORT_ON_PANIC", "1",
        ]
        chdir = ["--chdir", tempfile.gettempdir()]
        command = [kernel, *SETTINGS["kernel_arguments"][target], export]
    extra = [value.format(ws=project) for value in step.get("extra", [])]
    return (
        SETTINGS["covers"] + middle + extra + SETTINGS["isolation"] + network + chdir
        + ["--", *command]
    )


def apply(hook, places):
    """Change what a hook names, as a solution or another process might during a run."""
    action, argument = hook[0], hook[1].format(**places)
    if action == "append":
        with open(argument, "ab") as handle:
            handle.write(b"\n-- changed during the run\n")
    elif action == "write":
        with open(argument, "w", encoding="utf-8") as handle:
            handle.write("-- planted during the run\n")
    elif action == "touch":
        info = os.lstat(argument)
        os.utime(argument, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000000))
    elif action == "same_size":
        # One byte changes; the size and the times stay, so only a digest sees it.
        info = os.lstat(argument)
        with open(argument, "r+b") as handle:
            data = handle.read()
            handle.seek(0)
            handle.write(data[:-1] + (b"#" if data[-1:] != b"#" else b" "))
        os.utime(argument, ns=(info.st_atime_ns, info.st_mtime_ns))
    elif action == "chmod":
        os.chmod(argument, int(hook[2], 8))
    elif action == "mkdir":
        os.mkdir(argument)
    elif action in ("locked_directory", "read_only_directory"):
        os.mkdir(argument)
        with open(os.path.join(argument, "inside"), "w", encoding="utf-8") as handle:
            handle.write("inside\n")
        os.chmod(argument, 0 if action == "locked_directory" else 0o500)
    elif action == "sleep":
        time.sleep(float(argument))
    elif action == "flood":
        chunk = b"x" * 65536
        for _ in range(int(argument) // len(chunk) + 1):
            sys.stdout.buffer.write(chunk)
        sys.stdout.flush()


def comparator(args):
    project = os.path.realpath(os.getcwd())
    wrapper = os.environ.get("COMPARATOR_BWRAP", "")
    run = os.path.dirname(wrapper)
    config = args[args.index("--config") + 1]
    record({
        "call": "comparator",
        "args": args,
        "cwd": project,
        "env": environment(),
        "umask": umask(),
        "workspace": workspace(project),
        "run": sorted(os.listdir(run)),
        "modes": {name: mode(os.path.join(run, name)) for name in os.listdir(run)},
        "config": text(config),
        "wrapper": text(wrapper),
        "settings": json.loads(text(os.path.join(run, "wrapper.json"))),
        "log": text(os.path.join(run, "phases.jsonl")),
    })
    os.makedirs(os.path.join(project, ".lake"), exist_ok=True)
    places = {"ws": project, "run": run}
    for hook in PLAN.get("before", []):
        apply(hook, places)
    for step in PLAN.get("steps", []):
        completed = subprocess.run(
            ["/usr/bin/env", "-i", wrapper, *bwrap_args(step, project)],
            stdin=subprocess.DEVNULL,
            check=False,
        )
        record({"call": "wrapper", "step": step, "rc": completed.returncode})
    for hook in PLAN.get("after", []):
        apply(hook, places)
    sys.stdout.write(PLAN.get("stdout", ""))
    sys.stdout.flush()
    sys.stderr.write(PLAN.get("stderr", ""))
    sys.stderr.flush()
    return PLAN.get("rc", 0)


def main():
    tool, args = sys.argv[1], sys.argv[2:]
    if tool == "lean" and args == ["--version"]:
        record({"call": "lean", "args": args})
        print(SETTINGS["banner"])
        return 0
    if tool == "lake" and args == ["resolve-deps"]:
        record({
            "call": "resolve-deps",
            "cwd": os.getcwd(),
            "env": environment(),
            "umask": umask(),
            "entries": sorted(os.listdir(".")),
        })
        time.sleep(PLAN.get("resolve_deps_sleep", 0))
        if PLAN.get("manifest", True):
            with open("lake-manifest.json", "w", encoding="utf-8") as handle:
                handle.write(SETTINGS["manifest"])
        if PLAN.get("resolve_deps_rc", 0):
            sys.stderr.write("error: the scenario fails resolve-deps\n")
        return PLAN.get("resolve_deps_rc", 0)
    if tool == "lake" and args[:1] == ["comparator"]:
        return comparator(args[1:])
    if tool == "lake" and args == ["env"]:
        step = "resolve"
    elif tool == "lake" and args[:1] == ["build"]:
        step = " ".join(["build", *args[1:]])
    elif tool == "leanexport":
        step = "export " + args[0]
    else:
        step = "kernel " + tool
    record({"call": "step", "step": step, "args": args})
    return PLAN.get("rcs", {}).get(step, 0)


sys.exit(main())
'''
FAKE_RUN_BWRAP = r'''"""A fake bwrap: it answers --version and the preflight, and logs and runs a step."""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
if args == ["--version"]:
    print("bubblewrap 0.12.0")
    sys.exit(0)
with open(os.path.join(HERE, "scenario.json"), encoding="utf-8") as handle:
    plan = json.load(handle)
if args == ["--ro-bind", "/", "/", "--unshare-all", "--die-with-parent", "--", "/bin/true"]:
    sys.exit(plan.get("bwrap_preflight_rc", 0))
with open(os.path.join(HERE, "calls.jsonl"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"call": "bwrap", "args": args}, sort_keys=True) + "\n")
command = args[args.index("--") + 1 :]
os.execv(command[0], command)
'''
FAKE_HOST_TOOL = r'''"""A fake systemd-run or systemctl (argv[1]) that logs its call and environment.

systemd-run fails as one without a user bus does when the scenario says so,
and otherwise runs the command after --, as systemd-run --scope does.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
tool, args = sys.argv[1], sys.argv[2:]
# The environment this program was started with, before Python added LC_CTYPE (PEP 538).
with open("/proc/self/environ", "rb") as handle:
    pairs = [
        item.decode("utf-8", "surrogateescape").partition("=")
        for item in handle.read().split(b"\0")
        if item
    ]
started_with = {name: value for name, _, value in pairs}
with open(os.path.join(HERE, "calls.jsonl"), "a", encoding="utf-8") as handle:
    handle.write(
        json.dumps({"call": tool, "args": args, "env": started_with}, sort_keys=True) + "\n"
    )
with open(os.path.join(HERE, "scenario.json"), encoding="utf-8") as handle:
    plan = json.load(handle)
if tool == "systemd-run":
    if plan.get("systemd_run_rc", 0):
        sys.stderr.write("Failed to connect to bus: No medium found\n")
        sys.exit(plan["systemd_run_rc"])
    command = args[args.index("--") + 1 :]
    os.execv(command[0], command)
sys.exit(0)
'''


def _sh_tool(path: Path, implementation: Path, *arguments: str) -> Path:
    """A '#!/bin/sh' program that runs ``implementation`` with this interpreter.

    Every path in it is absolute, so it also runs under env -i, without PATH.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    words = [sys.executable, "-I", str(implementation), *arguments]
    path.write_text(
        "#!/bin/sh\nexec " + " ".join(shlex.quote(word) for word in words) + ' "$@"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _judge_toolchain(directory: Path, fake: Path) -> Path:
    """A complete toolchain tree whose programs are fake tools run by ``fake``/judge_tool.py."""

    challenge_tests._comparator_toolchain(directory)
    implementation = fake / "judge_tool.py"
    implementation.write_text(FAKE_JUDGE, encoding="utf-8")
    for name in (*challenge_tests.COMPARATOR_BINARIES, *challenge_tests.BUNDLED_KERNELS):
        _sh_tool(directory / "bin" / name, implementation, name)
    return directory


def _fake_bwrap(path: Path, fake: Path) -> Path:
    implementation = fake / "bwrap_tool.py"
    implementation.write_text(FAKE_RUN_BWRAP, encoding="utf-8")
    return _sh_tool(path, implementation)


def _host_tool(path: Path, fake: Path, name: str) -> Path:
    implementation = fake / "host_tool.py"
    implementation.write_text(FAKE_HOST_TOOL, encoding="utf-8")
    return _sh_tool(path, implementation, name)


@contextlib.contextmanager
def _restored(*paths: Path) -> Any:
    """Give each file back its bytes, mode and times after the block."""

    saved = [(path, path.read_bytes(), os.lstat(path)) for path in paths]
    try:
        yield
    finally:
        for path, data, info in saved:
            path.write_bytes(data)
            path.chmod(stat.S_IMODE(info.st_mode))
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))


def _without_shell(environment: dict[str, str]) -> dict[str, str]:
    return {name: value for name, value in environment.items() if name not in SHELL_VARIABLES}


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def _accepted_plan(*, paranoid: bool = False) -> dict[str, Any]:
    kernels = [*challenge_tests.BUNDLED_KERNELS, "leanchecker"] if paranoid else ["leanchecker"]
    return {
        "steps": [*PRE_KERNEL_PLAN, *({"step": "kernel", "target": kernel} for kernel in kernels)],
        "stdout": ComparatorClassificationTests.ACCEPTED_STDOUT,
        "rc": 0,
    }


def _failed_plan(steps: list[dict[str, Any]], message: str, rcs: dict[str, int]) -> dict[str, Any]:
    return {
        "steps": steps,
        "rcs": rcs,
        "stdout": "Resolving dependencies\n",
        "stderr": f"output of the build\nerror: {message}\n",
        "rc": 1,
    }


@unittest.skipUnless(*LINUX_RUN)
class ComparatorRunTests(unittest.TestCase):
    """The judge run on Linux, with a fake toolchain and a fake bwrap.

    The fake lake drives the wrapper program the gate writes, through
    /usr/bin/env -i, once per scripted step; the fake bwrap logs the step's
    arguments and runs its command, a fake tool that exits as the scenario
    says. --memory-limit is off unless a test says otherwise, and the state
    directory, HOME, the toolchain and the bwrap lie in a temporary directory.
    """

    @classmethod
    def setUpClass(cls) -> None:
        check = subprocess.run(
            ["/usr/bin/python3", "-I", "-c", "import sys; sys.exit(sys.version_info < (3, 9))"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if check.returncode != 0:
            raise unittest.SkipTest("the run's wrapper program needs /usr/bin/python3 3.9 or later")

    def setUp(self) -> None:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        # Real paths, as the run reports them.
        self.base = Path(os.path.realpath(stack.enter_context(tempfile.TemporaryDirectory())))
        self.home = self.base / "home"
        self.home.mkdir()
        self.fake = self.base / "fake"
        self.fake.mkdir()
        self.elan = self.base / "elan"
        self.toolchain = _judge_toolchain(
            self.elan / "toolchains" / "leanprover--lean4---v4.35.0", self.fake
        )
        self.files = challenge_tests._toolchain_files(self.toolchain)
        self.bwrap = _fake_bwrap(self.base / "opt" / "bubblewrap-0.12.0" / "bin" / "bwrap", self.fake)
        self.input = _tree(self.base / "input", {"Solution.lean": SOLUTION})
        self.record = _core_record(toolchain={"spec": DEFAULT_TOOLCHAIN, "files": dict(self.files)})
        self.frozen = self.base / "frozen"
        self.record_path, self.record_sha = _write_record(self.frozen, self.record)
        self.state = self.base / "state"
        self.comparator_state = self.state / "ai-agents-skills" / "comparator"
        (self.fake / "settings.json").write_text(
            json.dumps(
                {
                    "toolchain": str(self.toolchain),
                    "banner": BANNER,
                    "manifest": FAKE_MANIFEST,
                    "covers": BWRAP_COVERS,
                    "isolation": BWRAP_ISOLATION,
                    "kernel_arguments": KERNEL_ARGUMENTS,
                }
            ),
            encoding="utf-8",
        )
        self._plan(_accepted_plan())
        stack.enter_context(
            challenge_tests._readiness_environment(
                self.home,
                ELAN_HOME=str(self.elan),
                XDG_STATE_HOME=str(self.state),
                AAS_BWRAP=str(self.bwrap),
            )
        )

    def _plan(self, plan: dict[str, Any]) -> None:
        (self.fake / "scenario.json").write_text(json.dumps(plan), encoding="utf-8")

    def _judge(
        self, *, floor: int | None = 1, input_path: Path | None = None, **overrides: Any
    ) -> dict[str, Any]:
        """One comparator_payload run. The disk floor is patched to ``floor`` unless it is None."""

        arguments: dict[str, Any] = {
            "challenge_record": self.record_path,
            "challenge_record_sha256": self.record_sha,
            "solution_modules": ["Solution"],
            "timeout": 60,
            "paranoid": False,
            "keep_workspace": False,
            "memory_limit": "off",
        }
        arguments.update(overrides)
        # Emptied, not removed: the fakes append to it under any umask.
        (self.fake / "calls.jsonl").write_bytes(b"")
        with contextlib.ExitStack() as stack:
            if floor is not None:
                stack.enter_context(mock.patch.object(gate, "COMPARATOR_MIN_FREE_BYTES", floor))
            return gate.comparator_payload(input_path or self.input, **arguments)

    def _main(self) -> tuple[Any, dict[str, Any]]:
        """The verb through gate.main, with emit captured: (exit code, payload)."""

        emitted: list[dict[str, Any]] = []
        (self.fake / "calls.jsonl").write_bytes(b"")
        argv = [
            "comparator",
            "--input",
            str(self.input),
            "--challenge-record",
            str(self.record_path),
            "--challenge-record-sha256",
            self.record_sha,
            "--solution-module",
            "Solution",
            "--memory-limit",
            "off",
        ]
        with mock.patch.object(gate, "emit", side_effect=emitted.append), mock.patch.object(
            gate, "COMPARATOR_MIN_FREE_BYTES", 1
        ):
            code = gate.main(argv)
        self.assertEqual(len(emitted), 1)
        return code, emitted[0]

    def _calls(self, call: str) -> list[dict[str, Any]]:
        path = self.fake / "calls.jsonl"
        if not path.exists():
            return []
        entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return [entry for entry in entries if entry["call"] == call]

    def _runs(self) -> list[str]:
        runs = self.comparator_state / "runs"
        return sorted(os.listdir(runs)) if runs.exists() else []

    def _receipts(self) -> list[str]:
        receipts = self.comparator_state / "receipts"
        return sorted(os.listdir(receipts)) if receipts.exists() else []

    def _run_path(self, payload: dict[str, Any]) -> Path:
        return self.comparator_state / "runs" / payload["run"]["run_id"]

    def _receipt(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The run's receipt, checked against the payload: path, digest, mode and form."""

        path = Path(payload["receipt"]["path"])
        self.assertEqual(
            path, self.comparator_state / "receipts" / (payload["run"]["run_id"] + ".json")
        )
        data = path.read_bytes()
        self.assertEqual(payload["receipt"]["sha256"], _sha256(data))
        self.assertEqual(_mode(path), 0o600)
        receipt = json.loads(data.decode("utf-8"))
        self.assertEqual(
            data,
            (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
        )
        self.assertEqual(set(receipt), RECEIPT_KEYS)
        self.assertEqual(receipt["schema_version"], "host_comparator_receipt.v1")
        self.assertEqual(receipt["run_id"], payload["run"]["run_id"])
        return receipt

    def _assert_no_verdict(self, payload: dict[str, Any], kind: str, detail: str = "") -> None:
        self.assertEqual(
            (payload["comparator_status"], payload["comparator_rejection_kind"], payload["ok"]),
            ("command_failed", "", False),
            payload,
        )
        self.assertEqual([item["kind"] for item in payload["findings"]], [kind], payload)
        self.assertIn(detail, payload["findings"][0]["detail"])

    def _assert_guard(self, payload: dict[str, Any], status: str, kind: str, detail: str) -> None:
        """A host guard's refusal: no run directory, no lake, no receipt."""

        self.assertEqual((payload["comparator_status"], payload["ok"]), (status, False), payload)
        self.assertEqual([item["kind"] for item in payload["findings"]], [kind], payload)
        self.assertIn(detail, payload["findings"][0]["detail"])
        self.assertEqual(payload["run"]["run_id"], "")
        self.assertEqual(payload["receipt"], {"path": "", "sha256": ""})
        self.assertEqual(self._runs(), [])
        self.assertEqual(self._receipts(), [])
        self.assertEqual(self._calls("resolve-deps") + self._calls("comparator"), [])

    def test_accepted_and_each_rejection(self) -> None:
        code, payload = self._main()
        self.assertEqual(code, 0, payload)
        self.assertEqual(
            (
                payload["comparator_status"],
                payload["comparator_rejection_kind"],
                payload["comparator_detail"],
                payload["ok"],
                payload["findings"],
            ),
            ("accepted", "", "", True, []),
            payload,
        )
        run = payload["run"]
        self.assertEqual(
            [(step["phase"], step["target"], step["rc"]) for step in run["phases"]],
            [(phase, target, 0) for phase, target in [*STEPS_BEFORE_KERNELS, ("kernel", "leanchecker")]],
        )
        self.assertRegex(run["run_id"], r"\A[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\Z")
        self.assertEqual((run["returncode"], run["timeout_seconds"], run["workspace_kept"]), (0, 300, ""))
        self.assertIsInstance(run["seconds"], float)
        self.assertTrue(run["stdout_tail"].endswith(OKAY_LINE + "\n"), run["stdout_tail"])
        self.assertEqual(payload["solution"]["post_digest"], payload["solution"]["digest"])
        self.assertEqual(
            payload["sandbox"]["wrapper_sha256"],
            _sha256(b"#!/usr/bin/python3 -I\n" + gate.COMPARATOR_WRAPPER_SOURCE.encode("utf-8")),
        )
        self.assertEqual(payload["limitations"], [*LIMITATIONS, NO_MEMORY_LIMIT])
        # Nothing of the run is left but its receipt.
        self.assertEqual(self._runs(), [])
        receipt = self._receipt(payload)
        self.assertRegex(
            receipt["created_at"], r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
        )
        gate_path = Path(os.path.realpath(scanner.GATE_PY))
        self.assertEqual(
            receipt["gate"], {"path": str(gate_path), "sha256": _sha256(gate_path.read_bytes())}
        )
        self.assertEqual(
            receipt["platform"], {"system": os.uname().sysname, "machine": os.uname().machine}
        )
        for section in ("toolchain", "sandbox", "challenge"):
            self.assertEqual(receipt[section], payload[section], section)
        self.assertEqual(receipt["solution"], {"input": str(self.input), **payload["solution"]})
        self.assertEqual(
            receipt["run"],
            {key: run[key] for key in ("argv", "returncode", "seconds", "timeout_seconds", "phases")},
        )
        self.assertEqual(receipt["result"], {key: payload[key] for key in RESULT_KEYS})

        for kind, (steps, rcs, message) in REJECTION_PLANS.items():
            with self.subTest(kind=kind):
                self._plan(_failed_plan(steps, message, rcs))
                code, payload = self._main()
                self.assertEqual(code, 1)
                self.assertEqual(
                    (
                        payload["comparator_status"],
                        payload["comparator_rejection_kind"],
                        payload["comparator_detail"],
                        payload["ok"],
                        payload["findings"],
                    ),
                    ("rejected", kind, message, False, []),
                    payload,
                )
                self.assertEqual(payload["run"]["returncode"], 1)
                receipt = self._receipt(payload)
                self.assertEqual(receipt["result"], {key: payload[key] for key in RESULT_KEYS})
                self.assertEqual(self._runs(), [])

        with self.subTest(case="paranoid"):
            # The run is paranoid by the flag: the bundled kernels run before leanchecker.
            self._plan(_accepted_plan(paranoid=True))
            payload = self._judge(paranoid=True)
            self.assertEqual(payload["comparator_status"], "accepted", payload)
            self.assertEqual(payload["run"]["argv"][-1], "--paranoid")
            self.assertEqual(
                [step["target"] for step in payload["run"]["phases"][5:]],
                [*challenge_tests.BUNDLED_KERNELS, "leanchecker"],
            )

    def test_workspace_and_run_files(self) -> None:
        files = {
            "Sol/Main.lean": "import Sol.Lemmas\n"
            "theorem add_zero_right (n : Nat) : n + 0 = n := Sol.helper n\n",
            "Sol/Lemmas.lean": "namespace Sol\n"
            "theorem helper (n : Nat) : n + 0 = n := by simp\nend Sol\n",
            "Unused.lean": "theorem unused : True := trivial\n",
        }
        two = _tree(self.base / "two", files)
        payload = self._judge(input_path=two, solution_modules=["Sol.Main"])
        self.assertEqual(payload["comparator_status"], "accepted", payload)
        run = self._run_path(payload)
        ws = run / "ws"
        # lake resolve-deps runs on the generated files alone, before any solution byte.
        (resolve,) = self._calls("resolve-deps")
        self.assertEqual(resolve["cwd"], str(ws))
        self.assertEqual(resolve["entries"], ["lakefile.toml", "lean-toolchain"])
        (judged,) = self._calls("comparator")
        self.assertEqual(judged["cwd"], str(ws))
        self.assertEqual(judged["args"], ["--config", str(run / "comparator.json")])
        manifest_mode = judged["workspace"]["lake-manifest.json"][1]
        self.assertEqual(
            judged["workspace"],
            {
                "AASChallenge.lean": [CHALLENGE_BYTES.decode("utf-8"), 0o600],
                "AASSolution.lean": ["import Sol.Main\n", 0o600],
                "Sol": [None, 0o700],
                "Sol/Lemmas.lean": [files["Sol/Lemmas.lean"], 0o600],
                "Sol/Main.lean": [files["Sol/Main.lean"], 0o600],
                "lake-manifest.json": [FAKE_MANIFEST, manifest_mode],
                "lakefile.toml": [gate.comparator_lakefile(["Sol.Lemmas", "Sol.Main"]), 0o600],
                "lean-toolchain": [DEFAULT_TOOLCHAIN + "\n", 0o600],
            },
        )
        self.assertEqual(
            judged["modes"],
            {
                "bwrap-wrapper": 0o700,
                "comparator.json": 0o600,
                "home": 0o700,
                "phases.jsonl": 0o600,
                "tmp": 0o700,
                "wrapper.json": 0o600,
                "ws": 0o700,
            },
        )
        self.assertEqual(judged["run"], sorted(judged["modes"]))
        self.assertEqual(judged["config"], gate.comparator_config(self.record).decode("ascii"))
        self.assertEqual(judged["wrapper"], "#!/usr/bin/python3 -I\n" + gate.COMPARATOR_WRAPPER_SOURCE)
        self.assertEqual(judged["log"], "")
        self.assertEqual(
            judged["settings"],
            {
                "bwrap": str(self.bwrap),
                "log": str(run / "phases.jsonl"),
                "toolchain_dir": str(self.toolchain),
                "run_dir": str(run),
                "writable": str(ws / ".lake"),
            },
        )
        self.assertEqual(
            payload["solution"]["files"],
            {name: _sha256(files[name].encode("utf-8")) for name in ("Sol/Main.lean", "Sol/Lemmas.lean")},
        )
        self.assertEqual(_mode(self.comparator_state / "comparator.lock"), 0o600)
        self.assertEqual(self._runs(), [])

        # The modes do not depend on the umask, even one that strips the owner's bits.
        previous = os.umask(0o277)
        try:
            payload = self._judge(input_path=two, solution_modules=["Sol.Main"])
        finally:
            os.umask(previous)
        self.assertEqual(payload["comparator_status"], "accepted", payload)
        (judged,) = self._calls("comparator")
        self.assertEqual(
            {name: entry[1] for name, entry in judged["workspace"].items() if name != "lake-manifest.json"},
            {
                "AASChallenge.lean": 0o600,
                "AASSolution.lean": 0o600,
                "Sol": 0o700,
                "Sol/Lemmas.lean": 0o600,
                "Sol/Main.lean": 0o600,
                "lakefile.toml": 0o600,
                "lean-toolchain": 0o600,
            },
        )
        self.assertEqual(
            judged["modes"],
            {
                "bwrap-wrapper": 0o700,
                "comparator.json": 0o600,
                "home": 0o700,
                "phases.jsonl": 0o600,
                "tmp": 0o700,
                "wrapper.json": 0o600,
                "ws": 0o700,
            },
        )
        self._receipt(payload)
        self.assertEqual(self._runs(), [])

    def test_processes_get_a_private_umask(self) -> None:
        """lake resolve-deps and the judge start with umask 077, whatever the gate's umask is.

        Lake makes the workspace's .lake/build under the umask it inherits.
        Under the gate's umask 0277, Lean v4.35.0 could not write there, and
        every run ended without a verdict (challenge_build_failed). The gate's
        own umask is left as it was.
        """

        self.assertEqual(gate.COMPARATOR_UMASK, 0o077)
        # Made under this umask, so the fakes can append to it under any other.
        (self.fake / "calls.jsonl").write_bytes(b"")
        for gate_umask in (0o277, 0o022, 0o000):
            with self.subTest(umask=f"{gate_umask:04o}"):
                previous = os.umask(gate_umask)
                try:
                    payload = self._judge()
                    after = os.umask(gate_umask)
                finally:
                    os.umask(previous)
                self.assertEqual(after, gate_umask)
                self.assertEqual(payload["comparator_status"], "accepted", payload)
                (resolve,) = self._calls("resolve-deps")
                (judged,) = self._calls("comparator")
                self.assertEqual((resolve["umask"], judged["umask"]), (0o077, 0o077))

    def test_state_directory_rules(self) -> None:
        # A parent that exists keeps its mode; the directories the run owns are 0700.
        self.state.mkdir()
        self.state.chmod(0o755)
        own = (
            self.comparator_state,
            self.comparator_state / "receipts",
            self.comparator_state / "runs",
        )
        payload = self._judge()
        self.assertEqual(payload["comparator_status"], "accepted", payload)
        self.assertEqual(_mode(self.state), 0o755)
        for directory in (self.state / "ai-agents-skills", *own):
            self.assertEqual(_mode(directory), 0o700, directory)
        # They become 0700 again on each run.
        for directory in own:
            directory.chmod(0o755)
        payload = self._judge()
        self.assertEqual(payload["comparator_status"], "accepted", payload)
        for directory in own:
            self.assertEqual(_mode(directory), 0o700, directory)

        # The lock file is kept 0600, and a symlink in its place is refused.
        lock = self.comparator_state / "comparator.lock"
        lock.chmod(0o644)
        payload = self._judge()
        self.assertEqual(payload["comparator_status"], "accepted", payload)
        self.assertEqual(_mode(lock), 0o600)
        target = self.base / "elsewhere-lock"
        target.write_text("not a lock\n", encoding="utf-8")
        target.chmod(0o644)
        lock.unlink()
        lock.symlink_to(target)
        payload = self._judge()
        self._assert_no_verdict(payload, "comparator_failed")
        self.assertEqual(self._calls("resolve-deps"), [])
        self.assertEqual((target.read_text(encoding="utf-8"), _mode(target)), ("not a lock\n", 0o644))
        lock.unlink()

        # The disk floor is a floor: exactly that much free space is enough.
        floor = gate.COMPARATOR_MIN_FREE_BYTES
        for free, status in ((floor, "accepted"), (floor - 1, "tool_unavailable")):
            with self.subTest(free=free), mock.patch.object(
                gate.shutil, "disk_usage", return_value=mock.Mock(free=free)
            ):
                payload = self._judge(floor=None)
                self.assertEqual(payload["comparator_status"], status, payload)

        # A state directory reached through a symlinked parent: the run uses real paths.
        linked = self.base / "linked-state"
        linked.symlink_to(self.state, target_is_directory=True)
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(linked)}):
            payload = self._judge(keep_workspace=True)
        self.assertEqual(payload["comparator_status"], "accepted", payload)
        self.assertEqual(payload["run"]["workspace_kept"], str(self._run_path(payload)))
        self.assertTrue(payload["receipt"]["path"].startswith(str(self.comparator_state) + os.sep))
        gate.shutil.rmtree(self._run_path(payload))

        # A symlink in place of the state directory is refused; nothing is made where it points.
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        moved = self.base / "moved-state"
        self.comparator_state.rename(moved)
        self.comparator_state.symlink_to(elsewhere, target_is_directory=True)
        payload = self._judge()
        self._assert_no_verdict(payload, "comparator_failed", "symlink")
        self.assertEqual(os.listdir(elsewhere), [])
        self.assertEqual(self._calls("resolve-deps"), [])
        self.comparator_state.unlink()
        moved.rename(self.comparator_state)
        # So is a receipts/ that is not a directory.
        receipts = self.comparator_state / "receipts"
        for name in os.listdir(receipts):
            (receipts / name).unlink()
        receipts.rmdir()
        receipts.write_text("not a directory\n", encoding="utf-8")
        payload = self._judge()
        self._assert_no_verdict(payload, "comparator_failed", "receipts")
        self.assertEqual(self._calls("resolve-deps"), [])

    def test_no_verdict_runs(self) -> None:
        refused_bind = [
            PRE_KERNEL_PLAN[0],
            {"step": "build", "target": "AASChallenge", "extra": ["--bind", "{ws}", "{ws}"]},
        ]
        for label, plan, kind in (
            (
                "the challenge does not build",
                _failed_plan(PRE_KERNEL_PLAN[:2], "Child exited with 1", {"build AASChallenge": 1}),
                "challenge_build_failed",
            ),
            (
                "the solution's build is killed",
                _failed_plan(PRE_KERNEL_PLAN[:4], "Child exited with 137", {"build AASSolution": 137}),
                "solution_build_aborted",
            ),
            (
                "a step binds the workspace writable",
                _failed_plan(refused_bind, "Child exited with 125", {}),
                "wrapper_refused",
            ),
        ):
            with self.subTest(case=label):
                self._plan(plan)
                payload = self._judge()
                self._assert_no_verdict(payload, kind)
                receipt = self._receipt(payload)
                self.assertEqual(receipt["result"], {key: payload[key] for key in RESULT_KEYS})
                self.assertEqual(self._runs(), [])
        # The refused step never ran: the bwrap ran the resolve step alone.
        self.assertEqual(len(self._calls("bwrap")), 1)

        receipts = self._receipts()
        for label, change, detail in (
            ("resolve-deps fails", {"resolve_deps_rc": 1}, "exited 1"),
            ("resolve-deps writes no manifest", {"manifest": False}, "lake-manifest.json"),
            ("resolve-deps hangs", {"resolve_deps_sleep": 30}, "timeout after 1 seconds"),
        ):
            with self.subTest(case=label), mock.patch.object(gate, "COMPARATOR_RESOLVE_TIMEOUT", 1):
                self._plan({**_accepted_plan(), **change})
                started = time.monotonic()
                payload = self._judge()
                self.assertLess(time.monotonic() - started, 20)
                self._assert_no_verdict(payload, "workspace_setup_failed", detail)
                self.assertNotEqual(payload["run"]["run_id"], "")
                self.assertEqual(payload["receipt"], {"path": "", "sha256": ""})
                self.assertEqual(self._receipts(), receipts)
                self.assertEqual(self._calls("comparator"), [])
                self.assertEqual(self._runs(), [])

    def test_integrity(self) -> None:
        solution = self.input / "Solution.lean"
        stat_only = self.toolchain / "lib" / "lean" / "Init.olean"
        pinned = self.toolchain / "bin" / "leanchecker"
        challenge = self.frozen / "Challenge.lean"
        for label, restore, hook, kind, detail in (
            ("an input file", solution, ["append", str(solution)], "input", "Solution.lean"),
            ("the workspace", None, ["write", "{ws}/Planted.lean"], "workspace", "outside .lake"),
            ("a toolchain entry", stat_only, ["touch", str(stat_only)], "toolchain", "stat walk"),
            ("a pinned toolchain file", pinned, ["same_size", str(pinned)], "toolchain", "pinned"),
            ("the wrapper", None, ["append", "{run}/bwrap-wrapper"], "sandbox", "wrapper program"),
            ("the bwrap", self.bwrap, ["append", str(self.bwrap)], "sandbox", "bwrap binary"),
            ("the record", self.record_path, ["append", str(self.record_path)], "challenge", "record"),
            ("Challenge.lean", challenge, ["append", str(challenge)], "challenge", "Challenge.lean"),
        ):
            finding = f"{kind}_changed_during_verification"
            with self.subTest(change=label), _restored(*([restore] if restore else [])):
                self._plan({**_accepted_plan(), "after": [hook]})
                payload = self._judge()
                self._assert_no_verdict(payload, finding, detail)
                # Each check names only what it saw change.
                if label == "a toolchain entry":
                    self.assertNotIn("pinned", payload["findings"][0]["detail"])
                if label == "a pinned toolchain file":
                    self.assertNotIn("stat walk", payload["findings"][0]["detail"])
                if kind == "input":
                    self.assertNotEqual(
                        payload["solution"]["post_digest"], payload["solution"]["digest"]
                    )
                else:
                    self.assertEqual(
                        payload["solution"]["post_digest"], payload["solution"]["digest"]
                    )
                receipt = self._receipt(payload)
                self.assertEqual(receipt["result"], {key: payload[key] for key in RESULT_KEYS})
                self.assertEqual(self._runs(), [])

        # A new directory in the toolchain shows in the stat walk.
        planted = self.toolchain / "planted"
        self._plan({**_accepted_plan(), "after": [["mkdir", str(planted)]]})
        try:
            payload = self._judge()
        finally:
            if os.path.lexists(planted):
                planted.rmdir()
        self._assert_no_verdict(payload, "toolchain_changed_during_verification", "stat walk")

        # A change after a rejection takes the rejection back.
        steps, rcs, message = REJECTION_PLANS["statement_mismatch"]
        with _restored(solution):
            self._plan({**_failed_plan(steps, message, rcs), "after": [["append", str(solution)]]})
            payload = self._judge()
        self._assert_no_verdict(payload, "input_changed_during_verification")
        self.assertEqual(payload["comparator_detail"], message)

    def test_limits_and_host_guards(self) -> None:
        import fcntl  # Linux only, as this class is

        host = self.base / "host"
        systemd_run = _host_tool(host / "systemd-run", self.fake, "systemd-run")
        systemctl = _host_tool(host / "systemctl", self.fake, "systemctl")
        lock = self.comparator_state / "comparator.lock"
        bus = {
            "XDG_RUNTIME_DIR": "/run/user/4242",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/4242/bus",
        }

        # The disk floor comes before the memory preflight, and both before the lock.
        with mock.patch.object(gate, "COMPARATOR_SYSTEMD_RUN", str(systemd_run)):
            payload = self._judge(floor=2**62, memory_limit="512M")
        self._assert_guard(payload, "tool_unavailable", "insufficient_disk", "bytes free")
        self.assertEqual(self._calls("systemd-run"), [])
        self.assertFalse(os.path.lexists(lock))

        self._plan({**_accepted_plan(), "systemd_run_rc": 1})
        with mock.patch.object(gate, "COMPARATOR_SYSTEMD_RUN", str(systemd_run)), mock.patch.dict(
            os.environ, bus
        ):
            payload = self._judge(memory_limit="512M")
        self._assert_guard(
            payload, "tool_unavailable", "memory_limit_unavailable", "Failed to connect to bus"
        )
        (preflight,) = self._calls("systemd-run")
        self.assertEqual(
            preflight["args"],
            [
                "--user",
                "--scope",
                "--quiet",
                "--collect",
                "-p",
                "MemoryMax=512M",
                "-p",
                "MemorySwapMax=0",
                "--",
                "/bin/true",
            ],
        )
        self.assertEqual(_without_shell(preflight["env"]), {"PATH": "/usr/bin:/bin", **bus})
        self.assertFalse(os.path.lexists(lock))
        self._plan(_accepted_plan())

        not_executable = host / "python3-not-executable"
        not_executable.write_text("#!/bin/sh\n", encoding="utf-8")
        not_executable.chmod(0o644)
        for interpreter in (host / "missing" / "python3", not_executable):
            with self.subTest(interpreter=interpreter.name), mock.patch.object(
                gate, "COMPARATOR_WRAPPER_INTERPRETER", str(interpreter)
            ):
                payload = self._judge()
                self._assert_guard(
                    payload, "tool_unavailable", "wrapper_interpreter_unavailable", str(interpreter)
                )
                self.assertFalse(os.path.lexists(lock))

        # Another run holds the lock: this one waits for its --timeout, then gives up.
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            started = time.monotonic()
            payload = self._judge(timeout=1)
            waited = time.monotonic() - started
        finally:
            os.close(descriptor)
        self._assert_guard(payload, "command_failed", "host_busy", "waited 1 s")
        self.assertGreaterEqual(waited, 1.0)
        self.assertLess(waited, 30)
        # The wait is the timeout, and never more than COMPARATOR_LOCK_MAX_WAIT.
        descriptor = os.open(lock, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            started = time.monotonic()
            with mock.patch.object(gate, "COMPARATOR_LOCK_MAX_WAIT", 1):
                payload = self._judge(timeout=8)
            waited = time.monotonic() - started
        finally:
            os.close(descriptor)
        self._assert_guard(payload, "command_failed", "host_busy", "waited 1 s")
        self.assertGreaterEqual(waited, 1.0)
        self.assertLess(waited, 6)

        # Once the judge has started, a run without a verdict still gets a receipt (D10).
        self._plan({**_accepted_plan(), "before": [["sleep", "30"]]})
        with mock.patch.object(gate, "COMPARATOR_SYSTEMCTL", str(systemctl)), mock.patch.object(
            gate, "COMPARATOR_SYSTEMD_RUN", str(systemd_run)
        ):
            started = time.monotonic()
            payload = self._judge(timeout=2)
            self.assertLess(time.monotonic() - started, 25)
        self._assert_no_verdict(payload, "timeout", "2 seconds")
        self.assertIsNone(payload["run"]["returncode"])
        self._receipt(payload)
        self.assertEqual(self._runs(), [])
        # Without a memory limit there is no scope: no preflight, and none to stop.
        self.assertEqual(self._calls("systemd-run") + self._calls("systemctl"), [])

        self._plan({**_accepted_plan(), "before": [["flood", str(17 * 1024 * 1024)]]})
        payload = self._judge()
        self._assert_no_verdict(payload, "output_limit")
        self.assertIsNone(payload["run"]["returncode"])
        self._receipt(payload)
        self.assertEqual(self._runs(), [])

    def test_memory_limit_scope(self) -> None:
        """--memory-limit puts the judge in a user scope of its own, here through a fake systemd-run."""

        host = self.base / "host"
        systemd_run = _host_tool(host / "systemd-run", self.fake, "systemd-run")
        systemctl = _host_tool(host / "systemctl", self.fake, "systemctl")
        bus = {
            "XDG_RUNTIME_DIR": "/run/user/4242",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/4242/bus",
        }
        outer = {"PATH": "/usr/bin:/bin", **bus}
        with mock.patch.object(gate, "COMPARATOR_SYSTEMD_RUN", str(systemd_run)), mock.patch.object(
            gate, "COMPARATOR_SYSTEMCTL", str(systemctl)
        ), mock.patch.dict(os.environ, bus):
            payload = self._judge(memory_limit="512M")
            self.assertEqual(payload["comparator_status"], "accepted", payload)
            unit = "aas-comparator-" + payload["run"]["run_id"]
            prefix = [
                str(systemd_run),
                "--user",
                "--scope",
                "--quiet",
                "--collect",
                f"--unit={unit}",
                "-p",
                "MemoryMax=512M",
                "-p",
                "MemorySwapMax=0",
                "--",
                "/usr/bin/nice",
                "-n",
                "19",
                "/usr/bin/env",
                "-i",
            ]
            self.assertEqual(payload["run"]["argv"][: len(prefix)], prefix)
            preflight, judge = self._calls("systemd-run")
            self.assertEqual(preflight["args"][-2:], ["--", "/bin/true"])
            self.assertEqual(judge["args"], payload["run"]["argv"][1:])
            for call in (preflight, judge):
                self.assertEqual(_without_shell(call["env"]), outer)
            self.assertEqual(payload["sandbox"]["memory_limit"], "512M")
            self.assertEqual(payload["limitations"], LIMITATIONS)
            self.assertEqual(self._calls("systemctl"), [])

            # A judge that runs out of time has its scope stopped as well.
            self._plan({**_accepted_plan(), "before": [["sleep", "30"]]})
            payload = self._judge(memory_limit="512M", timeout=2)
            self._assert_no_verdict(payload, "timeout")
            (kill,) = self._calls("systemctl")
            self.assertEqual(
                kill["args"],
                [
                    "--user",
                    "kill",
                    "--signal=KILL",
                    "aas-comparator-" + payload["run"]["run_id"] + ".scope",
                ],
            )
            self.assertEqual(_without_shell(kill["env"]), outer)

    def test_environment_wrapper_and_lifecycle(self) -> None:
        leaks = {
            "ELAN_HOME": str(self.elan),
            "LEAN_PATH": "/nonexistent/lean-path",
            "LAKE_OVERRIDE_LEAN": "/nonexistent/lean",
            "LD_LIBRARY_PATH": "/nonexistent/lib",
            "XDG_RUNTIME_DIR": "/run/user/4242",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/4242/bus",
        }
        with mock.patch.dict(os.environ, leaks):
            payload = self._judge()
        self.assertEqual(payload["comparator_status"], "accepted", payload)
        run = self._run_path(payload)
        bin_dir = self.toolchain / "bin"
        path = f"{bin_dir}:/usr/bin:/bin"
        (judged,) = self._calls("comparator")
        self.assertEqual(
            _without_shell(judged["env"]),
            {
                "PATH": path,
                "HOME": str(run / "home"),
                "TMPDIR": str(run / "tmp"),
                "LANG": "C.UTF-8",
                "COMPARATOR_BWRAP": str(run / "bwrap-wrapper"),
            },
        )
        (resolve,) = self._calls("resolve-deps")
        self.assertEqual(
            _without_shell(resolve["env"]),
            {"PATH": path, "HOME": str(run / "home"), "TMPDIR": str(run / "tmp"), "LANG": "C.UTF-8"},
        )
        self.assertEqual(
            payload["run"]["argv"],
            [
                "/usr/bin/nice",
                "-n",
                "19",
                "/usr/bin/env",
                "-i",
                f"PATH={path}",
                f"HOME={run / 'home'}",
                f"TMPDIR={run / 'tmp'}",
                "LANG=C.UTF-8",
                f"COMPARATOR_BWRAP={run / 'bwrap-wrapper'}",
                str(bin_dir / "lake"),
                "comparator",
                "--config",
                str(run / "comparator.json"),
            ],
        )
        # The wrapper drops --share-net: the bwrap never sees it, and the phase log names it.
        steps = self._calls("bwrap")
        self.assertEqual(len(steps), 6)
        for step in steps:
            self.assertNotIn("--share-net", step["args"])
        self.assertEqual(
            [phase["dropped"] for phase in payload["run"]["phases"]], [["--share-net"]] + [[]] * 5
        )

        # An unexpected error after the judge started: no verdict, but a receipt.
        with mock.patch.object(gate, "classify_comparator_run", side_effect=RuntimeError("x" * 600)):
            payload = self._judge()
        self._assert_no_verdict(payload, "comparator_failed")
        self.assertEqual(payload["findings"][0]["detail"], ("RuntimeError: " + "x" * 600)[:500])
        receipt = self._receipt(payload)
        self.assertEqual(receipt["result"], {key: payload[key] for key in RESULT_KEYS})
        self.assertEqual(self._runs(), [])

        # --keep-workspace keeps the run directory and reports where it is.
        payload = self._judge(keep_workspace=True)
        self.assertEqual(payload["comparator_status"], "accepted", payload)
        run = self._run_path(payload)
        self.assertEqual(payload["run"]["workspace_kept"], str(run))
        self.assertEqual(self._runs(), [run.name])
        self.assertEqual((run / "ws" / "Solution.lean").read_bytes(), SOLUTION.encode("utf-8"))
        self.assertEqual(len(gate.parse_phase_log((run / "phases.jsonl").read_bytes())), 6)
        self._receipt(payload)
        gate.shutil.rmtree(run)

        # A receipt that cannot be written leaves no verdict (D11).
        receipts = self.comparator_state / "receipts"
        self._plan({**_accepted_plan(), "after": [["chmod", str(receipts), "500"]]})
        try:
            payload = self._judge()
        finally:
            receipts.chmod(0o700)
        self._assert_no_verdict(payload, "receipt_write_failed")
        self.assertEqual(payload["receipt"], {"path": "", "sha256": ""})
        self.assertNotIn(payload["run"]["run_id"] + ".json", self._receipts())
        self.assertEqual(self._runs(), [])
        # A receipt whose write fails part way is removed, not left half written.
        self._plan(_accepted_plan())
        accepted = self._judge()
        before = self._receipts()
        with mock.patch.object(gate.os, "write", side_effect=OSError(28, "No space left on device")):
            gate._comparator_receipt(accepted, mock.Mock(input_root=self.input), "partial", receipts)
        self._assert_no_verdict(accepted, "receipt_write_failed", "No space left on device")
        self.assertEqual(self._receipts(), before)

        # The cleanup removes what a solution locked away in .lake: a directory
        # its owner cannot list, and one whose entries its owner cannot remove.
        self._plan(
            {
                **_accepted_plan(),
                "after": [
                    ["locked_directory", "{ws}/.lake/locked"],
                    ["read_only_directory", "{ws}/.lake/read-only"],
                ],
            }
        )
        payload = self._judge()
        self.assertEqual(
            (payload["comparator_status"], payload["findings"]), ("accepted", []), payload
        )
        self.assertEqual(self._runs(), [])

        # A cleanup that fails is reported and leaves the verdict alone.
        self._plan(_accepted_plan())
        with mock.patch.object(gate.shutil, "rmtree", side_effect=OSError("cannot remove")):
            payload = self._judge()
        self.assertEqual((payload["comparator_status"], payload["ok"]), ("accepted", True), payload)
        self.assertEqual([item["kind"] for item in payload["findings"]], ["cleanup_failed"])
        self.assertIn("cannot remove", payload["findings"][0]["detail"])
        # The receipt was written before the cleanup, so it records the verdict alone.
        self.assertEqual(self._receipt(payload)["result"]["findings"], [])
        self.assertEqual(self._runs(), [payload["run"]["run_id"]])
        gate.shutil.rmtree(self._run_path(payload))
        # So is a removal that reports nothing but leaves the directory.
        with mock.patch.object(gate.shutil, "rmtree", return_value=None):
            payload = self._judge()
        self.assertEqual((payload["comparator_status"], payload["ok"]), ("accepted", True), payload)
        self.assertEqual([item["kind"] for item in payload["findings"]], ["cleanup_failed"])
        self.assertIn("still there", payload["findings"][0]["detail"])
        gate.shutil.rmtree(self._run_path(payload))

    def test_pre_run_refusals(self) -> None:
        """REGRESSION CHECK: these refusals come before the run and held before it was wired.

        Each refusal happens before the state directory is made and before
        any lake runs; the run's own code is never reached, so these
        assertions already held at the S3-A2 head.
        """

        def refused(payload: dict[str, Any], status: str, kind: str) -> None:
            self.assertEqual((payload["comparator_status"], payload["ok"]), (status, False), payload)
            self.assertEqual([item["kind"] for item in payload["findings"]], [kind], payload)
            for call in ("resolve-deps", "comparator", "bwrap"):
                self.assertEqual(self._calls(call), [], call)
            self.assertFalse(os.path.lexists(self.state))

        old = _core_record(
            toolchain={"spec": "leanprover/lean4:v4.34.1", "files": dict(self.files)}
        )
        old_path, old_sha = _write_record(self.base / "frozen-old", old)
        payload = self._judge(floor=None, challenge_record=old_path, challenge_record_sha256=old_sha)
        refused(payload, "unsupported_toolchain", "unsupported_toolchain")
        # Not even lean --version ran.
        self.assertEqual(self._calls("lean"), [])

        payload = self._judge(floor=None, challenge_record_sha256="0" * 64)
        refused(payload, "not_run", "challenge_record_mismatch")
        self.assertEqual(self._calls("lean"), [])

        self._plan({**_accepted_plan(), "bwrap_preflight_rc": 1})
        payload = self._judge(floor=None)
        refused(payload, "tool_unavailable", "sandbox_unavailable")
        self._plan(_accepted_plan())

        exporter = self.toolchain / "bin" / "leanexport"
        with _restored(exporter):
            with exporter.open("a", encoding="utf-8") as handle:
                handle.write("# rebuilt after the freeze\n")
            payload = self._judge(floor=None)
        refused(payload, "tool_unavailable", "toolchain_changed")

        con_ron = self.toolchain / "bin" / "con-ron"
        with _restored(con_ron):
            con_ron.unlink()
            lacking = {name: digest for name, digest in self.files.items() if name != "bin/con-ron"}
            record = _core_record(toolchain={"spec": DEFAULT_TOOLCHAIN, "files": lacking})
            lacking_path, lacking_sha = _write_record(self.base / "frozen-lacking", record)
            payload = self._judge(
                floor=None,
                challenge_record=lacking_path,
                challenge_record_sha256=lacking_sha,
                paranoid=True,
            )
        refused(payload, "tool_unavailable", "paranoid_kernel_missing")

    def test_real_systemd_scope(self) -> None:
        problem = gate._comparator_memory_preflight("10G")
        if problem:
            self.skipTest(f"this host starts no user scope with a memory limit: {problem}")
        payload = self._judge(memory_limit="10G")
        self.assertEqual(payload["comparator_status"], "accepted", payload)
        self.assertEqual(
            payload["run"]["argv"][:6],
            [
                "/usr/bin/systemd-run",
                "--user",
                "--scope",
                "--quiet",
                "--collect",
                "--unit=aas-comparator-" + payload["run"]["run_id"],
            ],
        )
        self.assertEqual(self._runs(), [])


# The comparator verb with Lean itself: lake comparator v4.35.0, its leanexport
# and leanchecker, in the local bwrap. One theorem is judged: comm, frozen from
# this reviewed statement.
REAL_TOOLCHAIN = "leanprover/lean4:v4.35.0"
REAL_CHALLENGE = "theorem comm (a b : Nat) : a + b = b + a := by\n  sorry\n"
# The steps of a core run that reaches the judgment, each exiting 0, and of an
# accepted one: the judgment, then leanchecker.
JUDGED_STEPS = [(phase, target, 0) for phase, target in STEPS_BEFORE_KERNELS]
ACCEPTED_STEPS = [*JUDGED_STEPS, ("kernel", "leanchecker", 0)]


class RealComparatorTests(unittest.TestCase):
    """REGRESSION CHECKS: the comparator verb on Lean v4.35.0 and the local bwrap.

    These assertions already held at the S3-B head, where the run was wired,
    except test_restrictive_umask, which came with the fix of the run's
    umask. The five core cases confirm on Lean itself what the fake toolchain
    only assumes: Lake builds a two-module closure from the generated
    lakefile's globs, lake comparator's temporary files land under the run's
    TMPDIR (the wrapper refuses a kernel step that binds anything else), and
    the judgments the classifier knows reach the payload as rejections.

    The class runs only on a Linux host, not as root, where Lean v4.35.0 is
    installed where the gate looks for it, bubblewrap 0.12.0 or later is
    found and starts its sandbox, /usr/bin/python3 is 3.9 or later, and the
    temporary directory has the free space a run needs; elsewhere it is
    skipped with the reason. Each case freezes the challenge with that
    toolchain and runs the verb once, in about 5 seconds on the host where
    they were written, with a temporary XDG_STATE_HOME and the default memory
    limit (off when this host starts no user scope with that limit).
    """

    @classmethod
    def setUpClass(cls) -> None:
        if sys.platform != "linux":
            raise unittest.SkipTest("lake comparator sandboxes the solution with Linux namespaces")
        if os.geteuid() == 0:
            raise unittest.SkipTest("the comparator never runs as root")
        resolved = gate.resolve_comparator_toolchain(REAL_TOOLCHAIN)
        if resolved["status"] != "available":
            raise unittest.SkipTest(f"needs {REAL_TOOLCHAIN} installed: {resolved['detail']}")
        bwrap = gate.bwrap_status(probe=True)
        if bwrap["status"] != "available":
            raise unittest.SkipTest(f"needs bubblewrap 0.12.0 or later: {bwrap['detail']}")
        preflight = gate.bwrap_preflight(bwrap["path"])
        if preflight["status"] != "ok":
            raise unittest.SkipTest(f"the bwrap sandbox does not start: {preflight['detail']}")
        check = subprocess.run(
            ["/usr/bin/python3", "-I", "-c", "import sys; sys.exit(sys.version_info < (3, 9))"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if check.returncode != 0:
            raise unittest.SkipTest("the run's wrapper program needs /usr/bin/python3 3.9 or later")
        free = gate.shutil.disk_usage(tempfile.gettempdir()).free
        if free < gate.COMPARATOR_MIN_FREE_BYTES:
            raise unittest.SkipTest(
                f"the temporary directory has {free} bytes free; a run needs "
                f"{gate.COMPARATOR_MIN_FREE_BYTES}"
            )
        limit = gate.COMPARATOR_DEFAULT_MEMORY_LIMIT
        cls.memory_limit = "off" if gate._comparator_memory_preflight(limit) else limit

    def _judge(
        self, files: dict[str, str], *modules: str, umask: int | None = None
    ) -> dict[str, Any]:
        """Freeze REAL_CHALLENGE, run the verb on ``files`` once, and check what it leaves.

        ``umask``, when given, is the gate's umask during the run, not during
        the setup. The run leaves no run directory, and its receipt holds the
        payload's result, sections and run.
        """

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(os.path.realpath(tmp.name))
        statement = base / "statement"
        statement.mkdir()
        frozen = challenge_tests._freeze(
            challenge_tests._write_source(statement, REAL_CHALLENGE),
            base / "frozen",
            theorems=("comm",),
            toolchain=REAL_TOOLCHAIN,
        )
        self.assertTrue(frozen["ok"], frozen)
        project = _tree(base / "input", files)
        state = base / "state"
        previous = os.umask(umask) if umask is not None else None
        try:
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(state)}):
                payload = gate.comparator_payload(
                    project,
                    challenge_record=Path(frozen["challenge_record"]),
                    challenge_record_sha256=frozen["record_sha256"],
                    solution_modules=list(modules or ("Solution",)),
                    timeout=gate.COMPARATOR_DEFAULT_TIMEOUT,
                    paranoid=False,
                    keep_workspace=False,
                    memory_limit=self.memory_limit,
                )
        finally:
            if previous is not None:
                os.umask(previous)
        comparator_state = state / "ai-agents-skills" / "comparator"
        self.assertEqual(os.listdir(comparator_state / "runs"), [], payload)
        receipt_path = Path(payload["receipt"]["path"])
        self.assertEqual(
            receipt_path,
            comparator_state / "receipts" / (payload["run"]["run_id"] + ".json"),
            payload,
        )
        data = receipt_path.read_bytes()
        self.assertEqual(payload["receipt"]["sha256"], _sha256(data))
        self.assertEqual(_mode(receipt_path), 0o600)
        receipt = json.loads(data.decode("utf-8"))
        self.assertEqual(receipt["result"], {key: payload[key] for key in RESULT_KEYS})
        for section in ("toolchain", "sandbox", "challenge"):
            self.assertEqual(receipt[section], payload[section], section)
        self.assertEqual(receipt["solution"], {"input": str(project), **payload["solution"]})
        self.assertEqual(
            receipt["run"],
            {
                key: payload["run"][key]
                for key in ("argv", "returncode", "seconds", "timeout_seconds", "phases")
            },
        )
        self.assertEqual(payload["toolchain"]["spec"], REAL_TOOLCHAIN)
        self.assertTrue(
            payload["toolchain"]["lean_version"].startswith("Lean (version 4.35.0,"),
            payload["toolchain"],
        )
        self.assertEqual(payload["sandbox"]["memory_limit"], self.memory_limit)
        self.assertEqual(payload["solution"]["post_digest"], payload["solution"]["digest"])
        return payload

    def _assert_result(
        self, payload: dict[str, Any], status: str, kind: str = "", detail: str = ""
    ) -> None:
        self.assertEqual(
            (
                payload["comparator_status"],
                payload["comparator_rejection_kind"],
                payload["comparator_detail"],
                payload["ok"],
                payload["findings"],
            ),
            (status, kind, detail, status == "accepted", []),
            payload,
        )

    def _assert_steps(self, payload: dict[str, Any], steps: list[tuple[str, str, int]]) -> None:
        phases = payload["run"]["phases"]
        self.assertEqual(
            [(step["phase"], step["target"], step["rc"]) for step in phases], steps, payload
        )
        # Resolving asks for a network, and the wrapper takes it away.
        self.assertEqual(phases[0]["dropped"], ["--share-net"])
        self.assertEqual([step["refused"] for step in phases], [""] * len(phases))

    def test_accepted(self) -> None:
        payload = self._judge(
            {"Solution.lean": "theorem comm (a b : Nat) : a + b = b + a := Nat.add_comm a b\n"}
        )
        self._assert_result(payload, "accepted")
        self._assert_steps(payload, ACCEPTED_STEPS)
        self.assertEqual(payload["run"]["returncode"], 0)
        self.assertEqual(payload["run"]["stdout_tail"].rstrip("\n").splitlines()[-1], OKAY_LINE)
        self.assertEqual(list(payload["solution"]["files"]), ["Solution.lean"])

    def test_weakened_statement(self) -> None:
        payload = self._judge(
            {"Solution.lean": "theorem comm (a b : Nat) : a + b = a + b := rfl\n"}
        )
        self._assert_result(
            payload,
            "rejected",
            "statement_mismatch",
            "Challenge and solution theorem statement do not match: 'comm'",
        )
        self._assert_steps(payload, JUDGED_STEPS)

    def test_sorry(self) -> None:
        payload = self._judge(
            {"Solution.lean": "theorem comm (a b : Nat) : a + b = b + a := by\n  sorry\n"}
        )
        self._assert_result(
            payload, "rejected", "forbidden_axiom", "Illegal axiom detected: 'sorryAx'"
        )
        self._assert_steps(payload, JUDGED_STEPS)

    def test_compile_error(self) -> None:
        payload = self._judge(
            {
                "Solution.lean": (
                    "theorem comm (a b : Nat) : a + b = b + a := by\n  exact not_a_lemma a b\n"
                )
            }
        )
        self._assert_result(payload, "rejected", "build_failed", "Child exited with 1")
        self._assert_steps(payload, [*JUDGED_STEPS[:3], ("build", "AASSolution", 1)])

    def test_two_module_solution(self) -> None:
        # Sol.Main imports Sol.Lemmas; Sol/Broken.lean is not in the closure,
        # so it is neither copied nor built.
        payload = self._judge(
            {
                "Sol/Lemmas.lean": (
                    "theorem Sol.swap (a b : Nat) : a + b = b + a := Nat.add_comm a b\n"
                ),
                "Sol/Main.lean": (
                    "import Sol.Lemmas\n\n"
                    "theorem comm (a b : Nat) : a + b = b + a := Sol.swap a b\n"
                ),
                "Sol/Broken.lean": "this file is not Lean\n",
            },
            "Sol.Main",
        )
        self._assert_result(payload, "accepted")
        self._assert_steps(payload, ACCEPTED_STEPS)
        self.assertEqual(payload["solution"]["modules"], ["Sol.Main"])
        self.assertEqual(list(payload["solution"]["files"]), ["Sol/Main.lean", "Sol/Lemmas.lean"])

    def test_restrictive_umask(self) -> None:
        """Not a regression check: this case failed at the S3-B head.

        Under the gate's umask 0277, Lake in the sandbox made .lake/build
        without the owner's write bit, and the challenge's build failed with
        "permission denied" (challenge_build_failed, no verdict). The run's
        processes now start with COMPARATOR_UMASK, so the verdict comes back.
        """

        payload = self._judge(
            {"Solution.lean": "theorem comm (a b : Nat) : a + b = b + a := Nat.add_comm a b\n"},
            umask=0o277,
        )
        self._assert_result(payload, "accepted")
        self._assert_steps(payload, ACCEPTED_STEPS)


if __name__ == "__main__":
    unittest.main()
