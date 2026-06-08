from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .selectors import split_csv
from .state import write_text_atomic


def antigravity_settings_path(root: Path) -> Path:
    return root / ".gemini" / "antigravity-cli" / "settings.json"


def antigravity_fixup(root: Path, *, workspace: str | None = None, workspaces: str | None = None, apply: bool = False) -> dict[str, Any]:
    target = antigravity_settings_path(root)
    selected_workspaces = resolve_workspace_paths(workspace=workspace, workspaces=workspaces)
    before, existed = load_settings(target)
    after, changes = merged_settings(before, selected_workspaces)
    operation = "create" if not existed else "update"
    if not changes:
        operation = "noop"
    result = {
        "status": "ok",
        "path": str(target),
        "operation": operation,
        "applied": apply and operation != "noop",
        "workspace_paths": [str(path) for path in selected_workspaces],
        "changes": changes,
    }
    if apply and operation != "noop":
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(target, json.dumps(after, indent=2, sort_keys=True) + "\n")
    return result


def resolve_workspace_paths(*, workspace: str | None = None, workspaces: str | None = None) -> list[Path]:
    candidates = []
    if workspace:
        candidates.append(workspace)
    candidates.extend(split_csv(workspaces))
    if not candidates:
        candidates.append(str(Path.cwd()))
    resolved_paths: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if not path.exists():
            raise ValueError(f"workspace path does not exist: {path}")
        if not path.is_dir():
            raise ValueError(f"workspace path is not a directory: {path}")
        resolved = path.resolve()
        text = str(resolved)
        if text in seen:
            continue
        seen.add(text)
        resolved_paths.append(resolved)
    return resolved_paths


def load_settings(path: Path) -> tuple[dict[str, Any], bool]:
    if not path.exists():
        return {}, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Antigravity settings file is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Antigravity settings file must contain a JSON object: {path}")
    return data, True


def merged_settings(settings: dict[str, Any], workspace_paths: list[Path]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = json.loads(json.dumps(settings))
    changes: list[dict[str, Any]] = []
    gcp = result.get("gcp")
    if gcp is not None and not isinstance(gcp, dict):
        raise ValueError("Antigravity settings field `gcp` must be an object when present")
    if isinstance(gcp, dict):
        project = gcp.get("project")
        if isinstance(project, str):
            trimmed = project.strip()
            if trimmed != project:
                gcp["project"] = trimmed
                changes.append({
                    "field": "gcp.project",
                    "action": "trim-whitespace",
                })
    trusted = result.get("trustedWorkspaces")
    if trusted is None:
        trusted_list: list[str] = []
    else:
        if not isinstance(trusted, list) or any(not isinstance(item, str) for item in trusted):
            raise ValueError("Antigravity settings field `trustedWorkspaces` must be an array of strings when present")
        trusted_list = list(trusted)
    seen = {normalized_workspace_key(item) for item in trusted_list}
    added = []
    for workspace_path in workspace_paths:
        text = str(workspace_path)
        key = normalized_workspace_key(text)
        if key in seen:
            continue
        trusted_list.append(text)
        seen.add(key)
        added.append(text)
    if added or trusted is not None:
        result["trustedWorkspaces"] = trusted_list
    if added:
        changes.append({
            "field": "trustedWorkspaces",
            "action": "add-workspaces",
            "added": added,
        })
    status_line = result.get("statusLine")
    if status_line is not None and not isinstance(status_line, dict):
        raise ValueError("Antigravity settings field `statusLine` must be an object when present")
    if isinstance(status_line, dict) and status_line.get("enabled") is True and status_line_is_empty(status_line):
        cleaned = {
            key: value
            for key, value in status_line.items()
            if key not in {"command", "type"}
        }
        cleaned["enabled"] = False
        result["statusLine"] = cleaned
        changes.append({
            "field": "statusLine",
            "action": "disable-empty-status-line",
        })
    return result, changes


def normalized_workspace_key(value: str) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        return value.strip()
    return str(path.resolve(strict=False))


def status_line_is_empty(status_line: dict[str, Any]) -> bool:
    return blank_text(status_line.get("type")) and blank_text(status_line.get("command"))


def blank_text(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())
