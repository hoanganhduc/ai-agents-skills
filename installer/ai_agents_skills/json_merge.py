"""Idempotent merge of a single managed hook entry into a JSON settings file.

JSON cannot carry the Markdown managed-block comment markers used elsewhere in
this installer, so a managed settings entry is identified by two tag fields,
``_managedBy`` and ``_id``. The pair (MANAGED_BY, managed_id) makes an entry
idempotently upsertable and removable without moving or modifying any
user-authored entry. A full merge-then-remove round trip restores the file to
its pre-merge shape, except that a stripped copy of the managed entry adopted
by the merge leaves with it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MANAGED_BY = "ai-agents-skills"
MANAGED_BY_KEY = "_managedBy"
MANAGED_ID_KEY = "_id"


def json_path_value(settings: dict[str, Any], path: list[str]) -> tuple[bool, Any]:
    """Return whether a dotted JSON path exists and its value."""
    current: Any = settings
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return False, None
        current = current[key]
    return True, current


def merge_json_value(
    settings: dict[str, Any],
    path: list[str],
    value: Any,
) -> tuple[dict[str, Any], bool, list[str], bool, Any]:
    """Set one scalar path without replacing unrelated user configuration.

    Returns ``(merged, changed, created_containers, original_exists,
    original_value)``. Existing non-object intermediate values are conflicts.
    """
    if not path:
        raise ValueError("managed JSON setting path must not be empty")
    result = json.loads(json.dumps(settings))
    original_exists, original_value = json_path_value(result, path)
    current: dict[str, Any] = result
    created: list[str] = []
    traversed: list[str] = []
    for key in path[:-1]:
        traversed.append(key)
        child = current.get(key)
        if child is None:
            child = {}
            current[key] = child
            created.append(".".join(traversed))
        if not isinstance(child, dict):
            raise ValueError(f"settings field `{'.'.join(traversed)}` must be an object when present")
        current = child
    changed = not original_exists or original_value != value
    if changed:
        current[path[-1]] = value
    return result, changed, created, original_exists, original_value


def restore_json_value(
    settings: dict[str, Any],
    path: list[str],
    *,
    installed_value: Any,
    original_exists: bool,
    original_value: Any,
    created_containers: list[str] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Restore one managed scalar if the installed value is still present."""
    exists, current_value = json_path_value(settings, path)
    if not exists:
        return json.loads(json.dumps(settings)), False
    if current_value != installed_value:
        raise ValueError(f"managed JSON setting `{'.'.join(path)}` changed since install")
    result = json.loads(json.dumps(settings))
    parent: dict[str, Any] = result
    for key in path[:-1]:
        child = parent.get(key)
        if not isinstance(child, dict):
            raise ValueError(f"settings field `{key}` changed since install")
        parent = child
    if original_exists:
        parent[path[-1]] = original_value
    else:
        parent.pop(path[-1], None)
        created = set(created_containers or [])
        for depth in range(len(path) - 1, 0, -1):
            container_path = path[:depth]
            if ".".join(container_path) not in created:
                continue
            ancestor: dict[str, Any] = result
            for key in container_path[:-1]:
                child = ancestor.get(key)
                if not isinstance(child, dict):
                    break
                ancestor = child
            child = ancestor.get(container_path[-1])
            if isinstance(child, dict) and not child:
                del ancestor[container_path[-1]]
    return result, True


def load_json_object(path: Path) -> tuple[dict[str, Any], bool]:
    """Read a JSON object from ``path`` as ``(data, existed)``.

    A missing file yields an empty object. A file that is not valid JSON, or
    whose top level is not an object, raises ``ValueError`` so the caller
    refuses to overwrite an unparseable user settings file rather than
    clobbering it.
    """
    if not path.exists():
        return {}, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"settings file is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"settings file must contain a JSON object: {path}")
    return data, True


def is_managed_entry(entry: Any, managed_id: str) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get(MANAGED_BY_KEY) == MANAGED_BY
        and entry.get(MANAGED_ID_KEY) == managed_id
    )


def _is_orphaned_entry(item: Any, tagged: dict[str, Any]) -> bool:
    """True when ``item`` is an untagged copy of exactly what this merge writes.

    A settings writer that keeps only the keys it knows about strips
    ``_managedBy`` and ``_id`` and leaves the entry itself behind, unowned. The
    next merge would find no managed entry and append a second copy, so the
    stripped entry is treated as this installer's own orphan and re-tagged in
    place. The match is exact: any extra field, or any difference in the hook
    command, keeps the entry user-authored and untouched.
    """
    if not isinstance(item, dict):
        return False
    if MANAGED_BY_KEY in item or MANAGED_ID_KEY in item:
        return False
    return item == {k: v for k, v in tagged.items() if k not in (MANAGED_BY_KEY, MANAGED_ID_KEY)}


def _hook_event_list(settings: dict[str, Any], event: str) -> list[Any]:
    hooks = settings.get("hooks")
    if hooks is None:
        return []
    if not isinstance(hooks, dict):
        raise ValueError("settings field `hooks` must be an object when present")
    event_entries = hooks.get(event)
    if event_entries is None:
        return []
    if not isinstance(event_entries, list):
        raise ValueError(f"settings field `hooks.{event}` must be an array when present")
    return event_entries


def merge_hook_entry(
    settings: dict[str, Any],
    event: str,
    entry: dict[str, Any],
    managed_id: str,
) -> tuple[dict[str, Any], bool, dict[str, bool]]:
    """Idempotently upsert one managed hook entry under ``hooks.<event>``.

    The entry is tagged with the managed markers. If a managed entry with the
    same id already exists it is replaced; otherwise the entry is appended. An
    untagged entry identical to what this merge writes is adopted in place
    rather than duplicated (see :func:`_is_orphaned_entry`), and a repeat of the
    owned entry is collapsed to one, so an external writer that strips the
    markers cannot make the hook accumulate copies. Every other entry is never
    moved or modified.

    Returns ``(merged, changed, created)`` where ``created`` records whether the merge
    had to create the ``hooks`` object and/or the ``hooks.<event>`` list. Pass
    ``created`` to :func:`remove_hook_entry` so uninstall prunes only what the
    merge added and never a user-authored empty container.
    """
    result = json.loads(json.dumps(settings))
    hooks_existed = isinstance(result.get("hooks"), dict)
    event_existed = hooks_existed and isinstance(result["hooks"].get(event), list)
    created = {"hooks": not hooks_existed, "event": not event_existed}
    existing = _hook_event_list(result, event)
    tagged: dict[str, Any] = {MANAGED_BY_KEY: MANAGED_BY, MANAGED_ID_KEY: managed_id}
    tagged.update({k: v for k, v in entry.items() if k not in (MANAGED_BY_KEY, MANAGED_ID_KEY)})
    new_list: list[Any] = []
    placed = False
    for item in existing:
        if is_managed_entry(item, managed_id) or _is_orphaned_entry(item, tagged):
            # The first occurrence keeps its position; any further copy of the
            # same entry -- tagged or stripped -- is a duplicate of the one
            # entry this installer owns and is dropped.
            if not placed:
                new_list.append(tagged)
                placed = True
            continue
        new_list.append(item)
    if not placed:
        new_list.append(tagged)
    if new_list == list(existing):
        return result, False, created
    hooks = result.setdefault("hooks", {})
    hooks[event] = new_list
    return result, True, created


def extract_hook_entry(settings: dict[str, Any], event: str, managed_id: str) -> dict[str, Any] | None:
    for item in _hook_event_list(settings, event):
        if is_managed_entry(item, managed_id):
            return item
    return None


def remove_hook_entry(
    settings: dict[str, Any],
    event: str,
    managed_id: str,
    created: dict[str, bool] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Remove the managed hook entry, leaving every user-authored entry and
    container intact.

    ``created`` (as returned by :func:`merge_hook_entry`) lets the removal prune
    the ``hooks.<event>`` list and/or the ``hooks`` object when, and only when,
    the merge created them. Without it, an emptied container is left in place so
    a user-authored empty container is never deleted. With it, a full
    merge-then-remove round trip restores the file to its pre-merge shape --
    minus any stripped copy of the managed entry that the merge adopted, which
    belongs to this installer and leaves with the entry it duplicated.
    Returns ``(merged, changed)``.
    """
    created = created or {}
    result = json.loads(json.dumps(settings))
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        return result, False
    event_entries = hooks.get(event)
    if not isinstance(event_entries, list):
        return result, False
    kept = [item for item in event_entries if not is_managed_entry(item, managed_id)]
    if len(kept) == len(event_entries):
        return result, False
    if kept:
        hooks[event] = kept
    elif created.get("event"):
        del hooks[event]
    else:
        hooks[event] = kept  # preserve a user-authored (now empty) event list
    if not hooks and created.get("hooks"):
        del result["hooks"]
    return result, True
