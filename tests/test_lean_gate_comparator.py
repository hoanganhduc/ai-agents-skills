"""The gate's ``comparator`` verb, up to the judge run, and the run's own pieces.

``comparator`` judges a solution project with ``lake comparator`` against a
challenge that ``challenge-freeze`` wrote. Before any judge runs, the verb
checks the host, admits the pinned record and the ``Challenge.lean`` beside
it, resolves and attests the toolchain the record names, checks the pinned
bwrap, and reads the solution's local import closure exactly once. It also
generates the workspace's ``lakefile.toml``, ``AASSolution.lean`` and
``comparator.json``. The judge run is not wired yet, so an admitted run ends
as ``command_failed``. The run's own pieces are tested here before they are
wired: the wrapper program lake comparator runs in place of bwrap, the phase
log it writes, and the classification of a finished run.

Everything here runs on every platform, except the wrapper program itself,
which forks and runs bwrap and so is run only on POSIX hosts. The comparator
runs only on Linux, so the payload tests patch the platform, and admission,
the closure, the generators, the wrapper's argument check and the
classification are also called directly. Mocks stand in for the toolchain
attestation and the bwrap probes, which would run tools, and a tool that
runs by mistake fails the test. A case that needs POSIX modes, a symlink or
a hard link is skipped with a stated reason on a host that cannot make it.
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
import subprocess
import sys
import tempfile
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
            "The judge run is not wired yet",
            "`run not implemented`",
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
        """Run comparator_payload with the tool probes mocked; ``self.calls`` holds the mocks."""

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
            ("_comparator_run", {"wraps": gate._comparator_run}),
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
        self._assert_finding(payload, "command_failed", "comparator_failed", "run not implemented")
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
        self._assert_finding(payload, "command_failed", "comparator_failed", "run not implemented")
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
        self.assertEqual(payload["findings"][0]["detail"], "run not implemented")
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
        self._assert_finding(payload, "command_failed", "comparator_failed", "run not implemented")
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


if __name__ == "__main__":
    unittest.main()
