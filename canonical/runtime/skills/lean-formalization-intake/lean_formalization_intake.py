#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


STATUS_VALUES = {
    "detected",
    "missing",
    "reported",
    "statically_checked",
    "unchecked",
    "not_run",
    "not_applicable",
}

CHECKS_NOT_RUN = [
    "lean_build",
    "lake_build",
    "axle_api_call",
    "axle_check",
    "mcp_session",
    "safeverify_run",
    "lean4checker_run",
    "comparator_run",
    "strict_verifier",
    "python_package_install",
    "ci_status",
    "notebook_execution",
    "training_workflow",
]

DENIED_NAMES = {
    ".env",
    ".env.local",
    ".envrc",
    "secrets.json",
    "secrets.toml",
    "config.toml",
    "config.json",
}

DENIED_PARTS = {
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "reports",
    "cache",
    ".cache",
}

IGNORED_TRAVERSAL_PARTS = set(DENIED_PARTS)

SOURCE_SUFFIXES = {".tex", ".md", ".pdf"}
SIDECAR_SUFFIXES = {".py", ".sage", ".ipynb"}
TEXT_SUFFIXES = {".lean", ".md", ".toml", ".json", ".yaml", ".yml", ".txt"}

SECRET_PATTERNS = [
    re.compile(r"(?i)(token|secret|api[_-]?key|authorization)\s*[:=]\s*['\"]?([A-Za-z0-9_./+=:-]{8,})"),
    re.compile(r"\bgh[opsu]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]

PRIVATE_URL_RE = re.compile(r"https?://[^\s'\"]*(?:token|key|secret|signature|X-Amz-|auth)[^\s'\"]*", re.I)

COMMAND_RE = re.compile(r"(?im)^\s*(?:\$|>)?\s*((?:lake|lean|python|sage|pytest|make|git|curl|wget|scp|rsync|docker|kubectl|twine|npm|pnpm|yarn)\b[^\n`]*)")
DESTRUCTIVE_RE = re.compile(
    r"(?i)\b(rm\s+-rf|git\s+push|twine\s+upload|npm\s+publish|curl\b.*\|\s*(?:sh|bash)|wget\b.*\|\s*(?:sh|bash)|scp\b|rsync\b|docker\s+push|kubectl\s+apply|deploy|publish)\b"
)

SOUNDNESS_PATTERNS = [
    ("sorry", re.compile(r"\bsorry\b")),
    ("axiom", re.compile(r"\baxiom\b")),
    ("native_decide", re.compile(r"\bnative_decide\b")),
    ("implemented_by", re.compile(r"\bimplemented_by\b")),
    ("ffi", re.compile(r"@\[\s*extern|\bforeign\b|\bextern\b")),
    ("opaque", re.compile(r"\bopaque\b")),
    ("oracle", re.compile(r"\boracle(?:\b|_)", re.I)),
]

BANNED_PROOF_PHRASES = [
    "proof valid",
    "problem solved",
    "formally verified",
    "final verifier passed",
    "trusted proof",
]


def trust_claim() -> dict[str, Any]:
    return {
        "tier": "T0_STATIC_INTAKE",
        "runtime_behavior": "incomplete analysis",
        "incomplete_analysis": True,
        "checks_run": [],
        "checks_not_run": list(CHECKS_NOT_RUN),
        "tool_environment": None,
        "artifact_hashes": {},
        "allowed_axioms": "unchecked",
        "sorries_policy": "unchecked",
        "statement_intent_review": {"status": "not_reviewed"},
        "claim": "static metadata only; no proof-validity claim",
    }


def rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def status_entry(status: str, path: str | None = None, **extra: Any) -> dict[str, Any]:
    if status not in STATUS_VALUES:
        raise ValueError(f"invalid status: {status}")
    data: dict[str, Any] = {"status": status}
    if path is not None:
        data["path"] = path
    data.update(extra)
    return data


def sanitize_text(text: str) -> str:
    sanitized = text
    sanitized = PRIVATE_URL_RE.sub("<REDACTED_PRIVATE_URL>", sanitized)
    for pattern in SECRET_PATTERNS:
        sanitized = pattern.sub(lambda match: match.group(0).split(match.group(2))[0] + "<REDACTED_SECRET>" if match.lastindex else "<REDACTED_SECRET>", sanitized)
    sanitized = re.sub(r"(?i)(/home|/users|c:\\users)[^\s'\"`]+", "<REDACTED_LOCAL_PATH>", sanitized)
    return sanitized


def raw_secret_present(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS) or bool(PRIVATE_URL_RE.search(text))


def path_denied(relative: str) -> bool:
    path = Path(relative)
    if path.name in DENIED_NAMES:
        return True
    lowered_parts = {part.lower() for part in path.parts}
    if lowered_parts.intersection(DENIED_PARTS):
        return True
    if relative.endswith((".sqlite", ".sqlite3", ".db", ".db-wal", ".db-shm", ".zip", ".tar", ".gz", ".epub")):
        return True
    return False


def path_ignored_for_traversal(relative: str) -> bool:
    path = Path(relative)
    lowered_parts = {part.lower() for part in path.parts}
    return bool(lowered_parts.intersection(IGNORED_TRAVERSAL_PARTS))


def safe_read_text(root: Path, path: Path, gaps: list[dict[str, Any]]) -> str | None:
    relative = rel(root, path)
    if path_denied(relative):
        gaps.append(status_entry("not_run", relative, reason="denied_source_input"))
        return None
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        gaps.append(status_entry("unchecked", relative, reason="not_utf8"))
    except OSError as exc:
        gaps.append(status_entry("unchecked", relative, reason=f"read_error:{exc.__class__.__name__}"))
    return None


def list_files(root: Path, gaps: list[dict[str, Any]]) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        try:
            relative = rel(root, path)
        except ValueError:
            gaps.append(status_entry("unchecked", None, reason="path_escape"))
            continue
        if path.is_symlink():
            gaps.append(status_entry("unchecked", relative, reason="symlink_skipped"))
            continue
        if path.is_file():
            if path_ignored_for_traversal(relative):
                continue
            if path_denied(relative):
                gaps.append(status_entry("not_run", relative, reason="denied_source_input"))
                continue
            files.append(path)
    return files


def first_file(root: Path, files: list[Path], names: tuple[str, ...]) -> dict[str, Any]:
    for path in files:
        if path.name in names:
            return status_entry("detected", rel(root, path))
    return status_entry("missing")


def collect_named(root: Path, files: list[Path], name: str) -> list[dict[str, Any]]:
    return [status_entry("detected", rel(root, path)) for path in files if path.name == name]


def parse_lake_manifest(text: str, relative: str, gaps: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        gaps.append(status_entry("unchecked", relative, reason=f"malformed_json:{exc.msg}"))
        return {"status": "unchecked", "mathlib": status_entry("unchecked")}
    packages = payload.get("packages", []) if isinstance(payload, dict) else []
    mathlib = status_entry("missing")
    if isinstance(packages, list):
        for package in packages:
            if isinstance(package, dict) and str(package.get("name", "")).lower() == "mathlib":
                mathlib = status_entry(
                    "reported",
                    revision=package.get("rev") or package.get("revision"),
                    url=sanitize_text(str(package.get("url", ""))) if package.get("url") else None,
                )
                break
    return {"status": "statically_checked", "mathlib": mathlib}


def parse_lakefile_toml(text: str) -> dict[str, Any]:
    values: dict[str, Any] = {"status": "statically_checked"}
    name_match = re.search(r"(?m)^\s*name\s*=\s*\"([^\"]+)\"", text)
    if name_match:
        values["name"] = name_match.group(1)
    if text.count("[[require]]") or "mathlib" in text.lower():
        values["dependencies_reported"] = True
    if text.count('"') % 2:
        values["parse_warning"] = "possibly_malformed_toml"
    return values


def classify_commands(text: str) -> tuple[list[str], list[str]]:
    candidate: list[str] = []
    do_not_run: list[str] = []
    for match in COMMAND_RE.finditer(text):
        command = sanitize_text(match.group(1).strip())
        if not command:
            continue
        if DESTRUCTIVE_RE.search(command):
            do_not_run.append(command)
        elif command.startswith(("lake ", "lean ", "make ", "pytest", "python ")):
            candidate.append(command)
    return sorted(set(candidate)), sorted(set(do_not_run))


def scan_soundness(root: Path, path: Path, text: str) -> list[dict[str, Any]]:
    findings = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in SOUNDNESS_PATTERNS:
            if pattern.search(line):
                findings.append(
                    {
                        "status": "reported",
                        "kind": kind,
                        "path": rel(root, path),
                        "line": line_no,
                        "advisory_only": True,
                        "snippet": sanitize_text(line.strip())[:160],
                    }
                )
    return findings


def compute_risk(files: list[Path], texts: dict[str, str]) -> dict[str, Any]:
    signals = []
    for path in files:
        if path.suffix.lower() == ".ipynb":
            signals.append("notebook_present")
        if path.name.lower() in {"train.py", "finetune.py"}:
            signals.append("training_script_present")
    joined = "\n".join(texts.values()).lower()
    for token, signal in (("cuda", "gpu_reference"), ("benchmark", "benchmark_reference"), ("modal", "remote_compute_reference")):
        if token in joined:
            signals.append(signal)
    if signals:
        return {"status": "reported", "risk": "medium", "signals": sorted(set(signals))}
    return {"status": "reported", "risk": "low", "signals": []}


def scan_repo(repo: Path, source_id: str | None = None, expected_family: str | None = None) -> dict[str, Any]:
    root = repo.resolve()
    gaps: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if not root.exists() or not root.is_dir():
        return base_output(repo, source_id, expected_family, errors=[status_entry("unchecked", str(repo), reason="repo_not_found")])

    files = list_files(root, gaps)
    texts: dict[str, str] = {}
    redactions: list[dict[str, Any]] = []
    candidate_commands: set[str] = set()
    do_not_run_commands: set[str] = set()
    advisory: list[dict[str, Any]] = []

    for path in files:
        text = safe_read_text(root, path, gaps)
        if text is None:
            continue
        relative = rel(root, path)
        sanitized = sanitize_text(text)
        if sanitized != text or raw_secret_present(text):
            redactions.append(status_entry("reported", relative, reason="sensitive_material_redacted"))
        texts[relative] = sanitized
        candidate, do_not_run = classify_commands(text)
        candidate_commands.update(candidate)
        do_not_run_commands.update(do_not_run)
        if path.suffix.lower() == ".lean":
            advisory.extend(scan_soundness(root, path, text))

    lean_toolchain = first_file(root, files, ("lean-toolchain",))
    environment = first_file(root, files, (".environment",))
    lakefile_toml = first_file(root, files, ("lakefile.toml",))
    lakefile_lean = first_file(root, files, ("lakefile.lean",))
    lake_manifest = first_file(root, files, ("lake-manifest.json",))
    license_file = first_file(root, files, ("LICENSE", "LICENSE.md", "COPYING"))

    lakefile_toml_data = status_entry("not_applicable")
    if lakefile_toml["status"] == "detected":
        lakefile_toml_data = parse_lakefile_toml(texts.get(lakefile_toml["path"], ""))

    lake_manifest_data = status_entry("missing")
    mathlib_revision = status_entry("missing")
    if lake_manifest["status"] == "detected":
        parsed = parse_lake_manifest(texts.get(lake_manifest["path"], ""), lake_manifest["path"], gaps)
        lake_manifest_data = {"status": parsed["status"]}
        mathlib_revision = parsed["mathlib"]

    ci_files = [
        status_entry("detected", rel(root, path))
        for path in files
        if ".github/workflows" in rel(root, path) and path.suffix.lower() in {".yml", ".yaml"}
    ]
    source_materials = [
        status_entry("detected", rel(root, path), kind=path.suffix.lower().lstrip("."))
        for path in files
        if path.suffix.lower() in SOURCE_SUFFIXES
    ]
    sidecars = [
        status_entry("detected", rel(root, path), kind=path.suffix.lower().lstrip("."))
        for path in files
        if path.suffix.lower() in SIDECAR_SUFFIXES
    ]

    fields = {
        "lean_toolchain": lean_toolchain,
        "environment": environment,
        "lakefile_toml": {**lakefile_toml, "metadata": lakefile_toml_data},
        "lakefile_lean": lakefile_lean,
        "lake_manifest": {**lake_manifest, "metadata": lake_manifest_data},
        "mathlib_revision": mathlib_revision,
        "license": license_file,
        "ci": {"status": "detected" if ci_files else "missing", "files": ci_files},
        "task": {"status": "detected" if collect_named(root, files, "task.md") else "missing", "files": collect_named(root, files, "task.md")},
        "requirement": {"status": "detected" if collect_named(root, files, "requirement.md") else "missing", "files": collect_named(root, files, "requirement.md")},
        "problem_files": {"status": "detected" if collect_named(root, files, "problem.lean") else "missing", "files": collect_named(root, files, "problem.lean")},
        "solution_files": {"status": "detected" if collect_named(root, files, "solution.lean") else "missing", "files": collect_named(root, files, "solution.lean")},
        "source_materials": {"status": "detected" if source_materials else "missing", "files": source_materials},
        "sidecars": {"status": "detected" if sidecars else "missing", "files": sidecars},
    }

    missing_fields = [
        name
        for name, value in fields.items()
        if isinstance(value, dict) and value.get("status") == "missing"
    ]

    output = base_output(root, source_id, expected_family, errors=errors)
    output.update(
        {
            "repo": {
                "source_id": source_id or root.name,
                "expected_family": expected_family,
                "path": ".",
                "path_status": "statically_checked",
            },
            "fields": fields,
            "candidate_commands": [status_entry("reported", command=cmd, executed=False) for cmd in sorted(candidate_commands)],
            "do_not_run_commands": [status_entry("reported", command=cmd, executed=False) for cmd in sorted(do_not_run_commands)],
            "advisory_soundness_signals": advisory,
            "redactions": redactions,
            "compute_risk": compute_risk(files, texts),
            "missing_fields": missing_fields,
            "unchecked_gaps": gaps,
        }
    )
    return output


def base_output(
    repo: Path,
    source_id: str | None,
    expected_family: str | None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "lean-formalization-intake.v1",
        "ok": not errors,
        "runtime_behavior": "incomplete analysis",
        "incomplete_analysis": True,
        "repo": {
            "source_id": source_id or repo.name,
            "expected_family": expected_family,
            "path": ".",
            "path_status": "unchecked",
        },
        "fields": {},
        "candidate_commands": [],
        "do_not_run_commands": [],
        "advisory_soundness_signals": [],
        "redactions": [],
        "compute_risk": {"status": "unchecked"},
        "missing_fields": [],
        "unchecked_gaps": [],
        "errors": errors or [],
        "trust_claim": trust_claim(),
    }


def validate_contract(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("runtime_behavior") != "incomplete analysis":
        errors.append("runtime_behavior must be incomplete analysis")
    if payload.get("incomplete_analysis") is not True:
        errors.append("incomplete_analysis must be true")
    claim = payload.get("trust_claim", {})
    if claim.get("tier") != "T0_STATIC_INTAKE":
        errors.append("trust_claim.tier must be T0_STATIC_INTAKE")
    if claim.get("checks_run") != []:
        errors.append("trust_claim.checks_run must be empty")
    if claim.get("allowed_axioms") != "unchecked":
        errors.append("trust_claim.allowed_axioms must be unchecked")
    if claim.get("sorries_policy") != "unchecked":
        errors.append("trust_claim.sorries_policy must be unchecked")
    if claim.get("statement_intent_review", {}).get("status") != "not_reviewed":
        errors.append("statement_intent_review.status must be not_reviewed")
    serialized = json.dumps(payload).lower()
    for phrase in BANNED_PROOF_PHRASES:
        if phrase in serialized:
            errors.append(f"banned proof phrase present: {phrase}")
    return errors


def render_markdown(payload: dict[str, Any]) -> str:
    fields = payload.get("fields", {})
    lines = [
        "# Lean Formalization Intake",
        "",
        "Runtime behavior: incomplete analysis",
        "",
        f"Source ID: `{payload.get('repo', {}).get('source_id')}`",
        "",
        "## Static Fields",
        "",
    ]
    for name in sorted(fields):
        value = fields[name]
        if isinstance(value, dict):
            lines.append(f"- `{name}`: `{value.get('status', 'unchecked')}`")
    lines.extend(["", "## Trust Claim", "", "- Tier: `T0_STATIC_INTAKE`", "- Claim: static metadata only; no proof-validity claim", ""])
    if payload.get("advisory_soundness_signals"):
        lines.extend(["## Advisory Soundness Signals", ""])
        for item in payload["advisory_soundness_signals"]:
            lines.append(f"- `{item['kind']}` at `{item['path']}:{item['line']}` advisory only")
        lines.append("")
    if payload.get("unchecked_gaps"):
        lines.extend(["## Unchecked Gaps", ""])
        for item in payload["unchecked_gaps"]:
            lines.append(f"- `{item.get('path', 'repo')}`: `{item.get('reason', item.get('status'))}`")
        lines.append("")
    return "\n".join(lines)


def write_text(path: Path, text: str) -> None:
    if path.exists() and path.is_symlink():
        raise ValueError(f"refusing to replace symlink output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Static Lean formalization intake")
    parser.add_argument("repo", help="explicit local repository path to inspect")
    parser.add_argument("--source-id", default=None)
    parser.add_argument("--expected-family", default=None)
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--output-md", default=None)
    args = parser.parse_args(argv)

    payload = scan_repo(Path(args.repo), source_id=args.source_id, expected_family=args.expected_family)
    contract_errors = validate_contract(payload)
    if contract_errors:
        payload["ok"] = False
        payload.setdefault("errors", []).extend(status_entry("unchecked", reason=error) for error in contract_errors)

    json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output_json:
        write_text(Path(args.output_json), json_text)
    else:
        sys.stdout.write(json_text)
    if args.output_md:
        write_text(Path(args.output_md), render_markdown(payload))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
