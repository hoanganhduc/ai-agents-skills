from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .agents import all_agent_names, detect_agents
from .apply import apply_plan
from .discovery import current_platform, discover_python_package, discover_tool
from .docs import generate_docs
from .lifecycle import rollback as rollback_artifacts
from .lifecycle import uninstall as uninstall_artifacts
from .manifest import load_manifests, skill_names
from .planner import build_plan
from .selectors import (
    artifact_dependency_skills,
    canonical_artifact_name,
    canonical_skill_name,
    resolve_artifacts,
    resolve_skills,
    split_csv,
)
from .verify import verify as verify_state


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    argv = normalize_global_flags(argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-agents-skills")
    parser.add_argument("--root", type=Path, default=Path.home(), help="home root to inspect or manage")
    parser.add_argument("--platform", choices=["linux", "windows"], default=None)
    parser.add_argument("--agent", dest="agents", help="single agent filter")
    parser.add_argument("--agents", help="comma-separated agent filter")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-skills")
    sub.add_parser("list-artifacts")
    describe = sub.add_parser("describe")
    describe.add_argument("skill")
    describe_artifact = sub.add_parser("describe-artifact")
    describe_artifact.add_argument("artifact")
    sub.add_parser("generate-docs")

    doctor = sub.add_parser("doctor")
    add_selection_args(doctor)

    precheck = sub.add_parser("precheck")
    add_selection_args(precheck)
    precheck.add_argument("--ignore", help="comma-separated dependency names to ignore")
    precheck.add_argument("--skip", help="comma-separated dependency names to skip for this run")
    precheck.add_argument("--interactive", action="store_true")
    precheck.add_argument("--save-state", action="store_true")

    plan = sub.add_parser("plan")
    add_selection_args(plan)
    add_conflict_args(plan)

    install = sub.add_parser("install")
    add_selection_args(install)
    add_conflict_args(install)
    install.add_argument("--dry-run", action="store_true")
    install.add_argument("--apply", action="store_true")
    install.add_argument("--real-system", action="store_true")

    verify = sub.add_parser("verify")
    verify.add_argument("--skill")
    verify.add_argument("--skills")

    rollback = sub.add_parser("rollback")
    rollback.add_argument("--run")
    rollback.add_argument("--skill")
    rollback.add_argument("--skills")
    rollback.add_argument("--artifact")
    rollback.add_argument("--artifacts")
    rollback.add_argument("--apply", action="store_true")
    rollback.add_argument("--dry-run", action="store_true")

    uninstall = sub.add_parser("uninstall")
    uninstall.add_argument("--all", action="store_true")
    uninstall.add_argument("--skill")
    uninstall.add_argument("--skills")
    uninstall.add_argument("--artifact")
    uninstall.add_argument("--artifacts")
    uninstall.add_argument("--apply", action="store_true")
    uninstall.add_argument("--remove-owned-dependencies", action="store_true")

    return parser


def add_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--skill")
    parser.add_argument("--skills")
    parser.add_argument("--profile")
    parser.add_argument("--exclude")
    parser.add_argument("--no-skills", action="store_true", help="do not select default skills")
    parser.add_argument("--artifact", help="single artifact in type:name form")
    parser.add_argument("--artifacts", help="comma-separated artifacts in type:name form")
    parser.add_argument("--artifact-profile", help="comma-separated artifact profiles")
    parser.add_argument("--exclude-artifact", help="comma-separated artifacts to exclude")
    parser.add_argument("--with-deps", action="store_true", help="include skills required by selected artifacts")


def add_conflict_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--adopt", action="store_true")
    parser.add_argument("--backup-replace", action="store_true")
    parser.add_argument("--migrate", action="store_true")


def run(args: argparse.Namespace) -> int:
    manifests = load_manifests()
    if args.command == "list-skills":
        return output({"skills": skill_names(manifests)}, args)
    if args.command == "list-artifacts":
        return output(list_artifacts(manifests), args)
    if args.command == "describe":
        return describe(args, manifests)
    if args.command == "describe-artifact":
        return describe_artifact(args, manifests)
    if args.command == "generate-docs":
        written = generate_docs(manifests)
        return output({"written": [str(path) for path in written]}, args)
    if args.command == "doctor":
        return doctor(args, manifests)
    if args.command == "precheck":
        return precheck(args, manifests)
    if args.command == "plan":
        plan = make_plan(args, manifests)
        return output(summarize_plan(plan), args)
    if args.command == "install":
        ensure_apply_allowed(args)
        plan = make_plan(args, manifests)
        result = apply_plan(args.root, plan, dry_run=not args.apply)
        return output(result, args)
    if args.command == "verify":
        return verify(args, manifests)
    if args.command == "rollback":
        skills = resolve_skill_filter(args, manifests)
        artifacts = resolve_artifact_filter(args, manifests)
        agents = set(split_csv(args.agents)) if args.agents else None
        dry_run = not args.apply
        result = rollback_artifacts(args.root, args.run, skills, agents, artifacts, dry_run=dry_run)
        return output(result, args)
    if args.command == "uninstall":
        if args.apply and not args.all and not args.skill and not args.skills and not args.artifact and not args.artifacts:
            raise ValueError("applied uninstall requires --all, --skill, --skills, --artifact, or --artifacts")
        skills = resolve_skill_filter(args, manifests)
        artifacts = resolve_artifact_filter(args, manifests)
        agents = set(split_csv(args.agents)) if args.agents else None
        dry_run = not args.apply
        result = uninstall_artifacts(args.root, skills, agents, artifacts, dry_run=dry_run)
        return output(result, args)
    raise ValueError(f"unknown command: {args.command}")


def describe(args: argparse.Namespace, manifests: dict[str, Any]) -> int:
    canonical = canonical_skill_name(args.skill, manifests)
    if canonical is None:
        raise ValueError(f"unknown skill: {args.skill}")
    return output({"skill": canonical, **manifests["skills"]["skills"][canonical]}, args)


def describe_artifact(args: argparse.Namespace, manifests: dict[str, Any]) -> int:
    artifact_type, name = canonical_artifact_name(args.artifact, manifests)
    return output(
        {
            "artifact": f"{artifact_type}:{name}",
            "artifact_type": artifact_type,
            "name": name,
            **manifests["artifacts"]["artifacts"][artifact_type][name],
        },
        args,
    )


def list_artifacts(manifests: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_profiles": sorted(manifests["artifacts"]["artifact_profiles"]),
        "artifacts": [
            f"{artifact_type}:{name}"
            for artifact_type, specs in sorted(manifests["artifacts"]["artifacts"].items())
            for name in sorted(specs)
        ],
    }


def doctor(args: argparse.Namespace, manifests: dict[str, Any]) -> int:
    platform = current_platform(args.platform)
    selected, selected_artifacts = resolve_install_selection(args, manifests)
    agent_filter = split_csv(args.agents) if args.agents else None
    agents = detect_agents(args.root, agent_filter)
    detected_agent_names = {agent.name for agent in agents}
    active_skills = [
        skill for skill in selected
        if detected_agent_names
        and detected_agent_names.intersection(manifests["skills"]["skills"][skill]["supported_agents"])
    ]
    required_tools = sorted(required_tools_for(active_skills, manifests))
    tool_results = {
        name: discover_tool(name, manifests["dependencies"]["tools"][name], platform, args.root)
        for name in required_tools
        if name in manifests["dependencies"]["tools"]
    }
    result = {
        "platform": platform,
        "root": str(args.root),
        "selected_skills": selected,
        "selected_artifacts": [f"{kind}:{name}" for kind, name in selected_artifacts],
        "active_skills": active_skills,
        "detected_agents": [agent.name for agent in agents],
        "skipped_agents": [
            agent for agent in all_agent_names() if agent not in {target.name for target in agents}
        ],
        "tools": tool_results,
    }
    return output(result, args)


def precheck(args: argparse.Namespace, manifests: dict[str, Any]) -> int:
    platform = current_platform(args.platform)
    selected, selected_artifacts = resolve_install_selection(args, manifests)
    agent_filter = split_csv(args.agents) if args.agents else None
    agents = detect_agents(args.root, agent_filter)
    detected_agent_names = {agent.name for agent in agents}
    active_skills = [
        skill for skill in selected
        if detected_agent_names
        and detected_agent_names.intersection(manifests["skills"]["skills"][skill]["supported_agents"])
    ]
    ignored = set(split_csv(args.ignore))
    skipped = set(split_csv(args.skip))
    required = required_dependencies_for(active_skills, manifests) - skipped
    optional = (optional_dependencies_for(active_skills, manifests) - required) - skipped
    results = []
    python_command: str | None = None

    ordered_required = sorted(required, key=lambda item: (item != "python-runtime", item))
    ordered_optional = sorted(optional, key=lambda item: (item != "python-runtime", item))
    for name in ordered_required:
        result = discover_dependency(name, True, manifests, platform, python_command, args.root)
        if name == "python-runtime" and result.get("command"):
            python_command = result["command"]
        results.append(result)
    for name in ordered_optional:
        result = discover_dependency(name, False, manifests, platform, python_command, args.root)
        if name == "python-runtime" and result.get("command"):
            python_command = result["command"]
        results.append(result)

    actionable = [item for item in results if item["dependency"] not in ignored]
    missing_required = [
        item for item in actionable
        if item["required"] and item["status"] in {"missing", "unknown"}
    ]
    degraded_required = [
        item for item in actionable
        if item["required"] and item["status"] == "degraded"
    ]
    status = "ok"
    if missing_required:
        status = "missing-required"
    elif degraded_required:
        status = "degraded"
    result = {
        "status": status,
        "platform": platform,
        "root": str(args.root),
        "selected_skills": selected,
        "selected_artifacts": [f"{kind}:{name}" for kind, name in selected_artifacts],
        "active_skills": active_skills,
        "detected_agents": [agent.name for agent in agents],
        "skipped_agents": [
            agent for agent in all_agent_names() if agent not in {target.name for target in agents}
        ],
        "ignored_dependencies": sorted(ignored),
        "skipped_dependencies": sorted(skipped),
        "dependencies": results,
        "missing_required": missing_required,
        "missing_optional": [
            item for item in actionable
            if not item["required"] and item["status"] in {"missing", "unknown"}
        ],
        "resume_hint": "install missing software, then rerun precheck; use --ignore or --skip for accepted gaps",
    }
    if args.interactive and not args.json:
        interactive_precheck(result)
    if args.save_state:
        path = args.root / ".ai-agents-skills" / "precheck.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result["state_file"] = str(path)
    return output(result, args)


def make_plan(args: argparse.Namespace, manifests: dict[str, Any]) -> dict[str, Any]:
    selected, selected_artifacts = resolve_install_selection(args, manifests)
    agent_filter = split_csv(args.agents) if args.agents else None
    agents = detect_agents(args.root, agent_filter)
    return build_plan(
        args.root,
        manifests,
        selected,
        agents,
        args.adopt,
        args.backup_replace,
        args.migrate,
        selected_artifacts,
    )


def resolve_install_selection(
    args: argparse.Namespace,
    manifests: dict[str, Any],
) -> tuple[list[str], list[tuple[str, str]]]:
    selected_artifacts = resolve_artifacts(args, manifests)
    selected_skills = set(resolve_skills(args, manifests))
    if getattr(args, "with_deps", False):
        selected_skills.update(artifact_dependency_skills(selected_artifacts, manifests))
    return sorted(selected_skills), selected_artifacts


def summarize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "root": plan["root"],
        "action_count": len(plan["actions"]),
        "actions": [
            {
                key: action.get(key)
                for key in (
                    "kind",
                    "agent",
                    "skill",
                    "artifact_id",
                    "artifact_name",
                    "path",
                    "legacy_path",
                    "classification",
                    "operation",
                    "reason",
                    "artifact_type",
                )
            }
            for action in plan["actions"]
        ],
        "skipped_agents": plan["skipped_agents"],
    }


def verify(args: argparse.Namespace, manifests: dict[str, Any]) -> int:
    skills = resolve_skill_filter(args, manifests)
    agents = set(split_csv(args.agents)) if args.agents else None
    result = verify_state(args.root, skills, agents)
    return output(result, args)


def resolve_skill_filter(args: argparse.Namespace, manifests: dict[str, Any]) -> set[str] | None:
    raw = []
    if getattr(args, "skill", None):
        raw.append(args.skill)
    if getattr(args, "skills", None):
        raw.extend(split_csv(args.skills))
    if not raw:
        return None
    resolved = set()
    for item in raw:
        canonical = canonical_skill_name(item, manifests)
        if canonical is None:
            raise ValueError(f"unknown skill: {item}")
        resolved.add(canonical)
    return resolved


def resolve_artifact_filter(args: argparse.Namespace, manifests: dict[str, Any]) -> set[str] | None:
    raw = []
    if getattr(args, "artifact", None):
        raw.append(args.artifact)
    if getattr(args, "artifacts", None):
        raw.extend(split_csv(args.artifacts))
    if not raw:
        return None
    return {
        f"{artifact_type}:{name}"
        for artifact_type, name in (canonical_artifact_name(item, manifests) for item in raw)
    }


def required_tools_for(skills: list[str], manifests: dict[str, Any]) -> set[str]:
    return {
        item for item in required_dependencies_for(skills, manifests)
        if item in manifests["dependencies"]["tools"]
    }


def required_dependencies_for(skills: list[str], manifests: dict[str, Any]) -> set[str]:
    specs = manifests["skills"]["skills"]
    dependencies: set[str] = set()
    for skill in skills:
        dependencies.update(specs[skill].get("required_dependencies", []))
    return dependencies


def optional_dependencies_for(skills: list[str], manifests: dict[str, Any]) -> set[str]:
    specs = manifests["skills"]["skills"]
    dependencies: set[str] = set()
    for skill in skills:
        dependencies.update(specs[skill].get("optional_dependencies", []))
    return dependencies


def discover_dependency(
    name: str,
    required: bool,
    manifests: dict[str, Any],
    platform: str,
    python_command: str | None,
    root: Path,
) -> dict[str, Any]:
    tools = manifests["dependencies"].get("tools", {})
    packages = manifests["dependencies"].get("packages", {})
    if name in tools:
        result = discover_tool(name, tools[name], platform, root)
    elif name in packages:
        package = packages[name]
        if package.get("type") == "python":
            if not python_command:
                python = discover_tool("python-runtime", tools["python-runtime"], platform, root)
                python_command = python.get("command")
            result = discover_python_package(
                name,
                package["module"],
                python_command,
                platform=platform,
                root=root,
                python_candidates=python_candidates_for(package, manifests, platform),
                site_candidates=python_site_candidates_for(package, manifests, platform),
            )
        elif package.get("type") == "tool":
            logical_tool = package.get("logical_tool")
            if logical_tool in tools:
                result = discover_tool(logical_tool, tools[logical_tool], platform, root)
                result["logical_name"] = name
            else:
                result = {
                    "logical_name": name,
                    "status": "unknown",
                    "reason": f"logical tool is not declared: {logical_tool}",
                }
        elif package.get("type") == "remote-service":
            result = {
                "logical_name": name,
                "status": "manual",
                "reason": "remote credentials or account state must be configured outside this repo",
            }
        else:
            result = {"logical_name": name, "status": "unknown", "reason": "unknown package type"}
    else:
        result = {"logical_name": name, "status": "unknown", "reason": "dependency is not declared"}
    result["dependency"] = name
    result["required"] = required
    result["install_hint"] = install_hint(name, result)
    return result


def python_candidates_for(
    package: dict[str, Any],
    manifests: dict[str, Any],
    platform: str,
) -> list[str]:
    candidate_sets = manifests["dependencies"].get("python_candidate_sets", {})
    selected: list[str] = []
    candidate_set = package.get("candidate_set", "default")
    if candidate_set in candidate_sets:
        selected.extend(candidate_sets[candidate_set].get(platform, []))
    explicit = package.get("python_candidates", {})
    if isinstance(explicit, dict):
        selected.extend(explicit.get(platform, []))
    if "python-runtime" not in selected:
        selected.append("python-runtime")
    return selected


def python_site_candidates_for(
    package: dict[str, Any],
    manifests: dict[str, Any],
    platform: str,
) -> list[str]:
    candidate_sets = manifests["dependencies"].get("python_site_candidate_sets", {})
    selected: list[str] = []
    candidate_set = package.get("candidate_set", "default")
    if candidate_set in candidate_sets:
        selected.extend(candidate_sets[candidate_set].get(platform, []))
    explicit = package.get("python_site_candidates", {})
    if isinstance(explicit, dict):
        selected.extend(explicit.get(platform, []))
    return selected


def install_hint(name: str, result: dict[str, Any]) -> str:
    if result.get("status") == "ok":
        return "already available"
    hints = {
        "python-runtime": "install Python 3.10+ with ssl, venv, and pip",
        "powershell-runtime": "install PowerShell 5.1+ or PowerShell 7+",
        "node-runtime": "install Node.js 18+ with npm",
        "wsl-runtime": "enable WSL 2 and install a Linux distro",
        "sage-runtime": "install SageMath locally on Linux or inside WSL on Windows",
        "tex-runtime": "install TeX Live, MiKTeX, or another TeX distribution",
        "ocr-runtime": "install Tesseract OCR if OCR support is needed",
        "calibre-cli": "install Calibre command line tools",
        "git-cli": "install Git",
        "github-cli": "install GitHub CLI and authenticate it if needed",
        "ripgrep-cli": "install ripgrep",
        "nvidia-smi-tool": "install NVIDIA drivers/tools if NVIDIA GPU detection is needed",
        "rocm-smi-tool": "install ROCm tools if AMD GPU detection is needed",
        "docling-python-package": "install the Python package in the selected Python environment: python -m pip install docling",
        "docling-mcp-python-package": "install the Python package in the selected Python environment: python -m pip install docling-mcp",
        "rapidocr-python-package": "install Docling rapidocr support in the selected Python environment: python -m pip install 'docling[rapidocr]'",
        "networkx-python-package": "install the Python package in the selected Python environment: python -m pip install networkx",
        "psutil-python-package": "install the Python package in the selected Python environment: python -m pip install psutil",
        "pymupdf-python-package": "install the Python package in the selected Python environment: python -m pip install pymupdf",
        "pylatexenc-python-package": "install the Python package in the selected Python environment: python -m pip install pylatexenc",
        "shapely-python-package": "install the Python package in the selected Python environment: python -m pip install shapely",
        "svgelements-python-package": "install the Python package in the selected Python environment: python -m pip install svgelements",
        "numpy-python-package": "install the Python package in the selected Python environment: python -m pip install numpy",
        "requests-python-package": "install the Python package in the selected Python environment: python -m pip install requests",
        "feedparser-python-package": "install the Python package in the selected Python environment: python -m pip install feedparser",
        "pyzotero-python-package": "install the Python package in the selected Python environment: python -m pip install pyzotero",
        "pypdf2-python-package": "install the Python package in the selected Python environment: python -m pip install PyPDF2",
        "pdfplumber-python-package": "install the Python package in the selected Python environment: python -m pip install pdfplumber",
        "pytest-python-package": "install the Python package in the selected Python environment: python -m pip install pytest",
        "responses-python-package": "install the Python package in the selected Python environment: python -m pip install responses",
        "google-api-python-client-package": "install the Python package in the selected Python environment: python -m pip install google-api-python-client",
        "google-auth-python-package": "install the Python package in the selected Python environment: python -m pip install google-auth",
        "ebooklib-python-package": "install the Python package in the selected Python environment: python -m pip install ebooklib",
        "modal-python-package": "install the Python package in the selected Python environment: python -m pip install modal",
        "torch-python-package": "install torch in the selected Python environment; use the platform-appropriate PyTorch index",
        "torchvision-python-package": "install torchvision in the selected Python environment; use the platform-appropriate PyTorch index",
        "zotero-credentials": "configure Zotero credentials outside this repo",
        "modal-auth": "configure Modal authentication outside this repo",
    }
    return hints.get(name, "install or configure this dependency, then rerun precheck")


def interactive_precheck(result: dict[str, Any]) -> None:
    missing = [*result.get("missing_required", []), *result.get("missing_optional", [])]
    if not missing:
        print("precheck: no missing required or optional dependencies")
        return
    ignored = set(result.get("ignored_dependencies", []))
    skipped = set(result.get("skipped_dependencies", []))
    for item in missing:
        name = item["dependency"]
        print(f"missing dependency: {name}")
        print(f"hint: {item.get('install_hint')}")
        answer = input("press Enter after installing, 's' to skip this run, 'i' to ignore, 'q' to stop: ").strip().lower()
        if answer == "q":
            break
        if answer == "s":
            skipped.add(name)
        elif answer == "i":
            ignored.add(name)
    result["ignored_dependencies"] = sorted(ignored)
    result["skipped_dependencies"] = sorted(skipped)
    result["resume_hint"] = "rerun precheck to verify newly installed software"


def ensure_apply_allowed(args: argparse.Namespace) -> None:
    if getattr(args, "apply", False) and getattr(args, "dry_run", False):
        raise ValueError("--apply and --dry-run cannot be used together")
    if not args.apply:
        return
    if args.root.resolve() == Path.home().resolve() and not args.real_system:
        raise ValueError("real-system writes require --real-system")


def output(data: Any, args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print_human(data)
    return 0


def normalize_global_flags(argv: list[str]) -> list[str]:
    """Allow selected global flags before or after the subcommand."""
    global_flags: list[str] = []
    remaining: list[str] = []
    value_flags = {"--root", "--platform", "--agents", "--agent"}
    bool_flags = {"--json"}
    i = 0
    while i < len(argv):
        item = argv[i]
        if item in bool_flags:
            global_flags.append(item)
            i += 1
            continue
        if item in value_flags:
            if i + 1 >= len(argv):
                remaining.append(item)
                i += 1
                continue
            global_flags.extend([item, argv[i + 1]])
            i += 2
            continue
        if any(item.startswith(flag + "=") for flag in value_flags):
            global_flags.append(item)
            i += 1
            continue
        remaining.append(item)
        i += 1
    return [*global_flags, *remaining]


def print_human(data: Any) -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                print(f"{key}: {json.dumps(value, indent=2, sort_keys=True)}")
            else:
                print(f"{key}: {value}")
    else:
        print(data)
