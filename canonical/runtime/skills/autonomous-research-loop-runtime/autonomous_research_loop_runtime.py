#!/usr/bin/env python3
"""Offline ledger helper for autonomous research loops."""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import math
import os
import re
import signal
import stat
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple

try:
    from arl_credential_client import (  # type: ignore
        BrokerError as CredentialBrokerError,
        broker_active as credential_broker_active,
        request as credential_broker_request,
    )
except ImportError:  # pragma: no cover - package-style import during tests
    from .arl_credential_client import (  # type: ignore
        BrokerError as CredentialBrokerError,
        broker_active as credential_broker_active,
        request as credential_broker_request,
    )

# Sibling modules: hybrid panel + optional goal_priority.v1.
try:
    from panel_parent import (  # type: ignore  # noqa: I001 — same-dir runtime import
        PanelIsolationError,
        ProviderResourceError,
        ProviderResourceCleanupError,
        brokered_provider_containment_command,
        CONTAINMENT_TAIL_MOUNT_MASKS,
        STRICT_ISOLATED_TRANSPORT,
        TRUSTED_LOCAL_TRANSPORT,
        assert_panel_prompt_safe,
        attest_provider_executable,
        cleanup_resource_scope,
        containment_hidden_root,
        containment_mask_roots_for_host,
        interpreter_bound_provider_command,
        ensure_iter_dir,
        load_panel_config,
        panel_prompt_addon,
        panel_payload_sensitive_findings,
        panel_prompt_secret_findings,
        preflight_resource_backend,
        prepare_provider_sandbox_mounts,
        provider_resource_limits,
        provider_transport_mode,
        public_resource_limits,
        provider_sandbox_resolver_mounts,
        provider_family,
        revalidate_provider_executable_attestation,
        resolve_panel_mode,
        resource_control_environment,
        resource_limited_command,
        run_bounded_resource_process,
        trusted_local_containment_command,
        run_panel_phase_for_drive,
        cleanup_provider_sandbox_vault,
        smoke as panel_smoke,
    )
    from goal_priority import (  # type: ignore
        apply_hard_path_discipline,
        collect_goal_priority_warnings,
        example_goal_priority_json,
        goal_priority_prompt_addon,
        is_goal_priority_active,
        load_goal_priority,
    )
    from compute_policy import (  # type: ignore
        compute_policy_addon,
        normalize_compute_job_ref,
    )
    import goal_focus as goal_focus_v2  # type: ignore
    import notify_v2  # type: ignore
    from state_transaction import (  # type: ignore
        LoopLock,
        TransactionQuarantined,
        iteration_ledger_paths,
    )
    from formal_policy import (  # type: ignore
        evaluate_formal_terminal_state,
        export_formal_env,
        formal_force_tick,
        formal_policy_prompt_addon,
        formal_track_status,
        is_force_tick_enabled,
        is_formal_track,
        load_formal_policy,
        load_formal_terminal_state,
        merge_standing_orders_formal,
        pin_privileged_policy,
        resolve_formal_project,
        reverify_formal_evidence,
        write_host_pin,
        write_track_pin,
    )
except ImportError:  # pragma: no cover - package-style import during tests
    from .panel_parent import (  # type: ignore
        PanelIsolationError,
        ProviderResourceError,
        ProviderResourceCleanupError,
        brokered_provider_containment_command,
        CONTAINMENT_TAIL_MOUNT_MASKS,
        STRICT_ISOLATED_TRANSPORT,
        TRUSTED_LOCAL_TRANSPORT,
        assert_panel_prompt_safe,
        attest_provider_executable,
        cleanup_resource_scope,
        containment_hidden_root,
        containment_mask_roots_for_host,
        interpreter_bound_provider_command,
        ensure_iter_dir,
        load_panel_config,
        panel_prompt_addon,
        panel_payload_sensitive_findings,
        panel_prompt_secret_findings,
        preflight_resource_backend,
        prepare_provider_sandbox_mounts,
        provider_resource_limits,
        provider_transport_mode,
        public_resource_limits,
        provider_sandbox_resolver_mounts,
        provider_family,
        revalidate_provider_executable_attestation,
        resolve_panel_mode,
        resource_control_environment,
        resource_limited_command,
        run_bounded_resource_process,
        trusted_local_containment_command,
        run_panel_phase_for_drive,
        cleanup_provider_sandbox_vault,
        smoke as panel_smoke,
    )
    from .goal_priority import (  # type: ignore
        apply_hard_path_discipline,
        collect_goal_priority_warnings,
        example_goal_priority_json,
        goal_priority_prompt_addon,
        is_goal_priority_active,
        load_goal_priority,
    )
    from .compute_policy import (  # type: ignore
        compute_policy_addon,
        normalize_compute_job_ref,
    )
    from . import goal_focus as goal_focus_v2  # type: ignore
    from . import notify_v2  # type: ignore
    from .state_transaction import (  # type: ignore
        LoopLock,
        TransactionQuarantined,
        iteration_ledger_paths,
    )
    from .formal_policy import (  # type: ignore
        evaluate_formal_terminal_state,
        export_formal_env,
        formal_force_tick,
        formal_policy_prompt_addon,
        formal_track_status,
        is_force_tick_enabled,
        is_formal_track,
        load_formal_policy,
        load_formal_terminal_state,
        merge_standing_orders_formal,
        pin_privileged_policy,
        resolve_formal_project,
        reverify_formal_evidence,
        write_host_pin,
        write_track_pin,
    )


SCHEMA_VERSION = "1.0"
DEFAULT_PLATEAU_RULE = "stop after three consecutive iterations with no new evidence or reduced uncertainty"
VALID_DECISIONS = {"continue", "revise", "delegate", "stop", "blocked"}
TERMINAL_DECISIONS = {"stop", "blocked"}
TERMINAL_STATUSES = {"stopped", "blocked"}
SUCCESS_STOP_REASONS = {"success", "success_criteria_met", "proof", "proof_found", "found_proof", "proved"}
HOST_REVIEWED_GOAL_SUCCESS_REASON = "goal_obligations_satisfied_after_review"
PROOF_ARTIFACT_DIRNAME = "proof_artifacts"
PROOF_ARTIFACT_TYPES = {"lean", "coq", "isabelle", "agda", "sagemath", "python-verifier", "external-verifier"}
SAFE_EVIDENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
VALID_MODES = {
    "monitor",
    "bounded-research",
    "implementation-support",
    "panel-loop",
    "recovery",
}
GOAL_FOCUS_MODES = {"off", "monitor", "enforce"}
COMPUTE_SERVICE_ALIASES = {
    "gha": "github-actions",
    "github_actions": "github-actions",
    "github-actions": "github-actions",
    "hetzner": "hetzner",
    "kaggle": "kaggle",
    "local": "local",
    "modal": "modal",
}
COMPUTE_RUN_STATUSES = {"succeeded", "failed", "cancelled", "unknown"}
# Provenance-metadata synonyms accepted on --compute-run records. Aliasing is
# allowed here (unlike stop_reason, which gates the early-stop policy).
COMPUTE_RUN_STATUS_ALIASES = {"completed": "succeeded"}
COMPUTE_RUN_KNOWN_KEYS = (
    "service",
    "status",
    "job_ref",
    "detail",
    "started_at",
    "finished_at",
    "duration_seconds",
)
ITERATION_SUBMISSION_SCHEMA = "iteration_submission.v1"
ITERATION_SUBMISSION_FILENAME = ".iteration_submission.json"
ITERATION_SUBMISSION_MAX_BYTES = 1_000_000
ITERATION_SUBMISSION_ARG_FIELDS = (
    "mode",
    "objective",
    "decision",
    "input_ref",
    "source_id",
    "claim_id",
    "evidence_id",
    "guard_ref",
    "action_taken",
    "output",
    "remaining_gap",
    "tokens",
    "usd",
    "wall_time_seconds",
    "stop_reason",
    "goal_contribution",
    "campaign_id",
    "local_without_goal_delta",
    "local_without_goal_delta_tag",
    "residual_id",
    "scope_lock",
    "goal_contribution_detail",
    "completed_summary",
    "current_summary",
    "next_action",
    "campaign_delta",
    "global_delta",
    "obligation_id",
    "compute_run",
    "compute_none",
    "executor_provider",
)
ITERATION_SUBMISSION_LIST_FIELDS = frozenset(
    {
        "input_ref",
        "source_id",
        "claim_id",
        "evidence_id",
        "guard_ref",
        "action_taken",
        "remaining_gap",
        "obligation_id",
        "compute_run",
    }
)
ITERATION_SUBMISSION_BOOL_FIELDS = frozenset(
    {"local_without_goal_delta", "compute_none"}
)
ITERATION_SUBMISSION_INT_FIELDS = frozenset(
    {"tokens", "wall_time_seconds"}
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _open_directory_nofollow(path: Path) -> int:
    """Open every POSIX directory component without following symlinks."""

    absolute = Path(os.path.abspath(path))
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    anchor = absolute.anchor or os.sep
    fd = os.open(anchor, flags)
    try:
        for component in absolute.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


def _read_regular_text(
    path: Path, *, errors: str = "strict", max_bytes: int = 16_000_000
) -> str:
    """Read one bounded regular runtime file without following a leaf link."""

    absolute = Path(os.path.abspath(path))
    if os.name == "nt":
        info = os.lstat(absolute)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise OSError(f"runtime input is not a regular file: {absolute}")
        payload = absolute.read_bytes()
    else:
        dir_fd = _open_directory_nofollow(absolute.parent)
        try:
            fd = os.open(
                absolute.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
                dir_fd=dir_fd,
            )
        finally:
            os.close(dir_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
                raise OSError(f"runtime input is unsafe or oversized: {absolute}")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                payload = handle.read(max_bytes + 1)
        finally:
            os.close(fd)
    if len(payload) > max_bytes:
        raise OSError(f"runtime input exceeds {max_bytes} bytes: {absolute}")
    return payload.decode("utf-8", errors=errors)


def _read_contained_regular_text(
    root: Path,
    path: Path,
    *,
    errors: str = "strict",
    max_bytes: int = 16_000_000,
) -> str:
    """Read a bounded regular file lexically contained beneath ``root``.

    On POSIX, each directory and the leaf are opened relative to no-follow
    descriptors so a planted symlink cannot redirect the read outside the
    allowed panel input boundary.
    """

    base = Path(os.path.abspath(root))
    supplied = Path(path).expanduser()
    candidate = Path(
        os.path.abspath(supplied if supplied.is_absolute() else base / supplied)
    )
    try:
        relative = candidate.relative_to(base)
    except ValueError as exc:
        raise OSError(
            f"runtime input escapes the allowed root: {candidate} (root {base})"
        ) from exc
    if not relative.parts:
        raise OSError(f"runtime input must name a file beneath the allowed root: {candidate}")
    if os.name == "nt":
        for component in [*reversed(candidate.parent.parents), candidate.parent]:
            info = os.lstat(component)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise OSError(f"runtime input directory is unsafe: {component}")
        return _read_regular_text(candidate, errors=errors, max_bytes=max_bytes)

    directory_fd = _open_directory_nofollow(base)
    try:
        for component in relative.parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(
            relative.parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
            dir_fd=directory_fd,
        )
    finally:
        os.close(directory_fd)
    try:
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            raise OSError(f"runtime input is unsafe or oversized: {candidate}")
        with os.fdopen(file_fd, "rb", closefd=False) as handle:
            payload = handle.read(max_bytes + 1)
    finally:
        os.close(file_fd)
    if len(payload) > max_bytes:
        raise OSError(f"runtime input exceeds {max_bytes} bytes: {candidate}")
    return payload.decode("utf-8", errors=errors)


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(_read_regular_text(path))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    # Write atomically (temp file + os.replace) so a crash mid-write cannot
    # truncate the destination and lose loop state.
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _atomic_write_runtime_text(
    path: Path,
    text: str,
    *,
    mode: int = 0o600,
) -> None:
    """Atomically replace one host-owned runtime leaf without following links."""

    absolute = Path(os.path.abspath(path))
    directory = _ensure_real_directory(absolute.parent)
    name = absolute.name
    if not name or name in {".", ".."} or Path(name).name != name:
        raise OSError(f"invalid runtime output name: {name!r}")
    payload = text.encode("utf-8")

    def _validate_existing(info: os.stat_result) -> None:
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise OSError(f"runtime output is not a regular file: {absolute}")
        if getattr(info, "st_nlink", 1) != 1:
            raise OSError(f"runtime output has multiple hard links: {absolute}")
        if os.name == "posix" and info.st_uid != os.geteuid():
            raise OSError(f"runtime output is not current-user owned: {absolute}")

    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        try:
            _validate_existing(os.lstat(absolute))
        except FileNotFoundError:
            pass
        temp_path = directory / f".runtime-write-{uuid.uuid4().hex}-{name}"
        fd = os.open(
            temp_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            mode,
        )
        try:
            remaining = memoryview(payload)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("could not write runtime output")
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            try:
                _validate_existing(os.lstat(absolute))
            except FileNotFoundError:
                pass
            os.replace(temp_path, absolute)
        finally:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
        return

    directory_fd = _open_directory_nofollow(directory)
    temp_name = f".runtime-write-{uuid.uuid4().hex}-{name}"
    fd = -1
    try:
        directory_info = os.fstat(directory_fd)
        if (
            directory_info.st_uid != os.geteuid()
            or directory_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise OSError("runtime output directory is not private and host-owned")
        try:
            _validate_existing(
                os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            )
        except FileNotFoundError:
            pass
        fd = os.open(
            temp_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=directory_fd,
        )
        os.fchmod(fd, mode)
        remaining = memoryview(payload)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise OSError("could not write runtime output")
            remaining = remaining[written:]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        try:
            _validate_existing(
                os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            )
        except FileNotFoundError:
            pass
        os.replace(
            temp_name,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temp_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def safe_registry_run_id(value: object) -> str:
    """Validate the sole filename component used by active.d registry rows."""

    run_id = str(value or "")
    if (
        not run_id
        or len(run_id) > 128
        or run_id in {".", ".."}
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id) is None
    ):
        raise ValueError("run_id must be one bounded safe registry filename component")
    return run_id


def _write_registry_json_snapshot(
    reg: Path,
    run_id: object,
    data: dict[str, Any],
    *,
    expected: RegistryEntrySnapshot | None = None,
) -> RegistryEntrySnapshot:
    """Install one row with no-overwrite linking and exact replacement CAS."""

    safe_id = safe_registry_run_id(run_id)
    directory = _ensure_real_directory(reg)
    name = f"{safe_id}.json"
    path = directory / name
    payload = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")

    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        if expected is None:
            try:
                expected = _read_registry_snapshot_at(directory, name)
            except FileNotFoundError:
                pass
        elif expected.path.name != name:
            raise RegistrySafetyError("registry replacement snapshot has the wrong name")
        temp_path = directory / f".registry-write-{uuid.uuid4().hex}-{name}"
        quarantine_path = directory / f".registry-replaced-{uuid.uuid4().hex}-{name}"
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(fd)
        moved: RegistryEntrySnapshot | None = None
        try:
            if expected is not None:
                current = _read_registry_snapshot_at(directory, name)
                if not _same_registry_snapshot(current, expected):
                    raise RegistrySafetyError(
                        f"registry entry changed before replacement: {path}"
                    )
                os.replace(path, quarantine_path)
                moved = _read_registry_snapshot_at(directory, quarantine_path.name)
                if not _same_registry_snapshot(
                    moved, expected._replace(path=quarantine_path)
                ):
                    raise RegistrySafetyError(
                        f"registry entry changed during replacement: {quarantine_path}"
                    )
            try:
                os.link(temp_path, path)
            except FileExistsError as exc:
                raise RegistrySafetyError(
                    f"registry destination appeared during replacement: {path}"
                ) from exc
            os.unlink(temp_path)
            installed = _read_registry_snapshot_at(directory, name)
            if installed.payload != payload:
                raise RegistrySafetyError(
                    f"registry entry changed while being installed: {path}"
                )
            if moved is not None:
                delete_registry_snapshot(moved)
            return installed
        finally:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass

    directory_fd = _open_trusted_registry_directory(directory)
    _lock_registry_descriptor(directory_fd, exclusive=True)
    temp_name = f".registry-write-{uuid.uuid4().hex}-{name}"
    quarantine_name = f".registry-replaced-{uuid.uuid4().hex}-{name}"
    temp_fd = -1
    moved: RegistryEntrySnapshot | None = None
    try:
        if expected is None:
            try:
                expected = _read_registry_snapshot_at(
                    directory, name, directory_fd=directory_fd
                )
            except FileNotFoundError:
                pass
        elif expected.path.name != name:
            raise RegistrySafetyError("registry replacement snapshot has the wrong name")

        temp_fd = os.open(
            temp_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        with os.fdopen(temp_fd, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(temp_fd)
        temp_fd = -1

        if expected is not None:
            current = _read_registry_snapshot_at(
                directory, name, directory_fd=directory_fd
            )
            if not _same_registry_snapshot(current, expected):
                raise RegistrySafetyError(
                    f"registry entry changed before replacement: {path}"
                )
            os.rename(
                name,
                quarantine_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            moved = _read_registry_snapshot_at(
                directory, quarantine_name, directory_fd=directory_fd
            )
            if not _same_registry_snapshot(
                moved, expected._replace(path=directory / quarantine_name)
            ):
                raise RegistrySafetyError(
                    f"registry entry changed during replacement: {directory / quarantine_name}"
                )

        try:
            os.link(
                temp_name,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise RegistrySafetyError(
                f"registry destination appeared during replacement: {path}"
            ) from exc
        os.unlink(temp_name, dir_fd=directory_fd)
        installed = _read_registry_snapshot_at(
            directory, name, directory_fd=directory_fd
        )
        if installed.payload != payload:
            raise RegistrySafetyError(
                f"registry entry changed while being installed: {path}"
            )
        os.fsync(directory_fd)
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        try:
            os.unlink(temp_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)
    if moved is not None:
        delete_registry_snapshot(moved)
    return installed


def write_registry_json(reg: Path, run_id: object, data: dict[str, Any]) -> Path:
    """Atomically replace exactly one observed registry row, or create it."""

    return _write_registry_json_snapshot(reg, run_id, data).path


def _ensure_real_directory(path: Path) -> Path:
    """Create/open a directory chain component-wise without following links."""

    absolute = Path(os.path.abspath(path))
    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        for component in [*reversed(absolute.parents), absolute]:
            try:
                info = os.lstat(component)
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700)
                except FileExistsError:
                    info = os.lstat(component)
                else:
                    continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise OSError(f"driver directory is not a real directory: {component}")
        return absolute

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(absolute.anchor or os.sep, flags)
    try:
        for component in absolute.parts[1:]:
            try:
                next_descriptor = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
    finally:
        os.close(descriptor)
    return absolute


def _open_exclusive_driver_log(path: Path):  # noqa: ANN202 - TextIO inferred
    """Create a new private streaming log without following a planted link."""

    directory = _ensure_real_directory(path.parent)
    name = path.name
    if not name or name in {".", ".."} or Path(name).name != name:
        raise OSError(f"invalid driver log name: {name!r}")
    if os.name == "nt":
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    else:
        dir_fd = _open_directory_nofollow(directory)
        try:
            fd = os.open(
                name,
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=dir_fd,
            )
        finally:
            os.close(dir_fd)
    return os.fdopen(fd, "w+", encoding="utf-8", errors="replace", newline="\n")


def read_iterations(path: Path) -> list[dict[str, Any]]:
    """Read the whole iteration ledger the given live file belongs to.

    Callers pass ``paths["iterations"]``; the rotated shards beside it are part
    of the same ledger, so validation and iteration numbering span them all.
    """

    records: list[dict[str, Any]] = []
    for shard in iteration_ledger_paths(path.parent):
        if not shard.exists():
            continue
        for index, raw_line in enumerate(_read_regular_text(shard).splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{shard.name} line {index} is invalid JSON: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{shard.name} line {index} must contain a JSON object")
            records.append(record)
    return records


def append_jsonl(path: Path, data: dict[str, Any]) -> None:
    """Append one line through pinned parents and a private no-follow leaf."""

    directory = _ensure_real_directory(path.parent)
    name = path.name
    if not name or name in {".", ".."} or Path(name).name != name:
        raise OSError(f"invalid JSONL destination name: {name!r}")
    payload = (json.dumps(data, sort_keys=True) + "\n").encode("utf-8")
    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
        file_fd = os.open(directory / name, flags, 0o600)
        try:
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("JSONL destination is not a single-link regular file")
            remaining = memoryview(payload)
            while remaining:
                written = os.write(file_fd, remaining)
                if written <= 0:
                    raise OSError("could not append JSONL record")
                remaining = remaining[written:]
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
        return

    directory_fd = _open_directory_nofollow(directory)
    try:
        directory_info = os.fstat(directory_fd)
        if (
            directory_info.st_uid != os.geteuid()
            or directory_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise OSError("JSONL parent directory is not host-controlled")
        file_fd = os.open(
            name,
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        try:
            import fcntl

            fcntl.flock(file_fd, fcntl.LOCK_EX)
            info = os.fstat(file_fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise OSError("JSONL destination is not a private host-owned file")
            remaining = memoryview(payload)
            while remaining:
                written = os.write(file_fd, remaining)
                if written <= 0:
                    raise OSError("could not append JSONL record")
                remaining = remaining[written:]
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def parse_many(values: list[str] | None) -> list[str]:
    return [value for value in values or [] if value]


def parse_json_or_file(value: str) -> Any:
    """Parse JSON text or ``@path`` without guessing from arbitrary prose."""
    raw = str(value or "").strip()
    if raw.startswith("@"):
        path = Path(raw[1:]).expanduser()
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(raw)


def parse_compute_runs(values: list[str] | None, *, explicit_none: bool = False) -> dict[str, Any]:
    if explicit_none and values:
        raise ValueError("--compute-none is mutually exclusive with --compute-run")
    if explicit_none:
        return {"recording_status": "explicit", "usage": "none", "services": []}
    if not values:
        return {"recording_status": "unreported", "usage": "unknown", "services": []}
    services: list[dict[str, Any]] = []
    for raw in values:
        item = parse_json_or_file(raw)
        if not isinstance(item, dict):
            raise ValueError("each --compute-run value must be a JSON object")
        raw_service = item.get("service")
        if raw_service in (None, "") and item.get("backend") not in (None, ""):
            # Provenance metadata: accept the common 'backend' synonym.
            raw_service = item.get("backend")
        token = str(raw_service or "").strip().lower()
        service = COMPUTE_SERVICE_ALIASES.get(token, token)
        if service.startswith("other:"):
            slug = service.split(":", 1)[1]
            if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", slug) is None:
                raise ValueError(f"invalid custom compute service {service!r}")
        elif service not in set(COMPUTE_SERVICE_ALIASES.values()):
            raise ValueError(
                "compute run 'service' must be local, hetzner, kaggle, modal, "
                f"github-actions, or other:<safe-slug> (got {token!r}); "
                f"recognized --compute-run keys: {', '.join(COMPUTE_RUN_KNOWN_KEYS)}"
            )
        status = str(item.get("status") or "unknown").strip().lower()
        status = COMPUTE_RUN_STATUS_ALIASES.get(status, status)
        if status not in COMPUTE_RUN_STATUSES:
            raise ValueError(
                f"compute run status must be one of {sorted(COMPUTE_RUN_STATUSES)} "
                f"(got {status!r})"
            )
        normalized: dict[str, Any] = {"service": service, "status": status}
        residual_job_detail: str | None = None
        for key in ("job_ref", "detail", "started_at", "finished_at", "duration_seconds"):
            value = item.get(key)
            if value not in (None, ""):
                if key == "job_ref":
                    safe_ref, residual = normalize_compute_job_ref(value)
                    if safe_ref is None:
                        continue
                    normalized["job_ref"] = safe_ref
                    residual_job_detail = residual
                else:
                    normalized[key] = value
        if residual_job_detail:
            existing_detail = str(normalized.get("detail") or "").strip()
            if residual_job_detail not in existing_detail:
                if existing_detail:
                    merged = f"{existing_detail}; cmd={residual_job_detail}"
                else:
                    merged = residual_job_detail
                normalized["detail"] = merged[:500]
        services.append(normalized)
    return {
        "recording_status": "explicit",
        "usage": "mixed" if len({s["service"] for s in services}) > 1 else services[0]["service"],
        "services": services,
    }


def normalized_stop_reason(reason: object) -> str:
    return str(reason or "").strip().lower().replace("-", "_").replace(" ", "_")


def is_success_stop_reason(reason: object) -> bool:
    return normalized_stop_reason(reason) in SUCCESS_STOP_REASONS


class GuardError(ValueError):
    """Guard failure carrying structured hints for the CLI failure payload.

    ``str(exc)`` stays the stable ``error`` string; ``payload`` keys are merged
    into the JSON failure result under their own names so scripted callers that
    parse ``error`` are unaffected.
    """

    def __init__(self, message: str, **payload: Any) -> None:
        super().__init__(message)
        self.payload: dict[str, Any] = payload


def proof_artifact_example() -> dict[str, Any]:
    """The exact record shape validate_proof_artifact accepts (see selftest)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "id": "<evidence-id>",
        "artifact_type": "lean",
        "machine_checkable": True,
        "target": "<theorem or claim this artifact proves>",
        "proof_path": "<path relative to the loop dir, e.g. proof_artifacts/final.lean>",
        "checker": {"name": "lake", "status": "passed"},
    }


def early_stop_contract() -> dict[str, Any]:
    """Structured hints attached to early-stop guard failures (one round-trip)."""
    return {
        "accepted_stop_reasons": sorted(SUCCESS_STOP_REASONS),
        "honest_negative_stop_reason": (
            "formal_open_ledger (requires a host-authored formal/terminal_state.json "
            "with terminal_state=open_ledger; run the formal-terminal-state command first)"
        ),
        "accepted_artifact_types": sorted(PROOF_ARTIFACT_TYPES),
        "evidence_requirement": (
            "each --evidence-id must be a safe id (letters/digits/_/.-) resolving to "
            f"{PROOF_ARTIFACT_DIRNAME}/<id>.json inside --dir, with a relative proof_path "
            "inside the loop directory; use the stage-proof command to scaffold one, "
            "validate-proof-artifact to check it, and append-iteration --dry-run to "
            "run every guard without writing"
        ),
        "expected_proof_artifact": proof_artifact_example(),
    }


def suggest_stop_reason(raw: object) -> str | None:
    """Suggestion-only stop_reason hint; never aliased (the append gate and the
    validate re-check both compare the stored literal, so silent normalization
    on one side would desync them and refuse all later appends)."""
    text = str(raw or "").strip()
    if not text:
        return None
    head = normalized_stop_reason(text.split(":", 1)[0])
    accepted = SUCCESS_STOP_REASONS | {"formal_open_ledger"}
    if head in accepted and normalized_stop_reason(text) not in accepted:
        return head
    matches = difflib.get_close_matches(
        normalized_stop_reason(text), sorted(accepted), n=1, cutoff=0.75
    )
    return matches[0] if matches else None


def suggest_artifact_type(raw: object) -> str | None:
    token = str(raw or "").strip().lower()
    if not token:
        return None
    for known in sorted(PROOF_ARTIFACT_TYPES):
        if token.startswith(known) or known.startswith(token):
            return known
    matches = difflib.get_close_matches(token, sorted(PROOF_ARTIFACT_TYPES), n=1, cutoff=0.6)
    return matches[0] if matches else None


def proof_artifacts_dir(run_dir: Path) -> Path:
    return run_dir / PROOF_ARTIFACT_DIRNAME


def is_safe_evidence_id(evidence_id: object) -> bool:
    return isinstance(evidence_id, str) and SAFE_EVIDENCE_ID.fullmatch(evidence_id) is not None


def proof_artifact_path(run_dir: Path, evidence_id: str) -> Path:
    return proof_artifacts_dir(run_dir) / f"{evidence_id}.json"


def validate_relative_proof_path(run_dir: Path, raw_path: object, evidence_id: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(raw_path, str) or not raw_path.strip():
        return [f"proof artifact {evidence_id!r} proof_path must be a non-empty relative path"]
    candidate = Path(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return [
            f"proof artifact {evidence_id!r} proof_path must stay inside the loop directory "
            f"(use a relative path such as {PROOF_ARTIFACT_DIRNAME}/<file>; the stage-proof "
            "command copies an outside file in and records its provenance)"
        ]
    resolved_run_dir = run_dir.resolve()
    resolved_proof = (resolved_run_dir / candidate).resolve()
    try:
        resolved_proof.relative_to(resolved_run_dir)
    except ValueError:
        errors.append(
            f"proof artifact {evidence_id!r} proof_path must stay inside the loop directory "
            "(symlinks escaping --dir are rejected; use the stage-proof command to copy the file in)"
        )
    if not resolved_proof.is_file():
        errors.append(
            f"proof artifact {evidence_id!r} proof_path does not exist: {raw_path} "
            "(the stage-proof command copies a checked file into the loop directory)"
        )
    return errors


def validate_proof_artifact(run_dir: Path, evidence_id: object) -> list[str]:
    if not is_safe_evidence_id(evidence_id):
        return [
            "proof evidence_id must be 1-128 characters of letters, digits, underscore, hyphen, or dot, "
            "and must start with a letter or digit"
        ]
    evidence_id = str(evidence_id)
    path = proof_artifact_path(run_dir, evidence_id)
    if not path.exists():
        return [f"proof artifact for evidence_id {evidence_id!r} is missing: {PROOF_ARTIFACT_DIRNAME}/{evidence_id}.json"]
    try:
        artifact = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"invalid proof artifact for evidence_id {evidence_id!r}: {exc}"]

    errors: list[str] = []
    if artifact.get("id") != evidence_id:
        errors.append(f"proof artifact {evidence_id!r} id must match evidence_id")
    if artifact.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"proof artifact {evidence_id!r} schema_version must be {SCHEMA_VERSION!r}")
    if artifact.get("machine_checkable") is not True:
        errors.append(f"proof artifact {evidence_id!r} machine_checkable must be true")
    if artifact.get("artifact_type") not in PROOF_ARTIFACT_TYPES:
        hint = suggest_artifact_type(artifact.get("artifact_type"))
        suffix = f"; did you mean {hint!r}?" if hint else ""
        errors.append(
            f"proof artifact {evidence_id!r} artifact_type is invalid "
            f"(got {artifact.get('artifact_type')!r}; accepted: "
            f"{', '.join(sorted(PROOF_ARTIFACT_TYPES))}){suffix}"
        )

    checker = artifact.get("checker")
    if not isinstance(checker, dict):
        errors.append(f"proof artifact {evidence_id!r} checker must be an object")
    else:
        if not isinstance(checker.get("name"), str) or not checker.get("name", "").strip():
            errors.append(f"proof artifact {evidence_id!r} checker.name must be non-empty")
        if checker.get("status") != "passed":
            errors.append(f"proof artifact {evidence_id!r} checker.status must be 'passed'")

    if not isinstance(artifact.get("target"), str) or not artifact.get("target", "").strip():
        errors.append(f"proof artifact {evidence_id!r} target must be non-empty")
    errors.extend(validate_relative_proof_path(run_dir, artifact.get("proof_path"), evidence_id))
    return errors


def valid_proof_artifact_evidence_ids(run_dir: Path, evidence_ids: list[str]) -> list[str]:
    return [evidence_id for evidence_id in evidence_ids if not validate_proof_artifact(run_dir, evidence_id)]


def record_evidence_ids(record: dict[str, Any]) -> list[str]:
    evidence_checked = record.get("evidence_checked")
    if not isinstance(evidence_checked, dict):
        return []
    evidence_ids = evidence_checked.get("evidence_ids")
    return [item for item in evidence_ids or [] if isinstance(item, str) and item]


def loop_paths(run_dir: Path) -> dict[str, Path]:
    return {
        "state": run_dir / "loop_state.json",
        "budget": run_dir / "budget.json",
        "iterations": run_dir / "iterations.jsonl",
        "recovery": run_dir / "recovery.md",
    }


def goal_focus_runtime_mode(run_dir: Path) -> str:
    """Return v2 mode; malformed v2 state must never silently disable enforcement."""
    return str(goal_focus_v2.goal_focus_mode(Path(run_dir)) or "off")


def goal_focus_is_enforced(run_dir: Path) -> bool:
    return goal_focus_runtime_mode(run_dir) == "enforce"


def goal_focus_is_enabled(run_dir: Path) -> bool:
    return goal_focus_runtime_mode(run_dir) in {"monitor", "enforce"}


def goal_focus_state_present(run_dir: Path) -> bool:
    return any(
        (Path(run_dir) / name).exists()
        for name in (
            goal_focus_v2.GOAL_CONTRACT_FILE,
            goal_focus_v2.APPROACH_REGISTRY_FILE,
            goal_focus_v2.CURRENT_PLAN_FILE,
        )
    )


def _preflight_init_leaf(path: Path) -> None:
    """Reject an unsafe pre-existing direct init output without mutating it."""

    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise OSError(f"init output must be absent or a regular file: {path}")
    if getattr(info, "st_nlink", 1) != 1:
        raise OSError(f"init output must have exactly one hard link: {path}")
    if os.name == "posix" and info.st_uid != os.geteuid():
        raise OSError(f"init output must be current-user owned: {path}")


def _preflight_init_directory(path: Path) -> None:
    """Reject a planted link/non-directory at a direct init directory."""

    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise OSError(f"init directory must be a real directory: {path}")
    if os.name == "posix":
        if info.st_uid != os.geteuid():
            raise OSError(f"init directory must be current-user owned: {path}")


def _init_run_directory(raw_path: object) -> Path:
    """Normalize an init target without resolving any existing path link."""

    absolute = Path(os.path.abspath(Path(str(raw_path)).expanduser()))
    current = Path(absolute.anchor or os.sep)
    for component in absolute.parts[1:]:
        current = current / component
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            break
        if stat.S_ISLNK(info.st_mode):
            raise OSError(f"init loop path must not contain a symlink: {current}")
        if current != absolute and not stat.S_ISDIR(info.st_mode):
            raise OSError(f"init loop parent must be a real directory: {current}")
    return absolute


def init_loop(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = _init_run_directory(args.dir)
    if not math.isfinite(float(args.max_usd)) or float(args.max_usd) < 0:
        raise ValueError("max_usd must be a finite non-negative number")
    paths = loop_paths(run_dir)
    # Validate all destructive/pre-existing-state conditions before writing a
    # single control file. ``--force`` is a legacy-loop reset switch; it must
    # never partially overwrite an existing Goal-Focus authority.
    goal_focus_mode = str(getattr(args, "goal_focus_mode", "off") or "off")
    if goal_focus_mode not in GOAL_FOCUS_MODES:
        raise ValueError(f"goal_focus_mode must be one of {sorted(GOAL_FOCUS_MODES)}")
    if goal_focus_state_present(run_dir):
        raise ValueError(
            f"{run_dir} already contains Goal-Focus v2 authority; init --force cannot overwrite it"
        )
    if run_dir.exists() and any(path.exists() for path in paths.values()) and not args.force:
        raise ValueError(f"{run_dir} already contains loop files; pass --force to overwrite")
    gp_path = run_dir / "goal_priority.json"
    if bool(getattr(args, "goal_priority_template", False)) and gp_path.exists() and not args.force:
        raise ValueError(
            f"{gp_path} already exists; pass --force to overwrite goal_priority template"
        )

    formal_requested = bool(
        getattr(args, "formal_policy", None) is not None
        or getattr(args, "formal_project", None)
    )
    default_formal_config = None
    if formal_requested:
        try:
            from formal_policy import default_formal_config  # type: ignore
        except Exception:  # noqa: BLE001
            try:
                from .formal_policy import default_formal_config  # type: ignore
            except Exception:  # noqa: BLE001
                default_formal_config = None  # type: ignore

    try:
        run_info = os.lstat(run_dir)
    except FileNotFoundError:
        _ensure_real_directory(run_dir)
        run_info = os.lstat(run_dir)
    if os.name == "posix":
        if (
            stat.S_ISLNK(run_info.st_mode)
            or not stat.S_ISDIR(run_info.st_mode)
            or run_info.st_uid != os.geteuid()
        ):
            raise OSError("loop directory must be a real current-user-owned directory")
    # Every containment shape masks these mounts, so a loop that lives under
    # one of them cannot be chdir'd into and dies once per iteration with a
    # raw bwrap errno. Refuse it here, where the operator can still move it.
    masked = containment_hidden_root(
        run_dir.resolve(), containment_mask_roots_for_host()
    )
    if masked is not None:
        raise OSError(
            f"loop directory is hidden by the primary containment mount mask "
            f"{masked}; choose a path outside it"
        )
    # Check every output owned directly by init before changing any of them.
    # Atomic replacement below prevents a later leaf-link race from reaching
    # the link target; these checks additionally fail visibly on planted state.
    for output_path in paths.values():
        _preflight_init_leaf(output_path)
    if bool(getattr(args, "goal_priority_template", False)):
        _preflight_init_leaf(gp_path)
    _preflight_init_directory(proof_artifacts_dir(run_dir))
    if formal_requested and default_formal_config is not None:
        formal_dir = run_dir / "formal"
        _preflight_init_directory(formal_dir)
        _preflight_init_leaf(formal_dir / "formal_policy.json")

    if os.name == "posix":
        # Runtime-owned authority and journals assume their parent cannot be
        # replaced or edited by group/other principals.  Apply this invariant
        # at creation rather than asking callers to compensate for their umask.
        os.chmod(run_dir, 0o700, follow_symlinks=False)

    now = utc_now()
    state = {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(uuid.uuid4()),
        "goal": args.goal,
        "success_criteria": args.success_criteria,
        "default_mode": args.mode,
        "status": "initialized",
        "stop_flags": {
            "stop_on_guard_fail": args.stop_on_guard_fail,
            "stop_on_missing_evidence": args.stop_on_missing_evidence,
            "stop_on_scope_change": args.stop_on_scope_change,
        },
        "plateau_rule": args.plateau_rule,
        "stop_conditions": {
            "require_user_stop_only": args.require_user_stop_only,
            "user_overrides": parse_many(args.stop_condition),
        },
        "success_check": args.success_check,
        "created_at": now,
        "updated_at": now,
        "last_iteration": 0,
    }
    budget = {
        "schema_version": SCHEMA_VERSION,
        "budget_owner": args.budget_owner,
        "max_iterations": args.max_iterations,
        "max_wall_time_seconds": args.max_wall_time_seconds,
        "max_tokens": args.max_tokens,
        "max_usd": args.max_usd,
        "max_depth": args.max_depth,
        "max_hops": args.max_hops,
        "max_child_workers": args.max_child_workers,
        "spent_iterations": 0,
        "spent_tokens": 0,
        "spent_usd": 0.0,
        "created_at": now,
        "updated_at": now,
    }

    formal_mirror: dict[str, Any] | None = None
    if formal_requested and default_formal_config is not None:
        formal_updates: dict[str, Any] = {}
        if getattr(args, "formal_policy", None) is not None:
            formal_updates["policy"] = str(args.formal_policy)
        if getattr(args, "formal_project", None):
            formal_updates["project"] = str(args.formal_project)
        if getattr(args, "formal_force_credits", None) is not None:
            formal_updates["force_credits"] = int(args.formal_force_credits)
        if bool(getattr(args, "formal_allow_path_steal", False)):
            formal_updates["allow_path_steal"] = True
        if bool(getattr(args, "formal_typecheck", False)):
            formal_updates["typecheck"] = True
        if bool(getattr(args, "formal_force_after_iteration", False)):
            formal_updates["force_after_iteration"] = True
        formal_mirror = dict(default_formal_config())
        formal_mirror.update(formal_updates)
        state["standing_orders"] = {"formal": copy.deepcopy(formal_mirror)}

    _atomic_write_runtime_text(
        paths["state"], json.dumps(state, indent=2, sort_keys=True) + "\n"
    )
    _atomic_write_runtime_text(
        paths["budget"], json.dumps(budget, indent=2, sort_keys=True) + "\n"
    )
    _atomic_write_runtime_text(paths["iterations"], "")
    proof_dir = _ensure_real_directory(proof_artifacts_dir(run_dir))
    if os.name == "posix":
        os.chmod(proof_dir, 0o700, follow_symlinks=False)
    _atomic_write_runtime_text(
        paths["recovery"],
        "\n".join(
            [
                "# Autonomous Research Loop Recovery",
                "",
                f"- Goal: {args.goal}",
                "- Status: initialized",
                "- Last completed iteration: 0",
                "- Next safe action: start the first bounded iteration",
                "- Remaining evidence gaps: not yet assessed",
                "- Active blockers: none recorded",
                f"- Budget remaining: {args.max_iterations} iterations",
                "",
            ]
        ),
    )
    files_out = {name: str(path) for name, path in paths.items()}
    if bool(getattr(args, "goal_priority_template", False)):
        _atomic_write_runtime_text(gp_path, example_goal_priority_json())
        files_out["goal_priority"] = str(gp_path)

    # Direct Python callers from the v1 runtime do not carry this new field;
    # preserve their legacy behavior. The CLI parser below deliberately defaults
    # new command-line loops to enforce.
    if goal_focus_mode != "off":
        gf = goal_focus_v2.initialize_goal_focus(
            run_dir,
            goal=str(args.goal),
            success_criteria=str(args.success_criteria),
            mode=goal_focus_mode,
        )
        for name, path in (gf.get("paths") or gf.get("files") or {}).items():
            files_out[f"goal_focus_{name}"] = str(path)

    # Optional mirror is written through the same no-follow atomic boundary as
    # the core init files. The standing-order copy was included in the initial
    # loop_state write above, so no later direct rewrite can follow a planted
    # state-file link.
    if formal_mirror is not None:
        formal_dir = _ensure_real_directory(run_dir / "formal")
        if os.name == "posix":
            os.chmod(formal_dir, 0o700, follow_symlinks=False)
        mirror_path = formal_dir / "formal_policy.json"
        _atomic_write_runtime_text(
            mirror_path, json.dumps(formal_mirror, indent=2) + "\n"
        )
        files_out["formal_policy"] = str(mirror_path)
    return {
        "status": "ok",
        "action": "init",
        "dir": str(run_dir),
        "files": files_out,
        "directories": {"proof_artifacts": str(proof_artifacts_dir(run_dir))},
    }


def _validated_iteration_submission_request(
    request: object,
) -> dict[str, Any]:
    """Validate the exact untrusted worker-to-host request schema."""

    if not isinstance(request, dict):
        raise ValueError("iteration submission request must be a JSON object")
    expected = set(ITERATION_SUBMISSION_ARG_FIELDS)
    if set(request) != expected:
        raise ValueError("iteration submission request has unexpected or missing fields")
    validated: dict[str, Any] = {}
    for name in ITERATION_SUBMISSION_ARG_FIELDS:
        value = request[name]
        if name in ITERATION_SUBMISSION_LIST_FIELDS:
            if value is not None and (
                not isinstance(value, list)
                or any(not isinstance(item, str) for item in value)
            ):
                raise ValueError(
                    f"iteration submission field {name} must be null or a string list"
                )
        elif name in ITERATION_SUBMISSION_BOOL_FIELDS:
            if not isinstance(value, bool):
                raise ValueError(
                    f"iteration submission field {name} must be a boolean"
                )
        elif name in ITERATION_SUBMISSION_INT_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"iteration submission field {name} must be a non-negative integer"
                )
        elif name == "usd":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("iteration submission field usd must be numeric")
            amount = float(value)
            if not math.isfinite(amount) or amount < 0:
                raise ValueError(
                    "iteration submission field usd must be finite and non-negative"
                )
        elif not isinstance(value, str):
            raise ValueError(
                f"iteration submission field {name} must be a string"
            )
        validated[name] = copy.deepcopy(value)
    evidence_ids = parse_many(validated.get("evidence_id"))
    if ITERATION_SUBMISSION_FILENAME in evidence_ids:
        raise ValueError("the reserved iteration submission cannot be used as evidence")
    return validated


def _iteration_submission_payload(args: argparse.Namespace) -> dict[str, Any]:
    request = {
        name: copy.deepcopy(getattr(args, name, None))
        for name in ITERATION_SUBMISSION_ARG_FIELDS
    }
    request = _validated_iteration_submission_request(request)
    run_id = safe_registry_run_id(os.environ.get("AAS_AUTOLOOP_RUN_ID"))
    dispatch_id = safe_registry_run_id(
        os.environ.get("AAS_AUTOLOOP_DISPATCH_ID")
    )
    candidate_id = safe_registry_run_id(
        os.environ.get("AAS_AUTOLOOP_CANDIDATE_ID")
    )
    provider = str(os.environ.get("AAS_AUTOLOOP_PRIMARY_PROVIDER") or "").strip()
    if provider not in PROVIDER_SPECS:
        raise ValueError("host-mediated submission requires a known primary provider")
    return {
        "schema_version": ITERATION_SUBMISSION_SCHEMA,
        "run_id": run_id,
        "dispatch_id": dispatch_id,
        "candidate_id": candidate_id,
        "executor_provider": provider,
        "request": request,
    }


def _write_worker_iteration_submission(args: argparse.Namespace) -> dict[str, Any]:
    """Write the only enforce-mode worker output accepted by the host."""

    raw_evidence_dir = str(
        os.environ.get("AAS_AUTOLOOP_EVIDENCE_DIR") or ""
    ).strip()
    raw_evidence_root = str(
        os.environ.get("AAS_AUTOLOOP_EVIDENCE_ROOT") or ""
    ).strip()
    if (
        not raw_evidence_dir
        or raw_evidence_dir != raw_evidence_root
        or not Path(raw_evidence_dir).is_absolute()
    ):
        raise ValueError(
            "host-mediated submission requires one exact absolute evidence directory"
        )
    evidence_dir = Path(os.path.abspath(raw_evidence_dir))
    payload_object = _iteration_submission_payload(args)
    payload = (
        json.dumps(
            payload_object,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    if len(payload) > ITERATION_SUBMISSION_MAX_BYTES:
        raise ValueError("iteration submission is oversized")

    # The walk raises whatever the platform reports for the path anchor, which
    # on a host without directory descriptors is a bare FileNotFoundError for a
    # drive root. Name the directory the worker was told to write into so the
    # failure does not read as a missing submission file.
    try:
        directory_fd = _open_directory_nofollow(evidence_dir)
    except OSError as exc:
        raise OSError(
            f"evidence directory {evidence_dir} cannot be opened for submission: {exc}"
        ) from exc
    try:
        directory_info = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_info.st_mode):
            raise OSError("candidate evidence root is not a directory")
        if os.name == "posix" and (
            directory_info.st_uid != os.geteuid()
            or directory_info.st_mode & 0o077
        ):
            raise OSError("candidate evidence root is not host-private")
        file_fd = os.open(
            ITERATION_SUBMISSION_FILENAME,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        try:
            remaining = memoryview(payload)
            while remaining:
                written = os.write(file_fd, remaining)
                if written <= 0:
                    raise OSError("could not write iteration submission")
                remaining = remaining[written:]
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return {
        "status": "ok",
        "action": "submit-iteration",
        "candidate_id": payload_object["candidate_id"],
        "submission": str(evidence_dir / ITERATION_SUBMISSION_FILENAME),
    }


def _validate_iteration_submission_payload(
    payload: bytes,
    *,
    expected_run_id: str,
    expected_dispatch_id: str,
    expected_candidate_id: str,
    expected_provider: str,
) -> dict[str, Any]:
    if len(payload) > ITERATION_SUBMISSION_MAX_BYTES:
        raise ValueError("iteration submission is oversized")
    text_payload = payload.decode("utf-8")
    findings = panel_prompt_secret_findings(text_payload)
    if findings:
        raise ValueError(
            "iteration submission contains credential-like content "
            f"({', '.join(findings)})"
        )
    value = json.loads(text_payload)
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "run_id",
        "dispatch_id",
        "candidate_id",
        "executor_provider",
        "request",
    }:
        raise ValueError("iteration submission has an invalid top-level schema")
    if value.get("schema_version") != ITERATION_SUBMISSION_SCHEMA:
        raise ValueError("iteration submission schema_version is invalid")
    expected_identity = {
        "run_id": expected_run_id,
        "dispatch_id": expected_dispatch_id,
        "candidate_id": expected_candidate_id,
        "executor_provider": expected_provider,
    }
    for field, expected in expected_identity.items():
        if value.get(field) != expected:
            raise ValueError(
                f"iteration submission {field} does not match host dispatch"
            )
    value["request"] = _validated_iteration_submission_request(value["request"])
    return value


def consume_iteration_submission(
    run_dir: Path,
    evidence_dir: Path,
    *,
    expected_run_id: str,
    expected_dispatch_id: str,
    expected_candidate_id: str,
    expected_provider: str,
    iteration_started_at: str,
    resource_attestation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Claim one exact worker submission, validate it, then stage it as host."""

    # The caller treats FileNotFoundError as "the worker never submitted" and
    # every other error as a host rejection. The host's own walk of the
    # evidence root raises FileNotFoundError too, so passing it through
    # unchanged makes the driver blame the worker for a host-side failure.
    # Re-raise as a plain OSError to keep that boundary readable.
    try:
        directory_fd = _open_directory_nofollow(evidence_dir)
    except OSError as exc:
        raise OSError(
            f"candidate evidence root cannot be opened for host review: {exc}"
        ) from exc
    quarantine = f".host-submission-{uuid.uuid4().hex}"
    original_fd = -1
    moved_fd = -1
    original_identity: tuple[int, int, int, int] | None = None
    original_payload = b""
    try:
        directory_info = os.fstat(directory_fd)
        if os.name == "posix" and (
            directory_info.st_uid != os.geteuid()
            or directory_info.st_mode & 0o077
        ):
            raise OSError("candidate evidence root is not host-private")
        original_fd = os.open(
            ITERATION_SUBMISSION_FILENAME,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
            dir_fd=directory_fd,
        )
        before = os.fstat(original_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > ITERATION_SUBMISSION_MAX_BYTES
            or (
                os.name == "posix"
                and (
                    before.st_uid != directory_info.st_uid
                    or before.st_mode & 0o077
                )
            )
        ):
            raise OSError("iteration submission is not a private single-link file")
        with os.fdopen(original_fd, "rb", closefd=False) as handle:
            original_payload = handle.read(ITERATION_SUBMISSION_MAX_BYTES + 1)
        after = os.fstat(original_fd)
        original_identity = (
            int(before.st_dev),
            int(before.st_ino),
            int(before.st_size),
            int(before.st_mtime_ns),
        )
        if original_identity != (
            int(after.st_dev),
            int(after.st_ino),
            int(after.st_size),
            int(after.st_mtime_ns),
        ):
            raise OSError("iteration submission changed while being read")
        submission = _validate_iteration_submission_payload(
            original_payload,
            expected_run_id=expected_run_id,
            expected_dispatch_id=expected_dispatch_id,
            expected_candidate_id=expected_candidate_id,
            expected_provider=expected_provider,
        )
        os.rename(
            ITERATION_SUBMISSION_FILENAME,
            quarantine,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        moved_fd = os.open(
            quarantine,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
            dir_fd=directory_fd,
        )
        moved = os.fstat(moved_fd)
        with os.fdopen(moved_fd, "rb", closefd=False) as handle:
            moved_payload = handle.read(ITERATION_SUBMISSION_MAX_BYTES + 1)
        moved_identity = (
            int(moved.st_dev),
            int(moved.st_ino),
            int(moved.st_size),
            int(moved.st_mtime_ns),
        )
        if moved_identity != original_identity or moved_payload != original_payload:
            raise OSError(
                "iteration submission changed during host claim and was retained"
            )
        try:
            os.stat(
                ITERATION_SUBMISSION_FILENAME,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise OSError(
                "another iteration submission appeared during host claim"
            )

        host_args = argparse.Namespace(
            dir=str(run_dir), **copy.deepcopy(submission["request"])
        )
        host_control = {
            "AAS_AUTOLOOP_DISPATCH_ID": expected_dispatch_id,
            "AAS_AUTOLOOP_CANDIDATE_ID": expected_candidate_id,
            "AAS_AUTOLOOP_PRIMARY_PROVIDER": expected_provider,
            "AAS_AUTOLOOP_ITERATION_STARTED_AT": iteration_started_at,
            "AAS_AUTOLOOP_RESOURCE_ATTESTATION": copy.deepcopy(
                dict(resource_attestation or {})
            ),
        }
        result = append_iteration(host_args, _host_control=host_control)

        # Delete only the exact claimed inode/content.  A raced replacement is
        # retained for inspection and never mistaken for this submission.
        check_fd = os.open(
            quarantine,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
            dir_fd=directory_fd,
        )
        try:
            check = os.fstat(check_fd)
            with os.fdopen(check_fd, "rb", closefd=False) as handle:
                check_payload = handle.read(ITERATION_SUBMISSION_MAX_BYTES + 1)
        finally:
            os.close(check_fd)
        if (
            (
                int(check.st_dev),
                int(check.st_ino),
                int(check.st_size),
                int(check.st_mtime_ns),
            )
            != original_identity
            or check_payload != original_payload
        ):
            raise OSError(
                "claimed iteration submission changed after staging and was retained"
            )
        os.unlink(quarantine, dir_fd=directory_fd)
        os.fsync(directory_fd)
        return result
    finally:
        if original_fd >= 0:
            os.close(original_fd)
        if moved_fd >= 0:
            os.close(moved_fd)
        os.close(directory_fd)


def _require_formal_terminal_state_for_success(run_dir: Path) -> dict[str, Any]:
    """The host verdict a formal-track success has to stand on, or a refusal.

    Returns the summary to stamp on the row, and ``{}`` when the run is not
    formal-track (nothing to require). Stamping matters as much as checking:
    the row records which verdict admitted it, so the re-check at finalize can
    tell a run that never staged one from a run whose stamp went missing.

    Every route to a success claim goes through here, the last allowed
    iteration included: the requirement follows the claim, not the iteration
    it lands on.
    """
    formal_pol_local = load_formal_policy(run_dir)
    track = formal_track_status(run_dir)
    if formal_pol_local.policy not in {"on", "force"} or not track.formal_track:
        return {}
    terminal = load_formal_terminal_state(run_dir)
    if not terminal or terminal.get("terminal_state") != "sorry_free_artifact":
        # Name the pin when it is the reason: the committed path no longer reads
        # as formal, so without this line the refusal looks like it contradicts
        # loop_state, and the honest fix (write the path back, or produce the
        # terminal state) is not obvious from the message.
        why = (
            "formal policy is active on a formal-track path"
            if track.derived
            else (
                "formal policy is active and the host dispatched this iteration "
                "on the formal track (formal/track.pin.json), whatever the "
                "committed path now says"
            )
        )
        raise GuardError(
            f"{why}: a success claim "
            "requires a host-authored formal/terminal_state.json "
            "with terminal_state=sorry_free_artifact "
            "(run the formal-terminal-state command first)",
            **early_stop_contract(),
        )
    gate = terminal.get("gate") if isinstance(terminal.get("gate"), dict) else {}
    scan = gate.get("scan") if isinstance(gate.get("scan"), dict) else {}
    return {
        "terminal_state": str(terminal.get("terminal_state") or ""),
        "decided_at": str(terminal.get("decided_at") or ""),
        "coverage_digest": str(scan.get("coverage_digest") or ""),
        "source_digest": str(scan.get("source_digest") or ""),
        "track_source": "loop_state" if track.derived else "host_dispatch_pin",
    }


def _require_legacy_host_reverification(run_dir: Path) -> dict[str, Any]:
    """Re-run the host formal checks before a legacy run banks a proof claim.

    Goal-Focus enforce mode stages the record and re-checks at finalize, so a
    stale verdict is caught there. A legacy run has no finalize: the append is
    the last moment left to ask whether the staged ``sorry_free_artifact``
    still holds, and the project stays agent-writable up to it. Refuse on
    anything other than agreement, so "the host could not re-check" never
    banks as "the host confirmed".

    ``not_applicable`` is refused here too. The only caller reaches this after
    reading a certified verdict off disk, so "there is nothing staged to
    re-check" means the stamp went away in between — which is how an agent
    turns a re-check it cannot survive into no re-check at all.
    """
    try:
        result = reverify_formal_evidence(run_dir)
    except Exception as exc:  # noqa: BLE001 - any failure to re-check is a refusal.
        raise GuardError(
            f"host re-verification of the staged formal verdict failed: {exc}",
            **early_stop_contract(),
        ) from exc
    if not isinstance(result, Mapping):
        raise GuardError(
            "host re-verification of the staged formal verdict returned no result",
            **early_stop_contract(),
        )
    status = str(result.get("status") or "").strip()
    if status == "reverified":
        return dict(result)
    detail = str(result.get("detail") or "").strip()
    raise GuardError(
        "host re-verification of the staged formal verdict did not confirm it: "
        + (status or "no_status")
        + (f": {detail}" if detail else ""),
        **early_stop_contract(),
    )


def append_iteration(
    args: argparse.Namespace,
    *,
    _host_control: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if (
        _host_control is None
        and os.environ.get("AAS_AUTOLOOP_HOST_MEDIATED_SUBMISSION") == "1"
    ):
        if getattr(args, "dry_run", False):
            raise ValueError(
                "append-iteration --dry-run is unavailable under host-mediated "
                "submission (AAS_AUTOLOOP_HOST_MEDIATED_SUBMISSION=1): guards run "
                "host-side on the real submission; submit without --dry-run"
            )
        return _write_worker_iteration_submission(args)
    control = os.environ if _host_control is None else _host_control
    run_dir = Path(args.dir).expanduser().resolve()
    if not math.isfinite(float(args.usd)) or float(args.usd) < 0:
        raise ValueError("usd must be a finite non-negative number")
    paths = loop_paths(run_dir)
    errors = validate_loop_dir(run_dir)["errors"]
    if errors:
        raise ValueError("cannot append iteration before validation passes: " + "; ".join(errors))
    enforced_goal_focus = goal_focus_is_enforced(run_dir)
    if goal_focus_state_present(run_dir) and enforced_goal_focus:
        strict_goal_focus = goal_focus_v2.validate_goal_focus(
            run_dir, require_enabled=True
        )
        if strict_goal_focus.get("errors"):
            raise ValueError(
                "cannot append against invalid Goal-Focus authority: "
                + "; ".join(strict_goal_focus.get("errors") or [])
            )
    dispatch = goal_focus_v2.load_iteration_dispatch(run_dir)
    expected_dispatch_id = str(
        control.get("AAS_AUTOLOOP_DISPATCH_ID") or ""
    ).strip()
    if enforced_goal_focus:
        if not dispatch:
            raise ValueError(
                "Goal-Focus enforce mode requires a live host dispatch intent"
            )
        if not expected_dispatch_id:
            raise ValueError(
                "Goal-Focus enforce mode requires the exact host dispatch id"
            )
        goal_focus_v2.validate_iteration_dispatch(
            run_dir,
            dispatch,
            expected_dispatch_id=expected_dispatch_id,
        )
    elif dispatch:
        raise ValueError("an in-flight enforce dispatch cannot append through legacy mode")
    if args.decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of {sorted(VALID_DECISIONS)}")
    if args.mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(VALID_MODES)}")

    state = read_json(paths["state"])
    budget = read_json(paths["budget"])
    iterations = read_iterations(paths["iterations"])
    max_iterations = int(budget["max_iterations"])
    spent_iterations = int(budget.get("spent_iterations", 0))
    if state.get("status") in TERMINAL_STATUSES:
        raise ValueError(f"cannot append iteration after loop status is {state.get('status')}")
    if len(iterations) >= max_iterations:
        raise ValueError("cannot append iteration because max_iterations is exhausted")
    if spent_iterations >= max_iterations:
        raise ValueError("cannot append iteration because spent_iterations reached max_iterations")
    number = len(iterations) + 1
    remaining_after_append = max_iterations - number
    if remaining_after_append == 0 and args.decision not in TERMINAL_DECISIONS:
        raise ValueError("final allowed iteration must use decision stop or blocked, not a continuing decision")
    if args.decision == "blocked" and remaining_after_append > 0:
        raise ValueError(
            "early blocked before max_iterations is not a valid stop under the enforcement policy: "
            "record the blocker and continue with decision revise or delegate"
        )
    claim_ids = parse_many(args.claim_id)
    if enforced_goal_focus:
        if not claim_ids:
            raise ValueError(
                "Goal-Focus enforce mode requires at least one explicit --claim-id "
                "covering every material result in the staged output"
            )
        if len(set(claim_ids)) != len(claim_ids) or any(
            SAFE_EVIDENCE_ID.fullmatch(claim_id) is None for claim_id in claim_ids
        ):
            raise ValueError("Goal-Focus claim ids must be unique safe identifiers")
    evidence_ids = parse_many(args.evidence_id)
    if enforced_goal_focus and claim_ids and not evidence_ids:
        raise ValueError(
            "Goal-Focus enforce mode requires at least one staged --evidence-id "
            "for material claim review"
        )
    host_reverification: dict[str, Any] | None = None
    formal_terminal_claim: dict[str, Any] = {}
    if args.decision == "stop" and remaining_after_append > 0:
        if str(args.stop_reason or "").strip() == "formal_open_ledger":
            # Honest negative for formal-track runs: allowed early only when the
            # host-authored terminal state records an open obligation ledger.
            terminal = load_formal_terminal_state(run_dir)
            if not terminal or terminal.get("terminal_state") != "open_ledger":
                raise GuardError(
                    "stop_reason formal_open_ledger requires a host-authored "
                    "formal/terminal_state.json with terminal_state=open_ledger "
                    "(run the formal-terminal-state command first)",
                    **early_stop_contract(),
                )
        else:
            if not is_success_stop_reason(args.stop_reason):
                hint = suggest_stop_reason(args.stop_reason)
                suffix = (
                    f" (got {str(args.stop_reason or '')!r}; did you mean {hint!r}? "
                    "the stop_reason must be exactly one accepted token — put detail in --output)"
                    if hint
                    else f" (got {str(args.stop_reason or '')!r})"
                )
                raise GuardError(
                    "early stop before max_iterations requires a success/proof stop_reason"
                    + suffix,
                    **early_stop_contract(),
                )
            if not evidence_ids:
                raise GuardError(
                    "early stop before max_iterations requires at least one proof artifact evidence_id "
                    f"(--evidence-id <id> resolving to {PROOF_ARTIFACT_DIRNAME}/<id>.json)",
                    **early_stop_contract(),
                )
            proof_errors: list[str] = []
            for evidence_id in evidence_ids:
                proof_errors.extend(validate_proof_artifact(run_dir, evidence_id))
            if len(proof_errors) == len(evidence_ids) or not valid_proof_artifact_evidence_ids(run_dir, evidence_ids):
                raise GuardError(
                    "early stop before max_iterations requires at least one evidence_id with a valid proof artifact: "
                    + "; ".join(proof_errors),
                    **early_stop_contract(),
                )
            formal_terminal_claim = _require_formal_terminal_state_for_success(run_dir)
            if formal_terminal_claim and not enforced_goal_focus:
                # Enforce mode re-checks the staged verdict when the host
                # finalizes the candidate. A legacy run never reaches that
                # step, so it re-checks here instead of banking a verdict
                # nothing has confirmed since it was written.
                host_reverification = _require_legacy_host_reverification(run_dir)
    now = utc_now()
    record = {
        "schema_version": SCHEMA_VERSION,
        "iteration": number,
        "timestamp": now,
        "mode": args.mode,
        "objective": args.objective,
        "candidate_id": str(
            control.get("AAS_AUTOLOOP_CANDIDATE_ID") or ""
        ).strip(),
        "claim_ids": claim_ids,
        "input_refs": parse_many(args.input_ref),
        "evidence_checked": {
            "source_ids": parse_many(args.source_id),
            "claim_ids": claim_ids,
            "evidence_ids": evidence_ids,
            "guard_refs": parse_many(args.guard_ref),
        },
        "actions_taken": parse_many(args.action_taken),
        "output": args.output,
        "remaining_gaps": parse_many(args.remaining_gap),
        "budget_delta": {
            "iterations": 1,
            "tokens": args.tokens,
            "usd": args.usd,
            "wall_time_seconds": args.wall_time_seconds,
        },
        "decision": args.decision,
        "stop_reason": args.stop_reason,
    }
    if host_reverification is not None:
        # The banked row carries the proof that the host re-checked, so a later
        # audit can tell a re-verified bank from an unchecked one.
        record["host_reverification"] = host_reverification
    # Optional goal_priority soft fields (open vocabulary; advise+ may warn).
    goal_contrib = getattr(args, "goal_contribution", None) or ""
    campaign_id = getattr(args, "campaign_id", None) or ""
    if str(goal_contrib).strip():
        record["goal_contribution"] = str(goal_contrib).strip()
    if str(campaign_id).strip():
        record["campaign_id"] = str(campaign_id).strip()
    if bool(getattr(args, "local_without_goal_delta", False)):
        record["local_without_goal_delta"] = True
    tag = getattr(args, "local_without_goal_delta_tag", None) or ""
    if str(tag).strip():
        record["local_without_goal_delta_tag"] = str(tag).strip()
    residual_id = getattr(args, "residual_id", None) or ""
    if str(residual_id).strip():
        record["residual_id"] = str(residual_id).strip()
    scope_lock = getattr(args, "scope_lock", None) or ""
    if str(scope_lock).strip():
        record["scope_lock"] = str(scope_lock).strip()
    detail = getattr(args, "goal_contribution_detail", None) or ""
    if str(detail).strip():
        record["goal_contribution_detail"] = str(detail).strip()

    completed_summary = str(getattr(args, "completed_summary", None) or args.output or "").strip()
    current_summary = str(getattr(args, "current_summary", None) or "").strip()
    next_action = str(getattr(args, "next_action", None) or "").strip()
    if completed_summary:
        record["completed_summary"] = completed_summary
    if current_summary:
        record["current_summary"] = current_summary
    if next_action:
        record["proposed_next_action"] = next_action
    campaign_delta = str(getattr(args, "campaign_delta", None) or "none").strip().lower()
    global_delta = str(getattr(args, "global_delta", None) or "none").strip().lower()
    if campaign_delta not in {"none", "incremental", "substantial", "closed"}:
        raise ValueError("campaign_delta must be none, incremental, substantial, or closed")
    if global_delta not in {"none", "reduced", "satisfied"}:
        raise ValueError("global_delta must be none, reduced, or satisfied")
    if (
        enforced_goal_focus
        and global_delta == "satisfied"
        and args.decision != "stop"
        and remaining_after_append > 0
    ):
        valid_terminal_evidence = valid_proof_artifact_evidence_ids(
            run_dir, evidence_ids
        )
        if not valid_terminal_evidence:
            raise ValueError(
                "an early Goal-Focus global_delta=satisfied claim requires at least "
                "one staged evidence_id with a valid proof artifact"
            )
        # An enforce-mode submission never says "stop": it reports the goal
        # satisfied, and `finalize_candidate` rewrites the accepted row to a
        # success stop. The formal-track requirement therefore cannot hang off
        # the decision alone, or this route banks a goal success on a
        # formal-track run with no host verdict behind it.
        formal_terminal_claim = _require_formal_terminal_state_for_success(run_dir)
    if not formal_terminal_claim and (
        (enforced_goal_focus and global_delta == "satisfied")
        or (args.decision == "stop" and is_success_stop_reason(args.stop_reason))
    ):
        # Both routes above are guarded by `remaining_after_append > 0`, because
        # stopping early is what they police. The formal verdict is not about
        # stopping early: it is what a success claim stands on. The last allowed
        # iteration is the one route to a success stop that passes neither guard,
        # so without this the run that exhausts its budget banks the strongest
        # claim under the weakest check.
        formal_terminal_claim = _require_formal_terminal_state_for_success(run_dir)
        if formal_terminal_claim and not enforced_goal_focus and host_reverification is None:
            host_reverification = _require_legacy_host_reverification(run_dir)
    record["progress_assessment"] = {
        "campaign_delta": campaign_delta,
        "global_delta": global_delta,
        "obligation_ids": parse_many(getattr(args, "obligation_id", None)),
    }
    if formal_terminal_claim:
        # Which host verdict admitted this row. The re-check at finalize reads
        # it to tell "this run never staged a verdict" — a pass — from "the
        # verdict this row stands on is gone", which is not.
        record["formal_terminal_state"] = formal_terminal_claim
    claimed_executor = str(getattr(args, "executor_provider", None) or "").strip()
    host_executor = str(control.get("AAS_AUTOLOOP_PRIMARY_PROVIDER") or "").strip()
    if enforced_goal_focus and host_executor:
        if claimed_executor and claimed_executor != host_executor:
            raise ValueError(
                "--executor-provider conflicts with the host-selected Goal-Focus driver"
            )
        executor_provider = host_executor
    else:
        executor_provider = claimed_executor or host_executor
    compute_execution = parse_compute_runs(
        getattr(args, "compute_run", None),
        explicit_none=bool(getattr(args, "compute_none", False)),
    )
    if enforced_goal_focus:
        goal_focus_v2.validate_compute_execution(run_dir, compute_execution)
    record["execution"] = {
        "executor_provider": executor_provider,
        "started_at": str(
            getattr(args, "iteration_started_at", None)
            or control.get("AAS_AUTOLOOP_ITERATION_STARTED_AT")
            or ""
        ).strip(),
        "work_finished_at": now,
        "compute": compute_execution,
    }

    if goal_focus_is_enabled(run_dir):
        plan = goal_focus_v2.load_current_plan(
            run_dir, required=goal_focus_is_enforced(run_dir)
        )
        plan = plan if isinstance(plan, dict) else {}
        record["goal_focus"] = {
            "plan_id": str(plan.get("plan_id") or ""),
            "plan_revision": int(plan.get("plan_revision") or 0),
            "direction_id": str(plan.get("decision_id") or ""),
            "approach_id": str(plan.get("approach_id") or ""),
            "campaign_id": str(plan.get("campaign_id") or campaign_id),
            "scope_lock": str(plan.get("scope_lock") or scope_lock),
        }
        if not current_summary:
            record["current_summary"] = str(
                plan.get("current_summary")
                or plan.get("objective")
                or "Active plan remains unresolved."
            )
        if not next_action:
            record["proposed_next_action"] = str(plan.get("next_action") or "")
        if enforced_goal_focus:
            if getattr(args, "dry_run", False):
                # Every guard above has passed; stop before the first write.
                return {
                    "status": "ok",
                    "action": "append-iteration",
                    "dry_run": True,
                    "would_stage": True,
                    "dir": str(run_dir),
                    "iteration": number,
                    "decision": args.decision,
                    "would_append": record,
                    "warnings": collect_goal_priority_warnings(run_dir, latest_record=record),
                }
            staged = goal_focus_v2.stage_iteration_candidate(
                run_dir,
                record,
                expected_plan_revision=int(plan.get("plan_revision") or 0),
                expected_dispatch_id=expected_dispatch_id,
                host_resource_attestation=(
                    control.get("AAS_AUTOLOOP_RESOURCE_ATTESTATION")
                    if isinstance(
                        control.get("AAS_AUTOLOOP_RESOURCE_ATTESTATION"), Mapping
                    )
                    else None
                ),
            )
            return {
                # CLI success must remain exit 0. Expose the workflow state in
                # a separate field so the driver can proceed to host review.
                "status": "ok",
                "staging_status": "staged",
                "action": "stage-iteration",
                "dir": str(run_dir),
                "iteration": number,
                "decision": args.decision,
                "candidate": staged,
                "warnings": collect_goal_priority_warnings(run_dir, latest_record=record),
            }
    if getattr(args, "dry_run", False):
        # Every guard above has passed; stop before the first write.
        return {
            "status": "ok",
            "action": "append-iteration",
            "dry_run": True,
            "would_stage": False,
            "dir": str(run_dir),
            "iteration": number,
            "decision": args.decision,
            "would_append": record,
            "warnings": collect_goal_priority_warnings(run_dir, latest_record=record),
        }
    append_jsonl(paths["iterations"], record)

    state["last_iteration"] = number
    state["updated_at"] = now
    state["status"] = "blocked" if args.decision == "blocked" else "stopped" if args.decision == "stop" else "running"
    budget["spent_iterations"] = number
    budget["spent_tokens"] = int(budget.get("spent_tokens", 0)) + args.tokens
    budget["spent_usd"] = float(budget.get("spent_usd", 0.0)) + args.usd
    budget["updated_at"] = now
    write_json(paths["state"], state)
    write_json(paths["budget"], budget)

    remaining_iterations = max(0, int(budget["max_iterations"]) - int(budget["spent_iterations"]))
    # Path.write_text lacks the newline argument before Python 3.10.
    with paths["recovery"].open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "\n".join(
                [
                    "# Autonomous Research Loop Recovery",
                    "",
                    f"- Goal: {state.get('goal', '')}",
                    f"- Status: {state.get('status', '')}",
                    f"- Last completed iteration: {number}",
                    f"- Next safe action: {'report stop status' if args.decision in {'stop', 'blocked'} else 'continue from the last recorded decision'}",
                    f"- Remaining evidence gaps: {', '.join(record['remaining_gaps']) if record['remaining_gaps'] else 'none recorded'}",
                    f"- Active blockers: {args.stop_reason if args.decision == 'blocked' and args.stop_reason else 'none recorded'}",
                    f"- Budget remaining: {remaining_iterations} iterations",
                    "",
                ]
            )
        )
    gp_warnings = collect_goal_priority_warnings(run_dir, latest_record=record)
    return {
        "status": "ok",
        "action": "append-iteration",
        "dir": str(run_dir),
        "iteration": number,
        "decision": args.decision,
        "warnings": gp_warnings,
    }


def validate_loop_dir(run_dir: Path) -> dict[str, Any]:
    paths = loop_paths(run_dir)
    errors: list[str] = []
    for name, path in paths.items():
        if not path.exists():
            errors.append(f"missing {name} file: {path.name}")

    state: dict[str, Any] = {}
    budget: dict[str, Any] = {}
    iterations: list[dict[str, Any]] = []
    if paths["state"].exists():
        try:
            state = read_json(paths["state"])
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"invalid loop_state.json: {exc}")
    if paths["budget"].exists():
        try:
            budget = read_json(paths["budget"])
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"invalid budget.json: {exc}")
    if paths["iterations"].exists():
        try:
            iterations = read_iterations(paths["iterations"])
        except (OSError, ValueError) as exc:
            errors.append(str(exc))

    for field in ("schema_version", "run_id", "goal", "success_criteria", "default_mode", "status"):
        if state and field not in state:
            errors.append(f"loop_state.json missing {field}")
    if state and state.get("default_mode") not in VALID_MODES:
        errors.append("loop_state.json default_mode is invalid")

    for field in ("schema_version", "max_iterations", "max_depth", "max_hops", "max_child_workers"):
        if budget and field not in budget:
            errors.append(f"budget.json missing {field}")
    if budget:
        for field in ("max_iterations", "max_depth", "max_hops", "max_child_workers"):
            value = budget.get(field)
            if not isinstance(value, int) or value < 0:
                errors.append(f"budget.json {field} must be a non-negative integer")
        spent_iterations = budget.get("spent_iterations", 0)
        if not isinstance(spent_iterations, int) or spent_iterations < 0:
            errors.append("budget.json spent_iterations must be a non-negative integer")
        else:
            max_iterations = budget.get("max_iterations")
            if isinstance(max_iterations, int) and max_iterations >= 0:
                remaining_iterations = max(0, max_iterations - spent_iterations)
                if len(iterations) > max_iterations:
                    errors.append("iterations.jsonl exceeds budget.json max_iterations")
                if spent_iterations != len(iterations):
                    errors.append("budget.json spent_iterations must equal iterations.jsonl record count")
                if state and state.get("status") == "running" and remaining_iterations == 0:
                    errors.append("loop_state.json status cannot be running when iteration budget is exhausted")
        for field in ("max_usd", "spent_usd"):
            raw = budget.get(field, 0.0)
            if isinstance(raw, bool):
                errors.append(f"budget.json {field} must be a finite non-negative number")
                continue
            try:
                amount = float(raw)
            except (TypeError, ValueError):
                errors.append(f"budget.json {field} must be a finite non-negative number")
                continue
            if not math.isfinite(amount) or amount < 0:
                errors.append(f"budget.json {field} must be a finite non-negative number")

    expected = 1
    for record in iterations:
        if record.get("iteration") != expected:
            errors.append(f"iterations.jsonl expected iteration {expected}")
        if record.get("decision") not in VALID_DECISIONS:
            errors.append(f"iteration {expected} has invalid decision")
        if record.get("mode") not in VALID_MODES:
            errors.append(f"iteration {expected} has invalid mode")
        if "objective" not in record:
            errors.append(f"iteration {expected} missing objective")
        delta = record.get("budget_delta")
        if isinstance(delta, dict):
            raw_usd = delta.get("usd", 0.0)
            try:
                usd = float(raw_usd)
            except (TypeError, ValueError):
                usd = float("nan")
            if isinstance(raw_usd, bool) or not math.isfinite(usd) or usd < 0:
                errors.append(
                    f"iteration {expected} budget_delta.usd must be a finite non-negative number"
                )
        expected += 1
    if budget and iterations:
        max_iterations = budget.get("max_iterations")
        spent_iterations = budget.get("spent_iterations")
        if isinstance(max_iterations, int) and isinstance(spent_iterations, int):
            remaining_iterations = max(0, max_iterations - spent_iterations)
            last = iterations[-1]
            if last.get("decision") not in TERMINAL_DECISIONS and remaining_iterations == 0:
                errors.append("latest iteration cannot have a continuing decision when iteration budget is exhausted")
            for record in iterations:
                iteration_number = record.get("iteration")
                host_reviewed_goal_success = (
                    normalized_stop_reason(record.get("stop_reason"))
                    == HOST_REVIEWED_GOAL_SUCCESS_REASON
                )
                if host_reviewed_goal_success:
                    errors.extend(
                        f"iteration {iteration_number}: {error}"
                        for error in goal_focus_v2.validate_host_finalized_goal_success(
                            run_dir, record
                        )
                    )
                if (
                    record.get("decision") == "stop"
                    and isinstance(iteration_number, int)
                    and iteration_number < max_iterations
                ):
                    if host_reviewed_goal_success:
                        continue
                    if str(record.get("stop_reason") or "").strip() == "formal_open_ledger":
                        # Mirror the append-gate exemption: an honest-negative early
                        # stop is valid when the host-authored terminal state records
                        # an open obligation ledger.
                        terminal = load_formal_terminal_state(run_dir)
                        if not terminal or terminal.get("terminal_state") != "open_ledger":
                            errors.append(
                                f"iteration {iteration_number} early stop with stop_reason "
                                "formal_open_ledger requires a host-authored "
                                "formal/terminal_state.json with terminal_state=open_ledger"
                            )
                        continue
                    if not is_success_stop_reason(record.get("stop_reason")):
                        errors.append(
                            f"iteration {iteration_number} early stop before max_iterations must use a success/proof stop_reason"
                        )
                        errors.append(
                            f"iteration {iteration_number}: accepted stop_reason values: "
                            f"{', '.join(sorted(SUCCESS_STOP_REASONS))} "
                            "(or formal_open_ledger with a host-authored open_ledger terminal state)"
                        )
                    evidence_ids = record_evidence_ids(record)
                    if not evidence_ids:
                        errors.append(
                            f"iteration {iteration_number} early stop before max_iterations must cite proof artifact evidence_ids"
                        )
                    elif not valid_proof_artifact_evidence_ids(run_dir, evidence_ids):
                        errors.append(
                            f"iteration {iteration_number} early stop before max_iterations must cite a valid proof artifact"
                        )
                        for evidence_id in evidence_ids:
                            errors.extend(
                                f"iteration {iteration_number}: {error}"
                                for error in validate_proof_artifact(run_dir, evidence_id)
                            )
                    # Mirror the append-gate formal-track success rule: an agent
                    # deleting (or never obtaining) the host verdict must not
                    # leave validate green.
                    formal_pol_local = load_formal_policy(run_dir)
                    if formal_pol_local.policy in {"on", "force"} and is_formal_track(run_dir):
                        terminal = load_formal_terminal_state(run_dir)
                        if not terminal or terminal.get("terminal_state") != "sorry_free_artifact":
                            errors.append(
                                f"iteration {iteration_number} early success stop on a "
                                "formal-track run requires a host-authored "
                                "formal/terminal_state.json with terminal_state=sorry_free_artifact"
                            )
                if (
                    record.get("decision") == "stop"
                    and isinstance(iteration_number, int)
                    and iteration_number >= max_iterations
                    and not host_reviewed_goal_success
                    and is_success_stop_reason(record.get("stop_reason"))
                ):
                    # The last allowed iteration never enters the early-stop
                    # block above, so its success row would be the one place a
                    # formal-track claim needs no host verdict. The rule follows
                    # the claim, not the iteration it lands on.
                    formal_pol_local = load_formal_policy(run_dir)
                    if formal_pol_local.policy in {"on", "force"} and is_formal_track(run_dir):
                        terminal = load_formal_terminal_state(run_dir)
                        if not terminal or terminal.get("terminal_state") != "sorry_free_artifact":
                            errors.append(
                                f"iteration {iteration_number} success stop on a "
                                "formal-track run requires a host-authored "
                                "formal/terminal_state.json with terminal_state=sorry_free_artifact"
                            )

    latest = iterations[-1] if iterations else None
    warnings = collect_goal_priority_warnings(run_dir, latest_record=latest)
    goal_focus_checked: dict[str, Any] | None = None
    if goal_focus_state_present(run_dir):
        try:
            checked_mode = goal_focus_runtime_mode(run_dir)
            goal_focus_checked = goal_focus_v2.validate_goal_focus(
                run_dir, require_enabled=checked_mode in {"monitor", "enforce"}
            )
            gf_errors = list(goal_focus_checked.get("errors") or [])
            gf_warnings = list(goal_focus_checked.get("warnings") or [])
            if checked_mode == "enforce":
                errors.extend(f"goal_focus: {item}" for item in gf_errors)
            else:
                warnings.extend(f"goal_focus: {item}" for item in gf_errors)
            warnings.extend(f"goal_focus: {item}" for item in gf_warnings)
        except Exception as exc:  # noqa: BLE001 - surface a deterministic validation finding
            message = f"goal_focus validation failed: {exc}"
            errors.append(message)
    return {
        "status": "failed" if errors else "ok",
        "errors": errors,
        "warnings": warnings,
        "checked": {
            "dir": str(run_dir),
            "files": {name: path.exists() for name, path in paths.items()},
            "iterations": len(iterations),
            "goal_focus": goal_focus_checked,
        },
    }


def validate_command(args: argparse.Namespace) -> dict[str, Any]:
    return validate_loop_dir(Path(args.dir).expanduser().resolve())


def validate_proof_artifact_command(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    evidence_id = str(args.evidence_id or "").strip()
    errors = validate_proof_artifact(run_dir, evidence_id)
    result: dict[str, Any] = {
        "status": "ok" if not errors else "failed",
        "action": "validate-proof-artifact",
        "dir": str(run_dir),
        "evidence_id": evidence_id,
        "errors": errors,
    }
    if errors:
        result["expected_proof_artifact"] = proof_artifact_example()
    return result


def stage_proof_command(args: argparse.Namespace) -> dict[str, Any]:
    """Copy a checked proof file into the loop dir and scaffold its artifact.

    The copy records provenance (source path + sha256) so the staged file can
    be traced; the scaffold is exactly the shape validate_proof_artifact
    accepts, with checker fields taken from the caller's flags.
    """
    run_dir = Path(args.dir).expanduser().resolve()
    if not run_dir.is_dir():
        raise ValueError(f"--dir does not exist or is not a directory: {run_dir}")
    evidence_id = str(args.id or "").strip()
    if not is_safe_evidence_id(evidence_id):
        raise ValueError(
            "stage-proof --id must be 1-128 characters of letters, digits, "
            "underscore, hyphen, or dot, and must start with a letter or digit"
        )
    source = Path(args.file).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"stage-proof --file does not exist: {args.file}")
    artifact_type = str(args.artifact_type or "").strip().lower()
    if artifact_type not in PROOF_ARTIFACT_TYPES:
        hint = suggest_artifact_type(artifact_type)
        suffix = f"; did you mean {hint!r}?" if hint else ""
        raise ValueError(
            f"stage-proof --artifact-type must be one of "
            f"{', '.join(sorted(PROOF_ARTIFACT_TYPES))} (got {artifact_type!r}){suffix}"
        )
    target = str(args.target or "").strip()
    if not target:
        raise ValueError("stage-proof --target must describe the proved statement")
    checker_name = str(args.checker_name or "").strip()
    if not checker_name:
        raise ValueError("stage-proof --checker-name must name the checker (e.g. lake)")

    artifacts_dir = proof_artifacts_dir(run_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    # Prefix with the evidence id: Lean sources routinely share a basename
    # (Proof.lean, Main.lean), so a bare source.name would collide across
    # different artifacts, and the id prefix also keeps the staged copy from
    # ever colliding with the <id>.json artifact record itself.
    staged_name = f"{evidence_id}__{source.name}"
    staged_path = artifacts_dir / staged_name
    artifact_path = proof_artifact_path(run_dir, evidence_id)
    if artifact_path.exists():
        raise ValueError(
            f"proof artifact {evidence_id!r} already exists: "
            f"{PROOF_ARTIFACT_DIRNAME}/{evidence_id}.json (pick a new --id)"
        )
    if staged_path.exists():
        raise ValueError(
            f"staged file already exists: {PROOF_ARTIFACT_DIRNAME}/{staged_name} "
            "(pick a new --id or remove the stale copy first)"
        )
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    staged_path.write_bytes(payload)
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "id": evidence_id,
        "artifact_type": artifact_type,
        "machine_checkable": True,
        "target": target,
        "proof_path": f"{PROOF_ARTIFACT_DIRNAME}/{staged_name}",
        "checker": {"name": checker_name, "status": str(args.checker_status or "passed")},
        "staged_from": str(source),
        "sha256": digest,
    }
    write_json(artifact_path, artifact)
    errors = validate_proof_artifact(run_dir, evidence_id)
    return {
        "status": "ok" if not errors else "failed",
        "action": "stage-proof",
        "dir": str(run_dir),
        "evidence_id": evidence_id,
        "artifact_path": f"{PROOF_ARTIFACT_DIRNAME}/{evidence_id}.json",
        "proof_path": f"{PROOF_ARTIFACT_DIRNAME}/{staged_name}",
        "sha256": digest,
        "errors": errors,
        "artifact": artifact,
    }


RETRACTIONS_FILENAME = "retractions.jsonl"
RETRACTIONS_PER_ITERATION_CAP = 3


def retract_iteration_command(args: argparse.Namespace) -> dict[str, Any]:
    """Remove the newest non-terminal ledger record and restore coherence.

    Legacy-mode recovery only: an agent that botched an append can undo it
    instead of rewriting the ledger by hand (the T1 failure mode). Refused
    under Goal-Focus enforce, under host-mediated submission, and while any
    armed registry entry claims the loop — in those modes the host owns the
    ledger. Every retraction (and any rollback) lands in an append-only
    retractions.jsonl audit file, and the loop must validate cleanly after
    the mutation or the change is rolled back byte-for-byte.
    """
    if os.environ.get("AAS_AUTOLOOP_HOST_MEDIATED_SUBMISSION") == "1":
        raise ValueError(
            "retract-iteration is unavailable under host-mediated submission "
            "(AAS_AUTOLOOP_HOST_MEDIATED_SUBMISSION=1): the host owns the ledger"
        )
    run_dir = Path(args.dir).expanduser().resolve()
    if not run_dir.is_dir():
        raise ValueError(f"--dir does not exist or is not a directory: {run_dir}")
    reason = str(args.reason or "").strip()
    if not reason:
        raise ValueError(
            "retract-iteration requires a non-empty --reason describing why the "
            "record is being withdrawn"
        )
    if goal_focus_is_enforced(run_dir):
        raise ValueError(
            "retract-iteration is refused while Goal-Focus enforce mode is active: "
            "host banking owns the ledger (use the host review flow instead)"
        )
    if goal_focus_v2.load_iteration_dispatch(run_dir):
        raise ValueError(
            "retract-iteration is refused while a host dispatch intent is in flight"
        )
    try:
        reg = registry_dir(args)
        if reg.is_dir():
            # pathlib.glob swallows PermissionError, which would read an
            # unreadable registry as "disarmed"; probe it so we fail closed.
            os.listdir(reg)
        target = str(run_dir)
        for entry_path, entry in list_registry_entries(reg):
            if str(entry.get("loop_dir") or "") == target:
                raise ValueError(
                    "retract-iteration is refused while the loop is armed "
                    f"(registry entry {entry_path.name}): stop or disarm the "
                    "driver first"
                )
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - unreadable registry fails closed.
        raise ValueError(
            f"retract-iteration could not prove the loop is disarmed: {exc}"
        ) from exc

    paths = loop_paths(run_dir)
    for name in ("state", "budget", "iterations"):
        if not paths[name].exists():
            raise ValueError(f"loop file missing: {paths[name].name}")
    state = read_json(paths["state"])
    budget = read_json(paths["budget"])
    iterations = read_iterations(paths["iterations"])
    if not iterations:
        raise ValueError("nothing to retract: iterations.jsonl has no records")
    if state.get("status") in TERMINAL_STATUSES:
        raise ValueError(
            f"cannot retract after loop status is {state.get('status')}: "
            "terminal records are permanent"
        )
    record = iterations[-1]
    if record.get("decision") in TERMINAL_DECISIONS:
        raise ValueError(
            f"cannot retract a terminal record (decision {record.get('decision')})"
        )
    number = record.get("iteration")

    # Rotation safety: only a record still in the live file can be removed.
    raw = _read_regular_text(paths["iterations"])
    lines = raw.splitlines(keepends=True)
    last_index = max(
        (i for i, line in enumerate(lines) if line.strip()), default=None
    )
    if last_index is None:
        raise ValueError(
            "the newest ledger record lives in a rotated shard: retraction only "
            "operates on the live iterations.jsonl"
        )
    try:
        live_last = json.loads(lines[last_index].strip())
    except json.JSONDecodeError as exc:
        raise ValueError(f"live ledger tail is invalid JSON: {exc}") from exc
    if live_last != record:
        raise ValueError(
            "the live iterations.jsonl tail does not match the newest ledger "
            "record (rotated or concurrently modified ledger): refusing to retract"
        )

    retractions_path = run_dir / RETRACTIONS_FILENAME
    prior_attempts = 0
    if retractions_path.exists():
        for line in _read_regular_text(retractions_path).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(entry, dict)
                and entry.get("action") == "retract"
                and entry.get("iteration") == number
            ):
                prior_attempts += 1
    if prior_attempts >= RETRACTIONS_PER_ITERATION_CAP:
        raise ValueError(
            f"iteration {number} has already been retracted "
            f"{RETRACTIONS_PER_ITERATION_CAP} times: the cap protects the audit "
            "trail from append/retract churn (fix the record content instead)"
        )

    now = utc_now()
    snapshots = {
        name: (paths[name].read_bytes() if paths[name].exists() else None)
        for name in ("state", "budget", "iterations", "recovery")
    }
    # The full removed record is audited BEFORE the mutation so a crash midway
    # can never lose it; a failed retraction appends a rollback entry after it.
    append_jsonl(
        retractions_path,
        {
            "schema_version": SCHEMA_VERSION,
            "action": "retract",
            "retracted_at": now,
            "iteration": number,
            "reason": reason,
            "record": record,
        },
    )

    prev_record = iterations[-2] if len(iterations) > 1 else None
    new_count = len(iterations) - 1
    delta = record.get("budget_delta") or {}
    try:
        paths["iterations"].parent.mkdir(parents=True, exist_ok=True)
        with paths["iterations"].open("w", encoding="utf-8", newline="") as handle:
            handle.write("".join(lines[:last_index]))
        state["last_iteration"] = (
            int(prev_record.get("iteration") or new_count) if prev_record else 0
        )
        state["status"] = "running" if prev_record else "initialized"
        state["updated_at"] = now
        budget["spent_iterations"] = new_count
        budget["spent_tokens"] = max(
            0, int(budget.get("spent_tokens", 0)) - int(delta.get("tokens") or 0)
        )
        budget["spent_usd"] = max(
            0.0, float(budget.get("spent_usd", 0.0)) - float(delta.get("usd") or 0.0)
        )
        budget["updated_at"] = now
        write_json(paths["state"], state)
        write_json(paths["budget"], budget)
        prev_gaps = list((prev_record or {}).get("remaining_gaps") or [])
        remaining_iterations = max(
            0, int(budget.get("max_iterations", 0)) - new_count
        )
        with paths["recovery"].open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                "\n".join(
                    [
                        "# Autonomous Research Loop Recovery",
                        "",
                        f"- Goal: {state.get('goal', '')}",
                        f"- Status: {state.get('status', '')}",
                        f"- Last completed iteration: {new_count}",
                        "- Next safe action: "
                        + (
                            "continue from the last recorded decision"
                            if prev_record
                            else "start the first bounded iteration"
                        ),
                        "- Remaining evidence gaps: "
                        + (
                            ", ".join(prev_gaps)
                            if prev_gaps
                            else ("none recorded" if prev_record else "not yet assessed")
                        ),
                        "- Active blockers: none recorded",
                        f"- Budget remaining: {remaining_iterations} iterations",
                        "",
                    ]
                )
            )
        validation_errors = validate_loop_dir(run_dir)["errors"]
        if validation_errors:
            raise ValueError(
                "loop did not validate after retraction: "
                + "; ".join(validation_errors)
            )
    except Exception as exc:
        for name, payload in snapshots.items():
            try:
                if payload is None:
                    paths[name].unlink(missing_ok=True)
                else:
                    paths[name].write_bytes(payload)
            except OSError:
                pass
        append_jsonl(
            retractions_path,
            {
                "schema_version": SCHEMA_VERSION,
                "action": "rollback",
                "rolled_back_at": utc_now(),
                "iteration": number,
                "error": str(exc),
            },
        )
        raise
    return {
        "status": "ok",
        "action": "retract-iteration",
        "dir": str(run_dir),
        "iteration": number,
        "reason": reason,
        "retracted_record": record,
        "restored": {
            "last_iteration": state["last_iteration"],
            "loop_status": state["status"],
            "spent_iterations": new_count,
        },
        "audit": str(retractions_path),
        "validation_errors": [],
    }


def status_command(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    validation = validate_loop_dir(run_dir)
    paths = loop_paths(run_dir)
    iterations = read_iterations(paths["iterations"]) if paths["iterations"].exists() else []
    state = read_json(paths["state"]) if paths["state"].exists() else {}
    budget = read_json(paths["budget"]) if paths["budget"].exists() else {}
    last = iterations[-1] if iterations else {}
    return {
        "status": validation["status"],
        "dir": str(run_dir),
        "state_status": state.get("status"),
        "iterations": len(iterations),
        "last_decision": last.get("decision"),
        "remaining_iterations": max(
            0,
            int(budget.get("max_iterations", 0)) - int(budget.get("spent_iterations", 0)),
        )
        if budget
        else None,
        "validation": validation,
    }


def selftest_init_args(run_dir: Path, max_iterations: int) -> argparse.Namespace:
    return argparse.Namespace(
        dir=str(run_dir),
        goal="offline smoke test",
        success_criteria="ledger validates after one iteration",
        mode="bounded-research",
        force=False,
        stop_on_guard_fail=True,
        stop_on_missing_evidence=True,
        stop_on_scope_change=True,
        plateau_rule=DEFAULT_PLATEAU_RULE,
        success_check="",
        require_user_stop_only=False,
        stop_condition=[],
        budget_owner="selftest",
        max_iterations=max_iterations,
        max_wall_time_seconds=300,
        max_tokens=0,
        max_usd=0.0,
        max_depth=1,
        max_hops=1,
        max_child_workers=0,
        goal_priority_template=False,
        goal_focus_mode="off",
        formal_policy=None,
        formal_project=None,
        formal_force_credits=None,
        formal_allow_path_steal=False,
        formal_typecheck=False,
        formal_force_after_iteration=False,
    )


def selftest_drive_args(run_dir: Path, registry: Path, stub_cmd: str) -> argparse.Namespace:
    return argparse.Namespace(
        dir=str(run_dir),
        root=str(run_dir),
        cmd=stub_cmd,
        provider=None,
        iteration_timeout=60,
        max_failures=1,
        poll=0.0,
        quota_backoff=0,
        max_quota_waits=3,
        log_dir=None,
        notify="off",
        notify_cmd=None,
        no_progress=False,
        registry_dir=str(registry),
        panel="off",
        formal_policy=None,
        formal_project=None,
        formal_force_credits=None,
        formal_allow_path_steal=False,
        formal_typecheck=False,
        formal_force_after_iteration=False,
    )


def _formal_cli_from_args(args: argparse.Namespace) -> dict[str, Any]:
    """Build formal_policy CLI overlay from argparse Namespace (drive/init)."""
    cli: dict[str, Any] = {}
    if getattr(args, "formal_policy", None) is not None:
        cli["policy"] = str(args.formal_policy)
    if getattr(args, "formal_project", None):
        cli["project"] = str(args.formal_project)
    if getattr(args, "formal_force_credits", None) is not None:
        cli["force_credits"] = int(args.formal_force_credits)
    if bool(getattr(args, "formal_allow_path_steal", False)):
        cli["allow_path_steal"] = True
    if bool(getattr(args, "formal_typecheck", False)):
        cli["typecheck"] = True
    if bool(getattr(args, "formal_force_after_iteration", False)):
        cli["force_after_iteration"] = True
    return cli


def _apply_formal_drive_start(
    run_dir: Path,
    args: argparse.Namespace,
) -> tuple[Any, dict[str, Any]]:
    """Resolve formal policy at drive start: pin, persist, export env. Never raises."""
    try:
        formal_cli = _formal_cli_from_args(args)
        pol = load_formal_policy(run_dir, cli=formal_cli or None)
        pin = pin_privileged_policy(pol)
        # The root belongs in the pin for the same reason the project does: a
        # later host process re-checking this verdict has to resolve the Lake
        # project the way the drive did, and it has no other record of where.
        pin["root"] = str(
            Path(args.root).expanduser().resolve()
            if getattr(args, "root", None)
            else Path(run_dir)
        )
        write_host_pin(run_dir, pin)
        # The track the host is starting on, read before the agent runs. Every
        # later reading ORs this in, so an agent that rewrites the committed
        # path mid-iteration cannot shed the formal terminal-state requirement
        # its own Lean work incurred. Pin the derived reading rather than the
        # combined one: refreshing beats latching, so a run that legitimately
        # leaves the formal track stops carrying the requirement.
        write_track_pin(
            run_dir,
            formal_track=formal_track_status(run_dir).derived,
            source="drive_start",
        )
        # Persist when host explicitly set CLI/env or non-off policy so nested tools see it.
        env_set = any(
            os.environ.get(k)
            for k in (
                "AAS_AUTOLOOP_FORMAL_POLICY",
                "AAS_AUTOLOOP_FORMAL_PROJECT",
                "AAS_AUTOLOOP_FORMAL_FORCE",
                "AAS_AUTOLOOP_FORMAL_TYPECHECK",
            )
        )
        if formal_cli or env_set or pol.policy != "off":
            merge_standing_orders_formal(
                run_dir,
                updates={
                    "policy": pol.policy,
                    "project": pol.project,
                    "force_credits": pol.force_credits,
                    "allow_path_steal": pol.allow_path_steal,
                    "typecheck": pol.typecheck,
                    "force_after_iteration": pol.force_after_iteration,
                    "allow_create_skeleton": pol.allow_create_skeleton,
                },
            )
            # Mirror for operators (best-effort)
            try:
                formal_dir = run_dir / "formal"
                formal_dir.mkdir(parents=True, exist_ok=True)
                mirror_path = formal_dir / "formal_policy.json"
                if not mirror_path.is_file() or formal_cli or env_set:
                    with mirror_path.open("w", encoding="utf-8", newline="\n") as handle:
                        handle.write(json.dumps(pol.as_dict(), indent=2) + "\n")
            except OSError:
                pass
        for key, value in export_formal_env(pol).items():
            os.environ[key] = value
        return pol, pin
    except Exception:  # noqa: BLE001
        return None, {}


STUB_ITERATION_SNIPPET = (
    "import json, os, sys\n"
    "run_dir = os.environ['AUTOLOOP_DIR']\n"
    "marker = os.path.join(run_dir, 'quota_marker')\n"
    "if '--quota-first' in sys.argv and not os.path.exists(marker):\n"
    "    open(marker, 'w').write('seen')\n"
    "    print('provider error: HTTP 429 Too Many Requests')\n"
    "    sys.exit(1)\n"
    "if '--auth-fail' in sys.argv:\n"
    "    print('ERROR: refresh_token_invalidated')\n"
    "    print('401 Unauthorized: Please try signing in again.')\n"
    "    sys.exit(1)\n"
    "if '--weekly-limit' in sys.argv:\n"
    "    print(\"You've hit your weekly limit · resets 4am (Asia/Ho_Chi_Minh)\")\n"
    "    sys.exit(1)\n"
    "if '--generic-fail' in sys.argv:\n"
    "    print('tool crashed: assertion failed')\n"
    "    sys.exit(1)\n"
    "budget_path = os.path.join(run_dir, 'budget.json')\n"
    "budget = json.load(open(budget_path))\n"
    "number = int(budget.get('spent_iterations', 0)) + 1\n"
    "budget['spent_iterations'] = number\n"
    "json.dump(budget, open(budget_path, 'w'))\n"
    "record = {'iteration': number, 'decision': 'continue',\n"
    "          'summary': 'stub iteration', 'evidence_ids': []}\n"
    "with open(os.path.join(run_dir, 'iterations.jsonl'), 'a') as handle:\n"
    "    handle.write(json.dumps(record) + '\\n')\n"
    "state_path = os.path.join(run_dir, 'loop_state.json')\n"
    "state = json.load(open(state_path))\n"
    "state['last_iteration'] = number\n"
    "json.dump(state, open(state_path, 'w'))\n"
    "print('stub iteration complete')\n"
)
GROK_PROFILE_STATUS_SCHEMA = "grok-remote.profile-status.v1"
GROK_PROFILE_STATUS_FIELDS = {
    "schema_version",
    "status",
    "profile_name",
    "profile_sha256",
    "release_id",
    "grok_release_id",
    "model_id",
    "eligible_rungs",
    "missing_rungs",
    "reason_code",
}
GROK_PROFILE_READY_STATUSES = {"ready", "degraded"}
GROK_PROFILE_BLOCKED_STATUSES = {"blocked", "unconfigured"}
GROK_PROFILE_HELP_TOKEN = "grok-remote doctor --json"
GROK_PROFILE_NAME = "default"
GROK_PROFILE_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
GROK_PROFILE_GROK_RELEASE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
GROK_PROFILE_RELEASE_RE = re.compile(r"^[A-Za-z0-9._:+/@-]{1,128}$")
GROK_PROFILE_RUNG_RE = re.compile(
    r"^(?:direct|vpn|home:[A-Za-z0-9._:+@-]+|ios:[a-z0-9][a-z0-9._-]{0,63})$"
)
GROK_PROFILE_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
GROK_PROFILE_STATUS_REASONS = {
    "ready": {"ready"},
    "degraded": {"ready_with_missing_optional_rungs"},
    "blocked": {
        "active_profile_invalid",
        "minimum_eligible_rungs_not_met",
        "release_evidence_invalid",
        "required_rungs_missing",
    },
    "unconfigured": {"no_active_profile"},
}
GROK_PROFILE_BOUND_BLOCKED_REASONS = {
    "minimum_eligible_rungs_not_met",
    "required_rungs_missing",
}
GROK_PROFILE_REDACTED_BLOCKED_REASONS = {
    "active_profile_invalid",
    "release_evidence_invalid",
}


def provider_subprocess_options(provider: str | None) -> dict[str, int]:
    """Return provider-scoped subprocess hardening without changing Windows."""
    if provider == "grok" and os.name == "posix":
        return {"umask": 0o077}
    return {}


def _posix_process_group_exists(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_posix_process_group(
    group_id: int, leader: subprocess.Popen[Any] | None = None
) -> str | None:
    """Terminate and observe disappearance of one isolated process group."""

    if not _posix_process_group_exists(group_id):
        return None
    try:
        os.killpg(group_id, signal.SIGTERM)
    except ProcessLookupError:
        return None
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if leader is not None:
            leader.poll()
        if not _posix_process_group_exists(group_id):
            return None
        time.sleep(0.02)
    try:
        os.killpg(group_id, signal.SIGKILL)
    except ProcessLookupError:
        return None
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if leader is not None:
            leader.poll()
        if not _posix_process_group_exists(group_id):
            return None
        time.sleep(0.02)
    return f"primary process group {group_id} survived SIGKILL"


def _create_windows_kill_on_close_job() -> tuple[Any, Callable[[], None]]:
    """Create a Windows Job Object whose close kills every assigned process."""

    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_job = kernel32.CreateJobObjectW
    create_job.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    create_job.restype = wintypes.HANDLE
    set_information = kernel32.SetInformationJobObject
    set_information.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    set_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    job = create_job(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
    if not set_information(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.get_last_error()
        close_handle(job)
        raise OSError(error, "SetInformationJobObject failed")

    def close() -> None:
        if not close_handle(job):
            raise OSError(ctypes.get_last_error(), "CloseHandle(job) failed")

    return (job, close)


def _assign_windows_job(job: Any, process: subprocess.Popen[Any]) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    assign = kernel32.AssignProcessToJobObject
    assign.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    assign.restype = wintypes.BOOL
    if not assign(job, wintypes.HANDLE(int(process._handle))):  # type: ignore[attr-defined]
        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")


def prepare_primary_private_prompt_transport(
    provider: str, run_args: list[str], prompt: str
) -> tuple[list[str], str]:
    """Remove exact primary prompt bytes from argv and deliver once on stdin."""

    if not prompt:
        raise ValueError("primary prompt must be non-empty")
    matches = [index for index, value in enumerate(run_args) if value == prompt]
    if len(matches) != 1 or any(
        prompt in value
        for index, value in enumerate(run_args)
        if index not in matches
    ):
        raise ValueError(
            "primary prompt is not isolated as one exact command argument"
        )
    index = matches[0]
    if provider == "claude":
        secured = [*run_args[:index], *run_args[index + 1 :]]
    elif provider == "codex":
        secured = [*run_args]
        secured[index] = "-"
    elif provider == "grok":
        # Host-proved: grok -p does not read stdin; --prompt-file /dev/stdin does
        # (same shape as cross-agent delegation dispatch).
        if index > 0 and run_args[index - 1] in {"-p", "--single"}:
            secured = [
                *run_args[: index - 1],
                "--prompt-file",
                "/dev/stdin",
                *run_args[index + 1 :],
            ]
        else:
            secured = [
                *run_args[:index],
                "--prompt-file",
                "/dev/stdin",
                *run_args[index + 1 :],
            ]
    else:
        raise ValueError(
            f"provider {provider} has no verified non-argv primary prompt transport"
        )
    if any(prompt in value for value in secured):
        raise ValueError("primary prompt remains visible in command argv")
    return secured, prompt


def run_primary_subprocess(
    run_args: list[str] | str,
    *,
    use_shell: bool,
    child_env: Mapping[str, str],
    cwd: Path,
    timeout_s: int,
    output: Any,
    provider: str | None,
    enforce_mode: bool = False,
    trusted_local: bool = False,
    run_dir: Path | None = None,
    evidence_dir: Path | None = None,
    executable_attestation: Mapping[str, Any] | None = None,
    stdin_text: str | None = None,
    resource_metadata: dict[str, Any] | None = None,
) -> tuple[int, bool, str | None]:
    """Run one primary inside a kernel-owned descendant lifetime boundary.

    Linux uses a fresh PID namespace, so daemonization or ``setsid`` cannot
    escape the namespace lifetime. Windows uses a kill-on-close Job Object.
    Other POSIX platforms fail closed because process groups alone are not a
    security boundary against a deliberately daemonizing child.
    """

    options: dict[str, Any] = dict(provider_subprocess_options(provider))
    windows_job: Any | None = None
    close_windows_job: Callable[[], None] | None = None
    credential_vault: Path | None = None
    resource_scope: str | None = None
    resource_limits: dict[str, int] = {}
    execution_env = dict(child_env)
    if os.name == "posix":
        if not sys.platform.startswith("linux"):
            raise OSError(
                "primary descendant containment requires Linux PID namespaces"
            )
        bwrap = next(
            (
                candidate
                for candidate in (Path("/usr/bin/bwrap"), Path("/bin/bwrap"))
                if candidate.is_file()
                and os.access(candidate, os.X_OK)
                and not stat.S_ISLNK(os.lstat(candidate).st_mode)
                and os.lstat(candidate).st_uid == 0
                and not os.lstat(candidate).st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            ),
            None,
        )
        if bwrap is None:
            raise OSError(
                "primary descendant containment requires a trusted bubblewrap binary"
            )
        inner_args = (
            ["/bin/sh", "-c", str(run_args)]
            if use_shell
            else [str(item) for item in run_args]
        )
        sandbox_args = [
            str(bwrap),
            "--die-with-parent",
            "--new-session",
            "--unshare-ipc",
            "--unshare-pid",
        ]
        if enforce_mode and not trusted_local:
            raise OSError(
                "Goal-Focus enforce primary execution is fail-closed: no "
                "credential-blind, prompt-private, allowlist-filesystem, "
                "resource-bounded model transport with constrained egress is "
                "available"
            )
        elif trusted_local:
            try:
                inner_args = interpreter_bound_provider_command(inner_args)
                strict_brokered = execution_env.pop(
                    "AAS_ARL_BROKER_STRICT_FS", ""
                ) == "1"
                if strict_brokered:
                    dependency_root = Path(
                        execution_env.pop(
                            "AAS_ARL_BROKER_DEPENDENCY_ROOT", ""
                        )
                    )
                    home = Path(str(execution_env.get("HOME") or ""))
                    raw_mounts = execution_env.pop(
                        "AAS_ARL_BROKER_CONFIG_MOUNTS", "{}"
                    )
                    parsed_mounts = json.loads(raw_mounts)
                    if not isinstance(parsed_mounts, dict) or not all(
                        isinstance(key, str) and isinstance(value, str)
                        for key, value in parsed_mounts.items()
                    ):
                        raise ProviderResourceError(
                            "brokered provider config mounts are invalid"
                        )
                    broker_socket_text = str(
                        execution_env.get("AAS_ARL_BROKER_SOCKET") or ""
                    )
                    run_args = brokered_provider_containment_command(
                        inner_args,
                        cwd=cwd,
                        dependency_root=dependency_root,
                        synthetic_home=home,
                        config_mounts=parsed_mounts,
                        broker_socket=Path(broker_socket_text)
                        if broker_socket_text
                        else None,
                    )
                else:
                    run_args = trusted_local_containment_command(
                        inner_args,
                        cwd=cwd,
                    )
            except ProviderResourceError as exc:
                raise OSError(str(exc)) from exc
        else:
            # Monitor/legacy runs preserve their historical writable view while
            # trusted-local enforce runs explicitly accept the same host view.
            # Both retain the PID-namespace descendant-lifetime boundary.
            masked = containment_hidden_root(
                Path(cwd).resolve(), CONTAINMENT_TAIL_MOUNT_MASKS
            )
            if masked is not None:
                raise OSError(
                    "primary working directory is hidden by the containment mount "
                    f"mask {masked}"
                )
            sandbox_args.extend(
                [
                    "--bind",
                    "/",
                    "/",
                    "--proc",
                    "/proc",
                    "--dev",
                    "/dev",
                ]
            )
            run_args = [
                *sandbox_args,
                "--chdir",
                str(cwd),
                "--",
                *inner_args,
            ]
        if trusted_local:
            try:
                run_args, resource_limits, resource_scope = resource_limited_command(
                    run_args,
                    timeout_s,
                    role="primary",
                )
                for temp_name in ("TMPDIR", "TMP", "TEMP"):
                    execution_env[temp_name] = "/tmp"
                execution_env = resource_control_environment(execution_env)
            except ProviderResourceError as exc:
                raise OSError(str(exc)) from exc
            if resource_metadata is not None:
                resource_metadata.clear()
                resource_metadata.update(
                    {
                        "schema_version": "provider_resource_attestation.v1",
                        "provider_transport": TRUSTED_LOCAL_TRANSPORT,
                        "role": "primary",
                        "scope_unit": resource_scope,
                        "limits": public_resource_limits(resource_limits),
                        "resource_gate": "pre-exec-cgroup-rlimit-v1",
                        "output_capture": "bounded-pipe",
                        "control_plane_masked": True,
                        "cgroup_api_masked": True,
                        "cleanup_verified": False,
                        "capture_verified": False,
                        "timed_out": False,
                        "oversized_output": False,
                        "sensitive_output_blocked": False,
                    }
                )
        use_shell = False
        options["start_new_session"] = True
    elif os.name == "nt":  # pragma: no cover - exercised by Windows CI
        if enforce_mode:
            raise OSError(
                "Goal-Focus enforce primary integrity isolation requires Linux bubblewrap"
            )
        windows_job, close_windows_job = _create_windows_kill_on_close_job()
        options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if trusted_local:
        assert resource_scope is not None
        try:
            bounded = run_bounded_resource_process(
                [str(item) for item in run_args],
                env=execution_env,
                cwd=cwd,
                timeout_s=timeout_s,
                output_limit_bytes=resource_limits["output_max_bytes"],
                scope_unit=resource_scope,
                stdin_text=stdin_text,
                merge_stderr=True,
            )
        except Exception as exc:
            prior_cleanup_error = getattr(exc, "cleanup_error", None)
            retry_cleanup_error = cleanup_resource_scope(resource_scope)
            cleanup_result = prior_cleanup_error or retry_cleanup_error
            if resource_metadata is not None:
                resource_metadata["cleanup_verified"] = cleanup_result is None
                resource_metadata["finished_at"] = utc_now()
            detail = (
                ": resource cleanup was not verified"
                if cleanup_result is not None
                else ""
            )
            raise OSError(f"trusted-local provider execution failed{detail}") from exc
        if resource_metadata is not None:
            resource_metadata["cleanup_verified"] = bounded.cleanup_error is None
            resource_metadata["timed_out"] = bounded.timed_out
            resource_metadata["oversized_output"] = bounded.oversized
            resource_metadata["capture_verified"] = bounded.capture_error is None
            resource_metadata["finished_at"] = utc_now()
        if bounded.oversized:
            return_code = 126
            output_text = (
                "primary output was blocked before persistence because it was oversized\n"
            )
        elif bounded.capture_error is not None:
            return_code = 126
            output_text = (
                "primary output was blocked because prompt delivery or output "
                "capture was incomplete\n"
            )
        else:
            return_code = bounded.return_code
            output_text = bounded.stdout.decode("utf-8", errors="replace")
            sensitive_findings = panel_payload_sensitive_findings(output_text)
            if sensitive_findings:
                if resource_metadata is not None:
                    resource_metadata["sensitive_output_blocked"] = True
                return_code = 126
                output_text = (
                    "primary output was blocked before persistence because it contained "
                    "sensitive data categories: "
                    + ", ".join(sensitive_findings)
                    + "\n"
                )
        output.write(output_text)
        output.flush()
        if bounded.cleanup_error is not None:
            return 126, bounded.timed_out, bounded.cleanup_error
        return return_code, bounded.timed_out, None
    # Allocate capture storage only after every fail-closed preflight.  In
    # particular, blocked enforce-mode calls must not retain a descriptor until
    # cyclic/implementation-specific garbage collection happens.
    private_output = tempfile.TemporaryFile(mode="w+b")
    try:
        process = subprocess.Popen(
            run_args,
            shell=use_shell,
            env=execution_env,
            cwd=str(cwd),
            stdout=private_output,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if stdin_text is not None else None,
            **options,
        )
    except Exception:
        private_output.close()
        cleanup_provider_sandbox_vault(credential_vault)
        if resource_scope is not None:
            cleanup_resource_scope(resource_scope)
        if close_windows_job is not None:  # pragma: no cover - Windows CI
            close_windows_job()
        raise
    if windows_job is not None:
        try:
            _assign_windows_job(windows_job, process)
        except Exception:
            process.kill()
            process.wait(timeout=5)
            assert close_windows_job is not None
            close_windows_job()
            raise
    timed_out = False
    cleanup_error: str | None = None
    try:
        if stdin_text is None:
            return_code = process.wait(timeout=timeout_s)
        else:
            process.communicate(
                input=stdin_text.encode("utf-8"), timeout=timeout_s
            )
            return_code = int(process.returncode or 0)
    except subprocess.TimeoutExpired:
        timed_out = True
        return_code = 124
    finally:
        if os.name == "posix":
            cleanup_error = _terminate_posix_process_group(process.pid, process)
            if process.poll() is None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    cleanup_error = cleanup_error or (
                        f"primary process {process.pid} did not exit after group termination"
                    )
        elif close_windows_job is not None:  # pragma: no cover - Windows CI
            try:
                close_windows_job()
            except OSError as exc:
                cleanup_error = str(exc)
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    cleanup_error = cleanup_error or "Windows Job Object cleanup timed out"
        if resource_scope is not None:
            scope_cleanup_error = cleanup_resource_scope(resource_scope)
            cleanup_error = cleanup_error or scope_cleanup_error
        cleanup_provider_sandbox_vault(credential_vault)
    private_output.flush()
    private_output.seek(0)
    output_limit = int(resource_limits.get("output_max_bytes") or 16_000_000)
    output_bytes = private_output.read(output_limit + 1)
    private_output.close()
    if len(output_bytes) > output_limit:
        return_code = 126
        output_text = "primary output was blocked before persistence because it was oversized\n"
    else:
        output_text = output_bytes.decode("utf-8", errors="replace")
        sensitive_findings = panel_payload_sensitive_findings(output_text)
        if sensitive_findings:
            return_code = 126
            output_text = (
                "primary output was blocked before persistence because it contained "
                "sensitive data categories: "
                + ", ".join(sensitive_findings)
                + "\n"
            )
    output.write(output_text)
    output.flush()
    if cleanup_error is not None:
        return 126, timed_out, cleanup_error
    return return_code, timed_out, None


def _containment_scratch_base() -> str | None:
    """Keep selftest scratch off mounts the containment boundary masks.

    ``TMPDIR`` is inherited, so an operator whose temp directory sits on a
    masked mount would otherwise see the probe fail and the gate blame the
    host.  ``None`` means the platform default is already usable.
    """

    if os.name != "posix":
        return None
    # Same precedence tempfile itself uses, read live rather than through the
    # cached ``gettempdir`` so a caller that sets TMPDIR is still honoured.
    override = next(
        (os.environ[name] for name in ("TMPDIR", "TEMP", "TMP") if os.environ.get(name)),
        None,
    )
    try:
        default = Path(override or tempfile.gettempdir()).resolve()
    except OSError:
        return "/tmp"
    roots = containment_mask_roots_for_host()
    if containment_hidden_root(default, roots) is None:
        return None
    return "/tmp"


def host_primary_containment_status() -> tuple[bool, str]:
    """Report whether this host can launch a contained primary, and why not.

    Containment is a precondition of every iteration, not a feature of one:
    Linux needs a working bubblewrap PID namespace, Windows needs a Job Object,
    and the remaining POSIX platforms are refused outright.  A host that cannot
    contain a primary cannot run the driver at all, which is a host property
    rather than a driver defect, so the offline selftest separates the two.
    """

    scratch_base = _containment_scratch_base()
    if scratch_base is not None:
        masked = containment_hidden_root(
            Path(scratch_base).resolve(), containment_mask_roots_for_host()
        )
        if masked is not None:
            # Not a host verdict: the scratch base is, so say so rather than
            # declaring the host unable to contain anything.
            return (
                False,
                "selftest scratch base is hidden by the containment mount "
                f"masks: {scratch_base}",
            )
    with tempfile.TemporaryDirectory(
        prefix="autoloop-contain-probe-", dir=scratch_base
    ) as tmp:
        probe_dir = Path(tmp).resolve()
        log_path = probe_dir / "probe.log"
        try:
            with log_path.open("w", encoding="utf-8", newline="\n") as handle:
                return_code, timed_out, _cleanup_error = run_primary_subprocess(
                    [sys.executable, "-c", "pass"],
                    use_shell=False,
                    child_env=dict(os.environ),
                    cwd=probe_dir,
                    timeout_s=60,
                    output=handle,
                    provider=None,
                )
        except OSError as exc:
            return False, str(exc)
        if timed_out:
            return False, "containment probe timed out"
        if return_code != 0:
            detail = " ".join(
                log_path.read_text(encoding="utf-8", errors="replace").split()
            )
            return False, f"containment probe exited {return_code}: {detail[:200]}"
    return True, "host containment is available"


def selftest_driver_checks() -> dict[str, Any]:
    """Offline checks for the provider adapters and the headless driver. Uses
    only stub commands (this Python interpreter); no provider CLI is invoked."""
    errors: list[str] = []
    providers_checked = 0
    drive_checks = "ran"
    drive_skip_reason = ""
    with tempfile.TemporaryDirectory(
        prefix="autoloop-driver-smoke-", dir=_containment_scratch_base()
    ) as tmp:
        # Every loop built below goes through ``init_loop``, which refuses a
        # path that crosses a symlink.  The default temp directory is one on
        # macOS, where ``/var`` links to ``/private/var``, so resolve the
        # scratch root and keep the guard pointed at real callers.
        base = Path(tmp).resolve()
        # 1. Provider command construction: every provider builds an argv with
        # the prompt substituted; a stubbed binary override must be honored and
        # reported as not found without consulting PATH defaults.
        for provider in sorted(PROVIDER_SPECS):
            key = provider_env_key(provider)
            environ = {f"AAS_AUTOLOOP_BIN_{key}": str(base / "missing-bin")}
            spec = resolve_provider_command(provider, base / "loop", environ=environ)
            providers_checked += 1
            if spec["mode"] != "argv" or spec["binary_found"]:
                errors.append(f"{provider}: expected argv mode with missing binary")
                continue
            joined = " ".join(spec["argv"])
            if "{prompt}" in joined or "exactly ONE iteration" not in joined:
                errors.append(f"{provider}: prompt placeholder not substituted")
            if str(base / "loop") not in joined:
                errors.append(f"{provider}: loop dir missing from command")
        # Full-command override: {dir} substituted, mode shell.
        override_env = {"AAS_AUTOLOOP_CMD_CLAUDE": "echo {dir}"}
        spec = resolve_provider_command("claude", base / "loop", environ=override_env)
        if spec["mode"] != "shell" or str(base / "loop") not in spec["shell"]:
            errors.append("claude: AAS_AUTOLOOP_CMD override not honored")
        # 2. Quota-signal detection (provider-shaped; no bare "quota").
        for text in (
            "HTTP 429 Too Many Requests",
            "insufficient credit balance",
            "usage limit reached, resets 5pm",
            "You have run out of credits",
            "rate limit exceeded",
            "quota exceeded for this account",
            "You've hit your weekly limit · resets 4am (Asia/Ho_Chi_Minh)",
            "You have hit your weekly limit",
            "API error (status 402 Payment Required): Grok Build usage balance exhausted",
        ):
            if not QUOTA_PATTERN.search(text):
                errors.append(f"quota pattern missed: {text!r}")
        if QUOTA_PATTERN.search("all checks passed cleanly"):
            errors.append("quota pattern false-positive on benign text")
        # Bare "quota" alone (as in the host prompt) must not match.
        if QUOTA_PATTERN.search("If you hit a credit or quota error, exit nonzero"):
            errors.append("quota pattern false-positive on host prompt phrase")
        weekly = (
            "You've hit your weekly limit · resets 4am (Asia/Ho_Chi_Minh)\n"
        )
        if classify_iteration_failure(weekly, prompt="") != "quota":
            errors.append("weekly limit phrase did not classify as quota")
        # 2b. Classification: AUTH before QUOTA; prompt dual-match → auth.
        host_prompt = (
            "You are one iteration of a bounded autonomous research loop. "
            "If you hit a credit or quota error, exit nonzero with the provider's error text."
        )
        auth_body = (
            "ERROR codex: 401 Unauthorized: Your authentication token has been "
            "invalidated. refresh_token_invalidated. Please try signing in again."
        )
        dual = host_prompt + "\n" + HOST_PROMPT_SENTINEL + "\n" + auth_body
        if classify_iteration_failure(dual, prompt=host_prompt) != "auth":
            errors.append("dual-match prompt+auth did not classify as auth")
        if classify_iteration_failure(
            host_prompt + "\n" + HOST_PROMPT_SENTINEL + "\ntool crashed",
            prompt=host_prompt,
        ) == "quota":
            errors.append("prompt-only residual classified as quota")
        skill_dump = (
            host_prompt
            + "\n"
            + HOST_PROMPT_SENTINEL
            + "\n"
            + "Skill says: Credit/quota outages (rate limit, 429, out of credits, "
            "usage limit, billing) detected in a FAILED iteration's output.\n"
            + "actual error: TypeError: bad operand\n"
        )
        # After strip, skill dump may still match strong signals; require that a
        # pure skill-prose line without HTTP/provider shape is not enough alone
        # when combined with an obvious non-quota crash and no 429 line.
        # Prefer classify as failure when crash is present and no real 429 line.
        if classify_iteration_failure(
            host_prompt + "\n" + HOST_PROMPT_SENTINEL + "\nTypeError: boom\n",
            prompt=host_prompt,
        ) != "failure":
            errors.append("generic fail after prompt strip not failure")
        if AUTH_PATTERN.search("401 total cells examined"):
            # bare 401 without Unauthorized must not match (no \b401\b alone)
            pass
        if classify_iteration_failure(
            "401 total polarities examined\nno auth issue\n", prompt=""
        ) == "auth":
            errors.append("weak 401 count classified as auth")
        # 2c. Notify identity: banned generic dir + goal → research title.
        loop_notify = base / "research_loop"
        loop_notify.mkdir(parents=True, exist_ok=True)
        init_loop(
            selftest_init_args(loop_notify, max_iterations=1)
        )
        # Override goal for title derivation.
        try:
            st = read_json(loop_notify / "loop_state.json")
            st["goal"] = (
                "Characterize TS_k(H) acyclicity for finite simple H and k >= 3."
            )
            write_json(loop_notify / "loop_state.json", st)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"notify identity fixture setup failed: {exc}")
        else:
            ident = resolve_loop_notify_identity(loop_notify)
            if ident.get("title", "").lower() in {
                "research_loop",
                "loop",
                "research",
            }:
                errors.append(
                    f"notify title stayed generic for research_loop dir: {ident}"
                )
            if ident.get("slug", "").lower() in {"research-loop", "loop", "research"}:
                errors.append(
                    f"notify slug stayed generic for research_loop dir: {ident}"
                )
            (loop_notify / "failover.json").write_text(
                json.dumps({"research_title": "Explicit Title Only"}),
                encoding="utf-8",
            )
            ident2 = resolve_loop_notify_identity(loop_notify)
            if ident2.get("title") != "Explicit Title Only":
                errors.append(f"explicit research_title not preferred: {ident2}")
        contained, containment_reason = host_primary_containment_status()
        if contained:
            errors.extend(selftest_drive_loop_checks(base))
        else:
            drive_checks = "skipped"
            drive_skip_reason = containment_reason
            print(
                "autoloop-selftest: driver drive checks skipped, no contained "
                f"primary available: {containment_reason}",
                file=sys.stderr,
            )
    return {
        "ok": not errors,
        "errors": errors,
        "providers_checked": providers_checked,
        "provider_cli_attempted": False,
        "drive_checks": drive_checks,
        "drive_skip_reason": drive_skip_reason,
    }


def selftest_drive_loop_checks(base: Path) -> list[str]:
    """Drive the headless loop on stub commands and report contract breaks.

    Every iteration runs inside the platform containment boundary, so these
    checks require a host that can contain a primary.
    """

    errors: list[str] = []
    # 3. Drive to completion on a stub command (budget cap = 2 iterations).
    loop_a = base / "loop-a"
    init_loop(selftest_init_args(loop_a, max_iterations=2))
    stub = base / "stub_iteration.py"
    # Path.write_text lacks the newline argument before Python 3.10.
    with stub.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(STUB_ITERATION_SNIPPET)
    stub_cmd = f'"{sys.executable}" "{stub}"'
    result = drive_command(selftest_drive_args(loop_a, base / "reg", stub_cmd))
    budget_a = read_json(loop_a / "budget.json")
    if (
        result.get("reason") != "done"
        or result.get("exit_code") != 0
        or int(budget_a.get("spent_iterations", 0)) != 2
        or result.get("iterations_run") != 2
    ):
        errors.append(f"drive stub run did not complete cleanly: {result}")
    elif not list((loop_a / "driver_logs").glob("iter_*.log")):
        errors.append("drive stub run left no iteration logs")
    # 4. Quota pause-and-resume: first stub call fails with a 429 signal and
    # must be waited out (not counted as a failure with max_failures=1),
    # the second call succeeds and the budget cap ends the loop.
    loop_b = base / "loop-b"
    init_loop(selftest_init_args(loop_b, max_iterations=1))
    quota_cmd = f'"{sys.executable}" "{stub}" --quota-first'
    # Short backoff so selftest stays fast.
    result_b = drive_command(
        selftest_drive_args(loop_b, base / "reg", quota_cmd)
    )
    if (
        result_b.get("reason") != "done"
        or result_b.get("quota_waits_total") != 1
        or int(read_json(loop_b / "budget.json").get("spent_iterations", 0)) != 1
    ):
        errors.append(f"drive quota pause-and-resume misbehaved: {result_b}")
    # 5. Auth fail → exit 7, no quota waits.
    loop_c = base / "loop-c"
    init_loop(selftest_init_args(loop_c, max_iterations=3))
    auth_cmd = f'"{sys.executable}" "{stub}" --auth-fail'
    args_c = selftest_drive_args(loop_c, base / "reg", auth_cmd)
    args_c.max_failures = 5
    result_c = drive_command(args_c)
    if (
        result_c.get("reason") != "auth_or_session_dead"
        or int(result_c.get("exit_code") or 0) != 7
        or int(result_c.get("quota_waits_total") or 0) != 0
    ):
        errors.append(f"drive auth fail did not exit 7: {result_c}")
    # 6. Weekly limit ×3 with max_quota_waits=3 → exit 5 (switch signal).
    loop_d = base / "loop-d"
    init_loop(selftest_init_args(loop_d, max_iterations=10))
    weekly_cmd = f'"{sys.executable}" "{stub}" --weekly-limit'
    args_d = selftest_drive_args(loop_d, base / "reg", weekly_cmd)
    args_d.max_quota_waits = 3
    args_d.quota_backoff = 0
    args_d.max_failures = 3
    result_d = drive_command(args_d)
    if (
        result_d.get("reason") != "quota_wait_exhausted"
        or int(result_d.get("exit_code") or 0) != 5
        or int(result_d.get("quota_waits_total") or 0) != 3
    ):
        errors.append(
            f"drive weekly-limit×3 did not exit 5 after 3 waits: {result_d}"
        )
    return errors


def selftest_command(_: argparse.Namespace) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="autonomous-loop-smoke-", dir=_containment_scratch_base()
    ) as tmp:
        # Resolve the scratch root before ``init_loop`` sees it: the guard
        # there rejects a loop path that crosses a symlink, which the default
        # macOS temp directory always does.
        run_dir = Path(tmp).resolve() / "loop"
        init_args = argparse.Namespace(
            dir=str(run_dir),
            goal="offline smoke test",
            success_criteria="ledger validates after one iteration",
            mode="bounded-research",
            force=False,
            stop_on_guard_fail=True,
            stop_on_missing_evidence=True,
            stop_on_scope_change=True,
            plateau_rule=DEFAULT_PLATEAU_RULE,
            success_check="",
            require_user_stop_only=False,
            stop_condition=[],
            budget_owner="selftest",
            max_iterations=2,
            max_wall_time_seconds=60,
            max_tokens=0,
            max_usd=0.0,
            max_depth=1,
            max_hops=1,
            max_child_workers=0,
            goal_focus_mode="off",
        )
        init_loop(init_args)
        proof_path = run_dir / "proofs" / "offline_smoke.proof"
        proof_path.parent.mkdir(parents=True, exist_ok=True)
        with proof_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write("offline smoke proof artifact\n")
        write_json(
            proof_artifact_path(run_dir, "offline-smoke-evidence"),
            {
                "schema_version": SCHEMA_VERSION,
                "id": "offline-smoke-evidence",
                "artifact_type": "python-verifier",
                "machine_checkable": True,
                "target": "offline smoke test",
                "proof_path": "proofs/offline_smoke.proof",
                "checker": {
                    "name": "offline-smoke",
                    "status": "passed",
                },
            },
        )
        append_args = argparse.Namespace(
            dir=str(run_dir),
            mode="bounded-research",
            objective="validate local ledger mechanics",
            decision="stop",
            input_ref=[],
            source_id=[],
            claim_id=[],
            evidence_id=["offline-smoke-evidence"],
            guard_ref=["offline-smoke"],
            action_taken=["initialized ledger"],
            output="selftest complete",
            remaining_gap=[],
            tokens=0,
            usd=0.0,
            wall_time_seconds=0,
            stop_reason="success",
        )
        append_iteration(append_args)
        validation = validate_loop_dir(run_dir)
        driver = selftest_driver_checks()
        return {
            "status": "ok" if validation["status"] == "ok" and driver["ok"] else "failed",
            "driver": driver,
            "smoke_mode": "offline",
            "network_required": False,
            "live_api_attempted": False,
            "package_install_attempted": False,
            "server_started": False,
            "config_written": False,
            "provider_cli_attempted": False,
            "subagents_spawned": False,
            "run_dir_created": run_dir.exists(),
            "validation_status": validation["status"],
            "iterations": validation["checked"]["iterations"],
        }


# --- Autoloop enforcement: arm/disarm/active/done/hook-check ------------------
# Force-management for autonomous loops: a registry of armed loops plus a
# fail-open stop check the Stop hook can call on every turn. The runtime never
# executes the success_check command (the driver/agent runs it and records a
# terminal stop); `done`/`hook-check` are read-only/derived and safe to call
# repeatedly. Stop policy (priority): explicit user stop > terminal status >
# recorded blocker > [user override: stop-only-on-user] > credit/budget caps >
# loops reached.

SENTINEL_STOP = "STOP_REQUESTED"
SENTINEL_BLOCKED = "BLOCKED"
SENTINEL_PAUSE = "PAUSE"
HEARTBEAT_TTL_SECONDS = 1800
MIGRATION_CLAIM_FILE = ".goal_focus_migration.claim"


class MigrationClaimError(RuntimeError):
    """Raised when driver ownership and migration quiescence conflict."""


class RegistrySafetyError(RuntimeError):
    """Raised when an authority-sensitive registry operation is unsafe."""


class RegistryEntrySnapshot(NamedTuple):
    """Exact registry bytes and inode metadata used for compare-and-delete."""

    path: Path
    entry: dict[str, Any]
    identity: tuple[int, int, int, int]
    payload: bytes


def migration_claim_active(run_dir: Path) -> bool:
    """Treat any claim leaf, including a symlink, as a fail-closed claim."""

    try:
        os.lstat(run_dir / MIGRATION_CLAIM_FILE)
        return True
    except FileNotFoundError:
        return False


def migration_claim_snapshot(run_dir: Path) -> tuple[dict[str, Any], tuple[int, int]]:
    """Read one bounded regular claim and return the exact inode observed.

    Invalid, oversized, or linked claims are deliberately errors: callers must
    not infer that a malformed ownership marker is stale and remove it.
    """

    path = run_dir / MIGRATION_CLAIM_FILE
    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise MigrationClaimError("migration claim is not a regular file")
        try:
            raw = _read_regular_text(path, max_bytes=4096)
        except UnicodeDecodeError as exc:
            raise MigrationClaimError(
                "migration claim is not valid UTF-8 JSON"
            ) from exc
        after = os.lstat(path)
        identity = (int(before.st_dev), int(before.st_ino))
        if identity != (int(after.st_dev), int(after.st_ino)):
            raise MigrationClaimError("migration claim changed while being inspected")
    else:
        directory_fd = _open_directory_nofollow(run_dir)
        try:
            fd = os.open(
                MIGRATION_CLAIM_FILE,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
                dir_fd=directory_fd,
            )
        finally:
            os.close(directory_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                raise MigrationClaimError(
                    "migration claim is not a bounded regular file"
                )
            chunks: list[bytes] = []
            observed = 0
            while observed <= 4096:
                chunk = os.read(fd, 4097 - observed)
                if not chunk:
                    break
                chunks.append(chunk)
                observed += len(chunk)
            payload = b"".join(chunks)
            if len(payload) > 4096:
                raise MigrationClaimError("migration claim exceeds 4096 bytes")
            try:
                raw = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise MigrationClaimError(
                    "migration claim is not valid UTF-8 JSON"
                ) from exc
            identity = (int(info.st_dev), int(info.st_ino))
        finally:
            os.close(fd)
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MigrationClaimError("migration claim is not valid UTF-8 JSON") from exc
    if not isinstance(record, dict):
        raise MigrationClaimError("migration claim must contain a JSON object")
    return record, identity


def acquire_migration_claim(run_dir: Path) -> tuple[int, int]:
    """Atomically claim a loop before checking its live-driver registry."""

    payload = json.dumps(
        {
            "pid": os.getpid(),
            "claimed_at": utc_now(),
            "nonce": uuid.uuid4().hex,
        },
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    path = run_dir / MIGRATION_CLAIM_FILE
    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            info = os.fstat(handle.fileno())
            return int(info.st_dev), int(info.st_ino)
    directory_fd = _open_directory_nofollow(run_dir)
    try:
        fd = os.open(
            MIGRATION_CLAIM_FILE,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        try:
            remaining = memoryview(payload)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("could not write migration claim")
                remaining = remaining[written:]
            os.fsync(fd)
            info = os.fstat(fd)
            return int(info.st_dev), int(info.st_ino)
        finally:
            os.close(fd)
    finally:
        os.close(directory_fd)


def reclaim_dead_migration_claim(run_dir: Path) -> bool:
    """Reclaim a claim only when its recorded owner PID is definitely dead.

    A live, malformed, linked, or unreadable claim remains in place so both
    migration and driver startup fail closed.  PID reuse therefore favors
    safety over automatic recovery; an operator can inspect such a claim.
    """

    try:
        record, identity = migration_claim_snapshot(run_dir)
    except (FileNotFoundError, OSError, MigrationClaimError):
        return False
    pid = record.get("pid")
    if not isinstance(pid, int) or pid <= 0 or pid_alive(pid):
        return False
    release_migration_claim(run_dir, identity)
    return True


def release_migration_claim(run_dir: Path, identity: tuple[int, int]) -> None:
    """Rename, revalidate, and delete only the exact owned claim inode."""

    path = run_dir / MIGRATION_CLAIM_FILE
    quarantine = f".goal-focus-claim-release-{uuid.uuid4().hex}.json"
    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        try:
            before_record, before_identity = migration_claim_snapshot(run_dir)
        except FileNotFoundError:
            return
        if before_identity != identity:
            raise MigrationClaimError("migration claim identity changed before release")
        before_payload = json.dumps(before_record, sort_keys=True, separators=(",", ":"))
        quarantine_path = run_dir / quarantine
        os.replace(path, quarantine_path)
        try:
            info = os.lstat(quarantine_path)
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISREG(info.st_mode)
                or (int(info.st_dev), int(info.st_ino)) != identity
            ):
                raise MigrationClaimError(
                    f"migration claim changed during release and was retained: {quarantine_path}"
                )
            moved = json.loads(
                _read_regular_text(quarantine_path, max_bytes=4096)
            )
            moved_payload = json.dumps(moved, sort_keys=True, separators=(",", ":"))
            if moved_payload != before_payload:
                raise MigrationClaimError(
                    f"migration claim content changed during release and was retained: {quarantine_path}"
                )
            os.unlink(quarantine_path)
        except Exception:
            raise
        return

    directory_fd = _open_directory_nofollow(run_dir)
    try:
        try:
            file_fd = os.open(
                MIGRATION_CLAIM_FILE,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            return
        try:
            before = os.fstat(file_fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size > 4096
                or (int(before.st_dev), int(before.st_ino)) != identity
            ):
                raise MigrationClaimError(
                    "migration claim identity changed before release"
                )
            with os.fdopen(file_fd, "rb", closefd=False) as handle:
                before_payload = handle.read(4097)
            after = os.fstat(file_fd)
            if (
                len(before_payload) > 4096
                or _registry_identity(before) != _registry_identity(after)
            ):
                raise MigrationClaimError(
                    "migration claim changed while preparing release"
                )
        finally:
            os.close(file_fd)
        try:
            os.rename(
                MIGRATION_CLAIM_FILE,
                quarantine,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
        except FileNotFoundError:
            raise MigrationClaimError(
                "migration claim disappeared during release"
            )
        moved_fd = os.open(
            quarantine,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
            dir_fd=directory_fd,
        )
        try:
            moved = os.fstat(moved_fd)
            with os.fdopen(moved_fd, "rb", closefd=False) as handle:
                moved_payload = handle.read(4097)
        finally:
            os.close(moved_fd)
        if (
            (int(moved.st_dev), int(moved.st_ino)) != identity
            or not stat.S_ISREG(moved.st_mode)
            or moved.st_nlink != 1
            or moved_payload != before_payload
        ):
            raise MigrationClaimError(
                "migration claim changed during release and was retained as "
                f"{run_dir / quarantine}"
            )
        os.unlink(quarantine, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def registry_dir(args: argparse.Namespace) -> Path:
    raw = getattr(args, "registry_dir", None) or os.environ.get("AAS_AUTOLOOP_REGISTRY")
    base = Path(raw).expanduser() if raw else Path.home() / ".local" / "share" / "ai-agents-skills" / "autoloop"
    return base / "active.d"


def windows_pid_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    if pid > 0xFFFFFFFF:
        return False

    process_query_limited_information = 0x1000
    error_access_denied = 5
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_exit_code_process = kernel32.GetExitCodeProcess
    get_exit_code_process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    get_exit_code_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied
    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code_process(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        close_handle(handle)


def pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        # On Windows, os.kill(pid, 0) sends CTRL_C_EVENT instead of performing
        # the harmless POSIX existence check.
        return windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def try_cancel_orphaned_host_dispatch(
    run_dir: str | Path,
    *,
    reason: str = "owner_driver_pid_absent",
) -> dict[str, Any]:
    """Cancel an unconsumed host dispatch whose owner drive process is gone.

    Safe automatic recovery after service restart / SIGKILL: never cancels when a
    candidate is already staged (that path stays on independent result review),
    and never cancels while the recorded ``driver_pid`` is still alive.
    """

    root = Path(run_dir)
    try:
        goal_focus_v2.recover_transactions(root)
    except Exception:  # noqa: BLE001 - best-effort before load
        pass
    dispatch = goal_focus_v2.load_iteration_dispatch(root)
    if not dispatch:
        return {"status": "absent", "applied": False}
    if goal_focus_v2.load_pending_candidate(root):
        return {
            "status": "pending_candidate",
            "applied": False,
            "dispatch_id": str(dispatch.get("dispatch_id") or ""),
        }
    dispatch_id = str(dispatch.get("dispatch_id") or "").strip()
    owner = dispatch.get("driver_pid")
    if not dispatch_id:
        return {"status": "invalid_dispatch", "applied": False}
    if pid_alive(owner):
        return {
            "status": "owner_alive",
            "applied": False,
            "dispatch_id": dispatch_id,
            "driver_pid": owner,
        }
    try:
        result = goal_focus_v2.cancel_iteration_dispatch(
            root,
            dispatch_id=dispatch_id,
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001 - caller decides fail-open vs wait
        return {
            "status": "failed",
            "applied": False,
            "dispatch_id": dispatch_id,
            "driver_pid": owner,
            "error": str(exc),
        }
    return {
        "status": str(result.get("status") or "cancelled"),
        "applied": result.get("status") == "cancelled",
        "dispatch_id": dispatch_id,
        "driver_pid": owner,
        "result": result,
    }


def try_cancel_own_unconsumed_dispatch(
    run_dir: str | Path,
    *,
    owner_pid: int,
    reason: str = "drive_shutdown_unconsumed_dispatch",
) -> dict[str, Any]:
    """Cancel a dispatch owned by this drive process when no candidate was staged.

    Used from the drive ``finally`` path so a clean SIGTERM/systemd stop does not
    leave the next drive wedged on ``goal_focus_wait`` forever.
    """

    root = Path(run_dir)
    if not isinstance(owner_pid, int) or owner_pid <= 0:
        return {"status": "bad_owner_pid", "applied": False}
    try:
        goal_focus_v2.recover_transactions(root)
    except Exception:  # noqa: BLE001 - best-effort before load
        pass
    dispatch = goal_focus_v2.load_iteration_dispatch(root)
    if not dispatch:
        return {"status": "absent", "applied": False}
    if goal_focus_v2.load_pending_candidate(root):
        return {
            "status": "pending_candidate",
            "applied": False,
            "dispatch_id": str(dispatch.get("dispatch_id") or ""),
        }
    dispatch_id = str(dispatch.get("dispatch_id") or "").strip()
    recorded = dispatch.get("driver_pid")
    if not dispatch_id:
        return {"status": "invalid_dispatch", "applied": False}
    if int(recorded or 0) != int(owner_pid):
        return {
            "status": "not_owner",
            "applied": False,
            "dispatch_id": dispatch_id,
            "driver_pid": recorded,
        }
    try:
        result = goal_focus_v2.cancel_iteration_dispatch(
            root,
            dispatch_id=dispatch_id,
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001 - shutdown is best-effort
        return {
            "status": "failed",
            "applied": False,
            "dispatch_id": dispatch_id,
            "driver_pid": recorded,
            "error": str(exc),
        }
    return {
        "status": str(result.get("status") or "cancelled"),
        "applied": result.get("status") == "cancelled",
        "dispatch_id": dispatch_id,
        "driver_pid": recorded,
        "result": result,
    }


def parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def entry_is_live(entry: dict[str, Any]) -> bool:
    pid = entry.get("pid")
    if isinstance(pid, int) and pid > 0 and not pid_alive(pid):
        return False
    stamp = parse_iso(entry.get("heartbeat") or entry.get("created_at"))
    if stamp is None:
        return True
    return (datetime.now(timezone.utc) - stamp).total_seconds() <= HEARTBEAT_TTL_SECONDS


def entry_owned_by_live_driver(entry: dict[str, Any]) -> bool:
    """True when a live headless-driver process owns this registry entry.

    The interactive Stop-hook must stand down while a driver governs the loop;
    otherwise a hooked session would run an iteration concurrently with the
    driver's own iteration session against the same single-path ledger.
    Entries written by `drive` carry driver=true plus the driver pid; entries
    from before that flag are recognized via /proc cmdline proof only, so a
    merely-alive non-driver pid never suppresses the hook.
    """
    pid = entry.get("pid")
    if not isinstance(pid, int) or pid <= 0 or not pid_alive(pid):
        return False
    if entry.get("driver") is True:
        return True
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    return b"drive" in argv and any(b"autonomous_research_loop_runtime" in part for part in argv)


def list_registry_entries(reg: Path) -> list[tuple[Path, dict[str, Any]]]:
    out: list[tuple[Path, dict[str, Any]]] = []
    if not reg.exists():
        return out
    for path in sorted(reg.glob("*.json")):
        try:
            out.append((path, read_json(path)))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return out


def _open_trusted_registry_directory(reg: Path) -> int:
    """Open a registry chain owned by root/current user and not writable by peers."""

    absolute = Path(os.path.abspath(reg))
    if os.name != "posix":  # pragma: no cover - POSIX authority implementation
        raise OSError("descriptor-pinned registry authority requires POSIX")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(absolute.anchor or os.sep, flags)
    effective_uid = os.geteuid()
    try:
        components = [absolute.anchor or os.sep, *absolute.parts[1:]]
        for index, component in enumerate(components):
            if index:
                next_descriptor = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            info = os.fstat(descriptor)
            root_sticky = bool(
                info.st_uid == 0
                and info.st_mode & stat.S_ISVTX
                and info.st_mode & stat.S_IWOTH
            )
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid not in {0, effective_uid}
                or (
                    info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                    and not root_sticky
                )
            ):
                raise OSError(
                    f"registry directory chain is not host-controlled: {absolute}"
                )
        final = os.fstat(descriptor)
        if final.st_uid != effective_uid or final.st_mode & 0o077:
            raise OSError(
                "registry directory must be private and owned by the current "
                f"user: {absolute}"
            )
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _lock_registry_descriptor(descriptor: int, *, exclusive: bool) -> None:
    """Cooperatively serialize every current-runtime registry snapshot/mutation."""

    if os.name != "posix":  # pragma: no cover - Windows uses its fallback paths
        return
    import fcntl

    fcntl.flock(
        descriptor,
        fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH,
    )


def _registry_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
    )


def _validate_registry_leaf(
    info: os.stat_result, directory_info: os.stat_result
) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_size > 1_000_000
    ):
        raise OSError("registry entry is unsafe or oversized")
    if os.name == "posix" and (
        info.st_uid != directory_info.st_uid
        or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise OSError("registry entry has unsafe ownership or permissions")


def _parse_registry_snapshot(
    path: Path, payload: bytes, info: os.stat_result
) -> RegistryEntrySnapshot:
    if len(payload) > 1_000_000:
        raise OSError("registry entry is oversized")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("registry entry is not a JSON object")
    return RegistryEntrySnapshot(path, value, _registry_identity(info), payload)


def _read_registry_snapshot_at(
    reg: Path,
    name: str,
    *,
    directory_fd: int | None = None,
) -> RegistryEntrySnapshot:
    """Read one registry leaf through a pinned directory and exact descriptor."""

    path = reg / name
    if not name.endswith(".json") or Path(name).name != name:
        raise RegistrySafetyError(f"unsafe registry entry name: {name!r}")
    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode):
            raise OSError("registry entry is a symlink")
        # O_BINARY: this branch only runs on Windows, where a descriptor opened in
        # the CRT default text mode rewrites CRLF and truncates the read at Ctrl-Z,
        # so a registry leaf would parse as silently short.
        file_fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        try:
            opened = os.fstat(file_fd)
            directory_info = os.lstat(reg)
            _validate_registry_leaf(opened, directory_info)
            if _registry_identity(before) != _registry_identity(opened):
                raise OSError("registry entry changed during open")
            with os.fdopen(file_fd, "rb", closefd=False) as handle:
                payload = handle.read(1_000_001)
            after = os.fstat(file_fd)
            if _registry_identity(opened) != _registry_identity(after):
                raise OSError("registry entry changed during read")
        finally:
            os.close(file_fd)
        final_path = os.lstat(path)
        if _registry_identity(final_path) != _registry_identity(after):
            raise OSError("registry entry changed after read")
        return _parse_registry_snapshot(path, payload, after)

    owns_directory_fd = directory_fd is None
    if directory_fd is None:
        directory_fd = _open_trusted_registry_directory(reg)
    assert directory_fd is not None
    try:
        directory_info = os.fstat(directory_fd)
        file_fd = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
            dir_fd=directory_fd,
        )
        try:
            before = os.fstat(file_fd)
            _validate_registry_leaf(before, directory_info)
            with os.fdopen(file_fd, "rb", closefd=False) as handle:
                payload = handle.read(1_000_001)
            after = os.fstat(file_fd)
            if _registry_identity(before) != _registry_identity(after):
                raise OSError("registry entry changed during read")
        finally:
            os.close(file_fd)
        return _parse_registry_snapshot(path, payload, after)
    finally:
        if owns_directory_fd:
            os.close(directory_fd)


def strict_registry_snapshots(reg: Path) -> list[RegistryEntrySnapshot]:
    """Read every registry row exactly; any unsafe row blocks authority changes."""

    reg = Path(os.path.abspath(reg))
    try:
        root_info = os.lstat(reg)
    except FileNotFoundError:
        return []
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise RegistrySafetyError(f"registry is not a real directory: {reg}")
    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        names = sorted(path.name for path in reg.glob("*.json"))
        rows: list[RegistryEntrySnapshot] = []
        for name in names:
            try:
                rows.append(_read_registry_snapshot_at(reg, name))
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                raise RegistrySafetyError(
                    f"cannot safely parse registry entry {reg / name}"
                ) from exc
        return rows

    try:
        directory_fd = _open_trusted_registry_directory(reg)
    except OSError as exc:
        raise RegistrySafetyError(f"cannot securely open registry: {reg}") from exc
    rows = []
    try:
        _lock_registry_descriptor(directory_fd, exclusive=False)
        try:
            names = sorted(
                name for name in os.listdir(directory_fd) if name.endswith(".json")
            )
        except OSError as exc:
            raise RegistrySafetyError(f"cannot list registry: {reg}") from exc
        for name in names:
            try:
                rows.append(
                    _read_registry_snapshot_at(
                        reg, name, directory_fd=directory_fd
                    )
                )
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                raise RegistrySafetyError(
                    f"cannot safely parse registry entry {reg / name}"
                ) from exc
    finally:
        os.close(directory_fd)
    return rows


def strict_registry_entries(reg: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Compatibility view over the fail-closed exact registry snapshots."""

    return [(row.path, row.entry) for row in strict_registry_snapshots(reg)]


def _same_registry_snapshot(
    observed: RegistryEntrySnapshot, expected: RegistryEntrySnapshot
) -> bool:
    return (
        observed.path.name == expected.path.name
        and observed.identity == expected.identity
        and observed.payload == expected.payload
    )


def _validate_registry_authority_snapshot(snapshot: RegistryEntrySnapshot) -> None:
    """Require enough typed identity to prove a row unrelated before mutation."""

    entry = snapshot.entry
    try:
        safe_registry_run_id(entry.get("run_id"))
    except ValueError as exc:
        raise RegistrySafetyError(
            f"registry entry {snapshot.path} has an invalid run_id"
        ) from exc
    for field in ("loop_dir", "project_root"):
        raw = entry.get(field)
        if (
            not isinstance(raw, str)
            or not raw.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in raw)
            or not Path(raw).is_absolute()
        ):
            raise RegistrySafetyError(
                f"registry entry {snapshot.path} has an invalid {field}"
            )
    pid = entry.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 0:
        raise RegistrySafetyError(
            f"registry entry {snapshot.path} has an invalid pid"
        )
    if entry.get("driver") is not None and not isinstance(entry.get("driver"), bool):
        raise RegistrySafetyError(
            f"registry entry {snapshot.path} has an invalid driver marker"
        )


def delete_registry_snapshot(snapshot: RegistryEntrySnapshot) -> bool:
    """Rename, verify, then delete only the exact snapshotted registry inode.

    A raced replacement is retained under another ``*.json`` name so every
    subsequent authority-sensitive scan still sees it and fails closed.
    """

    reg = Path(os.path.abspath(snapshot.path.parent))
    name = snapshot.path.name
    quarantine = f".registry-delete-{uuid.uuid4().hex}-{name}"
    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        try:
            current = _read_registry_snapshot_at(reg, name)
        except FileNotFoundError:
            return False
        if not _same_registry_snapshot(current, snapshot):
            raise RegistrySafetyError(
                f"registry entry changed before deletion: {snapshot.path}"
            )
        try:
            os.replace(reg / name, reg / quarantine)
        except FileNotFoundError:
            return False
        moved = _read_registry_snapshot_at(reg, quarantine)
        if not _same_registry_snapshot(
            moved, snapshot._replace(path=reg / quarantine)
        ):
            raise RegistrySafetyError(
                f"registry entry changed during deletion and was retained: {reg / quarantine}"
            )
        os.unlink(reg / quarantine)
        return True

    directory_fd = _open_trusted_registry_directory(reg)
    _lock_registry_descriptor(directory_fd, exclusive=True)
    try:
        try:
            current = _read_registry_snapshot_at(
                reg, name, directory_fd=directory_fd
            )
        except FileNotFoundError:
            return False
        if not _same_registry_snapshot(current, snapshot):
            raise RegistrySafetyError(
                f"registry entry changed before deletion: {snapshot.path}"
            )
        try:
            os.rename(
                name,
                quarantine,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
        except FileNotFoundError:
            return False
        moved = _read_registry_snapshot_at(
            reg, quarantine, directory_fd=directory_fd
        )
        expected_moved = snapshot._replace(path=reg / quarantine)
        if not _same_registry_snapshot(moved, expected_moved):
            raise RegistrySafetyError(
                f"registry entry changed during deletion and was retained: {reg / quarantine}"
            )
        os.unlink(quarantine, dir_fd=directory_fd)
        os.fsync(directory_fd)
        return True
    finally:
        os.close(directory_fd)


def gc_registry(reg: Path) -> int:
    removed = 0
    for snapshot in strict_registry_snapshots(reg):
        _validate_registry_authority_snapshot(snapshot)
        entry = snapshot.entry
        # A long provider call can outlive the heartbeat TTL while its owning
        # driver PID is still running.  Never garbage-collect that ownership
        # proof: migration and a second driver must continue to fail closed.
        if not entry_is_live(entry) and not entry_owned_by_live_driver(entry):
            if delete_registry_snapshot(snapshot):
                removed += 1
    return removed


def strict_live_registry_entries(
    reg: Path, *, collect_garbage: bool
) -> list[tuple[Path, dict[str, Any]]]:
    """Derive one authority decision from one fail-closed registry snapshot."""

    live: list[tuple[Path, dict[str, Any]]] = []
    for snapshot in strict_registry_snapshots(reg):
        _validate_registry_authority_snapshot(snapshot)
        entry = snapshot.entry
        is_live = entry_is_live(entry) or entry_owned_by_live_driver(entry)
        if is_live:
            live.append((snapshot.path, entry))
        elif collect_garbage:
            delete_registry_snapshot(snapshot)
    return live


def arm_loop(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    driver_claim = bool(getattr(args, "driver", False))
    # Serialize current-runtime registrations for this loop.  Together with
    # migration's claim-before-scan order, this prevents two drivers from
    # replacing the same run-id registry entry and hiding a live owner.
    with LoopLock(run_dir):
        if driver_claim and migration_claim_active(run_dir):
            raise MigrationClaimError(
                "cannot start a driver while Goal-Focus migration owns the loop"
            )
        state = read_json(loop_paths(run_dir)["state"])
        run_id = safe_registry_run_id(state.get("run_id") or str(uuid.uuid4()))
        root = Path(args.root).expanduser().resolve() if args.root else run_dir
        reg = registry_dir(args)
        reg = _ensure_real_directory(reg)
        gc_registry(reg)
        requested_pid = int(args.pid) if args.pid else 0
        snapshots = strict_registry_snapshots(reg)
        target_snapshot: RegistryEntrySnapshot | None = None
        for snapshot in snapshots:
            _validate_registry_authority_snapshot(snapshot)
            existing = snapshot.entry
            if snapshot.path.name == f"{run_id}.json":
                if existing.get("run_id") != run_id:
                    raise RegistrySafetyError(
                        f"registry filename {snapshot.path} belongs to another run_id"
                    )
                if target_snapshot is not None:
                    raise RegistrySafetyError(
                        f"registry contains duplicate target rows for {run_id}"
                    )
                target_snapshot = snapshot
            elif existing.get("run_id") == run_id:
                raise RegistrySafetyError(
                    f"registry contains a duplicate row for run_id {run_id}: {snapshot.path}"
                )
            if (
                driver_claim
                and existing.get("loop_dir") == str(run_dir)
                and entry_owned_by_live_driver(existing)
                and existing.get("pid") != requested_pid
            ):
                raise ValueError(
                    f"a live driver already owns {run_dir}; stop it before starting another"
                )
            if (
                not args.force
                and existing.get("project_root") == str(root)
                and existing.get("run_id") != run_id
                and entry_is_live(existing)
            ):
                raise ValueError(
                    f"a live autoloop is already armed for {root}; pass --force to override"
                )
        # Notify policy: explicit arm flag → env/loop → secrets-backed auto.
        explicit_notify = getattr(args, "notify", None)
        notify_channel = resolve_notify_channel(
            explicit=explicit_notify,
            run_dir=run_dir,
            registry=reg,
            default_auto=True,
        )
        persist_token = normalize_notify_token(explicit_notify)
        if persist_token in (None, "auto"):
            # An unresolved 'auto' is 'no opinion', not a silence order.  Both
            # loop_state and the registry entry are decisive sources, so
            # latching 'off' here mutes a loop armed before its notify secrets
            # exist and no later source can revive it.
            persist_token = notify_channel or "auto"
        write_loop_notify_policy(
            run_dir, None if persist_token == "off" else persist_token
        )
        now = utc_now()
        registration_id = uuid.uuid4().hex
        entry = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "registration_id": registration_id,
            "loop_dir": str(run_dir),
            "project_root": str(root),
            "pid": requested_pid,
            "driver": driver_claim,
            "notify_channel": persist_token if persist_token != "off" else "off",
            "heartbeat": now,
            "created_at": now,
        }
        own_snapshot = _write_registry_json_snapshot(
            reg, run_id, entry, expected=target_snapshot
        )
        entry_path = own_snapshot.path

        def remove_own_registration() -> None:
            try:
                delete_registry_snapshot(own_snapshot)
            except (FileNotFoundError, OSError, RegistrySafetyError):
                pass

        # Close the check/register race with migration: a migration claim created
        # after the first check either observes this registry entry, or this second
        # check removes only our registration before the driver can execute work.
        if driver_claim and migration_claim_active(run_dir):
            remove_own_registration()
            raise MigrationClaimError(
                "Goal-Focus migration claimed the loop while the driver registered"
            )
        if driver_claim:
            try:
                current_snapshot = _read_registry_snapshot_at(reg, entry_path.name)
            except Exception:
                remove_own_registration()
                raise
            if (
                not _same_registry_snapshot(current_snapshot, own_snapshot)
                or current_snapshot.entry.get("registration_id") != registration_id
            ):
                remove_own_registration()
                raise RuntimeError(
                    "driver registry ownership changed during registration"
                )
        return {
            "status": "ok",
            "action": "arm",
            "run_id": run_id,
            "registration_id": registration_id,
            "registry": str(reg),
            "project_root": str(root),
            "notify_channel": entry["notify_channel"],
            "notify_resolved": notify_channel,
        }


def disarm_loop(args: argparse.Namespace) -> dict[str, Any]:
    reg = registry_dir(args)
    run_id = getattr(args, "run_id", None)
    loop_dir: str | None = None
    if getattr(args, "dir", None):
        loop_dir = str(Path(args.dir).expanduser().resolve())
        if not run_id:
            try:
                run_id = read_json(loop_paths(Path(loop_dir))["state"]).get("run_id")
            except (OSError, ValueError, json.JSONDecodeError):
                run_id = None
    removed: list[str] = []
    for snapshot in strict_registry_snapshots(reg):
        _validate_registry_authority_snapshot(snapshot)
        entry = snapshot.entry
        if (run_id and entry.get("run_id") == run_id) or (loop_dir and entry.get("loop_dir") == loop_dir):
            if delete_registry_snapshot(snapshot):
                removed.append(str(entry.get("run_id")))
    return {"status": "ok", "action": "disarm", "removed": removed, "registry": str(reg)}


def active_command(args: argparse.Namespace) -> dict[str, Any]:
    reg = registry_dir(args)
    loops = [
        entry
        for _, entry in strict_live_registry_entries(
            reg, collect_garbage=True
        )
    ]
    return {"status": "ok", "action": "active", "registry": str(reg), "count": len(loops), "loops": loops}


def compute_done(run_dir: Path) -> dict[str, Any]:
    if (run_dir / ".goal_focus_transactions").exists():
        goal_focus_v2.recover_transactions(run_dir)
    paths = loop_paths(run_dir)
    state = read_json(paths["state"]) if paths["state"].exists() else {}
    budget = read_json(paths["budget"]) if paths["budget"].exists() else {}
    for field in ("max_usd", "spent_usd"):
        raw = budget.get(field, 0.0)
        if isinstance(raw, bool):
            raise ValueError(f"budget.json {field} must be a finite non-negative number")
        try:
            amount = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"budget.json {field} must be a finite non-negative number"
            ) from exc
        if not math.isfinite(amount) or amount < 0:
            raise ValueError(f"budget.json {field} must be a finite non-negative number")
    stop_conditions = state.get("stop_conditions") or {}
    paused = (run_dir / SENTINEL_PAUSE).exists()
    require_user = bool(stop_conditions.get("require_user_stop_only"))
    max_usd = budget.get("max_usd") or 0
    max_tokens = budget.get("max_tokens") or 0
    max_wall = budget.get("max_wall_time_seconds") or 0
    max_iter = budget.get("max_iterations")
    started = parse_iso(state.get("created_at"))
    done = False
    reason: str | None = None
    if (run_dir / SENTINEL_STOP).exists():
        # user-owned stop sentinel: always terminal (stop condition 4).
        done, reason = True, "user_stop_requested"
    elif (run_dir / SENTINEL_BLOCKED).exists():
        # operator-owned stop file: always terminal.
        done, reason = True, "operator_blocked"
    elif max_usd and float(budget.get("spent_usd", 0)) >= float(max_usd):
        done, reason = True, "credit_exhausted:usd"
    elif max_tokens and int(budget.get("spent_tokens", 0)) >= int(max_tokens):
        done, reason = True, "credit_exhausted:tokens"
    elif max_wall and started and (datetime.now(timezone.utc) - started).total_seconds() >= float(max_wall):
        done, reason = True, "credit_exhausted:wall_time"
    elif isinstance(max_iter, int) and max_iter > 0 and int(budget.get("spent_iterations", 0)) >= max_iter:
        # the iteration cap is physical (append refuses beyond it): always terminal.
        done, reason = True, "loops_reached"
    elif require_user:
        # Strongest user policy: beyond a user/operator stop or a physical budget
        # cap (handled above), only the user may end the loop. An agent-written
        # terminal status must NOT release the session.
        done, reason = False, "awaiting_user_stop"
    elif state.get("status") in TERMINAL_STATUSES:
        done, reason = True, f"terminal_status:{state.get('status')}"
    return {"done": done, "paused": paused, "reason": reason}


def done_command(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    return {"status": "ok", "action": "done", "dir": str(run_dir), **compute_done(run_dir)}


def formal_terminal_state_command(args: argparse.Namespace) -> dict[str, Any]:
    """Host-run gate verdict on the formal artifact; writes formal/terminal_state.json."""
    run_dir = Path(args.dir).expanduser().resolve()
    root = Path(args.root).expanduser().resolve() if getattr(args, "root", None) else None
    pol = load_formal_policy(run_dir)
    verdict = evaluate_formal_terminal_state(
        run_dir,
        root=root,
        policy=pol,
        reason=str(getattr(args, "reason", "") or "cli"),
        require_typecheck=not bool(getattr(args, "no_typecheck", False)),
    )
    decided = verdict.get("terminal_state") in {"sorry_free_artifact", "open_ledger"}
    return {
        "status": "ok" if decided else "failed",
        "action": "formal-terminal-state",
        "dir": str(run_dir),
        "terminal_state": verdict.get("terminal_state"),
        "detail": verdict.get("detail"),
        "gate": verdict.get("gate"),
        "obligations": verdict.get("obligations"),
        "decided_at": verdict.get("decided_at"),
        "state_file": str(run_dir / "formal" / "terminal_state.json"),
    }


def read_hook_payload() -> str:
    """Best-effort read of the Stop-hook JSON on stdin that never blocks the
    fail-open hook. On POSIX a zero-timeout select guards against an inherited idle
    pipe; on Windows (or where select is unavailable) the runtime reads directly,
    matching how Claude Code and the tests pipe the payload and then close stdin."""
    stdin = sys.stdin
    try:
        if stdin is None or stdin.isatty():
            return ""
    except (ValueError, OSError):
        return ""
    if os.name == "posix":
        try:
            import select

            ready, _, _ = select.select([stdin], [], [], 0)
        except (OSError, ValueError):
            return ""
        if not ready:
            return ""
    try:
        return stdin.read() or ""
    except (OSError, ValueError):
        return ""


def hook_payload_is_reentrant(payload: str) -> bool:
    """True when Claude reports the Stop hook is already active, so a block can never
    build an infinite loop."""
    if not payload:
        return False
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        # Substring fallback for non-JSON or partial input (matches the old shell wrapper).
        return '"stop_hook_active": true' in payload or '"stop_hook_active":true' in payload
    return bool(isinstance(data, dict) and data.get("stop_hook_active"))


def hook_check_command(args: argparse.Namespace) -> dict[str, Any]:
    try:
        if os.environ.get("AUTOLOOP_DISABLE"):
            return {"status": "ok", "action": "hook-check", "block": False, "reason": "disabled_env"}
        # Headless driver runs enforce the policy themselves; the interactive hook
        # stands down so it never double-governs a driver iteration.
        if os.environ.get("AUTOLOOP_DRIVER"):
            return {"status": "ok", "action": "hook-check", "block": False, "reason": "driver_active"}
        # Re-entrancy: allow turn-end when Claude reports the Stop hook is already active.
        if hook_payload_is_reentrant(read_hook_payload()):
            return {"status": "ok", "action": "hook-check", "block": False, "reason": "stop_hook_active"}
        reg = registry_dir(args)
        # Workspace root for hooks: Grok sets GROK_WORKSPACE_ROOT and the Claude
        # alias CLAUDE_PROJECT_DIR; Claude sets CLAUDE_PROJECT_DIR. Diagnostic only
        # on Grok (Stop is non-blocking there).
        raw_root = (
            args.root
            or os.environ.get("GROK_WORKSPACE_ROOT")
            or os.environ.get("CLAUDE_PROJECT_DIR")
        )
        root = Path(raw_root).expanduser().resolve() if raw_root else Path.cwd().resolve()
        match: dict[str, Any] | None = None
        for _, entry in strict_live_registry_entries(
            reg, collect_garbage=True
        ):
            entry_root = entry.get("project_root")
            if not entry_root:
                continue
            # Re-resolve the stored root at match time so a planted or stale
            # entry cannot capture an unrelated session via a symlink or a
            # non-canonical path.
            entry_root = str(Path(str(entry_root)).expanduser().resolve())
            if str(root) == entry_root or str(root).startswith(entry_root + os.sep):
                match = entry
                break
        if match is None:
            return {"status": "ok", "action": "hook-check", "block": False, "reason": "no_active_loop"}
        if entry_owned_by_live_driver(match):
            return {
                "status": "ok",
                "action": "hook-check",
                "block": False,
                "reason": "headless_driver_active",
                "run_id": match.get("run_id"),
            }
        loop_dir = Path(str(match.get("loop_dir", ""))).expanduser()
        if not loop_dir.exists():
            return {"status": "ok", "action": "hook-check", "block": False, "reason": "loop_dir_missing"}
        verdict = compute_done(loop_dir)
        if verdict.get("done") or verdict.get("paused"):
            return {
                "status": "ok",
                "action": "hook-check",
                "block": False,
                "reason": verdict.get("reason") or ("paused" if verdict.get("paused") else "done"),
            }
        return {
            "status": "ok",
            "action": "hook-check",
            "block": True,
            "reason": "active_not_done",
            "loop_dir": str(loop_dir),
            "message": (
                "Autoloop is active and not finished: do the next iteration now. Do NOT stop, "
                "pause, or ask to continue. Kill switch: set AUTOLOOP_DISABLE=1, remove the "
                f"registry entry for run {match.get('run_id')}, or run `touch {loop_dir}/{SENTINEL_STOP}`."
            ),
        }
    except RegistrySafetyError as exc:
        return {
            "status": "ok",
            "action": "hook-check",
            "block": True,
            "reason": "registry_unsafe_fail_closed",
            "message": (
                "Autoloop registry authority is unsafe and must be repaired before "
                f"the session may stop: {exc}"
            ),
        }
    except Exception as exc:  # noqa: BLE001 - non-authority hook errors fail open.
        return {"status": "ok", "action": "hook-check", "block": False, "reason": f"error_fail_open:{exc}"}


# --- Provider adapters: truly-autonomous headless driving per install target ---
# Each spec builds the exact one-iteration headless invocation for one agent
# CLI. `agent-cmd` only constructs and PATH-probes commands (offline); `drive
# --provider` executes them. Operator overrides (highest first):
#   AAS_AUTOLOOP_CMD_<PROVIDER>  full shell command template; {prompt} is
#                                substituted shell-quoted, {dir} verbatim, and
#                                the prompt is also exported as $AUTOLOOP_PROMPT
#   AAS_AUTOLOOP_ARGS_<PROVIDER> replacement argument template (shlex-split;
#                                {prompt}/{dir} placeholders substituted)
#   AAS_AUTOLOOP_BIN_<PROVIDER>  replacement binary path
#   AAS_GROK                     (grok only) binary override when AAS_AUTOLOOP_BIN_GROK
#                                is unset; aligned with installer GROK_CLI_TOOL_SPEC
# The default flag sets grant full tool autonomy, which unattended research
# loops require; point the loop at a workspace you trust the agent to write.
#
# Grok binary preference MUST stay aligned with installer GROK_CLI_TOOL_SPEC
# (drive/delegation): use bare Grok by default, or, when
# AAS_GROK_LATEST_MODEL resolves an exact model, confirm that model in anchored
# ``grok models`` rows before allowing a grok-remote fallback. Diagnostics/smoke
# stay bare-grok-only (installer GROK_DIAGNOSTIC_*).
# Logical provider id is always "grok" — never a separate "grok-remote" provider.

GROK_MODEL_PROBE_SCHEMA = "grok-model-membership.v1"
GROK_MODEL_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:+/@-]{0,127}"
GROK_MODEL_ID_RE = re.compile(rf"^{GROK_MODEL_ID_PATTERN}$")
GROK_AVAILABLE_MODEL_LINE_RE = re.compile(
    rf"^\s*\*\s+(?P<model>{GROK_MODEL_ID_PATTERN})(?:\s+\(default\))?\s*$"
)

# Platform-keyed automatic candidate tiers for provider "grok". Keep these in
# sync with installer/ai_agents_skills/grok.py. The proxy tier is never
# consulted without an exact resolved model and a failed bare-model confirmation.
GROK_BARE_BINARY_CANDIDATES: dict[str, list[str]] = {
    "linux": [
        "grok",
        "~/.local/bin/grok",
        "~/.grok/bin/grok",
    ],
    "macos": [
        "grok",
        "~/.local/bin/grok",
        "~/.grok/bin/grok",
        "/opt/homebrew/bin/grok",
        "/usr/local/bin/grok",
    ],
    "wsl": [
        "grok",
        "~/.local/bin/grok",
        "~/.grok/bin/grok",
    ],
    "windows": [
        "%USERPROFILE%\\.grok\\bin\\grok.exe",
        "grok.exe",
        "grok",
    ],
}

GROK_REMOTE_BINARY_CANDIDATES: dict[str, list[str]] = {
    "linux": ["grok-remote", "~/grok-proxy/grok-remote"],
    "macos": ["grok-remote", "~/grok-proxy/grok-remote"],
    "wsl": ["grok-remote", "~/grok-proxy/grok-remote"],
    "windows": ["grok-remote.exe"],
}

GROK_BINARY_CANDIDATES: dict[str, list[str]] = {
    platform: [*GROK_BARE_BINARY_CANDIDATES[platform], *GROK_REMOTE_BINARY_CANDIDATES[platform]]
    for platform in GROK_BARE_BINARY_CANDIDATES
}

# Keep in sync with installer/ai_agents_skills/kimi.py KIMI_BARE_CLI_TOOL_SPEC.
KIMI_BINARY_CANDIDATES: dict[str, list[str]] = {
    "linux": [
        "kimi",
        "~/.local/bin/kimi",
        "~/.kimi-code/bin/kimi",
    ],
    "macos": [
        "kimi",
        "~/.local/bin/kimi",
        "~/.kimi-code/bin/kimi",
        "/opt/homebrew/bin/kimi",
        "/usr/local/bin/kimi",
    ],
    "wsl": [
        "kimi",
        "~/.local/bin/kimi",
        "~/.kimi-code/bin/kimi",
    ],
    "windows": [
        "%USERPROFILE%\\.kimi-code\\bin\\kimi.exe",
        "kimi.exe",
        "kimi",
    ],
}

PROVIDER_SPECS: dict[str, dict[str, Any]] = {
    "claude": {
        "binaries": ["claude"],
        "args": ["-p", "{prompt}", "--dangerously-skip-permissions"],
        "consent_note": "--dangerously-skip-permissions grants full tool autonomy",
        "model_env": "AAS_CLAUDE_LATEST_MODEL",
        "model_flag": "--model",
        "reasoning_env": "AAS_CLAUDE_HIGHEST_THINKING",
        "reasoning_flag": "--effort",
    },
    "codex": {
        "binaries": ["codex"],
        "args": [
            "exec",
            "--ignore-user-config",
            "-c",
            "model_provider=\"openai\"",
            "--full-auto",
            "{prompt}",
        ],
        "consent_note": "--full-auto runs with the workspace-write sandbox",
        "model_env": "AAS_CODEX_LATEST_MODEL",
        "model_flag": "--model",
        "reasoning_env": "AAS_CODEX_HIGHEST_THINKING",
        "reasoning_config": "model_reasoning_effort",
    },
    "deepseek": {
        "binaries": ["codewhale", "codewhale-tui", "deepseek"],
        "args": [
            "--provider",
            "deepseek",
            "--no-project-config",
            "-C",
            "{dir}",
            "exec",
            "--auto",
            "{prompt}",
        ],
        "binary_args": {
            "codewhale": [
                "--provider",
                "deepseek",
                "--no-project-config",
                "-C",
                "{dir}",
                "exec",
                "--auto",
                "{prompt}",
            ],
            "deepseek": [
                "--provider",
                "deepseek",
                "--no-project-config",
                "-C",
                "{dir}",
                "exec",
                "--auto",
                "{prompt}",
            ],
            "codewhale-tui": [
                "-w",
                "{dir}",
                "exec",
                "--provider",
                "deepseek",
                "--auto",
                "{prompt}",
            ],
        },
        "consent_note": "--auto enables tool-backed agent mode with auto-approvals",
        "model_env": "AAS_DEEPSEEK_LATEST_MODEL",
        "model_flag": "--model",
    },
    "opencode": {
        "binaries": ["opencode"],
        "args": ["run", "{prompt}"],
        "consent_note": "runs with the opencode agent's configured permissions",
        "model_env": "AAS_OPENCODE_LATEST_MODEL",
        "model_flag": "--model",
    },
    "copilot": {
        "binaries": ["copilot"],
        "args": ["-p", "{prompt}", "--allow-all-tools"],
        "consent_note": "--allow-all-tools grants full tool autonomy",
        "model_env": "AAS_COPILOT_LATEST_MODEL",
        "model_flag": "--model",
    },
    "antigravity": {
        # Google Antigravity CLI is `agy`.
        # REQUIRED order: -p, then prompt value, then autonomy flags.
        # -p/--print consumes the NEXT argv as the prompt — never put
        # --dangerously-skip-permissions (or any flag) between -p and the prompt.
        # agy does not read the user prompt from stdin (host-proved 2026-07-25).
        # `gemini` is an alternate binary (standalone Gemini CLI); args differ.
        "binaries": ["agy", "gemini"],
        "args": ["-p", "{prompt}", "--dangerously-skip-permissions"],
        "binary_args": {
            "agy": ["-p", "{prompt}", "--dangerously-skip-permissions"],
            "gemini": ["--yolo", "-p", "{prompt}"],
        },
        "consent_note": "agy --dangerously-skip-permissions (or gemini --yolo) auto-approves all actions",
        "model_env": "AAS_ANTIGRAVITY_LATEST_MODEL",
        "model_flag": "--model",
    },
    "grok": {
        # Short display list for error messages; full platform lists in GROK_BINARY_CANDIDATES.
        "binaries": ["grok", "grok-remote"],
        "args": ["-p", "{prompt}", "--yolo"],
        "consent_note": (
            "--yolo auto-approves tools for unattended loops; "
            "bare Grok is preferred and grok-remote is an exact-model fallback; "
            "override AAS_AUTOLOOP_BIN_GROK or AAS_GROK"
        ),
        "platform_candidates": GROK_BINARY_CANDIDATES,
        "model_env": "AAS_GROK_LATEST_MODEL",
        "model_flag": "-m",
    },
    "kimi": {
        "binaries": ["kimi"],
        "args": ["-p", "{prompt}", "--auto"],
        "consent_note": (
            "--auto is fully autonomous permission mode for unattended loops; "
            "override AAS_AUTOLOOP_BIN_KIMI or AAS_KIMI; "
            "do not force --yolo unless the operator opts in"
        ),
        "platform_candidates": KIMI_BINARY_CANDIDATES,
        "model_env": "AAS_KIMI_LATEST_MODEL",
        "model_flag": "-m",
    },
}

# Written into iteration logs after the host prompt so classification can strip
# host text before matching provider failure signals.
HOST_PROMPT_SENTINEL = "# --- END HOST PROMPT ---"

# Goal-Focus enforce + trusted-local drive primaries. Each must have a
# reviewed private (non-argv) prompt transport under prepare_primary_private_prompt_transport
# and resolve_provider_command host-attested argv scrubbing.
TRUSTED_LOCAL_ENFORCE_PRIMARY_PROVIDERS = frozenset({"claude", "codex", "grok"})

# Auth/session death: rotate or stop; never treat as credit wait.
# Prefer multi-token phrases; avoid bare \b401\b (hex / counts false positives).
AUTH_PATTERN = re.compile(
    r"token_invalidated|refresh_token_invalidated|"
    r"authentication token has been invalidated|"
    r"access token could not be refreshed|"
    r"Your access token could not be refreshed|"
    r"refresh token was revoked|"
    r"Please log out and sign in again|"
    r"Please try signing in again|"
    r"\b401 Unauthorized\b|HTTP\s*401\b",
    re.IGNORECASE,
)

# Provider credit/quota outage → pause-and-retry (not a hard failure).
# No bare "quota" (matches the fixed iteration prompt and skill dumps).
# No bare "billing"/"overloaded" (common in policy prose dumps).
# Includes Claude/Anthropic weekly caps: "You've hit your weekly limit · resets …"
QUOTA_PATTERN = re.compile(
    r"HTTP\s*402|\b402\b\s*Payment Required|"
    r"HTTP\s*429|\b429\b\s*Too Many|"
    r"rate.?limit(?:ed|s)?|"
    r"quota[ _-]?(?:exceeded|limit|reached|exhausted)|"
    r"credit ?balance|"
    r"insufficient[ _-]?(?:credit|funds|quota)|"
    r"usage ?limit|"
    r"usage ?balance(?: (?:exhausted|depleted))?|"
    r"weekly ?limit|"
    r"monthly ?limit|"
    r"hit your (?:weekly |monthly |usage )?limit|"
    r"you(?:'|’)ve hit your (?:weekly |monthly |usage )?limit|"
    r"out of credits?|"
    r"credits? (?:has |have |is |are )?(?:been )?"
    r"(?:run out|exhausted|depleted)|"
    r"limit (?:reached|exceeded)|"
    r"too many requests|"
    r"resets?\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?",
    re.IGNORECASE,
)

# Hard caps that will not clear in a few minutes: use a short backoff between the
# N consecutive quota signals, then exit 5 so the supervisor can switch primary.
QUOTA_SHORT_BACKOFF_PATTERN = re.compile(
    r"HTTP\s*402|\b402\b\s*Payment Required|"
    r"usage ?balance(?: (?:exhausted|depleted))?|"
    r"weekly ?limit|monthly ?limit|"
    r"hit your (?:weekly |monthly |usage )?limit|"
    r"you(?:'|’)ve hit your (?:weekly |monthly |usage )?limit|"
    r"resets?\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?|"
    r"out of credits?|"
    r"insufficient[ _-]?(?:credit|funds|quota)",
    re.IGNORECASE,
)
QUOTA_SHORT_BACKOFF_S = 15

# Directory / label names that must not be used as the sole notify identity
# when a research goal or explicit title is available.
_BANNED_NOTIFY_GENERIC_NAMES = frozenset(
    {
        "loop",
        "research",
        "research_loop",
        "research-loop",
        "researchloop",
        "autonomous",
        "autoloop",
        "autonomous_loop",
        "autonomous-loop",
        "run",
        "driver",
        "arl",
    }
)


def build_classification_text(log_tail: str, prompt: str | None = None) -> str:
    """Strip host prompt / sentinel so failure patterns match provider output."""
    text = log_tail or ""
    if prompt:
        text = text.replace(prompt, "\n")
    if HOST_PROMPT_SENTINEL in text:
        text = text.rsplit(HOST_PROMPT_SENTINEL, 1)[-1]
    # Drop residual host-prompt anchor lines even when the full prompt object
    # is unavailable or drifted slightly.
    drop_anchors = (
        "If you hit a credit or quota error, exit nonzero",
        "You are one iteration of a bounded autonomous research loop",
        "the headless driver owns the stop conditions",
    )
    kept: list[str] = []
    for line in text.splitlines():
        if any(anchor in line for anchor in drop_anchors):
            continue
        kept.append(line)
    return "\n".join(kept)


def classify_iteration_failure(
    log_tail: str, prompt: str | None = None
) -> str:
    """Return 'auth' | 'quota' | 'failure' for a nonzero iteration exit."""
    text = build_classification_text(log_tail, prompt)
    if AUTH_PATTERN.search(text):
        return "auth"
    if QUOTA_PATTERN.search(text):
        return "quota"
    return "failure"


def interruptible_sleep(seconds: float, run_dir: Path, *, slice_s: float = 5.0) -> bool:
    """Sleep in short slices. Return True if STOP_REQUESTED or PAUSE appears."""
    deadline = time.time() + max(0.0, float(seconds))
    stop = run_dir / "STOP_REQUESTED"
    pause = run_dir / "PAUSE"
    while time.time() < deadline:
        if stop.is_file() or pause.is_file():
            return True
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(float(slice_s), remaining))
    return stop.is_file() or pause.is_file()


def _load_remote_bridge_mod() -> Any | None:
    try:
        skills_root = Path(__file__).resolve().parent.parent
        rb_path = skills_root / "remote-bridge" / "remote_bridge.py"
        if not rb_path.is_file():
            return None
        import importlib.util

        # Never write __pycache__ into the canonical runtime tree (inventory CI).
        prev = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec = importlib.util.spec_from_file_location("aas_remote_bridge", rb_path)
            if spec is None or spec.loader is None:
                return None
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            return mod
        finally:
            sys.dont_write_bytecode = prev
    except Exception:  # noqa: BLE001
        return None


def peek_remote_inbox_for_prompt(job_id: str | None = None) -> str:
    """Read-only inbox preview (no claim/consume). Safe for agent-cmd inspection."""
    jid = job_id or os.environ.get("AAS_REMOTE_JOB_ID")
    if not jid:
        return ""
    try:
        mod = _load_remote_bridge_mod()
        if mod is None:
            return ""
        return mod.Mailbox().peek_inbox_block(jid) or ""
    except Exception:  # noqa: BLE001
        return ""


def claim_remote_inbox_for_drive(
    job_id: str | None = None, claimer: str | None = None
) -> tuple[str, list[str], dict[str, str], str]:
    """Drive-only exclusive claim. Returns (block, item_ids, fences, claimer)."""
    jid = job_id or os.environ.get("AAS_REMOTE_JOB_ID")
    if not jid:
        return "", [], {}, ""
    who = claimer or f"arl-drive-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        mod = _load_remote_bridge_mod()
        if mod is None:
            return "", [], {}, who
        block, ids, fences = mod.Mailbox().format_inbox_block(jid, claimer=who)
        return block or "", ids, fences, who
    except Exception:  # noqa: BLE001
        return "", [], {}, who


def finalize_remote_inbox_claim(
    job_id: str,
    item_ids: list[str],
    *,
    claimer: str,
    fences: dict[str, str],
    success: bool,
) -> None:
    """Consume on success; ownership-checked requeue on failure."""
    if not job_id or not item_ids:
        return
    try:
        mod = _load_remote_bridge_mod()
        if mod is None:
            return
        mb = mod.Mailbox()
        if success:
            mb.consume_claimed(job_id, item_ids, claimer=claimer, fences=fences)
        else:
            mb.requeue_claimed(job_id, item_ids, claimer=claimer, fences=fences)
    except Exception:  # noqa: BLE001
        return


def iteration_prompt(
    run_dir: Path,
    *,
    inbox_block: str | None = None,
    panel_enabled: bool = False,
    panel_iter_dir: Path | None = None,
) -> str:
    """The standard one-iteration contract handed to a headless agent.

    Pure by default: does **not** claim/consume remote-bridge inbox.
    Drive sets AAS_DRIVE_INBOX_BLOCK (or pass inbox_block) after exclusive claim.
    When host panel is enabled, appends the hybrid-model ban on nested panel CLIs.
    """
    if goal_focus_is_enforced(run_dir):
        base = (
            "You are one execution iteration of a bounded autonomous research loop "
            "governed by Goal Focus v2 and the autonomous-loop-enforcement policy. "
            f"The loop directory is: {run_dir}. Do exactly ONE iteration now: "
            "(1) read goal_contract.json, approach_registry.json, current_plan.json, "
            "recovery.md, loop_state.json, budget.json, and the tail of iterations.jsonl; "
            "(2) execute only current_plan.next_action within its campaign, approach, "
            "scope lock, and evidence gates; (3) run the narrowest meaningful local "
            "checks, but do not claim that you supplied the independent host review; "
            "(4) call the autonomous-research-loop-runtime append-iteration helper "
            "exactly once, including --completed-summary, --current-summary, "
            "--next-action, both progress deltas, actual compute provenance, and all "
            "changed obligation ids. Assign a unique --claim-id to every material "
            "result asserted in the staged output. The host has prepared "
            "<loop>/.goal_focus/evidence/$AAS_AUTOLOOP_CANDIDATE_ID/. Write each "
            "new evidence artifact there under one safe opaque artifact name, and "
            "pass only that artifact name (not a path) as --evidence-id. Do not use "
            "an existing project/configuration/credential file as evidence. Stage at "
            "least one such concrete --evidence-id that supports the claims; undeclared or evidence-free "
            "prose claims cannot be banked. In enforce mode this stages a pending candidate; "
            "it does not bank the result. Do not directly edit iterations.jsonl, "
            "loop_state.json, budget.json, current_plan.json, or the managed recovery "
            "block; (5) exit after staging. The host driver owns independent result "
            "review, atomic finalization, notifications, and all stop conditions. "
            "If you hit a credit or quota error, exit nonzero with the provider's "
            "error text visible in your output."
        )
    else:
        base = (
            "You are one iteration of a bounded autonomous research loop governed by "
            "the autonomous-research-loop skill and the autonomous-loop-enforcement "
            f"policy. The loop directory is: {run_dir}. Do exactly ONE iteration now: "
            "(1) read recovery.md, loop_state.json, budget.json, and the tail of "
            "iterations.jsonl in that directory; (2) execute the single next action "
            "they record, following the loop's single-path policy and evidence gates; "
            "(3) verify the result independently as the loop protocol requires; "
            "(4) append exactly one iteration record to iterations.jsonl (prefer the "
            "autonomous-research-loop-runtime append-iteration helper) and update "
            "loop_state.json, budget.json, and recovery.md so the next iteration can "
            "resume from files alone; (5) append a 3-6 sentence human-readable entry "
            "to PROGRESS_REPORT.md in the loop directory (create it with a short "
            "header if absent): what this iteration did, what it concluded, whether "
            "it was independently verified, and what comes next — written for the "
            "project owner, not for the next agent; (6) exit. Do not run more than one iteration. "
            "iterations.jsonl is append-only history: never truncate, rewrite, or "
            "reset it or the loop's earlier records, even to recover from your own "
            "failed append attempts — fix the new record instead (append-iteration "
            "--dry-run shows every guard without writing). "
            "Do not stop the loop yourself: the headless driver owns the stop "
            "conditions. If you hit a credit or quota error, exit nonzero with the "
            "provider's error text visible in your output."
        )
    base = base + compute_policy_addon(run_dir)
    if panel_enabled:
        base = base + panel_prompt_addon(run_dir, panel_iter_dir)
    if goal_focus_is_enforced(run_dir):
        try:
            base = base + goal_focus_v2.goal_focus_prompt_addon(run_dir)
        except Exception:  # noqa: BLE001 - prompt construction surfaces at dispatch gate
            pass
    elif goal_focus_runtime_mode(run_dir) == "monitor":
        try:
            base = base + goal_focus_v2.goal_focus_prompt_addon(run_dir)
        except Exception:  # noqa: BLE001 - monitor findings are advisory
            pass
        if is_goal_priority_active(run_dir):
            base = base + goal_priority_prompt_addon(run_dir)
    elif is_goal_priority_active(run_dir):
        base = base + goal_priority_prompt_addon(run_dir)
    # Formal after goal_priority (subordinate to single-path + hard replan); empty when off.
    try:
        base = base + formal_policy_prompt_addon(run_dir)
    except Exception:  # noqa: BLE001 — prompt construction must never fail
        pass
    block = inbox_block if inbox_block is not None else os.environ.get("AAS_DRIVE_INBOX_BLOCK")
    if block:
        return base + "\n\n" + block
    # Inspection path: read-only peek (no claim).
    peek = peek_remote_inbox_for_prompt()
    if peek:
        return (
            base
            + "\n\n"
            + peek
            + "\n(Note: peek-only; drive claims items transactionally per iteration.)"
        )
    return base


def _minimal_child_environment(parent: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if parent is None else parent
    home = source.get("HOME") or str(Path.home())
    child = {
        "HOME": home,
        "PATH": "/usr/bin:/bin" if os.name == "posix" else os.defpath,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    for name in (
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
    ):
        value = source.get(name)
        if value:
            child[name] = value
    return child


def _canonical_remote_bridge_authority(parent: dict[str, str] | None = None) -> Path | None:
    source = os.environ if parent is None else parent
    home_text = source.get("HOME") or str(Path.home())
    home = Path(home_text)
    if not home.is_absolute():
        return None
    if os.name == "nt":
        candidates = [
            home / "AppData" / "Roaming" / "remote-bridge" / "secrets.json",
            home / "AppData" / "Local" / "remote-bridge" / "secrets.json",
        ]
    else:
        candidates = [home / ".config" / "remote-bridge" / "secrets.json"]
        if sys.platform == "darwin":
            candidates.append(
                home / "Library" / "Application Support" / "remote-bridge" / "secrets.json"
            )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def remote_notify_environment(parent: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for the exact Remote Bridge child, excluding provider/compute lanes."""

    source = os.environ if parent is None else parent
    child = _minimal_child_environment(source)
    authority = _canonical_remote_bridge_authority(source)
    if authority is not None:
        child["REMOTE_BRIDGE_SECRETS_FILE"] = str(authority)
    strict_channel = source.get("AAS_REMOTE_STRICT_NOTIFY_CHANNEL", "").strip().lower()
    if strict_channel in {"zulip", "telegram"}:
        child["AAS_REMOTE_STRICT_NOTIFY_CHANNEL"] = strict_channel
    return child


def raw_notify_environment(
    payload: dict[str, str], parent: dict[str, str] | None = None
) -> dict[str, str]:
    """Environment for an explicitly enabled raw hook: base OS fields + event only."""

    child = _minimal_child_environment(parent)
    child.update({str(key): str(value) for key, value in payload.items() if key.startswith("AUTOLOOP_")})
    return child


def resolve_remote_notify_argv(
    channel: str,
    text: str,
    job_id: str | None = None,
    *,
    html: str | None = None,
    event_json_stdin: bool = False,
) -> list[str] | None:
    """Build argv for remote-bridge send (no shell). Returns None if unavailable."""
    if os.environ.get("AAS_ALLOW_RAW_NOTIFY_CMD") == "1" and os.environ.get("AAS_AUTOLOOP_NOTIFY_CMD"):
        return None  # caller may use shell escape hatch
    skills_root = Path(__file__).resolve().parent.parent
    rb = skills_root / "remote-bridge" / "run_remote_bridge.sh"
    if not rb.is_file():
        return None
    if event_json_stdin:
        argv = [str(rb), "send", "--event-json", "-"]
    else:
        argv = [str(rb), "send", "--text", text]
        if html:
            argv.extend(["--html", html])
    if channel in {"zulip", "telegram", "both"}:
        argv.extend(["--channel", channel])
    if job_id:
        argv.extend(["--job", job_id])
    return argv


_NOTIFY_OFF_TOKENS = frozenset({"", "off", "none", "no", "0", "false", "disable", "disabled"})
_NOTIFY_ON_CHANNELS = frozenset({"zulip", "telegram", "both", "auto"})


def normalize_notify_token(raw: str | None) -> str | None:
    """Return canonical channel token, 'auto', 'off', or None if unset."""
    if raw is None:
        return None
    token = str(raw).strip().lower()
    if token in _NOTIFY_OFF_TOKENS:
        return "off"
    if token in _NOTIFY_ON_CHANNELS:
        return token
    return None


def detect_configured_notify_channels() -> list[str]:
    """Channels with usable credentials in remote-bridge secrets/env (fail-open)."""
    try:
        mod = _load_remote_bridge_mod()
        if mod is None:
            return []
        cfg = mod.build_config()
        channels: list[str] = []
        zulip = cfg.zulip or {}
        telegram = cfg.telegram or {}
        if zulip.get("site") and zulip.get("email") and zulip.get("api_key"):
            channels.append("zulip")
        if telegram.get("bot_token"):
            channels.append("telegram")
        # Prefer declared notify_channels when subset of ready channels.
        declared = [str(c).lower() for c in (cfg.notify_channels or [])]
        if declared:
            ready = [c for c in declared if c in channels]
            if ready:
                return ready
        return channels
    except Exception:  # noqa: BLE001 - notify discovery is best-effort
        return []


def auto_notify_channel_from_secrets() -> str | None:
    """Pick default channel when secrets are configured; else None.

    Policy: **Zulip is the default primary**. Telegram is not dual-selected here;
    remote-bridge falls back to Telegram only when a Zulip send fails.
    """
    channels = detect_configured_notify_channels()
    if not channels:
        return None
    if "zulip" in channels:
        return "zulip"
    if "telegram" in channels:
        return "telegram"
    return None


def read_loop_notify_policy(run_dir: Path) -> str | None:
    """Notify preference stored on the loop (loop_state.json)."""
    try:
        state = read_json(loop_paths(run_dir)["state"])
    except Exception:  # noqa: BLE001
        return None
    for key in ("notify_channel", "notify", "autoloop_notify"):
        token = normalize_notify_token(state.get(key) if isinstance(state.get(key), str) else None)
        if token is not None:
            return token
    return None


def write_loop_notify_policy(run_dir: Path, channel: str | None) -> None:
    """Persist notify policy on loop_state (best-effort, never raises).

    Default policy is Zulip-primary with Telegram fallback. ``notify_fallback``
    is recorded for operators; remote-bridge enforces stop-on-first-success.
    """
    try:
        paths = loop_paths(run_dir)
        state = read_json(paths["state"])
        if channel and channel != "off":
            state["notify_channel"] = channel
            state["notify_policy"] = "on"
            # Telegram is the automatic fallback unless the operator forced
            # Telegram-only (no further fallback) or silenced notify.
            if channel == "telegram":
                state["notify_fallback"] = None
            else:
                state["notify_fallback"] = "telegram"
        else:
            state["notify_channel"] = "off"
            state["notify_policy"] = "off"
            state["notify_fallback"] = None
        state["updated_at"] = utc_now()
        write_json(paths["state"], state)
    except Exception:  # noqa: BLE001
        return


def read_registry_notify_policy(reg: Path, run_dir: Path) -> str | None:
    """Notify preference from an armed registry entry for this loop dir."""
    try:
        target = str(run_dir.resolve())
        for _, entry in list_registry_entries(reg):
            if entry.get("loop_dir") == target:
                token = normalize_notify_token(
                    entry.get("notify_channel") if isinstance(entry.get("notify_channel"), str) else None
                )
                if token is not None:
                    return token
    except Exception:  # noqa: BLE001
        return None
    return None


def resolve_notify_channel(
    *,
    explicit: str | None = None,
    run_dir: Path | None = None,
    registry: Path | None = None,
    default_auto: bool = True,
) -> str | None:
    """Resolve effective notify channel for drive/watch/arm.

    Order (first decisive wins):
      1. explicit CLI token (off or a concrete channel)
      2. AAS_AUTOLOOP_NOTIFY / AAS_REMOTE_NOTIFY env
      3. loop_state.json notify_channel
      4. armed registry entry notify_channel
      5. if auto was requested, or nothing decided and default_auto:
         secrets-backed auto channel (or None if unconfigured)

    ``auto`` is not decisive at any level: it means "I have no opinion, ask the
    next source, and fall back to secrets". ``drive``/``watch`` default their
    ``--notify`` flag to ``auto``, so a decisive token would make that default
    outrank every later source and strand levels 2-4 as dead code -- which is
    how an ``AAS_AUTOLOOP_NOTIFY=off`` guard could be set and still not silence
    a run whose secrets happened to be configured.

    Returns a concrete channel (zulip|telegram|both) or None when disabled/unavailable.
    Never raises.
    """
    candidates: list[str | None] = [
        normalize_notify_token(explicit),
        normalize_notify_token(os.environ.get("AAS_AUTOLOOP_NOTIFY")),
        normalize_notify_token(os.environ.get("AAS_REMOTE_NOTIFY")),
    ]
    if run_dir is not None:
        candidates.append(read_loop_notify_policy(run_dir))
    if registry is not None and run_dir is not None:
        candidates.append(read_registry_notify_policy(registry, run_dir))

    chosen: str | None = None
    for token in candidates:
        if token is None:
            continue
        if token == "off":
            return None
        if token == "auto":
            # Remember, but keep scanning: a later source may be decisive.
            chosen = "auto"
            continue
        if token in {"zulip", "telegram", "both"}:
            return token

    if chosen == "auto" or (chosen is None and default_auto):
        return auto_notify_channel_from_secrets()
    return None


def provider_env_key(provider: str) -> str:
    return provider.upper().replace("-", "_")


PROVIDER_ENDPOINT_IDENTITY_VARS: dict[str, frozenset[str]] = {
    "claude": frozenset(
        {
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_API_URL",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY",
        }
    ),
    "codex": frozenset(
        {
            "OPENAI_BASE_URL",
            "OPENAI_API_BASE",
            "CODEX_BASE_URL",
            "AZURE_OPENAI_ENDPOINT",
        }
    ),
    "deepseek": frozenset(
        {
            "DEEPSEEK_BASE_URL",
            "DEEPSEEK_API_BASE",
            "CODEWHALE_BASE_URL",
            "CODEWHALE_PROVIDER",
        }
    ),
    "grok": frozenset({"XAI_BASE_URL", "GROK_BASE_URL"}),
    "antigravity": frozenset(
        {
            "GOOGLE_GEMINI_BASE_URL",
            "GOOGLE_VERTEX_BASE_URL",
            "GEMINI_NEXT_GEN_API_BASE_URL",
        }
    ),
}

# A tool-enabled primary receives only ordinary process plumbing, explicit ARL
# control values, and credentials/config for its one host-attested provider.
# Notification, remote-bridge, CI, and other-provider secrets are intentionally
# absent even if the supervising process has them. Trusted-local execution may
# additionally receive only the Hetzner/Kaggle variables selected by the exact
# host-pinned current-plan compute policy.
PRIMARY_BASE_ENV_ALLOWLIST = frozenset(
    {
        "APPDATA",
        "COLORTERM",
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOCALAPPDATA",
        "LOGNAME",
        "NO_COLOR",
        "PATH",
        "PATHEXT",
        "SHELL",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "TZ",
        "USER",
        "USERPROFILE",
        "WINDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
    }
)
PRIMARY_PROVIDER_ENV_ALLOWLIST: dict[str, frozenset[str]] = {
    "claude": frozenset(
        {
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_API_KEY",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "CLAUDE_CONFIG_DIR",
        }
    ),
    "codex": frozenset({"OPENAI_API_KEY", "CODEX_HOME"}),
    "codewhale": frozenset({"DEEPSEEK_API_KEY", "CODEWHALE_HOME"}),
    "deepseek": frozenset({"DEEPSEEK_API_KEY", "CODEWHALE_HOME"}),
    "grok": frozenset({"GROK_API_KEY", "XAI_API_KEY", "GROK_CONFIG_DIR"}),
    "antigravity": frozenset(
        {"GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_CONFIG_DIR"}
    ),
    "copilot": frozenset(
        {
            "COPILOT_GITHUB_TOKEN",
            "COPILOT_PROVIDER_API_KEY",
            "COPILOT_PROVIDER_BEARER_TOKEN",
            "GITHUB_TOKEN",
            "GH_TOKEN",
        }
    ),
    "kimi": frozenset({"KIMI_API_KEY", "MOONSHOT_API_KEY", "KIMI_CONFIG_DIR"}),
    "opencode": frozenset({"OPENCODE_API_KEY", "OPENCODE_CONFIG_DIR"}),
}
PRIMARY_RUNTIME_ENV_ALLOWLIST = frozenset({"AAS_RUNTIME_ROOT", "AAS_RUNTIME_PYTHON"})
PRIMARY_COMPUTE_LANE_ENV_ALLOWLIST: dict[str, frozenset[str]] = {
    "hetzner": frozenset({"HCLOUD_TOKEN", "HCLOUD_SSH_KEYS"}),
    "kaggle": frozenset({"KAGGLE_API_TOKEN", "KAGGLE_CONFIG_DIR"}),
    "modal": frozenset({"MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"}),
}


def _host_pinned_primary_compute_lanes(run_dir: str | Path | None) -> frozenset[str]:
    """Return reviewed lanes that remain inside a structured operator pin.

    Credential admission is intentionally stricter than compute-result
    validation: a model-selected ``allowed_services`` value cannot create its
    own credential authority.  Any missing or unreadable authority fails closed
    to an empty set so ambient cloud credentials never become global primary
    process environment.
    """

    if run_dir is None:
        return frozenset()
    try:
        root = Path(run_dir)
        plan = goal_focus_v2.load_current_plan(root, required=True)
        # The current plan is host-committed authority. Model advice may select
        # allowed_services, but it cannot create the separately persisted
        # user_allowed_services pin used by this resolver.
        pinned, forbidden, _free_text_only = goal_focus_v2._pinned_compute_policy(
            root, plan
        )
        policy = (
            plan.get("compute_policy")
            if isinstance(plan.get("compute_policy"), Mapping)
            else {}
        )
        selected = goal_focus_v2._policy_allowed(policy)
        plan_forbidden = goal_focus_v2._compute_services(
            policy.get("forbidden_services")
        )
    except (OSError, TypeError, ValueError):
        return frozenset()
    if not pinned or not selected:
        return frozenset()
    return frozenset(
        (pinned & selected) - forbidden - plan_forbidden
    ) & PRIMARY_COMPUTE_LANE_ENV_ALLOWLIST.keys()


def build_primary_child_env(
    provider: str | None,
    *,
    executable_attestation: Mapping[str, Any] | None,
    control: Mapping[str, object],
    environ: Mapping[str, str] | None = None,
    include_provider_credentials: bool = True,
    include_compute_credentials: bool = False,
    compute_policy_run_dir: str | Path | None = None,
) -> dict[str, str]:
    """Build the strict environment for one primary execution process."""

    source = os.environ if environ is None else environ
    normalized = str(provider or "custom").strip().lower().replace("_", "-")
    child = {
        name: str(source[name])
        for name in PRIMARY_BASE_ENV_ALLOWLIST | PRIMARY_RUNTIME_ENV_ALLOWLIST
        if str(source.get(name) or "")
    }
    attested = bool(
        isinstance(executable_attestation, Mapping)
        and executable_attestation.get("provider") == normalized
        and executable_attestation.get("source")
        == "trusted_operator_provider_identity.v1"
    )
    if attested and include_provider_credentials:
        for name in PRIMARY_PROVIDER_ENV_ALLOWLIST.get(normalized, ()):
            if str(source.get(name) or ""):
                child[name] = str(source[name])
    if attested and include_compute_credentials:
        for lane in _host_pinned_primary_compute_lanes(compute_policy_run_dir):
            for name in PRIMARY_COMPUTE_LANE_ENV_ALLOWLIST[lane]:
                if str(source.get(name) or ""):
                    child[name] = str(source[name])
    for name, value in control.items():
        if str(name) == "AUTOLOOP_PROMPT":
            continue
        text = str(value if value is not None else "")
        if text:
            child[str(name)] = text
    return child


def provider_identity_overrides(
    provider: str, environ: Mapping[str, str] | None = None
) -> list[str]:
    """Return process-shape or endpoint overrides invalidating family attribution."""

    env = os.environ if environ is None else environ
    key = provider_env_key(provider)
    candidates = {
        f"AAS_AUTOLOOP_BIN_{key}",
        f"AAS_AUTOLOOP_CMD_{key}",
        f"AAS_AUTOLOOP_ARGS_{key}",
        f"AAS_{key}",
    }
    if provider == "grok":
        candidates.add("AAS_GROK")
    candidates.update(PROVIDER_ENDPOINT_IDENTITY_VARS.get(provider, ()))
    return sorted(name for name in candidates if str(env.get(name) or "").strip())


def runtime_platform_name() -> str:
    """Coarse platform key for binary candidate lists (linux/macos/wsl/windows)."""
    if os.name == "nt" or sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    try:
        if "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower():
            return "wsl"
    except OSError:
        pass
    return "linux"


def expand_env_in_path(raw: str, environ: dict[str, str]) -> str:
    """Expand ${VAR}, $VAR, and %VAR% using *environ*, then ~."""
    text = raw
    # Windows %VAR%
    while True:
        start = text.find("%")
        if start < 0:
            break
        end = text.find("%", start + 1)
        if end < 0:
            break
        name = text[start + 1 : end]
        if not name:
            break
        text = text[:start] + environ.get(name, environ.get(name.upper(), "")) + text[end + 1 :]
    # ${VAR} then $VAR (POSIX-ish)
    def _dollar_brace(match: re.Match[str]) -> str:
        return environ.get(match.group(1), "")

    text = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", _dollar_brace, text)
    text = re.sub(
        r"\$([A-Za-z_][A-Za-z0-9_]*)",
        lambda m: environ.get(m.group(1), ""),
        text,
    )
    # Expand ~ using *environ* HOME/USERPROFILE so isolated resolves (tests) work.
    home = environ.get("HOME") or environ.get("USERPROFILE")
    if text == "~":
        text = home or str(Path.home())
    elif text.startswith("~/") or text.startswith("~\\"):
        text = str(Path(home or Path.home()) / text[2:])
    elif text.startswith("~"):
        # Other ~user forms: fall back to Path.expanduser (host home).
        text = str(Path(text).expanduser())
    return text


def candidate_is_usable(raw: str, environ: dict[str, str]) -> tuple[bool, str]:
    """Return (usable, resolved_path_or_name) for a binary candidate."""
    expanded = expand_env_in_path(raw, environ)
    if not expanded:
        return False, raw
    if os.name == "nt" and Path(expanded).name.lower() == "grok-remote.cmd":
        return False, expanded
    path = Path(expanded)
    # Absolute / explicit path (after expand): must exist as a file.
    if path.is_absolute() or os.sep in expanded or (os.altsep and os.altsep in expanded):
        try:
            if path.is_file():
                return True, str(path)
        except OSError:
            return False, expanded
        return False, expanded
    # Bare command name: PATH lookup (Windows PATHEXT applies via shutil.which).
    # Honor *environ* PATH when provided (tests and isolated resolve).
    path_env = environ.get("PATH")
    located = shutil.which(expanded, path=path_env) if path_env is not None else shutil.which(expanded)
    if located:
        if os.name == "nt" and Path(located).name.lower() == "grok-remote.cmd":
            return False, located
        return True, located
    # Relative path that exists as a file (e.g. ./grok)
    try:
        if path.is_file():
            return True, str(path.resolve())
    except OSError:
        pass
    return False, expanded


def provider_binary_candidates(
    provider: str, environ: dict[str, str] | None = None, platform: str | None = None
) -> list[str]:
    """Ordered binary candidates for a provider (for probe + error messages)."""
    env = os.environ if environ is None else environ
    if provider not in PROVIDER_SPECS:
        raise ValueError(f"unknown provider: {provider}")
    spec = PROVIDER_SPECS[provider]
    plat = platform or runtime_platform_name()
    platform_map = spec.get("platform_candidates")
    if isinstance(platform_map, dict):
        return list(platform_map.get(plat) or platform_map.get("linux") or spec["binaries"])
    return list(spec["binaries"])


def parse_grok_available_models(output: str) -> list[str]:
    """Parse only anchored available-model rows from ``grok models``."""
    models: list[str] = []
    for line in output.splitlines():
        match = GROK_AVAILABLE_MODEL_LINE_RE.fullmatch(line)
        if match is not None and match.group("model") not in models:
            models.append(match.group("model"))
    return models


def probe_grok_model_membership(
    binary: str,
    resolved_model: str,
    environ: dict[str, str],
    *,
    timeout: int = 10,
) -> dict[str, Any]:
    """Confirm exact resolved-model membership before automatic proxy fallback."""
    result: dict[str, Any] = {
        "schema_version": GROK_MODEL_PROBE_SCHEMA,
        "status": "not-confirmed",
        "resolved_model": resolved_model,
        "available_models": [],
        "reason_code": "probe_failed",
    }
    if GROK_MODEL_ID_RE.fullmatch(resolved_model) is None:
        result["reason_code"] = "resolved_model_invalid"
        return result
    probe_env = dict(environ)
    probe_env.setdefault("NO_COLOR", "1")
    probe_env.setdefault("TERM", "dumb")
    private_umask = provider_subprocess_options("grok")
    try:
        completed = subprocess.run(
            [binary, "models"],
            text=True,
            capture_output=True,
            timeout=timeout,
            env=probe_env,
            check=False,
            **private_umask,
        )
    except subprocess.TimeoutExpired:
        result["reason_code"] = "probe_timed_out"
        return result
    except OSError:
        result["reason_code"] = "probe_could_not_execute"
        return result
    models = parse_grok_available_models(completed.stdout)
    result["available_models"] = models
    if completed.returncode != 0:
        result["reason_code"] = "probe_exit_nonzero"
    elif not models:
        result["reason_code"] = "available_model_rows_missing"
    elif resolved_model not in models:
        result["reason_code"] = "resolved_model_not_listed"
    else:
        result["status"] = "confirmed"
        result["reason_code"] = "resolved_model_listed"
    return result


def valid_grok_profile_status(value: Any) -> bool:
    """Validate the exact public managed-profile status contract."""
    if not isinstance(value, dict) or set(value) != GROK_PROFILE_STATUS_FIELDS:
        return False
    if value.get("schema_version") != GROK_PROFILE_STATUS_SCHEMA:
        return False
    status = value.get("status")
    if type(status) is not str or status not in GROK_PROFILE_READY_STATUSES | GROK_PROFILE_BLOCKED_STATUSES:
        return False
    identities = tuple(
        value.get(field)
        for field in ("profile_name", "profile_sha256", "release_id", "grok_release_id", "model_id")
    )
    if any(item is not None and type(item) is not str for item in identities):
        return False
    present = tuple(item is not None for item in identities)
    if status in GROK_PROFILE_READY_STATUSES and not all(present):
        return False
    if status == "unconfigured" and any(present):
        return False
    if status == "blocked" and any(present) and not all(present):
        return False
    profile_name, profile_sha256, release_id, grok_release_id, model_id = identities
    if profile_name is not None and profile_name != GROK_PROFILE_NAME:
        return False
    if profile_sha256 is not None and GROK_PROFILE_DIGEST_RE.fullmatch(profile_sha256) is None:
        return False
    if release_id is not None and GROK_PROFILE_RELEASE_RE.fullmatch(release_id) is None:
        return False
    if grok_release_id is not None and GROK_PROFILE_GROK_RELEASE_RE.fullmatch(grok_release_id) is None:
        return False
    if model_id is not None and GROK_MODEL_ID_RE.fullmatch(model_id) is None:
        return False
    for field in ("eligible_rungs", "missing_rungs"):
        field_value = value.get(field)
        if (
            type(field_value) is not list
            or any(
                type(item) is not str
                or len(item) > 128
                or GROK_PROFILE_RUNG_RE.fullmatch(item) is None
                for item in field_value
            )
            or len(set(field_value)) != len(field_value)
        ):
            return False
    if set(value["eligible_rungs"]) & set(value["missing_rungs"]):
        return False
    reason_code = value.get("reason_code")
    if (
        type(reason_code) is not str
        or GROK_PROFILE_REASON_RE.fullmatch(reason_code) is None
        or reason_code not in GROK_PROFILE_STATUS_REASONS[status]
    ):
        return False
    if status == "blocked":
        allowed_blocked_reasons = (
            GROK_PROFILE_BOUND_BLOCKED_REASONS
            if all(present)
            else GROK_PROFILE_REDACTED_BLOCKED_REASONS
        )
        if reason_code not in allowed_blocked_reasons:
            return False
    if status in GROK_PROFILE_READY_STATUSES and not value["eligible_rungs"]:
        return False
    if status == "ready" and value["missing_rungs"]:
        return False
    if status == "degraded" and not value["missing_rungs"]:
        return False
    if status == "unconfigured" and (value["eligible_rungs"] or value["missing_rungs"]):
        return False
    return True


def probe_grok_remote_profile(
    binary: str,
    resolved_model: str,
    environ: dict[str, str],
    *,
    timeout: int = 10,
) -> tuple[dict[str, Any] | None, str | None]:
    """Require exact managed-profile readiness and model match for auto fallback."""
    if os.name == "nt" and os.path.splitext(str(binary))[1].lower() in {".bat", ".cmd"}:
        return None, "cmd_entrypoint_unsupported"
    private_umask = provider_subprocess_options("grok")
    try:
        help_result = subprocess.run(
            [binary, "--help"],
            text=True,
            capture_output=True,
            timeout=timeout,
            env=environ,
            check=False,
            **private_umask,
        )
    except subprocess.TimeoutExpired:
        return None, "managed_profile_help_timed_out"
    except OSError:
        return None, "managed_profile_help_could_not_execute"
    if help_result.returncode != 0 or GROK_PROFILE_HELP_TOKEN not in help_result.stdout:
        return None, "managed_profile_help_unsupported"
    try:
        completed = subprocess.run(
            [binary, "doctor", "--json"],
            text=True,
            capture_output=True,
            timeout=timeout,
            env=environ,
            check=False,
            **private_umask,
        )
    except subprocess.TimeoutExpired:
        return None, "managed_profile_probe_timed_out"
    except OSError:
        return None, "managed_profile_probe_could_not_execute"
    try:
        value = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        return None, "managed_profile_output_invalid"
    if not valid_grok_profile_status(value):
        return None, "managed_profile_output_invalid"
    status = value["status"]
    if status in GROK_PROFILE_READY_STATUSES and completed.returncode != 0:
        return value, "managed_profile_exit_inconsistent"
    if status in GROK_PROFILE_BLOCKED_STATUSES and completed.returncode != 2:
        return value, "managed_profile_exit_inconsistent"
    if status in GROK_PROFILE_BLOCKED_STATUSES:
        return value, f"managed_profile_not_ready:{value['reason_code']}"
    if value["model_id"] != resolved_model:
        return value, "managed_profile_model_mismatch"
    return value, None


def resolve_provider_binary_details(
    provider: str,
    environ: dict[str, str] | None = None,
    platform: str | None = None,
) -> tuple[str, bool, list[str], dict[str, Any] | None]:
    """Resolve a binary and return Grok selection evidence when applicable."""
    env = os.environ if environ is None else environ
    if provider not in PROVIDER_SPECS:
        raise ValueError(f"unknown provider: {provider}")
    key = provider_env_key(provider)
    tried: list[str] = []
    executable_attestation = attest_provider_executable(
        provider,
        environ=env,
        required=False,
    )
    if executable_attestation is not None:
        attested_path = str(executable_attestation["executable_path"])
        tried.append(attested_path)
        return attested_path, True, tried, (
            {
                "status": "host-attested",
                "source": "provider_executable_attestation.v1",
                "executable_attestation": executable_attestation,
            }
            if provider == "grok"
            else None
        )
    if provider == "grok":
        resolved_model = env.get("AAS_GROK_LATEST_MODEL")
        if resolved_model and GROK_MODEL_ID_RE.fullmatch(resolved_model) is None:
            plat = platform or runtime_platform_name()
            bare_candidates = list(
                GROK_BARE_BINARY_CANDIDATES.get(plat)
                or GROK_BARE_BINARY_CANDIDATES["linux"]
            )
            fallback = bare_candidates[0] if bare_candidates else "grok"
            return (
                fallback,
                False,
                tried,
                {
                    "status": "blocked",
                    "source": "resolved-model-validation",
                    "reason_code": "resolved_model_invalid",
                    "resolved_model": resolved_model,
                },
            )
    override = env.get(f"AAS_AUTOLOOP_BIN_{key}")
    if override:
        tried.append(override)
        ok, resolved = candidate_is_usable(override, env)
        selection = (
            {
                "status": "operator-override",
                "source": f"AAS_AUTOLOOP_BIN_{key}",
                "reason_code": "automatic_model_probe_bypassed",
            }
            if provider == "grok"
            else None
        )
        return (resolved if ok else override), ok, tried, selection
    # Short alias AAS_<PROVIDER> for non-Grok providers (e.g. AAS_KIMI). Grok uses the
    # dedicated path below so model-gated remote fallback stays isolated.
    if provider != "grok":
        aas_alias = env.get(f"AAS_{key}")
        if aas_alias:
            tried.append(aas_alias)
            ok, resolved = candidate_is_usable(aas_alias, env)
            return (resolved if ok else aas_alias), ok, tried, None
    if provider == "grok":
        aas_grok = env.get("AAS_GROK")
        if aas_grok:
            tried.append(aas_grok)
            ok, resolved = candidate_is_usable(aas_grok, env)
            return (
                resolved if ok else aas_grok,
                ok,
                tried,
                {
                    "status": "operator-override",
                    "source": "AAS_GROK",
                    "reason_code": "automatic_model_probe_bypassed",
                },
            )

        plat = platform or runtime_platform_name()
        bare_candidates = list(
            GROK_BARE_BINARY_CANDIDATES.get(plat)
            or GROK_BARE_BINARY_CANDIDATES["linux"]
        )
        resolved_model = env.get("AAS_GROK_LATEST_MODEL")
        if not resolved_model:
            for candidate in bare_candidates:
                tried.append(candidate)
                ok, resolved = candidate_is_usable(candidate, env)
                if ok:
                    return (
                        resolved,
                        True,
                        tried,
                        {
                            "status": "not-performed",
                            "source": "bare-default-no-resolved-model",
                            "reason_code": "resolved_model_not_provided",
                        },
                    )
            fallback = bare_candidates[0] if bare_candidates else "grok"
            return (
                fallback,
                False,
                tried,
                {
                    "status": "not-performed",
                    "source": "bare-default-no-resolved-model",
                    "reason_code": "resolved_model_not_provided_no_proxy_fallback",
                },
            )
        last_probe: dict[str, Any] = {
            "schema_version": GROK_MODEL_PROBE_SCHEMA,
            "status": "not-confirmed",
            "resolved_model": resolved_model,
            "available_models": [],
            "reason_code": "bare_cli_missing",
        }
        probed_bare_executables: set[str] = set()
        for candidate in bare_candidates:
            tried.append(candidate)
            ok, resolved = candidate_is_usable(candidate, env)
            if not ok:
                continue
            executable_identity = os.path.normcase(os.path.realpath(resolved))
            if executable_identity in probed_bare_executables:
                continue
            probed_bare_executables.add(executable_identity)
            last_probe = probe_grok_model_membership(resolved, resolved_model, env)
            if last_probe["status"] == "confirmed":
                return (
                    resolved,
                    True,
                    tried,
                    {
                        "status": "confirmed",
                        "source": "bare-model-confirmed",
                        "model_probe": last_probe,
                    },
                )

        remote_candidates = list(
            GROK_REMOTE_BINARY_CANDIDATES.get(plat)
            or GROK_REMOTE_BINARY_CANDIDATES["linux"]
        )
        probed_remote_executables: set[str] = set()
        last_remote_profile: dict[str, Any] | None = None
        last_remote_error = "remote_cli_missing"
        for candidate in remote_candidates:
            tried.append(candidate)
            ok, resolved = candidate_is_usable(candidate, env)
            if not ok:
                continue
            executable_identity = os.path.normcase(os.path.realpath(resolved))
            if executable_identity in probed_remote_executables:
                continue
            probed_remote_executables.add(executable_identity)
            last_remote_profile, last_remote_error = probe_grok_remote_profile(
                resolved,
                resolved_model,
                env,
            )
            if last_remote_error is not None:
                continue
            return (
                resolved,
                True,
                tried,
                {
                    "status": "fallback",
                    "source": "remote-fallback-after-bare-nonconfirmation",
                    "model_probe": last_probe,
                    "grok_profile_status": last_remote_profile,
                },
            )
        fallback = bare_candidates[0] if bare_candidates else "grok"
        blocked_selection = {
            "status": "blocked",
            "source": "remote-fallback-after-bare-nonconfirmation",
            "reason_code": last_remote_error,
            "model_probe": last_probe,
        }
        if last_remote_profile is not None:
            blocked_selection["grok_profile_status"] = last_remote_profile
        return (
            fallback,
            False,
            tried,
            blocked_selection,
        )

    candidates = provider_binary_candidates(provider, environ=env, platform=platform)
    for candidate in candidates:
        tried.append(candidate)
        ok, resolved = candidate_is_usable(candidate, env)
        if ok:
            return resolved, True, tried, None
    fallback = candidates[0] if candidates else provider
    return fallback, False, tried, None


def resolve_provider_binary(
    provider: str, environ: dict[str, str] | None = None, platform: str | None = None
) -> tuple[str, bool, list[str]]:
    """Resolve binary path for a provider.

    Precedence: AAS_AUTOLOOP_BIN_<P> → (grok only) AAS_GROK → platform candidates.
    Returns (binary, found, tried_list).
    """
    binary, found, tried, _selection = resolve_provider_binary_details(
        provider,
        environ=environ,
        platform=platform,
    )
    return binary, found, tried


def resolve_provider_command(
    provider: str,
    run_dir: Path,
    environ: dict[str, str] | None = None,
    *,
    panel_enabled: bool = False,
    panel_iter_dir: Path | None = None,
) -> dict[str, Any]:
    """Build the headless one-iteration command for a provider (no execution)."""
    env = os.environ if environ is None else environ
    if provider not in PROVIDER_SPECS:
        raise ValueError(f"unknown provider: {provider}")
    spec = PROVIDER_SPECS[provider]
    key = provider_env_key(provider)
    executable_attestation = attest_provider_executable(
        provider, environ=env, required=False
    )
    prompt = iteration_prompt(
        run_dir,
        panel_enabled=panel_enabled,
        panel_iter_dir=panel_iter_dir,
    )
    full = env.get(f"AAS_AUTOLOOP_CMD_{key}")
    if executable_attestation is not None and full:
        raise ValueError(
            "a host-attested provider cannot use a custom shell command"
        )
    invalid_grok_model = (
        provider == "grok"
        and bool(env.get("AAS_GROK_LATEST_MODEL"))
        and GROK_MODEL_ID_RE.fullmatch(env["AAS_GROK_LATEST_MODEL"]) is None
    )
    if full and not invalid_grok_model:
        shell_cmd = full.replace("{prompt}", shlex.quote(prompt)).replace(
            "{dir}", str(run_dir)
        )
        result = {
            "provider": provider,
            "mode": "shell",
            "shell": shell_cmd,
            "binary": None,
            "binary_found": True,
            "prompt": prompt,
            "consent_note": spec["consent_note"],
            "tried": [],
        }
        if provider == "grok":
            result["grok_selection"] = {
                "status": "operator-override",
                "source": f"AAS_AUTOLOOP_CMD_{key}",
                "reason_code": "automatic_model_probe_bypassed",
            }
        return result
    binary, binary_found, tried, grok_selection = resolve_provider_binary_details(
        provider,
        environ=env,
    )
    args_raw = env.get(f"AAS_AUTOLOOP_ARGS_{key}")
    if executable_attestation is not None and args_raw:
        raise ValueError(
            "a host-attested provider cannot use custom argument overrides"
        )
    template = shlex.split(args_raw) if args_raw else list(spec["args"])
    # Per-binary arg templates: a spec may declare different flags per resolved
    # binary (e.g. antigravity: `agy -p ... --dangerously-skip-permissions` vs
    # `gemini --yolo -p ...`). An explicit AAS_AUTOLOOP_ARGS_* override wins.
    binary_args = spec.get("binary_args")
    if binary_args and not args_raw:
        base = os.path.basename(str(binary)).lower()
        base = re.sub(r"\.(exe|cmd|bat|ps1)$", "", base)
        if base in binary_args:
            template = list(binary_args[base])
    # A host-attested provider always launches the exact attested model.  The
    # older convenience model variable may agree, but can never override or
    # silently disagree with that identity.
    model_env_name = spec.get("model_env")
    model_flag = spec.get("model_flag")
    attested_model = str(
        (executable_attestation or {}).get("model") or ""
    ).strip()
    configured_model = str(
        env.get(str(model_env_name)) or "" if model_env_name else ""
    ).strip()
    if attested_model:
        if not model_flag:
            raise ValueError(
                f"provider {provider} has no verified exact-model launch flag"
            )
        if configured_model and configured_model != attested_model:
            raise ValueError(
                f"{model_env_name} conflicts with the host-attested model"
            )
        template.extend([str(model_flag), attested_model])
    elif model_env_name and model_flag and not args_raw and configured_model:
        template.extend([str(model_flag), configured_model])
    reasoning_env_name = spec.get("reasoning_env")
    reasoning_flag = spec.get("reasoning_flag")
    reasoning_config = spec.get("reasoning_config")
    if reasoning_env_name and not args_raw and env.get(str(reasoning_env_name)):
        reasoning_value = str(env[str(reasoning_env_name)])
        if reasoning_flag:
            template.extend([str(reasoning_flag), reasoning_value])
        elif reasoning_config:
            template.extend(
                ["-c", f'{reasoning_config}="{reasoning_value}"']
            )
    argv = [str(binary)] + [
        arg.replace("{prompt}", prompt).replace("{dir}", str(run_dir))
        for arg in template
    ]
    prompt_transport = "argv"
    if executable_attestation is not None:
        matches = [index for index, value in enumerate(argv) if value == prompt]
        if len(matches) != 1:
            raise ValueError(
                "host-attested provider command does not isolate one exact prompt argument"
            )
        prompt_index = matches[0]
        if provider == "claude":
            argv = [*argv[:prompt_index], *argv[prompt_index + 1 :]]
            prompt_transport = "stdin"
        elif provider == "codex":
            argv[prompt_index] = "-"
            prompt_transport = "stdin"
        elif provider == "grok":
            # Host-proved private transport: --prompt-file /dev/stdin (not -p).
            if prompt_index > 0 and argv[prompt_index - 1] in {"-p", "--single"}:
                argv = [
                    *argv[: prompt_index - 1],
                    "--prompt-file",
                    "/dev/stdin",
                    *argv[prompt_index + 1 :],
                ]
            else:
                argv = [
                    *argv[:prompt_index],
                    "--prompt-file",
                    "/dev/stdin",
                    *argv[prompt_index + 1 :],
                ]
            prompt_transport = "stdin"
        else:
            # Do not leave exact prompt bytes in argv merely because this
            # provider lacks a reviewed private transport.  The driver will
            # refuse it before spawn.
            argv = [*argv[:prompt_index], *argv[prompt_index + 1 :]]
            prompt_transport = "unavailable"
        if any(prompt in value for value in argv):
            raise ValueError("host-attested provider prompt remains in argv")
    result = {
        "provider": provider,
        "mode": "argv",
        "argv": argv,
        "binary": str(binary),
        "binary_found": binary_found,
        "prompt": prompt,
        "consent_note": spec["consent_note"],
        "tried": tried,
        "prompt_transport": prompt_transport,
    }
    if executable_attestation is not None:
        result["executable_attestation"] = executable_attestation
        result["model"] = attested_model
    if grok_selection is not None:
        result["grok_selection"] = grok_selection
    return result


def agent_cmd_command(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    providers = sorted(PROVIDER_SPECS) if args.provider == "all" else [args.provider]
    entries: dict[str, Any] = {}
    for provider in providers:
        entry = resolve_provider_command(provider, run_dir)
        if not args.print_prompt:
            entry.pop("prompt", None)
        entries[provider] = entry
    result: dict[str, Any] = {
        "status": "ok",
        "action": "agent-cmd",
        "dir": str(run_dir),
        "providers": entries,
    }
    if args.print_prompt:
        result["iteration_prompt"] = iteration_prompt(run_dir)
    return result


def last_ledger_record(run_dir: Path) -> dict[str, Any] | None:
    path = loop_paths(run_dir)["iterations"]
    try:
        lines = [ln for ln in _read_regular_text(path).splitlines() if ln.strip()]
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def loop_driver_entry(reg: Path, run_dir: Path) -> dict[str, Any] | None:
    for snapshot in strict_registry_snapshots(reg):
        _validate_registry_authority_snapshot(snapshot)
        entry = snapshot.entry
        if str(entry.get("loop_dir", "")) == str(run_dir):
            return entry
    return None


def live_driver_entries_for_loop(
    reg: Path, run_dir: Path
) -> list[tuple[Path, dict[str, Any]]]:
    """Return every live driver entry for one exact canonical loop path."""

    target_path = Path(run_dir).resolve()
    live: list[tuple[Path, dict[str, Any]]] = []
    for path, entry in strict_registry_entries(reg):
        raw_loop = entry.get("loop_dir")
        if not isinstance(raw_loop, str) or not raw_loop.strip():
            # A row without a loop identity cannot be proven unrelated to the
            # migration target, so destructive migration stops for inspection.
            raise RegistrySafetyError(
                f"registry entry {path} lacks a valid loop_dir"
            )
        if any(ord(char) < 32 or ord(char) == 127 for char in raw_loop):
            raise RegistrySafetyError(
                f"registry entry {path} has an invalid loop_dir"
            )
        try:
            entry_loop = Path(raw_loop).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise RegistrySafetyError(
                f"registry entry {path} loop_dir cannot be canonicalized"
            ) from exc
        if entry_loop != target_path:
            continue
        driver_marker = entry.get("driver")
        if driver_marker is not None and not isinstance(driver_marker, bool):
            raise RegistrySafetyError(
                f"matching registry entry {path} has an invalid driver marker"
            )
        pid = entry.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid < 0:
            raise RegistrySafetyError(
                f"matching registry entry {path} has an invalid pid"
            )
        if driver_marker is True and pid <= 0:
            raise RegistrySafetyError(
                f"matching driver registry entry {path} lacks a positive pid"
            )
        if entry_owned_by_live_driver(entry):
            live.append((path, entry))
    return live


def progress_paths(run_dir: Path, log_dir: Path | None = None) -> dict[str, Path]:
    """On-disk progress surfaces updated by drive and watch."""
    logs = Path(log_dir).expanduser() if log_dir is not None else run_dir / "driver_logs"
    return {
        "log_dir": logs,
        "progress_jsonl": logs / "progress.jsonl",
        "live_status": run_dir / "LIVE_STATUS.md",
    }


_NOTIFY_EVENT_MARKERS: dict[str, str] = {
    "drive_start": "🚀",
    "drive_stop": "🏁",
    "iteration_start": "▶️",
    "iteration_ok": "✅",
    "iteration_failed": "❌",
    "iteration": "📌",
    "quota_wait": "⏳",
    "auth_failure": "🔐",
    "paused": "⏸️",
    "terminal": "🛑",
    "driver_dead": "💀",
    "watch_start": "👀",
    "iteration_rejected": "⛔",
    "goal_focus_replan": "🧭",
    "goal_focus_wait": "⏳",
    "strategy_review_start": "🧠",
    "strategy_review_wait": "⏳",
    "result_review_start": "🔎",
    "result_review_wait": "⏳",
    "result_review_error": "⚠️",
    "supervisor": "🛠️",
}


# Events about launching or failing the *next* iteration after the banked tip.
# For these, progress text must not look like the last banked iteration failed.
_ATTEMPT_PROGRESS_EVENTS = frozenset(
    {
        "iteration_start",
        "iteration_failed",
        "quota_wait",
        "auth_failure",
        "panel_target_start",
        "panel_target_ok",
        "panel_target_fail",
        "strategy_review_start",
        "strategy_review_wait",
        "result_review_start",
        "result_review_wait",
        "result_review_error",
        "goal_focus_wait",
    }
)

_ARTIFACT_PATH_RE = re.compile(
    r"(?i)^[\w./\\-]+(?:/|\\)[\w./\\-]+\.(?:json|md|txt|pdf)$"
)


def looks_like_artifact_path(value: str) -> bool:
    """True when *value* is a single path-like token (not human prose)."""
    text = (value or "").strip()
    if not text or "\n" in text or " " in text:
        return False
    normalized = text.replace("\\", "/")
    if _ARTIFACT_PATH_RE.match(normalized):
        return True
    if "/iterations/" in normalized and re.search(
        r"(?i)\.(json|md|txt|pdf)$", normalized
    ):
        return True
    return False


def resolve_progress_result_text(record: dict[str, Any]) -> str:
    """Prefer human Result text; never surface a bare certificate path alone.

    Agents sometimes put ``iterations/.../certificate.json`` in ledger ``output``.
    Notify should show goal_contribution (and optional detail) instead, with the
    basename only as a parenthetical artifact pointer.
    """
    output = str(record.get("output") or "").strip()
    contrib = str(record.get("goal_contribution") or "").strip()
    detail = str(
        record.get("goal_contribution_detail") or record.get("contribution_detail") or ""
    ).strip()
    if output and not looks_like_artifact_path(output):
        return output[:700]
    if contrib:
        text = contrib
        if detail:
            text = f"{contrib} — {detail}"
        if output and looks_like_artifact_path(output):
            text = f"{text} (artifact: {Path(output).name})"
        return text[:700]
    if output and looks_like_artifact_path(output):
        return f"Banked artifact `{Path(output).name}`"[:700]
    return output[:700]


def research_result_text(record: dict[str, Any]) -> str:
    """Assemble a research-first result summary from a ledger record.

    Notifications must lead with what the iteration found, not which agent ran
    it. Prefer the worker's plain-language summary, then objective plus
    outcome, then the goal-contribution fallback; append verification
    agreement so the finding and its evidence status read together.
    """
    base = str(record.get("completed_summary") or "").strip()
    if not base:
        objective = str(record.get("objective") or "").strip()
        outcome = str(record.get("outcome") or "").strip().replace(";", "; ")
        base = objective or str(record.get("label") or "").strip()
        if outcome:
            base = f"{base} — {outcome}" if base else outcome
    if not base:
        base = resolve_progress_result_text(record)
    parts = [base] if base else []
    agree = record.get("primary_independent_agree")
    if agree is True:
        parts.append("Primary and independent verification agree.")
    elif agree is False:
        parts.append("Primary and independent verification disagree.")
    contribution = str(record.get("goal_contribution") or "").strip()
    campaign = str(record.get("campaign_id") or record.get("campaign") or "").strip()
    if contribution:
        suffix = f" (campaign {campaign})" if campaign else ""
        parts.append(f"Goal contribution: {contribution}{suffix}.")
    return " ".join(parts)[:900]


def research_position_text(record: dict[str, Any], fallback: str = "") -> str:
    """Describe where the goal stands after a record: remaining evidence gaps.

    Ledger records carry the gap list under ``remaining_gaps``; accept the
    ``evidence_gaps`` spelling too so hand-built records still render.
    """
    gaps = record.get("remaining_gaps") or record.get("evidence_gaps")
    if not (isinstance(gaps, list) and gaps):
        return fallback
    shown = ", ".join(str(gap) for gap in gaps[:4])
    extra = f" (+{len(gaps) - 4} more)" if len(gaps) > 4 else ""
    gap_text = f"Remaining evidence gaps: {shown}{extra}."
    return f"{fallback} {gap_text}".strip() if fallback else gap_text


def parse_recovery_table_field(recovery_md: str, field_name: str) -> str:
    """Extract a ``| Field | value |`` cell from recovery.md (best-effort)."""
    target = field_name.strip().lower()
    for line in (recovery_md or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        if cells[0].strip().lower() == target:
            return cells[1].strip()
    return ""


def build_progress_why_where(
    run_dir: Path,
    record: dict[str, Any],
    state: dict[str, Any],
    *,
    attempt_event: bool,
) -> tuple[str, str]:
    """Return (why, where) research-context lines for notify.

    Populated when goal_priority is active (or recovery has usable fields).
    Empty strings when nothing useful is available.
    """
    why = ""
    where = ""
    recovery_text = ""
    try:
        rec_path = Path(run_dir) / "recovery.md"
        if rec_path.is_file():
            recovery_text = _read_regular_text(rec_path, errors="replace")
    except OSError:
        recovery_text = ""

    next_safe = parse_recovery_table_field(recovery_text, "Next safe action")
    gaps = parse_recovery_table_field(recovery_text, "Remaining gaps")
    last_node = parse_recovery_table_field(recovery_text, "Last valid node")
    goal_focus = parse_recovery_table_field(recovery_text, "Goal focus")

    campaign = ""
    primary_obj = ""
    try:
        if is_goal_priority_active(run_dir):
            cfg = load_goal_priority(run_dir)
            campaign = str(cfg.get("primary_campaign") or "").strip()
            primary_obj = str(cfg.get("primary_objective") or "").strip()
    except Exception:  # noqa: BLE001 — notify must never fail
        pass

    npp = str(state.get("next_preferred_path") or "").strip()
    if attempt_event:
        if npp:
            why = npp
        elif next_safe:
            why = next_safe
        elif campaign:
            why = f"Campaign `{campaign}`" + (f": {primary_obj}" if primary_obj else "")
    else:
        # Completed bank: why this objective was on-path.
        if campaign and primary_obj:
            why = f"Campaign `{campaign}`: {primary_obj}"
        elif campaign:
            why = f"Campaign `{campaign}` (goal_priority)"
        elif goal_focus:
            why = goal_focus
        elif next_safe:
            why = next_safe

    where_parts: list[str] = []
    if campaign:
        where_parts.append(f"Campaign `{campaign}`")
    if last_node:
        where_parts.append(last_node)
    elif primary_obj:
        where_parts.append(primary_obj)
    if gaps:
        where_parts.append(f"Open: {gaps}")
    where = " · ".join(where_parts)

    return why[:900].strip(), where[:1100].strip()


# --- Notify prose formatting (unicode math, lists) --------------------------------

# Ordered longest-first so multi-char operators win.
_ASCII_MATH_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("<=>", "⇔"),
    ("=>", "⇒"),
    ("<=", "≤"),
    (">=", "≥"),
    ("!=", "≠"),
    ("~=", "≈"),
    ("->", "→"),
    ("<-", "←"),
    ("...", "…"),
)

# Word-ish Greek / symbol names used in this research loop (case-sensitive ids).
_WORD_MATH_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bbeta\b"), "β"),
    (re.compile(r"\bBeta\b"), "Β"),
    (re.compile(r"\bdelta\b"), "δ"),
    (re.compile(r"\bDelta\b"), "Δ"),
    (re.compile(r"\balpha\b"), "α"),
    (re.compile(r"\bgamma\b"), "γ"),
    (re.compile(r"\brho\b"), "ρ"),
    (re.compile(r"\bsigma\b"), "σ"),
    (re.compile(r"\bphi\b"), "φ"),
    (re.compile(r"\bPhi\b"), "Φ"),
    (re.compile(r"\btheta\b"), "θ"),
    (re.compile(r"\binfty\b"), "∞"),
    (re.compile(r"\bapprox\b"), "≈"),
)


def normalize_math_unicode(text: str) -> str:
    """Map common ASCII math and Greek word tokens to Unicode symbols.

    Leaves existing Unicode intact. Does not interpret full LaTeX.
    """
    if not text:
        return ""
    out = text
    # Protect fenced code / backticks so we do not rewrite paths inside them.
    chunks: list[str] = []
    parts = re.split(r"(`[^`]*`)", out)
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) >= 2:
            chunks.append(part)
            continue
        s = part
        for ascii_op, uni in _ASCII_MATH_REPLACEMENTS:
            s = s.replace(ascii_op, uni)
        for pat, uni in _WORD_MATH_REPLACEMENTS:
            s = pat.sub(uni, s)
        # Compact D>=25 already handled; also D = 25 style stays.
        # Superscripts for common small integers after ^ when simple.
        s = re.sub(r"\^(\d)", lambda m: _SUPERSCRIPT.get(m.group(1), m.group(0)), s)
        chunks.append(s)
    return "".join(chunks)


_SUPERSCRIPT = {
    "0": "⁰",
    "1": "¹",
    "2": "²",
    "3": "³",
    "4": "⁴",
    "5": "⁵",
    "6": "⁶",
    "7": "⁷",
    "8": "⁸",
    "9": "⁹",
}


def split_notify_items(text: str, *, max_items: int = 10) -> list[str]:
    """Split prose into list items when it is clearly multi-clause.

    Prefer existing newlines/bullets; else split on ``;`` or middot ``·`` when
    that yields several substantive clauses.
    """
    raw = (text or "").strip()
    if not raw:
        return []

    # Already a list (markdown or unicode bullets).
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(lines) >= 2 and all(
        re.match(r"^([-*•‣▪◦]|\d+[.)])\s+", ln) for ln in lines
    ):
        cleaned: list[str] = []
        for ln in lines:
            cleaned.append(re.sub(r"^([-*•‣▪◦]|\d+[.)])\s+", "", ln).strip())
        return [c for c in cleaned if c][:max_items]

    if len(lines) >= 2:
        # Multi-line prose without bullets: keep as separate items if short lines.
        if all(len(ln) <= 220 for ln in lines) and len(lines) <= max_items:
            return lines[:max_items]

    # Semicolon / middot enumeration (common in recovery and contributions).
    for sep in (";", " · ", " • "):
        if sep in raw:
            parts = [p.strip(" \t-–—") for p in raw.split(sep)]
            parts = [p for p in parts if p]
            if len(parts) >= 2 and all(len(p) >= 8 for p in parts[:3]):
                return parts[:max_items]

    # Comma-separated only when many short tags (avoid splitting math commas).
    if raw.count(",") >= 3 and len(raw) < 400:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) >= 4 and all(len(p) <= 80 for p in parts):
            return parts[:max_items]

    return [raw]


def format_notify_body_block(
    text: str,
    *,
    style: str = "markdown",
    max_chars: int = 900,
    max_items: int = 10,
) -> str:
    """Normalize math Unicode and render as a bullet list when multi-item.

    *style*: ``markdown`` (Zulip) or ``plain`` (Telegram after HTML-escape of
    each line by the caller).
    """
    normalized = normalize_math_unicode((text or "").strip())
    if not normalized:
        return ""
    items = split_notify_items(normalized, max_items=max_items)
    if len(items) == 1:
        body = items[0]
        if len(body) > max_chars:
            body = body[: max_chars - 1].rstrip() + "…"
        return body

    bullet = "•"
    rendered: list[str] = []
    used = 0
    for item in items:
        # Each bullet gets a share of the budget.
        piece = item
        room = max_chars - used - (len(rendered) + 1) * 3
        if room < 24:
            break
        if len(piece) > room:
            piece = piece[: room - 1].rstrip() + "…"
        if style == "markdown":
            rendered.append(f"{bullet} {piece}")
        else:
            rendered.append(f"{bullet} {piece}")
        used += len(piece)
    return "\n".join(rendered)


def format_progress_notify_text(
    *,
    loop_name: str,
    event: str,
    iteration: int,
    max_iter: int,
    remaining: int,
    decision: str,
    status: str,
    objective: str,
    output: str,
    timestamp: str = "",
    last_completed: int | None = None,
    next_iteration: int | None = None,
    progress_note: str = "",
    why: str = "",
    where: str = "",
) -> str:
    """Human-readable multi-line notify body for Zulip (Markdown + Unicode)."""
    marker = _NOTIFY_EVENT_MARKERS.get(event, "•")
    if (
        event in _ATTEMPT_PROGRESS_EVENTS
        and last_completed is not None
        and next_iteration is not None
    ):
        if max_iter:
            progress_line = (
                f"📊 Progress: banked **{last_completed}/{max_iter}** · "
                f"attempting **{next_iteration}** ({remaining} left after banked)"
            )
        else:
            progress_line = (
                f"📊 Progress: banked **{last_completed}** · "
                f"attempting **{next_iteration}**"
            )
    else:
        prog = f"{iteration}/{max_iter}" if max_iter else str(iteration or "?")
        rem = f"{remaining} left" if max_iter else ""
        progress_line = f"📊 Progress: **{prog}**" + (f" ({rem})" if rem else "")
    decision_u = normalize_math_unicode(decision or "?")
    status_u = normalize_math_unicode(status or "n/a")
    lines = [
        f"{marker} **{loop_name}** — `{event}`",
        progress_line,
        f"🏷 Decision: `{decision_u}` · Status: `{status_u}`",
    ]
    note = normalize_math_unicode((progress_note or "").strip())
    if note:
        lines.append(f"ℹ️ {note}")
    if timestamp:
        lines.append(f"🕒 {timestamp}")

    def _section(title: str, body: str, *, max_chars: int) -> None:
        block = format_notify_body_block(
            body, style="markdown", max_chars=max_chars
        )
        if not block:
            return
        lines.append("")
        lines.append(f"**{title}**")
        lines.append(block)

    _section("Why", why, max_chars=600)
    _section("Where (goal)", where, max_chars=800)
    _section("Objective", objective, max_chars=600)
    _section("Result", output, max_chars=800)
    return "\n".join(lines).strip()


def format_progress_notify_telegram_html(
    *,
    loop_name: str,
    event: str,
    iteration: int,
    max_iter: int,
    remaining: int,
    decision: str,
    status: str,
    objective: str,
    output: str,
    timestamp: str = "",
    last_completed: int | None = None,
    next_iteration: int | None = None,
    progress_note: str = "",
    why: str = "",
    where: str = "",
) -> str:
    """Telegram HTML body (parse_mode=HTML); preserves Unicode math symbols."""

    def esc(s: str) -> str:
        return (
            str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    marker = _NOTIFY_EVENT_MARKERS.get(event, "•")
    if (
        event in _ATTEMPT_PROGRESS_EVENTS
        and last_completed is not None
        and next_iteration is not None
    ):
        if max_iter:
            progress_line = (
                f"📊 Progress: banked <b>{esc(str(last_completed))}/{esc(str(max_iter))}</b> · "
                f"attempting <b>{esc(str(next_iteration))}</b> "
                f"({esc(str(remaining))} left after banked)"
            )
        else:
            progress_line = (
                f"📊 Progress: banked <b>{esc(str(last_completed))}</b> · "
                f"attempting <b>{esc(str(next_iteration))}</b>"
            )
    else:
        prog = f"{iteration}/{max_iter}" if max_iter else str(iteration or "?")
        rem = f"{remaining} left" if max_iter else ""
        progress_line = f"📊 Progress: <b>{esc(prog)}</b>" + (
            f" ({esc(rem)})" if rem else ""
        )
    decision_u = normalize_math_unicode(decision or "?")
    status_u = normalize_math_unicode(status or "n/a")
    lines = [
        f"{marker} <b>{esc(loop_name)}</b> — <code>{esc(event)}</code>",
        progress_line,
        f"🏷 Decision: <code>{esc(decision_u)}</code> · Status: <code>{esc(status_u)}</code>",
    ]
    note = normalize_math_unicode((progress_note or "").strip())
    if note:
        lines.append(f"ℹ️ {esc(note)}")
    if timestamp:
        lines.append(f"🕒 {esc(timestamp)}")

    def _section(title: str, body: str, *, max_chars: int) -> None:
        block = format_notify_body_block(body, style="plain", max_chars=max_chars)
        if not block:
            return
        lines.append("")
        lines.append(f"<b>{esc(title)}</b>")
        # Escape each line; bullets and Unicode math remain visible.
        for ln in block.splitlines():
            lines.append(esc(ln))

    _section("Why", why, max_chars=600)
    _section("Where (goal)", where, max_chars=800)
    _section("Objective", objective, max_chars=600)
    _section("Result", output, max_chars=800)
    return "\n".join(lines).strip()


def _read_optional_json_object(path: Path) -> dict[str, Any]:
    try:
        return read_json(path) if path.is_file() else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _notify_iteration_status(
    event: str,
    extra: dict[str, Any],
    *,
    record: Mapping[str, Any] | None = None,
) -> str:
    explicit = str(extra.get("iteration_status") or "").strip().lower()
    if explicit:
        return explicit
    bank_status = str((record or {}).get("bank_status") or "").strip().lower()
    if event == "iteration" and bank_status == "rejected":
        return "failure"
    if event in {"iteration_ok", "iteration", "result_accepted"}:
        return "success"
    if event in {"iteration_rejected", "result_rejected"}:
        return "failure"
    if event in {
        "iteration_failed",
        "auth_failure",
        "driver_dead",
        "runtime_error",
        "result_review_error",
    }:
        return "error"
    if event in {
        "quota_wait",
        "strategy_review_wait",
        "result_review_wait",
        "goal_focus_wait",
    }:
        return "waiting"
    if event == "paused":
        return "paused"
    if event in {
        "iteration_start",
        "panel_target_start",
        "strategy_review_start",
        "result_review_start",
    }:
        return "running"
    return "not_applicable"


def _notify_review_status(
    event: str,
    extra: dict[str, Any],
    *,
    enforced: bool,
    record: Mapping[str, Any] | None = None,
) -> str:
    explicit = str(extra.get("review_status") or "").strip().lower()
    if explicit:
        return explicit
    bank_status = str((record or {}).get("bank_status") or "").strip().lower()
    if event == "iteration" and bank_status == "rejected":
        return "failed"
    if event == "iteration" and bank_status == "accepted":
        return "passed"
    if event in {"iteration_rejected", "result_rejected"}:
        return "failed"
    if event in {
        "strategy_review_start",
        "strategy_review_wait",
        "result_review_wait",
        "result_review_start",
    }:
        return "pending"
    if event == "result_review_error":
        return "error"
    if event in {"iteration_ok", "result_accepted"} and enforced:
        return "passed"
    if event == "iteration_start" and enforced:
        return "pending"
    return "not_required"


def _notify_compute_value(record: dict[str, Any], extra: dict[str, Any], *, attempt: bool) -> Any:
    if "compute" in extra:
        return extra.get("compute")
    if attempt:
        return None
    execution = record.get("execution")
    raw = execution.get("compute") if isinstance(execution, dict) else None
    if not isinstance(raw, dict) or "recording_status" not in raw:
        return raw
    if raw.get("recording_status") != "explicit":
        return None
    if raw.get("usage") == "none":
        return []
    services = raw.get("services")
    return services if isinstance(services, list) else None


def _notify_goal_progress(record: dict[str, Any], contract: dict[str, Any]) -> str:
    progress = record.get("progress_assessment")
    if isinstance(progress, dict) or record.get("bank_status") in {"accepted", "rejected"}:
        progress = progress if isinstance(progress, dict) else {}
        campaign_delta = str(
            record.get("campaign_delta") or progress.get("campaign_delta") or "none"
        )
        global_delta = str(
            record.get("global_delta") or progress.get("global_delta") or "none"
        )
        transitions = record.get("obligation_transitions")
        if isinstance(transitions, list) and transitions:
            obligations = [
                str(item.get("obligation_id"))
                for item in transitions
                if isinstance(item, dict) and item.get("obligation_id")
            ]
        else:
            obligations = [str(item) for item in progress.get("obligation_ids") or [] if item]
        detail = f"campaign {campaign_delta}; global {global_delta}"
        if obligations:
            detail += "; obligations " + ", ".join(obligations)
        return detail
    obligations = contract.get("obligations")
    if isinstance(obligations, dict) and obligations:
        obligation_rows = list(obligations.values())
    elif isinstance(obligations, list):
        obligation_rows = obligations
    else:
        obligation_rows = []
    if obligation_rows:
        closed = sum(
            1
            for obligation in obligation_rows
            if isinstance(obligation, dict)
            and str(obligation.get("status") or "").lower()
            in {"closed", "satisfied", "discharged"}
        )
        return f"{closed}/{len(obligation_rows)} named obligations satisfied"
    return "Not measured"


def _notify_success_criteria(value: Any) -> list[str]:
    """Flatten Goal-Focus criterion objects for the transport schema."""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        raw_rows: Any = list(value.values())
    elif isinstance(value, list):
        raw_rows = value
    else:
        return []
    rows: list[str] = []
    for item in raw_rows:
        if isinstance(item, dict):
            text_value = str(
                item.get("description") or item.get("criterion") or item.get("id") or ""
            ).strip()
        else:
            text_value = str(item or "").strip()
        if text_value:
            rows.append(text_value)
    return rows


def _build_notify_v2_envelope(
    run_dir: Path,
    event: str,
    *,
    record: dict[str, Any],
    state: dict[str, Any],
    budget: dict[str, Any],
    extra: dict[str, Any],
    iteration: int,
    spent: int,
    max_iter: int,
    objective: str,
    output: str,
    progress_note: str,
    why: str,
    where: str,
    timestamp: str,
    attempt_event: bool,
) -> dict[str, Any]:
    """Resolve host-owned facts and build the mandatory Notify v2 envelope."""
    identity = resolve_loop_notify_identity(run_dir)
    contract = _read_optional_json_object(run_dir / "goal_contract.json")
    plan = _read_optional_json_object(run_dir / "current_plan.json")
    goal = str(
        contract.get("goal")
        or contract.get("main_goal")
        or state.get("goal")
        or "Research goal was not recorded."
    ).strip()
    success_criteria = _notify_success_criteria(
        contract.get("success_criteria") or state.get("success_criteria")
    )

    iteration_status = _notify_iteration_status(event, extra, record=record)
    review_status = _notify_review_status(
        event,
        extra,
        enforced=goal_focus_is_enforced(run_dir),
        record=record,
    )
    if iteration_status == "success":
        completed = str(
            extra.get("completed_summary")
            or output
            or record.get("completed_summary")
            or "The iteration was reviewed and banked."
        ).strip()
    elif iteration_status == "failure":
        completed = str(
            extra.get("completed_summary")
            or extra.get("review_summary")
            or "No research result was banked because the staged candidate failed review."
        ).strip()
    elif iteration_status == "error":
        completed = str(
            extra.get("completed_summary")
            or progress_note
            or extra.get("error")
            or "No new research result was banked because this iteration encountered an error."
        ).strip()
    else:
        completed = str(
            extra.get("completed_summary")
            or "No new research result has been banked by this event."
        ).strip()

    current = str(
        extra.get("current_summary")
        or record.get("current_summary")
        or plan.get("current_summary")
        or where
        or state.get("current_summary")
        or "The research remains open at the last independently reviewed state."
    ).strip()
    next_action = str(
        extra.get("next_action")
        or plan.get("next_action")
        or state.get("next_preferred_path")
        or why
        or objective
        or "Reconcile the active plan before another iteration."
    ).strip()
    execution = record.get("execution") if isinstance(record.get("execution"), dict) else {}
    executor = str(
        extra.get("provider")
        or extra.get("executor")
        or execution.get("executor_provider")
        or os.environ.get("AAS_AUTOLOOP_PRIMARY_PROVIDER")
        or "Not recorded"
    ).strip()
    started_at = str(extra.get("started_at") or execution.get("started_at") or "").strip()
    finished_at = str(
        extra.get("finished_at") or execution.get("work_finished_at") or timestamp
    ).strip()
    duration = extra.get("duration_seconds")
    if duration in (None, ""):
        delta = record.get("budget_delta")
        duration = delta.get("wall_time_seconds") if isinstance(delta, dict) else None
    goal_focus = record.get("goal_focus") if isinstance(record.get("goal_focus"), dict) else {}
    plan_revision = extra.get("plan_revision")
    if plan_revision in (None, ""):
        plan_revision = plan.get("plan_revision", goal_focus.get("plan_revision"))
    campaign_id = str(
        extra.get("campaign_id")
        or goal_focus.get("campaign_id")
        or record.get("campaign_id")
        or plan.get("campaign_id")
        or ""
    )
    objective_id = str(
        extra.get("objective_id") or plan.get("objective_id") or record.get("objective") or ""
    )
    scope = str(
        extra.get("scope")
        or goal_focus.get("scope_lock")
        or record.get("scope_lock")
        or plan.get("scope_lock")
        or ""
    )
    persisted_review = (
        record.get("result_review")
        if isinstance(record.get("result_review"), dict)
        else {}
    )
    reviewer_families = (
        extra.get("reviewer_families")
        or persisted_review.get("reviewer_families")
        or []
    )
    driver_agent: Any = extra.get("driver_agent")
    if driver_agent is None and executor != "Not recorded":
        driver_agent = {
            "name": executor,
            "provider": executor,
            "role": "driver",
        }
        driver_model = str(extra.get("driver_model") or execution.get("executor_model") or "").strip()
        if driver_model:
            driver_agent["model"] = driver_model
    panel_agents: Any = None
    for key in ("panel_agents", "reviewer_agents", "reviewer_providers", "usable_providers"):
        if key in extra:
            panel_agents = extra.get(key)
            break
    if panel_agents is None:
        persisted_agents: list[Any] = []
        for key in ("providers", "usable_providers", "reviewer_providers"):
            raw_agents = persisted_review.get(key)
            if isinstance(raw_agents, (str, dict)):
                raw_agents = [raw_agents]
            if not isinstance(raw_agents, list):
                continue
            for agent in raw_agents:
                if agent not in persisted_agents:
                    persisted_agents.append(agent)
        if persisted_agents:
            panel_agents = persisted_agents
    other_agents: Any = extra.get("other_agents") if "other_agents" in extra else None
    compute = _notify_compute_value(record, extra, attempt=attempt_event)
    candidate_id = str(extra.get("candidate_id") or record.get("candidate_id") or "").strip()
    stable_event_id = str(extra.get("event_id") or "").strip()
    if not stable_event_id and candidate_id and event in {
        "iteration_ok",
        "iteration_rejected",
        "result_accepted",
        "result_rejected",
    }:
        stable_event_id = f"arl-{event}-{candidate_id}"

    # --- Notify v2.1 host fields (event-class gated; never invent banked claims) ---
    banked_event = event in {
        "iteration_ok",
        "result_accepted",
    } and not attempt_event
    reject_event = event in {
        "iteration_rejected",
        "result_rejected",
        "result_review_failed",
    }
    error_event = event in {
        "iteration_failed",
        "quota_wait",
        "auth_or_session_dead",
    } or iteration_status == "error"

    # Results: only attribute claim ids to this iteration on banked success.
    results_text = "No claims banked."
    if banked_event and iteration_status == "success":
        claim_ids = extra.get("claim_ids") or record.get("claim_ids") or []
        if isinstance(claim_ids, str):
            claim_ids = [claim_ids]
        if isinstance(claim_ids, list) and claim_ids:
            results_text = "Banked claims: " + ", ".join(
                str(c) for c in claim_ids if str(c).strip()
            )
        else:
            results_text = str(
                extra.get("results_summary")
                or record.get("output")
                or completed
                or "Iteration banked; no claim ids were recorded."
            ).strip()
    elif attempt_event:
        results_text = "No claims banked by this event."

    # Decision: do not copy tip ledger decision onto attempt events.
    if attempt_event:
        decision_text = ""
        decision_reason = "Not recorded."
    else:
        decision_text = str(extra.get("decision") or record.get("decision") or "").strip()
        reason_bits: list[str] = []
        for key in (
            "decision_reason",
            "goal_contribution_detail",
            "review_summary",
            "completed_summary",
        ):
            val = str(extra.get(key) or record.get(key) or "").strip()
            if val:
                reason_bits.append(val)
                break
        if not reason_bits and next_action and not banked_event:
            # next_action is a plan, not always a rationale — only use when no better.
            if extra.get("decision_reason") or record.get("goal_contribution_detail"):
                reason_bits.append(next_action)
        decision_reason = reason_bits[0] if reason_bits else "Not recorded."

    # Issues: tri-state (reported true only when host filled structured issues).
    issue_errors: list[dict[str, str]] = []
    issue_failures: list[dict[str, str]] = []
    issues_reported = False
    failure_class = str(
        extra.get("failure_class") or extra.get("error_class") or ""
    ).strip().lower()
    if error_event or failure_class:
        issues_reported = True
        code = failure_class or "runtime"
        # Never forward sensitive blocked stdout — category/code only.
        if code in {"sensitive_output", "content:credential-assignment", "pii"} or (
            "sensitive" in code
        ):
            msg = "Primary output blocked as sensitive; details omitted."
            code = "sensitive_output"
        else:
            raw_err = str(extra.get("error") or progress_note or "").strip()
            msg = raw_err[:240] if raw_err else f"Iteration error ({code})."
        issue_errors.append({"code": code[:64], "message": msg, "stage": "drive"})
    if reject_event or review_status == "failed":
        issues_reported = True
        claim_reviews = (
            extra.get("claim_reviews")
            or persisted_review.get("claim_reviews")
            or []
        )
        if isinstance(claim_reviews, list):
            for review in claim_reviews:
                if not isinstance(review, dict):
                    continue
                status = str(review.get("status") or "").lower()
                if status in {"disputed", "rejected", "fail", "failed", "unsupported"}:
                    cid = str(review.get("claim_id") or "claim").strip()
                    reason = str(review.get("reason") or status).strip()[:240]
                    issue_failures.append(
                        {
                            "code": "claim_disputed",
                            "message": f"{cid}: {reason}",
                            "stage": "result_review",
                        }
                    )
        if not issue_failures:
            summary = str(
                extra.get("review_summary")
                or persisted_review.get("conservative_verdict")
                or "Candidate failed independent review."
            ).strip()[:240]
            issue_failures.append(
                {
                    "code": "result_review_rejected",
                    "message": summary,
                    "stage": "result_review",
                }
            )
    if banked_event and iteration_status == "success" and not issue_errors and not issue_failures:
        issues_reported = True  # host asserts none

    issues_payload: dict[str, Any] = {
        "reported": issues_reported,
        "errors": issue_errors,
        "failures": issue_failures,
    }

    # body_profile: notify.json > standing_orders.notify > env > default
    body_profile = "operator_full"
    candidates: list[str] = []
    try:
        notify_cfg = _read_optional_json_object(run_dir / "notify.json")
        candidates.append(str(notify_cfg.get("body_profile") or "").strip())
    except Exception:  # noqa: BLE001
        pass
    so = state.get("standing_orders") if isinstance(state.get("standing_orders"), dict) else {}
    so_notify = so.get("notify") if isinstance(so, dict) else None
    if isinstance(so_notify, dict):
        candidates.append(str(so_notify.get("body_profile") or "").strip())
    elif isinstance(so_notify, str):
        candidates.append(so_notify.strip())
    candidates.append(str(extra.get("body_profile") or "").strip())
    candidates.append(str(os.environ.get("AAS_AUTOLOOP_NOTIFY_BODY_PROFILE") or "").strip())
    for cand in candidates:
        if cand in {"operator_full", "legacy"}:
            body_profile = cand
            break

    error_class_out = failure_class or None

    return notify_v2.build_event(
        event=event,
        event_id=stable_event_id or None,
        occurred_at=timestamp,
        title=str(identity.get("title") or run_dir.name),
        topic_slug=str(identity.get("slug") or run_dir.name),
        goal=goal,
        success_criteria=success_criteria,
        goal_status=str(contract.get("status") or contract.get("goal_status") or "open"),
        completed=completed,
        current=current,
        plan=next_action,
        iteration_status=iteration_status,
        loop_status=str(extra.get("loop_status") or state.get("status") or "running"),
        review_status=review_status,
        iteration_number=iteration,
        spent_iterations=spent,
        max_iterations=max_iter or None,
        goal_progress=str(extra.get("goal_progress") or _notify_goal_progress(record, contract)),
        executor=executor,
        driver_agent=driver_agent,
        panel_agents=panel_agents,
        other_agents=other_agents,
        compute=compute,
        started_at=started_at or None,
        finished_at=finished_at or None,
        duration_seconds=duration,
        decision=decision_text,
        decision_reason=decision_reason,
        results=results_text,
        issues=issues_payload,
        body_profile=body_profile,
        error_class=error_class_out,
        campaign_id=campaign_id,
        objective_id=objective_id,
        scope=scope,
        reviewer_families=reviewer_families,
        plan_revision=plan_revision,
    )


def build_progress_event(
    run_dir: Path,
    event: str,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a structured progress event from the current ledger tip."""
    paths = loop_paths(run_dir)
    record = last_ledger_record(run_dir) or {}
    state: dict[str, Any] = {}
    budget: dict[str, Any] = {}
    try:
        if paths["state"].exists():
            state = read_json(paths["state"])
    except (OSError, ValueError, json.JSONDecodeError):
        state = {}
    try:
        if paths["budget"].exists():
            budget = read_json(paths["budget"])
    except (OSError, ValueError, json.JSONDecodeError):
        budget = {}
    last_completed = int(record.get("iteration") or state.get("last_iteration") or 0)
    spent = int(budget.get("spent_iterations") or last_completed or 0)
    if spent < last_completed:
        spent = last_completed
    max_iter = int(budget.get("max_iterations") or 0)
    decision = str(record.get("decision") or state.get("status") or "?")
    last_objective = str(record.get("objective") or "")
    remaining = max(0, max_iter - spent) if max_iter else 0
    status = str(state.get("status") or "")
    ts = utc_now()

    # Next ledger index the driver is (or was) trying to append.
    if max_iter and spent >= max_iter:
        next_iteration = spent
    else:
        next_iteration = spent + 1 if spent >= 0 else 1

    attempt_event = event in _ATTEMPT_PROGRESS_EVENTS
    if attempt_event:
        # Do not present the last banked row as the failed/started attempt.
        iteration = next_iteration
        npp = str(state.get("next_preferred_path") or "").strip()
        if npp:
            objective = npp
        elif last_completed:
            objective = (
                f"Start iteration {next_iteration} (next after banked {last_completed})"
            )
        else:
            objective = f"Start iteration {next_iteration}"
        if event == "iteration_failed":
            progress_note = (
                f"Failed starting next after banked {last_completed} "
                f"(attempting {next_iteration}); banked work is unchanged."
            )
            output = (
                f"(No new ledger row.) Last banked iter {last_completed} remains "
                f"successful if present; this notify is only about the next driver attempt."
            )
        elif event == "quota_wait":
            progress_note = (
                f"Provider quota/credit while attempting {next_iteration} "
                f"(after banked {last_completed})."
            )
            output = ""
        elif event == "auth_failure":
            progress_note = (
                f"Provider auth/session failure while attempting {next_iteration} "
                f"(after banked {last_completed}); not a credit wait."
            )
            output = ""
        elif event.startswith("panel_target"):
            progress_note = (
                f"Panel target phase for upcoming iter {next_iteration} "
                f"(banked {last_completed})."
            )
            output = ""
        else:
            # iteration_start
            progress_note = (
                f"Starting iter {next_iteration} after banked {last_completed}."
                if last_completed
                else f"Starting iter {next_iteration}."
            )
            output = ""
    else:
        iteration = last_completed
        objective = last_objective
        output = resolve_progress_result_text(record)
        progress_note = ""

    why, where = build_progress_why_where(
        run_dir, record, state, attempt_event=attempt_event
    )

    # Research-topic title for notify identity (not bare "loop" / dir only).
    try:
        notify_ident = resolve_loop_notify_identity(run_dir)
    except Exception:  # noqa: BLE001
        notify_ident = {"title": run_dir.name, "slug": run_dir.name}
    research_title = str(notify_ident.get("title") or run_dir.name).strip() or run_dir.name
    # Compact line: prefer research title; append dir when it adds disambiguation.
    if run_dir.name and run_dir.name != research_title and len(research_title) < 60:
        compact_name = f"{research_title} ({run_dir.name})"
    else:
        compact_name = research_title

    # Compact one-liner for logs / LIVE_STATUS summaries.
    if attempt_event:
        compact = (
            f"autoloop {compact_name}: [{event}] banked {last_completed}/"
            f"{max_iter or '?'} attempting {next_iteration} "
            f"({decision}) — {objective[:160]}"
        )
    else:
        compact = (
            f"autoloop {compact_name}: [{event}] iter {iteration}/{max_iter or '?'} "
            f"({decision}) — {objective[:160]}"
            + (f" | {output[:240]}" if output else "")
        )
    text = format_progress_notify_text(
        loop_name=research_title,
        event=event,
        iteration=iteration,
        max_iter=max_iter,
        remaining=remaining,
        decision=decision,
        status=status,
        objective=objective,
        output=output,
        timestamp=ts,
        last_completed=last_completed if attempt_event else None,
        next_iteration=next_iteration if attempt_event else None,
        progress_note=progress_note,
        why=why,
        where=where,
    )
    text_html = format_progress_notify_telegram_html(
        loop_name=research_title,
        event=event,
        iteration=iteration,
        max_iter=max_iter,
        remaining=remaining,
        decision=decision,
        status=status,
        objective=objective,
        output=output,
        timestamp=ts,
        last_completed=last_completed if attempt_event else None,
        next_iteration=next_iteration if attempt_event else None,
        progress_note=progress_note,
        why=why,
        where=where,
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": ts,
        "event": event,
        "dir": str(run_dir),
        "iteration": iteration,
        "last_completed_iteration": last_completed,
        "next_iteration": next_iteration,
        "spent_iterations": spent,
        "max_iterations": max_iter,
        "remaining_iterations": remaining,
        "decision": decision,
        "status": status,
        "objective": objective[:400],
        "output_preview": output[:500],
        "why": why[:500] if why else "",
        "where": where[:700] if where else "",
        "text": text,
        "text_compact": compact,
        "text_html": text_html,
    }
    if progress_note:
        payload["progress_note"] = progress_note
    extra_data = dict(extra or {})
    if extra_data:
        for key, value in extra_data.items():
            if value is None or key == "text_override":
                continue
            payload[key] = value
        if extra_data.get("text_override"):
            payload["legacy_text_override"] = str(extra_data["text_override"])

    # Notify v2 owns all externally visible renderings. Keep the original flat
    # fields for progress readers while exposing the validated envelope for the
    # remote bridge and future consumers.
    # A poisoned ledger row must cost the rich rendering, not the drive: the
    # flat payload assembled above is already a complete progress event.
    try:
        envelope = _build_notify_v2_envelope(
            run_dir,
            event,
            record=record,
            state=state,
            budget=budget,
            extra=extra_data,
            iteration=iteration,
            spent=spent,
            max_iter=max_iter,
            objective=objective,
            output=output,
            progress_note=progress_note,
            why=why,
            where=where,
            timestamp=ts,
            attempt_event=attempt_event,
        )
        rendered = notify_v2.render_all(envelope)
    except notify_v2.NotifyValidationError as exc:
        sys.stderr.write(
            f"autoloop: notify envelope unavailable ({exc}); plain-text progress only\n"
        )
        payload["notification_error"] = str(exc)[:200]
        return _scrub_progress_payload(payload)
    payload.update(notify_v2.legacy_flat_fields(envelope))
    payload.update(
        {
            "dir": str(run_dir),
            "last_completed_iteration": last_completed,
            "next_iteration": next_iteration,
            "why": why[:500] if why else "",
            "where": where[:700] if where else "",
            "notification_schema": envelope.get("schema"),
            "notification_schema_version": envelope.get("schema_version"),
            "notification": envelope,
            "text": rendered["markdown"],
            "text_html": rendered["telegram_html"],
            "text_compact": rendered["compact"],
        }
    )
    return _scrub_progress_payload(payload)


_PROGRESS_SECRET_ENV_NAME = re.compile(
    r"(?:API[_-]?KEY|AUTH|BEARER|COOKIE|CREDENTIAL|OAUTH|PASS(?:WORD|WD)?|"
    r"PRIVATE[_-]?KEY|SECRET|SESSION|TOKEN)",
    re.IGNORECASE,
)
_PROGRESS_PII_KEY = re.compile(
    r"(?i)(?:^|[_ -])(?:pii|personal[_ -]?data|participant|patient|subject|"
    r"research[_ -]?subject|data[_ -]?subject|full[_ -]?name|contact[_ -]?name|"
    r"email|phone|home[_ -]?address|street[_ -]?address|date[_ -]?of[_ -]?birth|"
    r"birth[_ -]?date|dob|ssn|passport|national[_ -]?id|tax[_ -]?id)(?:$|[_ -])"
)


def _progress_secret_values(
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Return only credential-shaped environment values for exact redaction."""

    source = os.environ if environ is None else environ
    return [
        str(value)
        for name, value in source.items()
        if _PROGRESS_SECRET_ENV_NAME.search(str(name)) and len(str(value)) >= 4
    ]


def _scrub_progress_payload(
    payload: Any,
    *,
    secret_values: list[str] | None = None,
    key_hint: str = "",
) -> Any:
    """Recursively scrub every string before progress leaves host memory."""

    secrets = _progress_secret_values() if secret_values is None else secret_values
    if isinstance(payload, str):
        if key_hint and _PROGRESS_SECRET_ENV_NAME.search(key_hint):
            return notify_v2.REDACTION
        if key_hint and _PROGRESS_PII_KEY.search(key_hint):
            return notify_v2.PII_REDACTION
        return notify_v2.redact_text(payload, secrets)
    if isinstance(payload, (list, tuple)):
        return [
            _scrub_progress_payload(item, secret_values=secrets, key_hint=key_hint)
            for item in payload
        ]
    if isinstance(payload, Mapping):
        scrubbed: dict[Any, Any] = {}
        for raw_key, item in payload.items():
            key = (
                notify_v2.redact_text(raw_key, secrets)
                if isinstance(raw_key, str)
                else raw_key
            )
            scrubbed[key] = _scrub_progress_payload(
                item,
                secret_values=secrets,
                key_hint=str(key),
            )
        return scrubbed
    return payload


def write_live_status(run_dir: Path, payload: dict[str, Any], log_dir: Path | None = None) -> None:
    """Write LIVE_STATUS.md and append progress.jsonl (best-effort, never raises)."""
    try:
        # Defend direct callers as well as build_progress_event(): neither the
        # JSONL audit surface nor LIVE_STATUS may receive raw caller extras.
        payload = _scrub_progress_payload(payload)
        paths = progress_paths(run_dir, log_dir)
        _ensure_real_directory(paths["log_dir"])
        if os.name == "posix":
            log_info = os.lstat(paths["log_dir"])
            if log_info.st_uid != os.geteuid() or not stat.S_ISDIR(log_info.st_mode):
                raise OSError("progress log directory is not current-user owned")
            os.chmod(paths["log_dir"], 0o700, follow_symlinks=False)
        append_jsonl(paths["progress_jsonl"], payload)
        recovery_hint = ""
        recovery_path = loop_paths(run_dir)["recovery"]
        if recovery_path.exists():
            try:
                for line in recovery_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("- HEARTBEAT") or line.startswith("- Next safe action"):
                        recovery_hint = notify_v2.redact_text(
                            line.lstrip("- ").strip(),
                            _progress_secret_values(),
                        )
                        break
            except OSError:
                recovery_hint = ""
        body = "\n".join(
            [
                "# Autonomous loop live status",
                "",
                f"- Updated: {payload.get('timestamp', utc_now())}",
                f"- Event: `{payload.get('event', '')}`",
                f"- Loop: `{run_dir}`",
                (
                    f"- Progress: **{payload.get('spent_iterations', '?')}"
                    f"/{payload.get('max_iterations') or '?'}**"
                    f" ({payload.get('remaining_iterations', '?')} remaining)"
                ),
                f"- Loop status: `{payload.get('status') or '?'}`",
                f"- Last decision: `{payload.get('decision') or '?'}`",
                f"- Last banked iteration: **{payload.get('last_completed_iteration', payload.get('iteration', '?'))}**",
                (
                    f"- Display iteration: **{payload.get('iteration', '?')}**"
                    + (
                        f" (attempting next={payload.get('next_iteration')})"
                        if payload.get("next_iteration") is not None
                        and payload.get("event")
                        in (
                            "iteration_start",
                            "iteration_failed",
                            "quota_wait",
                            "panel_target_start",
                            "panel_target_ok",
                            "panel_target_fail",
                        )
                        else ""
                    )
                ),
                f"- Objective: {payload.get('objective') or '(none yet)'}",
                f"- Output preview: {payload.get('output_preview') or '(none yet)'}",
                f"- Summary: {payload.get('text') or ''}",
            ]
            + (
                [f"- Driver rc: `{payload.get('rc')}`", f"- Drive cycle: {payload.get('drive_cycle')}"]
                if payload.get("drive_cycle") is not None or payload.get("rc") is not None
                else []
            )
            + (
                [f"- Log: `{payload.get('log_path')}`"]
                if payload.get("log_path")
                else []
            )
            + (
                [f"- Recovery hint: {recovery_hint}"]
                if recovery_hint
                else []
            )
            + [
                "",
                "This file is rewritten after every drive cycle and every `watch` event.",
                "Full history: `driver_logs/progress.jsonl`. Narrative log: `PROGRESS_REPORT.md`.",
                "Kill switches: `STOP_REQUESTED`, `PAUSE`, or disarm the driver registry entry.",
                "",
            ]
        )
        _atomic_write_runtime_text(paths["live_status"], body)
    except Exception:  # noqa: BLE001 - progress surfaces must never kill the driver.
        pass


# Events that fan out to Zulip/Telegram.
#
# Intentionally omits:
# - iteration_start / watch_start: pair with iteration_ok ~1s later (looks like
#   "every message twice").
# - iteration (watch ledger tick): drive already owns remote completion via
#   iteration_ok / iteration_failed. When drive + watch run together, notifying
#   both produced duplicate Zulip posts for the same iteration.
# Wait ticks (strategy_review_wait / goal_focus_wait / result_review_wait) stay
# in progress.jsonl / LIVE_STATUS but are not remote-notified: replan loops re-emit
# them every ≥30s and produced Zulip spam. Remote is for outcomes and hard stops.
_DEFAULT_REMOTE_NOTIFY_EVENTS = frozenset(
    {
        "drive_start",
        "drive_stop",
        "iteration_ok",
        "iteration_rejected",
        "iteration_failed",
        "quota_wait",
        "auth_failure",
        "paused",
        "terminal",
        "driver_dead",
        "goal_priority_hard_replan",
        "goal_focus_replan",
        "result_review_error",
        "supervisor",
    }
)

# In-process + on-disk dedupe so concurrent drive/watch (or restarts) cannot
# double-post the exact rendered event. A corrected body for the same
# event/iteration is deliberately not suppressed. Disk file is per loop dir.
_LAST_REMOTE_NOTIFY: dict[str, Any] = {"fp": "", "at": 0.0}
_REMOTE_NOTIFY_DEDUPE_SEC = 15.0
_REMOTE_NOTIFY_ITER_DEDUPE_SEC = 120.0


def _remote_notify_dedupe_path(run_dir: Path) -> Path:
    return Path(run_dir).expanduser().resolve() / "driver_logs" / ".remote_notify_dedupe.json"


def _remote_notify_load_disk(run_dir: Path) -> dict[str, Any]:
    path = _remote_notify_dedupe_path(run_dir)
    try:
        info = os.lstat(path)
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_nlink", 1) != 1
            or (os.name == "posix" and info.st_uid != os.geteuid())
            or (os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077)
        ):
            return {}
        data = json.loads(_read_regular_text(path, max_bytes=64_000))
        if isinstance(data, dict):
            return data
    except Exception:  # noqa: BLE001 - best-effort
        pass
    return {}


def _remote_notify_store_disk(run_dir: Path, data: dict[str, Any]) -> None:
    path = _remote_notify_dedupe_path(run_dir)
    try:
        _atomic_write_runtime_text(path, json.dumps(data, sort_keys=True) + "\n")
    except Exception:  # noqa: BLE001 - best-effort
        pass


def _remote_notify_is_duplicate(
    run_dir: Path,
    *,
    fp: str,
    iter_key: str,
    now: float,
) -> bool:
    """True if this remote body/iteration was already sent recently."""
    last_fp = str(_LAST_REMOTE_NOTIFY.get("fp") or "")
    last_at = float(_LAST_REMOTE_NOTIFY.get("at") or 0.0)
    if fp and fp == last_fp and (now - last_at) < _REMOTE_NOTIFY_DEDUPE_SEC:
        return True
    memory_iter_key = str(_LAST_REMOTE_NOTIFY.get("iter_key") or "")
    memory_iter_at = float(_LAST_REMOTE_NOTIFY.get("iter_at") or 0.0)
    if (
        iter_key
        and iter_key == memory_iter_key
        and (now - memory_iter_at) < _REMOTE_NOTIFY_ITER_DEDUPE_SEC
    ):
        return True
    disk = _remote_notify_load_disk(run_dir)
    disk_fp = str(disk.get("fp") or "")
    disk_at = float(disk.get("at") or 0.0)
    if fp and fp == disk_fp and (now - disk_at) < _REMOTE_NOTIFY_DEDUPE_SEC:
        return True
    disk_iter_key = str(disk.get("iter_key") or "")
    disk_iter_at = float(disk.get("iter_at") or 0.0)
    if (
        iter_key
        and iter_key == disk_iter_key
        and (now - disk_iter_at) < _REMOTE_NOTIFY_ITER_DEDUPE_SEC
    ):
        return True
    return False


def _remote_notify_remember(
    run_dir: Path,
    *,
    fp: str,
    iter_key: str,
    now: float,
) -> None:
    _LAST_REMOTE_NOTIFY["fp"] = fp
    _LAST_REMOTE_NOTIFY["at"] = now
    if iter_key:
        _LAST_REMOTE_NOTIFY["iter_key"] = iter_key
        _LAST_REMOTE_NOTIFY["iter_at"] = now
    disk = {
        "fp": fp,
        "at": now,
        "iter_key": iter_key or str(_LAST_REMOTE_NOTIFY.get("iter_key") or ""),
        "iter_at": now if iter_key else float(_LAST_REMOTE_NOTIFY.get("iter_at") or 0.0),
    }
    _remote_notify_store_disk(run_dir, disk)


def _slugify_notify_id(text: str, *, max_len: int = 48) -> str:
    raw = (text or "").strip().lower()
    if not raw:
        return ""
    out: list[str] = []
    prev_dash = False
    for ch in raw:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif ch in {" ", "_", "-", ".", "/"}:
            if not prev_dash and out:
                out.append("-")
                prev_dash = True
    slug = "".join(out).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug


def _is_banned_notify_generic(name: str) -> bool:
    cleaned = (name or "").strip().lower().replace(" ", "_")
    if not cleaned:
        return True
    if cleaned in _BANNED_NOTIFY_GENERIC_NAMES:
        return True
    # "autonomous-kge3" is specific enough; bare "autonomous" is not.
    return False


def _is_legacy_notify_placeholder(value: str) -> bool:
    """Treat the old example topic marker as missing, not as an identity.

    Historical copies of ``failover.example.json`` used this prose marker as
    a concrete ``job_slug``.  Accept only the obvious case/separator variants
    so a legitimate explicit title or topic is never discarded accidentally.
    """

    normalized = re.sub(r"[\s._-]+", "-", (value or "").strip().casefold()).strip("-")
    return normalized == "optional-stable-zulip-topic-id"


def _short_title_from_goal(goal: str, *, max_len: int = 80) -> str:
    g = " ".join((goal or "").split())
    if not g:
        return ""
    # Prefer first sentence / clause.
    for sep in (". ", "; ", " — ", " - "):
        if sep in g:
            g = g.split(sep, 1)[0].strip()
            break
    if len(g) > max_len:
        cut = g[: max_len - 1].rsplit(" ", 1)[0]
        g = (cut or g[: max_len - 1]).rstrip(" ,;:") + "…"
    return g


def resolve_loop_notify_identity(run_dir: Path | None = None) -> dict[str, str]:
    """Research-topic title + Zulip job slug for progress notify.

    Resolution for *title* (human):
      1. failover.json / notify.json / standing_orders.notify research_title
         (aliases: notify_title, display_name)
      2. env AAS_AUTOLOOP_RESEARCH_TITLE / AAS_REMOTE_JOB_TITLE
      3. short form of authoritative goal_contract.goal
      4. short form of loop_state.goal
      5. directory name if not a banned generic

    Resolution for *slug* (Zulip topic / job id):
      1. explicit job_slug in config, then AAS_REMOTE_JOB_ID
      2. slugify(title)
      3. directory name if not banned-generic
    """
    title = ""
    job_slug = ""
    dir_name = ""
    authoritative_goal = ""
    state_goal = ""
    if run_dir is not None:
        rd = Path(run_dir).expanduser().resolve()
        dir_name = rd.name.strip()
        # Explicit config files / standing orders.
        for rel in ("failover.json", "notify.json"):
            path = rd / rel
            if not path.is_file():
                continue
            try:
                data = read_json(path)
            except Exception:  # noqa: BLE001
                data = {}
            if not isinstance(data, dict):
                continue
            for key in ("research_title", "notify_title", "display_name"):
                val = str(data.get(key) or "").strip()
                if val and not _is_legacy_notify_placeholder(val) and not title:
                    title = val
            for key in ("job_slug", "remote_job_id"):
                val = str(data.get(key) or "").strip()
                if val and not _is_legacy_notify_placeholder(val) and not job_slug:
                    job_slug = val
        try:
            contract_path = rd / "goal_contract.json"
            if contract_path.is_file():
                contract = read_json(contract_path)
                if isinstance(contract, dict):
                    authoritative_goal = str(
                        contract.get("goal") or contract.get("main_goal") or ""
                    ).strip()
        except Exception:  # noqa: BLE001
            authoritative_goal = ""
        try:
            state_path = rd / "loop_state.json"
            if state_path.is_file():
                state = read_json(state_path)
                if isinstance(state, dict):
                    state_goal = str(state.get("goal") or "").strip()
                    so = state.get("standing_orders")
                    if isinstance(so, dict):
                        notify_so = so.get("notify")
                        if isinstance(notify_so, dict):
                            for key in (
                                "research_title",
                                "notify_title",
                                "display_name",
                            ):
                                val = str(notify_so.get(key) or "").strip()
                                if (
                                    val
                                    and not _is_legacy_notify_placeholder(val)
                                    and not title
                                ):
                                    title = val
                            for key in ("job_slug", "remote_job_id"):
                                val = str(notify_so.get(key) or "").strip()
                                if (
                                    val
                                    and not _is_legacy_notify_placeholder(val)
                                    and not job_slug
                                ):
                                    job_slug = val
        except Exception:  # noqa: BLE001
            pass
    if not title:
        for env_key in ("AAS_AUTOLOOP_RESEARCH_TITLE", "AAS_REMOTE_JOB_TITLE"):
            env_title = (os.environ.get(env_key) or "").strip()
            if env_title and not _is_legacy_notify_placeholder(env_title):
                title = env_title
                break
    if not job_slug:
        env_job = (os.environ.get("AAS_REMOTE_JOB_ID") or "").strip()
        if env_job and not _is_legacy_notify_placeholder(env_job):
            job_slug = env_job
    if not title and authoritative_goal:
        title = _short_title_from_goal(authoritative_goal)
    if not title and state_goal:
        title = _short_title_from_goal(state_goal)
    if not title and dir_name and not _is_banned_notify_generic(dir_name):
        title = dir_name
    if not title:
        title = (
            dir_name
            if dir_name and not _is_banned_notify_generic(dir_name)
            else "research"
        )
    if not job_slug:
        job_slug = _slugify_notify_id(title)
    if not job_slug and dir_name and not _is_banned_notify_generic(dir_name):
        job_slug = _slugify_notify_id(dir_name) or dir_name
    if not job_slug:
        job_slug = _slugify_notify_id(dir_name) or "research"
    return {"title": title, "slug": job_slug, "dir_name": dir_name}


def resolve_remote_job_id(run_dir: Path | None = None) -> str | None:
    """Topic id for remote-bridge: explicit env/config, else research-topic slug."""
    env_id = (os.environ.get("AAS_REMOTE_JOB_ID") or "").strip()
    if env_id and not _is_legacy_notify_placeholder(env_id):
        return env_id
    if run_dir is not None:
        ident = resolve_loop_notify_identity(run_dir)
        slug = (ident.get("slug") or "").strip()
        if slug:
            return slug
        name = Path(run_dir).expanduser().resolve().name.strip()
        if name:
            return name
    return None


def emit_loop_progress(
    run_dir: Path,
    event: str,
    *,
    log_dir: Path | None = None,
    notify_cmd: str | None = None,
    notify_channel: str | None = None,
    to_stderr: bool = True,
    to_stdout_json: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a progress event to disk, optional notify hook, and console."""
    payload = build_progress_event(run_dir, event, extra=extra)
    write_live_status(run_dir, payload, log_dir=log_dir)
    env_payload = {
        "AUTOLOOP_EVENT": str(event),
        "AUTOLOOP_DIR": str(run_dir),
        "AUTOLOOP_ITERATION": str(payload.get("iteration", "")),
        "AUTOLOOP_DECISION": str(payload.get("decision", "")),
        "AUTOLOOP_TEXT": str(payload.get("text", "")),
        "AUTOLOOP_SPENT": str(payload.get("spent_iterations", "")),
        "AUTOLOOP_MAX": str(payload.get("max_iterations", "")),
        "AUTOLOOP_STATUS": str(payload.get("status", "")),
    }
    if to_stderr:
        # Prefer compact one-liner on stderr; multi-line body is for notify clients.
        sys.stderr.write(str(payload.get("text_compact") or payload.get("text") or "") + "\n")
        sys.stderr.flush()
    # Machine-readable JSON lines stay on stdout whenever requested. Remote-bridge
    # notify is additive and must not suppress them (secrets in env would break
    # watch consumers / tests). Only a raw --notify-cmd shell hook replaces JSON.
    if to_stdout_json and not notify_cmd:
        print(json.dumps(env_payload), flush=True)
    external_notify_allowed = (
        goal_focus_runtime_mode(run_dir) != "enforce"
        or os.environ.get("AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS") == "allow"
    )
    # Structured remote-bridge notify (preferred). Channel already resolved by
    # drive/watch/arm via resolve_notify_channel; env is a last-resort fallback.
    channel = notify_channel
    if channel is None:
        channel = resolve_notify_channel(explicit=None, run_dir=run_dir, default_auto=False)
    if (
        external_notify_allowed
        and channel
        and channel != "off"
        and event in _DEFAULT_REMOTE_NOTIFY_EVENTS
    ):
        # Prefer the validated v2 envelope; Telegram/Zulip rendering and stable
        # topic selection belong to remote-bridge, not this caller.
        notify_text = str(payload.get("text") or payload.get("text_compact") or "")
        notify_html = str(payload.get("text_html") or "")
        notify_event = payload.get("notification")
        now = time.time()
        try:
            fp = (
                notify_v2.delivery_fingerprint(notify_event)
                if isinstance(notify_event, dict)
                else f"{event}\n{notify_text}".strip()
            )
            retry_fp = (
                notify_v2.retry_fingerprint(notify_event)
                if isinstance(notify_event, dict)
                else fp
            )
        except Exception:  # noqa: BLE001 - delivery remains best-effort
            fp = f"{event}\n{notify_text}".strip()
            retry_fp = fp
        iter_no = payload.get("iteration")
        # Timestamp-only rerenders share a key, while materially changed state
        # keeps a distinct semantic fingerprint and remains deliverable.
        iter_key = (
            f"{event}:{iter_no}:{retry_fp}"
            if iter_no not in (None, "")
            else ""
        )
        if _remote_notify_is_duplicate(
            run_dir, fp=fp, iter_key=iter_key, now=now
        ):
            pass  # skip duplicate remote send
        else:
            argv = resolve_remote_notify_argv(
                channel if channel != "both" else "both",
                notify_text,
                job_id=resolve_remote_job_id(run_dir),
                html=notify_html or None,
                event_json_stdin=isinstance(notify_event, dict),
            )
            if argv:
                try:
                    completed = subprocess.run(
                        argv,
                        check=False,
                        timeout=60,
                        capture_output=True,
                        text=True,
                        input=(
                            json.dumps(notify_event, ensure_ascii=False)
                            if isinstance(notify_event, dict)
                            else None
                        ),
                        env=remote_notify_environment(),
                    )
                    bridge_result: dict[str, Any] = {}
                    try:
                        loaded = json.loads(completed.stdout or "{}")
                        if isinstance(loaded, dict):
                            bridge_result = loaded
                    except json.JSONDecodeError:
                        bridge_result = {}
                    delivery = bridge_result.get("delivery")
                    delivered_or_known = bool(
                        isinstance(delivery, dict)
                        and (delivery.get("delivered") or delivery.get("deduplicated"))
                    )
                    if (
                        completed.returncode == 0
                        and bridge_result.get("ok") is True
                        and not bridge_result.get("dry_run")
                        and delivered_or_known
                    ):
                        _remote_notify_remember(
                            run_dir, fp=fp, iter_key=iter_key, now=now
                        )
                except Exception:  # noqa: BLE001 - notify is best-effort
                    pass
    if (
        external_notify_allowed
        and notify_cmd
        and os.environ.get("AAS_ALLOW_RAW_NOTIFY_CMD") == "1"
    ):
        watch_notify(notify_cmd, env_payload)
    return payload


def notify_event_command(args: argparse.Namespace) -> dict[str, Any]:
    """Emit one host-authored Notify v2 event through the normal progress path."""
    run_dir = Path(args.dir).expanduser().resolve()
    compute: Any = None
    if bool(getattr(args, "compute_none", False)):
        if getattr(args, "compute_run", None):
            raise ValueError("--compute-none is mutually exclusive with --compute-run")
        compute = []
    elif getattr(args, "compute_run", None):
        parsed = parse_compute_runs(args.compute_run)
        compute = parsed.get("services") or []
    extra = {
        "completed_summary": str(args.completed or "").strip(),
        "current_summary": str(args.current or "").strip(),
        "next_action": str(args.plan or "").strip(),
        "iteration_status": str(args.iteration_status or "not_applicable"),
        "review_status": str(args.review_status or "not_required"),
        "loop_status": str(args.loop_status or ""),
        "provider": str(args.provider or "").strip(),
        "driver_agent": str(getattr(args, "driver_agent", "") or "").strip() or None,
        "panel_agents": (
            list(args.panel_agent) if getattr(args, "panel_agent", None) is not None else None
        ),
        "other_agents": (
            list(args.other_agent) if getattr(args, "other_agent", None) is not None else None
        ),
        "finished_at": str(args.finished_at or "").strip(),
        "duration_seconds": args.duration_seconds,
    }
    if bool(getattr(args, "compute_none", False)) or getattr(
        args, "compute_run", None
    ):
        extra["compute"] = compute
    payload = emit_loop_progress(
        run_dir,
        str(args.event),
        notify_channel=(
            resolve_notify_channel(
                explicit=args.notify,
                run_dir=run_dir,
                default_auto=True,
            )
            or "off"
        ),
        to_stderr=not bool(args.quiet),
        to_stdout_json=False,
        extra=extra,
    )
    return {
        "status": "ok",
        "action": "notify-event",
        "dir": str(run_dir),
        "event": payload.get("notification") or payload,
    }


def watch_notify(cmd: str | None, payload: dict[str, str]) -> None:
    if not cmd:
        print(json.dumps(payload), flush=True)
        return
    env = raw_notify_environment(payload)
    try:
        subprocess.run(cmd, shell=True, env=env, timeout=60, check=False)
    except Exception:  # noqa: BLE001 - notification is best-effort.
        pass


def watch_command(args: argparse.Namespace) -> dict[str, Any]:
    """Progress reporter for a driven loop.

    Emits one event per newly appended iteration, one on terminal state, and
    one when the registry says a driver owns the loop but its pid is dead.
    Always refreshes LIVE_STATUS.md and appends driver_logs/progress.jsonl.
    Read-only alongside `drive`; safe to start or stop at any time. Without
    --notify-cmd, events also print as JSON lines on stdout (remote-bridge
    channel notify is additive and does not suppress them). With --notify-cmd
    and AAS_ALLOW_RAW_NOTIFY_CMD=1, the command runs via the shell with
    AUTOLOOP_EVENT/_DIR/_ITERATION/_DECISION/_TEXT in env instead of JSON.
    """
    run_dir = Path(args.dir).expanduser().resolve()
    reg = registry_dir(args)
    log_dir = (
        Path(args.log_dir).expanduser()
        if getattr(args, "log_dir", None)
        else run_dir / "driver_logs"
    )
    start_record = last_ledger_record(run_dir)
    start_iter = int(start_record.get("iteration", 0)) if start_record else 0
    seen = args.from_iteration if args.from_iteration >= 0 else start_iter
    driver_dead_alerted = False
    events = 0
    notify_channel = resolve_notify_channel(
        explicit=getattr(args, "notify", None),
        run_dir=run_dir,
        registry=reg,
        default_auto=True,
    ) or "off"
    notify_enabled = notify_channel != "off"
    # Seed LIVE_STATUS immediately so operators see current tip without waiting.
    emit_loop_progress(
        run_dir,
        "watch_start",
        log_dir=log_dir,
        notify_cmd=None,
        notify_channel=notify_channel,
        to_stderr=False,
        to_stdout_json=False,
        extra={"source": "watch", "notify_channel": notify_channel or "off"},
    )
    while True:
        verdict = compute_done(run_dir)
        record = last_ledger_record(run_dir)
        current = int(record.get("iteration", 0)) if record else 0
        if record and current > seen:
            decision = str(record.get("decision", "?"))
            text = (
                f"autoloop {run_dir.name}: iteration {current} ({decision}) — "
                f"{str(record.get('objective', ''))[:160]} | {str(record.get('output', ''))[:240]}"
            )
            emit_loop_progress(
                run_dir,
                "iteration",
                log_dir=log_dir,
                notify_cmd=args.notify_cmd,
                notify_channel=notify_channel,
                to_stderr=bool(args.notify_cmd or notify_enabled),
                to_stdout_json=not bool(args.notify_cmd),
                extra={"source": "watch", "text_override": text},
            )
            events += 1
            seen = current
        if verdict.get("done"):
            reason = str(verdict.get("reason") or "done")
            emit_loop_progress(
                run_dir,
                "terminal",
                log_dir=log_dir,
                notify_cmd=args.notify_cmd,
                notify_channel=notify_channel,
                to_stderr=bool(args.notify_cmd or notify_enabled),
                to_stdout_json=not bool(args.notify_cmd),
                extra={"source": "watch", "terminal_reason": reason},
            )
            events += 1
            return {"status": "ok", "action": "watch", "events": events, "reason": reason}
        if not verdict.get("paused"):
            entry = loop_driver_entry(reg, run_dir)
            pid = entry.get("pid") if entry else None
            alive = isinstance(pid, int) and pid > 0 and pid_alive(pid)
            if entry is not None and not alive and not driver_dead_alerted:
                driver_dead_alerted = True
                emit_loop_progress(
                    run_dir,
                    "driver_dead",
                    log_dir=log_dir,
                    notify_cmd=args.notify_cmd,
                    notify_channel=notify_channel,
                    to_stderr=bool(args.notify_cmd or notify_enabled),
                    to_stdout_json=not bool(args.notify_cmd),
                    extra={"source": "watch", "driver_pid": pid},
                )
                events += 1
            elif alive:
                driver_dead_alerted = False
        if args.once:
            return {"status": "ok", "action": "watch", "events": events, "reason": "once"}
        time.sleep(max(5, int(args.poll)))


def refresh_heartbeat(reg: Path, run_id: object) -> None:
    try:
        safe_id = safe_registry_run_id(run_id)
    except ValueError:
        return
    try:
        snapshots = strict_registry_snapshots(reg)
        for candidate in snapshots:
            _validate_registry_authority_snapshot(candidate)
        matches = [
            candidate
            for candidate in snapshots
            if candidate.path.name == f"{safe_id}.json"
            and candidate.entry.get("run_id") == safe_id
        ]
        if len(matches) != 1:
            return
        snapshot = matches[0]
        entry = dict(snapshot.entry)
        entry["heartbeat"] = utc_now()
        _write_registry_json_snapshot(reg, safe_id, entry, expected=snapshot)
    except Exception:  # noqa: BLE001 - heartbeat refresh is best-effort.
        pass


def read_log_tail(path: Path, limit: int = 8192) -> str:
    absolute = Path(os.path.abspath(path))
    try:
        if os.name == "nt":
            info = os.lstat(absolute)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                return ""
            handle = absolute.open("rb")
        else:
            directory_fd = _open_directory_nofollow(absolute.parent)
            try:
                file_fd = os.open(
                    absolute.name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
                    dir_fd=directory_fd,
                )
            finally:
                os.close(directory_fd)
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode):
                os.close(file_fd)
                return ""
            handle = os.fdopen(file_fd, "rb")
        with handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _read_open_log_tail(handle: Any, limit: int = 8192) -> str:
    """Capture the subprocess log through the exact descriptor created by host."""

    try:
        handle.flush()
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - limit))
        body = handle.read()
        handle.seek(0, os.SEEK_END)
        return str(body)
    except (OSError, ValueError):
        return ""


def _structured_panel_payloads(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for provider_name, metadata in (summary.get("results") or {}).items():
        if not isinstance(metadata, dict) or metadata.get("structured_valid") is not True:
            continue
        payload = metadata.get("structured_payload")
        if isinstance(payload, dict):
            payloads[str(provider_name)] = payload
    return payloads


def _panel_adjusted_registry(
    registry: dict[str, Any], payloads: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], set[str]]:
    """Build an ephemeral registry scored from reviewed interval estimates."""
    adjusted = copy.deepcopy(registry)
    observations: dict[str, dict[str, list[tuple[float, float]]]] = {}
    mentioned: set[str] = set()
    for payload in payloads.values():
        for candidate in payload.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            approach_id = str(candidate.get("approach_id") or "").strip()
            if not approach_id:
                continue
            mentioned.add(approach_id)
            factor_rows = observations.setdefault(approach_id, {})
            estimates = candidate.get("estimates")
            if not isinstance(estimates, dict):
                continue
            for factor, bounds in estimates.items():
                if not isinstance(bounds, dict):
                    continue
                # The panel wire schema uses the more explicit name while the
                # registry/scorer contract retains the shorter v2 key.
                registry_factor = (
                    "goal_resolution"
                    if str(factor) == "goal_resolution_contribution"
                    else str(factor)
                )
                try:
                    lower = float(bounds.get("lower"))
                    upper = float(bounds.get("upper"))
                except (TypeError, ValueError):
                    continue
                factor_rows.setdefault(registry_factor, []).append(
                    (min(lower, upper), max(lower, upper))
                )
    campaigns = adjusted.get("campaigns")
    if not isinstance(campaigns, dict):
        return adjusted, mentioned
    for campaign in campaigns.values():
        if not isinstance(campaign, dict):
            continue
        approaches = campaign.get("approaches")
        if not isinstance(approaches, dict):
            continue
        for approach_id, approach in approaches.items():
            if not isinstance(approach, dict):
                continue
            aid = str(approach_id)
            # A direction that no valid reviewer inspected is not eligible for
            # this decision, even if stale registry estimates look attractive.
            if aid not in mentioned:
                approach["status"] = "parked"
                continue
            reviewed_estimates: dict[str, dict[str, float]] = {}
            for factor, bounds in observations.get(aid, {}).items():
                reviewed_estimates[factor] = {
                    "lower": min(row[0] for row in bounds),
                    "upper": max(row[1] for row in bounds),
                }
            if reviewed_estimates:
                approach["estimates"] = reviewed_estimates
    return adjusted, mentioned


def _strategy_selection_from_panel(
    run_dir: Path, summary: dict[str, Any]
) -> dict[str, Any]:
    synthesis = summary.get("structured_synthesis")
    synthesis = synthesis if isinstance(synthesis, dict) else {}
    payloads = _structured_panel_payloads(summary)
    different = set(synthesis.get("different_family_valid_providers") or [])
    if not payloads or not different:
        return {
            "status": "waiting",
            "reason": "no valid different-family strategy advice",
            "summary": synthesis,
        }
    raw_primary_attestation = summary.get("primary_execution_attestation")
    raw_provider_attestations = summary.get("provider_execution_attestations")
    if not isinstance(raw_primary_attestation, dict) or not isinstance(
        raw_provider_attestations, dict
    ) or set(raw_provider_attestations) != set(payloads):
        return {
            "status": "waiting",
            "reason": "strategy panel lacks exact host executable attestations",
            "summary": synthesis,
        }
    try:
        primary_execution_attestation = (
            revalidate_provider_executable_attestation(
                raw_primary_attestation,
                forbidden_roots=(run_dir,),
            )
        )
        provider_execution_attestations = {
            provider_name: revalidate_provider_executable_attestation(
                raw_provider_attestations[provider_name],
                forbidden_roots=(run_dir,),
            )
            for provider_name in sorted(payloads)
        }
    except PanelIsolationError as exc:
        return {
            "status": "waiting",
            "reason": f"strategy panel executable attestation is invalid: {exc}",
            "summary": synthesis,
        }
    if any(
        payloads.get(provider_name, {}).get("decision") == "no_viable_candidate"
        for provider_name in different
    ):
        return {
            "status": "waiting",
            "reason": "different-family reviewer found no viable candidate",
            "summary": synthesis,
        }
    snapshot = summary.get("authority_snapshot")
    if not isinstance(snapshot, dict):
        return {
            "status": "waiting",
            "reason": "strategy panel result lacks the exact reviewed authority snapshot",
            "summary": synthesis,
        }
    try:
        authority_binding = goal_focus_v2.strategy_authority_binding(snapshot)
    except ValueError as exc:
        return {
            "status": "waiting",
            "reason": f"strategy authority snapshot is invalid: {exc}",
            "summary": synthesis,
        }
    registry = snapshot.get("approach_registry")
    if not isinstance(registry, dict):
        return {
            "status": "waiting",
            "reason": "strategy authority snapshot lacks the reviewed registry",
            "summary": synthesis,
        }
    registry = copy.deepcopy(registry)
    adjusted, mentioned = _panel_adjusted_registry(registry, payloads)
    selection = goal_focus_v2.select_direction(adjusted, run_dir=run_dir)
    selected_id = str(selection.get("selected_approach_id") or "")
    if selection.get("status") != "selected" or not selected_id or selected_id not in mentioned:
        return {
            "status": "waiting",
            "reason": "reviewed portfolio has no eligible direction",
            "selection": selection,
            "summary": synthesis,
        }
    reviewed_by_different = [
        provider_name
        for provider_name in sorted(different)
        if any(
            isinstance(candidate, dict)
            and str(candidate.get("approach_id") or "") == selected_id
            for candidate in payloads.get(provider_name, {}).get("candidates") or []
        )
    ]
    if not reviewed_by_different:
        return {
            "status": "waiting",
            "reason": "selected direction was not inspected by a different-family reviewer",
            "selection": selection,
            "summary": synthesis,
        }
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    # The concrete bounded action committed to current_plan must itself come
    # from a reviewer independent of the active primary family, not merely
    # share an approach id that another family happened to mention.
    for provider_name in reviewed_by_different:
        payload = payloads[provider_name]
        for candidate in payload.get("candidates") or []:
            if (
                isinstance(candidate, dict)
                and str(candidate.get("approach_id") or "") == selected_id
            ):
                candidates.append(
                    (int(candidate.get("rank") or 999), provider_name, candidate)
                )
    candidates.sort(key=lambda row: (row[0], row[1]))
    selected_candidate = copy.deepcopy(candidates[0][2]) if candidates else {}
    selection = copy.deepcopy(selection)
    selection.update(
        {
            "selected_candidate": selected_candidate,
            "panel_synthesis": copy.deepcopy(synthesis),
            "reviewed_by": sorted(payloads),
            "reviewed_by_different_family": reviewed_by_different,
            "panel_dissent": bool(synthesis.get("dissent")),
        }
    )
    review = {
        "schema_version": "direction_review.v2",
        "status": "passed",
        "different_family": True,
        "primary_provider": synthesis.get("primary_provider"),
        "primary_family": synthesis.get("primary_family"),
        "primary_execution_attestation": primary_execution_attestation,
        "providers": sorted(payloads),
        "different_family_providers": reviewed_by_different,
        "reviewer_families": sorted(
            {
                str(attestation.get("family") or "unverified")
                for attestation in provider_execution_attestations.values()
            }
        ),
        "provider_execution_attestations": provider_execution_attestations,
        "structured_synthesis": copy.deepcopy(synthesis),
        "provider_advice": copy.deepcopy(payloads),
        "authority_snapshot": authority_binding,
    }
    return {"status": "ready", "selection": selection, "review": review}


def _result_review_from_panel(
    pending: dict[str, Any], summary: dict[str, Any]
) -> dict[str, Any]:
    expected_id = str(pending.get("candidate_id") or "")
    expected_fingerprint = goal_focus_v2.candidate_fingerprint(pending)
    synthesis = summary.get("structured_synthesis")
    synthesis = synthesis if isinstance(synthesis, dict) else {}
    payloads = _structured_panel_payloads(summary)
    if not payloads:
        return {"status": "pending", "reason": "no valid structured result review"}
    wrong_ids = {
        str(payload.get("candidate_id") or "")
        for payload in payloads.values()
        if str(payload.get("candidate_id") or "") != expected_id
    }
    if wrong_ids or set(synthesis.get("candidate_ids") or []) != {expected_id}:
        return {
            "status": "pending",
            "reason": "result reviewers did not review the exact pending candidate",
            "wrong_candidate_ids": sorted(wrong_ids),
        }
    wrong_fingerprints = {
        str(payload.get("candidate_fingerprint") or "")
        for payload in payloads.values()
        if str(payload.get("candidate_fingerprint") or "") != expected_fingerprint
    }
    if (
        wrong_fingerprints
        or set(synthesis.get("candidate_fingerprints") or [])
        != {expected_fingerprint}
    ):
        return {
            "status": "pending",
            "reason": "result reviewers did not bind the exact pending candidate content",
            "expected_candidate_fingerprint": expected_fingerprint,
            "wrong_candidate_fingerprints": sorted(wrong_fingerprints),
        }
    pending_record = (
        pending.get("record") if isinstance(pending.get("record"), dict) else {}
    )
    attestation = (
        pending.get("host_execution_attestation")
        if isinstance(pending.get("host_execution_attestation"), dict)
        else {}
    )
    executor_provider = str(attestation.get("executor_provider") or "").strip()
    if (
        attestation.get("schema_version") != "host_execution_attestation.v1"
        or attestation.get("source") != "host_dispatch"
        or str(attestation.get("candidate_id") or "") != expected_id
        or not executor_provider
    ):
        return {
            "status": "pending",
            "reason": "staged candidate lacks a valid host-pinned executor attestation",
        }
    execution = (
        pending_record.get("execution")
        if isinstance(pending_record.get("execution"), dict)
        else {}
    )
    if str(execution.get("executor_provider") or "").strip() != executor_provider:
        return {
            "status": "pending",
            "reason": "staged execution provenance disagrees with its host attestation",
        }
    raw_executor_attestation = attestation.get("executor_attestation")
    raw_provider_attestations = summary.get("provider_execution_attestations")
    if not isinstance(raw_executor_attestation, dict) or not isinstance(
        raw_provider_attestations, dict
    ) or set(raw_provider_attestations) != set(payloads):
        return {
            "status": "pending",
            "reason": "result review lacks exact host executable attestations",
        }
    try:
        executor_attestation = revalidate_provider_executable_attestation(
            raw_executor_attestation
        )
        provider_execution_attestations = {
            provider_name: revalidate_provider_executable_attestation(
                raw_provider_attestations[provider_name]
            )
            for provider_name in sorted(payloads)
        }
    except PanelIsolationError as exc:
        return {
            "status": "pending",
            "reason": f"result review executable attestation is invalid: {exc}",
        }
    primary_family = str(executor_attestation.get("family") or "unverified")
    if (
        str(executor_attestation.get("provider") or "") != executor_provider
        or str(attestation.get("executor_family") or "") != primary_family
    ):
        return {
            "status": "pending",
            "reason": "staged executor identity disagrees with its host attestation",
        }
    different_providers = [
        name
        for name in sorted(payloads)
        if primary_family != "unverified"
        and str(
            provider_execution_attestations[name].get("family") or "unverified"
        )
        not in {"unverified", primary_family}
    ]
    if not different_providers:
        return {
            "status": "pending",
            "reason": "no valid different-family result review",
        }
    reviewer_families = sorted(
        {
            str(identity.get("family") or "unverified")
            for identity in provider_execution_attestations.values()
        }
    )
    requested_targets = goal_focus_v2.proposed_obligation_targets(pending_record)
    evidence_checked = pending_record.get("evidence_checked")
    try:
        staged_evidence = goal_focus_v2.validate_evidence_artifacts(
            pending_record, require_artifacts=True, candidate_id=expected_id
        )
    except ValueError as exc:
        return {
            "status": "pending",
            "reason": f"candidate evidence snapshot is invalid: {exc}",
        }
    requested_claims = {
        str(item) for item in pending_record.get("claim_ids") or [] if str(item)
    }
    if isinstance(evidence_checked, dict):
        requested_claims.update(
            str(item) for item in evidence_checked.get("claim_ids") or [] if str(item)
        )
    all_reviewers = set(payloads)
    reviewer_coverage_errors: dict[str, dict[str, list[str]]] = {}
    for provider_name, payload in payloads.items():
        provider_claims = {
            str(item.get("claim_id") or "")
            for item in payload.get("claim_reviews") or []
            if isinstance(item, dict) and str(item.get("claim_id") or "")
        }
        provider_obligations = {
            str(item.get("obligation_id") or "")
            for item in payload.get("obligation_reviews") or []
            if isinstance(item, dict) and str(item.get("obligation_id") or "")
        }
        missing_claims = requested_claims - provider_claims
        unexpected_claims = provider_claims - requested_claims
        missing_obligations = set(requested_targets) - provider_obligations
        unexpected_obligations = provider_obligations - set(requested_targets)
        if missing_claims or unexpected_claims or missing_obligations or unexpected_obligations:
            reviewer_coverage_errors[provider_name] = {
                "missing_claim_ids": sorted(missing_claims),
                "unexpected_claim_ids": sorted(unexpected_claims),
                "missing_obligation_ids": sorted(missing_obligations),
                "unexpected_obligation_ids": sorted(unexpected_obligations),
            }
    obligation_reviews: list[dict[str, Any]] = []
    accepted_obligations: set[str] = set()
    for obligation_id, target_status in sorted(requested_targets.items()):
        rows: list[tuple[str, dict[str, Any]]] = []
        for provider_name, payload in payloads.items():
            for item in payload.get("obligation_reviews") or []:
                if (
                    isinstance(item, dict)
                    and str(item.get("obligation_id") or "") == obligation_id
                ):
                    rows.append((provider_name, item))
        def exact_accept(provider_name: str, item: dict[str, Any]) -> bool:
            refs = {str(ref) for ref in item.get("evidence_refs") or [] if str(ref)}
            inspected = {
                str(path)
                for path in payloads[provider_name].get("inspected_paths") or []
                if str(path)
            }
            return (
                item.get("verdict") == "accept"
                and str(item.get("target_status") or "") == target_status
                and bool(refs & staged_evidence)
                and bool(refs & staged_evidence & inspected)
            )

        different_accept = any(
            provider_name in different_providers and exact_accept(provider_name, item)
            for provider_name, item in rows
        )
        unanimous_accept = {provider_name for provider_name, _ in rows} == all_reviewers and all(
            exact_accept(provider_name, item) for provider_name, item in rows
        )
        evidence_refs = sorted(
            {
                str(ref)
                for provider_name, item in rows
                for ref in item.get("evidence_refs") or []
                if str(ref)
                and str(ref) in staged_evidence
                and str(ref)
                in {
                    str(path)
                    for path in payloads[provider_name].get("inspected_paths") or []
                    if str(path)
                }
            }
        )
        accepted = different_accept and unanimous_accept and bool(evidence_refs)
        if accepted:
            accepted_obligations.add(obligation_id)
        obligation_reviews.append(
            {
                "obligation_id": obligation_id,
                "target_status": target_status,
                "verdict": "accept" if accepted else "uncertain",
                "evidence_refs": evidence_refs,
                "reason": (
                    "All responding reviewers, including a different family, accepted the exact target using staged evidence."
                    if accepted
                    else "The requested obligation transition lacked unanimous exact-target, staged-evidence, different-family support."
                ),
            }
        )
    missing_obligation_reviews = set(requested_targets) - accepted_obligations
    reviewed_claims = {
        str(item.get("claim_id") or "")
        for payload in payloads.values()
        for item in payload.get("claim_reviews") or []
        if isinstance(item, dict) and str(item.get("claim_id") or "")
    }
    unexpected_claim_reviews = reviewed_claims - requested_claims
    supported_claims: set[str] = set()
    claim_reviews: list[dict[str, Any]] = []
    for claim_id in sorted(requested_claims):
        rows: list[tuple[str, dict[str, Any]]] = []
        for provider_name, payload in payloads.items():
            for item in payload.get("claim_reviews") or []:
                if (
                    isinstance(item, dict)
                    and str(item.get("claim_id") or "") == claim_id
                ):
                    rows.append((provider_name, item))

        def evidence_supported(provider_name: str, item: dict[str, Any]) -> bool:
            refs = {str(ref) for ref in item.get("evidence_refs") or [] if str(ref)}
            inspected = {
                str(path)
                for path in payloads[provider_name].get("inspected_paths") or []
                if str(path)
            }
            return (
                item.get("status") == "supported"
                and bool(refs & staged_evidence & inspected)
            )

        different_support = any(
            provider_name in different_providers
            and evidence_supported(provider_name, item)
            for provider_name, item in rows
        )
        unanimous_support = {provider_name for provider_name, _ in rows} == all_reviewers and all(
            evidence_supported(provider_name, item) for provider_name, item in rows
        )
        evidence_refs = sorted(
            {
                str(ref)
                for provider_name, item in rows
                for ref in item.get("evidence_refs") or []
                if str(ref)
                and str(ref) in staged_evidence
                and str(ref)
                in {
                    str(path)
                    for path in payloads[provider_name].get("inspected_paths") or []
                    if str(path)
                }
            }
        )
        supported = different_support and unanimous_support and bool(evidence_refs)
        if supported:
            supported_claims.add(claim_id)
        claim_reviews.append(
            {
                "claim_id": claim_id,
                "status": "supported" if supported else "disputed",
                "evidence_refs": evidence_refs,
                "reason": (
                    "All responding reviewers, including a different family, supported the claim using staged evidence."
                    if supported
                    else "The claim lacked unanimous staged-evidence, different-family support."
                ),
            }
        )
    missing_claim_reviews = requested_claims - supported_claims
    machine_checks = [
        {**copy.deepcopy(item), "reviewer": provider_name}
        for provider_name, payload in sorted(payloads.items())
        for item in payload.get("machine_checks") or []
        if isinstance(item, dict)
    ]
    conservative = str(synthesis.get("conservative_verdict") or "unavailable")
    common_review = {
        "schema_version": "result_review_summary.v2",
        "candidate_id": expected_id,
        "candidate_fingerprint": expected_fingerprint,
        "different_family": True,
        "executor_provider": executor_provider,
        "executor_family": primary_family,
        "executor_attestation": executor_attestation,
        "providers": sorted(payloads),
        "different_family_providers": different_providers,
        "reviewer_families": reviewer_families,
        "provider_execution_attestations": provider_execution_attestations,
        "conservative_verdict": conservative,
        "structured_synthesis": copy.deepcopy(synthesis),
        "provider_reviews": copy.deepcopy(payloads),
        "claim_reviews": claim_reviews,
        "obligation_reviews": obligation_reviews,
        "machine_checks": machine_checks,
    }
    if conservative == "pass" and (
        not requested_claims
        or missing_claim_reviews
        or unexpected_claim_reviews
        or missing_obligation_reviews
        or reviewer_coverage_errors
    ):
        return {
            "status": "pending",
            "reason": "result review did not exactly cover every proposed claim/obligation",
            "missing_claim_ids": sorted(missing_claim_reviews),
            "unexpected_claim_ids": sorted(unexpected_claim_reviews),
            "missing_obligation_ids": sorted(missing_obligation_reviews),
            "reviewer_coverage_errors": reviewer_coverage_errors,
            "review": common_review,
        }
    if conservative == "pass":
        return {"status": "accepted", "review": {**common_review, "status": "passed"}}
    if conservative in {"fail", "partial"}:
        return {"status": "rejected", "review": {**common_review, "status": "failed"}}
    return {
        "status": "pending",
        "reason": "result review did not reach a bank-or-reject verdict",
        "review": common_review,
    }


def _reviewed_ledger_record(
    pending: dict[str, Any], review: dict[str, Any]
) -> dict[str, Any]:
    """Translate staged self-assessment into evidence-gated finalized fields."""
    record = copy.deepcopy(pending.get("record") or {})
    record["source_candidate_fingerprint"] = goal_focus_v2.candidate_fingerprint(
        pending
    )
    assessment = (
        record.get("progress_assessment")
        if isinstance(record.get("progress_assessment"), dict)
        else {}
    )
    record["campaign_delta"] = str(assessment.get("campaign_delta") or "none")
    requested_global = str(assessment.get("global_delta") or "none")
    requested_targets = goal_focus_v2.proposed_obligation_targets(record)
    checked = record.get("evidence_checked")
    staged_evidence = {
        str(item)
        for item in (checked.get("evidence_ids") if isinstance(checked, dict) else []) or []
        if str(item)
    }
    transitions: list[dict[str, str]] = []
    review_evidence: set[str] = set()
    for obligation in review.get("obligation_reviews") or []:
        if not isinstance(obligation, dict) or obligation.get("verdict") != "accept":
            continue
        oid = str(obligation.get("obligation_id") or "")
        target = str(obligation.get("target_status") or "")
        refs = {str(item) for item in obligation.get("evidence_refs") or [] if str(item)}
        if requested_targets.get(oid) == target and refs & staged_evidence:
            transitions.append({"obligation_id": oid, "to": target})
            review_evidence.update(refs & staged_evidence)
    evidence = sorted(staged_evidence | review_evidence)
    record["evidence_ids"] = evidence
    record["obligation_transitions"] = transitions
    record["reported_global_delta"] = requested_global
    return record


DRIVE_EXIT_CODES = {
    "max_failures": 3,
    "runtime_error": 4,
    "quota_wait_exhausted": 5,
    "provider_unavailable": 6,
    "auth_or_session_dead": 7,
    "resource_cleanup_unverified": 8,
    "candidate_quarantined": 9,
    "quarantine_persistence_unverified": 10,
    "review_wait_exhausted": 16,
    "panel_roster_withdrawn": 17,
    "bad_arguments": 2,
}

BUILD_CONFIG_FILENAMES = (
    "lakefile.lean",
    "lakefile.toml",
    "lake-manifest.json",
    "lean-toolchain",
)


class LedgerIntegrityWatch:
    """Host-memory watch over the append-only iteration-ledger prefix.

    The observed prefix hash lives only in driver memory: the loop tree
    (including driver_logs) is agent-writable, so nothing an iteration agent
    writes can alter what this watch has already seen. The watch hashes the
    logical ledger stream — rotated shards in record order plus the live file
    (``iteration_ledger_paths``) — so the host's own shard rotation, which
    moves live bytes across a shard boundary without changing the stream, is
    not a violation. A violation means bytes the host previously observed
    changed or disappeared — a truncate, rewrite, or reset — never merely
    that new records were appended or rotated.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.seen_bytes = 0
        self.prefix_sha256 = hashlib.sha256(b"").hexdigest()
        self.violations: list[dict[str, Any]] = []
        self._advance(self._read())

    def _read(self) -> bytes:
        chunks: list[bytes] = []
        for shard in iteration_ledger_paths(self.path.parent):
            try:
                if shard.exists():
                    chunks.append(shard.read_bytes())
            except OSError:
                continue
        return b"".join(chunks)

    def _advance(self, data: bytes) -> None:
        self.seen_bytes = len(data)
        self.prefix_sha256 = hashlib.sha256(data).hexdigest()

    def check(self) -> dict[str, Any] | None:
        """Record and return a violation if the seen prefix changed, else None."""
        data = self._read()
        if len(data) < self.seen_bytes:
            kind = "truncated"
        elif hashlib.sha256(data[: self.seen_bytes]).hexdigest() != self.prefix_sha256:
            kind = "rewritten"
        else:
            self._advance(data)
            return None
        violation = {
            "kind": kind,
            "observed_at": utc_now(),
            "expected_prefix_bytes": self.seen_bytes,
            "expected_prefix_sha256": self.prefix_sha256,
            "found_bytes": len(data),
            "found_prefix_sha256": hashlib.sha256(
                data[: min(len(data), self.seen_bytes)]
            ).hexdigest(),
        }
        self.violations.append(violation)
        # Re-baseline so one rewrite is one violation, not one per later cycle.
        self._advance(data)
        return violation


class BuildConfigWatch:
    """Hash-snapshot watch over host-owned Lean build configuration files."""

    def __init__(self, dirs: list[Path]) -> None:
        seen: set[str] = set()
        self.dirs: list[Path] = []
        for candidate in dirs:
            try:
                key = str(Path(candidate).resolve())
            except OSError:
                continue
            if key not in seen:
                seen.add(key)
                self.dirs.append(Path(key))
        self.baseline = self._snapshot()
        self.changes: list[dict[str, Any]] = []

    def _snapshot(self) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for directory in self.dirs:
            for name in BUILD_CONFIG_FILENAMES:
                path = directory / name
                try:
                    out[str(path)] = (
                        hashlib.sha256(path.read_bytes()).hexdigest()
                        if path.is_file()
                        else None
                    )
                except OSError:
                    out[str(path)] = None
        return out

    def check(self) -> list[dict[str, Any]]:
        """Record and return the files whose content changed since last check."""
        current = self._snapshot()
        fresh: list[dict[str, Any]] = []
        for key, prior in self.baseline.items():
            now = current.get(key)
            if now == prior:
                continue
            fresh.append(
                {
                    "path": key,
                    "change": (
                        "added" if prior is None else "removed" if now is None else "modified"
                    ),
                    "prior_sha256": prior,
                    "current_sha256": now,
                    "observed_at": utc_now(),
                }
            )
        if fresh:
            self.changes.extend(fresh)
            # Re-baseline so one edit is one report, not one per later cycle.
            self.baseline = current
        return fresh


def _write_integrity_snapshot(
    log_dir: Path, tag: str, payload: dict[str, Any], ledger_bytes: bytes
) -> str:
    """Exclusive-create snapshot of a violation plus the ledger bytes seen.

    Uses the same O_EXCL|O_NOFOLLOW discipline as the per-iteration driver logs
    so an agent-planted file or symlink at the target name fails the write
    instead of redirecting it.
    """
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    nonce = uuid.uuid4().hex[:12]
    path = log_dir / f"integrity_{tag}_{stamp}_{nonce}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(
            {
                **payload,
                "ledger_snapshot_utf8": ledger_bytes.decode("utf-8", errors="replace"),
            },
            handle,
            indent=2,
            sort_keys=True,
        )
    return str(path)


def _ledger_consistency_errors(run_dir: Path) -> list[str]:
    """Cheap post-iteration cross-file coherence check (IG-3).

    Reuses the validate invariants that tie budget.json and loop_state.json to
    the iterations.jsonl record count. This is a complement to
    LedgerIntegrityWatch, not a substitute: an agent that rewrites all three
    files consistently passes here but still trips the in-memory prefix watch.
    """
    try:
        paths = loop_paths(run_dir)
        iterations = (
            read_iterations(paths["iterations"]) if paths["iterations"].exists() else []
        )
        budget = read_json(paths["budget"]) if paths["budget"].exists() else {}
        state = read_json(paths["state"]) if paths["state"].exists() else {}
    except Exception as exc:  # noqa: BLE001 - unreadable ledger is itself a finding.
        return [f"ledger unreadable during consistency check: {exc}"]
    errors: list[str] = []
    spent = budget.get("spent_iterations")
    if isinstance(spent, int) and spent != len(iterations):
        errors.append(
            f"budget.json spent_iterations ({spent}) does not equal the "
            f"iterations.jsonl record count ({len(iterations)})"
        )
    if iterations:
        last_number = iterations[-1].get("iteration")
        recorded_last = state.get("last_iteration")
        if (
            isinstance(last_number, int)
            and isinstance(recorded_last, int)
            and recorded_last != last_number
        ):
            errors.append(
                f"loop_state.json last_iteration ({recorded_last}) does not match "
                f"the newest ledger record ({last_number})"
            )
    return errors


def _non_linux_enforce_note() -> str:
    """Name the platform gap behind a Goal-Focus enforce refusal, if that is it."""

    if sys.platform.startswith("linux"):
        return ""
    return (
        ". This host is not Linux, and Goal-Focus enforce requires the "
        "trusted-local transport whose resource preflight is Linux-only, so "
        "enforce cannot drive here at all: use --goal-focus-mode monitor, or "
        "drive from WSL or Linux"
    )


def drive_command(args: argparse.Namespace) -> dict[str, Any]:
    """Cross-platform headless driver: run one iteration command per loop until the
    runtime reports the loop is done (loops reached, credit/budget exhausted, goal
    resolved, or user stop), or until the iteration command fails too many times in
    a row, or the runtime state cannot be read. The driver is the sole enforcer in
    headless mode: it exports AUTOLOOP_DRIVER=1 so the interactive Stop hook stands
    down, and it derives "done" only from the runtime, never from the agent's own
    say-so. On any inability to determine state it fails safe (stops). This is the
    platform-neutral replacement for the bash driver; the .sh shim delegates here."""
    run_dir = Path(args.dir).expanduser().resolve()
    root = Path(args.root).expanduser().resolve() if args.root else run_dir
    if migration_claim_active(run_dir):
        return {
            "status": "failed",
            "action": "drive",
            "dir": str(run_dir),
            "reason": "migration_in_progress",
            "error": "Goal-Focus migration owns this loop; retry after it finishes",
            "exit_code": DRIVE_EXIT_CODES["runtime_error"],
        }
    try:
        if (run_dir / ".goal_focus_transactions").exists():
            goal_focus_v2.recover_transactions(run_dir)
    except TransactionQuarantined as exc:
        # The entry has been moved aside, so this failure is loud once rather
        # than permanent: the operator inspects the manifest, then re-runs.
        return {
            "status": "failed",
            "action": "drive",
            "dir": str(run_dir),
            "reason": "runtime_error",
            "error": (
                f"Goal-Focus transaction journal quarantined: {exc}; inspect the "
                "quarantined manifest.json against the live targets, then re-run "
                "start"
            ),
            "exit_code": DRIVE_EXIT_CODES["runtime_error"],
        }
    except Exception as exc:  # noqa: BLE001 - never mutate around a torn authority
        return {
            "status": "failed",
            "action": "drive",
            "dir": str(run_dir),
            "reason": "runtime_error",
            "error": f"Goal-Focus transaction recovery failed: {exc}",
            "exit_code": DRIVE_EXIT_CODES["runtime_error"],
        }
    iter_timeout = args.iteration_timeout if args.iteration_timeout and args.iteration_timeout > 0 else None
    max_failures = max(1, int(args.max_failures))
    poll = max(0.0, float(args.poll))
    provider = getattr(args, "provider", None)
    cmd = getattr(args, "cmd", None)
    if bool(provider) == bool(cmd):
        return {
            "status": "failed",
            "action": "drive",
            "dir": str(run_dir),
            "reason": "bad_arguments",
            "error": "exactly one of --cmd or --provider is required",
            "exit_code": DRIVE_EXIT_CODES["bad_arguments"],
        }
    try:
        initial_goal_focus_mode = (
            goal_focus_runtime_mode(run_dir)
            if goal_focus_state_present(run_dir)
            else "off"
        )
    except Exception as exc:
        return {
            "status": "failed",
            "action": "drive",
            "dir": str(run_dir),
            "reason": "runtime_error",
            "error": f"cannot determine Goal-Focus mode: {exc}",
            "exit_code": DRIVE_EXIT_CODES["runtime_error"],
        }
    provider_transport = provider_transport_mode()
    trusted_local_resource_profile: dict[str, int] = {}
    if initial_goal_focus_mode == "enforce":
        if provider_transport != TRUSTED_LOCAL_TRANSPORT:
            return {
                "status": "failed",
                "action": "drive",
                "dir": str(run_dir),
                "reason": "secure_primary_transport_unavailable",
                "error": (
                    "Goal-Focus enforce execution is blocked before registration, "
                    "panel review, dispatch, or worker spawn because no credential-blind, "
                    "prompt-private, allowlist-filesystem, resource-bounded model "
                    "transport with constrained egress is available"
                    + _non_linux_enforce_note()
                ),
                "exit_code": DRIVE_EXIT_CODES["runtime_error"],
            }
        try:
            run_dir.relative_to(root)
        except ValueError:
            trusted_root_error = "the loop directory is outside the trusted project root"
        else:
            trusted_root_error = None
        if (
            trusted_root_error is not None
            or root == Path("/")
            or not root.is_dir()
            or cmd is not None
            or provider not in TRUSTED_LOCAL_ENFORCE_PRIMARY_PROVIDERS
            or iter_timeout is None
        ):
            return {
                "status": "failed",
                "action": "drive",
                "dir": str(run_dir),
                "reason": "bad_arguments",
                "error": trusted_root_error
                or (
                    "trusted-local enforce execution requires a real scoped project "
                    "root, a positive iteration timeout, and an attested Claude, "
                    "Codex, or Grok provider"
                ),
                "exit_code": DRIVE_EXIT_CODES["bad_arguments"],
            }
    if provider_transport == TRUSTED_LOCAL_TRANSPORT:
        if iter_timeout is None:
            return {
                "status": "failed",
                "action": "drive",
                "dir": str(run_dir),
                "reason": "bad_arguments",
                "error": "trusted-local execution requires a positive iteration timeout",
                "exit_code": DRIVE_EXIT_CODES["bad_arguments"],
            }
        try:
            trusted_local_resource_profile = preflight_resource_backend(
                iter_timeout, role="primary"
            )
        except ProviderResourceCleanupError as exc:
            return {
                "status": "failed",
                "action": "drive",
                "dir": str(run_dir),
                "reason": "resource_cleanup_unverified",
                "error": str(exc),
                "exit_code": DRIVE_EXIT_CODES["resource_cleanup_unverified"],
            }
        except ProviderResourceError as exc:
            return {
                "status": "failed",
                "action": "drive",
                "dir": str(run_dir),
                "reason": "resource_limits_unavailable",
                "error": str(exc)
                + (
                    _non_linux_enforce_note()
                    if initial_goal_focus_mode == "enforce"
                    else ""
                ),
                "exit_code": DRIVE_EXIT_CODES["runtime_error"],
            }
    quota_backoff = max(0, int(getattr(args, "quota_backoff", 900)))
    max_quota_waits = max(0, int(getattr(args, "max_quota_waits", 0)))
    max_review_waits = max(0, int(getattr(args, "max_review_waits", 0)))
    log_dir = (
        Path(args.log_dir).expanduser()
        if getattr(args, "log_dir", None)
        else run_dir / "driver_logs"
    )
    log_dir = _ensure_real_directory(log_dir)
    notify_cmd = getattr(args, "notify_cmd", None)
    reg = registry_dir(args)
    # Default: auto (secrets-backed). Explicit --notify off disables. Env and
    # prior arm/loop_state preferences take precedence via resolve_notify_channel.
    notify_channel = resolve_notify_channel(
        explicit=getattr(args, "notify", None),
        run_dir=run_dir,
        registry=reg,
        default_auto=True,
    ) or "off"
    progress_enabled = not bool(getattr(args, "no_progress", False))

    def _progress(event: str, **extra: Any) -> None:
        if not progress_enabled:
            return
        try:
            emit_loop_progress(
                run_dir,
                event,
                log_dir=log_dir,
                notify_cmd=notify_cmd,
                notify_channel=notify_channel,
                to_stderr=True,
                to_stdout_json=False,
                extra=extra or None,
            )
        except Exception as exc:  # noqa: BLE001 - progress must not kill drive
            sys.stderr.write(f"autoloop-driver: progress emit failed: {exc}\n")

    def _cancel_prepared_dispatch(intent: dict[str, Any], why: str) -> bool:
        dispatch_id = str(intent.get("dispatch_id") or "")
        if not dispatch_id:
            return True
        try:
            goal_focus_v2.cancel_iteration_dispatch(
                run_dir,
                dispatch_id=dispatch_id,
                reason=why,
            )
            return True
        except Exception as exc:  # noqa: BLE001 - caller must fail closed
            sys.stderr.write(
                f"autoloop-driver: could not cancel dispatch {dispatch_id}: {exc}\n"
            )
            return False

    # Registration is a safety boundary for migration quiescence as well as the
    # interactive Stop hook.  A driver must not execute unregistered.
    run_id: object = None
    arm_ns = argparse.Namespace(
        dir=str(run_dir),
        root=str(root),
        force=False,
        pid=os.getpid(),
        driver=True,
        notify=getattr(args, "notify", None) or ("off" if notify_channel is None else notify_channel),
        registry_dir=getattr(args, "registry_dir", None),
    )
    try:
        arm_result = arm_loop(arm_ns)
        run_id = arm_result.get("run_id")
        # Prefer resolved channel from arm when drive used auto.
        if arm_result.get("notify_resolved") and getattr(args, "notify", None) in (None, "auto"):
            notify_channel = arm_result.get("notify_resolved")
    except MigrationClaimError as exc:
        return {
            "status": "failed",
            "action": "drive",
            "dir": str(run_dir),
            "reason": "migration_in_progress",
            "error": str(exc),
            "exit_code": DRIVE_EXIT_CODES["runtime_error"],
        }
    except Exception as exc:  # noqa: BLE001 - fail closed before agent execution.
        return {
            "status": "failed",
            "action": "drive",
            "dir": str(run_dir),
            "reason": "driver_registration_failed",
            "error": str(exc),
            "exit_code": DRIVE_EXIT_CODES["runtime_error"],
        }
    failures = 0
    quota_waits = 0
    review_waits = 0
    quota_waits_total = 0
    iterations_run = 0
    reason = "unknown"
    was_paused = False
    panel_mode = getattr(args, "panel", None) or "auto"
    panel_enabled = resolve_panel_mode(panel_mode, run_dir)
    formal_pol, formal_pin = _apply_formal_drive_start(run_dir, args)
    # Host-memory integrity watches (IG-1/IG-2): baselines are taken before the
    # first iteration agent runs and never live in agent-writable files.
    ledger_watch = LedgerIntegrityWatch(loop_paths(run_dir)["iterations"])
    ledger_desync_events: list[dict[str, Any]] = []
    integrity_snapshots: list[str] = []
    build_config_lock = bool(getattr(args, "build_config_lock", False))
    build_config_watch: BuildConfigWatch | None = None
    if formal_pol is not None and formal_pol.policy in {"on", "force"}:
        watch_dirs: list[Path] = []
        try:
            project_dir = resolve_formal_project(
                run_dir, formal_pol.project, root=root
            )
            if project_dir is not None:
                watch_dirs.append(project_dir)
        except Exception:  # noqa: BLE001 - watch setup must never block the drive.
            pass
        watch_dirs.append(root)
        try:
            build_config_watch = BuildConfigWatch(watch_dirs)
        except Exception:  # noqa: BLE001
            build_config_watch = None
    prior_primary_provider = os.environ.get("AAS_AUTOLOOP_PRIMARY_PROVIDER")
    os.environ["AAS_AUTOLOOP_PRIMARY_PROVIDER"] = str(provider or "custom")
    _progress(
        "drive_start",
        source="drive",
        provider=provider or "",
        drive_pid=os.getpid(),
        panel=panel_mode,
        panel_enabled=panel_enabled,
        provider_transport=provider_transport,
        resource_limits=public_resource_limits(trusted_local_resource_profile),
        formal_policy=(formal_pol.policy if formal_pol is not None else "off"),
    )
    try:
        while True:
            goal_focus_present = goal_focus_state_present(run_dir)
            goal_focus_mode = "off"
            goal_focus_gate: dict[str, Any] | None = None
            try:
                # A prepared Goal-Focus transaction may already contain the
                # terminal budget post-image. Finish it before compute_done can
                # mistake a torn commit for a completed loop.
                if (run_dir / ".goal_focus_transactions").exists():
                    goal_focus_v2.recover_transactions(run_dir)
                    goal_focus_present = goal_focus_state_present(run_dir)
                if goal_focus_present:
                    goal_focus_mode = goal_focus_runtime_mode(run_dir)
                    if goal_focus_mode == "off":
                        validation = goal_focus_v2.validate_goal_focus(run_dir)
                        if validation.get("errors"):
                            sys.stderr.write(
                                "autoloop-driver: Goal-Focus off-mode finding: "
                                + "; ".join(validation.get("errors") or [])
                                + "\n"
                            )
                    elif goal_focus_mode == "monitor":
                        # Monitor is observational: no reconciliation writes and
                        # no automatic transaction mutation after the recovery
                        # of an already-prepared atomic commit above.
                        try:
                            goal_focus_gate = goal_focus_v2.pre_dispatch_gate(
                                run_dir,
                                auto_recover=False,
                                regenerate_views=False,
                            )
                        except Exception as monitor_exc:  # noqa: BLE001 - report only
                            goal_focus_gate = {
                                "ok": True,
                                "action": "monitor_invalid_authority",
                                "authority_errors": [str(monitor_exc)],
                                "errors": [str(monitor_exc)],
                                "triggers": [],
                            }
                    else:
                        goal_focus_gate = goal_focus_v2.pre_dispatch_gate(run_dir)
            except Exception as exc:  # noqa: BLE001 - unreadable mode/authority fails closed
                _progress(
                    "goal_focus_wait",
                    source="drive",
                    event_id="goal-focus-invalid-authority",
                    iteration_status="waiting",
                    review_status="pending",
                    completed_summary="No new result was banked; Goal-Focus authority is invalid.",
                    current_summary=f"Dispatch is blocked by invalid Goal-Focus state: {str(exc)[:400]}",
                    next_action="Repair or reconcile the authoritative Goal-Focus files, then validate again.",
                    error=str(exc)[:400],
                )
                interruptible_sleep(max(poll, 30.0), run_dir)
                continue

            identity_overrides = (
                provider_identity_overrides(str(provider)) if provider else []
            )
            driver_execution_attestation: dict[str, Any] | None = None
            driver_identity_error: str | None = None
            if goal_focus_mode == "enforce" and provider and not identity_overrides:
                try:
                    driver_execution_attestation = attest_provider_executable(
                        str(provider),
                        forbidden_roots=(root, run_dir),
                        required=True,
                    )
                except PanelIsolationError as exc:
                    driver_identity_error = str(exc)
            if goal_focus_mode == "enforce" and (
                not provider
                or driver_execution_attestation is None
                or str(driver_execution_attestation.get("family") or "unverified")
                == "unverified"
                or bool(identity_overrides)
            ):
                reason = "bad_arguments"
                sys.stderr.write(
                    "autoloop-driver: Goal-Focus enforce mode requires --provider "
                    "with a host-pinned model family; custom commands, provider command/"
                    "argument/binary overrides, and unverified gateways cannot establish "
                    "review independence"
                    + (
                        f" (identity overrides: {', '.join(identity_overrides)})"
                        if identity_overrides
                        else f" ({driver_identity_error})"
                        if driver_identity_error
                        else ""
                    )
                    + "\n"
                )
                break

            try:
                verdict = compute_done(run_dir)
            except Exception:  # noqa: BLE001 - unreadable state -> fail safe (stop).
                reason = "runtime_error"
                break
            if verdict.get("done"):
                reason = "done"
                break
            if verdict.get("paused"):
                if not was_paused:
                    _progress("paused", source="drive")
                    was_paused = True
                time.sleep(poll)
                continue
            was_paused = False
            # Re-resolve each cycle so loop_state/panel.json can opt in mid-run.
            panel_enabled = resolve_panel_mode(panel_mode, run_dir)
            refresh_heartbeat(reg, run_id)

            if goal_focus_gate is not None and goal_focus_mode == "monitor":
                # Monitor reports but never mutates strategy or blocks dispatch.
                if goal_focus_gate.get("action") != "dispatch":
                    sys.stderr.write(
                        "autoloop-driver: Goal-Focus monitor finding: "
                        + json.dumps(
                            {
                                "action": goal_focus_gate.get("action"),
                                "errors": goal_focus_gate.get("errors") or [],
                                "triggers": goal_focus_gate.get("triggers") or [],
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )

            if goal_focus_gate is not None and goal_focus_mode == "enforce":
                gate_action = str(goal_focus_gate.get("action") or "reconcile")
                gate_plan = (
                    goal_focus_gate.get("plan")
                    if isinstance(goal_focus_gate.get("plan"), dict)
                    else {}
                )
                plan_revision = int(gate_plan.get("plan_revision") or 0)
                trigger_codes = [
                    str(item.get("code"))
                    for item in goal_focus_gate.get("triggers") or []
                    if isinstance(item, dict) and item.get("code")
                ]
                current_driver_family = (
                    str(
                        driver_execution_attestation.get("family")
                        or "unverified"
                    )
                    if driver_execution_attestation is not None
                    else "unverified"
                )
                reviewed_driver_family = str(
                    gate_plan.get("dispatch_provider_family") or ""
                ).strip()
                if (
                    gate_action == "dispatch"
                    and (
                        current_driver_family == "unverified"
                        or reviewed_driver_family != current_driver_family
                    )
                ):
                    gate_action = "replan"
                    trigger_codes.append("driver_family_changed")
                if gate_action == "dispatch_inflight":
                    inflight = goal_focus_v2.load_iteration_dispatch(run_dir) or {}
                    inflight_id = str(inflight.get("dispatch_id") or "")
                    reclaim = try_cancel_orphaned_host_dispatch(
                        run_dir,
                        reason="owner_driver_pid_absent",
                    )
                    if reclaim.get("applied"):
                        _progress(
                            "goal_focus_recover",
                            source="drive",
                            event_id=(
                                f"goal-focus-dispatch-orphan-cancel-"
                                f"{reclaim.get('dispatch_id') or inflight_id}"
                            ),
                            iteration_status="waiting",
                            review_status="pending",
                            completed_summary=(
                                "Cancelled an orphaned host dispatch whose owner driver "
                                "process is gone and left no staged candidate."
                            ),
                            current_summary=(
                                f"Dispatch {reclaim.get('dispatch_id') or inflight_id} "
                                f"was reclaimed (driver_pid "
                                f"{reclaim.get('driver_pid')!r} not alive)."
                            ),
                            next_action=(
                                "Prepare a fresh host-pinned dispatch for the active plan."
                            ),
                        )
                        continue
                    _progress(
                        "goal_focus_wait",
                        source="drive",
                        event_id=f"goal-focus-dispatch-inflight-{inflight_id}",
                        iteration_status="waiting",
                        review_status="pending",
                        completed_summary="No new result was banked; a host-pinned worker dispatch remains in flight.",
                        current_summary=(
                            f"Dispatch {inflight_id} is awaiting its exact staged candidate."
                        ),
                        next_action=(
                            "Wait for the original worker. If it is confirmed dead, inspect "
                            "goal-focus recover-dispatch and cancel only with the exact dispatch id."
                        ),
                    )
                    interruptible_sleep(max(poll, 30.0), run_dir)
                    continue
                if gate_action == "candidate_quarantined":
                    quarantine = goal_focus_v2.load_candidate_quarantine(run_dir) or {}
                    _progress(
                        "goal_focus_wait",
                        source="drive",
                        event_id=f"goal-focus-candidate-quarantine-{quarantine.get('quarantine_id')}",
                        iteration_status="error",
                        review_status="error",
                        completed_summary="No result was banked; a failed provider completion was quarantined.",
                        current_summary=(
                            "Automatic review and dispatch are stopped for candidate "
                            f"{quarantine.get('candidate_id') or 'unknown'}."
                        ),
                        next_action=(
                            (
                                "Confirm every provider descendant is gone. "
                                if "cleanup" in str(quarantine.get("reason") or "").lower()
                                else ""
                            )
                            + "Inspect the quarantine, then release its exact candidate "
                            "fingerprint with goal-focus recover-quarantine."
                        ),
                    )
                    reason = "candidate_quarantined"
                    break
                if gate_action == "review_pending":
                    pending = goal_focus_v2.load_pending_candidate(run_dir)
                    if not pending:
                        _progress(
                            "goal_focus_wait",
                            source="drive",
                            event_id=f"goal-focus-missing-pending-r{plan_revision}",
                            iteration_status="waiting",
                            review_status="pending",
                            completed_summary="No new result was banked.",
                            current_summary="The plan reports a pending result, but no candidate can be loaded.",
                            next_action="Recover transactions and reconcile Goal-Focus state.",
                        )
                        interruptible_sleep(max(poll, 30.0), run_dir)
                        continue
                    pending_record = (
                        pending.get("record") if isinstance(pending.get("record"), dict) else {}
                    )
                    try:
                        goal_focus_v2.validate_host_staged_candidate(run_dir, pending)
                    except Exception as exc:  # noqa: BLE001 - invalid authority never banks
                        _progress(
                            "result_review_wait",
                            source="drive",
                            event_id=f"result-review-invalid-candidate-{pending.get('candidate_id')}",
                            candidate_id=str(pending.get("candidate_id") or ""),
                            iteration_status="waiting",
                            review_status="pending",
                            completed_summary="No new result has been banked; the staged candidate violates host authority.",
                            current_summary=str(exc)[:400],
                            next_action="Repair or explicitly reject the exact candidate before review.",
                        )
                        review_waits += 1
                        if max_review_waits and review_waits >= max_review_waits:
                            reason = "review_wait_exhausted"
                            _progress(
                                "result_review_error",
                                source="drive",
                                iteration_status="error",
                                review_status="error",
                                completed_summary=(
                                    "Review wait budget exhausted; the staged candidate is "
                                    "preserved and the drive exits resumably."
                                ),
                                current_summary=(
                                    f"{review_waits} consecutive review waits without a "
                                    "bank-or-reject verdict."
                                ),
                                next_action=(
                                    "Repair the candidate or reviewer, then restart the drive."
                                ),
                            )
                            break
                        interruptible_sleep(max(poll, 30.0), run_dir)
                        continue
                    pending_iteration = int(
                        pending_record.get("iteration")
                        or (read_json(loop_paths(run_dir)["budget"]).get("spent_iterations", 0) + 1)
                    )
                    review_dir = ensure_iter_dir(run_dir, iteration=pending_iteration)
                    _progress(
                        "result_review_start",
                        source="drive",
                        event_id=f"result-review-start-{pending.get('candidate_id')}",
                        candidate_id=str(pending.get("candidate_id") or ""),
                        iteration_status="running",
                        review_status="pending",
                        iter_dir=str(review_dir),
                    )
                    try:
                        review_summary = run_panel_phase_for_drive(
                            run_dir,
                            root,
                            "result_review",
                            iter_dir=review_dir,
                        )
                        if review_summary.get("fatal_resource_cleanup_failure"):
                            _progress(
                                "result_review_error",
                                source="drive",
                                iteration_status="error",
                                review_status="error",
                                completed_summary="No result was banked because panel resource cleanup was not verified.",
                                current_summary="A panel process scope may still be live; automatic retry is stopped.",
                                next_action="Inspect and terminate the recorded panel scope before resuming.",
                            )
                            reason = "resource_cleanup_unverified"
                            break
                        if review_summary.get("panel_roster_withdrawn"):
                            _progress(
                                "result_review_error",
                                source="drive",
                                candidate_id=str(pending.get("candidate_id") or ""),
                                iteration_status="error",
                                review_status="error",
                                completed_summary="No result was banked because every panel reviewer is withdrawn.",
                                current_summary=(
                                    "All configured reviewers are excluded, so the independent "
                                    "result review cannot run: "
                                    + (
                                        ", ".join(
                                            review_summary.get("excluded_providers") or []
                                        )
                                        or "no reviewers remain"
                                    )
                                ),
                                next_action="Restore credit for at least one reviewer or clear exclude_until_credit, then resume.",
                            )
                            reason = "panel_roster_withdrawn"
                            break
                        review_outcome = _result_review_from_panel(pending, review_summary)
                    except Exception as exc:  # noqa: BLE001 - pending must survive
                        review_outcome = {
                            "status": "pending",
                            "reason": f"result review error: {exc}",
                        }
                    if review_outcome.get("status") in {"accepted", "rejected"}:
                        accepted = review_outcome["status"] == "accepted"
                        review = review_outcome.get("review") or {}
                        ledger_record = _reviewed_ledger_record(pending, review)
                        try:
                            finalized = goal_focus_v2.finalize_candidate(
                                run_dir,
                                accepted=accepted,
                                review=review,
                                ledger_record=ledger_record,
                                expected_plan_revision=int(pending.get("plan_revision") or 0),
                                # In-memory pin, not the persisted one: the
                                # re-check must resolve the project the drive
                                # pinned, from a copy the agent cannot rewrite.
                                formal_pin=formal_pin,
                            )
                        except Exception as exc:  # noqa: BLE001 - preserve pending on failed commit
                            _progress(
                                "result_review_error",
                                source="drive",
                                event_id=f"result-review-finalize-error-{pending.get('candidate_id')}",
                                candidate_id=str(pending.get("candidate_id") or ""),
                                iteration_status="error",
                                review_status="error",
                                completed_summary="No new result was banked because finalization failed.",
                                current_summary=f"The reviewed candidate remains pending: {str(exc)[:400]}",
                                next_action="Recover the transaction and retry exact-candidate finalization.",
                                error=str(exc)[:400],
                            )
                            interruptible_sleep(max(poll, 30.0), run_dir)
                            continue
                        review_waits = 0
                        final_record = finalized.get("record") or {}
                        final_plan = finalized.get("plan") or {}
                        review_families = review.get("reviewer_families") or []
                        review_agents = review.get("providers") or []
                        if accepted:
                            _progress(
                                "iteration_ok",
                                source="drive",
                                event_id=f"arl-iteration_ok-{pending.get('candidate_id')}",
                                candidate_id=str(pending.get("candidate_id") or ""),
                                iteration_status="success",
                                review_status="passed",
                                reviewer_families=review_families,
                                panel_agents=review_agents,
                                completed_summary=str(
                                    research_result_text(final_record)
                                    or "The reviewed result was banked."
                                ),
                                current_summary=research_position_text(
                                    final_record,
                                    str(
                                        final_record.get("current_summary")
                                        or "The accepted result is now part of the authoritative ledger."
                                    ),
                                ),
                                next_action=str(
                                    final_record.get("proposed_next_action")
                                    or final_plan.get("next_action")
                                    or "Run the pre-dispatch gate for the next bounded action."
                                ),
                                provider=str(
                                    (final_record.get("execution") or {}).get("executor_provider")
                                    if isinstance(final_record.get("execution"), dict)
                                    else provider or ""
                                ),
                            )
                        else:
                            _progress(
                                "iteration_rejected",
                                source="drive",
                                event_id=f"arl-iteration_rejected-{pending.get('candidate_id')}",
                                candidate_id=str(pending.get("candidate_id") or ""),
                                iteration_status="failure",
                                review_status="failed",
                                reviewer_families=review_families,
                                panel_agents=review_agents,
                                completed_summary="No research claim was banked; the staged candidate failed independent review.",
                                current_summary="The failed attempt consumed its recorded budget and the active plan now requires replanning.",
                                next_action="Run a fresh structured strategy review before dispatching more research.",
                                review_summary=str(review.get("conservative_verdict") or "failed"),
                                provider=str(
                                    (final_record.get("execution") or {}).get("executor_provider")
                                    if isinstance(final_record.get("execution"), dict)
                                    else provider or ""
                                ),
                            )
                        continue
                    _progress(
                        "result_review_wait",
                        source="drive",
                        event_id=f"result-review-wait-{pending.get('candidate_id')}",
                        candidate_id=str(pending.get("candidate_id") or ""),
                        iteration_status="waiting",
                        review_status="pending",
                        completed_summary=(
                            "Staged, not yet banked (awaiting independent review): "
                            + (
                                research_result_text(pending_record)
                                or "the exact staged candidate."
                            )
                        ),
                        current_summary=str(
                            review_outcome.get("reason")
                            or "Independent result review is not yet sufficient."
                        ),
                        next_action="Obtain a valid different-family review of the exact pending candidate.",
                    )
                    review_waits += 1
                    if max_review_waits and review_waits >= max_review_waits:
                        reason = "review_wait_exhausted"
                        _progress(
                            "result_review_error",
                            source="drive",
                            iteration_status="error",
                            review_status="error",
                            completed_summary=(
                                "Review wait budget exhausted; the staged candidate is "
                                "preserved and the drive exits resumably."
                            ),
                            current_summary=(
                                f"{review_waits} consecutive review waits without a "
                                "bank-or-reject verdict."
                            ),
                            next_action=(
                                "Repair the result-review panel, then restart the drive."
                            ),
                        )
                        break
                    interruptible_sleep(max(poll, 30.0), run_dir)
                    continue

                if gate_action == "replan":
                    strategy_dir = ensure_iter_dir(run_dir)
                    _progress(
                        "strategy_review_start",
                        source="drive",
                        event_id=f"strategy-review-start-r{plan_revision}",
                        iteration_status="running",
                        review_status="pending",
                        iter_dir=str(strategy_dir),
                    )
                    try:
                        strategy_summary = run_panel_phase_for_drive(
                            run_dir,
                            root,
                            "strategy_review",
                            iter_dir=strategy_dir,
                        )
                        if strategy_summary.get("fatal_resource_cleanup_failure"):
                            _progress(
                                "strategy_review_error",
                                source="drive",
                                iteration_status="error",
                                review_status="error",
                                completed_summary="No direction was committed because panel resource cleanup was not verified.",
                                current_summary="A panel process scope may still be live; automatic retry is stopped.",
                                next_action="Inspect and terminate the recorded panel scope before resuming.",
                            )
                            reason = "resource_cleanup_unverified"
                            break
                        if strategy_summary.get("panel_roster_withdrawn"):
                            _progress(
                                "strategy_review_error",
                                source="drive",
                                iteration_status="error",
                                review_status="error",
                                completed_summary="No direction was committed because every panel reviewer is withdrawn.",
                                current_summary=(
                                    "All configured reviewers are excluded, so the strategy "
                                    "review cannot run: "
                                    + (
                                        ", ".join(
                                            strategy_summary.get("excluded_providers") or []
                                        )
                                        or "no reviewers remain"
                                    )
                                ),
                                next_action="Restore credit for at least one reviewer or clear exclude_until_credit, then resume.",
                            )
                            reason = "panel_roster_withdrawn"
                            break
                        strategy = _strategy_selection_from_panel(run_dir, strategy_summary)
                    except Exception as exc:  # noqa: BLE001 - no unreviewed dispatch
                        strategy = {"status": "waiting", "reason": str(exc)}
                    if strategy.get("status") == "ready":
                        try:
                            committed = goal_focus_v2.commit_selected_direction(
                                run_dir,
                                strategy["selection"],
                                strategy["review"],
                                ",".join(trigger_codes) or "pre_dispatch",
                                expected_plan_revision=plan_revision,
                            )
                        except Exception as exc:  # noqa: BLE001 - stale strategy must not stand
                            strategy = {
                                "status": "waiting",
                                "reason": f"direction commit failed: {exc}",
                            }
                        else:
                            committed_plan = committed.get("plan") or {}
                            _progress(
                                "goal_focus_replan",
                                source="drive",
                                event_id=f"goal-focus-replan-r{committed_plan.get('plan_revision')}",
                                iteration_status="not_applicable",
                                review_status="passed",
                                reviewer_families=(strategy.get("review") or {}).get(
                                    "reviewer_families"
                                )
                                or [],
                                panel_agents=(strategy.get("review") or {}).get("providers")
                                or [],
                                completed_summary=(
                                    "Committed a different-family-reviewed research direction: "
                                    f"campaign {committed_plan.get('campaign_id')}, "
                                    f"approach {committed_plan.get('approach_id')}."
                                ),
                                current_summary="The authoritative plan is active and coherent.",
                                next_action=str(committed_plan.get("next_action") or ""),
                                plan_revision=committed_plan.get("plan_revision"),
                            )
                            review_waits = 0
                            continue
                    _progress(
                        "strategy_review_wait",
                        source="drive",
                        event_id=f"strategy-review-wait-r{plan_revision}",
                        iteration_status="waiting",
                        review_status="pending",
                        completed_summary="No direction was committed and no new research result was banked.",
                        current_summary=str(
                            strategy.get("reason")
                            or "Structured strategy review did not yield a safe direction."
                        ),
                        next_action="Obtain valid different-family strategy advice or repair the approach registry.",
                    )
                    review_waits += 1
                    if max_review_waits and review_waits >= max_review_waits:
                        reason = "review_wait_exhausted"
                        _progress(
                            "strategy_review_error",
                            source="drive",
                            iteration_status="error",
                            review_status="error",
                            completed_summary=(
                                "Strategy review wait budget exhausted; the drive exits "
                                "resumably without committing a direction."
                            ),
                            current_summary=(
                                f"{review_waits} consecutive review waits without a "
                                "committed direction."
                            ),
                            next_action=(
                                "Repair the strategy panel or approach registry, then "
                                "restart the drive."
                            ),
                        )
                        break
                    interruptible_sleep(max(poll, 30.0), run_dir)
                    continue

                if not goal_focus_gate.get("ok") or gate_action != "dispatch":
                    _progress(
                        "goal_focus_wait",
                        source="drive",
                        event_id=f"goal-focus-wait-r{plan_revision}-{gate_action}",
                        iteration_status="waiting",
                        review_status="pending",
                        completed_summary="No new result was banked because the dispatch gate did not pass.",
                        current_summary=(
                            "; ".join(goal_focus_gate.get("errors") or [])
                            or f"Goal-Focus gate action is {gate_action}."
                        ),
                        next_action="Reconcile and validate authoritative Goal-Focus state before dispatch.",
                    )
                    interruptible_sleep(max(poll, 30.0), run_dir)
                    continue

            if goal_focus_mode in {"off", "monitor"}:
                # Monitor is observational: preserve the legacy hard-steering
                # dispatch path while reporting Goal-Focus findings.
                try:
                    steer = apply_hard_path_discipline(run_dir)
                    if steer.get("applied"):
                        _progress(
                            "goal_priority_hard_replan",
                            source="drive",
                            reason=str(steer.get("reason") or "")[:200],
                            residual_id=str(steer.get("residual_id") or ""),
                            campaign_id=str(steer.get("campaign_id") or ""),
                            path_preview=str(steer.get("path") or "")[:300],
                        )
                except Exception as exc:  # noqa: BLE001 - steering must not kill drive
                    sys.stderr.write(
                        f"autoloop-driver: goal_priority hard replan failed: {exc}\n"
                    )
            # Transactional remote-bridge claim (drive only; agent-cmd uses peek).
            remote_job = os.environ.get("AAS_REMOTE_JOB_ID")
            inbox_block, claim_ids, claim_fences, claimer = claim_remote_inbox_for_drive(
                remote_job
            )
            if inbox_block:
                os.environ["AAS_DRIVE_INBOX_BLOCK"] = inbox_block
            else:
                os.environ.pop("AAS_DRIVE_INBOX_BLOCK", None)

            # Goal-Focus enforce mode always needs the host-owned panel for its
            # independent result review. This remains true when the legacy
            # target-advice panel switch is explicitly off.
            effective_panel_enabled = panel_enabled or goal_focus_mode == "enforce"
            panel_iter_dir: Path | None = None
            if goal_focus_mode == "enforce":
                # Strategy is already committed by the strict pre-dispatch
                # gate. Reserve the matching result-review directory without
                # running legacy target advice.
                panel_iter_dir = ensure_iter_dir(run_dir)
            elif panel_enabled:
                try:
                    panel_iter_dir = ensure_iter_dir(run_dir)
                    _progress(
                        "panel_target_start",
                        source="drive",
                        drive_cycle=iterations_run + 1,
                        iter_dir=str(panel_iter_dir),
                    )
                    target_summary = run_panel_phase_for_drive(
                        run_dir,
                        root,
                        "target_advice",
                        iter_dir=panel_iter_dir,
                    )
                    if target_summary.get("fatal_resource_cleanup_failure"):
                        _progress(
                            "panel_target_fail",
                            source="drive",
                            drive_cycle=iterations_run + 1,
                            error="panel resource cleanup was not verified",
                        )
                        reason = "resource_cleanup_unverified"
                        break
                    if target_summary.get("panel_roster_withdrawn"):
                        _progress(
                            "panel_target_fail",
                            source="drive",
                            drive_cycle=iterations_run + 1,
                            error=(
                                "every panel reviewer is withdrawn: "
                                + (
                                    ", ".join(target_summary.get("excluded_providers") or [])
                                    or "no reviewers remain"
                                )
                            ),
                        )
                        reason = "panel_roster_withdrawn"
                        break
                    _progress(
                        "panel_target_ok" if target_summary.get("panel_content_pass") else "panel_target_fail",
                        source="drive",
                        drive_cycle=iterations_run + 1,
                        usable_providers=target_summary.get("usable_providers") or [],
                        iter_dir=str(panel_iter_dir),
                    )
                except Exception as exc:  # noqa: BLE001 - panel must not kill drive
                    sys.stderr.write(f"autoloop-driver: panel target_advice failed: {exc}\n")
                    _progress(
                        "panel_target_fail",
                        source="drive",
                        drive_cycle=iterations_run + 1,
                        error=str(exc)[:200],
                    )

            iteration_started_at = utc_now()
            iteration_started_monotonic = time.monotonic()
            # Read the committed path while the host still owns the loop files,
            # and pin it for the append-time formal gate. Refreshed here rather
            # than latched: a pivot off the formal track was itself committed
            # through review, so the next dispatch is entitled to drop the pin.
            try:
                write_track_pin(
                    run_dir,
                    formal_track=formal_track_status(run_dir).derived,
                    source="drive_dispatch",
                    iteration=iterations_run + 1,
                )
            except Exception:  # noqa: BLE001 - the pin only ever adds a check
                pass
            dispatch_intent: dict[str, Any] = {}
            if goal_focus_mode == "enforce":
                identity_overrides = (
                    provider_identity_overrides(provider) if provider else []
                )
                if (
                    not provider
                    or driver_execution_attestation is None
                    or bool(identity_overrides)
                ):
                    finalize_remote_inbox_claim(
                        remote_job or "",
                        claim_ids,
                        claimer=claimer,
                        fences=claim_fences,
                        success=False,
                    )
                    os.environ.pop("AAS_DRIVE_INBOX_BLOCK", None)
                    reason = "bad_arguments"
                    sys.stderr.write(
                        "autoloop-driver: Goal-Focus enforce mode requires --provider "
                        "with a host-pinned model family; custom commands, provider command/"
                        "argument/binary overrides, and unverified gateways cannot establish "
                        "review independence\n"
                    )
                    break
                try:
                    driver_execution_attestation = (
                        revalidate_provider_executable_attestation(
                            driver_execution_attestation,
                            forbidden_roots=(root, run_dir),
                        )
                    )
                    prepared_dispatch = goal_focus_v2.prepare_iteration_dispatch(
                        run_dir,
                        executor_provider=provider,
                        executor_family=str(
                            driver_execution_attestation.get("family")
                            or "unverified"
                        ),
                        executor_attestation=driver_execution_attestation,
                        started_at=iteration_started_at,
                        driver_pid=os.getpid(),
                    )
                    dispatch_intent = prepared_dispatch.get("dispatch") or {}
                except Exception as exc:  # noqa: BLE001 - dispatch authority fails closed
                    finalize_remote_inbox_claim(
                        remote_job or "",
                        claim_ids,
                        claimer=claimer,
                        fences=claim_fences,
                        success=False,
                    )
                    os.environ.pop("AAS_DRIVE_INBOX_BLOCK", None)
                    reason = "runtime_error"
                    sys.stderr.write(
                        f"autoloop-driver: could not prepare Goal-Focus dispatch: {exc}\n"
                    )
                    break

            if provider:
                try:
                    spec = resolve_provider_command(
                        provider,
                        run_dir,
                        panel_enabled=effective_panel_enabled,
                        panel_iter_dir=panel_iter_dir,
                    )
                except ValueError:
                    _cancel_prepared_dispatch(
                        dispatch_intent, "provider_command_resolution_failed"
                    )
                    finalize_remote_inbox_claim(
                        remote_job or "",
                        claim_ids,
                        claimer=claimer,
                        fences=claim_fences,
                        success=False,
                    )
                    os.environ.pop("AAS_DRIVE_INBOX_BLOCK", None)
                    reason = "bad_arguments"
                    break
                if spec["mode"] == "argv" and not spec["binary_found"]:
                    tried = spec.get("tried") or PROVIDER_SPECS[provider]["binaries"]
                    sys.stderr.write(
                        f"autoloop-driver: no {provider} binary found "
                        f"(tried: {', '.join(tried)}); "
                        f"set AAS_AUTOLOOP_BIN_{provider_env_key(provider)} or "
                        f"AAS_AUTOLOOP_CMD_{provider_env_key(provider)}"
                        + (
                            " or AAS_GROK"
                            if provider == "grok"
                            else ""
                        )
                        + "\n"
                    )
                    finalize_remote_inbox_claim(
                        remote_job or "",
                        claim_ids,
                        claimer=claimer,
                        fences=claim_fences,
                        success=False,
                    )
                    os.environ.pop("AAS_DRIVE_INBOX_BLOCK", None)
                    _cancel_prepared_dispatch(
                        dispatch_intent, "provider_binary_unavailable"
                    )
                    reason = "provider_unavailable"
                    break
                if goal_focus_mode == "enforce" and (
                    spec.get("mode") != "argv"
                    or driver_execution_attestation is None
                    or str(spec.get("binary") or "")
                    != str(driver_execution_attestation.get("executable_path") or "")
                ):
                    _cancel_prepared_dispatch(
                        dispatch_intent, "provider_executable_attestation_mismatch"
                    )
                    finalize_remote_inbox_claim(
                        remote_job or "",
                        claim_ids,
                        claimer=claimer,
                        fences=claim_fences,
                        success=False,
                    )
                    os.environ.pop("AAS_DRIVE_INBOX_BLOCK", None)
                    reason = "bad_arguments"
                    sys.stderr.write(
                        "autoloop-driver: resolved provider executable does not match "
                        "the host-pinned executable attestation\n"
                    )
                    break
                run_args = spec["argv"] if spec["mode"] == "argv" else spec["shell"]
                use_shell = spec["mode"] == "shell"
                prompt = spec["prompt"]
                primary_prompt_transport = str(
                    spec.get("prompt_transport") or "argv"
                )
            else:
                run_args = cmd
                use_shell = True
                prompt = iteration_prompt(
                    run_dir,
                    panel_enabled=effective_panel_enabled,
                    panel_iter_dir=panel_iter_dir,
                )
                primary_prompt_transport = "shell"
            primary_stdin_text: str | None = None
            if provider:
                try:
                    assert_panel_prompt_safe(prompt)
                    if primary_prompt_transport == "stdin":
                        primary_stdin_text = prompt
                    elif primary_prompt_transport == "unavailable":
                        raise ValueError(
                            f"provider {provider} has no reviewed private prompt transport"
                        )
                    elif not isinstance(run_args, list):
                        raise ValueError(
                            "provider prompt privacy requires argv execution"
                        )
                    else:
                        run_args, primary_stdin_text = (
                            prepare_primary_private_prompt_transport(
                                provider, run_args, prompt
                            )
                        )
                except PanelIsolationError as exc:
                    _cancel_prepared_dispatch(
                        dispatch_intent, "primary_prompt_privacy_gate_failed"
                    )
                    finalize_remote_inbox_claim(
                        remote_job or "",
                        claim_ids,
                        claimer=claimer,
                        fences=claim_fences,
                        success=False,
                    )
                    os.environ.pop("AAS_DRIVE_INBOX_BLOCK", None)
                    reason = "bad_arguments"
                    sys.stderr.write(
                        "autoloop-driver: primary prompt privacy gate failed: "
                        f"{exc}\n"
                    )
                    break
                except ValueError as exc:
                    _cancel_prepared_dispatch(
                        dispatch_intent, "primary_prompt_transport_unavailable"
                    )
                    finalize_remote_inbox_claim(
                        remote_job or "",
                        claim_ids,
                        claimer=claimer,
                        fences=claim_fences,
                        success=False,
                    )
                    os.environ.pop("AAS_DRIVE_INBOX_BLOCK", None)
                    reason = "bad_arguments"
                    sys.stderr.write(
                        "autoloop-driver: primary prompt transport failed: "
                        f"{exc}\n"
                    )
                    break
            control_env: dict[str, object] = {
                "AUTOLOOP_DRIVER": "1",
                "AUTOLOOP_DIR": str(run_dir),
                "AUTOLOOP_ROOT": str(root),
                "AAS_AUTOLOOP_PRIMARY_PROVIDER": str(provider or "custom"),
                "AAS_AUTOLOOP_ITERATION_STARTED_AT": iteration_started_at,
            }
            if goal_focus_mode != "enforce":
                control_env["AUTOLOOP_PROMPT"] = prompt
            evidence_dir: Path | None = None
            if provider == "deepseek":
                # The argv pins CodeWhale to DeepSeek. Do not allow its normal
                # active-provider or endpoint overrides to disagree with that pin.
                control_env["DEEPSEEK_BASE_URL"] = "https://api.deepseek.com"
            if dispatch_intent:
                candidate_id = str(dispatch_intent.get("candidate_id") or "")
                try:
                    safe_candidate_id = safe_registry_run_id(candidate_id)
                    relative_evidence_root = Path(
                        str(dispatch_intent.get("evidence_root") or "")
                    )
                    expected_evidence_root = (
                        Path(".goal_focus") / "evidence" / safe_candidate_id
                    )
                    if relative_evidence_root != expected_evidence_root:
                        raise ValueError(
                            "dispatch evidence_root is not the exact candidate-scoped root"
                        )
                    evidence_dir = _ensure_real_directory(
                        run_dir / relative_evidence_root
                    )
                except (OSError, ValueError) as exc:
                    _cancel_prepared_dispatch(
                        dispatch_intent, "evidence_directory_unavailable"
                    )
                    finalize_remote_inbox_claim(
                        remote_job or "",
                        claim_ids,
                        claimer=claimer,
                        fences=claim_fences,
                        success=False,
                    )
                    os.environ.pop("AAS_DRIVE_INBOX_BLOCK", None)
                    reason = "bad_arguments"
                    sys.stderr.write(
                        "autoloop-driver: could not prepare the candidate-scoped "
                        f"evidence directory: {exc}\n"
                    )
                    break
                control_env["AAS_AUTOLOOP_DISPATCH_ID"] = str(
                    dispatch_intent.get("dispatch_id") or ""
                )
                control_env["AAS_AUTOLOOP_CANDIDATE_ID"] = safe_candidate_id
                control_env["AAS_AUTOLOOP_EVIDENCE_DIR"] = str(evidence_dir)
                control_env["AAS_AUTOLOOP_EVIDENCE_ROOT"] = str(evidence_dir)
                control_env["AAS_AUTOLOOP_PLAN_REVISION"] = str(
                    dispatch_intent.get("plan_revision") or ""
                )
                control_env["AAS_AUTOLOOP_RUN_ID"] = safe_registry_run_id(run_id)
                control_env["AAS_AUTOLOOP_HOST_MEDIATED_SUBMISSION"] = "1"
            if formal_pol is not None:
                control_env.update(export_formal_env(formal_pol))
            if effective_panel_enabled:
                control_env["AAS_AUTOLOOP_PANEL"] = "on"
                if panel_iter_dir is not None:
                    control_env["AAS_AUTOLOOP_PANEL_ITER_DIR"] = str(panel_iter_dir)
            child_env = build_primary_child_env(
                provider,
                executable_attestation=driver_execution_attestation,
                control=control_env,
                compute_policy_run_dir=run_dir,
                include_provider_credentials=(
                    goal_focus_mode != "enforce"
                    or provider_transport == TRUSTED_LOCAL_TRANSPORT
                ),
                include_compute_credentials=(
                    provider_transport == TRUSTED_LOCAL_TRANSPORT
                ),
            )
            iterations_run += 1
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            log_nonce = uuid.uuid4().hex[:16]
            log_path = log_dir / f"iter_{stamp}_{iterations_run:04d}_{log_nonce}.log"
            log_created = False
            captured_log_tail = ""
            timed_out = False
            cleanup_error: str | None = None
            primary_resource_metadata: dict[str, Any] = {}
            host_runtime_error = False
            _progress(
                "iteration_start",
                source="drive",
                drive_cycle=iterations_run,
                log_path=str(log_path),
                provider=provider or "",
                panel_enabled=effective_panel_enabled,
                started_at=iteration_started_at,
            )
            pre_spent = 0
            try:
                budget_path = loop_paths(run_dir)["budget"]
                if budget_path.exists():
                    pre_spent = int(read_json(budget_path).get("spent_iterations") or 0)
            except Exception:  # noqa: BLE001
                pre_spent = 0
            try:
                with _open_exclusive_driver_log(log_path) as log_fh:
                    log_created = True
                    # Never persist the outbound prompt in the iteration log.
                    # The sentinel still separates host metadata from any
                    # eventual child output for failure classification.
                    try:
                        log_fh.write(HOST_PROMPT_SENTINEL)
                        log_fh.write("\n")
                        log_fh.flush()
                    except Exception:  # noqa: BLE001
                        pass
                    # Always run the iteration agent with project root as cwd so
                    # headless CLIs (including grok -p) see the correct workspace
                    # even when the driver was started from another directory.
                    spawn_identity_error: str | None = None
                    if goal_focus_mode == "enforce":
                        try:
                            if driver_execution_attestation is None:
                                raise PanelIsolationError(
                                    "missing provider executable attestation"
                                )
                            driver_execution_attestation = (
                                revalidate_provider_executable_attestation(
                                    driver_execution_attestation,
                                    forbidden_roots=(root, run_dir),
                                )
                            )
                        except PanelIsolationError as exc:
                            spawn_identity_error = str(exc)
                    if spawn_identity_error is not None:
                        log_fh.write(
                            "autoloop-driver: provider executable attestation "
                            f"failed immediately before spawn: {spawn_identity_error}\n"
                        )
                        log_fh.flush()
                        rc = 126
                    else:
                        try:
                            if credential_broker_active():
                                broker_response = credential_broker_request(
                                    {
                                        "operation": "primary",
                                        "run_args": run_args,
                                        "use_shell": use_shell,
                                        "child_env": child_env,
                                        "cwd": str(root),
                                        "timeout_s": iter_timeout,
                                        "provider": provider,
                                        "enforce_mode": goal_focus_mode == "enforce",
                                        "trusted_local": provider_transport
                                        == TRUSTED_LOCAL_TRANSPORT,
                                        "run_dir": str(run_dir),
                                        "evidence_dir": str(evidence_dir)
                                        if evidence_dir is not None
                                        else None,
                                        "executable_attestation": driver_execution_attestation,
                                        "stdin_text": primary_stdin_text,
                                        "compute_lanes": sorted(
                                            _host_pinned_primary_compute_lanes(run_dir)
                                        ),
                                    },
                                    timeout_s=iter_timeout,
                                )
                                rc = int(broker_response.get("returncode", 126))
                                timed_out = bool(broker_response.get("timed_out"))
                                cleanup_error = (
                                    str(broker_response["cleanup_error"])
                                    if broker_response.get("cleanup_error")
                                    else None
                                )
                                broker_metadata = broker_response.get(
                                    "resource_metadata"
                                )
                                if isinstance(broker_metadata, dict):
                                    primary_resource_metadata.update(broker_metadata)
                                log_fh.write(str(broker_response.get("stdout") or ""))
                                log_fh.write(str(broker_response.get("stderr") or ""))
                            else:
                                rc, timed_out, cleanup_error = run_primary_subprocess(
                                    run_args,
                                    use_shell=use_shell,
                                    child_env=child_env,
                                    cwd=root,
                                    timeout_s=iter_timeout,
                                    output=log_fh,
                                    provider=provider,
                                    enforce_mode=goal_focus_mode == "enforce",
                                    trusted_local=(
                                        provider_transport == TRUSTED_LOCAL_TRANSPORT
                                    ),
                                    run_dir=run_dir,
                                    evidence_dir=evidence_dir,
                                    executable_attestation=driver_execution_attestation,
                                    stdin_text=primary_stdin_text,
                                    resource_metadata=primary_resource_metadata,
                                )
                        except (OSError, CredentialBrokerError) as exc:
                            log_fh.write(f"autoloop-driver: spawn failed: {exc}\n")
                            log_fh.flush()
                            if (
                                primary_resource_metadata
                                and primary_resource_metadata.get(
                                    "cleanup_verified"
                                )
                                is False
                            ):
                                cleanup_error = (
                                    "primary resource cleanup was not verified"
                                )
                                rc = 126
                            else:
                                rc = 127
                        else:
                            if timed_out:
                                log_fh.write(
                                    "autoloop-driver: iteration timed out; "
                                    "the isolated primary process group was terminated\n"
                                )
                            if cleanup_error:
                                log_fh.write(
                                    "autoloop-driver: primary descendant cleanup failed: "
                                    f"{cleanup_error}\n"
                                )
                            log_fh.flush()
                    # Bind failure classification to the exact host-created
                    # descriptor. Never close and reopen a pathname that a
                    # same-user workspace process could replace.
                    captured_log_tail = _read_open_log_tail(log_fh)
            except OSError as exc:
                sys.stderr.write(f"autoloop-driver: could not create iteration log: {exc}\n")
                rc = 127
                host_runtime_error = True
            iteration_finished_at = utc_now()
            iteration_duration = max(0.0, time.monotonic() - iteration_started_monotonic)
            if host_runtime_error:
                if goal_focus_mode == "enforce":
                    _cancel_prepared_dispatch(
                        dispatch_intent, "host_iteration_log_unavailable"
                    )
                finalize_remote_inbox_claim(
                    remote_job or "",
                    claim_ids,
                    claimer=claimer,
                    fences=claim_fences,
                    success=False,
                )
                os.environ.pop("AAS_DRIVE_INBOX_BLOCK", None)
                reason = "runtime_error"
                break
            # The submission gate below rewrites rc to its own verdict, so keep
            # the status the worker actually exited with for later diagnostics.
            worker_rc = rc
            if goal_focus_mode == "enforce":
                try:
                    if cleanup_error is not None:
                        raise OSError(
                            "primary resource/descendant cleanup did not complete"
                        )
                    if timed_out:
                        raise OSError(
                            "primary exceeded its enforced wall-time limit"
                        )
                    goal_focus_v2.validate_provider_resource_attestation(
                        primary_resource_metadata
                    )
                    if evidence_dir is None:
                        raise OSError("candidate evidence directory is unavailable")
                    consume_iteration_submission(
                        run_dir,
                        evidence_dir,
                        expected_run_id=safe_registry_run_id(run_id),
                        expected_dispatch_id=str(
                            dispatch_intent.get("dispatch_id") or ""
                        ),
                        expected_candidate_id=str(
                            dispatch_intent.get("candidate_id") or ""
                        ),
                        expected_provider=str(provider or ""),
                        iteration_started_at=iteration_started_at,
                        resource_attestation=primary_resource_metadata,
                    )
                except FileNotFoundError:
                    if rc == 0:
                        rc = 126
                        sys.stderr.write(
                            "autoloop-driver: worker exited without the exact "
                            "host-mediated iteration submission\n"
                        )
                except Exception as exc:  # noqa: BLE001 - submission gate fails closed
                    rc = 126
                    sys.stderr.write(
                        "autoloop-driver: host rejected iteration submission: "
                        f"{exc}\n"
                    )
                else:
                    # A securely claimed and host-staged candidate is the
                    # completion boundary even if the worker later returned a
                    # non-zero status after emitting its submission.
                    rc = 0
            post_spent = pre_spent
            try:
                budget_path = loop_paths(run_dir)["budget"]
                if budget_path.exists():
                    post_spent = int(read_json(budget_path).get("spent_iterations") or 0)
            except Exception:  # noqa: BLE001
                post_spent = pre_spent
            ledger_advanced = post_spent > pre_spent
            # IG-2: previously observed ledger bytes must never change.
            integrity_failure_class = ""
            ledger_violation = ledger_watch.check()
            if ledger_violation is not None:
                integrity_failure_class = "ledger_integrity_violation"
                snapshot_error = ""
                try:
                    snapshot_path = _write_integrity_snapshot(
                        log_dir,
                        "ledger",
                        {**ledger_violation, "drive_cycle": iterations_run},
                        ledger_watch._read(),
                    )
                    integrity_snapshots.append(snapshot_path)
                except Exception as exc:  # noqa: BLE001 - snapshot is evidence, not a gate.
                    snapshot_path = ""
                    snapshot_error = str(exc)
                _progress(
                    "ledger_integrity_violation",
                    source="drive",
                    drive_cycle=iterations_run,
                    violation=ledger_violation,
                    snapshot_path=snapshot_path,
                    snapshot_error=snapshot_error,
                )
                sys.stderr.write(
                    "autoloop-driver: iterations.jsonl history changed under the "
                    f"host watch ({ledger_violation['kind']}); treating this cycle "
                    "as failed\n"
                )
            # IG-3: cross-file coherence (complement to the prefix watch above —
            # a consistent rewrite of all ledger files passes here by design).
            desync_errors = _ledger_consistency_errors(run_dir)
            if desync_errors:
                if not integrity_failure_class:
                    integrity_failure_class = "ledger_desync"
                event = {
                    "drive_cycle": iterations_run,
                    "observed_at": utc_now(),
                    "errors": desync_errors,
                }
                ledger_desync_events.append(event)
                _progress(
                    "ledger_desync",
                    source="drive",
                    drive_cycle=iterations_run,
                    errors=desync_errors,
                )
                sys.stderr.write(
                    "autoloop-driver: ledger files disagree after this iteration: "
                    + "; ".join(desync_errors)
                    + "\n"
                )
            # IG-1: host-owned build configuration must not drift mid-run.
            if build_config_watch is not None:
                config_changes = build_config_watch.check()
                if config_changes:
                    _progress(
                        "build_config_change",
                        source="drive",
                        drive_cycle=iterations_run,
                        changes=config_changes,
                        locked=build_config_lock,
                    )
                    sys.stderr.write(
                        "autoloop-driver: build configuration changed during the "
                        "loop: "
                        + ", ".join(
                            f"{item['path']} ({item['change']})" for item in config_changes
                        )
                        + ("\n" if not build_config_lock else " [--build-config-lock]\n")
                    )
                    if build_config_lock and not integrity_failure_class:
                        integrity_failure_class = "build_config_change"
            if integrity_failure_class:
                # An integrity finding invalidates this cycle's apparent
                # progress. Legacy mode counts it as a failure through the
                # shared stall handling below; the enforce-mode paths never
                # reach that handling, so they consult integrity_failure_class
                # directly before resetting or bypassing the failure counter.
                ledger_advanced = False
            quarantined_after_failure = False
            if rc != 0 and goal_focus_mode == "enforce":
                try:
                    staged_after_error = goal_focus_v2.load_pending_candidate(run_dir)
                except Exception:  # noqa: BLE001
                    staged_after_error = None
                if staged_after_error or cleanup_error is not None:
                    if cleanup_error is not None:
                        quarantine_reason = (
                            "primary resource cleanup was not verified: "
                            f"{cleanup_error}"
                        )
                    elif timed_out:
                        quarantine_reason = (
                            "primary exceeded its enforced wall-time limit"
                        )
                    elif primary_resource_metadata.get("oversized_output") is True:
                        quarantine_reason = "primary exceeded its bounded output limit"
                    elif primary_resource_metadata.get("sensitive_output_blocked") is True:
                        quarantine_reason = "primary emitted blocked sensitive output"
                    elif primary_resource_metadata.get("capture_verified") is not True:
                        quarantine_reason = "primary output capture was not verified"
                    else:
                        quarantine_reason = (
                            "candidate existed after the host submission gate failed "
                            f"with worker status {worker_rc}"
                        )
                    try:
                        quarantine_result = goal_focus_v2.quarantine_failed_completion(
                            run_dir,
                            reason=quarantine_reason,
                            fallback_dispatch=dispatch_intent,
                        )
                    except Exception as exc:  # noqa: BLE001 - stop before review
                        sys.stderr.write(
                            "autoloop-driver: could not quarantine ineligible candidate: "
                            f"{exc}\n"
                        )
                        reason = (
                            "resource_cleanup_unverified"
                            if cleanup_error is not None
                            else "quarantine_persistence_unverified"
                        )
                        break
                    quarantined_after_failure = quarantine_result.get("status") in {
                        "quarantined",
                        "already_quarantined",
                    }
                    if quarantined_after_failure:
                        quarantine = quarantine_result.get("quarantine") or {}
                        _progress(
                            "goal_focus_wait",
                            source="drive",
                            event_id=(
                                "goal-focus-candidate-quarantine-"
                                f"{quarantine.get('quarantine_id') or 'unknown'}"
                            ),
                            candidate_id=str(quarantine.get("candidate_id") or ""),
                            iteration_status="error",
                            review_status="error",
                            completed_summary=(
                                "No result was banked; the host installed a quarantine "
                                "tombstone after a failed completion gate."
                            ),
                            current_summary=quarantine_reason,
                            next_action=(
                                "Inspect and explicitly release the exact candidate "
                                "fingerprint before dispatch can resume."
                            ),
                        )
                if cleanup_error is not None:
                    # The quarantine marker also blocks a later restart from
                    # reviewing the stale pending object. If an unverified
                    # descendant writes another pending file, validation sees
                    # both files and still fails closed.
                    reason = "resource_cleanup_unverified"
                    break
                if (
                    not staged_after_error
                    and not quarantined_after_failure
                    and not _cancel_prepared_dispatch(
                        dispatch_intent, "worker_exited_without_staging"
                    )
                ):
                    reason = "runtime_error"
                    break
            if cleanup_error is not None:
                # Descendant cleanup is a transport safety boundary, independent
                # of Goal-Focus policy. Never retry or hand an unverified process
                # tree to an outer provider failover path. Enforce mode performs
                # its quarantine/tombstone work above before reaching this gate.
                finalize_remote_inbox_claim(
                    remote_job or "",
                    claim_ids,
                    claimer=claimer,
                    fences=claim_fences,
                    success=False,
                )
                os.environ.pop("AAS_DRIVE_INBOX_BLOCK", None)
                reason = "resource_cleanup_unverified"
                break
            if rc != 0:
                finalize_remote_inbox_claim(
                    remote_job or "",
                    claim_ids,
                    claimer=claimer,
                    fences=claim_fences,
                    success=False,
                )
                os.environ.pop("AAS_DRIVE_INBOX_BLOCK", None)
                # A rejected exclusive create has no trusted descriptor. A
                # successful create is classified only from its in-memory tail.
                tail = captured_log_tail if log_created else ""
                failure_class = (
                    "timeout"
                    if timed_out
                    else classify_iteration_failure(
                        tail, prompt=str(prompt or "")
                    )
                )
                if quarantined_after_failure:
                    failures += 1
                    _progress(
                        "iteration_failed",
                        source="drive",
                        drive_cycle=iterations_run,
                        rc=rc,
                        log_path=str(log_path),
                        failures=failures,
                        max_failures=max_failures,
                        provider=provider or "custom",
                        started_at=iteration_started_at,
                        finished_at=iteration_finished_at,
                        duration_seconds=iteration_duration,
                        failure_class=failure_class,
                        timed_out=timed_out,
                    )
                    sys.stderr.write(
                        "autoloop-driver: failed completion was quarantined; "
                        f"stopping without retry or failover (log: {log_path})\n"
                    )
                    reason = "candidate_quarantined"
                    break
                if failure_class == "auth":
                    # Auth/session death is not a credit wait: exit immediately
                    # so an outer supervisor can rotate (exit 7).
                    reason = "auth_or_session_dead"
                    _progress(
                        "auth_failure",
                        source="drive",
                        drive_cycle=iterations_run,
                        rc=rc,
                        log_path=str(log_path),
                        failure_class="auth",
                        provider=provider or "custom",
                        started_at=iteration_started_at,
                        finished_at=iteration_finished_at,
                        duration_seconds=iteration_duration,
                    )
                    sys.stderr.write(
                        f"autoloop-driver: provider auth/session failure (rc={rc}); "
                        f"exiting with auth_or_session_dead "
                        f"(log: {log_path})\n"
                    )
                    break
                if failure_class == "quota":
                    # Credit/quota outage: pause-and-retry, or after N consecutive
                    # quota signals (default operator policy: N=3) exit 5 so an
                    # outer supervisor can session-exclude as quota_or_credit and
                    # switch to the first available primary.
                    quota_waits += 1
                    quota_waits_total += 1
                    class_text = build_classification_text(
                        tail, prompt=str(prompt or "")
                    )
                    short_cap = bool(QUOTA_SHORT_BACKOFF_PATTERN.search(class_text))
                    wait_s = (
                        min(int(quota_backoff), QUOTA_SHORT_BACKOFF_S)
                        if short_cap and max_quota_waits
                        else int(quota_backoff)
                    )
                    _progress(
                        "quota_wait",
                        source="drive",
                        drive_cycle=iterations_run,
                        rc=rc,
                        log_path=str(log_path),
                        quota_waits=quota_waits,
                        short_cap=short_cap,
                        provider=provider or "custom",
                        started_at=iteration_started_at,
                        duration_seconds=iteration_duration,
                    )
                    # N means switch after the N-th consecutive quota failure
                    # (was `>` which required N+1 signals).
                    if max_quota_waits and quota_waits >= max_quota_waits:
                        reason = "quota_wait_exhausted"
                        sys.stderr.write(
                            f"autoloop-driver: provider credit/quota exhausted after "
                            f"{quota_waits} consecutive signal(s) "
                            f"(max-quota-waits={max_quota_waits}); "
                            f"exiting quota_wait_exhausted (log: {log_path})\n"
                        )
                        break
                    sys.stderr.write(
                        f"autoloop-driver: provider credit/quota signal (rc={rc}); "
                        f"waiting {wait_s}s before retry "
                        f"(consecutive waits: {quota_waits}"
                        + (f"/{max_quota_waits}" if max_quota_waits else "")
                        + f", log: {log_path})\n"
                    )
                    if interruptible_sleep(wait_s, run_dir):
                        # STOP/PAUSE during wait: re-check done path next loop.
                        continue
                    continue
                failures += 1
                _progress(
                    "iteration_failed",
                    source="drive",
                    drive_cycle=iterations_run,
                    rc=rc,
                    log_path=str(log_path),
                    failures=failures,
                    max_failures=max_failures,
                    provider=provider or "custom",
                    started_at=iteration_started_at,
                    finished_at=iteration_finished_at,
                    duration_seconds=iteration_duration,
                    failure_class=failure_class,
                    timed_out=timed_out,
                )
                sys.stderr.write(
                    f"autoloop-driver: iteration command failed "
                    f"(rc={rc}, {failures}/{max_failures}, log: {log_path})\n"
                )
                if failures >= max_failures:
                    reason = "max_failures"
                    break
            else:
                if goal_focus_mode == "enforce":
                    # The worker may only stage. The host must review and
                    # atomically finalize the exact candidate before emitting
                    # success or exposing any research claim in the ledger.
                    try:
                        pending = goal_focus_v2.load_pending_candidate(run_dir)
                    except Exception as exc:  # noqa: BLE001
                        pending = None
                        pending_error = str(exc)
                    else:
                        pending_error = ""
                    if not pending:
                        _cancel_prepared_dispatch(
                            dispatch_intent, "worker_completed_without_staging"
                        )
                        finalize_remote_inbox_claim(
                            remote_job or "",
                            claim_ids,
                            claimer=claimer,
                            fences=claim_fences,
                            success=False,
                        )
                        os.environ.pop("AAS_DRIVE_INBOX_BLOCK", None)
                        failures += 1
                        _progress(
                            "result_review_error",
                            source="drive",
                            event_id=f"missing-staged-candidate-{iterations_run}",
                            drive_cycle=iterations_run,
                            iteration_status="error",
                            review_status="error",
                            provider=provider or "custom",
                            started_at=iteration_started_at,
                            finished_at=iteration_finished_at,
                            duration_seconds=iteration_duration,
                            completed_summary="No result was banked because the worker did not leave a staged candidate.",
                            current_summary=(
                                f"The pending candidate could not be loaded: {pending_error}"
                                if pending_error
                                else "The worker exited successfully without satisfying the Goal-Focus staging contract."
                            ),
                            next_action="Inspect the iteration log, correct the worker command, and retry the same reviewed plan.",
                            log_path=str(log_path),
                        )
                        if failures >= max_failures:
                            reason = "max_failures"
                            break
                        continue

                    try:
                        goal_focus_v2.validate_host_staged_candidate(
                            run_dir,
                            pending,
                            expected_dispatch_id=str(
                                dispatch_intent.get("dispatch_id") or ""
                            ),
                        )
                    except Exception as exc:  # noqa: BLE001 - never review unpinned provenance
                        finalize_remote_inbox_claim(
                            remote_job or "",
                            claim_ids,
                            claimer=claimer,
                            fences=claim_fences,
                            success=True,
                        )
                        os.environ.pop("AAS_DRIVE_INBOX_BLOCK", None)
                        _progress(
                            "result_review_error",
                            source="drive",
                            event_id=f"dispatch-attestation-error-{pending.get('candidate_id')}",
                            candidate_id=str(pending.get("candidate_id") or ""),
                            iteration_status="error",
                            review_status="error",
                            provider=provider or "custom",
                            started_at=iteration_started_at,
                            finished_at=iteration_finished_at,
                            duration_seconds=iteration_duration,
                            completed_summary="No result was banked because host dispatch validation failed.",
                            current_summary=f"The exact staged candidate remains pending: {str(exc)[:400]}",
                            next_action="Repair or reject the exact pending candidate before independent review.",
                            error=str(exc)[:400],
                        )
                        reason = "runtime_error"
                        break

                    # A durable staged candidate means the remote instruction
                    # was executed exactly once. Consume its inbox claim even
                    # if independent review must be retried later.
                    finalize_remote_inbox_claim(
                        remote_job or "",
                        claim_ids,
                        claimer=claimer,
                        fences=claim_fences,
                        success=True,
                    )
                    os.environ.pop("AAS_DRIVE_INBOX_BLOCK", None)
                    pending_record = (
                        pending.get("record")
                        if isinstance(pending.get("record"), dict)
                        else {}
                    )
                    pending_iteration = int(
                        pending_record.get("iteration") or (pre_spent + 1)
                    )
                    review_dir = panel_iter_dir
                    if review_dir is None or not review_dir.is_dir():
                        review_dir = ensure_iter_dir(
                            run_dir, iteration=pending_iteration
                        )
                    review_started_monotonic = time.monotonic()
                    _progress(
                        "result_review_start",
                        source="drive",
                        event_id=f"result-review-start-{pending.get('candidate_id')}",
                        drive_cycle=iterations_run,
                        candidate_id=str(pending.get("candidate_id") or ""),
                        iteration_status="running",
                        review_status="pending",
                        provider=provider or "custom",
                        started_at=iteration_started_at,
                        iter_dir=str(review_dir),
                    )
                    try:
                        review_summary = run_panel_phase_for_drive(
                            run_dir,
                            root,
                            "result_review",
                            iter_dir=review_dir,
                        )
                        if review_summary.get("fatal_resource_cleanup_failure"):
                            _progress(
                                "result_review_error",
                                source="drive",
                                candidate_id=str(pending.get("candidate_id") or ""),
                                iteration_status="error",
                                review_status="error",
                                completed_summary="No result was banked because panel resource cleanup was not verified.",
                                current_summary="A panel process scope may still be live; automatic retry is stopped.",
                                next_action="Inspect and terminate the recorded panel scope before resuming.",
                            )
                            reason = "resource_cleanup_unverified"
                            break
                        if review_summary.get("panel_roster_withdrawn"):
                            _progress(
                                "result_review_error",
                                source="drive",
                                candidate_id=str(pending.get("candidate_id") or ""),
                                iteration_status="error",
                                review_status="error",
                                completed_summary="No result was banked because every panel reviewer is withdrawn.",
                                current_summary=(
                                    "All configured reviewers are excluded, so the independent "
                                    "result review cannot run: "
                                    + (
                                        ", ".join(
                                            review_summary.get("excluded_providers") or []
                                        )
                                        or "no reviewers remain"
                                    )
                                ),
                                next_action="Restore credit for at least one reviewer or clear exclude_until_credit, then resume.",
                            )
                            reason = "panel_roster_withdrawn"
                            break
                        review_outcome = _result_review_from_panel(
                            pending, review_summary
                        )
                    except Exception as exc:  # noqa: BLE001 - candidate remains pending
                        review_outcome = {
                            "status": "pending",
                            "reason": f"result review error: {exc}",
                        }
                    review_finished_at = utc_now()
                    total_duration = iteration_duration + max(
                        0.0, time.monotonic() - review_started_monotonic
                    )
                    if review_outcome.get("status") in {"accepted", "rejected"}:
                        accepted = review_outcome["status"] == "accepted"
                        review = review_outcome.get("review") or {}
                        ledger_record = _reviewed_ledger_record(pending, review)
                        try:
                            finalized = goal_focus_v2.finalize_candidate(
                                run_dir,
                                accepted=accepted,
                                review=review,
                                ledger_record=ledger_record,
                                expected_plan_revision=int(
                                    pending.get("plan_revision") or 0
                                ),
                                # In-memory pin, not the persisted one: the
                                # re-check must resolve the project the drive
                                # pinned, from a copy the agent cannot rewrite.
                                formal_pin=formal_pin,
                            )
                        except Exception as exc:  # noqa: BLE001 - preserve pending
                            failures += 1
                            _progress(
                                "result_review_error",
                                source="drive",
                                event_id=(
                                    "result-review-finalize-error-"
                                    f"{pending.get('candidate_id')}"
                                ),
                                candidate_id=str(pending.get("candidate_id") or ""),
                                iteration_status="error",
                                review_status="error",
                                provider=provider or "custom",
                                started_at=iteration_started_at,
                                finished_at=review_finished_at,
                                duration_seconds=total_duration,
                                completed_summary="No result was banked because atomic finalization failed.",
                                current_summary=f"The reviewed candidate remains pending: {str(exc)[:400]}",
                                next_action="Recover the transaction and retry exact-candidate finalization.",
                                error=str(exc)[:400],
                            )
                            if failures >= max_failures:
                                reason = "max_failures"
                                break
                            continue
                        if integrity_failure_class:
                            # The reviewed candidate stays banked, but the
                            # integrity finding still counts toward
                            # max_failures: enforce mode never reaches the
                            # shared stall handling below, so resetting the
                            # counter here would make tampering uncountable.
                            failures += 1
                        else:
                            failures = 0
                        quota_waits = 0
                        review_waits = 0
                        final_record = finalized.get("record") or {}
                        final_plan = finalized.get("plan") or {}
                        reviewer_families = review.get("reviewer_families") or []
                        reviewer_agents = review.get("providers") or []
                        if accepted:
                            _progress(
                                "iteration_ok",
                                source="drive",
                                event_id=f"arl-iteration_ok-{pending.get('candidate_id')}",
                                candidate_id=str(pending.get("candidate_id") or ""),
                                drive_cycle=iterations_run,
                                rc=0,
                                log_path=str(log_path),
                                iteration_status="success",
                                review_status="passed",
                                reviewer_families=reviewer_families,
                                panel_agents=reviewer_agents,
                                provider=provider or "custom",
                                started_at=iteration_started_at,
                                finished_at=review_finished_at,
                                duration_seconds=total_duration,
                                completed_summary=str(
                                    research_result_text(final_record)
                                    or "The reviewed result was banked."
                                ),
                                current_summary=research_position_text(
                                    final_record,
                                    str(
                                        final_record.get("current_summary")
                                        or "The accepted result is now part of the authoritative ledger."
                                    ),
                                ),
                                next_action=str(
                                    final_record.get("proposed_next_action")
                                    or final_plan.get("next_action")
                                    or "Run the next Goal-Focus pre-dispatch gate."
                                ),
                            )
                        else:
                            _progress(
                                "iteration_rejected",
                                source="drive",
                                event_id=f"arl-iteration_rejected-{pending.get('candidate_id')}",
                                candidate_id=str(pending.get("candidate_id") or ""),
                                drive_cycle=iterations_run,
                                rc=0,
                                log_path=str(log_path),
                                iteration_status="failure",
                                review_status="failed",
                                reviewer_families=reviewer_families,
                                panel_agents=reviewer_agents,
                                provider=provider or "custom",
                                started_at=iteration_started_at,
                                finished_at=review_finished_at,
                                duration_seconds=total_duration,
                                completed_summary="No research claim was banked; the staged candidate failed independent review.",
                                current_summary=(
                                    "The failed attempt consumed its recorded budget; "
                                    "the plan now requires replanning unless the budget is exhausted."
                                ),
                                next_action=str(
                                    final_plan.get("next_action")
                                    or "Run a fresh structured strategy review."
                                ),
                                review_summary=str(
                                    review.get("conservative_verdict") or "failed"
                                ),
                            )

                        # Formal hygiene is downstream of accepted research,
                        # never of a merely staged or rejected candidate.
                        if (
                            accepted
                            and formal_pol is not None
                            and is_force_tick_enabled(formal_pol)
                        ):
                            try:
                                _progress(
                                    "formal_force_tick_start",
                                    source="drive",
                                    drive_cycle=iterations_run,
                                )
                                force_report = formal_force_tick(
                                    run_dir,
                                    root=root,
                                    policy=formal_pol,
                                    pin=formal_pin,
                                )
                                _progress(
                                    "formal_force_tick_done",
                                    source="drive",
                                    drive_cycle=iterations_run,
                                    terminal=force_report.get("terminal"),
                                    hygiene_status=force_report.get("hygiene_status"),
                                )
                            except Exception as exc:  # noqa: BLE001
                                sys.stderr.write(
                                    "autoloop-driver: formal_force_tick failed: "
                                    f"{exc}\n"
                                )
                                _progress(
                                    "formal_force_tick_fail",
                                    source="drive",
                                    drive_cycle=iterations_run,
                                    error=str(exc)[:200],
                                )
                        if integrity_failure_class and failures >= max_failures:
                            reason = "max_failures"
                            break
                        continue

                    _progress(
                        "result_review_wait",
                        source="drive",
                        event_id=f"result-review-wait-{pending.get('candidate_id')}",
                        candidate_id=str(pending.get("candidate_id") or ""),
                        drive_cycle=iterations_run,
                        iteration_status="waiting",
                        review_status="pending",
                        provider=provider or "custom",
                        started_at=iteration_started_at,
                        duration_seconds=total_duration,
                        completed_summary=(
                            "Staged, not yet banked (awaiting independent review): "
                            + (
                                research_result_text(pending_record)
                                or "the exact staged candidate."
                            )
                        ),
                        current_summary=str(
                            review_outcome.get("reason")
                            or "Independent result review is not yet sufficient."
                        ),
                        next_action="Obtain a valid different-family review of the exact pending candidate.",
                    )
                    if integrity_failure_class:
                        # A wait cycle banks nothing, but the integrity finding
                        # must still be counted: enforce mode never reaches the
                        # shared stall handling below.
                        failures += 1
                        if failures >= max_failures:
                            reason = "max_failures"
                            break
                    review_waits += 1
                    if max_review_waits and review_waits >= max_review_waits:
                        reason = "review_wait_exhausted"
                        _progress(
                            "result_review_error",
                            source="drive",
                            drive_cycle=iterations_run,
                            iteration_status="error",
                            review_status="error",
                            completed_summary=(
                                "Review wait budget exhausted; the staged candidate is "
                                "preserved and the drive exits resumably."
                            ),
                            current_summary=(
                                f"{review_waits} consecutive review waits without a "
                                "bank-or-reject verdict."
                            ),
                            next_action=(
                                "Repair the result-review panel, then restart the drive."
                            ),
                        )
                        break
                    interruptible_sleep(max(poll, 30.0), run_dir)
                    continue

                # Legacy mode keeps its established append-then-review behavior.
                iter_ok = ledger_advanced or not remote_job
                finalize_remote_inbox_claim(
                    remote_job or "",
                    claim_ids,
                    claimer=claimer,
                    fences=claim_fences,
                    success=iter_ok,
                )
                os.environ.pop("AAS_DRIVE_INBOX_BLOCK", None)
                # A zero exit that appends no iteration and leaves the loop
                # neither terminal nor paused cannot move compute_done, so
                # clearing the counter here would respawn the same command
                # forever. Count that like a failed iteration, mirroring the
                # enforce staging contract above. Stop/pause requests raised by
                # the iteration itself are legitimate control actions and are
                # settled by the next top-of-loop verdict instead.
                stalled = not ledger_advanced
                if stalled and not integrity_failure_class:
                    # A done/paused verdict excuses a quiet cycle, but never an
                    # integrity finding: a rewritten ledger could fake "done".
                    try:
                        stall_verdict = compute_done(run_dir)
                    except Exception:  # noqa: BLE001 - unreadable state -> count it.
                        stall_verdict = {}
                    stalled = not (
                        stall_verdict.get("done") or stall_verdict.get("paused")
                    )
                if stalled:
                    failures += 1
                    _progress(
                        "iteration_failed",
                        source="drive",
                        drive_cycle=iterations_run,
                        rc=rc,
                        log_path=str(log_path),
                        failures=failures,
                        max_failures=max_failures,
                        provider=provider or "custom",
                        started_at=iteration_started_at,
                        finished_at=iteration_finished_at,
                        duration_seconds=iteration_duration,
                        failure_class=integrity_failure_class or "no_ledger_progress",
                        error=(
                            "the loop's ledger integrity checks failed this cycle"
                            if integrity_failure_class
                            else (
                                "the iteration command exited 0 without appending an "
                                "iteration record, so the loop did not advance"
                            )
                        ),
                    )
                    sys.stderr.write(
                        "autoloop-driver: iteration command exited 0 without "
                        "advancing the iteration ledger "
                        f"({failures}/{max_failures}, log: {log_path})\n"
                    )
                    if failures >= max_failures:
                        reason = "max_failures"
                        break
                    continue
                failures = 0
                quota_waits = 0
                review_waits = 0
                _progress(
                    "iteration_ok",
                    source="drive",
                    drive_cycle=iterations_run,
                    rc=0,
                    log_path=str(log_path),
                    provider=provider or "custom",
                    started_at=iteration_started_at,
                    finished_at=iteration_finished_at,
                    duration_seconds=iteration_duration,
                )
                # Host-owned advisory review after a legacy ledger-advancing iter.
                if panel_enabled and ledger_advanced:
                    try:
                        review_dir = panel_iter_dir
                        if review_dir is None or not review_dir.is_dir():
                            review_dir = ensure_iter_dir(
                                run_dir, iteration=post_spent
                            )
                        _progress(
                            "panel_review_start",
                            source="drive",
                            drive_cycle=iterations_run,
                            iter_dir=str(review_dir),
                        )
                        review_summary = run_panel_phase_for_drive(
                            run_dir,
                            root,
                            "result_review",
                            iter_dir=review_dir,
                        )
                        if review_summary.get("fatal_resource_cleanup_failure"):
                            _progress(
                                "panel_review_fail",
                                source="drive",
                                drive_cycle=iterations_run,
                                error="panel resource cleanup was not verified",
                            )
                            reason = "resource_cleanup_unverified"
                            break
                        if review_summary.get("panel_roster_withdrawn"):
                            _progress(
                                "panel_review_fail",
                                source="drive",
                                drive_cycle=iterations_run,
                                error=(
                                    "every panel reviewer is withdrawn: "
                                    + (
                                        ", ".join(
                                            review_summary.get("excluded_providers") or []
                                        )
                                        or "no reviewers remain"
                                    )
                                ),
                            )
                            reason = "panel_roster_withdrawn"
                            break
                        _progress(
                            "panel_review_ok"
                            if review_summary.get("panel_content_pass")
                            else "panel_review_fail",
                            source="drive",
                            drive_cycle=iterations_run,
                            usable_providers=review_summary.get("usable_providers") or [],
                            iter_dir=str(review_dir),
                        )
                    except Exception as exc:  # noqa: BLE001
                        sys.stderr.write(
                            f"autoloop-driver: panel result_review failed: {exc}\n"
                        )
                        _progress(
                            "panel_review_fail",
                            source="drive",
                            drive_cycle=iterations_run,
                            error=str(exc)[:200],
                        )
                # Host formal hygiene tick (policy=force + flag only). Non-terminal;
                # never OpenGauss; never claim_support=supported; never loop stop.
                if formal_pol is not None and is_force_tick_enabled(formal_pol):
                    try:
                        _progress(
                            "formal_force_tick_start",
                            source="drive",
                            drive_cycle=iterations_run,
                        )
                        force_report = formal_force_tick(
                            run_dir,
                            root=root,
                            policy=formal_pol,
                            pin=formal_pin,
                        )
                        _progress(
                            "formal_force_tick_done",
                            source="drive",
                            drive_cycle=iterations_run,
                            terminal=force_report.get("terminal"),
                            hygiene_status=force_report.get("hygiene_status"),
                        )
                    except Exception as exc:  # noqa: BLE001
                        sys.stderr.write(
                            f"autoloop-driver: formal_force_tick failed: {exc}\n"
                        )
                        _progress(
                            "formal_force_tick_fail",
                            source="drive",
                            drive_cycle=iterations_run,
                            error=str(exc)[:200],
                        )
    finally:
        # If this drive prepared a host dispatch and exits before a candidate is
        # staged (systemd stop, SIGTERM, crash after prepare), clear it so the
        # next drive is not wedged on goal_focus_wait forever.
        try:
            shutdown_reclaim = try_cancel_own_unconsumed_dispatch(
                run_dir,
                owner_pid=os.getpid(),
                reason="drive_shutdown_unconsumed_dispatch",
            )
            if shutdown_reclaim.get("applied"):
                _progress(
                    "goal_focus_recover",
                    source="drive",
                    event_id=(
                        "goal-focus-dispatch-shutdown-cancel-"
                        f"{shutdown_reclaim.get('dispatch_id') or 'unknown'}"
                    ),
                    iteration_status="waiting",
                    review_status="pending",
                    completed_summary=(
                        "Cancelled this drive's unconsumed host dispatch during shutdown."
                    ),
                    current_summary=(
                        f"Dispatch {shutdown_reclaim.get('dispatch_id')} released on "
                        "drive exit (no staged candidate)."
                    ),
                    next_action="Next drive may prepare a fresh host-pinned dispatch.",
                )
        except Exception:  # noqa: BLE001 - shutdown reclaim is best-effort.
            pass
        disarm_ns = argparse.Namespace(
            dir=str(run_dir), run_id=None, registry_dir=getattr(args, "registry_dir", None)
        )
        try:
            disarm_loop(disarm_ns)
        except Exception:  # noqa: BLE001 - disarm is best-effort cleanup.
            pass
        integrity_summary = {
            "ledger_violations": list(ledger_watch.violations),
            "ledger_desync_events": list(ledger_desync_events),
            "build_config_changes": (
                list(build_config_watch.changes) if build_config_watch is not None else []
            ),
            "build_config_lock": build_config_lock,
            "snapshots": list(integrity_snapshots),
        }
        integrity_clean = not (
            integrity_summary["ledger_violations"]
            or integrity_summary["ledger_desync_events"]
            or integrity_summary["build_config_changes"]
        )
        formal_terminal = ""
        if formal_pol is not None and formal_pol.policy in {"on", "force"}:
            # Host-authored verdict on the formal artifact. A "done" exit pays
            # for the full lake build; failure exits record a scan-only ledger
            # so shutdown stays fast and never certifies sorry-free.
            try:
                formal_verdict = evaluate_formal_terminal_state(
                    run_dir,
                    root=root,
                    policy=formal_pol,
                    pin=formal_pin,
                    reason=f"drive_stop:{reason}",
                    require_typecheck=(reason == "done"),
                    integrity=integrity_summary,
                )
                formal_terminal = str(formal_verdict.get("terminal_state") or "")
            except Exception:  # noqa: BLE001 - verdict is best-effort at shutdown.
                formal_terminal = ""
        _progress(
            "drive_stop",
            source="drive",
            drive_cycle=iterations_run,
            terminal_reason=reason,
            formal_terminal_state=formal_terminal,
            provider=provider or "",
            ledger_violations=len(integrity_summary["ledger_violations"]),
            ledger_desync_events=len(integrity_summary["ledger_desync_events"]),
            build_config_changes=len(integrity_summary["build_config_changes"]),
        )
        if prior_primary_provider is None:
            os.environ.pop("AAS_AUTOLOOP_PRIMARY_PROVIDER", None)
        else:
            os.environ["AAS_AUTOLOOP_PRIMARY_PROVIDER"] = prior_primary_provider

    exit_code = DRIVE_EXIT_CODES.get(reason, 0)
    return {
        "status": "failed" if exit_code else "ok",
        "action": "drive",
        "dir": str(run_dir),
        "provider": provider,
        "reason": reason,
        "iterations_run": iterations_run,
        "quota_waits_total": quota_waits_total,
        "log_dir": str(log_dir),
        "exit_code": exit_code,
        "live_status": str(run_dir / "LIVE_STATUS.md"),
        "progress_jsonl": str(log_dir / "progress.jsonl"),
        "integrity": {**integrity_summary, "clean": integrity_clean},
    }


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def nonnegative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be finite and non-negative")
    return parsed


def _goal_focus_negative_space_status(run_dir: Path) -> dict[str, Any]:
    """Compact negative-space summary for goal-focus status (never bank authority)."""

    try:
        import negative_space as ns_mod
    except ImportError:  # pragma: no cover
        try:
            from . import negative_space as ns_mod  # type: ignore
        except ImportError:
            return {"open_count": 0, "available": False}
    try:
        report = ns_mod.validate_negative_space(run_dir, enforce=False)
        open_rows = ns_mod.summarize_open(run_dir, limit=10)
        return {
            "available": True,
            "open_count": int(report.get("open_count") or 0),
            "path": report.get("path"),
            "open_entries": open_rows,
            "errors": list(report.get("errors") or []),
            "warnings": list(report.get("warnings") or []),
        }
    except Exception as exc:  # noqa: BLE001 - status must stay diagnostic
        return {"available": False, "open_count": 0, "error": str(exc)}


def goal_focus_status_command(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    validation = goal_focus_v2.validate_goal_focus(run_dir, require_enabled=True)
    try:
        bundle = goal_focus_v2.load_goal_focus(run_dir)
        triggers = goal_focus_v2.evaluate_replan_triggers(run_dir, bundle)
    except Exception as exc:  # noqa: BLE001 - surface complete diagnostic JSON
        bundle = {}
        triggers = []
        if not validation.get("errors"):
            validation = {
                **validation,
                "status": "error",
                "errors": [str(exc)],
            }
    plan = bundle.get("plan") if isinstance(bundle.get("plan"), dict) else {}
    pending = (
        bundle.get("pending_candidate")
        if isinstance(bundle.get("pending_candidate"), dict)
        else None
    )
    dispatch = (
        bundle.get("iteration_dispatch")
        if isinstance(bundle.get("iteration_dispatch"), dict)
        else None
    )
    quarantine = (
        bundle.get("candidate_quarantine")
        if isinstance(bundle.get("candidate_quarantine"), dict)
        else None
    )
    return {
        "status": "ok" if not validation.get("errors") else "failed",
        "action": "goal-focus-status",
        "dir": str(run_dir),
        "mode": plan.get("enforcement_mode", "off"),
        "plan": {
            key: plan.get(key)
            for key in (
                "plan_id",
                "plan_revision",
                "state",
                "campaign_id",
                "approach_id",
                "objective_id",
                "scope_lock",
                "next_action",
                "valid_through_iteration",
            )
        },
        "pending_candidate": (
            {
                "candidate_id": pending.get("candidate_id"),
                "status": pending.get("status"),
                "plan_revision": pending.get("plan_revision"),
                "staged_at": pending.get("staged_at"),
            }
            if pending
            else None
        ),
        "inflight_dispatch": (
            {
                key: dispatch.get(key)
                for key in (
                    "dispatch_id",
                    "candidate_id",
                    "executor_provider",
                    "executor_family",
                    "plan_revision",
                    "created_at",
                    "driver_pid",
                )
            }
            if dispatch
            else None
        ),
        "candidate_quarantine": (
            {
                key: quarantine.get(key)
                for key in (
                    "quarantine_id",
                    "object_kind",
                    "candidate_id",
                    "candidate_fingerprint",
                    "reason",
                    "quarantined_at",
                )
            }
            if quarantine
            else None
        ),
        "replan_triggers": triggers,
        "validation": validation,
        "negative_space": _goal_focus_negative_space_status(run_dir),
    }


def goal_focus_validate_command(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    result = goal_focus_v2.validate_goal_focus(run_dir, require_enabled=True)
    return {
        **result,
        "status": "ok" if not result.get("errors") else "failed",
        "action": "goal-focus-validate",
        "dir": str(run_dir),
    }


def goal_focus_set_mode_command(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    mode = str(args.mode)
    result: dict[str, Any] = {
        "action": "goal-focus-set-mode",
        "dir": str(run_dir),
        "mode": mode,
        "dry_run": not bool(args.apply),
        "applied": False,
    }
    try:
        goal_focus_v2.recover_transactions(run_dir)
        validation = goal_focus_v2.validate_goal_focus(run_dir, require_enabled=True)
        plan = goal_focus_v2.load_current_plan(run_dir)
    except (OSError, ValueError) as exc:
        return {**result, "status": "failed", "error": str(exc)}
    # An unbound enforcement_mode is precisely what this command repairs, so it
    # is the one validation error that must not block it.
    blocking = [
        error
        for error in (validation.get("errors") or [])
        if error != goal_focus_v2.MODE_AUTHORITY_ERROR
    ]
    previous_mode = str(plan.get("enforcement_mode") or "").lower()
    result.update(
        {
            "previous_mode": previous_mode,
            "plan_state": plan.get("state"),
            "validation": validation,
        }
    )
    if blocking:
        return {
            **result,
            "status": "failed",
            "errors": blocking,
            "error": "repair the Goal-Focus authority before changing the mode",
        }
    if not bool(args.apply):
        return {
            **result,
            "status": "ok",
            "would_change": previous_mode != mode or bool(validation.get("errors")),
        }
    try:
        # The plan revision moves under the driver's feet otherwise; a live
        # driver must be stopped before the mode is re-bound.
        with LoopLock(run_dir):
            live_drivers = live_driver_entries_for_loop(registry_dir(args), run_dir)
    except RegistrySafetyError as exc:
        return {**result, "status": "failed", "error": str(exc)}
    if live_drivers:
        return {
            **result,
            "status": "failed",
            "driver_pids": sorted(
                {
                    int(entry.get("pid"))
                    for _, entry in live_drivers
                    if isinstance(entry.get("pid"), int)
                }
            ),
            "error": "refusing to change enforcement_mode while a live driver owns this loop",
        }
    try:
        committed = goal_focus_v2.set_enforcement_mode(
            run_dir, mode=mode, trigger=str(args.trigger or "operator")
        )
    except (OSError, ValueError, goal_focus_v2.RevisionConflict) as exc:
        return {**result, "status": "failed", "error": str(exc)}
    validation = goal_focus_v2.validate_goal_focus(run_dir, require_enabled=True)
    return {
        **result,
        "status": "ok" if not validation.get("errors") else "failed",
        "set_mode_status": committed.get("status"),
        "applied": committed.get("status") != "unchanged",
        "plan_revision": committed.get("plan", {}).get("plan_revision"),
        "decision_id": (committed.get("decision") or {}).get("decision_id"),
        "validation": validation,
    }


def goal_focus_migrate_command(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    claim_identity: tuple[int, int] | None = None
    migration_guard: dict[str, Any] | None = None
    if bool(args.apply):
        try:
            claim_identity = acquire_migration_claim(run_dir)
        except FileExistsError:
            # A crashed owner is recoverable only with positive evidence that
            # its recorded PID is dead.  Malformed/symlinked/live claims remain
            # fail-closed and require operator inspection.
            if reclaim_dead_migration_claim(run_dir):
                try:
                    claim_identity = acquire_migration_claim(run_dir)
                except FileExistsError:
                    claim_identity = None
            if claim_identity is None:
                return {
                    "status": "failed",
                    "migration_status": "migration_in_progress",
                    "action": "goal-focus-migrate",
                    "dir": str(run_dir),
                    "dry_run": False,
                    "applied": False,
                    "error": (
                        "another Goal-Focus migration owns this loop, or its "
                        "claim cannot be safely proven stale"
                    ),
                }
    try:
        if bool(args.apply):
            # Current-runtime driver registrations use this same loop lock.  A
            # claim exists before the scan, so a waiter must fail its in-lock
            # precheck; an earlier registrant is stable and visible here.
            try:
                with LoopLock(run_dir):
                    live_drivers = live_driver_entries_for_loop(
                        registry_dir(args), run_dir
                    )
            except RegistrySafetyError as exc:
                return {
                    "status": "failed",
                    "migration_status": "registry_unsafe",
                    "action": "goal-focus-migrate",
                    "dir": str(run_dir),
                    "dry_run": False,
                    "applied": False,
                    "error": str(exc),
                }
            if live_drivers:
                entry = live_drivers[0][1]
                return {
                    "status": "failed",
                    "migration_status": "active_driver",
                    "action": "goal-focus-migrate",
                    "dir": str(run_dir),
                    "dry_run": False,
                    "applied": False,
                    "driver_pid": entry.get("pid"),
                    "driver_pids": sorted(
                        {
                            int(candidate.get("pid"))
                            for _, candidate in live_drivers
                            if isinstance(candidate.get("pid"), int)
                        }
                    ),
                    "error": "refusing migration while a live driver owns this loop",
                }
            if claim_identity is None:
                raise MigrationClaimError("migration claim disappeared before apply")
            claim_record, observed_identity = migration_claim_snapshot(run_dir)
            nonce = str(claim_record.get("nonce") or "")
            if (
                observed_identity != claim_identity
                or claim_record.get("pid") != os.getpid()
                or re.fullmatch(r"[0-9a-f]{32}", nonce) is None
            ):
                raise MigrationClaimError(
                    "migration claim ownership changed after the live-driver scan"
                )
            migration_guard = {
                "schema_version": "goal_focus_migration_guard.v1",
                "run_dir": str(run_dir),
                "claim_identity": [int(value) for value in claim_identity],
                "claim_pid": os.getpid(),
                "nonce": nonce,
                "live_driver_count": 0,
            }
        if bool(args.apply):
            result = goal_focus_v2.migrate_v1(
                run_dir,
                apply=True,
                active_campaign=(str(args.active_campaign).strip() or None),
                migration_claim=migration_guard,
            )
        else:
            result = goal_focus_v2.migrate_v1(
                run_dir,
                apply=False,
                active_campaign=(str(args.active_campaign).strip() or None),
            )
    finally:
        if claim_identity is not None:
            release_migration_claim(run_dir, claim_identity)
    failed = bool(result.get("error")) or (
        bool(args.apply) and result.get("status") not in {"migrated", "already_migrated"}
    )
    return {
        **result,
        "status": "failed" if failed else "ok",
        "migration_status": result.get("status"),
        "action": "goal-focus-migrate",
        "dir": str(run_dir),
        "dry_run": not bool(args.apply),
    }


def goal_focus_reconcile_command(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    validation = goal_focus_v2.validate_goal_focus(run_dir, require_enabled=True)
    if validation.get("errors"):
        return {
            "status": "failed",
            "reconcile_status": "invalid_authority",
            "action": "goal-focus-reconcile",
            "dir": str(run_dir),
            "dry_run": not bool(args.apply),
            "applied": False,
            "validation": validation,
        }
    result = goal_focus_v2.reconcile_goal_focus(run_dir, apply=bool(args.apply))
    validation = goal_focus_v2.validate_goal_focus(run_dir, require_enabled=True)
    return {
        **result,
        "status": "ok" if not validation.get("errors") else "failed",
        "reconcile_status": result.get("status"),
        "action": "goal-focus-reconcile",
        "dir": str(run_dir),
        "dry_run": not bool(args.apply),
        "validation": validation,
    }


def goal_focus_recover_dispatch_command(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    goal_focus_v2.recover_transactions(run_dir)
    dispatch = goal_focus_v2.load_iteration_dispatch(run_dir)
    if not dispatch:
        return {
            "status": "ok",
            "action": "goal-focus-recover-dispatch",
            "dir": str(run_dir),
            "recovery_status": "no_inflight_dispatch",
            "applied": False,
        }
    dispatch_id = str(dispatch.get("dispatch_id") or "")
    if not bool(args.cancel):
        return {
            "status": "ok",
            "action": "goal-focus-recover-dispatch",
            "dir": str(run_dir),
            "recovery_status": "awaiting_explicit_cancel",
            "applied": False,
            "dispatch": dispatch,
            "note": (
                "Confirm that the original worker is no longer running, then rerun "
                f"with --cancel --dispatch-id {dispatch_id}."
            ),
        }
    if str(args.dispatch_id or "").strip() != dispatch_id:
        return {
            "status": "failed",
            "action": "goal-focus-recover-dispatch",
            "dir": str(run_dir),
            "recovery_status": "dispatch_id_mismatch",
            "applied": False,
            "error": "--dispatch-id must exactly match the in-flight host dispatch",
            "dispatch": dispatch,
        }
    result = goal_focus_v2.cancel_iteration_dispatch(
        run_dir,
        dispatch_id=dispatch_id,
        reason=str(args.reason or "operator_confirmed_worker_absent"),
    )
    return {
        "status": "ok",
        "action": "goal-focus-recover-dispatch",
        "dir": str(run_dir),
        "recovery_status": result.get("status"),
        "applied": result.get("status") == "cancelled",
        "dispatch_id": dispatch_id,
        "result": result,
    }


def goal_focus_recover_quarantine_command(
    args: argparse.Namespace,
) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    goal_focus_v2.recover_transactions(run_dir)
    quarantine = goal_focus_v2.load_candidate_quarantine(run_dir)
    if not quarantine:
        return {
            "status": "ok",
            "action": "goal-focus-recover-quarantine",
            "dir": str(run_dir),
            "recovery_status": "no_candidate_quarantine",
            "applied": False,
        }
    summary = {
        key: quarantine.get(key)
        for key in (
            "quarantine_id",
            "object_kind",
            "candidate_id",
            "candidate_fingerprint",
            "reason",
            "quarantined_at",
        )
    }
    fingerprint = str(quarantine.get("candidate_fingerprint") or "")
    if not bool(args.release):
        return {
            "status": "ok",
            "action": "goal-focus-recover-quarantine",
            "dir": str(run_dir),
            "recovery_status": "awaiting_explicit_release",
            "applied": False,
            "quarantine": summary,
            "note": (
                (
                    "First confirm every provider descendant is gone. "
                    if "cleanup" in str(quarantine.get("reason") or "").lower()
                    else ""
                )
                + "Inspect the quarantined completion and evidence, then rerun with "
                "--release --candidate-fingerprint " + fingerprint
            ),
        }
    if str(args.candidate_fingerprint or "").strip() != fingerprint:
        return {
            "status": "failed",
            "action": "goal-focus-recover-quarantine",
            "dir": str(run_dir),
            "recovery_status": "candidate_fingerprint_mismatch",
            "applied": False,
            "error": (
                "--candidate-fingerprint must exactly match the active quarantine"
            ),
            "quarantine": summary,
        }
    result = goal_focus_v2.release_candidate_quarantine(
        run_dir,
        expected_candidate_fingerprint=fingerprint,
    )
    return {
        "status": "ok",
        "action": "goal-focus-recover-quarantine",
        "dir": str(run_dir),
        "recovery_status": result.get("status"),
        "applied": result.get("status") == "released",
        "candidate_fingerprint": fingerprint,
        "result": result,
    }


def goal_focus_replan_command(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    root = Path(args.root).expanduser().resolve() if args.root else run_dir
    gate = goal_focus_v2.pre_dispatch_gate(run_dir)
    if gate.get("errors"):
        return {
            "status": "failed",
            "action": "goal-focus-replan",
            "dir": str(run_dir),
            "error": "Goal-Focus authority must validate before replanning",
            "gate": gate,
        }
    if gate.get("action") in {"review_pending", "dispatch_inflight"}:
        return {
            "status": "failed",
            "action": "goal-focus-replan",
            "dir": str(run_dir),
            "error": (
                "the exact pending result must be reviewed before replanning"
                if gate.get("action") == "review_pending"
                else "an in-flight host dispatch must finish or be explicitly cancelled before replanning"
            ),
            "gate": gate,
        }
    registry = goal_focus_v2.load_approach_registry(run_dir, required=True)
    provisional = goal_focus_v2.select_direction(registry, run_dir=run_dir)
    if not bool(args.apply):
        return {
            "status": "ok",
            "action": "goal-focus-replan",
            "dir": str(run_dir),
            "dry_run": True,
            "reviewed": False,
            "trigger": args.trigger,
            "gate": gate,
            "provisional_registry_selection": provisional,
            "note": "No panel was dispatched and no authority file was changed.",
        }

    plan = goal_focus_v2.load_current_plan(run_dir, required=True)
    primary_provider = str(
        getattr(args, "primary_provider", None)
        or os.environ.get("AAS_AUTOLOOP_PRIMARY_PROVIDER")
        or ""
    ).strip()
    try:
        primary_execution_attestation = attest_provider_executable(
            primary_provider,
            forbidden_roots=(root, run_dir),
            required=True,
        ) if primary_provider else None
    except PanelIsolationError as exc:
        primary_execution_attestation = None
        primary_identity_error = str(exc)
    else:
        primary_identity_error = None
    if (
        not primary_provider
        or primary_execution_attestation is None
        or str(primary_execution_attestation.get("family") or "unverified")
        == "unverified"
    ):
        return {
            "status": "failed",
            "action": "goal-focus-replan",
            "dir": str(run_dir),
            "dry_run": False,
            "committed": False,
            "error": (
                "--primary-provider with a host-attested executable identity is required "
                "for an applied manual strategy review"
                + (f": {primary_identity_error}" if primary_identity_error else "")
            ),
        }
    if args.providers:
        try:
            providers = parse_explicit_provider_roster(args.providers)
        except ValueError as exc:
            return {
                "status": "failed",
                "action": "goal-focus-replan",
                "dir": str(run_dir),
                "dry_run": False,
                "committed": False,
                "error": str(exc),
            }
    else:
        providers = None
    iter_dir = ensure_iter_dir(run_dir)
    previous_primary = os.environ.get("AAS_AUTOLOOP_PRIMARY_PROVIDER")
    os.environ["AAS_AUTOLOOP_PRIMARY_PROVIDER"] = primary_provider
    try:
        summary = run_panel_phase_for_drive(
            run_dir,
            root,
            "strategy_review",
            iter_dir=iter_dir,
            providers=providers,
        )
    finally:
        if previous_primary is None:
            os.environ.pop("AAS_AUTOLOOP_PRIMARY_PROVIDER", None)
        else:
            os.environ["AAS_AUTOLOOP_PRIMARY_PROVIDER"] = previous_primary
    strategy = _strategy_selection_from_panel(run_dir, summary)
    if strategy.get("status") != "ready":
        return {
            "status": "failed",
            "action": "goal-focus-replan",
            "dir": str(run_dir),
            "dry_run": False,
            "committed": False,
            "error": str(
                strategy.get("reason")
                or "structured strategy review did not yield a safe direction"
            ),
            "strategy": strategy,
            "panel": summary,
        }
    committed = goal_focus_v2.commit_selected_direction(
        run_dir,
        strategy["selection"],
        strategy["review"],
        str(args.trigger or "manual"),
        expected_plan_revision=int(plan.get("plan_revision") or 0),
    )
    validation = goal_focus_v2.validate_goal_focus(run_dir, require_enabled=True)
    return {
        "status": "ok" if not validation.get("errors") else "failed",
        "action": "goal-focus-replan",
        "dir": str(run_dir),
        "dry_run": False,
        "committed": True,
        "trigger": args.trigger,
        "selection": strategy.get("selection"),
        "review": strategy.get("review"),
        "plan": committed.get("plan"),
        "transaction": committed.get("transaction"),
        "validation": validation,
    }


def add_formal_policy_args(sub: argparse.ArgumentParser) -> None:
    """Shared init/drive formal_policy CLI (default-off; opt-in Lean assist)."""
    sub.add_argument(
        "--formal-policy",
        default=None,
        choices=["off", "mention-only", "auto", "on", "force"],
        help=(
            "Lean formalization assist policy (default off / file / env "
            "AAS_AUTOLOOP_FORMAL_POLICY). off = no prompt injection; force = "
            "hygiene host tick when also --formal-force-after-iteration. "
            "Not the same as headless force-driven ARL."
        ),
    )
    sub.add_argument(
        "--formal-project",
        default=None,
        help="relative Lake project path under the loop/root (default formal/)",
    )
    sub.add_argument(
        "--formal-force-credits",
        type=positive_int,
        default=None,
        help="host formal_force_tick credit budget when policy=force (default 3)",
    )
    sub.add_argument(
        "--formal-allow-path-steal",
        action="store_true",
        help=(
            "reserved: allow host to propose formal-track path when policy=force "
            "(MVP still refuses path steal writes; default false)"
        ),
    )
    sub.add_argument(
        "--formal-typecheck",
        action="store_true",
        help="opt-in Lake typecheck inside host formal_force_tick (default scan-only)",
    )
    sub.add_argument(
        "--formal-force-after-iteration",
        action="store_true",
        help=(
            "run non-terminal formal_force_tick after each successful iteration "
            "when --formal-policy force (or standing/env force). Never stops the loop."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline autonomous research loop ledger helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="initialize loop ledger files")
    init.add_argument("--dir", required=True, help="loop directory")
    init.add_argument("--goal", required=True, help="research loop goal")
    init.add_argument("--success-criteria", required=True, help="observable success criteria")
    init.add_argument("--mode", choices=sorted(VALID_MODES), default="bounded-research")
    init.add_argument("--max-iterations", type=positive_int, default=5)
    init.add_argument("--max-wall-time-seconds", type=positive_int, default=3600)
    init.add_argument("--max-tokens", type=positive_int, default=0)
    init.add_argument("--max-usd", type=nonnegative_float, default=0.0)
    init.add_argument("--max-depth", type=positive_int, default=3)
    init.add_argument("--max-hops", type=positive_int, default=20)
    init.add_argument("--max-child-workers", type=positive_int, default=2)
    init.add_argument("--plateau-rule", default=DEFAULT_PLATEAU_RULE)
    init.add_argument("--budget-owner", default="user")
    init.add_argument("--force", action="store_true")
    init.add_argument("--stop-on-guard-fail", action=argparse.BooleanOptionalAction, default=True)
    init.add_argument("--stop-on-missing-evidence", action=argparse.BooleanOptionalAction, default=True)
    init.add_argument("--stop-on-scope-change", action=argparse.BooleanOptionalAction, default=True)
    init.add_argument(
        "--success-check",
        default="",
        help="machine-checkable shell command that exits 0 when the loop goal is resolved "
        "(run by the driver/agent, never by the Stop hook)",
    )
    init.add_argument(
        "--require-user-stop-only",
        action="store_true",
        help="user override (priority 0): stop ONLY on explicit user stop; ignore the "
        "loop-count/credit/goal defaults",
    )
    init.add_argument("--stop-condition", action="append", help="free-text user stop requirement (priority 0)")
    init.add_argument(
        "--goal-priority-template",
        action="store_true",
        help="also write legacy goal_priority.json example (default enabled:false, discipline_mode:soft)",
    )
    init.add_argument(
        "--goal-focus-mode",
        choices=sorted(GOAL_FOCUS_MODES),
        default="enforce",
        help="Goal Focus v2 mode for new loops (default: enforce; off preserves legacy-only behavior)",
    )
    add_formal_policy_args(init)
    init.set_defaults(func=init_loop)

    goal_focus = subparsers.add_parser(
        "goal-focus",
        help="inspect, migrate, reconcile, or replan Goal-Focus v2 authority",
    )
    goal_focus_sub = goal_focus.add_subparsers(
        dest="goal_focus_command", required=True
    )

    goal_focus_status = goal_focus_sub.add_parser(
        "status", help="show the authoritative plan, triggers, pending review, and validation"
    )
    goal_focus_status.add_argument("--dir", required=True)
    goal_focus_status.set_defaults(func=goal_focus_status_command)

    goal_focus_validate = goal_focus_sub.add_parser(
        "validate", help="validate Goal-Focus schemas and cross-file invariants"
    )
    goal_focus_validate.add_argument("--dir", required=True)
    goal_focus_validate.set_defaults(func=goal_focus_validate_command)

    goal_focus_set_mode = goal_focus_sub.add_parser(
        "set-mode",
        help="escalate or relax enforcement_mode through one bound decision row",
    )
    goal_focus_set_mode.add_argument("--dir", required=True)
    goal_focus_set_mode.add_argument(
        "--mode", choices=sorted(GOAL_FOCUS_MODES), required=True
    )
    goal_focus_set_mode.add_argument(
        "--registry-dir",
        default=None,
        help="autoloop registry root used to refuse apply while a driver is live",
    )
    goal_focus_set_mode.add_argument("--trigger", default="operator")
    set_mode_mode = goal_focus_set_mode.add_mutually_exclusive_group()
    set_mode_mode.add_argument("--dry-run", action="store_true")
    set_mode_mode.add_argument("--apply", action="store_true")
    goal_focus_set_mode.set_defaults(func=goal_focus_set_mode_command)

    goal_focus_migrate = goal_focus_sub.add_parser(
        "migrate", help="plan or apply a provenance-preserving v1 migration"
    )
    goal_focus_migrate.add_argument("--dir", required=True)
    goal_focus_migrate.add_argument(
        "--registry-dir",
        default=None,
        help="autoloop registry root used to refuse apply while a driver is live",
    )
    goal_focus_migrate.add_argument(
        "--active-campaign",
        default="",
        help="explicitly resolve ambiguous legacy direction signals",
    )
    migration_mode = goal_focus_migrate.add_mutually_exclusive_group()
    migration_mode.add_argument("--dry-run", action="store_true")
    migration_mode.add_argument("--apply", action="store_true")
    goal_focus_migrate.set_defaults(func=goal_focus_migrate_command)

    goal_focus_reconcile = goal_focus_sub.add_parser(
        "reconcile", help="inspect or regenerate managed compatibility projections"
    )
    goal_focus_reconcile.add_argument("--dir", required=True)
    reconcile_mode = goal_focus_reconcile.add_mutually_exclusive_group()
    reconcile_mode.add_argument("--dry-run", action="store_true")
    reconcile_mode.add_argument("--apply", action="store_true")
    goal_focus_reconcile.set_defaults(func=goal_focus_reconcile_command)

    goal_focus_recover_dispatch = goal_focus_sub.add_parser(
        "recover-dispatch",
        help="inspect or explicitly cancel an in-flight host dispatch after confirming its worker is gone",
    )
    goal_focus_recover_dispatch.add_argument("--dir", required=True)
    goal_focus_recover_dispatch.add_argument("--cancel", action="store_true")
    goal_focus_recover_dispatch.add_argument("--dispatch-id", default="")
    goal_focus_recover_dispatch.add_argument(
        "--reason", default="operator_confirmed_worker_absent"
    )
    goal_focus_recover_dispatch.set_defaults(
        func=goal_focus_recover_dispatch_command
    )

    goal_focus_recover_quarantine = goal_focus_sub.add_parser(
        "recover-quarantine",
        help=(
            "inspect or explicitly release a timed-out/failed provider candidate "
            "by its exact fingerprint"
        ),
    )
    goal_focus_recover_quarantine.add_argument("--dir", required=True)
    goal_focus_recover_quarantine.add_argument("--release", action="store_true")
    goal_focus_recover_quarantine.add_argument(
        "--candidate-fingerprint", default=""
    )
    goal_focus_recover_quarantine.set_defaults(
        func=goal_focus_recover_quarantine_command
    )

    goal_focus_replan = goal_focus_sub.add_parser(
        "replan", help="preview registry scoring or commit a panel-reviewed direction"
    )
    goal_focus_replan.add_argument("--dir", required=True)
    goal_focus_replan.add_argument(
        "--root", default=None, help="project root used as the strategy-panel cwd"
    )
    goal_focus_replan.add_argument("--trigger", default="manual")
    goal_focus_replan.add_argument(
        "--providers",
        default=None,
        help="comma-separated panel providers; default comes from panel configuration",
    )
    goal_focus_replan.add_argument(
        "--primary-provider",
        choices=sorted(PROVIDER_SPECS),
        default=None,
        help="host-attested active driver provider (required with --apply)",
    )
    replan_mode = goal_focus_replan.add_mutually_exclusive_group()
    replan_mode.add_argument("--dry-run", action="store_true")
    replan_mode.add_argument("--apply", action="store_true")
    goal_focus_replan.set_defaults(func=goal_focus_replan_command)

    append_epilog = (
        "early-stop evidence contract (decision=stop before max_iterations):\n"
        f"  accepted stop_reason values: {', '.join(sorted(SUCCESS_STOP_REASONS))}\n"
        "  honest negative: --stop-reason formal_open_ledger (requires a host-authored\n"
        "    formal/terminal_state.json with terminal_state=open_ledger)\n"
        "  each --evidence-id <id> must resolve to a valid "
        f"{PROOF_ARTIFACT_DIRNAME}/<id>.json:\n"
        + json.dumps(proof_artifact_example(), indent=4)
        + "\n"
        f"  accepted artifact_type values: {', '.join(sorted(PROOF_ARTIFACT_TYPES))}\n"
        "\n"
        "workflow: stage-proof scaffolds an artifact, validate-proof-artifact checks one,\n"
        "append-iteration --dry-run runs every guard without writing anything.\n"
    )
    append = subparsers.add_parser(
        "append-iteration",
        help="append one iteration record",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=append_epilog,
    )
    append.add_argument("--dir", required=True, help="loop directory created by init")
    append.add_argument(
        "--mode",
        choices=sorted(VALID_MODES),
        required=True,
        help="iteration mode recorded in the ledger",
    )
    append.add_argument(
        "--objective", required=True, help="what this iteration set out to do"
    )
    append.add_argument(
        "--decision",
        choices=sorted(VALID_DECISIONS),
        required=True,
        help="continue, or a terminal decision that ends the loop",
    )
    append.add_argument(
        "--input-ref", action="append", help="input consulted this iteration (repeatable)"
    )
    append.add_argument(
        "--source-id", action="append", help="evidence source id checked (repeatable)"
    )
    append.add_argument(
        "--claim-id", action="append", help="material claim id staged (repeatable)"
    )
    append.add_argument(
        "--evidence-id",
        action="append",
        help=(
            "proof-artifact id; must resolve to "
            f"{PROOF_ARTIFACT_DIRNAME}/<id>.json (repeatable; see epilog)"
        ),
    )
    append.add_argument(
        "--guard-ref", action="append", help="guard or gate reference consulted (repeatable)"
    )
    append.add_argument(
        "--action-taken", action="append", help="action performed this iteration (repeatable)"
    )
    append.add_argument(
        "--output", default="", help="free-text result summary (put stop detail here)"
    )
    append.add_argument(
        "--remaining-gap", action="append", help="known open gap after this iteration (repeatable)"
    )
    append.add_argument(
        "--tokens", type=positive_int, default=0, help="tokens spent this iteration"
    )
    append.add_argument(
        "--usd", type=nonnegative_float, default=0.0, help="USD spent this iteration"
    )
    append.add_argument(
        "--wall-time-seconds",
        type=positive_int,
        default=0,
        help="wall time spent this iteration",
    )
    append.add_argument(
        "--stop-reason",
        default="",
        help=(
            "required for early stops; exactly one accepted token (see epilog) — "
            "free-text detail belongs in --output"
        ),
    )
    append.add_argument(
        "--goal-contribution",
        default="",
        help="optional goal_priority soft field (open vocabulary contribution label)",
    )
    append.add_argument(
        "--campaign-id",
        default="",
        help="optional goal_priority campaign id for this iteration",
    )
    append.add_argument(
        "--local-without-goal-delta",
        action="store_true",
        help="mark iteration as local residual without goal progress",
    )
    append.add_argument(
        "--local-without-goal-delta-tag",
        default="",
        help="optional advisory tag for local-without-goal-delta",
    )
    append.add_argument(
        "--residual-id",
        default="",
        help="optional residual inventory leaf id for this iteration",
    )
    append.add_argument(
        "--scope-lock",
        default="",
        help="optional scope lock (encoding_only|goal_sc|manuscript|mixed)",
    )
    append.add_argument(
        "--goal-contribution-detail",
        default="",
        help="optional free-text detail for goal_contribution",
    )
    append.add_argument(
        "--completed-summary",
        default="",
        help="plain-language summary of what this iteration completed",
    )
    append.add_argument(
        "--current-summary",
        default="",
        help="plain-language position of the research after this result",
    )
    append.add_argument(
        "--next-action",
        default="",
        help="proposed exact next action; Goal Focus review commits or rejects it",
    )
    append.add_argument(
        "--campaign-delta",
        choices=["none", "incremental", "substantial", "closed"],
        default="none",
    )
    append.add_argument(
        "--global-delta",
        choices=["none", "reduced", "satisfied"],
        default="none",
    )
    append.add_argument(
        "--obligation-id",
        action="append",
        help="goal-contract obligation changed by this iteration (repeatable)",
    )
    append.add_argument(
        "--compute-run",
        action="append",
        help=(
            "actual compute record as a JSON object or @JSON-file (repeatable); "
            f"keys: {', '.join(COMPUTE_RUN_KNOWN_KEYS)}; 'service' accepts "
            "local|hetzner|kaggle|modal|github-actions|other:<slug> ('backend' is "
            "accepted as a synonym), 'status' one of "
            f"{'|'.join(sorted(COMPUTE_RUN_STATUSES))} ('completed' maps to 'succeeded')"
        ),
    )
    append.add_argument(
        "--compute-none",
        action="store_true",
        help="explicitly record that no computation service was used",
    )
    append.add_argument(
        "--executor-provider",
        default="",
        help="executor provider; drive normally supplies this automatically",
    )
    append.add_argument(
        "--dry-run",
        action="store_true",
        help="run every append guard and report the record that would be written, without writing",
    )
    append.set_defaults(func=append_iteration)

    validate = subparsers.add_parser("validate", help="validate loop ledger files")
    validate.add_argument("--dir", required=True)
    validate.set_defaults(func=validate_command)

    validate_artifact = subparsers.add_parser(
        "validate-proof-artifact",
        help="check one proof artifact against the early-stop evidence contract",
    )
    validate_artifact.add_argument("--dir", required=True, help="loop directory")
    validate_artifact.add_argument(
        "--evidence-id", required=True, help=f"artifact id under {PROOF_ARTIFACT_DIRNAME}/"
    )
    validate_artifact.set_defaults(func=validate_proof_artifact_command)

    stage_proof = subparsers.add_parser(
        "stage-proof",
        help="copy a checked proof file into the loop dir and scaffold its artifact record",
    )
    stage_proof.add_argument("--dir", required=True, help="loop directory")
    stage_proof.add_argument("--id", required=True, help="new proof-artifact evidence id")
    stage_proof.add_argument("--file", required=True, help="proof file to copy in (source recorded)")
    stage_proof.add_argument(
        "--artifact-type",
        required=True,
        help=f"one of {', '.join(sorted(PROOF_ARTIFACT_TYPES))}",
    )
    stage_proof.add_argument(
        "--target", required=True, help="the theorem or claim this artifact proves"
    )
    stage_proof.add_argument(
        "--checker-name", required=True, help="checker that verified the file (e.g. lake)"
    )
    stage_proof.add_argument(
        "--checker-status",
        default="passed",
        help="checker outcome; only 'passed' artifacts satisfy the early-stop gate",
    )
    stage_proof.set_defaults(func=stage_proof_command)

    retract = subparsers.add_parser(
        "retract-iteration",
        help=(
            "remove the newest non-terminal ledger record (legacy mode only) and "
            "restore loop_state/budget/recovery coherently; audited in "
            f"{RETRACTIONS_FILENAME}"
        ),
    )
    retract.add_argument("--dir", required=True, help="loop directory")
    retract.add_argument(
        "--reason",
        required=True,
        help="why the record is being withdrawn (recorded verbatim in the audit)",
    )
    retract.add_argument(
        "--registry-dir",
        default=None,
        help="armed-loop registry override (default: AAS_AUTOLOOP_REGISTRY or the shared registry)",
    )
    retract.set_defaults(func=retract_iteration_command)

    status = subparsers.add_parser("status", help="summarize loop status")
    status.add_argument("--dir", required=True)
    status.set_defaults(func=status_command)

    selftest = subparsers.add_parser("selftest", help="run offline smoke test")
    selftest.set_defaults(func=selftest_command)

    def add_registry_args(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--registry-dir",
            default=None,
            help="autoloop registry root (default: $AAS_AUTOLOOP_REGISTRY or "
            "~/.local/share/ai-agents-skills/autoloop)",
        )

    watch = subparsers.add_parser(
        "watch",
        help="report loop progress: per-iteration, terminal, and driver-death events "
        "(also refreshes LIVE_STATUS.md and driver_logs/progress.jsonl)",
    )
    watch.add_argument("--dir", required=True)
    watch.add_argument(
        "--notify",
        default="auto",
        choices=["auto", "off", "zulip", "telegram", "both"],
        help="remote-bridge notify: auto (default when secrets configured), off, or a channel",
    )
    watch.add_argument(
        "--notify-cmd", default=None,
        help="shell command run per event with AUTOLOOP_EVENT/_DIR/_ITERATION/_DECISION/_TEXT in env (default: print JSON lines)",
    )
    watch.add_argument("--poll", type=int, default=60)
    watch.add_argument("--from-iteration", type=int, default=-1,
                       help="baseline iteration; report anything newer (default: current ledger tip)")
    watch.add_argument("--once", action="store_true", help="single poll cycle, then exit")
    watch.add_argument(
        "--log-dir",
        default=None,
        help="directory for progress.jsonl (default: <dir>/driver_logs)",
    )
    add_registry_args(watch)
    watch.set_defaults(func=watch_command)

    notify_event = subparsers.add_parser(
        "notify-event",
        help="emit one structured Goal/Completed/Current/Plan notification",
    )
    notify_event.add_argument("--dir", required=True)
    notify_event.add_argument("--event", required=True)
    notify_event.add_argument("--completed", default="")
    notify_event.add_argument("--current", default="")
    notify_event.add_argument("--plan", default="")
    notify_event.add_argument(
        "--iteration-status",
        choices=sorted(notify_v2.ITERATION_STATUSES),
        default="not_applicable",
    )
    notify_event.add_argument(
        "--review-status",
        choices=sorted(notify_v2.REVIEW_STATUSES),
        default="not_required",
    )
    notify_event.add_argument(
        "--loop-status",
        choices=sorted(notify_v2.LOOP_STATUSES),
        default=None,
    )
    notify_event.add_argument("--provider", default="")
    notify_event.add_argument(
        "--driver-agent",
        default="",
        help="driver agent/provider actually used (defaults to --provider when known)",
    )
    notify_event.add_argument(
        "--panel-agent",
        action="append",
        default=None,
        help="panel agent/provider that actually returned usable work (repeatable; omit = unreported)",
    )
    notify_event.add_argument(
        "--other-agent",
        action="append",
        default=None,
        help="other participating agent/provider (repeatable; omit = unreported)",
    )
    notify_event.add_argument("--compute-run", action="append")
    notify_event.add_argument("--compute-none", action="store_true")
    notify_event.add_argument("--finished-at", default="")
    notify_event.add_argument("--duration-seconds", type=nonnegative_float, default=None)
    notify_event.add_argument(
        "--notify",
        default="auto",
        choices=["auto", "off", "zulip", "telegram", "both"],
    )
    notify_event.add_argument("--quiet", action="store_true")
    notify_event.set_defaults(func=notify_event_command)

    arm = subparsers.add_parser("arm", help="register a loop as active (force-management)")
    arm.add_argument("--dir", required=True)
    arm.add_argument("--root", default=None, help="project root this loop governs (default: loop dir)")
    arm.add_argument("--pid", type=int, default=0, help="long-lived loop/driver pid for liveness (0 = heartbeat-only)")
    arm.add_argument("--driver", action="store_true",
                     help="mark the entry as owned by a headless driver; the interactive Stop-hook stands down while that pid is alive")
    arm.add_argument("--force", action="store_true")
    arm.add_argument(
        "--notify",
        default="auto",
        choices=["auto", "off", "zulip", "telegram", "both"],
        help="persist notify policy for this loop (auto = secrets-backed default when configured)",
    )
    add_registry_args(arm)
    arm.set_defaults(func=arm_loop)

    disarm = subparsers.add_parser("disarm", help="deregister an active loop (kill switch)")
    disarm.add_argument("--dir", default=None)
    disarm.add_argument("--run-id", default=None)
    add_registry_args(disarm)
    disarm.set_defaults(func=disarm_loop)

    active = subparsers.add_parser("active", help="list live active loops")
    add_registry_args(active)
    active.set_defaults(func=active_command)

    done = subparsers.add_parser("done", help="report whether a loop dir has met its stop condition")
    done.add_argument("--dir", required=True)
    done.set_defaults(func=done_command)

    formal_ts = subparsers.add_parser(
        "formal-terminal-state",
        help=(
            "host-run strict-gate verdict on the formal artifact "
            "(sorry_free_artifact | open_ledger | indeterminate); "
            "writes formal/terminal_state.json"
        ),
    )
    formal_ts.add_argument("--dir", required=True)
    formal_ts.add_argument("--root", default=None, help="project root (default: loop parent)")
    formal_ts.add_argument("--reason", default="", help="free-text reason recorded with the verdict")
    formal_ts.add_argument(
        "--no-typecheck",
        action="store_true",
        help="scan only; without a host build the verdict can never be sorry_free_artifact",
    )
    formal_ts.set_defaults(func=formal_terminal_state_command)

    hook = subparsers.add_parser(
        "hook-check",
        help="fail-open Stop-hook check; exit 2 only when an active loop for --root is not done",
    )
    hook.add_argument(
        "--root",
        default=None,
        help=(
            "current session project root "
            "(default: $GROK_WORKSPACE_ROOT, $CLAUDE_PROJECT_DIR, or cwd)"
        ),
    )
    add_registry_args(hook)
    hook.set_defaults(func=hook_check_command)

    agent_cmd = subparsers.add_parser(
        "agent-cmd",
        help="print the per-provider headless one-iteration command (offline; PATH probe only)",
    )
    agent_cmd.add_argument(
        "--provider",
        required=True,
        choices=sorted(PROVIDER_SPECS) + ["all"],
        help="install target whose iteration command to build, or `all` for the matrix",
    )
    agent_cmd.add_argument("--dir", required=True, help="loop directory the prompt references")
    agent_cmd.add_argument(
        "--print-prompt",
        action="store_true",
        help="include the standard one-iteration prompt in the output",
    )
    agent_cmd.set_defaults(func=agent_cmd_command)

    drive = subparsers.add_parser(
        "drive",
        help="cross-platform headless driver: run the iteration command per loop until done",
    )
    drive.add_argument("--dir", required=True)
    drive.add_argument("--root", default=None, help="project root this loop governs (default: loop dir)")
    drive.add_argument(
        "--cmd",
        default=None,
        help="iteration shell command run once per loop (mutually exclusive with --provider)",
    )
    drive.add_argument(
        "--provider",
        default=None,
        choices=sorted(PROVIDER_SPECS),
        help="build and run the standard headless iteration command for this install target",
    )
    drive.add_argument("--iteration-timeout", type=positive_int, default=1800)
    drive.add_argument("--max-failures", type=positive_int, default=3)
    drive.add_argument("--poll", type=nonnegative_float, default=5.0)
    drive.add_argument(
        "--quota-backoff",
        type=positive_int,
        default=900,
        help="seconds to wait after a detected credit/quota outage before retrying",
    )
    drive.add_argument(
        "--max-quota-waits",
        type=positive_int,
        default=0,
        help="max consecutive quota waits before giving up (0 = wait indefinitely, honoring pause-and-resume on credit exhaustion)",
    )
    drive.add_argument(
        "--max-review-waits",
        type=positive_int,
        default=0,
        help=(
            "max consecutive review/strategy wait laps (>=30s each) before the drive "
            "exits resumably with reason review_wait_exhausted (0 = wait indefinitely)"
        ),
    )
    drive.add_argument(
        "--log-dir",
        default=None,
        help="directory for per-iteration output logs (default: <dir>/driver_logs)",
    )
    drive.add_argument(
        "--notify",
        default="auto",
        choices=["auto", "off", "zulip", "telegram", "both"],
        help="remote-bridge notify (default auto: on when secrets configured; off to silence)",
    )
    drive.add_argument(
        "--notify-cmd",
        default=None,
        help="optional shell command per progress event (AUTOLOOP_EVENT/_DIR/_ITERATION/_DECISION/_TEXT env); "
        "prefer --notify; set AAS_ALLOW_RAW_NOTIFY_CMD=1 when using untrusted templates",
    )
    drive.add_argument(
        "--no-progress",
        action="store_true",
        help="disable LIVE_STATUS.md / progress.jsonl / stderr progress lines",
    )
    drive.add_argument(
        "--build-config-lock",
        action="store_true",
        help=(
            "treat any mid-run change to host-owned Lean build configuration "
            "(lakefile.lean, lakefile.toml, lake-manifest.json, lean-toolchain) "
            "as a failed cycle instead of record-only"
        ),
    )
    drive.add_argument(
        "--panel",
        default="auto",
        choices=["auto", "on", "off"],
        help=(
            "host-owned multi-agent panel phases around each iteration "
            "(auto = panel.json / loop_state.standing_orders.panel / AAS_AUTOLOOP_PANEL; "
            "on = always; off = never). Primary agent must not nest panel CLIs."
        ),
    )
    add_formal_policy_args(drive)
    add_registry_args(drive)
    drive.set_defaults(func=drive_command)

    panel = subparsers.add_parser(
        "panel",
        help="host-owned multi-agent panel dispatch (hybrid parent; offline smoke or phase run)",
    )
    panel.add_argument("--dir", default=None, help="loop directory (for config + iter layout)")
    panel.add_argument("--root", default=None, help="project root for child cwd (default: --dir or cwd)")
    panel.add_argument(
        "--phase",
        choices=["strategy_review", "target_advice", "result_review", "smoke"],
        default=None,
        help="panel phase (or use --smoke)",
    )
    panel.add_argument("--smoke", action="store_true", help="ping configured providers")
    panel.add_argument("--iter-dir", default=None, help="iterations/iterNNN directory")
    panel.add_argument("--prompt-file", default=None, help="prompt file (optional; auto-built if omitted)")
    panel.add_argument("--prompt", default="", help="inline prompt")
    panel.add_argument(
        "--providers",
        default=None,
        help="comma-separated providers (default from panel.json / standing orders)",
    )
    panel.add_argument("--timeout", type=int, default=0, help="per-provider timeout seconds")
    panel.set_defaults(func=panel_command)
    return parser


def parse_explicit_provider_roster(raw: str) -> list[str]:
    """Split an explicit ``--providers`` value, refusing one that names nobody.

    ``--providers ','`` parses to an empty list, and every panel entry point
    reads an empty roster as "no roster supplied" and substitutes the default
    one. The dispatch then goes to providers the operator never asked for, and
    ``panel`` prints a ``results`` map keyed by the empty roster, so the report
    describes nobody while the run described somebody. Refuse instead: passing
    the flag and naming no provider is always a mistake.
    """
    roster = [item.strip() for item in str(raw).split(",") if item.strip()]
    if not roster:
        raise ValueError(
            f"--providers {raw!r} names no provider; omit the flag to use the "
            "configured default roster"
        )
    return roster


def panel_command(args: argparse.Namespace) -> dict[str, Any]:
    """CLI entry for host-owned panel dispatch (does not start drive)."""
    root = Path(args.root).expanduser().resolve() if args.root else None
    run_dir = Path(args.dir).expanduser().resolve() if args.dir else None
    if root is None:
        root = run_dir if run_dir is not None else Path.cwd().resolve()
    if run_dir is None:
        run_dir = root
    cfg = load_panel_config(run_dir)
    if args.providers:
        try:
            providers = parse_explicit_provider_roster(args.providers)
        except ValueError as exc:
            return {"status": "failed", "action": "panel", "error": str(exc)}
    else:
        providers = list(cfg.get("providers") or ["codex", "claude", "codewhale"])
    if args.smoke or args.phase == "smoke":
        timeout = args.timeout or int((cfg.get("timeouts") or {}).get("smoke", 120))
        summary = panel_smoke(root, providers=providers, timeout_s=timeout)
        return {
            "status": "ok" if summary.get("all_invited_usable") or summary.get("panel_content_pass") else "failed",
            "action": "panel_smoke",
            "usable_providers": summary.get("usable_providers"),
            "results": {
                p: (summary.get("results") or {}).get(p, {}).get("status")
                for p in providers
            },
            "summary": summary,
        }
    phase = args.phase
    if not phase:
        return {
            "status": "failed",
            "action": "panel",
            "error": "provide --phase strategy_review|target_advice|result_review or --smoke",
        }
    prompt = args.prompt or ""
    if args.prompt_file:
        prompt = _read_contained_regular_text(
            run_dir,
            Path(args.prompt_file),
            max_bytes=2_000_000,
        )
    iter_dir = Path(args.iter_dir).expanduser().resolve() if args.iter_dir else None
    timeout = args.timeout or None
    summary = run_panel_phase_for_drive(
        run_dir,
        root,
        phase,
        iter_dir=iter_dir,
        prompt=prompt or None,
        providers=providers,
        timeout_s=timeout,
    )
    return {
        "status": "ok" if summary.get("panel_content_pass") else "failed",
        "action": f"panel_{phase}",
        "usable_providers": summary.get("usable_providers"),
        "iter_dir": summary.get("iter_dir"),
        "summary": summary,
    }


def main(argv: list[str] | None = None) -> int:
    # Force UTF-8 stdio so non-ASCII payloads (e.g. research text, provider
    # output) never crash JSON emission under a legacy Windows cp1252 console.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    command = getattr(args, "command", None)
    try:
        result = args.func(args)
    except Exception as exc:  # noqa: BLE001 - CLI should return structured failure.
        result = {"status": "failed", "error": str(exc)}
        if isinstance(exc, GuardError):
            # Merge structured hints without ever touching the stable keys above.
            for key, value in exc.payload.items():
                result.setdefault(key, value)
        print(json.dumps(result, indent=2, sort_keys=True), file=sys.stdout)
        # The Stop hook must fail open: never block turn-end on an internal error.
        return 0 if command == "hook-check" else 1
    if command == "hook-check":
        if result.get("block"):
            sys.stderr.write(
                (result.get("message") or "Autoloop active and not finished: continue the next iteration now.")
                + "\n"
            )
            return 2
        return 0
    if command == "drive":
        print(json.dumps(result, indent=2, sort_keys=True), file=sys.stdout)
        return int(result.get("exit_code", 0))
    print(json.dumps(result, indent=2, sort_keys=True), file=sys.stdout)
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
