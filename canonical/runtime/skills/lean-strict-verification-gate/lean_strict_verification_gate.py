#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


PLACEHOLDER_PATTERNS = {
    "sorry": re.compile(r"\bsorry\b"),
    "admit": re.compile(r"\badmit\b"),
}
TRUST_BASE_PATTERNS = {
    "axiom": re.compile(r"^\s*axiom\s+", re.M),
    "unsafe": re.compile(r"\bunsafe\b"),
}
SAFETY_PATTERNS = {
    "#eval": re.compile(r"(^|[^\w])#eval\b"),
    "IO.Process": re.compile(r"\bIO\.Process\b"),
    "run_cmd": re.compile(r"\brun_cmd\b"),
    "initialize": re.compile(r"\binitialize\b"),
    "@[extern]": re.compile(r"@\s*\[\s*extern\b"),
    "foreign": re.compile(r"\b(foreign import|@[A-Za-z0-9_]*extern)\b"),
}
FORMAL_ARTIFACT_STAGES = {"intake", "stub", "candidate_solution", "final_candidate", "archived"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lean-strict-verification-gate")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")

    scan = sub.add_parser("scan")
    add_scan_args(scan)

    verify = sub.add_parser("verify")
    add_scan_args(verify)
    verify.add_argument("--typecheck", action="store_true")
    verify.add_argument("--timeout", type=int, default=20)

    args = parser.parse_args(argv)
    if args.command == "doctor":
        emit(doctor_payload())
        return 0
    if args.command == "scan":
        payload = scan_path(Path(args.input), args.artifact_stage, set(args.allow_import or []))
        emit(payload)
        return 0 if payload["ok"] else 1
    if args.command == "verify":
        payload = scan_path(Path(args.input), args.artifact_stage, set(args.allow_import or []))
        payload["lean_check_status"] = "not_run"
        if payload["ok"] and args.typecheck:
            payload.update(typecheck(Path(args.input), timeout=args.timeout))
        emit(payload)
        return 0 if payload["ok"] and payload.get("lean_check_status") not in {"typecheck_failed", "command_failed"} else 1
    raise AssertionError(args.command)


def add_scan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", required=True)
    parser.add_argument("--artifact-stage", choices=sorted(FORMAL_ARTIFACT_STAGES), default="final_candidate")
    parser.add_argument("--allow-import", action="append", default=[])


def doctor_payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "tool_status": {
            "lean": tool_status("lean"),
            "lake": tool_status("lake"),
            "elan": tool_status("elan"),
            "npm": tool_status("npm"),
            "npx": tool_status("npx"),
            "pip": tool_status("pip"),
        },
        "no_auto_install": True,
        "network_required": False,
        "installs_attempted": False,
    }


def tool_status(name: str) -> dict[str, str]:
    path = shutil.which(name)
    return {"status": "available" if path else "tool_unavailable", "path": path or ""}


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


def typecheck(path: Path, *, timeout: int) -> dict[str, Any]:
    lean = shutil.which("lean")
    if not lean:
        return {"lean_check_status": "tool_unavailable", "typecheck_command": "lean <input>", "typecheck_stdout": "", "typecheck_stderr": ""}
    try:
        completed = subprocess.run(
            [lean, str(path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "lean_check_status": "command_failed",
            "typecheck_command": "lean <input>",
            "typecheck_stdout": (exc.stdout or "")[-2000:],
            "typecheck_stderr": f"timeout after {timeout} seconds",
        }
    except OSError as exc:
        return {
            "lean_check_status": "command_failed",
            "typecheck_command": "lean <input>",
            "typecheck_stdout": "",
            "typecheck_stderr": str(exc),
        }
    return {
        "lean_check_status": "typechecked" if completed.returncode == 0 else "typecheck_failed",
        "typecheck_command": "lean <input>",
        "typecheck_stdout": completed.stdout[-2000:],
        "typecheck_stderr": completed.stderr[-2000:],
    }


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
