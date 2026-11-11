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
"""

from __future__ import annotations

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


def _make_dir(path: Path) -> Path:
    path.mkdir()
    return path


def _make_bytes(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


if __name__ == "__main__":
    unittest.main()
