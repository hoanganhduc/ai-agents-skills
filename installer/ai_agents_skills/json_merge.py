"""Idempotent merge of a single managed hook entry into a JSON settings file.

JSON cannot carry the Markdown managed-block comment markers used elsewhere in
this installer, so a managed settings entry is identified by two tag fields,
``_managedBy`` and ``_id``. The pair (MANAGED_BY, managed_id) makes an entry
idempotently upsertable and removable without moving or modifying any
user-authored entry. A full merge-then-remove round trip restores the file to
its pre-merge shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MANAGED_BY = "ai-agents-skills"
MANAGED_BY_KEY = "_managedBy"
MANAGED_ID_KEY = "_id"


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
    same id already exists it is replaced; otherwise the entry is appended.
    User-authored entries are never moved or modified. Returns
    ``(merged, changed, created)`` where ``created`` records whether the merge
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
    new_list = list(existing)
    replaced = False
    for index, item in enumerate(new_list):
        if is_managed_entry(item, managed_id):
            if item == tagged:
                return result, False, created
            new_list[index] = tagged
            replaced = True
            break
    if not replaced:
        new_list.append(tagged)
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
    merge-then-remove round trip restores the file to its pre-merge shape.
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
