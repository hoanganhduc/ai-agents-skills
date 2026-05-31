from __future__ import annotations

import json
import os
import shutil
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
from .state import load_state
from .verify import verify


RUNTIME_SMOKE_SKILLS = (
    "axiom-axle-mcp",
    "deep-research-workflow",
    "formal-skeleton-helper",
    "get-available-resources",
    "graph-verifier",
    "lean-formalization-intake",
    "lean-strict-verification-gate",
    "self-improving-agent",
)


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
        workspace = runtime_root / "workspace"
        results = []
        for runner in runner_invocations(runtime_root, host_platform):
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
) -> dict[str, Any]:
    target_platform = current_platform(platform)
    host_platform = current_platform(None)
    if target_platform != host_platform:
        return {
            "status": "skipped",
            "mode": "installed",
            "platform": target_platform,
            "host_platform": host_platform,
            "checked": 0,
            "results": [],
            "reason": "installed runtime smoke only runs on the current host platform",
        }
    state = load_state(root)
    runtime_artifacts = [
        item for item in state.get("artifacts", [])
        if item.get("artifact_type") == "runtime-file"
        and item.get("managed")
        and item.get("skill") != "runtime-runner"
        and (not skills or item.get("skill") in skills)
    ]
    installed_skills = sorted({str(item.get("skill")) for item in runtime_artifacts if item.get("skill")})
    if not installed_skills:
        return {
            "status": "skipped",
            "mode": "installed",
            "platform": target_platform,
            "checked": 0,
            "results": [],
            "reason": "no managed runtime-backed skills matched this scope",
        }

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in state.get("artifacts", []):
        if item.get("artifact_type") != "runtime-file" or not item.get("managed") or not item.get("runtime_root"):
            continue
        if item.get("runtime_root") not in {artifact.get("runtime_root") for artifact in runtime_artifacts}:
            continue
        grouped.setdefault(str(item["runtime_root"]), []).append(item)

    results: list[dict[str, Any]] = []
    for runtime_root_text, artifacts in sorted(grouped.items()):
        runtime_root = Path(runtime_root_text)
        selected_for_root = sorted(
            {
                str(item.get("skill"))
                for item in artifacts
                if item.get("skill") in installed_skills and item.get("skill") != "runtime-runner"
            }
        )
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
            for runner in runner_invocations(runtime_root, target_platform):
                for skill in selected_for_root:
                    if not has_runtime_smoke_contract(manifests, skill):
                        results.append({
                            "status": "unsupported",
                            "mode": "installed",
                            "runtime_root": str(runtime_root),
                            "skill": skill,
                            "runner": runner["name"],
                            "checked": 0,
                            "results": [],
                            "reason": "runtime skill has no safe smoke contract for installed mode",
                        })
                        continue
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
    status = aggregate_runtime_status(results)
    return {
        "status": status,
        "mode": "installed",
        "platform": target_platform,
        "selected_skills": installed_skills,
        "checked": len([item for item in results if item.get("status") != "unsupported"]),
        "results": results,
    }


def selected_runtime_skills(manifests: dict[str, Any], skills: set[str] | None) -> list[str]:
    smoke_supported = set(runtime_smoke_skill_names(manifests)) or set(RUNTIME_SMOKE_SKILLS)
    selected = set(smoke_supported) if skills is None else set(skills)
    unknown = sorted(selected - smoke_supported)
    if unknown:
        raise ValueError("skills do not have runtime smoke coverage: " + ", ".join(unknown))
    return sorted(selected)


def runtime_smoke_skill_names(manifests: dict[str, Any]) -> list[str]:
    return sorted(
        skill
        for skill, spec in manifests.get("runtime", {}).get("skills", {}).items()
        if isinstance(spec, dict) and isinstance(spec.get("smoke"), dict)
    )


def has_runtime_smoke_contract(manifests: dict[str, Any], skill: str) -> bool:
    return skill in runtime_smoke_skill_names(manifests)


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
        runners.append({"name": "run_skill.bat", "argv": [str(runtime_root / "run_skill.bat")]})
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
    command = [*runner["argv"], command_target, *args]
    env = smoke_env(manifests, skill, workspace)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=effective_timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "failed",
            "mode": mode,
            "runner": runner["name"],
            "skill": skill,
            "command_target": command_target,
            "args": args,
            "timeout_seconds": effective_timeout,
            "returncode": None,
            "checks": [{"name": "completed-before-timeout", "ok": False}],
            "stdout_tail": (exc.stdout or "")[-2000:],
            "stderr_tail": (exc.stderr or "")[-2000:],
        }
    try:
        checks = validate_smoke_output(manifests, skill, completed, args)
    except Exception as exc:
        checks = [
            {"name": "exit-zero", "ok": completed.returncode == 0},
            {"name": "output-validation", "ok": False, "reason": str(exc)},
        ]
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
        suffixes = (".ps1", ".bat") if runner_name == "run_skill.ps1" else (".bat", ".ps1")
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
            keys = ("windows_ps1", "windows", "windows_bat")
        elif platform == "windows":
            keys = ("windows_bat", "windows")
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
    if skill in {"lean-formalization-intake", "lean-strict-verification-gate"}:
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
            out_dir = Path(args[args.index("--dir") + 1]) / args[args.index("--subdir") + 1]
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
    elif skill in {"lean-formalization-intake", "lean-strict-verification-gate"}:
        payload = parse_json_stdout(completed.stdout)
        checks.append({"name": "json-ok", "ok": payload.get("status") == "ok"})
        checks.append({"name": "no-auto-install", "ok": payload.get("no_auto_install") is True})
        checks.append({"name": "installs-not-attempted", "ok": payload.get("installs_attempted") is False})
        checks.append({
            "name": "lean-status-recorded",
            "ok": payload.get("tool_status", {}).get("lean", {}).get("status") in {"available", "tool_unavailable"},
        })
    elif skill == "axiom-axle-mcp":
        payload = parse_json_stdout(completed.stdout)
        serialized = json.dumps(payload, sort_keys=True)
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
        checks.append({"name": "no-secret-value", "ok": "AXLE-SMOKE-CANARY" not in serialized})
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
        serialized = json.dumps(payload, sort_keys=True)
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
        checks.append({"name": "canary-not-leaked", "ok": "SUBMISSION-VENUE-SELECTOR-CANARY" not in serialized})
        checks.append({"name": "schema-list-present", "ok": "delivery.json" in payload.get("schemas", [])})
    return checks


def copy_installed_runtime_workspace(
    runtime_root: Path,
    artifacts: list[dict[str, Any]],
    scratch_workspace: Path,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    runtime_workspace = runtime_root / "workspace"
    scratch_workspace.mkdir(parents=True, exist_ok=True)
    for artifact in artifacts:
        target_relpath = artifact.get("target_relpath")
        if not isinstance(target_relpath, str):
            continue
        source = Path(str(artifact.get("artifact", "")))
        check_prefix = f"copy:{target_relpath}"
        if source.is_symlink():
            checks.append({"name": f"{check_prefix}:not-symlink", "ok": False})
            continue
        if not source.is_file():
            checks.append({"name": f"{check_prefix}:source-file", "ok": False})
            continue
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
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        checks.append({"name": f"{check_prefix}:copied", "ok": True})
    if not checks:
        return {
            "status": "failed",
            "reason": "no managed runtime files were available to smoke",
            "checks": [{"name": "managed-runtime-files", "ok": False}],
        }
    if not all(check["ok"] for check in checks):
        return {"status": "failed", "reason": "failed to prepare scratch runtime workspace", "checks": checks}
    return {"status": "ok", "checks": checks}


def aggregate_runtime_status(results: list[dict[str, Any]]) -> str:
    if not results:
        return "skipped"
    statuses = {str(item.get("status")) for item in results}
    if "failed" in statuses:
        return "failed"
    if statuses & {"degraded", "unsupported"}:
        return "degraded"
    return "ok"


def parse_json_stdout(stdout: str) -> dict[str, Any]:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"runtime smoke command did not emit JSON: {exc}") from exc
