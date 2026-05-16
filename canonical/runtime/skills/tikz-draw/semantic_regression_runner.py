#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
SUITE_PATH = SCRIPT_DIR / "assets" / "examples" / "semantic-regression" / "suite.json"
DEFAULT_OUT_ROOT = Path("/tmp") / "tikz-semantic-regression"
PLATFORM_COMMANDS = {
    "codex": [
        "bash",
        str(Path.home() / ".codex" / "runtime" / "run_skill.sh"),
        "skills/tikz-draw/run_tikz_draw.sh",
    ],
    "claude": [
        "bash",
        str(Path.home() / ".claude" / "skills" / "_run.sh"),
        "skills/tikz-draw/run_tikz_draw.sh",
    ],
}


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def parse_json_output(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else {"value": payload}


def run_command(command: list[str]) -> dict[str, Any]:
    proc = subprocess.run(command, text=True, capture_output=True)
    payload = parse_json_output(proc.stdout)
    return {
        "command": command,
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "payload": payload,
    }


def expected_case_dir(root: Path, fixture_id: str, case_id: str) -> Path:
    return root / fixture_id / case_id


def expected_paths(case_dir: Path, figure_id: str) -> dict[str, Path]:
    basename = figure_id
    return {
        "brief": case_dir / f"{figure_id}.figure-brief.json",
        "standalone_tex": case_dir / f"{basename}.standalone.tex",
        "figure_tex": case_dir / f"{basename}.figure.tex",
        "diagram_spec": case_dir / f"{basename}.diagram.json",
        "artifacts": case_dir / f"{basename}.artifacts.json",
        "render_semantics": case_dir / f"{basename}.render-semantics.json",
        "semantic_review": case_dir / f"{basename}.semantic-review.json",
        "pdf": case_dir / f"{basename}.standalone.pdf",
    }


def render_direct(platform: str, fixture: dict[str, Any], case_dir: Path) -> dict[str, Any]:
    render = fixture["render"]
    figure_id = render["figure_id"]
    command = [
        *PLATFORM_COMMANDS[platform],
        "render",
        "--diagram-family",
        fixture["diagram_family"],
        "--out-dir",
        str(case_dir),
        "--figure-id",
        figure_id,
        "--basename",
        figure_id,
        "--title",
        render["title"],
        "--purpose",
        render["purpose"],
        "--caption",
        render["caption"],
    ]
    for requirement in render.get("content_requirements", []):
        command.extend(["--content-requirement", requirement])
    render_result = run_command(command)
    paths = expected_paths(case_dir, figure_id)
    return {
        "render_result": render_result,
        "paths": {key: str(value) for key, value in paths.items()},
    }


def label_to_node_id(spec: dict[str, Any], label: str) -> str:
    for node in spec.get("nodes", []):
        if node.get("label") == label:
            return str(node["id"])
    raise ValueError(f"node label not found: {label}")


def edge_endpoint_ids(spec: dict[str, Any], operation: dict[str, Any]) -> tuple[str, str]:
    left_label = operation.get("left_label") or operation.get("from_label")
    right_label = operation.get("right_label") or operation.get("to_label")
    if left_label is None or right_label is None:
        raise ValueError(f"edge operation requires left/right or from/to labels: {operation!r}")
    return label_to_node_id(spec, str(left_label)), label_to_node_id(spec, str(right_label))


def edge_matches(spec: dict[str, Any], edge: dict[str, Any], left_id: str, right_id: str, *, undirected: bool) -> bool:
    if undirected or spec.get("diagram_family") == "graph" or (edge.get("metadata") or {}).get("undirected"):
        return {str(edge.get("from")), str(edge.get("to"))} == {left_id, right_id}
    return str(edge.get("from")) == left_id and str(edge.get("to")) == right_id


def apply_operations(base_spec: dict[str, Any], operations: list[dict[str, Any]]) -> dict[str, Any]:
    spec = copy.deepcopy(base_spec)
    for operation in operations:
        op = operation["op"]
        if op == "replace_node_style":
            target = operation["label"]
            for node in spec.get("nodes", []):
                if node.get("label") == target:
                    node["style"] = operation["style"]
                    break
            else:
                raise ValueError(f"replace_node_style target not found: {target}")
            continue
        if op == "replace_node_label":
            target = operation["label"]
            for node in spec.get("nodes", []):
                if node.get("label") == target:
                    node["label"] = operation["new_label"]
                    break
            else:
                raise ValueError(f"replace_node_label target not found: {target}")
            continue
        if op == "reverse_edge":
            from_id = label_to_node_id(spec, operation["from_label"])
            to_id = label_to_node_id(spec, operation["to_label"])
            for edge in spec.get("edges", []):
                if edge.get("from") == from_id and edge.get("to") == to_id:
                    edge["from"], edge["to"] = edge["to"], edge["from"]
                    break
            else:
                raise ValueError(f"reverse_edge target not found: {operation['from_label']} -> {operation['to_label']}")
            continue
        if op == "replace_edge_label":
            from_id = label_to_node_id(spec, operation["from_label"])
            to_id = label_to_node_id(spec, operation["to_label"])
            for edge in spec.get("edges", []):
                if edge.get("from") == from_id and edge.get("to") == to_id:
                    edge["label"] = operation["new_label"]
                    break
            else:
                raise ValueError(
                    f"replace_edge_label target not found: {operation['from_label']} -> {operation['to_label']}"
                )
            continue
        if op == "remove_edge":
            left_id, right_id = edge_endpoint_ids(spec, operation)
            undirected = bool(operation.get("undirected", spec.get("diagram_family") == "graph"))
            for index, edge in enumerate(spec.get("edges", [])):
                if edge_matches(spec, edge, left_id, right_id, undirected=undirected):
                    spec["edges"].pop(index)
                    break
            else:
                raise ValueError(f"remove_edge target not found: {operation!r}")
            continue
        if op == "add_edge":
            left_id, right_id = edge_endpoint_ids(spec, operation)
            undirected = bool(operation.get("undirected", spec.get("diagram_family") == "graph"))
            for edge in spec.get("edges", []):
                if edge_matches(spec, edge, left_id, right_id, undirected=undirected):
                    raise ValueError(f"add_edge target already exists: {operation!r}")
            new_edge = {
                "from": left_id,
                "to": right_id,
                "style": operation.get("style", "graphedge" if spec.get("diagram_family") == "graph" else None),
            }
            if operation.get("label") is not None:
                new_edge["label"] = operation["label"]
            if undirected:
                new_edge["metadata"] = {"undirected": True}
            spec.setdefault("edges", []).append({key: value for key, value in new_edge.items() if value is not None})
            continue
        raise ValueError(f"unsupported mutation op: {op}")
    return spec


def render_mutation(
    platform: str,
    fixture: dict[str, Any],
    mutation: dict[str, Any],
    *,
    base_paths: dict[str, str],
    case_dir: Path,
) -> dict[str, Any]:
    base_spec = read_json(Path(base_paths["diagram_spec"]))
    mutated_spec = apply_operations(base_spec, mutation.get("operations", []))
    figure_id = fixture["render"]["figure_id"]
    mutated_spec_path = case_dir / f"{figure_id}.mutated-source.diagram.json"
    write_json(mutated_spec_path, mutated_spec)

    command = [
        *PLATFORM_COMMANDS[platform],
        "render",
        "--brief",
        base_paths["brief"],
        "--spec",
        str(mutated_spec_path),
        "--out-dir",
        str(case_dir),
        "--basename",
        figure_id,
    ]
    render_result = run_command(command)
    paths = expected_paths(case_dir, figure_id)
    if render_result["exit_code"] == 0:
        write_json(Path(paths["diagram_spec"]), base_spec)
    return {
        "render_result": render_result,
        "paths": {key: str(value) for key, value in paths.items()},
        "mutated_source_spec": str(mutated_spec_path),
    }


def run_case_commands(platform: str, paths: dict[str, str]) -> dict[str, Any]:
    manifest = paths["artifacts"]
    work_dir = str(Path(manifest).parent)
    standalone_tex = paths["standalone_tex"]
    return {
        "check": run_command([*PLATFORM_COMMANDS[platform], "check", "--tex", standalone_tex]),
        "compile": run_command([*PLATFORM_COMMANDS[platform], "compile", "--tex", standalone_tex]),
        "review_visual": run_command([*PLATFORM_COMMANDS[platform], "review-visual", "--artifacts", manifest, "--work-dir", work_dir]),
        "verify_semantic": run_command([*PLATFORM_COMMANDS[platform], "verify-semantic", "--artifacts", manifest, "--work-dir", work_dir]),
        "review_semantic": run_command([*PLATFORM_COMMANDS[platform], "review", "--semantic", "--artifacts", manifest, "--work-dir", work_dir]),
    }


def assert_expected(command_name: str, result: dict[str, Any], expected: dict[str, Any], errors: list[str]) -> None:
    if result["exit_code"] != expected.get("exit_code", result["exit_code"]):
        errors.append(f"{command_name}: expected exit {expected['exit_code']}, got {result['exit_code']}")
    payload = result.get("payload")
    for key in ("verdict", "review_status", "visual_status", "semantic_verdict", "supported_family"):
        if key in expected:
            actual = payload.get(key) if isinstance(payload, dict) else None
            if actual != expected[key]:
                errors.append(f"{command_name}: expected {key}={expected[key]!r}, got {actual!r}")
    if "mismatch_codes" in expected:
        actual_codes = sorted((payload or {}).get("mismatch_codes", []))
        if actual_codes != sorted(expected["mismatch_codes"]):
            errors.append(f"{command_name}: expected mismatch_codes={expected['mismatch_codes']!r}, got {actual_codes!r}")
    if "mismatch_codes_include" in expected:
        actual_codes = set((payload or {}).get("mismatch_codes", []))
        missing = sorted(set(expected["mismatch_codes_include"]) - actual_codes)
        if missing:
            errors.append(f"{command_name}: missing expected mismatch codes {missing!r}")


def evaluate_case(case_id: str, execution: dict[str, Any], expected: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    render_result = execution["render_result"]
    if render_result["exit_code"] != 0:
        errors.append(f"{case_id}: render failed with exit {render_result['exit_code']}")
        return False, errors

    for label in ("brief", "standalone_tex", "figure_tex", "diagram_spec", "artifacts"):
        path = execution["paths"][label]
        if not Path(path).exists():
            errors.append(f"{case_id}: expected artifact missing: {path}")

    command_results = run_case_commands(execution["platform"], execution["paths"])
    execution["commands"] = command_results
    for command_name, command_expected in expected.items():
        assert_expected(command_name, command_results[command_name], command_expected, errors)
    if command_results["compile"]["exit_code"] == 0 and not Path(execution["paths"]["pdf"]).exists():
        errors.append(f"{case_id}: compile succeeded but pdf is missing: {execution['paths']['pdf']}")
    if command_results["review_visual"]["exit_code"] in {0, 1} and not Path(execution["paths"]["render_semantics"]).exists():
        errors.append(
            f"{case_id}: review-visual completed without creating render semantics: {execution['paths']['render_semantics']}"
        )
    if command_results["review_semantic"]["exit_code"] in {0, 1, 4} and not Path(execution["paths"]["semantic_review"]).exists():
        errors.append(
            f"{case_id}: semantic review completed without writing semantic review report: {execution['paths']['semantic_review']}"
        )
    return not errors, errors


def build_case_summary(
    *,
    platform: str,
    fixture_id: str,
    case_id: str,
    family: str,
    kind: str,
    expected: dict[str, Any],
    execution: dict[str, Any],
    passed: bool,
    errors: list[str],
) -> dict[str, Any]:
    return {
        "platform": platform,
        "fixture_id": fixture_id,
        "case_id": case_id,
        "family": family,
        "kind": kind,
        "run_dir": str(Path(execution["paths"]["artifacts"]).parent),
        "expected": expected,
        "paths": execution["paths"],
        "render": execution["render_result"],
        "commands": execution.get("commands", {}),
        "mutated_source_spec": execution.get("mutated_source_spec"),
        "passed": passed,
        "errors": errors,
    }


def run_fixture(platform: str, fixture: dict[str, Any], out_root: Path) -> list[dict[str, Any]]:
    base_case_dir = expected_case_dir(out_root, fixture["id"], "base")
    execution = render_direct(platform, fixture, base_case_dir)
    execution["platform"] = platform
    base_passed, base_errors = evaluate_case("base", execution, fixture["expected"])
    results = [
        build_case_summary(
            platform=platform,
            fixture_id=fixture["id"],
            case_id="base",
            family=fixture["diagram_family"],
            kind="base",
            expected=fixture["expected"],
            execution=execution,
            passed=base_passed,
            errors=base_errors,
        )
    ]

    base_paths = execution["paths"]
    for mutation in fixture.get("mutations", []):
        case_id = mutation["id"]
        mutation_dir = expected_case_dir(out_root, fixture["id"], case_id)
        mutation_exec = render_mutation(platform, fixture, mutation, base_paths=base_paths, case_dir=mutation_dir)
        mutation_exec["platform"] = platform
        mutation_passed, mutation_errors = evaluate_case(case_id, mutation_exec, mutation["expected"])
        results.append(
            build_case_summary(
                platform=platform,
                fixture_id=fixture["id"],
                case_id=case_id,
                family=fixture["diagram_family"],
                kind="mutation",
                expected=mutation["expected"],
                execution=mutation_exec,
                passed=mutation_passed,
                errors=mutation_errors,
            )
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run persistent semantic regression fixtures for tikz-draw.")
    parser.add_argument("--platform", choices=("codex", "claude", "both"), default="both")
    parser.add_argument("--fixture", action="append", help="Run only the named fixture id. Repeatable.")
    parser.add_argument("--out-dir", help="Optional output root for generated regression runs.")
    args = parser.parse_args()

    suite = read_json(SUITE_PATH)
    selected_fixtures = suite["fixtures"]
    if args.fixture:
        wanted = set(args.fixture)
        selected_fixtures = [fixture for fixture in selected_fixtures if fixture["id"] in wanted]
        missing = sorted(wanted - {fixture["id"] for fixture in selected_fixtures})
        if missing:
            raise SystemExit(f"unknown fixture ids: {', '.join(missing)}")

    stamp = now_stamp()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else (DEFAULT_OUT_ROOT / stamp)
    out_dir.mkdir(parents=True, exist_ok=True)

    platforms = ("codex", "claude") if args.platform == "both" else (args.platform,)
    all_results: list[dict[str, Any]] = []
    for platform in platforms:
        platform_root = out_dir / platform
        platform_root.mkdir(parents=True, exist_ok=True)
        for fixture in selected_fixtures:
            all_results.extend(run_fixture(platform, fixture, platform_root))

    passed = sum(1 for item in all_results if item["passed"])
    failed = len(all_results) - passed
    summary = {
        "suite_id": suite["suite_id"],
        "generated_at": datetime.now().isoformat(),
        "suite_path": str(SUITE_PATH),
        "out_dir": str(out_dir),
        "platforms": list(platforms),
        "fixture_filter": args.fixture or [],
        "passed_cases": passed,
        "failed_cases": failed,
        "results": all_results,
    }
    summary_path = out_dir / "summary.json"
    write_json(summary_path, summary)
    print(json.dumps({"status": "OK" if failed == 0 else "FAIL", "summary": str(summary_path), "failed_cases": failed}, indent=2))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
