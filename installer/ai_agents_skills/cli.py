from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
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
from .render import MANAGED_MARKER, canonical_skill_path
from .selectors import (
    artifact_dependency_skills,
    canonical_artifact_name,
    canonical_skill_name,
    resolve_artifacts,
    resolve_skills,
    split_csv,
)
from .state import load_state
from .verify import verify as verify_state


INSTALL_CONFIRMATION_PHRASE = "I understand the installation and uninstall process"


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

    audit = sub.add_parser("audit-system")
    add_selection_args(audit)
    add_conflict_args(audit)
    add_install_mode_args(audit)

    plan = sub.add_parser("plan")
    add_selection_args(plan)
    add_conflict_args(plan)
    add_install_mode_args(plan)

    install = sub.add_parser("install")
    add_selection_args(install)
    add_conflict_args(install)
    add_install_mode_args(install)
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
    rollback.add_argument("--real-system", action="store_true")

    uninstall = sub.add_parser("uninstall")
    uninstall.add_argument("--all", action="store_true")
    uninstall.add_argument("--skill")
    uninstall.add_argument("--skills")
    uninstall.add_argument("--artifact")
    uninstall.add_argument("--artifacts")
    uninstall.add_argument("--apply", action="store_true")
    uninstall.add_argument("--dry-run", action="store_true")
    uninstall.add_argument("--real-system", action="store_true")

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


def add_install_mode_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--install-mode",
        choices=["auto", "symlink", "reference", "copy"],
        default="auto",
        help=(
            "skill installation mode; auto is the default and resolves per "
            "agent, symlink forces links, reference writes thin adapters, copy "
            "writes full files"
        ),
    )


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
    if args.command == "audit-system":
        return audit_system(args, manifests)
    if args.command == "plan":
        plan = make_plan(args, manifests)
        return output(summarize_plan(plan), args)
    if args.command == "install":
        ensure_apply_allowed(args)
        plan = make_plan(args, manifests)
        confirm_install_process_understood(args, plan)
        result = apply_plan(args.root, plan, dry_run=not args.apply)
        return output(result, args)
    if args.command == "verify":
        return verify(args, manifests)
    if args.command == "rollback":
        ensure_apply_allowed(args)
        confirm_lifecycle_process_understood(args, "rollback")
        skills = resolve_skill_filter(args, manifests)
        artifacts = resolve_artifact_filter(args, manifests)
        agents = set(split_csv(args.agents)) if args.agents else None
        dry_run = not args.apply
        result = rollback_artifacts(args.root, args.run, skills, agents, artifacts, dry_run=dry_run)
        return output(result, args)
    if args.command == "uninstall":
        ensure_apply_allowed(args)
        if args.apply and not args.all and not args.skill and not args.skills and not args.artifact and not args.artifacts:
            raise ValueError("applied uninstall requires --all, --skill, --skills, --artifact, or --artifacts")
        skills = resolve_skill_filter(args, manifests)
        artifacts = resolve_artifact_filter(args, manifests)
        agents = set(split_csv(args.agents)) if args.agents else None
        dry_run = not args.apply
        if args.apply:
            preview = uninstall_artifacts(args.root, skills, agents, artifacts, dry_run=True)
            confirm_uninstall_process_understood(args, preview)
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
    result = build_precheck_result(args, manifests)
    if getattr(args, "interactive", False) and not args.json:
        interactive_precheck(result)
    if getattr(args, "save_state", False):
        path = args.root / ".ai-agents-skills" / "precheck.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result["state_file"] = str(path)
    return output(result, args)


def build_precheck_result(args: argparse.Namespace, manifests: dict[str, Any]) -> dict[str, Any]:
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
    ignored = set(split_csv(getattr(args, "ignore", None)))
    skipped = set(split_csv(getattr(args, "skip", None)))
    required = required_dependencies_for(active_skills, manifests) - skipped
    optional = (optional_dependencies_for(active_skills, manifests) - required) - skipped
    results = []
    python_command: str | None = None

    ordered_required = sorted(required, key=lambda item: (item != "python-runtime", item))
    ordered_optional = sorted(optional, key=lambda item: (item != "python-runtime", item))
    for name in ordered_required:
        result = discover_dependency(name, True, manifests, platform, python_command, args.root)
        result.update(dependency_context(name, active_skills, manifests))
        if name == "python-runtime" and result.get("command"):
            python_command = result["command"]
        results.append(result)
    for name in ordered_optional:
        result = discover_dependency(name, False, manifests, platform, python_command, args.root)
        result.update(dependency_context(name, active_skills, manifests))
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
    return {
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
        args.install_mode,
    )


def audit_system(args: argparse.Namespace, manifests: dict[str, Any]) -> int:
    platform = current_platform(args.platform)
    selected, selected_artifacts = resolve_install_selection(args, manifests)
    agent_filter = split_csv(args.agents) if args.agents else None
    agents = detect_agents(args.root, agent_filter)
    precheck_result = build_precheck_result(args, manifests)
    default_plan = build_plan(args.root, manifests, selected, agents, artifacts=selected_artifacts, install_mode=args.install_mode)
    requested_plan = build_plan(
        args.root,
        manifests,
        selected,
        agents,
        args.adopt,
        args.backup_replace,
        args.migrate,
        selected_artifacts,
        args.install_mode,
    )
    adopt_plan = build_plan(args.root, manifests, selected, agents, adopt=True, artifacts=selected_artifacts, install_mode=args.install_mode)
    migrate_plan = build_plan(args.root, manifests, selected, agents, migrate=True, artifacts=selected_artifacts, install_mode=args.install_mode)
    adopt_migrate_plan = build_plan(
        args.root,
        manifests,
        selected,
        agents,
        adopt=True,
        migrate=True,
        artifacts=selected_artifacts,
        install_mode=args.install_mode,
    )
    state = load_state(args.root)
    result = {
        "status": audit_status(default_plan, precheck_result, state),
        "platform": platform,
        "root": str(args.root),
        "selected_skills": selected,
        "selected_artifacts": [f"{kind}:{name}" for kind, name in selected_artifacts],
        "detected_agents": [agent.name for agent in agents],
        "skipped_agents": [
            agent for agent in all_agent_names() if agent not in {target.name for target in agents}
        ],
        "managed_state": {
            "artifact_count": len(state.get("artifacts", [])),
            "run_count": len(state.get("runs", [])),
        },
        "instruction_files": [instruction_file_status(agent.instructions_file, agent.name) for agent in agents],
        "skill_coverage": [skill_coverage(agent, manifests, state) for agent in agents],
        "plan_summaries": {
            "default": plan_counts(default_plan),
            "requested": plan_counts(requested_plan),
            "adopt": plan_counts(adopt_plan),
            "migrate": plan_counts(migrate_plan),
            "adopt_and_migrate": plan_counts(adopt_migrate_plan),
        },
        "missing_by_agent": missing_by_agent(default_plan),
        "dependency_summary": {
            "status": precheck_result["status"],
            "missing_required": [item["dependency"] for item in precheck_result["missing_required"]],
            "degraded_required": [
                item["dependency"]
                for item in precheck_result["dependencies"]
                if item.get("required") and item.get("status") == "degraded"
            ],
            "missing_optional": [item["dependency"] for item in precheck_result["missing_optional"]],
            "manual": [
                item["dependency"]
                for item in precheck_result["dependencies"]
                if item.get("status") == "manual"
            ],
        },
        "recommendations": audit_recommendations(default_plan, precheck_result, state),
    }
    return output(result, args)


def audit_status(plan: dict[str, Any], precheck_result: dict[str, Any], state: dict[str, Any]) -> str:
    if not state.get("artifacts"):
        return "not-managed"
    if precheck_result["status"] in {"missing-required", "degraded"}:
        return "attention-required"
    counts = plan_counts(plan)
    if counts["operations"].get("create") or counts["operations"].get("upsert"):
        return "drift-detected"
    return "ok"


def instruction_file_status(path: Path, agent: str) -> dict[str, Any]:
    if not path.exists():
        return {"agent": agent, "path": str(path), "exists": False, "managed_marker_count": 0, "size": 0}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "agent": agent,
        "path": str(path),
        "exists": True,
        "managed_marker_count": text.count("<!-- ai-agents-skills:"),
        "has_repo_management_notice": "ai-agents-skills:repo-management:start" in text,
        "size": len(text.encode("utf-8")),
    }


def skill_coverage(agent: Any, manifests: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    canonical = set(manifests["skills"]["skills"])
    aliases = manifests["skills"].get("legacy_aliases", {})
    alias_to_canonical = {
        alias: skill
        for skill, skill_aliases in aliases.items()
        for alias in skill_aliases
    }
    names = {
        path.parent.name
        for path in agent.skills_dir.glob("*/SKILL.md")
        if path.is_file()
    } if agent.skills_dir.exists() else set()
    legacy_present = {
        alias_to_canonical[name]: name
        for name in names
        if name in alias_to_canonical
    }
    state = state or {"artifacts": []}
    state_managed = {
        item.get("skill")
        for item in state.get("artifacts", [])
        if item.get("agent") == agent.name
        and item.get("artifact_type") == "skill-file"
        and item.get("managed")
    }
    managed = []
    unmanaged = []
    for name in sorted(names & canonical):
        path = agent.skills_dir / name / "SKILL.md"
        if skill_is_managed(name, path, state_managed):
            managed.append(name)
        else:
            unmanaged.append(name)
    return {
        "agent": agent.name,
        "skills_dir": str(agent.skills_dir),
        "canonical_present": sorted(names & canonical),
        "canonical_missing": sorted(canonical - names),
        "managed_canonical": managed,
        "unmanaged_canonical": unmanaged,
        "legacy_aliases_present": legacy_present,
        "extra_local": sorted(names - canonical - set(alias_to_canonical)),
    }


def skill_is_managed(skill: str, path: Path, state_managed: set[str | None]) -> bool:
    if skill in state_managed:
        return True
    source_path = canonical_skill_path(skill)
    if path.is_symlink() and source_path.exists() and path.resolve() == source_path.resolve():
        return True
    text = path.read_text(encoding="utf-8", errors="replace")
    return MANAGED_MARKER in text


def plan_counts(plan: dict[str, Any]) -> dict[str, Any]:
    operations = Counter(action["operation"] for action in plan["actions"])
    classifications = Counter(action.get("classification") for action in plan["actions"])
    artifact_types = Counter(action.get("artifact_type") for action in plan["actions"])
    return {
        "action_count": len(plan["actions"]),
        "operations": dict(sorted(operations.items())),
        "classifications": dict(sorted(classifications.items(), key=lambda item: str(item[0]))),
        "artifact_types": dict(sorted(artifact_types.items(), key=lambda item: str(item[0]))),
    }


def missing_by_agent(plan: dict[str, Any]) -> dict[str, list[str]]:
    missing: dict[str, set[str]] = defaultdict(set)
    for action in plan["actions"]:
        if action.get("operation") in {"create", "upsert"}:
            missing[action["agent"]].add(action.get("skill", action.get("artifact_name", "")))
    return {agent: sorted(skills) for agent, skills in sorted(missing.items())}


def audit_recommendations(
    plan: dict[str, Any],
    precheck_result: dict[str, Any],
    state: dict[str, Any],
) -> list[str]:
    recommendations = []
    counts = plan_counts(plan)
    if not state.get("artifacts"):
        recommendations.append("No managed artifacts are recorded; install or adopt selected artifacts before relying on verify.")
    if counts["classifications"].get("legacy"):
        recommendations.append("Run a reviewed --migrate dry-run for legacy aliases before canonicalizing names.")
    if counts["classifications"].get("unmanaged"):
        recommendations.append("Review unmanaged canonical files and choose --adopt or --backup-replace per skill.")
    if precheck_result["status"] == "degraded":
        recommendations.append("Run native-substrate checks for degraded Windows executables before treating them as fully verified.")
    if precheck_result["missing_required"]:
        recommendations.append("Install or explicitly skip/ignore missing required dependencies before installing affected skills.")
    return recommendations


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
                    "source_path",
                    "classification",
                    "operation",
                    "install_mode",
                    "fallback_mode",
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


def dependency_context(name: str, skills: list[str], manifests: dict[str, Any]) -> dict[str, Any]:
    specs = manifests["skills"]["skills"]
    required_by = sorted(
        skill for skill in skills if name in specs[skill].get("required_dependencies", [])
    )
    optional_for = sorted(
        skill for skill in skills if name in specs[skill].get("optional_dependencies", [])
    )
    related = sorted(set(required_by) | set(optional_for))
    return {
        "required_by": required_by,
        "optional_for": optional_for,
        "related_skills": related,
    }


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
    if is_real_system_root(args.root) and not getattr(args, "real_system", False):
        raise ValueError("real-system writes require --real-system")


def is_real_system_root(root: Path) -> bool:
    resolved = root.resolve()
    if resolved == Path.home().resolve():
        return True
    parts = [part.lower() for part in resolved.parts]
    if len(parts) >= 3 and parts[-2] == "users":
        return True
    if len(parts) == 3 and parts[1] == "home":
        return True
    return False


def confirm_install_process_understood(args: argparse.Namespace, plan: dict[str, Any]) -> None:
    if not args.apply:
        return
    counts = plan_counts(plan)
    operations = ", ".join(
        f"{name}={count}" for name, count in sorted(counts["operations"].items())
    ) or "none"
    message = f"""Install confirmation required

You are about to apply an ai-agents-skills install for:
  root: {args.root}
  planned actions: {counts["action_count"]}
  planned operations: {operations}

Installation process:
- Run `plan` or `install --dry-run` first to preview what will change.
- `install --apply` writes the planned managed skill files, support files,
  instruction blocks, and selected artifacts.
- Real home-directory writes require both `--apply` and `--real-system`.
- Existing unmanaged files are skipped unless you explicitly selected
  `--adopt`, `--backup-replace`, or `--migrate`.

Uninstall and rollback process:
- `uninstall` is a dry-run by default; applying it requires `--apply` and an
  explicit scope such as `--all`, `--skill`, `--skills`, `--artifact`, or
  `--artifacts`.
- `uninstall` restores installer backups when the installed artifact is
  unchanged, removes repo-managed blocks when user text was added around them,
  and leaves changed user-owned content in place.
- `rollback` uses the installer journal to reverse a recorded run or selected
  managed scope.

To confirm that you understand the installation and uninstall process, type
exactly:
{INSTALL_CONFIRMATION_PHRASE}

Confirmation: """
    print(message, file=sys.stderr, end="")
    answer = sys.stdin.readline()
    if not answer:
        raise ValueError("install confirmation required before applying changes")
    if answer.strip() != INSTALL_CONFIRMATION_PHRASE:
        raise ValueError("install aborted: confirmation phrase did not match")


def confirm_lifecycle_process_understood(args: argparse.Namespace, operation: str) -> None:
    if not args.apply:
        return
    message = f"""{operation.title()} confirmation required

You are about to apply an ai-agents-skills {operation} for:
  root: {args.root}

Process summary:
- `{operation}` is a dry-run by default; `--apply` performs file changes.
- Applied lifecycle commands affect only artifacts recorded in the installer
  state journal, scoped by the command arguments.
- Uninstall restores installer backups when the installed artifact is
  unchanged, removes repo-managed blocks when user text was added around them,
  and preserves changed user-owned content.
- Rollback reverses a recorded run or selected managed scope from the journal.
- Real home-directory writes require `--real-system`.

To confirm that you understand the installation and uninstall process, type
exactly:
{INSTALL_CONFIRMATION_PHRASE}

Confirmation: """
    print(message, file=sys.stderr, end="")
    answer = sys.stdin.readline()
    if not answer:
        raise ValueError(f"{operation} confirmation required before applying changes")
    if answer.strip() != INSTALL_CONFIRMATION_PHRASE:
        raise ValueError(f"{operation} aborted: confirmation phrase did not match")


def confirm_uninstall_process_understood(args: argparse.Namespace, preview: dict[str, Any]) -> None:
    if not args.apply:
        return
    counts = uninstall_counts(preview)
    operations = ", ".join(
        f"{name}={count}" for name, count in sorted(counts["operations"].items())
    ) or "none"
    scope = uninstall_scope_summary(args)
    message = f"""Uninstall confirmation required

You are about to apply an ai-agents-skills uninstall for:
  root: {args.root}
  scope: {scope}
  planned uninstall actions: {counts["action_count"]}
  planned operations: {operations}

What uninstall does:
- `uninstall` reads `.ai-agents-skills/state.json` under the selected root.
- It acts only on recorded managed artifacts that match the requested scope.
- It restores installer backups when the installed artifact is missing or still
  matches the recorded installed signature.
- It deletes installer-created files only when they still match the recorded
  installed signature.
- It removes managed instruction blocks only when the block still matches the
  recorded managed block content.
- It unmanages adopted files without deleting them.
- It skips changed or suspicious artifacts and keeps their state records so you
  can inspect them later.

Safety boundary:
- Dry-run is the default; `--apply` is required to change files.
- Applied uninstall requires an explicit scope such as `--all`, `--skill`,
  `--skills`, `--artifact`, or `--artifacts`.
- Real home-directory writes require both `--apply` and `--real-system`.
- Uninstall does not roll back unrelated user edits outside recorded managed
  artifact paths.

To confirm that you understand the installation and uninstall process, type
exactly:
{INSTALL_CONFIRMATION_PHRASE}

Confirmation: """
    print(message, file=sys.stderr, end="")
    answer = sys.stdin.readline()
    if not answer:
        raise ValueError("uninstall confirmation required before applying changes")
    if answer.strip() != INSTALL_CONFIRMATION_PHRASE:
        raise ValueError("uninstall aborted: confirmation phrase did not match")


def uninstall_counts(preview: dict[str, Any]) -> dict[str, Any]:
    actions = preview.get("actions", [])
    operations = Counter(action.get("operation") for action in actions)
    return {
        "action_count": len(actions),
        "operations": dict(sorted(operations.items(), key=lambda item: str(item[0]))),
    }


def uninstall_scope_summary(args: argparse.Namespace) -> str:
    parts = []
    if getattr(args, "all", False):
        parts.append("all managed artifacts")
    if getattr(args, "skill", None):
        parts.append(f"skill={args.skill}")
    if getattr(args, "skills", None):
        parts.append(f"skills={args.skills}")
    if getattr(args, "artifact", None):
        parts.append(f"artifact={args.artifact}")
    if getattr(args, "artifacts", None):
        parts.append(f"artifacts={args.artifacts}")
    if getattr(args, "agents", None):
        parts.append(f"agents={args.agents}")
    return ", ".join(parts) if parts else "unspecified"


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
