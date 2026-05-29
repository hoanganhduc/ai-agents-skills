#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LEARNING_FILES = ("ERRORS.md", "LEARNINGS.md", "FEATURE_REQUESTS.md")
HEADER_RE = re.compile(r"^## \[(.+?)\]\s*(.*)$")

SAFETY_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "destructive rm targeting root or home directory",
        re.compile(r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?(-[a-zA-Z]*r[a-zA-Z]*\s+)?(/|/home(\s|$)|~/?(\s|$))"),
    ),
    (
        "force push to main/master",
        re.compile(r"git\s+push\s+.*--force.*\s+(origin\s+)?(main|master)\b", re.IGNORECASE),
    ),
    (
        "pipe-to-shell pattern",
        re.compile(r"(curl|wget)\s+[^|]*\|\s*(ba)?sh", re.IGNORECASE),
    ),
    (
        "DROP DATABASE/TABLE",
        re.compile(r"DROP\s+(DATABASE|TABLE)\s", re.IGNORECASE),
    ),
    (
        "PowerShell recursive forced deletion",
        re.compile(r"\b(Remove-Item|rm|del|erase)\b(?=.*(?:^|\s)(-Recurse|-r)(?:\s|$))(?=.*(?:^|\s)(-Force|-f)(?:\s|$))", re.IGNORECASE),
    ),
    (
        "CMD recursive quiet deletion",
        re.compile(r"\b(rmdir|rd)\b\s+/s\s+/q\b|\b(del|erase)\b\s+/[sq]\b", re.IGNORECASE),
    ),
    (
        "Windows volume formatting",
        re.compile(r"\b(Format-Volume|format)\b", re.IGNORECASE),
    ),
)

ERROR_MARKERS = re.compile(
    r"("
    r"error:|Error:|FATAL|fatal:|Traceback|Exception|ModuleNotFoundError|"
    r"TypeError|ImportError|Permission denied|Access is denied|No such file|"
    r"command not found|is not recognized|FullyQualifiedErrorId|CategoryInfo|"
    r"npm ERR!|SyntaxError|NameError|KeyError|ValueError|FileNotFoundError"
    r")",
    re.IGNORECASE,
)

INTEGRATION_FIELDS = (
    "Related Skills",
    "Related Settings Or Artifacts",
    "Affected Install Targets",
    "Affected OS/Substrates",
    "Canonical Repo Change",
    "Docs And Generated Outputs",
    "Verification Plan",
    "Blocked Or Unsupported Targets",
)


def parse_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        match = HEADER_RE.match(line)
        if match:
            if current:
                entries.append(current)
            current = {
                "id": match.group(1),
                "title": match.group(2).strip(),
                "priority": None,
                "status": None,
            }
            continue
        if current is None:
            continue
        if line.startswith("**Priority**:"):
            current["priority"] = line.split(":", 1)[1].strip().lower()
        elif line.startswith("**Status**:"):
            current["status"] = line.split(":", 1)[1].strip().lower()
    if current:
        entries.append(current)
    return entries


def learnings_dir_for(target: str | None) -> Path:
    base = Path(target or ".").expanduser()
    return base if base.name == ".learnings" else base / ".learnings"


def cmd_review_pending(args: argparse.Namespace) -> int:
    learnings_dir = learnings_dir_for(args.target)
    if not learnings_dir.is_dir():
        payload = {
            "status": "missing",
            "directory": str(learnings_dir),
            "message": "No .learnings directory found.",
        }
        if args.json:
            emit_json(payload)
        else:
            print(f"No .learnings directory found at: {learnings_dir}")
            print("Tip: create .learnings/ and populate LEARNINGS.md, ERRORS.md, and FEATURE_REQUESTS.md as needed.")
        return 0

    by_file = {name: parse_entries(learnings_dir / name) for name in LEARNING_FILES}
    pending_total = 0
    promoted_total = 0
    resolved_total = 0
    shown_by_file: dict[str, list[dict[str, Any]]] = {}
    for name, entries in by_file.items():
        pending = [entry for entry in entries if entry["status"] == "pending"]
        promoted = [entry for entry in entries if entry["status"] == "promoted"]
        resolved = [entry for entry in entries if entry["status"] == "resolved"]
        pending_total += len(pending)
        promoted_total += len(promoted)
        resolved_total += len(resolved)
        if args.high_only:
            pending = [entry for entry in pending if entry["priority"] in {"high", "critical"}]
        shown_by_file[name] = pending

    payload = {
        "status": "ok",
        "directory": str(learnings_dir),
        "pending_total": pending_total,
        "promoted_total": promoted_total,
        "resolved_total": resolved_total,
        "shown": shown_by_file,
    }
    if args.json:
        emit_json(payload)
        return 0

    print("=== Pending Learnings Review ===")
    print(f"Directory: {learnings_dir}")
    print()
    for name in LEARNING_FILES:
        print(f"--- {name} ---")
        if shown_by_file[name]:
            for entry in shown_by_file[name]:
                priority = entry.get("priority") or ""
                icon = "!!" if priority == "critical" else "! " if priority == "high" else "- "
                title = f" {entry['title']}" if entry.get("title") else ""
                print(f"  {icon} [{entry['id']}]{title}")
        else:
            print("  (no matching pending items)" if args.high_only else "  (no pending items)")
        print()
    print(f"Total: {pending_total} pending, {promoted_total} promoted, {resolved_total} resolved")
    print()
    print("Actions:")
    print("  Resolve:  change **Status**: pending -> resolved and add a short resolution note")
    print("  Promote:  distill durable rules into this repo's canonical skill, manifest, docs, or test files")
    print("  Log new:  append a structured entry to .learnings/{LEARNINGS,ERRORS,FEATURE_REQUESTS}.md")
    return 0


def cmd_check_command_safety(args: argparse.Namespace) -> int:
    command = " ".join(args.command).strip() if args.command else sys.stdin.read().strip()
    if not command:
        print("No command provided.", file=sys.stderr)
        return 2
    for label, pattern in SAFETY_RULES:
        if pattern.search(command):
            print(f"BLOCKED: {label}.", file=sys.stderr)
            return 2
    print("ALLOW: no lightweight safety rule matched.")
    return 0


def cmd_detect_common_errors(args: argparse.Namespace) -> int:
    if args.file:
        path = Path(args.file)
        if not path.is_file():
            print(f"error: file not found: {path}", file=sys.stderr)
            return 2
        output = path.read_text(encoding="utf-8", errors="replace")
    else:
        output = sys.stdin.read()
    if ERROR_MARKERS.search(output):
        print("Potential failure markers detected.")
        print()
        print("Consider whether this should be logged with `self-improving-agent`:")
        print("- unexpected command failure")
        print("- recurring environment or path issue")
        print("- missing capability")
        print("- fix or workaround worth preserving")
        print()
        print("Useful next step:")
        print("  run the portable review-pending helper from the installed ai-agents-skills runtime")
    else:
        print("No common error markers detected.")
    return 0


def cmd_integration_plan(args: argparse.Namespace) -> int:
    summary = args.summary.strip()
    if not summary:
        print("error: --summary is required", file=sys.stderr)
        return 2
    payload = {
        "summary": summary,
        "logged": datetime.now(timezone.utc).isoformat(),
        "related_skills": args.skill,
        "targets": args.target,
        "oses": args.os,
        "fields": list(INTEGRATION_FIELDS),
    }
    if args.json:
        emit_json(payload)
        return 0

    print("### Canonical Integration Plan")
    print(f"- Summary: {summary}")
    print(f"- Related Skills: {', '.join(args.skill) if args.skill else 'unknown'}")
    print(f"- Affected Install Targets: {', '.join(args.target) if args.target else 'codex, claude, deepseek, copilot, openclaw (verify limits)'}")
    print(f"- Affected OS/Substrates: {', '.join(args.os) if args.os else 'linux, macos, windows, wsl, mounted-windows (verify limits)'}")
    print("- Canonical Repo Change: update `canonical/`, `manifest/`, generated docs, and focused tests as needed")
    print("- Docs And Generated Outputs: update `installer/ai_agents_skills/docs.py` or manuals, then run `make docs`")
    print("- Verification Plan: run focused unit tests, `make runtime-smoke ARGS=\"--skills self-improving-agent\"`, and relevant lifecycle gates")
    print("- Blocked Or Unsupported Targets: state any Copilot/OpenClaw/native-OS limits explicitly before claiming coverage")
    return 0


def cmd_smoke(_: argparse.Namespace) -> int:
    emit_json(
        {
            "status": "ok",
            "smoke_mode": "offline",
            "no_auto_install": True,
            "network_required": False,
            "live_api_attempted": False,
            "package_install_attempted": False,
            "server_started": False,
            "config_written": False,
            "integration_plan_fields": list(INTEGRATION_FIELDS),
            "windows_error_patterns": True,
            "windows_safety_patterns": True,
        }
    )
    return 0


def emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="self-improving-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    review = sub.add_parser("review-pending")
    review.add_argument("target", nargs="?", default=None)
    review.add_argument("--high-only", action="store_true")
    review.add_argument("--json", action="store_true")
    review.set_defaults(func=cmd_review_pending)

    safety = sub.add_parser("check-command-safety")
    safety.add_argument("command", nargs=argparse.REMAINDER)
    safety.set_defaults(func=cmd_check_command_safety)

    errors = sub.add_parser("detect-common-errors")
    errors.add_argument("file", nargs="?")
    errors.set_defaults(func=cmd_detect_common_errors)

    plan = sub.add_parser("integration-plan")
    plan.add_argument("--summary", required=True)
    plan.add_argument("--skill", action="append", default=[])
    plan.add_argument("--target", action="append", default=[])
    plan.add_argument("--os", action="append", default=[])
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(func=cmd_integration_plan)

    smoke = sub.add_parser("smoke")
    smoke.set_defaults(func=cmd_smoke)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
