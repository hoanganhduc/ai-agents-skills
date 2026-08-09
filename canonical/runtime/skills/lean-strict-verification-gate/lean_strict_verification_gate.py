#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


PLACEHOLDER_PATTERNS = {
    "sorry": re.compile(r"\bsorry\b"),
    "admit": re.compile(r"\badmit\b"),
    # sorryAx is the axiom `sorry` elaborates to; `exact sorryAx _ _` slips past
    # the \bsorry\b word boundary, so it needs its own pattern.
    "sorryAx": re.compile(r"\bsorryAx\b"),
}
TRUST_BASE_PATTERNS = {
    "axiom": re.compile(r"^\s*axiom\s+", re.M),
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
}


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


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
