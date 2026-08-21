from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from .agents import detect_agents
from .apply import apply_plan
from .capabilities import normalized_path_within, resolved_path_within
from .discovery import current_platform
from .planner import build_plan
from .runtime import (
    RUNTIME_SOURCE_ROOT,
    runtime_dependency_closure,
    runtime_entry_applies,
    runtime_expected_sha256,
)
from .state import load_state, sha256_file
from .verify import verify, verify_artifact


RUNTIME_SMOKE_SKILLS = (
    "autonomous-research-loop-runtime",
    "axiom-axle-mcp",
    "deep-research-workflow",
    "formal-skeleton-helper",
    "get-available-resources",
    "graph-verifier",
    "lean-explore-mcp",
    "lean-formalization-intake",
    "lean-research-library",
    "lean-strict-verification-gate",
    "self-improving-agent",
)

DECLARED_RUNTIME_EXCLUSION_STATUSES = frozenset(
    {"manual-native", "doctor-only", "static-only"}
)
INSTALLED_RUNTIME_SMOKE_SCHEMA = "ai-agents-skills.installed-runtime-smoke.v1"
INSTALLED_RUNTIME_SMOKE_SCHEMA_VERSION = 1


def installed_runtime_smoke_report(**fields: Any) -> dict[str, Any]:
    report = {
        "schema": INSTALLED_RUNTIME_SMOKE_SCHEMA,
        "schema_version": INSTALLED_RUNTIME_SMOKE_SCHEMA_VERSION,
    }
    report.update(fields)
    return report


def relax_ephemeral_credential_enforcement(runtime_root: Path) -> None:
    """Relax the credential-runtime generation gate in an ephemeral copy.

    run_skill.sh documents that ``credential_runtime_enforcement=1`` is
    intentionally patchable only in ephemeral copies.  Both smoke harnesses
    execute exactly such a copy -- the temporary install, and the installed
    harness's hash-verified scratch tree -- and neither can satisfy the
    root-owned exact-generation check, so the smoke canaries would otherwise
    turn every credential-bearing case into a gate refusal.  The installed
    runtime itself is never patched: the caller passes the scratch root.
    """
    runner = runtime_root / "run_skill.sh"
    if not runner.is_file():
        return
    text = runner.read_text(encoding="utf-8")
    patched = text.replace(
        "credential_runtime_enforcement=1",
        "credential_runtime_enforcement=0",
        1,
    )
    if patched != text:
        runner.write_text(patched, encoding="utf-8")
        runner.chmod(0o755)


def run_runtime_smoke(
    manifests: dict[str, Any],
    *,
    skills: set[str] | None = None,
    platform: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    host_platform = current_platform(platform)
    selected_skills = selected_runtime_skills(manifests, skills)
    with tempfile.TemporaryDirectory(prefix="aas-runtime-smoke-") as tmp:
        root = Path(tmp)
        (root / ".codex").mkdir(parents=True)
        agents = detect_agents(root, ["codex"])
        plan = build_plan(
            root,
            manifests,
            selected_skills,
            agents,
            install_mode="copy",
            runtime_profile="full",
            platform=host_platform,
            requested_agents=["codex"],
        )
        install_result = apply_plan(root, plan, dry_run=False)
        verify_result = verify(root)
        runtime_root = root / ".codex" / "runtime"
        relax_ephemeral_credential_enforcement(runtime_root)
        workspace = runtime_root / "workspace"
        runners = runner_invocations(runtime_root, host_platform)
        if not runners:
            return {
                "status": "failed",
                "platform": host_platform,
                "selected_skills": selected_skills,
                "coverage": runtime_smoke_coverage_rows(manifests),
                "install_action_count": len(install_result.get("actions", [])),
                "verify_status": verify_result["status"],
                "checked": 0,
                "results": [
                    {
                        "status": "failed",
                        "mode": "temporary",
                        "runner": None,
                        "skill": skill,
                        "checked": 0,
                        "results": [],
                        "failure_kind": "runner-unavailable",
                        "reason": "no native runtime runner is available on this host",
                    }
                    for skill in selected_skills
                ],
            }
        results = []
        for runner in runners:
            for skill in selected_skills:
                results.append(run_smoke_case(
                    manifests,
                    skill=skill,
                    runner=runner,
                    workspace=workspace,
                    platform=host_platform,
                    timeout=timeout,
                ))
        status = "ok" if verify_result["status"] == "ok" and all(item["status"] == "ok" for item in results) else "failed"
        return {
            "status": status,
            "platform": host_platform,
            "selected_skills": selected_skills,
            "coverage": runtime_smoke_coverage_rows(manifests),
            "install_action_count": len(install_result.get("actions", [])),
            "verify_status": verify_result["status"],
            "checked": len(results),
            "results": results,
        }


def run_installed_runtime_smoke(
    root: Path,
    manifests: dict[str, Any],
    *,
    skills: set[str] | None = None,
    agents: set[str] | None = None,
    platform: str | None = None,
    timeout: int = 60,
    require_complete_coverage: bool = False,
) -> dict[str, Any]:
    target_platform = current_platform(platform)
    host_platform = current_platform(None)
    runtime_specs = manifests.get("runtime", {}).get("skills", {})
    declared_runtime_skills = set(runtime_specs) if isinstance(runtime_specs, dict) else set()
    explicit_skills = set(skills or ())
    requested_report_skills = (
        explicit_skills | declared_runtime_skills
        if require_complete_coverage
        else explicit_skills
    )
    requested_runtime_skills = requested_report_skills & declared_runtime_skills
    if target_platform != host_platform:
        return installed_runtime_smoke_report(
            status="skipped",
            mode="installed",
            platform=target_platform,
            host_platform=host_platform,
            checked=0,
            unknown_coverage_count=0,
            missing_managed_runtime_count=0,
            declared_exclusion_count=0,
            declared_exclusions=[],
            results=[],
            reason="installed runtime smoke only runs on the current host platform",
        )
    try:
        state = load_state(root)
    except (OSError, RuntimeError, ValueError):
        result_skills = sorted(requested_report_skills)
        return installed_runtime_smoke_report(
            status="failed",
            mode="installed",
            platform=target_platform,
            selected_skills=result_skills,
            coverage=runtime_smoke_coverage_rows(manifests),
            checked=0,
            unknown_coverage_count=0,
            missing_managed_runtime_count=0,
            declared_exclusion_count=0,
            declared_exclusions=[],
            results=installed_runtime_failure_rows(
                result_skills,
                failure_kind="invalid-managed-state",
                reason="installer state could not be loaded",
            ),
            managed_state_verify_status="not-run-invalid-state",
            managed_state_checked=0,
            runtime_state_coverage_status="not-run-invalid-state",
            runtime_state_expected_count=0,
            runtime_state_selected_record_count=0,
            runtime_state_missing_count=0,
            runtime_state_extra_count=0,
            runtime_state_duplicate_count=0,
            runtime_state_mismatched_count=0,
            runtime_boundary_violation_count=0,
            failure_kind="invalid-managed-state",
            reason="installer state could not be loaded",
        )
    state_artifacts = state.get("artifacts")
    if not isinstance(state_artifacts, list) or any(
        not isinstance(item, dict) for item in state_artifacts
    ):
        result_skills = sorted(requested_report_skills)
        return installed_runtime_smoke_report(
            status="failed",
            mode="installed",
            platform=target_platform,
            selected_skills=result_skills,
            coverage=runtime_smoke_coverage_rows(manifests),
            checked=0,
            unknown_coverage_count=0,
            missing_managed_runtime_count=0,
            declared_exclusion_count=0,
            declared_exclusions=[],
            results=installed_runtime_failure_rows(
                result_skills,
                failure_kind="invalid-managed-state",
                reason="managed artifact state shape is invalid",
            ),
            managed_state_verify_status="not-run-invalid-state",
            managed_state_checked=0,
            runtime_state_coverage_status="not-run-invalid-state",
            runtime_state_expected_count=0,
            runtime_state_selected_record_count=0,
            runtime_state_missing_count=0,
            runtime_state_extra_count=0,
            runtime_state_duplicate_count=0,
            runtime_state_mismatched_count=0,
            runtime_boundary_violation_count=0,
            failure_kind="invalid-managed-state",
            reason="managed artifact state shape is invalid",
        )

    managed_runtime_artifacts = [
        item for item in state_artifacts
        if item.get("artifact_type") == "runtime-file"
        and bool(item.get("managed"))
    ]
    globally_unscoped_runtime_artifacts = [
        item
        for item in managed_runtime_artifacts
        if not isinstance(item.get("skill"), str)
        or not item.get("skill")
        or not isinstance(item.get("runtime_root"), str)
        or not item.get("runtime_root")
    ]
    installed_from_runtime = {
        item["skill"]
        for item in managed_runtime_artifacts
        if isinstance(item.get("skill"), str)
        and item.get("skill")
        and item.get("skill") != "runtime-runner"
    }
    installed_from_managed_state = {
        item["skill"]
        for item in state_artifacts
        if bool(item.get("managed"))
        and isinstance(item.get("skill"), str)
        and item.get("skill") in declared_runtime_skills
    }
    if skills is None:
        result_skill_set = installed_from_runtime | installed_from_managed_state
    else:
        result_skill_set = set(explicit_skills)
    if require_complete_coverage:
        result_skill_set.update(declared_runtime_skills)
        result_skill_set.update(installed_from_runtime)
    result_skills = sorted(result_skill_set)
    if not result_skills:
        if globally_unscoped_runtime_artifacts:
            violations = runtime_state_boundary_violations(
                root,
                set(),
                globally_unscoped_runtime_artifacts,
            )
            return installed_runtime_smoke_report(
                status="failed",
                mode="installed",
                platform=target_platform,
                selected_skills=[],
                coverage=runtime_smoke_coverage_rows(manifests),
                checked=0,
                unknown_coverage_count=0,
                missing_managed_runtime_count=0,
                declared_exclusion_count=0,
                declared_exclusions=[],
                results=[
                    {
                        "status": "failed",
                        "mode": "installed",
                        "runtime_root": item.get("runtime_root"),
                        "skill": item.get("skill"),
                        "runner": None,
                        "checked": 0,
                        "results": [],
                        "failure_kind": "unscoped-runtime-record",
                        "reason": "managed runtime record cannot be safely scoped",
                    }
                    for item in globally_unscoped_runtime_artifacts
                ],
                managed_state_verify_status="not-run-runtime-state-coverage-failure",
                managed_state_checked=0,
                runtime_state_coverage_status="failed",
                runtime_state_expected_count=0,
                runtime_state_selected_record_count=len(globally_unscoped_runtime_artifacts),
                runtime_state_missing_count=0,
                runtime_state_extra_count=0,
                runtime_state_duplicate_count=0,
                runtime_state_mismatched_count=len(globally_unscoped_runtime_artifacts),
                runtime_boundary_violation_count=len(violations),
                runtime_boundary_violations=violations,
                failure_kind="runtime-state-coverage",
                reason="managed runtime state contains records that cannot be safely scoped",
            )
        return installed_runtime_smoke_report(
            status="skipped",
            mode="installed",
            platform=target_platform,
            selected_skills=[],
            coverage=runtime_smoke_coverage_rows(manifests),
            checked=0,
            unknown_coverage_count=0,
            missing_managed_runtime_count=0,
            declared_exclusion_count=0,
            declared_exclusions=[],
            results=[],
            reason="no managed runtime-backed skills matched this scope",
        )

    direct_runtime_artifacts = [
        item
        for item in managed_runtime_artifacts
        if item.get("skill") in result_skill_set
        and item.get("skill") != "runtime-runner"
    ]
    skills_with_runtime_records = {
        str(item.get("skill"))
        for item in direct_runtime_artifacts
        if isinstance(item.get("skill"), str) and item.get("skill")
    }
    missing_managed_runtime = sorted(
        (result_skill_set & declared_runtime_skills) - skills_with_runtime_records
    )

    root_result_skills: dict[str, set[str]] = {}
    for item in direct_runtime_artifacts:
        runtime_root_text = valid_selected_runtime_root(root, item)
        skill = item.get("skill")
        if runtime_root_text is not None and isinstance(skill, str):
            root_result_skills.setdefault(runtime_root_text, set()).add(skill)

    root_closure_skills: dict[str, set[str]] = {}
    manifest_closure_issues: list[dict[str, Any]] = []
    for runtime_root_text, root_skills in sorted(root_result_skills.items()):
        known_root_skills = sorted(root_skills & declared_runtime_skills)
        try:
            closure = runtime_dependency_closure(known_root_skills, runtime_specs)
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            manifest_closure_issues.append({
                "kind": "invalid-runtime-dependency-closure",
                "runtime_root": runtime_root_text,
                "reason": str(exc),
            })
            closure = known_root_skills
        root_closure_skills[runtime_root_text] = set(closure)

    selected_record_ids = {
        id(item)
        for item in [*direct_runtime_artifacts, *globally_unscoped_runtime_artifacts]
    }
    for item in managed_runtime_artifacts:
        runtime_root_text = item.get("runtime_root")
        if not isinstance(runtime_root_text, str):
            continue
        closure = root_closure_skills.get(runtime_root_text)
        if closure is None:
            continue
        item_skill = item.get("skill")
        malformed_or_unknown_skill = (
            not isinstance(item_skill, str)
            or not item_skill
            or item_skill not in declared_runtime_skills
        )
        if item_skill == "runtime-runner" or item_skill in closure or malformed_or_unknown_skill:
            selected_record_ids.add(id(item))
    scoped_runtime_artifacts = [
        item for item in managed_runtime_artifacts if id(item) in selected_record_ids
    ]
    selected_runtime_roots = set(root_result_skills)
    boundary_violations = runtime_state_boundary_violations(
        root,
        selected_runtime_roots,
        scoped_runtime_artifacts,
    )

    expected_by_root: dict[str, list[dict[str, Any]]] = {}
    canonical_source_issues: list[dict[str, Any]] = []
    for runtime_root_text, closure in sorted(root_closure_skills.items()):
        expected, issues = expected_runtime_state_records(
            manifests,
            runtime_root=Path(runtime_root_text),
            skills=closure,
            platform=target_platform,
        )
        expected_by_root[runtime_root_text] = expected
        canonical_source_issues.extend(issues)
    expected_runtime_artifacts = [
        item
        for runtime_root_text in sorted(expected_by_root)
        for item in expected_by_root[runtime_root_text]
    ]
    comparison = compare_runtime_state_records(
        scoped_runtime_artifacts,
        expected_runtime_artifacts,
        host_platform=target_platform,
    )
    invalid_requested_skills = sorted(explicit_skills - declared_runtime_skills)
    unknown_installed_skills = sorted(
        (result_skill_set & installed_from_runtime) - declared_runtime_skills
    )
    preflight_failed = any((
        missing_managed_runtime,
        invalid_requested_skills,
        unknown_installed_skills,
        boundary_violations,
        manifest_closure_issues,
        canonical_source_issues,
        comparison["missing"],
        comparison["extra"],
        comparison["duplicates"],
        comparison["mismatched"],
    ))
    if preflight_failed:
        if boundary_violations:
            reason = "managed runtime state escapes the selected root or has malformed paths"
        elif invalid_requested_skills:
            reason = "requested skills include entries without a managed runtime surface"
        elif unknown_installed_skills:
            reason = "managed runtime state contains undeclared runtime skills"
        else:
            reason = "managed runtime state does not match the current manifest closure"
        rows = installed_runtime_failure_rows(
            result_skills,
            failure_kind="runtime-state-coverage",
            reason=reason,
            missing_skills=set(missing_managed_runtime),
            unknown_skills=set(unknown_installed_skills),
            not_runtime_skills=set(invalid_requested_skills),
            roots_by_skill=runtime_roots_by_skill(direct_runtime_artifacts),
        )
        return installed_runtime_smoke_report(
            status="failed",
            mode="installed",
            platform=target_platform,
            selected_skills=result_skills,
            coverage=runtime_smoke_coverage_rows(manifests),
            checked=0,
            unknown_coverage_count=0,
            missing_managed_runtime_count=len(missing_managed_runtime),
            missing_managed_runtime_skills=missing_managed_runtime,
            declared_exclusion_count=0,
            declared_exclusions=[],
            results=rows,
            managed_state_verify_status="not-run-runtime-state-coverage-failure",
            managed_state_checked=0,
            runtime_state_coverage_status="failed",
            runtime_state_expected_count=len(expected_runtime_artifacts),
            runtime_state_selected_record_count=len(scoped_runtime_artifacts),
            runtime_state_missing_count=len(comparison["missing"]),
            runtime_state_missing_records=comparison["missing"],
            runtime_state_extra_count=len(comparison["extra"]),
            runtime_state_extra_records=comparison["extra"],
            runtime_state_foreign_platform_count=len(comparison["foreign_platform"]),
            runtime_state_foreign_platform_records=comparison["foreign_platform"],
            runtime_state_duplicate_count=len(comparison["duplicates"]),
            runtime_state_duplicate_records=comparison["duplicates"],
            runtime_state_mismatched_count=(
                len(comparison["mismatched"])
                + len(manifest_closure_issues)
                + len(canonical_source_issues)
            ),
            runtime_state_mismatched_records=[
                *comparison["mismatched"],
                *manifest_closure_issues,
                *canonical_source_issues,
            ],
            runtime_boundary_violation_count=len(boundary_violations),
            runtime_boundary_violations=boundary_violations,
            failure_kind="runtime-state-coverage",
            reason=reason,
        )

    try:
        managed_integrity = verify(root, skill_filter=skills, agent_filter=agents)
        scoped_integrity_results = [
            verify_artifact(artifact) for artifact in scoped_runtime_artifacts
        ]
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        integrity = {"status": "error", "checked": 0, "results": []}
    else:
        managed_status = managed_integrity.get("status")
        scoped_ok = bool(scoped_integrity_results) and all(
            item.get("status") == "ok" for item in scoped_integrity_results
        )
        integrity = {
            "status": (
                "ok"
                if managed_status in {"ok", "no-managed-artifacts"} and scoped_ok
                else "failed"
            ),
            "checked": int(managed_integrity.get("checked", 0)) + len(scoped_integrity_results),
            "results": [
                *managed_integrity.get("results", []),
                *scoped_integrity_results,
            ],
        }
    if integrity.get("status") != "ok":
        reason = "managed artifact integrity verification failed before runtime execution"
        return installed_runtime_smoke_report(
            status="failed",
            mode="installed",
            platform=target_platform,
            selected_skills=result_skills,
            coverage=runtime_smoke_coverage_rows(manifests),
            checked=0,
            unknown_coverage_count=0,
            missing_managed_runtime_count=len(missing_managed_runtime),
            missing_managed_runtime_skills=missing_managed_runtime,
            declared_exclusion_count=0,
            declared_exclusions=[],
            results=installed_runtime_failure_rows(
                result_skills,
                failure_kind="managed-state-integrity",
                reason=reason,
                roots_by_skill=runtime_roots_by_skill(direct_runtime_artifacts),
            ),
            managed_state_verify_status=integrity.get("status"),
            managed_state_checked=integrity.get("checked", 0),
            runtime_state_coverage_status="ok",
            runtime_state_expected_count=len(expected_runtime_artifacts),
            runtime_state_selected_record_count=len(scoped_runtime_artifacts),
            runtime_state_missing_count=0,
            runtime_state_missing_records=[],
            runtime_state_extra_count=0,
            runtime_state_extra_records=[],
            runtime_state_foreign_platform_count=len(comparison["foreign_platform"]),
            runtime_state_foreign_platform_records=comparison["foreign_platform"],
            runtime_state_duplicate_count=0,
            runtime_state_duplicate_records=[],
            runtime_state_mismatched_count=0,
            runtime_state_mismatched_records=[],
            runtime_boundary_violation_count=0,
            runtime_boundary_violations=[],
            failure_kind="managed-state-integrity",
            reason=reason,
        )

    results: list[dict[str, Any]] = []
    for runtime_root_text, artifacts in sorted(expected_by_root.items()):
        runtime_root = Path(runtime_root_text)
        selected_for_root = sorted(root_result_skills.get(runtime_root_text, set()))
        if not selected_for_root:
            continue
        with tempfile.TemporaryDirectory(prefix="aas-installed-runtime-smoke-") as tmp:
            scratch_workspace = Path(tmp) / "workspace"
            copy_result = copy_installed_runtime_workspace(runtime_root, artifacts, scratch_workspace)
            if copy_result["status"] != "ok":
                for skill in selected_for_root:
                    results.append({
                        "status": "failed",
                        "mode": "installed",
                        "runtime_root": str(runtime_root),
                        "skill": skill,
                        "runner": None,
                        "checks": copy_result["checks"],
                        "reason": copy_result["reason"],
                    })
                continue
            # Execute only the descriptor-read, hash-verified scratch copy.  In
            # particular, never invoke a runner from the mutable installed
            # runtime root after its integrity check.
            #
            # The scratch tree is a per-user temporary directory, so it can never
            # be a root-owned component generation.  Left enforcing, the gate
            # refuses every credential-bearing skill with exit 127 before its
            # offline contract runs, which reports as a skill failure and leaves
            # those contracts permanently unexercised.  Relax the scratch copy,
            # exactly as the temporary harness relaxes its own.
            relax_ephemeral_credential_enforcement(scratch_workspace.parent)
            runners = runner_invocations(scratch_workspace.parent, target_platform)
            for skill in selected_for_root:
                if not has_runtime_smoke_contract(manifests, skill):
                    coverage = runtime_smoke_coverage_status(manifests, skill)
                    declared_exclusion = coverage in DECLARED_RUNTIME_EXCLUSION_STATUSES
                    results.append({
                        "status": "declared-exclusion" if declared_exclusion else "failed",
                        "mode": "installed",
                        "runtime_root": str(runtime_root),
                        "skill": skill,
                        "coverage": coverage,
                        "failure_kind": None if declared_exclusion else "unknown-coverage",
                        "runner": None,
                        "checked": 0,
                        "results": [],
                        "reason": (
                            runtime_smoke_coverage_reason(manifests, skill)
                            if declared_exclusion
                            else f"runtime skill has unknown smoke coverage: {coverage}"
                        ),
                    })
                    continue
                if not runners:
                    results.append({
                        "status": "failed",
                        "mode": "installed",
                        "runtime_root": str(runtime_root),
                        "skill": skill,
                        "runner": None,
                        "checked": 0,
                        "results": [],
                        "reason": "no native runtime runner is available on this host",
                    })
                    continue
                for runner in runners:
                    results.append(run_smoke_case(
                        manifests,
                        skill=skill,
                        runner=runner,
                        workspace=scratch_workspace,
                        platform=target_platform,
                        timeout=timeout,
                        mode="installed",
                        runtime_root=runtime_root,
                    ))
    reported_skills = {
        str(item.get("skill")) for item in results if isinstance(item.get("skill"), str)
    }
    for skill in sorted(result_skill_set - reported_skills):
        results.append({
            "status": "failed",
            "mode": "installed",
            "runtime_root": None,
            "skill": skill,
            "runner": None,
            "checked": 0,
            "results": [],
            "failure_kind": "result-row-omitted",
            "reason": "installed runtime smoke did not produce a result row for the selected skill",
        })
    unknown_coverage_failures = [
        item
        for item in results
        if item.get("failure_kind") == "unknown-coverage"
    ]
    status = aggregate_runtime_status(results)
    if unknown_coverage_failures:
        status = "failed"
    declared_exclusions = [
        {
            "skill": item["skill"],
            "runtime_root": item["runtime_root"],
            "coverage": item["coverage"],
            "reason": item["reason"],
        }
        for item in results
        if item.get("status") == "declared-exclusion"
    ]
    return installed_runtime_smoke_report(
        status=status,
        mode="installed",
        platform=target_platform,
        selected_skills=result_skills,
        coverage=runtime_smoke_coverage_rows(manifests),
        checked=len([item for item in results if item.get("status") != "declared-exclusion"]),
        unknown_coverage_count=len(unknown_coverage_failures),
        missing_managed_runtime_count=len(missing_managed_runtime),
        missing_managed_runtime_skills=missing_managed_runtime,
        managed_state_verify_status=integrity.get("status"),
        managed_state_checked=integrity.get("checked", 0),
        runtime_state_coverage_status="ok",
        runtime_state_expected_count=len(expected_runtime_artifacts),
        runtime_state_selected_record_count=len(scoped_runtime_artifacts),
        runtime_state_missing_count=0,
        runtime_state_missing_records=[],
        runtime_state_extra_count=0,
        runtime_state_extra_records=[],
        runtime_state_foreign_platform_count=len(comparison["foreign_platform"]),
        runtime_state_foreign_platform_records=comparison["foreign_platform"],
        runtime_state_duplicate_count=0,
        runtime_state_duplicate_records=[],
        runtime_state_mismatched_count=0,
        runtime_state_mismatched_records=[],
        runtime_boundary_violation_count=0,
        runtime_boundary_violations=[],
        declared_exclusion_count=len(declared_exclusions),
        declared_exclusions=declared_exclusions,
        results=results,
    )


RUNTIME_STATE_DESCRIPTOR_FIELDS = (
    "artifact_type",
    "managed",
    "agent",
    "owner",
    "skill",
    "artifact_id",
    "artifact_name",
    "runtime_root",
    "artifact",
    "target_relpath",
    "source_relpath",
    "source_sha256",
    "canonical_source_sha256",
    "mode",
    "newline_policy",
    "file_type",
    "platforms",
)


def installed_runtime_failure_rows(
    skills: list[str],
    *,
    failure_kind: str,
    reason: str,
    missing_skills: set[str] | None = None,
    unknown_skills: set[str] | None = None,
    not_runtime_skills: set[str] | None = None,
    roots_by_skill: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    missing = missing_skills or set()
    unknown = unknown_skills or set()
    not_runtime = not_runtime_skills or set()
    roots = roots_by_skill or {}
    rows: list[dict[str, Any]] = []
    for skill in skills:
        skill_roots = roots.get(skill, [])
        row_failure_kind = failure_kind
        row_reason = reason
        if skill in missing:
            row_failure_kind = "missing-managed-runtime"
            row_reason = "requested runtime skill has no managed runtime files"
        elif skill in not_runtime:
            row_failure_kind = "not-runtime-backed"
            row_reason = "requested skill has no managed runtime surface"
        elif skill in unknown:
            row_failure_kind = "unknown-runtime-skill"
            row_reason = "managed runtime state names a skill absent from the current manifest"
        rows.append({
            "status": "failed",
            "mode": "installed",
            "runtime_root": skill_roots[0] if len(skill_roots) == 1 else None,
            "runtime_roots": skill_roots,
            "skill": skill,
            "runner": None,
            "checked": 0,
            "results": [],
            "failure_kind": row_failure_kind,
            "reason": row_reason,
        })
    return rows


def valid_selected_runtime_root(root: Path, artifact: dict[str, Any]) -> str | None:
    runtime_root_text = artifact.get("runtime_root")
    if not isinstance(runtime_root_text, str) or not runtime_root_text.strip():
        return None
    runtime_root = Path(runtime_root_text)
    try:
        safe = (
            runtime_root.is_absolute()
            and normalized_path_within(root, runtime_root)
            and resolved_path_within(root, runtime_root)
        )
    except (OSError, RuntimeError, ValueError):
        return None
    if not safe:
        return None
    return runtime_root_text


def runtime_roots_by_skill(
    artifacts: list[dict[str, Any]],
) -> dict[str, list[str]]:
    grouped: dict[str, set[str]] = {}
    for artifact in artifacts:
        skill = artifact.get("skill")
        runtime_root_text = artifact.get("runtime_root")
        if (
            isinstance(skill, str)
            and isinstance(runtime_root_text, str)
            and runtime_root_text.strip()
            and Path(runtime_root_text).is_absolute()
        ):
            grouped.setdefault(skill, set()).add(runtime_root_text)
    return {skill: sorted(runtime_roots) for skill, runtime_roots in grouped.items()}


def expected_runtime_state_records(
    manifests: dict[str, Any],
    *,
    runtime_root: Path,
    skills: set[str],
    platform: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    runtime_manifest = manifests.get("runtime", {})
    runtime_specs = runtime_manifest.get("skills", {})
    entries: list[tuple[str, dict[str, Any], str]] = []
    issues: list[dict[str, Any]] = []
    runners = runtime_manifest.get("runners", [])
    if not isinstance(runners, list):
        issues.append({"kind": "invalid-runtime-runner-manifest"})
        runners = []
    for entry in runners:
        if not isinstance(entry, dict):
            issues.append({"kind": "invalid-runtime-runner-entry"})
            continue
        if runtime_entry_applies(entry, platform):
            target = entry.get("target")
            artifact_name = PurePosixPath(target).name if isinstance(target, str) else ""
            entries.append(("runtime-runner", entry, artifact_name))
    for skill in sorted(skills):
        spec = runtime_specs.get(skill) if isinstance(runtime_specs, dict) else None
        if not isinstance(spec, dict) or not isinstance(spec.get("files", []), list):
            issues.append({"kind": "invalid-runtime-skill-manifest", "skill": skill})
            continue
        for entry in spec.get("files", []):
            if not isinstance(entry, dict):
                issues.append({"kind": "invalid-runtime-skill-entry", "skill": skill})
                continue
            if runtime_entry_applies(entry, platform):
                target = entry.get("target")
                entries.append((skill, entry, target if isinstance(target, str) else ""))

    expected: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    for skill, entry, artifact_name in entries:
        source_relpath = entry.get("source")
        target_relpath = entry.get("target")
        if (
            not isinstance(source_relpath, str)
            or not source_relpath
            or not isinstance(target_relpath, str)
            or not target_relpath
        ):
            issues.append({
                "kind": "invalid-current-manifest-runtime-descriptor",
                "skill": skill,
            })
            continue
        source_relative = PurePosixPath(source_relpath)
        target_relative = PurePosixPath(target_relpath)
        if (
            source_relative.is_absolute()
            or ".." in source_relative.parts
            or target_relative.is_absolute()
            or ".." in target_relative.parts
        ):
            issues.append({
                "kind": "unsafe-current-manifest-runtime-descriptor",
                "skill": skill,
                "target_relpath": target_relpath,
            })
            continue
        target_key = os.path.normcase(target_relpath.replace("/", os.sep))
        if target_key in seen_targets:
            issues.append({
                "kind": "duplicate-current-manifest-runtime-target",
                "skill": skill,
                "target_relpath": target_relpath,
            })
            continue
        seen_targets.add(target_key)
        source = RUNTIME_SOURCE_ROOT.joinpath(*source_relative.parts)
        try:
            installed_hash = runtime_expected_sha256(source, entry)
            canonical_hash = sha256_file(source)
        except (OSError, RuntimeError, ValueError):
            installed_hash = None
            canonical_hash = None
        if installed_hash is None or canonical_hash is None:
            issues.append({
                "kind": "canonical-runtime-source-unavailable",
                "skill": skill,
                "target_relpath": target_relpath,
            })
        artifact_path = runtime_root.joinpath(*target_relative.parts)
        expected.append({
            "artifact_type": "runtime-file",
            "managed": True,
            "agent": "runtime",
            "owner": "runtime",
            "skill": skill,
            "artifact_id": f"runtime-file:{skill}:{artifact_name}",
            "artifact_name": artifact_name,
            "runtime_root": str(runtime_root),
            "artifact": str(artifact_path),
            "target_relpath": target_relpath,
            "source_path": str(source),
            "source_relpath": source_relpath,
            "source_sha256": installed_hash,
            "canonical_source_sha256": canonical_hash,
            "mode": entry.get("mode", "0644"),
            "newline_policy": entry.get("newline"),
            "file_type": entry.get("type", "text"),
            "platforms": entry.get("platforms", []),
        })
    return expected, issues


def runtime_state_record_key(
    artifact: dict[str, Any],
) -> tuple[str, str, str] | None:
    values = (
        artifact.get("runtime_root"),
        artifact.get("skill"),
        artifact.get("target_relpath"),
    )
    if not all(isinstance(value, str) and value for value in values):
        return None
    return values  # type: ignore[return-value]


def runtime_state_record_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "runtime_root": artifact.get("runtime_root"),
        "skill": artifact.get("skill"),
        "target_relpath": artifact.get("target_relpath"),
    }


def compare_runtime_state_records(
    actual: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    *,
    host_platform: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    actual_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    expected_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    mismatched: list[dict[str, Any]] = []
    for artifact in actual:
        key = runtime_state_record_key(artifact)
        if key is None:
            mismatched.append({
                "kind": "runtime-record-identity-invalid",
                **runtime_state_record_summary(artifact),
            })
            continue
        actual_by_key.setdefault(key, []).append(artifact)
    for artifact in expected:
        key = runtime_state_record_key(artifact)
        if key is not None:
            expected_by_key[key] = artifact

    missing = [
        runtime_state_record_summary(expected_by_key[key])
        for key in sorted(set(expected_by_key) - set(actual_by_key))
    ]
    extra: list[dict[str, Any]] = []
    foreign_platform: list[dict[str, Any]] = []
    for key in sorted(set(actual_by_key) - set(expected_by_key)):
        artifact = actual_by_key[key][0]
        declared = artifact.get("platforms")
        # A record whose entry does not declare this platform is outside the
        # current closure by construction, not an unmanaged extra. Native
        # Windows and WSL share a runtime root, so POSIX-only entries installed
        # by a WSL run legitimately persist in a root a Windows run also owns.
        # They are reported separately rather than failing state coverage.
        if (
            host_platform
            and isinstance(declared, list)
            and declared
            and host_platform not in declared
        ):
            foreign_platform.append(runtime_state_record_summary(artifact))
            continue
        extra.append(runtime_state_record_summary(artifact))
    duplicates = [
        {
            **runtime_state_record_summary(actual_by_key[key][0]),
            "count": len(actual_by_key[key]),
        }
        for key in sorted(actual_by_key)
        if len(actual_by_key[key]) > 1
    ]
    for key in sorted(set(actual_by_key) & set(expected_by_key)):
        expected_artifact = expected_by_key[key]
        for actual_artifact in actual_by_key[key]:
            fields = [
                field
                for field in RUNTIME_STATE_DESCRIPTOR_FIELDS
                if actual_artifact.get(field) != expected_artifact.get(field)
            ]
            if fields:
                mismatched.append({
                    "kind": "runtime-record-descriptor-mismatch",
                    **runtime_state_record_summary(expected_artifact),
                    "fields": fields,
                })
    mismatched.sort(
        key=lambda item: (
            str(item.get("runtime_root", "")),
            str(item.get("skill", "")),
            str(item.get("target_relpath", "")),
            str(item.get("kind", "")),
        )
    )
    return {
        "missing": missing,
        "extra": extra,
        "foreign_platform": foreign_platform,
        "duplicates": duplicates,
        "mismatched": mismatched,
    }


def selected_runtime_skills(manifests: dict[str, Any], skills: set[str] | None) -> list[str]:
    smoke_supported = set(runtime_smoke_skill_names(manifests)) or set(RUNTIME_SMOKE_SKILLS)
    selected = set(smoke_supported) if skills is None else set(skills)
    unknown = sorted(selected - smoke_supported)
    if unknown:
        raise ValueError("skills do not have offline runtime smoke coverage: " + ", ".join(unknown))
    return sorted(selected)


def runtime_smoke_skill_names(manifests: dict[str, Any]) -> list[str]:
    return sorted(
        skill
        for skill, spec in manifests.get("runtime", {}).get("skills", {}).items()
        if (
            isinstance(spec, dict)
            and isinstance(spec.get("smoke"), dict)
            and runtime_smoke_coverage_status(manifests, skill) == "offline-smoke"
        )
    )


def has_runtime_smoke_contract(manifests: dict[str, Any], skill: str) -> bool:
    return skill in runtime_smoke_skill_names(manifests)


def runtime_smoke_coverage_status(manifests: dict[str, Any], skill: str) -> str:
    spec = manifests.get("runtime", {}).get("skills", {}).get(skill, {})
    coverage = spec.get("smoke_coverage") if isinstance(spec, dict) else None
    if isinstance(coverage, dict) and isinstance(coverage.get("status"), str):
        return coverage["status"]
    return "unsupported"


def runtime_smoke_coverage_reason(manifests: dict[str, Any], skill: str) -> str:
    spec = manifests.get("runtime", {}).get("skills", {}).get(skill, {})
    coverage = spec.get("smoke_coverage") if isinstance(spec, dict) else None
    if isinstance(coverage, dict) and isinstance(coverage.get("reason"), str):
        return coverage["reason"]
    return "runtime skill has no smoke coverage metadata"


def runtime_smoke_coverage_rows(manifests: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for skill in sorted(manifests.get("runtime", {}).get("skills", {})):
        rows.append(
            {
                "skill": skill,
                "status": runtime_smoke_coverage_status(manifests, skill),
                "has_smoke_contract": has_runtime_smoke_contract(manifests, skill),
                "reason": runtime_smoke_coverage_reason(manifests, skill),
            }
        )
    return rows


def runner_invocations(runtime_root: Path, platform: str) -> list[dict[str, Any]]:
    if platform == "windows":
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        runners: list[dict[str, Any]] = []
        if powershell:
            runners.append({
                "name": "run_skill.ps1",
                "argv": [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(runtime_root / "run_skill.ps1"),
                ],
            })
        return runners
    return [{"name": "run_skill.sh", "argv": [str(runtime_root / "run_skill.sh")]}]


def run_smoke_case(
    manifests: dict[str, Any],
    *,
    skill: str,
    runner: dict[str, Any],
    workspace: Path,
    platform: str,
    timeout: int,
    mode: str = "temporary",
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    command_target = runtime_command_target(manifests, skill, platform, runner["name"])
    args = smoke_args(manifests, skill, workspace)
    effective_timeout = smoke_timeout(manifests, skill, timeout)
    command = [*runner["argv"], command_target]
    env = smoke_env(manifests, skill, workspace)
    checks_override: list[dict[str, Any]] | None = None
    try:
        if skill == "deep-research-workflow" and args == ["selftest"]:
            completed, checks_override = run_deep_research_workflow_smoke(
                command,
                workspace=workspace,
                timeout=effective_timeout,
                env=env,
                args=args,
            )
        else:
            completed = run_smoke_process(command, args=args, timeout=effective_timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        stdout = smoke_output_text(exc.stdout)
        stderr = smoke_output_text(exc.stderr)
        return {
            "status": "failed",
            "mode": mode,
            "runner": runner["name"],
            "skill": skill,
            "command_target": command_target,
            "args": args,
            "timeout_seconds": effective_timeout,
            "returncode": None,
            "checks": [
                {"name": "completed-before-timeout", "ok": False},
                *canary_checks(manifests, skill, stdout, stderr),
            ],
            "stdout_tail": stdout[-2000:],
            "stderr_tail": stderr[-2000:],
            **({"runtime_root": str(runtime_root)} if runtime_root is not None else {}),
        }
    except OSError as exc:
        return {
            "status": "failed",
            "mode": mode,
            "runner": runner["name"],
            "skill": skill,
            "command_target": command_target,
            "args": args,
            "timeout_seconds": effective_timeout,
            "returncode": None,
            "failure_kind": "launch-error",
            "checks": [
                {"name": "process-launched", "ok": False, "reason": type(exc).__name__},
                *canary_checks(manifests, skill, "", ""),
            ],
            "stdout_tail": "",
            "stderr_tail": f"runtime smoke launch failed: {type(exc).__name__}",
            **({"runtime_root": str(runtime_root)} if runtime_root is not None else {}),
        }
    try:
        checks = validate_smoke_output(manifests, skill, completed, args) if checks_override is None else checks_override
    except Exception as exc:
        checks = [
            {"name": "exit-zero", "ok": completed.returncode == 0},
            {"name": "output-validation", "ok": False, "reason": str(exc)},
        ]
    # Appended out here rather than inside validate_smoke_output, which returns
    # early on a non-zero exit and is bypassed entirely when parsing raises or a
    # checks_override is supplied. Those are the paths a leak most likely takes,
    # so the canary scan has to outlive all of them.
    checks = [*checks, *canary_checks(manifests, skill, completed.stdout, completed.stderr)]
    status = "ok" if completed.returncode == 0 and all(check["ok"] for check in checks) else "failed"
    result = {
        "status": status,
        "mode": mode,
        "runner": runner["name"],
        "skill": skill,
        "command_target": command_target,
        "args": args,
        "timeout_seconds": effective_timeout,
        "returncode": completed.returncode,
        "checks": checks,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }
    if runtime_root is not None:
        result["runtime_root"] = str(runtime_root)
    return result


def run_smoke_process(
    command: list[str],
    *,
    args: list[str],
    timeout: int,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*command, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=env,
    )


def run_deep_research_workflow_smoke(
    command: list[str],
    *,
    workspace: Path,
    timeout: int,
    env: dict[str, str],
    args: list[str],
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]] | None]:
    first = run_smoke_process(command, args=args, timeout=timeout, env=env)
    if first.returncode == 0 or not is_deep_research_selftest_unsupported(first):
        return first, None

    smoke_dir = workspace / "runtime-smoke"
    out_dir = smoke_dir / "deep"
    init_args = ["init", "--dir", str(smoke_dir), "--subdir", "deep", "--structured", "--schema-version", "2"]
    init_result = run_smoke_process(command, args=init_args, timeout=timeout, env=env)

    checks: list[dict[str, Any]] = [
        {"name": "deep-research-selftest-unsupported", "ok": True},
        {"name": "deep-research-smoke-init", "ok": init_result.returncode == 0},
    ]
    if init_result.returncode != 0:
        return init_result, checks

    validate_args = [
        "validate",
        "--dir",
        str(out_dir),
        "--schema-version",
        "2",
    ]
    validate_result = run_smoke_process(command, args=validate_args, timeout=timeout, env=env)
    try:
        validate_checks = validate_smoke_output(
            {},
            "deep-research-workflow",
            validate_result,
            validate_args,
        )
    except Exception as exc:
        validate_checks = [
            {"name": "exit-zero", "ok": validate_result.returncode == 0},
            {"name": "output-validation", "ok": False, "reason": str(exc)},
        ]
    checks.extend(validate_checks)
    return validate_result, checks


def is_deep_research_selftest_unsupported(result: subprocess.CompletedProcess[str]) -> bool:
    if result.returncode == 0:
        return False
    text = f"{result.stdout} {result.stderr}".lower()
    # argparse reports an unknown subcommand as "invalid choice: 'selftest'",
    # so matching "invalid command: selftest" recognised nothing and left the
    # legacy fallback below unreachable. Keep the older wording as well.
    return "selftest" in text and ("invalid choice" in text or "invalid command" in text)


def runtime_command_target(
    manifests: dict[str, Any],
    skill: str,
    platform: str,
    runner_name: str | None = None,
) -> str:
    contract_target = runtime_contract_command_target(manifests, skill, platform, runner_name)
    if contract_target is not None:
        return contract_target
    if platform == "windows":
        suffixes = (".ps1", ".py")
    else:
        suffixes = (".sh",)
    spec = manifests["runtime"]["skills"][skill]
    for suffix in suffixes:
        for entry in spec.get("files", []):
            target = entry.get("target", "")
            if target.endswith(suffix) and platform in entry.get("platforms", []):
                return target.removeprefix("workspace/")
    raise ValueError(f"no {platform} runtime command declared for {skill}")


def runtime_contract_command_target(
    manifests: dict[str, Any],
    skill: str,
    platform: str,
    runner_name: str | None = None,
) -> str | None:
    spec = manifests.get("runtime", {}).get("skills", {}).get(skill, {})
    smoke = spec.get("smoke") if isinstance(spec, dict) else None
    if not isinstance(smoke, dict):
        return None
    command = smoke.get("command")
    if isinstance(command, dict):
        keys: tuple[str, ...]
        if platform == "windows" and runner_name == "run_skill.ps1":
            keys = ("windows_ps1", "windows")
        elif platform == "windows":
            keys = ("windows", "windows_ps1")
        else:
            keys = (platform,)
        target = next((command.get(key) for key in keys if command.get(key)), None)
    else:
        target = command
    if not isinstance(target, str) or not target:
        return None
    return normalize_runtime_command_target(target)


def normalize_runtime_command_target(target: str) -> str:
    if target.startswith("workspace/"):
        target = target.removeprefix("workspace/")
    path = PurePosixPath(target)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe runtime smoke command target: {target}")
    return path.as_posix()


def smoke_args(manifests: dict[str, Any], skill: str, workspace: Path) -> list[str]:
    smoke_dir = workspace / "runtime-smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    spec = manifests.get("runtime", {}).get("skills", {}).get(skill, {})
    smoke = spec.get("smoke") if isinstance(spec, dict) else None
    if isinstance(smoke, dict) and isinstance(smoke.get("args"), list):
        replacements = {
            "{workspace}": str(workspace),
            "{smoke_dir}": str(smoke_dir),
        }
        args = []
        for item in smoke["args"]:
            text = str(item)
            for placeholder, value in replacements.items():
                text = text.replace(placeholder, value)
            args.append(text)
        return args
    if skill == "formal-skeleton-helper":
        return ["--output-dir", str(smoke_dir / "formal")]
    if skill == "get-available-resources":
        return ["--output", str(smoke_dir / "resources.json")]
    if skill == "deep-research-workflow":
        return ["init", "--dir", str(smoke_dir), "--subdir", "deep", "--structured"]
    if skill == "axiom-axle-mcp":
        return ["smoke"]
    if skill == "lean-explore-mcp":
        return ["smoke"]
    if skill in {"lean-formalization-intake", "lean-research-library", "lean-strict-verification-gate"}:
        return ["doctor"]
    return []


def smoke_timeout(manifests: dict[str, Any], skill: str, requested_timeout: int) -> int:
    smoke = manifests.get("runtime", {}).get("skills", {}).get(skill, {}).get("smoke", {})
    contract_timeout = smoke.get("timeout_seconds") if isinstance(smoke, dict) else None
    if isinstance(contract_timeout, int) and contract_timeout > 0:
        return min(requested_timeout, contract_timeout)
    return requested_timeout


def smoke_env(manifests: dict[str, Any], skill: str, workspace: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not env_name_looks_secret(key)
    }
    env["AAS_ALLOW_EXTERNAL_RUNTIME_WORKSPACE"] = "1"
    env["AAS_RUNTIME_WORKSPACE"] = str(workspace)
    env["PYTHONUTF8"] = env.get("PYTHONUTF8", "1")
    env["PYTHONIOENCODING"] = env.get("PYTHONIOENCODING", "utf-8")
    # The smoke runs a dispatcher out of the canonical tree, and importing it
    # writes ``__pycache__`` directories the runtime inventory check then denies
    # as sources it never enrolled. Keep the child from emitting bytecode so a
    # smoke run leaves the tree exactly as it found it.
    env["PYTHONDONTWRITEBYTECODE"] = env.get("PYTHONDONTWRITEBYTECODE", "1")
    smoke = manifests.get("runtime", {}).get("skills", {}).get(skill, {}).get("smoke", {})
    env_canaries = smoke.get("env_canaries", {}) if isinstance(smoke, dict) else {}
    if isinstance(env_canaries, dict):
        for key, value in env_canaries.items():
            if isinstance(key, str) and isinstance(value, str):
                env[key] = value
    return env


def env_name_looks_secret(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY", "AUTH"))


def canary_checks(
    manifests: dict[str, Any],
    skill: str,
    stdout: str,
    stderr: str,
) -> list[dict[str, Any]]:
    """Assert that none of the injected canaries came back out.

    ``smoke_env`` plants these values in the child environment, so any occurrence
    in the output means the skill echoed something it was handed as a secret. The
    scan reads the raw streams rather than the parsed payload: a canary printed to
    stderr, or beside the JSON on stdout, has leaked just as surely as one carried
    inside it. Callers run this on every exit path, because a traceback is the
    most likely place for an environment value to escape.
    """
    smoke = manifests.get("runtime", {}).get("skills", {}).get(skill, {}).get("smoke", {})
    env_canaries = smoke.get("env_canaries", {}) if isinstance(smoke, dict) else {}
    if not isinstance(env_canaries, dict):
        return []
    combined = f"{stdout or ''}\n{stderr or ''}"
    return [
        {"name": f"canary-not-leaked:{name}", "ok": value not in combined}
        for name, value in sorted(env_canaries.items())
        if isinstance(name, str) and isinstance(value, str)
    ]


def validate_smoke_output(
    manifests: dict[str, Any],
    skill: str,
    completed: subprocess.CompletedProcess[str],
    args: list[str],
) -> list[dict[str, Any]]:
    checks = [{"name": "exit-zero", "ok": completed.returncode == 0}]
    if completed.returncode != 0:
        return checks
    if skill == "graph-verifier":
        payload = parse_json_stdout(completed.stdout)
        checks.append({"name": "json-ok", "ok": payload.get("ok") is True})
        checks.append({"name": "matches-expected", "ok": payload.get("matches_expected") is True})
    elif skill == "formal-skeleton-helper":
        payload = parse_json_stdout(completed.stdout)
        output_path = Path(payload.get("path", ""))
        checks.append({"name": "json-ok", "ok": payload.get("ok") is True})
        checks.append({"name": "output-file-exists", "ok": output_path.is_file()})
    elif skill == "get-available-resources":
        output_path = Path(args[args.index("--output") + 1]) if "--output" in args else Path(".codex_resources.json")
        checks.append({"name": "output-file-exists", "ok": output_path.is_file()})
        payload = json.loads(output_path.read_text(encoding="utf-8")) if output_path.is_file() else {}
        checks.append({"name": "resource-json-has-os", "ok": "os" in payload})
        checks.append({"name": "resource-json-has-cpu", "ok": "cpu" in payload})
    elif skill == "deep-research-workflow":
        if args == ["selftest"]:
            payload = parse_json_stdout(completed.stdout)
            names = {item.get("name") for item in payload.get("scenarios", []) if isinstance(item, dict)}
            required = {
                "v2_ready_success",
                "v2_ready_failure",
                "v2_ready_with_caveats_success",
                "v2_ready_with_caveats_failure",
                "agd_evidence_success",
                "agd_evidence_failure",
                "weak_computation_failure",
                "formal_promotion_success",
                "formal_promotion_failure",
                "artifact_ref_path_safety",
            }
            checks.append({"name": "json-ok", "ok": payload.get("status") == "ok"})
            checks.append({"name": "schema-version", "ok": payload.get("schema_version") == "deep-research.selftest.v1"})
            checks.append({"name": "positive-count", "ok": payload.get("positive_count") == 4})
            checks.append({"name": "negative-count", "ok": payload.get("negative_count") == 6})
            checks.append({"name": "scenario-names", "ok": names == required})
            checks.append({"name": "scenario-results", "ok": all(item.get("passed") for item in payload.get("scenarios", []) if isinstance(item, dict))})
        else:
            dir_index = args.index("--dir")
            out_dir = Path(args[dir_index + 1])
            if "--subdir" in args:
                out_dir = out_dir / args[args.index("--subdir") + 1]
            for name in (
                "sources.md",
                "analysis.md",
                "report.md",
                "sources.jsonl",
                "claims.jsonl",
                "guards.jsonl",
                "delivery.json",
            ):
                checks.append({"name": f"{name}-exists", "ok": (out_dir / name).is_file()})
            checks.append({"name": "delegation-dir-exists", "ok": (out_dir / "delegation").is_dir()})
            if "validate" in args:
                payload = parse_json_stdout(completed.stdout)
                checks.append({"name": "json-ok", "ok": payload.get("status") == "ok"})
    elif skill in {"lean-formalization-intake", "lean-strict-verification-gate"}:
        payload = parse_json_stdout(completed.stdout)
        checks.append({"name": "json-ok", "ok": payload.get("status") == "ok"})
        checks.append({"name": "no-auto-install", "ok": payload.get("no_auto_install") is True})
        checks.append({"name": "installs-not-attempted", "ok": payload.get("installs_attempted") is False})
        checks.append({
            "name": "lean-status-recorded",
            "ok": payload.get("tool_status", {}).get("lean", {}).get("status") in {"available", "tool_unavailable"},
        })
    elif skill == "lean-research-library":
        payload = parse_json_stdout(completed.stdout)
        checks.append({"name": "json-ok", "ok": payload.get("status") == "ok"})
        checks.append({"name": "no-auto-install", "ok": payload.get("no_auto_install") is True})
        checks.append({"name": "installs-not-attempted", "ok": payload.get("installs_attempted") is False})
        checks.append({"name": "network-not-required", "ok": payload.get("network_required") is False})
        checks.append({
            "name": "lean-status-recorded",
            "ok": payload.get("tool_status", {}).get("lean", {}).get("status") in {"available", "tool_unavailable"},
        })
        # an unconfigured library is a reported state with guidance, never a smoke failure
        checks.append({"name": "library-state-recorded", "ok": isinstance(payload.get("library_configured"), bool)})
    elif skill == "axiom-axle-mcp":
        payload = parse_json_stdout(completed.stdout)
        checks.append({"name": "json-ok", "ok": payload.get("status") == "ok"})
        checks.append({"name": "offline-smoke", "ok": payload.get("smoke_mode") == "offline"})
        checks.append({"name": "no-auto-install", "ok": payload.get("no_auto_install") is True})
        checks.append({"name": "installs-not-attempted", "ok": payload.get("installs_attempted") is False})
        checks.append({"name": "network-not-required", "ok": payload.get("network_required") is False})
        checks.append({"name": "live-api-not-attempted", "ok": payload.get("live_api_attempted") is False})
        checks.append({"name": "server-not-started", "ok": payload.get("server_started") is False})
        checks.append({"name": "config-not-written", "ok": payload.get("config_written") is False})
        checks.append({"name": "placeholder-present", "ok": payload.get("snippet_contains_placeholder") is True})
        checks.append({"name": "package-pinned", "ok": payload.get("snippet_package_pinned") is True})
    elif skill == "opengauss":
        payload = parse_json_stdout(completed.stdout)
        checks.append({"name": "json-ok", "ok": payload.get("status") == "ok" and payload.get("ok") is True})
        checks.append({"name": "offline-smoke", "ok": payload.get("smoke_mode") == "offline"})
        checks.append({"name": "no-auto-install", "ok": payload.get("no_auto_install") is True})
        checks.append({"name": "installs-not-attempted", "ok": payload.get("installs_attempted") is False})
        checks.append({"name": "network-not-required", "ok": payload.get("network_required") is False})
        checks.append({"name": "live-api-not-attempted", "ok": payload.get("live_api_attempted") is False})
        checks.append({"name": "server-not-started", "ok": payload.get("server_started") is False})
        checks.append({"name": "config-not-written", "ok": payload.get("config_written") is False})
        checks.append({"name": "gauss-not-launched", "ok": payload.get("gauss_launched") is False})
        checks.append({"name": "placeholder-present", "ok": payload.get("snippet_contains_placeholder") is True})
        checks.append({"name": "install-pointer-present", "ok": payload.get("snippet_has_install_pointer") is True})
        checks.append({"name": "windows-live-policy", "ok": payload.get("native_windows_refused") is True})
        policy = payload.get("evidence_policy") if isinstance(payload.get("evidence_policy"), dict) else {}
        checks.append({
            "name": "evidence-policy-present",
            "ok": "opengauss_run" in policy and "formal_check" in policy,
        })
    elif skill == "lean-explore-mcp":
        payload = parse_json_stdout(completed.stdout)
        checks.append({"name": "json-ok", "ok": payload.get("status") == "ok"})
        checks.append({"name": "offline-smoke", "ok": payload.get("smoke_mode") == "offline"})
        checks.append({"name": "no-auto-install", "ok": payload.get("no_auto_install") is True})
        checks.append({"name": "installs-not-attempted", "ok": payload.get("installs_attempted") is False})
        checks.append({"name": "network-not-required", "ok": payload.get("network_required") is False})
        checks.append({"name": "live-api-not-attempted", "ok": payload.get("live_api_attempted") is False})
        checks.append({"name": "server-not-started", "ok": payload.get("server_started") is False})
        checks.append({"name": "config-not-written", "ok": payload.get("config_written") is False})
        checks.append({"name": "downloads-not-attempted", "ok": payload.get("downloads_attempted") is False})
        checks.append({"name": "api-placeholder-present", "ok": payload.get("api_snippet_contains_placeholder") is True})
        checks.append({"name": "local-snippet-omits-api-key", "ok": payload.get("local_snippet_omits_api_key") is True})
    elif skill == "self-improving-agent":
        payload = parse_json_stdout(completed.stdout)
        checks.append({"name": "json-ok", "ok": payload.get("status") == "ok"})
        checks.append({"name": "offline-smoke", "ok": payload.get("smoke_mode") == "offline"})
        checks.append({"name": "no-auto-install", "ok": payload.get("no_auto_install") is True})
        checks.append({"name": "network-not-required", "ok": payload.get("network_required") is False})
        checks.append({"name": "live-api-not-attempted", "ok": payload.get("live_api_attempted") is False})
        checks.append({"name": "package-install-not-attempted", "ok": payload.get("package_install_attempted") is False})
        checks.append({"name": "server-not-started", "ok": payload.get("server_started") is False})
        checks.append({"name": "config-not-written", "ok": payload.get("config_written") is False})
        checks.append({"name": "integration-plan-fields", "ok": bool(payload.get("integration_plan_fields"))})
        checks.append({"name": "windows-error-patterns", "ok": payload.get("windows_error_patterns") is True})
        checks.append({"name": "windows-safety-patterns", "ok": payload.get("windows_safety_patterns") is True})
    elif skill == "submission-venue-selector":
        payload = parse_json_stdout(completed.stdout)
        checks.append({"name": "json-ok", "ok": payload.get("status") == "ok"})
        checks.append({"name": "offline-smoke", "ok": payload.get("smoke_mode") == "offline"})
        checks.append({"name": "network-not-required", "ok": payload.get("network_required") is False})
        checks.append({"name": "live-api-not-attempted", "ok": payload.get("live_api_attempted") is False})
        checks.append({"name": "package-install-not-attempted", "ok": payload.get("package_install_attempted") is False})
        checks.append({"name": "server-not-started", "ok": payload.get("server_started") is False})
        checks.append({"name": "config-not-written", "ok": payload.get("config_written") is False})
        checks.append({"name": "real-secrets-not-read", "ok": payload.get("real_secrets_read") is False})
        checks.append({"name": "downloads-not-attempted", "ok": payload.get("downloads_attempted") is False})
        checks.append({"name": "mutations-not-attempted", "ok": payload.get("mutations_attempted") is False})
        checks.append({"name": "schema-list-present", "ok": "delivery.json" in payload.get("schemas", [])})
    elif skill == "autonomous-research-loop-runtime":
        payload = parse_json_stdout(completed.stdout)
        checks.append({"name": "json-ok", "ok": payload.get("status") == "ok"})
        checks.append({"name": "offline-smoke", "ok": payload.get("smoke_mode") == "offline"})
        checks.append({"name": "network-not-required", "ok": payload.get("network_required") is False})
        checks.append({"name": "live-api-not-attempted", "ok": payload.get("live_api_attempted") is False})
        checks.append({"name": "package-install-not-attempted", "ok": payload.get("package_install_attempted") is False})
        checks.append({"name": "server-not-started", "ok": payload.get("server_started") is False})
        checks.append({"name": "config-not-written", "ok": payload.get("config_written") is False})
        checks.append({"name": "provider-cli-not-attempted", "ok": payload.get("provider_cli_attempted") is False})
        checks.append({"name": "subagents-not-spawned", "ok": payload.get("subagents_spawned") is False})
        checks.append({"name": "run-dir-created", "ok": payload.get("run_dir_created") is True})
        checks.append({"name": "validation-ok", "ok": payload.get("validation_status") == "ok"})
    elif skill == "url-to-screenshot-runtime":
        payload = parse_json_stdout(completed.stdout)
        checks.append({"name": "json-ok", "ok": payload.get("ok") is True})
        checks.append({"name": "status-ok", "ok": payload.get("status") == "ok"})
        checks.append({"name": "no-failures", "ok": payload.get("failures") == []})
        u2s_passed = payload.get("passed")
        u2s_total = payload.get("total")
        checks.append({
            "name": "all-passed",
            "ok": isinstance(u2s_passed, int) and isinstance(u2s_total, int) and u2s_passed == u2s_total,
        })
        checks.append({"name": "offline-smoke", "ok": payload.get("smoke_mode") == "offline"})
        checks.append({"name": "network-not-required", "ok": payload.get("network_required") is False})
        checks.append({"name": "live-api-not-attempted", "ok": payload.get("live_api_attempted") is False})
        checks.append({"name": "package-install-not-attempted", "ok": payload.get("package_install_attempted") is False})
        checks.append({"name": "server-not-started", "ok": payload.get("server_started") is False})
        checks.append({"name": "browser-not-launched", "ok": payload.get("browser_launched") is False})
    elif skill == "venue-ranking-evidence":
        payload = parse_json_stdout(completed.stdout)
        checks.append({"name": "json-ok", "ok": payload.get("status") == "ok"})
        checks.append({"name": "offline-smoke", "ok": payload.get("smoke_mode") == "offline"})
        checks.append({"name": "network-not-required", "ok": payload.get("network_required") is False})
        checks.append({"name": "live-api-not-attempted", "ok": payload.get("live_api_attempted") is False})
        checks.append({"name": "config-not-written", "ok": payload.get("config_written") is False})
        checks.append({"name": "real-secrets-not-read", "ok": payload.get("real_secrets_read") is False})
        checks.append({
            "name": "source-registry-validated",
            "ok": isinstance(payload.get("validated_sources"), int) and payload.get("validated_sources", 0) > 0,
        })
        checks.append({"name": "ambiguity-preserved", "ok": payload.get("ambiguous_fixture_matches") == 2})
    elif skill == "remote-bridge":
        payload = parse_json_stdout(completed.stdout)
        checks.append({"name": "json-ok", "ok": payload.get("ok") is True and payload.get("status") == "ok"})
        checks.append({"name": "offline-smoke", "ok": payload.get("smoke_mode") == "offline"})
        checks.append({"name": "network-not-required", "ok": payload.get("network_required") is False})
        checks.append({"name": "live-api-not-attempted", "ok": payload.get("live_api_attempted") is False})
        checks.append({"name": "package-install-not-attempted", "ok": payload.get("package_install_attempted") is False})
        checks.append({"name": "server-not-started", "ok": payload.get("server_started") is False})
        checks.append({"name": "config-not-written", "ok": payload.get("config_written") is False})
        checks.append({"name": "real-secrets-not-read", "ok": payload.get("real_secrets_read") is False})
        required_checks = {
            "arm_conflict",
            "digest",
            "cas",
            "single_use_approval",
            "inbox_once",
            "parse",
            "redaction",
        }
        reported = set(payload.get("checks") or [])
        checks.append({"name": "selftest-checks", "ok": required_checks.issubset(reported)})
    elif skill in {"manim-math-animation", "slides-to-video"}:
        payload = parse_json_stdout(completed.stdout)
        clip_passed = payload.get("passed")
        clip_total = payload.get("total")
        checks.append({"name": "json-ok", "ok": payload.get("ok") is True})
        checks.append({"name": "no-failures", "ok": payload.get("failures") == []})
        checks.append({
            "name": "all-passed",
            "ok": isinstance(clip_passed, int) and isinstance(clip_total, int) and clip_passed == clip_total,
        })
        # A selftest that asserts nothing also reports nothing failing, so an
        # empty run has to count as a failure rather than a clean sheet.
        checks.append({"name": "checks-not-empty", "ok": isinstance(clip_total, int) and clip_total > 0})
    elif skill == "send-email":
        payload = parse_json_stdout(completed.stdout)
        mail_passed = payload.get("passed")
        checks.append({"name": "json-ok", "ok": payload.get("ok") is True})
        checks.append({"name": "selftest-command", "ok": payload.get("command") == "selftest"})
        checks.append({"name": "no-failed-checks", "ok": payload.get("failed") == 0})
        checks.append({"name": "checks-not-empty", "ok": isinstance(mail_passed, int) and mail_passed > 0})
    return checks


def make_trusted_scratch_directory(path: Path, ceiling: Path) -> None:
    """Create ``path`` with an umask-independent, owner-write-only mode.

    run_skill.sh walks the command's parent chain and refuses any component a
    group or other can write.  A bare ``mkdir`` takes the ambient umask, so on a
    host carrying the common ``0002`` user-private-group umask every scratch
    directory lands at ``0775`` and the credential-bearing skills fail the
    command-chain check before their offline contract ever runs.  The installed
    runtime this copies from is owner-only, so normalising the copy is what
    makes it faithful.  ``ceiling`` is the tempfile root, already ``0700`` and
    left alone.
    """
    path.mkdir(parents=True, exist_ok=True)
    current = path
    while current != ceiling and ceiling in current.parents:
        os.chmod(current, 0o755)
        current = current.parent


def copy_installed_runtime_workspace(
    runtime_root: Path,
    artifacts: list[dict[str, Any]],
    scratch_workspace: Path,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    runtime_workspace = runtime_root / "workspace"
    make_trusted_scratch_directory(scratch_workspace, scratch_workspace.parent)
    for artifact in artifacts:
        target_relpath = artifact.get("target_relpath")
        if not isinstance(target_relpath, str):
            continue
        source = Path(str(artifact.get("artifact", "")))
        check_prefix = f"copy:{target_relpath}"
        try:
            source_scope = runtime_workspace if target_relpath.startswith("workspace/") else runtime_root
            if not normalized_path_within(source_scope, source) or not resolved_path_within(source_scope, source):
                checks.append({"name": f"{check_prefix}:contained", "ok": False})
                continue
            if target_relpath.startswith("workspace/"):
                rel = PurePosixPath(target_relpath).relative_to("workspace")
                dest = scratch_workspace.joinpath(*rel.parts)
            else:
                rel = PurePosixPath(target_relpath)
                dest = scratch_workspace.parent.joinpath(*rel.parts)
            if not normalized_path_within(scratch_workspace.parent, dest):
                checks.append({"name": f"{check_prefix}:scratch-contained", "ok": False})
                continue
            flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_BINARY", 0)
            )
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(source, flags)
            try:
                information = os.fstat(descriptor)
                if not stat.S_ISREG(information.st_mode):
                    raise OSError("source is not a regular file")
                expected_mode = artifact.get("mode")
                if (
                    os.name != "nt"
                    and isinstance(expected_mode, str)
                    and stat.S_IMODE(information.st_mode) != int(expected_mode, 8)
                ):
                    raise OSError("source mode changed after integrity verification")
                digest = hashlib.sha256()
                payload = bytearray()
                while True:
                    block = os.read(descriptor, 64 * 1024)
                    if not block:
                        break
                    digest.update(block)
                    payload.extend(block)
            finally:
                os.close(descriptor)
            expected_hash = artifact.get("source_sha256")
            if not isinstance(expected_hash, str) or "sha256:" + digest.hexdigest() != expected_hash:
                checks.append({"name": f"{check_prefix}:source-hash", "ok": False})
                continue
            make_trusted_scratch_directory(dest.parent, scratch_workspace.parent)
            dest.write_bytes(payload)
            if isinstance(expected_mode, str):
                os.chmod(dest, int(expected_mode, 8))
            checks.append({"name": f"{check_prefix}:copied-verified", "ok": True})
        except (OSError, RuntimeError, ValueError):
            checks.append({"name": f"{check_prefix}:descriptor-copy", "ok": False})
    if not checks:
        return {
            "status": "failed",
            "reason": "no managed runtime files were available to smoke",
            "checks": [{"name": "managed-runtime-files", "ok": False}],
        }
    if not all(check["ok"] for check in checks):
        return {"status": "failed", "reason": "failed to prepare scratch runtime workspace", "checks": checks}
    return {"status": "ok", "checks": checks}


def runtime_state_boundary_violations(
    root: Path,
    runtime_roots: set[str],
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reject copied/forged state that points runtime execution outside root."""
    violations: list[dict[str, Any]] = []
    safe_roots: dict[str, Path] = {}
    for runtime_root_text in sorted(runtime_roots):
        runtime_root = Path(runtime_root_text)
        try:
            safe = (
                bool(runtime_root_text.strip())
                and runtime_root.is_absolute()
                and normalized_path_within(root, runtime_root)
                and resolved_path_within(root, runtime_root)
            )
        except (OSError, RuntimeError, ValueError):
            safe = False
        if not safe:
            violations.append({
                "kind": "runtime-root-outside-selected-root",
                "runtime_root": runtime_root_text,
            })
            continue
        safe_roots[runtime_root_text] = runtime_root
    for artifact in artifacts:
        summary = runtime_state_record_summary(artifact)
        if artifact.get("managed") is not True:
            violations.append({"kind": "runtime-managed-flag-invalid", **summary})
        runtime_root_value = artifact.get("runtime_root")
        if not isinstance(runtime_root_value, str) or not runtime_root_value.strip():
            violations.append({"kind": "runtime-root-missing", **summary})
            continue
        runtime_root_text = runtime_root_value
        runtime_root = safe_roots.get(runtime_root_text)
        if runtime_root is None:
            violations.append({"kind": "runtime-root-outside-selected-root", **summary})
            continue
        artifact_value = artifact.get("artifact")
        if not isinstance(artifact_value, str) or not artifact_value.strip():
            violations.append({"kind": "runtime-artifact-missing", **summary})
            continue
        artifact_path = Path(artifact_value)
        target_relpath = artifact.get("target_relpath")
        if not isinstance(target_relpath, str) or not target_relpath:
            violations.append({"kind": "runtime-artifact-target-missing", **summary})
            continue
        relative = PurePosixPath(target_relpath)
        if relative.is_absolute() or ".." in relative.parts or relative == PurePosixPath("."):
            violations.append({"kind": "runtime-artifact-target-unsafe", **summary})
            continue
        expected_artifact = runtime_root.joinpath(*relative.parts)
        if artifact_path != expected_artifact:
            violations.append({"kind": "runtime-artifact-path-mismatch", **summary})
            continue
        try:
            contained = (
                artifact_path.is_absolute()
                and normalized_path_within(root, artifact_path)
                and resolved_path_within(root, artifact_path)
                and normalized_path_within(runtime_root, artifact_path)
                and resolved_path_within(runtime_root, artifact_path)
            )
        except (OSError, RuntimeError, ValueError):
            contained = False
        if not contained:
            violations.append({"kind": "runtime-artifact-outside-selected-root", **summary})
    return violations


def aggregate_runtime_status(results: list[dict[str, Any]]) -> str:
    if not results:
        return "skipped"
    statuses = {str(item.get("status")) for item in results}
    if "failed" in statuses:
        return "failed"
    if statuses & {"degraded", "unsupported"}:
        return "degraded"
    return "ok"


def smoke_output_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def parse_json_stdout(stdout: str) -> dict[str, Any]:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"runtime smoke command did not emit JSON: {exc}") from exc
