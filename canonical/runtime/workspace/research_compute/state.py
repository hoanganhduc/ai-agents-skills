from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def ensure_root(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_dir(state_root: Path, job_id: str) -> Path:
    return state_root / "jobs" / job_id


def attempt_dir(state_root: Path, job_id: str, attempt_id: str) -> Path:
    return job_dir(state_root, job_id) / "attempts" / attempt_id


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = dict(event)
    event.setdefault("ts", datetime.now(timezone.utc).isoformat())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def next_attempt_id(state_root: Path, job_id: str) -> str:
    attempts_root = job_dir(state_root, job_id) / "attempts"
    if not attempts_root.exists():
        return "attempt-001"
    existing = sorted(p.name for p in attempts_root.iterdir() if p.is_dir() and p.name.startswith("attempt-"))
    if not existing:
        return "attempt-001"
    last = existing[-1]
    try:
        number = int(last.split("-", 1)[1]) + 1
    except (IndexError, ValueError):
        number = len(existing) + 1
    return f"attempt-{number:03d}"


def status_path(state_root: Path, job_id: str) -> Path:
    return job_dir(state_root, job_id) / "status.json"


def manifest_path(state_root: Path, job_id: str) -> Path:
    return job_dir(state_root, job_id) / "manifest.json"


def plan_path(state_root: Path, job_id: str) -> Path:
    return job_dir(state_root, job_id) / "plan.json"
