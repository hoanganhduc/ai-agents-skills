#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, NamedTuple


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


class BoundedCommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str
    output_bytes: int


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
        help="replay compiled modules through the kernel with lean4checker",
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
        help="require a completed kernel replay for exit 0: a missing lean4checker is a failure",
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
    raise AssertionError(args.command)


def add_scan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", required=True)
    parser.add_argument("--artifact-stage", choices=sorted(FORMAL_ARTIFACT_STAGES), default="final_candidate")
    parser.add_argument("--allow-import", action="append", default=[])


def doctor_payload(*, project_root: Path, probe: bool) -> dict[str, Any]:
    payload = {
        "status": "ok",
        "tool_status": {
            "lean": tool_status("lean"),
            "lake": tool_status("lake"),
            "elan": tool_status("elan"),
            # Reported so an operator can see that kernel-check has no checker
            # to run, rather than discovering it as a tool_unavailable verdict.
            "lean4checker": lean4checker_status(),
            "npm": tool_status("npm"),
            "npx": tool_status("npx"),
            "pip": tool_status("pip"),
        },
        "project_status": project_status(project_root),
        "no_auto_install": True,
        "network_required": False,
        "installs_attempted": False,
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
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
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
) -> BoundedCommandResult:
    """Run one isolated child while hard-capping combined stdout and stderr."""

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
        group_options = {"start_new_session": True}
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
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
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_tree(process)
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
        raise OSError("child output pipes did not close after termination")
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
            raise OSError("child could not be reaped after termination") from exc

    stdout_bytes = bytes(buffers["stdout"])
    stderr_bytes = bytes(buffers["stderr"])
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if output_limit_hit.is_set():
        raise CommandOutputLimitExceeded(
            max_output_bytes,
            stdout[-4000:],
            stderr[-4000:],
        )
    if timed_out:
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=stdout_bytes,
            stderr=stderr_bytes,
        )
    if reader_errors:
        raise OSError("could not capture child output: " + ", ".join(reader_errors))
    return BoundedCommandResult(
        int(process.returncode or 0),
        stdout,
        stderr,
        total[0],
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
        )
    except subprocess.TimeoutExpired as exc:
        payload = command_failed(runner, command_label, str(cwd) if cwd else "", f"timeout after {timeout} seconds")
        payload["typecheck_stdout"] = _output_tail(exc.stdout, 2000)
        payload["tool_status"] = tool_status_payload
        if project_status_payload:
            payload["project_status"] = project_status_payload
        return payload
    except CommandOutputLimitExceeded as exc:
        payload = command_failed(runner, command_label, str(cwd) if cwd else "", str(exc))
        payload["typecheck_stdout"] = exc.stdout[-2000:]
        payload["typecheck_stderr"] = (exc.stderr + "\n" + str(exc))[-2000:]
        payload["output_limit_exceeded"] = True
        payload["tool_status"] = tool_status_payload
        if project_status_payload:
            payload["project_status"] = project_status_payload
        return payload
    except OSError as exc:
        payload = command_failed(runner, command_label, str(cwd) if cwd else "", str(exc))
        payload["tool_status"] = tool_status_payload
        if project_status_payload:
            payload["project_status"] = project_status_payload
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
            )
        except subprocess.TimeoutExpired:
            return fail("audit_timeout", f"timeout after {timeout} seconds", "command_failed")
        except CommandOutputLimitExceeded as exc:
            payload["audit_stdout"] = exc.stdout
            payload["audit_stderr"] = (exc.stderr + "\n" + str(exc))[-4000:]
            return fail("audit_output_limit", str(exc), "command_failed")
        except OSError as exc:
            return fail("audit_failed", str(exc), "command_failed")

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
            "a passing lake build is not a kernel replay: only lean4checker re-checks proof terms",
            "modules are replayed as compiled, so the audit inherits the build's own toolchain",
            "sources Lake did not build are skipped, so they are never replayed",
            "--timeout is the total budget for every module, not a per-module allowance",
            "source-newer-than-olean is an mtime preflight, not proof that compiled content came from source",
            "explicit dependency modules outside the project root are resolved by Lake but not content-hashed",
            "pre/post hashes detect endpoint differences, not a change-and-restore between snapshots",
            "Lean, Lake, lean4checker, their launchers, environment, and dependency resolution are trusted inputs, not content-attested",
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
            "lean4checker or lake was not found: no kernel replay evidence was produced"
        )
        return payload

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    output_remaining = COMMAND_OUTPUT_MAX_BYTES
    # One budget for the whole verb: a per-module allowance would let 50
    # modules run 50x past the timeout the caller bounded the process with,
    # and the caller would see a killed gate instead of a replay verdict.
    deadline = time.monotonic() + timeout
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
            )
        except subprocess.TimeoutExpired:
            payload["kernel_check_stdout"] = "\n".join(stdout_chunks)[-4000:]
            payload["kernel_check_stderr"] = "\n".join(stderr_chunks)[-4000:]
            return fail("kernel_check_timeout", f"{module}: timeout after {timeout} seconds", "command_failed")
        except CommandOutputLimitExceeded as exc:
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
            payload["kernel_check_stdout"] = "\n".join(stdout_chunks)[-4000:]
            payload["kernel_check_stderr"] = "\n".join(stderr_chunks)[-4000:]
            return fail("kernel_check_failed", f"{module}: {exc}", "command_failed")
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


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
