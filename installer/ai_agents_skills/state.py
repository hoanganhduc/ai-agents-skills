from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .capabilities import normalized_path_within, resolved_path_within


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str | None:
    if not path.exists() or path.is_symlink() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def now_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def artifact_signature(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        return {
            "exists": True,
            "kind": "symlink",
            "target": os.readlink(path),
        }
    if not path.exists():
        return {"exists": False, "kind": "missing"}
    if path.is_dir():
        return {
            "exists": True,
            "kind": "directory",
            "tree_hash": sha256_tree(path),
        }
    if path.is_file():
        return {
            "exists": True,
            "kind": "file",
            "hash": sha256_file(path),
        }
    return {"exists": True, "kind": "other"}


def signatures_match(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    return normalize_signature(left) == normalize_signature(right)


def normalize_signature(signature: dict[str, Any] | None) -> dict[str, Any] | None:
    if signature is None:
        return None
    normalized = dict(signature)
    if normalized.get("kind") == "symlink" and "target" in normalized:
        normalized["target"] = str(normalized["target"])
    return normalized


def sha256_tree(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
        rel = child.relative_to(path).as_posix()
        digest.update(rel.encode("utf-8"))
        if child.is_symlink():
            digest.update(b"\0symlink\0")
            digest.update(os.readlink(child).encode("utf-8"))
        elif child.is_dir():
            digest.update(b"\0dir\0")
        elif child.is_file():
            digest.update(b"\0file\0")
            with child.open("rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    digest.update(chunk)
        else:
            digest.update(b"\0other\0")
    return "sha256:" + digest.hexdigest()


def state_dir(root: Path) -> Path:
    return root / ".ai-agents-skills"


def state_file(root: Path) -> Path:
    return state_dir(root) / "state.json"


def load_state(root: Path) -> dict[str, Any]:
    path = state_file(root)
    preflight_state_path(root, path)
    if not path.exists():
        return {"schema_version": 1, "artifacts": [], "runs": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"installer state is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"installer state must be a JSON object: {path}")
    return data


def save_state(root: Path, data: dict[str, Any]) -> None:
    path = state_file(root)
    preflight_state_path(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def backup_file(root: Path, run_id: str, path: Path) -> Path | None:
    if not path.exists() and not path.is_symlink():
        return None
    rel = str(path).replace(":", "").replace("\\", "/").lstrip("/")
    dest = state_dir(root) / "backups" / run_id / rel
    preflight_state_path(root, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        symlink_atomic(dest, Path(os.readlink(path)))
    elif path.is_dir():
        shutil.copytree(path, dest, symlinks=True)
    else:
        shutil.copy2(path, dest)
    return dest


def upsert_record(records: list[dict[str, Any]], record: dict[str, Any]) -> None:
    key = record["key"]
    for i, existing in enumerate(records):
        if existing.get("key") == key:
            records[i] = record
            return
    records.append(record)


def upsert_artifact(data: dict[str, Any], artifact: dict[str, Any]) -> None:
    upsert_record(data.setdefault("artifacts", []), artifact)


def upsert_uninstall_record(data: dict[str, Any], record: dict[str, Any]) -> None:
    upsert_record(data.setdefault("uninstall_records", []), record)


def run_record_path(root: Path, run_id: str) -> Path:
    return state_dir(root) / "runs" / f"{run_id}.json"


def write_run_record(root: Path, run_id: str, actions: list[dict[str, Any]]) -> None:
    path = run_record_path(root, run_id)
    preflight_state_path(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json.dumps({"run_id": run_id, "actions": actions}, indent=2, sort_keys=True) + "\n")


def preflight_state_dir(root: Path) -> None:
    directory = state_dir(root)
    if not normalized_path_within(root, directory) or not resolved_path_within(root, directory.parent):
        raise ValueError(f"installer state directory escapes selected root: {directory}")
    for parent in existing_contained_parents(directory, root):
        if parent.is_symlink():
            raise ValueError(f"refusing to use symlinked installer state path: {parent}")
        if not parent.is_dir():
            raise ValueError(f"refusing to use non-directory installer state path: {parent}")


def preflight_state_path(root: Path, path: Path) -> None:
    if not normalized_path_within(state_dir(root), path) or not resolved_path_within(root, path.parent):
        raise ValueError(f"installer state path escapes selected root: {path}")
    if path.is_symlink():
        raise ValueError(f"refusing to use symlinked installer state file: {path}")
    preflight_state_dir(root)
    for parent in existing_contained_parents(path.parent, state_dir(root)):
        if parent.is_symlink():
            raise ValueError(f"refusing to write installer state through symlinked parent: {parent}")
        if not parent.is_dir():
            raise ValueError(f"refusing to write installer state through non-directory parent: {parent}")


def existing_contained_parents(path: Path, stop_at: Path) -> list[Path]:
    parents: list[Path] = []
    current = Path(os.path.abspath(path))
    stop = Path(os.path.abspath(stop_at))
    while True:
        if current.exists() or current.is_symlink():
            parents.append(current)
        if current == stop:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return parents


def write_text_atomic(path: Path, content: str) -> None:
    tmp_name: str | None = None
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=False,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        if tmp_name is not None:
            try:
                Path(tmp_name).unlink()
            except FileNotFoundError:
                pass
        raise


def symlink_atomic(path: Path, source_path: Path) -> None:
    for _ in range(100):
        tmp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        try:
            os.symlink(source_path, tmp)
        except FileExistsError:
            continue
        try:
            os.replace(tmp, path)
        except Exception:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            raise
        return
    raise FileExistsError(f"could not create unique temporary symlink for {path}")
