from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .agents import all_agent_names, detect_agents
from .apply import apply_plan
from .discovery import current_platform, discover_tool
from .docs import generate_docs
from .lifecycle import rollback as rollback_artifacts
from .lifecycle import uninstall as uninstall_artifacts
from .manifest import load_manifests, skill_names
from .planner import build_plan
from .selectors import canonical_skill_name, resolve_skills, split_csv
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
    describe = sub.add_parser("describe")
    describe.add_argument("skill")
    sub.add_parser("generate-docs")

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--skill")
    doctor.add_argument("--skills")
    doctor.add_argument("--profile")
    doctor.add_argument("--exclude")

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
    rollback.add_argument("--apply", action="store_true")
    rollback.add_argument("--dry-run", action="store_true")

    uninstall = sub.add_parser("uninstall")
    uninstall.add_argument("--all", action="store_true")
    uninstall.add_argument("--skill")
    uninstall.add_argument("--skills")
    uninstall.add_argument("--apply", action="store_true")
    uninstall.add_argument("--remove-owned-dependencies", action="store_true")

    return parser


def add_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--skill")
    parser.add_argument("--skills")
    parser.add_argument("--profile")
    parser.add_argument("--exclude")


def add_conflict_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--adopt", action="store_true")
    parser.add_argument("--backup-replace", action="store_true")
    parser.add_argument("--migrate", action="store_true")


def run(args: argparse.Namespace) -> int:
    manifests = load_manifests()
    if args.command == "list-skills":
        return output({"skills": skill_names(manifests)}, args)
    if args.command == "describe":
        return describe(args, manifests)
    if args.command == "generate-docs":
        written = generate_docs(manifests)
        return output({"written": [str(path) for path in written]}, args)
    if args.command == "doctor":
        return doctor(args, manifests)
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
        agents = set(split_csv(args.agents)) if args.agents else None
        dry_run = not args.apply
        result = rollback_artifacts(args.root, args.run, skills, agents, dry_run=dry_run)
        return output(result, args)
    if args.command == "uninstall":
        skills = resolve_skill_filter(args, manifests)
        agents = set(split_csv(args.agents)) if args.agents else None
        dry_run = not args.apply
        result = uninstall_artifacts(args.root, skills, agents, dry_run=dry_run)
        return output(result, args)
    raise ValueError(f"unknown command: {args.command}")


def describe(args: argparse.Namespace, manifests: dict[str, Any]) -> int:
    canonical = canonical_skill_name(args.skill, manifests)
    if canonical is None:
        raise ValueError(f"unknown skill: {args.skill}")
    return output({"skill": canonical, **manifests["skills"]["skills"][canonical]}, args)


def doctor(args: argparse.Namespace, manifests: dict[str, Any]) -> int:
    platform = current_platform(args.platform)
    selected = resolve_skills(args, manifests)
    agent_filter = split_csv(args.agents) if args.agents else None
    agents = detect_agents(args.root, agent_filter)
    required_tools = sorted(required_tools_for(selected, manifests))
    tool_results = {
        name: discover_tool(name, manifests["dependencies"]["tools"][name], platform)
        for name in required_tools
        if name in manifests["dependencies"]["tools"]
    }
    result = {
        "platform": platform,
        "root": str(args.root),
        "selected_skills": selected,
        "detected_agents": [agent.name for agent in agents],
        "skipped_agents": [
            agent for agent in all_agent_names() if agent not in {target.name for target in agents}
        ],
        "tools": tool_results,
    }
    return output(result, args)


def make_plan(args: argparse.Namespace, manifests: dict[str, Any]) -> dict[str, Any]:
    selected = resolve_skills(args, manifests)
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
    )


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


def required_tools_for(skills: list[str], manifests: dict[str, Any]) -> set[str]:
    specs = manifests["skills"]["skills"]
    tools: set[str] = set()
    for skill in skills:
        tools.update(specs[skill].get("required_dependencies", []))
    return tools


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
