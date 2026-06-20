"""OpenClaw runtime broker lifecycle + legacy-dir migration (P8).

- Per-workspace refcount for the host broker: the broker is shared per host but
  referenced by each OpenClaw workspace that installs a runtime skill; it (and its
  managed firewall rule) is torn down only when the last workspace uninstalls.
- Legacy migration: the pre-existing hand-placed ~/.openclaw/workspace/skills dirs
  are LEFT UNTOUCHED by default; opt-in adoption takes only byte+slug-matching dirs.

Pure, fully-testable; the actual systemd unit + firewall provisioning is the live
host-gated shell that consumes these decisions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

BROKER_RECORD_SCHEMA = "aas.broker.managed.v1"


def normalize_slug(name: str) -> str:
    return name.replace("_", "-")


def new_broker_record(
    *,
    file_hashes: dict[str, str],
    unit_path: str,
    token_path: str,
    endpoint: str,
    firewall_rule: str,
) -> dict[str, Any]:
    return {
        "schema": BROKER_RECORD_SCHEMA,
        "file_hashes": dict(file_hashes),
        "unit_path": unit_path,
        "token_path": token_path,
        "endpoint": endpoint,
        "firewall_rule": firewall_rule,
        "workspace_refs": [],
    }


def broker_install(record: dict[str, Any], workspace_key: str) -> dict[str, Any]:
    """Reference the broker from a workspace (idempotent)."""
    record = dict(record)
    refs = list(record.get("workspace_refs", []))
    if workspace_key not in refs:
        refs.append(workspace_key)
    record["workspace_refs"] = sorted(refs)
    return record


def broker_uninstall(record: dict[str, Any], workspace_key: str) -> tuple[dict[str, Any], bool]:
    """Drop a workspace's reference. Returns (record, teardown) where teardown is
    True only when no workspace references the broker any more (refcount 0)."""
    record = dict(record)
    refs = [r for r in record.get("workspace_refs", []) if r != workspace_key]
    record["workspace_refs"] = sorted(refs)
    return record, not refs


def broker_refcount(record: dict[str, Any]) -> int:
    return len(record.get("workspace_refs", []))


# --- Legacy migration --------------------------------------------------------
def classify_legacy_dir(
    legacy_dir: Path,
    *,
    canonical_root: Path,
    known_skills: set[str],
) -> dict[str, Any]:
    """Classify a hand-placed legacy ~/.openclaw/workspace/skills/<dir>:
      - 'unrecognized'  : slug is not a current canonical skill -> leave untouched
      - 'adopt-eligible': slug matches AND SKILL.md is byte-identical to canonical
      - 'divergent'     : slug matches but content differs -> leave untouched
    Default action is always to leave the legacy dir untouched unless adoption is
    opted into AND the dir is adopt-eligible.
    """
    slug = normalize_slug(legacy_dir.name)
    if slug not in known_skills:
        return {"dir": legacy_dir.name, "slug": slug, "decision": "unrecognized", "adopt_eligible": False}
    legacy_md = legacy_dir / "SKILL.md"
    canonical_md = canonical_root / slug / "SKILL.md"
    if not legacy_md.is_file() or not canonical_md.is_file():
        return {"dir": legacy_dir.name, "slug": slug, "decision": "divergent", "adopt_eligible": False}
    same = legacy_md.read_bytes() == canonical_md.read_bytes()
    decision = "adopt-eligible" if same else "divergent"
    return {"dir": legacy_dir.name, "slug": slug, "decision": decision, "adopt_eligible": same}


def plan_legacy_migration(
    workspace_skills_dir: Path,
    *,
    canonical_root: Path,
    known_skills: set[str],
    adopt: bool = False,
) -> list[dict[str, Any]]:
    """Produce a per-dir migration plan. Default (adopt=False) leaves everything
    untouched; with adopt=True, only adopt-eligible dirs are marked 'adopt'."""
    if not workspace_skills_dir.is_dir():
        return []
    plan = []
    for child in sorted(p for p in workspace_skills_dir.iterdir() if p.is_dir()):
        entry = classify_legacy_dir(child, canonical_root=canonical_root, known_skills=known_skills)
        if adopt and entry["adopt_eligible"]:
            entry["action"] = "adopt"
        else:
            entry["action"] = "leave-untouched"
        plan.append(entry)
    return plan
