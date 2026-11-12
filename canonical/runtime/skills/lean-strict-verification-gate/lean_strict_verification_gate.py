#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, NamedTuple


# The three pattern tables below are frozen. They are an advisory preflight for
# `scan` and `verify`, not a security boundary: a Lean proof supports a claim
# only when a host-run `lake comparator` accepts it against a frozen challenge.
# Do not add patterns here; a newly found trick becomes a comparator regression
# case instead.
PLACEHOLDER_PATTERNS = {
    "sorry": re.compile(r"\bsorry\b"),
    "admit": re.compile(r"\badmit\b"),
    # sorryAx is the axiom `sorry` elaborates to; `exact sorryAx _ _` slips past
    # the \bsorry\b word boundary, so it needs its own pattern.
    "sorryAx": re.compile(r"\bsorryAx\b"),
}
TRUST_BASE_PATTERNS = {
    # Attributes, visibility, and other declaration modifiers may precede the
    # keyword, as may a same-line `... in` command prefix. `axiom` is reserved;
    # exclude only a qualified-name segment such as `Foo.axiom`.
    "axiom": re.compile(r"(?<![.\w])axiom\s+"),
    "unsafe": re.compile(r"\bunsafe\b"),
    # Proof-by-native-evaluation trusts the compiler and native code, not just
    # the kernel — a trust-base expansion the gate must surface.
    "native_decide": re.compile(r"\bnative_decide\b"),
    # Lean 4.33's DecideConfig documents these as spellings of the same
    # compiler-trusting tactic. Keep the option grammar narrow so a later local
    # named `native` is not mistaken for a decide option.
    "decide_native": re.compile(
        r"\bdecide\b(?:\s*[+-]\s*[A-Za-z_]\w*)*\s*\+\s*native\b"
        r"|\bdecide\b\s*\([^)]{0,512}\bnative\s*:=\s*true\b[^)]{0,512}\)",
        re.S,
    ),
    "ofReduceBool": re.compile(r"\bofReduceBool\b"),
    "ofReduceNat": re.compile(r"\bofReduceNat\b"),
    "trustCompiler": re.compile(r"\btrustCompiler\b"),
    "reduceBool": re.compile(r"\breduceBool\b"),
    "reduceNat": re.compile(r"\breduceNat\b"),
}
SAFETY_PATTERNS = {
    "#eval": re.compile(r"(^|[^\w])#eval\b"),
    "#exit": re.compile(r"(^|[^\w])#exit\b"),
    "#check_failure": re.compile(r"(^|[^\w])#check_failure\b"),
    "#guard_msgs": re.compile(r"(^|[^\w])#guard_msgs\b"),
    "IO.Process": re.compile(r"\bIO\.Process\b"),
    "run_cmd": re.compile(r"\brun_cmd\b"),
    # The builtin elaborator executes the body through unsafe TacticM eval.
    "run_tac": re.compile(r"\brun_tac\b"),
    # CommandElabM and TermElabM lift IO directly. Inline elaborators and
    # user-registered elaborator attributes therefore execute host effects
    # during an otherwise ordinary typecheck.
    "elab": re.compile(r"(?<![.\w])elab\b"),
    "elab_rules": re.compile(r"\belab_rules\b"),
    "by_elab": re.compile(r"\bby_elab\b"),
    "elaborator_attribute": re.compile(
        r"(?:@\s*\[|\battribute\s*\[)[^\]]{0,512}"
        r"\b(?:builtin_)?(?:command_elab|term_elab|tactic)\b"
    ),
    "initialize": re.compile(r"\b(?:builtin_)?initialize\b"),
    "@[extern]": re.compile(r"@\s*\[\s*extern\b"),
    # The leading \b must stay inside the alternatives: a shared one would
    # demand a word character before "@", so "@extern" at the start of a line
    # never matched.
    "foreign": re.compile(r"(?:\bforeign import\b|@[A-Za-z0-9_]*extern\b)"),
}
FORMAL_ARTIFACT_STAGES = {"intake", "stub", "candidate_solution", "final_candidate", "archived"}
RUNNERS = {"direct-lean", "lake-env-lean", "lake-build"}
PROJECT_EXCLUDED_DIRS = {".lake", ".git", "lake-packages"}
PROJECT_MAX_FILES = 2000
TREE_MAX_ENTRIES = 100_000
SOURCE_MAX_BYTES = 64 * 1024 * 1024
PROJECT_CONTEXT_MAX_BYTES = 64 * 1024 * 1024
COMPILED_MODULE_MAX_BYTES = 512 * 1024 * 1024
COMMAND_OUTPUT_MAX_BYTES = 16 * 1024 * 1024
PROJECT_CONTEXT_FILES = (
    "lakefile.lean",
    "lakefile.toml",
    "lean-toolchain",
    "lake-manifest.json",
)
TOOL_ENV = {
    "lean": "AAS_LEAN",
    "lake": "AAS_LAKE",
    "lean4checker": "AAS_LEAN4CHECKER",
    # Read only by bwrap_status(): the comparator's bwrap is never looked up
    # by tool_status() or on PATH.
    "bwrap": "AAS_BWRAP",
}
# The three axioms an ordinary mathlib proof reports. Anything else is a
# trust-base expansion the audit has to surface by name.
SANCTIONED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
# sorryAx is what `sorry` elaborates to: an --allow-axiom flag must never be
# able to sanction it, so it is refused ahead of the operator allowlist.
NEVER_SANCTIONED_AXIOMS = {"sorryAx"}
# Fixed names reported by older native-reduction paths. Lean 4.33's decide
# tactic instead emits declaration-local `_native.*.ax_*` names, matched below.
# Unlike sorryAx these mark a complete proof, so a project may knowingly accept
# them, but the result always remains compiler trust rather than kernel-only.
COMPILER_TRUST_AXIOMS = {
    "Lean.ofReduceBool",
    "Lean.ofReduceNat",
    "Lean.trustCompiler",
}
COMPILER_TRUST_AXIOM_PATTERNS = (
    re.compile(r"(?:^|\.)_native\.(?:decide|native_decide)\.ax_[A-Za-z0-9_]+$"),
)
AXIOM_AUDIT_MAX_DECLARATIONS = 500
AXIOM_AUDIT_MAX_MODULES = 500
# Unparsed lines are named in the report, but a pathological file must not be
# able to grow the payload without bound; the count past the cap is summarized.
AXIOM_AUDIT_MAX_UNPARSED = 20
KERNEL_CHECK_MAX_MODULES = 50
# A lakefile is a build script, not a library module: importing it into the
# audit harness would be a build-time error, never a proof dependency.
NON_MODULE_STEMS = {"lakefile"}
SOURCE_UNREADABLE_PREFIX = "__AAS_SOURCE_UNREADABLE__:"
# `lake comparator` ships with Lean from v4.35.0. An older release, a release
# candidate or a nightly gives no verdict.
COMPARATOR_MIN_LEAN_VERSION = (4, 35, 0)
DEFAULT_COMPARATOR_TOOLCHAIN = "leanprover/lean4:v4.35.0"
# A frozen challenge is the record below plus a byte-for-byte copy of the
# reviewed statement file. CHALLENGE_MODULE is the module name the comparator
# workspace gives that copy.
CHALLENGE_RECORD_SCHEMA = "aas_lean_challenge.v1"
CHALLENGE_MODULE = "AASChallenge"
CHALLENGE_FILE = "Challenge.lean"
CHALLENGE_RECORD_FILE = "challenge.json"
CHALLENGE_SOURCE_MAX_BYTES = 1024 * 1024
# The toolchain's own libraries: the only imports a core challenge may use.
CORE_IMPORT_ROOTS = ("Init", "Lake", "Lean", "Std")
CHALLENGE_RECORD_KEYS = frozenset(
    {
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
)
# `leanprover/lean4:vX.Y.Z` names one stable release, and a stable release's
# `lean --version` banner starts `Lean (version X.Y.Z, `. The digits are ASCII
# on purpose: \d also matches other scripts' digits, which int() accepts.
_LEAN_TOOLCHAIN_SPEC_RE = re.compile(
    r"leanprover/lean4:v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
)
_LEAN_VERSION_BANNER_RE = re.compile(
    r"Lean \(version (0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*), "
)
# The comparator's sandbox: bubblewrap 0.12.0 or later, installed outside PATH
# as ~/.local/opt/bubblewrap-X.Y.Z/bin/bwrap or named by AAS_BWRAP.
BWRAP_MIN_VERSION = (0, 12, 0)
_BWRAP_DIRECTORY_RE = re.compile(
    r"bubblewrap-(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
)
_BWRAP_VERSION_RE = re.compile(
    r"bubblewrap (0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
)
# An absolute toolchain directory that replaces $ELAN_HOME/toolchains/<name>.
COMPARATOR_TOOLCHAIN_ENV = "AAS_LEAN_COMPARATOR_TOOLCHAIN"
# lake comparator drives the run with lake and lean, and exports and replays
# both environments with the same toolchain's leanexport and leanchecker.
COMPARATOR_TOOLCHAIN_BINARIES = ("lake", "lean", "leanchecker", "leanexport")
# The attestation hashes every file in bin/, the kernels that --paranoid runs
# among them; a bin/ with more entries is refused, not truncated.
TOOLCHAIN_BIN_MAX_FILES = 64
# bin/lean and bin/lake are launchers for lib/lean/*.so, so the attestation
# hashes those libraries too; a toolchain with more is refused, not truncated.
TOOLCHAIN_LIBRARY_MAX_FILES = 32
READINESS_OUTPUT_MAX_BYTES = 64 * 1024
# The limit on the attestation's `lean --version`. With the toolchain's files
# dropped from the page cache, Lean v4.35.0 took at most 0.73 s to answer; the
# limit is at least three times the slowest cold start, rounded up, and never
# below 5 s.
TOOLCHAIN_ATTESTATION_TIMEOUT_SECONDS = 5
# Elan stores leanprover/lean4:<release> as toolchains/leanprover--lean4---<release>.
_ELAN_LEAN4_PREFIX = "leanprover--lean4---"
# The comparator verb's defaults come from the Mathlib spike: 300 s for a
# core run, and a 10 GiB memory limit, about 1.5 times the largest cold peak
# it measured.
COMPARATOR_DEFAULT_TIMEOUT = 300
COMPARATOR_DEFAULT_MEMORY_LIMIT = "10G"
CHALLENGE_RECORD_MAX_BYTES = 1024 * 1024
# The module the comparator workspace generates to import the solution's
# named modules.
COMPARATOR_SOLUTION_MODULE = "AASSolution"
# Module names the workspace owns: its two generated modules and lakefile,
# because a copied lakefile.lean would win over the generated lakefile.toml.
# A solution module may not be one, nor lie below the first two.
COMPARATOR_RESERVED_MODULES = (CHALLENGE_MODULE, COMPARATOR_SOLUTION_MODULE, "lakefile")
# The solution files one run copies, within the remote formal lane's budget.
COMPARATOR_SOLUTION_MAX_FILES = 240
COMPARATOR_SOLUTION_MAX_TOTAL_BYTES = 15 * 1024 * 1024
COMPARATOR_SOLUTION_MAX_FILE_BYTES = 16 * 1024 * 1024
# The kernels bundled with Lean that `lake comparator --paranoid` runs, in
# its order, before Lean's own leanchecker.
COMPARATOR_PARANOID_KERNELS = (
    "leanchecker-paranoid",
    "lean4lean",
    "nanoda_bin",
    "con-leche",
    "con-ron",
)
COMPARATOR_PAYLOAD_KEYS = frozenset(
    {
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
)
COMPARATOR_LIMITATIONS = (
    "the comparator decides statement match, axiom use and kernel acceptance; whether "
    "the challenge states the intended claim is still a review step",
    "statement comparison is exact: an equivalent statement written differently is rejected",
    "a process running as the same user can change the toolchain, bwrap, the gate or a "
    "receipt; the digests detect a change made during a run, not one made before the "
    "freeze or after the receipt",
    "the solution's own build directory is writable inside the sandbox, so a solution can "
    "fill the disk; the disk floor and cleanup bound this only after the fact",
    "core profile: imports outside Init, Std, Lean and Lake are refused",
)
# The comparator's sandbox wrapper logs each step it runs or refuses as one
# line: a JSON object with exactly these keys. The log is capped in lines and
# bytes; COMPARATOR_WRAPPER_SOURCE keeps its own copy of the keys and the line
# cap, since it runs on its own.
COMPARATOR_PHASE_LOG_KEYS = frozenset({"phase", "target", "rc", "seconds", "dropped", "refused"})
COMPARATOR_PHASE_LOG_MAX_LINES = 64
COMPARATOR_PHASE_LOG_MAX_BYTES = 64 * 1024
# The only reasons a comparator run rejects a solution. Every other failure,
# including a message the gate does not know, is no verdict.
COMPARATOR_REJECTION_KINDS = (
    "statement_mismatch",
    "kind_mismatch",
    "constant_mismatch",
    "missing_constant",
    "forbidden_axiom",
    "kernel_rejected",
    "build_failed",
)
# The name lake comparator gives each kernel in its messages, by executable.
COMPARATOR_KERNEL_NAMES = {
    "leanchecker": "Lean default",
    "leanchecker-paranoid": "Lean paranoid",
    "lean4lean": "lean4lean",
    "nanoda_bin": "nanoda",
    "con-leche": "con-leche",
    "con-ron": "con-ron",
}
# A comparator run starts only with this much space free where its state lies.
COMPARATOR_MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024
# The host programs a comparator run uses, by absolute path: systemd-run puts
# the judge in a memory-limited user scope and systemctl stops that scope, nice
# lowers the judge's priority, env gives it its whole environment, and python3
# runs the sandbox wrapper. PATH is never searched for them.
COMPARATOR_SYSTEMD_RUN = "/usr/bin/systemd-run"
COMPARATOR_SYSTEMCTL = "/usr/bin/systemctl"
COMPARATOR_NICE = "/usr/bin/nice"
COMPARATOR_ENV = "/usr/bin/env"
COMPARATOR_WRAPPER_INTERPRETER = "/usr/bin/python3"
# The limit on lake resolve-deps, which writes the workspace's manifest from the
# generated lakefile before any byte of the solution is copied.
COMPARATOR_RESOLVE_TIMEOUT = 60
# One comparator run at a time on a host: a run waits for the lock for its
# timeout, and never longer than this.
COMPARATOR_LOCK_MAX_WAIT = 600
COMPARATOR_RECEIPT_SCHEMA = "host_comparator_receipt.v1"
# The umask of the processes a comparator run starts, lake resolve-deps and
# the judge, whatever the gate's own umask is. Lake makes the workspace's
# .lake/build under the umask it inherits; under one without the owner's write
# bit, such as 0277, it could not write there, and every run ended without a
# verdict.
COMPARATOR_UMASK = 0o077


class BoundedCommandResult(tuple):
    """Preserve the four-field runner tuple while attaching optional evidence."""

    def __new__(cls, returncode: int, stdout: str, stderr: str, output_bytes: int,
                process_evidence: dict[str, Any] | None = None):
        result = super().__new__(cls, (returncode, stdout, stderr, output_bytes))
        result.process_evidence = process_evidence
        return result

    returncode = property(lambda self: self[0])
    stdout = property(lambda self: self[1])
    stderr = property(lambda self: self[2])
    output_bytes = property(lambda self: self[3])


class CommandOutputLimitExceeded(Exception):
    """A child produced more output than the gate can safely retain."""

    def __init__(self, limit: int, stdout: str, stderr: str):
        super().__init__(f"combined stdout/stderr exceeded {limit} bytes")
        self.limit = limit
        self.stdout = stdout
        self.stderr = stderr


def positive_timeout(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def valid_lean_name(value: str, *, allow_root_prefix: bool = False) -> bool:
    """Conservative Unicode Lean identifier validation for generated argv/source."""

    if not value or len(value) > 512:
        return False
    parts = value.split(".")
    if allow_root_prefix and parts and parts[0] == "_root_":
        parts = parts[1:]
    if not parts or any(not part for part in parts):
        return False

    def initial(character: str) -> bool:
        return character == "_" or unicodedata.category(character).startswith("L")

    def continuation(character: str) -> bool:
        category = unicodedata.category(character)
        return initial(character) or category.startswith(("M", "N")) or character in "'!?"

    return all(initial(part[0]) and all(continuation(char) for char in part[1:]) for part in parts)


def is_compiler_trust_axiom(name: str) -> bool:
    return name in COMPILER_TRUST_AXIOMS or any(
        pattern.search(name) for pattern in COMPILER_TRUST_AXIOM_PATTERNS
    )


def parse_lean_version(value: object) -> tuple[int, int, int] | None:
    """(major, minor, patch) of one stable Lean release, or None.

    Accepts exactly a ``leanprover/lean4:vX.Y.Z`` toolchain spec or a
    ``lean --version`` banner that starts ``Lean (version X.Y.Z, ``. Release
    candidates, nightlies, leading zeros and non-strings give None. A release
    is supported for ``lake comparator`` when its triple is at least
    COMPARATOR_MIN_LEAN_VERSION.
    """

    if not isinstance(value, str):
        return None
    match = _LEAN_TOOLCHAIN_SPEC_RE.fullmatch(value) or _LEAN_VERSION_BANNER_RE.match(value)
    if match is None:
        return None
    try:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:  # a component past sys.get_int_max_str_digits()
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lean-strict-verification-gate")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--project-root")
    doctor.add_argument("--probe", action="store_true", help="run non-installing version/toolchain probes")

    scan = sub.add_parser("scan")
    add_scan_args(scan)

    verify = sub.add_parser("verify")
    add_scan_args(verify)
    verify.add_argument("--typecheck", action="store_true")
    verify.add_argument(
        "--timeout",
        type=positive_timeout,
        default=600,
        help="typecheck wall limit in seconds (default 600; mathlib-importing builds need minutes, not 20s)",
    )
    verify.add_argument("--runner", choices=sorted(RUNNERS), default="direct-lean")
    verify.add_argument("--project-root")
    verify.add_argument(
        "--strict",
        action="store_true",
        help="require a passing typecheck for exit 0: 'typecheck never ran' is a failure (implies --typecheck)",
    )

    audit = sub.add_parser(
        "axiom-audit",
        help="report the axioms each theorem actually depends on (#print axioms)",
    )
    audit.add_argument("--input", required=True, help="project directory to audit")
    audit.add_argument("--project-root", help="lake workspace root (defaults to --input)")
    audit.add_argument(
        "--declaration",
        action="append",
        default=[],
        help="fully qualified declaration to audit; repeatable (default: discovered by scan)",
    )
    audit.add_argument(
        "--import",
        dest="import_module",
        action="append",
        default=[],
        help="module the harness imports; repeatable (default: derived from the project)",
    )
    audit.add_argument(
        "--allow-axiom",
        action="append",
        default=[],
        help=(
            f"extra sanctioned axiom; repeatable. {sorted(NEVER_SANCTIONED_AXIOMS)} can never be "
            f"allowed, and {sorted(COMPILER_TRUST_AXIOMS)} stay reported in compiler_trust_axioms "
            "even once allowed"
        ),
    )
    audit.add_argument("--timeout", type=positive_timeout, default=600)
    audit.add_argument(
        "--strict",
        action="store_true",
        help="require a completed audit for exit 0: 'audit never ran' is a failure",
    )

    kernel = sub.add_parser(
        "kernel-check",
        help="replay compiled modules through the kernel with lean4checker or the toolchain's leanchecker",
    )
    kernel.add_argument("--input", required=True, help="project directory to re-check")
    kernel.add_argument("--project-root", help="lake workspace root (defaults to --input)")
    kernel.add_argument(
        "--module",
        action="append",
        default=[],
        help="module to replay; repeatable (default: derived from the project)",
    )
    kernel.add_argument("--timeout", type=positive_timeout, default=600)
    kernel.add_argument(
        "--strict",
        action="store_true",
        help="require a completed kernel replay for exit 0: a missing checker is a failure",
    )

    freeze = sub.add_parser(
        "challenge-freeze",
        help="freeze a reviewed Lean statement as the challenge lake comparator judges against",
    )
    freeze.add_argument("--source", required=True, help="Lean file holding the reviewed statements")
    freeze.add_argument(
        "--theorem",
        action="append",
        required=True,
        help="fully qualified theorem a solution must prove; repeatable",
    )
    freeze.add_argument(
        "--definition",
        action="append",
        default=[],
        help="definition hole; repeatable, and always refused: a challenge names theorems only",
    )
    freeze.add_argument(
        "--permitted-axiom",
        action="append",
        default=[],
        help=(
            f"axiom a solution may use; repeatable, and replaces the default {sorted(SANCTIONED_AXIOMS)}. "
            f"{sorted(NEVER_SANCTIONED_AXIOMS)} is never permitted"
        ),
    )
    freeze.add_argument(
        "--allow-compiler-trust",
        action="store_true",
        help="let --permitted-axiom name a compiler-trust axiom such as Lean.ofReduceBool",
    )
    freeze.add_argument(
        "--paranoid",
        action="store_true",
        help="record that the comparator also runs every external kernel bundled with Lean",
    )
    freeze.add_argument(
        "--toolchain",
        default=DEFAULT_COMPARATOR_TOOLCHAIN,
        help=(
            "installed stable Lean release to judge with, v4.35.0 or later; the record pins "
            f"its bin/ and lib/lean/*.so bytes (default {DEFAULT_COMPARATOR_TOOLCHAIN})"
        ),
    )
    freeze.add_argument(
        "--out",
        required=True,
        help="directory for Challenge.lean and challenge.json: absent under an existing parent, or empty",
    )

    comparator = sub.add_parser(
        "comparator",
        help="judge a solution project with lake comparator against a frozen challenge; Linux only",
    )
    comparator.add_argument(
        "--input", required=True, help="solution project directory, not a symlink"
    )
    comparator.add_argument(
        "--challenge-record",
        required=True,
        help="challenge.json from challenge-freeze, outside --input, with Challenge.lean beside it",
    )
    comparator.add_argument(
        "--challenge-record-sha256",
        required=True,
        type=sha256_argument,
        help="the record_sha256 challenge-freeze reported: 64 lowercase hex digits",
    )
    comparator.add_argument(
        "--solution-module",
        action="append",
        required=True,
        help="module of --input that declares the record's theorems; repeatable",
    )
    comparator.add_argument(
        "--timeout",
        type=positive_timeout,
        default=COMPARATOR_DEFAULT_TIMEOUT,
        help=f"wall limit of the run in seconds (default {COMPARATOR_DEFAULT_TIMEOUT})",
    )
    comparator.add_argument(
        "--paranoid",
        action="store_true",
        help=(
            "also run every external kernel bundled with Lean; a record frozen with "
            "--paranoid asks for this too"
        ),
    )
    comparator.add_argument(
        "--keep-workspace", action="store_true", help="keep the run's directory"
    )
    comparator.add_argument(
        "--memory-limit",
        type=memory_limit_argument,
        default=COMPARATOR_DEFAULT_MEMORY_LIMIT,
        help=(
            "memory limit of the run: a positive whole number with K, M or G, or off "
            f"(default {COMPARATOR_DEFAULT_MEMORY_LIMIT})"
        ),
    )

    args = parser.parse_args(argv)
    if args.command == "doctor":
        emit(doctor_payload(project_root=Path(args.project_root) if args.project_root else Path.cwd(), probe=args.probe))
        return 0
    if args.command == "scan":
        payload = scan_input(Path(args.input), args.artifact_stage, set(args.allow_import or []))
        emit(payload)
        return 0 if payload["ok"] else 1
    if args.command == "verify":
        input_path = Path(args.input)
        strict = bool(args.strict)
        want_typecheck = bool(args.typecheck) or strict
        payload = scan_input(input_path, args.artifact_stage, set(args.allow_import or []))
        payload["lean_check_status"] = "not_run"
        payload["strict"] = strict
        if payload["ok"] and want_typecheck:
            payload.setdefault("limitations", []).extend(
                [
                    "pre/post hashes detect endpoint differences, not a change-and-restore between snapshots",
                    "Lean, Lake, their launchers, environment, and dependency resolution are trusted inputs, not content-attested",
                ]
            )
            runner = args.runner
            project_root = Path(args.project_root) if args.project_root else None
            if input_path.is_dir():
                # A whole project can only be typechecked by building it.
                runner = "lake-build"
                if project_root is None:
                    project_root = input_path
                elif project_root.expanduser().resolve() != input_path.expanduser().resolve():
                    payload["ok"] = False
                    payload["safety_status"] = "failed"
                    payload["lean_check_status"] = "command_failed"
                    payload.setdefault("findings", []).append(
                        {
                            "kind": "runner_input_mismatch",
                            "detail": "directory input and --project-root must identify the same project",
                        }
                    )
            elif runner == "lake-build":
                payload["ok"] = False
                payload["safety_status"] = "failed"
                payload["lean_check_status"] = "command_failed"
                payload.setdefault("findings", []).append(
                    {
                        "kind": "runner_input_mismatch",
                        "detail": "lake-build can certify only a directory input; use lake-env-lean for one file",
                    }
                )
            context_before: dict[str, Any] | None = None
            if payload["ok"] and runner in {"lake-build", "lake-env-lean"} and project_root is not None:
                supplied_root = project_root.expanduser()
                if supplied_root.is_symlink():
                    payload["ok"] = False
                    payload["safety_status"] = "failed"
                    payload["lean_check_status"] = "command_failed"
                    payload.setdefault("findings", []).append(
                        {
                            "kind": "unstable_project_context",
                            "detail": "--project-root must not be a symlink",
                        }
                    )
                else:
                    context_before = project_context_snapshot(supplied_root)
                    payload["verification_project_context"] = context_before["files"]
                    payload["verification_project_context_fingerprint"] = context_before[
                        "fingerprint"
                    ]
                    if context_before["errors"]:
                        payload["ok"] = False
                        payload["safety_status"] = "failed"
                        payload["lean_check_status"] = "command_failed"
                        payload.setdefault("findings", []).extend(
                            {
                                "kind": "unstable_project_context",
                                "detail": detail,
                            }
                            for detail in context_before["errors"]
                        )
            if payload["ok"]:
                before_fingerprint = scan_fingerprint(payload)
                payload.update(typecheck(
                    input_path,
                    timeout=args.timeout,
                    runner=runner,
                    project_root=project_root,
                ))
                after_scan = scan_input(
                    input_path,
                    args.artifact_stage,
                    set(args.allow_import or []),
                )
                after_fingerprint = scan_fingerprint(after_scan)
                payload["verification_input_fingerprint"] = before_fingerprint
                payload["post_verification_input_fingerprint"] = after_fingerprint
                if before_fingerprint != after_fingerprint:
                    payload["ok"] = False
                    payload["safety_status"] = "failed"
                    payload.setdefault("findings", []).append(
                        {
                            "kind": "input_changed_during_verification",
                            "detail": "the scanned input changed before typecheck evidence was finalized",
                        }
                    )
                if context_before is not None and project_root is not None:
                    context_after = project_context_snapshot(project_root.expanduser())
                    payload["post_verification_project_context"] = context_after["files"]
                    payload["post_verification_project_context_fingerprint"] = context_after[
                        "fingerprint"
                    ]
                    context_stable, context_transition = (
                        accepted_project_context_transition(
                            context_before,
                            context_after,
                            runner=runner,
                        )
                    )
                    if context_after["errors"] or not context_stable:
                        payload["ok"] = False
                        payload["safety_status"] = "failed"
                        payload.setdefault("findings", []).append(
                            {
                                "kind": "project_context_changed_during_verification",
                                "detail": (
                                    "Lake configuration or toolchain selection changed before "
                                    "typecheck evidence was finalized"
                                ),
                            }
                        )
                    elif context_transition:
                        payload["project_context_transition"] = context_transition
        emit(payload)
        if strict:
            return 0 if payload["ok"] and payload.get("lean_check_status") == "typechecked" else 1
        return 0 if payload["ok"] and payload.get("lean_check_status") not in {"typecheck_failed", "command_failed"} else 1
    if args.command == "axiom-audit":
        input_path = Path(args.input)
        payload = axiom_audit_payload(
            input_path,
            project_root=Path(args.project_root) if args.project_root else input_path,
            timeout=args.timeout,
            declarations=list(args.declaration),
            imports=list(args.import_module),
            allowed_axioms=set(args.allow_axiom),
            strict=bool(args.strict),
        )
        emit(payload)
        if args.strict:
            return 0 if payload["ok"] and payload["axiom_audit_status"] == "audited" else 1
        return 0 if payload["ok"] and payload["axiom_audit_status"] != "command_failed" else 1
    if args.command == "kernel-check":
        input_path = Path(args.input)
        payload = kernel_check_payload(
            input_path,
            project_root=Path(args.project_root) if args.project_root else input_path,
            timeout=args.timeout,
            modules=list(args.module),
            strict=bool(args.strict),
        )
        emit(payload)
        if args.strict:
            return 0 if payload["ok"] and payload["kernel_check_status"] == "kernel_checked" else 1
        return 0 if payload["ok"] and payload["kernel_check_status"] != "command_failed" else 1
    if args.command == "challenge-freeze":
        payload = challenge_freeze_payload(
            Path(args.source),
            out_dir=Path(args.out),
            theorems=list(args.theorem),
            definitions=list(args.definition),
            permitted_axioms=list(args.permitted_axiom),
            allow_compiler_trust=bool(args.allow_compiler_trust),
            paranoid=bool(args.paranoid),
            toolchain=args.toolchain,
        )
        emit(payload)
        return 0 if payload["ok"] else 1
    if args.command == "comparator":
        payload = comparator_payload(
            Path(args.input),
            challenge_record=Path(args.challenge_record),
            challenge_record_sha256=args.challenge_record_sha256,
            solution_modules=list(args.solution_module),
            timeout=args.timeout,
            paranoid=bool(args.paranoid),
            keep_workspace=bool(args.keep_workspace),
            memory_limit=args.memory_limit,
        )
        emit(payload)
        # Only accepted is a verdict a caller may build on; rejected and
        # every no verdict exit 1.
        return 0 if payload["ok"] and payload["comparator_status"] == "accepted" else 1
    raise AssertionError(args.command)


def add_scan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", required=True)
    parser.add_argument("--artifact-stage", choices=sorted(FORMAL_ARTIFACT_STAGES), default="final_candidate")
    parser.add_argument("--allow-import", action="append", default=[])


def doctor_payload(*, project_root: Path, probe: bool) -> dict[str, Any]:
    # Reported so an operator can see whether AAS_LEAN4CHECKER or PATH gives
    # kernel-check a checker. The toolchain's leanchecker is found only by
    # kernel-check, through `lake env`, which doctor never runs because it
    # loads the project's Lake configuration.
    checker = lean4checker_status()
    if checker["source"] == "not-found":
        checker["detail"] = (
            "neither AAS_LEAN4CHECKER nor PATH gives a checker; kernel-check "
            "resolves the toolchain's leanchecker through lake env at run time"
        )
    payload = {
        "status": "ok",
        "tool_status": {
            "lean": tool_status("lean"),
            "lake": tool_status("lake"),
            "elan": tool_status("elan"),
            "lean4checker": checker,
            "npm": tool_status("npm"),
            "npx": tool_status("npx"),
            "pip": tool_status("pip"),
        },
        "project_status": project_status(project_root),
        "no_auto_install": True,
        "network_required": False,
        "installs_attempted": False,
        "comparator": comparator_readiness(probe=probe),
    }
    if probe:
        payload["probe_status"] = probe_status(payload["tool_status"])
    return payload


def tool_status(name: str) -> dict[str, Any]:
    env_var = TOOL_ENV.get(name)
    if env_var:
        env_value = os.environ.get(env_var, "").strip()
        if env_value:
            resolved = resolve_candidate(env_value)
            return {
                "status": "available" if resolved else "tool_unavailable",
                "path": resolved or env_value,
                "source": "env",
                "env_var": env_var,
            }
    path = resolve_candidate(name)
    if path:
        return {"status": "available", "path": path, "source": "path"}
    elan_candidate = Path.home() / ".elan" / "bin" / executable_name(name)
    if elan_candidate.is_file():
        return {"status": "available", "path": str(elan_candidate), "source": "elan-home"}
    return {"status": "tool_unavailable", "path": "", "source": "not-found"}


def executable_name(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def resolve_candidate(candidate: str) -> str:
    expanded = str(Path(candidate).expanduser())
    if any(sep in candidate for sep in ("/", "\\")):
        found = expanded
    else:
        found = shutil.which(candidate) or ""
    if not found:
        return ""
    try:
        path = Path(found).resolve(strict=True)
    except (OSError, RuntimeError):
        return ""
    return str(path) if path.is_file() else ""


def project_status(root: Path) -> dict[str, Any]:
    root = root.expanduser()
    lakefile = first_existing(root, ("lakefile.lean", "lakefile.toml"))
    lean_toolchain = root / "lean-toolchain"
    lake_manifest = root / "lake-manifest.json"
    lake_dir = root / ".lake"
    return {
        "root": str(root),
        "lake_workspace_detected": bool(lakefile),
        "lakefile": str(lakefile) if lakefile else "",
        "lean_toolchain": str(lean_toolchain) if lean_toolchain.is_file() else "",
        "lake_manifest": str(lake_manifest) if lake_manifest.is_file() else "",
        "lake_dir": str(lake_dir) if lake_dir.is_dir() else "",
        "cache_status": "observed_only",
    }


def first_existing(root: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        path = root / name
        if path.is_file():
            return path
    return None


def probe_status(tools: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "lean_version": probe_command(tools.get("lean", {}).get("path", ""), ["--version"]),
        "lake_version": probe_command(tools.get("lake", {}).get("path", ""), ["--version"]),
        "elan_show": probe_command(tools.get("elan", {}).get("path", ""), ["show"]),
        "limitations": [
            "version probes execute local tools but do not install dependencies",
            "cache and mathlib readiness are not proven by these probes",
        ],
    }


def probe_command(executable: str, args: list[str]) -> dict[str, str]:
    if not executable:
        return {"status": "tool_unavailable", "stdout": "", "stderr": ""}
    try:
        completed = run_bounded_command(
            [executable, *args],
            timeout=5,
            max_output_bytes=COMMAND_OUTPUT_MAX_BYTES,
        )
    except subprocess.TimeoutExpired:
        return {"status": "command_failed", "stdout": "", "stderr": "timeout after 5 seconds"}
    except CommandOutputLimitExceeded as exc:
        return {
            "status": "command_failed",
            "stdout": exc.stdout[-2000:],
            "stderr": (exc.stderr + "\n" + str(exc))[-2000:],
        }
    except OSError as exc:
        return {"status": "command_failed", "stdout": "", "stderr": str(exc)}
    return {
        "status": "ok" if completed.returncode == 0 else "command_failed",
        "stdout": completed.stdout[-2000:],
        "stderr": completed.stderr[-2000:],
    }


def comparator_platform_supported() -> bool:
    """Whether this host can run ``lake comparator`` for AAS.

    The comparator sandboxes the code it judges with Linux namespaces, and AAS
    never runs it as root. Every other host gives no verdict.
    """

    geteuid = getattr(os, "geteuid", None)
    return sys.platform == "linux" and geteuid is not None and geteuid() != 0


def comparator_readiness(*, probe: bool) -> dict[str, Any]:
    """The doctor's ``comparator`` section, read from directory listings.

    Without ``probe`` nothing runs. With it, ``bwrap --version`` decides the
    bwrap version, and a supported bwrap gets the sandbox preflight. An
    unexpected error lands in ``error`` and never fails the doctor.
    """

    readiness: dict[str, Any] = {}
    try:
        readiness["platform_supported"] = comparator_platform_supported()
        readiness["bwrap"] = bwrap_status(probe=probe)
        readiness.update(_comparator_toolchain_rows())
        if probe:
            bwrap = readiness["bwrap"]
            if bwrap["status"] == "available":
                readiness["sandbox_probe"] = bwrap_preflight(bwrap["path"])
            else:
                readiness["sandbox_probe"] = {
                    "status": "tool_unavailable",
                    "command": [],
                    "returncode": None,
                    "stderr": "",
                    "detail": "no supported bwrap to probe: " + str(bwrap["detail"]),
                }
    except Exception as exc:  # readiness is a report; the doctor still succeeds
        readiness["error"] = f"{type(exc).__name__}: {exc}"[:500]
    return readiness


def bwrap_status(*, probe: bool = False) -> dict[str, Any]:
    """Find the bwrap ``lake comparator`` may use, never through PATH.

    AAS_BWRAP, when set, must name an executable regular file by absolute
    path, and nothing else is tried. Otherwise the highest
    ``~/.local/opt/bubblewrap-X.Y.Z/bin/bwrap`` is chosen. ``path`` is the
    resolved file. Without ``probe`` its version comes from the
    ``bubblewrap-X.Y.Z`` directory and nothing runs; with ``probe``,
    ``bwrap --version`` decides. Only bubblewrap 0.12.0 or later is
    ``available``. Never raises.
    """

    env_var = TOOL_ENV["bwrap"]
    result: dict[str, Any] = {
        "status": "tool_unavailable",
        "path": "",
        "source": "not-found",
        "version": None,
        "version_source": None,
        "supported": False,
        "detail": "",
    }
    try:
        configured = os.environ.get(env_var, "").strip()
        if configured:
            result.update(source="env", path=configured)
            if not os.path.isabs(configured):
                result["detail"] = f"{env_var} must be an absolute path; PATH is never searched"
                return result
            candidate = configured
        else:
            candidate = _newest_local_bwrap()
            if not candidate:
                result["detail"] = (
                    f"no ~/.local/opt/bubblewrap-X.Y.Z/bin/bwrap; set {env_var} to an absolute path"
                )
                return result
            result["source"] = "local-opt"
        resolved = os.path.realpath(candidate)
        result["path"] = resolved
        problem = _executable_file_problem(resolved)
        if problem:
            result["detail"] = problem
            return result
        if probe:
            probed = probe_command(resolved, ["--version"])
            match = (
                _BWRAP_VERSION_RE.fullmatch(probed["stdout"].strip())
                if probed["status"] == "ok"
                else None
            )
            version = _version_triple(match)
            version_source = "probe"
            if version is None:
                output = (probed["stderr"] or probed["stdout"]).strip()
                result["detail"] = (
                    "bwrap --version did not report bubblewrap X.Y.Z: " + output[-200:]
                )
        else:
            version = _bwrap_install_version(resolved)
            version_source = "directory"
            if version is None:
                result["detail"] = (
                    "the version is unknown: the path is not .../bubblewrap-X.Y.Z/bin/bwrap; "
                    "doctor --probe runs bwrap --version"
                )
        if version is not None:
            result.update(version=_version_text(version), version_source=version_source)
            if version < BWRAP_MIN_VERSION:
                result["detail"] = (
                    f"bubblewrap {_version_text(version)} is older than "
                    f"{_version_text(BWRAP_MIN_VERSION)}"
                )
            else:
                result.update(status="available", supported=True)
    except (OSError, RuntimeError, ValueError) as exc:
        result.update(
            status="tool_unavailable",
            supported=False,
            detail=f"{type(exc).__name__}: {exc}"[:500],
        )
    return result


def bwrap_preflight(path: str, *, timeout: float = 5) -> dict[str, Any]:
    """Start the sandbox once with every namespace unshared.

    The argv is fixed, ``<path> --ro-bind / / --unshare-all --die-with-parent
    -- /bin/true``; PATH=/usr/bin:/bin is the whole environment and the output
    is capped at 64 KiB. Nothing is spawned unless
    comparator_platform_supported() holds and ``path`` is an absolute,
    executable regular file. A ``path`` that is neither a str nor an
    os.PathLike is ``tool_unavailable`` with the detail ``invalid path
    argument``. ``ok`` means the sandbox started; a non-zero exit, a timeout
    or a failure to run is ``command_failed``. Never raises.
    """

    command = [path, "--ro-bind", "/", "/", "--unshare-all", "--die-with-parent", "--", "/bin/true"]
    result: dict[str, Any] = {
        "status": "tool_unavailable",
        "command": command,
        "returncode": None,
        "stderr": "",
        "detail": "",
    }
    try:
        if not isinstance(path, (str, os.PathLike)):
            result["detail"] = "invalid path argument"
            return result
        if not comparator_platform_supported():
            result["detail"] = "the comparator sandbox runs only on Linux, and never as root"
            return result
        if not os.path.isabs(path):
            result["detail"] = "bwrap must be named by an absolute path; PATH is never searched"
            return result
        problem = _executable_file_problem(path)
        if problem:
            result["detail"] = problem
            return result
        try:
            completed = run_bounded_command(
                command,
                timeout=timeout,
                max_output_bytes=READINESS_OUTPUT_MAX_BYTES,
                env={"PATH": "/usr/bin:/bin"},
            )
        except subprocess.TimeoutExpired:
            result.update(status="command_failed", detail=f"timeout after {timeout} seconds")
            return result
        except CommandOutputLimitExceeded as exc:
            result.update(status="command_failed", stderr=exc.stderr[-2000:], detail=str(exc))
            return result
        except OSError as exc:
            result.update(status="command_failed", detail=f"{type(exc).__name__}: {exc}"[:500])
            return result
        result.update(returncode=completed.returncode, stderr=completed.stderr[-2000:])
        if completed.returncode == 0:
            result["status"] = "ok"
        else:
            result.update(
                status="command_failed",
                detail=(
                    f"bwrap exited {completed.returncode}: the sandbox did not start; "
                    "unprivileged user namespaces may be unavailable"
                ),
            )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        result.update(status="tool_unavailable", detail=f"{type(exc).__name__}: {exc}"[:500])
    return result


def resolve_comparator_toolchain(
    toolchain: str, *, input_root: Path | None = None
) -> dict[str, Any]:
    """Find the directory of the stable Lean release a challenge names.

    AAS_LEAN_COMPARATOR_TOOLCHAIN, when set, must be an absolute directory.
    Otherwise the directory is ``$ELAN_HOME/toolchains/<name>``, ELAN_HOME
    defaulting to ``~/.elan``; elan names ``leanprover/lean4:v4.35.0``
    ``leanprover--lean4---v4.35.0`` ("/" becomes "--", ":" becomes "---").
    The proxies in ``$ELAN_HOME/bin`` follow the working directory's
    lean-toolchain and are never used. The resolved directory is refused when
    it is ``input_root`` or lies inside it, whichever way it was found, and its
    bin/ must hold lake, lean, leanchecker and leanexport as regular files.
    A ``toolchain``, or an ``input_root`` other than None, that is neither a
    str nor an os.PathLike is ``tool_unavailable`` with the detail ``invalid
    path argument``. Never raises.
    """

    result: dict[str, Any] = {
        "status": "tool_unavailable",
        "toolchain": toolchain,
        "path": "",
        "bin_dir": "",
        "source": "",
        "detail": "",
    }
    try:
        if not isinstance(toolchain, (str, os.PathLike)) or not (
            input_root is None or isinstance(input_root, (str, os.PathLike))
        ):
            result["detail"] = "invalid path argument"
            return result
        version = (
            parse_lean_version(toolchain)
            if isinstance(toolchain, str) and _LEAN_TOOLCHAIN_SPEC_RE.fullmatch(toolchain)
            else None
        )
        if version is None or version < COMPARATOR_MIN_LEAN_VERSION:
            result.update(
                status="unsupported_toolchain",
                detail=(
                    f"{toolchain}: lake comparator needs a stable "
                    "leanprover/lean4:vX.Y.Z release, v4.35.0 or later"
                ),
            )
            return result
        override = os.environ.get(COMPARATOR_TOOLCHAIN_ENV, "").strip()
        if override:
            result["source"] = "env"
            if not os.path.isabs(override):
                result["detail"] = f"{COMPARATOR_TOOLCHAIN_ENV} must be an absolute directory"
                return result
            candidate = override
        else:
            result["source"] = "elan-home"
            elan_home = os.environ.get("ELAN_HOME", "").strip()
            if elan_home and not os.path.isabs(elan_home):
                result["detail"] = "ELAN_HOME must be an absolute path"
                return result
            base = elan_home or str(_home_directory() / ".elan")
            directory_name = toolchain.replace("/", "--").replace(":", "---")
            candidate = os.path.join(base, "toolchains", directory_name)
        resolved = os.path.realpath(candidate)
        result["path"] = resolved
        if input_root is not None:
            root = Path(os.path.realpath(input_root))
            if Path(resolved) == root or root in Path(resolved).parents:
                result["detail"] = "the toolchain lies inside the input under review"
                return result
        try:
            info = os.lstat(resolved)
        except FileNotFoundError:
            result["detail"] = f"{toolchain} is not installed at {resolved}"
            return result
        bin_dir = os.path.join(resolved, "bin")
        if not stat.S_ISDIR(info.st_mode) or not _plain_directory(bin_dir):
            result["detail"] = f"{resolved} is not a toolchain directory with a bin/ directory"
            return result
        missing = [
            name
            for name in COMPARATOR_TOOLCHAIN_BINARIES
            if not _regular_file(os.path.join(bin_dir, executable_name(name)))
        ]
        if missing:
            result["detail"] = "bin/ lacks " + ", ".join(missing) + " as regular files"
            return result
        result.update(status="available", bin_dir=bin_dir)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        result.update(status="tool_unavailable", bin_dir="", detail=f"{type(exc).__name__}: {exc}"[:500])
    return result


def toolchain_file_digests(toolchain_dir: Path | str) -> dict[str, Any]:
    """sha256 of the files a comparator toolchain runs, by POSIX relative path.

    ``files`` maps ``bin/<name>`` for every entry directly in bin/ and
    ``lib/lean/<library>.so`` for each shared library there (bin/lean and
    bin/lake are launchers for them) to sha256. bin/ holds lake, lean,
    leanchecker and leanexport, which must be there, and the kernels that
    ``--paranoid`` runs: leanchecker-paranoid, lean4lean, nanoda_bin,
    con-leche and con-ron. A symlink or any other entry of bin/ that is not a
    regular file is refused, and so is a bin/ with more than
    TOOLCHAIN_BIN_MAX_FILES entries or a lib/lean with more than
    TOOLCHAIN_LIBRARY_MAX_FILES libraries: the map is never truncated.
    ``status`` is ``ok``, or ``tool_unavailable`` with ``detail`` and no
    files; a ``toolchain_dir`` that is neither a str nor an os.PathLike gives
    the detail ``invalid path argument``. Nothing runs. Never raises.
    """

    result: dict[str, Any] = {"status": "tool_unavailable", "files": {}, "detail": ""}
    try:
        if not isinstance(toolchain_dir, (str, os.PathLike)):
            result["detail"] = "invalid path argument"
            return result
        root = Path(toolchain_dir)
        if not root.is_absolute() or not _plain_directory(root):
            result["detail"] = f"{root} is not an absolute toolchain directory"
            return result
        files: dict[str, str] = {}
        location = "toolchain"
        try:
            stable_directory_chain(root, ("bin",))
            stable_directory_chain(root, ("lib", "lean"))
            names: list[str] = []
            with os.scandir(root / "bin") as entries:
                for entry in entries:
                    if len(names) == TOOLCHAIN_BIN_MAX_FILES:
                        result["detail"] = (
                            f"bin/ holds more than the {TOOLCHAIN_BIN_MAX_FILES} entries "
                            "an attestation hashes"
                        )
                        return result
                    mode = entry.stat(follow_symlinks=False).st_mode
                    if not stat.S_ISREG(mode):
                        what = "a symlink" if stat.S_ISLNK(mode) else "not a regular file"
                        result["detail"] = (
                            f"bin/{entry.name} is {what}; bin/ must hold regular files only"
                        )
                        return result
                    names.append(entry.name)
            missing = [
                name for name in COMPARATOR_TOOLCHAIN_BINARIES if executable_name(name) not in names
            ]
            if missing:
                result["detail"] = "bin/ lacks " + ", ".join(missing)
                return result
            with os.scandir(root / "lib" / "lean") as entries:
                libraries = sorted(entry.name for entry in entries if entry.name.endswith(".so"))
            if len(libraries) > TOOLCHAIN_LIBRARY_MAX_FILES:
                result["detail"] = (
                    f"lib/lean holds {len(libraries)} shared libraries, more than "
                    f"the {TOOLCHAIN_LIBRARY_MAX_FILES} an attestation hashes"
                )
                return result
            relatives = [f"bin/{name}" for name in sorted(names)]
            relatives += [f"lib/lean/{name}" for name in libraries]
            for location in relatives:
                files[location] = stable_regular_file_sha256(
                    root.joinpath(*location.split("/")), max_bytes=COMPILED_MODULE_MAX_BYTES
                )
        except StableReadError as exc:
            result["detail"] = f"{location}: {exc.kind}: {exc.detail}"
            return result
        result.update(status="ok", files=files)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        result.update(status="tool_unavailable", files={}, detail=f"{type(exc).__name__}: {exc}"[:500])
    return result


def toolchain_attestation(toolchain_dir: Path | str) -> dict[str, Any]:
    """Hash a comparator toolchain and read the version its lean reports.

    ``files`` is the map toolchain_file_digests() gives: every file directly
    in bin/, the kernels that ``--paranoid`` runs among them, and each
    lib/lean/*.so, by POSIX relative path. A toolchain that helper refuses,
    or a ``toolchain_dir`` it calls an ``invalid path argument``, is
    ``tool_unavailable``, and nothing runs. Then ``lean --version`` runs in
    the toolchain directory with only PATH=<bin>:/usr/bin:/bin and
    LANG=C.UTF-8, so no LEAN_*, ELAN_* or LAKE_* variable reaches it; nothing
    from an input is read or run. A banner that is not a stable release at
    v4.35.0 or later is ``unsupported_toolchain``, and a non-zero exit or an
    unreadable banner is ``command_failed``. Never raises.
    """

    result: dict[str, Any] = {
        "status": "tool_unavailable",
        "files": {},
        "lean_version": "",
        "version": None,
        "detail": "",
    }
    try:
        digests = toolchain_file_digests(toolchain_dir)
        if digests["status"] != "ok":
            result["detail"] = digests["detail"]
            return result
        root = Path(toolchain_dir)
        bin_dir = root / "bin"
        result["files"] = digests["files"]
        command = [str(bin_dir / executable_name("lean")), "--version"]
        try:
            completed = run_bounded_command(
                command,
                timeout=TOOLCHAIN_ATTESTATION_TIMEOUT_SECONDS,
                cwd=root,
                max_output_bytes=READINESS_OUTPUT_MAX_BYTES,
                env={"PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]), "LANG": "C.UTF-8"},
            )
        except subprocess.TimeoutExpired:
            result.update(
                status="command_failed",
                detail=(
                    f"lean --version: timeout after {TOOLCHAIN_ATTESTATION_TIMEOUT_SECONDS} seconds"
                ),
            )
            return result
        except CommandOutputLimitExceeded as exc:
            result.update(status="command_failed", detail=f"lean --version: {exc}")
            return result
        except OSError as exc:
            result.update(
                status="command_failed", detail=f"lean --version: {type(exc).__name__}: {exc}"[:500]
            )
            return result
        lines = completed.stdout.splitlines()
        banner = lines[0][:200] if lines else ""
        result["lean_version"] = banner
        if completed.returncode != 0:
            result.update(
                status="command_failed", detail=f"lean --version exited {completed.returncode}"
            )
            return result
        version = _version_triple(_LEAN_VERSION_BANNER_RE.match(banner))
        if version is None:
            if banner.startswith("Lean (version "):
                result.update(
                    status="unsupported_toolchain",
                    detail="lean --version names no stable X.Y.Z release",
                )
            else:
                result.update(
                    status="command_failed", detail="lean --version printed no Lean version banner"
                )
            return result
        result["version"] = _version_text(version)
        if version < COMPARATOR_MIN_LEAN_VERSION:
            result.update(
                status="unsupported_toolchain",
                detail=f"Lean {_version_text(version)} is older than v4.35.0",
            )
            return result
        result["status"] = "ok"
    except (OSError, RuntimeError, ValueError) as exc:
        result.update(status="tool_unavailable", detail=f"{type(exc).__name__}: {exc}"[:500])
    return result


def _comparator_toolchain_rows() -> dict[str, Any]:
    """``toolchains``: a row per ``$ELAN_HOME/toolchains/leanprover--lean4---*`` directory.

    The rows come from names and listings only: ``name`` decodes the elan
    directory, ``version`` is set for a stable release, ``supported`` means
    v4.35.0 or later, and ``has_comparator`` means bin/ holds lake, lean,
    leanchecker and leanexport as regular files. Nothing runs. A listing that
    cannot be made gives no rows and ``toolchains_error``.
    """

    try:
        elan_home = os.environ.get("ELAN_HOME", "").strip()
        if elan_home and not os.path.isabs(elan_home):
            return {"toolchains": [], "toolchains_error": "ELAN_HOME must be an absolute path"}
        directory = Path(elan_home) if elan_home else _home_directory() / ".elan"
        directory = directory / "toolchains"
        try:
            with os.scandir(directory) as entries:
                names = [
                    entry.name
                    for entry in entries
                    if entry.name.startswith(_ELAN_LEAN4_PREFIX) and entry.is_dir()
                ]
        except FileNotFoundError:
            return {"toolchains": []}
        rows = []
        for name in names:
            spec = "leanprover/lean4:" + name[len(_ELAN_LEAN4_PREFIX):]
            version = parse_lean_version(spec)
            rows.append(
                {
                    "name": spec,
                    "version": _version_text(version) if version else None,
                    "supported": version is not None and version >= COMPARATOR_MIN_LEAN_VERSION,
                    "has_comparator": all(
                        _regular_file(directory / name / "bin" / executable_name(binary))
                        for binary in COMPARATOR_TOOLCHAIN_BINARIES
                    ),
                }
            )
        return {"toolchains": sorted(rows, key=lambda row: row["name"])}
    except (OSError, RuntimeError, ValueError) as exc:
        return {"toolchains": [], "toolchains_error": f"{type(exc).__name__}: {exc}"[:500]}


def _newest_local_bwrap() -> str:
    """The highest ``~/.local/opt/bubblewrap-X.Y.Z/bin/bwrap`` that is executable, or ""."""

    opt = _home_directory() / ".local" / "opt"
    try:
        with os.scandir(opt) as entries:
            names = sorted(entry.name for entry in entries)
    except FileNotFoundError:
        return ""
    best: tuple[tuple[int, int, int], str] | None = None
    for name in names:
        version = _version_triple(_BWRAP_DIRECTORY_RE.fullmatch(name))
        if version is None:
            continue
        candidate = os.path.join(opt, name, "bin", "bwrap")
        if _executable_file_problem(candidate):
            continue
        if best is None or version > best[0]:
            best = (version, candidate)
    return best[1] if best else ""


def _bwrap_install_version(path: str) -> tuple[int, int, int] | None:
    """X.Y.Z of a ``.../bubblewrap-X.Y.Z/bin/bwrap`` path, or None."""

    binary = Path(path)
    if binary.name != "bwrap" or binary.parent.name != "bin":
        return None
    return _version_triple(_BWRAP_DIRECTORY_RE.fullmatch(binary.parent.parent.name))


def _version_triple(match: re.Match[str] | None) -> tuple[int, int, int] | None:
    if match is None:
        return None
    try:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:  # a component past sys.get_int_max_str_digits()
        return None


def _version_text(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def _home_directory() -> Path:
    """Path.home(), which must be absolute; RuntimeError when it is unknown."""

    home = Path.home()
    if not home.is_absolute():
        raise ValueError(f"the home directory {home} is not an absolute path")
    return home


def _executable_file_problem(path: str) -> str:
    """"" when ``path`` is an executable regular file, else what is wrong with it."""

    try:
        info = os.stat(path)
    except OSError as exc:
        return f"{path} cannot be used: {exc.strerror or type(exc).__name__}"
    if not stat.S_ISREG(info.st_mode):
        return f"{path} is not a regular file"
    if not os.access(path, os.X_OK):
        return f"{path} is not executable"
    return ""


def _regular_file(path: str | Path) -> bool:
    """A regular file that is not a symlink."""

    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except (OSError, ValueError):
        return False


def _plain_directory(path: str | Path) -> bool:
    """A directory that is not a symlink."""

    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except (OSError, ValueError):
        return False


def scan_input(path: Path, artifact_stage: str, allowed_imports: set[str]) -> dict[str, Any]:
    """Dispatch: a directory is scanned recursively as a project, a file as before."""
    if not path.is_symlink() and path.is_dir():
        return scan_project(path, artifact_stage, allowed_imports)
    return scan_path(path, artifact_stage, allowed_imports)


class StableReadError(Exception):
    """A file could not be read as one stable, non-symlinked regular object."""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def _stable_regular_file_read(
    path: Path,
    *,
    max_bytes: int | None,
    collect_bytes: bool,
) -> bytes | str:
    """Read or hash one file while binding path and descriptor identities."""

    limit = SOURCE_MAX_BYTES if max_bytes is None else max_bytes
    if limit <= 0:
        raise ValueError("max_bytes must be positive")

    try:
        path_info = os.lstat(path)
    except FileNotFoundError as exc:
        raise StableReadError("missing_file", "input file does not exist") from exc
    except OSError as exc:
        raise StableReadError("read_error", str(exc)) from exc
    if stat.S_ISLNK(path_info.st_mode):
        raise StableReadError(
            "symlink_input",
            "input must be a stable regular file, not a symlink",
        )
    if not stat.S_ISREG(path_info.st_mode):
        raise StableReadError("non_regular_input", "input is not a regular file")

    descriptor: int | None = None
    try:
        # O_NONBLOCK: a FIFO swapped in after the lstat above must not block
        # the open; the fstat below then refuses it. Regular files ignore it.
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_BINARY", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or (int(path_info.st_dev), int(path_info.st_ino))
            != (int(before.st_dev), int(before.st_ino))
        ):
            raise StableReadError(
                "input_changed_during_scan",
                "input path changed before it could be read",
            )
        if int(before.st_size) < 0 or int(before.st_size) > limit:
            raise StableReadError(
                "input_too_large",
                f"input exceeds the {limit}-byte read limit",
            )
        chunks: list[bytes] = []
        hasher = hashlib.sha256()
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            if collect_bytes:
                chunks.append(chunk)
            else:
                hasher.update(chunk)
            remaining -= len(chunk)
        if remaining == 0:
            raise StableReadError(
                "input_too_large",
                f"input exceeds the {limit}-byte read limit",
            )
        after = os.fstat(descriptor)
        stable = (
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
            "st_nlink",
        )
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise StableReadError(
                "input_changed_during_scan",
                "input changed while it was being read",
            )
        return b"".join(chunks) if collect_bytes else hasher.hexdigest()
    except StableReadError:
        raise
    except OSError as exc:
        raise StableReadError("read_error", str(exc)) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def stable_regular_file_bytes(path: Path, *, max_bytes: int | None = None) -> bytes:
    """Read exact bytes while binding the path and descriptor identities."""

    content = _stable_regular_file_read(
        path,
        max_bytes=max_bytes,
        collect_bytes=True,
    )
    assert isinstance(content, bytes)
    return content


def stable_regular_file_sha256(path: Path, *, max_bytes: int) -> str:
    """Hash exact bytes without materializing a potentially large artifact."""

    digest = _stable_regular_file_read(
        path,
        max_bytes=max_bytes,
        collect_bytes=False,
    )
    assert isinstance(digest, str)
    return digest


def scan_fingerprint(payload: dict[str, Any]) -> str:
    """Stable digest of the exact source bytes represented by a scan payload."""

    if payload.get("mode") == "project":
        rows = (payload.get("coverage") or {}).get("files") or []
        material = [
            [str(row.get("file") or ""), str(row.get("sha256") or "")]
            for row in rows
            if isinstance(row, dict)
        ]
    else:
        material = [[str(payload.get("input") or ""), str(payload.get("content_sha256") or "")]]
    return hashlib.sha256(
        json.dumps(material, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def stable_tree_files(
    root: Path,
    *,
    suffix: str,
    excluded_dirs: set[str],
    missing_ok: bool = False,
    reject_symlink_files: bool = False,
) -> list[Path]:
    """Walk a tree without silently skipping unreadable or symlinked subtrees."""

    try:
        root_info = os.lstat(root)
    except FileNotFoundError as exc:
        if missing_ok:
            return []
        raise StableReadError("missing_project", "project root does not exist") from exc
    except OSError as exc:
        raise StableReadError("project_traversal_error", str(exc)) from exc
    if stat.S_ISLNK(root_info.st_mode):
        raise StableReadError("symlink_project", "project tree root must not be a symlink")
    if not stat.S_ISDIR(root_info.st_mode):
        raise StableReadError("non_directory_project", "project tree root is not a directory")

    files: list[Path] = []
    pending = [root]
    entries_seen = 0
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                children = []
                for entry in entries:
                    entries_seen += 1
                    if entries_seen > TREE_MAX_ENTRIES:
                        raise StableReadError(
                            "too_many_tree_entries",
                            f"tree walk exceeds the {TREE_MAX_ENTRIES}-entry cap",
                        )
                    children.append(entry)
        except OSError as exc:
            relative = directory.relative_to(root).as_posix() or "."
            raise StableReadError(
                "project_traversal_error",
                f"could not enumerate {relative}: {type(exc).__name__}",
            ) from exc
        for entry in children:
            path = Path(entry.path)
            if entry.name in excluded_dirs:
                continue
            try:
                entry_info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                relative = path.relative_to(root).as_posix()
                raise StableReadError(
                    "project_traversal_error",
                    f"could not inspect {relative}: {type(exc).__name__}",
                ) from exc
            if stat.S_ISLNK(entry_info.st_mode):
                relative = path.relative_to(root).as_posix()
                if entry.name.endswith(suffix):
                    if reject_symlink_files:
                        raise StableReadError(
                            "symlink_input",
                            f"project tree contains symlinked evidence file {relative}",
                        )
                    files.append(path)
                    continue
                try:
                    points_to_directory = entry.is_dir(follow_symlinks=True)
                except OSError as exc:
                    raise StableReadError(
                        "project_traversal_error",
                        f"could not resolve symlink {relative}: {type(exc).__name__}",
                    ) from exc
                if points_to_directory:
                    raise StableReadError(
                        "symlink_directory",
                        f"project tree contains symlinked directory {relative}",
                    )
                continue
            if stat.S_ISDIR(entry_info.st_mode):
                pending.append(path)
            elif stat.S_ISREG(entry_info.st_mode) and entry.name.endswith(suffix):
                files.append(path)
    # Sort on project-relative POSIX text, not Path. Path ordering is case-folded
    # on Windows and byte-ordered elsewhere.
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def stable_directory_chain(
    root: Path,
    parts: tuple[str, ...],
    *,
    missing_ok: bool = False,
) -> Path | None:
    """Resolve only ordinary directory components below an already-bound root."""

    current = root
    for part in parts:
        current /= part
        try:
            info = os.lstat(current)
        except FileNotFoundError as exc:
            if missing_ok:
                return None
            raise StableReadError(
                "missing_directory",
                f"required directory {current.relative_to(root).as_posix()} does not exist",
            ) from exc
        except OSError as exc:
            raise StableReadError("project_traversal_error", str(exc)) from exc
        relative = current.relative_to(root).as_posix()
        if stat.S_ISLNK(info.st_mode):
            raise StableReadError(
                "symlink_directory",
                f"project evidence directory {relative} must not be a symlink",
            )
        if not stat.S_ISDIR(info.st_mode):
            raise StableReadError(
                "non_directory_build_output",
                f"project evidence path {relative} is not a directory",
            )
    return current


def project_lean_files(root: Path) -> list[Path]:
    return stable_tree_files(
        root,
        suffix=".lean",
        excluded_dirs=PROJECT_EXCLUDED_DIRS,
    )


def scan_project(root: Path, artifact_stage: str, allowed_imports: set[str]) -> dict[str, Any]:
    """Recursive scan of every .lean file with a per-file coverage manifest.

    Coverage is explicit so a re-scan can be diffed against staged evidence:
    each row records the relative path and content hash actually scanned.
    """
    try:
        files = project_lean_files(root)
    except StableReadError as exc:
        return {
            "schema_version": "lean-strict-verification-gate.v1",
            "ok": False,
            "input": str(root),
            "mode": "project",
            "artifact_stage": artifact_stage,
            "lean_check_status": "not_run",
            "placeholder_status": "not_scanned",
            "trust_base_status": "not_scanned",
            "safety_status": "failed",
            "findings": [{"kind": exc.kind, "detail": exc.detail}],
            "coverage": {"files_total": 0, "files_scanned": 0, "files": []},
        }
    if len(files) > PROJECT_MAX_FILES:
        return {
            "schema_version": "lean-strict-verification-gate.v1",
            "ok": False,
            "input": str(root),
            "mode": "project",
            "artifact_stage": artifact_stage,
            "lean_check_status": "not_run",
            "placeholder_status": "not_scanned",
            "trust_base_status": "not_scanned",
            "safety_status": "failed",
            "findings": [{
                "kind": "too_many_files",
                "detail": f"{len(files)} .lean files exceed the {PROJECT_MAX_FILES}-file project cap; refusing a partial scan",
            }],
            "coverage": {"files_total": len(files), "files_scanned": 0, "files": []},
        }
    findings: list[dict[str, str]] = []
    coverage: list[dict[str, str]] = []
    files_scanned = 0
    placeholder_any = False
    trust_any = False
    for lean_file in files:
        rel = str(lean_file.relative_to(root))
        file_payload = scan_path(lean_file, artifact_stage, allowed_imports)
        digest = str(file_payload.get("content_sha256") or "")
        if digest:
            files_scanned += 1
        coverage.append({"file": rel, "sha256": digest, "ok": file_payload["ok"]})
        for finding in file_payload["findings"]:
            findings.append({**finding, "file": rel})
        if file_payload["placeholder_status"] in {"active_placeholders_found", "placeholders_allowed_for_stub"}:
            placeholder_any = True
        if file_payload["trust_base_status"] == "unsanctioned_axiom_or_unsafe":
            trust_any = True
    if not files:
        findings.append({"kind": "empty_project", "detail": "no .lean files found under the project root"})
    return {
        "schema_version": "lean-strict-verification-gate.v1",
        "ok": not findings,
        "input": str(root),
        "mode": "project",
        "artifact_stage": artifact_stage,
        "lean_check_status": "not_run",
        "placeholder_status": (
            "placeholders_allowed_for_stub"
            if artifact_stage == "stub" and placeholder_any
            else "active_placeholders_found"
            if placeholder_any
            else "no_active_placeholders"
        ),
        "trust_base_status": "unsanctioned_axiom_or_unsafe" if trust_any else "accepted_trust_base",
        "safety_status": (
            "failed"
            if any(
                item["kind"] not in {"active_placeholder", "trust_base_blocker"}
                for item in findings
            )
            else "passed"
        ),
        "findings": findings,
        "coverage": {"files_total": len(files), "files_scanned": files_scanned, "files": coverage},
        "limitations": [
            "scanner is a preflight guard, not a complete Lean parser",
            "statement equivalence is not checked by this helper",
        ],
    }


def scan_path(path: Path, artifact_stage: str, allowed_imports: set[str]) -> dict[str, Any]:
    try:
        source_bytes = stable_regular_file_bytes(path)
        text = source_bytes.decode("utf-8")
    except StableReadError as exc:
        return unreadable_payload(path, artifact_stage, exc.kind, exc.detail)
    except UnicodeDecodeError:
        return unreadable_payload(path, artifact_stage, "invalid_utf8", "input file is not valid UTF-8")
    stripped, lexical_errors = _strip_comments_and_strings(text)
    findings: list[dict[str, str]] = []
    findings.extend(
        {"kind": "lexical_scan_incomplete", "detail": detail}
        for detail in lexical_errors
    )
    for name, pattern in SAFETY_PATTERNS.items():
        if pattern.search(stripped):
            findings.append({"kind": "unsafe_construct", "detail": name})
    for imp in imported_modules(stripped):
        if not valid_lean_name(imp):
            findings.append({"kind": "invalid_import", "detail": imp or "<missing>"})
        elif allowed_imports and imp not in allowed_imports:
            findings.append({"kind": "non_allowlisted_import", "detail": imp})
        elif not allowed_imports and imp not in {"Init", "Std", "Mathlib"} and not imp.startswith(("Mathlib.", "Std.")):
            findings.append({"kind": "non_allowlisted_import", "detail": imp})
    placeholder_hits = [name for name, pattern in PLACEHOLDER_PATTERNS.items() if pattern.search(stripped)]
    trust_hits = [name for name, pattern in TRUST_BASE_PATTERNS.items() if pattern.search(stripped)]
    if artifact_stage != "stub":
        findings.extend({"kind": "active_placeholder", "detail": name} for name in placeholder_hits)
    findings.extend({"kind": "trust_base_blocker", "detail": name} for name in trust_hits)
    return {
        "schema_version": "lean-strict-verification-gate.v1",
        "ok": not findings,
        "input": str(path),
        "content_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "artifact_stage": artifact_stage,
        "lean_check_status": "not_run",
        "placeholder_status": (
            "placeholders_allowed_for_stub"
            if artifact_stage == "stub" and placeholder_hits
            else "active_placeholders_found"
            if placeholder_hits
            else "no_active_placeholders"
        ),
        "trust_base_status": "unsanctioned_axiom_or_unsafe" if trust_hits else "accepted_trust_base",
        "safety_status": (
            "failed"
            if any(
                item["kind"] not in {"active_placeholder", "trust_base_blocker"}
                for item in findings
            )
            else "passed"
        ),
        "findings": findings,
        "limitations": [
            "scanner is a preflight guard, not a complete Lean parser",
            "statement equivalence is not checked by this helper",
        ],
    }


def _lean_char_literal_length(text: str, index: int) -> int:
    """Length of a valid Lean character literal at ``index``, or zero."""

    if index >= len(text) or text[index] != "'" or index + 2 >= len(text):
        return 0
    position = index + 1
    character = text[position]
    if character == "\\":
        position += 1
        if position >= len(text):
            return 0
        escape = text[position]
        if escape in "\\\"'rnt":
            position += 1
        elif escape == "x":
            digits = text[position + 1 : position + 3]
            if len(digits) != 2 or any(
                digit not in "0123456789abcdefABCDEF" for digit in digits
            ):
                return 0
            position += 3
        elif escape == "u":
            digits = text[position + 1 : position + 5]
            if len(digits) != 4 or any(
                digit not in "0123456789abcdefABCDEF" for digit in digits
            ):
                return 0
            position += 5
        else:
            return 0
    elif character in "'\r\n":
        return 0
    else:
        position += 1
    if position >= len(text) or text[position] != "'":
        return 0
    return position - index + 1


def _strip_comments_and_strings(text: str) -> tuple[str, list[str]]:
    """Lex source without letting inert text hide active Lean terms."""

    out: list[str] = []
    errors: list[str] = []
    index = 0
    block_depth = 0
    # Every unescaped `{...}` inside a string is treated as a possible
    # `interpolatedStr(term)` escape. Lean's parser aliases are extensible, so
    # prefix matching only `s!`/`m!`/`f!` would leave custom and keyword-driven
    # interpolated strings unscanned. A code frame stores its open-brace depth;
    # zero is ordinary top-level code.
    modes: list[tuple[str, int]] = [("code", 0)]
    while index < len(text):
        char = text[index]
        pair = text[index : index + 2]
        if block_depth:
            if pair == "/-":
                block_depth += 1
                out.extend((" ", " "))
                index += 2
                continue
            if pair == "-/":
                block_depth -= 1
                out.extend((" ", " "))
                index += 2
                continue
            out.append("\n" if char == "\n" else " ")
            index += 1
            continue
        mode, brace_depth = modes[-1]
        if mode == "string":
            out.append("\n" if char == "\n" else " ")
            if char == "\\":
                index += 1
                if index < len(text):
                    out.append("\n" if text[index] == "\n" else " ")
            elif char == '"':
                modes.pop()
            elif char == "{":
                modes.append(("code", 1))
            index += 1
            continue
        if pair == "--":
            out.extend((" ", " "))
            index += 2
            while index < len(text) and text[index] != "\n":
                out.append(" ")
                index += 1
            continue
        if pair == "/-":
            block_depth = 1
            out.extend((" ", " "))
            index += 2
            continue
        char_literal_length = _lean_char_literal_length(text, index)
        if char_literal_length:
            out.extend(
                "\n" if item == "\n" else " "
                for item in text[index : index + char_literal_length]
            )
            index += char_literal_length
            continue
        if char == '"':
            modes.append(("string", 0))
            out.append(" ")
            index += 1
            continue
        if brace_depth and char == "{":
            modes[-1] = ("code", brace_depth + 1)
            out.append(char)
            index += 1
            continue
        if brace_depth and char == "}":
            if brace_depth == 1:
                modes.pop()
                out.append(" ")
            else:
                modes[-1] = ("code", brace_depth - 1)
                out.append(char)
            index += 1
            continue
        out.append(char)
        index += 1
    if block_depth:
        errors.append("unterminated block comment")
    if any(mode == "string" for mode, _depth in modes[1:]):
        errors.append("unterminated string literal")
    if any(mode == "code" and depth for mode, depth in modes[1:]):
        errors.append("unterminated string interpolation")
    return "".join(out), errors


def strip_comments_and_strings(text: str) -> str:
    return _strip_comments_and_strings(text)[0]


def imported_modules(text: str) -> list[str]:
    modules = []
    for line in text.splitlines():
        # Lean 4.33's module header accepts `[public] [meta] import [all] Mod`.
        # Retain `private` as a fail-closed legacy spelling, but do not let the
        # supported `public meta import` form bypass the allowlist.
        match = re.match(
            r"\s*(?:(?:public|private)\s+)?(?:meta\s+)?import\b(?P<body>.*)$",
            line,
        )
        if match:
            body = match.group("body").strip()
            tokens = body.split()
            if tokens[:1] == ["all"]:
                tokens = tokens[1:]
            modules.extend(tokens if tokens else [""])
    return modules


def unreadable_payload(path: Path, artifact_stage: str, kind: str, detail: str) -> dict[str, Any]:
    return {
        "schema_version": "lean-strict-verification-gate.v1",
        "ok": False,
        "input": str(path),
        "artifact_stage": artifact_stage,
        "lean_check_status": "not_run",
        "placeholder_status": "not_scanned",
        "trust_base_status": "not_scanned",
        "safety_status": "failed",
        "findings": [{"kind": kind, "detail": detail}],
    }


def typecheck(path: Path, *, timeout: int, runner: str, project_root: Path | None) -> dict[str, Any]:
    if runner == "direct-lean":
        return typecheck_direct(path, timeout=timeout)
    if runner == "lake-env-lean":
        return typecheck_lake_env(path, timeout=timeout, project_root=project_root)
    if runner == "lake-build":
        return typecheck_lake_build(timeout=timeout, project_root=project_root or (path if path.is_dir() else None))
    raise AssertionError(runner)


def typecheck_lake_build(*, timeout: int, project_root: Path | None) -> dict[str, Any]:
    """Typecheck a whole project with `lake build` — the only honest whole-project check."""
    if project_root is None:
        return command_failed("lake-build", "lake build", "", "runner requires --project-root or a directory input")
    root = project_root.expanduser().resolve()
    if not root.is_dir():
        return command_failed("lake-build", "lake build", str(root), "project root does not exist")
    status = project_status(root)
    if not status["lake_workspace_detected"]:
        return command_failed("lake-build", "lake build", str(root), "project root must contain lakefile.lean or lakefile.toml")
    lake = tool_status("lake")
    if lake["status"] != "available":
        return {
            "lean_check_status": "tool_unavailable",
            "runner": "lake-build",
            "typecheck_command": "lake build",
            "typecheck_cwd": str(root),
            "project_status": status,
            "tool_status": {"lake": lake},
            "typecheck_stdout": "",
            "typecheck_stderr": "",
        }
    payload = run_typecheck(
        [lake["path"], "build"],
        timeout=timeout,
        command_label="lake build",
        runner="lake-build",
        cwd=root,
        tool_status_payload={"lake": lake},
        project_status_payload=status,
    )
    if payload["lean_check_status"] != "typechecked":
        return payload
    try:
        built_modules, unbuilt_modules = built_project_modules(root)
        stale_modules = stale_project_modules(root, built_modules)
    except StableReadError as exc:
        payload["lean_check_status"] = "command_failed"
        payload["typecheck_coverage_status"] = "failed"
        payload["typecheck_stderr"] = f"post-build coverage could not be read: {exc.detail}"
        return payload
    payload["typecheck_modules_built"] = built_modules
    payload["typecheck_modules_unbuilt"] = unbuilt_modules
    payload["typecheck_modules_stale"] = stale_modules
    if not built_modules or unbuilt_modules or stale_modules:
        details = []
        if not built_modules:
            details.append("no local source module produced a compiled artifact")
        if unbuilt_modules:
            details.append("unbuilt local modules: " + ", ".join(unbuilt_modules))
        if stale_modules:
            details.append("stale local modules: " + ", ".join(stale_modules))
        payload["lean_check_status"] = "command_failed"
        payload["typecheck_coverage_status"] = "failed"
        payload["typecheck_stderr"] = "; ".join(details)
    else:
        payload["typecheck_coverage_status"] = "complete"
    return payload


def typecheck_direct(path: Path, *, timeout: int) -> dict[str, Any]:
    lean = tool_status("lean")
    if lean["status"] != "available":
        return {
            "lean_check_status": "tool_unavailable",
            "runner": "direct-lean",
            "typecheck_command": "lean <input>",
            "typecheck_cwd": "",
            "tool_status": {"lean": lean},
            "typecheck_stdout": "",
            "typecheck_stderr": "",
        }
    return run_typecheck(
        [lean["path"], str(path)],
        timeout=timeout,
        command_label="lean <input>",
        runner="direct-lean",
        cwd=None,
        tool_status_payload={"lean": lean},
    )


def typecheck_lake_env(path: Path, *, timeout: int, project_root: Path | None) -> dict[str, Any]:
    if project_root is None:
        return command_failed("lake-env-lean", "lake env lean <input>", "", "runner requires --project-root")
    root = project_root.expanduser().resolve()
    if not root.is_dir():
        return command_failed("lake-env-lean", "lake env lean <input>", str(root), "project root does not exist")
    status = project_status(root)
    if not status["lake_workspace_detected"]:
        return command_failed("lake-env-lean", "lake env lean <input>", str(root), "project root must contain lakefile.lean or lakefile.toml")
    lake = tool_status("lake")
    if lake["status"] != "available":
        return {
            "lean_check_status": "tool_unavailable",
            "runner": "lake-env-lean",
            "typecheck_command": "lake env lean <input>",
            "typecheck_cwd": str(root),
            "project_status": status,
            "tool_status": {"lake": lake},
            "typecheck_stdout": "",
            "typecheck_stderr": "",
        }
    return run_typecheck(
        [lake["path"], "env", "lean", str(path.resolve())],
        timeout=timeout,
        command_label="lake env lean <input>",
        runner="lake-env-lean",
        cwd=root,
        tool_status_payload={"lake": lake},
        project_status_payload=status,
    )


def command_failed(runner: str, command: str, cwd: str, stderr: str) -> dict[str, Any]:
    return {
        "lean_check_status": "command_failed",
        "runner": runner,
        "typecheck_command": command,
        "typecheck_cwd": cwd,
        "typecheck_stdout": "",
        "typecheck_stderr": stderr,
    }


def _output_tail(value: str | bytes | None, limit: int) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value or "")[-limit:]


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Best-effort hard termination for the isolated child process group."""

    if os.name == "nt":
        try:
            killer = subprocess.Popen(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            killer.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                killer.kill()
                killer.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
        except OSError:
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            try:
                process.kill()
            except OSError:
                pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def run_bounded_command(
    command: list[str],
    *,
    timeout: float,
    cwd: Path | None = None,
    max_output_bytes: int = COMMAND_OUTPUT_MAX_BYTES,
    capture_evidence: bool = False,
    env: dict[str, str] | None = None,
    umask: int = -1,
    kill_on_interrupt: bool = False,
) -> BoundedCommandResult:
    """Run one isolated child while hard-capping combined stdout and stderr.

    ``env``, when given, is the child's whole environment; otherwise the child
    inherits this process's environment. ``umask``, when not negative, is the
    child's umask on POSIX; this process's own umask stays as it is. With
    ``kill_on_interrupt``, a KeyboardInterrupt (Ctrl-C) that arrives while the
    child runs kills the child's process group before it goes on.
    """

    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive")
    group_options: dict[str, Any]
    if os.name == "nt":
        group_options = {
            "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        }
    else:
        group_options = {"start_new_session": True, "umask": umask}
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        env=env,
        **group_options,
    )
    if process.stdout is None or process.stderr is None:
        _terminate_process_tree(process)
        raise OSError("could not create child output pipes")

    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    total = [0]
    lock = threading.Lock()
    output_limit_hit = threading.Event()
    reader_errors: list[str] = []

    def drain(stream: Any, name: str) -> None:
        try:
            while True:
                read = getattr(stream, "read1", stream.read)
                chunk = read(65_536)
                if not chunk:
                    return
                should_terminate = False
                with lock:
                    room = max(0, max_output_bytes - total[0])
                    if room:
                        buffers[name].extend(chunk[:room])
                    total[0] += len(chunk)
                    if total[0] > max_output_bytes and not output_limit_hit.is_set():
                        output_limit_hit.set()
                        should_terminate = True
                if should_terminate:
                    _terminate_process_tree(process)
        except (OSError, ValueError) as exc:
            with lock:
                reader_errors.append(f"{name}: {type(exc).__name__}")
            _terminate_process_tree(process)

    threads = [
        threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True),
    ]
    for thread in threads:
        thread.start()

    timed_out = False
    cleanup_error: str | None = None

    def evidence() -> dict[str, Any] | None:
        if not capture_evidence:
            return None
        with lock:
            streams = {name: bytes(raw) for name, raw in buffers.items()}
            capture_error = ", ".join(reader_errors) or None
        partial = bool(timed_out or output_limit_hit.is_set() or capture_error or cleanup_error)
        result: dict[str, Any] = {
            "schema_version": "process_evidence.v1", "return_code": process.returncode,
            "timed_out": timed_out, "oversized": output_limit_hit.is_set(),
            "capture_error": capture_error, "cleanup_error": cleanup_error,
            "capture_complete": not partial,
        }
        for name, raw in streams.items():
            result[name] = {"state": "partial" if partial else "captured", "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "base64": base64.b64encode(raw).decode("ascii")}
        return result

    def evidence_error(error: Exception) -> Exception:
        error.process_evidence = evidence()
        return error

    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_tree(process)
    except KeyboardInterrupt:
        if kill_on_interrupt:
            _terminate_process_tree(process)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            for thread in threads:
                thread.join(timeout=1)
            for stream in (process.stdout, process.stderr):
                try:
                    stream.close()
                except OSError:
                    pass
        raise
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    for thread in threads:
        thread.join(timeout=5)
    if any(thread.is_alive() for thread in threads):
        _terminate_process_tree(process)
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass
        for thread in threads:
            thread.join(timeout=1)
    if any(thread.is_alive() for thread in threads):
        cleanup_error = "child output pipes did not close after termination"
        raise evidence_error(OSError(cleanup_error))
    for stream in (process.stdout, process.stderr):
        try:
            stream.close()
        except OSError:
            pass
    if process.poll() is None:
        _terminate_process_tree(process)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            cleanup_error = "child could not be reaped after termination"
            raise evidence_error(OSError(cleanup_error)) from exc

    stdout_bytes = bytes(buffers["stdout"])
    stderr_bytes = bytes(buffers["stderr"])
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if output_limit_hit.is_set():
        raise evidence_error(CommandOutputLimitExceeded(
            max_output_bytes, stdout[-4000:], stderr[-4000:],
        ))
    if timed_out:
        raise evidence_error(subprocess.TimeoutExpired(
            command, timeout, output=stdout_bytes, stderr=stderr_bytes,
        ))
    if reader_errors:
        raise evidence_error(OSError("could not capture child output: " + ", ".join(reader_errors)))
    return BoundedCommandResult(
        int(process.returncode or 0),
        stdout,
        stderr,
        total[0],
        evidence(),
    )


def run_typecheck(
    command: list[str],
    *,
    timeout: int,
    command_label: str,
    runner: str,
    cwd: Path | None,
    tool_status_payload: dict[str, Any],
    project_status_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        completed = run_bounded_command(
            command,
            timeout=timeout,
            cwd=cwd,
            max_output_bytes=COMMAND_OUTPUT_MAX_BYTES,
            capture_evidence=True,
        )
    except subprocess.TimeoutExpired as exc:
        payload = command_failed(runner, command_label, str(cwd) if cwd else "", f"timeout after {timeout} seconds")
        payload["typecheck_stdout"] = _output_tail(exc.stdout, 2000)
        payload["tool_status"] = tool_status_payload
        if project_status_payload:
            payload["project_status"] = project_status_payload
        if isinstance(getattr(exc, "process_evidence", None), dict):
            payload["process_evidence"] = exc.process_evidence
        return payload
    except CommandOutputLimitExceeded as exc:
        payload = command_failed(runner, command_label, str(cwd) if cwd else "", str(exc))
        payload["typecheck_stdout"] = exc.stdout[-2000:]
        payload["typecheck_stderr"] = (exc.stderr + "\n" + str(exc))[-2000:]
        payload["output_limit_exceeded"] = True
        payload["tool_status"] = tool_status_payload
        if project_status_payload:
            payload["project_status"] = project_status_payload
        if isinstance(getattr(exc, "process_evidence", None), dict):
            payload["process_evidence"] = exc.process_evidence
        return payload
    except OSError as exc:
        payload = command_failed(runner, command_label, str(cwd) if cwd else "", str(exc))
        payload["tool_status"] = tool_status_payload
        if project_status_payload:
            payload["project_status"] = project_status_payload
        if isinstance(getattr(exc, "process_evidence", None), dict):
            payload["process_evidence"] = exc.process_evidence
        return payload
    payload = {
        "lean_check_status": "typechecked" if completed.returncode == 0 else "typecheck_failed",
        "runner": runner,
        "typecheck_command": command_label,
        "typecheck_cwd": str(cwd) if cwd else "",
        "tool_status": tool_status_payload,
        "typecheck_stdout": completed.stdout[-2000:],
        "typecheck_stderr": completed.stderr[-2000:],
    }
    if project_status_payload:
        payload["project_status"] = project_status_payload
    if isinstance(getattr(completed, "process_evidence", None), dict):
        payload["process_evidence"] = completed.process_evidence
    return payload


_AXIOM_DEPENDS_RE = re.compile(r"'(?P<decl>[^']+)' depends on axioms: \[(?P<axioms>[^\]]*)\]", re.S)
_AXIOM_NONE_RE = re.compile(r"'(?P<decl>[^']+)' does not depend on any axioms")
_MODIFIERS = r"(?:(?:protected|noncomputable|partial|nonrec|unsafe|scoped)\s+)*"
_DECLARATION_RE = re.compile(
    # Any command may be scoped to the next declaration with `... in`, and it
    # may sit on that declaration's own line: `open X in`, `set_option k v in`,
    # `attribute [simp] f in`, `variable (n : Nat) in`, `local notation ... in`.
    # Matching only `open` here hid every other one from the audit. The inner
    # `[^\n]*?` also spans a chain of them, since each ` in ` can be absorbed.
    r"^\s*(?:\S[^\n]*?\sin\s+)?(?:@\[[^\]]*\]\s*)*"
    # `private` may sit anywhere in the modifier run, so both sides allow one.
    + _MODIFIERS
    + r"(?P<private>private\s+)?"
    + _MODIFIERS
    + r"(?:theorem|lemma)\s+(?P<name>[^\s:({\[]+)"
)
# What a declaration line looks like before the walk tries to read a name off
# it. `theorem`/`lemma` are reserved, so the only way the keyword appears
# elsewhere is inside a longer identifier or after a dot — both excluded here.
_DECLARATION_KEYWORD_RE = re.compile(r"(?<![.\w])(?:theorem|lemma)\b")
_NAMESPACE_RE = re.compile(r"^\s*namespace\s+(?P<name>[^\s:({\[]+)")
# Every block `end` closes must push a scope, or the walk pops a namespace it
# never entered and every later name loses its prefix. `noncomputable section`
# is the common one: matching bare `section` alone left its `end` to pop the
# enclosing namespace, so every declaration after it was audited under a name
# that is either unresolvable or, worse, some other declaration's.
_ANONYMOUS_SCOPE_RE = re.compile(
    r"^\s*" + _MODIFIERS + r"(?P<kind>section|mutual)\b"
)
_END_RE = re.compile(r"^\s*end\b")


class DeclarationScan(NamedTuple):
    """What a source walk found, split by what the audit can do with it."""

    names: list[str]
    private: list[str]
    unparsed: list[str]


def scan_declarations(text: str) -> DeclarationScan:
    """Qualified theorem/lemma names, split by what the audit can do with them.

    This is a line walk over comment-stripped source, not a Lean parser, so a
    name it invents comes back as ``declaration_unresolved`` rather than
    silently passing. A line it cannot read a name off is the dangerous half of
    that: a missed declaration would simply never be audited, and the report
    would present a clean trust base over a scan that skipped it. Those lines
    are returned as ``unparsed`` so the caller can refuse instead.
    Definitions (``def``, ``abbrev``, ``instance``) are deliberately out of
    scope — a theorem that uses one inherits its axioms, so an unsound
    definition still surfaces through the theorem that depends on it — and
    ``example`` has no name to ask ``#print axioms`` about.

    Lean mangles a ``private`` name, so an importing harness cannot ask about
    it and every one of them would come back unresolved. They are returned
    separately, to be reported as skipped rather than audited or hidden: the
    same transitivity argument covers them, since nothing outside their module
    can cite one and any public theorem using one inherits its axioms.
    """
    scopes: list[str] = []
    names: list[str] = []
    private: list[str] = []
    unparsed: list[str] = []
    for line in strip_comments_and_strings(text).splitlines():
        namespace = _NAMESPACE_RE.match(line)
        if namespace:
            namespace_name = namespace.group("name")
            if valid_lean_name(namespace_name) and not line[namespace.end() :].strip():
                scopes.append(namespace_name)
            else:
                unparsed.append(line.strip())
            continue
        anonymous_scope = _ANONYMOUS_SCOPE_RE.match(line)
        if anonymous_scope:
            remainder = line[anonymous_scope.end() :].strip()
            parsed_scope = False
            if anonymous_scope.group("kind") == "section":
                section_names = remainder.split()
                parsed_scope = len(section_names) <= 1 and (
                    not section_names or valid_lean_name(section_names[0])
                )
            else:
                parsed_scope = not remainder
            if not parsed_scope:
                unparsed.append(line.strip())
            else:
                scopes.append("")
            continue
        end = _END_RE.match(line)
        if end:
            end_names = line[end.end() :].strip().split()
            parsed_end = len(end_names) <= 1 and (
                not end_names or valid_lean_name(end_names[0])
            )
            if not parsed_end:
                unparsed.append(line.strip())
            elif scopes:
                scopes.pop()
            continue
        declaration = _DECLARATION_RE.match(line)
        declaration_keywords = list(_DECLARATION_KEYWORD_RE.finditer(line))
        if declaration:
            prefix = ".".join(scope for scope in scopes if scope)
            name = declaration.group("name")
            qualified = f"{prefix}.{name}" if prefix else name
            (private if declaration.group("private") else names).append(qualified)
            if len(declaration_keywords) != 1:
                unparsed.append(line.strip())
        elif declaration_keywords:
            unparsed.append(line.strip())
    return DeclarationScan(names, private, unparsed)


def declaration_names(text: str) -> list[str]:
    """The auditable half of :func:`scan_declarations`."""
    return scan_declarations(text).names


def project_module_sources(root: Path) -> list[tuple[str, Path]]:
    """(module name, source file) for every .lean file the project owns."""
    pairs = []
    for lean_file in project_lean_files(root):
        parts = lean_file.relative_to(root).with_suffix("").parts
        if len(parts) == 1 and parts[0] in NON_MODULE_STEMS:
            continue
        pairs.append((".".join(parts), lean_file))
    return pairs


def project_modules(root: Path) -> list[str]:
    """Module names derived from the project layout, built or not."""
    return [module for module, _ in project_module_sources(root)]


def built_module_artifacts(root: Path) -> dict[str, Path]:
    """Module -> stable regular olean path under Lake's build output."""

    legacy_lib_dir = stable_directory_chain(
        root,
        (".lake", "build", "lib"),
        missing_ok=True,
    )
    if legacy_lib_dir is None:
        return {}
    lean_dir = legacy_lib_dir / "lean"
    try:
        lean_info = os.lstat(lean_dir)
    except FileNotFoundError:
        lib_dir = legacy_lib_dir
    except OSError as exc:
        raise StableReadError("project_traversal_error", str(exc)) from exc
    else:
        if stat.S_ISLNK(lean_info.st_mode):
            raise StableReadError(
                "symlink_directory",
                ".lake/build/lib/lean must not be a symlinked directory",
            )
        if not stat.S_ISDIR(lean_info.st_mode):
            raise StableReadError(
                "non_directory_build_output",
                ".lake/build/lib/lean is not a directory",
            )
        lib_dir = lean_dir
    oleans = stable_tree_files(
        lib_dir,
        suffix=".olean",
        excluded_dirs=set(),
        missing_ok=True,
        reject_symlink_files=True,
    )
    return {
        ".".join(olean.relative_to(lib_dir).with_suffix("").parts): olean
        for olean in oleans
    }


def built_module_names(root: Path) -> set[str]:
    """Modules Lake has actually compiled, read off its olean output tree."""

    return set(built_module_artifacts(root))


def project_context_snapshot(root: Path) -> dict[str, Any]:
    """Hash the Lake configuration that selects the build and toolchain."""

    rows: list[dict[str, str]] = []
    errors: list[str] = []
    for name in PROJECT_CONTEXT_FILES:
        path = root / name
        try:
            digest = stable_regular_file_sha256(
                path,
                max_bytes=PROJECT_CONTEXT_MAX_BYTES,
            )
        except StableReadError as exc:
            if exc.kind == "missing_file":
                rows.append({"file": name, "status": "missing", "sha256": ""})
            else:
                rows.append({"file": name, "status": exc.kind, "sha256": ""})
                errors.append(f"{name}: {exc.detail}")
        else:
            rows.append(
                {
                    "file": name,
                    "status": "hashed",
                    "sha256": digest,
                }
            )
    fingerprint = hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"fingerprint": fingerprint, "files": rows, "errors": errors}


def accepted_project_context_transition(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    runner: str,
) -> tuple[bool, str]:
    """Accept only Lake's first-build creation of a previously absent manifest."""

    if before.get("fingerprint") == after.get("fingerprint"):
        return True, ""
    if runner != "lake-build" or before.get("errors") or after.get("errors"):
        return False, ""
    before_rows = {
        row.get("file"): row
        for row in before.get("files", [])
        if isinstance(row, dict)
    }
    after_rows = {
        row.get("file"): row
        for row in after.get("files", [])
        if isinstance(row, dict)
    }
    if set(before_rows) != set(PROJECT_CONTEXT_FILES) or set(after_rows) != set(
        PROJECT_CONTEXT_FILES
    ):
        return False, ""
    for name in PROJECT_CONTEXT_FILES:
        if name == "lake-manifest.json":
            continue
        if before_rows[name] != after_rows[name]:
            return False, ""
    manifest_before = before_rows["lake-manifest.json"]
    manifest_after = after_rows["lake-manifest.json"]
    if (
        manifest_before.get("status") == "missing"
        and not manifest_before.get("sha256")
        and manifest_after.get("status") == "hashed"
        and bool(manifest_after.get("sha256"))
    ):
        return True, "lake_manifest_created_by_lake_build"
    return False, ""


def project_evidence_snapshot(root: Path, modules: list[str]) -> dict[str, Any]:
    """Bind local source, compiled modules, and Lake configuration by content."""

    context = project_context_snapshot(root)
    rows: list[dict[str, str]] = []
    errors = list(context["errors"])
    try:
        sources = dict(project_module_sources(root))
    except StableReadError as exc:
        sources = {}
        errors.append(exc.detail)
    try:
        artifacts = built_module_artifacts(root)
    except StableReadError as exc:
        artifacts = {}
        errors.append(exc.detail)
    for module in modules:
        source = sources.get(module)
        artifact = artifacts.get(module)
        row = {
            "module": module,
            "source": "",
            "source_sha256": "",
            "compiled": "",
            "compiled_sha256": "",
            "scope": "local" if source is not None or artifact is not None else "external_or_dependency",
        }
        for kind, path in (("source", source), ("compiled", artifact)):
            if path is None:
                continue
            row[kind] = str(path.relative_to(root))
            try:
                digest = stable_regular_file_sha256(
                    path,
                    max_bytes=(
                        SOURCE_MAX_BYTES if kind == "source" else COMPILED_MODULE_MAX_BYTES
                    ),
                )
            except StableReadError as exc:
                errors.append(f"{module} {kind}: {exc.detail}")
            else:
                row[f"{kind}_sha256"] = digest
        if source is not None and artifact is None:
            errors.append(f"{module}: local source has no stable local compiled module")
        if artifact is not None and source is None:
            errors.append(f"{module}: local compiled module has no stable local source")
        rows.append(row)
    material = {"project_context": context["files"], "modules": rows}
    fingerprint = hashlib.sha256(
        json.dumps(material, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "fingerprint": fingerprint,
        "project_context": context["files"],
        "modules": rows,
        "errors": errors,
    }


def stale_project_modules(root: Path, modules: list[str]) -> list[str]:
    """Obvious source-newer-than-olean mismatches that cannot support a claim."""

    sources = dict(project_module_sources(root))
    artifacts = built_module_artifacts(root)
    stale: list[str] = []
    for module in modules:
        source = sources.get(module)
        artifact = artifacts.get(module)
        if source is None or artifact is None:
            continue
        try:
            if source.stat().st_mtime_ns > artifact.stat().st_mtime_ns:
                stale.append(module)
        except OSError:
            stale.append(module)
    return stale


def built_project_modules(root: Path) -> tuple[list[str], list[str]]:
    """Split the project's modules into the ones Lake built and the rest.

    Both audits work on compiled modules, so a source Lake never built is not
    importable wherever it sits under the root. Staged proof artifacts are the
    common case — a loop directory inside the project holds copies of the very
    file under audit — and naming one in the harness aborts it with ``unknown
    module prefix`` before a single line of evidence is produced. Reading the
    olean tree asks the build itself which modules exist rather than guessing
    from the directory layout.
    """
    built = built_module_names(root)
    present = [module for module, _ in project_module_sources(root) if module in built]
    missing = [module for module, _ in project_module_sources(root) if module not in built]
    return present, missing


def project_declaration_scan(root: Path, modules: list[str] | None = None) -> DeclarationScan:
    """Declaration names across the project, optionally limited to modules.

    Limiting matters for the audit: a declaration that lives only in a source
    Lake never built cannot be resolved against the compiled environment, and
    would be reported as an unresolved declaration — a refusal caused by a
    stray copy rather than by anything wrong with the proof. An unparsed line
    is reported with its module, since the line alone rarely says where to look.
    """
    allowed = set(modules) if modules is not None else None
    names: list[str] = []
    private: list[str] = []
    unparsed: list[str] = []
    for module, lean_file in project_module_sources(root):
        if allowed is not None and module not in allowed:
            continue
        try:
            text = stable_regular_file_bytes(lean_file).decode("utf-8")
        except StableReadError as exc:
            unparsed.append(
                f"{SOURCE_UNREADABLE_PREFIX}{module}: source could not be read ({exc.kind})"
            )
            continue
        except UnicodeDecodeError:
            unparsed.append(
                f"{SOURCE_UNREADABLE_PREFIX}{module}: source is not valid UTF-8"
            )
            continue
        _stripped, lexical_errors = _strip_comments_and_strings(text)
        if lexical_errors:
            unparsed.extend(
                f"{SOURCE_UNREADABLE_PREFIX}{module}: {detail}"
                for detail in lexical_errors
            )
        found = scan_declarations(text)
        names.extend(found.names)
        private.extend(found.private)
        unparsed.extend(f"{module}: {line}" for line in found.unparsed)

    def _unique(values: list[str]) -> list[str]:
        seen: set[str] = set()
        return [v for v in values if not (v in seen or seen.add(v))]

    return DeclarationScan(_unique(names), _unique(private), _unique(unparsed))


def project_declarations(root: Path, modules: list[str] | None = None) -> list[str]:
    """The auditable half of :func:`project_declaration_scan`."""
    return project_declaration_scan(root, modules).names


def parse_axiom_report(text: str) -> dict[str, list[str]]:
    """Map declaration -> axiom list from ``#print axioms`` output.

    Lean wraps long messages, so the bracket body is matched across newlines.
    """
    observed: dict[str, list[str]] = {}
    for match in _AXIOM_NONE_RE.finditer(text):
        observed[match.group("decl")] = []
    for match in _AXIOM_DEPENDS_RE.finditer(text):
        observed[match.group("decl")] = [
            item.strip() for item in match.group("axioms").split(",") if item.strip()
        ]
    return observed


def axiom_audit_payload(
    input_path: Path,
    *,
    project_root: Path,
    timeout: int,
    declarations: list[str],
    imports: list[str],
    allowed_axioms: set[str],
    strict: bool,
) -> dict[str, Any]:
    supplied_project = project_root.expanduser()
    root = supplied_project.resolve()
    sanctioned = (SANCTIONED_AXIOMS | allowed_axioms) - NEVER_SANCTIONED_AXIOMS
    payload: dict[str, Any] = {
        "schema_version": "lean-strict-verification-gate.v1",
        "ok": True,
        "input": str(input_path),
        "mode": "axiom-audit",
        "strict": strict,
        "axiom_audit_status": "not_run",
        "sanctioned_axioms": sorted(sanctioned),
        "declarations": [],
        "unsanctioned_axioms": [],
        "compiler_trust_axioms": [],
        "findings": [],
        "audit_command": "lake env lean <harness>",
        "audit_cwd": str(root),
        "audit_stdout": "",
        "audit_stderr": "",
        "limitations": [
            "declaration discovery is a regex walk, not a Lean parser",
            "the audit needs an already-built project: it imports compiled modules",
            "sources Lake did not build are skipped, so their declarations go unaudited",
            "#print axioms reports the trust base, not statement equivalence",
            "source-newer-than-olean is an mtime preflight, not proof that compiled content came from source",
            "explicit dependency modules outside the project root are resolved by Lake but not content-hashed",
            "pre/post hashes detect endpoint differences, not a change-and-restore between snapshots",
            "Lean, Lake, their launchers, environment, and dependency resolution are trusted inputs, not content-attested",
        ],
    }

    def fail(kind: str, detail: str, status: str) -> dict[str, Any]:
        payload["ok"] = False
        payload["axiom_audit_status"] = status
        payload["findings"].append({"kind": kind, "detail": detail})
        return payload

    supplied_input = input_path.expanduser()
    if (
        supplied_project.is_symlink()
        or supplied_input.is_symlink()
        or not supplied_input.is_dir()
        or supplied_input.resolve() != root
    ):
        return fail(
            "input_project_mismatch",
            "--input and --project-root must identify the same stable project directory",
            "command_failed",
        )
    if not root.is_dir():
        return fail("missing_project", "project root does not exist", "command_failed")
    status = project_status(root)
    payload["project_status"] = status
    if not status["lake_workspace_detected"]:
        return fail(
            "missing_lakefile",
            "project root must contain lakefile.lean or lakefile.toml",
            "command_failed",
        )

    # Modules come first: what the harness can import decides which
    # declarations there is any point in asking about.
    if imports:
        modules, unbuilt = imports, []
    else:
        try:
            modules, unbuilt = built_project_modules(root)
        except StableReadError as exc:
            return fail("evidence_unreadable", exc.detail, "command_failed")
        payload["modules_skipped_unbuilt"] = unbuilt
    invalid_modules = [module for module in modules if not valid_lean_name(module)]
    if invalid_modules:
        return fail(
            "invalid_import",
            f"invalid module name: {invalid_modules[0]!r}",
            "command_failed",
        )
    if len(modules) != len(set(modules)):
        return fail("invalid_import", "duplicate import module", "command_failed")
    if not modules:
        return fail(
            "project_not_built" if unbuilt else "no_modules",
            (
                f"no compiled module under {root / '.lake' / 'build' / 'lib'}: "
                "build the project before auditing"
                if unbuilt
                else "no importable module found under the project root"
            ),
            "command_failed",
        )
    if len(modules) > AXIOM_AUDIT_MAX_MODULES:
        return fail(
            "too_many_modules",
            f"{len(modules)} modules exceed the {AXIOM_AUDIT_MAX_MODULES} cap; pass --import",
            "command_failed",
        )
    evidence_before = project_evidence_snapshot(root, modules)
    payload["evidence_input_fingerprint"] = evidence_before["fingerprint"]
    payload["evidence_manifest"] = {
        "project_context": evidence_before["project_context"],
        "modules": evidence_before["modules"],
    }
    if evidence_before["errors"]:
        return fail(
            "evidence_unreadable",
            evidence_before["errors"][0],
            "command_failed",
        )
    try:
        stale_modules = stale_project_modules(root, modules)
    except StableReadError as exc:
        return fail("evidence_unreadable", exc.detail, "command_failed")
    payload["modules_stale"] = stale_modules
    if stale_modules:
        return fail(
            "stale_compiled_module",
            "source is newer than compiled evidence: " + ", ".join(stale_modules),
            "command_failed",
        )

    if declarations:
        targets, private_targets, unparsed_lines = declarations, [], []
    else:
        try:
            targets, private_targets, unparsed_lines = project_declaration_scan(root, modules)
        except StableReadError as exc:
            return fail("evidence_unreadable", exc.detail, "command_failed")
    payload["declarations_requested"] = len(targets)
    invalid_targets = [
        declaration
        for declaration in targets
        if not valid_lean_name(declaration, allow_root_prefix=True)
    ]
    if invalid_targets:
        return fail(
            "invalid_declaration",
            f"invalid declaration name: {invalid_targets[0]!r}",
            "command_failed",
        )
    if len(targets) != len(set(targets)):
        return fail(
            "invalid_declaration",
            "duplicate declaration target",
            "command_failed",
        )
    if unparsed_lines:
        # A line the walk could not read a name off is a coverage hole, not a
        # warning: it may have hidden a theorem, and an audit that reports a
        # clean trust base over a partial scan is worse than no audit. Refuse
        # and name the lines so the operator can pass --declaration instead.
        source_errors = [
            line.removeprefix(SOURCE_UNREADABLE_PREFIX)
            for line in unparsed_lines
            if line.startswith(SOURCE_UNREADABLE_PREFIX)
        ]
        declaration_errors = [
            line
            for line in unparsed_lines
            if not line.startswith(SOURCE_UNREADABLE_PREFIX)
        ]
        shown = declaration_errors[:AXIOM_AUDIT_MAX_UNPARSED]
        if shown:
            payload["declarations_unparsed"] = shown
        payload["findings"].extend(
            {"kind": "declaration_unparsed", "detail": line} for line in shown
        )
        if len(declaration_errors) > len(shown):
            payload["findings"].append(
                {
                    "kind": "declaration_unparsed",
                    "detail": f"{len(declaration_errors) - len(shown)} further unparsed declaration lines",
                }
            )
        payload["findings"].extend(
            {"kind": "source_unreadable", "detail": detail}
            for detail in source_errors[:AXIOM_AUDIT_MAX_UNPARSED]
        )
        if len(source_errors) > AXIOM_AUDIT_MAX_UNPARSED:
            payload["findings"].append(
                {
                    "kind": "source_unreadable",
                    "detail": (
                        f"{len(source_errors) - AXIOM_AUDIT_MAX_UNPARSED} "
                        "further unreadable source files"
                    ),
                }
            )
        payload["ok"] = False
    if private_targets:
        # Named, not hidden: an operator reading the report can see exactly
        # which proofs the harness was unable to ask about.
        payload["declarations_skipped_private"] = private_targets
        payload["limitations"].append(
            "private theorems cannot be named by an importing harness, so they went "
            "unaudited; a public theorem that uses one still inherits its axioms"
        )
    if not targets:
        payload["axiom_audit_status"] = "no_declarations"
        payload["findings"].append(
            {"kind": "no_declarations", "detail": "no theorem or lemma found to audit"}
        )
        payload["ok"] = False
        return payload
    if len(targets) > AXIOM_AUDIT_MAX_DECLARATIONS:
        return fail(
            "too_many_declarations",
            f"{len(targets)} declarations exceed the {AXIOM_AUDIT_MAX_DECLARATIONS} cap; pass --declaration",
            "command_failed",
        )

    lake = tool_status("lake")
    payload["tool_status"] = {"lake": lake}
    if lake["status"] != "available":
        payload["axiom_audit_status"] = "tool_unavailable"
        payload["limitations"].append("lake was not found: no axiom evidence was produced")
        return payload

    with tempfile.TemporaryDirectory() as tmp:
        harness = Path(tmp) / "AasAxiomAudit.lean"
        harness.write_text(
            "\n".join(
                [*(f"import {module}" for module in modules), *(f"#print axioms {name}" for name in targets)]
            )
            + "\n",
            encoding="utf-8",
        )
        command = [lake["path"], "env", "lean", str(harness)]
        try:
            completed = run_bounded_command(
                command,
                timeout=timeout,
                cwd=root,
                max_output_bytes=COMMAND_OUTPUT_MAX_BYTES,
                capture_evidence=True,
            )
        except subprocess.TimeoutExpired as exc:
            if isinstance(getattr(exc, "process_evidence", None), dict):
                payload["process_evidence"] = exc.process_evidence
            return fail("audit_timeout", f"timeout after {timeout} seconds", "command_failed")
        except CommandOutputLimitExceeded as exc:
            if isinstance(getattr(exc, "process_evidence", None), dict):
                payload["process_evidence"] = exc.process_evidence
            payload["audit_stdout"] = exc.stdout
            payload["audit_stderr"] = (exc.stderr + "\n" + str(exc))[-4000:]
            return fail("audit_output_limit", str(exc), "command_failed")
        except OSError as exc:
            if isinstance(getattr(exc, "process_evidence", None), dict):
                payload["process_evidence"] = exc.process_evidence
            return fail("audit_failed", str(exc), "command_failed")

    if isinstance(getattr(completed, "process_evidence", None), dict):
        payload["process_evidence"] = completed.process_evidence
    evidence_after = project_evidence_snapshot(root, modules)
    payload["post_audit_evidence_fingerprint"] = evidence_after["fingerprint"]
    if (
        evidence_after["errors"]
        or evidence_before["fingerprint"] != evidence_after["fingerprint"]
    ):
        return fail(
            "evidence_changed_during_audit",
            "source, compiled modules, or Lake configuration changed during axiom audit",
            "command_failed",
        )
    payload["audit_stdout"] = completed.stdout[-4000:]
    payload["audit_stderr"] = completed.stderr[-4000:]
    observed = parse_axiom_report(completed.stdout)
    if not observed:
        return fail(
            "audit_produced_no_report",
            f"lake env lean exited {completed.returncode} without any #print axioms output",
            "command_failed",
        )

    unsanctioned: set[str] = set()
    compiler_trust: set[str] = set()
    for name in targets:
        if name not in observed:
            payload["declarations"].append(
                {"declaration": name, "axioms": [], "status": "unresolved"}
            )
            payload["findings"].append({"kind": "declaration_unresolved", "detail": name})
            payload["ok"] = False
            continue
        axioms = observed[name]
        offending = [axiom for axiom in axioms if axiom not in sanctioned]
        # A compiler-trust axiom the operator allowlisted passes, but it is
        # never allowed to read as an ordinary kernel-checked proof.
        compiler_dependencies = [axiom for axiom in axioms if is_compiler_trust_axiom(axiom)]
        if offending:
            status = "unsanctioned_axiom"
        elif compiler_dependencies:
            status = "sanctioned_compiler_trust"
        else:
            status = "sanctioned"
        payload["declarations"].append(
            {"declaration": name, "axioms": axioms, "status": status}
        )
        for axiom in compiler_dependencies:
            compiler_trust.add(axiom)
            payload["findings"].append(
                {"kind": "compiler_trust_axiom", "detail": axiom, "declaration": name}
            )
        for axiom in offending:
            unsanctioned.add(axiom)
            payload["findings"].append(
                {"kind": "unsanctioned_axiom", "detail": axiom, "declaration": name}
            )
            payload["ok"] = False

    payload["unsanctioned_axioms"] = sorted(unsanctioned)
    payload["compiler_trust_axioms"] = sorted(compiler_trust)
    if compiler_trust:
        payload["limitations"].append(
            "a compiler-trust axiom means the proof rests on native "
            "evaluation and the compiler, not on the kernel alone"
        )
    if completed.returncode != 0:
        return fail(
            "audit_command_failed",
            f"lake env lean exited {completed.returncode}",
            "command_failed",
        )
    payload["axiom_audit_status"] = "audited"
    return payload


def lean4checker_status() -> dict[str, Any]:
    """Resolve an operator-selected checker without trusting project build output."""
    env_var = TOOL_ENV["lean4checker"]
    env_value = os.environ.get(env_var, "").strip()
    if env_value:
        resolved = resolve_candidate(env_value)
        return {
            "status": "available" if resolved else "tool_unavailable",
            "path": resolved or env_value,
            "source": "env",
            "env_var": env_var,
        }
    path = resolve_candidate("lean4checker")
    if path:
        return {"status": "available", "path": path, "source": "path"}
    return {"status": "tool_unavailable", "path": "", "source": "not-found"}


def toolchain_leanchecker_status(
    lake_path: str,
    project_root: Path,
    *,
    timeout: float,
    max_output_bytes: int,
) -> tuple[dict[str, Any], int]:
    """The leanchecker of the toolchain that ``lake_path`` runs, and the output bytes spent.

    Lean toolchains ship leanchecker from v4.28. ``lake env`` with no command
    prints the environment Lake runs a command in, and its LEAN_SYSROOT is the
    toolchain directory. ``<LEAN_SYSROOT>/bin/leanchecker`` is taken only when
    it is a regular file whose real path is outside ``project_root``. The file
    beside ``lake_path`` is never taken: under elan it is a proxy that follows
    the project's lean-toolchain, and it fails for a toolchain that ships no
    leanchecker. Every other outcome is tool_unavailable with a ``detail``
    naming the reason; nothing here raises. The byte count is 0 when ``lake
    env`` did not finish, and kernel-check then stops anyway.
    """

    def unavailable(detail: str) -> dict[str, Any]:
        return {"status": "tool_unavailable", "path": "", "source": "not-found", "detail": detail}

    try:
        completed = run_bounded_command(
            [lake_path, "env"],
            timeout=timeout,
            cwd=project_root,
            max_output_bytes=max_output_bytes,
        )
    except subprocess.TimeoutExpired:
        return unavailable(
            f"lake env did not finish within the {timeout:.1f} seconds left of the --timeout budget"
        ), 0
    except CommandOutputLimitExceeded as exc:
        return unavailable(f"lake env: {exc}"), 0
    except (OSError, ValueError) as exc:
        return unavailable(f"lake env could not run: {exc}"), 0
    used = completed.output_bytes
    if completed.returncode != 0:
        reason = _output_tail(completed.stderr.strip(), 500)
        return unavailable(
            f"lake env exited {completed.returncode}" + (f": {reason}" if reason else "")
        ), used
    sysroots = [
        line[len("LEAN_SYSROOT="):]
        for line in completed.stdout.splitlines()
        if line.startswith("LEAN_SYSROOT=")
    ]
    if not sysroots:
        return unavailable("lake env printed no LEAN_SYSROOT"), used
    if len(sysroots) > 1:
        return unavailable(f"lake env printed {len(sysroots)} LEAN_SYSROOT lines"), used
    sysroot = sysroots[0]
    shown = _output_tail(sysroot, 500)
    if not Path(sysroot).is_absolute():
        return unavailable(
            f"lake env printed a LEAN_SYSROOT that is not an absolute path: {shown!r}"
        ), used
    try:
        root = Path(os.path.realpath(project_root))
        checker = Path(os.path.realpath(Path(sysroot) / "bin" / executable_name("leanchecker")))
        inside_project = checker == root or root in checker.parents
        regular = _regular_file(checker)
    except (OSError, RuntimeError, ValueError) as exc:
        return unavailable(f"the leanchecker under LEAN_SYSROOT {shown} cannot be used: {exc}"), used
    if inside_project:
        return unavailable(
            f"the leanchecker under LEAN_SYSROOT {shown} resolves inside the project root"
        ), used
    if not regular:
        return unavailable(
            f"the toolchain at LEAN_SYSROOT {shown} has no leanchecker; Lean ships it from v4.28"
        ), used
    return {
        "status": "available",
        "path": str(checker),
        "source": "toolchain",
        "name": "leanchecker",
    }, used


def kernel_check_payload(
    input_path: Path,
    *,
    project_root: Path,
    timeout: int,
    modules: list[str],
    strict: bool,
) -> dict[str, Any]:
    supplied_project = project_root.expanduser()
    root = supplied_project.resolve()
    payload: dict[str, Any] = {
        "schema_version": "lean-strict-verification-gate.v1",
        "ok": True,
        "input": str(input_path),
        "mode": "kernel-check",
        "strict": strict,
        "kernel_check_status": "not_run",
        "runner": "lake-env-lean4checker",
        "modules": [],
        "findings": [],
        "kernel_check_cwd": str(root),
        "kernel_check_stdout": "",
        "kernel_check_stderr": "",
        "limitations": [
            "a passing lake build is not a kernel replay: only lean4checker or leanchecker re-checks proof terms",
            "modules are replayed as compiled, so the audit inherits the build's own toolchain",
            "sources Lake did not build are skipped, so they are never replayed",
            "--timeout is the total budget for every module, not a per-module allowance",
            "source-newer-than-olean is an mtime preflight, not proof that compiled content came from source",
            "explicit dependency modules outside the project root are resolved by Lake but not content-hashed",
            "pre/post hashes detect endpoint differences, not a change-and-restore between snapshots",
            "Lean, Lake, lean4checker or leanchecker, their launchers, environment, and dependency resolution are trusted inputs, not content-attested",
        ],
    }

    def fail(kind: str, detail: str, status: str) -> dict[str, Any]:
        payload["ok"] = False
        payload["kernel_check_status"] = status
        payload["findings"].append({"kind": kind, "detail": detail})
        return payload

    supplied_input = input_path.expanduser()
    if (
        supplied_project.is_symlink()
        or supplied_input.is_symlink()
        or not supplied_input.is_dir()
        or supplied_input.resolve() != root
    ):
        return fail(
            "input_project_mismatch",
            "--input and --project-root must identify the same stable project directory",
            "command_failed",
        )
    if not root.is_dir():
        return fail("missing_project", "project root does not exist", "command_failed")
    status = project_status(root)
    payload["project_status"] = status
    if not status["lake_workspace_detected"]:
        return fail(
            "missing_lakefile",
            "project root must contain lakefile.lean or lakefile.toml",
            "command_failed",
        )

    if modules:
        targets, unbuilt = modules, []
    else:
        try:
            targets, unbuilt = built_project_modules(root)
        except StableReadError as exc:
            return fail("evidence_unreadable", exc.detail, "command_failed")
        payload["modules_skipped_unbuilt"] = unbuilt
    invalid_targets = [module for module in targets if not valid_lean_name(module)]
    if invalid_targets:
        return fail(
            "invalid_module",
            f"invalid module name: {invalid_targets[0]!r}",
            "command_failed",
        )
    if len(targets) != len(set(targets)):
        return fail("invalid_module", "duplicate module target", "command_failed")
    if not targets:
        return fail(
            "project_not_built" if unbuilt else "no_modules",
            (
                f"no compiled module under {root / '.lake' / 'build' / 'lib'}: "
                "build the project before replaying it"
                if unbuilt
                else "no importable module found under the project root"
            ),
            "command_failed",
        )
    if len(targets) > KERNEL_CHECK_MAX_MODULES:
        return fail(
            "too_many_modules",
            f"{len(targets)} modules exceed the {KERNEL_CHECK_MAX_MODULES} cap; pass --module",
            "command_failed",
        )
    evidence_before = project_evidence_snapshot(root, targets)
    payload["evidence_input_fingerprint"] = evidence_before["fingerprint"]
    payload["evidence_manifest"] = {
        "project_context": evidence_before["project_context"],
        "modules": evidence_before["modules"],
    }
    if evidence_before["errors"]:
        return fail(
            "evidence_unreadable",
            evidence_before["errors"][0],
            "command_failed",
        )
    try:
        stale_modules = stale_project_modules(root, targets)
    except StableReadError as exc:
        return fail("evidence_unreadable", exc.detail, "command_failed")
    payload["modules_stale"] = stale_modules
    if stale_modules:
        return fail(
            "stale_compiled_module",
            "source is newer than compiled evidence: " + ", ".join(stale_modules),
            "command_failed",
        )

    lake = tool_status("lake")
    checker = lean4checker_status()
    # One budget for the whole verb, the `lake env` that finds the toolchain's
    # leanchecker included: a per-module allowance would let 50 modules run
    # 50x past the timeout the caller bounded the process with, and the caller
    # would see a killed gate instead of a replay verdict.
    output_remaining = COMMAND_OUTPUT_MAX_BYTES
    deadline = time.monotonic() + timeout
    if checker["source"] == "not-found" and lake["status"] == "available":
        checker, used = toolchain_leanchecker_status(
            lake["path"],
            root,
            timeout=deadline - time.monotonic(),
            max_output_bytes=output_remaining,
        )
        output_remaining -= used
    if checker["status"] == "available":
        checker_path = Path(checker["path"])
        checker["inside_project_root"] = checker_path == root or root in checker_path.parents
        if checker["inside_project_root"]:
            payload["limitations"].append(
                "the explicitly selected lean4checker is inside the project root and is therefore project-controlled, not an independent verifier"
            )
    payload["tool_status"] = {"lake": lake, "lean4checker": checker}
    if lake["status"] != "available" or checker["status"] != "available":
        payload["kernel_check_status"] = "tool_unavailable"
        payload["limitations"].append(
            "lean4checker, leanchecker or lake was not found: no kernel replay evidence was produced"
        )
        return payload

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    for module in targets:
        if output_remaining <= 0:
            payload["kernel_check_stdout"] = "\n".join(stdout_chunks)[-4000:]
            payload["kernel_check_stderr"] = "\n".join(stderr_chunks)[-4000:]
            return fail(
                "kernel_check_output_limit",
                f"combined stdout/stderr exhausted the {COMMAND_OUTPUT_MAX_BYTES}-byte budget before {module}",
                "command_failed",
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            payload["kernel_check_stdout"] = "\n".join(stdout_chunks)[-4000:]
            payload["kernel_check_stderr"] = "\n".join(stderr_chunks)[-4000:]
            return fail(
                "kernel_check_timeout",
                f"{timeout}s budget was exhausted before {module}",
                "command_failed",
            )
        command = [lake["path"], "env", checker["path"], module]
        try:
            completed = run_bounded_command(
                command,
                timeout=remaining,
                cwd=root,
                max_output_bytes=output_remaining,
                capture_evidence=True,
            )
        except subprocess.TimeoutExpired as exc:
            if isinstance(getattr(exc, "process_evidence", None), dict):
                payload.setdefault("process_evidence", []).append({"module": module, "evidence": exc.process_evidence})
            payload["kernel_check_stdout"] = "\n".join(stdout_chunks)[-4000:]
            payload["kernel_check_stderr"] = "\n".join(stderr_chunks)[-4000:]
            return fail("kernel_check_timeout", f"{module}: timeout after {timeout} seconds", "command_failed")
        except CommandOutputLimitExceeded as exc:
            if isinstance(getattr(exc, "process_evidence", None), dict):
                payload.setdefault("process_evidence", []).append({"module": module, "evidence": exc.process_evidence})
            stdout_chunks.append(exc.stdout)
            stderr_chunks.append(exc.stderr)
            payload["kernel_check_stdout"] = "\n".join(stdout_chunks)[-4000:]
            payload["kernel_check_stderr"] = (
                "\n".join(stderr_chunks) + "\n" + str(exc)
            )[-4000:]
            return fail(
                "kernel_check_output_limit",
                f"{module}: {exc}",
                "command_failed",
            )
        except OSError as exc:
            if isinstance(getattr(exc, "process_evidence", None), dict):
                payload.setdefault("process_evidence", []).append({"module": module, "evidence": exc.process_evidence})
            payload["kernel_check_stdout"] = "\n".join(stdout_chunks)[-4000:]
            payload["kernel_check_stderr"] = "\n".join(stderr_chunks)[-4000:]
            return fail("kernel_check_failed", f"{module}: {exc}", "command_failed")
        if isinstance(getattr(completed, "process_evidence", None), dict):
            payload.setdefault("process_evidence", []).append({"module": module, "evidence": completed.process_evidence})
        output_remaining -= completed.output_bytes
        stdout_chunks.append(completed.stdout[-4000:])
        stderr_chunks.append(completed.stderr[-4000:])
        replayed = completed.returncode == 0
        payload["modules"].append(
            {"module": module, "status": "kernel_checked" if replayed else "kernel_check_failed"}
        )
        if not replayed:
            payload["ok"] = False
            payload["findings"].append({"kind": "kernel_check_failed", "detail": module})

    payload["kernel_check_stdout"] = "\n".join(stdout_chunks)[-4000:]
    payload["kernel_check_stderr"] = "\n".join(stderr_chunks)[-4000:]
    evidence_after = project_evidence_snapshot(root, targets)
    payload["post_kernel_evidence_fingerprint"] = evidence_after["fingerprint"]
    if (
        evidence_after["errors"]
        or evidence_before["fingerprint"] != evidence_after["fingerprint"]
    ):
        return fail(
            "evidence_changed_during_kernel_check",
            "source, compiled modules, or Lake configuration changed during kernel replay",
            "command_failed",
        )
    payload["kernel_check_status"] = "kernel_checked" if payload["ok"] else "kernel_check_failed"
    return payload


def challenge_freeze_payload(
    source: Path,
    *,
    out_dir: Path,
    theorems: list[str],
    definitions: list[str],
    permitted_axioms: list[str],
    allow_compiler_trust: bool,
    paranoid: bool,
    toolchain: str,
) -> dict[str, Any]:
    """Freeze a reviewed challenge: copy its bytes and pin a comparator record.

    ``out_dir`` receives ``Challenge.lean``, the exact source bytes, and
    ``challenge.json``, the closed ``aas_lean_challenge.v1`` record. The record
    holds no timestamp, so the same inputs and the same toolchain bytes always
    give the same bytes, and their sha256 (``record_sha256``) is the pin a
    caller keeps. Its ``toolchain`` is the spec with toolchain_file_digests()
    of the installed toolchain that resolve_comparator_toolchain() finds
    outside the source's directory, and every import must be a module that
    toolchain ships. A refusal is a finding and leaves nothing behind. No
    tool runs here: the checks read names and bytes, and whether the
    statement says what the claim means stays a review step.
    """

    payload: dict[str, Any] = {
        "schema_version": "lean-strict-verification-gate.v1",
        "ok": False,
        "mode": "challenge-freeze",
        "input": str(source),
        "out": str(out_dir),
        "findings": [],
        "challenge_record": "",
        "challenge_file": "",
        "record_sha256": "",
        "challenge_sha256": "",
        "advisory_scan": {},
        "limitations": [
            "statement fidelity, whether the challenge states the intended claim, "
            "still needs a human or different-family review",
            "the import and construct checks and advisory_scan are an advisory preflight; "
            "a proof supports a claim only when a host-run lake comparator accepts it "
            "against this record",
            "theorem names are found by a line walk over the source, not by Lean",
            "the record binds the challenge bytes, the comparator options and the "
            "toolchain bytes seen at freeze time; a changed toolchain, or one on another "
            "host or architecture, does not match it and needs a new freeze",
        ],
        "toolchain_dir": "",
    }
    findings: list[dict[str, str]] = payload["findings"]
    created_files: list[Path] = []
    created_dirs: list[Path] = []
    changed_modes: list[tuple[Path, int]] = []
    try:
        version = (
            parse_lean_version(toolchain)
            if isinstance(toolchain, str) and _LEAN_TOOLCHAIN_SPEC_RE.fullmatch(toolchain)
            else None
        )
        toolchain_dir = ""
        toolchain_files: dict[str, str] = {}
        if version is None or version < COMPARATOR_MIN_LEAN_VERSION:
            findings.append(
                {
                    "kind": "unsupported_toolchain",
                    "detail": (
                        f"{toolchain}: lake comparator needs a stable "
                        "leanprover/lean4:vX.Y.Z release, v4.35.0 or later"
                    ),
                }
            )
        else:
            # The record pins the bytes of the installed toolchain, which must
            # not lie in the directory of the source under review.
            resolved = resolve_comparator_toolchain(toolchain, input_root=source.parent)
            if resolved["status"] != "available":
                findings.append({"kind": "toolchain_unavailable", "detail": resolved["detail"]})
            else:
                digests = toolchain_file_digests(resolved["path"])
                if digests["status"] != "ok":
                    findings.append({"kind": "toolchain_unavailable", "detail": digests["detail"]})
                else:
                    toolchain_dir = resolved["path"]
                    toolchain_files = digests["files"]

        axioms = sorted(set(permitted_axioms)) if permitted_axioms else sorted(SANCTIONED_AXIOMS)
        for axiom in axioms:
            if axiom in SANCTIONED_AXIOMS:
                continue
            if axiom in NEVER_SANCTIONED_AXIOMS:
                findings.append(
                    {"kind": "never_sanctioned_axiom", "detail": f"{axiom} can never be permitted"}
                )
            elif valid_lean_name(axiom) and is_compiler_trust_axiom(axiom):
                if not allow_compiler_trust:
                    findings.append(
                        {
                            "kind": "compiler_trust_not_allowed",
                            "detail": f"{axiom} trusts the compiler; it needs --allow-compiler-trust",
                        }
                    )
            else:
                findings.append(
                    {"kind": "never_sanctioned_axiom", "detail": f"{axiom} is not a sanctioned axiom"}
                )

        # A definition hole lets the solution choose the definition itself.
        for name in sorted(set(definitions)):
            findings.append(
                {
                    "kind": "definition_hole_not_allowed",
                    "detail": f"{name}: a challenge names theorems only, never definition holes",
                }
            )

        theorem_names = sorted(set(theorems))
        if not theorem_names:
            findings.append({"kind": "theorem_not_declared", "detail": "no theorem was named"})
        for name in theorem_names:
            if not valid_lean_name(name):
                findings.append(
                    {"kind": "theorem_not_declared", "detail": f"{name} is not a valid Lean name"}
                )
            elif "_root_" in name.split("."):
                # `theorem _root_.foo` inside `namespace N` declares `foo`, which
                # the line walk reports as `N._root_.foo`.
                findings.append(
                    {
                        "kind": "theorem_not_declared",
                        "detail": (
                            f"{name} has a _root_ component; the line walk cannot tell "
                            "which name Lean declares for it"
                        ),
                    }
                )

        source_bytes = b""
        text: str | None = None
        try:
            source_bytes = stable_regular_file_bytes(source, max_bytes=CHALLENGE_SOURCE_MAX_BYTES)
            text = source_bytes.decode("utf-8")
        except StableReadError as exc:
            findings.append({"kind": "source_unreadable", "detail": f"{exc.kind}: {exc.detail}"})
        except UnicodeDecodeError:
            findings.append(
                {"kind": "source_unreadable", "detail": "invalid_utf8: the source is not valid UTF-8"}
            )

        imports: list[str] = []
        if text is not None:
            stripped, lexical_errors = _strip_comments_and_strings(text)
            if lexical_errors:
                # Where this lexer and Lean's disagree, as on a raw string, a
                # construct can hide in what the lexer takes for a string.
                findings.append(
                    {
                        "kind": "challenge_unsafe_construct",
                        "detail": f"lexical_scan_incomplete: {lexical_errors[0]}",
                    }
                )
            for module in imported_modules(stripped):
                if valid_lean_name(module) and module.split(".", 1)[0] in CORE_IMPORT_ROOTS:
                    imports.append(module)
                    # Lean looks for `import A.B` at <toolchain>/lib/lean/A/B.olean,
                    # so a module the toolchain does not ship can never build.
                    *package, name = module.split(".")
                    if toolchain_dir and not _regular_file(
                        Path(toolchain_dir, "lib", "lean", *package, name + ".olean")
                    ):
                        findings.append(
                            {
                                "kind": "challenge_import_not_trusted",
                                "detail": f"{module}: not shipped by {toolchain}",
                            }
                        )
                else:
                    findings.append(
                        {"kind": "challenge_import_not_trusted", "detail": module or "<missing>"}
                    )
            for pattern_name, pattern in SAFETY_PATTERNS.items():
                if pattern.search(stripped):
                    findings.append({"kind": "challenge_unsafe_construct", "detail": pattern_name})
            # A solution may declare the same axiom as the challenge, so an
            # axiom the challenge declares under a permitted compiler-trust
            # name proves anything: lake comparator v4.35.0 accepted `1 = 2`
            # that way. No other axiom can be permitted, so a challenge
            # declares none.
            if TRUST_BASE_PATTERNS["axiom"].search(stripped):
                findings.append(
                    {
                        "kind": "challenge_unsafe_construct",
                        "detail": "axiom: a challenge may not declare an axiom",
                    }
                )
            declarations = scan_declarations(text)
            if declarations.unparsed:
                # A line the walk cannot read may open the namespace Lean puts
                # a name in, as `namespace «A B»` does, so no name it reports
                # can be trusted.
                findings.append(
                    {
                        "kind": "theorem_not_declared",
                        "detail": (
                            f"unparsed declaration lines: {len(declarations.unparsed)}; "
                            f"the first is: {declarations.unparsed[0]}"
                        ),
                    }
                )
            # `lemma` is not a core Lean command, so a core challenge that
            # declares anything with it does not build.
            lemma_lines = [
                line.strip()
                for line in stripped.splitlines()
                if _DECLARATION_RE.match(line)
                and _DECLARATION_KEYWORD_RE.findall(line) == ["lemma"]
            ]
            if lemma_lines:
                findings.append(
                    {
                        "kind": "theorem_not_declared",
                        "detail": (
                            "lemma is not a core Lean command, so this core challenge cannot "
                            f"build: {lemma_lines[0]}"
                        ),
                    }
                )
            for name in theorem_names:
                if valid_lean_name(name) and name not in declarations.names:
                    findings.append(
                        {
                            "kind": "theorem_not_declared",
                            "detail": (
                                f"{name} is not declared as a public theorem in the source "
                                f"(unparsed declaration lines: {len(declarations.unparsed)})"
                            ),
                        }
                    )

        out_info, out_problem = _challenge_output_dir(out_dir)
        if out_problem:
            findings.append({"kind": "output_dir_unusable", "detail": out_problem})
        if findings:
            return payload

        record = {
            "schema_version": CHALLENGE_RECORD_SCHEMA,
            "challenge_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "challenge_module": CHALLENGE_MODULE,
            "theorem_names": theorem_names,
            "definition_names": [],
            "permitted_axioms": axioms,
            "paranoid": bool(paranoid),
            "toolchain": {"spec": toolchain, "files": toolchain_files},
            "workspace": {"kind": "core"},
            "allowed_import_roots": list(CORE_IMPORT_ROOTS),
        }
        record_bytes = (
            json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        ).encode("ascii")
        challenge_path = out_dir / CHALLENGE_FILE
        record_path = out_dir / CHALLENGE_RECORD_FILE
        try:
            if out_info is None:
                os.mkdir(out_dir, 0o700)
                created_dirs.append(out_dir)
            if os.name == "posix":
                # The umask filters the mode os.mkdir creates with.
                os.chmod(out_dir, 0o700)
                if out_info is not None:
                    # A refusal gives an existing --out its own mode back.
                    changed_modes.append((out_dir, stat.S_IMODE(out_info.st_mode)))
            for target, data in ((challenge_path, source_bytes), (record_path, record_bytes)):
                _write_new_private_file(target, data, created_files)
        except OSError as exc:
            _remove_created_outputs(created_files, created_dirs, changed_modes)
            findings.append({"kind": "output_write_failed", "detail": str(exc)})
            return payload

        payload["advisory_scan"] = scan_path(challenge_path.absolute(), "stub", set(imports))
        payload.update(
            ok=True,
            challenge_file=str(challenge_path.absolute()),
            challenge_record=str(record_path.absolute()),
            challenge_sha256=record["challenge_sha256"],
            record_sha256=hashlib.sha256(record_bytes).hexdigest(),
            toolchain_dir=toolchain_dir,
        )
    except Exception as exc:  # a crash is a refusal too, and leaves nothing behind
        _remove_created_outputs(created_files, created_dirs, changed_modes)
        payload.update(
            ok=False,
            challenge_file="",
            challenge_record="",
            challenge_sha256="",
            record_sha256="",
            advisory_scan={},
            toolchain_dir="",
        )
        findings.append(
            {"kind": "challenge_freeze_failed", "detail": f"{type(exc).__name__}: {exc}"[:500]}
        )
    return payload


def _challenge_output_dir(out_dir: Path) -> tuple[os.stat_result | None, str]:
    """lstat ``--out``: absent under an existing directory, or an empty directory.

    The parent may be reached through a symlink, as macOS's /tmp is, since
    os.mkdir follows it; ``--out`` itself must not be one. Returns the lstat
    result (None when absent) and a problem, or "" when the directory can be
    used.
    """

    try:
        info = os.lstat(out_dir)
    except FileNotFoundError:
        try:
            parent = os.stat(out_dir.parent)
        except OSError as exc:
            return None, f"the parent of --out is unusable: {exc}"
        if not stat.S_ISDIR(parent.st_mode):
            return None, "the parent of --out must be an existing directory"
        return None, ""
    except OSError as exc:
        return None, f"--out cannot be examined: {exc}"
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return info, "--out must be a directory, not a symlink or a file"
    try:
        with os.scandir(out_dir) as entries:
            if next(entries, None) is not None:
                return info, "--out must be absent or an empty directory"
    except OSError as exc:
        return info, f"--out cannot be listed: {exc}"
    return info, ""


def _write_new_private_file(path: Path, data: bytes, created: list[Path]) -> None:
    """Create ``path`` exclusively with mode 0600 and write all of ``data``.

    O_BINARY keeps CRLF bytes intact on Windows. On POSIX the descriptor is
    set to 0600 before the write, since the umask filters the mode os.open
    creates with. The path joins ``created`` as soon as it exists, so a
    failed write can remove it.
    """

    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0),
        0o600,
    )
    created.append(path)
    try:
        if os.name == "posix" and hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(f"writing {path.name} made no progress")
            view = view[written:]
    finally:
        os.close(descriptor)


def _remove_created_outputs(
    files: list[Path], directories: list[Path], modes: list[tuple[Path, int]]
) -> None:
    """Undo what one freeze wrote, newest first; best effort.

    The files and directories it created are removed, then each existing
    directory in ``modes`` gets the mode it had before the freeze.
    """

    for path in reversed(files):
        try:
            os.unlink(path)
        except OSError:
            pass
    for path in reversed(directories):
        try:
            os.rmdir(path)
        except OSError:
            pass
    for path, mode in reversed(modes):
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    files.clear()
    directories.clear()
    modes.clear()


def sha256_argument(value: str) -> str:
    """argparse type: a sha256 written as 64 lowercase hex digits."""

    if not _is_sha256(value):
        raise argparse.ArgumentTypeError("must be 64 lowercase hex digits")
    return value


def memory_limit_argument(value: str) -> str:
    """argparse type: ``off``, or a positive whole number followed by K, M or G."""

    if value != "off" and not re.fullmatch(r"[1-9][0-9]*[KMG]", value):
        raise argparse.ArgumentTypeError(
            "must be off, or a positive whole number followed by K, M or G, such as 10G"
        )
    return value


class ComparatorRefusal(Exception):
    """A comparator check that ends the run: the status, finding kind and detail it gives."""

    def __init__(self, status: str, kind: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.kind = kind
        self.detail = detail


class ComparatorContext(NamedTuple):
    """What the checks before the run admitted, for the run to use."""

    input_root: Path
    record_path: Path
    record: dict[str, Any]
    record_bytes: bytes
    challenge_path: Path
    challenge_bytes: bytes
    toolchain_dir: str
    toolchain_files: dict[str, str]
    bwrap: str
    bwrap_sha256: str
    modules: list[str]
    closure: dict[str, bytes]
    state_root: Path
    paranoid: bool
    timeout: int
    keep_workspace: bool
    memory_limit: str


def comparator_payload(
    input_path: Path,
    *,
    challenge_record: Path,
    challenge_record_sha256: str,
    solution_modules: list[str],
    timeout: int,
    paranoid: bool,
    keep_workspace: bool,
    memory_limit: str,
) -> dict[str, Any]:
    """Judge a solution project with ``lake comparator`` against a frozen challenge.

    The checks run in this order. The first that fails ends the run with its
    status and finding, and nothing after it runs:

    1. the host is Linux and the gate is not root (tool_unavailable);
    2. --input is a directory, not a symlink, and the record and its
       Challenge.lean are admitted by admit_challenge_record (not_run);
    3. the record's toolchain resolves outside --input, and its attestation
       matches the record's files and version (unsupported_toolchain or
       tool_unavailable);
    4. the pinned bwrap reports a supported version, starts its sandbox and
       is hashed (tool_unavailable);
    5. a paranoid run, by the flag or by the record, finds every bundled
       kernel among the record's files (tool_unavailable);
    6. solution_closure reads the solution files once (not_run);
    7. the state directory lies outside --input (not_run); then
       _comparator_run checks the host and runs the judge.

    The challenge section is filled after step 2, the toolchain after 3, the
    sandbox after 4 and the solution after 6. An unexpected error gives
    command_failed with comparator_failed. ``ok`` is true only for accepted.
    """

    payload: dict[str, Any] = {
        "schema_version": "lean-strict-verification-gate.v1",
        "ok": False,
        "mode": "comparator",
        "input": str(input_path),
        "comparator_status": "not_run",
        "comparator_rejection_kind": "",
        "comparator_detail": "",
        "findings": [],
        "challenge": {
            "record": "",
            "record_sha256": "",
            "challenge_sha256": "",
            "theorem_names": [],
            "permitted_axioms": [],
            "paranoid": False,
            "workspace": {},
        },
        "toolchain": {"spec": "", "dir": "", "lean_version": "", "files_sha256": ""},
        "sandbox": {
            "bwrap": "",
            "bwrap_version": "",
            "bwrap_sha256": "",
            "wrapper_sha256": "",
            "memory_limit": memory_limit,
        },
        "solution": {"modules": [], "files": {}, "digest": "", "post_digest": ""},
        "run": {
            "run_id": "",
            "argv": [],
            "returncode": None,
            "seconds": 0,
            "timeout_seconds": timeout,
            "phases": [],
            "stdout_tail": "",
            "stderr_tail": "",
            "workspace_kept": "",
        },
        "receipt": {"path": "", "sha256": ""},
        "limitations": list(COMPARATOR_LIMITATIONS),
    }
    if memory_limit == "off":
        payload["limitations"].append("no memory limit was applied")

    def fail(status: str, kind: str, detail: str) -> dict[str, Any]:
        payload["ok"] = False
        payload["comparator_status"] = status
        payload["findings"].append({"kind": kind, "detail": detail})
        return payload

    try:
        if sys.platform != "linux":
            return fail(
                "tool_unavailable",
                "platform_unsupported",
                "lake comparator sandboxes the solution with Linux namespaces; "
                f"this host is {sys.platform}",
            )
        geteuid = getattr(os, "geteuid", None)
        if geteuid is None:
            return fail(
                "tool_unavailable",
                "running_as_root",
                "this host reports no effective user id, so the gate cannot tell it is not root",
            )
        if geteuid() == 0:
            return fail("tool_unavailable", "running_as_root", "the comparator never runs as root")

        try:
            input_info = os.lstat(input_path)
        except (OSError, ValueError) as exc:
            reason = getattr(exc, "strerror", None) or type(exc).__name__
            return fail("not_run", "input_invalid", f"--input cannot be examined: {reason}")
        # lstat reports a symlink as a symlink, never as a directory.
        if not stat.S_ISDIR(input_info.st_mode):
            return fail(
                "not_run", "input_invalid", "--input must be a directory, not a symlink or a file"
            )
        input_root = Path(os.path.abspath(input_path))
        record_path = Path(os.path.abspath(challenge_record))
        record, record_bytes, challenge_bytes = admit_challenge_record(
            record_path, challenge_record_sha256, input_root
        )
        payload["challenge"].update(
            record=str(record_path),
            record_sha256=hashlib.sha256(record_bytes).hexdigest(),
            challenge_sha256=record["challenge_sha256"],
            theorem_names=record["theorem_names"],
            permitted_axioms=record["permitted_axioms"],
            paranoid=record["paranoid"],
            workspace=record["workspace"],
        )

        spec = record["toolchain"]["spec"]
        pinned = record["toolchain"]["files"]
        resolved = resolve_comparator_toolchain(spec, input_root=input_root)
        if resolved["status"] == "unsupported_toolchain":
            return fail("unsupported_toolchain", "unsupported_toolchain", resolved["detail"])
        if resolved["status"] != "available":
            return fail("tool_unavailable", "toolchain_unavailable", resolved["detail"])
        attested = toolchain_attestation(resolved["path"])
        if attested["status"] == "unsupported_toolchain":
            return fail("unsupported_toolchain", "unsupported_toolchain", attested["detail"])
        if attested["status"] != "ok":
            return fail("tool_unavailable", "toolchain_unavailable", attested["detail"])
        differing = sorted(
            name
            for name in set(pinned) | set(attested["files"])
            if pinned.get(name) != attested["files"].get(name)
        )
        if differing:
            more = f" and {len(differing) - 5} more" if len(differing) > 5 else ""
            return fail(
                "tool_unavailable",
                "toolchain_changed",
                "the installed toolchain differs from the record's pins at "
                + ", ".join(differing[:5])
                + more
                + "; a changed toolchain needs a new freeze",
            )
        if attested["version"] != _version_text(parse_lean_version(spec)):
            return fail(
                "tool_unavailable",
                "toolchain_changed",
                f"lean --version reports {attested['lean_version']!r}, not the release "
                f"{spec} that the record names",
            )
        payload["toolchain"].update(
            spec=spec,
            dir=resolved["path"],
            lean_version=attested["lean_version"],
            files_sha256=files_digest(pinned),
        )

        bwrap = bwrap_status(probe=True)
        if bwrap["status"] != "available":
            return fail("tool_unavailable", "sandbox_unavailable", "bwrap: " + bwrap["detail"])
        preflight = bwrap_preflight(bwrap["path"])
        if preflight["status"] != "ok":
            return fail(
                "tool_unavailable", "sandbox_unavailable", "bwrap preflight: " + preflight["detail"]
            )
        try:
            bwrap_sha256 = stable_regular_file_sha256(
                Path(bwrap["path"]), max_bytes=COMPILED_MODULE_MAX_BYTES
            )
        except StableReadError as exc:
            return fail(
                "tool_unavailable",
                "sandbox_unavailable",
                f"{bwrap['path']}: {exc.kind}: {exc.detail}",
            )
        payload["sandbox"].update(
            bwrap=bwrap["path"], bwrap_version=bwrap["version"], bwrap_sha256=bwrap_sha256
        )

        run_paranoid = bool(paranoid) or record["paranoid"]
        if run_paranoid:
            missing = [
                kernel
                for kernel in COMPARATOR_PARANOID_KERNELS
                if "bin/" + executable_name(kernel) not in pinned
            ]
            if missing:
                return fail(
                    "tool_unavailable",
                    "paranoid_kernel_missing",
                    "a paranoid run needs "
                    + ", ".join(missing)
                    + ", which the record's toolchain lacks",
                )

        closure = solution_closure(input_root, solution_modules, record["allowed_import_roots"])
        files = {relative: hashlib.sha256(data).hexdigest() for relative, data in closure.items()}
        modules = sorted(set(solution_modules))
        payload["solution"].update(modules=modules, files=files, digest=files_digest(files))

        state_root = comparator_state_root()
        state_real = Path(os.path.realpath(state_root))
        input_real = Path(os.path.realpath(input_root))
        if state_real == input_real or input_real in state_real.parents:
            return fail(
                "not_run",
                "state_dir_inside_input",
                f"the comparator state directory {state_root} lies inside --input",
            )
        _comparator_run(
            payload,
            ComparatorContext(
                input_root=input_root,
                record_path=record_path,
                record=record,
                record_bytes=record_bytes,
                challenge_path=record_path.parent / CHALLENGE_FILE,
                challenge_bytes=challenge_bytes,
                toolchain_dir=resolved["path"],
                toolchain_files=attested["files"],
                bwrap=bwrap["path"],
                bwrap_sha256=bwrap_sha256,
                modules=modules,
                closure=closure,
                state_root=state_root,
                paranoid=run_paranoid,
                timeout=timeout,
                keep_workspace=bool(keep_workspace),
                memory_limit=memory_limit,
            ),
        )
    except ComparatorRefusal as exc:
        return fail(exc.status, exc.kind, exc.detail)
    except Exception as exc:  # no verdict, never a crash
        return fail("command_failed", "comparator_failed", f"{type(exc).__name__}: {exc}"[:500])
    return payload


def admit_challenge_record(
    record_path: Path, expected_sha256: str, input_root: Path
) -> tuple[dict[str, Any], bytes, bytes]:
    """Admit a frozen challenge: the pinned record and the Challenge.lean beside it.

    The record must not lie in ``input_root`` or under it, by real path. It
    must pass _private_file_problem(), be read once within
    CHALLENGE_RECORD_MAX_BYTES, hash to ``expected_sha256`` and hold exactly
    what challenge-freeze writes. Challenge.lean beside it must pass the same
    file rules, be read once within CHALLENGE_SOURCE_MAX_BYTES and hash to
    the record's challenge_sha256. Returns the record and the exact bytes of
    both files. A refusal raises ComparatorRefusal with status not_run,
    except unsupported_workspace, which is tool_unavailable.
    """

    record_real = Path(os.path.realpath(record_path))
    root = Path(os.path.realpath(input_root))
    if record_real == root or root in record_real.parents:
        raise ComparatorRefusal(
            "not_run",
            "challenge_record_inside_input",
            f"{record_path} lies inside --input, which the solution controls",
        )
    problem = _private_file_problem(record_path)
    if problem:
        raise ComparatorRefusal("not_run", "challenge_record_unreadable", problem)
    try:
        record_bytes = stable_regular_file_bytes(record_path, max_bytes=CHALLENGE_RECORD_MAX_BYTES)
    except StableReadError as exc:
        raise ComparatorRefusal(
            "not_run", "challenge_record_unreadable", f"{record_path}: {exc.kind}: {exc.detail}"
        ) from exc
    if hashlib.sha256(record_bytes).hexdigest() != expected_sha256:
        raise ComparatorRefusal(
            "not_run",
            "challenge_record_mismatch",
            f"{record_path} does not hash to --challenge-record-sha256",
        )
    record = _challenge_record_content(record_bytes)
    challenge_path = Path(record_path).parent / CHALLENGE_FILE
    problem = _private_file_problem(challenge_path)
    if problem:
        raise ComparatorRefusal("not_run", "challenge_unreadable", problem)
    try:
        challenge_bytes = stable_regular_file_bytes(
            challenge_path, max_bytes=CHALLENGE_SOURCE_MAX_BYTES
        )
    except StableReadError as exc:
        raise ComparatorRefusal(
            "not_run", "challenge_unreadable", f"{challenge_path}: {exc.kind}: {exc.detail}"
        ) from exc
    if hashlib.sha256(challenge_bytes).hexdigest() != record["challenge_sha256"]:
        raise ComparatorRefusal(
            "not_run",
            "challenge_mismatch",
            f"{challenge_path} does not hash to the record's challenge_sha256",
        )
    return record, record_bytes, challenge_bytes


def _private_file_problem(path: Path) -> str:
    """"" when ``path`` may hold a frozen challenge file, else what is wrong with it.

    The file must be a regular file, not a symlink, with one link. On POSIX
    it must also belong to the user the gate runs as and be private to that
    user (mode 0600, as challenge-freeze writes it), so a record another user
    could write, or a copy that lost its mode, is refused.
    """

    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return f"{path} does not exist"
    except (OSError, ValueError) as exc:
        reason = getattr(exc, "strerror", None) or type(exc).__name__
        return f"{path} cannot be examined: {reason}"
    if stat.S_ISLNK(info.st_mode):
        return f"{path} is a symlink"
    if not stat.S_ISREG(info.st_mode):
        return f"{path} is not a regular file"
    if info.st_nlink != 1:
        return f"{path} has {info.st_nlink} hard links; it must have one"
    if os.name == "posix":
        if info.st_uid != os.geteuid():
            return f"{path} is not owned by the user the gate runs as"
        mode = stat.S_IMODE(info.st_mode)
        if mode & 0o077:
            return (
                f"{path} has mode {mode:04o}; it must be private to its owner, mode 0600 as "
                "challenge-freeze writes it (a copy that lost its mode needs chmod 0600)"
            )
    return ""


def _challenge_record_content(data: bytes) -> dict[str, Any]:
    """The record ``data`` holds, which must be exactly what challenge-freeze writes.

    Each violation is challenge_record_invalid with the rule as its detail.
    A workspace other than {"kind": "core"} is unsupported_workspace, after
    the shape checks; the core import roots are checked only for a core
    workspace.
    """

    def invalid(rule: str) -> ComparatorRefusal:
        return ComparatorRefusal("not_run", "challenge_record_invalid", rule)

    try:
        record = json.loads(data.decode("utf-8"))
        canonical = (
            json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        ).encode("ascii")
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise invalid("the record is not JSON text") from exc
    if canonical != data:
        raise invalid("the record is not in the canonical form challenge-freeze writes")
    if not isinstance(record, dict) or set(record) != CHALLENGE_RECORD_KEYS:
        raise invalid(
            f"the record is not an object with exactly the keys of {CHALLENGE_RECORD_SCHEMA}"
        )
    if record["schema_version"] != CHALLENGE_RECORD_SCHEMA:
        raise invalid(f"schema_version is not {CHALLENGE_RECORD_SCHEMA}")
    if record["challenge_module"] != CHALLENGE_MODULE:
        raise invalid(f"challenge_module is not {CHALLENGE_MODULE}")
    if not _is_sha256(record["challenge_sha256"]):
        raise invalid("challenge_sha256 is not 64 lowercase hex digits")
    theorems = record["theorem_names"]
    if not _sorted_distinct_strings(theorems):
        raise invalid("theorem_names is not a non-empty, sorted list of distinct strings")
    for name in theorems:
        if not _plain_lean_name(name):
            raise invalid(
                f"theorem_names holds {name!r}, not a valid Lean name without a _root_ component"
            )
    if record["definition_names"] != []:
        raise invalid("definition_names is not empty")
    axioms = record["permitted_axioms"]
    if not _sorted_distinct_strings(axioms):
        raise invalid("permitted_axioms is not a non-empty, sorted list of distinct strings")
    for axiom in axioms:
        if axiom in NEVER_SANCTIONED_AXIOMS:
            raise invalid(f"permitted_axioms holds {axiom}, which is never permitted")
        compiler_trust = valid_lean_name(axiom) and is_compiler_trust_axiom(axiom)
        if axiom not in SANCTIONED_AXIOMS and not compiler_trust:
            raise invalid(
                f"permitted_axioms holds {axiom!r}, which is neither sanctioned nor compiler trust"
            )
    if not isinstance(record["paranoid"], bool):
        raise invalid("paranoid is not a boolean")
    toolchain = record["toolchain"]
    if not (
        isinstance(toolchain, dict)
        and set(toolchain) == {"spec", "files"}
        and isinstance(toolchain["spec"], str)
        and isinstance(toolchain["files"], dict)
        and all(_is_sha256(digest) for digest in toolchain["files"].values())
    ):
        raise invalid('toolchain is not {"spec": <string>, "files": {<path>: <sha256>}}')
    workspace = record["workspace"]
    if not isinstance(workspace, dict) or not isinstance(workspace.get("kind"), str):
        raise invalid("workspace is not an object with a string kind")
    if workspace != {"kind": "core"}:
        raise ComparatorRefusal(
            "tool_unavailable",
            "unsupported_workspace",
            (
                f"the record's workspace is {json.dumps(workspace, sort_keys=True)}; "
                'this gate judges only {"kind": "core"}'
            )[:500],
        )
    if record["allowed_import_roots"] != list(CORE_IMPORT_ROOTS):
        raise invalid(
            f"allowed_import_roots is not {', '.join(CORE_IMPORT_ROOTS)}, as a core workspace has"
        )
    return record


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _sorted_distinct_strings(values: object) -> bool:
    return (
        isinstance(values, list)
        and bool(values)
        and all(isinstance(value, str) for value in values)
        and values == sorted(set(values))
    )


def _plain_lean_name(name: str) -> bool:
    """A valid Lean name without a _root_ component."""

    return valid_lean_name(name) and "_root_" not in name.split(".")


def _comparator_reserved_module(name: str) -> bool:
    return name in COMPARATOR_RESERVED_MODULES or name.startswith(
        (CHALLENGE_MODULE + ".", COMPARATOR_SOLUTION_MODULE + ".")
    )


def solution_closure(
    input_root: Path, modules: list[str], allowed_roots: list[str]
) -> dict[str, bytes]:
    """The solution files a comparator workspace receives: POSIX path to exact bytes.

    The walk is breadth first from the sorted, distinct ``modules``, and the
    map is in walk order. Module ``A.B`` is the file ``A/B.lean`` under
    ``input_root``. Each file is read once, and these bytes are the ones the
    workspace gets. Each import of a file is one of:

    - a module under a root in ``allowed_roots``: the toolchain's, never
      copied; refused when ``input_root`` also holds a file for it;
    - a module the workspace owns (COMPARATOR_RESERVED_MODULES, or a name
      below its first two), or not a valid module name: refused;
    - otherwise a file of ``input_root``, which the walk follows; refused
      when there is no such file.

    A named module must be a valid Lean name without a _root_ component,
    neither reserved nor under a root in ``allowed_roots``, and its file must
    exist. Every file must be a regular file with one link, under directories
    that are not symlinks, in UTF-8 that the scanner's lexer reads to its end
    (otherwise its imports cannot be trusted). The file count, the total size
    and each file's size are capped; the walk refuses past a cap and never
    truncates. A refusal raises ComparatorRefusal with status not_run.
    """

    root = Path(input_root)
    trusted = set(allowed_roots)

    def refuse(kind: str, detail: str) -> ComparatorRefusal:
        return ComparatorRefusal("not_run", kind, detail)

    named = sorted(set(modules))
    if not named:
        raise refuse("solution_module_invalid", "no --solution-module was given")
    for module in named:
        if not _plain_lean_name(module):
            raise refuse(
                "solution_module_invalid",
                f"{module!r} is not a valid Lean module name without a _root_ component",
            )
        if _comparator_reserved_module(module):
            raise refuse(
                "solution_module_invalid",
                f"{module} is a module name the comparator workspace owns",
            )
        if module.split(".")[0] in trusted:
            raise refuse(
                "solution_module_invalid", f"{module} lies under a trusted root of the toolchain"
            )

    closure: dict[str, bytes] = {}
    total = 0
    queue = list(named)
    seen = set(named)
    position = 0
    while position < len(queue):
        module = queue[position]
        position += 1
        *package, name = module.split(".")
        relative = "/".join([*package, name + ".lean"])
        try:
            directory = stable_directory_chain(root, tuple(package), missing_ok=True)
        except StableReadError as exc:
            raise refuse("solution_file_unsafe", f"{relative}: {exc.kind}: {exc.detail}") from exc
        path = root.joinpath(*package, name + ".lean")
        try:
            info = os.lstat(path) if directory is not None else None
        except FileNotFoundError:
            info = None
        except OSError as exc:
            raise refuse(
                "solution_file_unsafe",
                f"{relative} cannot be examined: {exc.strerror or type(exc).__name__}",
            ) from exc
        if info is None:
            if position <= len(named):
                raise refuse("solution_module_missing", f"{module}: --input has no {relative}")
            raise refuse(
                "solution_import_not_allowed",
                f"{module}: neither a file of --input nor a module under "
                + ", ".join(sorted(trusted)),
            )
        if stat.S_ISLNK(info.st_mode):
            raise refuse("solution_file_unsafe", f"{relative} is a symlink")
        if not stat.S_ISREG(info.st_mode):
            raise refuse("solution_file_unsafe", f"{relative} is not a regular file")
        if info.st_nlink != 1:
            raise refuse(
                "solution_file_unsafe",
                f"{relative} has {info.st_nlink} hard links; it must have one",
            )
        if len(closure) >= COMPARATOR_SOLUTION_MAX_FILES:
            raise refuse(
                "solution_too_large",
                f"the solution needs more than {COMPARATOR_SOLUTION_MAX_FILES} files",
            )
        try:
            data = stable_regular_file_bytes(path, max_bytes=COMPARATOR_SOLUTION_MAX_FILE_BYTES)
        except StableReadError as exc:
            kind = "solution_too_large" if exc.kind == "input_too_large" else "solution_file_unsafe"
            raise refuse(kind, f"{relative}: {exc.kind}: {exc.detail}") from exc
        total += len(data)
        if total > COMPARATOR_SOLUTION_MAX_TOTAL_BYTES:
            raise refuse(
                "solution_too_large",
                f"the solution files exceed {COMPARATOR_SOLUTION_MAX_TOTAL_BYTES} bytes in total",
            )
        closure[relative] = data
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise refuse(
                "solution_file_unsafe", f"{relative}: invalid_utf8: the file is not UTF-8"
            ) from exc
        stripped, lexical_errors = _strip_comments_and_strings(text)
        if lexical_errors:
            raise refuse(
                "solution_file_unsafe",
                f"{relative}: lexical_scan_incomplete: {lexical_errors[0]}",
            )
        for imported in imported_modules(stripped):
            if not _plain_lean_name(imported):
                raise refuse(
                    "solution_import_not_allowed",
                    f"{relative} imports {imported or '<missing>'}: not a valid Lean module name "
                    "without a _root_ component",
                )
            if _comparator_reserved_module(imported):
                raise refuse(
                    "solution_import_not_allowed",
                    f"{relative} imports {imported}, a module name the comparator workspace owns",
                )
            if imported.split(".")[0] in trusted:
                *parts, last = imported.split(".")
                if os.path.lexists(root.joinpath(*parts, last + ".lean")):
                    raise refuse(
                        "solution_shadows_trusted_module",
                        f"--input holds a file for {imported}, which {relative} imports "
                        "from the toolchain",
                    )
                continue
            if imported not in seen:
                seen.add(imported)
                queue.append(imported)
    return closure


def files_digest(files: dict[str, str]) -> str:
    """sha256 of a path-to-sha256 map, dumped with sorted keys and compact separators."""

    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def comparator_state_root() -> Path:
    """The directory of comparator runs and receipts; nothing is created here.

    It is $XDG_STATE_HOME/ai-agents-skills/comparator when XDG_STATE_HOME is
    an absolute path, and ~/.local/state/ai-agents-skills/comparator
    otherwise.
    """

    configured = os.environ.get("XDG_STATE_HOME", "")
    base = Path(configured) if os.path.isabs(configured) else _home_directory() / ".local" / "state"
    return base / "ai-agents-skills" / "comparator"


def comparator_lakefile(closure_modules: list[str]) -> str:
    """The workspace's lakefile.toml, for the modules of the solution closure.

    TOML runs no configuration code. AASSolution's roots are its own name and
    the root of each closure module, so Lake finds the solution's imports as
    local modules. Its globs name AASSolution and each closure module
    exactly: the default glob of a root would make Lake build a module the
    closure does not have, such as Sol for Sol.Main. Names are written as
    TOML basic strings; a JSON string is one for every name valid_lean_name
    admits, and a character outside the BMP stays raw, since TOML has no
    surrogate-pair escape.
    """

    modules = sorted(set(closure_modules))
    roots = sorted({module.split(".")[0] for module in modules})

    def strings(names: list[str]) -> str:
        return ", ".join(json.dumps(name, ensure_ascii=False) for name in names)

    return (
        'name = "aas_comparator"\n'
        'version = "0.1.0"\n'
        "\n"
        "[[lean_lib]]\n"
        f"name = {json.dumps(CHALLENGE_MODULE)}\n"
        "\n"
        "[[lean_lib]]\n"
        f"name = {json.dumps(COMPARATOR_SOLUTION_MODULE)}\n"
        f"roots = [{strings([COMPARATOR_SOLUTION_MODULE, *roots])}]\n"
        f"globs = [{strings([COMPARATOR_SOLUTION_MODULE, *modules])}]\n"
    )


def comparator_solution_source(modules: list[str]) -> str:
    """AASSolution.lean: prelude, then one import line for each named solution module.

    The modules are sorted and distinct. prelude keeps Init out of the file
    itself, so a prelude solution that declares a name Init also declares is
    judged by lake comparator instead of failing to build here.
    """

    return "prelude\n" + "".join(f"import {module}\n" for module in sorted(set(modules)))


def comparator_config(record: dict[str, Any]) -> bytes:
    """comparator.json for an admitted record.

    It names the record's theorems and permitted axioms, no definition hole
    and no extra kernel: enable_nanoda and external_kernels are left out, so
    only Lean's own kernel runs unless --paranoid adds the bundled ones.
    """

    config = {
        "challenge_module": CHALLENGE_MODULE,
        "solution_module": COMPARATOR_SOLUTION_MODULE,
        "theorem_names": record["theorem_names"],
        "definition_names": [],
        "permitted_axioms": record["permitted_axioms"],
    }
    return (
        json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode("ascii")


# The program lake comparator runs as COMPARATOR_BWRAP, in place of bwrap, for
# each sandboxed step of a run. It is AAS code held here as text, so
# gate_sha256 covers it; a run writes it beside its wrapper.json, after a
# "#!/usr/bin/python3 -I" line. It needs only Python 3.9's standard library.
COMPARATOR_WRAPPER_SOURCE = r'''"""COMPARATOR_BWRAP for a comparator run of the AAS Lean gate.

lake comparator runs this program in place of bwrap for each sandboxed step of
a run, with an empty environment, bwrap's options, "--" and the step's command.
The program checks them against the steps lake comparator takes, drops
--share-net, runs the pinned bwrap and waits for it, then appends one line for
the step to the run's phase log and exits as bwrap did. A step it refuses never
runs and is logged as refused. Its settings are wrapper.json, beside its real
path. It never writes to stdout: in an export step, stdout is the export.
"""

import json
import os
import sys
import time

CONFIG_KEYS = frozenset({"bwrap", "log", "toolchain_dir", "run_dir", "writable"})
PHASE_LOG_KEYS = frozenset({"phase", "target", "rc", "seconds", "dropped", "refused"})
PHASE_LOG_MAX_LINES = 64
REFUSED_EXIT = 125
EXEC_FAILED_EXIT = 127
# The bwrap options lake comparator uses, with the number of values each takes.
OPTION_ARITY = {
    "--ro-bind": 2,
    "--bind": 2,
    "--setenv": 2,
    "--tmpfs": 1,
    "--dir": 1,
    "--dev": 1,
    "--proc": 1,
    "--chdir": 1,
    "--clearenv": 0,
    "--unshare-all": 0,
    "--die-with-parent": 0,
    "--new-session": 0,
    "--share-net": 0,
}
REQUIRED_OPTIONS = ("--unshare-all", "--die-with-parent", "--clearenv", "--new-session")
# Resolving the workspace asks for a network; no step gets one.
DROPPED_OPTIONS = ("--share-net",)
MODULES = ("AASChallenge", "AASSolution")
KERNELS = (
    "leanchecker",
    "leanchecker-paranoid",
    "lean4lean",
    "nanoda_bin",
    "con-leche",
    "con-ron",
)


class Refused(Exception):
    """A step the wrapper does not run; the message says why."""


def inside(path, root):
    """Whether the real path ``path`` is the real path ``root`` or lies under it."""
    return path == root or path.startswith(root.rstrip("/") + "/")


def validate_bwrap_argv(args, config):
    """Check one step's arguments; return (opts, cmd, phase, target, dropped).

    args are bwrap's options, then "--" and the command; config is wrapper.json.
    Options are read by the number of values each takes, so only a "--" where
    an option belongs ends them. Each option must be in OPTION_ARITY, each of
    REQUIRED_OPTIONS must be there, and --share-net is dropped and named in
    dropped. --bind must bind the workspace's .lake onto itself. --ro-bind must
    bind a path onto itself whose real path is /, or is the toolchain or the
    run directory or lies under one of them. The command's real path must be a
    file in the toolchain's bin directory, and the step must be one lake
    comparator takes: lake env; lake build of AASChallenge or AASSolution;
    leanexport of one of them; or a kernel. Raises Refused otherwise.
    """
    opts = []
    dropped = []
    seen = set()
    index = 0
    while True:
        if index >= len(args):
            raise Refused("the bwrap options do not end with --")
        option = args[index]
        if option == "--":
            break
        arity = OPTION_ARITY.get(option)
        if arity is None:
            raise Refused(f"{option!r} is not an allowed bwrap option")
        values = args[index + 1 : index + 1 + arity]
        if len(values) < arity:
            raise Refused(f"{option} needs {arity} value{'s' if arity > 1 else ''}")
        index += 1 + arity
        seen.add(option)
        if option in DROPPED_OPTIONS:
            dropped.append(option)
            continue
        if option == "--bind":
            source, destination = values
            if source != destination or os.path.realpath(source) != config["writable"]:
                raise Refused(
                    f"--bind {source} {destination} is not the workspace's writable .lake"
                )
        elif option == "--ro-bind":
            source, destination = values
            real = os.path.realpath(source)
            if source != destination or not (
                real == "/"
                or inside(real, config["toolchain_dir"])
                or inside(real, config["run_dir"])
            ):
                raise Refused(
                    f"--ro-bind {source} {destination} is not /, the toolchain or the run directory"
                )
        opts.extend([option, *values])
    missing = [option for option in REQUIRED_OPTIONS if option not in seen]
    if missing:
        raise Refused("required bwrap options are missing: " + ", ".join(missing))
    cmd = args[index + 1 :]
    if not cmd:
        raise Refused("no command follows --")
    program = cmd[0]
    real = os.path.realpath(program)
    if (
        not os.path.isabs(program)
        or os.path.dirname(real) != os.path.join(config["toolchain_dir"], "bin")
        or not os.path.isfile(real)
    ):
        raise Refused(f"{program} is not a program in the toolchain's bin directory")
    name = os.path.basename(real)
    rest = cmd[1:]
    if name == "lake" and rest == ["env"]:
        return opts, cmd, "resolve", "", dropped
    if name == "lake" and len(rest) == 2 and rest[0] == "build" and rest[1] in MODULES:
        return opts, cmd, "build", rest[1], dropped
    if name == "leanexport" and len(rest) >= 2 and rest[0] in MODULES and rest[1] == "--":
        return opts, cmd, "export", rest[0], dropped
    if name in KERNELS:
        return opts, cmd, "kernel", name, dropped
    raise Refused(" ".join([name, *rest[:3]]) + " is not a step lake comparator runs")


def phase_record(phase, target, rc, seconds, dropped, refused):
    """One phase-log line: a JSON object with exactly PHASE_LOG_KEYS and a newline, as bytes."""
    entry = {
        "phase": phase,
        "target": target,
        "rc": rc,
        "seconds": seconds,
        "dropped": list(dropped),
        "refused": refused,
    }
    return (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def read_flags():
    """Flags that open a file to read, never through a symlink."""
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_BINARY", 0)


def load_config(program):
    """wrapper.json beside the real path of program: exactly CONFIG_KEYS, each an absolute path."""
    path = os.path.join(os.path.dirname(os.path.realpath(program)), "wrapper.json")
    try:
        descriptor = os.open(path, read_flags())
        try:
            data = os.read(descriptor, 65536)
        finally:
            os.close(descriptor)
        config = json.loads(data.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"wrapper.json cannot be read: {exc}") from None
    if not isinstance(config, dict) or set(config) != CONFIG_KEYS:
        raise Refused("wrapper.json does not hold exactly " + ", ".join(sorted(CONFIG_KEYS)))
    for key in sorted(CONFIG_KEYS):
        if not isinstance(config[key], str) or not os.path.isabs(config[key]):
            raise Refused(f"wrapper.json's {key} is not an absolute path")
    return config


def logged_lines(path):
    """The lines the phase log holds, counted up to PHASE_LOG_MAX_LINES."""
    descriptor = os.open(path, read_flags())
    try:
        count = 0
        while count < PHASE_LOG_MAX_LINES:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            count += chunk.count(b"\n")
        return count
    finally:
        os.close(descriptor)


def append(path, line):
    """Append line to the phase log in one write; the log is never created or followed."""
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.write(descriptor, line)
    finally:
        os.close(descriptor)


def report(message):
    """One line on stderr, which lake comparator passes on."""
    try:
        os.write(2, f"aas-comparator-wrapper: {message}\n".encode("utf-8", "backslashreplace"))
    except OSError:
        pass


def refuse(log, reason):
    """Report a refused step, log it when the log can take it, and return REFUSED_EXIT."""
    report("refused: " + reason)
    if log is not None:
        try:
            append(log, phase_record("refused", "", REFUSED_EXIT, 0.0, [], reason[:500]))
        except OSError:
            pass
    return REFUSED_EXIT


def main(argv=None):
    """Run one step of a comparator run in bwrap and log it; return the exit status."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        config = load_config(sys.argv[0])
    except Refused as exc:
        return refuse(None, str(exc))
    log = config["log"]
    try:
        logged = logged_lines(log)
    except OSError as exc:
        return refuse(None, f"the phase log cannot be read: {exc}")
    if logged >= PHASE_LOG_MAX_LINES:
        return refuse(None, f"the phase log already holds {PHASE_LOG_MAX_LINES} lines")
    try:
        opts, cmd, phase, target, dropped = validate_bwrap_argv(args, config)
    except Refused as exc:
        return refuse(log, str(exc))
    started = time.monotonic()
    pid = os.fork()
    if pid == 0:
        try:
            os.execv(config["bwrap"], [config["bwrap"], *opts, "--", *cmd])
        finally:
            os._exit(EXEC_FAILED_EXIT)
    _, status = os.waitpid(pid, 0)
    rc = os.waitstatus_to_exitcode(status)
    seconds = round(time.monotonic() - started, 3)
    try:
        append(log, phase_record(phase, target, rc, seconds, dropped, ""))
    except OSError as exc:
        report(f"the step ran, but the phase log cannot be written: {exc}")
        return REFUSED_EXIT
    return rc if rc >= 0 else 128 - rc


if __name__ == "__main__":
    sys.exit(main())
'''

# lake comparator ends a failed run with "error: <message>" as its last stderr
# line. These messages are its judgments that reject a solution, by rejection
# kind (Lake/Check/Compare.lean and Lake/Check/Axioms.lean, Lean v4.35.0).
_COMPARATOR_ERROR_PREFIX = "error: "
_COMPARATOR_OKAY_LINE = "Your solution is okay!"
_COMPARATOR_JUDGMENTS = (
    (
        "statement_mismatch",
        re.compile(r"Challenge and solution theorem statement do not match: '(.+)'"),
    ),
    ("kind_mismatch", re.compile(r"Challenge and solution constant kind don't match: '(.+)'")),
    ("kind_mismatch", re.compile(r"Solution constant is not a theorem: '(.+)'")),
    ("kind_mismatch", re.compile(r"Solution constant is not a definition: '(.+)'")),
    (
        "constant_mismatch",
        re.compile(r"Const does not match between challenge and target '(.+)'"),
    ),
    ("missing_constant", re.compile(r"Const not found in solution:? '(.+)'")),
    ("missing_constant", re.compile(r"Constant not found in solution '(.+)'")),
    ("forbidden_axiom", re.compile(r"Illegal axiom detected: '(.+)'")),
)
# The first kernel that failed, as lake comparator reports it. The exit code is
# ASCII digits: \d also matches other scripts' digits, which int() accepts.
_COMPARATOR_KERNEL_EXIT_RE = re.compile(r"(.+) exited with ([0-9]+)")
# The sandboxed steps of every run, in order, before the kernels.
_COMPARATOR_STEPS_BEFORE_KERNELS = (
    ("resolve", ""),
    ("build", CHALLENGE_MODULE),
    ("export", CHALLENGE_MODULE),
    ("build", COMPARATOR_SOLUTION_MODULE),
    ("export", COMPARATOR_SOLUTION_MODULE),
)
# A failed step other than the solution's build or a kernel, by its finding.
_COMPARATOR_STEP_FINDINGS = {
    ("resolve", ""): "resolve_failed",
    ("build", CHALLENGE_MODULE): "challenge_build_failed",
    ("export", CHALLENGE_MODULE): "challenge_export_failed",
    ("export", COMPARATOR_SOLUTION_MODULE): "solution_export_failed",
}


def parse_phase_log(data: bytes) -> list[dict[str, Any]] | None:
    """The steps the comparator wrapper logged, or None when ``data`` is not such a log.

    A log is at most COMPARATOR_PHASE_LOG_MAX_BYTES of UTF-8 in at most
    COMPARATOR_PHASE_LOG_MAX_LINES lines, each ending in a newline and holding a
    JSON object with exactly COMPARATOR_PHASE_LOG_KEYS: phase is resolve,
    build, export, kernel or refused; target is "", one of the two generated
    modules or a kernel's executable; rc is an integer, negative for a signal;
    seconds is a finite float that is not negative; dropped is a list of
    strings; refused is a string. An empty log holds no step.
    """

    if len(data) > COMPARATOR_PHASE_LOG_MAX_BYTES:
        return None
    if not data:
        return []
    if not data.endswith(b"\n"):
        return None
    try:
        lines = data.decode("utf-8").split("\n")[:-1]
    except UnicodeDecodeError:
        return None
    if len(lines) > COMPARATOR_PHASE_LOG_MAX_LINES:
        return None
    phase_names = {"resolve", "build", "export", "kernel", "refused"}
    targets = {"", CHALLENGE_MODULE, COMPARATOR_SOLUTION_MODULE, *COMPARATOR_KERNEL_NAMES}
    phases = []
    for line in lines:
        try:
            entry = json.loads(line)
        except (ValueError, RecursionError):
            return None
        if not isinstance(entry, dict) or set(entry) != COMPARATOR_PHASE_LOG_KEYS:
            return None
        phase, target, rc = entry["phase"], entry["target"], entry["rc"]
        seconds, dropped = entry["seconds"], entry["dropped"]
        if (
            not isinstance(phase, str)
            or phase not in phase_names
            or not isinstance(target, str)
            or target not in targets
            or not isinstance(rc, int)
            or isinstance(rc, bool)
            or not isinstance(seconds, float)
            or not 0.0 <= seconds < float("inf")
            or not isinstance(dropped, list)
            or not all(isinstance(option, str) for option in dropped)
            or not isinstance(entry["refused"], str)
        ):
            return None
        phases.append(entry)
    return phases


def classify_comparator_run(
    returncode: int,
    stdout: str,
    stderr: str,
    phases: list[dict[str, Any]] | None,
    paranoid: bool,
) -> tuple[str, str, str, dict[str, str] | None]:
    """Judge a finished lake comparator run: (status, rejection kind, detail, finding).

    ``phases`` is the wrapper's phase log as parse_phase_log gives it, and
    ``paranoid`` says whether the run asked for the bundled kernels. The
    solution's build prints whatever it likes, so the only output trusted here
    is the exit code and the last non-blank stderr line, which lake comparator
    writes last, as "error: <message>", when it exits 1 or 2; the phase log says
    which sandboxed steps ran and how each exited. ``detail`` is that message,
    capped at 500 characters, and "" for any other exit code.

    accepted needs exit 0, exactly the steps a run takes (paranoid or not),
    each exiting 0, and "Your solution is okay!" as the last stdout line. With
    exit 1 the steps must be a beginning of those, and the solution is rejected
    only when:

    - the five steps before the kernels ran, each exiting 0, and the message is
      one of the comparator's judgments, which gives its kind;
    - the solution's build was the last step and exited 1, and the message is
      "Child exited with 1" (build_failed);
    - every kernel ran, leanchecker last and exiting 1, and the message names
      the first kernel that failed and its exit code (kernel_rejected).

    A stderr line starting "bwrap: " takes the last two verdicts back, since a
    bwrap that fails also exits 1 (sandbox_failed). Every other run is
    command_failed with one finding, such as a refused step, a message that is
    not a judgment, a failure on the challenge's side, or a bundled kernel that
    rejects what leanchecker accepts (external_kernel_disagreement). A rejection
    and an acceptance have no finding.
    """

    final = _last_text_line(stderr)
    message = (
        final[len(_COMPARATOR_ERROR_PREFIX) :]
        if final.startswith(_COMPARATOR_ERROR_PREFIX)
        else None
    )
    detail = message[:500] if message is not None and returncode in (1, 2) else ""

    def no_verdict(kind: str, text: str) -> tuple[str, str, str, dict[str, str]]:
        return "command_failed", "", detail, {"kind": kind, "detail": text[:500]}

    if phases is None:
        return no_verdict(
            "phase_log_unreadable",
            "the sandbox wrapper's phase log is missing or malformed, so the steps are unknown",
        )
    refused = next((step for step in phases if step["phase"] == "refused"), None)
    if refused is not None:
        return no_verdict(
            "wrapper_refused", f"the sandbox wrapper refused a step: {refused['refused']}"
        )
    if returncode not in (0, 1, 2):
        ended = (
            f"was stopped by signal {-returncode}"
            if returncode < 0
            else f"exited with {returncode}"
        )
        return no_verdict("comparator_exit", f"lake comparator {ended}")
    if returncode == 2:
        return no_verdict(
            "comparator_could_not_start",
            f"lake comparator could not start: {final or 'it gave no message'}",
        )
    steps = [(step["phase"], step["target"]) for step in phases]
    kernels = [*(COMPARATOR_PARANOID_KERNELS if paranoid else ()), "leanchecker"]
    expected = [*_COMPARATOR_STEPS_BEFORE_KERNELS, *(("kernel", kernel) for kernel in kernels)]
    if returncode == 0:
        if steps != expected:
            return no_verdict(
                "inconsistent_run",
                f"lake comparator exited 0 after the steps {_comparator_steps_text(steps)}, "
                f"not {_comparator_steps_text(expected)}",
            )
        if any(step["rc"] != 0 for step in phases):
            return no_verdict(
                "inconsistent_run", "lake comparator exited 0 although a sandboxed step failed"
            )
        if _last_text_line(stdout) != _COMPARATOR_OKAY_LINE:
            return no_verdict(
                "inconsistent_run",
                f"lake comparator exited 0 without {_COMPARATOR_OKAY_LINE!r} as its last line",
            )
        return "accepted", "", "", None

    # Exit 1: the run failed, and lake comparator said why on its last line.
    if steps != expected[: len(steps)]:
        return no_verdict(
            "inconsistent_run",
            f"the steps {_comparator_steps_text(steps)} are not the order lake comparator "
            "takes them in",
        )
    failed = next((index for index, step in enumerate(phases) if step["rc"] != 0), None)
    if failed is None:
        kind = next(
            (
                kind
                for kind, pattern in _COMPARATOR_JUDGMENTS
                if message is not None and pattern.fullmatch(message)
            ),
            None,
        )
        if kind is None:
            return no_verdict(
                "unclassified_failure",
                f"lake comparator failed with a message that is not a rejection: "
                f"{final or 'it gave no message'}",
            )
        if steps != list(_COMPARATOR_STEPS_BEFORE_KERNELS):
            return no_verdict(
                "inconsistent_run",
                "lake comparator gave its judgment after steps other than the five before "
                f"the kernels: {_comparator_steps_text(steps)}",
            )
        return "rejected", kind, detail, None
    phase, target = steps[failed]
    rc = phases[failed]["rc"]
    bwrap_error = next((line for line in stderr.split("\n") if line.startswith("bwrap: ")), None)
    if (phase, target) == ("build", COMPARATOR_SOLUTION_MODULE):
        if rc == 1 and failed == len(phases) - 1 and message == "Child exited with 1":
            if bwrap_error is not None:
                return no_verdict(
                    "sandbox_failed", f"bwrap failed, so the build may not have run: {bwrap_error}"
                )
            return "rejected", "build_failed", detail, None
        return no_verdict(
            "solution_build_aborted",
            f"the build of {COMPARATOR_SOLUTION_MODULE} exited with {rc}, and lake comparator "
            "did not report a failed build as its last step",
        )
    if phase != "kernel":
        return no_verdict(
            _COMPARATOR_STEP_FINDINGS[(phase, target)],
            f"the step {_comparator_steps_text([(phase, target)])} exited with {rc}",
        )
    if steps != expected:
        return no_verdict(
            "inconsistent_run",
            "a kernel failed, but the phase log does not hold every kernel with leanchecker last",
        )
    checker_rc = phases[-1]["rc"]
    reported = _COMPARATOR_KERNEL_EXIT_RE.fullmatch(message) if message is not None else None
    if (
        checker_rc == 1
        and reported is not None
        and reported.group(1) == COMPARATOR_KERNEL_NAMES[target]
        and int(reported.group(2)) == rc
    ):
        if bwrap_error is not None:
            return no_verdict(
                "sandbox_failed", f"bwrap failed, so a kernel may not have run: {bwrap_error}"
            )
        return "rejected", "kernel_rejected", detail, None
    if checker_rc == 0:
        rejecting = [
            COMPARATOR_KERNEL_NAMES[step["target"]]
            for step in phases[len(_COMPARATOR_STEPS_BEFORE_KERNELS) :]
            if step["rc"] != 0
        ]
        return no_verdict(
            "external_kernel_disagreement",
            f"Lean's own kernel accepted the solution, but {', '.join(rejecting)} rejected it",
        )
    return no_verdict(
        "kernel_failed",
        f"the first kernel to fail, {COMPARATOR_KERNEL_NAMES[target]}, exited with {rc}, and "
        f"leanchecker with {checker_rc}; lake comparator did not report a kernel rejection",
    )


def _last_text_line(text: str) -> str:
    """The last line of ``text`` that is not blank, without its trailing white space.

    Lines end at "\\n" only, as lake comparator writes them: a name inside a
    message may hold another line separator.
    """

    for line in reversed(text.split("\n")):
        if line.strip():
            return line.rstrip()
    return ""


def _comparator_steps_text(steps: list[tuple[str, str]]) -> str:
    return ", ".join(f"{phase} {target}".rstrip() for phase, target in steps) or "none"


def _comparator_run(payload: dict[str, Any], context: ComparatorContext) -> None:
    """Run lake comparator on the admitted solution and record the result in ``payload``.

    The host comes first, in this order, and each refusal raises
    ComparatorRefusal: the state directory, its receipts/ and its runs/ are
    made private (a symlink there is refused); at least
    COMPARATOR_MIN_FREE_BYTES must be free there (insufficient_disk); unless
    the memory limit is off, a user scope with that limit must start
    (memory_limit_unavailable); the wrapper's interpreter must be an
    executable file (wrapper_interpreter_unavailable); and the host lock must
    come free within the timeout, and within COMPARATOR_LOCK_MAX_WAIT seconds
    (host_busy). The run then gets a private directory under runs/, which
    _comparator_judge fills, runs and judges. The directory is removed on
    every path unless keep_workspace keeps it, and a removal that fails adds
    cleanup_failed without changing the verdict. The lock is held to the end.
    """

    state = _comparator_state_directories(context.state_root)
    free = shutil.disk_usage(state).free
    if free < COMPARATOR_MIN_FREE_BYTES:
        raise ComparatorRefusal(
            "tool_unavailable",
            "insufficient_disk",
            f"{state} has {free} bytes free; a comparator run needs {COMPARATOR_MIN_FREE_BYTES}",
        )
    if context.memory_limit != "off":
        problem = _comparator_memory_preflight(context.memory_limit)
        if problem:
            raise ComparatorRefusal("tool_unavailable", "memory_limit_unavailable", problem)
    problem = _executable_file_problem(COMPARATOR_WRAPPER_INTERPRETER)
    if problem:
        raise ComparatorRefusal(
            "tool_unavailable",
            "wrapper_interpreter_unavailable",
            "the sandbox wrapper's interpreter: " + problem,
        )
    lock = _comparator_lock(
        state / "comparator.lock", min(context.timeout, COMPARATOR_LOCK_MAX_WAIT)
    )
    try:
        run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(4)
        run_dir = state / "runs" / run_id
        _comparator_private_directory(run_dir)
        payload["run"]["run_id"] = run_id
        try:
            _comparator_judge(payload, context, run_id, run_dir, state / "receipts")
        finally:
            if context.keep_workspace:
                payload["run"]["workspace_kept"] = str(run_dir)
            else:
                problem = _comparator_remove_run(run_dir)
                if problem:
                    payload["findings"].append({"kind": "cleanup_failed", "detail": problem[:500]})
    finally:
        os.close(lock)


class _ComparatorWorkspace(NamedTuple):
    """A run's workspace and the files beside it, as _comparator_workspace made them."""

    ws: Path
    home: Path
    tmp: Path
    config: Path
    log: Path
    wrapper: Path
    digests: dict[str, str]


def _comparator_judge(
    payload: dict[str, Any],
    context: ComparatorContext,
    run_id: str,
    run_dir: Path,
    receipts: Path,
) -> None:
    """Fill the run directory, run lake comparator in it, judge the run and check what it read.

    A refusal or an unexpected error ends the run without a verdict, with its
    finding. Once the judge has been started, the run's receipt is written
    whatever the result (D10), after the checks that follow the run.
    """

    started = False
    try:
        workspace = _comparator_workspace(payload, context, run_dir)
        toolchain_before = _comparator_tree_fingerprint(Path(context.toolchain_dir))
        argv = _comparator_argv(context, run_id, workspace)
        payload["run"]["argv"] = argv
        started = True
        _comparator_execute(payload, context, run_id, argv, workspace)
        _comparator_integrity(payload, context, workspace, toolchain_before)
    except ComparatorRefusal as exc:
        _comparator_fail(payload, exc.status, exc.kind, exc.detail)
    except Exception as exc:  # no verdict, never a crash
        _comparator_fail(
            payload, "command_failed", "comparator_failed", f"{type(exc).__name__}: {exc}"
        )
    if started:
        _comparator_receipt(payload, context, run_id, receipts)


def _comparator_workspace(
    payload: dict[str, Any], context: ComparatorContext, run_dir: Path
) -> _ComparatorWorkspace:
    """Make the run's workspace and files, then take W0 (design section 7).

    ws/ gets the generated lakefile.toml and lean-toolchain, then the
    manifest that lake resolve-deps writes for them, before any byte of the
    solution; then AASChallenge.lean, the solution's files and
    AASSolution.lean, from the bytes admission read. The run directory gets
    comparator.json, an empty phases.jsonl, the wrapper program and its
    wrapper.json. Each file is new (O_EXCL) and 0600, the wrapper program
    0700, and each directory 0700. lake resolve-deps runs with
    COMPARATOR_UMASK; one that fails, or that leaves no manifest, is
    workspace_setup_failed. W0 maps what ws/ holds outside .lake.
    """

    ws, home, tmp = run_dir / "ws", run_dir / "home", run_dir / "tmp"
    for directory in (ws, home, tmp):
        _comparator_private_directory(directory)
    created: list[Path] = []
    modules = [relative[: -len(".lean")].replace("/", ".") for relative in context.closure]
    _write_new_private_file(
        ws / "lakefile.toml", comparator_lakefile(modules).encode("utf-8"), created
    )
    _write_new_private_file(
        ws / "lean-toolchain", (context.record["toolchain"]["spec"] + "\n").encode("utf-8"), created
    )
    bin_dir = os.path.join(context.toolchain_dir, "bin")
    environment = {
        "PATH": os.pathsep.join([bin_dir, "/usr/bin", "/bin"]),
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "LANG": "C.UTF-8",
    }
    try:
        resolved = run_bounded_command(
            [os.path.join(bin_dir, "lake"), "resolve-deps"],
            timeout=COMPARATOR_RESOLVE_TIMEOUT,
            cwd=ws,
            max_output_bytes=READINESS_OUTPUT_MAX_BYTES,
            env=environment,
            umask=COMPARATOR_UMASK,
        )
    except subprocess.TimeoutExpired as exc:
        raise ComparatorRefusal(
            "command_failed",
            "workspace_setup_failed",
            f"lake resolve-deps: timeout after {COMPARATOR_RESOLVE_TIMEOUT} seconds",
        ) from exc
    except (CommandOutputLimitExceeded, OSError) as exc:
        raise ComparatorRefusal(
            "command_failed",
            "workspace_setup_failed",
            f"lake resolve-deps: {type(exc).__name__}: {exc}",
        ) from exc
    if resolved.returncode != 0:
        raise ComparatorRefusal(
            "command_failed",
            "workspace_setup_failed",
            f"lake resolve-deps exited {resolved.returncode}: {resolved.stderr.strip()[-300:]}",
        )
    if not _regular_file(ws / "lake-manifest.json"):
        raise ComparatorRefusal(
            "command_failed",
            "workspace_setup_failed",
            "lake resolve-deps wrote no lake-manifest.json",
        )
    _write_new_private_file(ws / f"{CHALLENGE_MODULE}.lean", context.challenge_bytes, created)
    for relative, data in context.closure.items():
        *parts, name = relative.split("/")
        directory = ws
        for part in parts:
            directory = directory / part
            if not os.path.lexists(directory):
                _comparator_private_directory(directory)
        _write_new_private_file(directory / name, data, created)
    _write_new_private_file(
        ws / f"{COMPARATOR_SOLUTION_MODULE}.lean",
        comparator_solution_source(context.modules).encode("utf-8"),
        created,
    )
    config = run_dir / "comparator.json"
    _write_new_private_file(config, comparator_config(context.record), created)
    log = run_dir / "phases.jsonl"
    _write_new_private_file(log, b"", created)
    wrapper = run_dir / "bwrap-wrapper"
    program = f"#!{COMPARATOR_WRAPPER_INTERPRETER} -I\n{COMPARATOR_WRAPPER_SOURCE}".encode("utf-8")
    _write_new_private_file(wrapper, program, created)
    os.chmod(wrapper, 0o700)
    payload["sandbox"]["wrapper_sha256"] = hashlib.sha256(program).hexdigest()
    settings = {
        "bwrap": context.bwrap,
        "log": str(log),
        "toolchain_dir": context.toolchain_dir,
        "run_dir": str(run_dir),
        "writable": str(ws / ".lake"),
    }
    _write_new_private_file(
        run_dir / "wrapper.json",
        (json.dumps(settings, sort_keys=True) + "\n").encode("ascii"),
        created,
    )
    return _ComparatorWorkspace(
        ws, home, tmp, config, log, wrapper, _comparator_workspace_digests(ws)
    )


def _comparator_argv(
    context: ComparatorContext, run_id: str, workspace: _ComparatorWorkspace
) -> list[str]:
    """The judge's argv (design section 9).

    Unless the memory limit is off, systemd-run puts the judge in a user
    scope of its own with that limit and no swap (D3). nice lowers its
    priority when COMPARATOR_NICE is there (D5). env -i gives it exactly
    PATH, HOME, TMPDIR, LANG and COMPARATOR_BWRAP, so no LEAN_*, LAKE_*,
    ELAN_* or LD_* variable can choose another Lean for it.
    """

    argv: list[str] = []
    if context.memory_limit != "off":
        argv += [
            COMPARATOR_SYSTEMD_RUN,
            "--user",
            "--scope",
            "--quiet",
            "--collect",
            f"--unit={_comparator_unit(run_id)}",
            "-p",
            f"MemoryMax={context.memory_limit}",
            "-p",
            "MemorySwapMax=0",
            "--",
        ]
    if not _executable_file_problem(COMPARATOR_NICE):
        argv += [COMPARATOR_NICE, "-n", "19"]
    bin_dir = os.path.join(context.toolchain_dir, "bin")
    argv += [
        COMPARATOR_ENV,
        "-i",
        "PATH=" + os.pathsep.join([bin_dir, "/usr/bin", "/bin"]),
        f"HOME={workspace.home}",
        f"TMPDIR={workspace.tmp}",
        "LANG=C.UTF-8",
        f"COMPARATOR_BWRAP={workspace.wrapper}",
        os.path.join(bin_dir, "lake"),
        "comparator",
        "--config",
        str(workspace.config),
    ]
    if context.paranoid:
        argv.append("--paranoid")
    return argv


def _comparator_execute(
    payload: dict[str, Any],
    context: ComparatorContext,
    run_id: str,
    argv: list[str],
    workspace: _ComparatorWorkspace,
) -> None:
    """Run the judge in the workspace and judge the run (design sections 9 and 10).

    The judge runs under the timeout, with at most COMMAND_OUTPUT_MAX_BYTES
    of output, the outer environment of D3 and COMPARATOR_UMASK. A timeout,
    an output flood or a judge that cannot be run is no verdict (timeout,
    output_limit, comparator_failed), and the judge's scope, when it has one,
    is stopped. So is it on Ctrl-C, which kills the judge's process group and
    then goes on. Otherwise classify_comparator_run judges the run from its
    exit code, its output and the wrapper's phase log.
    """

    run = payload["run"]
    started = time.monotonic()
    try:
        completed = run_bounded_command(
            argv,
            timeout=context.timeout,
            cwd=workspace.ws,
            max_output_bytes=COMMAND_OUTPUT_MAX_BYTES,
            env=_comparator_outer_environment(),
            umask=COMPARATOR_UMASK,
            kill_on_interrupt=True,
        )
    except KeyboardInterrupt:
        # Ctrl-C: the judge's process group is gone; its scope goes too.
        _comparator_stop_scope(context, run_id)
        raise
    except (subprocess.TimeoutExpired, CommandOutputLimitExceeded, OSError) as exc:
        _comparator_stop_scope(context, run_id)
        run["seconds"] = round(time.monotonic() - started, 3)
        run["phases"] = _comparator_phase_log(workspace.log) or []
        if isinstance(exc, subprocess.TimeoutExpired):
            run.update(
                stdout_tail=_output_tail(exc.output, 4000),
                stderr_tail=_output_tail(exc.stderr, 4000),
            )
            _comparator_fail(
                payload,
                "command_failed",
                "timeout",
                f"lake comparator did not finish within {context.timeout} seconds",
            )
        elif isinstance(exc, CommandOutputLimitExceeded):
            run.update(stdout_tail=exc.stdout[-4000:], stderr_tail=exc.stderr[-4000:])
            _comparator_fail(payload, "command_failed", "output_limit", str(exc))
        else:
            _comparator_fail(
                payload, "command_failed", "comparator_failed", f"{type(exc).__name__}: {exc}"
            )
        return
    run["seconds"] = round(time.monotonic() - started, 3)
    phases = _comparator_phase_log(workspace.log)
    run.update(
        returncode=completed.returncode,
        phases=phases if phases is not None else [],
        stdout_tail=completed.stdout[-4000:],
        stderr_tail=completed.stderr[-4000:],
    )
    status, kind, detail, finding = classify_comparator_run(
        completed.returncode, completed.stdout, completed.stderr, phases, context.paranoid
    )
    payload.update(
        ok=status == "accepted",
        comparator_status=status,
        comparator_rejection_kind=kind,
        comparator_detail=detail,
    )
    if finding is not None:
        payload["findings"].append(finding)


def _comparator_integrity(
    payload: dict[str, Any],
    context: ComparatorContext,
    workspace: _ComparatorWorkspace,
    toolchain_before: str,
) -> None:
    """Check after the run that nothing it relied on changed (design section 11).

    Each change found is a finding that makes the result command_failed: the
    solution's files in --input (S1 against S0, giving post_digest), the
    workspace outside .lake (W1 against W0), the toolchain's pinned files or
    its stat walk, the wrapper program or the bwrap binary, and the record or
    its Challenge.lean.
    """

    after: dict[str, str] = {}
    for relative in context.closure:
        *parts, name = relative.split("/")
        try:
            directory = stable_directory_chain(context.input_root, tuple(parts))
            after[relative] = stable_regular_file_sha256(
                directory / name, max_bytes=COMPARATOR_SOLUTION_MAX_FILE_BYTES
            )
        except StableReadError:
            after[relative] = ""
    payload["solution"]["post_digest"] = files_digest(after)
    problems: list[tuple[str, str]] = []
    before = payload["solution"]["files"]
    changed = sorted(relative for relative, digest in after.items() if digest != before[relative])
    if changed:
        problems.append(
            (
                "input_changed_during_verification",
                "the solution's files in --input changed during the run: " + ", ".join(changed[:5]),
            )
        )
    try:
        workspace_after: dict[str, str] | None = _comparator_workspace_digests(workspace.ws)
    except OSError:
        workspace_after = None
    if workspace_after != workspace.digests:
        problems.append(
            (
                "workspace_changed_during_verification",
                "the workspace changed outside .lake during the run",
            )
        )
    toolchain = Path(context.toolchain_dir)
    reasons = []
    pinned = toolchain_file_digests(toolchain)
    if pinned["status"] != "ok" or pinned["files"] != context.record["toolchain"]["files"]:
        reasons.append("its pinned files no longer match the record")
    try:
        toolchain_after = _comparator_tree_fingerprint(toolchain)
    except OSError:
        toolchain_after = ""
    if toolchain_after != toolchain_before:
        reasons.append("an entry's path, mode, size or modification time changed (stat walk)")
    if reasons:
        problems.append(
            (
                "toolchain_changed_during_verification",
                "the toolchain changed during the run: " + "; ".join(reasons),
            )
        )
    reasons = []
    if _comparator_sha256(workspace.wrapper) != payload["sandbox"]["wrapper_sha256"]:
        reasons.append("the wrapper program")
    if _comparator_sha256(Path(context.bwrap)) != context.bwrap_sha256:
        reasons.append("the bwrap binary")
    if reasons:
        problems.append(
            (
                "sandbox_changed_during_verification",
                " and ".join(reasons) + " changed during the run",
            )
        )
    reasons = []
    if _comparator_sha256(context.record_path) != hashlib.sha256(context.record_bytes).hexdigest():
        reasons.append("the challenge record")
    if _comparator_sha256(context.challenge_path) != context.record["challenge_sha256"]:
        reasons.append(CHALLENGE_FILE)
    if reasons:
        problems.append(
            (
                "challenge_changed_during_verification",
                " and ".join(reasons) + " changed during the run",
            )
        )
    for kind, detail in problems:
        _comparator_fail(payload, "command_failed", kind, detail)


def _comparator_receipt(
    payload: dict[str, Any], context: ComparatorContext, run_id: str, receipts: Path
) -> None:
    """Write the run's host_comparator_receipt.v1 under receipts/ (D10).

    The receipt is new (O_EXCL) and 0600, its JSON has sorted keys and
    compact separators, and the payload's receipt gets its path and sha256.
    A receipt that cannot be written makes the result command_failed with
    receipt_write_failed (D11), and what was written of it is removed.
    """

    created: list[Path] = []
    try:
        gate_file = Path(os.path.realpath(__file__))
        machine = os.uname()
        solution = payload["solution"]
        receipt = {
            "schema_version": COMPARATOR_RECEIPT_SCHEMA,
            "run_id": run_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "gate": {
                "path": str(gate_file),
                "sha256": stable_regular_file_sha256(gate_file, max_bytes=SOURCE_MAX_BYTES),
            },
            "platform": {"system": machine.sysname, "machine": machine.machine},
            "toolchain": dict(payload["toolchain"]),
            "sandbox": dict(payload["sandbox"]),
            "challenge": dict(payload["challenge"]),
            "solution": {
                "input": str(context.input_root),
                "modules": solution["modules"],
                "files": solution["files"],
                "digest": solution["digest"],
                "post_digest": solution["post_digest"],
            },
            "run": {
                key: payload["run"][key]
                for key in ("argv", "returncode", "seconds", "timeout_seconds", "phases")
            },
            "result": {
                key: payload[key]
                for key in (
                    "comparator_status",
                    "comparator_rejection_kind",
                    "comparator_detail",
                    "findings",
                )
            },
        }
        data = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        path = receipts / f"{run_id}.json"
        _write_new_private_file(path, data, created)
    except Exception as exc:  # no acceptance goes unrecorded
        for leftover in created:
            try:
                os.unlink(leftover)
            except OSError:
                pass
        _comparator_fail(
            payload, "command_failed", "receipt_write_failed", f"{type(exc).__name__}: {exc}"
        )
        return
    payload["receipt"] = {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}


def _comparator_fail(payload: dict[str, Any], status: str, kind: str, detail: str) -> None:
    """End a run with ``status`` and no rejection kind, adding the finding ``kind``."""

    payload.update(ok=False, comparator_status=status, comparator_rejection_kind="")
    payload["findings"].append({"kind": kind, "detail": detail[:500]})


def _comparator_state_directories(state_root: Path) -> Path:
    """Make the comparator's state directory, its receipts/ and runs/; return its real path.

    Each directory made, missing parents included, is 0700; a parent that
    already exists keeps its mode. The state directory, receipts/ and runs/
    are set to 0700 even when they exist, and each must be a directory, not
    a symlink: anything else is refused with comparator_failed.
    """

    root = Path(state_root)
    missing = []
    current = root
    while not os.path.lexists(current):
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        _comparator_private_directory(directory)
    for directory in (root, root / "receipts", root / "runs"):
        if not os.path.lexists(directory):
            _comparator_private_directory(directory)
        if not stat.S_ISDIR(os.lstat(directory).st_mode):
            raise ComparatorRefusal(
                "command_failed",
                "comparator_failed",
                f"{directory} is a symlink or not a directory; the comparator's state "
                "directories must be real directories",
            )
        os.chmod(directory, 0o700)
    return Path(os.path.realpath(root))


def _comparator_private_directory(path: Path) -> None:
    """Make a new directory with mode 0700, whatever the umask."""

    os.mkdir(path, 0o700)
    os.chmod(path, 0o700)


def _comparator_lock(path: Path, wait: int) -> int:
    """Take the host's comparator lock (D8) and return its descriptor.

    The lock file is opened without following a symlink and kept 0600. A run
    that cannot take the lock within ``wait`` seconds is host_busy.
    """

    import fcntl  # POSIX only, like the comparator run

    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ComparatorRefusal(
                        "command_failed",
                        "host_busy",
                        f"another comparator run holds the lock {path}; this run waited {wait} s",
                    ) from None
                time.sleep(0.5)
    except BaseException:
        os.close(descriptor)
        raise


def _comparator_memory_preflight(limit: str) -> str:
    """"" when a user scope with MemoryMax=``limit`` and no swap starts, else why it did not."""

    command = [
        COMPARATOR_SYSTEMD_RUN,
        "--user",
        "--scope",
        "--quiet",
        "--collect",
        "-p",
        f"MemoryMax={limit}",
        "-p",
        "MemorySwapMax=0",
        "--",
        "/bin/true",
    ]
    try:
        completed = run_bounded_command(
            command,
            timeout=15,
            max_output_bytes=READINESS_OUTPUT_MAX_BYTES,
            env=_comparator_outer_environment(),
        )
    except subprocess.TimeoutExpired:
        return "systemd-run: timeout after 15 seconds"
    except (CommandOutputLimitExceeded, OSError) as exc:
        return f"systemd-run: {type(exc).__name__}: {exc}"[:500]
    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout).strip()
        return f"systemd-run exited {completed.returncode}: {output[-300:]}"
    return ""


def _comparator_outer_environment() -> dict[str, str]:
    """The whole environment of systemd-run and the judge's outer process (D3).

    PATH=/usr/bin:/bin, with the gate's XDG_RUNTIME_DIR and
    DBUS_SESSION_BUS_ADDRESS when they are set, since systemd-run --user
    needs them to reach the user's service manager. env -i clears all of it
    for the judge.
    """

    environment = {"PATH": "/usr/bin:/bin"}
    for name in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _comparator_unit(run_id: str) -> str:
    return "aas-comparator-" + run_id


def _comparator_stop_scope(context: ComparatorContext, run_id: str) -> None:
    """Kill what is left in the judge's scope, when it has one; best effort.

    bwrap's --new-session takes each sandboxed step out of the judge's
    process group, so killing that group relies on --die-with-parent.
    """

    if context.memory_limit == "off":
        return
    try:
        run_bounded_command(
            [
                COMPARATOR_SYSTEMCTL,
                "--user",
                "kill",
                "--signal=KILL",
                _comparator_unit(run_id) + ".scope",
            ],
            timeout=15,
            max_output_bytes=READINESS_OUTPUT_MAX_BYTES,
            env=_comparator_outer_environment(),
        )
    except (subprocess.TimeoutExpired, CommandOutputLimitExceeded, OSError):
        pass


def _comparator_phase_log(path: Path) -> list[dict[str, Any]] | None:
    """The wrapper's phase log; None when it is missing, too large or malformed."""

    try:
        data = stable_regular_file_bytes(path, max_bytes=COMPARATOR_PHASE_LOG_MAX_BYTES)
    except StableReadError:
        return None
    return parse_phase_log(data)


def _comparator_sha256(path: Path) -> str:
    """sha256 of a file a run relies on, read as one stable regular file; "" when it cannot be."""

    try:
        return stable_regular_file_sha256(Path(path), max_bytes=COMPILED_MODULE_MAX_BYTES)
    except StableReadError:
        return ""


def _comparator_workspace_digests(ws: Path) -> dict[str, str]:
    """W: what ws/ holds outside its .lake, by POSIX relative path.

    A regular file maps to its sha256 (or to why it could not be read), a
    directory to "directory", a symlink to "symlink" and anything else to
    "special". Nothing is followed.
    """

    digests: dict[str, str] = {}
    pending = [ws]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(ws).as_posix()
                if relative == ".lake":
                    continue
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISDIR(mode):
                    digests[relative] = "directory"
                    pending.append(path)
                elif stat.S_ISREG(mode):
                    try:
                        digests[relative] = stable_regular_file_sha256(
                            path, max_bytes=COMPARATOR_SOLUTION_MAX_FILE_BYTES
                        )
                    except StableReadError as exc:
                        digests[relative] = "unreadable: " + exc.kind
                else:
                    digests[relative] = "symlink" if stat.S_ISLNK(mode) else "special"
    return digests


def _comparator_tree_fingerprint(root: Path) -> str:
    """A stat walk of ``root``: sha256 over each entry's path, mode, size and mtime.

    The walk never follows a symlink, and an entry or a directory it cannot
    read raises OSError.
    """

    def fail(error: OSError) -> None:
        raise error

    hasher = hashlib.sha256()
    for directory, directories, files in os.walk(root, onerror=fail):
        directories.sort()
        for name in sorted(directories + files):
            path = os.path.join(directory, name)
            info = os.lstat(path)
            hasher.update(
                f"{os.path.relpath(path, root)}\0{info.st_mode}\0{info.st_size}\0"
                f"{info.st_mtime_ns}\n".encode("utf-8", "surrogateescape")
            )
    return hasher.hexdigest()


def _comparator_remove_run(run_dir: Path) -> str:
    """Remove a run directory without following a symlink; "" or why it could not be.

    An entry that cannot be removed gets one retry: its parent, and the entry
    itself when it is a directory, are made readable, writable and
    searchable by their owner first, since a solution may lock away what it
    wrote under .lake.
    """

    def retry(function: Any, path: str, _error: Any) -> None:
        parent = os.path.dirname(path)
        os.chmod(parent, stat.S_IMODE(os.lstat(parent).st_mode) | stat.S_IRWXU)
        info = os.lstat(path)
        if stat.S_ISDIR(info.st_mode):
            os.chmod(path, stat.S_IMODE(info.st_mode) | stat.S_IRWXU)
            shutil.rmtree(path)
        else:
            function(path)

    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(run_dir, onexc=retry)
        else:
            shutil.rmtree(run_dir, onerror=retry)
    except OSError as exc:
        return f"{run_dir} was not removed: {type(exc).__name__}: {exc}"
    if os.path.lexists(run_dir):
        return f"{run_dir} is still there after its removal"
    return ""


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
