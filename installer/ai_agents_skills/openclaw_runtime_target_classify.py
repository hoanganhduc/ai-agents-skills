"""OpenClaw support-file classifier + metadata generator (issue 4).

Consumes the previously-unread ``manifest/schema/openclaw/target-support-file.schema.json``
and produces a schema-valid ``openclaw.target-support-file.v1`` doc per skill.

Classification is deny-first and deterministic so S3 (inert data) vs S4 (executable
helper, host broker) routing is mechanical:
  1. secret/provider/live-config (denied)        -> blocked record (not installable)
  2. binary                                       -> blocked (issue 9)
  3. executable (suffix / shebang / mode 0755)    -> executable-helper, S4
  4. allowlisted inert text                       -> text-support, S3
  5. anything else                                -> ValueError (fail closed)
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from .runtime import runtime_example_config, runtime_path_denied

SCHEMA_VERSION = "openclaw.target-support-file.v1"

EXECUTABLE_SUFFIXES = (".sh", ".bash", ".bat", ".ps1", ".py", ".cmd", ".js", ".sage")
INERT_SUFFIXES = (
    ".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".csv", ".tsv",
    ".cfg", ".ini", ".tex", ".html", ".css", ".svg", ".sql",
)
# Incidental no-extension text files that are inert data, not skill payload code.
INERT_DOTFILES = (".gitignore", ".gitattributes", ".dockerignore", ".editorconfig")
DEFAULT_PLATFORMS = ("linux", "macos", "windows", "wsl-native")

_SHELL_BY_SUFFIX = {
    ".sh": ["posix-sh", "bash"],
    ".bash": ["posix-sh", "bash"],
    ".ps1": ["powershell"],
    ".bat": ["cmd"],
    ".cmd": ["cmd"],
}


def _suffix(relative_path: str) -> str:
    return PurePosixPath(relative_path).suffix.lower()


def _shell_families(suffix: str) -> list[str]:
    return list(_SHELL_BY_SUFFIX.get(suffix, ["none"]))


def _path_styles(suffix: str) -> list[str]:
    if suffix in (".ps1", ".bat", ".cmd"):
        return ["windows-drive"]
    if suffix in (".sh", ".bash"):
        return ["posix"]
    return ["posix", "windows-drive"]


def classify_support_file(
    relative_path: str,
    *,
    mode: str = "0644",
    file_type: str = "text",
    has_shebang: bool = False,
    platforms: list[str] | None = None,
) -> dict[str, Any]:
    """Return a schema-valid per-file record with an added private ``decision`` key
    (deny | binary-blocked | s4 | s3). Raises ValueError for unclassified files."""
    suffix = _suffix(relative_path)
    plats = list(platforms) if platforms else list(DEFAULT_PLATFORMS)
    executable = mode == "0755" or has_shebang or suffix in EXECUTABLE_SUFFIXES

    name = PurePosixPath(relative_path).name
    if runtime_path_denied(relative_path):
        decision, artifact_class, execution_role = "deny", "openclaw-native-surface", "blocked"
        wrapper, mode_policy, shells, helper = "blocked", "blocked", ["none"], True
    elif file_type == "binary":
        decision, artifact_class, execution_role = "binary-blocked", "binary-support", "blocked"
        wrapper, mode_policy, shells, helper = "blocked", "blocked", ["none"], True
    elif file_type == "text" and (runtime_example_config(relative_path) or name in INERT_DOTFILES):
        # Config/template examples and incidental VCS dotfiles are inert data the
        # user reads/copies — never executed directly, so route to S3 (read-only).
        decision, artifact_class, execution_role = "s3", "text-support", "read-only-reference"
        wrapper, mode_policy, shells, helper = "none", "0644", ["none"], False
    elif executable:
        decision, artifact_class = "s4", "executable-helper"
        execution_role = "runtime-wrapper" if PurePosixPath(relative_path).name.startswith("run_") else "direct-helper"
        wrapper, mode_policy, shells, helper = "shared-ai-agents-skills-runtime", "0755", _shell_families(suffix), True
    elif file_type == "text" and suffix in INERT_SUFFIXES:
        decision, artifact_class, execution_role = "s3", "text-support", "read-only-reference"
        wrapper, mode_policy, shells, helper = "none", "0644", ["none"], False
    else:
        raise ValueError(f"OpenClaw support file is unclassified (fail closed): {relative_path}")

    newline_policy = "binary" if file_type == "binary" else "preserve"
    path_styles = _path_styles(suffix)
    record = {
        "relative_path": relative_path,
        "artifact_class": artifact_class,
        "execution_role": execution_role,
        "compatibility": {
            "platform": plats[0],
            "path_style": path_styles[0],
            "shell_family": shells[0],
            "wrapper_runtime_class": wrapper,
            "newline_policy": newline_policy,
            "mode_policy": mode_policy,
        },
        "platforms": plats,
        "path_styles": path_styles,
        "shell_families": shells,
        "wrapper_runtime_class": wrapper,
        "newline_policy": newline_policy,
        "mode_policy": mode_policy,
        "file_type": file_type,
        "helper_evidence_required": helper,
        "decision": decision,
    }
    return record


def build_support_file_metadata(skill: str, file_specs: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a schema-valid openclaw.target-support-file.v1 doc for a skill.

    Each file_spec: {relative_path, mode?, file_type?, has_shebang?, platforms?}.
    Fail-closed: an unclassified file raises and no doc is emitted.
    """
    files = []
    for spec in file_specs:
        record = classify_support_file(
            spec["relative_path"],
            mode=spec.get("mode", "0644"),
            file_type=spec.get("file_type", "text"),
            has_shebang=spec.get("has_shebang", False),
            platforms=spec.get("platforms"),
        )
        # ``decision`` is a private routing hint; the schema is additionalProperties:false,
        # so it is stripped from the emitted doc (callers re-derive S3/S4/skip from
        # artifact_class/execution_role).
        record.pop("decision", None)
        files.append(record)
    return {"schema_version": SCHEMA_VERSION, "skill": skill, "files": files}


def support_file_routing(record: dict[str, Any]) -> str:
    """Re-derive S3/S4/skip routing from a schema record (no private decision key)."""
    if record.get("execution_role") == "blocked":
        return "skip"
    if record.get("artifact_class") == "executable-helper":
        return "s4"
    if record.get("artifact_class") == "text-support":
        return "s3"
    return "skip"
