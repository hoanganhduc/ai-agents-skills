#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
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
    # A command prefix may share the declaration's line, so `set_option x y in
    # axiom evil : False` never reaches the start of one — the anchor has to
    # allow an `... in` run ahead of the keyword.
    "axiom": re.compile(r"(?:^\s*|\sin\s+)axiom\s+", re.M),
    "unsafe": re.compile(r"\bunsafe\b"),
    # Proof-by-native-evaluation trusts the compiler and native code, not just
    # the kernel — a trust-base expansion the gate must surface.
    "native_decide": re.compile(r"\bnative_decide\b"),
    "ofReduceBool": re.compile(r"\bofReduceBool\b"),
}
SAFETY_PATTERNS = {
    "#eval": re.compile(r"(^|[^\w])#eval\b"),
    "IO.Process": re.compile(r"\bIO\.Process\b"),
    "run_cmd": re.compile(r"\brun_cmd\b"),
    "initialize": re.compile(r"\binitialize\b"),
    "@[extern]": re.compile(r"@\s*\[\s*extern\b"),
    # The leading \b must stay inside the alternatives: a shared one would
    # demand a word character before "@", so "@extern" at the start of a line
    # never matched.
    "foreign": re.compile(r"(?:\bforeign import\b|@[A-Za-z0-9_]*extern\b)"),
}
FORMAL_ARTIFACT_STAGES = {"intake", "stub", "candidate_solution", "final_candidate", "archived"}
RUNNERS = {"direct-lean", "lake-env-lean", "lake-build"}
PROJECT_EXCLUDED_DIRS = {".lake", ".git", "lake-packages", "build"}
PROJECT_MAX_FILES = 2000
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
# What `native_decide` reports. Unlike sorryAx these mark a complete proof, so a
# project may knowingly accept them, but the proof then rests on the compiler
# and the native runtime rather than on the kernel alone. Allowlisting them is
# therefore permitted and always reported, never silent.
COMPILER_TRUST_AXIOMS = {"Lean.ofReduceBool", "Lean.trustCompiler"}
AXIOM_AUDIT_MAX_DECLARATIONS = 500
# Unparsed lines are named in the report, but a pathological file must not be
# able to grow the payload without bound; the count past the cap is summarized.
AXIOM_AUDIT_MAX_UNPARSED = 20
KERNEL_CHECK_MAX_MODULES = 50
# A lakefile is a build script, not a library module: importing it into the
# audit harness would be a build-time error, never a proof dependency.
NON_MODULE_STEMS = {"lakefile"}


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
        type=int,
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
    audit.add_argument("--timeout", type=int, default=600)
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
    kernel.add_argument("--timeout", type=int, default=600)
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
            runner = args.runner
            project_root = Path(args.project_root) if args.project_root else None
            if input_path.is_dir():
                # A whole project can only be typechecked by building it.
                runner = "lake-build"
                project_root = project_root or input_path
            payload.update(typecheck(
                input_path,
                timeout=args.timeout,
                runner=runner,
                project_root=project_root,
            ))
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
            "lean4checker": lean4checker_status(project_root),
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
    path = shutil.which(name)
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
        path = Path(expanded)
        return str(path) if path.is_file() else ""
    return shutil.which(candidate) or ""


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
        completed = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "command_failed", "stdout": "", "stderr": "timeout after 5 seconds"}
    except OSError as exc:
        return {"status": "command_failed", "stdout": "", "stderr": str(exc)}
    return {
        "status": "ok" if completed.returncode == 0 else "command_failed",
        "stdout": completed.stdout[-2000:],
        "stderr": completed.stderr[-2000:],
    }


def scan_input(path: Path, artifact_stage: str, allowed_imports: set[str]) -> dict[str, Any]:
    """Dispatch: a directory is scanned recursively as a project, a file as before."""
    if path.is_dir():
        return scan_project(path, artifact_stage, allowed_imports)
    return scan_path(path, artifact_stage, allowed_imports)


def project_lean_files(root: Path) -> list[Path]:
    files = []
    for candidate in sorted(root.rglob("*.lean")):
        if any(part in PROJECT_EXCLUDED_DIRS for part in candidate.relative_to(root).parts):
            continue
        files.append(candidate)
    return files


def scan_project(root: Path, artifact_stage: str, allowed_imports: set[str]) -> dict[str, Any]:
    """Recursive scan of every .lean file with a per-file coverage manifest.

    Coverage is explicit so a re-scan can be diffed against staged evidence:
    each row records the relative path and content hash actually scanned.
    """
    files = project_lean_files(root)
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
    placeholder_any = False
    trust_any = False
    for lean_file in files:
        rel = str(lean_file.relative_to(root))
        file_payload = scan_path(lean_file, artifact_stage, allowed_imports)
        try:
            digest = hashlib.sha256(lean_file.read_bytes()).hexdigest()[:16]
        except OSError:
            digest = ""
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
        "safety_status": "failed" if any(item["kind"] in {"unsafe_construct", "non_allowlisted_import"} for item in findings) else "passed",
        "findings": findings,
        "coverage": {"files_total": len(files), "files_scanned": len(files), "files": coverage},
        "limitations": [
            "scanner is a preflight guard, not a complete Lean parser",
            "statement equivalence is not checked by this helper",
        ],
    }


def scan_path(path: Path, artifact_stage: str, allowed_imports: set[str]) -> dict[str, Any]:
    if not path.is_file():
        return {
            "schema_version": "lean-strict-verification-gate.v1",
            "ok": False,
            "input": str(path),
            "artifact_stage": artifact_stage,
            "lean_check_status": "not_run",
            "placeholder_status": "not_scanned",
            "trust_base_status": "not_scanned",
            "safety_status": "failed",
            "findings": [{"kind": "missing_file", "detail": "input file does not exist"}],
        }
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return unreadable_payload(path, artifact_stage, "invalid_utf8", "input file is not valid UTF-8")
    except OSError as exc:
        return unreadable_payload(path, artifact_stage, "read_error", str(exc))
    stripped = strip_comments_and_strings(text)
    findings: list[dict[str, str]] = []
    for name, pattern in SAFETY_PATTERNS.items():
        if pattern.search(stripped):
            findings.append({"kind": "unsafe_construct", "detail": name})
    for imp in imported_modules(stripped):
        if allowed_imports and imp not in allowed_imports:
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
        "safety_status": "failed" if any(item["kind"] in {"unsafe_construct", "non_allowlisted_import"} for item in findings) else "passed",
        "findings": findings,
        "limitations": [
            "scanner is a preflight guard, not a complete Lean parser",
            "statement equivalence is not checked by this helper",
        ],
    }


def strip_comments_and_strings(text: str) -> str:
    text = re.sub(r"/-.*?-/", "", text, flags=re.S)
    stripped_lines = []
    for line in text.splitlines():
        line = line.split("--", 1)[0]
        line = re.sub(r'"(?:\\.|[^"\\])*"', '""', line)
        stripped_lines.append(line)
    return "\n".join(stripped_lines)


def imported_modules(text: str) -> list[str]:
    modules = []
    for line in text.splitlines():
        match = re.match(r"\s*import\s+([A-Za-z0-9_.'-]+)\s*$", line)
        if match:
            modules.append(match.group(1))
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
    return run_typecheck(
        [lake["path"], "build"],
        timeout=timeout,
        command_label="lake build",
        runner="lake-build",
        cwd=root,
        tool_status_payload={"lake": lake},
        project_status_payload=status,
    )


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
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=str(cwd) if cwd else None,
        )
    except subprocess.TimeoutExpired as exc:
        payload = command_failed(runner, command_label, str(cwd) if cwd else "", f"timeout after {timeout} seconds")
        payload["typecheck_stdout"] = (exc.stdout or "")[-2000:]
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
_NAMESPACE_RE = re.compile(r"^\s*namespace\s+(?P<name>[A-Za-z_][A-Za-z0-9_.'!?]*)")
# Every block `end` closes must push a scope, or the walk pops a namespace it
# never entered and every later name loses its prefix. `noncomputable section`
# is the common one: matching bare `section` alone left its `end` to pop the
# enclosing namespace, so every declaration after it was audited under a name
# that is either unresolvable or, worse, some other declaration's.
_ANONYMOUS_SCOPE_RE = re.compile(r"^\s*" + _MODIFIERS + r"(?:section|mutual)\b")


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
            scopes.append(namespace.group("name"))
            continue
        if _ANONYMOUS_SCOPE_RE.match(line):
            scopes.append("")
            continue
        if re.match(r"\s*end\b", line):
            if scopes:
                scopes.pop()
            continue
        declaration = _DECLARATION_RE.match(line)
        if declaration:
            prefix = ".".join(scope for scope in scopes if scope)
            name = declaration.group("name")
            qualified = f"{prefix}.{name}" if prefix else name
            (private if declaration.group("private") else names).append(qualified)
        elif _DECLARATION_KEYWORD_RE.search(line):
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


def built_module_names(root: Path) -> set[str]:
    """Modules Lake has actually compiled, read off its olean output tree."""
    lean_dir = root / ".lake" / "build" / "lib" / "lean"
    lib_dir = lean_dir if lean_dir.is_dir() else root / ".lake" / "build" / "lib"
    if not lib_dir.is_dir():
        return set()
    return {
        ".".join(olean.relative_to(lib_dir).with_suffix("").parts)
        for olean in lib_dir.rglob("*.olean")
    }


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
            text = lean_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
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
    root = project_root.expanduser().resolve()
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
        ],
    }

    def fail(kind: str, detail: str, status: str) -> dict[str, Any]:
        payload["ok"] = False
        payload["axiom_audit_status"] = status
        payload["findings"].append({"kind": kind, "detail": detail})
        return payload

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
        modules, unbuilt = built_project_modules(root)
        payload["modules_skipped_unbuilt"] = unbuilt
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

    if declarations:
        targets, private_targets, unparsed_lines = declarations, [], []
    else:
        targets, private_targets, unparsed_lines = project_declaration_scan(root, modules)
    payload["declarations_requested"] = len(targets)
    if unparsed_lines:
        # A line the walk could not read a name off is a coverage hole, not a
        # warning: it may have hidden a theorem, and an audit that reports a
        # clean trust base over a partial scan is worse than no audit. Refuse
        # and name the lines so the operator can pass --declaration instead.
        shown = unparsed_lines[:AXIOM_AUDIT_MAX_UNPARSED]
        payload["declarations_unparsed"] = shown
        payload["findings"].extend(
            {"kind": "declaration_unparsed", "detail": line} for line in shown
        )
        if len(unparsed_lines) > len(shown):
            payload["findings"].append(
                {
                    "kind": "declaration_unparsed",
                    "detail": f"{len(unparsed_lines) - len(shown)} further unparsed declaration lines",
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
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                cwd=str(root),
            )
        except subprocess.TimeoutExpired:
            return fail("audit_timeout", f"timeout after {timeout} seconds", "command_failed")
        except OSError as exc:
            return fail("audit_failed", str(exc), "command_failed")

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
        allowed_compiler_trust = [
            axiom for axiom in axioms if axiom in COMPILER_TRUST_AXIOMS and axiom in sanctioned
        ]
        if offending:
            status = "unsanctioned_axiom"
        elif allowed_compiler_trust:
            status = "sanctioned_compiler_trust"
        else:
            status = "sanctioned"
        payload["declarations"].append(
            {"declaration": name, "axioms": axioms, "status": status}
        )
        for axiom in allowed_compiler_trust:
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
            "an allowlisted compiler-trust axiom means the proof rests on native "
            "evaluation and the compiler, not on the kernel alone"
        )
    payload["axiom_audit_status"] = "audited"
    return payload


def lean4checker_status(project_root: Path) -> dict[str, Any]:
    """lean4checker from the env/PATH, else the project's own build output."""
    status = tool_status("lean4checker")
    if status["status"] == "available":
        return status
    local = project_root / ".lake" / "build" / "bin" / executable_name("lean4checker")
    if local.is_file():
        return {"status": "available", "path": str(local), "source": "project-build"}
    return status


def kernel_check_payload(
    input_path: Path,
    *,
    project_root: Path,
    timeout: int,
    modules: list[str],
    strict: bool,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
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
        ],
    }

    def fail(kind: str, detail: str, status: str) -> dict[str, Any]:
        payload["ok"] = False
        payload["kernel_check_status"] = status
        payload["findings"].append({"kind": kind, "detail": detail})
        return payload

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
        targets, unbuilt = built_project_modules(root)
        payload["modules_skipped_unbuilt"] = unbuilt
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

    lake = tool_status("lake")
    checker = lean4checker_status(root)
    payload["tool_status"] = {"lake": lake, "lean4checker": checker}
    if lake["status"] != "available" or checker["status"] != "available":
        payload["kernel_check_status"] = "tool_unavailable"
        payload["limitations"].append(
            "lean4checker or lake was not found: no kernel replay evidence was produced"
        )
        return payload

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    # One budget for the whole verb: a per-module allowance would let 50
    # modules run 50x past the timeout the caller bounded the process with,
    # and the caller would see a killed gate instead of a replay verdict.
    deadline = time.monotonic() + timeout
    for module in targets:
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
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=remaining,
                check=False,
                cwd=str(root),
            )
        except subprocess.TimeoutExpired:
            payload["kernel_check_stdout"] = "\n".join(stdout_chunks)[-4000:]
            payload["kernel_check_stderr"] = "\n".join(stderr_chunks)[-4000:]
            return fail("kernel_check_timeout", f"{module}: timeout after {timeout} seconds", "command_failed")
        except OSError as exc:
            payload["kernel_check_stdout"] = "\n".join(stdout_chunks)[-4000:]
            payload["kernel_check_stderr"] = "\n".join(stderr_chunks)[-4000:]
            return fail("kernel_check_failed", f"{module}: {exc}", "command_failed")
        stdout_chunks.append(completed.stdout)
        stderr_chunks.append(completed.stderr)
        replayed = completed.returncode == 0
        payload["modules"].append(
            {"module": module, "status": "kernel_checked" if replayed else "kernel_check_failed"}
        )
        if not replayed:
            payload["ok"] = False
            payload["findings"].append({"kind": "kernel_check_failed", "detail": module})

    payload["kernel_check_stdout"] = "\n".join(stdout_chunks)[-4000:]
    payload["kernel_check_stderr"] = "\n".join(stderr_chunks)[-4000:]
    payload["kernel_check_status"] = "kernel_checked" if payload["ok"] else "kernel_check_failed"
    return payload


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
