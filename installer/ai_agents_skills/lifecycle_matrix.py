from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agents import all_agent_names, detect_agents, target_for
from .apply import apply_plan
from .capabilities import smoke_artifact
from .lifecycle import uninstall
from .planner import build_plan
from .selectors import artifact_dependency_skills, resolve_artifacts, resolve_skills, split_csv
from .state import artifact_signature, load_state, save_state
from .verify import verify


@dataclass(frozen=True)
class LifecycleScenario:
    name: str
    description: str
    skills: str | None = "zotero"
    profile: str | None = None
    no_skills: bool = False
    artifact: str | None = None
    artifacts: str | None = None
    artifact_profile: str | None = None
    with_deps: bool = False
    install_mode: str = "auto"
    adopt: bool = False
    backup_replace: bool = False
    migrate: bool = False
    seed: str = "clean"
    expected_smoke_statuses: tuple[str, ...] = ("ok", "no-managed-artifacts")
    agent_subset: tuple[str, ...] | None = None
    path_variant: str = "normal"


DEFAULT_SCENARIOS: tuple[LifecycleScenario, ...] = (
    LifecycleScenario(
        name="clean-auto",
        description="clean skill install with per-agent auto install modes",
    ),
    LifecycleScenario(
        name="copy-mode",
        description="clean skill install forced to regular copied files",
        install_mode="copy",
    ),
    LifecycleScenario(
        name="reference-mode",
        description="clean skill install forced to reference adapters",
        install_mode="reference",
    ),
    LifecycleScenario(
        name="symlink-mode",
        description="clean skill install forced to symlinked files",
        install_mode="symlink",
        expected_smoke_statuses=("ok", "degraded"),
    ),
    LifecycleScenario(
        name="adopt-unmanaged",
        description="adopt an existing canonical user-owned skill file",
        adopt=True,
        seed="unmanaged",
    ),
    LifecycleScenario(
        name="backup-replace-unmanaged",
        description="replace an existing canonical user-owned skill file and restore it on uninstall",
        backup_replace=True,
        seed="unmanaged",
    ),
    LifecycleScenario(
        name="migrate-legacy",
        description="migrate a legacy skill alias and restore it on uninstall",
        skills="deep-research-workflow",
        migrate=True,
        seed="legacy",
    ),
    LifecycleScenario(
        name="artifact-with-deps",
        description="install an entrypoint artifact and its backing skill",
        no_skills=True,
        artifact="entrypoint-alias:deep-research",
        with_deps=True,
    ),
)

FULL_EXTRA_SCENARIOS: tuple[LifecycleScenario, ...] = (
    LifecycleScenario(
        name="skip-unmanaged",
        description="default planning preserves existing unmanaged canonical files",
        seed="unmanaged",
    ),
    LifecycleScenario(
        name="skip-legacy",
        description="default planning preserves legacy aliases unless migration is requested",
        skills="deep-research-workflow",
        seed="legacy",
    ),
    LifecycleScenario(
        name="profile-research-core",
        description="default profile lifecycle for the bundled research-core skills",
        skills=None,
    ),
    LifecycleScenario(
        name="artifact-profile-workflow-templates",
        description="artifact profile lifecycle without skill installation",
        skills=None,
        no_skills=True,
        artifact_profile="workflow-templates",
    ),
)

STRESS_EXTRA_SCENARIOS: tuple[LifecycleScenario, ...] = (
    LifecycleScenario(
        name="profile-full-research",
        description="all declared skills through the full-research profile",
        skills=None,
        profile="full-research",
    ),
    LifecycleScenario(
        name="workflow-artifacts-with-deps",
        description="all portable workflow artifacts plus required backing skills",
        skills=None,
        no_skills=True,
        artifact_profile="workflow-artifacts",
        with_deps=True,
    ),
    LifecycleScenario(
        name="codex-only-auto",
        description="clean auto-mode install for Codex only",
        agent_subset=("codex",),
    ),
    LifecycleScenario(
        name="claude-only-auto",
        description="clean auto-mode install for Claude only",
        agent_subset=("claude",),
    ),
    LifecycleScenario(
        name="deepseek-only-auto",
        description="clean auto-mode install for DeepSeek only",
        agent_subset=("deepseek",),
    ),
    LifecycleScenario(
        name="special-path-clean-auto",
        description="clean auto-mode install under a path with spaces and punctuation",
        path_variant="spaces",
    ),
)

SCENARIOS: dict[str, LifecycleScenario] = {
    scenario.name: scenario
    for scenario in (*DEFAULT_SCENARIOS, *FULL_EXTRA_SCENARIOS, *STRESS_EXTRA_SCENARIOS)
}


def run_lifecycle_matrix(
    manifests: dict[str, Any],
    *,
    matrix: str = "default",
    scenario_names: list[str] | None = None,
    platform_shape: str = "all",
    agents: str | None = None,
    keep_fake_roots: bool = False,
    custom_args: Any | None = None,
) -> dict[str, Any]:
    scenarios = selected_scenarios(matrix, scenario_names, custom_args)
    shapes = ["linux", "macos", "windows", "wsl"] if platform_shape == "all" else [platform_shape]
    requested_agents = split_csv(agents) if agents else all_agent_names()
    runs = []
    for scenario in scenarios:
        for shape in shapes:
            runs.append(
                run_lifecycle_case(
                    manifests,
                    scenario,
                    shape=shape,
                    requested_agents=requested_agents,
                    keep_fake_root=keep_fake_roots,
                )
            )
    state_checks = run_state_stress_checks(manifests, shapes, requested_agents, keep_fake_roots) if matrix == "stress" else []
    failed = [run for run in runs if run["status"] != "ok"]
    failed_state_checks = [check for check in state_checks if check["status"] != "ok"]
    return {
        "status": "ok" if not failed and not failed_state_checks else "failed",
        "matrix": matrix,
        "scenario_count": len(scenarios),
        "run_count": len(runs),
        "failed_count": len(failed) + len(failed_state_checks),
        "runs": runs,
        "state_checks": state_checks,
    }


def selected_scenarios(
    matrix: str,
    scenario_names: list[str] | None,
    custom_args: Any | None,
) -> list[LifecycleScenario]:
    if custom_args is not None:
        return [custom_scenario(custom_args)]
    if scenario_names:
        unknown = sorted(set(scenario_names) - set(SCENARIOS))
        if unknown:
            raise ValueError(f"unknown lifecycle scenario(s): {', '.join(unknown)}")
        return [SCENARIOS[name] for name in scenario_names]
    if matrix == "default":
        return list(DEFAULT_SCENARIOS)
    if matrix == "full":
        return [*DEFAULT_SCENARIOS, *FULL_EXTRA_SCENARIOS]
    if matrix == "stress":
        return [*DEFAULT_SCENARIOS, *FULL_EXTRA_SCENARIOS, *STRESS_EXTRA_SCENARIOS]
    raise ValueError(f"unknown lifecycle matrix: {matrix}")


def custom_scenario(args: Any) -> LifecycleScenario:
    return LifecycleScenario(
        name="custom",
        description="caller-selected lifecycle scope",
        skills=getattr(args, "skills", None) or getattr(args, "skill", None),
        profile=getattr(args, "profile", None),
        no_skills=getattr(args, "no_skills", False),
        artifact=getattr(args, "artifact", None),
        artifacts=getattr(args, "artifacts", None),
        artifact_profile=getattr(args, "artifact_profile", None),
        with_deps=getattr(args, "with_deps", False),
        install_mode=getattr(args, "install_mode", "auto"),
        adopt=getattr(args, "adopt", False),
        backup_replace=getattr(args, "backup_replace", False),
        migrate=getattr(args, "migrate", False),
    )


def run_lifecycle_case(
    manifests: dict[str, Any],
    scenario: LifecycleScenario,
    *,
    shape: str,
    requested_agents: list[str],
    keep_fake_root: bool,
) -> dict[str, Any]:
    base = Path(tempfile.mkdtemp(prefix=f"aas-{shape}-{scenario.name}-"))
    shape_base = base / "path with spaces [stress]" if scenario.path_variant == "spaces" else base
    root = fake_root_for_shape(shape_base, shape)
    failures: list[str] = []
    warnings: list[str] = []
    try:
        root.mkdir(parents=True, exist_ok=True)
        active_requested_agents = list(scenario.agent_subset or tuple(requested_agents))
        for agent in active_requested_agents:
            (root / f".{agent}").mkdir(parents=True, exist_ok=True)
        agents = detect_agents(root, active_requested_agents)
        selected = scenario_skills(scenario, manifests)
        selected_artifacts = scenario_artifacts(scenario, manifests)
        seed_fixture(root, agents, scenario, selected, manifests)
        baseline_with_state = root_snapshot(root, include_installer_state=True)
        baseline_without_state = root_snapshot(root, include_installer_state=False, include_dirs=False)

        plan = build_plan(
            root,
            manifests,
            selected,
            agents,
            adopt=scenario.adopt,
            backup_replace=scenario.backup_replace,
            migrate=scenario.migrate,
            artifacts=selected_artifacts,
            install_mode=scenario.install_mode,
        )
        install_dry_run = apply_plan(root, plan, dry_run=True)
        after_install_dry_run = root_snapshot(root, include_installer_state=True)
        install_dry_run_preserved = after_install_dry_run == baseline_with_state
        if not install_dry_run_preserved:
            failures.append("install dry-run changed fake root")

        install_result = apply_plan(root, plan, dry_run=False)
        install_actions_match = normalized_install_actions(install_dry_run["actions"]) == normalized_install_actions(
            install_result["actions"]
        )
        if not install_actions_match:
            failures.append("install dry-run actions differ from applied actions")

        install_verify = verify(root)
        expected_install_status = expected_verify_status(root)
        if install_verify["status"] != expected_install_status:
            failures.append(
                f"install verify status {install_verify['status']} != expected {expected_install_status}"
            )

        smoke_result = smoke_managed_skill_files(root)
        if smoke_result["status"] not in scenario.expected_smoke_statuses:
            failures.append(f"smoke status {smoke_result['status']}")
        elif smoke_result["status"] not in {"ok", "no-managed-artifacts"}:
            warnings.append(f"expected smoke status {smoke_result['status']}")

        before_uninstall_dry_run = root_snapshot(root, include_installer_state=True)
        uninstall_dry_run = uninstall(root, dry_run=True)
        after_uninstall_dry_run = root_snapshot(root, include_installer_state=True)
        uninstall_dry_run_preserved = after_uninstall_dry_run == before_uninstall_dry_run
        if not uninstall_dry_run_preserved:
            failures.append("uninstall dry-run changed fake root")

        uninstall_result = uninstall(root, dry_run=False)
        uninstall_actions_match = normalized_uninstall_actions(uninstall_dry_run["actions"]) == normalized_uninstall_actions(
            uninstall_result["actions"]
        )
        if not uninstall_actions_match:
            failures.append("uninstall dry-run actions differ from applied actions")

        uninstall_verify = verify(root)
        if uninstall_verify["status"] != "no-managed-artifacts":
            failures.append(f"uninstall verify status {uninstall_verify['status']} != no-managed-artifacts")

        final_without_state = root_snapshot(root, include_installer_state=False, include_dirs=False)
        final_preserved = final_without_state == baseline_without_state
        if not final_preserved:
            failures.append("final fake root differs from baseline outside installer state")

        return {
            "status": "ok" if not failures else "failed",
            "scenario": scenario.name,
            "description": scenario.description,
            "shape": shape,
            "base": str(base),
            "root": str(root),
            "fake_root_kept": keep_fake_root,
            "agents": [agent.name for agent in agents],
            "selected_skills": selected,
            "selected_artifacts": [f"{kind}:{name}" for kind, name in selected_artifacts],
            "install": {
                "dry_run_preserved_root": install_dry_run_preserved,
                "dry_apply_actions_match": install_actions_match,
                "planned_actions": len(install_dry_run["actions"]),
                "applied_actions": len(install_result["actions"]),
                "verify_status": install_verify["status"],
                "verify_checked": install_verify["checked"],
                "smoke_status": smoke_result["status"],
                "smoke_checked": smoke_result["checked"],
            },
            "uninstall": {
                "dry_run_preserved_root": uninstall_dry_run_preserved,
                "dry_apply_actions_match": uninstall_actions_match,
                "planned_actions": len(uninstall_dry_run["actions"]),
                "applied_actions": len(uninstall_result["actions"]),
                "verify_status": uninstall_verify["status"],
                "verify_checked": uninstall_verify["checked"],
                "final_preserved_root": final_preserved,
            },
            "failures": failures,
            "warnings": warnings,
        }
    finally:
        if not keep_fake_root:
            shutil.rmtree(base, ignore_errors=True)


def scenario_skills(scenario: LifecycleScenario, manifests: dict[str, Any]) -> list[str]:
    args = ScenarioArgs(scenario)
    selected = set(resolve_skills(args, manifests))
    if scenario.with_deps:
        selected.update(artifact_dependency_skills(scenario_artifacts(scenario, manifests), manifests))
    return sorted(selected)


def scenario_artifacts(scenario: LifecycleScenario, manifests: dict[str, Any]) -> list[tuple[str, str]]:
    args = ScenarioArgs(scenario)
    return resolve_artifacts(args, manifests)


class ScenarioArgs:
    def __init__(self, scenario: LifecycleScenario):
        self.skill = None
        self.skills = scenario.skills
        self.profile = scenario.profile
        self.exclude = None
        self.no_skills = scenario.no_skills
        self.artifact = scenario.artifact
        self.artifacts = scenario.artifacts
        self.artifact_profile = scenario.artifact_profile
        self.exclude_artifact = None
        self.with_deps = scenario.with_deps


def seed_fixture(
    root: Path,
    agents: list[Any],
    scenario: LifecycleScenario,
    selected: list[str],
    manifests: dict[str, Any],
) -> None:
    if scenario.seed == "clean":
        return
    if not selected:
        return
    skill = selected[0]
    if scenario.seed == "unmanaged":
        for agent in agents:
            target = target_for(root, agent.name).skills_dir / skill / "SKILL.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"user-owned {agent.name} {skill}\n", encoding="utf-8")
        return
    if scenario.seed == "legacy":
        aliases = manifests["skills"].get("legacy_aliases", {}).get(skill, [])
        if not aliases:
            raise ValueError(f"scenario {scenario.name} requested legacy seed for skill without alias: {skill}")
        alias = aliases[0]
        for agent in agents:
            target = target_for(root, agent.name).skills_dir / alias / "SKILL.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"legacy {agent.name} {alias}\n", encoding="utf-8")
        return
    raise ValueError(f"unknown lifecycle scenario seed: {scenario.seed}")


def expected_verify_status(root: Path) -> str:
    state = load_state(root)
    return "ok" if state.get("artifacts") else "no-managed-artifacts"


def smoke_managed_skill_files(root: Path) -> dict[str, Any]:
    state = load_state(root)
    results = [
        smoke_artifact(item)
        for item in state.get("artifacts", [])
        if item.get("artifact_type") == "skill-file"
    ]
    if not results:
        return {
            "status": "no-managed-artifacts",
            "checked": 0,
            "results": [],
        }
    status = "ok" if all(item["status"] == "ok" for item in results) else "degraded"
    return {"status": status, "checked": len(results), "results": results}


def root_snapshot(
    root: Path,
    *,
    include_installer_state: bool,
    include_dirs: bool = True,
) -> dict[str, tuple[str, str | None]]:
    snapshot: dict[str, tuple[str, str | None]] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        if not include_installer_state and (rel == ".ai-agents-skills" or rel.startswith(".ai-agents-skills/")):
            continue
        if path.is_symlink():
            snapshot[rel] = ("symlink", path.readlink().as_posix())
        elif path.is_dir():
            if include_dirs:
                snapshot[rel] = ("dir", None)
        elif path.is_file():
            snapshot[rel] = ("file", sha256_bytes(path.read_bytes()))
    return snapshot


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def normalized_install_actions(actions: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [
        (
            action.get("agent"),
            action.get("skill"),
            action.get("artifact_type"),
            action.get("artifact_id"),
            action.get("artifact_name"),
            action.get("classification"),
            action.get("operation"),
            action.get("install_mode"),
            action.get("fallback_mode"),
            action.get("path") or action.get("artifact"),
            action.get("legacy_path"),
            action.get("source_path"),
        )
        for action in actions
    ]


def normalized_uninstall_actions(actions: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [
        (
            action.get("agent"),
            action.get("skill"),
            action.get("artifact_type"),
            action.get("artifact_id"),
            action.get("artifact_name"),
            action.get("operation"),
            action.get("artifact"),
            action.get("key"),
        )
        for action in actions
    ]


def fake_root_for_shape(base: Path, shape: str) -> Path:
    if shape == "macos":
        return base / "Users" / "agent"
    if shape == "windows":
        return base / "C" / "Users" / "agent"
    if shape == "wsl":
        return base / "mnt" / "c" / "Users" / "agent"
    return base / "home" / "agent"


def run_state_stress_checks(
    manifests: dict[str, Any],
    shapes: list[str],
    requested_agents: list[str],
    keep_fake_roots: bool,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for shape in shapes:
        checks.append(changed_managed_file_preserved_check(manifests, shape, requested_agents, keep_fake_roots))
        checks.append(missing_managed_file_forget_check(manifests, shape, requested_agents, keep_fake_roots))
        checks.append(outside_root_state_refused_check(shape, keep_fake_roots))
        checks.append(corrupt_state_reports_error_check(shape, keep_fake_roots))
    return checks


def installed_root(
    manifests: dict[str, Any],
    shape: str,
    requested_agents: list[str],
    *,
    install_mode: str = "copy",
) -> tuple[Path, Path]:
    base = Path(tempfile.mkdtemp(prefix=f"aas-{shape}-state-stress-"))
    root = fake_root_for_shape(base, shape)
    root.mkdir(parents=True, exist_ok=True)
    for agent in requested_agents:
        (root / f".{agent}").mkdir(parents=True, exist_ok=True)
    agents = detect_agents(root, requested_agents)
    scenario = LifecycleScenario(name="state-stress", description="state stress helper", install_mode=install_mode)
    plan = build_plan(root, manifests, scenario_skills(scenario, manifests), agents, install_mode=install_mode)
    apply_plan(root, plan, dry_run=False)
    return base, root


def first_managed_skill_file(root: Path) -> Path:
    state = load_state(root)
    for item in state.get("artifacts", []):
        if item.get("artifact_type") == "skill-file":
            return Path(item["artifact"])
    raise ValueError("state stress setup did not create a managed skill file")


def changed_managed_file_preserved_check(
    manifests: dict[str, Any],
    shape: str,
    requested_agents: list[str],
    keep_fake_root: bool,
) -> dict[str, Any]:
    base, root = installed_root(manifests, shape, requested_agents, install_mode="copy")
    failures: list[str] = []
    try:
        target = first_managed_skill_file(root)
        target.write_text("user edit after install\n", encoding="utf-8")
        install_verify = verify(root)
        if install_verify["status"] != "failed":
            failures.append("verify did not fail after managed file edit")
        dry = uninstall(root, dry_run=True)
        result = uninstall(root, dry_run=False)
        operations = {action.get("operation") for action in result.get("actions", [])}
        if "skip-conflict" not in operations:
            failures.append("uninstall did not skip changed managed artifact")
        if not target.exists() or target.read_text(encoding="utf-8") != "user edit after install\n":
            failures.append("changed managed file was not preserved")
        if normalized_uninstall_actions(dry["actions"]) != normalized_uninstall_actions(result["actions"]):
            failures.append("uninstall dry-run actions differ from applied actions")
        return state_check_result("changed-managed-file-preserved", shape, base, root, keep_fake_root, failures)
    finally:
        if not keep_fake_root:
            shutil.rmtree(base, ignore_errors=True)


def missing_managed_file_forget_check(
    manifests: dict[str, Any],
    shape: str,
    requested_agents: list[str],
    keep_fake_root: bool,
) -> dict[str, Any]:
    base, root = installed_root(manifests, shape, requested_agents, install_mode="copy")
    failures: list[str] = []
    try:
        target = first_managed_skill_file(root)
        target.unlink()
        if verify(root)["status"] != "failed":
            failures.append("verify did not fail after managed file deletion")
        dry = uninstall(root, dry_run=True)
        result = uninstall(root, dry_run=False)
        operations = {action.get("operation") for action in result.get("actions", [])}
        if "forget-missing" not in operations:
            failures.append("uninstall did not forget missing managed artifact")
        if verify(root)["status"] != "no-managed-artifacts":
            failures.append("uninstall did not clear managed state after missing artifact cleanup")
        if normalized_uninstall_actions(dry["actions"]) != normalized_uninstall_actions(result["actions"]):
            failures.append("uninstall dry-run actions differ from applied actions")
        return state_check_result("missing-managed-file-forget", shape, base, root, keep_fake_root, failures)
    finally:
        if not keep_fake_root:
            shutil.rmtree(base, ignore_errors=True)


def outside_root_state_refused_check(
    shape: str,
    keep_fake_root: bool,
) -> dict[str, Any]:
    base = Path(tempfile.mkdtemp(prefix=f"aas-{shape}-outside-root-"))
    root = fake_root_for_shape(base, shape)
    outside = base / "outside.txt"
    failures: list[str] = []
    try:
        root.mkdir(parents=True, exist_ok=True)
        outside.write_text("do not remove\n", encoding="utf-8")
        save_state(
            root,
            {
                "schema_version": 1,
                "runs": [],
                "artifacts": [
                    {
                        "key": "tampered",
                        "agent": "claude",
                        "skill": "zotero",
                        "artifact": str(outside),
                        "artifact_type": "skill-file",
                        "managed": True,
                        "uninstall": {"action": "delete-created"},
                        "installed_signature": artifact_signature(outside),
                    }
                ],
            },
        )
        result = uninstall(root, skills={"zotero"}, dry_run=False)
        if outside.read_text(encoding="utf-8") != "do not remove\n":
            failures.append("outside-root artifact was modified")
        if result["actions"][0].get("operation") != "skip-conflict":
            failures.append("outside-root artifact was not skipped as conflict")
        return state_check_result("outside-root-state-refused", shape, base, root, keep_fake_root, failures)
    finally:
        if not keep_fake_root:
            shutil.rmtree(base, ignore_errors=True)


def corrupt_state_reports_error_check(
    shape: str,
    keep_fake_root: bool,
) -> dict[str, Any]:
    base = Path(tempfile.mkdtemp(prefix=f"aas-{shape}-corrupt-state-"))
    root = fake_root_for_shape(base, shape)
    failures: list[str] = []
    try:
        root.mkdir(parents=True, exist_ok=True)
        state_path = root / ".ai-agents-skills" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("{not json\n", encoding="utf-8")
        try:
            verify(root)
        except ValueError as exc:
            if "installer state is not valid JSON" not in str(exc):
                failures.append(f"unexpected corrupt-state error: {exc}")
        else:
            failures.append("corrupt state did not raise an error")
        return state_check_result("corrupt-state-reports-error", shape, base, root, keep_fake_root, failures)
    finally:
        if not keep_fake_root:
            shutil.rmtree(base, ignore_errors=True)


def state_check_result(
    name: str,
    shape: str,
    base: Path,
    root: Path,
    keep_fake_root: bool,
    failures: list[str],
) -> dict[str, Any]:
    return {
        "status": "ok" if not failures else "failed",
        "check": name,
        "shape": shape,
        "base": str(base),
        "root": str(root),
        "fake_root_kept": keep_fake_root,
        "failures": failures,
    }
