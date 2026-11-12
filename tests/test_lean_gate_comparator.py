"""The gate's ``comparator`` verb, up to the judge run.

``comparator`` judges a solution project with ``lake comparator`` against a
challenge that ``challenge-freeze`` wrote. Before any judge runs, the verb
checks the host, admits the pinned record and the ``Challenge.lean`` beside
it, resolves and attests the toolchain the record names, checks the pinned
bwrap, and reads the solution's local import closure exactly once. It also
generates the workspace's ``lakefile.toml``, ``AASSolution.lean`` and
``comparator.json``. The judge run is not wired yet, so an admitted run ends
as ``command_failed``.

Everything here runs on every platform. The comparator runs only on Linux,
so the payload tests patch the platform, and admission, the closure and the
generators are also called directly. Mocks stand in for the toolchain
attestation and the bwrap probes, which would run tools, and a tool that
runs by mistake fails the test. A case that needs POSIX modes, a symlink or
a hard link is skipped with a stated reason on a host that cannot make it.
The gate module comes from ``test_lean_gate_scanner``, and the fake
toolchain and freeze helpers from ``test_lean_gate_challenge``, so one copy
of the gate is loaded.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
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


if __name__ == "__main__":
    unittest.main()
