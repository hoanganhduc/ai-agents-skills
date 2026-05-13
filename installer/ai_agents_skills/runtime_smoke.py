from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .agents import detect_agents
from .apply import apply_plan
from .discovery import current_platform
from .planner import build_plan
from .verify import verify


RUNTIME_SMOKE_SKILLS = ("formal-skeleton-helper", "get-available-resources", "graph-verifier")


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


def selected_runtime_skills(manifests: dict[str, Any], skills: set[str] | None) -> list[str]:
    declared = set(manifests.get("runtime", {}).get("skills", {}))
    selected = set(RUNTIME_SMOKE_SKILLS) if skills is None else set(skills)
    unknown = sorted(selected - declared)
    if unknown:
        raise ValueError("skills do not have runtime smoke coverage: " + ", ".join(unknown))
    return sorted(selected)


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
) -> dict[str, Any]:
    command_target = runtime_command_target(manifests, skill, platform)
    args = smoke_args(skill, workspace)
    command = [*runner["argv"], command_target, *args]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "failed",
            "runner": runner["name"],
            "skill": skill,
            "command_target": command_target,
            "args": args,
            "returncode": None,
            "checks": [{"name": "completed-before-timeout", "ok": False}],
            "stdout_tail": (exc.stdout or "")[-2000:],
            "stderr_tail": (exc.stderr or "")[-2000:],
        }
    try:
        checks = validate_smoke_output(skill, completed, args)
    except Exception as exc:
        checks = [
            {"name": "exit-zero", "ok": completed.returncode == 0},
            {"name": "output-validation", "ok": False, "reason": str(exc)},
        ]
    status = "ok" if completed.returncode == 0 and all(check["ok"] for check in checks) else "failed"
    return {
        "status": status,
        "runner": runner["name"],
        "skill": skill,
        "command_target": command_target,
        "args": args,
        "returncode": completed.returncode,
        "checks": checks,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def runtime_command_target(manifests: dict[str, Any], skill: str, platform: str) -> str:
    suffix = ".bat" if platform == "windows" else ".sh"
    spec = manifests["runtime"]["skills"][skill]
    for entry in spec.get("files", []):
        target = entry.get("target", "")
        if target.endswith(suffix) and platform in entry.get("platforms", []):
            return target.removeprefix("workspace/")
    raise ValueError(f"no {platform} runtime command declared for {skill}")


def smoke_args(skill: str, workspace: Path) -> list[str]:
    smoke_dir = workspace / "runtime-smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    if skill == "formal-skeleton-helper":
        return ["--output-dir", str(smoke_dir / "formal")]
    if skill == "get-available-resources":
        return ["--output", str(smoke_dir / "resources.json")]
    return []


def validate_smoke_output(skill: str, completed: subprocess.CompletedProcess[str], args: list[str]) -> list[dict[str, Any]]:
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
    return checks


def parse_json_stdout(stdout: str) -> dict[str, Any]:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"runtime smoke command did not emit JSON: {exc}") from exc
