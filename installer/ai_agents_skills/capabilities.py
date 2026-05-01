from __future__ import annotations

import os
from pathlib import Path
from typing import Any


AGENT_SKILL_LOADER_POLICY: dict[str, dict[str, Any]] = {
    "codex": {
        "symlink_skill_file": False,
        "default_mode": "reference",
        "reason": "Codex user skill discovery is known to ignore file-symlinked SKILL.md files.",
    },
    "claude": {
        "symlink_skill_file": True,
        "default_mode": "symlink",
        "reason": "Claude skill discovery is expected to load symlinked SKILL.md files.",
    },
    "deepseek": {
        "symlink_skill_file": True,
        "default_mode": "symlink",
        "reason": "DeepSeek skill adapters are expected to load symlinked SKILL.md files.",
    },
}


def effective_install_mode_with_evidence(
    agent: str,
    requested_mode: str,
    source_path: Path,
) -> tuple[str, str, dict[str, Any]]:
    """Resolve requested install mode and return human-readable evidence.

    Planning must remain side-effect free, so filesystem symlink creation is
    verified during apply through the existing fallback path. The plan records
    that deferred probe explicitly instead of pretending the check happened.
    """

    source_exists = source_path.exists()
    policy = AGENT_SKILL_LOADER_POLICY.get(
        agent,
        {
            "symlink_skill_file": True,
            "default_mode": "symlink",
            "reason": "Unknown agents default to symlink-capable policy with apply-time fallback.",
        },
    )
    evidence = {
        "requested_mode": requested_mode,
        "source_exists": source_exists,
        "agent_policy": {
            "symlink_skill_file": policy["symlink_skill_file"],
            "default_mode": policy["default_mode"],
            "reason": policy["reason"],
        },
        "filesystem_symlink_probe": "deferred-to-apply",
    }
    if not source_exists:
        return "copy", "canonical source missing; copy rendered skill content", evidence
    if requested_mode == "auto":
        if policy["symlink_skill_file"]:
            return "symlink", policy["reason"], evidence
        return "reference", policy["reason"], evidence
    if requested_mode == "symlink":
        return "symlink", "symlink explicitly requested; apply will fallback if creation fails", evidence
    return requested_mode, f"{requested_mode} explicitly requested", evidence


def normalized_path_within(root: Path, path: Path) -> bool:
    root_text = os.path.normcase(os.path.abspath(root))
    path_text = os.path.normcase(os.path.abspath(path))
    try:
        return os.path.commonpath([root_text, path_text]) == root_text
    except ValueError:
        return False


def smoke_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    path = Path(artifact["artifact"])
    agent = artifact.get("agent")
    install_mode = artifact.get("install_mode")
    checks: list[dict[str, Any]] = [{"name": "artifact-exists", "ok": path.exists() or path.is_symlink()}]
    status = "ok"
    reason = "managed artifact is present in the agent-visible skill path"
    if artifact.get("artifact_type") != "skill-file":
        return {
            "agent": agent,
            "skill": artifact.get("skill"),
            "artifact": artifact.get("artifact"),
            "status": "skipped",
            "reason": "smoke checks only apply to skill-file artifacts",
            "checks": checks,
        }
    checks.append({"name": "agent-visible-path", "ok": path.parent.name == artifact.get("skill")})
    if install_mode == "symlink":
        policy = AGENT_SKILL_LOADER_POLICY.get(agent or "", {})
        supported = bool(policy.get("symlink_skill_file", True))
        checks.append({"name": "agent-policy-allows-symlink", "ok": supported})
        if not supported:
            status = "degraded"
            reason = "agent policy does not confirm symlinked skill discovery"
        else:
            reason = "symlink mode matches the recorded agent loader policy"
    elif install_mode == "reference":
        reason = "reference adapter is a regular agent-visible SKILL.md file"
    elif install_mode == "copy":
        reason = "copy mode is a regular agent-visible SKILL.md file"
    else:
        status = "unsupported"
        reason = f"unknown install mode: {install_mode}"
    if not all(check["ok"] for check in checks):
        status = "degraded" if status == "ok" else status
    return {
        "agent": agent,
        "skill": artifact.get("skill"),
        "artifact": artifact.get("artifact"),
        "status": status,
        "reason": reason,
        "checks": checks,
    }
