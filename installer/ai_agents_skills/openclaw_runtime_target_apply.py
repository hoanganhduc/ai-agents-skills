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
from .openclaw_runtime_broker import ENV_ALLOW_EXACT, ENV_ALLOW_PREFIX, AgentToken, BrokerState
from .openclaw_runtime_target_evidence import build_runtime_target_evidence, now_utc
from .openclaw_runtime_target_paths import neutral_runtime_root_block_reason
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
    rroot = Path(runtime_root).expanduser().resolve(strict=False)
    if str(rroot) != str(manifest["runtime_realpath"]):
        raise ValueError("OpenClaw runtime target manifest runtime root does not match selected runtime root")
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
        runtime_root=rroot,
        tokens={token: AgentToken(agent=agent, allowed=allowed)},
        commands=commands,
    )


def validate_runtime_target_apply_paths(
    manifest: dict[str, Any], *, root: Path, runtime_root: Path
) -> tuple[Path, Path]:
    expanded_root = Path(root).expanduser()
    paths = validate_openclaw_target_home(expanded_root)
    if str(paths["home_realpath"]) != str(manifest["target_realpath"]):
        raise ValueError("OpenClaw runtime target manifest does not match selected root")
    if str(paths["managed_skills_realpath"]) != str(manifest["managed_skills_realpath"]):
        raise ValueError("OpenClaw runtime target manifest managed skills root does not match selected root")
    rroot = Path(runtime_root).expanduser().resolve(strict=False)
    if str(rroot) != str(manifest["runtime_realpath"]):
        raise ValueError("OpenClaw runtime target manifest runtime root does not match selected runtime root")
    root_reason = neutral_runtime_root_block_reason(rroot)
    if root_reason is not None:
        raise ValueError(f"OpenClaw runtime target selected runtime root is not neutral: {root_reason}")
    return expanded_root, rroot


def build_runtime_probe_evidence(
    *,
    root: Path,
    skill: str,
    runtime_root: Path,
    platform: str = "linux",
    path_style: str = "posix",
    live: bool = True,
    openclaw_bin: str = "openclaw",
) -> dict[str, Any]:
    """Gather v3 evidence for a runtime skill on this host.

    Offline-derivable records (neutral-runtime-root, runtime/support-pre-state,
    compatibility-tuple-match, helper-invocation derived from the runner contract)
    are always emitted. native-loader + quiescence-lock require the LIVE openclaw
    binary on a quiescent host; with ``live`` they are attempted and any failure is
    recorded as a limitation (fail-open on the probe, fail-closed on authorization)."""
    paths = validate_openclaw_target_home(root)
    rroot = Path(runtime_root).expanduser().resolve(strict=False)
    rp = dict(
        target_realpath=paths["home_realpath"],
        managed_skills_realpath=paths["managed_skills_realpath"],
        runtime_realpath=str(rroot),
    )
    manifests = load_manifests()
    if skill not in manifests["runtime"]["skills"]:
        raise ValueError(f"{skill!r} is not a runtime-backed skill")
    files = gather_runtime_files(skill, manifests)
    file_hashes = sorted((f["relative_path"], f["source_sha256"] or "") for f in files)
    has_exec = any(
        f["relative_path"].endswith((".py", ".sh", ".bat", ".ps1")) or str(f.get("mode")) == "0755" for f in files
    )
    evidence: list[dict[str, Any]] = []
    limitations: list[str] = []

    def _ev(etype: str, behavior: str, checks: dict[str, Any]) -> None:
        evidence.append(
            build_runtime_target_evidence(
                evidence_type=etype, platform=platform, path_style=path_style,
                observed_behavior=behavior, checks=checks, **rp))

    root_reason = neutral_runtime_root_block_reason(rroot)
    if root_reason is None:
        _ev("neutral-runtime-root", "validated neutral runtime root", {"runtime_root_realpath": str(rroot), "validator": "passed"})
    else:
        limitations.append(f"neutral-runtime-root rejected: {root_reason}")
    _ev("runtime-pre-state", "hashed runtime source files", {"files": file_hashes})
    _ev("support-file-pre-state", "hashed support source files", {"files": file_hashes})
    _ev("compatibility-tuple-match", "host compatibility tuple", {"platform": platform, "path_style": path_style})
    # helper-invocation derived from the runner contract (static host inspection).
    _ev("helper-invocation", "derived from runner contract inspection (static, not execution-recorded)", {
        "argv_template": "run_skill.sh <command_rel> -- <args>",
        "shell_family": "powershell" if platform == "windows" else "bash",
        "exec_mode": "exec-list-no-shell",
        "env_allowlist": sorted(set(ENV_ALLOW_EXACT)) + [p + "*" for p in ENV_ALLOW_PREFIX],
        "line_ending_policy": "crlf" if platform == "windows" else "lf",
        "has_executable": has_exec,
    })
    if has_exec:
        limitations.append("helper-invocation is static-derived; execution-recorded evidence needs an on-host run")

    if live:
        from .openclaw_target_apply import quiescence_checks, run_text

        try:
            version = run_text([openclaw_bin, "--version"], cwd=root.expanduser())
            _ev("native-loader", "openclaw native loader available", {"openclaw_version": version})
        except Exception as exc:  # noqa: BLE001 - probe degrades to a limitation
            limitations.append(f"native-loader probe needs a live openclaw binary: {exc}")
        try:
            quiescence = quiescence_checks(root.expanduser(), openclaw_bin=openclaw_bin)
            if quiescence.get("quiescent"):
                _ev("quiescence-lock", "openclaw target quiescent", {"quiescent": True})
            else:
                limitations.append("not quiescent: an active OpenClaw process or lock is present")
        except Exception as exc:  # noqa: BLE001
            limitations.append(f"quiescence probe failed: {exc}")
    else:
        limitations.append("native-loader + quiescence-lock skipped (--no-live)")

    types = {e["evidence_type"] for e in evidence}
    complete = {"native-loader", "quiescence-lock"} <= types
    return {
        "status": "ok" if complete else "incomplete",
        "skill": skill,
        "runtime_root": str(rroot),
        "evidence": evidence,
        "limitations": limitations,
    }


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
    root, rroot = validate_runtime_target_apply_paths(manifest, root=root, runtime_root=runtime_root)
    if not dry_run:
        if not real_system:
            raise ValueError("OpenClaw runtime real writes require --real-system")
        if confirm_phrase != OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE:
            raise ValueError(f"apply aborted: confirmation phrase must be exactly: {OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE}")

    skill = manifest["skill"]
    manifests = load_manifests()
    source_by_target = {f["target"]: f for f in manifests["runtime"]["skills"][skill]["files"]}
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
