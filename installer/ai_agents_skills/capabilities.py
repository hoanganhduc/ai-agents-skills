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
        "symlink_skill_file": False,
        "default_mode": "reference",
        "reason": "DeepSeek native symlinked SKILL.md loading has not been verified, so auto mode uses reference adapters.",
    },
    "copilot": {
        "symlink_skill_file": False,
        "default_mode": "reference",
        "reason": "Copilot agent skills are regular SKILL.md files; symlinked skill discovery is not assumed.",
    },
    "opencode": {
        "symlink_skill_file": False,
        "default_mode": "copy",
        "reason": "OpenCode native skills are regular SKILL.md files; auto mode copies canonical skill files and support files for cross-platform parity.",
    },
    "antigravity": {
        "symlink_skill_file": False,
        "default_mode": "copy",
        "reason": "Antigravity CLI global skills are flat Markdown files; auto mode copies the full canonical skill body into the official skills directory so triggered skills work without reaching outside the workspace sandbox.",
    },
    "grok": {
        "symlink_skill_file": False,
        "default_mode": "copy",
        "reason": "Grok native ~/.grok/skills SKILL.md files; copy keeps the install self-contained and toggle-independent; symlink loading is unverified and Windows-privilege-gated.",
    },
    "openclaw": {
        "symlink_skill_file": False,
        "default_mode": "copy",
        "reason": "OpenClaw native target support is fake-root-only; auto mode uses regular files for layout tests.",
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
        if policy["default_mode"] == "symlink" and policy["symlink_skill_file"]:
            return "symlink", policy["reason"], evidence
        return policy["default_mode"], policy["reason"], evidence
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


def resolved_path_within(root: Path, path: Path) -> bool:
    root_text = os.path.normcase(str(root.resolve(strict=False)))
    path_text = os.path.normcase(str(path.resolve(strict=False)))
    try:
        return os.path.commonpath([root_text, path_text]) == root_text
    except ValueError:
        return False


def existing_parents(path: Path, root: Path | None = None) -> list[Path]:
    parents: list[Path] = []
    current = Path(os.path.abspath(path))
    root_abs = Path(os.path.abspath(root)) if root is not None else None
    while True:
        if current.exists() or current.is_symlink():
            parents.append(current)
        if root_abs is not None and current == root_abs:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return parents


def looks_like_real_system_root(root: Path, home: Path | None = None) -> bool:
    resolved = root.resolve(strict=False)
    current_home = (home or Path.home()).resolve(strict=False)
    if resolved == current_home:
        return True
    parts = [part.lower() for part in resolved.parts]
    if len(parts) == 3 and parts[1] in {"home", "users"}:
        return True
    if len(parts) == 5 and parts[1] == "mnt" and len(parts[2]) == 1 and parts[3] == "users":
        return True
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
    checks.append({
        "name": "agent-visible-path",
        "ok": skill_path_is_agent_visible(str(agent), path, str(artifact.get("skill"))),
    })
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
        reason = "reference adapter is a regular agent-visible skill file"
    elif install_mode == "copy":
        reason = "copy mode is a regular agent-visible skill file"
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


def skill_path_is_agent_visible(agent: str, path: Path, skill: str) -> bool:
    if agent == "antigravity":
        return path.name == f"{skill}.md"
    return path.name == "SKILL.md" and path.parent.name == skill
