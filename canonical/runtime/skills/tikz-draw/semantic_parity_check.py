#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
CODEX_HELPER = HERE / "tikz_draw.py"
CLAUDE_HELPER = Path.home() / ".claude" / "skills" / "tikz-draw" / "tikz_draw.py"
CODEX_GRAPH_BACKEND = HERE / "sage_graph_backend.py"
CLAUDE_GRAPH_BACKEND = Path.home() / ".claude" / "skills" / "tikz-draw" / "sage_graph_backend.py"
CODEX_FAMILY_VERIFIERS = HERE / "family_verifiers.py"
CLAUDE_FAMILY_VERIFIERS = Path.home() / ".claude" / "skills" / "tikz-draw" / "family_verifiers.py"
CODEX_DIAGRAM_SCHEMA = HERE / "assets" / "spec-schema" / "diagram.schema.json"
CLAUDE_DIAGRAM_SCHEMA = Path.home() / ".claude" / "skills" / "tikz-draw" / "assets" / "spec-schema" / "diagram.schema.json"
CODEX_FIGURE_BRIEF_SCHEMA = HERE / "assets" / "spec-schema" / "figure-brief.schema.json"
CLAUDE_FIGURE_BRIEF_SCHEMA = Path.home() / ".claude" / "skills" / "tikz-draw" / "assets" / "spec-schema" / "figure-brief.schema.json"
CODEX_RUNNER = HERE / "semantic_regression_runner.py"
CLAUDE_RUNNER = Path.home() / ".claude" / "skills" / "tikz-draw" / "semantic_regression_runner.py"
CODEX_SUITE = HERE / "assets" / "examples" / "semantic-regression" / "suite.json"
CLAUDE_SUITE = Path.home() / ".claude" / "skills" / "tikz-draw" / "assets" / "examples" / "semantic-regression" / "suite.json"
CODEX_SKILL = Path.home() / ".codex" / "skills" / "tikz-draw" / "SKILL.md"
CLAUDE_SKILL = Path.home() / ".claude" / "skills" / "tikz-draw" / "SKILL.md"
CLAUDE_COMMAND = Path.home() / ".claude" / "commands" / "tikz.md"
PY_VER = f"{sys.version_info.major}.{sys.version_info.minor}"


def prime_import_paths() -> None:
    candidates = [
        str(HERE),
        str(CLAUDE_HELPER.parent),
        str(Path.home() / ".codex" / "runtime" / "workspace" / ".local" / "lib" / f"python{PY_VER}" / "site-packages"),
        str(Path.home() / ".local" / "lib" / f"python{PY_VER}" / "site-packages"),
        str(Path.home() / ".claude" / ".local"),
    ]
    for candidate in candidates:
        if candidate not in sys.path and Path(candidate).exists():
            sys.path.insert(0, candidate)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parser_commands(module) -> list[str]:
    parser = module.build_parser()
    subparsers_action = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    return sorted(subparsers_action.choices.keys())


def main() -> int:
    prime_import_paths()
    codex = load_module(CODEX_HELPER, "codex_tikz_draw")
    claude = load_module(CLAUDE_HELPER, "claude_tikz_draw")

    mismatches: list[str] = []

    if tuple(codex.CLI_VERBS) != tuple(claude.CLI_VERBS):
        mismatches.append("CLI_VERBS differ")
    if codex.STATIC_RULES != claude.STATIC_RULES:
        mismatches.append("STATIC_RULES differ")
    if tuple(codex.VISUAL_REVIEW_PASS_IDS) != tuple(claude.VISUAL_REVIEW_PASS_IDS):
        mismatches.append("VISUAL_REVIEW_PASS_IDS differ")
    if tuple(codex.SEMANTIC_VERIFIER_FAMILIES) != tuple(claude.SEMANTIC_VERIFIER_FAMILIES):
        mismatches.append("SEMANTIC_VERIFIER_FAMILIES differ")
    if tuple(codex.MANIFEST_FRESHNESS_FIELDS) != tuple(claude.MANIFEST_FRESHNESS_FIELDS):
        mismatches.append("MANIFEST_FRESHNESS_FIELDS differ")
    if tuple(codex.GRAPH_MODE_VALUES) != tuple(claude.GRAPH_MODE_VALUES):
        mismatches.append("GRAPH_MODE_VALUES differ")
    if tuple(codex.GRAPH_ROUTE_STATUSES) != tuple(claude.GRAPH_ROUTE_STATUSES):
        mismatches.append("GRAPH_ROUTE_STATUSES differ")
    if tuple(codex.SEMANTIC_REPORT_FIELDS) != tuple(claude.SEMANTIC_REPORT_FIELDS):
        mismatches.append("SEMANTIC_REPORT_FIELDS differ")
    if codex.RENDER_SEMANTICS_SCHEMA_VERSION != claude.RENDER_SEMANTICS_SCHEMA_VERSION:
        mismatches.append("RENDER_SEMANTICS_SCHEMA_VERSION differs")
    if codex.RENDER_SEMANTICS_EXTRACTOR_VERSION != claude.RENDER_SEMANTICS_EXTRACTOR_VERSION:
        mismatches.append("RENDER_SEMANTICS_EXTRACTOR_VERSION differs")
    if parser_commands(codex) != parser_commands(claude):
        mismatches.append("parser subcommands differ")
    if file_sha256(Path.home() / ".codex" / "runtime" / "workspace" / "skills" / "tikz-draw" / "run_tikz_draw.sh") != file_sha256(Path.home() / ".claude" / "skills" / "tikz-draw" / "run_tikz_draw.sh"):
        mismatches.append("run_tikz_draw.sh differs across platforms")
    if file_sha256(CODEX_GRAPH_BACKEND) != file_sha256(CLAUDE_GRAPH_BACKEND):
        mismatches.append("sage_graph_backend.py differs across platforms")
    if file_sha256(CODEX_FAMILY_VERIFIERS) != file_sha256(CLAUDE_FAMILY_VERIFIERS):
        mismatches.append("family_verifiers.py differs across platforms")
    if file_sha256(CODEX_DIAGRAM_SCHEMA) != file_sha256(CLAUDE_DIAGRAM_SCHEMA):
        mismatches.append("diagram.schema.json differs across platforms")
    if file_sha256(CODEX_FIGURE_BRIEF_SCHEMA) != file_sha256(CLAUDE_FIGURE_BRIEF_SCHEMA):
        mismatches.append("figure-brief.schema.json differs across platforms")
    if file_sha256(CODEX_RUNNER) != file_sha256(CLAUDE_RUNNER):
        mismatches.append("semantic_regression_runner.py differs across platforms")
    if file_sha256(CODEX_SUITE) != file_sha256(CLAUDE_SUITE):
        mismatches.append("semantic regression suite differs across platforms")

    required_doc_tokens = {
        CODEX_SKILL: ["review-visual", "verify-semantic", "render-semantics.json", "unsupported family", "flowchart", "dag", "tree", "commutative", "graph", "Sage", "Sage-assisted", "baseline", "tikz-prevention.md", "tikz-measurement.md", "semantic_regression_runner.py"],
        CLAUDE_SKILL: ["review-visual", "verify-semantic", "render-semantics.json", "unsupported family", "flowchart", "dag", "tree", "commutative", "graph", "Sage", "Sage-assisted", "baseline", "tikz-prevention.md", "tikz-measurement.md", "semantic_regression_runner.py"],
        CLAUDE_COMMAND: ["review-visual", "verify-semantic", "--artifacts", "--work-dir", "render-semantics.json", "unsupported family", "flowchart", "dag", "tree", "commutative", "graph", "Sage", "Sage-assisted", "baseline", "semantic_regression_runner.py"],
    }
    for path, tokens in required_doc_tokens.items():
        text = path.read_text(encoding="utf-8")
        for token in tokens:
            if token not in text:
                mismatches.append(f"{path} missing token: {token}")

    payload = {
        "status": "OK" if not mismatches else "MISMATCH",
        "mismatches": mismatches,
        "codex_helper": str(CODEX_HELPER),
        "claude_helper": str(CLAUDE_HELPER),
        "commands": parser_commands(codex),
        "static_rule_ids": list(codex.STATIC_RULES.keys()),
        "visual_review_pass_ids": list(codex.VISUAL_REVIEW_PASS_IDS),
        "semantic_verifier_families": list(codex.SEMANTIC_VERIFIER_FAMILIES),
        "manifest_freshness_fields": list(codex.MANIFEST_FRESHNESS_FIELDS),
        "graph_mode_values": list(codex.GRAPH_MODE_VALUES),
        "graph_route_statuses": list(codex.GRAPH_ROUTE_STATUSES),
        "semantic_report_fields": list(codex.SEMANTIC_REPORT_FIELDS),
        "render_semantics_schema_version": codex.RENDER_SEMANTICS_SCHEMA_VERSION,
        "render_semantics_extractor_version": codex.RENDER_SEMANTICS_EXTRACTOR_VERSION,
        "semantic_regression_runner_sha256": file_sha256(CODEX_RUNNER),
        "semantic_regression_suite_sha256": file_sha256(CODEX_SUITE),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0 if payload["status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
