from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def now_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def state_dir(root: Path) -> Path:
    return root / ".ai-agents-skills"


def state_file(root: Path) -> Path:
    return state_dir(root) / "state.json"


def load_state(root: Path) -> dict[str, Any]:
    path = state_file(root)
    if not path.exists():
        return {"schema_version": 1, "artifacts": [], "runs": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(root: Path, data: dict[str, Any]) -> None:
    path = state_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def backup_file(root: Path, run_id: str, path: Path) -> Path | None:
    if not path.exists() and not path.is_symlink():
        return None
    rel = str(path).replace(":", "").replace("\\", "/").lstrip("/")
    dest = state_dir(root) / "backups" / run_id / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        os.symlink(os.readlink(path), dest)
    elif path.is_dir():
        shutil.copytree(path, dest, symlinks=True)
    else:
        shutil.copy2(path, dest)
    return dest


def upsert_artifact(data: dict[str, Any], artifact: dict[str, Any]) -> None:
    artifacts = data.setdefault("artifacts", [])
    key = artifact["key"]
    for i, existing in enumerate(artifacts):
        if existing.get("key") == key:
            artifacts[i] = artifact
            return
    artifacts.append(artifact)


def run_record_path(root: Path, run_id: str) -> Path:
    return state_dir(root) / "runs" / f"{run_id}.json"


def write_run_record(root: Path, run_id: str, actions: list[dict[str, Any]]) -> None:
    path = run_record_path(root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"run_id": run_id, "actions": actions}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
