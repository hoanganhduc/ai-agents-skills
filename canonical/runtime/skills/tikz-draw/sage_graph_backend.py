#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
IS_WINDOWS = os.name == "nt"


def detect_platform_name(script_dir: Path) -> str:
    parts = set(script_dir.parts)
    if ".codex" in parts:
        return "codex"
    if ".claude" in parts:
        return "claude"
    if ".deepseek" in parts:
        return "deepseek"
    return "ai-agents-skills"


def default_shared_workspace() -> Path:
    if os.environ.get("AAS_RUNTIME_WORKSPACE"):
        return Path(os.environ["AAS_RUNTIME_WORKSPACE"]).expanduser()
    if IS_WINDOWS:
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))).expanduser()
        return root / "ai-agents-skills" / "runtime" / "workspace"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))).expanduser() / "ai-agents-skills" / "runtime" / "workspace"


PLATFORM_NAME = detect_platform_name(SCRIPT_DIR)
SAGE_WORKSPACE = (
    (Path.home() / ".codex" / "runtime" / "workspace")
    if IS_WINDOWS and PLATFORM_NAME == "codex"
    else (Path.home() / ".claude")
    if IS_WINDOWS and PLATFORM_NAME == "claude"
    else default_shared_workspace()
    if IS_WINDOWS
    else Path("/tmp") / "tikz-draw-sage-runtime" / PLATFORM_NAME
)
SAFE_GRAPH_EXPR = re.compile(r"^(graphs\.[A-Za-z_][A-Za-z0-9_]*\([^\"'=;`]*\)|Graph\([A-Za-z0-9_{}\[\](),:.\s-]*\))$")
PLAIN_GRAPH_CONSTRUCTOR = re.compile(r"^(?:(?:graphs\.)?[A-Za-z_][A-Za-z0-9_]*|Graph)$")

GRAPH_MODE_VALUES = ("auto", "local", "sage")
GRAPH_ROUTE_STATUSES = (
    "BASELINE_GRAPH_PATH",
    "SAGE_ASSISTED_GRAPH_PATH",
    "SAGE_REQUEST_REQUIRED",
    "SAGE_REQUEST_UNSUPPORTED",
    "SAGE_BACKEND_UNAVAILABLE",
    "SAGE_OUTPUT_INVALID",
)
BASELINE_GRAPH_LAYOUTS = ("spring", "circular")
SAGE_ASSISTED_GRAPH_LAYOUTS = ("planar", "tree")
SUPPORTED_GRAPH_LAYOUTS = BASELINE_GRAPH_LAYOUTS + SAGE_ASSISTED_GRAPH_LAYOUTS


def candidate_sage_scripts() -> list[Path]:
    suffixes = (".bat", ".sh") if IS_WINDOWS else (".sh", ".bat")
    candidates: list[Path] = []
    platform_local_root = SCRIPT_DIR.parent / "sagemath"
    fallback_root = (
        Path.home() / ".codex" / "runtime" / "workspace" / "skills" / "sagemath"
        if PLATFORM_NAME == "claude"
        else Path.home() / ".claude" / "skills" / "sagemath"
    )
    for root in (platform_local_root, fallback_root):
        for suffix in suffixes:
            candidates.append(root / f"run_sage{suffix}")
    return candidates


def resolve_sage_script() -> Path:
    for candidate in candidate_sage_scripts():
        if candidate.is_file():
            return candidate
    return candidate_sage_scripts()[0]


def sagemath_backend_status() -> dict[str, Any]:
    script = resolve_sage_script()
    return {
        "platform": PLATFORM_NAME,
        "script": str(script),
        "script_kind": script.suffix.lower().lstrip("."),
        "script_exists": script.is_file(),
        "workspace": str(SAGE_WORKSPACE),
        "ready": script.is_file(),
        "graph_modes": list(GRAPH_MODE_VALUES),
        "baseline_graph_layouts": list(BASELINE_GRAPH_LAYOUTS),
        "sage_assisted_graph_layouts": list(SAGE_ASSISTED_GRAPH_LAYOUTS),
    }


def parse_bool_text(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"invalid graph show-labels value: {value!r}")


def parse_graph_mode(value: Any) -> str:
    if value is None:
        return "auto"
    lowered = str(value).strip().lower()
    if lowered not in GRAPH_MODE_VALUES:
        allowed = ", ".join(GRAPH_MODE_VALUES)
        raise SystemExit(f"invalid graph_mode {value!r}; allowed values: {allowed}")
    return lowered


def route_error(status: str, message: str) -> SystemExit:
    return SystemExit(f"{status}: {message}")


def serialize_graph_param(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(value)
    if isinstance(value, str):
        stripped = value.strip()
        lowered = stripped.lower()
        if lowered in {"true", "false"}:
            return "True" if lowered == "true" else "False"
        if re.fullmatch(r"-?[0-9]+", stripped) or re.fullmatch(r"-?[0-9]+\.[0-9]+", stripped):
            return stripped
        return json.dumps(stripped)
    raise route_error(
        "SAGE_REQUEST_UNSUPPORTED",
        "graph_params only support scalar JSON-like values in the first Sage-assisted slice",
    )


def normalize_graph_constructor(constructor: str, graph_params: Any) -> str:
    cleaned = constructor.strip()
    if not cleaned:
        raise route_error("SAGE_REQUEST_UNSUPPORTED", "graph constructor must not be empty")

    if cleaned.lower().startswith("sage:"):
        cleaned = cleaned.split(":", 1)[1].strip()

    if graph_params is not None and not isinstance(graph_params, (list, tuple)):
        raise route_error(
            "SAGE_REQUEST_UNSUPPORTED",
            "graph_params must be a list when using structured graph_constructor input",
        )

    if SAFE_GRAPH_EXPR.fullmatch(cleaned):
        if graph_params:
            raise route_error(
                "SAGE_REQUEST_UNSUPPORTED",
                "graph_params cannot be combined with a fully formed constructor expression",
            )
        return cleaned

    if "(" in cleaned or ")" in cleaned:
        raise route_error(
            "SAGE_REQUEST_UNSUPPORTED",
            "graph constructor expressions must use constrained forms like graphs.PetersenGraph()",
        )

    if not PLAIN_GRAPH_CONSTRUCTOR.fullmatch(cleaned):
        raise route_error(
            "SAGE_REQUEST_UNSUPPORTED",
            "graph constructor must be a named constructor like JohnsonGraph or graphs.JohnsonGraph",
        )

    if cleaned == "Graph" or cleaned.startswith("graphs."):
        base = cleaned
    else:
        base = f"graphs.{cleaned}"

    rendered_params = ", ".join(serialize_graph_param(value) for value in list(graph_params or []))
    normalized = f"{base}({rendered_params})"
    if not SAFE_GRAPH_EXPR.fullmatch(normalized):
        raise route_error(
            "SAGE_REQUEST_UNSUPPORTED",
            "normalized graph constructor fell outside the constrained Sage constructor surface",
        )
    return normalized


def baseline_graph_query(combined: str) -> dict[str, str] | None:
    if re.search(r"\bpetersen\b", combined, re.IGNORECASE):
        return {
            "constructor": "graphs.PetersenGraph()",
            "default_layout": "circular",
            "family_name": "PetersenGraph",
        }

    match = re.search(r"\b(?:johnson(?:\s+graph)?\s*)?J\((\d+)\s*,\s*(\d+)\)", combined, re.IGNORECASE)
    if match:
        return {
            "constructor": f"graphs.JohnsonGraph({int(match.group(1))}, {int(match.group(2))})",
            "default_layout": "spring",
            "family_name": "JohnsonGraph",
        }
    return None


def extract_graph_query(brief: dict[str, Any]) -> dict[str, Any]:
    title = str(brief.get("title", "")).strip()
    purpose = str(brief.get("purpose", "")).strip()
    graph_request = str(brief.get("graph_request", "")).strip()
    requirements = [str(item).strip() for item in brief.get("content_requirements", []) if str(item).strip()]
    combined = " ".join([title, purpose, graph_request, *requirements]).strip()

    requested_mode = parse_graph_mode(brief.get("graph_mode"))
    constructor_input = brief.get("graph_constructor")
    graph_params = brief.get("graph_params")
    layout_input = brief.get("graph_layout")
    show_labels_input = brief.get("show_labels")

    requirement_constructor: str | None = None
    requirement_layout: str | None = None
    requirement_show_labels: bool | None = None
    for requirement in requirements:
        lowered = requirement.lower()
        if lowered.startswith("sage:"):
            requirement_constructor = requirement.split(":", 1)[1].strip()
        elif lowered.startswith("layout:"):
            requirement_layout = requirement.split(":", 1)[1].strip().lower()
        elif lowered.startswith("show labels:"):
            requirement_show_labels = parse_bool_text(requirement.split(":", 1)[1])

    if show_labels_input is None:
        show_labels = bool(requirement_show_labels) if requirement_show_labels is not None else False
    elif isinstance(show_labels_input, bool):
        show_labels = show_labels_input
    else:
        show_labels = parse_bool_text(str(show_labels_input))

    normalized_layout = str(layout_input or requirement_layout or "").strip().lower() or None
    explicit_constructor = constructor_input or requirement_constructor
    explicit_sage_request = requested_mode == "sage" or bool(re.search(r"\b(?:sage|sagemath)\b", combined, re.IGNORECASE))
    baseline = baseline_graph_query(combined)

    if requested_mode == "local":
        if explicit_constructor:
            raise route_error(
                "SAGE_REQUEST_REQUIRED",
                "graph_mode=local does not allow explicit Sage graph constructors",
            )
        if normalized_layout and normalized_layout not in BASELINE_GRAPH_LAYOUTS:
            raise route_error(
                "SAGE_REQUEST_REQUIRED",
                "baseline graph path only supports spring and circular layouts",
            )

    route_status: str
    route_reason: str
    constructor: str
    family_name: str
    default_layout: str

    if explicit_constructor:
        if requested_mode == "local":
            raise route_error(
                "SAGE_REQUEST_REQUIRED",
                "baseline graph path cannot satisfy an explicit Sage graph constructor request",
            )
        constructor = normalize_graph_constructor(str(explicit_constructor), graph_params)
        family_match = re.match(r"^(?:graphs\.)?([A-Za-z_][A-Za-z0-9_]*)\(", constructor)
        family_name = family_match.group(1) if family_match else constructor
        default_layout = "spring"
        route_status = "SAGE_ASSISTED_GRAPH_PATH"
        route_reason = "explicit Sage graph constructor requested"
    elif baseline is not None:
        constructor = baseline["constructor"]
        family_name = baseline["family_name"]
        default_layout = baseline["default_layout"]
        if explicit_sage_request:
            route_status = "SAGE_ASSISTED_GRAPH_PATH"
            route_reason = "explicit Sage request for a baseline-supported graph"
        elif normalized_layout in SAGE_ASSISTED_GRAPH_LAYOUTS:
            route_status = "SAGE_ASSISTED_GRAPH_PATH"
            route_reason = f"requested graph layout '{normalized_layout}' exceeds the baseline graph path"
        else:
            route_status = "BASELINE_GRAPH_PATH"
            route_reason = "graph request matches the trusted baseline shorthand surface"
    else:
        if requested_mode == "local":
            raise route_error(
                "SAGE_REQUEST_REQUIRED",
                "baseline graph path only supports Petersen and J(n,k) shorthands in the current slice",
            )
        raise route_error(
            "SAGE_REQUEST_UNSUPPORTED",
            "graph request is outside the current baseline shorthand surface; provide a constrained graph_constructor or a 'sage: graphs.<Constructor>(...)' requirement",
        )

    layout = normalized_layout or default_layout
    if layout not in SUPPORTED_GRAPH_LAYOUTS:
        if route_status == "BASELINE_GRAPH_PATH" or requested_mode == "local":
            raise route_error(
                "SAGE_REQUEST_REQUIRED",
                f"layout {layout!r} is outside the baseline graph path; supported baseline layouts: {', '.join(BASELINE_GRAPH_LAYOUTS)}",
            )
        raise route_error(
            "SAGE_REQUEST_UNSUPPORTED",
            f"unsupported Sage-assisted graph layout {layout!r}; supported layouts in the current slice: {', '.join(SUPPORTED_GRAPH_LAYOUTS)}",
        )

    if route_status == "BASELINE_GRAPH_PATH" and layout not in BASELINE_GRAPH_LAYOUTS:
        raise route_error(
            "SAGE_REQUEST_REQUIRED",
            f"layout {layout!r} requires Sage-assisted routing in the current slice",
        )

    return {
        "constructor": constructor,
        "layout": layout,
        "show_labels": show_labels,
        "family_name": family_name,
        "graph_mode_requested": requested_mode,
        "graph_route_status": route_status,
        "graph_route_reason": route_reason,
        "graph_backend_used": "sage",
    }


def run_sage_graph_query(query: dict[str, Any]) -> dict[str, Any]:
    script = resolve_sage_script()
    if not script.is_file():
        raise route_error("SAGE_BACKEND_UNAVAILABLE", f"Sage graph backend script is missing: {script}")
    SAGE_WORKSPACE.mkdir(parents=True, exist_ok=True)

    layout_code = (
        "pos = G.layout(layout=layout_name, seed=0)\n"
        if query["layout"] == "spring"
        else "pos = G.layout(layout=layout_name)\n"
    )
    code = (
        "import json\n"
        "import sage\n"
        f"constructor = {json.dumps(query['constructor'])}\n"
        f"layout_name = {json.dumps(query['layout'])}\n"
        "G = eval(constructor, {'graphs': graphs, 'Graph': Graph}, {})\n"
        f"{layout_code}"
        "vertices = [str(v) for v in G.vertices(sort=True)]\n"
        "edge_list = [[str(u), str(v)] for (u, v, _) in G.edges(sort=True)]\n"
        "positions = {str(v): [float(pos[v][0]), float(pos[v][1])] for v in G.vertices(sort=True)}\n"
        "payload = {\n"
        "    'sage_version': sage.version.version,\n"
        "    'constructor': constructor,\n"
        "    'layout': layout_name,\n"
        "    'order': int(G.order()),\n"
        "    'size': int(G.size()),\n"
        "    'vertices': vertices,\n"
        "    'edges': edge_list,\n"
        "    'positions': positions,\n"
        "    'directed': bool(G.is_directed()),\n"
        "    'multiedges': bool(G.has_multiple_edges()),\n"
        "    'loops': bool(G.has_loops()),\n"
        "    'graph_metadata': {\n"
        "        'order': int(G.order()),\n"
        "        'size': int(G.size()),\n"
        "    },\n"
        "}\n"
        "print(json.dumps(payload))\n"
    )
    env = {
        **dict(os.environ),
        "OPENCLAW_WORKSPACE": str(SAGE_WORKSPACE),
    }
    if script.suffix.lower() == ".bat":
        temp_dir = SAGE_WORKSPACE / "data" / "research" / "sagemath" / "tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".sage",
            prefix="tikz_draw_graph_",
            dir=temp_dir,
            delete=False,
        ) as handle:
            handle.write(code)
            temp_file = Path(handle.name)
        try:
            proc = subprocess.run(
                [str(script), "--file", str(temp_file)],
                text=True,
                capture_output=True,
                env=env,
            )
        finally:
            temp_file.unlink(missing_ok=True)
    else:
        proc = subprocess.run(
            ["bash", str(script), code],
            text=True,
            capture_output=True,
            env=env,
        )
    if proc.returncode != 0:
        raise route_error(
            "SAGE_OUTPUT_INVALID",
            f"Sage graph backend failed with exit code {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}",
        )

    try:
        outer = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise route_error("SAGE_OUTPUT_INVALID", f"Sage graph backend returned non-JSON output: {exc}") from exc

    if isinstance(outer, dict) and outer.get("status") == "ok":
        output = str(outer.get("output", "")).strip()
        if not output:
            raise route_error("SAGE_OUTPUT_INVALID", "Sage graph backend returned empty output")
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            raise route_error("SAGE_OUTPUT_INVALID", f"Sage graph backend returned non-JSON graph output: {exc}") from exc
    elif isinstance(outer, dict) and "status" in outer:
        raise route_error("SAGE_OUTPUT_INVALID", f"Sage graph backend error: {outer.get('message', 'unknown Sage error')}")
    elif isinstance(outer, dict):
        payload = outer
    else:
        raise route_error("SAGE_OUTPUT_INVALID", "Sage graph backend returned a non-object JSON payload")

    required = {
        "sage_version",
        "constructor",
        "layout",
        "order",
        "size",
        "vertices",
        "edges",
        "positions",
        "directed",
        "multiedges",
        "loops",
        "graph_metadata",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise route_error(
            "SAGE_OUTPUT_INVALID",
            f"Sage graph backend output is missing required keys: {', '.join(missing)}",
        )

    if payload["directed"]:
        raise route_error("SAGE_REQUEST_UNSUPPORTED", "the current Sage-assisted graph slice only supports undirected graphs")
    if payload["multiedges"] or payload["loops"]:
        raise route_error(
            "SAGE_REQUEST_UNSUPPORTED",
            "the current Sage-assisted graph slice does not support multiedges or loops",
        )

    payload["show_labels"] = bool(query["show_labels"])
    payload["family_name"] = query["family_name"]
    payload["graph_mode_requested"] = query["graph_mode_requested"]
    payload["graph_route_status"] = query["graph_route_status"]
    payload["graph_route_reason"] = query["graph_route_reason"]
    payload["graph_backend_used"] = query["graph_backend_used"]
    payload["graph_metadata"]["family_name"] = query["family_name"]
    payload["graph_metadata"]["route_status"] = query["graph_route_status"]
    payload["graph_metadata"]["route_reason"] = query["graph_route_reason"]
    return payload
