from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .capabilities import normalized_path_within, resolved_path_within

STATE_SCHEMA_VERSION = 2
# v1 state files (no provenance) still load; only writes upgrade to v2.
SUPPORTED_STATE_SCHEMA_VERSIONS = (1, 2)
PROVENANCE_FIELDS = {
    "provenance_version",
    "source_commit",
    "content_id",
    "installer_version",
    "installed_at",
    "host_id",
    "provenance_status",
}
PROVENANCE_STATUSES = ("complete", "legacy-incomplete")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


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


def default_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "artifacts": [],
        "runs": [],
        "uninstall_records": [],
    }


def validate_state(data: Any, *, path: Path | None = None) -> dict[str, Any]:
    label = str(path) if path is not None else "state"
    if not isinstance(data, dict):
        raise ValueError(f"installer state must be a JSON object: {label}")
    if data.get("schema_version") not in SUPPORTED_STATE_SCHEMA_VERSIONS:
        raise ValueError(
            f"installer state has unsupported schema_version {data.get('schema_version')!r}: {label}"
        )
    for key in ("artifacts", "runs", "uninstall_records"):
        if key in data and not isinstance(data[key], list):
            raise ValueError(f"installer state field must be a list: {key} in {label}")
    if "provenance" in data:
        validate_state_provenance(data["provenance"], label=label)
    return data


def validate_state_provenance(provenance: Any, *, label: str = "state") -> None:
    """Strictly validate the optional top-level provenance record (D6/F6).

    Closes the prior 'extra top-level keys pass silently' gap for provenance:
    unknown keys, non-dict values, and bad provenance_status are rejected.
    Non-derivable provenance (e.g. pre-existing installs) is represented honestly
    via provenance_status='legacy-incomplete' rather than fabricated fields.
    """
    if not isinstance(provenance, dict):
        raise ValueError(f"installer state provenance must be an object: {label}")
    unknown = sorted(set(provenance) - PROVENANCE_FIELDS)
    if unknown:
        raise ValueError(f"installer state provenance has unknown fields {unknown}: {label}")
    status = provenance.get("provenance_status")
    if status is not None and status not in PROVENANCE_STATUSES:
        raise ValueError(f"installer state provenance_status is invalid: {status!r} in {label}")
    if "content_id" in provenance and not isinstance(provenance["content_id"], str):
        raise ValueError(f"installer state provenance content_id must be a string: {label}")


def build_state_provenance(
    *,
    source_commit: str,
    content_id: str,
    installer_version: str,
    installed_at: str,
    host_id: str,
) -> dict[str, Any]:
    """A complete, recomputed-not-fabricated provenance record for a new install."""
    provenance = {
        "provenance_version": 1,
        "source_commit": source_commit,
        "content_id": content_id,
        "installer_version": installer_version,
        "installed_at": installed_at,
        "host_id": host_id,
        "provenance_status": "complete",
    }
    validate_state_provenance(provenance)
    return provenance


def legacy_incomplete_provenance() -> dict[str, Any]:
    """Provenance marker for pre-existing records whose source_commit/content_id
    cannot be recomputed — flagged honestly rather than fabricated."""
    return {"provenance_version": 1, "provenance_status": "legacy-incomplete"}


def load_state(root: Path) -> dict[str, Any]:
    path = state_file(root)
    preflight_state_path(root, path)
    if not path.exists():
        return default_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"installer state is not valid JSON: {path}") from exc
    return validate_state(data, path=path)


def save_state(root: Path, data: dict[str, Any]) -> None:
    validate_state(data)
    path = state_file(root)
    preflight_state_path(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json_document_text(data))


def unused_backup_path(dest: Path) -> Path:
    """Return ``dest``, or the first free ``dest.N`` beside it.

    One run can back the same file up more than once -- a managed block is
    written per skill, so an agent's instructions file is snapshotted once for
    each -- and every one of those snapshots is a different file, taken before a
    different write.  A destination derived only from the run and the path would
    make them all the same name, so each copy would overwrite the one before it
    and only the last would survive.  Every record still holds the signature the
    file had before *its* own write, so the survivor matches at most one of them
    and rollback refuses the rest.  Giving each copy its own name keeps a
    snapshot per record, which is what rollback reads back.
    """
    if not dest.exists() and not dest.is_symlink():
        return dest
    ordinal = 1
    while True:
        candidate = dest.with_name(f"{dest.name}.{ordinal}")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        ordinal += 1


def backup_file(root: Path, run_id: str, path: Path) -> Path | None:
    if not path.exists() and not path.is_symlink():
        return None
    rel = str(path).replace(":", "").replace("\\", "/").lstrip("/")
    dest = state_dir(root) / "backups" / run_id / rel
    preflight_state_path(root, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest = unused_backup_path(dest)
    preflight_state_path(root, dest)
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


def upsert_run(data: dict[str, Any], run_id: str, action_count: int) -> None:
    """Record ``run_id`` in the run index, replacing any earlier entry.

    Run entries are keyed by ``run_id`` rather than the ``key`` field
    ``upsert_record`` expects, so they need their own upsert.  The apply loop
    calls this after every action: ``load_run_actions`` resolves a rollback
    target through this list, and a run absent from it is refused as unknown
    however complete its record is.
    """
    runs = data.setdefault("runs", [])
    entry = {"run_id": run_id, "action_count": action_count}
    for index, existing in enumerate(runs):
        if isinstance(existing, dict) and existing.get("run_id") == run_id:
            runs[index] = entry
            return
    runs.append(entry)


def run_record_path(root: Path, run_id: str) -> Path:
    validate_run_id(run_id)
    return state_dir(root) / "runs" / f"{run_id}.json"


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or run_id in {".", ".."} or RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError(f"invalid run id: {run_id!r}")
    return run_id


def write_run_record(root: Path, run_id: str, actions: list[dict[str, Any]]) -> None:
    path = run_record_path(root, run_id)
    preflight_state_path(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json_document_text({"run_id": run_id, "actions": actions}))


def json_document_text(data: Any) -> str:
    """Serialize large installer journals with a compact, fast encoder."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"


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
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        inherit_replaced_file_mode(tmp_name, path)
        os.replace(tmp_name, path)
    except Exception:
        if tmp_name is not None:
            try:
                Path(tmp_name).unlink()
            except FileNotFoundError:
                pass
        raise


def inherit_replaced_file_mode(tmp_name: str, path: Path) -> None:
    """Carry an existing file's permissions onto the replacement written for it.

    An atomic write replaces the destination inode, so the new file's mode is
    whatever ``mkstemp`` chose -- owner-only -- and not the mode the file being
    replaced had.  Editing a file is not a request to change who may read it,
    and the narrowing is silent and permanent: nothing records the previous
    mode, so no uninstall or rollback puts it back.  Only replacement is
    covered; a file this call creates keeps the private default.
    """
    if os.name == "nt":
        return
    try:
        existing = os.stat(path, follow_symlinks=False)
    except OSError:
        return
    if not stat.S_ISREG(existing.st_mode):
        return
    try:
        os.chmod(tmp_name, stat.S_IMODE(existing.st_mode))
    except OSError:
        return


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
