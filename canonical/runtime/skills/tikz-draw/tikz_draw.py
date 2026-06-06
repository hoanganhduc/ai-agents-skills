#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from pdf_extract import (
    EXTRACTOR_VERSION as RENDER_SEMANTICS_EXTRACTOR_VERSION,
    RENDER_SEMANTICS_SCHEMA_VERSION,
    extract_pdf_render_semantics,
)
try:
    from family_verifiers import SUPPORTED_SEMANTIC_FAMILIES, verify_rendered_family
except Exception as exc:  # noqa: BLE001
    FAMILY_VERIFIER_IMPORT_ERROR = exc
    SUPPORTED_SEMANTIC_FAMILIES = ("flowchart", "dag", "tree", "commutative", "graph")

    def verify_rendered_family(_spec: dict[str, Any], _render_semantics: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"semantic family verifier dependencies are unavailable: {FAMILY_VERIFIER_IMPORT_ERROR}")
else:
    FAMILY_VERIFIER_IMPORT_ERROR = None
from sage_graph_backend import (
    GRAPH_MODE_VALUES,
    GRAPH_ROUTE_STATUSES,
    extract_graph_query,
    parse_bool_text,
    run_sage_graph_query,
    sagemath_backend_status,
)


SCRIPT_DIR = Path(__file__).resolve().parent
ASSETS_DIR = SCRIPT_DIR / "assets"
SCHEMA_DIR = ASSETS_DIR / "spec-schema"
CHECKS_DIR = ASSETS_DIR / "checks"
STYLES_DIR = ASSETS_DIR / "styles"
TEMPLATES_DIR = ASSETS_DIR / "templates" / "tikz-snippets"
IS_WINDOWS = os.name == "nt"
CLI_PROG = "run_tikz_draw.bat" if IS_WINDOWS else "run_tikz_draw.sh"


def detect_platform_name(script_dir: Path) -> str:
    parts = set(script_dir.parts)
    if ".codex" in parts:
        return "codex"
    if ".claude" in parts:
        return "claude"
    if ".deepseek" in parts:
        return "deepseek"
    return "ai-agents-skills"


PLATFORM_NAME = detect_platform_name(SCRIPT_DIR)

SUPPORTED_FAMILIES = {"flowchart", "dag", "tree", "commutative", "graph"}
SEMANTIC_VERIFIER_FAMILIES = tuple(SUPPORTED_SEMANTIC_FAMILIES)
BACKEND_BY_FAMILY = {
    "flowchart": "positioning",
    "dag": "positioning",
    "tree": "forest",
    "commutative": "tikz-cd",
    "graph": "raw-tikz",
}

CLI_VERBS = (
    "doctor",
    "contract",
    "design",
    "spec",
    "render",
    "check",
    "compile",
    "review-visual",
    "verify-design",
    "verify-semantic",
    "approve",
    "review",
    "extract",
)

STATIC_RULES = {
    "P0_ADJUSTBOX_ENV": "document-facing output must use the adjustbox environment wrapper",
    "P0_STANDALONE_CLASS": "standalone output using width-fit must use plain standalone class, not standalone[tikz]",
    "P0_ADJUSTBOX_PACKAGE": "standalone output must load adjustbox",
    "P1_BOXED_NODE_DIMENSIONS": "boxed text-bearing nodes must declare explicit dimensions",
    "P2_COORDINATE_MAP": "nontrivial diagrams should include a coordinate-map comment block",
    "P3_BARE_SCALE": "bare scale= is not allowed without matching node scaling",
    "P4_DIRECTIONAL_EDGE_LABELS": "edge labels must include explicit directional or anchoring placement",
    "P5_EXTRACT_FRESHNESS": "extracted figures require freshness metadata and current source-of-truth alignment",
    "P6_EXPLICIT_GRAPH_CLOSURE": "verification-sensitive graph closures must use explicit final edges instead of cycle",
    "P7_APPROVAL_PROVENANCE": "strict approval requires current generated artifacts and provenance hashes",
    "P8_SYMMETRY_CONTRACT": "strict approval requires a declared symmetry contract",
    "P9_DESIGN_CONTRACT": "strict approval requires a visual-semantic design contract for scoped semantic figures",
}

VISUAL_REVIEW_PASS_IDS = (
    "V1_LABEL_GAP",
    "V2_BOUNDARY_CLEARANCE",
    "V3_PAGE_MARGIN",
    "V4_TEXT_TEXT_OVERLAP",
    "V5_TEXT_SHAPE_OVERLAP",
    "V6_LINE_TEXT_OVERLAP",
    "V7_LINE_SHAPE_OVERLAP",
    "V8_SHAPE_SHAPE_OVERLAP",
)

STRICT_APPROVAL_VERSION = "strict-approval.v1"
FIGURE_CONTRACT_VERSION = "figure-contract.v1"
FIGURE_DESIGN_VERSION = "figure-design.v1"
DESIGN_MARK_ROLES = (
    "graph_object",
    "annotation",
    "callout",
    "correspondence",
    "gadget_region",
    "highlight_region",
    "legend",
)
DESIGN_GATE_INTENT_TOKENS = ("graph_hardness_reduction", "proof", "reduction", "gadget")
SYMMETRY_CONTRACT_STATUSES = ("required", "not_required", "intentionally_asymmetric")
SYMMETRY_MODES = (
    "mirror_vertical_axis",
    "mirror_horizontal_axis",
    "row_alignment",
    "column_alignment",
    "paired_panels",
)
DEFAULT_SYMMETRY_TOLERANCE_PT = 3.0
OVERLAP_EPSILON_PT = 0.25

MANIFEST_FRESHNESS_FIELDS = (
    "source_hash",
    "source_mtime",
    "extracted_from",
    "freshness_status",
)

GRAPH_ROUTE_FIELDS = (
    "graph_mode_requested",
    "graph_route_status",
    "graph_route_reason",
    "graph_backend_used",
)

MANIFEST_REQUIRED_FIELDS = {
    "run_id",
    "run_root",
    "work_dir",
    "figure_id",
    "diagram_family",
    "figure_brief",
    "figure_tex",
    "standalone_tex",
    "diagram_spec",
    "figure_design",
    "pdf",
    "svg",
    "source_ids",
    "render_semantics",
    "semantic_review",
    "semantic_target_present",
    "approval_contract_version",
    "artifact_hashes",
    *MANIFEST_FRESHNESS_FIELDS,
    *GRAPH_ROUTE_FIELDS,
}

SEMANTIC_REPORT_FIELDS = (
    "review_status",
    "family",
    "static_status",
    "visual_status",
    "overlap_status",
    "compile_status",
    "semantic_status",
    "design_status",
    "symmetry_status",
    "semantic_verdict",
    "final_verdict",
    "supported_family",
    "mismatches",
    "mismatch_codes",
    "rule_hits",
    "rule_refs",
    "warnings",
    "visual_review",
    "design_review",
    "evidence",
    *GRAPH_ROUTE_FIELDS,
)

REQUIRED_SEMANTIC_MODULES = {
    "fitz": "PyMuPDF / fitz",
    "shapely": "shapely",
}

OPTIONAL_SEMANTIC_MODULES = {
    "svgelements": "svgelements",
}

VISUAL_THRESHOLDS_PT = {
    "V1_LABEL_GAP": 2.0,
    "V2_BOUNDARY_CLEARANCE": 3.0,
    "V3_PAGE_MARGIN": 5.0,
    "V4_TEXT_TEXT_OVERLAP": OVERLAP_EPSILON_PT,
    "V5_TEXT_SHAPE_OVERLAP": OVERLAP_EPSILON_PT,
    "V6_LINE_TEXT_OVERLAP": OVERLAP_EPSILON_PT,
    "V7_LINE_SHAPE_OVERLAP": OVERLAP_EPSILON_PT,
    "V8_SHAPE_SHAPE_OVERLAP": OVERLAP_EPSILON_PT,
}

BRIEF_REQUIRED = {
    "figure_id",
    "title",
    "purpose",
    "source_ids",
    "diagram_family",
    "content_requirements",
    "layout_constraints",
    "output_dir",
}

FIGURE_CONTRACT_REQUIRED = {
    "schema_version",
    "figure_id",
    "request",
    "context_summary",
    "recommended_diagram_family",
    "intent",
    "required_objects",
    "required_relations",
    "forbidden_simplifications",
    "notation_requirements",
    "approval_criteria",
}

FIGURE_DESIGN_REQUIRED = {
    "schema_version",
    "figure_id",
    "design_intent",
    "audience_task",
    "caption_claims",
    "source_prose_claims",
    "marks",
    "visual_encoding_policy",
    "rationale",
    "approval_requirements",
}

SPEC_REQUIRED = {
    "diagram_family",
    "tikz_backend",
    "title",
    "nodes",
    "edges",
    "groups",
    "layout_constraints",
    "validation_rules",
}


def candidate_tool_paths(tool: str) -> list[Path]:
    if not IS_WINDOWS:
        return []
    if tool not in {"latexmk", "pdflatex", "dvisvgm"}:
        return []
    candidates: list[Path] = []
    texlive_root = Path("C:/texlive")
    if texlive_root.is_dir():
        for version_dir in sorted((path for path in texlive_root.iterdir() if path.is_dir()), reverse=True):
            candidates.append(version_dir / "bin" / "windows" / f"{tool}.exe")
    miktex_roots = (
        Path.home() / "AppData" / "Local" / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64",
        Path("C:/Program Files/MiKTeX/miktex/bin/x64"),
    )
    for root in miktex_roots:
        candidates.append(root / f"{tool}.exe")
    return candidates


def resolve_tool(tool: str) -> str | None:
    if tool == "python":
        names = ("python", "python3", "py") if IS_WINDOWS else ("python3", "python")
        for name in names:
            resolved = shutil.which(name)
            if resolved:
                return resolved
        current = Path(sys.executable)
        if current.is_file():
            return str(current)
        return None
    resolved = shutil.which(tool)
    if resolved:
        return resolved
    for candidate in candidate_tool_paths(tool):
        if candidate.is_file():
            return str(candidate)
    return None


def tool_environment() -> dict[str, str]:
    env = dict(os.environ)
    path_entries: list[str] = []
    for tool in ("latexmk", "pdflatex", "dvisvgm"):
        resolved = resolve_tool(tool)
        if resolved:
            parent = str(Path(resolved).parent)
            if parent not in path_entries:
                path_entries.append(parent)
    if path_entries:
        existing = env.get("PATH", "")
        env["PATH"] = os.pathsep.join([*path_entries, existing] if existing else path_entries)
    return env


def probe_tool(tool_path: str, args: list[str]) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            [tool_path, *args],
            text=True,
            capture_output=True,
            timeout=20,
            env=tool_environment(),
        )
    except Exception as exc:  # noqa: BLE001
        return {"probe_status": "FAILED", "error": str(exc)}
    return {
        "probe_status": "OK" if proc.returncode == 0 else "FAILED",
        "exit_code": proc.returncode,
        "stdout": proc.stdout[:500],
        "stderr": proc.stderr[:500],
    }


def abs_path(value: str | None) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve(strict=False)
    return path.resolve(strict=False)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(read_text(path))


def dump_json(path: Path, payload: dict[str, Any]) -> None:
    write_text(path, json.dumps(payload, indent=2, ensure_ascii=True) + "\n")


def ensure_keys(payload: dict[str, Any], required: set[str], kind: str) -> None:
    missing = sorted(required - set(payload))
    if missing:
        raise SystemExit(f"{kind} missing required keys: {', '.join(missing)}")


def default_symmetry_contract(reason: str) -> dict[str, Any]:
    return {
        "status": "not_required",
        "justification": reason,
    }


def normalize_symmetry_contract(value: Any, *, default_reason: str | None = None) -> dict[str, Any]:
    if value is None:
        if default_reason is None:
            raise SystemExit("symmetry_contract is required for strict approval")
        return default_symmetry_contract(default_reason)
    if not isinstance(value, dict):
        raise SystemExit("symmetry_contract must be an object")
    status = str(value.get("status", "")).strip()
    if status not in SYMMETRY_CONTRACT_STATUSES:
        raise SystemExit(
            "symmetry_contract.status must be one of: " + ", ".join(SYMMETRY_CONTRACT_STATUSES)
        )
    contract = dict(value)
    contract["status"] = status
    if status in {"not_required", "intentionally_asymmetric"}:
        justification = str(contract.get("justification", "")).strip()
        if not justification:
            raise SystemExit(f"symmetry_contract.status={status!r} requires a nonempty justification")
        contract["justification"] = justification
    if status == "required":
        mode = str(contract.get("mode", "mirror_vertical_axis")).strip()
        if mode not in SYMMETRY_MODES:
            raise SystemExit("symmetry_contract.mode must be one of: " + ", ".join(SYMMETRY_MODES))
        contract["mode"] = mode
        if "tolerance_pt" in contract:
            contract["tolerance_pt"] = float(contract["tolerance_pt"])
        else:
            contract["tolerance_pt"] = DEFAULT_SYMMETRY_TOLERANCE_PT
    return contract


def compact_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def text_entries(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    entries: list[str] = []
    for value in values:
        text = compact_ws(str(value))
        if text:
            entries.append(text)
    return entries


def entry_dict(value: Any, *, prefix: str, index: int, field: str = "description") -> dict[str, str]:
    if isinstance(value, dict):
        payload = {str(key): str(item) for key, item in value.items() if item is not None}
        if "id" not in payload:
            basis = payload.get(field) or payload.get("label") or prefix
            payload["id"] = slugify(basis, f"{prefix}{index + 1}")
        return payload
    text = compact_ws(str(value))
    return {"id": slugify(text, f"{prefix}{index + 1}"), field: text}


def normalize_contract_entries(values: Any, *, prefix: str, field: str = "description") -> list[dict[str, str]]:
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    return [entry_dict(value, prefix=prefix, index=index, field=field) for index, value in enumerate(values)]


def contract_item_text(item: Any) -> str:
    if isinstance(item, dict):
        return compact_ws(" ".join(str(value) for value in item.values() if value is not None))
    return compact_ws(str(item))


def unique_contract_entries(items: list[dict[str, str]], *, key: str = "description") -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for item in items:
        basis = compact_ws(item.get(key) or item.get("label") or contract_item_text(item)).lower()
        if not basis or basis in seen:
            continue
        seen.add(basis)
        result.append(item)
    return result


def extract_math_labels(text: str) -> list[str]:
    labels: list[str] = []
    for match in re.finditer(r"\$([^$]+)\$", text):
        label = "$" + compact_ws(match.group(1)) + "$"
        labels.append(label)
    for match in re.finditer(r"\\\(?([A-Za-z0-9_{}^,=+\-\\ ]{2,})\\\)?", text):
        body = compact_ws(match.group(1))
        if any(token in body for token in ("_", "^", "\\")):
            labels.append(body)
    return list(dict.fromkeys(labels))


def read_context_inputs(args: argparse.Namespace) -> tuple[str, list[str]]:
    chunks: list[str] = []
    evidence: list[str] = []
    for index, text in enumerate(text_entries(getattr(args, "context_text", None))):
        chunks.append(text)
        evidence.append(f"--context-text[{index}]")
    for value in text_entries(getattr(args, "context_file", None)):
        path = abs_path(value)
        if path is None or not path.is_file():
            raise SystemExit(f"context file is missing: {value}")
        chunks.append(read_text(path))
        evidence.append(str(path))
    source_tex = abs_path(getattr(args, "source_tex", None))
    if source_tex is not None:
        if not source_tex.is_file():
            raise SystemExit(f"source tex file is missing: {source_tex}")
        text = read_text(source_tex)
        around_label = compact_ws(str(getattr(args, "around_label", "") or ""))
        if around_label:
            index = text.find(around_label)
            if index >= 0:
                start = max(index - 1800, 0)
                end = min(index + len(around_label) + 1800, len(text))
                text = text[start:end]
        chunks.append(text)
        evidence.append(str(source_tex))
    return "\n\n".join(chunks), evidence


def classify_diagram_family(
    text: str,
    *,
    requested_family: str | None = None,
) -> tuple[str, dict[str, Any]]:
    lowered = text.lower()
    scores = {family: 0 for family in SUPPORTED_FAMILIES}
    evidence: list[str] = []

    def hit(family: str, points: int, pattern: str, description: str) -> None:
        if re.search(pattern, lowered, re.IGNORECASE):
            scores[family] += points
            evidence.append(description)

    hit("graph", 5, r"\bgraph(s)?\b|\bvertices\b|\bvertex\b", "graph/vertex vocabulary")
    hit("graph", 4, r"\bedge(s)?\b.*\bgadget\b|\bgadget\b.*\bedge(s)?\b", "edge-gadget vocabulary")
    hit("graph", 4, r"\bhardness reduction\b|\breduction\b.*\bgadget\b", "hardness-reduction/gadget vocabulary")
    hit("graph", 3, r"\breplac(?:e|ed|ing|ement)\b.*\bedge\b|\bedge\b.*\breplac(?:e|ed|ing|ement)\b", "edge replacement vocabulary")
    hit("graph", 2, r"kempe|claw-free|k_\{1,|k1,|constructed instance|original instance|source instance", "graph reconfiguration context")

    hit("commutative", 5, r"commutative|tikz-cd|category|morphism|pullback|pushout", "commutative-diagram vocabulary")
    hit("tree", 4, r"\btree\b|rooted|children|parent|branch", "tree vocabulary")
    hit("dag", 4, r"\bdag\b|dependency graph|acyclic", "DAG vocabulary")
    hit("flowchart", 4, r"flowchart|pipeline|workflow|process|validation loop|decision", "flowchart/process vocabulary")

    if requested_family:
        scores[requested_family] += 1
        evidence.append(f"explicit requested family: {requested_family}")

    family = max(sorted(scores), key=lambda item: scores[item])
    if scores[family] <= 0:
        family = requested_family or "flowchart"
        evidence.append("defaulted from requested family or flowchart fallback")
    return family, {"scores": scores, "evidence": evidence}


def infer_figure_contract(
    *,
    figure_id: str,
    request: str,
    title: str,
    purpose: str,
    source_ids: list[str],
    content_requirements: list[str],
    requested_family: str | None,
    args: argparse.Namespace | None = None,
) -> dict[str, Any]:
    context_text = ""
    context_evidence: list[str] = []
    if args is not None:
        context_text, context_evidence = read_context_inputs(args)
    combined = compact_ws("\n".join([request, title, purpose, *content_requirements, context_text]))
    family, family_evidence = classify_diagram_family(combined, requested_family=requested_family)
    lowered = combined.lower()
    is_reduction = bool(re.search(r"\breduction\b|\bhardness\b|\bgadget\b|\breplac(?:e|ed|ing|ement)\b", lowered))

    required_objects: list[dict[str, str]] = []
    required_relations: list[dict[str, str]] = []
    forbidden_simplifications: list[dict[str, str]] = []
    approval_criteria: list[dict[str, str]] = []

    if family == "graph":
        required_objects.extend(
            [
                {"id": "graph-vertices", "description": "Visible graph vertices must be drawn as graph vertices, not as free text or process boxes."},
                {"id": "graph-edges", "description": "Visible graph edges must connect the corresponding graph vertices."},
            ]
        )
        required_relations.append(
            {"id": "incidence", "description": "Adjacency must be represented by line segments incident with graph vertices."}
        )
        forbidden_simplifications.append(
            {
                "id": "box-only-flowchart",
                "description": "A box-only flowchart or schematic without graph vertices and graph edges is not acceptable for a graph request.",
            }
        )
        if is_reduction:
            required_objects.extend(
                [
                    {
                        "id": "source-instance-part",
                        "description": "The original/source instance part relevant to the reduction must be visible.",
                    },
                    {
                        "id": "constructed-instance-part",
                        "description": "The corresponding constructed instance part must be visible.",
                    },
                    {
                        "id": "replacement-gadget",
                        "description": "The gadget replacing the chosen source edge or local structure must be visible as graph structure.",
                    },
                ]
            )
            required_relations.append(
                {
                    "id": "replacement-correspondence",
                    "description": "The figure must mark which source edge or local structure corresponds to which constructed gadget.",
                }
            )
            forbidden_simplifications.append(
                {
                    "id": "label-only-gadget",
                    "description": "A label naming a gadget is not enough unless the gadget vertices and edges are drawn.",
                }
            )
    elif family in {"flowchart", "dag"}:
        required_objects.append(
            {"id": f"{family}-nodes", "description": f"The {family} must have visible nodes for the main steps or states."}
        )
        required_relations.append(
            {"id": f"{family}-arcs", "description": f"The {family} must have directed connections showing the intended order or dependency."}
        )
    elif family == "tree":
        required_objects.append({"id": "tree-nodes", "description": "The tree must have a visible root and visible child nodes."})
        required_relations.append({"id": "tree-edges", "description": "Parent-child relations must be shown by tree edges."})
    elif family == "commutative":
        required_objects.append(
            {"id": "commutative-objects", "description": "The diagram must show the mathematical objects in a commutative grid."}
        )
        required_relations.append(
            {"id": "commutative-arrows", "description": "The arrows must connect the intended source and target objects."}
        )

    for index, value in enumerate(text_entries(getattr(args, "required_object", None) if args is not None else None)):
        required_objects.append(entry_dict(value, prefix="required-object", index=index))
    for index, value in enumerate(text_entries(getattr(args, "required_relation", None) if args is not None else None)):
        required_relations.append(entry_dict(value, prefix="required-relation", index=index))
    for index, value in enumerate(text_entries(getattr(args, "forbidden_simplification", None) if args is not None else None)):
        forbidden_simplifications.append(entry_dict(value, prefix="forbidden", index=index))

    notation_requirements = [
        {"id": slugify(label, f"notation{index + 1}"), "label": label, "description": "Preserve this mathematical label as TeX notation."}
        for index, label in enumerate(extract_math_labels(combined))
    ]
    for index, value in enumerate(text_entries(getattr(args, "notation_requirement", None) if args is not None else None)):
        notation_requirements.append(entry_dict(value, prefix="notation", index=index, field="label"))

    approval_criteria.extend(
        [
            {"id": "contract-family", "description": f"The rendered figure must use the {family} family unless the contract is revised."},
            {"id": "no-overlaps", "description": "Text, edges, and shapes must pass the visual overlap checker."},
            {"id": "semantic-match", "description": "The rendered structure must match the diagram spec and this contract."},
        ]
    )
    for index, value in enumerate(text_entries(getattr(args, "approval_criterion", None) if args is not None else None)):
        approval_criteria.append(entry_dict(value, prefix="approval", index=index))

    context_summary = " ".join(family_evidence["evidence"][:4])
    if context_evidence:
        context_summary = compact_ws(f"{context_summary}; context files: {', '.join(context_evidence)}")
    if not context_summary:
        context_summary = "No strong external context was supplied; contract was inferred from request/title/purpose."

    intent_kind = "graph_hardness_reduction" if family == "graph" and is_reduction else f"{family}_figure"
    contract = {
        "schema_version": FIGURE_CONTRACT_VERSION,
        "figure_id": figure_id,
        "request": request or purpose or title,
        "context_summary": context_summary,
        "source_ids": source_ids,
        "recommended_diagram_family": family,
        "intent": {
            "kind": intent_kind,
            "description": f"Generate a {family} figure that satisfies the required objects, relations, notation, and approval criteria.",
            "classification": family_evidence,
        },
        "required_objects": unique_contract_entries(required_objects),
        "required_relations": unique_contract_entries(required_relations),
        "forbidden_simplifications": unique_contract_entries(forbidden_simplifications),
        "notation_requirements": unique_contract_entries(notation_requirements, key="label"),
        "approval_criteria": unique_contract_entries(approval_criteria),
    }
    return normalize_figure_contract(contract)


def normalize_figure_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SystemExit("semantic_contract must be an object")
    ensure_keys(value, FIGURE_CONTRACT_REQUIRED, "figure contract")
    contract = dict(value)
    if contract.get("schema_version") != FIGURE_CONTRACT_VERSION:
        raise SystemExit(f"unsupported figure contract schema_version: {contract.get('schema_version')!r}")
    contract["figure_id"] = ensure_figure_id(str(contract.get("figure_id") or "F1"))
    family = str(contract.get("recommended_diagram_family", "")).strip()
    if family not in SUPPORTED_FAMILIES:
        raise SystemExit(
            "figure contract recommended_diagram_family must be one of: " + ", ".join(sorted(SUPPORTED_FAMILIES))
        )
    contract["recommended_diagram_family"] = family
    if not isinstance(contract.get("intent"), dict):
        raise SystemExit("figure contract intent must be an object")
    contract["source_ids"] = text_entries(contract.get("source_ids"))
    contract["required_objects"] = normalize_contract_entries(contract.get("required_objects"), prefix="required-object")
    contract["required_relations"] = normalize_contract_entries(contract.get("required_relations"), prefix="required-relation")
    contract["forbidden_simplifications"] = normalize_contract_entries(
        contract.get("forbidden_simplifications"), prefix="forbidden"
    )
    contract["notation_requirements"] = normalize_contract_entries(
        contract.get("notation_requirements"), prefix="notation", field="label"
    )
    contract["approval_criteria"] = normalize_contract_entries(contract.get("approval_criteria"), prefix="approval")
    return contract


def normalize_claim_entries(value: Any, *, source: str, prefix: str) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for index, item in enumerate(value or []):
        if isinstance(item, dict):
            text = compact_ws(str(item.get("text") or item.get("description") or ""))
            claim_id = str(item.get("id") or f"{prefix}-{index + 1}")
            claim_source = str(item.get("source") or source)
            if text:
                claims.append({"id": claim_id, "text": text, "source": claim_source})
        else:
            text = compact_ws(str(item))
            if text:
                claims.append({"id": f"{prefix}-{index + 1}", "text": text, "source": source})
    return claims


def normalize_design_mark(value: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SystemExit("figure design marks must be objects")
    mark = dict(value)
    mark["id"] = slugify(str(mark.get("id") or f"mark-{index + 1}"), f"mark{index + 1}")
    role = str(mark.get("role") or "").strip()
    if role not in DESIGN_MARK_ROLES:
        raise SystemExit(f"figure design mark {mark['id']!r} has unsupported role {role!r}")
    mark["role"] = role
    semantic_type = compact_ws(str(mark.get("semantic_type") or role))
    if not semantic_type:
        raise SystemExit(f"figure design mark {mark['id']!r} requires semantic_type")
    mark["semantic_type"] = semantic_type
    visual_encoding = compact_ws(str(mark.get("visual_encoding") or "plain"))
    if not visual_encoding:
        raise SystemExit(f"figure design mark {mark['id']!r} requires visual_encoding")
    mark["visual_encoding"] = visual_encoding
    mark["counts_as_graph_object"] = bool(mark.get("counts_as_graph_object", role == "graph_object"))
    for key in ("targets", "source_targets", "target_targets", "caption_claim_ids", "source_prose_claim_ids"):
        mark[key] = text_entries(mark.get(key))
    for key in ("label", "boundary_style", "fill_policy", "label_policy", "rationale"):
        if mark.get(key) is not None:
            mark[key] = compact_ws(str(mark[key]))
    return mark


def normalize_figure_design(value: Any, *, figure_id: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SystemExit("figure_design must be an object")
    ensure_keys(value, FIGURE_DESIGN_REQUIRED, "figure design")
    design = dict(value)
    if design.get("schema_version") != FIGURE_DESIGN_VERSION:
        raise SystemExit(f"unsupported figure design schema_version: {design.get('schema_version')!r}")
    design["figure_id"] = ensure_figure_id(str(design.get("figure_id") or figure_id or "F1"))
    if figure_id is not None and design["figure_id"] != figure_id:
        raise SystemExit(
            f"figure design figure_id {design['figure_id']!r} does not match requested figure_id {figure_id!r}"
        )
    design["design_intent"] = compact_ws(str(design.get("design_intent") or ""))
    design["audience_task"] = compact_ws(str(design.get("audience_task") or ""))
    if not design["design_intent"] or not design["audience_task"]:
        raise SystemExit("figure design requires nonempty design_intent and audience_task")
    design["caption_claims"] = normalize_claim_entries(
        design.get("caption_claims"), source="caption", prefix="caption-claim"
    )
    design["source_prose_claims"] = normalize_claim_entries(
        design.get("source_prose_claims"), source="source_prose", prefix="source-prose-claim"
    )
    design["marks"] = [
        normalize_design_mark(mark, index=index) for index, mark in enumerate(design.get("marks") or [])
    ]
    design["visual_encoding_policy"] = (
        dict(design.get("visual_encoding_policy")) if isinstance(design.get("visual_encoding_policy"), dict) else {}
    )
    design["rationale"] = text_entries(design.get("rationale"))
    design["approval_requirements"] = text_entries(design.get("approval_requirements"))
    return design


def contract_text_index(contract: dict[str, Any]) -> str:
    values = [
        str(contract.get("recommended_diagram_family", "")),
        str((contract.get("intent") or {}).get("kind", "")),
        str((contract.get("intent") or {}).get("description", "")),
    ]
    for key in ("required_objects", "required_relations", "forbidden_simplifications", "approval_criteria"):
        values.extend(contract_item_text(item) for item in contract.get(key, []))
    return compact_ws(" ".join(values)).lower()


def contract_requires_design(contract_value: Any) -> bool:
    if contract_value is None:
        return False
    contract = normalize_figure_contract(contract_value)
    index = contract_text_index(contract)
    return any(token in index for token in DESIGN_GATE_INTENT_TOKENS)


def claim_entry(claim_id: str, text: str, *, source: str) -> dict[str, str]:
    return {"id": claim_id, "text": compact_ws(text), "source": source}


def mark_entry(
    mark_id: str,
    role: str,
    semantic_type: str,
    visual_encoding: str,
    *,
    counts_as_graph_object: bool,
    targets: list[str] | None = None,
    source_targets: list[str] | None = None,
    target_targets: list[str] | None = None,
    label: str | None = None,
    caption_claim_ids: list[str] | None = None,
    source_prose_claim_ids: list[str] | None = None,
    boundary_style: str | None = None,
    fill_policy: str | None = None,
    label_policy: str | None = None,
    rationale: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": mark_id,
        "role": role,
        "semantic_type": semantic_type,
        "visual_encoding": visual_encoding,
        "counts_as_graph_object": counts_as_graph_object,
        "targets": targets or [],
        "source_targets": source_targets or [],
        "target_targets": target_targets or [],
        "caption_claim_ids": caption_claim_ids or [],
        "source_prose_claim_ids": source_prose_claim_ids or [],
    }
    for key, value in (
        ("label", label),
        ("boundary_style", boundary_style),
        ("fill_policy", fill_policy),
        ("label_policy", label_policy),
        ("rationale", rationale),
    ):
        if value:
            payload[key] = value
    return payload


def infer_figure_design(
    *,
    contract: dict[str, Any],
    caption: str = "",
    source_prose: str = "",
) -> dict[str, Any]:
    contract = normalize_figure_contract(contract)
    figure_id = contract["figure_id"]
    intent_kind = str((contract.get("intent") or {}).get("kind", ""))
    index = contract_text_index(contract)
    is_reduction = "reduction" in index or "gadget" in index or intent_kind == "graph_hardness_reduction"
    caption_claims: list[dict[str, str]] = []
    if caption.strip():
        caption_claims.append(claim_entry("caption-claim-1", caption, source="caption"))
    if is_reduction:
        caption_claims.append(
            claim_entry(
                "contract-replacement-claim",
                "The selected source edge or local structure is replaced by the constructed gadget.",
                source="contract",
            )
        )
    source_prose_claims: list[dict[str, str]] = []
    if source_prose.strip():
        source_prose_claims.append(claim_entry("source-prose-claim-1", source_prose, source="source_prose"))

    replacement_claim_ids = [claim["id"] for claim in caption_claims] if caption_claims else []
    marks = [
        mark_entry(
            "graph_vertices",
            "graph_object",
            "graph_vertices",
            "visible graph vertices",
            counts_as_graph_object=True,
            targets=["graph-vertices"],
            rationale="Vertices are mathematical graph structure.",
        ),
        mark_entry(
            "graph_edges",
            "graph_object",
            "graph_edges",
            "visible graph edges",
            counts_as_graph_object=True,
            targets=["graph-edges"],
            rationale="Edges are mathematical graph structure.",
        ),
    ]
    if is_reduction:
        marks.extend(
            [
                mark_entry(
                    "source_part",
                    "graph_object",
                    "source_instance_part",
                    "source-side graph structure",
                    counts_as_graph_object=True,
                    targets=["source-instance-part"],
                    caption_claim_ids=replacement_claim_ids,
                    rationale="The source side must be graph structure, not a process box.",
                ),
                mark_entry(
                    "constructed_part",
                    "graph_object",
                    "constructed_instance_part",
                    "constructed-side graph structure",
                    counts_as_graph_object=True,
                    targets=["constructed-instance-part"],
                    caption_claim_ids=replacement_claim_ids,
                    rationale="The constructed side must be graph structure, not a process box.",
                ),
                mark_entry(
                    "replacement_gadget_region",
                    "gadget_region",
                    "replacement_gadget",
                    "outline-only gadget boundary",
                    counts_as_graph_object=False,
                    targets=["replacement-gadget"],
                    caption_claim_ids=replacement_claim_ids,
                    boundary_style="dashed outline",
                    fill_policy="outline_only",
                    rationale="The region identifies the gadget without hiding vertices or edges.",
                ),
                mark_entry(
                    "replacement_correspondence",
                    "correspondence",
                    "source_to_gadget_replacement",
                    "direct correspondence arrow or matched boundary style",
                    counts_as_graph_object=False,
                    source_targets=["source-edge"],
                    target_targets=["replacement-gadget"],
                    caption_claim_ids=replacement_claim_ids,
                    rationale="The reader must see which source object is replaced by which gadget.",
                ),
            ]
        )
    for item in contract.get("notation_requirements", []):
        label = compact_ws(str(item.get("label") or item.get("description") or ""))
        if not label:
            continue
        marks.append(
            mark_entry(
                slugify(label, "notation_mark"),
                "annotation",
                "notation_label",
                "adjacent text label or callout",
                counts_as_graph_object=False,
                label=label,
                label_policy="attach to the referenced vertex, edge, or gadget without boxing it as graph structure",
                rationale="Notation labels are metadata unless the contract explicitly makes them graph vertices.",
            )
        )
    design = {
        "schema_version": FIGURE_DESIGN_VERSION,
        "figure_id": figure_id,
        "design_intent": str((contract.get("intent") or {}).get("description") or contract.get("request") or ""),
        "audience_task": (
            "Distinguish graph structure, annotations, gadget regions, and correspondence marks from the figure and caption."
        ),
        "caption_claims": caption_claims,
        "source_prose_claims": source_prose_claims,
        "marks": marks,
        "visual_encoding_policy": {
            "metadata_default": "unboxed adjacent label or callout",
            "overlapping_regions": "outline_only_with_distinct_line_styles",
            "color_dependency": "do_not_require_color_to_decode semantics",
        },
        "rationale": [
            "Graph objects and proof metadata must use different visual encodings.",
            "Regions and correspondences must explain replacement without hiding graph structure.",
        ],
        "approval_requirements": [
            "every visual mark has a declared role",
            "metadata is not counted as graph structure",
            "caption/prose claims are bound to marks when declared",
        ],
    }
    return normalize_figure_design(design, figure_id=figure_id)


def load_or_infer_design(
    args: argparse.Namespace | None,
    *,
    contract: dict[str, Any],
    figure_id: str,
    caption: str = "",
) -> dict[str, Any] | None:
    design_path = abs_path(getattr(args, "design", None)) if args is not None else None
    if design_path is not None:
        if not design_path.is_file():
            raise SystemExit(f"figure design is missing: {design_path}")
        return normalize_figure_design(load_json(design_path), figure_id=figure_id)
    if args is not None and getattr(args, "source_tex", None):
        source_prose, _evidence = read_context_inputs(args)
    else:
        source_prose = ""
    if contract_requires_design(contract):
        return infer_figure_design(contract=contract, caption=caption, source_prose=source_prose)
    return None


def ensure_brief_design(brief: dict[str, Any], args: argparse.Namespace | None = None) -> None:
    contract = normalize_figure_contract(brief.get("semantic_contract"))
    figure_id = ensure_figure_id(str(brief.get("figure_id") or contract["figure_id"]))
    if args is not None and getattr(args, "design", None):
        design = load_or_infer_design(args, contract=contract, figure_id=figure_id, caption=str(brief.get("caption") or ""))
    elif brief.get("semantic_design") is not None:
        design = normalize_figure_design(brief["semantic_design"], figure_id=figure_id)
    elif contract_requires_design(contract):
        design = infer_figure_design(contract=contract, caption=str(brief.get("caption") or ""))
    else:
        design = None
    if design is not None:
        brief["semantic_design"] = design


def load_or_infer_contract(
    args: argparse.Namespace,
    *,
    figure_id: str,
    request: str,
    title: str,
    purpose: str,
    source_ids: list[str],
    content_requirements: list[str],
    requested_family: str | None,
) -> dict[str, Any]:
    contract_path = abs_path(getattr(args, "contract", None))
    if contract_path is not None:
        if not contract_path.is_file():
            raise SystemExit(f"figure contract is missing: {contract_path}")
        contract = normalize_figure_contract(load_json(contract_path))
        if contract["figure_id"] != figure_id:
            raise SystemExit(
                f"figure contract figure_id {contract['figure_id']!r} does not match requested figure_id {figure_id!r}"
            )
        return contract
    return infer_figure_contract(
        figure_id=figure_id,
        request=request,
        title=title,
        purpose=purpose,
        source_ids=source_ids,
        content_requirements=content_requirements,
        requested_family=requested_family,
        args=args,
    )


def ensure_brief_contract(brief: dict[str, Any], args: argparse.Namespace | None = None) -> None:
    figure_id = ensure_figure_id(str(brief.get("figure_id") or "F1"))
    requested_family = str(brief.get("diagram_family", "") or "") or None
    if args is not None and getattr(args, "contract", None):
        contract = load_or_infer_contract(
            args,
            figure_id=figure_id,
            request=str(brief.get("purpose", "")),
            title=str(brief.get("title", "")),
            purpose=str(brief.get("purpose", "")),
            source_ids=text_entries(brief.get("source_ids")),
            content_requirements=text_entries(brief.get("content_requirements")),
            requested_family=requested_family,
        )
    elif brief.get("semantic_contract") is not None:
        contract = normalize_figure_contract(brief["semantic_contract"])
    else:
        namespace = args if args is not None else argparse.Namespace()
        contract = infer_figure_contract(
            figure_id=figure_id,
            request=str(brief.get("purpose", "")),
            title=str(brief.get("title", "")),
            purpose=str(brief.get("purpose", "")),
            source_ids=text_entries(brief.get("source_ids")),
            content_requirements=text_entries(brief.get("content_requirements")),
            requested_family=requested_family,
            args=namespace,
        )
    family = contract["recommended_diagram_family"]
    if requested_family and family != requested_family:
        raise SystemExit(
            f"figure contract recommends diagram_family={family!r}, but brief requests {requested_family!r}; revise the contract or the brief"
        )
    brief["semantic_contract"] = contract


def spec_text_index(spec: dict[str, Any]) -> str:
    values: list[str] = [
        str(spec.get("title", "")),
        str(spec.get("caption", "")),
        str(spec.get("diagram_family", "")),
    ]
    for node in spec.get("nodes", []):
        values.extend(str(node.get(key, "")) for key in ("id", "label", "style"))
    for edge in spec.get("edges", []):
        values.extend(str(edge.get(key, "")) for key in ("from", "to", "label", "style"))
    for group in spec.get("groups", []):
        values.extend(str(group.get(key, "")) for key in ("id", "label", "style"))
        values.extend(str(member) for member in group.get("members", []))
    for mark in spec.get("marks", []):
        values.extend(
            str(mark.get(key, ""))
            for key in ("id", "role", "semantic_type", "visual_encoding", "label", "rationale")
        )
        values.extend(str(item) for item in mark.get("targets", []))
        values.extend(str(item) for item in mark.get("source_targets", []))
        values.extend(str(item) for item in mark.get("target_targets", []))
    values.extend(str(item) for item in spec.get("layout_constraints", []))
    values.extend(str(item) for item in spec.get("validation_rules", []))
    return compact_ws(" ".join(values)).lower()


def contract_mismatch(code: str, message: str, **payload: Any) -> dict[str, Any]:
    mismatch = {"code": code, "message": message}
    mismatch.update(payload)
    return mismatch


def validate_contract_against_spec(contract_value: Any, spec: dict[str, Any]) -> list[dict[str, Any]]:
    if contract_value is None:
        return [
            contract_mismatch(
                "CONTRACT_MISSING",
                "diagram spec must carry a semantic_contract before semantic approval",
            )
        ]
    contract = normalize_figure_contract(contract_value)
    mismatches: list[dict[str, Any]] = []
    family = str(spec.get("diagram_family", ""))
    expected_family = contract["recommended_diagram_family"]
    if family != expected_family:
        mismatches.append(
            contract_mismatch(
                "CONTRACT_FAMILY_MISMATCH",
                f"contract requires family {expected_family!r}, but spec uses {family!r}",
                expected_family=expected_family,
                actual_family=family,
            )
        )

    nodes = spec.get("nodes", [])
    edges = spec.get("edges", [])
    index = spec_text_index(spec)
    for item in contract.get("required_objects", []):
        text = contract_item_text(item).lower()
        if "vertex" in text and not nodes:
            mismatches.append(contract_mismatch("CONTRACT_GRAPH_VERTEX_MISSING", "contract requires graph vertices"))
        if "edge" in text and not edges:
            mismatches.append(contract_mismatch("CONTRACT_GRAPH_EDGE_MISSING", "contract requires graph edges"))
        if any(token in text for token in ("source instance", "original/source", "original instance")) and not any(
            token in index for token in ("source", "original", "edge e", "source-edge")
        ):
            mismatches.append(
                contract_mismatch("CONTRACT_SOURCE_PART_MISSING", "contract requires the source/original instance part")
            )
        if "constructed instance" in text and "constructed" not in index:
            mismatches.append(
                contract_mismatch(
                    "CONTRACT_CONSTRUCTED_PART_MISSING",
                    "contract requires the constructed instance part",
                )
            )
        if "gadget" in text and "gadget" not in index:
            mismatches.append(contract_mismatch("CONTRACT_GADGET_MISSING", "contract requires a visible gadget"))

    for item in contract.get("required_relations", []):
        text = contract_item_text(item).lower()
        if "replacement" in text and not any(token in index for token in ("replacement", "replaces", "gadget")):
            mismatches.append(
                contract_mismatch(
                    "CONTRACT_REPLACEMENT_RELATION_MISSING",
                    "contract requires the source-to-gadget replacement relation",
                )
            )
        if any(token in text for token in ("adjacency", "incident", "edge")) and not edges:
            mismatches.append(contract_mismatch("CONTRACT_RELATION_EDGE_MISSING", "contract relation requires edges"))

    for item in contract.get("forbidden_simplifications", []):
        text = contract_item_text(item).lower()
        if "flowchart" in text and family in {"flowchart", "dag"} and expected_family == "graph":
            mismatches.append(
                contract_mismatch(
                    "CONTRACT_FORBIDDEN_SIMPLIFICATION",
                    "contract forbids satisfying a graph request by a flowchart/DAG",
                )
            )
        if "label" in text and "gadget" in text and "gadget" in index and not edges:
            mismatches.append(
                contract_mismatch(
                    "CONTRACT_FORBIDDEN_LABEL_ONLY_GADGET",
                    "contract forbids a label-only gadget without graph edges",
                )
            )

    raw_spec_text = " ".join(
        [
            str(spec.get("title", "")),
            str(spec.get("caption", "")),
            *[str(node.get("label", "")) for node in nodes],
            *[str(edge.get("label", "")) for edge in edges],
        ]
    )
    for item in contract.get("notation_requirements", []):
        label = compact_ws(str(item.get("label") or item.get("description") or ""))
        if label and label not in raw_spec_text:
            mismatches.append(
                contract_mismatch(
                    "CONTRACT_NOTATION_MISSING",
                    f"contract requires notation {label!r} to be preserved in labels",
                    label=label,
                )
            )
    return mismatches


def validate_brief(brief: dict[str, Any]) -> None:
    ensure_keys(brief, BRIEF_REQUIRED, "figure-brief")
    family = brief["diagram_family"]
    if family not in BACKEND_BY_FAMILY:
        raise SystemExit(
            f"unsupported diagram_family '{family}' in phase 1; supported: {', '.join(sorted(SUPPORTED_FAMILIES))}"
        )
    if not isinstance(brief["source_ids"], list):
        raise SystemExit("figure-brief source_ids must be a list")
    if "graph_mode" in brief and family == "graph":
        lowered = str(brief["graph_mode"]).strip().lower()
        if lowered not in GRAPH_MODE_VALUES:
            raise SystemExit(f"invalid graph_mode {brief['graph_mode']!r}; allowed: {', '.join(GRAPH_MODE_VALUES)}")
    if "symmetry_contract" in brief:
        brief["symmetry_contract"] = normalize_symmetry_contract(brief["symmetry_contract"])
    ensure_brief_contract(brief)
    ensure_brief_design(brief)


def validate_spec(spec: dict[str, Any]) -> None:
    ensure_keys(spec, SPEC_REQUIRED, "diagram spec")
    family = spec["diagram_family"]
    backend = spec["tikz_backend"]
    if family not in BACKEND_BY_FAMILY:
        raise SystemExit(
            f"unsupported diagram_family '{family}' in phase 1; supported: {', '.join(sorted(SUPPORTED_FAMILIES))}"
        )
    expected = BACKEND_BY_FAMILY[family]
    if backend != expected:
        raise SystemExit(f"phase 1 expects backend '{expected}' for family '{family}', got '{backend}'")
    spec["symmetry_contract"] = normalize_symmetry_contract(
        spec.get("symmetry_contract"),
        default_reason=None,
    )
    if spec.get("semantic_contract") is not None:
        spec["semantic_contract"] = normalize_figure_contract(spec["semantic_contract"])
    if spec.get("semantic_design") is not None:
        spec["semantic_design"] = normalize_figure_design(
            spec["semantic_design"], figure_id=str(spec["semantic_design"].get("figure_id") or "F1")
        )
    if spec.get("marks") is not None:
        spec["marks"] = [normalize_design_mark(mark, index=index) for index, mark in enumerate(spec.get("marks") or [])]
    contract_mismatches = validate_contract_against_spec(spec.get("semantic_contract"), spec)
    if contract_mismatches:
        codes = ", ".join(sorted({str(item["code"]) for item in contract_mismatches}))
        raise SystemExit(f"diagram spec violates semantic_contract: {codes}")


def slugify(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"n-{cleaned}"
    return cleaned.replace("-", "_")


def tex_escape(value: str) -> str:
    if any(token in value for token in ("\\", "$", "{", "}")):
        return value
    replacements = {
        "&": r"\&",
        "%": r"\%",
        "#": r"\#",
        "_": r"\_",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


def strip_outer_math_delimiters(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text.startswith("$") and text.endswith("$"):
        return text[1:-1]
    return text


def make_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def normalize_run_id(value: str | None) -> str:
    if not value:
        return make_run_id()
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return cleaned or make_run_id()


def ensure_figure_id(value: str | None) -> str:
    figure_id = value or "F1"
    if not re.fullmatch(r"F[1-9][0-9]*", figure_id):
        raise SystemExit("figure_id must match F<number>, for example F1")
    return figure_id


def infer_title_from_request(request: str, figure_id: str) -> str:
    cleaned = re.sub(r"\s+", " ", request).strip().rstrip(".")
    if not cleaned:
        return f"{figure_id} figure"
    if len(cleaned) > 80:
        cleaned = cleaned[:77].rstrip() + "..."
    return cleaned[:1].upper() + cleaned[1:]


def default_direct_run_root(run_id: str) -> Path:
    home = Path.home()
    if PLATFORM_NAME == "codex":
        return home / ".codex" / "runs" / "tikz-draw" / run_id
    if os.environ.get("AAS_RUNS_ROOT"):
        runs_root = Path(os.environ["AAS_RUNS_ROOT"]).expanduser()
    elif os.environ.get("AAS_DATA_ROOT"):
        runs_root = Path(os.environ["AAS_DATA_ROOT"]).expanduser() / "runs"
    elif IS_WINDOWS and os.environ.get("LOCALAPPDATA"):
        runs_root = Path(os.environ["LOCALAPPDATA"]).expanduser() / "ai-agents-skills" / "runs"
    elif os.environ.get("XDG_DATA_HOME"):
        runs_root = Path(os.environ["XDG_DATA_HOME"]).expanduser() / "ai-agents-skills" / "runs"
    else:
        runs_root = home / ".local" / "share" / "ai-agents-skills" / "runs"
    return runs_root / "tikz-draw" / run_id


def resolve_output_dir(
    args: argparse.Namespace,
    *,
    run_id: str,
    brief_output_dir: str | None = None,
    fallback_parent: Path | None = None,
) -> Path:
    if getattr(args, "out_dir", None):
        out_dir = abs_path(args.out_dir)
        assert out_dir is not None
        return out_dir
    if getattr(args, "research_root", None):
        research_root = abs_path(args.research_root)
        assert research_root is not None
        return research_root / "figures"
    if brief_output_dir:
        out_dir = abs_path(brief_output_dir)
        assert out_dir is not None
        return out_dir
    if fallback_parent is not None:
        return fallback_parent
    return default_direct_run_root(run_id)


def bootstrap_brief(
    args: argparse.Namespace,
    *,
    fallback_parent: Path | None = None,
) -> tuple[dict[str, Any], Path, str]:
    request = (getattr(args, "request", None) or "").strip()
    title = (getattr(args, "title", None) or "").strip()
    purpose = (getattr(args, "purpose", None) or "").strip()

    run_id = normalize_run_id(getattr(args, "run_id", None))
    figure_id = ensure_figure_id(getattr(args, "figure_id", None))
    out_dir = resolve_output_dir(args, run_id=run_id, fallback_parent=fallback_parent)
    source_ids = list(getattr(args, "source_id", None) or [])
    content_requirements = list(getattr(args, "content_requirement", None) or ([request] if request else []))
    contract = load_or_infer_contract(
        args,
        figure_id=figure_id,
        request=request,
        title=title,
        purpose=purpose,
        source_ids=source_ids,
        content_requirements=content_requirements,
        requested_family=getattr(args, "diagram_family", None),
    )
    contract_request = compact_ws(str(contract.get("request", "")))
    if not (request or title or purpose or contract_request):
        raise SystemExit("render/spec without --brief requires --request, --title, --purpose, or --contract")
    if getattr(args, "diagram_family", None) and args.diagram_family != contract["recommended_diagram_family"]:
        raise SystemExit(
            f"requested --diagram-family {args.diagram_family!r} conflicts with semantic contract "
            f"recommendation {contract['recommended_diagram_family']!r}; revise the request or pass an updated contract"
        )
    final_title = title or infer_title_from_request(request or contract_request, figure_id)
    final_purpose = purpose or request or contract_request or f"Illustrate {final_title}."
    brief = {
        "figure_id": figure_id,
        "title": final_title,
        "purpose": final_purpose,
        "source_ids": source_ids,
        "diagram_family": contract["recommended_diagram_family"],
        "backend_hint": getattr(args, "backend_hint", None),
        "content_requirements": content_requirements,
        "layout_constraints": list(
            getattr(args, "layout_constraint", None) or ["Fit within text width using adjustbox."]
        ),
        "caption": getattr(args, "caption", None),
        "semantic_contract": contract,
        "output_dir": str(out_dir),
    }
    symmetry_status = getattr(args, "symmetry", None)
    symmetry_justification = (getattr(args, "symmetry_justification", None) or "").strip()
    if symmetry_status:
        brief["symmetry_contract"] = {
            "status": symmetry_status,
            "justification": symmetry_justification
            or ("No mirror or alignment symmetry is required for this generated figure."),
        }
        if symmetry_status == "required":
            brief["symmetry_contract"]["mode"] = getattr(args, "symmetry_mode", None) or "mirror_vertical_axis"
    else:
        brief["symmetry_contract"] = default_symmetry_contract(
            "No mirror or alignment symmetry was requested for this generated figure."
        )
    if brief["diagram_family"] == "graph":
        brief["graph_request"] = request or final_purpose
        if getattr(args, "graph_mode", None):
            brief["graph_mode"] = args.graph_mode
        if getattr(args, "graph_constructor", None):
            brief["graph_constructor"] = args.graph_constructor
        graph_params = list(getattr(args, "graph_param", None) or [])
        if graph_params:
            brief["graph_params"] = graph_params
        if getattr(args, "graph_layout", None):
            brief["graph_layout"] = args.graph_layout
        if getattr(args, "show_labels", None) is not None:
            brief["show_labels"] = parse_bool_text(args.show_labels)
    return brief, out_dir, run_id


def infer_flow_style(index: int, label: str) -> str:
    lowered = label.lower()
    if index == 0:
        return "io"
    if "?" in label or lowered.startswith("check ") or lowered.startswith("is "):
        return "decision"
    return "box"


def extract_flow_labels(requirements: list[str]) -> list[str]:
    joined = " ".join(requirements)
    lowered = joined.lower()
    canonical = [
        ("input", "Input"),
        ("parse", "Parse"),
        ("validate", "Validate?"),
        ("emit", "Emit"),
        ("repair", "Repair"),
    ]
    labels = [label for token, label in canonical if re.search(rf"\b{token}\b", lowered)]
    if labels:
        return labels

    fragments = []
    for item in requirements:
        pieces = re.split(r",| and | then ", item, flags=re.IGNORECASE)
        for piece in pieces:
            cleaned = re.sub(r"^(include|show|use|prefer)\s+", "", piece.strip(), flags=re.IGNORECASE)
            cleaned = cleaned.rstrip(".")
            if cleaned:
                fragments.append(cleaned[:1].upper() + cleaned[1:])
    return fragments


def normalize_graph_positions(raw_positions: dict[str, list[float]]) -> dict[str, tuple[float, float]]:
    xs = [float(pair[0]) for pair in raw_positions.values()]
    ys = [float(pair[1]) for pair in raw_positions.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    span = max(max_x - min_x, max_y - min_y, 1.0)
    half_extent = 2.4
    scale = (2.0 * half_extent) / span
    return {
        label: ((float(coords[0]) - center_x) * scale, (float(coords[1]) - center_y) * scale)
        for label, coords in raw_positions.items()
    }


def finalize_spec_from_brief(spec: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any]:
    design = brief.get("semantic_design")
    if design is not None:
        design = normalize_figure_design(design, figure_id=str(brief.get("figure_id") or design.get("figure_id") or "F1"))
        spec["semantic_design"] = design
        spec["marks"] = design["marks"]
    else:
        spec.setdefault("marks", [])
    return spec


def spec_from_brief(brief: dict[str, Any]) -> dict[str, Any]:
    family = brief["diagram_family"]
    backend = brief.get("backend_hint") or BACKEND_BY_FAMILY[family]
    requirements = brief.get("content_requirements") or []
    title = brief["title"]
    caption = brief.get("caption", "")
    symmetry_contract = normalize_symmetry_contract(
        brief.get("symmetry_contract"),
        default_reason="No mirror or alignment symmetry was requested for this generated figure.",
    )
    validation_rules = [
        "document-facing output must use the adjustbox environment with max width textwidth",
        "prefer structural placement over absolute coordinates",
        "avoid bare scale as primary width-fit control",
        "strict approval requires the declared symmetry contract to be satisfied",
    ]

    if family in {"flowchart", "dag"}:
        labels = extract_flow_labels(requirements)[:5] or ["Input", "Parse", "Validate?", "Emit"]
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        for index, label in enumerate(labels):
            node_id = slugify(label, f"node{index + 1}")
            node: dict[str, Any] = {
                "id": node_id,
                "label": label,
                "style": infer_flow_style(index, label),
                "width": "18mm" if "?" in label else "28mm",
            }
            if index > 0:
                node["placement"] = {
                    "kind": "relative",
                    "target": nodes[index - 1]["id"],
                    "relation": "right",
                }
            nodes.append(node)
            if index > 0:
                edges.append({"from": nodes[index - 1]["id"], "to": node_id, "style": "edge"})
        node_ids = {node["label"]: node["id"] for node in nodes}
        if "Repair" in node_ids and "Parse" in node_ids and re.search(
            r"\b(return\w*|loop|retry)\b", " ".join(requirements), re.IGNORECASE
        ):
            edges.append(
                {
                    "from": node_ids["Repair"],
                    "to": node_ids["Parse"],
                    "label": "retry",
                    "label_pos": "below",
                    "bend": "below_loop",
                    "style": "edge",
                }
            )
        groups = []
        if len(nodes) >= 3:
            groups.append(
                {
                    "id": "pipeline",
                    "label": "Pipeline",
                    "members": [node["id"] for node in nodes[1:]],
                    "style": "groupbox",
                }
            )
        return finalize_spec_from_brief({
            "diagram_family": family,
            "tikz_backend": backend,
            "title": title,
            "caption": caption,
            "global_styles": {},
            "nodes": nodes,
            "edges": edges,
            "groups": groups,
            "layout_constraints": brief["layout_constraints"],
            "validation_rules": validation_rules,
            "symmetry_contract": symmetry_contract,
            "semantic_contract": brief.get("semantic_contract"),
        }, brief)

    if family == "tree":
        child_labels = requirements[:4] or ["Assumption A", "Assumption B", "Conclusion"]
        root_id = slugify(title, "root")
        nodes = [{"id": root_id, "label": title, "style": "box"}]
        edges = []
        for index, label in enumerate(child_labels):
            child_id = slugify(label, f"child{index + 1}")
            nodes.append({"id": child_id, "label": label, "style": "box"})
            edges.append({"from": root_id, "to": child_id})
        return finalize_spec_from_brief({
            "diagram_family": family,
            "tikz_backend": backend,
            "title": title,
            "caption": caption,
            "global_styles": {},
            "nodes": nodes,
            "edges": edges,
            "groups": [],
            "layout_constraints": brief["layout_constraints"],
            "validation_rules": validation_rules,
            "symmetry_contract": symmetry_contract,
            "semantic_contract": brief.get("semantic_contract"),
        }, brief)

    if family == "commutative":
        node_labels = requirements[:4] or ["A", "B", "C", "D"]
        while len(node_labels) < 4:
            node_labels.append(chr(ord("A") + len(node_labels)))
        nodes = [
            {"id": "a", "label": node_labels[0]},
            {"id": "b", "label": node_labels[1]},
            {"id": "c", "label": node_labels[2]},
            {"id": "d", "label": node_labels[3]},
        ]
        return finalize_spec_from_brief({
            "diagram_family": family,
            "tikz_backend": backend,
            "title": title,
            "caption": caption,
            "global_styles": {},
            "nodes": nodes,
            "edges": [
                {"from": "a", "to": "b", "label": "f"},
                {"from": "a", "to": "c", "label": "g"},
                {"from": "b", "to": "d", "label": "h"},
                {"from": "c", "to": "d", "label": "k"},
            ],
            "groups": [],
            "layout_constraints": brief["layout_constraints"],
            "validation_rules": validation_rules,
            "symmetry_contract": symmetry_contract,
            "semantic_contract": brief.get("semantic_contract"),
        }, brief)

    if family == "graph":
        graph_query = extract_graph_query(brief)
        graph_payload = run_sage_graph_query(graph_query)
        normalized_positions = normalize_graph_positions(graph_payload["positions"])
        graph_routing = {
            "mode_requested": graph_payload["graph_mode_requested"],
            "route_status": graph_payload["graph_route_status"],
            "route_reason": graph_payload["graph_route_reason"],
            "backend_used": graph_payload["graph_backend_used"],
        }
        nodes = []
        label_to_node_id: dict[str, str] = {}
        for index, vertex_label in enumerate(graph_payload["vertices"]):
            node_id = f"v{index}"
            label_to_node_id[vertex_label] = node_id
            pos_x, pos_y = normalized_positions[vertex_label]
            nodes.append(
                {
                    "id": node_id,
                    "label": vertex_label,
                    "style": "graphnode",
                    "placement": {
                        "kind": "absolute",
                        "x": f"{pos_x:.4f}",
                        "y": f"{pos_y:.4f}",
                    },
                    "metadata": {
                        "graph_position": [pos_x, pos_y],
                        "graph_vertex": vertex_label,
                        "show_label": bool(graph_payload.get("show_labels", False)),
                        "graph_order": graph_payload["order"],
                        "graph_size": graph_payload["size"],
                        "graph_constructor": graph_payload["constructor"],
                        "graph_layout": graph_payload["layout"],
                        "graph_route_status": graph_payload["graph_route_status"],
                        "graph_backend_used": graph_payload["graph_backend_used"],
                    },
                }
            )
        edges = [
            {
                "from": label_to_node_id[str(source)],
                "to": label_to_node_id[str(target)],
                "style": "graphedge",
                "metadata": {"undirected": True},
            }
            for source, target in graph_payload["edges"]
        ]
        validation_rules.append("graph family uses a Sage-backed graph constructor and layout backend")
        validation_rules.append(
            f"graph routing currently selected {graph_payload['graph_route_status']} with backend {graph_payload['graph_backend_used']}"
        )
        return finalize_spec_from_brief({
            "diagram_family": family,
            "tikz_backend": backend,
            "title": title,
            "caption": caption,
            "global_styles": {
                "graph_show_labels": "true" if graph_payload.get("show_labels") else "false",
                "graph_constructor": graph_payload["constructor"],
                "graph_layout": graph_payload["layout"],
                "graph_route_status": graph_payload["graph_route_status"],
                "graph_backend_used": graph_payload["graph_backend_used"],
            },
            "nodes": nodes,
            "edges": edges,
            "groups": [],
            "layout_constraints": brief["layout_constraints"],
            "validation_rules": validation_rules,
            "symmetry_contract": symmetry_contract,
            "semantic_contract": brief.get("semantic_contract"),
            "graph_routing": graph_routing,
        }, brief)

    raise SystemExit(f"unsupported diagram_family '{family}'")


def load_style_assets() -> tuple[str, str]:
    return (
        read_text(STYLES_DIR / "tikz_palette.tex").rstrip(),
        read_text(STYLES_DIR / "tikz_styles.tex").rstrip(),
    )


def node_options(node: dict[str, Any]) -> list[str]:
    options = [node.get("style", "box")]
    if width := node.get("width"):
        options.append(f"minimum width={width}")
    if height := node.get("height"):
        options.append(f"minimum height={height}")
    return options


def placement_fragment(node: dict[str, Any]) -> str:
    placement = node.get("placement")
    if not placement or placement.get("kind") != "relative":
        return ""
    relation = placement.get("relation", "right")
    target = placement.get("target", "")
    if target:
        return f", {relation}=of {target}"
    return ""


def comment_block_for_spec(spec: dict[str, Any]) -> list[str]:
    lines = [f"% Diagram: {spec.get('title', 'Untitled diagram')}"]
    family = spec.get("diagram_family")
    if family in {"flowchart", "dag"}:
        lines.append("% Coordinates:")
        for node in spec.get("nodes", []):
            placement = node.get("placement")
            if placement and placement.get("kind") == "relative":
                lines.append(
                    f"%   {node['id']} {placement.get('relation', 'relative')} of {placement.get('target', 'unknown')} -- {node.get('label', node['id'])}"
                )
            else:
                lines.append(f"%   {node['id']} anchor node -- {node.get('label', node['id'])}")
    elif family == "tree":
        lines.append("% Coordinates: structural tree layout from the root downward.")
    elif family == "commutative":
        lines.append("% Coordinates: 2x2 categorical grid in tikz-cd order a,b / c,d.")
    elif family == "graph":
        lines.append("% Coordinates:")
        for node in spec.get("nodes", []):
            placement = node.get("placement") or {}
            lines.append(
                f"%   {node['id']} at ({placement.get('x', '0')}, {placement.get('y', '0')}) -- {node.get('label', node['id'])}"
            )
    return lines


def render_flowchart(spec: dict[str, Any]) -> tuple[str, list[str], list[str], str]:
    palette, styles = load_style_assets()
    lines = [*comment_block_for_spec(spec), r"\begin{tikzpicture}[node distance=10mm and 14mm]"]
    nodes = spec["nodes"]
    for index, node in enumerate(nodes):
        options = ", ".join(node_options(node))
        placement = placement_fragment(node)
        statement = (
            rf"\node[{options}{placement}] ({node['id']}) {{{tex_escape(node['label'])}}};"
            if index > 0
            else rf"\node[{options}] ({node['id']}) {{{tex_escape(node['label'])}}};"
        )
        lines.append(statement)
    for edge in spec["edges"]:
        label = edge.get("label")
        if edge.get("bend") == "below_loop":
            label_fragment = f" node[below, note] {{{tex_escape(label)}}}" if label else ""
            lines.append(
                rf"\draw[edge] ({edge['from']}.south) to[out=-90,in=-90,looseness=1.15]{label_fragment} ({edge['to']}.south);"
            )
        elif label:
            label_pos = edge.get("label_pos", "above")
            lines.append(
                rf"\draw[edge] ({edge['from']}) -- node[{label_pos}, note] {{{tex_escape(label)}}} ({edge['to']});"
            )
        else:
            lines.append(rf"\draw[edge] ({edge['from']}) -- ({edge['to']});")
    for group in spec.get("groups", []):
        members = "".join(f"({member})" for member in group["members"])
        label = group.get("label")
        label_fragment = f", label=above:{tex_escape(label)}" if label else ""
        lines.append(rf"\node[groupbox, fit={members}{label_fragment}] {{}};")
    lines.append(r"\end{tikzpicture}")
    body = "\n".join(lines)
    packages = [r"\usepackage{adjustbox}", r"\usepackage{tikz}"]
    libraries = [r"\usetikzlibrary{positioning,fit,arrows.meta,shapes.geometric}"]
    extra_defs = "\n".join([palette, styles]).strip()
    return body, packages, libraries, extra_defs


def render_tree(spec: dict[str, Any]) -> tuple[str, list[str], list[str], str]:
    palette, _ = load_style_assets()
    node_labels = {node["id"]: tex_escape(node["label"]) for node in spec["nodes"]}
    children: dict[str, list[str]] = {}
    roots = {node["id"] for node in spec["nodes"]}
    for edge in spec["edges"]:
        children.setdefault(edge["from"], []).append(edge["to"])
        roots.discard(edge["to"])
    root = sorted(roots)[0]

    def build(node_id: str) -> str:
        child_chunks = "".join(f"\n  {build(child_id)}" for child_id in children.get(node_id, []))
        if child_chunks:
            return f"[{node_labels[node_id]}{child_chunks}\n]"
        return f"[{node_labels[node_id]}]"

    body = "\n".join(
        [
            *comment_block_for_spec(spec),
            r"\begin{forest}",
            r"for tree={",
            r"  draw=tikzdrawPrimary,",
            r"  rounded corners=2pt,",
            r"  align=center,",
            r"  edge={->, very thick, draw=tikzdrawNeutral},",
            r"  minimum height=8mm,",
            r"  inner sep=2mm,",
            r"  s sep=10mm,",
            r"  l sep=12mm",
            r"}",
            build(root),
            r"\end{forest}",
        ]
    )
    packages = [r"\usepackage{adjustbox}", r"\usepackage[edges]{forest}"]
    libraries: list[str] = []
    extra_defs = palette
    return body, packages, libraries, extra_defs


def render_commutative(spec: dict[str, Any]) -> tuple[str, list[str], list[str], str]:
    node_labels = {node["id"]: strip_outer_math_delimiters(tex_escape(node["label"])) for node in spec["nodes"]}
    cell_positions = {
        "a": (1, 1),
        "b": (1, 2),
        "c": (2, 1),
        "d": (2, 2),
    }
    swap_pairs = {
        frozenset({"a", "c"}),
        frozenset({"c", "d"}),
    }

    def arrow_command(edge: dict[str, Any]) -> str:
        source = edge["from"]
        target = edge["to"]
        if source not in cell_positions or target not in cell_positions:
            raise SystemExit(f"commutative renderer expects node ids in {{a,b,c,d}}, got {source!r} -> {target!r}")
        from_row, from_col = cell_positions[source]
        to_row, to_col = cell_positions[target]
        if abs(from_row - to_row) + abs(from_col - to_col) != 1:
            raise SystemExit(
                f"commutative renderer currently supports only adjacent square edges, got {source!r} -> {target!r}"
            )
        label = strip_outer_math_delimiters(tex_escape(edge.get("label", ""))) if edge.get("label") is not None else ""
        label_fragment = ""
        if label:
            if frozenset({source, target}) in swap_pairs:
                label_fragment = f', "{label}"\''
            else:
                label_fragment = f', "{label}"'
        return rf"\arrow[from={from_row}-{from_col}, to={to_row}-{to_col}{label_fragment}]"

    body = "\n".join(
        [
            *comment_block_for_spec(spec),
            r"\begin{tikzcd}[column sep=large, row sep=large]",
            f"{node_labels.get('a', 'A')} & {node_labels.get('b', 'B')} \\\\",
            f"{node_labels.get('c', 'C')} & {node_labels.get('d', 'D')}",
            *[arrow_command(edge) for edge in spec["edges"]],
            r"\end{tikzcd}",
        ]
    )
    packages = [r"\usepackage{adjustbox}", r"\usepackage{tikz-cd}"]
    libraries: list[str] = []
    extra_defs = ""
    return body, packages, libraries, extra_defs


def render_graph(spec: dict[str, Any]) -> tuple[str, list[str], list[str], str]:
    palette, styles = load_style_assets()
    lines = [*comment_block_for_spec(spec), r"\begin{tikzpicture}[x=12mm, y=12mm]"]
    coord_ids: dict[str, str] = {}
    for node in spec["nodes"]:
        placement = node.get("placement") or {}
        x = placement.get("x", "0")
        y = placement.get("y", "0")
        coord_id = f"{node['id']}-coord"
        coord_ids[str(node["id"])] = coord_id
        lines.append(rf"\coordinate ({coord_id}) at ({x},{y});")
    for edge in spec["edges"]:
        lines.append(rf"\draw[graphedge] ({coord_ids[edge['from']]}) -- ({coord_ids[edge['to']]});")
    for node in spec["nodes"]:
        show_label = bool((node.get("metadata") or {}).get("show_label", False))
        body = tex_escape(node["label"]) if show_label else ""
        lines.append(rf"\node[graphnode] ({node['id']}) at ({coord_ids[node['id']]}) {{{body}}};")
    lines.append(r"\end{tikzpicture}")
    body = "\n".join(lines)
    packages = [r"\usepackage{adjustbox}", r"\usepackage{tikz}"]
    libraries = [r"\usetikzlibrary{arrows.meta}"]
    extra_defs = "\n".join([palette, styles]).strip()
    return body, packages, libraries, extra_defs


def wrap_in_adjustbox_environment(body: str) -> list[str]:
    return [
        r"\begin{adjustbox}{max width=\textwidth}",
        body,
        r"\end{adjustbox}",
    ]


def build_outputs(spec: dict[str, Any], figure_id: str, caption: str) -> tuple[str, str]:
    family = spec["diagram_family"]
    if family in {"flowchart", "dag"}:
        body, packages, libraries, extra_defs = render_flowchart(spec)
    elif family == "tree":
        body, packages, libraries, extra_defs = render_tree(spec)
    elif family == "commutative":
        body, packages, libraries, extra_defs = render_commutative(spec)
    elif family == "graph":
        body, packages, libraries, extra_defs = render_graph(spec)
    else:
        raise SystemExit(f"unsupported diagram_family '{family}'")

    border_pt = "6pt" if family in {"commutative", "graph"} else "4pt"
    preamble = [rf"\documentclass[border={border_pt}]{{standalone}}", *packages, *libraries]
    if extra_defs:
        preamble.append(extra_defs)
    standalone = "\n".join(
        [
            *preamble,
            "",
            r"\begin{document}",
            *wrap_in_adjustbox_environment(body),
            r"\end{document}",
            "",
        ]
    )

    snippet_lines = [
        "% Generated by tikz-draw.",
        "% Required in the parent preamble:",
    ]
    for package in packages:
        if package != r"\usepackage{adjustbox}":
            snippet_lines.append(f"% {package}")
    for library in libraries:
        snippet_lines.append(f"% {library}")
    snippet_lines.extend(
        [
            "% \\usepackage{adjustbox}",
            extra_defs if extra_defs else "",
            r"\begin{figure}[t]",
            r"\centering",
            *wrap_in_adjustbox_environment(body),
        ]
    )
    if caption:
        snippet_lines.append(rf"\caption{{{tex_escape(caption)}}}")
    snippet_lines.append(rf"\label{{fig:{figure_id}}}")
    snippet_lines.append(r"\end{figure}")
    snippet = "\n".join(line for line in snippet_lines if line != "") + "\n"
    return standalone, snippet


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_metadata(path: Path) -> tuple[str, str]:
    stat = path.stat()
    return file_sha256(path), str(stat.st_mtime_ns)


def artifact_hash_payload(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"path": None, "exists": False}
    payload: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if path.is_file():
        stat = path.stat()
        payload["sha256"] = file_sha256(path)
        payload["mtime_ns"] = str(stat.st_mtime_ns)
        payload["size"] = stat.st_size
    return payload


def refresh_manifest_artifact_hashes(manifest: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "figure_brief",
        "figure_design",
        "standalone_tex",
        "figure_tex",
        "diagram_spec",
        "pdf",
        "render_semantics",
        "semantic_review",
    )
    manifest["approval_contract_version"] = STRICT_APPROVAL_VERSION
    manifest["artifact_hashes"] = {
        field: artifact_hash_payload(abs_path(manifest.get(field))) for field in fields
    }
    return manifest


def make_rule_hit(rule_id: str, message: str, *, severity: str = "FAIL") -> dict[str, str]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "message": message,
    }


def detect_static_rule_hits(text: str) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []

    if r"\begin{adjustbox}{max width=\textwidth}" not in text or r"\end{adjustbox}" not in text:
        hits.append(make_rule_hit("P0_ADJUSTBOX_ENV", STATIC_RULES["P0_ADJUSTBOX_ENV"]))

    if r"\documentclass[tikz" in text:
        hits.append(make_rule_hit("P0_STANDALONE_CLASS", STATIC_RULES["P0_STANDALONE_CLASS"]))

    if r"\documentclass" in text and r"\usepackage{adjustbox}" not in text:
        hits.append(make_rule_hit("P0_ADJUSTBOX_PACKAGE", STATIC_RULES["P0_ADJUSTBOX_PACKAGE"]))

    boxed_node_pattern = re.compile(r"\\node\[(?P<opts>[^\]]+)\][^;]*\{(?P<label>[^{}]*)\}\s*;", re.DOTALL)
    boxed_tokens = ("draw", "rectangle", "diamond", "circle", "box", "io", "decision", "dag-node", "flow-node")
    for match in boxed_node_pattern.finditer(text):
        opts = match.group("opts").lower()
        label = match.group("label").strip()
        if not label:
            continue
        if not any(token in opts for token in boxed_tokens):
            continue
        if any(token in opts for token in ("minimum width", "minimum height", "text width")):
            continue
        hits.append(make_rule_hit("P1_BOXED_NODE_DIMENSIONS", STATIC_RULES["P1_BOXED_NODE_DIMENSIONS"]))
        break

    node_count = len(re.findall(r"\\node(?:\[[^\]]*\])?", text))
    if node_count >= 3 and not re.search(r"(?im)^\s*%+\s*(coordinates|coordinate map)\s*:", text):
        hits.append(make_rule_hit("P2_COORDINATE_MAP", STATIC_RULES["P2_COORDINATE_MAP"]))

    for match in re.finditer(r"\\begin\{tikzpicture\}(?:\[(?P<opts>.*?)\])?", text, re.DOTALL):
        opts = (match.group("opts") or "").lower()
        if "scale=" in opts and "transform shape" not in opts and "every node/.style={scale=" not in opts:
            hits.append(make_rule_hit("P3_BARE_SCALE", STATIC_RULES["P3_BARE_SCALE"]))
            break

    direction_tokens = (
        "above",
        "below",
        "left",
        "right",
        "near start",
        "near end",
        "very near start",
        "very near end",
        "anchor=",
        "pos=",
    )
    for draw_stmt in re.finditer(r"\\draw(?:\[[^\]]*\])?.*?;", text, re.DOTALL):
        stmt = draw_stmt.group(0)
        for node_match in re.finditer(r"node(?:\[(?P<opts>[^\]]*)\])?\s*\{(?P<label>[^{}]+)\}", stmt, re.DOTALL):
            opts = (node_match.group("opts") or "").lower()
            label = node_match.group("label").strip()
            if label and not any(token in opts for token in direction_tokens):
                hits.append(make_rule_hit("P4_DIRECTIONAL_EDGE_LABELS", STATIC_RULES["P4_DIRECTIONAL_EDGE_LABELS"]))
                break
        if any(hit["rule_id"] == "P4_DIRECTIONAL_EDGE_LABELS" for hit in hits):
            break

    if re.search(r"\\draw(?:\[[^\]]*\])?\s*(?:\([^)]+\)\s*--\s*){2,}cycle\s*;", text, re.DOTALL):
        hits.append(make_rule_hit("P6_EXPLICIT_GRAPH_CLOSURE", STATIC_RULES["P6_EXPLICIT_GRAPH_CLOSURE"]))

    return hits


def check_file(tex_path: Path) -> dict[str, Any]:
    text = read_text(tex_path)
    rule_hits = detect_static_rule_hits(text)
    verdict = "PRECHECK_PASS" if not rule_hits else "NEEDS_REVISION"
    return {
        "verdict": verdict,
        "preflight_only": True,
        "final_verdict": "NOT_APPROVAL",
        "file": str(tex_path),
        "failed_rules": [hit["message"] for hit in rule_hits],
        "rule_hits": rule_hits,
        "rule_refs": [hit["rule_id"] for hit in rule_hits],
    }


def static_preflight_pass(result: dict[str, Any]) -> bool:
    return not result.get("rule_hits") and result.get("verdict") in {"PRECHECK_PASS", "APPROVED"}


def corrective_actions_for_rules(rule_ids: list[str]) -> list[str]:
    actions_by_rule = {
        "P0_ADJUSTBOX_ENV": "wrap the document-facing diagram in the adjustbox environment",
        "P0_STANDALONE_CLASS": "use plain standalone class and load TikZ packages explicitly",
        "P0_ADJUSTBOX_PACKAGE": "load adjustbox in standalone output",
        "P1_BOXED_NODE_DIMENSIONS": "add explicit width, height, or text width to boxed nodes",
        "P2_COORDINATE_MAP": "add a coordinate-map comment block ahead of the diagram",
        "P3_BARE_SCALE": "remove bare scale= or pair it with transform shape or every-node scaling",
        "P4_DIRECTIONAL_EDGE_LABELS": "add explicit directional or anchoring placement to edge labels",
        "P5_EXTRACT_FRESHNESS": "refresh the extracted artifacts from the current source-of-truth file",
        "P6_EXPLICIT_GRAPH_CLOSURE": "replace cycle closure with an explicit final edge between named nodes",
        "P7_APPROVAL_PROVENANCE": "rerun strict approval from current generated artifacts so provenance hashes refresh",
        "P8_SYMMETRY_CONTRACT": "add a structured symmetry_contract with required/not_required/intentionally_asymmetric intent",
        "P9_DESIGN_CONTRACT": "add or fix the figure-design artifact and rerun strict approval",
    }
    ordered = []
    for rule_id in rule_ids:
        action = actions_by_rule.get(rule_id)
        if action and action not in ordered:
            ordered.append(action)
    return ordered


def compile_tex(tex_path: Path, svg: bool) -> dict[str, Any]:
    latexmk = resolve_tool("latexmk")
    if not latexmk:
        return {
            "status": "BLOCKED_ENVIRONMENT",
            "exit_code": 5,
            "tool": "latexmk",
            "message": "latexmk is not available",
        }
    env = tool_environment()
    cmd = [latexmk, "-pdf", "-interaction=nonstopmode", "-halt-on-error", tex_path.name]
    proc = subprocess.run(cmd, cwd=tex_path.parent, text=True, capture_output=True, env=env)
    result: dict[str, Any] = {
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "exit_code": proc.returncode,
        "command": cmd,
        "cwd": str(tex_path.parent),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "pdf": str(tex_path.with_suffix(".pdf")),
        "svg": str(tex_path.with_suffix(".svg")),
    }
    if proc.returncode != 0:
        return result
    pdf_path = tex_path.with_suffix(".pdf")
    if svg:
        dvisvgm = resolve_tool("dvisvgm")
        if dvisvgm:
            svg_path = tex_path.with_suffix(".svg")
            svg_cmd = [dvisvgm, "--pdf", str(pdf_path), "-n", "-o", str(svg_path)]
            svg_proc = subprocess.run(svg_cmd, cwd=tex_path.parent, text=True, capture_output=True, env=env)
            result["svg_command"] = svg_cmd
            result["svg_stdout"] = svg_proc.stdout
            result["svg_stderr"] = svg_proc.stderr
            if svg_proc.returncode != 0:
                result["status"] = "FAIL"
                result["exit_code"] = svg_proc.returncode
                return result
        else:
            return {
                **result,
                "status": "BLOCKED_ENVIRONMENT",
                "exit_code": 5,
                "tool": "dvisvgm",
                "message": "requested --svg but dvisvgm is not available",
            }
    return result


def run_compile(tex_path: Path, svg: bool) -> int:
    result = compile_tex(tex_path, svg)
    if result["status"] == "BLOCKED_ENVIRONMENT":
        raise SystemExit(result.get("message", "compile dependency is not available"))
    if result["exit_code"] != 0:
        sys.stdout.write(result.get("stdout", ""))
        sys.stderr.write(result.get("stderr", ""))
        if result.get("svg_stdout") or result.get("svg_stderr"):
            sys.stdout.write(result.get("svg_stdout", ""))
            sys.stderr.write(result.get("svg_stderr", ""))
        return int(result["exit_code"])
    print(f"PDF\t{tex_path.with_suffix('.pdf')}")
    if svg:
        print(f"SVG\t{tex_path.with_suffix('.svg')}")
    return 0


def detect_env_block(text: str) -> tuple[str, str]:
    for env in ("tikzpicture", "forest", "tikzcd"):
        match = re.search(rf"(\\begin\{{{env}\}}.*?\\end\{{{env}\}})", text, re.DOTALL)
        if match:
            return env, match.group(1)
    raise SystemExit("no tikzpicture, forest, or tikzcd environment found")


def outputs_from_existing_env(env: str, body: str, figure_id: str) -> tuple[str, str]:
    if env == "tikzpicture":
        packages = [r"\usepackage{adjustbox}", r"\usepackage{tikz}"]
        libraries: list[str] = []
    elif env == "forest":
        packages = [r"\usepackage{adjustbox}", r"\usepackage[edges]{forest}"]
        libraries = []
    else:
        packages = [r"\usepackage{adjustbox}", r"\usepackage{tikz-cd}"]
        libraries = []

    standalone = "\n".join(
        [
            r"\documentclass[border=4pt]{standalone}",
            *packages,
            *libraries,
            "",
            r"\begin{document}",
            *wrap_in_adjustbox_environment(body),
            r"\end{document}",
            "",
        ]
    )
    snippet = "\n".join(
        [
            "% Generated by tikz-draw extract.",
            r"\begin{figure}[t]",
            r"\centering",
            *wrap_in_adjustbox_environment(body),
            rf"\label{{fig:{figure_id}}}",
            r"\end{figure}",
            "",
        ]
    )
    return standalone, snippet


def semantic_dependency_report() -> dict[str, Any]:
    report = {
        "required": [],
        "optional": [],
        "ready": True,
    }
    for module_name, label in REQUIRED_SEMANTIC_MODULES.items():
        try:
            module = importlib.import_module(module_name)
            version = getattr(module, "__version__", None)
            report["required"].append(
                {
                    "module": module_name,
                    "label": label,
                    "status": "OK",
                    "version": version,
                    "path": getattr(module, "__file__", None),
                }
            )
        except Exception as exc:  # noqa: BLE001
            report["required"].append(
                {
                    "module": module_name,
                    "label": label,
                    "status": "MISSING",
                    "error": str(exc),
                }
            )
            report["ready"] = False
    for module_name, label in OPTIONAL_SEMANTIC_MODULES.items():
        try:
            module = importlib.import_module(module_name)
            version = getattr(module, "__version__", None)
            report["optional"].append(
                {
                    "module": module_name,
                    "label": label,
                    "status": "OK",
                    "version": version,
                    "path": getattr(module, "__file__", None),
                }
            )
        except Exception as exc:  # noqa: BLE001
            report["optional"].append(
                {
                    "module": module_name,
                    "label": label,
                    "status": "MISSING",
                    "error": str(exc),
                }
            )
    return report


def base_semantic_report(manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    family = manifest.get("diagram_family") if manifest else None
    evidence = {
        "figure_brief": manifest.get("figure_brief") if manifest else None,
        "figure_contract": manifest.get("figure_contract") if manifest else None,
        "figure_design": manifest.get("figure_design") if manifest else None,
        "diagram_spec": manifest.get("diagram_spec") if manifest else None,
        "standalone_tex": manifest.get("standalone_tex") if manifest else None,
        "pdf": manifest.get("pdf") if manifest else None,
        "render_semantics": manifest.get("render_semantics") if manifest else None,
        "semantic_review": manifest.get("semantic_review") if manifest else None,
    }
    return {
        "review_status": "TOOL_ERROR",
        "family": family,
        "static_status": "SKIPPED",
        "visual_status": "SKIPPED",
        "overlap_status": "SKIPPED",
        "compile_status": "SKIPPED",
        "semantic_status": "SKIPPED",
        "design_status": "SKIPPED",
        "symmetry_status": "SKIPPED",
        "semantic_verdict": None,
        "final_verdict": None,
        "supported_family": family in SEMANTIC_VERIFIER_FAMILIES if family else False,
        "mismatches": [],
        "mismatch_codes": [],
        "rule_hits": [],
        "rule_refs": [],
        "warnings": [],
        "visual_review": {
            "passes_run": [],
            "findings": [],
        },
        "design_review": {
            "findings": [],
        },
        "symmetry_review": {
            "contract": None,
            "findings": [],
        },
        "evidence": evidence,
        "graph_mode_requested": manifest.get("graph_mode_requested") if manifest else None,
        "graph_route_status": manifest.get("graph_route_status") if manifest else None,
        "graph_route_reason": manifest.get("graph_route_reason") if manifest else None,
        "graph_backend_used": manifest.get("graph_backend_used") if manifest else None,
    }


def finalize_report(report: dict[str, Any]) -> dict[str, Any]:
    report["rule_refs"] = [hit["rule_id"] for hit in report.get("rule_hits", [])]
    return report


def write_semantic_report(manifest: dict[str, Any], report: dict[str, Any]) -> None:
    report_path = abs_path(manifest.get("semantic_review"))
    if report_path is None:
        return
    dump_json(report_path, report)


def load_render_semantics(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    required = {"schema_version", "extractor_version", "pdf", "page_count", "normalization", "pages"}
    missing = sorted(required - set(payload))
    if missing:
        raise SystemExit(f"render-semantics missing required keys: {', '.join(missing)}")
    return payload


def materialize_render_semantics(manifest: dict[str, Any], manifest_path: Path, work_dir: Path) -> tuple[dict[str, Any], Path]:
    pdf_path = abs_path(manifest.get("pdf"))
    render_path = abs_path(manifest.get("render_semantics"))
    if pdf_path is None or not pdf_path.is_file():
        raise SystemExit("compiled PDF is required before render-semantic extraction")
    if render_path is None:
        render_path = work_dir / f"{manifest['figure_id']}.render-semantics.json"
    payload = extract_pdf_render_semantics(pdf_path, manifest_path)
    dump_json(render_path, payload)
    return load_render_semantics(render_path), render_path


def bbox_tuple(bbox: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(bbox["x0"]),
        float(bbox["y0"]),
        float(bbox["x1"]),
        float(bbox["y1"]),
    )


def make_visual_finding(
    pass_id: str,
    *,
    page_index: int,
    message: str,
    severity: str = "FAIL",
    subject: str | None = None,
    measured_pt: float | None = None,
    threshold_pt: float | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    finding: dict[str, Any] = {
        "pass_id": pass_id,
        "severity": severity,
        "page_index": page_index,
        "message": message,
    }
    if subject is not None:
        finding["subject"] = subject
    if measured_pt is not None:
        finding["measured_pt"] = round(float(measured_pt), 4)
    if threshold_pt is not None:
        finding["threshold_pt"] = round(float(threshold_pt), 4)
    if evidence is not None:
        finding["evidence"] = evidence
    return finding


def bbox_payload_from_tuple(bounds: tuple[float, float, float, float]) -> dict[str, float]:
    x0, y0, x1, y1 = bounds
    return {
        "x0": round(float(x0), 4),
        "y0": round(float(y0), 4),
        "x1": round(float(x1), 4),
        "y1": round(float(y1), 4),
        "width": round(float(x1 - x0), 4),
        "height": round(float(y1 - y0), 4),
    }


def words_bbox(words: list[dict[str, Any]]) -> tuple[float, float, float, float] | None:
    boxes = [bbox_tuple(item["bbox"]) for item in words if item.get("bbox")]
    if not boxes:
        return None
    return (
        min(item[0] for item in boxes),
        min(item[1] for item in boxes),
        max(item[2] for item in boxes),
        max(item[3] for item in boxes),
    )


def should_merge_block_as_single_label(words: list[dict[str, Any]]) -> bool:
    if len(words) <= 1:
        return False
    lines = {int(word.get("line", 0)) for word in words}
    if len(lines) <= 1:
        return False
    bounds = words_bbox(words)
    if bounds is None:
        return False
    x0, y0, x1, y1 = bounds
    width = x1 - x0
    height = y1 - y0
    compact_text = "".join(
        str(word.get("text", ""))
        for word in sorted(words, key=lambda item: (int(item.get("line", 0)), int(item.get("word", 0))))
    )
    compact_text = compact_ws(compact_text)
    if width > 90.0 or height > 36.0 or len(compact_text) > 28:
        return False
    math_like = bool(re.fullmatch(r"[A-Za-z0-9_{}^=,+\-()\\| ]+", compact_text))
    small_fragment = any(
        float(word.get("bbox", {}).get("height", 0.0)) < 5.0 or len(str(word.get("text", ""))) <= 2
        for word in words
    )
    return math_like and small_fragment


def word_cluster_lines(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    by_line: dict[int, list[dict[str, Any]]] = {}
    for word in words:
        by_line.setdefault(int(word.get("line", 0)), []).append(word)
    clusters: list[list[dict[str, Any]]] = []
    for _line, line_words in sorted(by_line.items()):
        ordered = sorted(line_words, key=lambda item: float(item.get("bbox", {}).get("x0", 0.0)))
        current: list[dict[str, Any]] = []
        current_bounds: tuple[float, float, float, float] | None = None
        for word in ordered:
            bounds = bbox_tuple(word["bbox"]) if word.get("bbox") else None
            if bounds is None:
                continue
            if current and current_bounds is not None:
                gap = bounds[0] - current_bounds[2]
                if gap > 9.0:
                    clusters.append(current)
                    current = []
                    current_bounds = None
            current.append(word)
            current_bounds = words_bbox(current)
        if current:
            clusters.append(current)
    return clusters


def should_merge_clusters_as_math(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    left_bounds = words_bbox(left)
    right_bounds = words_bbox(right)
    if left_bounds is None or right_bounds is None:
        return False
    combined = [*left, *right]
    combined_bounds = words_bbox(combined)
    if combined_bounds is None:
        return False
    x0, y0, x1, y1 = combined_bounds
    if x1 - x0 > 45.0 or y1 - y0 > 24.0:
        return False
    left_x0, _left_y0, left_x1, _left_y1 = left_bounds
    right_x0, _right_y0, right_x1, _right_y1 = right_bounds
    x_overlap_or_near = min(left_x1, right_x1) - max(left_x0, right_x0) >= -3.0
    if not x_overlap_or_near:
        return False
    compact_text = "".join(
        str(word.get("text", ""))
        for word in sorted(combined, key=lambda item: (int(item.get("line", 0)), int(item.get("word", 0))))
    )
    return bool(re.fullmatch(r"[A-Za-z0-9_{}^=,+\-()\\| ]{1,28}", compact_text.strip()))


def visual_word_clusters(block_words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    clusters = word_cluster_lines(block_words)
    changed = True
    while changed:
        changed = False
        next_clusters: list[list[dict[str, Any]]] = []
        used: set[int] = set()
        for left_index, left in enumerate(clusters):
            if left_index in used:
                continue
            merged = list(left)
            used.add(left_index)
            for right_index, right in enumerate(clusters[left_index + 1 :], start=left_index + 1):
                if right_index in used:
                    continue
                if should_merge_clusters_as_math(merged, right):
                    merged.extend(right)
                    used.add(right_index)
                    changed = True
            next_clusters.append(merged)
        clusters = next_clusters
    return clusters


def merge_words_into_visual_labels(page: dict[str, Any]) -> list[dict[str, Any]]:
    by_block: dict[int, list[dict[str, Any]]] = {}
    for word in page.get("words", []):
        by_block.setdefault(int(word.get("block", 0)), []).append(word)
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for block, block_words in sorted(by_block.items()):
        for cluster_index, cluster in enumerate(visual_word_clusters(block_words)):
            grouped[(block, cluster_index)] = cluster
    labels: list[dict[str, Any]] = []
    for (block, line), words in sorted(grouped.items()):
        ordered = sorted(words, key=lambda item: (int(item.get("line", 0)), int(item.get("word", 0))))
        bounds = words_bbox(ordered)
        if bounds is None:
            continue
        x0, y0, x1, y1 = bounds
        text = " ".join(str(item.get("text", "")) for item in ordered).strip()
        labels.append(
            {
                "primitive_id": f"text-{block}-{line}",
                "text": text,
                "bbox": bbox_payload_from_tuple((x0, y0, x1, y1)),
                "block": block,
                "line": line,
                "word_count": len(ordered),
            }
        )
    return labels


def drawing_primitive_id(drawing: dict[str, Any], index: int) -> str:
    return str(drawing.get("primitive_id") or f"drawing-{drawing.get('seqno', index)}")


def drawing_ops(drawing: dict[str, Any]) -> list[str]:
    return [str(item.get("op")) for item in drawing.get("items", [])]


def is_groupbox_drawing(drawing: dict[str, Any]) -> bool:
    dashes = drawing.get("dashes")
    return dashes not in (None, "[] 0", [])


def visual_shape_primitives(page: dict[str, Any]) -> list[dict[str, Any]]:
    from shapely.geometry import box  # type: ignore

    shapes: list[dict[str, Any]] = []
    for index, drawing in enumerate(page.get("drawings", [])):
        rect = drawing.get("rect")
        if not rect:
            continue
        ops = drawing_ops(drawing)
        if ops == ["l"]:
            continue
        shape_like = ops == ["l", "l", "l", "l"] or (len(ops) >= 4 and set(ops).issubset({"l", "c"}))
        if not shape_like:
            continue
        if float(rect.get("width", 0.0)) < 4.0 or float(rect.get("height", 0.0)) < 4.0:
            continue
        shapes.append(
            {
                "primitive_id": drawing_primitive_id(drawing, index),
                "drawing_index": index,
                "kind": "groupbox" if is_groupbox_drawing(drawing) else "shape",
                "bbox": rect,
                "geom": box(*bbox_tuple(rect)),
            }
        )
    return shapes


def visual_line_primitives(page: dict[str, Any]) -> list[dict[str, Any]]:
    from shapely.geometry import LineString  # type: ignore

    lines: list[dict[str, Any]] = []
    for index, drawing in enumerate(page.get("drawings", [])):
        if is_groupbox_drawing(drawing):
            continue
        ops = drawing_ops(drawing)
        if ops != ["l"]:
            continue
        base_id = drawing_primitive_id(drawing, index)
        for item_index, item in enumerate(drawing.get("items", [])):
            args = item.get("args") or []
            if len(args) != 2:
                continue
            start, end = args
            if not all(key in start for key in ("x", "y")) or not all(key in end for key in ("x", "y")):
                continue
            start_xy = (float(start["x"]), float(start["y"]))
            end_xy = (float(end["x"]), float(end["y"]))
            lines.append(
                {
                    "primitive_id": f"{base_id}-line-{item_index}",
                    "drawing_index": index,
                    "start": {"x": round(start_xy[0], 4), "y": round(start_xy[1], 4)},
                    "end": {"x": round(end_xy[0], 4), "y": round(end_xy[1], 4)},
                    "geom": LineString([start_xy, end_xy]),
                    "width": float(drawing.get("width") or 0.0),
                }
            )
    return lines


def endpoint_touches_shape(line: dict[str, Any], shape: dict[str, Any], tolerance: float = 2.0) -> bool:
    from shapely.geometry import Point  # type: ignore

    start = Point(float(line["start"]["x"]), float(line["start"]["y"]))
    end = Point(float(line["end"]["x"]), float(line["end"]["y"]))
    geom = shape["geom"].buffer(tolerance)
    return geom.intersects(start) or geom.intersects(end)


def evaluate_visual_review(render_semantics: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    from shapely.geometry import box  # type: ignore

    findings: list[dict[str, Any]] = []
    warnings: list[str] = []
    for page in render_semantics.get("pages", []):
        page_index = int(page["page_index"])
        page_width = float(page["width"])
        page_height = float(page["height"])
        labels = merge_words_into_visual_labels(page)
        shapes = visual_shape_primitives(page)
        lines = visual_line_primitives(page)

        for left_index, left in enumerate(labels):
            left_box = box(*bbox_tuple(left["bbox"]))
            for right in labels[left_index + 1 :]:
                right_box = box(*bbox_tuple(right["bbox"]))
                overlap_area = left_box.intersection(right_box).area
                if overlap_area > OVERLAP_EPSILON_PT:
                    findings.append(
                        make_visual_finding(
                            "V4_TEXT_TEXT_OVERLAP",
                            page_index=page_index,
                            subject=f"{left['primitive_id']}:{right['primitive_id']}",
                            measured_pt=overlap_area,
                            threshold_pt=OVERLAP_EPSILON_PT,
                            message=f"text labels '{left['text']}' and '{right['text']}' overlap",
                            evidence={
                                "left": {"id": left["primitive_id"], "text": left["text"], "bbox": left["bbox"]},
                                "right": {"id": right["primitive_id"], "text": right["text"], "bbox": right["bbox"]},
                            },
                        )
                    )

        for label in labels:
            word_box = box(*bbox_tuple(label["bbox"]))
            subject = str(label.get("text", "")).strip() or label["primitive_id"]

            x0, y0, x1, y1 = bbox_tuple(label["bbox"])
            page_margin = min(x0, y0, page_width - x1, page_height - y1)
            if page_margin < VISUAL_THRESHOLDS_PT["V3_PAGE_MARGIN"]:
                findings.append(
                    make_visual_finding(
                        "V3_PAGE_MARGIN",
                        page_index=page_index,
                        subject=subject,
                        measured_pt=page_margin,
                        threshold_pt=VISUAL_THRESHOLDS_PT["V3_PAGE_MARGIN"],
                        message=f"text '{subject}' sits too close to the page boundary",
                        evidence={"text": {"id": label["primitive_id"], "bbox": label["bbox"]}},
                    )
                )

            containing_shapes: list[float] = []
            exterior_gaps: list[float] = []
            for shape in shapes:
                drawing_box = shape["geom"]
                if drawing_box.area <= word_box.area * 1.05:
                    continue
                if drawing_box.buffer(1e-6).contains(word_box):
                    clearance = word_box.distance(drawing_box.boundary)
                    containing_shapes.append(clearance)
                else:
                    gap = word_box.distance(drawing_box)
                    exterior_gaps.append(gap)
                    if word_box.intersects(drawing_box):
                        overlap_area = word_box.intersection(drawing_box).area
                        if overlap_area > OVERLAP_EPSILON_PT:
                            findings.append(
                                make_visual_finding(
                                    "V5_TEXT_SHAPE_OVERLAP",
                                    page_index=page_index,
                                    subject=subject,
                                    measured_pt=overlap_area,
                                    threshold_pt=OVERLAP_EPSILON_PT,
                                    message=f"text '{subject}' overlaps a non-containing shape",
                                    evidence={
                                        "text": {"id": label["primitive_id"], "bbox": label["bbox"]},
                                        "shape": {"id": shape["primitive_id"], "bbox": shape["bbox"]},
                                    },
                                )
                            )

            if containing_shapes:
                clearance = min(containing_shapes)
                if clearance < VISUAL_THRESHOLDS_PT["V2_BOUNDARY_CLEARANCE"]:
                    findings.append(
                        make_visual_finding(
                            "V2_BOUNDARY_CLEARANCE",
                            page_index=page_index,
                            subject=subject,
                            measured_pt=clearance,
                            threshold_pt=VISUAL_THRESHOLDS_PT["V2_BOUNDARY_CLEARANCE"],
                            message=f"text '{subject}' is too close to its enclosing shape boundary",
                            evidence={"text": {"id": label["primitive_id"], "bbox": label["bbox"]}},
                        )
                    )
            if exterior_gaps:
                gap = min(exterior_gaps)
                if 0.0 <= gap < VISUAL_THRESHOLDS_PT["V1_LABEL_GAP"]:
                    findings.append(
                        make_visual_finding(
                            "V1_LABEL_GAP",
                            page_index=page_index,
                            subject=subject,
                            measured_pt=gap,
                            threshold_pt=VISUAL_THRESHOLDS_PT["V1_LABEL_GAP"],
                            message=f"text '{subject}' is too close to nearby linework or shapes",
                            evidence={"text": {"id": label["primitive_id"], "bbox": label["bbox"]}},
                        )
                    )

            for line in lines:
                shrunken_text = word_box.buffer(-OVERLAP_EPSILON_PT)
                text_target = shrunken_text if not shrunken_text.is_empty else word_box
                if line["geom"].intersects(text_target):
                    findings.append(
                        make_visual_finding(
                            "V6_LINE_TEXT_OVERLAP",
                            page_index=page_index,
                            subject=subject,
                            measured_pt=0.0,
                            threshold_pt=OVERLAP_EPSILON_PT,
                            message=f"linework crosses text '{subject}'",
                            evidence={
                                "line": {"id": line["primitive_id"], "start": line["start"], "end": line["end"]},
                                "text": {"id": label["primitive_id"], "bbox": label["bbox"]},
                            },
                        )
                    )

        for line in lines:
            for shape in shapes:
                if shape["kind"] == "groupbox" or endpoint_touches_shape(line, shape):
                    continue
                interior = shape["geom"].buffer(-0.5)
                target = interior if not interior.is_empty else shape["geom"]
                if line["geom"].intersects(target):
                    findings.append(
                        make_visual_finding(
                            "V7_LINE_SHAPE_OVERLAP",
                            page_index=page_index,
                            subject=f"{line['primitive_id']}:{shape['primitive_id']}",
                            measured_pt=0.0,
                            threshold_pt=OVERLAP_EPSILON_PT,
                            message="linework crosses a non-incident shape",
                            evidence={
                                "line": {"id": line["primitive_id"], "start": line["start"], "end": line["end"]},
                                "shape": {"id": shape["primitive_id"], "bbox": shape["bbox"]},
                            },
                        )
                    )

        non_group_shapes = [shape for shape in shapes if shape["kind"] != "groupbox"]
        for left_index, left in enumerate(non_group_shapes):
            for right in non_group_shapes[left_index + 1 :]:
                if left["geom"].contains(right["geom"]) or right["geom"].contains(left["geom"]):
                    continue
                overlap_area = left["geom"].intersection(right["geom"]).area
                if overlap_area > OVERLAP_EPSILON_PT:
                    findings.append(
                        make_visual_finding(
                            "V8_SHAPE_SHAPE_OVERLAP",
                            page_index=page_index,
                            subject=f"{left['primitive_id']}:{right['primitive_id']}",
                            measured_pt=overlap_area,
                            threshold_pt=OVERLAP_EPSILON_PT,
                            message="non-group shapes overlap",
                            evidence={
                                "left": {"id": left["primitive_id"], "bbox": left["bbox"]},
                                "right": {"id": right["primitive_id"], "bbox": right["bbox"]},
                            },
                        )
                    )

    return findings, warnings


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = load_json(path)
    missing = sorted(MANIFEST_REQUIRED_FIELDS - set(manifest))
    if missing:
        raise SystemExit(f"artifact manifest missing required keys: {', '.join(missing)}")
    return manifest


def assess_freshness(manifest: dict[str, Any]) -> list[dict[str, str]]:
    extracted_from = manifest.get("extracted_from")
    freshness_status = manifest.get("freshness_status")
    if not extracted_from and freshness_status in {None, "not_applicable"}:
        return []
    if not extracted_from:
        return [make_rule_hit("P5_EXTRACT_FRESHNESS", STATIC_RULES["P5_EXTRACT_FRESHNESS"])]
    source_hash = manifest.get("source_hash")
    source_mtime = manifest.get("source_mtime")
    if not source_hash or not source_mtime or not freshness_status:
        return [make_rule_hit("P5_EXTRACT_FRESHNESS", STATIC_RULES["P5_EXTRACT_FRESHNESS"])]
    source_path = abs_path(extracted_from)
    assert source_path is not None
    if not source_path.is_file():
        return [make_rule_hit("P5_EXTRACT_FRESHNESS", f"source-of-truth file is missing: {source_path}")]
    current_hash, current_mtime = source_metadata(source_path)
    if current_hash != source_hash or current_mtime != source_mtime:
        return [make_rule_hit("P5_EXTRACT_FRESHNESS", "extracted artifact is stale relative to the current source-of-truth file")]
    return []


def semantic_status_from_missing_dependencies(report: dict[str, Any]) -> tuple[dict[str, Any], int] | None:
    deps = semantic_dependency_report()
    if deps["ready"]:
        return None
    report["review_status"] = "BLOCKED_ENVIRONMENT"
    report["warnings"].append("required semantic-verifier dependencies are missing")
    report["warnings"].append(json.dumps(deps, ensure_ascii=True))
    return finalize_report(report), 5


def run_review_visual_report(manifest_path: Path, work_dir: Path) -> tuple[dict[str, Any], int]:
    manifest = load_manifest(manifest_path)
    report = base_semantic_report(manifest)
    report["visual_review"]["passes_run"] = list(VISUAL_REVIEW_PASS_IDS)

    freshness_hits = assess_freshness(manifest)
    if freshness_hits:
        report["review_status"] = "BLOCKED_INPUT"
        report["visual_status"] = "BLOCKED"
        report["overlap_status"] = "BLOCKED"
        report["rule_hits"].extend(freshness_hits)
        report["warnings"].append("freshness checks failed before visual review")
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 3

    pdf_path = abs_path(manifest.get("pdf"))
    if pdf_path is None or not pdf_path.is_file():
        report["review_status"] = "BLOCKED_INPUT"
        report["visual_status"] = "BLOCKED"
        report["overlap_status"] = "BLOCKED"
        report["warnings"].append("compiled PDF is required for review-visual")
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 3

    missing_deps = semantic_status_from_missing_dependencies(report)
    if missing_deps is not None:
        report, exit_code = missing_deps
        report["visual_status"] = "BLOCKED"
        report["overlap_status"] = "BLOCKED"
        write_semantic_report(manifest, report)
        return report, exit_code

    render_semantics, render_path = materialize_render_semantics(manifest, manifest_path, work_dir)
    report["evidence"]["render_semantics"] = str(render_path)
    findings, warnings = evaluate_visual_review(render_semantics)
    report["warnings"].extend(warnings)
    report["compile_status"] = "PASS"
    report["visual_review"]["findings"] = findings
    report["review_status"] = "COMPLETE"
    report["visual_status"] = "FAIL" if findings else "PASS"
    report["overlap_status"] = report["visual_status"]
    finalized = finalize_report(report)
    write_semantic_report(manifest, finalized)
    return finalized, 1 if findings else 0


def run_verify_semantic_report(manifest_path: Path, work_dir: Path) -> tuple[dict[str, Any], int]:
    manifest = load_manifest(manifest_path)
    report = base_semantic_report(manifest)

    family = manifest.get("diagram_family")

    freshness_hits = assess_freshness(manifest)
    if freshness_hits:
        report["review_status"] = "BLOCKED_INPUT"
        report["semantic_status"] = "BLOCKED"
        report["rule_hits"].extend(freshness_hits)
        report["warnings"].append("freshness checks failed before semantic verification")
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 3

    pdf_path = abs_path(manifest.get("pdf"))
    if pdf_path is None or not pdf_path.is_file():
        report["review_status"] = "BLOCKED_INPUT"
        report["semantic_status"] = "BLOCKED"
        report["warnings"].append("compiled PDF is required for semantic verification")
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 3

    missing_deps = semantic_status_from_missing_dependencies(report)
    if missing_deps is not None:
        report, exit_code = missing_deps
        report["semantic_status"] = "BLOCKED"
        write_semantic_report(manifest, report)
        return report, exit_code

    render_semantics, render_path = materialize_render_semantics(manifest, manifest_path, work_dir)
    report["evidence"]["render_semantics"] = str(render_path)
    report["compile_status"] = "PASS"

    if not manifest.get("semantic_target_present") or not manifest.get("diagram_spec"):
        report["review_status"] = "BLOCKED_INPUT"
        report["semantic_status"] = "BLOCKED"
        report["warnings"].append("semantic verification requires a confirmed semantic target and diagram spec")
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 3

    if family not in SEMANTIC_VERIFIER_FAMILIES:
        report["review_status"] = "UNSUPPORTED_FAMILY"
        report["semantic_status"] = "BLOCKED"
        report["supported_family"] = False
        report["warnings"].append(f"family-specific semantic verification is not implemented yet for: {family}")
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 4

    spec_path = abs_path(manifest.get("diagram_spec"))
    if spec_path is None or not spec_path.is_file():
        report["review_status"] = "BLOCKED_INPUT"
        report["semantic_status"] = "BLOCKED"
        report["warnings"].append("diagram_spec is required for semantic verification")
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 3

    spec = load_json(spec_path)
    verification = verify_rendered_family(spec, render_semantics)
    contract_mismatches = validate_contract_against_spec(spec.get("semantic_contract"), spec)
    report["supported_family"] = verification["supported_family"]
    report["mismatches"] = [*verification["mismatches"], *contract_mismatches]
    report["mismatch_codes"] = [
        *verification["mismatch_codes"],
        *[str(item.get("code")) for item in contract_mismatches],
    ]
    report["evidence"]["recovered"] = verification["recovered"]
    report["semantic_status"] = "FAIL" if report["mismatches"] else "PASS"
    report["review_status"] = "COMPLETE"
    report["semantic_verdict"] = "NEEDS_REVISION" if report["mismatches"] else "APPROVED"
    if report["mismatches"]:
        report["warnings"].append(f"semantic verification found {len(report['mismatches'])} mismatch(es)")
    else:
        report["warnings"].append(
            f"semantic verification matched the current rendered {family} structure using {render_semantics.get('extractor_version')}"
        )
    finalized = finalize_report(report)
    write_semantic_report(manifest, finalized)
    return finalized, 1 if report["mismatches"] else 0


def contract_from_manifest_or_spec(manifest: dict[str, Any], spec: dict[str, Any] | None) -> dict[str, Any] | None:
    if spec and spec.get("semantic_contract") is not None:
        return normalize_figure_contract(spec["semantic_contract"])
    contract_path = abs_path(manifest.get("figure_contract"))
    if contract_path and contract_path.is_file():
        return normalize_figure_contract(load_json(contract_path))
    return None


def design_from_manifest_or_spec(manifest: dict[str, Any], spec: dict[str, Any] | None) -> dict[str, Any] | None:
    figure_id = str(manifest.get("figure_id") or "F1")
    design_path = abs_path(manifest.get("figure_design"))
    if design_path and design_path.is_file():
        return normalize_figure_design(load_json(design_path), figure_id=figure_id)
    if spec and spec.get("semantic_design") is not None:
        return normalize_figure_design(spec["semantic_design"], figure_id=figure_id)
    return None


def design_required_for_approval(
    *,
    manifest: dict[str, Any],
    spec: dict[str, Any] | None,
    contract: dict[str, Any] | None,
    design: dict[str, Any] | None,
) -> bool:
    if design is not None:
        return True
    if contract is not None and contract_requires_design(contract):
        return True
    if spec and spec.get("marks"):
        return True
    if manifest.get("extracted_from") and manifest.get("semantic_target_present"):
        return True
    return False


def make_design_finding(code: str, message: str, *, severity: str = "FAIL", **payload: Any) -> dict[str, Any]:
    finding = contract_mismatch(code, message, severity=severity)
    finding.update(payload)
    return finding


def mark_claim_ids(marks: list[dict[str, Any]], key: str) -> set[str]:
    values: set[str] = set()
    for mark in marks:
        values.update(str(item) for item in mark.get(key, []))
    return values


def contract_has_text(contract: dict[str, Any] | None, *tokens: str) -> bool:
    if contract is None:
        return False
    index = contract_text_index(contract)
    return any(token in index for token in tokens)


def evaluate_design_review(
    *,
    manifest: dict[str, Any],
    spec: dict[str, Any] | None,
    contract: dict[str, Any] | None,
    design: dict[str, Any] | None,
    required: bool,
) -> dict[str, Any]:
    review: dict[str, Any] = {
        "required": required,
        "status": "SKIPPED",
        "findings": [],
        "mismatch_codes": [],
    }
    if design is None:
        if required:
            review["status"] = "BLOCKED"
            review["findings"].append(
                make_design_finding(
                    "DESIGN_STATUS_MISSING",
                    "scoped semantic figure requires a figure-design artifact before approval",
                    severity="BLOCKED",
                )
            )
        return review

    findings: list[dict[str, Any]] = []
    marks = design.get("marks", [])
    if not marks:
        findings.append(
            make_design_finding(
                "DESIGN_MARKS_MISSING",
                "figure design must declare at least one visual-semantic mark",
            )
        )
    mark_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    for mark in marks:
        mark_id = str(mark.get("id"))
        if mark_id in mark_ids:
            duplicate_ids.add(mark_id)
        mark_ids.add(mark_id)
    for mark_id in sorted(duplicate_ids):
        findings.append(make_design_finding("DUPLICATE_DESIGN_MARK", "figure design has duplicate mark id", mark=mark_id))

    if spec is not None and design.get("figure_id") != manifest.get("figure_id"):
        findings.append(
            make_design_finding(
                "DESIGN_FIGURE_ID_MISMATCH",
                "figure design figure_id does not match the artifact manifest",
                design_figure_id=design.get("figure_id"),
                manifest_figure_id=manifest.get("figure_id"),
            )
        )

    if spec is not None:
        spec_mark_ids = {str(mark.get("id")) for mark in spec.get("marks", []) if isinstance(mark, dict)}
        if marks and not spec_mark_ids:
            findings.append(
                make_design_finding(
                    "DESIGN_MARKS_NOT_IN_SPEC",
                    "diagram spec does not carry the visual-semantic marks from the figure design",
                )
            )
        missing_in_spec = sorted(mark_ids - spec_mark_ids) if spec_mark_ids else []
        for mark_id in missing_in_spec:
            findings.append(
                make_design_finding(
                    "DESIGN_MARKS_NOT_IN_SPEC",
                    "figure design mark is missing from the diagram spec",
                    mark=mark_id,
                )
            )

    caption_claim_ids = {str(claim.get("id")) for claim in design.get("caption_claims", [])}
    bound_caption_claim_ids = mark_claim_ids(marks, "caption_claim_ids")
    for claim_id in sorted(caption_claim_ids - bound_caption_claim_ids):
        findings.append(
            make_design_finding(
                "CAPTION_CONTRACT_MISMATCH",
                "caption or contract claim is not bound to any visual-semantic mark",
                claim=claim_id,
            )
        )
    for claim_id in sorted(bound_caption_claim_ids - caption_claim_ids):
        findings.append(
            make_design_finding(
                "CAPTION_CONTRACT_MISMATCH",
                "visual-semantic mark references an unknown caption claim",
                claim=claim_id,
            )
        )

    has_gadget_mark = False
    has_correspondence_mark = False
    for mark in marks:
        mark_id = str(mark.get("id"))
        role = str(mark.get("role"))
        semantic_type = str(mark.get("semantic_type", "")).lower()
        visual_encoding = str(mark.get("visual_encoding", "")).lower()
        fill_policy = str(mark.get("fill_policy", "")).lower()
        label = str(mark.get("label", ""))
        counts_as_graph_object = bool(mark.get("counts_as_graph_object"))

        if role not in DESIGN_MARK_ROLES:
            findings.append(
                make_design_finding("INVALID_DESIGN_MARK_ROLE", "figure design mark has invalid role", mark=mark_id)
            )
            continue
        if role == "graph_object" and not counts_as_graph_object:
            findings.append(
                make_design_finding(
                    "GRAPH_OBJECT_NOT_COUNTED",
                    "graph_object mark must count as graph structure",
                    mark=mark_id,
                )
            )
        if role != "graph_object" and counts_as_graph_object:
            findings.append(
                make_design_finding(
                    "ANNOTATION_COUNTED_AS_GRAPH_OBJECT",
                    "non-graph visual mark is counted as graph structure",
                    mark=mark_id,
                    role=role,
                )
            )
        metadata_like = role in {"annotation", "callout", "legend"} or any(
            token in semantic_type for token in ("metadata", "notation", "constraint", "label")
        )
        boxed_like = any(token in visual_encoding for token in ("boxed", "box", "graphnode", "vertex", "node style"))
        if metadata_like and boxed_like:
            findings.append(
                make_design_finding(
                    "METADATA_RENDERED_AS_GRAPH_OBJECT",
                    "metadata or notation mark uses graph-object-like visual encoding",
                    mark=mark_id,
                    visual_encoding=mark.get("visual_encoding"),
                )
            )
        if label.startswith("L(") and boxed_like:
            findings.append(
                make_design_finding(
                    "METADATA_RENDERED_AS_GRAPH_OBJECT",
                    "list annotation is boxed like a graph object",
                    mark=mark_id,
                    label=label,
                )
            )
        if role in {"gadget_region", "highlight_region"}:
            has_gadget_mark = has_gadget_mark or role == "gadget_region" or "gadget" in semantic_type
            opaque_fill = any(token in fill_policy for token in ("opaque", "filled", "solid_fill"))
            encoded_fill = any(token in visual_encoding for token in ("opaque", "filled box", "solid fill"))
            if opaque_fill or encoded_fill:
                findings.append(
                    make_design_finding(
                        "FILL_OCCLUDES_GRAPH_STRUCTURE",
                        "region fill can obscure graph structure or make membership ambiguous",
                        mark=mark_id,
                    )
                )
        if role == "correspondence":
            has_correspondence_mark = True
            if not mark.get("source_targets") or not mark.get("target_targets"):
                findings.append(
                    make_design_finding(
                        "CONTRACT_REPLACEMENT_RELATION_MISSING",
                        "correspondence mark must bind source_targets and target_targets",
                        mark=mark_id,
                    )
                )
        if "port" in semantic_type and not mark.get("targets"):
            findings.append(
                make_design_finding(
                    "WRONG_PORT_LABEL",
                    "port label mark must declare the port or vertex it attaches to",
                    mark=mark_id,
                )
            )

    if contract_has_text(contract, "gadget") and not has_gadget_mark:
        findings.append(
            make_design_finding(
                "CONTRACT_FORBIDDEN_LABEL_ONLY_GADGET",
                "contract requires a gadget mark rather than a label-only gadget",
            )
        )
    if contract_has_text(contract, "replacement", "replaces") and not has_correspondence_mark:
        findings.append(
            make_design_finding(
                "CONTRACT_REPLACEMENT_RELATION_MISSING",
                "contract requires a source-to-target correspondence mark",
            )
        )

    review["findings"] = findings
    review["mismatch_codes"] = [str(item.get("code")) for item in findings]
    review["status"] = "FAIL" if findings else "PASS"
    return review


def run_verify_design_report(manifest_path: Path, work_dir: Path) -> tuple[dict[str, Any], int]:
    del work_dir
    manifest = load_manifest(manifest_path)
    report = base_semantic_report(manifest)
    spec = None
    spec_path = abs_path(manifest.get("diagram_spec"))
    if spec_path and spec_path.is_file():
        spec = load_json(spec_path)
    contract = contract_from_manifest_or_spec(manifest, spec)
    design = design_from_manifest_or_spec(manifest, spec)
    required = design_required_for_approval(manifest=manifest, spec=spec, contract=contract, design=design)
    review = evaluate_design_review(
        manifest=manifest,
        spec=spec,
        contract=contract,
        design=design,
        required=required,
    )
    report["design_review"] = review
    report["design_status"] = review["status"]
    report["mismatches"] = review.get("findings", [])
    report["mismatch_codes"] = review.get("mismatch_codes", [])
    if report["design_status"] == "BLOCKED":
        report["review_status"] = "BLOCKED_INPUT"
        report["warnings"].append("design verification is blocked by a missing required figure design")
        report["rule_hits"].append(make_rule_hit("P9_DESIGN_CONTRACT", STATIC_RULES["P9_DESIGN_CONTRACT"]))
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 3
    report["review_status"] = "COMPLETE"
    if report["design_status"] == "FAIL":
        report["semantic_verdict"] = "NEEDS_REVISION"
        report["warnings"].append(f"design verification found {len(report['mismatches'])} mismatch(es)")
        report["rule_hits"].append(make_rule_hit("P9_DESIGN_CONTRACT", STATIC_RULES["P9_DESIGN_CONTRACT"]))
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 1
    if report["design_status"] == "PASS":
        report["warnings"].append("design verification matched declared visual-semantic roles")
    else:
        report["warnings"].append("design verification skipped because no scoped design gate is required")
    finalized = finalize_report(report)
    write_semantic_report(manifest, finalized)
    return finalized, 0


def bbox_center_payload(bbox: dict[str, Any]) -> dict[str, float]:
    x0, y0, x1, y1 = bbox_tuple(bbox)
    return {
        "x": round((x0 + x1) / 2.0, 4),
        "y": round((y0 + y1) / 2.0, 4),
    }


def rendered_label_map(render_semantics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    labels: dict[str, dict[str, Any]] = {}
    for page in render_semantics.get("pages", []):
        for label in merge_words_into_visual_labels(page):
            text = str(label.get("text", "")).strip()
            if not text or text in labels:
                continue
            labels[text] = {
                **label,
                "page_index": int(page.get("page_index", 0)),
                "center": bbox_center_payload(label["bbox"]),
            }
    return labels


def node_label_by_id(spec: dict[str, Any]) -> dict[str, str]:
    return {str(node.get("id")): str(node.get("label", "")) for node in spec.get("nodes", [])}


def symmetry_pair_entries(contract: dict[str, Any]) -> list[tuple[str, str]]:
    pairs = contract.get("pairs") or contract.get("node_pairs") or []
    entries: list[tuple[str, str]] = []
    for pair in pairs:
        if isinstance(pair, dict):
            left = pair.get("left") or pair.get("a") or pair.get("source")
            right = pair.get("right") or pair.get("b") or pair.get("target")
        elif isinstance(pair, (list, tuple)) and len(pair) == 2:
            left, right = pair
        else:
            continue
        if left is not None and right is not None:
            entries.append((str(left), str(right)))
    return entries


def make_symmetry_finding(
    message: str,
    *,
    severity: str = "FAIL",
    subject: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    finding: dict[str, Any] = {
        "rule_id": "P8_SYMMETRY_CONTRACT",
        "severity": severity,
        "message": message,
    }
    if subject is not None:
        finding["subject"] = subject
    if evidence is not None:
        finding["evidence"] = evidence
    return finding


def evaluate_symmetry_contract(spec: dict[str, Any] | None, render_semantics: dict[str, Any] | None) -> dict[str, Any]:
    review = {
        "contract": None,
        "findings": [],
        "mode": None,
    }
    if spec is None:
        review["status"] = "BLOCKED"
        review["findings"].append(make_symmetry_finding("diagram_spec is required for symmetry-contract approval"))
        return review
    try:
        contract = normalize_symmetry_contract(spec.get("symmetry_contract"))
    except SystemExit as exc:
        review["status"] = "BLOCKED"
        review["findings"].append(make_symmetry_finding(str(exc)))
        return review
    review["contract"] = contract
    status = contract["status"]
    if status in {"not_required", "intentionally_asymmetric"}:
        review["status"] = "PASS"
        review["mode"] = status
        return review
    if render_semantics is None:
        review["status"] = "BLOCKED"
        review["findings"].append(make_symmetry_finding("render semantics are required for required symmetry checking"))
        return review

    mode = contract.get("mode", "mirror_vertical_axis")
    tolerance = float(contract.get("tolerance_pt", DEFAULT_SYMMETRY_TOLERANCE_PT))
    pairs = symmetry_pair_entries(contract)
    if not pairs:
        review["status"] = "BLOCKED"
        review["mode"] = mode
        review["findings"].append(
            make_symmetry_finding("required symmetry_contract needs explicit node pair mappings")
        )
        return review

    labels_by_id = node_label_by_id(spec)
    rendered = rendered_label_map(render_semantics)
    pair_evidence: list[dict[str, Any]] = []
    midpoint_values: list[float] = []
    for left_id, right_id in pairs:
        left_label = labels_by_id.get(left_id, left_id)
        right_label = labels_by_id.get(right_id, right_id)
        left_rendered = rendered.get(left_label)
        right_rendered = rendered.get(right_label)
        if left_rendered is None or right_rendered is None:
            review["findings"].append(
                make_symmetry_finding(
                    "required symmetry pair could not be matched to rendered labels",
                    subject=f"{left_id}:{right_id}",
                    evidence={"left_label": left_label, "right_label": right_label},
                )
            )
            continue
        left_center = left_rendered["center"]
        right_center = right_rendered["center"]
        if mode == "mirror_vertical_axis":
            midpoint_values.append((left_center["x"] + right_center["x"]) / 2.0)
            delta = abs(left_center["y"] - right_center["y"])
            axis_delta = 0.0
        elif mode == "mirror_horizontal_axis":
            midpoint_values.append((left_center["y"] + right_center["y"]) / 2.0)
            delta = abs(left_center["x"] - right_center["x"])
            axis_delta = 0.0
        elif mode == "row_alignment":
            delta = abs(left_center["y"] - right_center["y"])
            axis_delta = 0.0
        elif mode == "column_alignment":
            delta = abs(left_center["x"] - right_center["x"])
            axis_delta = 0.0
        else:
            width_delta = abs(float(left_rendered["bbox"]["width"]) - float(right_rendered["bbox"]["width"]))
            height_delta = abs(float(left_rendered["bbox"]["height"]) - float(right_rendered["bbox"]["height"]))
            delta = max(width_delta, height_delta)
            axis_delta = 0.0
        pair_evidence.append(
            {
                "left": {"id": left_id, "label": left_label, "center": left_center, "bbox": left_rendered["bbox"]},
                "right": {"id": right_id, "label": right_label, "center": right_center, "bbox": right_rendered["bbox"]},
                "delta_pt": round(delta, 4),
            }
        )
        if delta > tolerance:
            review["findings"].append(
                make_symmetry_finding(
                    f"required symmetry pair exceeds tolerance for {mode}",
                    subject=f"{left_id}:{right_id}",
                    evidence={"delta_pt": round(delta, 4), "tolerance_pt": tolerance},
                )
            )

    if midpoint_values and mode in {"mirror_vertical_axis", "mirror_horizontal_axis"}:
        axis = sum(midpoint_values) / len(midpoint_values)
        max_axis_delta = max(abs(value - axis) for value in midpoint_values)
        review["axis_pt"] = round(axis, 4)
        if max_axis_delta > tolerance:
            review["findings"].append(
                make_symmetry_finding(
                    f"symmetry pair midpoints disagree on the {mode} axis",
                    evidence={"max_axis_delta_pt": round(max_axis_delta, 4), "tolerance_pt": tolerance},
                )
            )

    review["mode"] = mode
    review["pairs"] = pair_evidence
    review["status"] = "FAIL" if review["findings"] else "PASS"
    return review


def blocked_review_status(*reports: dict[str, Any]) -> str | None:
    for report in reports:
        status = report.get("review_status")
        if status in {"BLOCKED_INPUT", "BLOCKED_ENVIRONMENT", "UNSUPPORTED_FAMILY", "TOOL_ERROR"}:
            return str(status)
    return None


def run_approve_report(manifest_path: Path, work_dir: Path) -> tuple[dict[str, Any], int]:
    manifest = load_manifest(manifest_path)
    report = base_semantic_report(manifest)
    report["strict_approval_version"] = STRICT_APPROVAL_VERSION
    report["approval_command"] = "approve"

    standalone_tex = abs_path(manifest.get("standalone_tex"))
    if standalone_tex is None or not standalone_tex.is_file():
        report["review_status"] = "BLOCKED_INPUT"
        report["static_status"] = "BLOCKED"
        report["compile_status"] = "BLOCKED"
        report["final_verdict"] = "BLOCKED"
        report["warnings"].append("standalone_tex is required for strict approval")
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 3

    static_result = check_file(standalone_tex)
    report["static_status"] = "PASS" if static_preflight_pass(static_result) else "FAIL"
    report["rule_hits"].extend(static_result["rule_hits"])
    if report["static_status"] == "FAIL":
        report["review_status"] = "COMPLETE"
        report["final_verdict"] = "NEEDS_REVISION"
        report["semantic_verdict"] = "REJECTED"
        report["warnings"].append("strict approval stopped after static preflight failure")
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 1

    design_report, design_exit = run_verify_design_report(manifest_path, work_dir)
    report["design_status"] = design_report.get("design_status", "SKIPPED")
    report["design_review"] = design_report.get("design_review", report["design_review"])
    report["mismatches"].extend(design_report.get("mismatches", []))
    report["mismatch_codes"].extend(design_report.get("mismatch_codes", []))
    report["warnings"].extend(design_report.get("warnings", []))
    report["rule_hits"].extend(design_report.get("rule_hits", []))
    if report["design_status"] == "BLOCKED":
        report["review_status"] = "BLOCKED_INPUT"
        report["final_verdict"] = "BLOCKED"
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 3
    if design_exit != 0:
        report["review_status"] = "COMPLETE"
        report["final_verdict"] = "NEEDS_REVISION"
        report["semantic_verdict"] = "NEEDS_REVISION"
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 1

    compile_result = compile_tex(standalone_tex, svg=False)
    report["compile"] = {
        "status": compile_result["status"],
        "exit_code": compile_result["exit_code"],
        "pdf": compile_result.get("pdf"),
    }
    if compile_result["status"] == "BLOCKED_ENVIRONMENT":
        report["review_status"] = "BLOCKED_ENVIRONMENT"
        report["compile_status"] = "BLOCKED"
        report["final_verdict"] = "BLOCKED"
        report["warnings"].append(str(compile_result.get("message", "compile dependency unavailable")))
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 5
    if compile_result["exit_code"] != 0:
        report["review_status"] = "COMPLETE"
        report["compile_status"] = "FAIL"
        report["final_verdict"] = "NEEDS_REVISION"
        report["warnings"].append("latex compilation failed during strict approval")
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 1
    report["compile_status"] = "PASS"

    refresh_manifest_artifact_hashes(manifest)
    dump_json(manifest_path, manifest)

    visual_report, visual_exit = run_review_visual_report(manifest_path, work_dir)
    semantic_report, semantic_exit = run_verify_semantic_report(manifest_path, work_dir)
    report["visual_status"] = visual_report.get("visual_status", "SKIPPED")
    report["overlap_status"] = visual_report.get("overlap_status", report["visual_status"])
    report["semantic_status"] = semantic_report.get("semantic_status", "SKIPPED")
    report["semantic_verdict"] = semantic_report.get("semantic_verdict")
    report["supported_family"] = semantic_report.get("supported_family", False)
    report["mismatches"].extend(semantic_report.get("mismatches", []))
    report["mismatch_codes"].extend(semantic_report.get("mismatch_codes", []))
    report["warnings"].extend(visual_report.get("warnings", []))
    report["warnings"].extend(semantic_report.get("warnings", []))
    report["rule_hits"].extend(visual_report.get("rule_hits", []))
    report["rule_hits"].extend(semantic_report.get("rule_hits", []))
    report["visual_review"] = visual_report.get("visual_review", report["visual_review"])
    if semantic_report.get("evidence", {}).get("recovered") is not None:
        report["evidence"]["recovered"] = semantic_report["evidence"]["recovered"]
    if visual_report.get("evidence", {}).get("render_semantics") is not None:
        report["evidence"]["render_semantics"] = visual_report["evidence"]["render_semantics"]

    render_semantics = None
    render_path = abs_path(manifest.get("render_semantics"))
    if render_path and render_path.is_file():
        render_semantics = load_render_semantics(render_path)
    spec = None
    spec_path = abs_path(manifest.get("diagram_spec"))
    if spec_path and spec_path.is_file():
        spec = load_json(spec_path)
    symmetry_review = evaluate_symmetry_contract(spec, render_semantics)
    report["symmetry_review"] = symmetry_review
    report["symmetry_status"] = symmetry_review.get("status", "BLOCKED")
    if report["symmetry_status"] in {"FAIL", "BLOCKED"}:
        report["rule_hits"].append(
            make_rule_hit("P8_SYMMETRY_CONTRACT", STATIC_RULES["P8_SYMMETRY_CONTRACT"])
        )

    blocked_status = blocked_review_status(visual_report, semantic_report)
    if blocked_status is not None:
        report["review_status"] = blocked_status
        report["final_verdict"] = "BLOCKED"
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, visual_exit if visual_exit not in {0, 1} else semantic_exit
    if report["symmetry_status"] == "BLOCKED":
        report["review_status"] = "BLOCKED_INPUT"
        report["final_verdict"] = "BLOCKED"
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 3

    if visual_exit == 0 and semantic_exit == 0 and design_exit == 0 and report["symmetry_status"] == "PASS":
        report["review_status"] = "COMPLETE"
        report["final_verdict"] = "APPROVED"
        report["semantic_verdict"] = "APPROVED"
        refresh_manifest_artifact_hashes(manifest)
        dump_json(manifest_path, manifest)
        report["evidence"]["artifact_hashes"] = manifest["artifact_hashes"]
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 0

    report["review_status"] = "COMPLETE"
    report["final_verdict"] = "NEEDS_REVISION"
    if report["semantic_verdict"] is None or report["semantic_verdict"] == "APPROVED":
        report["semantic_verdict"] = "NEEDS_REVISION"
    finalized = finalize_report(report)
    write_semantic_report(manifest, finalized)
    return finalized, 1


def command_doctor() -> int:
    required_files = [
        SCRIPT_DIR / "requirements-semantic-verifier.txt",
        SCRIPT_DIR / "pdf_extract.py",
        SCRIPT_DIR / "family_verifiers.py",
        SCRIPT_DIR / "sage_graph_backend.py",
        SCHEMA_DIR / "figure-contract.schema.json",
        SCHEMA_DIR / "figure-design.schema.json",
        SCHEMA_DIR / "diagram.schema.json",
        SCHEMA_DIR / "figure-brief.schema.json",
        SCHEMA_DIR / "render-semantics.schema.json",
        SCHEMA_DIR / "semantic-review.schema.json",
        CHECKS_DIR / "prevention-rules.md",
        CHECKS_DIR / "review-rules.md",
        CHECKS_DIR / "tikz-prevention.md",
        CHECKS_DIR / "tikz-measurement.md",
        STYLES_DIR / "tikz_palette.tex",
        STYLES_DIR / "tikz_styles.tex",
        TEMPLATES_DIR / "README.md",
    ]
    assets: list[dict[str, str]] = []
    missing_required = False
    for path in required_files:
        status = "OK" if path.is_file() else "MISSING"
        assets.append({"path": str(path), "status": status})
        if status != "OK":
            missing_required = True

    tools: list[dict[str, Any]] = []
    for tool in ("python", "latexmk", "pdflatex"):
        resolved = resolve_tool(tool)
        status = "OK" if resolved else "MISSING"
        entry: dict[str, Any] = {"name": tool, "status": status}
        if resolved:
            entry["path"] = resolved
            if tool == "python":
                entry["probe"] = probe_tool(resolved, ["--version"])
            else:
                entry["probe"] = probe_tool(resolved, ["--version"])
                if entry["probe"].get("probe_status") != "OK":
                    status = "MISSING"
                    entry["status"] = "MISSING"
        tools.append(entry)
        if status != "OK":
            missing_required = True
    dvisvgm_path = resolve_tool("dvisvgm")
    dvisvgm_entry: dict[str, Any] = {
        "name": "dvisvgm",
        "status": "OK" if dvisvgm_path else "MISSING",
        **({"path": dvisvgm_path} if dvisvgm_path else {}),
        "optional": True,
    }
    if dvisvgm_path:
        dvisvgm_entry["probe"] = probe_tool(dvisvgm_path, ["--version"])
        if dvisvgm_entry["probe"].get("probe_status") != "OK":
            dvisvgm_entry["status"] = "MISSING"
    tools.append(dvisvgm_entry)

    semantic_deps = semantic_dependency_report()
    graph_backend = sagemath_backend_status()
    report = {
        "platform": PLATFORM_NAME,
        "status": "OK"
        if not missing_required and semantic_deps["ready"] and graph_backend["ready"]
        else "BLOCKED_ENVIRONMENT",
        "assets": assets,
        "tools": tools,
        "semantic_dependencies": semantic_deps,
        "graph_backend": graph_backend,
        "contracts": {
            "verbs": list(CLI_VERBS),
            "static_rule_ids": list(STATIC_RULES.keys()),
            "visual_review_pass_ids": list(VISUAL_REVIEW_PASS_IDS),
            "manifest_freshness_fields": list(MANIFEST_FRESHNESS_FIELDS),
            "graph_mode_values": list(GRAPH_MODE_VALUES),
            "graph_route_statuses": list(GRAPH_ROUTE_STATUSES),
            "report_fields": list(SEMANTIC_REPORT_FIELDS),
            "render_semantics_schema_version": RENDER_SEMANTICS_SCHEMA_VERSION,
            "render_semantics_extractor_version": RENDER_SEMANTICS_EXTRACTOR_VERSION,
        },
    }
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report["status"] == "OK" else 1


def command_contract(args: argparse.Namespace) -> int:
    out_path = abs_path(args.out)
    assert out_path is not None
    figure_id = ensure_figure_id(getattr(args, "figure_id", None))
    request = (getattr(args, "request", None) or "").strip()
    title = (getattr(args, "title", None) or "").strip()
    purpose = (getattr(args, "purpose", None) or "").strip()
    content_requirements = list(getattr(args, "content_requirement", None) or ([request] if request else []))
    contract = load_or_infer_contract(
        args,
        figure_id=figure_id,
        request=request,
        title=title,
        purpose=purpose,
        source_ids=list(getattr(args, "source_id", None) or []),
        content_requirements=content_requirements,
        requested_family=getattr(args, "diagram_family", None),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dump_json(out_path, contract)
    print(f"WROTE\t{out_path}")
    return 0


def command_design(args: argparse.Namespace) -> int:
    out_path = abs_path(args.out)
    assert out_path is not None
    figure_id = ensure_figure_id(getattr(args, "figure_id", None))
    if args.brief:
        brief_path = abs_path(args.brief)
        assert brief_path is not None
        brief = load_json(brief_path)
        ensure_brief_contract(brief, args)
        ensure_brief_design(brief, args)
        design = brief.get("semantic_design")
        if design is None:
            design = infer_figure_design(
                contract=brief["semantic_contract"],
                caption=str(brief.get("caption") or ""),
            )
    else:
        request = (getattr(args, "request", None) or "").strip()
        title = (getattr(args, "title", None) or "").strip()
        purpose = (getattr(args, "purpose", None) or "").strip()
        caption = (getattr(args, "caption", None) or "").strip()
        content_requirements = list(getattr(args, "content_requirement", None) or ([request] if request else []))
        contract = load_or_infer_contract(
            args,
            figure_id=figure_id,
            request=request,
            title=title,
            purpose=purpose,
            source_ids=list(getattr(args, "source_id", None) or []),
            content_requirements=content_requirements,
            requested_family=getattr(args, "diagram_family", None),
        )
        if getattr(args, "source_tex", None):
            source_prose, _evidence = read_context_inputs(args)
        else:
            source_prose = ""
        design = infer_figure_design(contract=contract, caption=caption, source_prose=source_prose)
    design = normalize_figure_design(design, figure_id=design.get("figure_id", figure_id))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dump_json(out_path, design)
    print(f"WROTE\t{out_path}")
    return 0


def command_spec(args: argparse.Namespace) -> int:
    out_path = abs_path(args.out)
    assert out_path is not None
    if args.brief:
        brief_path = abs_path(args.brief)
        assert brief_path is not None
        brief = load_json(brief_path)
        run_id = normalize_run_id(getattr(args, "run_id", None))
        out_dir = resolve_output_dir(
            args,
            run_id=run_id,
            brief_output_dir=brief.get("output_dir"),
            fallback_parent=out_path.parent,
        )
        brief["output_dir"] = str(out_dir)
        ensure_brief_contract(brief, args)
        ensure_brief_design(brief, args)
    else:
        brief, out_dir, _run_id = bootstrap_brief(args, fallback_parent=out_path.parent)
        ensure_brief_design(brief, args)
    validate_brief(brief)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = spec_from_brief(brief)
    validate_spec(spec)
    brief_out_path = out_dir / f"{brief['figure_id']}.figure-brief.json"
    contract_out_path = out_dir / f"{brief['figure_id']}.figure-contract.json"
    design_out_path = out_dir / f"{brief['figure_id']}.figure-design.json"
    dump_json(brief_out_path, brief)
    dump_json(contract_out_path, brief["semantic_contract"])
    if brief.get("semantic_design") is not None:
        dump_json(design_out_path, brief["semantic_design"])
    dump_json(out_path, spec)
    print(f"WROTE\t{brief_out_path}")
    print(f"WROTE\t{contract_out_path}")
    if brief.get("semantic_design") is not None:
        print(f"WROTE\t{design_out_path}")
    print(f"WROTE\t{out_path}")
    return 0


def build_render_manifest(
    *,
    run_id: str,
    out_dir: Path,
    basename: str,
    brief_path: Path,
    standalone_path: Path,
    snippet_path: Path,
    spec_out_path: Path,
    contract_path: Path | None,
    design_path: Path | None,
    brief: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any]:
    graph_routing = spec.get("graph_routing", {}) if spec.get("diagram_family") == "graph" else {}
    return {
        "run_id": run_id,
        "run_root": str(out_dir),
        "work_dir": str(out_dir),
        "figure_id": brief["figure_id"],
        "source_ids": brief["source_ids"],
        "diagram_family": spec["diagram_family"],
        "figure_brief": str(brief_path),
        "figure_contract": str(contract_path) if contract_path else None,
        "figure_design": str(design_path) if design_path else None,
        "standalone_tex": str(standalone_path),
        "figure_tex": str(snippet_path),
        "diagram_spec": str(spec_out_path),
        "pdf": str(standalone_path.with_suffix(".pdf")),
        "svg": str(standalone_path.with_suffix(".svg")),
        "source_hash": None,
        "source_mtime": None,
        "extracted_from": None,
        "freshness_status": "not_applicable",
        "render_semantics": str(out_dir / f"{basename}.render-semantics.json"),
        "semantic_review": str(out_dir / f"{basename}.semantic-review.json"),
        "semantic_target_present": True,
        "approval_contract_version": STRICT_APPROVAL_VERSION,
        "artifact_hashes": {},
        "graph_mode_requested": brief.get("graph_mode", "auto") if spec.get("diagram_family") == "graph" else None,
        "graph_route_status": graph_routing.get("route_status"),
        "graph_route_reason": graph_routing.get("route_reason"),
        "graph_backend_used": graph_routing.get("backend_used"),
    }


def command_render(args: argparse.Namespace) -> int:
    spec_path = abs_path(args.spec) if args.spec else None
    if args.brief:
        brief_path = abs_path(args.brief)
        assert brief_path is not None
        brief = load_json(brief_path)
        run_id = normalize_run_id(getattr(args, "run_id", None))
        out_dir = resolve_output_dir(args, run_id=run_id, brief_output_dir=brief.get("output_dir"))
        brief["output_dir"] = str(out_dir)
        ensure_brief_contract(brief, args)
        ensure_brief_design(brief, args)
    else:
        brief, out_dir, run_id = bootstrap_brief(args)
        ensure_brief_design(brief, args)
    validate_brief(brief)
    spec = load_json(spec_path) if spec_path else spec_from_brief(brief)
    validate_spec(spec)

    out_dir.mkdir(parents=True, exist_ok=True)

    figure_id = brief["figure_id"]
    basename = args.basename or figure_id
    brief_out_path = out_dir / f"{figure_id}.figure-brief.json"
    standalone_path = out_dir / f"{basename}.standalone.tex"
    snippet_path = out_dir / f"{basename}.figure.tex"
    spec_out_path = out_dir / f"{basename}.diagram.json"
    contract_out_path = out_dir / f"{figure_id}.figure-contract.json"
    design_out_path = out_dir / f"{figure_id}.figure-design.json"
    manifest_path = out_dir / f"{basename}.artifacts.json"

    standalone, snippet = build_outputs(spec, figure_id, brief.get("caption", ""))
    dump_json(brief_out_path, brief)
    dump_json(contract_out_path, brief["semantic_contract"])
    if brief.get("semantic_design") is not None:
        dump_json(design_out_path, brief["semantic_design"])
    write_text(standalone_path, standalone)
    write_text(snippet_path, snippet)
    dump_json(spec_out_path, spec)
    manifest = build_render_manifest(
        run_id=run_id,
        out_dir=out_dir,
        basename=basename,
        brief_path=brief_out_path,
        standalone_path=standalone_path,
        snippet_path=snippet_path,
        spec_out_path=spec_out_path,
        contract_path=contract_out_path,
        design_path=design_out_path if brief.get("semantic_design") is not None else None,
        brief=brief,
        spec=spec,
    )
    dump_json(
        manifest_path,
        refresh_manifest_artifact_hashes(manifest),
    )
    print(f"WROTE\t{brief_out_path}")
    print(f"WROTE\t{contract_out_path}")
    if brief.get("semantic_design") is not None:
        print(f"WROTE\t{design_out_path}")
    print(f"WROTE\t{standalone_path}")
    print(f"WROTE\t{snippet_path}")
    print(f"WROTE\t{spec_out_path}")
    print(f"WROTE\t{manifest_path}")
    return 0


def command_check(args: argparse.Namespace) -> int:
    tex_path = abs_path(args.tex)
    assert tex_path is not None
    result = check_file(tex_path)
    print(json.dumps(result, indent=2))
    return 0 if static_preflight_pass(result) else 1


def command_compile(args: argparse.Namespace) -> int:
    tex_path = abs_path(args.tex)
    assert tex_path is not None
    return run_compile(tex_path, args.svg)


def command_review_visual(args: argparse.Namespace) -> int:
    manifest_path = abs_path(args.artifacts)
    work_dir = abs_path(args.work_dir)
    assert manifest_path is not None
    assert work_dir is not None
    report, exit_code = run_review_visual_report(manifest_path, work_dir)
    print(json.dumps(report, indent=2))
    return exit_code


def command_verify_design(args: argparse.Namespace) -> int:
    manifest_path = abs_path(args.artifacts)
    work_dir = abs_path(args.work_dir)
    assert manifest_path is not None
    assert work_dir is not None
    report, exit_code = run_verify_design_report(manifest_path, work_dir)
    print(json.dumps(report, indent=2))
    return exit_code


def command_verify_semantic(args: argparse.Namespace) -> int:
    manifest_path = abs_path(args.artifacts)
    work_dir = abs_path(args.work_dir)
    assert manifest_path is not None
    assert work_dir is not None
    report, exit_code = run_verify_semantic_report(manifest_path, work_dir)
    print(json.dumps(report, indent=2))
    return exit_code


def command_approve(args: argparse.Namespace) -> int:
    manifest_path = abs_path(args.artifacts)
    work_dir = abs_path(args.work_dir)
    assert manifest_path is not None
    assert work_dir is not None
    report, exit_code = run_approve_report(manifest_path, work_dir)
    print(json.dumps(report, indent=2))
    return exit_code


def command_review(args: argparse.Namespace) -> int:
    if args.semantic or args.artifacts or args.work_dir:
        if not args.artifacts or not args.work_dir:
            raise SystemExit("semantic review requires --artifacts and --work-dir")
        manifest_path = abs_path(args.artifacts)
        work_dir = abs_path(args.work_dir)
        assert manifest_path is not None
        assert work_dir is not None
        report, exit_code = run_approve_report(manifest_path, work_dir)
        report["review_alias"] = "review --semantic delegates to approve; approve is the authoritative final gate"
        print(json.dumps(report, indent=2))
        return exit_code

    tex_path = abs_path(args.tex)
    if tex_path is None:
        raise SystemExit("legacy review requires --tex")
    result = check_file(tex_path)
    review = {
        "verdict": result["verdict"],
        "preflight_only": True,
        "final_verdict": "NOT_APPROVAL",
        "failed_rules": result["failed_rules"],
        "rule_hits": result["rule_hits"],
        "rule_refs": result["rule_refs"],
        "file": str(tex_path),
        "corrective_actions": corrective_actions_for_rules(result["rule_refs"]) if result["rule_refs"] else [],
    }
    print(json.dumps(review, indent=2))
    return 0 if static_preflight_pass(result) else 1


def build_extract_manifest(
    *,
    run_id: str,
    out_dir: Path,
    basename: str,
    figure_id: str,
    standalone_path: Path,
    snippet_path: Path,
    extracted_from: Path,
) -> dict[str, Any]:
    source_hash, source_mtime = source_metadata(extracted_from)
    return {
        "run_id": run_id,
        "run_root": str(out_dir),
        "work_dir": str(out_dir),
        "figure_id": figure_id,
        "source_ids": [],
        "diagram_family": None,
        "figure_brief": None,
        "figure_contract": None,
        "figure_design": None,
        "standalone_tex": str(standalone_path),
        "figure_tex": str(snippet_path),
        "diagram_spec": None,
        "pdf": str(standalone_path.with_suffix(".pdf")),
        "svg": str(standalone_path.with_suffix(".svg")),
        "source_hash": source_hash,
        "source_mtime": source_mtime,
        "extracted_from": str(extracted_from),
        "freshness_status": "fresh_at_extract",
        "render_semantics": str(out_dir / f"{basename}.render-semantics.json"),
        "semantic_review": str(out_dir / f"{basename}.semantic-review.json"),
        "semantic_target_present": False,
        "approval_contract_version": STRICT_APPROVAL_VERSION,
        "artifact_hashes": {},
        "graph_mode_requested": None,
        "graph_route_status": None,
        "graph_route_reason": None,
        "graph_backend_used": None,
    }


def command_extract(args: argparse.Namespace) -> int:
    tex_path = abs_path(args.tex)
    assert tex_path is not None
    run_id = normalize_run_id(getattr(args, "run_id", None))
    out_dir = resolve_output_dir(args, run_id=run_id)
    figure_id = ensure_figure_id(args.figure_id or "F1")
    out_dir.mkdir(parents=True, exist_ok=True)
    env, body = detect_env_block(read_text(tex_path))
    standalone, snippet = outputs_from_existing_env(env, body, figure_id)
    basename = args.basename or figure_id
    standalone_path = out_dir / f"{basename}.standalone.tex"
    snippet_path = out_dir / f"{basename}.figure.tex"
    manifest_path = out_dir / f"{basename}.artifacts.json"
    write_text(standalone_path, standalone)
    write_text(snippet_path, snippet)
    manifest = build_extract_manifest(
        run_id=run_id,
        out_dir=out_dir,
        basename=basename,
        figure_id=figure_id,
        standalone_path=standalone_path,
        snippet_path=snippet_path,
        extracted_from=tex_path,
    )
    dump_json(
        manifest_path,
        refresh_manifest_artifact_hashes(manifest),
    )
    print(f"WROTE\t{standalone_path}")
    print(f"WROTE\t{snippet_path}")
    print(f"WROTE\t{manifest_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=CLI_PROG,
        description=f"{PLATFORM_NAME.capitalize()} runtime helper for structural TikZ generation and staged semantic review.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor")

    def add_intent_contract_args(target: argparse.ArgumentParser) -> None:
        target.add_argument("--contract", help="Existing figure-contract.json to enforce instead of inferring one.")
        target.add_argument("--context-text", action="append", help="Additional task/literature context used for intent inference.")
        target.add_argument("--context-file", action="append", help="File containing additional context used for intent inference.")
        target.add_argument("--source-tex", help="Source TeX file containing nearby manuscript context.")
        target.add_argument("--around-label", help="Optional anchor text for cropping --source-tex context.")
        target.add_argument("--required-object", action="append", help="Object that the figure must visibly contain.")
        target.add_argument("--required-relation", action="append", help="Relation that the figure must visibly encode.")
        target.add_argument(
            "--forbidden-simplification",
            action="append",
            help="Simplification that must fail semantic approval if used.",
        )
        target.add_argument("--notation-requirement", action="append", help="Math label or notation that must be preserved.")
        target.add_argument("--approval-criterion", action="append", help="Additional contract-level approval criterion.")

    def add_bootstrap_args(target: argparse.ArgumentParser) -> None:
        target.add_argument("--request")
        target.add_argument("--title")
        target.add_argument("--purpose")
        target.add_argument("--diagram-family", choices=sorted(SUPPORTED_FAMILIES))
        target.add_argument("--backend-hint")
        target.add_argument("--content-requirement", action="append")
        target.add_argument("--layout-constraint", action="append")
        target.add_argument("--graph-mode", choices=GRAPH_MODE_VALUES)
        target.add_argument("--graph-constructor")
        target.add_argument("--graph-param", action="append")
        target.add_argument("--graph-layout")
        target.add_argument("--show-labels", choices=("true", "false"))
        target.add_argument("--caption")
        target.add_argument("--design", help="Existing figure-design.json to enforce instead of inferring one.")
        target.add_argument("--figure-id")
        target.add_argument("--source-id", action="append")
        target.add_argument("--symmetry", choices=SYMMETRY_CONTRACT_STATUSES)
        target.add_argument("--symmetry-mode", choices=SYMMETRY_MODES)
        target.add_argument("--symmetry-justification")
        target.add_argument("--run-id")
        target.add_argument("--out-dir")
        target.add_argument("--research-root")
        add_intent_contract_args(target)

    contract_parser = subparsers.add_parser("contract")
    contract_parser.add_argument("--out", required=True)
    add_bootstrap_args(contract_parser)

    design_parser = subparsers.add_parser("design")
    design_parser.add_argument("--brief")
    design_parser.add_argument("--out", required=True)
    add_bootstrap_args(design_parser)

    spec_parser = subparsers.add_parser("spec")
    spec_parser.add_argument("--brief")
    spec_parser.add_argument("--out", required=True)
    add_bootstrap_args(spec_parser)

    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--brief")
    render_parser.add_argument("--spec")
    add_bootstrap_args(render_parser)
    render_parser.add_argument("--basename")

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--tex", required=True)

    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("--tex", required=True)
    compile_parser.add_argument("--svg", action="store_true")

    review_visual_parser = subparsers.add_parser("review-visual")
    review_visual_parser.add_argument("--artifacts", required=True)
    review_visual_parser.add_argument("--work-dir", required=True)

    verify_design_parser = subparsers.add_parser("verify-design")
    verify_design_parser.add_argument("--artifacts", required=True)
    verify_design_parser.add_argument("--work-dir", required=True)

    verify_parser = subparsers.add_parser("verify-semantic")
    verify_parser.add_argument("--artifacts", required=True)
    verify_parser.add_argument("--work-dir", required=True)

    approve_parser = subparsers.add_parser("approve")
    approve_parser.add_argument("--artifacts", required=True)
    approve_parser.add_argument("--work-dir", required=True)

    review_parser = subparsers.add_parser("review")
    review_parser.add_argument("--tex")
    review_parser.add_argument("--semantic", action="store_true")
    review_parser.add_argument("--artifacts")
    review_parser.add_argument("--work-dir")

    extract_parser = subparsers.add_parser("extract")
    extract_parser.add_argument("--tex", required=True)
    extract_parser.add_argument("--out-dir")
    extract_parser.add_argument("--basename")
    extract_parser.add_argument("--figure-id")
    extract_parser.add_argument("--run-id")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "doctor":
        return command_doctor()
    if args.command == "contract":
        return command_contract(args)
    if args.command == "design":
        return command_design(args)
    if args.command == "spec":
        return command_spec(args)
    if args.command == "render":
        return command_render(args)
    if args.command == "check":
        return command_check(args)
    if args.command == "compile":
        return command_compile(args)
    if args.command == "review-visual":
        return command_review_visual(args)
    if args.command == "verify-design":
        return command_verify_design(args)
    if args.command == "verify-semantic":
        return command_verify_semantic(args)
    if args.command == "approve":
        return command_approve(args)
    if args.command == "review":
        return command_review(args)
    if args.command == "extract":
        return command_extract(args)
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
