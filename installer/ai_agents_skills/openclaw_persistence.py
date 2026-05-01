from __future__ import annotations

from pathlib import Path
from typing import Any

from .openclaw_manifest import load_manifest, validate_manifest


PERSISTENCE_TERMS = ("hook", "hooks", "scheduler", "schedulers", "cron", "launchd", "systemd", "profile")


def check_persistence_manifest_file(path: Path) -> dict[str, Any]:
    return check_persistence_manifest(load_manifest(path))


def check_persistence_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    validate_manifest(manifest)
    persistent_actions = []
    inert_actions = []
    for action in manifest["actions"]:
        relative_path = action["target"]["relative_path"].lower()
        if any(term in relative_path for term in PERSISTENCE_TERMS):
            record = {
                "action_id": action["action_id"],
                "operation": action["operation"],
                "relative_path": action["target"]["relative_path"],
            }
            if action["operation"] == "no-op":
                inert_actions.append(record)
            else:
                persistent_actions.append(record)
    if persistent_actions:
        return {
            "status": "blocked",
            "reason": "persistent execution actions require a separate persistence manifest and approval",
            "persistent_actions": persistent_actions,
            "inert_actions": inert_actions,
        }
    return {
        "status": "inert-only",
        "reason": "no persistent execution actions are enabled",
        "persistent_actions": [],
        "inert_actions": inert_actions,
    }
