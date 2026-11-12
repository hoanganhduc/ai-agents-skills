"""Challenge records for ``lake comparator``: the gate's ``challenge-freeze`` verb.

A Lean proof supports a claim only when a host-run ``lake comparator`` accepts
it against a trusted challenge. ``challenge-freeze`` produces that challenge:
it copies the reviewed source byte for byte to ``Challenge.lean`` and writes
the closed, timestamp-free record ``challenge.json`` beside it. The freeze runs
no tool, so every test here is hermetic: the toolchain whose bytes the record
pins is a fake tree under a temporary ELAN_HOME. A case that needs POSIX file
modes, a FIFO or a symlink is skipped with a stated reason on a host that
lacks it, so a lost case shows up as a skip, never as a silent pass. The gate
module and its path come from ``test_lean_gate_scanner``, so one copy of the
gate is loaded.

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
import shutil
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
    "toolchain_dir",
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
# The kernels a toolchain bundles for `lake comparator --paranoid`.
BUNDLED_KERNELS = ("leanchecker-paranoid", "lean4lean", "nanoda_bin", "con-leche", "con-ron")
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


def _comparator_toolchain(directory: Path) -> Path:
    """A complete toolchain tree whose bytes a record pins; nothing in it runs.

    bin/ holds the comparator's binaries and the bundled kernels. lib/lean
    holds two shared libraries beside a module file and a directory, which
    are not libraries, and the module files of Lean.Elab and Std.Data.HashMap,
    the core imports the accepted challenges use.
    """

    _toolchain_dir(directory, COMPARATOR_BINARIES + BUNDLED_KERNELS)
    library = directory / "lib" / "lean"
    (library / "Init").mkdir(parents=True)
    for name in ("libleanshared.so", "libInit_shared.so"):
        (library / name).write_bytes(f"\x7fELF {name}\n".encode("utf-8"))
    (library / "Init.olean").write_bytes(b"not a library\n")
    for parts in (("Lean", "Elab.olean"), ("Std", "Data", "HashMap.olean")):
        module = library.joinpath(*parts)
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_bytes(b"a module, not a library\n")
    return directory


def _toolchain_files(directory: Path) -> dict[str, str]:
    """The digests a record and an attestation pin for ``_comparator_toolchain(directory)``."""

    names = sorted(gate.executable_name(name) for name in COMPARATOR_BINARIES + BUNDLED_KERNELS)
    files = {f"bin/{name}": _sha256(directory / "bin" / name) for name in names}
    for name in ("libInit_shared.so", "libleanshared.so"):
        files[f"lib/lean/{name}"] = _sha256(directory / "lib" / "lean" / name)
    return files


class ChallengeFreezeTests(unittest.TestCase):
    def setUp(self) -> None:
        # The record pins the bytes of the toolchain it names, so every freeze
        # here finds a complete fake toolchain under a temporary ELAN_HOME,
        # outside each test's source directory.
        elan = tempfile.TemporaryDirectory()
        self.addCleanup(elan.cleanup)
        self.elan_home = Path(elan.name)
        self.toolchain = _comparator_toolchain(
            self.elan_home / "toolchains" / "leanprover--lean4---v4.35.0"
        )
        environment = mock.patch.dict(os.environ, {"ELAN_HOME": str(self.elan_home)})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("AAS_LEAN_COMPARATOR_TOOLCHAIN", None)

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
        for key in (
            "challenge_record",
            "challenge_file",
            "record_sha256",
            "challenge_sha256",
            "toolchain_dir",
        ):
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
            self.assertEqual(payload["toolchain_dir"], os.path.realpath(self.toolchain))
            limitations = " ".join(payload["limitations"])
            self.assertEqual(len(payload["limitations"]), 4)
            for phrase in (
                "human or different-family review",
                "lake comparator",
                "line walk",
                "toolchain bytes seen at freeze time",
            ):
                self.assertIn(phrase, limitations)
            self.assertNotIn("not the toolchain's bytes", limitations)

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
                    "toolchain": {
                        "spec": DEFAULT_TOOLCHAIN,
                        "files": _toolchain_files(self.toolchain),
                    },
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

    def test_record_pins_the_toolchain_bytes_and_the_freeze_runs_no_tool(self) -> None:
        """N3S12-F2: the record's toolchain is its spec and the attestation's file digests."""

        banner = "Lean (version 4.35.0, x86_64-unknown-linux-gnu, commit 0123abc, Release)"
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_source(tmp)
            out = Path(tmp) / "first"
            # A tool run would raise inside the freeze and turn it into a refusal.
            with mock.patch.object(
                gate, "run_bounded_command", side_effect=AssertionError("the freeze ran a tool")
            ), mock.patch.object(
                gate.subprocess, "Popen", side_effect=AssertionError("the freeze started a process")
            ):
                payload = _freeze(source, out)
            self.assertTrue(payload["ok"], payload)
            record_bytes = (out / "challenge.json").read_bytes()
            record = json.loads(record_bytes.decode("ascii"))
            files = _toolchain_files(self.toolchain)
            self.assertEqual(record["toolchain"], {"spec": DEFAULT_TOOLCHAIN, "files": files})
            for kernel in BUNDLED_KERNELS:
                self.assertIn(f"bin/{gate.executable_name(kernel)}", files)
            self.assertEqual(payload["toolchain_dir"], os.path.realpath(self.toolchain))

            # Exactly the map the attestation hashes before it asks lean for its version.
            lean_run = gate.BoundedCommandResult(0, banner + "\n", "", len(banner) + 1)
            with mock.patch.object(gate, "run_bounded_command", return_value=lean_run):
                attested = gate.toolchain_attestation(Path(payload["toolchain_dir"]))
            self.assertEqual(attested["status"], "ok", attested)
            self.assertEqual(attested["files"], record["toolchain"]["files"])

            # The same bytes in another directory give the same record bytes.
            elsewhere = self.elan_home / "elsewhere"
            shutil.copytree(self.toolchain, elsewhere)
            with mock.patch.dict(os.environ, {"AAS_LEAN_COMPARATOR_TOOLCHAIN": str(elsewhere)}):
                moved = _freeze(source, Path(tmp) / "moved")
            self.assertTrue(moved["ok"], moved)
            self.assertEqual(moved["toolchain_dir"], os.path.realpath(elsewhere))
            self.assertEqual((Path(tmp) / "moved" / "challenge.json").read_bytes(), record_bytes)

            # A rebuilt bundled kernel changes its digest, and so the record.
            kernel = self.toolchain / "bin" / gate.executable_name("nanoda_bin")
            kernel.write_bytes(kernel.read_bytes() + b"# rebuilt\n")
            edited = _freeze(source, Path(tmp) / "edited")
            self.assertTrue(edited["ok"], edited)
            self.assertNotEqual(edited["record_sha256"], payload["record_sha256"])
            edited_record = json.loads(
                (Path(tmp) / "edited" / "challenge.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                edited_record["toolchain"]["files"],
                {**files, f"bin/{kernel.name}": _sha256(kernel)},
            )

    def test_a_toolchain_whose_bytes_cannot_be_pinned_is_refused(self) -> None:
        """N3S12-F2: a toolchain whose bytes the freeze cannot pin is refused; nothing is written."""

        with tempfile.TemporaryDirectory() as custom:
            custom_root = Path(custom)
            _toolchain_dir(
                self.elan_home / "toolchains" / "leanprover--lean4---v4.35.1", ("lake", "lean")
            )
            with_directory = _comparator_toolchain(custom_root / "with-directory")
            (with_directory / "bin" / "share").mkdir()
            crowded = _comparator_toolchain(custom_root / "crowded")
            for index in range(65 - len(COMPARATOR_BINARIES + BUNDLED_KERNELS)):
                (crowded / "bin" / f"tool{index}").write_bytes(f"tool {index}\n".encode("utf-8"))
            cases = (
                ("not installed", {"toolchain": "leanprover/lean4:v4.36.0"}, {}, "not installed"),
                ("incomplete", {"toolchain": "leanprover/lean4:v4.35.1"}, {}, "leanexport"),
                ("relative ELAN_HOME", {}, {"ELAN_HOME": "elan"}, "ELAN_HOME"),
                (
                    "relative override",
                    {},
                    {"AAS_LEAN_COMPARATOR_TOOLCHAIN": "toolchain"},
                    "absolute",
                ),
                (
                    "a directory in bin/",
                    {},
                    {"AAS_LEAN_COMPARATOR_TOOLCHAIN": str(with_directory)},
                    "bin/share",
                ),
                (
                    "65 entries in bin/",
                    {},
                    {"AAS_LEAN_COMPARATOR_TOOLCHAIN": str(crowded)},
                    "64",
                ),
            )
            for label, options, environment, detail in cases:
                with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                    os.environ, environment
                ):
                    source = _write_source(tmp)
                    out = Path(tmp) / "out"
                    payload = _freeze(source, out, **options)
                    self._assert_refused(payload, "toolchain_unavailable", out)
                    self.assertEqual(_kinds(payload), ["toolchain_unavailable"], payload)
                    self.assertIn(detail, payload["findings"][0]["detail"], payload)

            with self.subTest(case="the toolchain lies in the source's directory"):
                with tempfile.TemporaryDirectory() as tmp:
                    source = _write_source(tmp)
                    inside = _comparator_toolchain(Path(tmp) / "toolchain")
                    out = Path(tmp) / "out"
                    with mock.patch.dict(
                        os.environ, {"AAS_LEAN_COMPARATOR_TOOLCHAIN": str(inside)}
                    ):
                        payload = _freeze(source, out)
                    self._assert_refused(payload, "toolchain_unavailable", out)
                    self.assertIn("inside the input", payload["findings"][0]["detail"], payload)

            with self.subTest(case="a symlink in bin/"):
                linked = _comparator_toolchain(custom_root / "with-symlink")
                kernel = linked / "bin" / gate.executable_name("nanoda_bin")
                kernel.unlink()
                try:
                    kernel.symlink_to(with_directory / "bin" / kernel.name)
                except (OSError, NotImplementedError) as exc:
                    self.skipTest(f"this host cannot create a symlink: {exc}")
                with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                    os.environ, {"AAS_LEAN_COMPARATOR_TOOLCHAIN": str(linked)}
                ):
                    source = _write_source(tmp)
                    out = Path(tmp) / "out"
                    payload = _freeze(source, out)
                    self._assert_refused(payload, "toolchain_unavailable", out)
                    self.assertIn(f"bin/{kernel.name}", payload["findings"][0]["detail"], payload)

    def test_the_toolchain_pin_is_documented(self) -> None:
        """N3S12-F2: SKILL.md says where to freeze and when a record stops matching."""

        skill = SKILL_MD.read_text(encoding="utf-8")
        section = " ".join(skill.split("## Challenge freeze", 1)[1].split("\n## ", 1)[0].split())
        for phrase in (
            "`toolchain_unavailable`",
            "`toolchain_dir`",
            "on the host that will judge",
            "needs a new freeze",
            "on another host or architecture will not match",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)
        for stale in ("needs no toolchain", "not the toolchain's bytes"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, section)

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
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"this host cannot create a symlink: {exc}")
            out = Path(tmp) / "out"
            payload = _freeze(link, out)
            self._assert_refused(payload, "source_unreadable", out)
            self.assertIn("symlink_input", payload["findings"][0]["detail"])

        with self.subTest(case="FIFO source"), tempfile.TemporaryDirectory() as tmp:
            if not hasattr(os, "mkfifo"):
                self.skipTest("FIFOs are POSIX-only")
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
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"this host cannot create a symlink: {exc}")
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
        # The toolchain is resolved and hashed, and scan_declarations runs,
        # before anything is written; scan_path runs after.
        for name in (
            "resolve_comparator_toolchain",
            "toolchain_file_digests",
            "scan_declarations",
            "scan_path",
        ):
            with self.subTest(crash_in=name), tempfile.TemporaryDirectory() as tmp:
                source = _write_source(tmp)
                out = Path(tmp) / "out"
                with mock.patch.object(gate, name, side_effect=RuntimeError("injected")):
                    payload = _freeze(source, out)
                self._assert_refused(payload, "challenge_freeze_failed", out)
                self.assertEqual(_kinds(payload), ["challenge_freeze_failed"])

    def test_a_source_the_lexer_cannot_finish_is_refused(self) -> None:
        """N3S12-F3: a lexical error refuses the freeze, as it fails ``scan``.

        The scanner's lexer does not read raw strings. It reads r#"a"b"# as a
        string and the start of another one that never ends, so the #eval that
        Lean runs after it never meets the safety patterns.
        """

        cases = (
            (
                "an #eval behind a raw string",
                'theorem t : True := sorry\ndef x := r#"a"b"#\n#eval IO.println "x"\ndef y := "c"\n',
                "lexical_scan_incomplete: unterminated string literal",
            ),
            (
                "an unterminated block comment",
                "theorem t : True := sorry\n/- never closed\n#eval 1\n",
                "lexical_scan_incomplete: unterminated block comment",
            ),
        )
        for label, text, detail in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                source = _write_source(tmp, text)
                scanned = gate.scan_path(source, "stub", set())
                self.assertIn(
                    "lexical_scan_incomplete", [item["kind"] for item in scanned["findings"]]
                )
                out = Path(tmp) / "out"
                payload = _freeze(source, out, theorems=("t",))
                self._assert_refused(payload, "challenge_unsafe_construct", out)
                self.assertEqual(
                    payload["findings"], [{"kind": "challenge_unsafe_construct", "detail": detail}]
                )
        skill = SKILL_MD.read_text(encoding="utf-8")
        section = " ".join(skill.split("## Challenge freeze", 1)[1].split("\n## ", 1)[0].split())
        for phrase in ("`lexical_scan_incomplete: ...`", "raw string"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)

    def test_out_may_sit_under_a_symlinked_parent(self) -> None:
        """N3S12-F4: an absent --out needs an existing parent directory, symlinked or not.

        os.mkdir follows a symlinked parent, as on macOS, whose /tmp is a
        symlink to /private/tmp. A symlinked --out itself stays refused
        (test_each_refusal_writes_nothing). That the help names no symlink
        rule is a regression check.
        """

        with self.subTest(case="a symlinked parent"), tempfile.TemporaryDirectory() as tmp:
            source = _write_source(tmp)
            private = Path(tmp) / "private"
            private.mkdir()
            linked = Path(tmp) / "linked"
            try:
                linked.symlink_to(private, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"this host cannot create a symlink: {exc}")
            payload = _freeze(source, linked / "frozen")
            self.assertTrue(payload["ok"], payload)
            frozen = private / "frozen"
            self.assertEqual(sorted(os.listdir(frozen)), ["Challenge.lean", "challenge.json"])
            self.assertEqual((frozen / "Challenge.lean").read_bytes(), source.read_bytes())
            self.assertTrue(os.path.samefile(payload["challenge_file"], frozen / "Challenge.lean"))

        skill = SKILL_MD.read_text(encoding="utf-8")
        section = " ".join(skill.split("## Challenge freeze", 1)[1].split("\n## ", 1)[0].split())
        self.assertIn("may be reached through a symlink", section)
        usage = self._run_gate("challenge-freeze", "--help")
        self.assertEqual(usage.returncode, 0, usage.stdout + usage.stderr)
        self.assertIn("--out", usage.stdout)
        self.assertNotIn("symlink", usage.stdout)

    def test_a_theorem_lean_would_not_declare_is_refused(self) -> None:
        """N3S12-F5: the record never pins a theorem name the comparator cannot find.

        A line the walk cannot read may change the namespace Lean puts a name
        in. ``theorem _root_.foo`` inside ``namespace N`` declares ``foo``, so a
        name with a ``_root_`` component is refused, not rewritten. ``lemma``
        is not a core Lean command, so a core challenge that declares anything
        with it does not build. The accepted cases guard against a check that
        reads too much.
        """

        refusals = (
            (
                "an unparsed scope line",
                "namespace «A B»\ntheorem foo : True := sorry\nend «A B»\n",
                "foo",
                ("unparsed declaration lines: 2", "namespace «A B»"),
            ),
            (
                "a _root_ component inside a namespace",
                "namespace N\ntheorem _root_.foo : True := sorry\nend N\n",
                "N._root_.foo",
                ("N._root_.foo", "_root_ component"),
            ),
            (
                "a _root_ name at the top level",
                "theorem _root_.foo : True := sorry\n",
                "_root_.foo",
                ("_root_.foo", "_root_ component"),
            ),
            (
                "the named theorem declared with lemma",
                "lemma l : True := sorry\n",
                "l",
                ("not a core Lean command", "lemma l : True := sorry"),
            ),
            (
                "another declaration made with lemma",
                "theorem t : True := sorry\n@[simp] lemma helper : True := trivial\n",
                "t",
                ("not a core Lean command", "@[simp] lemma helper : True := trivial"),
            ),
        )
        for label, text, theorem, details in refusals:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                source = _write_source(tmp, text)
                out = Path(tmp) / "out"
                payload = _freeze(source, out, theorems=(theorem,))
                self._assert_refused(payload, "theorem_not_declared", out)
                self.assertEqual(_kinds(payload), ["theorem_not_declared"], payload)
                for detail in details:
                    self.assertIn(detail, payload["findings"][0]["detail"], payload)

        accepted = (
            ("lemma inside a longer name", "theorem lemma_count : True := sorry\n", "lemma_count"),
            (
                "lemma in comments and a string",
                "-- lemma l : True\n/-\nlemma hidden : True := sorry\n-/\n"
                'theorem t : True := sorry\ndef s := "\nlemma quoted : True"\n',
                "t",
            ),
            ("_root_ inside a longer component", "theorem my_root_name : True := sorry\n", "my_root_name"),
        )
        for label, text, theorem in accepted:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                source = _write_source(tmp, text)
                payload = _freeze(source, Path(tmp) / "out", theorems=(theorem,))
                self.assertTrue(payload["ok"], payload)

        skill = SKILL_MD.read_text(encoding="utf-8")
        section = " ".join(skill.split("## Challenge freeze", 1)[1].split("\n## ", 1)[0].split())
        for phrase in (
            "a line the walk cannot read",
            "`_root_` component",
            "`lemma` is not a core Lean command",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)
        self.assertNotIn("public `theorem` or `lemma`", section)

    def test_the_compiler_trust_a_record_admits_is_documented(self) -> None:
        """N3S12-F6: only the three fixed names can never be accepted; a native_decide axiom can.

        Lean v4.35.0 declares no Lean.ofReduceBool, Lean.ofReduceNat or
        Lean.trustCompiler, but names each native_decide axiom after the
        declaration that uses it, so a challenge and a solution that declare
        the same helper export the same axiom.
        """

        skill = SKILL_MD.read_text(encoding="utf-8")
        section = " ".join(skill.split("## Challenge freeze", 1)[1].split("\n## ", 1)[0].split())
        for phrase in (
            "can never lead to an acceptance",
            "`<decl>._native.native_decide.ax_N`",
            "`mkAuxDeclName`",
            "through that helper is accepted",
            "really admits compiler trust",
            "the statement review must see it",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)
        self.assertNotIn("permits a compiler-trust axiom is therefore written", section)

    def test_an_import_the_toolchain_does_not_ship_is_refused(self) -> None:
        """OC-DS-2: every import is a module the pinned toolchain ships.

        Lean looks for ``import A.B`` at ``<toolchain>/lib/lean/A/B.olean``, so
        a challenge that imports a core-rooted module the toolchain does not
        ship can never build, and no comparator run against it gives a
        verdict. The fake toolchain ships Lean.Elab and Std.Data.HashMap; the
        shipped import beside each refused one shows that only the module
        the toolchain lacks is refused.
        """

        (self.toolchain / "lib" / "lean" / "Lean" / "Odd.olean").mkdir()
        for label, module in (
            ("a module the toolchain does not ship", "Lean.NoSuchModule"),
            ("a module file that is a directory", "Lean.Odd"),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                source = _write_source(tmp, f"import Lean.Elab\nimport {module}\n" + CHALLENGE)
                out = Path(tmp) / "out"
                payload = _freeze(source, out)
                self._assert_refused(payload, "challenge_import_not_trusted", out)
                self.assertEqual(
                    payload["findings"],
                    [
                        {
                            "kind": "challenge_import_not_trusted",
                            "detail": f"{module}: not shipped by {DEFAULT_TOOLCHAIN}",
                        }
                    ],
                )
        skill = SKILL_MD.read_text(encoding="utf-8")
        section = " ".join(skill.split("## Challenge freeze", 1)[1].split("\n## ", 1)[0].split())
        for phrase in ("`lib/lean/A/B.olean`", "`<module>: not shipped by <spec>`"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)

    @unittest.skipUnless(
        os.name == "posix", "POSIX umask and owner-only modes; Windows has no mode bits to check"
    )
    def test_output_modes_do_not_depend_on_the_umask(self) -> None:
        """OC-K-4: --out is 0700 and both files 0600 whatever the umask.

        The umask filters the modes os.mkdir and os.open create with. Under
        0o400 the files came out 0200, which the owner cannot read, and under
        0o277 a new --out came out 0500, so no file could be created in it.
        """

        for mask in (0o400, 0o277):
            for label in ("created out", "existing empty out"):
                with self.subTest(umask=oct(mask), case=label), tempfile.TemporaryDirectory() as tmp:
                    source = _write_source(tmp)
                    out = Path(tmp) / "out"
                    if label == "existing empty out":
                        out.mkdir()
                    previous = os.umask(mask)
                    try:
                        payload = _freeze(source, out)
                    finally:
                        os.umask(previous)
                    self.assertTrue(payload["ok"], payload)
                    self.assertEqual(stat.S_IMODE(os.lstat(out).st_mode), 0o700, out)
                    for name in ("Challenge.lean", "challenge.json"):
                        self.assertEqual(
                            stat.S_IMODE(os.lstat(out / name).st_mode), 0o600, out / name
                        )
        skill = SKILL_MD.read_text(encoding="utf-8")
        section = " ".join(skill.split("## Challenge freeze", 1)[1].split("\n## ", 1)[0].split())
        self.assertIn("whatever the umask", section)

    @unittest.skipUnless(
        os.name == "posix", "POSIX directory modes; Windows has no mode bits to restore"
    )
    def test_a_failed_freeze_gives_an_existing_out_its_mode_back(self) -> None:
        """OC-DS-7: a refusal leaves an existing empty --out as it found it, mode included.

        The freeze makes an existing --out 0700 before it writes. When a write
        fails, or the run crashes after the write, the files go and the
        directory gets its own mode back.
        """

        real_open = os.open

        def refuse_record(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            if Path(path).name == "challenge.json" and flags & os.O_CREAT:
                raise OSError(28, "No space left on device", str(path))
            return real_open(path, flags, *args, **kwargs)

        for label, target, name, effect, kind in (
            ("a write fails mid-way", gate.os, "open", refuse_record, "output_write_failed"),
            (
                "a crash after the write",
                gate,
                "scan_path",
                RuntimeError("injected"),
                "challenge_freeze_failed",
            ),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                source = _write_source(tmp)
                out = Path(tmp) / "out"
                out.mkdir()
                os.chmod(out, 0o755)
                with mock.patch.object(target, name, side_effect=effect):
                    payload = _freeze(source, out)
                self._assert_refused(payload, kind, out, before=[])
                self.assertEqual(stat.S_IMODE(os.lstat(out).st_mode), 0o755, payload)
        skill = SKILL_MD.read_text(encoding="utf-8")
        section = " ".join(skill.split("## Challenge freeze", 1)[1].split("\n## ", 1)[0].split())
        self.assertIn("an existing `--out` gets its mode back", section)

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
            self.assertEqual(
                record["toolchain"],
                {"spec": DEFAULT_TOOLCHAIN, "files": _toolchain_files(self.toolchain)},
            )
            self.assertEqual(payload["toolchain_dir"], os.path.realpath(self.toolchain))
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
            with self.subTest(case="a symlinked AAS_BWRAP"):
                alias = base / "alias" / "bwrap"
                borrowed = base / "borrowed" / "bubblewrap-0.13.0" / "bin" / "bwrap"
                alias.parent.mkdir()
                borrowed.parent.mkdir(parents=True)
                try:
                    alias.symlink_to(pinned)
                    borrowed.symlink_to(unversioned)
                except (OSError, NotImplementedError) as exc:
                    self.skipTest(f"this host cannot create a symlink: {exc}")
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
            else:
                with self.subTest(AAS_BWRAP="a file without the execute bit"):
                    self.skipTest("only POSIX has an execute bit to clear")
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
                link_problem = ""
            except (OSError, NotImplementedError) as exc:
                link_problem = f"this host cannot create a symlink: {exc}"
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
                    if label.startswith("a symlinked") and link_problem:
                        self.skipTest(link_problem)
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
            # Outside bin/: every file in bin/ is hashed, and this one changes per case.
            behaviour = Path(tmp) / "behaviour.json"
            scanner._fake_tool(
                bin_dir,
                "lean",
                "import json\n"
                "import os\n"
                "import sys\n"
                f"with open({str(record)!r}, 'w', encoding='utf-8') as handle:\n"
                "    json.dump({'env': dict(os.environ), 'cwd': os.getcwd(), 'argv': sys.argv[1:]}, handle)\n"
                f"with open({str(behaviour)!r}, encoding='utf-8') as handle:\n"
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
            # Every file in bin/ is hashed, the fake lean's script among them.
            expected_files = {
                f"bin/{name}": _sha256(bin_dir / name)
                for name in (*COMPARATOR_BINARIES, "fake_lean_impl.py")
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
            with self.subTest(case="a symlinked bin/"):
                try:
                    (linked / "bin").symlink_to(bin_dir, target_is_directory=True)
                except (OSError, NotImplementedError) as exc:
                    self.skipTest(f"this host cannot create a symlink: {exc}")
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
            with self.subTest(case="symlinked library"):
                try:
                    link.symlink_to(library / "libleanshared.so")
                except (OSError, NotImplementedError) as exc:
                    self.skipTest(f"this host cannot create a symlink: {exc}")
                linked = attest(banner)
                self.assertEqual(linked["status"], "tool_unavailable", linked)
                self.assertIn("libLake_shared.so", linked["detail"])
            if os.path.lexists(link):
                link.unlink()

            (bin_dir / "leanexport").unlink()
            with self.subTest(case="missing binary"):
                missing = attest(banner)
                self.assertEqual(missing["status"], "tool_unavailable", missing)
                self.assertIn("leanexport", missing["detail"])

    def test_the_attestation_hashes_every_file_in_bin_and_each_library(self) -> None:
        """N3S12-F7: bin/ is hashed whole, bundled kernels included, and never truncated.

        ``lean --version`` is stubbed, so this runs on every platform. The
        missing-binary case is a regression check: it was refused before.
        """

        banner = "Lean (version 4.35.0, x86_64-unknown-linux-gnu, commit 0123abc, Release)"
        lean_run = gate.BoundedCommandResult(0, banner + "\n", "", len(banner) + 1)

        def attest(root: Path) -> tuple[dict[str, Any], int]:
            with mock.patch.object(gate, "run_bounded_command", return_value=lean_run) as run:
                result = gate.toolchain_attestation(root)
            return result, run.call_count

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with self.subTest(case="every file in bin/ and each library"):
                root = _comparator_toolchain(base / "complete")
                attested, runs = attest(root)
                self.assertEqual(attested["status"], "ok", attested)
                self.assertEqual(attested["files"], _toolchain_files(root))
                self.assertEqual(runs, 1)

            with self.subTest(case="an edited bundled kernel"):
                root = _comparator_toolchain(base / "edited")
                before, _ = attest(root)
                kernel = root / "bin" / gate.executable_name("nanoda_bin")
                kernel.write_bytes(kernel.read_bytes() + b"# edited by the same user\n")
                after, _ = attest(root)
                self.assertEqual(after["status"], "ok", after)
                self.assertIn(f"bin/{kernel.name}", before["files"])
                self.assertEqual(
                    after["files"], {**before["files"], f"bin/{kernel.name}": _sha256(kernel)}
                )

            with self.subTest(case="64 entries in bin/"):
                root = _comparator_toolchain(base / "full")
                for index in range(64 - len(COMPARATOR_BINARIES + BUNDLED_KERNELS)):
                    (root / "bin" / f"tool{index}").write_bytes(f"tool {index}\n".encode("utf-8"))
                attested, _ = attest(root)
                self.assertEqual(attested["status"], "ok", attested)
                self.assertEqual(sum(key.startswith("bin/") for key in attested["files"]), 64)

            def a_directory(bin_dir: Path) -> str:
                (bin_dir / "share").mkdir()
                return "bin/share"

            def a_symlink(bin_dir: Path) -> str:
                kernel = bin_dir / gate.executable_name("nanoda_bin")
                target = base / "kernel-elsewhere"
                target.write_bytes(kernel.read_bytes())
                kernel.unlink()
                try:
                    kernel.symlink_to(target)
                except (OSError, NotImplementedError) as exc:
                    self.skipTest(f"this host cannot create a symlink: {exc}")
                return f"bin/{kernel.name}"

            def a_fifo(bin_dir: Path) -> str:
                if not hasattr(os, "mkfifo"):
                    self.skipTest("FIFOs are POSIX-only")
                os.mkfifo(bin_dir / "pipe")
                return "bin/pipe"

            def crowded(bin_dir: Path) -> str:
                for index in range(65 - len(COMPARATOR_BINARIES + BUNDLED_KERNELS)):
                    (bin_dir / f"tool{index}").write_bytes(f"tool {index}\n".encode("utf-8"))
                return "64"

            def missing(bin_dir: Path) -> str:
                (bin_dir / gate.executable_name("leanexport")).unlink()
                return "leanexport"

            for label, change in (
                ("a directory in bin/", a_directory),
                ("a symlink in bin/", a_symlink),
                ("a FIFO in bin/", a_fifo),
                ("65 entries in bin/", crowded),
                ("a missing comparator binary", missing),
            ):
                with self.subTest(case=label):
                    root = _comparator_toolchain(base / label.replace("/", "").replace(" ", "-"))
                    detail = change(root / "bin")
                    refused, runs = attest(root)
                    self.assertEqual(refused["status"], "tool_unavailable", refused)
                    self.assertEqual(refused["files"], {}, refused)
                    self.assertIn(detail, refused["detail"], refused)
                    self.assertEqual(runs, 0, "lean --version ran for a refused toolchain")

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
        # N3S12-F7: what the attestation hashes, and that it never truncates.
        readiness = " ".join(
            skill.split("## Comparator readiness", 1)[1].split("\n## ", 1)[0].split()
        )
        for phrase in (
            "every file directly in `bin/`",
            "`leanchecker-paranoid`",
            "`lean4lean`",
            "`nanoda_bin`",
            "`con-leche`",
            "`con-ron`",
            "more than 64 entries",
            "never truncates",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, readiness)
        self.assertNotIn("hashes those four binaries", readiness)


def _make_dir(path: Path) -> Path:
    path.mkdir()
    return path


def _make_bytes(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


if __name__ == "__main__":
    unittest.main()
