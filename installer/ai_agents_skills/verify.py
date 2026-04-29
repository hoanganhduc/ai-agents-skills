from __future__ import annotations

from pathlib import Path
from typing import Any

from .render import MANAGED_MARKER, block_id
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
    status = "ok" if all(item["status"] == "ok" for item in results) else "failed"
    return {"status": status, "checked": len(results), "results": results}


def verify_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    path = Path(artifact["artifact"])
    checks: list[dict[str, Any]] = []
    checks.append({"name": "file-exists", "ok": path.exists()})
    if artifact.get("artifact_type") == "skill-file" and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        checks.append({"name": "metadata-valid", "ok": f"name: {artifact['skill']}" in text})
        checks.append({"name": "managed-marker", "ok": MANAGED_MARKER in text})
        checks.append({"name": "agent-visible", "ok": path.parent.name == artifact["skill"]})
    if artifact.get("artifact_type") == "instruction-block" and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        checks.append({"name": "managed-block-present", "ok": block_id(artifact["skill"]) in text})
    return {
        "agent": artifact.get("agent"),
        "skill": artifact.get("skill"),
        "artifact": artifact.get("artifact"),
        "status": "ok" if all(check["ok"] for check in checks) else "failed",
        "checks": checks,
    }
