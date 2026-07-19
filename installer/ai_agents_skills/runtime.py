from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import stat
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from .capabilities import normalized_path_within, resolved_path_within
from .discovery import current_platform
from .manifest import REPO_ROOT
from .openclaw_target_gate import real_openclaw_path_block_reason
from .sanitize import has_sensitive_material, sanitize_text
from .state import artifact_signature, sha256_file


RUNTIME_SOURCE_ROOT = REPO_ROOT / "canonical" / "runtime"
TEXT_SUFFIXES = {".bat", ".json", ".md", ".ps1", ".py", ".sh", ".toml", ".txt", ".yaml", ".yml"}
FALLBACK_DENIED_PATTERNS = (
    ".env",
    "**/.env",
    "*.env",
    "**/*.env",
    "mcp*.json",
    "**/mcp*.json",
    "*mcp*.toml",
    "**/*mcp*.toml",
    ".mcp/**",
    "**/.mcp/**",
    "package.json",
    "**/package.json",
    "provider*.json",
    "**/provider*.json",
    "provider*.toml",
    "**/provider*.toml",
    "provider*.yaml",
    "**/provider*.yaml",
    "provider*.yml",
    "**/provider*.yml",
    "*provider-config*.json",
    "**/*provider-config*.json",
    "*provider-config*.toml",
    "**/*provider-config*.toml",
    "*provider-config*.yaml",
    "**/*provider-config*.yaml",
    "*provider-config*.yml",
    "**/*provider-config*.yml",
    "axle*.json",
    "**/axle*.json",
    "axle*.toml",
    "**/axle*.toml",
    "axle*.yaml",
    "**/axle*.yaml",
    "axle*.yml",
    "**/axle*.yml",
    "openclaw*.json",
    "**/openclaw*.json",
    "config.json",
    "**/config.json",
    "config.toml",
    "**/config.toml",
    "secrets.*",
    "**/secrets.*",
    ".secrets*",
    "**/.secrets*",
    "workspace/config/*.toml",
    "**/workspace/config/*.toml",
    "**/config/*.toml",
    "Dockerfile",
    "**/Dockerfile",
    "docker-compose.yml",
    "**/docker-compose.yml",
    "docker-compose.yaml",
    "**/docker-compose.yaml",
    "compose.yml",
    "**/compose.yml",
    "compose.yaml",
    "**/compose.yaml",
    "Procfile",
    "**/Procfile",
    "*.service",
    "**/*.service",
    "*.timer",
    "**/*.timer",
    "*.plist",
    "**/*.plist",
    "workspace/data/**",
    "**/reports/**",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/*.db",
    "**/*.db-*",
    "**/*.sqlite",
    "**/*.sqlite-*",
    "**/*.pdf",
    "**/*.epub",
    "**/*.zip",
)
PERSISTENCE_MARKERS = (
    ("restart: unless-stopped", "docker restart policy"),
    ("--restart=unless-stopped", "docker restart policy"),
    ("crontab -", "cron registration"),
    (" crontab ", "cron registration"),
    ("systemctl enable", "systemd service enablement"),
    ("launchctl load", "launchd service loading"),
    ("schtasks /create", "Windows scheduled task creation"),
    ("register-scheduledtask", "Windows scheduled task creation"),
)


def resolve_runtime_skills(
    selected_skills: list[str],
    runtime_manifest: dict[str, Any],
    runtime_profile: str = "auto",
) -> list[str]:
    if runtime_profile == "none":
        return []
    profiles = runtime_manifest.get("runtime_profiles", {})
    if runtime_profile not in profiles:
        raise ValueError(f"unknown runtime profile: {runtime_profile}")
    declared = runtime_manifest.get("skills", {})
    profile = profiles[runtime_profile]
    if profile.get("mode") == "selected-skills":
        roots = [skill for skill in selected_skills if skill in declared]
    else:
        roots = [skill for skill in profile.get("skills", []) if skill in declared]
    return runtime_dependency_closure(roots, declared)


def runtime_dependency_closure(
    roots: list[str],
    declared: dict[str, Any],
) -> list[str]:
    resolved: set[str] = set()
    active: list[str] = []
    active_set: set[str] = set()

    def visit(skill: str) -> None:
        if skill in resolved:
            return
        if skill in active_set:
            cycle_start = active.index(skill)
            cycle = active[cycle_start:] + [skill]
            raise ValueError(f"runtime dependency cycle: {' -> '.join(cycle)}")

        spec = declared.get(skill)
        if not isinstance(spec, dict):
            raise ValueError(f"runtime skill {skill} must be an object")
        requires = spec.get("runtime_requires", [])
        if not isinstance(requires, list):
            raise ValueError(f"runtime skill {skill} runtime_requires must be a list")

        dependencies: set[str] = set()
        for dependency in requires:
            if not isinstance(dependency, str) or not dependency:
                raise ValueError(
                    f"runtime skill {skill} runtime_requires entries must be non-empty strings"
                )
            if dependency not in declared:
                raise ValueError(
                    f"runtime skill {skill} requires unknown runtime skill {dependency}"
                )
            dependencies.add(dependency)

        active.append(skill)
        active_set.add(skill)
        try:
            for dependency in sorted(dependencies):
                visit(dependency)
        finally:
            active.pop()
            active_set.remove(skill)
        resolved.add(skill)

    for root in sorted(set(roots)):
        visit(root)
    return sorted(resolved)


def default_runtime_root(root: Path, agents: list[Any], platform: str | None = None) -> Path:
    agent_names = {agent.name for agent in agents}
    if agent_names == {"codex"}:
        return root / ".codex" / "runtime"
    platform_name = current_platform(platform)
    if platform_name == "windows":
        return root / "AppData" / "Local" / "ai-agents-skills" / "runtime"
    return root / ".local" / "share" / "ai-agents-skills" / "runtime"


def build_runtime_actions(
    *,
    root: Path,
    manifests: dict[str, Any],
    selected_skills: list[str],
    agents: list[Any],
    runtime_profile: str = "auto",
    runtime_root: Path | None = None,
    platform: str | None = None,
    backup_replace: bool = False,
) -> list[dict[str, Any]]:
    if not agents:
        return []
    runtime_manifest = manifests.get("runtime", {})
    runtime_skills = resolve_runtime_skills(selected_skills, runtime_manifest, runtime_profile)
    if not runtime_skills:
        return []
    platform_name = current_platform(platform)
    target_root = (runtime_root or default_runtime_root(root, agents, platform)).expanduser()
    actions: list[dict[str, Any]] = []
    seen_targets: dict[str, str] = {}
    for entry in runtime_manifest.get("runners", []):
        if not runtime_entry_applies(entry, platform_name):
            continue
        actions.append(
            runtime_file_action(
                root=root,
                runtime_root=target_root,
                entry=entry,
                skill="runtime-runner",
                artifact_name=Path(entry["target"]).name,
                backup_replace=backup_replace,
                seen_targets=seen_targets,
            )
        )
    for skill in runtime_skills:
        spec = runtime_manifest["skills"][skill]
        for entry in spec.get("files", []):
            if not runtime_entry_applies(entry, platform_name):
                continue
            actions.append(
                runtime_file_action(
                    root=root,
                    runtime_root=target_root,
                    entry=entry,
                    skill=skill,
                    artifact_name=entry["target"],
                    backup_replace=backup_replace,
                    seen_targets=seen_targets,
                )
            )
    return actions


def runtime_entry_applies(entry: dict[str, Any], platform_name: str) -> bool:
    platforms = entry.get("platforms", [])
    return not platforms or platform_name in platforms


def runtime_file_action(
    *,
    root: Path,
    runtime_root: Path,
    entry: dict[str, Any],
    skill: str,
    artifact_name: str,
    backup_replace: bool,
    seen_targets: dict[str, str],
) -> dict[str, Any]:
    source = RUNTIME_SOURCE_ROOT / entry["source"]
    target = runtime_root / entry["target"]
    source_hash = sha256_file(source)
    expected_hash = runtime_expected_sha256(source, entry)
    target_key = os.path.normcase(os.path.abspath(target))
    previous_hash = seen_targets.get(target_key)
    if previous_hash is not None and previous_hash != expected_hash:
        return blocked_runtime_action(
            target,
            source,
            skill,
            artifact_name,
            entry,
            "same runtime target has multiple source hashes",
        )
    seen_targets[target_key] = expected_hash or ""
    blocked = runtime_source_block_reason(source, entry)
    if blocked:
        return blocked_runtime_action(target, source, skill, artifact_name, entry, blocked)
    current_hash = sha256_file(target)
    reason = None
    if not target.exists() and not target.is_symlink():
        classification = "missing"
        operation = "create"
    elif target.is_dir():
        classification = "conflict"
        operation = "skip"
        reason = "target path is a directory"
    elif current_hash == expected_hash:
        classification = "managed"
        operation = "noop"
    elif backup_replace:
        classification = "conflict"
        operation = "backup-replace"
    else:
        classification = "unmanaged"
        operation = "skip"
        reason = "target path exists and differs from runtime source"
    action = {
        "kind": "runtime-file",
        "agent": "runtime",
        "owner": "runtime",
        "skill": skill,
        "path": str(target),
        "artifact_type": "runtime-file",
        "artifact_id": f"runtime-file:{skill}:{artifact_name}",
        "artifact_name": artifact_name,
        "classification": classification,
        "operation": operation,
        "source_path": str(source),
        "source_relpath": entry["source"],
        "target_relpath": entry["target"],
        "source_sha256": expected_hash,
        "canonical_source_sha256": source_hash,
        "current_hash": current_hash if target.exists() else None,
        "current_signature": artifact_signature(target),
        "mode": entry.get("mode", "0644"),
        "newline_policy": entry.get("newline"),
        "file_type": entry.get("type", "text"),
        "platforms": entry.get("platforms", []),
        "runtime_root": str(runtime_root),
    }
    if reason:
        action["reason"] = reason
    return action


def blocked_runtime_action(
    target: Path,
    source: Path,
    skill: str,
    artifact_name: str,
    entry: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    return {
        "kind": "runtime-file",
        "agent": "runtime",
        "owner": "runtime",
        "skill": skill,
        "path": str(target),
        "artifact_type": "runtime-file",
        "artifact_id": f"runtime-file:{skill}:{artifact_name}",
        "artifact_name": artifact_name,
        "classification": "blocked",
        "operation": "skip",
        "source_path": str(source),
        "source_relpath": entry["source"],
        "target_relpath": entry["target"],
        "mode": entry.get("mode", "0644"),
        "newline_policy": entry.get("newline"),
        "file_type": entry.get("type", "text"),
        "platforms": entry.get("platforms", []),
        "reason": reason,
    }


def runtime_source_block_reason(source: Path, entry: dict[str, Any]) -> str | None:
    if not source.is_file():
        return "runtime source is missing"
    if source.is_symlink():
        return "runtime source must not be a symlink"
    relative = source.relative_to(RUNTIME_SOURCE_ROOT).as_posix()
    if runtime_path_denied(relative):
        return "runtime source matches denied pattern"
    if entry.get("type") == "text":
        try:
            text = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return "text runtime source is not valid UTF-8"
        if has_sensitive_material(text):
            return "runtime source contains sensitive material or personal paths"
        persistence_reason = runtime_persistence_block_reason(text)
        if persistence_reason:
            return persistence_reason
    return None


@lru_cache(maxsize=1)
def runtime_denied_patterns() -> tuple[str, ...]:
    manifest_path = REPO_ROOT / "manifest" / "runtime.yaml"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return FALLBACK_DENIED_PATTERNS
    patterns = data.get("denied_patterns")
    if not isinstance(patterns, list) or not all(isinstance(item, str) and item for item in patterns):
        return FALLBACK_DENIED_PATTERNS
    return tuple(patterns)


# Secret-class files are denied even when named as .example/.sample templates
# (closes the secrets.example / .env.example bypass). Provider/config example
# templates are intentionally NOT secret-class and remain carve-out-eligible.
RUNTIME_SECRET_CLASS_PATTERNS = (
    "secrets.*", "**/secrets.*", ".secrets*", "**/.secrets*",
    ".env", "**/.env", "*.env", "**/*.env",
    "*.pem", "**/*.pem", "id_rsa*", "**/id_rsa*", "id_ed25519*", "**/id_ed25519*",
    ".netrc", "**/.netrc",
)


def _strip_example_markers(name: str) -> str:
    for marker in (".example", ".sample"):
        name = name.replace(marker, "")
    return name


def runtime_secret_class_denied(relative: str) -> bool:
    normalized = relative.replace("\\", "/")
    base = normalized.rsplit("/", 1)[-1]
    prefix = normalized[: len(normalized) - len(base)]
    deexampled = prefix + _strip_example_markers(base)
    for candidate in {normalized, deexampled}:
        if any(fnmatch.fnmatch(candidate, pattern) for pattern in RUNTIME_SECRET_CLASS_PATTERNS):
            return True
    return False


def runtime_path_denied(relative: str) -> bool:
    normalized = relative.replace("\\", "/")
    # Deny secret-class files BEFORE the .example/.sample carve-out (deny-before-carve-out).
    if runtime_secret_class_denied(normalized):
        return True
    if runtime_example_config(normalized):
        return False
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in runtime_denied_patterns())


def runtime_example_config(relative: str) -> bool:
    name = Path(relative).name.lower()
    return ".example." in name or ".sample." in name or name.endswith((".example", ".sample"))


def runtime_persistence_block_reason(text: str) -> str | None:
    lowered = text.lower()
    for marker, reason in PERSISTENCE_MARKERS:
        if marker in lowered:
            return f"persistent execution marker: {reason}"
    return None


def apply_runtime_file_action(root: Path, run_id: str, action: dict[str, Any], base_result: dict[str, Any]) -> dict[str, Any]:
    target = Path(action["path"])
    source = Path(action["source_path"])
    result = dict(base_result)
    result["created_file"] = not target.exists()
    result["previous_hash"] = sha256_file(target)
    result["previous_signature"] = artifact_signature(target)
    if action.get("operation") in {"skip", "noop"}:
        result["managed"] = action.get("operation") == "noop"
        result["applied"] = False
        result["installed_signature"] = artifact_signature(target)
        copy_runtime_metadata(action, result)
        return result
    if action.get("operation") != "create" and action.get("operation") != "backup-replace":
        raise ValueError(f"unsupported runtime file operation: {action.get('operation')}")
    expected_source = action.get("source_sha256")
    if expected_source is not None:
        actual_source = runtime_source_content_hash(source, action)
        if actual_source != expected_source:
            raise ValueError(
                "runtime source content changed before write: "
                f"{action.get('source_relpath') or source} "
                f"(approved {expected_source}, found {actual_source})"
            )
    from .state import backup_file

    backup = backup_file(root, run_id, target)
    created_parent_dirs = missing_parent_dirs(root, target.parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    replace_with_runtime_file(source, target, action)
    result["managed"] = True
    result["applied"] = True
    result["backup"] = str(backup) if backup else None
    result["new_hash"] = sha256_file(target)
    result["installed_signature"] = artifact_signature(target)
    copy_runtime_metadata(action, result)
    if created_parent_dirs:
        result["created_parent_dirs"] = [item.as_posix() for item in created_parent_dirs]
    return result


def replace_with_runtime_file(source: Path, target: Path, action: dict[str, Any]) -> None:
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.runtime.",
        suffix=".tmp",
        dir=target.parent,
    )
    tmp = Path(tmp_name)
    try:
        if action.get("file_type") == "text":
            text = source.read_text(encoding="utf-8")
            newline = "\r\n" if action.get("newline_policy") == "crlf" else "\n"
            handle = os.fdopen(fd, "w", encoding="utf-8", newline="")
            fd = -1
            with handle:
                handle.write(text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline))
        else:
            handle = os.fdopen(fd, "wb")
            fd = -1
            with handle, source.open("rb") as source_handle:
                shutil.copyfileobj(source_handle, handle)
        apply_mode(tmp, action.get("mode"))
        os.replace(tmp, target)
    finally:
        if fd != -1:
            os.close(fd)
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()


def runtime_expected_sha256(source: Path, entry: dict[str, Any]) -> str | None:
    if not source.is_file():
        return None
    if entry.get("type") == "text":
        try:
            text = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None
        newline = "\r\n" if entry.get("newline") == "crlf" else "\n"
        data = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline).encode("utf-8")
    else:
        data = source.read_bytes()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def runtime_source_content_hash(source: Path, action: dict[str, Any]) -> str | None:
    """Recompute the normalized sha256 of a runtime source at apply time.

    Uses the same normalization as ``runtime_expected_sha256`` but keyed on the
    fields recorded in an already-built action (``file_type``/``newline_policy``),
    so apply can verify the live source still matches the approved ``source_sha256``
    before writing. Returns ``None`` if the source is unreadable, which the apply
    gate treats as a mismatch (fail closed).
    """
    if not source.is_file():
        return None
    if action.get("file_type", "text") == "text":
        try:
            text = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None
        newline = "\r\n" if action.get("newline_policy") == "crlf" else "\n"
        data = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline).encode("utf-8")
    else:
        data = source.read_bytes()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def apply_mode(path: Path, mode_text: str | None) -> None:
    if os.name == "nt" or not mode_text:
        return
    try:
        path.chmod(int(mode_text, 8))
    except ValueError:
        return


def runtime_mode_ok(path: Path, mode_text: str | None) -> bool:
    if os.name == "nt" or not mode_text:
        return True
    try:
        expected = int(mode_text, 8)
    except ValueError:
        return False
    actual = stat.S_IMODE(path.stat().st_mode)
    return actual == expected


def runtime_newline_ok(path: Path, newline_policy: str | None) -> bool:
    if newline_policy not in {"lf", "crlf"} or not path.is_file():
        return True
    data = path.read_bytes()
    if newline_policy == "lf":
        return b"\r\n" not in data and b"\r" not in data
    return b"\n" not in data.replace(b"\r\n", b"")


def copy_runtime_metadata(action: dict[str, Any], result: dict[str, Any]) -> None:
    for key in (
        "source_relpath",
        "target_relpath",
        "source_sha256",
        "canonical_source_sha256",
        "mode",
        "newline_policy",
        "file_type",
        "platforms",
        "runtime_root",
        "owner",
    ):
        if key in action:
            result[key] = action[key]


def missing_parent_dirs(root: Path, parent: Path) -> list[Path]:
    missing: list[Path] = []
    current = parent
    while current != root and not current.exists():
        missing.append(current.relative_to(root))
        current = current.parent
    return missing


def runtime_inventory(source_root: Path, max_entries: int = 5000) -> dict[str, Any]:
    source_root = source_root.expanduser()
    # Inventory output is a portable evidence artifact.  The caller-selected
    # root is authority for the scan, but its machine-local absolute path is
    # never evidence and must not survive serialization (including temporary
    # worktrees outside a conventional home directory).
    source_root_label = "<RUNTIME_SOURCE_ROOT>"
    if not source_root.exists():
        return {"status": "missing", "source_root": source_root_label, "entries": []}
    if not source_root.is_dir() or source_root.is_symlink():
        return {"status": "blocked", "source_root": source_root_label, "reason": "source root must be a real directory", "entries": []}
    entries = []
    for index, path in enumerate(sorted(source_root.rglob("*"), key=lambda item: item.as_posix())):
        if index >= max_entries:
            return {
                "status": "truncated",
                "source_root": source_root_label,
                "max_entries": max_entries,
                "entries": entries,
            }
        relative = path.relative_to(source_root).as_posix()
        entry = {
            "path": relative,
            "kind": "symlink" if path.is_symlink() else "file",
            "size": safe_size(path),
            "classification": "candidate",
            "reason": None,
        }
        if path.is_symlink():
            entry["classification"] = "blocked"
            entry["reason"] = "symlink"
        elif path.is_dir():
            continue
        elif runtime_path_denied(relative):
            entry["classification"] = "denied"
            entry["reason"] = "denied pattern"
        elif path.suffix.lower() in TEXT_SUFFIXES:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                entry["classification"] = "blocked"
                entry["reason"] = "text-looking file is not UTF-8"
            else:
                if has_sensitive_material(text):
                    entry["classification"] = "blocked"
                    entry["reason"] = "sensitive material or personal path"
                else:
                    persistence_reason = runtime_persistence_block_reason(text)
                    if persistence_reason:
                        entry["classification"] = "blocked"
                        entry["reason"] = persistence_reason
        entries.append(entry)
    blocked = [item for item in entries if item["classification"] in {"blocked", "denied"}]
    return {
        "status": "ok",
        "source_root": source_root_label,
        "entry_count": len(entries),
        "blocked_count": len(blocked),
        "entries": entries,
    }


def safe_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def preflight_runtime_action(root: Path, action: dict[str, Any]) -> None:
    path = Path(action["path"])
    source = Path(action.get("source_path", ""))
    if not normalized_path_within(root, path) or not resolved_path_within(root, path.parent):
        raise ValueError(f"refusing to apply runtime artifact outside selected root: {path}")
    openclaw_block = real_openclaw_path_block_reason(root, path, operation="runtime", agent="runtime")
    if openclaw_block is not None:
        raise ValueError(openclaw_block)
    if not normalized_path_within(RUNTIME_SOURCE_ROOT, source) or not resolved_path_within(RUNTIME_SOURCE_ROOT, source.parent):
        raise ValueError(f"refusing runtime source outside canonical runtime root: {source}")
    for parent in existing_parents(path.parent, root):
        if parent.is_symlink():
            raise ValueError(f"refusing to apply runtime artifact through symlinked parent: {parent}")
        if not parent.is_dir():
            raise ValueError(f"refusing to apply runtime artifact through non-directory parent: {parent}")
    if action.get("operation") != "skip" and not planned_runtime_state_unchanged(path, action):
        raise ValueError(f"refusing to apply runtime artifact because target changed since plan: {path}")


def planned_runtime_state_unchanged(path: Path, action: dict[str, Any]) -> bool:
    planned = action.get("current_signature")
    if planned is None:
        return True
    from .state import signatures_match

    return signatures_match(artifact_signature(path), planned)


def existing_parents(path: Path, root: Path) -> list[Path]:
    parents: list[Path] = []
    current = Path(os.path.abspath(path))
    root_abs = Path(os.path.abspath(root))
    while True:
        if current.exists() or current.is_symlink():
            parents.append(current)
        if current == root_abs:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return parents
