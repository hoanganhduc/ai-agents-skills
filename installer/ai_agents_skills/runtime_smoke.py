from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from . import skill_python
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


DECLARED_RUNTIME_EXCLUSION_STATUSES = frozenset(
    {"manual-native", "doctor-only", "static-only"}
)
INSTALLED_RUNTIME_SMOKE_SCHEMA = "ai-agents-skills.installed-runtime-smoke.v1"
INSTALLED_RUNTIME_SMOKE_SCHEMA_VERSION = 1


def installed_runtime_smoke_report(**fields: Any) -> dict[str, Any]:
    report = {
        "schema": INSTALLED_RUNTIME_SMOKE_SCHEMA,
        "schema_version": INSTALLED_RUNTIME_SMOKE_SCHEMA_VERSION,
        "credential_launch": {"status": "skipped", "results": [], "reason": "runtime execution not reached"},
        "skill_venv": {"status": "skipped", "reason": "runtime execution not reached"},
        "functional": {"status": "skipped", "results": []},
        "live": {"status": "skipped", "results": [], "reason": "live checks require --live"},
    }
    report.update(fields)
    return report


def run_runtime_smoke(
    manifests: dict[str, Any],
    *,
    skills: set[str] | None = None,
    platform: str | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    host_platform = current_platform(platform)
    selected_skills = selected_runtime_skills(manifests, skills)
    venv = skill_venv_row()
    with tempfile.TemporaryDirectory(prefix="aas-runtime-smoke-") as tmp:
        # This root was just created by this harness. Use its physical path so
        # macOS /var -> /private/var aliases do not enter secret canary paths.
        # User-supplied secret paths still go through the strict no-follow gate.
        root = Path(tmp).resolve()
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
        workspace = runtime_root / "workspace"
        runners = runner_invocations(runtime_root, host_platform)
        if not runners and any(skill in runtime_smoke_skill_names(manifests) for skill in selected_skills):
            return {
                "status": "failed",
                "platform": host_platform,
                "selected_skills": selected_skills,
                "coverage": runtime_smoke_coverage_rows(manifests),
                "install_action_count": len(install_result.get("actions", [])),
                "verify_status": verify_result["status"],
                "checked": 0,
                "credential_launch": {"status": "skipped", "results": [], "reason": "no native runtime runner"},
                "skill_venv": venv,
                "functional": {"status": "skipped", "results": [], "reason": "functional cases require installed-runtime-smoke"},
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
                    for skill in selected_skills if skill in runtime_smoke_skill_names(manifests)
                ],
            }
        results = []
        for runner in runners:
            for skill in selected_skills:
                if skill not in runtime_smoke_skill_names(manifests):
                    continue
                results.append(run_smoke_case(
                    manifests,
                    skill=skill,
                    runner=runner,
                    workspace=workspace,
                    platform=host_platform,
                    timeout=timeout,
                ))
        credential_launch = credential_launch_canary(runtime_root, host_platform, results, manifests=manifests)
        status = "ok" if (verify_result["status"] == "ok" and all(item["status"] == "ok" for item in results)
                          and credential_launch["status"] != "failed") else "failed"
        return {
            "status": status,
            "platform": host_platform,
            "selected_skills": selected_skills,
            "coverage": runtime_smoke_coverage_rows(manifests),
            "install_action_count": len(install_result.get("actions", [])),
            "verify_status": verify_result["status"],
            "checked": len(results),
            "results": results,
            "credential_launch": credential_launch,
            "skill_venv": venv,
            "functional": {"status": "skipped", "results": [], "reason": "functional cases require installed-runtime-smoke"},
        }


def run_installed_runtime_smoke(
    root: Path,
    manifests: dict[str, Any],
    *,
    skills: set[str] | None = None,
    agents: set[str] | None = None,
    platform: str | None = None,
    timeout: int | None = None,
    require_complete_coverage: bool = False,
    require_functional: bool = False,
    live: bool = False,
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
    functional_rows: list[dict[str, Any]] = []
    live_rows: list[dict[str, Any]] = []
    credential_sections: list[dict[str, Any]] = []
    venv = skill_venv_row()
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
                if skill not in runtime_smoke_skill_names(manifests):
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
            root_results = [row for row in results if row.get("runtime_root") == str(runtime_root)]
            canary = credential_launch_canary(scratch_workspace.parent, target_platform, root_results,
                                              manifests=manifests)
            for row in canary["results"]:
                row["runtime_root"] = str(runtime_root)
            credential_sections.append(canary)
            functional_rows.extend(run_functional_smoke_cases(
                manifests, skills=selected_for_root, runtime_root=runtime_root, workspace=scratch_workspace,
                platform=target_platform, venv=venv, timeout=timeout)["results"])
        if live:
            live_rows.extend(run_live_checks(manifests, skills=selected_for_root, runtime_root=runtime_root,
                                             platform=target_platform, timeout=timeout)["results"])
    reported_skills = {
        str(item.get("skill")) for item in [*results, *functional_rows, *live_rows]
        if isinstance(item.get("skill"), str)
    }
    if not live:
        reported_skills.update(skill for skill in result_skill_set
                               if skill in live_check_skill_names(manifests)
                               and skill not in runtime_smoke_skill_names(manifests)
                               and skill not in functional_smoke_skill_names(manifests))
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
    functional = _case_section(functional_rows)
    live_section = _case_section(live_rows)
    if not live:
        live_section["reason"] = "live checks require --live"
    credential_launch = _case_section([row for section in credential_sections for row in section["results"]])
    if not credential_launch["results"]:
        credential_launch = {"status": "not-applicable" if target_platform == "windows" else "skipped",
                             "results": [], "reason": "no selected credential-bearing offline smoke"}
    reported_rows = [*results, *functional_rows, *live_rows]
    status = aggregate_runtime_status(reported_rows)
    if not reported_rows and reported_skills:
        # Live-only skills produce no offline row; that is coverage by design,
        # not a run that skipped every check.
        status = "ok"
    if (credential_launch["status"] == "failed"
            or (require_complete_coverage and any(section["status"] == "skipped" for section in credential_sections))
            or (require_functional and any(row["status"] != "ok" for row in functional_rows))):
        status = "failed"
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
        credential_launch=credential_launch,
        skill_venv=venv,
        functional=functional,
        live=live_section,
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


def skill_venv_row() -> dict[str, Any]:
    """Resolve the operator's venv before constructing any synthetic HOME."""
    prefix = os.environ.get("AAS_SKILL_VENV") or str(Path.home() / ".agents_skills_venv")
    if not os.path.lexists(prefix):
        return {"status": "absent", "path": prefix}
    try:
        base = skill_python.attested_base_python(preflight_ensurepip=False)
        admitted, reason = skill_python.admit_skill_venv(prefix, attested_python=base)
    except (OSError, ValueError, skill_python.SkillPythonError) as exc:
        return {"status": "refused", "path": prefix, "reason": str(exc)}
    return {"status": "admitted" if admitted else "refused", "path": prefix,
            **({} if admitted else {"reason": reason})}


def _case_section(rows: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = {row["status"] for row in rows}
    status = "failed" if "failed" in statuses else "ok" if "ok" in statuses else "skipped"
    return {"status": status, "results": rows}


def credential_launch_canary(
    runtime_root: Path, platform: str, results: list[dict[str, Any]], *,
    manifests: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if platform == "windows":
        return {"status": "not-applicable", "results": [], "reason": "POSIX launcher mode check"}
    if manifests is None:
        from .manifest import load_manifests
        manifests = load_manifests()
    credential_manifest = json.loads((RUNTIME_SOURCE_ROOT.parents[1] / "manifest" / "credential-runtime.json").read_text(encoding="utf-8"))
    commands = {command for consumer in credential_manifest["consumers"] for command in consumer["commands"]}
    candidate = next((row for row in results if row.get("command_target") in commands), None)
    if candidate is None:
        return {"status": "skipped", "results": [], "reason": "no selected credential-bearing offline smoke"}
    positive = {"kind": "positive", "skill": candidate["skill"], "status": candidate["status"]}
    negative = {"kind": "negative", "skill": candidate["skill"], "status": "failed"}
    launcher = runtime_root / "run_skill.sh"
    original_mode = stat.S_IMODE(launcher.stat().st_mode)
    workspace = runtime_root / "workspace"
    try:
        launcher.chmod(original_mode | stat.S_IWGRP)
        completed = run_smoke_process(
            [str(launcher), candidate["command_target"]],
            args=candidate["args"], timeout=candidate["timeout_seconds"],
            env=smoke_env(manifests, candidate["skill"], workspace),
        )
        negative.update(returncode=completed.returncode,
                        status="ok" if completed.returncode == 127 and "owner-controlled launcher" in completed.stderr else "failed",
                        stderr_tail=completed.stderr[-2000:])
    except (OSError, subprocess.TimeoutExpired) as exc:
        negative["reason"] = type(exc).__name__
    finally:
        launcher.chmod(original_mode)
    return _case_section([positive, negative])


def _skill_venv_python(prefix: str, platform: str) -> str:
    if platform == "windows":
        return str(PureWindowsPath(prefix) / "Scripts" / "python.exe")
    if platform in {"linux", "macos", "wsl"}:
        return str(PurePosixPath(prefix) / "bin" / "python")
    raise ValueError(f"unsupported runtime platform: {platform}")


def run_functional_smoke_cases(
    manifests: dict[str, Any], *, skills: list[str], runtime_root: Path, workspace: Path,
    platform: str, venv: dict[str, Any], timeout: int | None = None,
) -> dict[str, Any]:
    rows = []
    runners = runner_invocations(workspace.parent, platform)
    for skill in skills:
        cases = manifests["runtime"]["skills"][skill].get("functional_smoke", {})
        for name, contract in cases.items():
            row = {"skill": skill, "case": name, "runtime_root": str(runtime_root)}
            modules = contract["requires_python_modules"]
            if venv["status"] == "refused":
                rows.append({**row, "status": "failed", "reason": venv.get("reason", "skill venv refused")})
                continue
            missing = []
            prefix = venv["path"] if modules and venv["status"] == "admitted" else None
            if modules and prefix is None:
                missing = list(modules)
            elif modules:
                env = smoke_env(manifests, skill, workspace, contract=contract, skill_venv=prefix, inject_canaries=False)
                for module in modules:
                    try:
                        probe = run_smoke_process(
                            [_skill_venv_python(prefix, platform), "-I", "-c", "import importlib,sys; importlib.import_module(sys.argv[1])", module],
                            args=[], env=env,
                            timeout=smoke_timeout(manifests, skill, timeout, contract=contract),
                        )
                        if probe.returncode:
                            missing.append(module)
                    except (OSError, subprocess.TimeoutExpired):
                        missing.append(module)
            if missing:
                rows.append({**row, "status": "skipped", "missing_modules": missing,
                             "reason": "required Python modules unavailable"})
                continue
            if not runners:
                rows.append({**row, "status": "failed", "reason": "no native runtime runner"})
            for runner in runners:
                rows.append(run_smoke_case(manifests, skill=skill, runner=runner, workspace=workspace,
                            platform=platform, timeout=timeout, mode="installed", runtime_root=runtime_root,
                            contract=contract, case_name=name, skill_venv=prefix, inject_canaries=False))
    return _case_section(rows)


def run_live_checks(
    manifests: dict[str, Any], *, skills: list[str], runtime_root: Path,
    platform: str, timeout: int | None = None,
) -> dict[str, Any]:
    rows = []
    for skill in skills:
        for name, contract in manifests["runtime"]["skills"][skill].get("live_check", {}).items():
            requires = contract["requires"]
            missing = [name for name in requires.get("pointer_env", []) if not os.environ.get(name)]
            replacements = _smoke_replacements(runtime_root / "workspace")
            missing += [path for path in requires.get("config_files", [])
                        if not Path(_expand_smoke_value(path, replacements)).expanduser().is_file()]
            if missing:
                rows.append({"status": "skipped", "skill": skill, "case": name,
                             "runtime_root": str(runtime_root), "missing_requirements": missing})
                continue
            runners = runner_invocations(runtime_root, platform)
            if not runners:
                rows.append({"status": "failed", "skill": skill, "case": name, "reason": "no native runtime runner"})
            for runner in runners:
                rows.append(run_smoke_case(manifests, skill=skill, runner=runner,
                            workspace=runtime_root / "workspace", platform=platform, timeout=timeout,
                            mode="installed", runtime_root=runtime_root, contract=contract,
                            case_name=name, inject_canaries=False, live=True))
    return _case_section(rows)


def selected_runtime_skills(manifests: dict[str, Any], skills: set[str] | None) -> list[str]:
    supported = {skill for skill in manifests.get("runtime", {}).get("skills", {})
                 if has_runtime_smoke_contract(manifests, skill)}
    selected = set(runtime_smoke_skill_names(manifests)) if skills is None else set(skills)
    unknown = sorted(selected - supported)
    if unknown:
        raise ValueError("skills do not have runtime smoke coverage: " + ", ".join(unknown))
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


def functional_smoke_skill_names(manifests: dict[str, Any]) -> list[str]:
    return sorted(skill for skill, spec in manifests.get("runtime", {}).get("skills", {}).items()
                  if isinstance(spec, dict) and bool(spec.get("functional_smoke")))


def live_check_skill_names(manifests: dict[str, Any]) -> list[str]:
    return sorted(skill for skill, spec in manifests.get("runtime", {}).get("skills", {}).items()
                  if isinstance(spec, dict) and bool(spec.get("live_check")))


def has_runtime_smoke_contract(manifests: dict[str, Any], skill: str) -> bool:
    return skill in (runtime_smoke_skill_names(manifests) + functional_smoke_skill_names(manifests)
                     + live_check_skill_names(manifests))


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
    timeout: int | None,
    mode: str = "temporary",
    runtime_root: Path | None = None,
    contract: dict[str, Any] | None = None,
    case_name: str | None = None,
    skill_venv: str | None = None,
    inject_canaries: bool = True,
    live: bool = False,
) -> dict[str, Any]:
    contract = contract if contract is not None else manifests["runtime"]["skills"][skill]["smoke"]
    command_target = _contract_command_target(contract, platform, runner["name"])
    if command_target is None:
        return {"status": "failed", "skill": skill, "case": case_name, "reason": "no native command in contract"}
    args = smoke_args(manifests, skill, workspace, contract=contract, skill_venv=skill_venv)
    effective_timeout = smoke_timeout(manifests, skill, timeout, contract=contract)
    command = [*runner["argv"], command_target]
    checks_override: list[dict[str, Any]] | None = None
    result: dict[str, Any] = {
        "status": "failed", "mode": mode, "runner": runner["name"], "skill": skill,
        "command_target": command_target, "args": args, "timeout_seconds": effective_timeout,
        **({"runtime_root": str(runtime_root)} if runtime_root is not None else {}),
        **({"case": case_name} if case_name is not None else {}),
    }

    def leakage(stdout: str, stderr: str) -> list[dict[str, Any]]:
        return canary_checks(manifests, skill, stdout, stderr) if inject_canaries and not live else []

    try:
        if live:
            env = dict(os.environ)
            env.update({name: _expand_smoke_value(value, _smoke_replacements(workspace, skill_venv))
                        for name, value in contract.get("env", {}).items()})
        else:
            materialize_smoke_fixtures(contract, workspace, skill_venv=skill_venv)
            env = smoke_env(manifests, skill, workspace, contract=contract,
                            skill_venv=skill_venv, inject_canaries=inject_canaries)
        if skill == "deep-research-workflow" and args == ["selftest"] and case_name is None and not live:
            completed, checks_override = run_deep_research_workflow_smoke(
                command, workspace=workspace, timeout=effective_timeout, env=env,
                args=args, manifests=manifests, requested_timeout=timeout,
            )
        else:
            completed = run_smoke_process(command, args=args, timeout=effective_timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = smoke_output_text(exc.stdout), smoke_output_text(exc.stderr)
        return {**result, "returncode": None, "checks": [
            {"name": "completed-before-timeout", "ok": False}, *leakage(stdout, stderr),
        ], "stdout_tail": stdout[-2000:], "stderr_tail": stderr[-2000:]}
    except (OSError, ValueError) as exc:
        return {**result, "returncode": None, "failure_kind": "launch-error", "checks": [
            {"name": "process-launched", "ok": False, "reason": type(exc).__name__}, *leakage("", ""),
        ], "stdout_tail": "", "stderr_tail": f"runtime smoke launch failed: {type(exc).__name__}"}
    if checks_override is None:
        failures = judge_expect(contract["expect"], completed, workspace / "runtime-smoke") if "expect" in contract else ["missing-expect"]
        checks = [{"name": "output-validation", "ok": not failures, "failures": failures}]
    else:
        checks = checks_override
    checks = [*checks, *leakage(completed.stdout, completed.stderr)]
    result.update({
        "status": "ok" if all(check["ok"] for check in checks) else "failed",
        "returncode": completed.returncode, "checks": checks,
        "stdout_tail": completed.stdout[-2000:], "stderr_tail": completed.stderr[-2000:],
    })
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
    command: list[str], *, workspace: Path, timeout: int,
    env: dict[str, str], args: list[str], manifests: dict[str, Any],
    requested_timeout: int | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]] | None]:
    first = run_smoke_process(command, args=args, timeout=timeout, env=env)
    if first.returncode == 0 or not is_deep_research_selftest_unsupported(first):
        return first, None
    contracts = manifests["runtime"]["skills"]["deep-research-workflow"].get("functional_smoke", {})
    checks: list[dict[str, Any]] = [
        {"name": "deep-research-selftest-unsupported", "ok": True},
        *canary_checks(manifests, "deep-research-workflow", first.stdout, first.stderr),
    ]
    completed = first
    for name in ("init", "validate"):
        contract = contracts.get(name)
        if not isinstance(contract, dict) or "expect" not in contract:
            return completed, [*checks, {"name": "output-validation", "ok": False, "failures": [f"missing-deep-fallback:{name}"]}]
        materialize_smoke_fixtures(contract, workspace)
        arguments = smoke_args(manifests, "deep-research-workflow", workspace, contract=contract)
        completed = run_smoke_process(
            command, args=arguments,
            timeout=smoke_timeout(manifests, "deep-research-workflow", requested_timeout, contract=contract),
            env=smoke_env(manifests, "deep-research-workflow", workspace, contract=contract),
        )
        failures = judge_expect(contract["expect"], completed, workspace / "runtime-smoke")
        checks.append({"name": "output-validation", "case": name, "ok": not failures, "failures": failures})
        checks.extend(canary_checks(manifests, "deep-research-workflow", completed.stdout, completed.stderr))
        if completed.returncode != 0:
            break
    return completed, checks


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
    return _contract_command_target(smoke, platform, runner_name)


def _contract_command_target(
    contract: dict[str, Any], platform: str, runner_name: str | None,
) -> str | None:
    command = contract.get("command")
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


def _smoke_replacements(workspace: Path, skill_venv: str | None = None) -> dict[str, str]:
    smoke_dir = workspace / "runtime-smoke"
    return {
        "{workspace}": str(workspace),
        "{smoke_dir}": str(smoke_dir),
        "{home}": str(smoke_dir / "home"),
        "{skill_venv}": skill_venv or "",
    }


def _expand_smoke_value(value: str, replacements: dict[str, str]) -> str:
    for placeholder, replacement in replacements.items():
        value = value.replace(placeholder, replacement)
    return value


def smoke_args(
    manifests: dict[str, Any], skill: str, workspace: Path, *,
    contract: dict[str, Any] | None = None, skill_venv: str | None = None,
) -> list[str]:
    contract = contract if contract is not None else manifests.get("runtime", {}).get("skills", {}).get(skill, {}).get("smoke", {})
    return [_expand_smoke_value(item, _smoke_replacements(workspace, skill_venv)) for item in contract.get("args", [])]


def smoke_timeout(
    manifests: dict[str, Any], skill: str, requested_timeout: int | None, *,
    contract: dict[str, Any] | None = None,
) -> int:
    contract = contract if contract is not None else manifests.get("runtime", {}).get("skills", {}).get(skill, {}).get("smoke", {})
    contract_timeout = contract.get("timeout_seconds")
    if requested_timeout is None:
        return contract_timeout if isinstance(contract_timeout, int) and contract_timeout > 0 else 60
    if isinstance(contract_timeout, int) and contract_timeout > 0:
        return min(requested_timeout, contract_timeout)
    return requested_timeout


def _private_smoke_directory(path: Path, smoke_dir: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    current = path
    while current == smoke_dir or smoke_dir in current.parents:
        current.chmod(0o700)
        if current == smoke_dir:
            break
        current = current.parent


def smoke_path_entries() -> list[str]:
    """The PATH a smoke child gets: the running interpreter, then the base.

    ``/usr/bin:/bin`` alone offers only whatever python3 the platform ships.
    macOS ships the 3.9 command line tools stub, below the 3.10 floor
    ``run_skill.sh`` enforces, so every skill exits 127 before it starts; a
    Linux runner ships a python3 that is not the interpreter the smoke
    dependencies were installed into, so a skill importing one of them fails on
    the import. Lead with the directory holding the interpreter already running
    this process, which ``installer/bootstrap.sh`` resolved against that same
    floor.

    Naming that directory rather than linking the binary into the workspace is
    deliberate: CPython finds ``pyvenv.cfg`` beside the path it was invoked
    through, so a link planted outside a venv quietly demotes it to its base
    interpreter and the child runs against the wrong site-packages.
    """

    base = ["/usr/bin", "/bin"]
    interpreter_bin = os.path.dirname(sys.executable)
    if not interpreter_bin or interpreter_bin in base:
        return base
    return [interpreter_bin, *base]


def materialize_smoke_fixtures(
    contract: dict[str, Any], workspace: Path, *, skill_venv: str | None = None,
) -> None:
    smoke_dir = workspace / "runtime-smoke"
    _private_smoke_directory(smoke_dir, smoke_dir)
    replacements = _smoke_replacements(workspace, skill_venv)
    for fixture in contract.get("fixtures", []):
        destination = _smoke_path(smoke_dir, fixture["to"])
        _private_smoke_directory(destination.parent, smoke_dir)
        if "content" in fixture:
            payload = _expand_smoke_value(fixture["content"], replacements).encode("utf-8")
        else:
            source = _smoke_path(RUNTIME_SOURCE_ROOT, fixture["copy_from"])
            payload = source.read_bytes()
        destination.write_bytes(payload)
        destination.chmod(0o600)


def smoke_env(
    manifests: dict[str, Any], skill: str, workspace: Path, *,
    contract: dict[str, Any] | None = None, skill_venv: str | None = None,
    inject_canaries: bool = True,
) -> dict[str, str]:
    discarded = {
        "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME",
        "AAS_RUNTIME_PYTHON", "AAS_SKILL_VENV", "AAS_RUNTIME_PYTHON_PREFIX", "DOCLING_PYTHON",
        "PYTHONPATH", "VIRTUAL_ENV", "AAS_RUNTIME_ROOT", "AAS_RUNTIME_WORKSPACE",
        "AAS_ALLOW_EXTERNAL_RUNTIME_WORKSPACE", "OPENCLAW_WORKSPACE",
    }
    env = {key: value for key, value in os.environ.items() if key not in discarded and not env_name_looks_secret(key)}
    smoke_dir = workspace / "runtime-smoke"
    home = smoke_dir / "home"
    _private_smoke_directory(home, smoke_dir)
    env["HOME"] = str(home)
    if os.name == "posix":
        env["PATH"] = os.pathsep.join(smoke_path_entries())
    env["AAS_ALLOW_EXTERNAL_RUNTIME_WORKSPACE"] = "1"
    env["AAS_RUNTIME_WORKSPACE"] = str(workspace)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    contract = contract if contract is not None else manifests.get("runtime", {}).get("skills", {}).get(skill, {}).get("smoke", {})
    for key, value in contract.get("env", {}).items():
        env[key] = _expand_smoke_value(value, _smoke_replacements(workspace, skill_venv))
    if skill_venv:
        env["AAS_SKILL_VENV"] = skill_venv
    if inject_canaries:
        smoke = manifests.get("runtime", {}).get("skills", {}).get(skill, {}).get("smoke", {})
        for key, value in smoke.get("env_canaries", {}).items():
            env[key] = value
        plant_secret_file_canaries(manifests, skill, workspace, env)
    return env


def secret_file_canary_spec(manifests: dict[str, Any], skill: str) -> dict[str, Any] | None:
    """The declared secrets-file canary block for a skill, if it has one."""
    smoke = manifests.get("runtime", {}).get("skills", {}).get(skill, {}).get("smoke", {})
    spec = smoke.get("secret_file_canaries") if isinstance(smoke, dict) else None
    if not isinstance(spec, dict):
        return None
    pointer_env = spec.get("pointer_env")
    values = spec.get("values")
    if not isinstance(pointer_env, str) or not pointer_env:
        return None
    if not isinstance(values, dict) or not values:
        return None
    return spec


def plant_secret_file_canaries(
    manifests: dict[str, Any],
    skill: str,
    workspace: Path,
    env: dict[str, str],
) -> None:
    """Hand a credential skill its canaries the way the runner really delivers them.

    An ambient ``FOO_API_KEY=canary`` never survives ``run_skill.sh``: the launcher
    unsets every known secret name before exec, and a credential-contract command
    additionally keeps only its retained names and its own projected keys. So a
    canary planted in the environment reaches nothing, and the resulting
    ``canary-not-leaked`` check passes because the value was never in the process --
    a guarantee the check did not measure. Writing the value into the pointer file
    the runner projects puts it on the real delivery path, where echoing it is a
    genuine leak and the check can genuinely fail.
    """
    spec = secret_file_canary_spec(manifests, skill)
    if spec is None:
        return
    values = {
        key: value
        for key, value in spec["values"].items()
        if isinstance(key, str) and isinstance(value, str)
    }
    if not values:
        return
    smoke_dir = workspace / "runtime-smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    suffix = "json" if spec.get("format") == "json" else "env"
    pointer = smoke_dir / f"{skill}.smoke-canary.{suffix}"
    if suffix == "json":
        body = json.dumps(values, indent=2, sort_keys=True) + "\n"
    else:
        body = "".join(f"{key}={value}\n" for key, value in sorted(values.items()))
    pointer.write_text(body, encoding="utf-8")
    # The strict loader refuses anything group- or world-readable, so a canary file
    # that is not owner-private fails the launch instead of exercising it.
    pointer.chmod(0o600)
    env[spec["pointer_env"]] = str(pointer.resolve())


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

    ``smoke_env`` plants these values -- in the child environment, or, for a
    credential-contract skill, in the secrets file the runner projects -- so any
    occurrence in the output means the skill echoed something it was handed. The
    scan reads the raw streams rather than the parsed payload: a canary printed to
    stderr, or beside the JSON on stdout, has leaked just as surely as one carried
    inside it. Callers run this on every exit path, because a traceback is the
    most likely place for an environment value to escape.
    """
    smoke = manifests.get("runtime", {}).get("skills", {}).get(skill, {}).get("smoke", {})
    env_canaries = smoke.get("env_canaries", {}) if isinstance(smoke, dict) else {}
    declared: dict[str, str] = dict(env_canaries) if isinstance(env_canaries, dict) else {}
    file_spec = secret_file_canary_spec(manifests, skill)
    if file_spec is not None:
        declared.update(file_spec["values"])
    if not declared:
        return []
    combined = f"{stdout or ''}\n{stderr or ''}"
    return [
        {"name": f"canary-not-leaked:{name}", "ok": value not in combined}
        for name, value in sorted(declared.items())
        if isinstance(name, str) and isinstance(value, str)
    ]


_MISSING = object()
_JSON_PATH_PART = re.compile(r"([^.[\]]+)|\[(\d+|\*)\]")


def _json_values(payload: Any, path: str) -> list[Any]:
    if path == "":
        return [payload]
    values = [payload]
    position = 0
    for match in _JSON_PATH_PART.finditer(path):
        separator = path[position:match.start()]
        if separator not in ("", ".") or (position == 0 and separator):
            raise ValueError(f"invalid JSON path: {path}")
        key, index = match.groups()
        following = []
        for value in values:
            if key is not None:
                following.append(value.get(key, _MISSING) if isinstance(value, dict) else _MISSING)
            elif index == "*":
                following.extend(value if isinstance(value, list) else [_MISSING])
            else:
                number = int(index)
                following.append(value[number] if isinstance(value, list) and number < len(value) else _MISSING)
        values = following
        position = match.end()
    if position != len(path) or not position:
        raise ValueError(f"invalid JSON path: {path}")
    return values


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_json_equal(left[key], right[key]) for key in left)
    return left == right


def _json_assertions(assertions: list[dict[str, Any]], payload: Any, *, label: str) -> list[str]:
    failures = []
    types = {
        "null": lambda value: value is None,
        "boolean": lambda value: isinstance(value, bool),
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
        "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "string": lambda value: isinstance(value, str),
    }
    for assertion in assertions:
        path = assertion["path"]
        operator = next(key for key in assertion if key != "path")
        expected = assertion[operator]
        values = _json_values(payload, path)
        if operator == "absent":
            passed = not values or all(value is _MISSING for value in values)
        elif operator == "set_equals":
            passed = (
                all(value is not _MISSING for value in values)
                and all(any(_json_equal(value, item) for item in expected) for value in values)
                and all(any(_json_equal(value, item) for value in values) for item in expected)
            )
        elif not values or any(value is _MISSING for value in values):
            passed = False
        elif operator == "exists":
            passed = True
        elif operator == "equals_path":
            other = _json_values(payload, expected)
            passed = len(values) == len(other) and all(
                value is not _MISSING and _json_equal(value, actual) for actual, value in zip(values, other)
            )
        else:
            def matches(value: Any) -> bool:
                if operator == "equals":
                    return _json_equal(value, expected)
                if operator == "regex":
                    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
                    return re.search(expected, text, re.MULTILINE) is not None
                if operator == "min_len":
                    return isinstance(value, (list, str, dict)) and len(value) >= expected
                if operator == "contains":
                    if isinstance(value, list):
                        return any(_json_equal(item, expected) for item in value)
                    return isinstance(value, dict) and isinstance(expected, str) and expected in value
                if operator == "type":
                    return types[expected](value)
                if operator == "minimum":
                    return types["number"](value) and value >= expected
                raise ValueError(f"unknown JSON assertion operator: {operator}")
            passed = all(matches(value) for value in values)
        if not passed:
            failures.append(f"{label}:{path}:{operator}")
    return failures


def _smoke_path(smoke_dir: Path, raw: str) -> Path:
    relative = raw.removeprefix("{smoke_dir}/")
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or ".." in path.parts or "\\" in relative:
        raise ValueError(f"unsafe smoke path: {raw}")
    target = smoke_dir.joinpath(*path.parts)
    if not resolved_path_within(smoke_dir, target):
        raise ValueError(f"smoke path escapes its directory: {raw}")
    return target


def judge_expect(
    expect: dict[str, Any], completed: subprocess.CompletedProcess[str], smoke_dir: Path,
) -> list[str]:
    """Return contract assertion failures in order, without changing the filesystem."""
    failures: list[str] = []
    try:
        if "exit_code" in expect or "alternatives" not in expect:
            codes = expect.get("exit_code", [0])
            codes = [codes] if isinstance(codes, int) else codes
            if completed.returncode not in codes:
                failures.append("exit_code")
        for key, expected in expect.items():
            if key == "exit_code":
                continue
            if key in {"stdout_regex", "stderr_regex", "stdout_not_regex", "stderr_not_regex"}:
                stream = completed.stdout if key.startswith("stdout") else completed.stderr
                for pattern in [expected] if isinstance(expected, str) else expected:
                    matched = re.search(pattern, stream, re.MULTILINE) is not None
                    if matched == ("_not_" in key):
                        failures.append(f"{key}:{pattern}")
            elif key == "stdout_json":
                try:
                    payload = json.loads(completed.stdout)
                except (TypeError, json.JSONDecodeError):
                    failures.append("stdout_json:invalid-json")
                else:
                    failures.extend(_json_assertions(expected, payload, label="stdout_json"))
            elif key == "files":
                for item in expected:
                    raw = item if isinstance(item, str) else item["path"]
                    try:
                        path = _smoke_path(smoke_dir, raw)
                        if not path.exists():
                            failures.append(f"files:{raw}:missing")
                        elif isinstance(item, dict):
                            if "type" in item:
                                matches = path.is_file() if item["type"] == "file" else path.is_dir()
                                if not matches:
                                    failures.append(f"files:{raw}:type")
                            elif "regex" in item:
                                if re.search(item["regex"], path.read_text(encoding="utf-8"), re.MULTILINE) is None:
                                    failures.append(f"files:{raw}:regex")
                            elif "json" in item:
                                payload = json.loads(path.read_text(encoding="utf-8"))
                                failures.extend(_json_assertions(item["json"], payload, label=f"files:{raw}"))
                    except (OSError, UnicodeError, ValueError):
                        failures.append(f"files:{raw}:unsafe-or-unreadable")
            elif key == "alternatives":
                branch_failures = [judge_expect(branch, completed, smoke_dir) for branch in expected]
                if not any(not branch for branch in branch_failures):
                    failures.append("alternatives:no-match")
                    for index, branch in enumerate(branch_failures):
                        failures.extend(f"alternatives[{index}]:{failure}" for failure in branch)
            else:
                failures.append(f"unknown-expect-operator:{key}")
    except (KeyError, TypeError, ValueError, re.error) as exc:
        failures.append(f"invalid-expect:{type(exc).__name__}")
    return failures


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
    if statuses == {"skipped"}:
        # Nothing ran. A missing Python module or an absent credential skips a
        # row, so a whole run can skip without a single check executing; calling
        # that "ok" would report success for a smoke that proved nothing.
        return "skipped"
    return "ok"


def smoke_output_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""
