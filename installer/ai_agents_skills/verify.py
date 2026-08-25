from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .capabilities import normalized_path_within, resolved_path_within, skill_path_is_agent_visible
from .render import MANAGED_MARKER, block_id
from .runtime import runtime_mode_ok, runtime_newline_ok
from .sanitize import has_sensitive_material
from .state import artifact_signature, load_state, sha256_file, signatures_match


def verify(root: Path, skill_filter: set[str] | None = None, agent_filter: set[str] | None = None) -> dict[str, Any]:
    state = load_state(root)
    artifacts = state.get("artifacts", [])
    agent_runtime_skills = runtime_skills_for_agent_scope(artifacts, skill_filter, agent_filter)
    include_agent_runtime_runner = runtime_runner_needed(artifacts, agent_runtime_skills)
    results: list[dict[str, Any]] = []
    for artifact in artifacts:
        skill = artifact.get("skill")
        agent = artifact.get("agent")
        if skill_filter and skill not in skill_filter:
            continue
        if agent_filter:
            if artifact.get("artifact_type") == "runtime-file" and "runtime" not in agent_filter:
                if skill == "runtime-runner":
                    if not include_agent_runtime_runner:
                        continue
                elif skill not in agent_runtime_skills:
                    continue
            elif agent not in agent_filter:
                continue
        results.append(verify_artifact(artifact, root=root))
    if not results:
        return {
            "status": "no-managed-artifacts",
            "checked": 0,
            "results": [],
            "reason": "no managed ai-agents-skills artifacts matched this scope",
        }
    status = "ok" if all(item["status"] == "ok" for item in results) else "failed"
    return {"status": status, "checked": len(results), "results": results}


def runtime_skills_for_agent_scope(
    artifacts: list[Any],
    skill_filter: set[str] | None,
    agent_filter: set[str] | None,
) -> set[str]:
    if not agent_filter or "runtime" in agent_filter:
        return set()
    skills = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        if artifact.get("artifact_type") == "runtime-file":
            continue
        skill = artifact.get("skill")
        if skill_filter and skill not in skill_filter:
            continue
        if artifact.get("agent") not in agent_filter:
            continue
        if isinstance(skill, str):
            skills.add(skill)
    return skills


def runtime_runner_needed(artifacts: list[Any], runtime_skills: set[str]) -> bool:
    if not runtime_skills:
        return False
    return any(
        isinstance(artifact, dict)
        and artifact.get("artifact_type") == "runtime-file"
        and artifact.get("skill") in runtime_skills
        for artifact in artifacts
    )


_MARKER_TEXT_SUFFIXES = frozenset({".md", ".sh", ".py", ".yaml", ".yml", ".sage", ".toml", ".ps1"})


def managed_marker_applies(path: Path) -> bool:
    """Whether the installer embeds an inline managed-marker in this file type.

    Mirrors ``render.add_managed_support_header``: Markdown and comment-bearing
    code files get an inline marker; formats without a comment syntax (e.g.
    ``.json``) are written verbatim and rely on ``installed-signature-match``.
    """
    return path.suffix.lower() in _MARKER_TEXT_SUFFIXES


# Artifact types where the installer owns one region of a user-authored file and
# never the whole file: hashing the file would turn any ordinary user edit into a
# verify failure.  Each type gets a region-scoped check below instead.
PARTIALLY_OWNED_ARTIFACT_TYPES = frozenset({
    "instruction-block",
    "management-notice",
    "settings-hook-merge",
    "settings-compat-merge",
})


def verify_artifact(artifact: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    """Check one recorded artifact against disk.

    Damage does not always present as changed bytes: a managed file can be
    replaced by a directory or made unreadable.  Those are failures of this
    artifact, so they are reported as one, rather than raised into ``verify``
    where they would abort the run before the other artifacts are checked.
    """
    try:
        return _verify_artifact(artifact, root)
    except OSError as exc:
        return {
            "agent": artifact.get("agent"),
            "skill": artifact.get("skill"),
            "artifact": artifact.get("artifact"),
            "artifact_type": artifact.get("artifact_type"),
            "status": "failed",
            "checks": [{"name": "artifact-readable", "ok": False, "detail": str(exc)}],
        }


def _verify_artifact(artifact: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    path = Path(artifact["artifact"])
    checks: list[dict[str, Any]] = []
    path_checks = artifact_path_checks(path, artifact, root)
    checks.extend(path_checks)
    if any(check["ok"] is False for check in path_checks):
        return {
            "agent": artifact.get("agent"),
            "skill": artifact.get("skill"),
            "artifact": artifact.get("artifact"),
            "artifact_type": artifact.get("artifact_type"),
            "status": "failed",
            "checks": checks,
        }
    checks.append({"name": "file-exists", "ok": path.exists() or path.is_symlink()})
    expected_signature = artifact.get("installed_signature")
    if expected_signature is not None:
        if artifact.get("artifact_type") not in PARTIALLY_OWNED_ARTIFACT_TYPES:
            checks.append({
                "name": "installed-signature-match",
                "ok": signatures_match(artifact_signature(path), expected_signature),
            })
    if artifact.get("adopted"):
        expected_hash = artifact.get("new_hash")
        checks.append({
            "name": "adopted-hash-match",
            "ok": expected_hash is not None and sha256_file(path) == expected_hash,
        })
        if artifact.get("artifact_type") == "skill-file" and path.exists():
            checks.append(agent_visible_check(path, artifact, root))
        return {
            "agent": artifact.get("agent"),
            "skill": artifact.get("skill"),
            "artifact": artifact.get("artifact"),
            "artifact_type": artifact.get("artifact_type"),
            "status": "ok" if all(check["ok"] for check in checks) else "failed",
            "checks": checks,
        }
    if artifact.get("install_mode") == "symlink":
        verify_symlink_artifact(path, artifact, checks, root)
        return {
            "agent": artifact.get("agent"),
            "skill": artifact.get("skill"),
            "artifact": artifact.get("artifact"),
            "artifact_type": artifact.get("artifact_type"),
            "status": "ok" if all(check["ok"] for check in checks) else "failed",
            "checks": checks,
        }
    if artifact.get("artifact_type") == "skill-file" and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        checks.append({"name": "metadata-valid", "ok": skill_metadata_valid(text, artifact["skill"])})
        checks.append({"name": "managed-marker", "ok": MANAGED_MARKER in text})
        checks.append({"name": "no-secret-leak", "ok": not has_sensitive_material(text)})
        checks.append(agent_visible_check(path, artifact, root))
    if artifact.get("artifact_type") == "skill-support-file" and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        # Same asymmetry as the optional-artifact branch below: render only marks
        # comment-bearing formats, so demanding a marker on a .json support file
        # fails a fresh, undamaged install.
        if managed_marker_applies(path):
            checks.append({"name": "managed-marker", "ok": MANAGED_MARKER in text})
        checks.append({"name": "no-secret-leak", "ok": not has_sensitive_material(text)})
    if artifact.get("artifact_type") == "runtime-file" and path.exists():
        checks.append({
            "name": "source-hash-match",
            "ok": artifact.get("source_sha256") is not None and sha256_file(path) == artifact.get("source_sha256"),
        })
        checks.append({"name": "runtime-mode", "ok": runtime_mode_ok(path, artifact.get("mode"))})
        checks.append({"name": "runtime-newline-policy", "ok": runtime_newline_ok(path, artifact.get("newline_policy"))})
        if artifact.get("file_type") == "text":
            text = path.read_text(encoding="utf-8", errors="replace")
            checks.append({"name": "no-secret-leak", "ok": not has_sensitive_material(text)})
    if artifact.get("artifact_type") in {"instruction-block", "management-notice"}:
        instruction_regular = path.exists() and path.is_file() and not path.is_symlink()
        checks.append({"name": "instruction-regular-file", "ok": instruction_regular})
    if artifact.get("artifact_type") in {"instruction-block", "management-notice"} and instruction_regular:
        text = path.read_text(encoding="utf-8", errors="replace")
        identifier = block_id(artifact["skill"])
        checks.append({"name": "managed-block-unique", "ok": managed_block_unique(text, identifier)})
        managed_block = extract_managed_block(text, identifier)
        checks.append({"name": "managed-block-present", "ok": managed_block is not None})
        expected_block = artifact.get("managed_block")
        if expected_block is not None:
            checks.append({
                "name": "managed-block-match",
                "ok": managed_block is not None and managed_block.strip() == expected_block.strip(),
            })
        checks.append({
            "name": "no-secret-leak",
            "ok": managed_block is not None and not has_sensitive_material(managed_block),
        })
    if artifact.get("artifact_type") == "settings-hook-merge" and path.is_file():
        verify_hook_merge_artifact(path, artifact, checks)
    if artifact.get("artifact_type") == "settings-compat-merge" and path.is_file():
        verify_compat_merge_artifact(path, artifact, checks)
    if artifact.get("artifact_type") in {"template", "instruction-doc", "entrypoint-alias"} and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        # Only comment-bearing formats carry an inline managed-marker (see
        # render.add_managed_support_header). Marker-less formats such as .json
        # are written verbatim and verified by installed-signature-match above.
        if managed_marker_applies(path):
            checks.append({"name": "managed-marker", "ok": MANAGED_MARKER in text})
        checks.append({"name": "no-secret-leak", "ok": not has_sensitive_material(text)})
        if artifact.get("agent") == "antigravity" and artifact.get("artifact_type") == "entrypoint-alias":
            checks.append({"name": "antigravity-skill-frontmatter", "ok": skill_metadata_valid(text, str(artifact.get("artifact_name")))})
        if artifact.get("agent") == "antigravity" and artifact.get("artifact_type") == "instruction-doc":
            checks.append({"name": "antigravity-rule-frontmatter", "ok": skill_metadata_valid(text, str(artifact.get("artifact_name")))})
    if artifact.get("artifact_type") == "plugin" and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        checks.append({"name": "no-secret-leak", "ok": not has_sensitive_material(text)})
        checks.append({"name": "antigravity-plugin-json", "ok": antigravity_plugin_json_valid(text) if artifact.get("agent") == "antigravity" else True})
    if artifact.get("artifact_type") in {"mcp-config", "hook-config", "settings-file"} and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        checks.append({"name": "no-secret-leak", "ok": not has_sensitive_material(text)})
        if artifact.get("agent") == "antigravity":
            checks.append({
                "name": f"antigravity-{artifact.get('artifact_type')}-json",
                "ok": antigravity_native_config_valid(str(artifact.get("artifact_type")), text),
            })
    if artifact.get("artifact_type") == "agent-persona" and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        checks.append({"name": "managed-marker", "ok": MANAGED_MARKER in text})
        checks.append({"name": "no-secret-leak", "ok": not has_sensitive_material(text)})
        if artifact.get("agent") == "codex":
            checks.append({"name": "codex-persona-schema", "ok": "developer_instructions" in text})
        if artifact.get("agent") == "claude":
            checks.append({"name": "claude-persona-frontmatter", "ok": text.lstrip().startswith("---")})
        if artifact.get("agent") == "opencode":
            checks.append({"name": "opencode-persona-subagent", "ok": "mode: subagent" in text})
        if artifact.get("agent") == "antigravity":
            checks.append({"name": "antigravity-persona-frontmatter", "ok": text.lstrip().startswith("---") and "target: antigravity" in text})
        if artifact.get("agent") == "grok":
            checks.append({"name": "grok-persona-frontmatter", "ok": text.lstrip().startswith("---")})
        if artifact.get("agent") == "kimi":
            checks.append({"name": "kimi-persona-frontmatter", "ok": text.lstrip().startswith("---")})
    return {
        "agent": artifact.get("agent"),
        "skill": artifact.get("skill"),
        "artifact": artifact.get("artifact"),
        "artifact_type": artifact.get("artifact_type"),
        "status": "ok" if all(check["ok"] for check in checks) else "failed",
        "checks": checks,
    }


def verify_symlink_artifact(
    path: Path, artifact: dict[str, Any], checks: list[dict[str, Any]], root: Path | None = None
) -> None:
    source = Path(artifact.get("source_path", ""))
    checks.append({"name": "symlink", "ok": path.is_symlink()})
    checks.append({"name": "source-exists", "ok": source.exists()})
    checks.append({
        "name": "source-match",
        "ok": path.is_symlink() and source.exists() and path.resolve() == source.resolve(),
    })
    if artifact.get("artifact_type") == "skill-file" and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        checks.append({"name": "metadata-valid", "ok": skill_metadata_valid(text, artifact["skill"])})
        checks.append(agent_visible_check(path, artifact, root))
        checks.append({"name": "no-secret-leak", "ok": not has_sensitive_material(text)})
    if artifact.get("artifact_type") == "skill-support-file" and path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        checks.append({"name": "no-secret-leak", "ok": not has_sensitive_material(text)})


def agent_visible_check(path: Path, artifact: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    """Whether the agent would actually load this skill file today.

    The naming convention alone is fixed at install time by the path the planner
    chose, so a check built only from it can never fail.  What can change after
    install is where the agent looks: Antigravity, for one, resolves its skills
    directory from its own config, so a migrated home leaves correctly named
    files sitting outside the directory the agent now reads.  Re-resolving the
    target here is what turns this into a check.
    """
    named_for_agent = skill_path_is_agent_visible(
        str(artifact.get("agent")), path, str(artifact["skill"])
    )
    if not named_for_agent or root is None:
        return {"name": "agent-visible", "ok": named_for_agent}
    expected = agent_skill_file_now(root, str(artifact.get("agent")), str(artifact["skill"]))
    if expected is None:
        return {"name": "agent-visible", "ok": named_for_agent}
    return {"name": "agent-visible", "ok": str(expected) == str(path)}


def agent_skill_file_now(root: Path, agent: str, skill: str) -> Path | None:
    """Where this agent's skill file belongs given the home as it stands now."""
    from .agents import target_for

    try:
        return target_for(Path(root), agent).skill_file_for(skill)
    except Exception:  # noqa: BLE001 - an unknown agent name is not a disk failure
        return None


def verify_hook_merge_artifact(path: Path, artifact: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    """Check the one hook entry this installer owns, not the user's whole file.

    No secret scan here: the entry's command is a path under the user's own home
    by construction, which ``has_sensitive_material`` reports as a home-path leak.
    """
    from .json_merge import extract_hook_entry, load_json_object

    try:
        settings, _existed = load_json_object(path)
    except ValueError as exc:
        checks.append({"name": "settings-parseable", "ok": False, "detail": str(exc)})
        return
    checks.append({"name": "settings-parseable", "ok": True})
    entry = extract_hook_entry(settings, str(artifact.get("event")), str(artifact.get("managed_id")))
    checks.append({"name": "managed-entry-present", "ok": entry is not None})
    expected_entry = artifact.get("managed_entry")
    if expected_entry is not None:
        checks.append({"name": "managed-entry-match", "ok": entry == expected_entry})


def verify_compat_merge_artifact(path: Path, artifact: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    """Check the one managed block this installer owns inside the user's config.

    Same as the hook entry above: the block can carry the user's own paths, so it
    is checked for shape, not scanned for home paths.
    """
    from .toml_merge import managed_block_span, unmanaged_table_has_bool_values

    text = path.read_text(encoding="utf-8", errors="replace")
    span = managed_block_span(text, str(artifact.get("managed_id")))
    compatible_user_table = False
    expected_body = artifact.get("managed_body")
    if span is None and artifact.get("compat_table_policy") == "user-authored-compatible":
        compatible_user_table = unmanaged_table_has_bool_values(
            text,
            str(artifact.get("managed_id")),
            "compat.claude",
            {"skills": False, "agents": False, "rules": False, "hooks": False},
        )
        checks.append({"name": "compatible-user-table", "ok": compatible_user_table})
    checks.append({"name": "managed-block-present", "ok": span is not None or compatible_user_table})
    block = text[span[0]:span[1]] if span is not None else None
    if expected_body is not None and span is not None:
        checks.append({
            "name": "managed-block-match",
            "ok": block is not None and expected_body.strip() in block,
        })


def artifact_path_checks(
    path: Path,
    artifact: dict[str, Any],
    root: Path | None,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if root is not None:
        checks.append({"name": "artifact-contained", "ok": normalized_path_within(root, path)})
        checks.append({"name": "artifact-parent-contained", "ok": resolved_path_within(root, path.parent)})
    if path.is_symlink() and artifact.get("install_mode") != "symlink":
        checks.append({
            "name": "artifact-not-symlink",
            "ok": False,
            "detail": "non-symlink artifact record points at a symlink",
        })
    return checks


def extract_managed_block(text: str, identifier: str) -> str | None:
    start = f"<!-- {identifier}:start -->"
    end = f"<!-- {identifier}:end -->"
    if not managed_block_unique(text, identifier):
        return None
    start_index = text.find(start)
    if start_index == -1:
        return None
    end_index = text.find(end, start_index)
    if end_index == -1:
        return None
    return text[start_index:end_index + len(end)]


def managed_block_unique(text: str, identifier: str) -> bool:
    start = f"<!-- {identifier}:start -->"
    end = f"<!-- {identifier}:end -->"
    start_count = text.count(start)
    end_count = text.count(end)
    return (
        start_count == 1
        and end_count == 1
        and text.find(start) < text.find(end)
    )


def skill_metadata_valid(text: str, skill: str) -> bool:
    frontmatter = extract_frontmatter(text)
    if frontmatter is None:
        return False
    try:
        import yaml  # type: ignore
    except ImportError:
        data = parse_simple_frontmatter(frontmatter)
    else:
        try:
            data = yaml.safe_load(frontmatter)
        except Exception:
            return False
    return (
        isinstance(data, dict)
        and data.get("name") == skill
        and isinstance(data.get("description"), str)
    )


def antigravity_plugin_json_valid(text: str) -> bool:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    return (
        isinstance(data, dict)
        and data.get("name") == "ai-agents-skills"
        and isinstance(data.get("version"), str)
        and isinstance(data.get("components"), list)
    )


def antigravity_native_config_valid(artifact_type: str, text: str) -> bool:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    if artifact_type == "mcp-config":
        return isinstance(data.get("mcpServers"), dict)
    if artifact_type in {"hook-config", "settings-file"}:
        return True
    return False


def extract_frontmatter(text: str) -> str | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    return text[4:end]


def parse_simple_frontmatter(frontmatter: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if not line or line.startswith(" ") or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        value = raw_value.strip()
        if value.startswith('"') and value.endswith('"'):
            try:
                data[key.strip()] = json.loads(value)
                continue
            except json.JSONDecodeError:
                pass
        data[key.strip()] = value
    return data
