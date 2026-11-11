"""Challenge records for ``lake comparator``: the gate's ``challenge-freeze`` verb.

A Lean proof supports a claim only when a host-run ``lake comparator`` accepts
it against a trusted challenge. ``challenge-freeze`` produces that challenge:
it copies the reviewed source byte for byte to ``Challenge.lean`` and writes
the closed, timestamp-free record ``challenge.json`` beside it. The freeze runs
no Lean, so every test here is hermetic. Cases that need POSIX file modes, a
FIFO or a symlink skip elsewhere with a stated reason; that is why they live in
this module rather than in ``test_lean_gate_scanner``, which is never
skip-gated. The gate module and its path come from that module, so one copy of
the gate is loaded.

``ComparatorReadinessTests`` covers what the comparator will need from the
host: the platform check, the pinned bwrap, the toolchain directory, its
attestation, and the ``doctor`` fields. Fake HOME and ELAN_HOME trees stand in
for the host, and tools that must never run leave a marker if they do. The
sandbox preflight runs a fake bwrap and so needs Linux.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest import mock

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import test_lean_gate_scanner as scanner  # noqa: E402

gate = scanner.gate
GATE_PY = scanner.GATE_PY
SKILL_MD = (
    scanner.REPO_ROOT
    / "canonical"
    / "skills"
    / "lean-strict-verification-gate"
    / "SKILL.md"
)
DEFAULT_TOOLCHAIN = "leanprover/lean4:v4.35.0"
RECORD_KEYS = {
    "schema_version",
    "challenge_sha256",
    "challenge_module",
    "theorem_names",
    "definition_names",
    "permitted_axioms",
    "paranoid",
    "toolchain",
    "workspace",
    "allowed_import_roots",
}
PAYLOAD_KEYS = {
    "schema_version",
    "ok",
    "mode",
    "input",
    "out",
    "findings",
    "challenge_record",
    "challenge_file",
    "record_sha256",
    "challenge_sha256",
    "advisory_scan",
    "limitations",
}
# A reviewed core statement: the bodies are `sorry`, as a challenge's are.
CHALLENGE = (
    "-- A reviewed statement for lake comparator\n"
    "theorem add_zero_right (n : Nat) : n + 0 = n := by\n"
    "  sorry\n"
    "\n"
    "namespace AASDemo\n"
    "theorem two_add_two : 2 + 2 = 4 := sorry\n"
    "end AASDemo\n"
)


def _write_source(directory: str | Path, text: str = CHALLENGE) -> Path:
    source = Path(directory) / "Statement.lean"
    source.write_bytes(text.encode("utf-8"))
    return source


def _freeze(
    source: Path,
    out: Path,
    *,
    theorems: tuple[str, ...] = ("add_zero_right",),
    definitions: tuple[str, ...] = (),
    axioms: tuple[str, ...] = (),
    allow_compiler_trust: bool = False,
    paranoid: bool = False,
    toolchain: str = DEFAULT_TOOLCHAIN,
) -> dict[str, Any]:
    return gate.challenge_freeze_payload(
        source,
        out_dir=out,
        theorems=list(theorems),
        definitions=list(definitions),
        permitted_axioms=list(axioms),
        allow_compiler_trust=allow_compiler_trust,
        paranoid=paranoid,
        toolchain=toolchain,
    )


def _kinds(payload: dict[str, Any]) -> list[str]:
    return [item["kind"] for item in payload["findings"]]


def _release_fifo_reader(fifo: Path) -> None:
    """Open the write end once so a reader blocked in open() can return."""

    try:
        descriptor = os.open(fifo, os.O_WRONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return
    os.close(descriptor)


# What `lake comparator` takes from its toolchain, and the preflight argv after
# the bwrap path.
COMPARATOR_BINARIES = ("lake", "lean", "leanchecker", "leanexport")
PREFLIGHT_ARGS = ["--ro-bind", "/", "/", "--unshare-all", "--die-with-parent", "--", "/bin/true"]
# Variables that select a tool for the gate; each readiness test starts without them.
_READINESS_VARIABLES = (
    "AAS_BWRAP",
    "AAS_LEAN_COMPARATOR_TOOLCHAIN",
    "ELAN_HOME",
    "AAS_LEAN",
    "AAS_LAKE",
    "AAS_LEAN4CHECKER",
)


@contextlib.contextmanager
def _readiness_environment(home: Path, **values: str) -> Any:
    """HOME and USERPROFILE point at ``home``; only ``values`` select tools."""

    with mock.patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
        for name in _READINESS_VARIABLES:
            os.environ.pop(name, None)
        os.environ.update(values)
        yield


@contextlib.contextmanager
def _working_directory(path: Path) -> Any:
    """Run with ``path`` as the working directory, so a relative name could resolve there."""

    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _write_tool(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def _marker_tool(path: Path, marker: Path) -> Path:
    """An executable that leaves ``marker`` behind if anything ever runs it."""

    return _write_tool(path, f"#!/bin/sh\ntouch '{marker}'\nexit 99\n")


def _toolchain_dir(directory: Path, binaries: tuple[str, ...] = COMPARATOR_BINARIES) -> Path:
    """A toolchain directory whose bin/ holds ``binaries`` as plain files."""

    for name in binaries:
        _write_tool(directory / "bin" / gate.executable_name(name), f"#!/bin/sh\n# {name}\nexit 98\n")
    return directory


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ChallengeFreezeTests(unittest.TestCase):
    def _within_deadline(
        self,
        function: Callable[[], Any],
        *,
        fifo: Path,
        seconds: float = 10.0,
    ) -> Any:
        outcome: dict[str, Any] = {}

        def target() -> None:
            try:
                outcome["value"] = function()
            except BaseException as exc:  # re-raised on the test thread
                outcome["error"] = exc

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(seconds)
        if thread.is_alive():
            _release_fifo_reader(fifo)
            thread.join(seconds)
            self.fail(f"reading {fifo.name} was still blocked after {seconds} s")
        if "error" in outcome:
            raise outcome["error"]
        return outcome["value"]

    def _assert_refused(
        self,
        payload: dict[str, Any],
        kind: str,
        out: Path,
        *,
        before: list[str] | None = None,
    ) -> None:
        self.assertFalse(payload["ok"], payload)
        self.assertIn(kind, _kinds(payload), payload)
        self.assertEqual(set(payload), PAYLOAD_KEYS, payload)
        for key in ("challenge_record", "challenge_file", "record_sha256", "challenge_sha256"):
            self.assertEqual(payload[key], "", payload)
        self.assertEqual(payload["advisory_scan"], {}, payload)
        if before is None:
            self.assertFalse(os.path.lexists(out), payload)
        else:
            self.assertEqual(sorted(os.listdir(out)), before, payload)

    def _run_gate(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, "-B", str(GATE_PY), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=env,
            cwd=cwd,
            check=False,
        )

    def test_parse_lean_version_accepts_only_stable_releases_from_4_35(self) -> None:
        for value, expected in (
            ("leanprover/lean4:v4.35.0", (4, 35, 0)),
            ("leanprover/lean4:v4.36.1", (4, 36, 1)),
            ("leanprover/lean4:v5.0.0", (5, 0, 0)),
        ):
            with self.subTest(value=value):
                self.assertEqual(gate.parse_lean_version(value), expected)
                self.assertGreaterEqual(expected, gate.COMPARATOR_MIN_LEAN_VERSION)
        self.assertEqual(gate.COMPARATOR_MIN_LEAN_VERSION, (4, 35, 0))
        self.assertEqual(gate.parse_lean_version("leanprover/lean4:v4.34.1"), (4, 34, 1))
        self.assertLess((4, 34, 1), gate.COMPARATOR_MIN_LEAN_VERSION)
        for value in (
            "leanprover/lean4:v4.35.0-rc2",
            "leanprover/lean4:nightly-2026-10-01",
            "leanprover/lean4:v4.035.0",
            # Arabic-Indic digits: \d and int() accept them, a release never has them.
            "leanprover/lean4:v٤.٣٥.٠",
            "leanprover/lean4:v4.35.0\n",
            "v4.35.0",
            "4.35.0",
            "",
            None,
            b"leanprover/lean4:v4.35.0",
            ["leanprover/lean4:v4.35.0"],
            "Lean (version 4.35.0-rc2, aarch64-unknown-linux-gnu, commit x, Release)",
        ):
            with self.subTest(value=value):
                self.assertIsNone(gate.parse_lean_version(value))
        self.assertEqual(
            gate.parse_lean_version(
                "Lean (version 4.35.0, aarch64-unknown-linux-gnu, commit x, Release)"
            ),
            (4, 35, 0),
        )
        if hasattr(sys, "set_int_max_str_digits"):
            # A component past the int conversion limit is unparseable, not a crash.
            limit = sys.get_int_max_str_digits()
            sys.set_int_max_str_digits(640)
            try:
                self.assertIsNone(
                    gate.parse_lean_version("leanprover/lean4:v4.35." + "1" * 641)
                )
            finally:
                sys.set_int_max_str_digits(limit)

    def test_freeze_copies_bytes_and_pins_the_record(self) -> None:
        text = (
            "-- Thử thách: cộng với không\r\n"
            "theorem add_zero_right : ∀ n : Nat, n + 0 = n := by\r\n"
            "  sorry\r\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "Statement.lean"
            source.write_bytes(text.encode("utf-8"))
            source_bytes = source.read_bytes()
            self.assertIn(b"\r\n", source_bytes)
            out = Path(tmp) / "frozen"
            payload = _freeze(source, out)
            self.assertTrue(payload["ok"], payload)
            self.assertEqual(payload["findings"], [])
            self.assertEqual(set(payload), PAYLOAD_KEYS)
            self.assertEqual(payload["schema_version"], "lean-strict-verification-gate.v1")
            self.assertEqual(payload["mode"], "challenge-freeze")
            frozen = out / "Challenge.lean"
            record_path = out / "challenge.json"
            self.assertEqual(sorted(os.listdir(out)), ["Challenge.lean", "challenge.json"])
            self.assertEqual(frozen.read_bytes(), source_bytes)
            for key, path in (("challenge_file", frozen), ("challenge_record", record_path)):
                self.assertTrue(Path(payload[key]).is_absolute(), payload)
                self.assertTrue(os.path.samefile(payload[key], path), payload)
            self.assertEqual(
                payload["challenge_sha256"], hashlib.sha256(source_bytes).hexdigest()
            )
            record_bytes = record_path.read_bytes()
            self.assertEqual(
                payload["record_sha256"], hashlib.sha256(record_bytes).hexdigest()
            )
            record = json.loads(record_bytes.decode("ascii"))
            self.assertEqual(record["challenge_sha256"], payload["challenge_sha256"])
            self.assertEqual(record["theorem_names"], ["add_zero_right"])
            scan = payload["advisory_scan"]
            self.assertEqual(scan["placeholder_status"], "placeholders_allowed_for_stub")
            self.assertEqual(scan["content_sha256"], payload["challenge_sha256"])
            self.assertTrue(os.path.samefile(scan["input"], frozen))
            limitations = " ".join(payload["limitations"])
            self.assertEqual(len(payload["limitations"]), 4)
            for phrase in ("human or different-family review", "lake comparator", "line walk"):
                self.assertIn(phrase, limitations)

    def test_record_is_deterministic_and_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_source(tmp)
            first = _freeze(
                source,
                Path(tmp) / "first",
                theorems=("add_zero_right", "AASDemo.two_add_two"),
            )
            second = _freeze(
                source,
                Path(tmp) / "second",
                theorems=("AASDemo.two_add_two", "add_zero_right", "AASDemo.two_add_two"),
            )
            self.assertTrue(first["ok"], first)
            self.assertTrue(second["ok"], second)
            first_bytes = (Path(tmp) / "first" / "challenge.json").read_bytes()
            second_bytes = (Path(tmp) / "second" / "challenge.json").read_bytes()
            self.assertEqual(first_bytes, second_bytes)
            self.assertEqual(first["record_sha256"], second["record_sha256"])
            record = json.loads(first_bytes.decode("ascii"))
            self.assertEqual(set(record), RECORD_KEYS)
            self.assertEqual(set(gate.CHALLENGE_RECORD_KEYS), RECORD_KEYS)
            self.assertEqual(
                record,
                {
                    "schema_version": "aas_lean_challenge.v1",
                    "challenge_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "challenge_module": "AASChallenge",
                    "theorem_names": ["AASDemo.two_add_two", "add_zero_right"],
                    "definition_names": [],
                    "permitted_axioms": ["Classical.choice", "Quot.sound", "propext"],
                    "paranoid": False,
                    "toolchain": DEFAULT_TOOLCHAIN,
                    "workspace": {"kind": "core"},
                    "allowed_import_roots": ["Init", "Lake", "Lean", "Std"],
                },
            )
            canonical = (
                json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                + "\n"
            ).encode("ascii")
            self.assertEqual(first_bytes, canonical)
            self.assertTrue(first_bytes.endswith(b"}\n"))
            self.assertIsNone(re.search(rb"\d{4}-\d{2}-\d{2}|T\d{2}:\d{2}", first_bytes))

            # Given axioms replace the default set, sorted and de-duplicated.
            chosen = _freeze(
                source,
                Path(tmp) / "chosen",
                axioms=("propext", "Quot.sound", "propext"),
                paranoid=True,
            )
            self.assertTrue(chosen["ok"], chosen)
            chosen_record = json.loads(
                (Path(tmp) / "chosen" / "challenge.json").read_text(encoding="utf-8")
            )
            self.assertEqual(chosen_record["permitted_axioms"], ["Quot.sound", "propext"])
            self.assertIs(chosen_record["paranoid"], True)
            self.assertNotEqual(chosen["record_sha256"], first["record_sha256"])

    @unittest.skipUnless(os.name == "posix", "POSIX owner-only modes; Windows has no mode bits to check")
    def test_outputs_are_private_on_posix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_source(tmp)
            created = Path(tmp) / "created"
            existing = Path(tmp) / "existing"
            existing.mkdir()
            os.chmod(existing, 0o755)
            for out in (created, existing):
                payload = _freeze(source, out)
                self.assertTrue(payload["ok"], payload)
                self.assertEqual(stat.S_IMODE(os.lstat(out).st_mode), 0o700, out)
                for name in ("Challenge.lean", "challenge.json"):
                    self.assertEqual(
                        stat.S_IMODE(os.lstat(out / name).st_mode), 0o600, out / name
                    )

    def test_each_refusal_writes_nothing(self) -> None:
        refusals = (
            ("v4.34.1", "unsupported_toolchain", CHALLENGE, {"toolchain": "leanprover/lean4:v4.34.1"}),
            ("rc", "unsupported_toolchain", CHALLENGE, {"toolchain": "leanprover/lean4:v4.35.0-rc2"}),
            ("nightly", "unsupported_toolchain", CHALLENGE, {"toolchain": "leanprover/lean4:nightly-2026-10-01"}),
            ("bare version", "unsupported_toolchain", CHALLENGE, {"toolchain": "v4.35.0"}),
            (
                "version banner",
                "unsupported_toolchain",
                CHALLENGE,
                {"toolchain": "Lean (version 4.35.0, aarch64-unknown-linux-gnu, commit x, Release)"},
            ),
            ("sorryAx", "never_sanctioned_axiom", CHALLENGE, {"axioms": ("sorryAx",)}),
            (
                "sorryAx with compiler trust allowed",
                "never_sanctioned_axiom",
                CHALLENGE,
                {"axioms": ("sorryAx",), "allow_compiler_trust": True},
            ),
            ("unknown axiom", "never_sanctioned_axiom", CHALLENGE, {"axioms": ("myAxiom",)}),
            ("compiler trust", "compiler_trust_not_allowed", CHALLENGE, {"axioms": ("Lean.ofReduceBool",)}),
            ("definition hole", "definition_hole_not_allowed", CHALLENGE, {"definitions": ("myDef",)}),
            ("missing theorem", "theorem_not_declared", CHALLENGE, {"theorems": ("not_there",)}),
            ("a def", "theorem_not_declared", "def helper : Nat := 0\n", {"theorems": ("helper",)}),
            (
                "private theorem",
                "theorem_not_declared",
                "private theorem hidden : True := sorry\n",
                {"theorems": ("hidden",)},
            ),
            (
                "comment only",
                "theorem_not_declared",
                "-- theorem ghost : True := sorry\n",
                {"theorems": ("ghost",)},
            ),
            (
                "unqualified name",
                "theorem_not_declared",
                "namespace AASDemo\ntheorem inner : True := sorry\nend AASDemo\n",
                {"theorems": ("inner",)},
            ),
            ("invalid name", "theorem_not_declared", CHALLENGE, {"theorems": ("bad name",)}),
            (
                "escaped theorem name",
                "theorem_not_declared",
                "theorem «weird» : True := sorry\n",
                {"theorems": ("«weird»",)},
            ),
            ("no theorem named", "theorem_not_declared", CHALLENGE, {"theorems": ()}),
            ("import Mathlib", "challenge_import_not_trusted", "import Mathlib\n" + CHALLENGE, {}),
            ("import Foo.Bar", "challenge_import_not_trusted", "import Foo.Bar\n" + CHALLENGE, {}),
            ("bare import", "challenge_import_not_trusted", "import\n" + CHALLENGE, {}),
            (
                "escaped name under a core root",
                "challenge_import_not_trusted",
                "import Lean.«Elab»\n" + CHALLENGE,
                {},
            ),
            ("#eval", "challenge_unsafe_construct", CHALLENGE + "#eval 1 + 1\n", {}),
            ("run_cmd", "challenge_unsafe_construct", CHALLENGE + "run_cmd pure ()\n", {}),
        )
        expected_details = {
            "sorryAx": "sorryAx can never be permitted",
            "sorryAx with compiler trust allowed": "sorryAx can never be permitted",
            "import Mathlib": "Mathlib",
            "import Foo.Bar": "Foo.Bar",
            "bare import": "<missing>",
            "escaped name under a core root": "Lean.«Elab»",
            "#eval": "#eval",
            "run_cmd": "run_cmd",
        }
        for label, kind, text, options in refusals:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                source = _write_source(tmp, text)
                out = Path(tmp) / "out"
                payload = _freeze(source, out, **options)
                self._assert_refused(payload, kind, out)
                if label in expected_details:
                    details = {
                        item["detail"] for item in payload["findings"] if item["kind"] == kind
                    }
                    self.assertEqual(details, {expected_details[label]}, payload)

        with self.subTest(case="unparsed declaration line"), tempfile.TemporaryDirectory() as tmp:
            # Lean reads the name on the next line; the line walk cannot.
            source = _write_source(tmp, "theorem\n  weird : True := sorry\n")
            out = Path(tmp) / "out"
            payload = _freeze(source, out, theorems=("weird",))
            self._assert_refused(payload, "theorem_not_declared", out)
            self.assertIn("unparsed declaration lines: 1", payload["findings"][0]["detail"])

        accepted = (
            ("core import", "import Lean.Elab\nimport Std.Data.HashMap\n" + CHALLENGE, {}),
            ("commented #eval", CHALLENGE + "-- #eval 1 + 1\n", {}),
            ("compiler trust allowed", CHALLENGE, {"axioms": ("Lean.ofReduceBool",), "allow_compiler_trust": True}),
        )
        for label, text, options in accepted:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                source = _write_source(tmp, text)
                out = Path(tmp) / "out"
                payload = _freeze(source, out, **options)
                self.assertTrue(payload["ok"], payload)
                if label == "compiler trust allowed":
                    record = json.loads((out / "challenge.json").read_text(encoding="utf-8"))
                    self.assertEqual(record["permitted_axioms"], ["Lean.ofReduceBool"])

        limit = 1024 * 1024
        self.assertEqual(gate.CHALLENGE_SOURCE_MAX_BYTES, limit)
        padded = CHALLENGE + "-- " + "x" * (limit - len(CHALLENGE.encode("utf-8")) - 4) + "\n"
        with self.subTest(case="exactly 1 MiB"), tempfile.TemporaryDirectory() as tmp:
            source = _write_source(tmp, padded)
            self.assertEqual(source.stat().st_size, limit)
            payload = _freeze(source, Path(tmp) / "out")
            self.assertTrue(payload["ok"], payload)

        unreadable: list[tuple[str, Callable[[Path], Path]]] = [
            ("missing", lambda tmp: tmp / "Missing.lean"),
            ("a directory", lambda tmp: _make_dir(tmp / "Statement.lean")),
            ("invalid UTF-8", lambda tmp: _make_bytes(tmp / "Statement.lean", b"theorem t : True := sorry\n\xff\n")),
            ("1 MiB + 1", lambda tmp: _make_bytes(tmp / "Statement.lean", (padded + "x").encode("utf-8"))),
        ]
        for label, make in unreadable:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                source = make(Path(tmp))
                out = Path(tmp) / "out"
                payload = _freeze(source, out, theorems=("t",))
                self._assert_refused(payload, "source_unreadable", out)

        with self.subTest(case="symlinked source"), tempfile.TemporaryDirectory() as tmp:
            target = _write_source(tmp)
            link = Path(tmp) / "Link.lean"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                link = None
            if link is not None:
                out = Path(tmp) / "out"
                payload = _freeze(link, out)
                self._assert_refused(payload, "source_unreadable", out)
                self.assertIn("symlink_input", payload["findings"][0]["detail"])

        if hasattr(os, "mkfifo"):
            with self.subTest(case="FIFO source"), tempfile.TemporaryDirectory() as tmp:
                fifo = Path(tmp) / "Statement.lean"
                os.mkfifo(fifo)
                out = Path(tmp) / "out"
                payload = self._within_deadline(lambda: _freeze(fifo, out), fifo=fifo)
                self._assert_refused(payload, "source_unreadable", out)

        with self.subTest(case="non-empty out"), tempfile.TemporaryDirectory() as tmp:
            source = _write_source(tmp)
            out = Path(tmp) / "out"
            out.mkdir()
            (out / "keep.txt").write_text("kept\n", encoding="utf-8")
            payload = _freeze(source, out)
            self._assert_refused(payload, "output_dir_unusable", out, before=["keep.txt"])

        with self.subTest(case="out is a file"), tempfile.TemporaryDirectory() as tmp:
            source = _write_source(tmp)
            out = Path(tmp) / "out"
            out.write_bytes(b"not a directory\n")
            payload = _freeze(source, out)
            self.assertFalse(payload["ok"], payload)
            self.assertIn("output_dir_unusable", _kinds(payload))
            self.assertEqual(out.read_bytes(), b"not a directory\n")

        with self.subTest(case="out parent missing"), tempfile.TemporaryDirectory() as tmp:
            source = _write_source(tmp)
            out = Path(tmp) / "absent" / "out"
            payload = _freeze(source, out)
            self._assert_refused(payload, "output_dir_unusable", out)
            self.assertFalse(os.path.lexists(out.parent))

        with self.subTest(case="symlinked out"), tempfile.TemporaryDirectory() as tmp:
            source = _write_source(tmp)
            real = Path(tmp) / "real"
            real.mkdir()
            out = Path(tmp) / "out"
            try:
                out.symlink_to(real, target_is_directory=True)
            except (OSError, NotImplementedError):
                out = None
            if out is not None:
                payload = _freeze(source, out)
                self.assertFalse(payload["ok"], payload)
                self.assertIn("output_dir_unusable", _kinds(payload))
                self.assertEqual(os.listdir(real), [])

        real_open = os.open

        def refuse_record(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            if Path(path).name == "challenge.json" and flags & os.O_CREAT:
                raise OSError(28, "No space left on device", str(path))
            return real_open(path, flags, *args, **kwargs)

        for label, preexisting in (("created out", False), ("existing empty out", True)):
            with self.subTest(case=f"write fails mid-way, {label}"), tempfile.TemporaryDirectory() as tmp:
                source = _write_source(tmp)
                out = Path(tmp) / "out"
                if preexisting:
                    out.mkdir()
                with mock.patch.object(gate.os, "open", side_effect=refuse_record):
                    payload = _freeze(source, out)
                self._assert_refused(
                    payload, "output_write_failed", out, before=[] if preexisting else None
                )

    def test_a_crash_is_reported_and_leaves_nothing_behind(self) -> None:
        # scan_declarations runs before anything is written, scan_path after.
        for name in ("scan_declarations", "scan_path"):
            with self.subTest(crash_in=name), tempfile.TemporaryDirectory() as tmp:
                source = _write_source(tmp)
                out = Path(tmp) / "out"
                with mock.patch.object(gate, name, side_effect=RuntimeError("injected")):
                    payload = _freeze(source, out)
                self._assert_refused(payload, "challenge_freeze_failed", out)
                self.assertEqual(_kinds(payload), ["challenge_freeze_failed"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs are POSIX-only")
    def test_a_fifo_swapped_in_after_lstat_cannot_block_the_source_read(self) -> None:
        """The shared reader opens with O_NONBLOCK, so a FIFO swapped in after lstat fails fast."""

        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / "Statement.lean"
            os.mkfifo(fifo)
            real = os.lstat(fifo)
            # What lstat reported for the regular file the FIFO replaced.
            regular = os.stat_result(
                (
                    stat.S_IFREG | 0o644,
                    real.st_ino,
                    real.st_dev,
                    1,
                    real.st_uid,
                    real.st_gid,
                    0,
                    int(real.st_atime),
                    int(real.st_mtime),
                    int(real.st_ctime),
                )
            )
            real_lstat = os.lstat

            def swapped(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
                if os.fspath(path) == os.fspath(fifo):
                    return regular
                return real_lstat(path, *args, **kwargs)

            def read() -> str:
                try:
                    gate.stable_regular_file_bytes(fifo, max_bytes=1024)
                except gate.StableReadError as exc:
                    return exc.kind
                return "read"

            with mock.patch.object(gate.os, "lstat", side_effect=swapped):
                kind = self._within_deadline(read, fifo=fifo)
        self.assertEqual(kind, "input_changed_during_scan")

    def test_cli_exit_codes_and_documentation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_source(tmp)
            out = Path(tmp) / "frozen"
            accepted = self._run_gate(
                "challenge-freeze",
                "--source", str(source),
                "--theorem", "add_zero_right",
                "--out", str(out),
            )
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            payload = json.loads(accepted.stdout)
            self.assertTrue(payload["ok"], payload)
            self.assertEqual(sorted(os.listdir(out)), ["Challenge.lean", "challenge.json"])
            self.assertEqual((out / "Challenge.lean").read_bytes(), source.read_bytes())
            record = json.loads((out / "challenge.json").read_text(encoding="utf-8"))
            self.assertEqual(record["toolchain"], DEFAULT_TOOLCHAIN)
            self.assertEqual(
                hashlib.sha256((out / "challenge.json").read_bytes()).hexdigest(),
                payload["record_sha256"],
            )

            # Relative --source and --out still give absolute payload paths.
            relative = self._run_gate(
                "challenge-freeze",
                "--source", source.name,
                "--theorem", "add_zero_right",
                "--out", "relative",
                cwd=Path(tmp),
            )
            self.assertEqual(relative.returncode, 0, relative.stdout + relative.stderr)
            relative_payload = json.loads(relative.stdout)
            for key, name in (("challenge_file", "Challenge.lean"), ("challenge_record", "challenge.json")):
                self.assertTrue(Path(relative_payload[key]).is_absolute(), relative_payload)
                self.assertTrue(
                    os.path.samefile(relative_payload[key], Path(tmp) / "relative" / name),
                    relative_payload,
                )

            refused_out = Path(tmp) / "refused"
            refused = self._run_gate(
                "challenge-freeze",
                "--source", str(source),
                "--theorem", "not_there",
                "--out", str(refused_out),
            )
            self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
            self.assertEqual(_kinds(json.loads(refused.stdout)), ["theorem_not_declared"])
            self.assertFalse(os.path.lexists(refused_out))

            usage_out = Path(tmp) / "usage"
            usage = self._run_gate(
                "challenge-freeze", "--source", str(source), "--out", str(usage_out)
            )
            self.assertEqual(usage.returncode, 2, usage.stdout + usage.stderr)
            self.assertIn("--theorem", usage.stderr)
            self.assertFalse(os.path.lexists(usage_out))
        skill = SKILL_MD.read_text(encoding="utf-8")
        self.assertIn("run_lean_strict_verification_gate.sh challenge-freeze", skill)


class ComparatorReadinessTests(unittest.TestCase):
    def test_platform_needs_linux_and_a_non_root_user(self) -> None:
        for platform, euid, expected in (
            ("darwin", 1000, False),
            ("win32", 1000, False),
            ("linux", 0, False),
            ("linux", 1000, True),
        ):
            with self.subTest(platform=platform, euid=euid), mock.patch.object(
                gate.sys, "platform", platform
            ), mock.patch.object(gate.os, "geteuid", return_value=euid, create=True):
                self.assertIs(gate.comparator_platform_supported(), expected)

    def test_bwrap_resolution_never_uses_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "home"
            home.mkdir()
            marker = base / "executed"
            path_dir = base / "on-path"
            path_bwrap = _marker_tool(path_dir / "bwrap", marker)
            pinned = _marker_tool(base / "opt" / "bubblewrap-0.12.0" / "bin" / "bwrap", marker)
            unversioned = _marker_tool(base / "custom" / "bwrap", marker)
            on_path = {"PATH": os.pathsep.join([str(path_dir), os.environ.get("PATH", "")])}

            with _readiness_environment(home, AAS_BWRAP=str(pinned), **on_path):
                chosen = gate.bwrap_status()
            self.assertEqual(
                chosen,
                {
                    "status": "available",
                    "path": os.path.realpath(pinned),
                    "source": "env",
                    "version": "0.12.0",
                    "version_source": "directory",
                    "supported": True,
                    "detail": "",
                },
            )

            # Without a bubblewrap-X.Y.Z directory the version is unknown until probed.
            with _readiness_environment(home, AAS_BWRAP=str(unversioned), **on_path):
                unknown = gate.bwrap_status()
            self.assertEqual(unknown["status"], "tool_unavailable", unknown)
            self.assertEqual(unknown["path"], os.path.realpath(unversioned))
            self.assertEqual(unknown["source"], "env")
            self.assertIsNone(unknown["version"])
            self.assertIsNone(unknown["version_source"])
            self.assertFalse(unknown["supported"])
            self.assertIn("--probe", unknown["detail"])

            # The resolved file is reported, and only its own directory names a
            # version: a symlink cannot borrow one.
            alias = base / "alias" / "bwrap"
            borrowed = base / "borrowed" / "bubblewrap-0.13.0" / "bin" / "bwrap"
            alias.parent.mkdir()
            borrowed.parent.mkdir(parents=True)
            try:
                alias.symlink_to(pinned)
                borrowed.symlink_to(unversioned)
            except (OSError, NotImplementedError):
                pass
            else:
                with _readiness_environment(home, AAS_BWRAP=str(alias), **on_path):
                    aliased = gate.bwrap_status()
                self.assertEqual(aliased, {**chosen, "path": os.path.realpath(pinned)})
                with _readiness_environment(home, AAS_BWRAP=str(borrowed), **on_path):
                    unborrowed = gate.bwrap_status()
                self.assertEqual(unborrowed["path"], os.path.realpath(unversioned))
                self.assertIsNone(unborrowed["version"], unborrowed)
                self.assertFalse(unborrowed["supported"], unborrowed)

            # Each refused value would otherwise reach a usable bwrap: the relative
            # names resolve to one from the working directory, and the others sit
            # where a supported version would be read from the directory name.
            directory = base / "dirs" / "bubblewrap-0.12.0" / "bin" / "bwrap"
            directory.mkdir(parents=True)
            refused_values = {
                "a bare name": "bwrap",
                "a relative path": os.path.join("..", "on-path", "bwrap"),
                "a missing file": str(base / "missing" / "bubblewrap-0.12.0" / "bin" / "bwrap"),
                "a directory": str(directory),
                "an over-long name": str(base / ("x" * 300) / "bwrap"),
            }
            if os.name == "posix":
                not_executable = base / "plain" / "bubblewrap-0.12.0" / "bin" / "bwrap"
                not_executable.parent.mkdir(parents=True)
                not_executable.write_text("#!/bin/sh\n", encoding="utf-8")
                not_executable.chmod(0o644)
                refused_values["a file without the execute bit"] = str(not_executable)
            for label, value in refused_values.items():
                with self.subTest(AAS_BWRAP=label):
                    with _readiness_environment(home, AAS_BWRAP=value, **on_path), _working_directory(
                        path_dir
                    ):
                        refused = gate.bwrap_status()
                    self.assertEqual(refused["status"], "tool_unavailable", refused)
                    self.assertEqual(refused["source"], "env", refused)
                    self.assertFalse(refused["supported"], refused)
                    self.assertNotEqual(refused["path"], os.path.realpath(path_bwrap), refused)
                    self.assertTrue(refused["detail"], refused)

            # Unset, with an empty home: a bwrap on PATH or in the working
            # directory is never a fallback.
            with _readiness_environment(home, **on_path), _working_directory(path_dir):
                missing = gate.bwrap_status()
            self.assertEqual(
                missing,
                {
                    "status": "tool_unavailable",
                    "path": "",
                    "source": "not-found",
                    "version": None,
                    "version_source": None,
                    "supported": False,
                    "detail": missing["detail"],
                },
            )
            self.assertIn("AAS_BWRAP", missing["detail"])
            self.assertFalse(marker.exists())

    def test_bwrap_picks_the_newest_install_and_enforces_the_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            marker = base / "executed"
            home = base / "home"
            opt = home / ".local" / "opt"
            for name in (
                "bubblewrap-0.9.10",
                "bubblewrap-0.11.0",
                "bubblewrap-0.12.0",
                "bubblewrap-0.13.1",
                "bubblewrap-latest",
                # Not X.Y.Z with ASCII digits and no leading zeros: ignored.
                "bubblewrap-0.013.2",
                "bubblewrap-0.٩٩.0",
            ):
                _marker_tool(opt / name / "bin" / "bwrap", marker)
            (opt / "bubblewrap-0.14.0" / "bin").mkdir(parents=True)
            (opt / "bubblewrap-0.15.0").write_text("not a directory\n", encoding="utf-8")
            with _readiness_environment(home):
                newest = gate.bwrap_status()
            self.assertEqual(
                newest,
                {
                    "status": "available",
                    "path": os.path.realpath(opt / "bubblewrap-0.13.1" / "bin" / "bwrap"),
                    "source": "local-opt",
                    "version": "0.13.1",
                    "version_source": "directory",
                    "supported": True,
                    "detail": "",
                },
            )

            old_home = base / "old-home"
            old = _marker_tool(
                old_home / ".local" / "opt" / "bubblewrap-0.11.0" / "bin" / "bwrap", marker
            )
            with _readiness_environment(old_home):
                floor = gate.bwrap_status()
            self.assertEqual(floor["status"], "tool_unavailable", floor)
            self.assertEqual(floor["path"], os.path.realpath(old))
            self.assertEqual(floor["source"], "local-opt")
            self.assertEqual(floor["version"], "0.11.0")
            self.assertEqual(floor["version_source"], "directory")
            self.assertFalse(floor["supported"])
            self.assertIn("0.12.0", floor["detail"])
            self.assertFalse(marker.exists())

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "the sandbox preflight needs Linux namespaces, and the fake bwrap is a POSIX script",
    )
    def test_bwrap_preflight_with_a_fake_bwrap(self) -> None:
        check = (
            "import os\n"
            "import sys\n"
            f"if sys.argv[1:] != {PREFLIGHT_ARGS!r}:\n"
            "    print('unexpected argv', sys.argv[1:], file=sys.stderr)\n"
            "    sys.exit(3)\n"
            "if os.environ.get('PATH') != '/usr/bin:/bin' or 'GATE_TEST_CANARY' in os.environ:\n"
            "    print('unexpected environment', file=sys.stderr)\n"
            "    sys.exit(4)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)

            def fake(name: str, tail: str) -> Path:
                directory = base / name
                directory.mkdir()
                return scanner._fake_tool(directory, "bwrap", tail)

            passing = fake("pass", check + "sys.exit(0)\n")
            failing = fake(
                "fail",
                check + "print('bwrap: setting up uid map: Permission denied', file=sys.stderr)\n"
                "sys.exit(1)\n",
            )
            sleeper = fake("sleep", check + "import time\ntime.sleep(30)\n")
            flood = fake("flood", check + "sys.stdout.write('x' * (100 * 1024))\nsys.exit(0)\n")
            marker = base / "spawned"
            recorder = fake(
                "record",
                f"open({str(marker)!r}, 'w', encoding='utf-8').close()\n",
            )
            not_executable = base / "plain" / "bwrap"
            not_executable.parent.mkdir()
            not_executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            not_executable.chmod(0o644)
            with mock.patch.object(
                gate, "comparator_platform_supported", return_value=True
            ), mock.patch.dict(os.environ, {"GATE_TEST_CANARY": "parent only"}):
                ok = gate.bwrap_preflight(str(passing))
                failed = gate.bwrap_preflight(str(failing))
                slow = gate.bwrap_preflight(str(sleeper), timeout=0.5)
                flooded = gate.bwrap_preflight(str(flood))
                unusable = gate.bwrap_preflight(str(not_executable))
                relative = gate.bwrap_preflight("bwrap")
                # ./bwrap names the passing fake; it is refused all the same.
                with _working_directory(passing.parent):
                    dotted = gate.bwrap_preflight("./bwrap")
            self.assertEqual(
                ok,
                {
                    "status": "ok",
                    "command": [str(passing), *PREFLIGHT_ARGS],
                    "returncode": 0,
                    "stderr": "",
                    "detail": "",
                },
            )
            self.assertEqual(failed["status"], "command_failed", failed)
            self.assertEqual(failed["returncode"], 1)
            self.assertIn("Permission denied", failed["stderr"])
            self.assertEqual(slow["status"], "command_failed", slow)
            self.assertIn("timeout", slow["detail"])
            # Output past the 64 KiB cap is a failure, not a started sandbox.
            self.assertEqual(flooded["status"], "command_failed", flooded)
            self.assertIn("exceeded", flooded["detail"])
            # A relative name is never looked up, not even on the preflight's
            # PATH, and a file that cannot run is not spawned.
            for refused in (relative, dotted, unusable):
                self.assertEqual(refused["status"], "tool_unavailable", refused)
                self.assertIsNone(refused["returncode"], refused)
                self.assertTrue(refused["detail"], refused)

            with mock.patch.object(gate, "comparator_platform_supported", return_value=False):
                refused = gate.bwrap_preflight(str(recorder))
            self.assertEqual(refused["status"], "tool_unavailable", refused)
            self.assertIsNone(refused["returncode"])
            self.assertFalse(marker.exists())

    def test_toolchain_resolution_and_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "home"
            home.mkdir()
            marker = base / "executed"
            elan = base / "elan"
            # The elan proxies obey the working directory's lean-toolchain: never used.
            _marker_tool(elan / "bin" / gate.executable_name("lean"), marker)
            _marker_tool(elan / "bin" / gate.executable_name("lake"), marker)
            toolchains = elan / "toolchains"
            full = _toolchain_dir(toolchains / "leanprover--lean4---v4.35.0")
            _toolchain_dir(toolchains / "leanprover--lean4---v4.35.1", ("lake", "lean"))
            for name in ("v4.34.1", "v4.35.0-rc2", "nightly-2026-10-01"):
                _toolchain_dir(toolchains / f"leanprover--lean4---{name}")
            input_root = base / "input"
            input_root.mkdir()
            outside = base / "outside"
            outside.mkdir()

            with _readiness_environment(home, ELAN_HOME=str(elan)):
                found = gate.resolve_comparator_toolchain(
                    "leanprover/lean4:v4.35.0", input_root=outside
                )
                not_installed = gate.resolve_comparator_toolchain("leanprover/lean4:v4.36.0")
                incomplete = gate.resolve_comparator_toolchain("leanprover/lean4:v4.35.1")
                unsupported = {
                    value: gate.resolve_comparator_toolchain(value)
                    for value in (
                        "leanprover/lean4:v4.34.1",
                        "leanprover/lean4:v4.35.0-rc2",
                        "leanprover/lean4:nightly-2026-10-01",
                        "Lean (version 4.35.0, x86_64-unknown-linux-gnu, commit abc, Release)",
                        "v4.35.0",
                    )
                }
            resolved = os.path.realpath(full)
            self.assertEqual(
                found,
                {
                    "status": "available",
                    "toolchain": "leanprover/lean4:v4.35.0",
                    "path": resolved,
                    "bin_dir": os.path.join(resolved, "bin"),
                    "source": "elan-home",
                    "detail": "",
                },
            )
            # Elan names the directory by turning "/" into "--" and ":" into "---".
            self.assertEqual(Path(found["path"]).name, "leanprover--lean4---v4.35.0")
            self.assertEqual(not_installed["status"], "tool_unavailable", not_installed)
            self.assertEqual(not_installed["source"], "elan-home")
            self.assertEqual(incomplete["status"], "tool_unavailable", incomplete)
            for binary in ("leanchecker", "leanexport"):
                self.assertIn(binary, incomplete["detail"])
            for value, result in unsupported.items():
                with self.subTest(toolchain=value):
                    self.assertEqual(result["status"], "unsupported_toolchain", result)
                    self.assertEqual(result["path"], "", result)

            # ELAN_HOME defaults to ~/.elan.
            default_home = base / "default-home"
            default = _toolchain_dir(
                default_home / ".elan" / "toolchains" / "leanprover--lean4---v4.35.0"
            )
            with _readiness_environment(default_home):
                from_home = gate.resolve_comparator_toolchain("leanprover/lean4:v4.35.0")
            self.assertEqual(from_home["status"], "available", from_home)
            self.assertEqual(from_home["path"], os.path.realpath(default))

            override = _toolchain_dir(base / "custom-toolchain")
            _toolchain_dir(input_root)
            inside = _toolchain_dir(input_root / "toolchain")
            _toolchain_dir(input_root / "toolchains" / "leanprover--lean4---v4.35.0")
            # Every refused value names a complete toolchain, so only the rule
            # under test stands between it and "available". The relative values
            # resolve from the working directory, base.
            refusals = {
                "relative override": ({"AAS_LEAN_COMPARATOR_TOOLCHAIN": "custom-toolchain"}, "env"),
                "override inside the input": ({"AAS_LEAN_COMPARATOR_TOOLCHAIN": str(inside)}, "env"),
                "override is the input": ({"AAS_LEAN_COMPARATOR_TOOLCHAIN": str(input_root)}, "env"),
                "ELAN_HOME inside the input": ({"ELAN_HOME": str(input_root)}, "elan-home"),
                "relative ELAN_HOME": ({"ELAN_HOME": "elan"}, "elan-home"),
                "an over-long override": (
                    {"AAS_LEAN_COMPARATOR_TOOLCHAIN": str(base / ("x" * 300))},
                    "env",
                ),
            }
            linked_binary = _toolchain_dir(base / "linked-binary", ("lake", "lean", "leanchecker"))
            linked_bin = base / "linked-bin"
            linked_bin.mkdir()
            try:
                (linked_binary / "bin" / gate.executable_name("leanexport")).symlink_to(
                    override / "bin" / gate.executable_name("leanexport")
                )
                (linked_bin / "bin").symlink_to(override / "bin", target_is_directory=True)
            except (OSError, NotImplementedError):
                pass
            else:
                refusals["a symlinked binary"] = (
                    {"AAS_LEAN_COMPARATOR_TOOLCHAIN": str(linked_binary)},
                    "env",
                )
                refusals["a symlinked bin/"] = (
                    {"AAS_LEAN_COMPARATOR_TOOLCHAIN": str(linked_bin)},
                    "env",
                )
            for label, (values, source) in refusals.items():
                with self.subTest(case=label):
                    with _readiness_environment(home, **values), _working_directory(base):
                        refused = gate.resolve_comparator_toolchain(
                            "leanprover/lean4:v4.35.0", input_root=input_root
                        )
                    self.assertEqual(refused["status"], "tool_unavailable", refused)
                    self.assertEqual(refused["source"], source, refused)
                    self.assertEqual(refused["bin_dir"], "", refused)
                    self.assertTrue(refused["detail"], refused)

            with _readiness_environment(
                home, AAS_LEAN_COMPARATOR_TOOLCHAIN=str(override), ELAN_HOME=str(elan)
            ):
                chosen = gate.resolve_comparator_toolchain(
                    "leanprover/lean4:v4.35.0", input_root=input_root
                )
            self.assertEqual(chosen["status"], "available", chosen)
            self.assertEqual(chosen["source"], "env")
            self.assertEqual(chosen["path"], os.path.realpath(override))
            self.assertFalse(marker.exists())

    @unittest.skipUnless(
        os.name == "posix",
        "the fake lean is a POSIX script; the comparator toolchain is Linux-only",
    )
    def test_toolchain_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "leanprover--lean4---v4.35.0"
            bin_dir = root / "bin"
            bin_dir.mkdir(parents=True)
            record = Path(tmp) / "lean-run.json"
            behaviour = bin_dir / "behaviour.json"
            scanner._fake_tool(
                bin_dir,
                "lean",
                "import json\n"
                "import os\n"
                "import sys\n"
                f"with open({str(record)!r}, 'w', encoding='utf-8') as handle:\n"
                "    json.dump({'env': dict(os.environ), 'cwd': os.getcwd(), 'argv': sys.argv[1:]}, handle)\n"
                "here = os.path.dirname(os.path.abspath(__file__))\n"
                "with open(os.path.join(here, 'behaviour.json'), encoding='utf-8') as handle:\n"
                "    behaviour = json.load(handle)\n"
                "print(behaviour['stdout'])\n"
                "sys.exit(behaviour['exit'])\n",
            )
            for name in ("lake", "leanchecker", "leanexport"):
                (bin_dir / name).write_bytes(f"#!/bin/sh\n# {name}\n".encode("utf-8"))
            library = root / "lib" / "lean"
            library.mkdir(parents=True)
            (library / "libleanshared.so").write_bytes(b"\x7fELF shared\n")
            (library / "libInit_shared.so").write_bytes(b"\x7fELF init\n")
            (library / "Init.olean").write_bytes(b"not a library\n")
            banner = "Lean (version 4.35.0, x86_64-unknown-linux-gnu, commit 0123abc, Release)"

            def attest(stdout: str, exit_code: int = 0) -> dict[str, Any]:
                behaviour.write_text(
                    json.dumps({"stdout": stdout, "exit": exit_code}), encoding="utf-8"
                )
                with mock.patch.dict(
                    os.environ,
                    {"LEAN_PATH": "/nonexistent/lean-path", "ELAN_TOOLCHAIN": "leanprover/lean4:v4.0.0"},
                ):
                    return gate.toolchain_attestation(root)

            attested = attest(banner + "\nTarget: x86_64-unknown-linux-gnu")
            expected_files = {
                f"bin/{name}": _sha256(bin_dir / name) for name in COMPARATOR_BINARIES
            }
            expected_files["lib/lean/libInit_shared.so"] = _sha256(library / "libInit_shared.so")
            expected_files["lib/lean/libleanshared.so"] = _sha256(library / "libleanshared.so")
            self.assertEqual(
                attested,
                {
                    "status": "ok",
                    "files": expected_files,
                    "lean_version": banner,
                    "version": "4.35.0",
                    "detail": "",
                },
            )
            run = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual(run["argv"], ["--version"])
            self.assertTrue(os.path.samefile(run["cwd"], root), run["cwd"])
            self.assertNotIn("LEAN_PATH", run["env"])
            self.assertNotIn("ELAN_TOOLCHAIN", run["env"])
            self.assertEqual(run["env"].get("LANG"), "C.UTF-8")
            self.assertEqual(
                run["env"]["PATH"].split(os.pathsep), [str(bin_dir), "/usr/bin", "/bin"]
            )

            for label, stdout, exit_code, status in (
                ("release candidate", "Lean (version 4.35.0-rc2, x86_64, commit 1, Release)", 0, "unsupported_toolchain"),
                ("older release", "Lean (version 4.34.1, x86_64, commit 1, Release)", 0, "unsupported_toolchain"),
                ("not a banner", "lean 4.35.0", 0, "command_failed"),
                ("a toolchain spec instead of a banner", "leanprover/lean4:v4.35.0", 0, "command_failed"),
                ("non-zero exit", banner, 1, "command_failed"),
                ("output past the 64 KiB cap", banner + "\n" + "x" * (100 * 1024), 0, "command_failed"),
            ):
                with self.subTest(case=label):
                    result = attest(stdout, exit_code)
                    self.assertEqual(result["status"], status, result)
                    self.assertEqual(result["files"], expected_files, result)

            behaviour.write_text(json.dumps({"stdout": banner, "exit": 0}), encoding="utf-8")
            with self.subTest(case="a relative toolchain directory"), _working_directory(Path(tmp)):
                relative = gate.toolchain_attestation(root.name)
                self.assertEqual(relative["status"], "tool_unavailable", relative)
                self.assertEqual(relative["files"], {}, relative)
            # Only bin/ is a symlink here; lib/lean is an ordinary copy.
            linked = Path(tmp) / "linked-toolchain"
            (linked / "lib" / "lean").mkdir(parents=True)
            for name in ("libleanshared.so", "libInit_shared.so"):
                (linked / "lib" / "lean" / name).write_bytes((library / name).read_bytes())
            try:
                (linked / "bin").symlink_to(bin_dir, target_is_directory=True)
            except (OSError, NotImplementedError):
                linked = None
            if linked is not None:
                with self.subTest(case="a symlinked bin/"):
                    through_link = gate.toolchain_attestation(linked)
                    self.assertEqual(through_link["status"], "tool_unavailable", through_link)
                    self.assertEqual(through_link["files"], {}, through_link)

            libraries = sorted(library.iterdir())
            for index in range(gate.TOOLCHAIN_LIBRARY_MAX_FILES):
                (library / f"libextra{index}.so").write_bytes(b"\x7fELF extra\n")
            with self.subTest(case="too many libraries"):
                self.assertEqual(attest(banner)["status"], "tool_unavailable")
            for path in library.iterdir():
                if path not in libraries:
                    path.unlink()

            link = library / "libLake_shared.so"
            try:
                link.symlink_to(library / "libleanshared.so")
            except (OSError, NotImplementedError):
                link = None
            if link is not None:
                with self.subTest(case="symlinked library"):
                    linked = attest(banner)
                    self.assertEqual(linked["status"], "tool_unavailable", linked)
                    self.assertIn("libLake_shared.so", linked["detail"])
                link.unlink()

            (bin_dir / "leanexport").unlink()
            with self.subTest(case="missing binary"):
                missing = attest(banner)
                self.assertEqual(missing["status"], "tool_unavailable", missing)
                self.assertIn("leanexport", missing["detail"])

    def test_doctor_reports_readiness_from_listings_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            marker = base / "executed"
            home = base / "home"
            bwrap = _marker_tool(home / ".local" / "opt" / "bubblewrap-0.13.1" / "bin" / "bwrap", marker)
            _marker_tool(home / ".local" / "opt" / "bubblewrap-0.11.0" / "bin" / "bwrap", marker)
            toolchains = home / ".elan" / "toolchains"
            for name, binaries in (
                ("leanprover--lean4---v4.34.1", ("lake", "lean", "leanchecker")),
                ("leanprover--lean4---v4.35.0", COMPARATOR_BINARIES),
                ("leanprover--lean4---v4.35.0-rc2", COMPARATOR_BINARIES),
                ("leanprover--lean4---nightly-2026-10-01", ("lake", "lean")),
                # Not a leanprover/lean4 toolchain: not listed.
                ("custom-build", COMPARATOR_BINARIES),
            ):
                for binary in binaries:
                    _marker_tool(toolchains / name / "bin" / gate.executable_name(binary), marker)
            (toolchains / "leanprover--lean4---v9.9.9").write_text("not a directory\n", encoding="utf-8")
            project = base / "project"
            project.mkdir()
            with _readiness_environment(home), mock.patch.object(
                gate.shutil, "which", return_value=None
            ):
                payload = gate.doctor_payload(project_root=project, probe=False)
                os.environ["ELAN_HOME"] = "relative-elan"
                relative_elan = gate.doctor_payload(project_root=project, probe=False)
                os.environ["ELAN_HOME"] = str(base / "no-elan-here")
                no_elan = gate.doctor_payload(project_root=project, probe=False)
                del os.environ["ELAN_HOME"]
                with mock.patch.object(gate, "bwrap_status", side_effect=RuntimeError("injected")):
                    crashed = gate.doctor_payload(project_root=project, probe=False)

            self.assertEqual(payload["status"], "ok")
            for key in ("tool_status", "project_status", "no_auto_install", "network_required", "installs_attempted"):
                self.assertIn(key, payload)
            self.assertEqual(
                set(payload["tool_status"]),
                {"lean", "lake", "elan", "lean4checker", "npm", "npx", "pip"},
            )
            self.assertNotIn("probe_status", payload)
            comparator = payload["comparator"]
            self.assertEqual(set(comparator), {"platform_supported", "bwrap", "toolchains"})
            self.assertIs(comparator["platform_supported"], gate.comparator_platform_supported())
            self.assertEqual(
                comparator["bwrap"],
                {
                    "status": "available",
                    "path": os.path.realpath(bwrap),
                    "source": "local-opt",
                    "version": "0.13.1",
                    "version_source": "directory",
                    "supported": True,
                    "detail": "",
                },
            )
            self.assertEqual(
                comparator["toolchains"],
                [
                    {
                        "name": "leanprover/lean4:nightly-2026-10-01",
                        "version": None,
                        "supported": False,
                        "has_comparator": False,
                    },
                    {
                        "name": "leanprover/lean4:v4.34.1",
                        "version": "4.34.1",
                        "supported": False,
                        "has_comparator": False,
                    },
                    {
                        "name": "leanprover/lean4:v4.35.0",
                        "version": "4.35.0",
                        "supported": True,
                        "has_comparator": True,
                    },
                    {
                        "name": "leanprover/lean4:v4.35.0-rc2",
                        "version": None,
                        "supported": False,
                        "has_comparator": True,
                    },
                ],
            )

            # No elan installation is an empty listing, not an error.
            self.assertEqual(no_elan["comparator"]["toolchains"], [])
            self.assertNotIn("toolchains_error", no_elan["comparator"])
            # A listing that cannot be made is reported, and the doctor still succeeds.
            self.assertEqual(relative_elan["status"], "ok")
            self.assertEqual(relative_elan["comparator"]["toolchains"], [])
            self.assertIn("ELAN_HOME", relative_elan["comparator"]["toolchains_error"])
            # So is a crash anywhere in the readiness section.
            self.assertEqual(crashed["status"], "ok")
            self.assertIn("injected", crashed["comparator"]["error"])
            self.assertFalse(marker.exists())

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "the probe runs a fake bwrap, a POSIX script, through the Linux-only sandbox preflight",
    )
    def test_doctor_probe_reads_the_bwrap_version_and_runs_the_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "home"
            opt_bin = home / ".local" / "opt" / "bubblewrap-0.13.1" / "bin"
            opt_bin.mkdir(parents=True)
            release = base / "release.txt"
            release.write_text("0.13.1", encoding="utf-8")
            bwrap = scanner._fake_tool(
                opt_bin,
                "bwrap",
                "import sys\n"
                "if sys.argv[1:] == ['--version']:\n"
                f"    with open({str(release)!r}, encoding='utf-8') as handle:\n"
                "        print('bubblewrap ' + handle.read().strip())\n"
                "    sys.exit(0)\n"
                f"sys.exit(0 if sys.argv[1:] == {PREFLIGHT_ARGS!r} else 5)\n",
            )
            project = base / "project"
            project.mkdir()
            with _readiness_environment(home), mock.patch.object(
                gate.shutil, "which", return_value=None
            ), mock.patch.object(gate, "comparator_platform_supported", return_value=True):
                payload = gate.doctor_payload(project_root=project, probe=True)
                release.write_text("0.11.0", encoding="utf-8")
                old = gate.doctor_payload(project_root=project, probe=True)
            self.assertIn("probe_status", payload)
            comparator = payload["comparator"]
            self.assertEqual(
                comparator["bwrap"],
                {
                    "status": "available",
                    "path": os.path.realpath(bwrap),
                    "source": "local-opt",
                    "version": "0.13.1",
                    "version_source": "probe",
                    "supported": True,
                    "detail": "",
                },
            )
            self.assertEqual(
                comparator["sandbox_probe"],
                {
                    "status": "ok",
                    "command": [os.path.realpath(bwrap), *PREFLIGHT_ARGS],
                    "returncode": 0,
                    "stderr": "",
                    "detail": "",
                },
            )
            # The probed release decides, whatever the directory name says.
            old_bwrap = old["comparator"]["bwrap"]
            self.assertEqual(old_bwrap["version"], "0.11.0", old_bwrap)
            self.assertEqual(old_bwrap["version_source"], "probe")
            self.assertFalse(old_bwrap["supported"])
            self.assertEqual(old_bwrap["status"], "tool_unavailable")
            self.assertEqual(old["comparator"]["sandbox_probe"]["status"], "tool_unavailable")
            self.assertIsNone(old["comparator"]["sandbox_probe"]["returncode"])

    def test_readiness_is_documented(self) -> None:
        skill = SKILL_MD.read_text(encoding="utf-8")
        for phrase in (
            "## Comparator readiness",
            "AAS_BWRAP",
            "AAS_LEAN_COMPARATOR_TOOLCHAIN",
            "ELAN_HOME",
            "sandbox_probe",
        ):
            with self.subTest(phrase=phrase):
                self.assertTrue(phrase in skill, f"SKILL.md does not mention {phrase!r}")


def _make_dir(path: Path) -> Path:
    path.mkdir()
    return path


def _make_bytes(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


if __name__ == "__main__":
    unittest.main()
