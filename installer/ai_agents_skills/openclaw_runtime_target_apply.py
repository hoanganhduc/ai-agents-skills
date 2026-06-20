"""OpenClaw runtime/support target orchestration (P8 CLI surface).

Gathers a skill's runtime files + neutral render + evidence into a content-addressed
runtime manifest (dry-run), and builds a broker state from approved manifests. The
actual support-file writes to .openclaw and the live broker bind/exec are host-gated
(they require a real OpenClaw home + the running broker); this module produces the
decisions/plans those steps consume.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .manifest import load_manifests
from .openclaw_runtime_broker import AgentToken, BrokerState
from .openclaw_runtime_target_evidence import now_utc
from .openclaw_runtime_target_manifest import (
    build_openclaw_runtime_target_manifest,
    load_runtime_target_manifest,
    validate_runtime_target_manifest,
)
from .openclaw_target_paths import (
    OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE,
    openclaw_home,
    validate_openclaw_target_home,
)
from .render import render_skill_md
from .runtime import (
    RUNTIME_SOURCE_ROOT,
    replace_with_runtime_file,
    runtime_expected_sha256,
    runtime_source_content_hash,
)


def gather_runtime_files(skill: str, manifests: dict[str, Any]) -> list[dict[str, Any]]:
    """The skill's runtime files with pinned source hashes (P0 integrity)."""
    spec = manifests["runtime"]["skills"][skill]
    files = []
    for entry in spec.get("files", []):
        source = RUNTIME_SOURCE_ROOT / entry["source"]
        files.append(
            {
                "relative_path": entry["target"],
                "mode": str(entry.get("mode", "0644")),
                "file_type": entry.get("type", "text"),
                "source_sha256": runtime_expected_sha256(source, entry),
            }
        )
    return files


def build_runtime_dry_run_manifest(
    *,
    root: Path,
    skill: str,
    action_class: str,
    evidence_paths: list[Path],
    runtime_root: Path,
    source_commit: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    manifests = load_manifests()
    if skill not in manifests["runtime"]["skills"]:
        raise ValueError(f"{skill!r} is not a runtime-backed skill")
    neutral_md = render_skill_md(skill, manifests["skills"]["skills"][skill], "openclaw")  # P5 (raises if leaky)
    runtime_files = gather_runtime_files(skill, manifests)
    evidence_items = [json.loads(Path(p).read_text(encoding="utf-8")) for p in evidence_paths]
    paths = validate_openclaw_target_home(root)
    return build_openclaw_runtime_target_manifest(
        skill=skill,
        action_class=action_class,
        neutral_skill_md=neutral_md,
        runtime_files=runtime_files,
        evidence_items=evidence_items,
        runtime_realpath=str(Path(runtime_root).expanduser().resolve(strict=False)),
        target_realpath=paths["home_realpath"],
        managed_skills_realpath=paths["managed_skills_realpath"],
        source_commit=source_commit,
        created_at=created_at or now_utc(),
    )


def broker_state_from_manifest(
    manifest: dict[str, Any], *, runtime_root: Path, agent: str, token: str
) -> BrokerState:
    """Build a BrokerState exposing an approved manifest's runtime files as
    per-(skill,command) entries to one agent token (the live broker consumes this)."""
    commands: dict[tuple[str, str], dict[str, str]] = {}
    skill = manifest["skill"]
    for record, route in ((r, manifest["routing"].get(r["relative_path"])) for r in manifest["files"]):
        if route != "s4":
            continue  # only executable (broker-delivered) files become runnable commands
        command = Path(record["relative_path"]).stem
        commands[(skill, command)] = {
            "target_rel": record["relative_path"],
            "expected_sha256": record.get("source_sha256") or "",
        }
    allowed = set(commands.keys())
    return BrokerState(
        runtime_root=Path(runtime_root).expanduser(),
        tokens={token: AgentToken(agent=agent, allowed=allowed)},
        commands=commands,
    )


def apply_runtime_target_manifest_file(
    manifest_path: Path,
    root: Path,
    *,
    runtime_root: Path,
    dry_run: bool = True,
    real_system: bool = False,
    confirm_phrase: str | None = None,
) -> dict[str, Any]:
    """Apply an approved runtime/support manifest. Writes inert S3 files under
    .openclaw/skills/<skill>/ and runtime (S4) files under the neutral root, each via
    a verify-before-write gate (live source must match the approved source_sha256).
    The live broker registration/serve is host-gated and only PLANNED here."""
    manifest = load_runtime_target_manifest(Path(manifest_path))
    validate_runtime_target_manifest(manifest, require_approved=True)
    if not dry_run:
        if not real_system:
            raise ValueError("OpenClaw runtime real writes require --real-system")
        if confirm_phrase != OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE:
            raise ValueError(f"apply aborted: confirmation phrase must be exactly: {OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE}")

    skill = manifest["skill"]
    manifests = load_manifests()
    source_by_target = {f["target"]: f for f in manifests["runtime"]["skills"][skill]["files"]}
    rroot = Path(runtime_root).expanduser()
    actions: list[dict[str, Any]] = []
    broker_commands: list[dict[str, str]] = []

    for record in manifest["files"]:
        rel = record["relative_path"]
        route = manifest["routing"].get(rel, "skip")
        if route == "skip":
            actions.append({"relative_path": rel, "route": "skip", "operation": "skip"})
            continue
        entry = source_by_target.get(rel)
        if entry is None:
            raise ValueError(f"runtime manifest file has no source mapping: {rel}")
        source = RUNTIME_SOURCE_ROOT / entry["source"]
        action_meta = {"file_type": entry.get("type", "text"), "newline_policy": entry.get("newline"),
                       "mode": str(entry.get("mode", "0644"))}
        live = runtime_source_content_hash(source, action_meta)
        if live != record.get("source_sha256"):
            raise ValueError(f"runtime source content changed vs approved manifest: {rel}")
        if route == "s3":
            dest = openclaw_home(root) / "skills" / skill / Path(rel).name
        else:  # s4 -> neutral runtime root
            dest = rroot / rel
            broker_commands.append({"command": Path(rel).stem, "target_rel": rel, "expected_sha256": record["source_sha256"]})
        entry_action = {"relative_path": rel, "route": route, "dest": str(dest),
                        "operation": "create" if not dest.exists() else "overwrite"}
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            replace_with_runtime_file(source, dest, action_meta)
            entry_action["applied"] = True
        actions.append(entry_action)

    return {
        "status": "dry-run" if dry_run else "applied",
        "skill": skill,
        "action_class": manifest["action_class"],
        "content_id": manifest["content_id"],
        "actions": actions,
        # S4 runtime files are delivered host-side; the broker exposes them as commands.
        "broker_registration": {
            "runtime_root": str(rroot),
            "commands": broker_commands,
            "note": "register with the host broker (openclaw-broker) — live serve is host-gated",
        },
    }
