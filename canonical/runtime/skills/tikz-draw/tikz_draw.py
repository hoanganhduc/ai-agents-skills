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
from family_verifiers import SUPPORTED_SEMANTIC_FAMILIES, verify_rendered_family
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
PLATFORM_NAME = "claude" if ".claude" in SCRIPT_DIR.parts else "codex"
IS_WINDOWS = os.name == "nt"
CLI_PROG = "run_tikz_draw.bat" if IS_WINDOWS else "run_tikz_draw.sh"

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
    "spec",
    "render",
    "check",
    "compile",
    "review-visual",
    "verify-semantic",
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
}

VISUAL_REVIEW_PASS_IDS = (
    "V1_LABEL_GAP",
    "V2_BOUNDARY_CLEARANCE",
    "V3_PAGE_MARGIN",
    "V4_CURVE_POINT_PLACEMENT",
)

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
    "pdf",
    "svg",
    "source_ids",
    "render_semantics",
    "semantic_review",
    "semantic_target_present",
    *MANIFEST_FRESHNESS_FIELDS,
    *GRAPH_ROUTE_FIELDS,
}

SEMANTIC_REPORT_FIELDS = (
    "review_status",
    "family",
    "static_status",
    "visual_status",
    "compile_status",
    "semantic_status",
    "semantic_verdict",
    "supported_family",
    "mismatches",
    "mismatch_codes",
    "rule_hits",
    "rule_refs",
    "warnings",
    "visual_review",
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
    return home / ".claude" / "data" / "runs" / "tikz-draw" / run_id


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
    if not getattr(args, "diagram_family", None):
        raise SystemExit("render/spec without --brief requires --diagram-family")
    request = (getattr(args, "request", None) or "").strip()
    title = (getattr(args, "title", None) or "").strip()
    purpose = (getattr(args, "purpose", None) or "").strip()
    if not (request or title or purpose):
        raise SystemExit("render/spec without --brief requires --request, --title, or --purpose")

    run_id = normalize_run_id(getattr(args, "run_id", None))
    figure_id = ensure_figure_id(getattr(args, "figure_id", None))
    out_dir = resolve_output_dir(args, run_id=run_id, fallback_parent=fallback_parent)
    final_title = title or infer_title_from_request(request, figure_id)
    final_purpose = purpose or request or f"Illustrate {final_title}."
    brief = {
        "figure_id": figure_id,
        "title": final_title,
        "purpose": final_purpose,
        "source_ids": list(getattr(args, "source_id", None) or []),
        "diagram_family": args.diagram_family,
        "backend_hint": getattr(args, "backend_hint", None),
        "content_requirements": list(getattr(args, "content_requirement", None) or ([request] if request else [])),
        "layout_constraints": list(
            getattr(args, "layout_constraint", None) or ["Fit within text width using adjustbox."]
        ),
        "caption": getattr(args, "caption", None),
        "output_dir": str(out_dir),
    }
    if args.diagram_family == "graph":
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


def spec_from_brief(brief: dict[str, Any]) -> dict[str, Any]:
    family = brief["diagram_family"]
    backend = brief.get("backend_hint") or BACKEND_BY_FAMILY[family]
    requirements = brief.get("content_requirements") or []
    title = brief["title"]
    caption = brief.get("caption", "")
    validation_rules = [
        "document-facing output must use the adjustbox environment with max width textwidth",
        "prefer structural placement over absolute coordinates",
        "avoid bare scale as primary width-fit control",
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
        return {
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
        }

    if family == "tree":
        child_labels = requirements[:4] or ["Assumption A", "Assumption B", "Conclusion"]
        root_id = slugify(title, "root")
        nodes = [{"id": root_id, "label": title, "style": "box"}]
        edges = []
        for index, label in enumerate(child_labels):
            child_id = slugify(label, f"child{index + 1}")
            nodes.append({"id": child_id, "label": label, "style": "box"})
            edges.append({"from": root_id, "to": child_id})
        return {
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
        }

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
        return {
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
        }

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
        return {
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
            "graph_routing": graph_routing,
        }

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
        if label:
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
    node_labels = {node["id"]: tex_escape(node["label"]) for node in spec["nodes"]}
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
        label = tex_escape(edge.get("label", "")) if edge.get("label") is not None else ""
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
    verdict = "APPROVED" if not rule_hits else "NEEDS_REVISION"
    return {
        "verdict": verdict,
        "file": str(tex_path),
        "failed_rules": [hit["message"] for hit in rule_hits],
        "rule_hits": rule_hits,
        "rule_refs": [hit["rule_id"] for hit in rule_hits],
    }


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
    }
    ordered = []
    for rule_id in rule_ids:
        action = actions_by_rule.get(rule_id)
        if action and action not in ordered:
            ordered.append(action)
    return ordered


def run_compile(tex_path: Path, svg: bool) -> int:
    latexmk = resolve_tool("latexmk")
    if not latexmk:
        raise SystemExit("latexmk is not available")
    env = tool_environment()
    cmd = [latexmk, "-pdf", "-interaction=nonstopmode", "-halt-on-error", tex_path.name]
    proc = subprocess.run(cmd, cwd=tex_path.parent, text=True, capture_output=True, env=env)
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        return proc.returncode
    pdf_path = tex_path.with_suffix(".pdf")
    print(f"PDF\t{pdf_path}")
    if svg:
        dvisvgm = resolve_tool("dvisvgm")
        if dvisvgm:
            svg_path = tex_path.with_suffix(".svg")
            svg_cmd = [dvisvgm, "--pdf", str(pdf_path), "-n", "-o", str(svg_path)]
            svg_proc = subprocess.run(svg_cmd, cwd=tex_path.parent, text=True, capture_output=True, env=env)
            if svg_proc.returncode != 0:
                sys.stdout.write(svg_proc.stdout)
                sys.stderr.write(svg_proc.stderr)
                return svg_proc.returncode
            print(f"SVG\t{svg_path}")
        else:
            raise SystemExit("requested --svg but dvisvgm is not available")
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
        "compile_status": "SKIPPED",
        "semantic_status": "SKIPPED",
        "semantic_verdict": None,
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
    return finding


def evaluate_visual_review(render_semantics: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    from shapely.geometry import box  # type: ignore

    findings: list[dict[str, Any]] = []
    warnings: list[str] = []
    for page in render_semantics.get("pages", []):
        page_index = int(page["page_index"])
        page_width = float(page["width"])
        page_height = float(page["height"])
        drawing_boxes: list[tuple[dict[str, Any], Any]] = []
        for drawing in page.get("drawings", []):
            rect = drawing.get("rect")
            if not rect:
                continue
            try:
                drawing_boxes.append((drawing, box(*bbox_tuple(rect))))
            except Exception:
                continue

        for word in page.get("words", []):
            word_box = box(*bbox_tuple(word["bbox"]))
            text = str(word.get("text", "")).strip()
            subject = text or f"word-{word.get('word')}"

            x0, y0, x1, y1 = bbox_tuple(word["bbox"])
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
                    )
                )

            containing_shapes: list[float] = []
            exterior_gaps: list[float] = []
            for drawing, drawing_box in drawing_boxes:
                if drawing_box.area <= word_box.area * 1.05:
                    continue
                if drawing_box.buffer(1e-6).contains(word_box):
                    clearance = word_box.distance(drawing_box.boundary)
                    containing_shapes.append(clearance)
                else:
                    exterior_gaps.append(word_box.distance(drawing_box))

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
                        )
                    )
            if exterior_gaps:
                gap = min(exterior_gaps)
                if 0.0 < gap < VISUAL_THRESHOLDS_PT["V1_LABEL_GAP"]:
                    findings.append(
                        make_visual_finding(
                            "V1_LABEL_GAP",
                            page_index=page_index,
                            subject=subject,
                            measured_pt=gap,
                            threshold_pt=VISUAL_THRESHOLDS_PT["V1_LABEL_GAP"],
                            message=f"text '{subject}' is too close to nearby linework or shapes",
                        )
                    )

    warnings.append("V4_CURVE_POINT_PLACEMENT remains family-specific and is not evaluated in the current semantic-review slice.")
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
        report["rule_hits"].extend(freshness_hits)
        report["warnings"].append("freshness checks failed before visual review")
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 3

    pdf_path = abs_path(manifest.get("pdf"))
    if pdf_path is None or not pdf_path.is_file():
        report["review_status"] = "BLOCKED_INPUT"
        report["visual_status"] = "BLOCKED"
        report["warnings"].append("compiled PDF is required for review-visual")
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        return finalized, 3

    missing_deps = semantic_status_from_missing_dependencies(report)
    if missing_deps is not None:
        report, exit_code = missing_deps
        report["visual_status"] = "BLOCKED"
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
    report["supported_family"] = verification["supported_family"]
    report["mismatches"] = verification["mismatches"]
    report["mismatch_codes"] = verification["mismatch_codes"]
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


def command_doctor() -> int:
    required_files = [
        SCRIPT_DIR / "requirements-semantic-verifier.txt",
        SCRIPT_DIR / "pdf_extract.py",
        SCRIPT_DIR / "family_verifiers.py",
        SCRIPT_DIR / "sage_graph_backend.py",
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

    tools: list[dict[str, str]] = []
    for tool in ("python", "latexmk", "pdflatex"):
        resolved = resolve_tool(tool)
        status = "OK" if resolved else "MISSING"
        entry = {"name": tool, "status": status}
        if resolved:
            entry["path"] = resolved
        tools.append(entry)
        if status != "OK":
            missing_required = True
    dvisvgm_path = resolve_tool("dvisvgm")
    tools.append(
        {
            "name": "dvisvgm",
            "status": "OK" if dvisvgm_path else "MISSING",
            **({"path": dvisvgm_path} if dvisvgm_path else {}),
            "optional": True,
        }
    )

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
    else:
        brief, out_dir, _run_id = bootstrap_brief(args, fallback_parent=out_path.parent)
    validate_brief(brief)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = spec_from_brief(brief)
    brief_out_path = out_dir / f"{brief['figure_id']}.figure-brief.json"
    dump_json(brief_out_path, brief)
    dump_json(out_path, spec)
    print(f"WROTE\t{brief_out_path}")
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
    else:
        brief, out_dir, run_id = bootstrap_brief(args)
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
    manifest_path = out_dir / f"{basename}.artifacts.json"

    standalone, snippet = build_outputs(spec, figure_id, brief.get("caption", ""))
    dump_json(brief_out_path, brief)
    write_text(standalone_path, standalone)
    write_text(snippet_path, snippet)
    dump_json(spec_out_path, spec)
    dump_json(
        manifest_path,
        build_render_manifest(
            run_id=run_id,
            out_dir=out_dir,
            basename=basename,
            brief_path=brief_out_path,
            standalone_path=standalone_path,
            snippet_path=snippet_path,
            spec_out_path=spec_out_path,
            brief=brief,
            spec=spec,
        ),
    )
    print(f"WROTE\t{brief_out_path}")
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
    return 0 if result["verdict"] == "APPROVED" else 1


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


def command_verify_semantic(args: argparse.Namespace) -> int:
    manifest_path = abs_path(args.artifacts)
    work_dir = abs_path(args.work_dir)
    assert manifest_path is not None
    assert work_dir is not None
    report, exit_code = run_verify_semantic_report(manifest_path, work_dir)
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
        manifest = load_manifest(manifest_path)
        report = base_semantic_report(manifest)

        standalone_tex = abs_path(manifest.get("standalone_tex"))
        if standalone_tex is None or not standalone_tex.is_file():
            report["review_status"] = "BLOCKED_INPUT"
            report["static_status"] = "BLOCKED"
            report["warnings"].append("standalone_tex is required for semantic review aggregation")
            print(json.dumps(finalize_report(report), indent=2))
            return 3

        static_result = check_file(standalone_tex)
        report["static_status"] = "PASS" if static_result["verdict"] == "APPROVED" else "FAIL"
        report["rule_hits"].extend(static_result["rule_hits"])
        if report["static_status"] == "FAIL":
            report["review_status"] = "COMPLETE"
            report["semantic_verdict"] = "REJECTED"
            report["warnings"].append("semantic review stopped after static preflight failure")
            report["visual_status"] = "SKIPPED"
            report["compile_status"] = "SKIPPED"
            report["semantic_status"] = "SKIPPED"
            print(json.dumps(finalize_report(report), indent=2))
            return 1

        visual_report, visual_exit = run_review_visual_report(manifest_path, work_dir)
        semantic_report, semantic_exit = run_verify_semantic_report(manifest_path, work_dir)
        report["visual_status"] = visual_report["visual_status"]
        report["semantic_status"] = semantic_report["semantic_status"]
        report["compile_status"] = "PASS" if abs_path(manifest.get("pdf")) and abs_path(manifest.get("pdf")).is_file() else "BLOCKED"
        report["warnings"].extend(visual_report["warnings"])
        report["warnings"].extend(semantic_report["warnings"])
        report["rule_hits"].extend(visual_report["rule_hits"])
        report["rule_hits"].extend(semantic_report["rule_hits"])
        report["visual_review"] = visual_report["visual_review"]
        report["supported_family"] = semantic_report["supported_family"]
        report["mismatches"] = semantic_report["mismatches"]
        report["mismatch_codes"] = semantic_report["mismatch_codes"]
        if semantic_report.get("evidence", {}).get("recovered") is not None:
            report["evidence"]["recovered"] = semantic_report["evidence"]["recovered"]

        if visual_exit == 0 and semantic_exit == 0:
            report["review_status"] = "COMPLETE"
            report["semantic_verdict"] = "APPROVED"
            finalized = finalize_report(report)
            write_semantic_report(manifest, finalized)
            print(json.dumps(finalized, indent=2))
            return 0

        if visual_report["review_status"] in {"BLOCKED_INPUT", "BLOCKED_ENVIRONMENT", "UNSUPPORTED_FAMILY"}:
            report["review_status"] = visual_report["review_status"]
            finalized = finalize_report(report)
            write_semantic_report(manifest, finalized)
            print(json.dumps(finalized, indent=2))
            return visual_exit
        if semantic_report["review_status"] in {"BLOCKED_INPUT", "BLOCKED_ENVIRONMENT", "UNSUPPORTED_FAMILY"}:
            report["review_status"] = semantic_report["review_status"]
            finalized = finalize_report(report)
            write_semantic_report(manifest, finalized)
            print(json.dumps(finalized, indent=2))
            return semantic_exit

        if visual_exit == 1:
            report["review_status"] = "COMPLETE"
            report["semantic_verdict"] = "NEEDS_REVISION"
            finalized = finalize_report(report)
            write_semantic_report(manifest, finalized)
            print(json.dumps(finalized, indent=2))
            return 1

        if semantic_exit == 1:
            report["review_status"] = "COMPLETE"
            report["semantic_verdict"] = semantic_report["semantic_verdict"] or "NEEDS_REVISION"
            finalized = finalize_report(report)
            write_semantic_report(manifest, finalized)
            print(json.dumps(finalized, indent=2))
            return 1

        report["review_status"] = "TOOL_ERROR"
        finalized = finalize_report(report)
        write_semantic_report(manifest, finalized)
        print(json.dumps(finalized, indent=2))
        return 6

    tex_path = abs_path(args.tex)
    if tex_path is None:
        raise SystemExit("legacy review requires --tex")
    result = check_file(tex_path)
    review = {
        "verdict": result["verdict"],
        "failed_rules": result["failed_rules"],
        "rule_hits": result["rule_hits"],
        "rule_refs": result["rule_refs"],
        "file": str(tex_path),
        "corrective_actions": corrective_actions_for_rules(result["rule_refs"]) if result["rule_refs"] else [],
    }
    print(json.dumps(review, indent=2))
    return 0 if review["verdict"] == "APPROVED" else 1


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
    dump_json(
        manifest_path,
        build_extract_manifest(
            run_id=run_id,
            out_dir=out_dir,
            basename=basename,
            figure_id=figure_id,
            standalone_path=standalone_path,
            snippet_path=snippet_path,
            extracted_from=tex_path,
        ),
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

    spec_parser = subparsers.add_parser("spec")
    spec_parser.add_argument("--brief")
    spec_parser.add_argument("--out", required=True)
    spec_parser.add_argument("--request")
    spec_parser.add_argument("--title")
    spec_parser.add_argument("--purpose")
    spec_parser.add_argument("--diagram-family", choices=sorted(SUPPORTED_FAMILIES))
    spec_parser.add_argument("--backend-hint")
    spec_parser.add_argument("--content-requirement", action="append")
    spec_parser.add_argument("--layout-constraint", action="append")
    spec_parser.add_argument("--graph-mode", choices=GRAPH_MODE_VALUES)
    spec_parser.add_argument("--graph-constructor")
    spec_parser.add_argument("--graph-param", action="append")
    spec_parser.add_argument("--graph-layout")
    spec_parser.add_argument("--show-labels", choices=("true", "false"))
    spec_parser.add_argument("--caption")
    spec_parser.add_argument("--figure-id")
    spec_parser.add_argument("--source-id", action="append")
    spec_parser.add_argument("--run-id")
    spec_parser.add_argument("--out-dir")
    spec_parser.add_argument("--research-root")

    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--brief")
    render_parser.add_argument("--spec")
    render_parser.add_argument("--request")
    render_parser.add_argument("--title")
    render_parser.add_argument("--purpose")
    render_parser.add_argument("--diagram-family", choices=sorted(SUPPORTED_FAMILIES))
    render_parser.add_argument("--backend-hint")
    render_parser.add_argument("--content-requirement", action="append")
    render_parser.add_argument("--layout-constraint", action="append")
    render_parser.add_argument("--graph-mode", choices=GRAPH_MODE_VALUES)
    render_parser.add_argument("--graph-constructor")
    render_parser.add_argument("--graph-param", action="append")
    render_parser.add_argument("--graph-layout")
    render_parser.add_argument("--show-labels", choices=("true", "false"))
    render_parser.add_argument("--caption")
    render_parser.add_argument("--figure-id")
    render_parser.add_argument("--source-id", action="append")
    render_parser.add_argument("--run-id")
    render_parser.add_argument("--out-dir")
    render_parser.add_argument("--research-root")
    render_parser.add_argument("--basename")

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--tex", required=True)

    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("--tex", required=True)
    compile_parser.add_argument("--svg", action="store_true")

    review_visual_parser = subparsers.add_parser("review-visual")
    review_visual_parser.add_argument("--artifacts", required=True)
    review_visual_parser.add_argument("--work-dir", required=True)

    verify_parser = subparsers.add_parser("verify-semantic")
    verify_parser.add_argument("--artifacts", required=True)
    verify_parser.add_argument("--work-dir", required=True)

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
    if args.command == "verify-semantic":
        return command_verify_semantic(args)
    if args.command == "review":
        return command_review(args)
    if args.command == "extract":
        return command_extract(args)
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
