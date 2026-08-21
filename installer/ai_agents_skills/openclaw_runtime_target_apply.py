"""OpenClaw runtime/support target orchestration (P8 CLI surface).

Gathers a skill's runtime files + neutral render + evidence into a content-addressed
runtime manifest (dry-run), and builds a broker state from approved manifests. The
actual support-file writes to .openclaw and the live broker bind/exec are host-gated
(they require a real OpenClaw home + the running broker); this module produces the
decisions/plans those steps consume.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from .capabilities import normalized_path_within, resolved_path_within
from .manifest import load_manifests
from .openclaw_runtime_broker import ENV_ALLOW_EXACT, ENV_ALLOW_PREFIX, AgentToken, BrokerState
from .openclaw_runtime_target_evidence import build_runtime_target_evidence, now_utc
from .openclaw_runtime_target_paths import neutral_runtime_root_block_reason
from .openclaw_runtime_target_manifest import (
    build_openclaw_runtime_target_manifest,
    classify_runtime_files,
    load_runtime_target_manifest,
    validate_runtime_target_manifest,
)
from .openclaw_target_paths import (
    OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE,
    openclaw_home,
    validate_openclaw_target_home,
)
from .render import render_skill_md
from .state import existing_contained_parents
from .runtime import (
    RUNTIME_SOURCE_ROOT,
    replace_with_runtime_file,
    runtime_expected_sha256,
    runtime_source_content_hash,
)
from .windows_security import require_handle_bound_mutation


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


WORKSPACE_SKILL_PREFIX = ("workspace", "skills")


def runtime_target_relative_path(skill: str, relative_path: str) -> PurePosixPath:
    """The path a runtime file keeps *below* ``.openclaw/skills/<skill>/``.

    Runtime targets are addressed from the workspace root
    (``workspace/skills/<skill>/assets/...``). S3 delivers the part below the
    skill directory, so two shipped files that differ only in their directory
    stay two files instead of collapsing onto one basename.
    """
    parts = PurePosixPath(relative_path).parts
    if parts[:2] == WORKSPACE_SKILL_PREFIX and parts[2:3] == (skill,):
        parts = parts[3:]
    if not parts:
        raise ValueError(f"runtime manifest file has no path below its skill: {relative_path}")
    return PurePosixPath(*parts)


def runtime_target_destinations(
    manifest: dict[str, Any], *, root: Path, runtime_root: Path
) -> dict[str, Path]:
    """Map each routed manifest file to the single path it is delivered to.

    Fail-closed on a collision: two records that resolve to one destination would
    make apply write both and keep whichever ran last, reporting both applied.
    """
    skill = manifest["skill"]
    destinations: dict[str, Path] = {}
    claimed: dict[Path, str] = {}
    for record in manifest["files"]:
        rel = record["relative_path"]
        route = manifest["routing"].get(rel, "skip")
        if route == "skip":
            continue
        if route == "s3":
            dest = openclaw_home(root) / "skills" / skill / Path(runtime_target_relative_path(skill, rel))
        else:  # s4 -> neutral runtime root, which already keeps the full path
            dest = runtime_root / rel
        owner = claimed.get(dest)
        if owner is not None:
            raise ValueError(
                f"runtime manifest files collide on one destination: {owner} and {rel} -> {dest}"
            )
        claimed[dest] = rel
        destinations[rel] = dest
    return destinations


def runtime_destination_safety_reason(dest: Path, contain_root: Path) -> str | None:
    """Why ``dest`` must not be written, or None.

    The v2 skill-file path treats a symlinked or escaping destination as an
    attack (openclaw_target_apply.target_path_safety_reason); a runtime apply
    writes into the same home and gets the same treatment. Without this a
    symlink planted at ``.openclaw/skills/<skill>`` redirects every delivered
    file outside ``.openclaw`` entirely.
    """
    if not normalized_path_within(contain_root, dest):
        return f"destination escapes {contain_root}"
    if dest.is_symlink():
        return "destination is a symlink"
    if not resolved_path_within(contain_root, dest.parent):
        return f"destination resolves outside {contain_root}"
    for parent in existing_contained_parents(dest.parent, contain_root):
        if parent.is_symlink():
            return f"destination has a symlinked parent: {parent}"
        if not parent.is_dir():
            return f"destination has a non-directory parent: {parent}"
    return None


def runtime_destination_signature(dest: Path) -> str:
    """What is at a destination *right now* -- the observation a pre-state needs."""
    if dest.is_symlink():
        return "symlink"
    if not dest.exists():
        return "absent"
    if not dest.is_file():
        return "not-a-regular-file"
    try:
        return "sha256:" + hashlib.sha256(dest.read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def runtime_manifest_platform(manifest: dict[str, Any]) -> str:
    """The host platform the manifest's evidence was captured on."""
    evidence = manifest.get("target_evidence") or []
    for item in evidence:
        if item.get("evidence_type") == "compatibility-tuple-match":
            value = (item.get("checks") or {}).get("platform")
            if value:
                return str(value)
    for item in evidence:
        if item.get("platform"):
            return str(item["platform"])
    return "linux"


_PLATFORM_SHELLS = {"windows": {"powershell", "cmd"}}


def runtime_broker_commands(manifest: dict[str, Any], *, platform: str) -> dict[str, dict[str, Any]]:
    """Map broker command names to the S4 record each one runs.

    A command name is the file stem, so a skill shipping ``run_sage.sh`` and
    ``run_sage.ps1`` -- the same command for two shells -- produces one name for
    two files. Pick the variant this platform can actually execute rather than
    letting one silently replace the other, and fail closed if the tie is not a
    shell-family tie.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in manifest["files"]:
        rel = record["relative_path"]
        if manifest["routing"].get(rel) != "s4":
            continue  # only executable (broker-delivered) files become runnable commands
        grouped.setdefault(PurePosixPath(rel).stem, []).append(record)

    native = _PLATFORM_SHELLS.get(platform, {"posix-sh", "bash"})
    commands: dict[str, dict[str, Any]] = {}
    for command, records in grouped.items():
        if len(records) > 1:
            shell_bound = [r for r in records if native & set(r.get("shell_families") or ())]
            portable = [r for r in records if set(r.get("shell_families") or ()) <= {"none"}]
            records = shell_bound or portable
        if len(records) != 1:
            paths = ", ".join(sorted(r["relative_path"] for r in records))
            raise ValueError(f"runtime manifest commands collide on {command!r} for {platform}: {paths}")
        commands[command] = records[0]
    return commands


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
    skill = manifest["skill"]
    commands: dict[tuple[str, str], dict[str, str]] = {
        (skill, command): {
            "target_rel": record["relative_path"],
            "expected_sha256": record.get("source_sha256") or "",
        }
        for command, record in runtime_broker_commands(
            manifest, platform=runtime_manifest_platform(manifest)
        ).items()
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
    records, routing = classify_runtime_files(files)
    # A pre-state hashed from the shipped SOURCE is the same value on every host,
    # so a gate keyed on it can never observe drift and can never fail. What the
    # pre-state has to record is what is at each destination right now.
    destinations = runtime_target_destinations(
        {"skill": skill, "files": records, "routing": routing}, root=root, runtime_root=rroot
    )
    target_state = sorted(
        (rel, runtime_destination_signature(dest)) for rel, dest in destinations.items()
    )
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
    _ev("runtime-pre-state", "observed runtime destinations", {"files": target_state})
    _ev("support-file-pre-state", "observed support destinations", {"files": target_state})
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
        # Same attest-then-run contract the v2 probe uses (openclaw_target_apply:700-702):
        # the binary is pinned before it is invoked, and the descriptor identity is
        # re-checked inside run_openclaw_text.
        from .openclaw_target_apply import attest_openclaw_executable, quiescence_checks, run_openclaw_text

        try:
            executable = attest_openclaw_executable(openclaw_bin)
            version = run_openclaw_text(root.expanduser(), executable, ["--version"])
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
    if not dry_run:
        require_handle_bound_mutation("OpenClaw runtime-target apply")
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
    destinations = runtime_target_destinations(manifest, root=root, runtime_root=rroot)
    command_by_path = {
        record["relative_path"]: command
        for command, record in runtime_broker_commands(
            manifest, platform=runtime_manifest_platform(manifest)
        ).items()
    }
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
        dest = destinations[rel]
        contain_root = openclaw_home(root) / "skills" / skill if route == "s3" else rroot
        reason = runtime_destination_safety_reason(dest, contain_root)
        if reason is not None:
            raise ValueError(f"refusing to write runtime file {rel}: {reason}")
        # This module keeps no state and takes no backup, so an overwrite here is
        # unrecoverable. Anything at the destination that is not already this
        # approved content is somebody else's file: refuse instead of destroying it.
        present = runtime_destination_signature(dest)
        if present not in ("absent", record.get("source_sha256")):
            raise ValueError(
                f"refusing to overwrite unmanaged content at {dest} ({present}): "
                "move it aside or uninstall it first"
            )
        if command_by_path.get(rel):
            broker_commands.append({"command": command_by_path[rel], "target_rel": rel,
                                    "expected_sha256": record["source_sha256"]})
        entry_action = {"relative_path": rel, "route": route, "dest": str(dest),
                        "operation": "create" if present == "absent" else "replace-managed"}
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
