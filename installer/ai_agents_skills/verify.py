from __future__ import annotations

from pathlib import Path
from typing import Any

from .render import MANAGED_MARKER, block_id
from .sanitize import has_sensitive_material
from .state import load_state


def verify(root: Path, skill_filter: set[str] | None = None, agent_filter: set[str] | None = None) -> dict[str, Any]:
    state = load_state(root)
    results: list[dict[str, Any]] = []
    for artifact in state.get("artifacts", []):
        skill = artifact.get("skill")
        agent = artifact.get("agent")
        if skill_filter and skill not in skill_filter:
            continue
        if agent_filter and agent not in agent_filter:
            continue
        results.append(verify_artifact(artifact))
    if not results:
        return {
            "status": "no-managed-artifacts",
            "checked": 0,
            "results": [],
            "reason": "no managed ai-agents-skills artifacts matched this scope",
        }
    status = "ok" if all(item["status"] == "ok" for item in results) else "failed"
    return {"status": status, "checked": len(results), "results": results}


def verify_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    path = Path(artifact["artifact"])
    checks: list[dict[str, Any]] = []
    checks.append({"name": "file-exists", "ok": path.exists()})
    if artifact.get("install_mode") == "symlink":
        verify_symlink_artifact(path, artifact, checks)
        return {
            "agent": artifact.get("agent"),
            "skill": artifact.get("skill"),
            "artifact": artifact.get("artifact"),
            "status": "ok" if all(check["ok"] for check in checks) else "failed",
            "checks": checks,
        }
    if artifact.get("artifact_type") == "skill-file" and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        checks.append({"name": "metadata-valid", "ok": f"name: {artifact['skill']}" in text})
        checks.append({"name": "managed-marker", "ok": MANAGED_MARKER in text})
        checks.append({"name": "no-secret-leak", "ok": not has_sensitive_material(text)})
        checks.append({"name": "agent-visible", "ok": path.parent.name == artifact["skill"]})
    if artifact.get("artifact_type") == "skill-support-file" and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        checks.append({"name": "managed-marker", "ok": MANAGED_MARKER in text})
        checks.append({"name": "no-secret-leak", "ok": not has_sensitive_material(text)})
    if artifact.get("artifact_type") in {"instruction-block", "management-notice"} and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        managed_block = extract_managed_block(text, block_id(artifact["skill"]))
        checks.append({"name": "managed-block-present", "ok": managed_block is not None})
        checks.append({
            "name": "no-secret-leak",
            "ok": managed_block is not None and not has_sensitive_material(managed_block),
        })
    if artifact.get("artifact_type") in {"template", "instruction-doc", "entrypoint-alias"} and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        checks.append({"name": "managed-marker", "ok": MANAGED_MARKER in text})
        checks.append({"name": "no-secret-leak", "ok": not has_sensitive_material(text)})
    if artifact.get("artifact_type") == "agent-persona" and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        checks.append({"name": "managed-marker", "ok": MANAGED_MARKER in text})
        checks.append({"name": "no-secret-leak", "ok": not has_sensitive_material(text)})
        if artifact.get("agent") == "codex":
            checks.append({"name": "codex-persona-schema", "ok": "developer_instructions" in text})
        if artifact.get("agent") == "claude":
            checks.append({"name": "claude-persona-frontmatter", "ok": text.lstrip().startswith("---")})
    return {
        "agent": artifact.get("agent"),
        "skill": artifact.get("skill"),
        "artifact": artifact.get("artifact"),
        "status": "ok" if all(check["ok"] for check in checks) else "failed",
        "checks": checks,
    }


def verify_symlink_artifact(path: Path, artifact: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    source = Path(artifact.get("source_path", ""))
    checks.append({"name": "symlink", "ok": path.is_symlink()})
    checks.append({"name": "source-exists", "ok": source.exists()})
    checks.append({
        "name": "source-match",
        "ok": path.is_symlink() and source.exists() and path.resolve() == source.resolve(),
    })
    if artifact.get("artifact_type") == "skill-file" and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        checks.append({"name": "metadata-valid", "ok": f"name: {artifact['skill']}" in text})
        checks.append({"name": "agent-visible", "ok": path.parent.name == artifact["skill"]})
        checks.append({"name": "no-secret-leak", "ok": not has_sensitive_material(text)})
    if artifact.get("artifact_type") == "skill-support-file" and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        checks.append({"name": "no-secret-leak", "ok": not has_sensitive_material(text)})


def extract_managed_block(text: str, identifier: str) -> str | None:
    start = f"<!-- {identifier}:start -->"
    end = f"<!-- {identifier}:end -->"
    start_index = text.find(start)
    if start_index == -1:
        return None
    end_index = text.find(end, start_index)
    if end_index == -1:
        return None
    return text[start_index:end_index + len(end)]
