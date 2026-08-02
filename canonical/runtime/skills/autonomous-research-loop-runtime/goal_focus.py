"""Goal-Focus v2: revisioned research-goal and direction control.

The module is intentionally independent from the autonomous-loop CLI.  It
provides pure loading, validation, scoring and rendering helpers plus explicit
transactional mutation functions for runtime integration.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Mapping, Sequence

from state_transaction import (
    RevisionConflict,
    TransactionError,
    commit_transaction,
    recover_transactions,
)

try:
    import anti_false_consensus as afc
    import negative_space as ns
except ImportError:  # pragma: no cover - package-relative install layouts
    from . import anti_false_consensus as afc  # type: ignore
    from . import negative_space as ns  # type: ignore

GOAL_CONTRACT_FILE = "goal_contract.json"
APPROACH_REGISTRY_FILE = "approach_registry.json"
CURRENT_PLAN_FILE = "current_plan.json"
DIRECTION_DECISIONS_FILE = "direction_decisions.jsonl"
PENDING_CANDIDATE_FILE = "iteration_candidate.json"
CANDIDATE_QUARANTINE_FILE = "candidate_quarantine.json"
ITERATION_DISPATCH_FILE = "iteration_dispatch.json"
MIGRATION_CLAIM_FILE = ".goal_focus_migration.claim"
MIGRATION_BACKUP_SCHEMA = "goal_focus_migration_backup.v1"

LEGACY_MIGRATION_SOURCE_FILES = (
    "goal_priority.json",
    "loop_state.json",
    "budget.json",
    "iterations.jsonl",
    "recovery.md",
    "driver_logs/goal_priority_hard_replan.json",
)

GOAL_CONTRACT_SCHEMA = "goal_contract.v2"
APPROACH_REGISTRY_SCHEMA = "approach_registry.v2"
CURRENT_PLAN_SCHEMA = "current_plan.v2"
DIRECTION_DECISION_SCHEMA = "direction_decision.v2"
ITERATION_CANDIDATE_SCHEMA = "iteration_candidate.v1"
CANDIDATE_QUARANTINE_SCHEMA = "candidate_quarantine.v1"
RESULT_REVIEW_SCHEMA = "result_review.v1"
EVIDENCE_ARTIFACT_SCHEMA = "goal_focus_evidence.v1"
PROVIDER_RESOURCE_ATTESTATION_SCHEMA = "provider_resource_attestation.v1"
HOST_REVIEWED_GOAL_SUCCESS_REASON = "goal_obligations_satisfied_after_review"
MAX_EVIDENCE_ARTIFACT_BYTES = 64_000
MAX_EVIDENCE_TOTAL_BYTES = 192_000
EVIDENCE_ROOT_PARTS = (".goal_focus", "evidence")
_SAFE_EVIDENCE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SENSITIVE_EVIDENCE_COMPONENT = re.compile(
    r"(?i)(?:^|[._-])(?:env|secret|secrets|credential|credentials|token|tokens|"
    r"api[_-]?key|auth|authorization|password|passwd|private|ssh|config|"
    r"id[_-]?(?:rsa|dsa|ecdsa|ed25519))(?:$|[._-])"
)
_SENSITIVE_EVIDENCE_SUFFIXES = {
    ".env",
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
}

ENFORCEMENT_MODES = frozenset({"off", "monitor", "enforce"})
PLAN_STATES = frozenset({"provisional", "active", "needs_replan", "terminal"})
APPROACH_STATUSES = frozenset({"eligible", "parked", "blocked", "closed", "succeeded"})
CAMPAIGN_STATUSES = frozenset(
    {"eligible", "active", "open", "parked", "blocked", "closed", "succeeded"}
)
ELIGIBLE_CAMPAIGN_STATUSES = frozenset({"", "eligible", "active", "open"})
OBLIGATION_KINDS = frozenset({"supporting", "bridge", "terminal"})
OBLIGATION_STATUSES = frozenset({"open", "partial", "satisfied", "blocked", "closed"})
CAMPAIGN_DELTAS = frozenset({"none", "incremental", "substantial", "closed"})
GLOBAL_DELTAS = frozenset({"none", "reduced", "satisfied"})
COMPUTE_RECORDING_STATUSES = frozenset({"explicit", "unreported"})
COMPUTE_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled", "unknown"})
KNOWN_COMPUTE_SERVICES = frozenset(
    {"local", "hetzner", "kaggle", "modal", "github-actions"}
)
_SAFE_CUSTOM_COMPUTE_SERVICE = re.compile(r"^other:[a-z0-9][a-z0-9-]{0,47}$")
_COMPUTE_RUN_FIELDS = frozenset(
    {
        "service",
        "status",
        "job_ref",
        "detail",
        "started_at",
        "finished_at",
        "duration_seconds",
    }
)

BENEFIT_WEIGHTS = {
    "goal_resolution": 5,
    "information_gain": 3,
    "option_value": 2,
    "diversity": 1,
}
PENALTY_WEIGHTS = {
    "execution_cost": 2,
    "verification_cost": 2,
    "bridge_debt": 4,
    "dependency_risk": 3,
    "redundancy": 1,
}

_BAND_VALUES = {
    "none": 0.0,
    "very_low": 0.5,
    "low": 1.0,
    "medium": 2.0,
    "high": 3.0,
    "very_high": 4.0,
}
_MANAGED_START = "<!-- goal-focus-managed:start -->"
_MANAGED_END = "<!-- goal-focus-managed:end -->"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _compact_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _clean_text(value: Any, limit: int = 10000) -> str:
    return str(value or "").strip()[:limit]


def _finite_nonnegative_amount(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite non-negative number")
    try:
        amount = float(value or 0.0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite non-negative number") from exc
    if not math.isfinite(amount) or amount < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return amount


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "\x1f".join(str(part or "") for part in parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:20]}"


def _object_fingerprint(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def candidate_fingerprint(candidate: Mapping[str, Any]) -> str:
    """Return the canonical content hash reviewers must bind to."""

    return _object_fingerprint(candidate)


def strategy_authority_snapshot(run_dir: str | Path) -> dict[str, Any]:
    """Capture the exact authority bytes and objects shown to strategy reviewers."""

    root = Path(run_dir)

    def read_one(filename: str) -> tuple[dict[str, Any], str]:
        payload = _read_regular_bytes(root / filename)
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot snapshot invalid authority file {filename}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"cannot snapshot non-object authority file {filename}")
        return value, hashlib.sha256(payload).hexdigest()

    contract, contract_source_hash = read_one(GOAL_CONTRACT_FILE)
    registry, registry_source_hash = read_one(APPROACH_REGISTRY_FILE)
    plan, plan_source_hash = read_one(CURRENT_PLAN_FILE)
    return {
        "schema_version": "strategy_authority_snapshot.v1",
        "goal_revision": contract.get("goal_revision"),
        "registry_revision": registry.get("registry_revision"),
        "plan_revision": plan.get("plan_revision"),
        "goal_contract_fingerprint": _object_fingerprint(contract),
        "approach_registry_fingerprint": _object_fingerprint(registry),
        "current_plan_fingerprint": _object_fingerprint(plan),
        "goal_contract_source_sha256": contract_source_hash,
        "approach_registry_source_sha256": registry_source_hash,
        "current_plan_source_sha256": plan_source_hash,
        "goal_contract": contract,
        "approach_registry": registry,
        "current_plan": plan,
    }


def strategy_authority_binding(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a captured snapshot and return its compact commit binding."""

    if snapshot.get("schema_version") != "strategy_authority_snapshot.v1":
        raise ValueError("strategy review lacks a valid authority snapshot")
    contract = snapshot.get("goal_contract")
    registry = snapshot.get("approach_registry")
    plan = snapshot.get("current_plan")
    if not all(isinstance(value, dict) for value in (contract, registry, plan)):
        raise ValueError("strategy authority snapshot lacks complete authority objects")
    expected = {
        "goal_revision": contract.get("goal_revision"),
        "registry_revision": registry.get("registry_revision"),
        "plan_revision": plan.get("plan_revision"),
        "goal_contract_fingerprint": _object_fingerprint(contract),
        "approach_registry_fingerprint": _object_fingerprint(registry),
        "current_plan_fingerprint": _object_fingerprint(plan),
    }
    for field, value in expected.items():
        if snapshot.get(field) != value:
            raise ValueError(f"strategy authority snapshot {field} is inconsistent")
    binding = {
        "schema_version": "strategy_authority_binding.v1",
        **expected,
    }
    for field in (
        "goal_contract_source_sha256",
        "approach_registry_source_sha256",
        "current_plan_source_sha256",
    ):
        value = _clean_text(snapshot.get(field))
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError(f"strategy authority snapshot {field} is invalid")
        binding[field] = value
    return binding


def _plan_authority_fingerprint(plan: Mapping[str, Any]) -> str:
    """Bind all dispatch-relevant plan fields to one reviewed decision row."""
    fields = (
        "enforcement_mode",
        "plan_revision",
        "goal_revision",
        "registry_revision",
        "state",
        "campaign_id",
        "approach_id",
        "objective_id",
        "target_obligation_ids",
        "residual_id",
        "scope_lock",
        "next_action",
        "expected_artifacts",
        "compute_policy",
        "dispatch_provider_family",
        "dispatch_provider_attestation",
        "falsifier",
        "horizon_iterations",
        "valid_through_iteration",
        "trip_wires",
        "selection",
    )
    payload = {field: copy.deepcopy(plan.get(field)) for field in fields}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plan_authority_fingerprint(plan: Mapping[str, Any]) -> str:
    """Public stable fingerprint for host dispatch pinning."""

    return _plan_authority_fingerprint(plan)


def _open_directory_nofollow(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(absolute.anchor or os.sep, flags)
    try:
        for component in absolute.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


def _ensure_private_evidence_directory(run_dir: Path, candidate_id: str) -> str:
    """Create the host-owned per-dispatch evidence directory without symlink walks."""

    candidate = _clean_text(candidate_id)
    if not _SAFE_EVIDENCE_COMPONENT.fullmatch(candidate):
        raise ValueError("candidate id is unsafe for the evidence directory")
    relative_parts = (*EVIDENCE_ROOT_PARTS, candidate)
    if os.name == "nt":  # pragma: no cover - Windows lifecycle uses this fallback
        current = Path(os.path.abspath(run_dir))
        for index, component in enumerate(relative_parts):
            current /= component
            try:
                os.mkdir(current, 0o700)
            except FileExistsError:
                pass
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"evidence directory is not a real directory: {current}")
        return Path(*relative_parts).as_posix()

    root_fd = _open_directory_nofollow(run_dir)
    fd = root_fd
    try:
        for index, component in enumerate(relative_parts):
            try:
                os.mkdir(component, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            next_fd = os.open(
                component,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=fd,
            )
            info = os.fstat(next_fd)
            if not stat.S_ISDIR(info.st_mode):
                os.close(next_fd)
                raise ValueError(
                    f"evidence directory component is not a directory: {component}"
                )
            if index >= 0 and (
                int(info.st_uid) != int(os.geteuid())
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                os.close(next_fd)
                raise ValueError(
                    f"evidence directory must be private and host-owned: {component}"
                )
            if fd != root_fd:
                os.close(fd)
            fd = next_fd
        return Path(*relative_parts).as_posix()
    finally:
        if fd != root_fd:
            os.close(fd)
        os.close(root_fd)


def _read_regular_bytes(
    path: Path,
    *,
    max_bytes: int = 16_000_000,
    require_single_link: bool = False,
    require_current_owner: bool = False,
) -> bytes:
    """Read a bounded regular authority file without following symlinks."""

    absolute = Path(os.path.abspath(path))
    for component in [*reversed(absolute.parent.parents), absolute.parent]:
        info = os.lstat(component)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"authority parent is not a real directory: {component}")
    if os.name == "nt":
        info = os.lstat(absolute)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ValueError(f"authority input is not a regular file: {absolute}")
        if require_single_link and int(getattr(info, "st_nlink", 1)) != 1:
            raise ValueError(f"authority input must have exactly one link: {absolute}")
        if (
            require_current_owner
            and hasattr(os, "geteuid")
            and int(getattr(info, "st_uid", -1)) != int(os.geteuid())
        ):
            raise ValueError(f"authority input is not host-owned: {absolute}")
        payload = absolute.read_bytes()
    else:
        dir_fd = _open_directory_nofollow(absolute.parent)
        try:
            fd = os.open(
                absolute.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=dir_fd,
            )
        finally:
            os.close(dir_fd)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size > max_bytes
                or (
                    require_single_link
                    and int(getattr(info, "st_nlink", 1)) != 1
                )
            ):
                raise ValueError(f"authority input is unsafe or oversized: {absolute}")
            if require_current_owner and int(info.st_uid) != int(os.geteuid()):
                raise ValueError(f"authority input is not host-owned: {absolute}")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                payload = handle.read(max_bytes + 1)
        finally:
            os.close(fd)
    if len(payload) > max_bytes:
        raise ValueError(f"authority input exceeds {max_bytes} bytes: {absolute}")
    return payload


def _read_regular_text(path: Path, *, max_bytes: int = 16_000_000) -> str:
    return _read_regular_bytes(path, max_bytes=max_bytes).decode("utf-8")


def _decode_object_bytes(path: Path, payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _read_object_snapshot(
    path: Path, *, required: bool
) -> tuple[dict[str, Any], str | None]:
    """Parse and hash one bounded no-follow byte snapshot."""

    try:
        payload = _read_regular_bytes(path)
    except FileNotFoundError:
        if required:
            raise
        return {}, None
    return _decode_object_bytes(path, payload), hashlib.sha256(payload).hexdigest()


def _read_object(path: Path, *, required: bool) -> dict[str, Any]:
    return _read_object_snapshot(path, required=required)[0]


def _migration_claim_snapshot(
    run_dir: Path,
) -> tuple[dict[str, Any], tuple[int, int]]:
    path = run_dir / MIGRATION_CLAIM_FILE
    if os.name == "nt":  # pragma: no cover - Windows lifecycle fallback
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ValueError("migration claim is not a regular file")
        payload = _read_regular_bytes(
            path,
            max_bytes=4096,
            require_single_link=True,
            require_current_owner=True,
        )
        after = os.lstat(path)
        identity = (int(before.st_dev), int(before.st_ino))
        if identity != (int(after.st_dev), int(after.st_ino)):
            raise ValueError("migration claim changed while being read")
    else:
        directory_fd = _open_directory_nofollow(run_dir)
        try:
            file_fd = os.open(
                MIGRATION_CLAIM_FILE,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
        finally:
            os.close(directory_fd)
        try:
            before = os.fstat(file_fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or int(before.st_nlink) != 1
                or int(before.st_uid) != int(os.geteuid())
                or stat.S_IMODE(before.st_mode) & 0o077
                or before.st_size > 4096
            ):
                raise ValueError("migration claim is not a private host-owned file")
            chunks: list[bytes] = []
            observed = 0
            while observed <= 4096:
                chunk = os.read(file_fd, 4097 - observed)
                if not chunk:
                    break
                chunks.append(chunk)
                observed += len(chunk)
            payload = b"".join(chunks)
            after = os.fstat(file_fd)
            before_identity = (
                int(before.st_dev),
                int(before.st_ino),
                int(before.st_size),
                int(before.st_mtime_ns),
            )
            after_identity = (
                int(after.st_dev),
                int(after.st_ino),
                int(after.st_size),
                int(after.st_mtime_ns),
            )
            if (
                len(payload) > 4096
                or before_identity != after_identity
                or int(after.st_nlink) != 1
                or int(after.st_uid) != int(os.geteuid())
                or stat.S_IMODE(after.st_mode) & 0o077
            ):
                raise ValueError("migration claim changed while being read")
            identity = before_identity[:2]
        finally:
            os.close(file_fd)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("migration claim is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("migration claim must contain one object")
    return value, identity


def _validate_migration_apply_guard(
    run_dir: Path, guard: Mapping[str, Any] | None
) -> None:
    if not isinstance(guard, Mapping) or guard.get("schema_version") != (
        "goal_focus_migration_guard.v1"
    ):
        raise ValueError(
            "migration apply requires a verified host migration claim guard"
        )
    canonical_root = str(Path(os.path.realpath(os.path.abspath(run_dir))))
    if _clean_text(guard.get("run_dir")) != canonical_root:
        raise ValueError("migration guard belongs to a different loop")
    if guard.get("live_driver_count") != 0:
        raise ValueError("migration guard does not prove a quiescent driver registry")
    claim, identity = _migration_claim_snapshot(run_dir)
    declared_identity = guard.get("claim_identity")
    if (
        not isinstance(declared_identity, list)
        or len(declared_identity) != 2
        or tuple(int(item) for item in declared_identity) != identity
    ):
        raise ValueError("migration claim identity changed before apply")
    nonce = _clean_text(claim.get("nonce"))
    if (
        not nonce
        or nonce != _clean_text(guard.get("nonce"))
        or claim.get("pid") != os.getpid()
        or guard.get("claim_pid") != os.getpid()
    ):
        raise ValueError("migration claim ownership proof is invalid")


def _validate_migration_backup_parent(run_dir: Path) -> None:
    """Reject a planted or shared legacy backup namespace before commit."""

    parent = run_dir / ".goal_focus_backups"
    try:
        info = os.lstat(parent)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("migration backup namespace is not a real directory")
    if os.name == "posix" and (
        int(info.st_uid) != int(os.geteuid())
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ValueError("migration backup namespace is not private and host-owned")


def _decode_jsonl_bytes(path: Path, payload: bytes) -> list[dict[str, Any]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path.name} is not UTF-8: {exc}") from exc
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name} line {index} is invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path.name} line {index} must contain a JSON object")
        rows.append(value)
    return rows


def _read_jsonl_snapshot(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    """Parse and hash one optional JSONL no-follow byte snapshot."""

    try:
        payload = _read_regular_bytes(path)
    except FileNotFoundError:
        return [], None
    return _decode_jsonl_bytes(path, payload), hashlib.sha256(payload).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return _read_jsonl_snapshot(path)[0]


def _positive_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value if value >= 0 else default


def _require_nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def default_goal_contract(goal: str, success_criteria: str | Sequence[str]) -> dict[str, Any]:
    if isinstance(success_criteria, str):
        criteria = [success_criteria] if success_criteria.strip() else []
    else:
        criteria = [_clean_text(item) for item in success_criteria if _clean_text(item)]
    obligations: dict[str, dict[str, Any]] = {}
    success_rows: list[dict[str, Any]] = []
    for index, criterion in enumerate(criteria, start=1):
        oid = f"GOAL-SC-{index}"
        success_rows.append({"id": oid, "description": criterion})
        obligations[oid] = {
            "id": oid,
            "kind": "terminal",
            "description": criterion,
            "status": "open",
            "depends_on": [],
            "evidence_refs": [],
        }
    now = utc_now()
    main_goal = _clean_text(goal)
    return {
        "schema_version": GOAL_CONTRACT_SCHEMA,
        "goal_revision": 1,
        "title": (main_goal.splitlines()[0][:120] if main_goal else "Autonomous research goal"),
        "goal": main_goal,
        "success_criteria": success_rows,
        "scope": {"included": [], "excluded": []},
        "insufficient_results": [],
        "obligations": obligations,
        "created_at": now,
        "updated_at": now,
    }


def default_approach_registry() -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": APPROACH_REGISTRY_SCHEMA,
        "registry_revision": 1,
        "campaigns": {},
        "created_at": now,
        "updated_at": now,
    }


def default_current_plan(
    *, goal_revision: int = 1, registry_revision: int = 1, mode: str = "enforce"
) -> dict[str, Any]:
    if mode not in ENFORCEMENT_MODES:
        raise ValueError(f"mode must be one of {sorted(ENFORCEMENT_MODES)}")
    goal_revision = _require_nonnegative_int(goal_revision, "goal_revision")
    registry_revision = _require_nonnegative_int(
        registry_revision, "registry_revision"
    )
    now = utc_now()
    return {
        "schema_version": CURRENT_PLAN_SCHEMA,
        "plan_revision": 1,
        "goal_revision": goal_revision,
        "registry_revision": registry_revision,
        "enforcement_mode": mode,
        "state": "needs_replan",
        "campaign_id": "",
        "approach_id": "",
        "objective_id": "",
        "target_obligation_ids": [],
        "residual_id": "",
        "scope_lock": "",
        "next_action": "Run a structured strategy review and commit one bounded next action.",
        "expected_artifacts": [],
        "compute_policy": {"allowed_services": [], "forbidden_services": []},
        "dispatch_provider_family": "",
        "dispatch_provider_attestation": {},
        "falsifier": "",
        "horizon_iterations": 1,
        "valid_through_iteration": None,
        "trip_wires": [],
        "trip_wires_triggered": [],
        "selection": {},
        "selected_at": None,
        "created_at": now,
        "updated_at": now,
    }


def load_goal_contract(run_dir: str | Path, required: bool = True) -> dict[str, Any]:
    return _read_object(Path(run_dir) / GOAL_CONTRACT_FILE, required=required)


def load_approach_registry(run_dir: str | Path, required: bool = True) -> dict[str, Any]:
    return _read_object(Path(run_dir) / APPROACH_REGISTRY_FILE, required=required)


def load_current_plan(run_dir: str | Path, required: bool = True) -> dict[str, Any]:
    return _read_object(Path(run_dir) / CURRENT_PLAN_FILE, required=required)


def load_direction_decisions(run_dir: str | Path) -> list[dict[str, Any]]:
    return _read_jsonl(Path(run_dir) / DIRECTION_DECISIONS_FILE)


def load_pending_candidate(run_dir: str | Path) -> dict[str, Any] | None:
    path = Path(run_dir) / PENDING_CANDIDATE_FILE
    return _read_object(path, required=False) or None


def load_candidate_quarantine(run_dir: str | Path) -> dict[str, Any] | None:
    path = Path(run_dir) / CANDIDATE_QUARANTINE_FILE
    return _read_object(path, required=False) or None


def load_goal_focus(run_dir: str | Path) -> dict[str, Any]:
    root = Path(run_dir)
    return {
        "contract": load_goal_contract(root, required=False),
        "registry": load_approach_registry(root, required=False),
        "plan": load_current_plan(root, required=False),
        "decisions": load_direction_decisions(root),
        "pending_candidate": load_pending_candidate(root),
        "candidate_quarantine": load_candidate_quarantine(root),
        "iteration_dispatch": _read_object(
            root / ITERATION_DISPATCH_FILE, required=False
        )
        or None,
    }


def goal_focus_mode(run_dir: str | Path) -> str:
    root = Path(run_dir)
    path = root / CURRENT_PLAN_FILE
    if not path.exists():
        partial = [
            name
            for name in (
                GOAL_CONTRACT_FILE,
                APPROACH_REGISTRY_FILE,
                DIRECTION_DECISIONS_FILE,
                PENDING_CANDIDATE_FILE,
                CANDIDATE_QUARANTINE_FILE,
                ITERATION_DISPATCH_FILE,
            )
            if (root / name).exists()
        ]
        if partial:
            raise ValueError(
                f"partial Goal-Focus authority lacks {CURRENT_PLAN_FILE}: "
                + ", ".join(partial)
            )
        return "off"
    plan = load_current_plan(run_dir, required=True)
    mode = _clean_text(plan.get("enforcement_mode")).lower()
    if mode not in ENFORCEMENT_MODES:
        raise ValueError(
            f"{CURRENT_PLAN_FILE} enforcement_mode must be one of {sorted(ENFORCEMENT_MODES)}"
        )
    mode_error = _mode_authority_error(plan, load_direction_decisions(root))
    if mode_error:
        raise ValueError(mode_error)
    return mode


def is_goal_focus_enabled(run_dir: str | Path) -> bool:
    return goal_focus_mode(run_dir) in {"monitor", "enforce"}


def _campaigns(registry: Mapping[str, Any]) -> Mapping[str, Any]:
    value = registry.get("campaigns")
    return value if isinstance(value, dict) else {}


def _approach_rows(registry: Mapping[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for campaign_id, raw_campaign in _campaigns(registry).items():
        if not isinstance(raw_campaign, dict):
            continue
        approaches = raw_campaign.get("approaches")
        if not isinstance(approaches, dict):
            continue
        for approach_id, raw_approach in approaches.items():
            if isinstance(raw_approach, dict):
                approach = dict(raw_approach)
                approach["id"] = str(approach_id)
                approach["campaign_id"] = str(campaign_id)
                rows.append((str(campaign_id), str(approach_id), approach))
    return rows


def _find_approach(
    registry: Mapping[str, Any], campaign_id: str, approach_id: str
) -> dict[str, Any] | None:
    campaign = _campaigns(registry).get(campaign_id)
    if not isinstance(campaign, dict):
        return None
    approaches = campaign.get("approaches")
    if not isinstance(approaches, dict):
        return None
    value = approaches.get(approach_id)
    return dict(value) if isinstance(value, dict) else None


def _approach_ineligibility(
    registry: Mapping[str, Any],
    campaign_id: str,
    approach_id: str,
    *,
    run_dir: str | Path | None = None,
) -> str:
    """Return a stable exclusion reason, or an empty string when dispatchable."""
    campaign = _campaigns(registry).get(campaign_id)
    if not isinstance(campaign, dict):
        return "unknown_campaign"
    campaign_status = _clean_text(campaign.get("status")).lower()
    if campaign_status not in ELIGIBLE_CAMPAIGN_STATUSES:
        return f"campaign_status:{campaign_status or 'unknown'}"
    approach = _find_approach(registry, campaign_id, approach_id)
    if approach is None:
        return "unknown_approach"
    status = _clean_text(approach.get("status") or "eligible").lower()
    if status != "eligible":
        return f"status:{status}"
    status_by_id = {
        aid: _clean_text(row.get("status") or "eligible").lower()
        for _, aid, row in _approach_rows(registry)
    }
    missing = [
        str(dep)
        for dep in approach.get("dependencies") or []
        if status_by_id.get(str(dep)) != "succeeded"
    ]
    if missing:
        return "unsatisfied_dependencies:" + ",".join(sorted(missing))
    if approach.get("compute_allowed") is False:
        return "compute_forbidden"
    if approach.get("stale") is True:
        return "stale_estimate"
    if run_dir is not None:
        ns_reason = ns.approach_blocked_by_negative_space(run_dir, approach_id)
        if ns_reason:
            return ns_reason
    return ""


def _compute_services(value: Any) -> set[str]:
    if isinstance(value, str):
        raw_values = [value]
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        return set()
    aliases = {
        "github_action": "github-actions",
        "github_actions": "github-actions",
        "github action": "github-actions",
        "github actions": "github-actions",
        "hetzner-cloud": "hetzner",
    }
    return {
        aliases.get(_clean_text(item).lower(), _clean_text(item).lower())
        for item in raw_values
        if _clean_text(item)
    }


def _policy_allowed(policy: Mapping[str, Any]) -> set[str]:
    allowed = _compute_services(policy.get("allowed_services") or policy.get("backends"))
    backend = _clean_text(policy.get("backend")).lower()
    if backend:
        allowed.update(_compute_services([backend]))
    return allowed


def _is_machine_gated_next_action(text: object) -> bool:
    """True when next_action looks like a formal edit + lake/axiom gate.

    Used to prefer registry text over panel inspect/sketch softening.
    """
    t = _clean_text(text).lower()
    if not t:
        return False
    if any(
        marker in t
        for marker in (
            "read-only inspect",
            "inspect only",
            "use no compute",
            "proof sketch alone",
        )
    ) and "lake" not in t:
        return False
    has_action = any(word in t for word in ("edit", "prove", "fill", "sorry", "formal-track"))
    has_gate = "lake" in t or "axiom" in t
    return has_action and has_gate


def _file_compute_allowlist(run_dir: Path) -> set[str]:
    """Non-empty structured allowlist from compute_policy.json, else empty."""
    policy_path = run_dir / "compute_policy.json"
    if not policy_path.exists():
        return set()
    document = _read_object(policy_path, required=True)
    external = document.get("policy") if isinstance(document.get("policy"), dict) else document
    if not isinstance(external, Mapping):
        return set()
    return _policy_allowed(external)


def _old_plan_compute_allowlist(old_plan: Mapping[str, Any]) -> set[str]:
    old_policy = old_plan.get("compute_policy")
    if not isinstance(old_policy, dict):
        return set()
    from_user = _compute_services(
        old_policy.get("user_allowed_services")
        or old_policy.get("pinned_allowed_services")
    )
    if from_user:
        return from_user
    return _policy_allowed(old_policy)


def _pinned_compute_policy(run_dir: Path, old_plan: Mapping[str, Any]) -> tuple[set[str], set[str], bool]:
    """Return structured user allow/deny pins and whether only free text exists.

    When ``compute_policy.json`` defines a non-empty structured allowlist, that
    file plus ``loop_state.standing_orders.compute`` are authoritative: the old
    plan's sticky ``user_allowed_services`` / ``forbidden_services`` are not
    inherited (operator re-pin can admit ``local`` again after a prior forbid).
    Without a structured file allowlist, legacy behavior still includes the old
    plan pins so unpinned loops do not silently open services.
    """
    allowed_sets: list[set[str]] = []
    forbidden: set[str] = set()
    free_text_only = False

    file_allowed = _file_compute_allowlist(run_dir)
    operator_file_authoritative = bool(file_allowed)

    if not operator_file_authoritative:
        old_policy = old_plan.get("compute_policy")
        if isinstance(old_policy, dict):
            old_pins = _compute_services(
                old_policy.get("user_allowed_services")
                or old_policy.get("pinned_allowed_services")
            )
            if old_pins:
                allowed_sets.append(old_pins)
            forbidden.update(_compute_services(old_policy.get("forbidden_services")))

    policy_path = run_dir / "compute_policy.json"
    if policy_path.exists():
        document = _read_object(policy_path, required=True)
        external = document.get("policy") if isinstance(document.get("policy"), dict) else document
        external_allowed = _policy_allowed(external) if isinstance(external, Mapping) else set()
        if external_allowed:
            allowed_sets.append(external_allowed)
        if isinstance(external, Mapping):
            forbidden.update(_compute_services(external.get("forbidden_services")))

    state = _read_object(run_dir / "loop_state.json", required=False)
    orders = state.get("standing_orders") if isinstance(state.get("standing_orders"), dict) else {}
    standing = orders.get("compute") if isinstance(orders, dict) else None
    if isinstance(standing, dict):
        standing_allowed = _policy_allowed(standing)
        if standing_allowed:
            allowed_sets.append(standing_allowed)
        forbidden.update(_compute_services(standing.get("forbidden_services")))
    elif isinstance(standing, (str, list, tuple)) and standing:
        free_text_only = True

    if not allowed_sets:
        return set(), forbidden, free_text_only
    pinned = set(allowed_sets[0])
    for allowed in allowed_sets[1:]:
        pinned &= allowed
    if not pinned:
        raise ValueError("structured compute allowlists have an empty intersection")
    pinned -= forbidden
    if not pinned:
        raise ValueError("all structured user-allowed compute services are forbidden")
    return pinned, forbidden, free_text_only


def _resolve_reviewed_compute_policy(
    run_dir: Path,
    old_plan: Mapping[str, Any],
    reviewed_policy: Any,
) -> dict[str, Any]:
    if reviewed_policy in (None, ""):
        requested: dict[str, Any] = {}
    elif isinstance(reviewed_policy, dict):
        requested = copy.deepcopy(reviewed_policy)
    else:
        raise ValueError("compute_policy must be an object")
    pinned, forbidden, free_text_only = _pinned_compute_policy(run_dir, old_plan)
    requested_allowed = _policy_allowed(requested)
    old_allowed = _old_plan_compute_allowlist(old_plan)
    if pinned and not requested_allowed:
        selected_allowed = set(pinned)
    elif pinned:
        widening = requested_allowed - pinned
        if widening:
            raise ValueError(
                "reviewed compute policy widens the user allowlist: "
                + ", ".join(sorted(widening))
            )
        # Re-stating the previous plan's allowlist must not permanently exclude
        # services newly admitted by operator file pins (e.g. local for lake).
        # Explicit candidate/approach narrowing to a proper subset other than
        # the old plan allowlist is preserved.
        if requested_allowed < pinned and (
            not old_allowed or requested_allowed == old_allowed
        ):
            selected_allowed = set(pinned)
        else:
            selected_allowed = requested_allowed
    elif requested_allowed and free_text_only:
        raise ValueError(
            "cannot validate a reviewed compute policy against free-text-only user standing orders; "
            "add a structured compute_policy.json allowlist first"
        )
    else:
        selected_allowed = requested_allowed
    if selected_allowed & forbidden:
        raise ValueError("reviewed compute policy selects a forbidden service")
    result = requested
    result.pop("backends", None)
    result.pop("backend", None)
    result["allowed_services"] = sorted(selected_allowed)
    result["forbidden_services"] = sorted(forbidden)
    if pinned:
        result["user_allowed_services"] = sorted(pinned)
        result["allowlist_source"] = "operator_structured_policy"
    return result


def _validate_plan_compute_policy(run_dir: Path, plan: Mapping[str, Any], errors: list[str]) -> None:
    try:
        pinned, forbidden, _ = _pinned_compute_policy(run_dir, plan)
    except (OSError, ValueError) as exc:
        errors.append(f"compute policy authority is invalid: {exc}")
        return
    policy = plan.get("compute_policy") if isinstance(plan.get("compute_policy"), dict) else {}
    selected = _policy_allowed(policy)
    if pinned and (not selected or not selected <= pinned):
        errors.append("current_plan compute_policy is missing or widens the structured user allowlist")
    if selected & forbidden:
        errors.append("current_plan compute_policy selects a forbidden service")


def _criterion_ids(contract: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for row in contract.get("success_criteria") or []:
        if isinstance(row, dict) and _clean_text(row.get("id")):
            ids.add(_clean_text(row.get("id")))
    return ids


def _validate_contract(contract: Mapping[str, Any], errors: list[str], warnings: list[str]) -> None:
    if contract.get("schema_version") != GOAL_CONTRACT_SCHEMA:
        errors.append(f"{GOAL_CONTRACT_FILE} schema_version must be {GOAL_CONTRACT_SCHEMA}")
    if _positive_int(contract.get("goal_revision")) < 1:
        errors.append("goal_contract goal_revision must be a positive integer")
    if not _clean_text(contract.get("goal")):
        errors.append("goal_contract goal must be non-empty")
    criteria = contract.get("success_criteria")
    if not isinstance(criteria, list) or not criteria:
        errors.append("goal_contract success_criteria must be a non-empty list")
        criteria = []
    criterion_ids: list[str] = []
    for index, row in enumerate(criteria):
        if not isinstance(row, dict):
            errors.append(f"success criterion {index + 1} must be an object")
            continue
        criterion_id = _clean_text(row.get("id"))
        if not criterion_id:
            errors.append(f"success criterion {index + 1} id must be non-empty")
        else:
            criterion_ids.append(criterion_id)
        if not _clean_text(row.get("description")):
            errors.append(f"success criterion {criterion_id or index + 1} description must be non-empty")
    duplicated_criteria = sorted(
        {criterion_id for criterion_id in criterion_ids if criterion_ids.count(criterion_id) > 1}
    )
    if duplicated_criteria:
        errors.append(
            "success criterion ids must be unique: " + ", ".join(duplicated_criteria)
        )
    obligations = contract.get("obligations")
    if not isinstance(obligations, dict):
        errors.append("goal_contract obligations must be an object")
        return
    seen: set[str] = set()
    for key, raw in obligations.items():
        if not isinstance(raw, dict):
            errors.append(f"obligation {key!r} must be an object")
            continue
        oid = _clean_text(raw.get("id") or key)
        if not oid or oid in seen:
            errors.append(f"obligation id is empty or duplicated: {oid!r}")
        seen.add(oid)
        if oid != str(key):
            errors.append(f"obligation key {key!r} must match its id {oid!r}")
        if raw.get("kind") not in OBLIGATION_KINDS:
            errors.append(f"obligation {oid} has invalid kind")
        if raw.get("status") not in OBLIGATION_STATUSES:
            errors.append(f"obligation {oid} has invalid status")
        deps = raw.get("depends_on") or []
        if not isinstance(deps, list):
            errors.append(f"obligation {oid} depends_on must be a list")
        elif any(not _clean_text(dep) for dep in deps) or len(
            {_clean_text(dep) for dep in deps}
        ) != len(deps):
            errors.append(f"obligation {oid} depends_on must contain unique non-empty ids")
        evidence_refs = raw.get("evidence_refs")
        if not isinstance(evidence_refs, list):
            errors.append(f"obligation {oid} evidence_refs must be a list")
        elif raw.get("status") in {"satisfied", "closed"} and not any(
            _clean_text(ref) for ref in evidence_refs
        ):
            errors.append(
                f"completed obligation {oid} requires at least one evidence_ref"
            )
    missing = _criterion_ids(contract) - seen
    if missing:
        errors.append("success criteria lack obligation nodes: " + ", ".join(sorted(missing)))
    for oid, raw in obligations.items():
        if not isinstance(raw, dict):
            continue
        for dep in raw.get("depends_on") or []:
            if str(dep) not in seen:
                errors.append(f"obligation {oid} depends on unknown obligation {dep}")
        if raw.get("status") in {"satisfied", "closed"}:
            incomplete = [
                str(dep)
                for dep in raw.get("depends_on") or []
                if not isinstance(obligations.get(str(dep)), dict)
                or obligations[str(dep)].get("status") not in {"satisfied", "closed"}
            ]
            if incomplete:
                errors.append(
                    f"completed obligation {oid} has incomplete dependencies: "
                    + ", ".join(sorted(incomplete))
                )
    # The dependency relation is authority, not documentation: reject both
    # trivial self-dependencies and longer cycles so a terminal obligation can
    # never be made vacuously reachable through a cyclic contract.
    graph = {
        str(oid): [str(dep) for dep in (raw.get("depends_on") or []) if str(dep) in seen]
        for oid, raw in obligations.items()
        if isinstance(raw, dict)
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(oid: str, trail: list[str]) -> None:
        if oid in visited:
            return
        if oid in visiting:
            cycle_start = trail.index(oid) if oid in trail else 0
            cycle = trail[cycle_start:] + [oid]
            errors.append("obligation dependency cycle: " + " -> ".join(cycle))
            return
        visiting.add(oid)
        for dep in graph.get(oid, []):
            visit(dep, trail + [oid])
        visiting.remove(oid)
        visited.add(oid)

    for oid in sorted(graph):
        visit(oid, [])
    if not any(isinstance(row, dict) and row.get("kind") == "terminal" for row in obligations.values()):
        warnings.append("goal_contract has no terminal obligation")


def _validate_registry(registry: Mapping[str, Any], errors: list[str], warnings: list[str]) -> None:
    if registry.get("schema_version") != APPROACH_REGISTRY_SCHEMA:
        errors.append(f"{APPROACH_REGISTRY_FILE} schema_version must be {APPROACH_REGISTRY_SCHEMA}")
    if _positive_int(registry.get("registry_revision")) < 1:
        errors.append("approach_registry registry_revision must be a positive integer")
    campaigns = registry.get("campaigns")
    if not isinstance(campaigns, dict):
        errors.append("approach_registry campaigns must be an object")
        return
    approach_ids: set[str] = set()
    for campaign_id, raw_campaign in campaigns.items():
        if not isinstance(raw_campaign, dict):
            errors.append(f"campaign {campaign_id!r} must be an object")
            continue
        campaign_status = _clean_text(raw_campaign.get("status")).lower()
        if campaign_status and campaign_status not in CAMPAIGN_STATUSES:
            errors.append(f"campaign {campaign_id} has invalid status")
        approaches = raw_campaign.get("approaches")
        if not isinstance(approaches, dict):
            errors.append(f"campaign {campaign_id} approaches must be an object")
            continue
        for approach_id, raw in approaches.items():
            if approach_id in approach_ids:
                errors.append(f"approach id must be globally unique: {approach_id}")
            approach_ids.add(str(approach_id))
            if not isinstance(raw, dict):
                errors.append(f"approach {approach_id} must be an object")
                continue
            if raw.get("id") not in {None, "", approach_id}:
                errors.append(f"approach {approach_id} id disagrees with its registry key")
            if raw.get("campaign_id") not in {None, "", campaign_id}:
                errors.append(f"approach {approach_id} campaign_id disagrees with its campaign key")
            if raw.get("status", "eligible") not in APPROACH_STATUSES:
                errors.append(f"approach {approach_id} has invalid status")
            if raw.get("status") in {"blocked", "closed"} and not _clean_text(
                raw.get("reopen_condition")
            ):
                # Enforce-mode elevates this via validate_negative_space; keep a
                # soft warning here so monitor mode still surfaces the gap.
                warnings.append(
                    f"{raw.get('status')} approach {approach_id} has no reopen_condition"
                )
            if "estimates" in raw and not isinstance(raw.get("estimates"), Mapping):
                errors.append(f"approach {approach_id} estimates must be an object")
            estimates = raw.get("estimates") if isinstance(raw.get("estimates"), Mapping) else {}
            known_estimates = set(BENEFIT_WEIGHTS) | set(PENALTY_WEIGHTS)
            unexpected_estimates = sorted(set(estimates) - known_estimates)
            if unexpected_estimates:
                errors.append(
                    f"approach {approach_id} has unknown estimate factors: "
                    + ", ".join(unexpected_estimates)
                )
            for factor, estimate in estimates.items():
                if factor not in known_estimates:
                    continue
                try:
                    _interval(estimate, field=f"approach {approach_id} estimate {factor}")
                except ValueError as exc:
                    errors.append(str(exc))


def _validate_plan(
    plan: Mapping[str, Any],
    contract: Mapping[str, Any],
    registry: Mapping[str, Any],
    errors: list[str],
    *,
    run_dir: str | Path | None = None,
) -> None:
    if plan.get("schema_version") != CURRENT_PLAN_SCHEMA:
        errors.append(f"{CURRENT_PLAN_FILE} schema_version must be {CURRENT_PLAN_SCHEMA}")
    if _positive_int(plan.get("plan_revision")) < 1:
        errors.append("current_plan plan_revision must be a positive integer")
    if plan.get("state") not in PLAN_STATES:
        errors.append("current_plan state is invalid")
    if plan.get("enforcement_mode") not in ENFORCEMENT_MODES:
        errors.append("current_plan enforcement_mode is invalid")
    if plan.get("goal_revision") != contract.get("goal_revision"):
        errors.append("current_plan goal_revision does not match goal_contract")
    if plan.get("registry_revision") != registry.get("registry_revision"):
        errors.append("current_plan registry_revision does not match approach_registry")
    if plan.get("state") == "active":
        campaign_id = _clean_text(plan.get("campaign_id"))
        approach_id = _clean_text(plan.get("approach_id"))
        if not campaign_id or not approach_id:
            errors.append("active current_plan requires campaign_id and approach_id")
        eligibility_error = _approach_ineligibility(
            registry, campaign_id, approach_id, run_dir=run_dir
        )
        if eligibility_error:
            errors.append(
                "active current_plan references an ineligible campaign/approach: "
                + eligibility_error
            )
        if not _clean_text(plan.get("next_action")):
            errors.append("active current_plan requires a non-empty next_action")
        if plan.get("enforcement_mode") == "enforce":
            dispatch_attestation = (
                plan.get("dispatch_provider_attestation")
                if isinstance(plan.get("dispatch_provider_attestation"), Mapping)
                else {}
            )
            dispatch_family = _clean_text(plan.get("dispatch_provider_family"))
            if (
                dispatch_attestation.get("schema_version")
                != "provider_executable_attestation.v1"
            ):
                errors.append(
                    "active enforce current_plan requires a provider executable attestation"
                )
            if not _clean_text(dispatch_attestation.get("provider")):
                errors.append(
                    "active enforce current_plan dispatch attestation lacks a provider"
                )
            attested_family = _clean_text(dispatch_attestation.get("family"))
            if (
                not dispatch_family
                or dispatch_family == "unverified"
                or attested_family != dispatch_family
            ):
                errors.append(
                    "active enforce current_plan dispatch family disagrees with its executable attestation"
                )
            if not _clean_text(dispatch_attestation.get("executable_path")) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                _clean_text(dispatch_attestation.get("executable_sha256")),
            ):
                errors.append(
                    "active enforce current_plan dispatch executable attestation is incomplete"
                )
        obligations = contract.get("obligations") if isinstance(contract.get("obligations"), dict) else {}
        for oid in plan.get("target_obligation_ids") or []:
            if str(oid) not in obligations:
                errors.append(f"current_plan targets unknown obligation {oid}")


def _latest_finalized_row(run_dir: Path) -> dict[str, Any] | None:
    try:
        rows = _read_jsonl(run_dir / "iterations.jsonl")
    except ValueError:
        return None
    for row in reversed(rows):
        if row.get("bank_status", "accepted") == "accepted":
            return row
    return None


def _validate_active_direction_authority(
    plan: Mapping[str, Any], decisions: Sequence[Mapping[str, Any]], errors: list[str]
) -> None:
    """Require the active enforce plan to be the exact reviewed decision post-image."""
    decision_id = _clean_text(plan.get("decision_id"))
    if not decision_id:
        errors.append("active enforce current_plan requires a direction decision_id")
        return
    matches = [row for row in decisions if _clean_text(row.get("decision_id")) == decision_id]
    if len(matches) != 1:
        errors.append(
            "active enforce current_plan must reference exactly one direction decision"
        )
        return
    decision = matches[0]
    if decision.get("schema_version") != DIRECTION_DECISION_SCHEMA:
        errors.append("active direction decision has an invalid schema")
    if decision.get("decision_type") != "select_direction":
        errors.append("active direction decision must be a select_direction decision")
    for field in ("plan_revision", "goal_revision", "registry_revision", "campaign_id", "approach_id"):
        if decision.get(field) != plan.get(field):
            errors.append(f"active direction decision {field} disagrees with current_plan")
    review = decision.get("review") if isinstance(decision.get("review"), dict) else {}
    review_status = _clean_text(review.get("status") or review.get("verdict")).lower()
    if review_status not in {"passed", "accepted", "pass"}:
        errors.append("active direction decision lacks a passed review")
    if review.get("different_family") is not True:
        errors.append("active direction decision lacks a different-family review")
    reviewed_dispatch_attestation = review.get("primary_execution_attestation")
    if (
        not isinstance(reviewed_dispatch_attestation, Mapping)
        or dict(reviewed_dispatch_attestation)
        != dict(plan.get("dispatch_provider_attestation") or {})
    ):
        errors.append(
            "active direction decision executable identity disagrees with current_plan"
        )
    expected_fingerprint = _plan_authority_fingerprint(plan)
    if decision.get("plan_fingerprint") != expected_fingerprint:
        errors.append("active current_plan post-image is not bound to its reviewed direction decision")


def _mode_authority_error(
    plan: Mapping[str, Any], decisions: Sequence[Mapping[str, Any]]
) -> str:
    """Require a complete decision row bound to the exact current-plan postimage."""

    expected_mode = _clean_text(plan.get("enforcement_mode")).lower()
    expected_fingerprint = _object_fingerprint(plan)
    allowed_types = {"initialize", "migration", "select_direction", "result_finalize"}
    for row in reversed(list(decisions)):
        if not isinstance(row, Mapping):
            continue
        if row.get("schema_version") != DIRECTION_DECISION_SCHEMA:
            continue
        if row.get("decision_type") not in allowed_types:
            continue
        if not _clean_text(row.get("event_id")) or not _clean_text(row.get("decision_id")):
            continue
        if _clean_text(row.get("enforcement_mode")).lower() != expected_mode:
            continue
        if row.get("mode_plan_fingerprint") != expected_fingerprint:
            continue
        if any(
            row.get(field) != plan.get(field)
            for field in ("plan_revision", "goal_revision", "registry_revision")
        ):
            continue
        return ""
    return (
        "current_plan enforcement_mode lacks a complete decision row bound to "
        "the exact plan postimage"
    )


def validate_goal_focus(
    run_dir: str | Path, *, require_enabled: bool = False
) -> dict[str, Any]:
    """Validate Goal-Focus files and their cross-file invariants."""

    root = Path(run_dir)
    errors: list[str] = []
    warnings: list[str] = []
    checked: list[str] = []
    present = {
        name: (root / name).exists()
        for name in (GOAL_CONTRACT_FILE, APPROACH_REGISTRY_FILE, CURRENT_PLAN_FILE)
    }
    if not any(present.values()):
        if require_enabled:
            errors.append("Goal-Focus v2 is not initialized")
        else:
            warnings.append("Goal-Focus v2 is not initialized; legacy behavior remains active")
        return {"status": "error" if errors else "ok", "errors": errors, "warnings": warnings, "checked": checked}
    for name, exists in present.items():
        if not exists:
            errors.append(f"partial Goal-Focus state: missing {name}")
    if errors:
        return {"status": "error", "errors": errors, "warnings": warnings, "checked": checked}
    try:
        contract = load_goal_contract(root)
        checked.append(GOAL_CONTRACT_FILE)
        registry = load_approach_registry(root)
        checked.append(APPROACH_REGISTRY_FILE)
        plan = load_current_plan(root)
        checked.append(CURRENT_PLAN_FILE)
        decisions = load_direction_decisions(root)
        checked.append(DIRECTION_DECISIONS_FILE)
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
        return {"status": "error", "errors": errors, "warnings": warnings, "checked": checked}

    _validate_contract(contract, errors, warnings)
    _validate_registry(registry, errors, warnings)
    _validate_plan(plan, contract, registry, errors, run_dir=root)
    mode_error = _mode_authority_error(plan, decisions)
    if mode_error:
        errors.append(mode_error)
    _validate_plan_compute_policy(root, plan, errors)
    ns_report = ns.validate_negative_space(
        root,
        registry,
        enforce=plan.get("enforcement_mode") == "enforce",
    )
    checked.append(ns.NEGATIVE_SPACE_REL.as_posix())
    errors.extend(ns_report.get("errors") or [])
    warnings.extend(ns_report.get("warnings") or [])
    for index, decision in enumerate(decisions, start=1):
        if decision.get("schema_version") != DIRECTION_DECISION_SCHEMA:
            warnings.append(f"direction decision row {index} uses an unknown schema")

    if plan.get("state") == "active":
        _validate_active_direction_authority(plan, decisions, errors)

    pending = load_pending_candidate(root)
    if pending:
        checked.append(PENDING_CANDIDATE_FILE)
        if pending.get("schema_version") != ITERATION_CANDIDATE_SCHEMA:
            errors.append("pending iteration candidate has invalid schema")
        if pending.get("plan_revision") != plan.get("plan_revision"):
            errors.append("pending iteration candidate references a stale plan revision")
    quarantine = load_candidate_quarantine(root)
    if quarantine:
        checked.append(CANDIDATE_QUARANTINE_FILE)
        object_kind = _clean_text(quarantine.get("object_kind"))
        quarantined_object = (
            quarantine.get(object_kind)
            if object_kind in {"candidate", "dispatch"}
            and isinstance(quarantine.get(object_kind), dict)
            else {}
        )
        if quarantine.get("schema_version") != CANDIDATE_QUARANTINE_SCHEMA:
            errors.append("candidate quarantine has invalid schema")
        if not _clean_text(quarantine.get("reason")):
            errors.append("candidate quarantine requires a reason")
        if object_kind not in {"candidate", "dispatch"}:
            errors.append("candidate quarantine has invalid object kind")
        if not quarantined_object:
            errors.append("candidate quarantine lacks its exact blocked object")
        elif quarantine.get("candidate_fingerprint") != candidate_fingerprint(
            quarantined_object
        ):
            errors.append("candidate quarantine fingerprint is invalid")
        if pending:
            errors.append("active pending candidate and candidate quarantine cannot coexist")
    dispatch = _read_object(root / ITERATION_DISPATCH_FILE, required=False) or None
    if dispatch:
        checked.append(ITERATION_DISPATCH_FILE)
        if pending:
            errors.append("pending candidate and in-flight dispatch cannot coexist")
        if quarantine:
            errors.append("candidate quarantine and in-flight dispatch cannot coexist")
        try:
            validate_iteration_dispatch(root, dispatch)
        except (OSError, ValueError, TransactionError) as exc:
            errors.append(f"invalid in-flight dispatch: {exc}")

    latest = _latest_finalized_row(root)
    if latest and latest.get("plan_revision") == plan.get("plan_revision"):
        if latest.get("campaign_id") and latest.get("campaign_id") != plan.get("campaign_id"):
            errors.append("latest finalized iteration campaign disagrees with current_plan")
        if latest.get("approach_id") and latest.get("approach_id") != plan.get("approach_id"):
            errors.append("latest finalized iteration approach disagrees with current_plan")
    try:
        ledger_rows = _read_jsonl(root / "iterations.jsonl")
    except (OSError, ValueError) as exc:
        errors.append(f"cannot validate Goal-Focus terminal ledger: {exc}")
    else:
        latest_ledger = ledger_rows[-1] if ledger_rows else None
        if (
            isinstance(latest_ledger, Mapping)
            and _clean_text(latest_ledger.get("stop_reason"))
            == HOST_REVIEWED_GOAL_SUCCESS_REASON
        ):
            errors.extend(validate_host_finalized_goal_success(root, latest_ledger))

    mode = plan.get("enforcement_mode")
    if mode == "enforce" and plan.get("state") == "active":
        expected_path = render_current_path(plan)
        state_path = root / "loop_state.json"
        if state_path.exists():
            try:
                state = _read_object(state_path, required=True)
                if _clean_text(state.get("next_preferred_path")) != expected_path:
                    errors.append("loop_state.next_preferred_path disagrees with current_plan")
                projection = state.get("goal_focus_projection")
                if not isinstance(projection, dict) or projection.get("plan_revision") != plan.get("plan_revision"):
                    errors.append("loop_state Goal-Focus projection is missing or stale")
            except (OSError, ValueError) as exc:
                errors.append(f"cannot validate loop_state Goal-Focus projection: {exc}")
        recovery_path = root / "recovery.md"
        if recovery_path.exists():
            try:
                text = _read_regular_text(recovery_path)
                if _MANAGED_START not in text or expected_path not in text:
                    errors.append("recovery.md Goal-Focus managed view is missing or stale")
            except (OSError, ValueError) as exc:
                errors.append(f"cannot validate recovery.md: {exc}")

    return {
        "status": "error" if errors else "ok",
        "errors": errors,
        "warnings": warnings,
        "checked": checked,
    }


def _estimate_point(value: Any, field: str) -> float:
    if isinstance(value, str):
        number = _BAND_VALUES.get(value.strip().lower())
        if number is None:
            raise ValueError(f"{field} has unknown estimate band {value!r}")
        return number
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number or named estimate band")
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number) or not 0.0 <= number <= 4.0:
            raise ValueError(f"{field} must be finite and between 0 and 4")
        return number
    raise ValueError(f"{field} must be a number or named estimate band")


def _interval(value: Any, *, field: str = "estimate") -> tuple[float, float]:
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        number = _estimate_point(value, field)
        return (number, number)
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a bounded estimate")
    if isinstance(value, Mapping):
        allowed_fields = {"lower", "upper", "min", "max", "value"}
        unexpected = sorted(set(value) - allowed_fields)
        if unexpected:
            raise ValueError(
                f"{field} contains unsupported fields: " + ", ".join(unexpected)
            )
        if not value:
            raise ValueError(f"{field} interval cannot be empty")
        lower = value.get("lower", value.get("min", value.get("value", 0)))
        upper = value.get("upper", value.get("max", value.get("value", lower)))
        lo = _estimate_point(lower, f"{field}.lower")
        hi = _estimate_point(upper, f"{field}.upper")
        if lo > hi:
            raise ValueError(f"{field} lower bound exceeds upper bound")
        return (lo, hi)
    if isinstance(value, (tuple, list)) and len(value) == 2:
        lo = _estimate_point(value[0], f"{field}.lower")
        hi = _estimate_point(value[1], f"{field}.upper")
        if lo > hi:
            raise ValueError(f"{field} lower bound exceeds upper bound")
        return (lo, hi)
    raise ValueError(f"{field} must be a point or two-bound interval")


def score_approach(approach: Mapping[str, Any]) -> dict[str, Any]:
    """Score one approach with conservative and optimistic interval bounds."""

    if "estimates" in approach and not isinstance(approach.get("estimates"), Mapping):
        raise ValueError("approach estimates must be an object")
    estimates = approach.get("estimates") if isinstance(approach.get("estimates"), Mapping) else approach
    components: dict[str, dict[str, Any]] = {}
    conservative = 0.0
    optimistic = 0.0
    for name, weight in BENEFIT_WEIGHTS.items():
        lo, hi = _interval(estimates.get(name, 0), field=f"estimate {name}")
        conservative += weight * lo
        optimistic += weight * hi
        components[name] = {"lower": lo, "upper": hi, "weight": weight, "kind": "benefit"}
    for name, weight in PENALTY_WEIGHTS.items():
        lo, hi = _interval(estimates.get(name, 0), field=f"estimate {name}")
        conservative -= weight * hi
        optimistic -= weight * lo
        components[name] = {"lower": lo, "upper": hi, "weight": -weight, "kind": "penalty"}
    return {
        "approach_id": _clean_text(approach.get("id")),
        "campaign_id": _clean_text(approach.get("campaign_id")),
        "conservative": round(conservative, 6),
        "optimistic": round(optimistic, 6),
        "components": components,
    }


def _eligible_approaches(
    registry: Mapping[str, Any],
    *,
    run_dir: str | Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows = _approach_rows(registry)
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for campaign_id, approach_id, approach in rows:
        reason = _approach_ineligibility(
            registry, campaign_id, approach_id, run_dir=run_dir
        )
        if reason:
            excluded.append({"campaign_id": campaign_id, "approach_id": approach_id, "reason": reason})
            continue
        approach["id"] = approach_id
        approach["campaign_id"] = campaign_id
        eligible.append(approach)
    return eligible, excluded


def rank_approaches(
    registry: Mapping[str, Any],
    *,
    run_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    eligible, _ = _eligible_approaches(registry, run_dir=run_dir)
    scored = [score_approach(approach) for approach in eligible]
    return sorted(
        scored,
        key=lambda row: (-float(row["conservative"]), -float(row["optimistic"]), row["approach_id"]),
    )


def select_direction(
    registry: Mapping[str, Any],
    *,
    max_portfolio: int = 3,
    run_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Select a dominant exploitation path or a bounded informative experiment."""

    eligible, excluded = _eligible_approaches(registry, run_dir=run_dir)
    scores = [score_approach(approach) for approach in eligible]
    by_id = {str(approach["id"]): approach for approach in eligible}
    scores.sort(key=lambda row: (-row["conservative"], -row["optimistic"], row["approach_id"]))
    if not scores:
        return {
            "schema_version": "direction_selection.v2",
            "status": "no_eligible_direction",
            "selected_campaign_id": "",
            "selected_approach_id": "",
            "selection_mode": "none",
            "portfolio": [],
            "scores": [],
            "excluded": excluded,
            "generated_at": utc_now(),
        }
    first = scores[0]
    dominant = len(scores) == 1 or all(
        first["conservative"] > contender["optimistic"] for contender in scores[1:]
    )
    if dominant:
        selected = first
        mode = "dominant_exploitation"
    else:
        def exploration_key(score: Mapping[str, Any]) -> tuple[float, float, float, str]:
            approach = by_id[str(score["approach_id"])]
            info_lo = score["components"]["information_gain"]["lower"]
            execution_hi = score["components"]["execution_cost"]["upper"]
            verification_hi = score["components"]["verification_cost"]["upper"]
            ratio = info_lo / max(0.5, execution_hi + verification_hi)
            redundancy = score["components"]["redundancy"]["upper"]
            return (-ratio, redundancy, execution_hi, str(score["approach_id"]))

        selected = sorted(scores, key=exploration_key)[0]
        mode = "bounded_exploration"
    diverse: list[str] = []
    seen_tags: set[str] = set()
    for score in scores:
        approach = by_id[str(score["approach_id"])]
        tags = {str(tag) for tag in approach.get("diversity_tags") or [] if str(tag)}
        if not diverse or not tags or not (tags & seen_tags):
            diverse.append(str(score["approach_id"]))
            seen_tags |= tags
        if len(diverse) >= max(1, int(max_portfolio)):
            break
    if str(selected["approach_id"]) not in diverse:
        diverse = [str(selected["approach_id"])] + diverse[: max(0, int(max_portfolio) - 1)]
    return {
        "schema_version": "direction_selection.v2",
        "status": "selected",
        "selected_campaign_id": selected["campaign_id"],
        "selected_approach_id": selected["approach_id"],
        "selection_mode": mode,
        "portfolio": diverse,
        "scores": scores,
        "excluded": excluded,
        "generated_at": utc_now(),
    }


def _finalized_rows(run_dir: Path) -> list[dict[str, Any]]:
    try:
        return [
            row
            for row in _read_jsonl(run_dir / "iterations.jsonl")
            if row.get("bank_status", "accepted") in {"accepted", "rejected"}
        ]
    except ValueError:
        return []


def evaluate_replan_triggers(
    run_dir: str | Path, bundle: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    root = Path(run_dir)
    data = dict(bundle or load_goal_focus(root))
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
    registry = data.get("registry") if isinstance(data.get("registry"), dict) else {}
    triggers: list[dict[str, Any]] = []

    def add(code: str, detail: str) -> None:
        if not any(row["code"] == code for row in triggers):
            triggers.append({"code": code, "detail": detail})

    if not plan:
        add("no_current_plan", "Goal-Focus has no current plan")
        return triggers
    if plan.get("state") != "active":
        add("plan_not_active", f"current plan state is {plan.get('state')}")
    approach = _find_approach(
        registry, _clean_text(plan.get("campaign_id")), _clean_text(plan.get("approach_id"))
    )
    if plan.get("state") == "active":
        eligibility_error = _approach_ineligibility(
            registry,
            _clean_text(plan.get("campaign_id")),
            _clean_text(plan.get("approach_id")),
            run_dir=root,
        )
        if eligibility_error:
            add("selected_approach_ineligible", eligibility_error)
    if plan.get("trip_wires_triggered"):
        add("trip_wire", ", ".join(str(x) for x in plan.get("trip_wires_triggered") or []))
    if data.get("pending_candidate"):
        add("pending_result_review", "an iteration candidate awaits independent review")
    if data.get("candidate_quarantine"):
        add(
            "candidate_quarantined",
            "a failed provider completion is quarantined for explicit operator recovery",
        )
    if data.get("iteration_dispatch"):
        add(
            "dispatch_inflight",
            "a host-pinned worker dispatch is in flight; wait for staging or explicitly cancel it",
        )
    selection = plan.get("selection") if isinstance(plan.get("selection"), dict) else {}
    if selection.get("panel_dissent") is True or selection.get("substantive_dissent") is True:
        add("panel_dissent", "structured strategy review recorded substantive dissent")

    rows = _finalized_rows(root)
    last_iteration = max((_positive_int(row.get("iteration")) for row in rows), default=0)
    valid_through = plan.get("valid_through_iteration")
    if valid_through is not None and _positive_int(valid_through) < last_iteration + 1:
        add("plan_horizon_expired", f"plan expired at iteration {valid_through}")
    if approach is not None:
        estimated_at = approach.get("last_estimated_iteration")
        if estimated_at is not None and last_iteration - _positive_int(estimated_at) >= 10:
            add("estimate_expired", "selected approach estimates are at least ten iterations old")
        if approach.get("counterevidence") and approach.get("counterevidence_unreviewed", True):
            add("new_counterevidence", "selected approach has unreviewed counterevidence")

    current_plan_revision = _positive_int(plan.get("plan_revision"))

    def belongs_to_current_plan(row: Mapping[str, Any]) -> bool:
        row_revision = _positive_int(row.get("plan_revision"))
        if not row_revision:
            goal_focus = (
                row.get("goal_focus")
                if isinstance(row.get("goal_focus"), Mapping)
                else {}
            )
            row_revision = _positive_int(goal_focus.get("plan_revision"))
        return bool(current_plan_revision and row_revision == current_plan_revision)

    plan_rows = [row for row in rows if belongs_to_current_plan(row)]
    global_streak = 0
    scope_streak = 0
    for row in reversed(plan_rows):
        progress = row.get("progress_assessment") if isinstance(row.get("progress_assessment"), dict) else {}
        if (row.get("global_delta") or progress.get("global_delta")) in {"reduced", "satisfied"}:
            break
        global_streak += 1
    for row in reversed(plan_rows):
        goal_focus = row.get("goal_focus") if isinstance(row.get("goal_focus"), dict) else {}
        scope_only = (
            (row.get("scope_lock") or goal_focus.get("scope_lock")) == "encoding_only"
            or row.get("scope_only") is True
        )
        if not scope_only:
            break
        scope_streak += 1
    if global_streak >= 3:
        add("global_progress_stall", f"{global_streak} finalized iterations without global obligation reduction")
    if scope_streak >= 3:
        add("scope_only_streak", f"{scope_streak} consecutive scope-only iterations")
    return triggers


def _coherent_completed_obligations(obligations: Mapping[str, Any]) -> set[str]:
    """Derive completed nodes only through evidence-backed dependency closure."""

    complete: set[str] = set()
    remaining = {
        str(oid)
        for oid, node in obligations.items()
        if isinstance(node, dict)
        and node.get("status") in {"satisfied", "closed"}
        and any(_clean_text(ref) for ref in node.get("evidence_refs") or [])
    }
    while remaining:
        admitted = {
            oid
            for oid in remaining
            if {
                _clean_text(dep)
                for dep in (obligations.get(oid) or {}).get("depends_on") or []
                if _clean_text(dep)
            }
            <= complete
        }
        if not admitted:
            break
        complete.update(admitted)
        remaining -= admitted
    return complete


def _dependency_admissible_transitions(
    obligations: Mapping[str, Any], transitions: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Return transitions reachable from the pre-state in DAG order."""

    complete = _coherent_completed_obligations(obligations)
    pending = [dict(row) for row in transitions]
    admitted: list[dict[str, Any]] = []
    while pending:
        advanced = False
        remaining: list[dict[str, Any]] = []
        for row in pending:
            oid = _clean_text(row.get("obligation_id"))
            node = obligations.get(oid)
            if not isinstance(node, dict):
                continue
            target = row.get("to")
            if target not in {"partial", "satisfied", "closed"}:
                continue
            # Completed obligations are monotone and cannot be reopened by an
            # iteration result.
            if oid in complete and target not in {"satisfied", "closed"}:
                continue
            dependencies = {
                _clean_text(dep) for dep in node.get("depends_on") or [] if _clean_text(dep)
            }
            if dependencies <= complete:
                admitted.append(row)
                if target in {"satisfied", "closed"}:
                    complete.add(oid)
                advanced = True
            else:
                remaining.append(row)
        if not advanced:
            break
        pending = remaining
    return admitted


def classify_progress(record: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, Any]:
    nested = record.get("progress_assessment")
    nested = nested if isinstance(nested, dict) else {}
    campaign = str(record.get("campaign_delta") or nested.get("campaign_delta") or "none")
    if campaign not in CAMPAIGN_DELTAS:
        campaign = "none"
    transitions = [
        dict(item)
        for item in (record.get("obligation_transitions") or [])
        if isinstance(item, dict)
    ]
    obligations = contract.get("obligations") if isinstance(contract.get("obligations"), dict) else {}
    evidence = record.get("evidence_ids") or (
        record.get("evidence_checked", {}).get("evidence_ids", [])
        if isinstance(record.get("evidence_checked"), dict)
        else []
    )
    review = record.get("result_review")
    review = review if isinstance(review, dict) else {}
    review_status = _clean_text(review.get("status") or review.get("verdict")).lower()
    review_passed = review_status in {"passed", "accepted", "pass"}
    reviewed_obligations = {
        _clean_text(item.get("obligation_id"))
        for item in review.get("obligation_reviews") or []
        if isinstance(item, dict)
        and _clean_text(item.get("status") or item.get("verdict")).lower()
        in {"accept", "accepted", "pass", "supported"}
        and _clean_text(item.get("obligation_id"))
    }
    declared_global = str(nested.get("global_delta") or record.get("global_delta") or "none")
    # The v2 runtime stages compact obligation ids in progress_assessment.  A
    # reviewed declaration becomes a concrete transition only when evidence is
    # present and the independent reviewer accepted that named obligation.
    if not transitions and declared_global in {"reduced", "satisfied"} and evidence and review_passed:
        target = "satisfied" if declared_global == "satisfied" else "partial"
        for raw_oid in nested.get("obligation_ids") or []:
            oid = _clean_text(raw_oid)
            if oid and oid in reviewed_obligations:
                transitions.append({"obligation_id": oid, "to": target})
    reviewed_transitions: list[dict[str, Any]] = []
    if evidence:
        for transition in transitions:
            if not isinstance(transition, dict):
                continue
            oid = _clean_text(transition.get("obligation_id"))
            node = obligations.get(oid)
            if not isinstance(node, dict) or node.get("kind") not in OBLIGATION_KINDS:
                continue
            if transition.get("to") not in {"partial", "satisfied", "closed"}:
                continue
            # Once a review object is attached (the finalization path), only
            # reviewer-accepted obligation changes can become global progress.
            if review and (not review_passed or oid not in reviewed_obligations):
                continue
            reviewed_transitions.append(transition)
    accepted_transitions = _dependency_admissible_transitions(
        obligations, reviewed_transitions
    )
    completed_after = _coherent_completed_obligations(obligations)
    for transition in accepted_transitions:
        if transition.get("to") in {"satisfied", "closed"}:
            completed_after.add(_clean_text(transition.get("obligation_id")))
    required = _criterion_ids(contract)
    goal_satisfied = bool(required) and required <= completed_after
    verified_global = any(
        isinstance(obligations.get(_clean_text(row.get("obligation_id"))), dict)
        and obligations[_clean_text(row.get("obligation_id"))].get("kind")
        in {"bridge", "terminal"}
        for row in accepted_transitions
    )
    global_delta = "satisfied" if goal_satisfied else "reduced" if verified_global else "none"
    return {
        "campaign_delta": campaign,
        "global_delta": global_delta,
        "obligation_transitions": accepted_transitions,
    }


def render_current_path(plan: Mapping[str, Any]) -> str:
    action = _clean_text(plan.get("next_action"), 2000) or "Run a structured strategy review."
    campaign = _clean_text(plan.get("campaign_id")) or "unselected"
    approach = _clean_text(plan.get("approach_id")) or "unselected"
    revision = _positive_int(plan.get("plan_revision"))
    return f"GOAL-FOCUS plan r{revision}: campaign `{campaign}`, approach `{approach}` — {action}"


def render_recovery_managed(
    contract: Mapping[str, Any], plan: Mapping[str, Any], registry: Mapping[str, Any] | None = None
) -> str:
    goal = _clean_text(contract.get("goal"), 2000)
    targets = ", ".join(str(item) for item in plan.get("target_obligation_ids") or []) or "none selected"
    lines = [
        _MANAGED_START,
        "## Goal-Focus v2",
        "",
        f"- Goal: {goal}",
        f"- Plan state: {plan.get('state', '')}",
        f"- Active campaign: {_clean_text(plan.get('campaign_id')) or 'none'}",
        f"- Active approach: {_clean_text(plan.get('approach_id')) or 'none'}",
        f"- Target obligations: {targets}",
        f"- Plan revision: {_positive_int(plan.get('plan_revision'))}",
        f"- Goal revision: {_positive_int(plan.get('goal_revision'))}",
        f"- Registry revision: {_positive_int(plan.get('registry_revision'))}",
        f"- **Next safe action:** {render_current_path(plan)}",
        _MANAGED_END,
    ]
    return "\n".join(lines) + "\n"


def _merge_managed(existing: str, block: str) -> str:
    pattern = re.compile(re.escape(_MANAGED_START) + r".*?" + re.escape(_MANAGED_END) + r"\n?", re.S)
    if pattern.search(existing):
        merged = pattern.sub(block, existing, count=1)
    elif existing.strip():
        merged = existing.rstrip() + "\n\n" + block
    else:
        merged = "# Autonomous Research Loop Recovery\n\n" + block
    return merged if merged.endswith("\n") else merged + "\n"


def _managed_view_snapshot(
    run_dir: Path,
) -> tuple[dict[str, Any], str | None, str, str | None]:
    """Capture each compatibility view once for compare-and-swap projection writes."""

    state, state_hash = _read_object_snapshot(
        run_dir / "loop_state.json", required=False
    )
    try:
        recovery_payload = _read_regular_bytes(run_dir / "recovery.md")
    except FileNotFoundError:
        recovery = ""
        recovery_hash = None
    else:
        try:
            recovery = recovery_payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"recovery.md is not UTF-8: {exc}") from exc
        recovery_hash = hashlib.sha256(recovery_payload).hexdigest()
    return state, state_hash, recovery, recovery_hash


def _managed_postimages(
    contract: Mapping[str, Any],
    registry: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    state_preimage: Mapping[str, Any],
    recovery_preimage: str,
) -> tuple[dict[str, Any], str]:
    state = copy.deepcopy(dict(state_preimage))
    if state:
        expected_path = render_current_path(plan)
        projection_core = {
            "schema_version": "goal_focus_projection.v2",
            "plan_revision": plan.get("plan_revision"),
            "goal_revision": plan.get("goal_revision"),
            "registry_revision": plan.get("registry_revision"),
            "campaign_id": plan.get("campaign_id"),
            "approach_id": plan.get("approach_id"),
        }
        old_projection = state.get("goal_focus_projection")
        old_core = {
            key: old_projection.get(key)
            for key in projection_core
        } if isinstance(old_projection, dict) else {}
        if state.get("next_preferred_path") != expected_path or old_core != projection_core:
            now = utc_now()
            state["next_preferred_path"] = expected_path
            state["goal_focus_projection"] = {**projection_core, "updated_at": now}
            state["updated_at"] = now
    recovery = _merge_managed(
        recovery_preimage,
        render_recovery_managed(contract, plan, registry),
    )
    return state, recovery


def reconcile_goal_focus(run_dir: str | Path, *, apply: bool = False) -> dict[str, Any]:
    root = Path(run_dir)
    contract, contract_hash = _read_object_snapshot(
        root / GOAL_CONTRACT_FILE, required=True
    )
    registry, registry_hash = _read_object_snapshot(
        root / APPROACH_REGISTRY_FILE, required=True
    )
    plan, plan_hash = _read_object_snapshot(
        root / CURRENT_PLAN_FILE, required=True
    )
    previous_state, state_hash, previous_recovery, recovery_hash = (
        _managed_view_snapshot(root)
    )
    state, recovery = _managed_postimages(
        contract,
        registry,
        plan,
        state_preimage=previous_state,
        recovery_preimage=previous_recovery,
    )
    state_changed = bool(state) and state != previous_state
    recovery_changed = recovery != previous_recovery
    result = {
        "status": "drift" if state_changed or recovery_changed else "ok",
        "applied": False,
        "state_changed": state_changed,
        "recovery_changed": recovery_changed,
        "plan_revision": plan.get("plan_revision"),
    }
    if apply and (state_changed or recovery_changed):
        json_files = {"loop_state.json": state} if state else {}
        expected_hashes = {
            GOAL_CONTRACT_FILE: str(contract_hash),
            APPROACH_REGISTRY_FILE: str(registry_hash),
            CURRENT_PLAN_FILE: str(plan_hash),
        }
        expected_absent = []
        for filename, digest in (
            ("loop_state.json", state_hash),
            ("recovery.md", recovery_hash),
        ):
            if digest is None:
                expected_absent.append(filename)
            else:
                expected_hashes[filename] = digest
        tx = commit_transaction(
            root,
            json_files=json_files,
            text_files={"recovery.md": recovery},
            expected_revisions={
                CURRENT_PLAN_FILE: ("plan_revision", plan.get("plan_revision")),
                GOAL_CONTRACT_FILE: ("goal_revision", contract.get("goal_revision")),
                APPROACH_REGISTRY_FILE: ("registry_revision", registry.get("registry_revision")),
            },
            expected_hashes=expected_hashes,
            expected_absent=expected_absent,
        )
        result.update({"status": "ok", "applied": True, "transaction": tx})
    return result


def pre_dispatch_gate(
    run_dir: str | Path, *, auto_recover: bool = True, regenerate_views: bool = True
) -> dict[str, Any]:
    root = Path(run_dir)
    warnings: list[str] = []
    if auto_recover:
        recovered = recover_transactions(root)
        if recovered:
            warnings.append(f"recovered {len(recovered)} interrupted Goal-Focus transaction(s)")
    plan = load_current_plan(root, required=False)
    v2_present = any(
        (root / name).exists()
        for name in (GOAL_CONTRACT_FILE, APPROACH_REGISTRY_FILE, CURRENT_PLAN_FILE)
    )
    # Validate canonical authority before allowing reconciliation to mutate a
    # derived view.  Only projection-drift errors are safe to repair here.
    validation = validate_goal_focus(root, require_enabled=v2_present)
    view_error_prefixes = (
        "loop_state.next_preferred_path",
        "loop_state Goal-Focus projection",
        "recovery.md Goal-Focus managed view",
    )
    authority_errors = [
        error
        for error in validation["errors"]
        if not error.startswith(view_error_prefixes)
    ]
    if regenerate_views and plan and not authority_errors:
        try:
            reconciled = reconcile_goal_focus(root, apply=True)
            if reconciled.get("applied"):
                warnings.append("regenerated stale Goal-Focus managed views")
        except (OSError, ValueError, TransactionError) as exc:
            warnings.append(f"managed-view reconciliation failed: {exc}")
        validation = validate_goal_focus(root, require_enabled=True)
    warnings.extend(validation["warnings"])
    authority_errors = [
        error
        for error in validation["errors"]
        if not error.startswith(view_error_prefixes)
    ]
    bundle = load_goal_focus(root) if not validation["errors"] else {"plan": plan}
    triggers = evaluate_replan_triggers(root, bundle)
    mode = (
        _clean_text(plan.get("enforcement_mode")).lower()
        if plan
        else "enforce"
        if v2_present
        else "off"
    )
    if plan and mode not in ENFORCEMENT_MODES:
        # A malformed authority is never interpreted as an opt-out.
        mode = "enforce"
    if any("enforcement_mode" in error for error in authority_errors):
        # A mode whose decision-ledger authority is missing/mismatched cannot
        # downgrade an enforce loop into monitor/off behavior.
        mode = "enforce"
    errors = list(validation["errors"])
    if errors:
        action = "reconcile"
        ok = mode != "enforce"
    elif any(row["code"] == "candidate_quarantined" for row in triggers):
        action = "candidate_quarantined"
        ok = mode != "enforce"
    elif any(row["code"] == "pending_result_review" for row in triggers):
        action = "review_pending"
        ok = mode != "enforce"
    elif any(row["code"] == "dispatch_inflight" for row in triggers):
        action = "dispatch_inflight"
        ok = mode != "enforce"
    elif triggers:
        action = "replan"
        ok = mode != "enforce"
    else:
        action = "dispatch"
        ok = True
    return {
        "ok": ok,
        "action": action,
        "errors": errors,
        "authority_errors": authority_errors,
        "warnings": warnings,
        "triggers": triggers,
        "plan": plan,
    }


def goal_focus_prompt_addon(run_dir: str | Path) -> str:
    root = Path(run_dir)
    if not is_goal_focus_enabled(root):
        return ""
    contract = load_goal_contract(root)
    plan = load_current_plan(root)
    if goal_focus_mode(root) == "monitor":
        return "\n".join(
            [
                "GOAL-FOCUS v2 MONITOR (observational only):",
                f"Recorded goal: {_clean_text(contract.get('goal'), 1200)}",
                f"Observed v2 plan: {render_current_path(plan)}",
                "Do not treat this observed plan as dispatch authority, do not stage a candidate, "
                "and do not change legacy banking behavior. Follow the legacy recovery/goal_priority "
                "path; the host records v2 drift and replan findings only.",
            ]
        )
    obligations = contract.get("obligations") if isinstance(contract.get("obligations"), dict) else {}
    compute_policy = (
        plan.get("compute_policy") if isinstance(plan.get("compute_policy"), dict) else {}
    )
    allowed_compute = sorted(_policy_allowed(compute_policy))
    forbidden_compute = sorted(_compute_services(compute_policy.get("forbidden_services")))
    compute_line = (
        "Allowed compute services: "
        + (", ".join(allowed_compute) if allowed_compute else "no plan-specific allowlist")
        + "; forbidden services: "
        + (", ".join(forbidden_compute) if forbidden_compute else "none recorded")
        + ". Report every used service, or explicitly report no compute."
    )
    targets = []
    for oid in plan.get("target_obligation_ids") or []:
        node = obligations.get(str(oid))
        if isinstance(node, dict):
            targets.append(f"{oid}: {node.get('description', '')} [{node.get('status', '')}]")
    return "\n".join(
        [
            "GOAL-FOCUS v2 (authoritative):",
            f"Goal: {_clean_text(contract.get('goal'), 1200)}",
            f"Plan: {render_current_path(plan)}",
            f"Objective: {_clean_text(plan.get('objective_id')) or 'not recorded'}",
            f"Target obligations: {'; '.join(targets) if targets else 'none selected'}",
            f"Scope lock: {_clean_text(plan.get('scope_lock')) or 'none'}",
            f"Falsifier: {_clean_text(plan.get('falsifier')) or 'none recorded'}",
            compute_line,
            "Execute only the bounded next action. Report campaign_delta separately from global_delta.",
            "Global progress requires evidence-backed discharge of a named bridge or terminal obligation.",
            "Stage the result for independent review; do not claim it is banked before finalization.",
        ]
    )


def initialize_goal_focus(
    run_dir: str | Path, *, goal: str, success_criteria: str | Sequence[str], mode: str = "enforce"
) -> dict[str, Any]:
    root = Path(run_dir)
    if mode not in ENFORCEMENT_MODES:
        raise ValueError(f"mode must be one of {sorted(ENFORCEMENT_MODES)}")
    existing = [name for name in (GOAL_CONTRACT_FILE, APPROACH_REGISTRY_FILE, CURRENT_PLAN_FILE) if (root / name).exists()]
    if existing:
        raise ValueError("Goal-Focus is already initialized: " + ", ".join(existing))
    contract = default_goal_contract(goal, success_criteria)
    registry = default_approach_registry()
    plan = default_current_plan(mode=mode)
    plan["compute_policy"] = _resolve_reviewed_compute_policy(root, plan, {})
    event = {
        "schema_version": DIRECTION_DECISION_SCHEMA,
        "event_id": _stable_id("direction", "initialize", contract["goal"], contract["created_at"]),
        "decision_id": _stable_id("decision", "initialize", contract["goal"], contract["created_at"]),
        "decision_type": "initialize",
        "enforcement_mode": plan["enforcement_mode"],
        "mode_plan_fingerprint": _object_fingerprint(plan),
        "trigger": "new_loop",
        "plan_revision": plan["plan_revision"],
        "goal_revision": contract["goal_revision"],
        "registry_revision": registry["registry_revision"],
        "timestamp": utc_now(),
    }
    tx = commit_transaction(
        root,
        json_files={
            GOAL_CONTRACT_FILE: contract,
            APPROACH_REGISTRY_FILE: registry,
            CURRENT_PLAN_FILE: plan,
        },
        jsonl_appends={DIRECTION_DECISIONS_FILE: [event]},
        expected_revisions={
            GOAL_CONTRACT_FILE: ("goal_revision", 0),
            APPROACH_REGISTRY_FILE: ("registry_revision", 0),
            CURRENT_PLAN_FILE: ("plan_revision", 0),
        },
        expected_absent=[
            GOAL_CONTRACT_FILE,
            APPROACH_REGISTRY_FILE,
            CURRENT_PLAN_FILE,
            DIRECTION_DECISIONS_FILE,
        ],
    )
    reconcile_goal_focus(root, apply=True)
    return {
        "status": "initialized",
        "paths": {
            "goal_contract": str(root / GOAL_CONTRACT_FILE),
            "approach_registry": str(root / APPROACH_REGISTRY_FILE),
            "current_plan": str(root / CURRENT_PLAN_FILE),
            "direction_decisions": str(root / DIRECTION_DECISIONS_FILE),
        },
        "bundle": load_goal_focus(root),
        "transaction": tx,
    }


def _review_adjusted_registry(
    registry: Mapping[str, Any],
    payloads: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], set[str]]:
    """Rebuild the ephemeral interval portfolio from raw strategy advice."""

    adjusted = copy.deepcopy(dict(registry))
    observations: dict[str, dict[str, list[tuple[float, float]]]] = {}
    mentioned: set[str] = set()
    for payload in payloads.values():
        for candidate in payload.get("candidates") or []:
            if not isinstance(candidate, Mapping):
                continue
            approach_id = _clean_text(candidate.get("approach_id"))
            if not approach_id:
                continue
            mentioned.add(approach_id)
            factor_rows = observations.setdefault(approach_id, {})
            estimates = candidate.get("estimates")
            if not isinstance(estimates, Mapping):
                continue
            for factor, bounds in estimates.items():
                if not isinstance(bounds, Mapping):
                    continue
                registry_factor = (
                    "goal_resolution"
                    if str(factor) == "goal_resolution_contribution"
                    else str(factor)
                )
                lower = float(bounds.get("lower"))
                upper = float(bounds.get("upper"))
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


def _validate_direction_review_boundary(
    root: Path,
    registry: Mapping[str, Any],
    selection: Mapping[str, Any],
    review: Mapping[str, Any],
    *,
    selected_campaign_id: str,
    selected_approach_id: str,
    selected_candidate: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Validate host-associated raw advice and recompute the committed choice."""

    try:
        import panel_parent as panel_review  # type: ignore
    except ImportError:  # pragma: no cover - package-style import
        from . import panel_parent as panel_review  # type: ignore

    if review.get("schema_version") != "direction_review.v2":
        raise ValueError(
            "direction selection requires a host-owned direction_review.v2"
        )
    if _clean_text(review.get("status")).lower() != "passed":
        raise ValueError("direction selection requires a passed independent review")
    if review.get("different_family") is not True:
        raise ValueError("every direction commit requires a genuinely different-family review")
    raw_payloads = review.get("provider_advice")
    if not isinstance(raw_payloads, Mapping) or not raw_payloads:
        raise ValueError("direction_review.v2 requires non-empty provider_advice")
    if any(
        not _clean_text(provider) or not isinstance(payload, Mapping)
        for provider, payload in raw_payloads.items()
    ):
        raise ValueError("direction_review.v2 provider_advice is invalid")
    payloads = {
        _clean_text(provider): payload
        for provider, payload in raw_payloads.items()
    }
    declared_providers = {
        _clean_text(provider)
        for provider in review.get("providers") or []
        if _clean_text(provider)
    }
    if declared_providers != set(payloads):
        raise ValueError("direction_review.v2 providers do not match provider_advice")
    for provider, payload in payloads.items():
        errors = panel_review.validate_strategy_advice(payload)
        if errors:
            raise ValueError(
                f"invalid strategy_advice.v1 from {provider}: " + "; ".join(errors)
            )

    raw_primary_attestation = review.get("primary_execution_attestation")
    raw_provider_attestations = review.get("provider_execution_attestations")
    if not isinstance(raw_primary_attestation, Mapping) or not isinstance(
        raw_provider_attestations, Mapping
    ):
        raise ValueError(
            "direction_review.v2 requires host executable attestations"
        )
    if set(raw_provider_attestations) != set(payloads):
        raise ValueError(
            "direction_review.v2 provider attestations do not match provider_advice"
        )
    try:
        primary_attestation = panel_review.revalidate_provider_executable_attestation(
            raw_primary_attestation,
            forbidden_roots=(root,),
        )
        provider_attestations = {
            provider: panel_review.revalidate_provider_executable_attestation(
                raw_provider_attestations[provider],
                forbidden_roots=(root,),
            )
            for provider in sorted(payloads)
        }
    except (OSError, ValueError, panel_review.PanelIsolationError) as exc:
        raise ValueError(
            f"direction_review.v2 executable attestation is invalid: {exc}"
        ) from exc
    if dict(raw_primary_attestation) != primary_attestation or any(
        dict(raw_provider_attestations[provider]) != attestation
        for provider, attestation in provider_attestations.items()
    ):
        raise ValueError(
            "direction_review.v2 executable attestations are not exact host identities"
        )
    if any(
        _clean_text(attestation.get("provider")) != provider
        or _clean_text(attestation.get("family")) == "unverified"
        for provider, attestation in provider_attestations.items()
    ):
        raise ValueError(
            "direction_review.v2 contains an unverified or mismatched reviewer identity"
        )

    primary_provider = _clean_text(primary_attestation.get("provider"))
    primary_family = _clean_text(primary_attestation.get("family"))
    synthesis = review.get("structured_synthesis")
    if not isinstance(synthesis, Mapping):
        raise ValueError("direction_review.v2 requires structured_synthesis")
    if (
        not primary_provider
        or primary_family == "unverified"
        or _clean_text(review.get("primary_provider")) != primary_provider
        or _clean_text(review.get("primary_family")) != primary_family
        or _clean_text(synthesis.get("primary_provider")) != primary_provider
        or _clean_text(synthesis.get("primary_family")) != primary_family
    ):
        raise ValueError("direction_review.v2 primary executable identity is inconsistent")
    if {
        _clean_text(provider)
        for provider in synthesis.get("valid_providers") or []
        if _clean_text(provider)
    } != set(payloads):
        raise ValueError("strategy synthesis valid providers are inconsistent")
    actual_different = {
        provider
        for provider in payloads
        if _clean_text(provider_attestations[provider].get("family"))
        not in {"unverified", primary_family}
    }
    if {
        _clean_text(provider)
        for provider in synthesis.get("different_family_valid_providers") or []
        if _clean_text(provider)
    } != actual_different:
        raise ValueError("strategy synthesis different-family providers are inconsistent")
    if any(
        payloads[provider].get("decision") == "no_viable_candidate"
        for provider in actual_different
    ):
        raise ValueError("different-family strategy advice found no viable candidate")

    adjusted, mentioned = _review_adjusted_registry(registry, payloads)
    recomputed = select_direction(adjusted, run_dir=root)
    if (
        recomputed.get("status") != "selected"
        or _clean_text(recomputed.get("selected_campaign_id")) != selected_campaign_id
        or _clean_text(recomputed.get("selected_approach_id")) != selected_approach_id
        or selected_approach_id not in mentioned
    ):
        raise ValueError("direction selection disagrees with recomputed reviewed portfolio")

    independent_candidates: list[tuple[int, str, dict[str, Any]]] = []
    for provider in sorted(actual_different):
        for candidate in payloads[provider].get("candidates") or []:
            if (
                isinstance(candidate, dict)
                and _clean_text(candidate.get("approach_id")) == selected_approach_id
            ):
                independent_candidates.append(
                    (int(candidate.get("rank") or 999), provider, candidate)
                )
    independent_candidates.sort(key=lambda row: (row[0], row[1]))
    if not independent_candidates:
        raise ValueError("selected direction lacks different-family raw advice")
    expected_candidate = independent_candidates[0][2]
    if dict(selected_candidate) != expected_candidate:
        raise ValueError(
            "selected_candidate is not the exact different-family reviewed action"
        )
    supporting_providers = {
        provider for _rank, provider, _candidate in independent_candidates
    }
    declared_different = {
        _clean_text(provider)
        for provider in review.get("different_family_providers") or []
        if _clean_text(provider)
    }
    if declared_different != supporting_providers:
        raise ValueError(
            "direction_review.v2 different_family_providers do not match selected advice"
        )
    if {
        _clean_text(provider)
        for provider in selection.get("reviewed_by") or []
        if _clean_text(provider)
    } != set(payloads):
        raise ValueError("selection reviewed_by does not match raw strategy providers")
    if {
        _clean_text(provider)
        for provider in selection.get("reviewed_by_different_family") or []
        if _clean_text(provider)
    } != supporting_providers:
        raise ValueError(
            "selection different-family provenance does not match raw strategy advice"
        )
    expected_families = {
        _clean_text(attestation.get("family"))
        for attestation in provider_attestations.values()
    }
    if {
        _clean_text(family)
        for family in review.get("reviewer_families") or []
        if _clean_text(family)
    } != expected_families:
        raise ValueError("direction_review.v2 reviewer_families are inconsistent")
    return primary_family, primary_attestation


def commit_selected_direction(
    run_dir: str | Path,
    selection: Mapping[str, Any],
    review: Mapping[str, Any],
    trigger: str,
    *,
    expected_plan_revision: int | None = None,
) -> dict[str, Any]:
    root = Path(run_dir)
    contract = load_goal_contract(root)
    registry = load_approach_registry(root)
    old_plan = load_current_plan(root)
    reviewed_authority = review.get("authority_snapshot")
    if not isinstance(reviewed_authority, Mapping):
        raise ValueError("direction selection requires an authority-bound strategy review")
    if reviewed_authority.get("schema_version") != "strategy_authority_binding.v1":
        raise ValueError("direction strategy authority binding is invalid")
    live_bindings = {
        "goal_revision": contract.get("goal_revision"),
        "registry_revision": registry.get("registry_revision"),
        "plan_revision": old_plan.get("plan_revision"),
        "goal_contract_fingerprint": _object_fingerprint(contract),
        "approach_registry_fingerprint": _object_fingerprint(registry),
        "current_plan_fingerprint": _object_fingerprint(old_plan),
    }
    for field, observed in live_bindings.items():
        if reviewed_authority.get(field) != observed:
            raise RevisionConflict(
                f"strategy-reviewed authority changed before direction commit: {field}"
            )
    live_plan_revision = _require_nonnegative_int(
        old_plan.get("plan_revision"), "current_plan.plan_revision"
    )
    expected = (
        live_plan_revision
        if expected_plan_revision is None
        else _require_nonnegative_int(
            expected_plan_revision, "expected_plan_revision"
        )
    )
    if live_plan_revision != expected:
        raise RevisionConflict("current_plan changed before direction commit")
    campaign_id = _clean_text(selection.get("selected_campaign_id"))
    approach_id = _clean_text(selection.get("selected_approach_id"))
    if not campaign_id or not approach_id:
        raise ValueError("selection requires selected_campaign_id and selected_approach_id")
    approach = _find_approach(registry, campaign_id, approach_id)
    eligibility_error = _approach_ineligibility(
        registry, campaign_id, approach_id, run_dir=root
    )
    if approach is None or eligibility_error:
        raise ValueError(
            "selection does not reference an eligible registered approach"
            + (f": {eligibility_error}" if eligibility_error else "")
        )
    selected_candidate = selection.get("selected_candidate")
    if not isinstance(selected_candidate, dict):
        selected_candidate = {}
        for raw in selection.get("candidates") or []:
            if isinstance(raw, dict) and _clean_text(raw.get("approach_id")) == approach_id:
                selected_candidate = dict(raw)
                break
    if selected_candidate:
        override_id = _clean_text(selected_candidate.get("approach_id"))
        override_campaign = _clean_text(selected_candidate.get("campaign_id"))
        if override_id and override_id != approach_id:
            raise ValueError("selected_candidate approach_id disagrees with selected_approach_id")
        if override_campaign and override_campaign != campaign_id:
            raise ValueError("selected_candidate campaign_id disagrees with selected_campaign_id")

    dispatch_provider_family, dispatch_provider_attestation = (
        _validate_direction_review_boundary(
            root,
            registry,
            selection,
            review,
            selected_campaign_id=campaign_id,
            selected_approach_id=approach_id,
            selected_candidate=selected_candidate,
        )
    )

    def reviewed_value(name: str, default: Any = None) -> Any:
        value = selected_candidate.get(name) if selected_candidate else None
        return value if value is not None and value != "" else approach.get(name, default)

    now = utc_now()
    next_revision = expected + 1
    decision_id = _stable_id("decision", next_revision, campaign_id, approach_id, trigger)
    horizon = max(1, _positive_int(reviewed_value("horizon_iterations", 1), 1))
    valid_through = reviewed_value("valid_through_iteration")
    if valid_through in {None, ""}:
        valid_through = max(
            (_positive_int(row.get("iteration")) for row in _finalized_rows(root)),
            default=0,
        ) + horizon
    next_action = _clean_text(reviewed_value("next_action"))
    registry_next_action = _clean_text(approach.get("next_action"))
    # Prefer a machine-gated registry next_action when the panel softens to
    # inspect/sketch-only text (formal campaigns otherwise thrash forever).
    if _is_machine_gated_next_action(registry_next_action) and not _is_machine_gated_next_action(
        next_action
    ):
        next_action = registry_next_action
    falsifier = _clean_text(reviewed_value("falsifier"))
    if not next_action:
        raise ValueError("selected direction requires a non-empty reviewed or registered next_action")
    # Prefer approach compute_policy when present so local lake pin is not lost.
    compute_reviewed = reviewed_value("compute_policy", None)
    if compute_reviewed in (None, "") and isinstance(approach.get("compute_policy"), Mapping):
        compute_reviewed = approach.get("compute_policy")
    if compute_reviewed in (None, ""):
        compute_reviewed = old_plan.get("compute_policy") or {}
    compute_policy = _resolve_reviewed_compute_policy(
        root,
        old_plan,
        compute_reviewed,
    )
    plan = copy.deepcopy(old_plan)
    plan.update(
        {
            "plan_id": f"plan-r{next_revision}-{decision_id[-8:]}",
            "decision_id": decision_id,
            "plan_revision": next_revision,
            "goal_revision": contract["goal_revision"],
            "registry_revision": registry["registry_revision"],
            "state": "active",
            "campaign_id": campaign_id,
            "approach_id": approach_id,
            "objective_id": _clean_text(reviewed_value("objective_id") or reviewed_value("objective")),
            "target_obligation_ids": list(reviewed_value("target_obligation_ids", []) or []),
            "residual_id": _clean_text(reviewed_value("residual_id")),
            "scope_lock": _clean_text(reviewed_value("scope_lock")),
            "next_action": next_action,
            "expected_artifacts": list(reviewed_value("expected_artifacts", []) or []),
            "compute_policy": compute_policy,
            "dispatch_provider_family": dispatch_provider_family,
            "dispatch_provider_attestation": copy.deepcopy(
                dispatch_provider_attestation
            ),
            "falsifier": falsifier,
            "horizon_iterations": horizon,
            "valid_through_iteration": valid_through,
            "trip_wires": list(reviewed_value("trip_wires", []) or []),
            "trip_wires_triggered": [],
            "selection": {
                **copy.deepcopy(dict(selection)),
                "selected_candidate": copy.deepcopy(selected_candidate),
            },
            "selected_at": now,
            "updated_at": now,
        }
    )
    previous_state, state_hash, previous_recovery, recovery_hash = (
        _managed_view_snapshot(root)
    )
    state, recovery = _managed_postimages(
        contract,
        registry,
        plan,
        state_preimage=previous_state,
        recovery_preimage=previous_recovery,
    )
    decision = {
        "schema_version": DIRECTION_DECISION_SCHEMA,
        "event_id": decision_id,
        "decision_id": decision_id,
        "decision_type": "select_direction",
        "enforcement_mode": plan["enforcement_mode"],
        "mode_plan_fingerprint": _object_fingerprint(plan),
        "trigger": _clean_text(trigger),
        "previous_plan_revision": expected,
        "plan_revision": plan["plan_revision"],
        "goal_revision": plan["goal_revision"],
        "registry_revision": plan["registry_revision"],
        "campaign_id": campaign_id,
        "approach_id": approach_id,
        "selection": copy.deepcopy(dict(selection)),
        "review": copy.deepcopy(dict(review)),
        "plan_fingerprint": _plan_authority_fingerprint(plan),
        "timestamp": now,
    }
    json_files: dict[str, Any] = {CURRENT_PLAN_FILE: plan}
    if state:
        json_files["loop_state.json"] = state
    expected_hashes = {
        GOAL_CONTRACT_FILE: str(
            reviewed_authority.get("goal_contract_source_sha256") or ""
        ),
        APPROACH_REGISTRY_FILE: str(
            reviewed_authority.get("approach_registry_source_sha256") or ""
        ),
        CURRENT_PLAN_FILE: str(
            reviewed_authority.get("current_plan_source_sha256") or ""
        ),
    }
    expected_absent: list[str] = []
    for filename, digest in (
        ("loop_state.json", state_hash),
        ("recovery.md", recovery_hash),
    ):
        if digest is None:
            expected_absent.append(filename)
        else:
            expected_hashes[filename] = digest
    tx = commit_transaction(
        root,
        json_files=json_files,
        text_files={"recovery.md": recovery},
        jsonl_appends={DIRECTION_DECISIONS_FILE: [decision]},
        expected_revisions={
            CURRENT_PLAN_FILE: ("plan_revision", expected),
            GOAL_CONTRACT_FILE: ("goal_revision", contract.get("goal_revision")),
            APPROACH_REGISTRY_FILE: ("registry_revision", registry.get("registry_revision")),
        },
        expected_hashes=expected_hashes,
        expected_absent=expected_absent,
        transaction_id=decision_id,
    )
    return {"status": "committed", "plan": plan, "decision": decision, "transaction": tx}


def _extract_campaign(text: str, known: Iterable[str]) -> str:
    lowered = text.lower()
    known_list = [cid for cid in known if cid]
    # Prefer positive path instructions. Legacy prose often names an obsolete
    # campaign first in a negative clause ("do not restart campaign A2") and
    # only then names the actual pivot. A generic earliest-mention rule would
    # silently resurrect the stale campaign.
    positive_matches: list[tuple[int, str]] = []
    for cid in known_list:
        positive = (
            rf"\b(?:pivot(?:\s+to)?|switch(?:\s+to)?|move(?:\s+to)?|"
            rf"proceed(?:\s+with)?|continue(?:\s+with)?|execute|select|choose|"
            rf"use|resume|focus(?:\s+on)?)\s+(?:the\s+)?campaign\s+"
            rf"[`'\"]?{re.escape(cid.lower())}(?![\w-])"
        )
        for match in re.finditer(positive, lowered):
            positive_matches.append((match.start(), cid))
    if positive_matches:
        return sorted(positive_matches, key=lambda item: (item[0], -len(item[1]), item[1]))[0][1]

    mentions: list[tuple[int, str]] = []
    for cid in known_list:
        pattern = rf"\bcampaign\s+[`'\"]?{re.escape(cid.lower())}(?![\w-])"
        for match in re.finditer(pattern, lowered):
            clause = re.split(r"[.!?;\n]", lowered[: match.start()])[-1]
            if re.search(r"\b(?:do\s+not|don't|never|avoid|exclude|without|not)\b", clause):
                continue
            mentions.append((match.start(), cid))
    if mentions:
        return sorted(mentions, key=lambda item: (item[0], -len(item[1]), item[1]))[0][1]

    # Last-resort compatibility for legacy fields containing only the id, but
    # still reject an id whose current clause is explicitly negative.
    bare: list[tuple[int, str]] = []
    for cid in known_list:
        pattern = rf"(?<![\w-]){re.escape(cid.lower())}(?![\w-])"
        for match in re.finditer(pattern, lowered):
            clause = re.split(r"[.!?;\n]", lowered[: match.start()])[-1]
            if re.search(r"\b(?:do\s+not|don't|never|avoid|exclude|without|not)\b", clause):
                continue
            bare.append((match.start(), cid))
    return sorted(bare, key=lambda item: (item[0], -len(item[1]), item[1]))[0][1] if bare else ""


def _migration_source_snapshot(run_dir: Path) -> dict[str, bytes | None]:
    """Capture every legacy migration input as exact, no-follow bytes."""

    snapshot: dict[str, bytes | None] = {}
    for rel in LEGACY_MIGRATION_SOURCE_FILES:
        try:
            snapshot[rel] = _read_regular_bytes(run_dir / rel)
        except FileNotFoundError:
            snapshot[rel] = None
    return snapshot


def _migration_source_fingerprints(
    snapshot: Mapping[str, bytes | None],
) -> dict[str, dict[str, Any]]:
    return {
        rel: (
            {
                "present": True,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            if payload is not None
            else {"present": False}
        )
        for rel, payload in snapshot.items()
    }


def _json_postimage_bytes(value: Mapping[str, Any]) -> bytes:
    """Serialize one JSON object exactly as the transaction layer does."""

    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _safe_migration_backup_relative_path(
    value: Any, *, backup_root: Path | None = None
) -> Path:
    raw = _clean_text(value)
    rel = Path(raw)
    windows = PureWindowsPath(raw)
    if (
        rel.is_absolute()
        or bool(windows.drive)
        or bool(windows.root)
        or "\\" in raw
        or any(ord(char) < 32 or ord(char) == 127 for char in raw)
        or any(part in {"", ".", ".."} for part in rel.parts)
        or len(rel.parts) < 2
        or rel.parts[0] != ".goal_focus_backups"
        or re.fullmatch(
            r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}", rel.parts[1]
        )
        is None
    ):
        raise ValueError("migration backup path is outside its private namespace")
    if backup_root is not None and (
        len(rel.parts) <= len(backup_root.parts)
        or rel.parts[: len(backup_root.parts)] != backup_root.parts
    ):
        raise ValueError("migration backup artifact is outside its bound backup root")
    return rel


def _migration_backup_metadata(
    *,
    backup_root: Path,
    source_snapshot: Mapping[str, bytes | None],
    transaction_id: str,
    decision_id: str,
    created_at: str,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Build the durable migration manifest and exact backup postimages."""

    sources: list[dict[str, Any]] = []
    backup_bytes: dict[str, bytes] = {}
    for rel in LEGACY_MIGRATION_SOURCE_FILES:
        payload = source_snapshot.get(rel)
        record: dict[str, Any] = {"source_path": rel, "present": payload is not None}
        if payload is not None:
            backup_rel = (backup_root / rel).as_posix()
            record.update(
                {
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "backup_relative_path": backup_rel,
                }
            )
            backup_bytes[backup_rel] = payload
        sources.append(record)
    manifest_rel = (backup_root / "backup_manifest.json").as_posix()
    manifest = {
        "schema_version": MIGRATION_BACKUP_SCHEMA,
        "created_at": created_at,
        "migration_transaction_id": transaction_id,
        "decision_id": decision_id,
        "backup_relative_path": backup_root.as_posix(),
        "manifest_relative_path": manifest_rel,
        "sources": sources,
        "restore_instructions": [
            "Keep the loop quiesced and validate the backup manifest before restoring.",
            "Restore each present source from its backup_relative_path to source_path.",
            "Remove newly-created Goal-Focus v2 authority only after preserving it for audit.",
            "Run Goal-Focus validation before starting a driver.",
        ],
    }
    backup_bytes[manifest_rel] = _json_postimage_bytes(manifest)
    metadata = {
        "schema_version": MIGRATION_BACKUP_SCHEMA,
        "backup_relative_path": backup_root.as_posix(),
        "manifest_relative_path": manifest_rel,
        "manifest_sha256": hashlib.sha256(backup_bytes[manifest_rel]).hexdigest(),
        "migration_transaction_id": transaction_id,
        "restore_instructions": list(manifest["restore_instructions"]),
        "sources": copy.deepcopy(sources),
    }
    return metadata, backup_bytes


def _verify_migration_backup(
    run_dir: Path,
    *,
    metadata: Mapping[str, Any],
    source_snapshot: Mapping[str, bytes | None],
    decision_id: str,
) -> None:
    """Verify the committed backup, manifest, and durable decision binding."""

    if metadata.get("schema_version") != MIGRATION_BACKUP_SCHEMA:
        raise ValueError("migration backup metadata schema is invalid")
    backup_root = _safe_migration_backup_relative_path(
        metadata.get("backup_relative_path")
    )
    if len(backup_root.parts) != 2:
        raise ValueError("migration backup root has unexpected descendants")
    manifest_path = _safe_migration_backup_relative_path(
        metadata.get("manifest_relative_path"), backup_root=backup_root
    )
    if manifest_path != backup_root / "backup_manifest.json":
        raise ValueError("migration backup manifest path is not canonical")
    manifest_rel = manifest_path.as_posix()
    manifest_payload = _read_regular_bytes(run_dir / manifest_rel)
    if hashlib.sha256(manifest_payload).hexdigest() != metadata.get("manifest_sha256"):
        raise ValueError("migration backup manifest digest mismatch")
    manifest = _decode_object_bytes(run_dir / manifest_rel, manifest_payload)
    if (
        manifest.get("schema_version") != MIGRATION_BACKUP_SCHEMA
        or manifest.get("decision_id") != decision_id
        or manifest.get("migration_transaction_id")
        != metadata.get("migration_transaction_id")
        or manifest.get("backup_relative_path")
        != metadata.get("backup_relative_path")
        or manifest.get("manifest_relative_path") != manifest_rel
        or manifest.get("sources") != metadata.get("sources")
        or manifest.get("restore_instructions")
        != metadata.get("restore_instructions")
    ):
        raise ValueError("migration backup manifest binding mismatch")
    records = manifest.get("sources")
    if not isinstance(records, list) or len(records) != len(LEGACY_MIGRATION_SOURCE_FILES):
        raise ValueError("migration backup manifest source inventory is incomplete")
    by_source: dict[str, Mapping[str, Any]] = {}
    for raw_record in records:
        if not isinstance(raw_record, Mapping):
            raise ValueError("migration backup manifest source row is invalid")
        source_path = _clean_text(raw_record.get("source_path"))
        if source_path in by_source:
            raise ValueError("migration backup manifest repeats a source path")
        by_source[source_path] = raw_record
    for rel in LEGACY_MIGRATION_SOURCE_FILES:
        record = by_source.get(rel)
        payload = source_snapshot.get(rel)
        if not isinstance(record, Mapping) or record.get("present") is not (payload is not None):
            raise ValueError(f"migration backup source binding mismatch: {rel}")
        if payload is None:
            continue
        backup_path = _safe_migration_backup_relative_path(
            record.get("backup_relative_path"), backup_root=backup_root
        )
        if backup_path != backup_root / rel:
            raise ValueError(f"migration backup path mismatch: {rel}")
        backup_rel = backup_path.as_posix()
        if _read_regular_bytes(run_dir / backup_rel) != payload:
            raise ValueError(f"migration backup byte mismatch: {rel}")
        if (
            record.get("size_bytes") != len(payload)
            or record.get("sha256") != hashlib.sha256(payload).hexdigest()
        ):
            raise ValueError(f"migration backup fingerprint mismatch: {rel}")
    decisions = _read_jsonl(run_dir / DIRECTION_DECISIONS_FILE)
    matches = [row for row in decisions if row.get("decision_id") == decision_id]
    if len(matches) != 1 or matches[0].get("migration_backup") != dict(metadata):
        raise ValueError("migration decision does not durably bind the backup manifest")


def _migration_snapshot_object(
    snapshot: Mapping[str, bytes | None], rel: str
) -> dict[str, Any]:
    payload = snapshot.get(rel)
    if payload is None:
        return {}
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{rel} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{rel} must contain a JSON object")
    return value


def _migration_snapshot_jsonl(
    snapshot: Mapping[str, bytes | None], rel: str
) -> list[dict[str, Any]]:
    payload = snapshot.get(rel)
    if payload is None:
        return []
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{rel} is not UTF-8: {exc}") from exc
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{rel} line {index} is invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{rel} line {index} must contain a JSON object")
        rows.append(value)
    return rows


def _legacy_inputs(
    source_snapshot: Mapping[str, bytes | None],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    state = _migration_snapshot_object(source_snapshot, "loop_state.json")
    file_cfg = _migration_snapshot_object(source_snapshot, "goal_priority.json")
    standing = state.get("standing_orders") if isinstance(state.get("standing_orders"), dict) else {}
    standing_gp = standing.get("goal_priority") if isinstance(standing.get("goal_priority"), dict) else {}
    cfg = dict(file_cfg)
    cfg.update({key: value for key, value in standing_gp.items() if value is not None})
    return state, cfg, file_cfg


def _migration_documents(
    run_dir: Path,
    active_campaign: str | None,
    source_snapshot: Mapping[str, bytes | None],
) -> tuple[dict[str, Any], dict[str, Any]]:
    state, cfg, file_cfg = _legacy_inputs(source_snapshot)
    goal = _clean_text(state.get("goal")) or "Legacy autonomous research goal"
    success = _clean_text(state.get("success_criteria")) or "Satisfy the recorded research goal."
    contract = default_goal_contract(goal, success)
    registry = default_approach_registry()
    legacy_registry = cfg.get("campaign_registry") if isinstance(cfg.get("campaign_registry"), dict) else {}
    known: set[str] = {str(key) for key in legacy_registry}
    known |= {str(item) for item in cfg.get("next_campaigns_ordered") or [] if str(item)}
    primary = _clean_text(cfg.get("primary_campaign"))
    if primary:
        known.add(primary)
    closed_ids: set[str] = set()
    for raw in cfg.get("closed_campaigns") or []:
        if isinstance(raw, dict):
            cid = _clean_text(raw.get("id"))
        else:
            cid = _clean_text(raw)
        if cid:
            known.add(cid)
            closed_ids.add(cid)

    signals: list[dict[str, str]] = []
    path_text = _clean_text(state.get("next_preferred_path"), 10000)
    recovery_payload = source_snapshot.get("recovery.md")
    try:
        recovery_text = recovery_payload.decode("utf-8") if recovery_payload is not None else ""
    except UnicodeDecodeError as exc:
        raise ValueError(f"recovery.md is not UTF-8: {exc}") from exc
    ledger_rows = _migration_snapshot_jsonl(source_snapshot, "iterations.jsonl")
    latest_campaign = ""
    for row in reversed(ledger_rows):
        latest_campaign = _clean_text(row.get("campaign_id"))
        if latest_campaign:
            known.add(latest_campaign)
            break
    audit = _migration_snapshot_object(
        source_snapshot, "driver_logs/goal_priority_hard_replan.json"
    )
    audit_campaign = _clean_text(audit.get("campaign_id"))
    if audit_campaign:
        known.add(audit_campaign)
    # Re-extract after all campaign ids are known.
    path_campaign = _extract_campaign(path_text, known)
    recovery_campaign = _extract_campaign(recovery_text, known)
    for source, value in (
        ("current_path", path_campaign),
        ("recovery", recovery_campaign),
        ("latest_finalized_ledger", latest_campaign),
        ("hard_replan_audit", audit_campaign),
    ):
        if value:
            signals.append({"source": source, "campaign_id": value})
    dynamic_values = {row["campaign_id"] for row in signals}
    override = _clean_text(active_campaign)
    if override:
        known.add(override)
        chosen = override
        resolution = "explicit_override"
    elif len(dynamic_values) == 1:
        chosen = next(iter(dynamic_values))
        resolution = "dynamic_signals_agree"
    elif len(dynamic_values) > 1:
        chosen = ""
        resolution = "ambiguous_dynamic_signals"
    else:
        chosen = primary
        resolution = "legacy_primary_fallback" if primary else "no_direction_signal"

    campaigns: dict[str, Any] = {}
    for cid in sorted(known):
        legacy = legacy_registry.get(cid) if isinstance(legacy_registry.get(cid), dict) else {}
        approach_id = f"{cid}-legacy"
        status = "closed" if cid in closed_ids else "eligible"
        objective = _clean_text(legacy.get("objective"))
        next_action = path_text if cid == path_campaign and path_text else objective or f"Review and refine legacy campaign {cid}."
        campaigns[cid] = {
            "id": cid,
            "title": _clean_text(legacy.get("title")) or cid,
            "status": status,
            "objective": objective,
            "approaches": {
                approach_id: {
                    "id": approach_id,
                    "campaign_id": cid,
                    "status": status,
                    "mechanism": _clean_text(legacy.get("mechanism")) or "legacy campaign; mechanism not yet normalized",
                    "objective": objective,
                    "next_action": next_action,
                    "target_obligation_ids": [],
                    "dependencies": [],
                    "diversity_tags": ["legacy"],
                    "estimates": {},
                    "evidence_for": [],
                    "evidence_against": [],
                    "reopen_condition": "new reviewed mechanism" if status == "closed" else "",
                    "last_estimated_iteration": None,
                }
            },
        }
    registry["campaigns"] = campaigns
    registry["migration_provenance"] = {
        "goal_priority_file_present": bool(file_cfg),
        "standing_orders_present": bool(cfg),
        "legacy_primary_campaign": primary,
        "signals": signals,
        "resolution": resolution,
    }
    registry["updated_at"] = utc_now()
    plan = default_current_plan(mode="enforce")
    if chosen:
        approach_id = f"{chosen}-legacy"
        approach = _find_approach(registry, chosen, approach_id) or {}
        plan.update(
            {
                "state": "provisional",
                "campaign_id": chosen,
                "approach_id": approach_id,
                "objective_id": _clean_text(approach.get("objective")),
                "next_action": _clean_text(approach.get("next_action")) or "Run fresh structured strategy review.",
                "scope_lock": _clean_text(approach.get("scope_lock")),
                "selection": {"migration_resolution": resolution, "signals": signals},
            }
        )
    else:
        plan["state"] = "needs_replan"
        plan["selection"] = {"migration_resolution": resolution, "signals": signals}
    plan["compute_policy"] = _resolve_reviewed_compute_policy(run_dir, plan, {})
    plan["updated_at"] = utc_now()
    report = {
        "schema_version": "goal_focus_migration_report.v2",
        "status": "ready" if chosen else "ambiguous",
        "selected_campaign_id": chosen,
        "legacy_primary_campaign": primary,
        "signals": signals,
        "resolution": resolution,
        "provenance": {
            "goal": "loop_state.goal",
            "success_criteria": "loop_state.success_criteria",
            "campaign_registry": "goal_priority.json + standing_orders.goal_priority",
        },
    }
    documents = {"contract": contract, "registry": registry, "plan": plan}
    return report, documents


def plan_migration(
    run_dir: str | Path,
    *,
    active_campaign: str | None = None,
    _source_snapshot: Mapping[str, bytes | None] | None = None,
) -> dict[str, Any]:
    root = Path(run_dir)
    v2_present = [
        name
        for name in (GOAL_CONTRACT_FILE, APPROACH_REGISTRY_FILE, CURRENT_PLAN_FILE)
        if (root / name).exists()
    ]
    if v2_present:
        validation = validate_goal_focus(root, require_enabled=True)
        if validation.get("errors"):
            return {
                "schema_version": "goal_focus_migration_report.v2",
                "status": "invalid_existing_v2",
                "selected_campaign_id": "",
                "apply_allowed": False,
                "validation": validation,
                "error": "existing Goal-Focus authority is partial or invalid; repair it before migration",
            }
        plan = load_current_plan(root)
        return {
            "schema_version": "goal_focus_migration_report.v2",
            "status": "already_migrated",
            "selected_campaign_id": plan.get("campaign_id", ""),
            "apply_allowed": False,
            "validation": validation,
        }
    source_snapshot = (
        dict(_source_snapshot)
        if _source_snapshot is not None
        else _migration_source_snapshot(root)
    )
    report, documents = _migration_documents(root, active_campaign, source_snapshot)
    # Ambiguity is preserved as an explicit needs_replan state.  Migration may
    # still materialize canonical v2 authority; it must never guess a campaign.
    report["apply_allowed"] = report["status"] in {"ready", "ambiguous"}
    report["source_fingerprints"] = _migration_source_fingerprints(source_snapshot)
    report["proposed"] = documents
    return report


def migrate_v1(
    run_dir: str | Path,
    *,
    apply: bool = False,
    active_campaign: str | None = None,
    migration_claim: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(run_dir)
    if apply:
        _validate_migration_apply_guard(root, migration_claim)
    v2_present = any(
        (root / name).exists()
        for name in (GOAL_CONTRACT_FILE, APPROACH_REGISTRY_FILE, CURRENT_PLAN_FILE)
    )
    source_snapshot = None if v2_present else _migration_source_snapshot(root)
    report = plan_migration(
        root,
        active_campaign=active_campaign,
        _source_snapshot=source_snapshot,
    )
    if not apply or report.get("status") == "already_migrated":
        report["applied"] = False
        return report
    if not report.get("apply_allowed"):
        report["applied"] = False
        report["error"] = "migration inputs are not usable"
        return report
    documents = report.pop("proposed")
    contract = documents["contract"]
    registry = documents["registry"]
    plan = documents["plan"]
    if source_snapshot is None:
        report["applied"] = False
        report["error"] = "migration source snapshot is unavailable"
        return report
    _validate_migration_backup_parent(root)
    now = utc_now()
    unique_suffix = uuid.uuid4().hex
    backup_root = Path(".goal_focus_backups") / (
        f"{_compact_stamp()}-{unique_suffix}"
    )
    decision_id = _stable_id(
        "decision", "migration", plan["campaign_id"], now, unique_suffix
    )
    transaction_id = decision_id
    backup_metadata, backup_bytes = _migration_backup_metadata(
        backup_root=backup_root,
        source_snapshot=source_snapshot,
        transaction_id=transaction_id,
        decision_id=decision_id,
        created_at=now,
    )
    report["migration_backup"] = copy.deepcopy(backup_metadata)
    decision = {
        "schema_version": DIRECTION_DECISION_SCHEMA,
        "event_id": decision_id,
        "decision_id": decision_id,
        "decision_type": "migration",
        "enforcement_mode": plan["enforcement_mode"],
        "mode_plan_fingerprint": _object_fingerprint(plan),
        "trigger": "v1_to_v2",
        "campaign_id": plan.get("campaign_id"),
        "approach_id": plan.get("approach_id"),
        "plan_revision": plan["plan_revision"],
        "goal_revision": contract["goal_revision"],
        "registry_revision": registry["registry_revision"],
        "migration_transaction_id": transaction_id,
        "migration_backup": copy.deepcopy(backup_metadata),
        "provenance": report,
        "timestamp": now,
    }
    present_source_hashes = {
        rel: hashlib.sha256(payload).hexdigest()
        for rel, payload in source_snapshot.items()
        if payload is not None
    }
    absent_sources = [rel for rel, payload in source_snapshot.items() if payload is None]
    try:
        tx = commit_transaction(
            root,
            json_files={
                GOAL_CONTRACT_FILE: contract,
                APPROACH_REGISTRY_FILE: registry,
                CURRENT_PLAN_FILE: plan,
            },
            binary_files=backup_bytes,
            jsonl_appends={DIRECTION_DECISIONS_FILE: [decision]},
            expected_revisions={
                GOAL_CONTRACT_FILE: ("goal_revision", 0),
                APPROACH_REGISTRY_FILE: ("registry_revision", 0),
                CURRENT_PLAN_FILE: ("plan_revision", 0),
            },
            expected_absent=[
                GOAL_CONTRACT_FILE,
                APPROACH_REGISTRY_FILE,
                CURRENT_PLAN_FILE,
                DIRECTION_DECISIONS_FILE,
                backup_root.as_posix(),
                *backup_bytes.keys(),
                *absent_sources,
            ],
            expected_hashes=present_source_hashes,
            transaction_id=transaction_id,
        )
    except RevisionConflict as exc:
        report.update(
            {
                "status": "source_changed",
                "applied": False,
                "error": f"migration authority changed after planning: {exc}",
            }
        )
        return report
    backup_dir = str(root / backup_root)
    try:
        _verify_migration_backup(
            root,
            metadata=backup_metadata,
            source_snapshot=source_snapshot,
            decision_id=decision_id,
        )
    except (OSError, ValueError) as exc:
        report.update(
            {
                "status": "post_apply_backup_verification_failed",
                "applied": False,
                "authority_written": True,
                "recovery_required": True,
                "backup_dir": backup_dir,
                "backup_manifest": str(root / backup_metadata["manifest_relative_path"]),
                "transaction": tx,
                "error": f"migration committed but backup verification failed: {exc}",
                "recovery": (
                    "Keep the loop quiesced. Inspect the durable migration decision and "
                    "transaction journal before any restore or dispatch."
                ),
            }
        )
        return report
    try:
        reconciliation = reconcile_goal_focus(root, apply=True)
    except (OSError, ValueError, TransactionError, RevisionConflict) as exc:
        validation = validate_goal_focus(root, require_enabled=True)
        report.update(
            {
                "status": "post_apply_reconcile_failed",
                "applied": False,
                "authority_written": True,
                "recovery_required": True,
                "backup_dir": backup_dir,
                "backup_manifest": str(root / backup_metadata["manifest_relative_path"]),
                "transaction": tx,
                "validation": validation,
                "error": f"migration authority was written but projection reconciliation failed: {exc}",
                "recovery": (
                    "Keep the loop quiesced. Inspect validation, then restore the recorded "
                    "backup or repair and revalidate before dispatch."
                ),
            }
        )
        return report
    validation = validate_goal_focus(root, require_enabled=True)
    if validation.get("errors"):
        report.update(
            {
                "status": "post_apply_validation_failed",
                "applied": False,
                "authority_written": True,
                "recovery_required": True,
                "backup_dir": backup_dir,
                "backup_manifest": str(root / backup_metadata["manifest_relative_path"]),
                "transaction": tx,
                "reconciliation": reconciliation,
                "validation": validation,
                "error": "migration authority was written but failed Goal-Focus validation",
                "recovery": (
                    "Keep the loop quiesced. Inspect validation, then restore the recorded "
                    "backup or repair and revalidate before dispatch."
                ),
            }
        )
        return report
    report.update(
        {
            "status": "migrated",
            "applied": True,
            "authority_written": True,
            "recovery_required": False,
            "backup_dir": backup_dir,
            "backup_manifest": str(root / backup_metadata["manifest_relative_path"]),
            "transaction": tx,
            "reconciliation": reconciliation,
            "validation": validation,
        }
    )
    return report


def load_iteration_dispatch(run_dir: str | Path) -> dict[str, Any] | None:
    value = _read_object(Path(run_dir) / ITERATION_DISPATCH_FILE, required=False)
    return value or None


def prepare_iteration_dispatch(
    run_dir: str | Path,
    *,
    executor_provider: str,
    executor_family: str,
    executor_attestation: Mapping[str, Any],
    started_at: str,
    driver_pid: int | None = None,
) -> dict[str, Any]:
    """Persist the host's exact dispatch authority before launching a worker."""

    root = Path(run_dir)
    recover_transactions(root)
    if load_candidate_quarantine(root):
        raise RevisionConflict(
            "cannot dispatch while a timed-out candidate is quarantined"
        )
    if load_pending_candidate(root):
        raise RevisionConflict("cannot dispatch while a candidate is pending review")
    if load_iteration_dispatch(root):
        raise RevisionConflict(
            "an unresolved host dispatch intent already exists; recover or cancel it before relaunch"
        )
    plan, plan_hash = _read_object_snapshot(
        root / CURRENT_PLAN_FILE, required=True
    )
    contract, contract_hash = _read_object_snapshot(
        root / GOAL_CONTRACT_FILE, required=True
    )
    registry, registry_hash = _read_object_snapshot(
        root / APPROACH_REGISTRY_FILE, required=True
    )
    validation = validate_goal_focus(root, require_enabled=True)
    if validation.get("errors"):
        raise ValueError("cannot dispatch invalid Goal-Focus authority: " + "; ".join(validation["errors"]))
    if plan.get("enforcement_mode") != "enforce" or plan.get("state") != "active":
        raise ValueError("host dispatch requires an active enforce-mode plan")
    provider = _clean_text(executor_provider)
    family = _clean_text(executor_family)
    if not provider or not family or family == "unverified":
        raise ValueError("host dispatch requires a known executor provider and family")
    try:
        import panel_parent as panel_review  # type: ignore
    except ImportError:  # pragma: no cover - package-style import
        from . import panel_parent as panel_review  # type: ignore
    if not isinstance(executor_attestation, Mapping):
        raise ValueError("host dispatch requires an executor executable attestation")
    try:
        live_attestation = panel_review.revalidate_provider_executable_attestation(
            executor_attestation,
            forbidden_roots=(root,),
        )
    except (OSError, ValueError, panel_review.PanelIsolationError) as exc:
        raise ValueError(f"host executor executable attestation is invalid: {exc}") from exc
    if dict(executor_attestation) != live_attestation:
        raise ValueError("host executor executable attestation is not exact")
    if (
        _clean_text(live_attestation.get("provider")) != provider
        or _clean_text(live_attestation.get("family")) != family
    ):
        raise ValueError(
            "host executor provider/family disagrees with its executable attestation"
        )
    reviewed_family = _clean_text(plan.get("dispatch_provider_family"))
    if not reviewed_family or reviewed_family != family:
        raise RevisionConflict(
            "active plan was reviewed for a different driver family; run a fresh strategy review"
        )
    reviewed_attestation = plan.get("dispatch_provider_attestation")
    if not isinstance(reviewed_attestation, Mapping):
        raise RevisionConflict(
            "active plan lacks the reviewed driver executable attestation"
        )
    try:
        live_reviewed_attestation = (
            panel_review.revalidate_provider_executable_attestation(
                reviewed_attestation,
                forbidden_roots=(root,),
            )
        )
    except (OSError, ValueError, panel_review.PanelIsolationError) as exc:
        raise RevisionConflict(
            f"reviewed driver executable attestation is no longer valid: {exc}"
        ) from exc
    if (
        dict(reviewed_attestation) != live_reviewed_attestation
        or live_attestation != live_reviewed_attestation
    ):
        raise RevisionConflict(
            "host executor is not the exact executable approved by the active strategy review"
        )
    candidate_id = str(uuid.uuid4())
    evidence_root = _ensure_private_evidence_directory(root, candidate_id)
    dispatch_id = _stable_id(
        "dispatch",
        candidate_id,
        plan.get("plan_revision"),
        provider,
        started_at,
    )
    intent = {
        "schema_version": "goal_focus_dispatch.v1",
        "dispatch_id": dispatch_id,
        "candidate_id": candidate_id,
        "evidence_root": evidence_root,
        "executor_provider": provider,
        "executor_family": family,
        "executor_attestation": copy.deepcopy(live_attestation),
        "started_at": _clean_text(started_at),
        "driver_pid": int(driver_pid or 0),
        "plan_revision": plan.get("plan_revision"),
        "goal_revision": plan.get("goal_revision"),
        "registry_revision": plan.get("registry_revision"),
        "campaign_id": plan.get("campaign_id"),
        "approach_id": plan.get("approach_id"),
        "plan_fingerprint": _object_fingerprint(plan),
        "goal_contract_fingerprint": _object_fingerprint(contract),
        "approach_registry_fingerprint": _object_fingerprint(registry),
        "created_at": utc_now(),
    }
    tx = commit_transaction(
        root,
        json_files={ITERATION_DISPATCH_FILE: intent},
        expected_absent=[
            ITERATION_DISPATCH_FILE,
            PENDING_CANDIDATE_FILE,
            CANDIDATE_QUARANTINE_FILE,
        ],
        expected_revisions={
            CURRENT_PLAN_FILE: ("plan_revision", plan.get("plan_revision")),
            GOAL_CONTRACT_FILE: ("goal_revision", plan.get("goal_revision")),
            APPROACH_REGISTRY_FILE: ("registry_revision", plan.get("registry_revision")),
        },
        expected_hashes={
            CURRENT_PLAN_FILE: str(plan_hash),
            GOAL_CONTRACT_FILE: str(contract_hash),
            APPROACH_REGISTRY_FILE: str(registry_hash),
        },
        transaction_id=dispatch_id,
    )
    return {"status": "prepared", "dispatch": intent, "transaction": tx}


def validate_iteration_dispatch(
    run_dir: str | Path,
    dispatch: Mapping[str, Any],
    *,
    expected_dispatch_id: str = "",
    _authority: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    root = Path(run_dir)
    authority = _authority or {}
    plan = dict(authority.get("plan") or load_current_plan(root))
    contract = dict(authority.get("contract") or load_goal_contract(root))
    registry = dict(authority.get("registry") or load_approach_registry(root))
    errors: list[str] = []
    if dispatch.get("schema_version") != "goal_focus_dispatch.v1":
        errors.append("dispatch schema is invalid")
    if expected_dispatch_id and _clean_text(dispatch.get("dispatch_id")) != _clean_text(expected_dispatch_id):
        errors.append("dispatch id does not match the host-launched worker")
    if plan.get("enforcement_mode") != "enforce" or plan.get("state") != "active":
        errors.append("dispatched plan is no longer active enforce authority")
    for field in ("plan_revision", "goal_revision", "registry_revision", "campaign_id", "approach_id"):
        if dispatch.get(field) != plan.get(field):
            errors.append(f"dispatch {field} disagrees with current_plan")
    if dispatch.get("plan_fingerprint") != _object_fingerprint(plan):
        errors.append("current_plan changed after host dispatch")
    if dispatch.get("goal_contract_fingerprint") != _object_fingerprint(contract):
        errors.append("goal_contract changed after host dispatch")
    if dispatch.get("approach_registry_fingerprint") != _object_fingerprint(registry):
        errors.append("approach_registry changed after host dispatch")
    if not _clean_text(dispatch.get("candidate_id")) or not _clean_text(dispatch.get("executor_provider")):
        errors.append("dispatch lacks candidate or executor identity")
    expected_evidence_root = Path(
        *EVIDENCE_ROOT_PARTS,
        _clean_text(dispatch.get("candidate_id")),
    ).as_posix()
    if _clean_text(dispatch.get("evidence_root")) != expected_evidence_root:
        errors.append("dispatch evidence root is not bound to its candidate id")
    else:
        try:
            live_evidence_root = _ensure_private_evidence_directory(
                root, _clean_text(dispatch.get("candidate_id"))
            )
        except (OSError, ValueError) as exc:
            errors.append(f"dispatch evidence root is unsafe: {exc}")
        else:
            if live_evidence_root != expected_evidence_root:
                errors.append("dispatch evidence root changed after preparation")
    executor_attestation = (
        dispatch.get("executor_attestation")
        if isinstance(dispatch.get("executor_attestation"), Mapping)
        else {}
    )
    reviewed_attestation = (
        plan.get("dispatch_provider_attestation")
        if isinstance(plan.get("dispatch_provider_attestation"), Mapping)
        else {}
    )
    if not executor_attestation or executor_attestation != reviewed_attestation:
        errors.append("dispatch executable identity differs from the reviewed plan")
    else:
        try:
            import panel_parent as panel_review  # type: ignore
        except ImportError:  # pragma: no cover - package-style import
            from . import panel_parent as panel_review  # type: ignore
        try:
            live_attestation = panel_review.revalidate_provider_executable_attestation(
                executor_attestation,
                forbidden_roots=(root,),
            )
        except (OSError, ValueError, panel_review.PanelIsolationError) as exc:
            errors.append(f"dispatch executable attestation is invalid: {exc}")
        else:
            if live_attestation != executor_attestation:
                errors.append("dispatch executable attestation is not exact")
            if (
                _clean_text(dispatch.get("executor_provider"))
                != _clean_text(live_attestation.get("provider"))
                or _clean_text(dispatch.get("executor_family"))
                != _clean_text(live_attestation.get("family"))
                or _clean_text(plan.get("dispatch_provider_family"))
                != _clean_text(live_attestation.get("family"))
            ):
                errors.append(
                    "dispatch provider/family disagrees with its executable attestation"
                )
    if errors:
        raise RevisionConflict("; ".join(errors))
    return plan


def cancel_iteration_dispatch(
    run_dir: str | Path, *, dispatch_id: str, reason: str = "worker_failed_before_stage"
) -> dict[str, Any]:
    """Cancel the exact unconsumed intent after a host-observed worker failure."""

    root = Path(run_dir)
    recover_transactions(root)
    if load_pending_candidate(root):
        raise RevisionConflict("cannot cancel a dispatch after its candidate was staged")
    dispatch, dispatch_hash = _read_object_snapshot(
        root / ITERATION_DISPATCH_FILE, required=False
    )
    if not dispatch:
        return {"status": "absent"}
    if _clean_text(dispatch.get("dispatch_id")) != _clean_text(dispatch_id):
        raise RevisionConflict("refusing to cancel a different dispatch intent")
    tx = commit_transaction(
        root,
        jsonl_appends={
            DIRECTION_DECISIONS_FILE: [
                {
                    "schema_version": DIRECTION_DECISION_SCHEMA,
                    "event_id": _stable_id("dispatch-cancel", dispatch_id),
                    "decision_type": "dispatch_cancelled",
                    "dispatch_id": dispatch_id,
                    "reason": _clean_text(reason),
                    "timestamp": utc_now(),
                }
            ]
        },
        deletes=[ITERATION_DISPATCH_FILE],
        expected_hashes={ITERATION_DISPATCH_FILE: str(dispatch_hash)},
        transaction_id=_stable_id("cancel", dispatch_id),
    )
    return {"status": "cancelled", "transaction": tx}


def quarantine_failed_completion(
    run_dir: str | Path,
    *,
    reason: str,
    fallback_dispatch: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """CAS a failed completion into a durable non-reviewable tombstone.

    A cleanup-failed descendant may race the host's tombstone with staging. If
    staging wins, the transaction retries and quarantines the exact candidate;
    if the tombstone wins, staging's quarantine-absence precondition fails.
    The host's in-memory dispatch is a final fallback when the on-disk intent
    disappeared during the failure race.
    """

    root = Path(run_dir)
    normalized_reason = _clean_text(reason, 500)
    if not normalized_reason:
        raise ValueError("candidate quarantine requires a reason")
    fallback = copy.deepcopy(dict(fallback_dispatch or {}))
    last_conflict: Exception | None = None
    for _attempt in range(4):
        recover_transactions(root)
        existing = load_candidate_quarantine(root)
        if existing:
            return {"status": "already_quarantined", "quarantine": existing}
        pending, pending_hash = _read_object_snapshot(
            root / PENDING_CANDIDATE_FILE, required=False
        )
        dispatch, dispatch_hash = _read_object_snapshot(
            root / ITERATION_DISPATCH_FILE, required=False
        )
        if pending and dispatch:
            raise RevisionConflict(
                "pending candidate and in-flight dispatch cannot both be quarantined"
            )
        expected_absent = [CANDIDATE_QUARANTINE_FILE]
        expected_hashes: dict[str, str] = {}
        deletes: list[str] = []
        source = "live_state"
        if pending:
            object_kind = "candidate"
            blocked_object = pending
            deletes.append(PENDING_CANDIDATE_FILE)
            expected_hashes[PENDING_CANDIDATE_FILE] = str(pending_hash)
            expected_absent.append(ITERATION_DISPATCH_FILE)
        elif dispatch:
            object_kind = "dispatch"
            blocked_object = dispatch
            deletes.append(ITERATION_DISPATCH_FILE)
            expected_hashes[ITERATION_DISPATCH_FILE] = str(dispatch_hash)
            expected_absent.append(PENDING_CANDIDATE_FILE)
        elif fallback:
            object_kind = "dispatch"
            blocked_object = fallback
            source = "host_dispatch_fallback"
            expected_absent.extend(
                [PENDING_CANDIDATE_FILE, ITERATION_DISPATCH_FILE]
            )
        else:
            return {"status": "absent"}
        fingerprint = candidate_fingerprint(blocked_object)
        quarantine_id = _stable_id("candidate-quarantine", fingerprint)
        quarantined_at = utc_now()
        envelope = {
            "schema_version": CANDIDATE_QUARANTINE_SCHEMA,
            "quarantine_id": quarantine_id,
            "object_kind": object_kind,
            "candidate_id": _clean_text(blocked_object.get("candidate_id")),
            "candidate_fingerprint": fingerprint,
            "reason": normalized_reason,
            "source": source,
            "quarantined_at": quarantined_at,
            object_kind: copy.deepcopy(blocked_object),
        }
        event_id = _stable_id("decision", quarantine_id)
        event = {
            "schema_version": DIRECTION_DECISION_SCHEMA,
            "event_id": event_id,
            "decision_id": event_id,
            "decision_type": "candidate_quarantined",
            "object_kind": object_kind,
            "candidate_id": envelope["candidate_id"],
            "candidate_fingerprint": fingerprint,
            "reason": normalized_reason,
            "timestamp": quarantined_at,
        }
        try:
            tx = commit_transaction(
                root,
                json_files={CANDIDATE_QUARANTINE_FILE: envelope},
                jsonl_appends={DIRECTION_DECISIONS_FILE: [event]},
                deletes=deletes,
                expected_absent=expected_absent,
                expected_hashes=expected_hashes,
                transaction_id=_stable_id("quarantine", fingerprint),
            )
        except RevisionConflict as exc:
            last_conflict = exc
            continue
        return {
            "status": "quarantined",
            "quarantine": envelope,
            "transaction": tx,
        }
    raise RevisionConflict(
        "failed completion state kept changing during quarantine"
    ) from last_conflict


def quarantine_pending_candidate(
    run_dir: str | Path, *, reason: str
) -> dict[str, Any]:
    """Compatibility wrapper for quarantining the current failed completion."""

    return quarantine_failed_completion(run_dir, reason=reason)


def release_candidate_quarantine(
    run_dir: str | Path, *, expected_candidate_fingerprint: str
) -> dict[str, Any]:
    """Archive and release the exact quarantine after explicit inspection."""

    root = Path(run_dir)
    recover_transactions(root)
    quarantine, quarantine_hash = _read_object_snapshot(
        root / CANDIDATE_QUARANTINE_FILE, required=False
    )
    if not quarantine:
        return {"status": "absent"}
    fingerprint = _clean_text(quarantine.get("candidate_fingerprint"))
    if _clean_text(expected_candidate_fingerprint) != fingerprint:
        raise RevisionConflict(
            "candidate fingerprint does not exactly match the active quarantine"
        )
    object_kind = _clean_text(quarantine.get("object_kind"))
    blocked_object = (
        quarantine.get(object_kind)
        if object_kind in {"candidate", "dispatch"}
        and isinstance(quarantine.get(object_kind), dict)
        else {}
    )
    if (
        quarantine.get("schema_version") != CANDIDATE_QUARANTINE_SCHEMA
        or not blocked_object
        or fingerprint != candidate_fingerprint(blocked_object)
    ):
        raise ValueError("candidate quarantine is invalid")
    archive_name = _stable_id("candidate-quarantine", fingerprint) + ".json"
    archive_rel = (
        Path(".goal_focus") / "quarantined_candidates" / archive_name
    ).as_posix()
    released_at = utc_now()
    archived = copy.deepcopy(quarantine)
    archived["released_at"] = released_at
    event_id = _stable_id("decision", "quarantine-release", fingerprint)
    event = {
        "schema_version": DIRECTION_DECISION_SCHEMA,
        "event_id": event_id,
        "decision_id": event_id,
        "decision_type": "candidate_quarantine_released",
        "candidate_id": quarantine.get("candidate_id"),
        "candidate_fingerprint": fingerprint,
        "timestamp": released_at,
    }
    tx = commit_transaction(
        root,
        json_files={archive_rel: archived},
        jsonl_appends={DIRECTION_DECISIONS_FILE: [event]},
        deletes=[CANDIDATE_QUARANTINE_FILE],
        expected_absent=[archive_rel],
        expected_hashes={CANDIDATE_QUARANTINE_FILE: str(quarantine_hash)},
        transaction_id=_stable_id("release-quarantine", fingerprint),
    )
    return {
        "status": "released",
        "candidate_fingerprint": fingerprint,
        "archive": archive_rel,
        "transaction": tx,
    }


def validate_provider_resource_attestation(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate the host-only proof that a primary ran inside hard limits."""

    if not isinstance(value, Mapping) or not value:
        raise ValueError("host provider resource attestation is required")
    attestation = copy.deepcopy(dict(value))
    limits = attestation.get("limits")
    if (
        attestation.get("schema_version")
        != PROVIDER_RESOURCE_ATTESTATION_SCHEMA
        or attestation.get("provider_transport") != "trusted-local"
        or attestation.get("role") != "primary"
        or attestation.get("resource_gate")
        != "pre-exec-cgroup-rlimit-v1"
        or attestation.get("output_capture") != "bounded-pipe"
        or attestation.get("control_plane_masked") is not True
        or attestation.get("cgroup_api_masked") is not True
        or attestation.get("cleanup_verified") is not True
        or attestation.get("capture_verified") is not True
        or attestation.get("timed_out") is not False
        or attestation.get("oversized_output") is not False
        or attestation.get("sensitive_output_blocked") is not False
        or re.fullmatch(
            r"aas-arl-primary-[0-9]+-[0-9a-f]{12}\.scope",
            _clean_text(attestation.get("scope_unit")),
        )
        is None
        or not _clean_text(attestation.get("finished_at"))
        or not isinstance(limits, Mapping)
    ):
        raise ValueError("host provider resource attestation is invalid")

    required_limits = {
        "wall_time_seconds": (1, 172800),
        "runtime_scope_seconds": (16, 172815),
        "memory_max_bytes": (1024 * 1024 * 1024, 262144 * 1024 * 1024),
        "memory_swap_max_bytes": (0, 262144 * 1024 * 1024),
        "address_space_bytes": (2048 * 1024 * 1024, 524288 * 1024 * 1024),
        "cpu_time_seconds": (1, 172800),
        "cpu_quota_percent": (10, 6400),
        "tasks_max": (16, 4096),
        "open_files_max": (64, 65536),
        "file_size_max_bytes": (2 * 1024 * 1024, 4096 * 1024 * 1024),
        "output_max_bytes": (1 * 1024 * 1024, 16 * 1024 * 1024),
        "core_size_max_bytes": (0, 0),
    }
    normalized_limits: dict[str, int] = {}
    for field, (lower, upper) in required_limits.items():
        item = limits.get(field)
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError("host provider resource attestation is invalid")
        number = int(item)
        if number < lower or number > upper:
            raise ValueError("host provider resource attestation is invalid")
        normalized_limits[field] = number
    if (
        normalized_limits["address_space_bytes"]
        <= normalized_limits["memory_max_bytes"]
        or normalized_limits["file_size_max_bytes"]
        <= normalized_limits["output_max_bytes"]
        or normalized_limits["runtime_scope_seconds"]
        != normalized_limits["wall_time_seconds"] + 15
    ):
        raise ValueError("host provider resource attestation is invalid")
    attestation["limits"] = normalized_limits
    return attestation


def stage_iteration_candidate(
    run_dir: str | Path,
    record: Mapping[str, Any],
    expected_plan_revision: int,
    *,
    expected_dispatch_id: str = "",
    host_resource_attestation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(run_dir)
    recover_transactions(root)
    if load_candidate_quarantine(root):
        raise RevisionConflict(
            "cannot stage while a timed-out candidate is quarantined"
        )
    plan, plan_hash = _read_object_snapshot(
        root / CURRENT_PLAN_FILE, required=True
    )
    live_plan_revision = _require_nonnegative_int(
        plan.get("plan_revision"), "current_plan.plan_revision"
    )
    expected_plan_revision = _require_nonnegative_int(
        expected_plan_revision, "expected_plan_revision"
    )
    if live_plan_revision != expected_plan_revision:
        raise RevisionConflict("cannot stage result for a stale plan revision")
    pending = load_pending_candidate(root)
    supplied_id = _clean_text(record.get("candidate_id"))
    if pending:
        if supplied_id and supplied_id == pending.get("candidate_id"):
            return {"status": "already_staged", "candidate": pending}
        raise ValueError("an iteration candidate is already pending review")
    dispatch, dispatch_hash = _read_object_snapshot(
        root / ITERATION_DISPATCH_FILE, required=False
    )
    staged_record = copy.deepcopy(dict(record))
    proposed_obligation_targets(staged_record)
    delta = (
        staged_record.get("budget_delta")
        if isinstance(staged_record.get("budget_delta"), dict)
        else {}
    )
    _finite_nonnegative_amount(delta.get("usd", 0.0), "budget_delta.usd")
    if plan.get("enforcement_mode") == "enforce":
        staged_claims = _proposed_claim_ids(staged_record)
        if not staged_claims:
            raise ValueError("enforce-mode staging requires explicit material claim ids")
        if not _record_evidence_ids(staged_record):
            raise ValueError(
                "enforce-mode staging requires staged evidence for material claims"
            )
    if plan.get("enforcement_mode") == "enforce" and not dispatch:
        raise RevisionConflict(
            "enforce-mode staging requires an exact live host dispatch intent"
        )
    if dispatch:
        contract, contract_hash = _read_object_snapshot(
            root / GOAL_CONTRACT_FILE, required=True
        )
        registry, registry_hash = _read_object_snapshot(
            root / APPROACH_REGISTRY_FILE, required=True
        )
        if not _clean_text(expected_dispatch_id):
            raise RevisionConflict("a host dispatch id is required to consume the dispatch intent")
        validate_iteration_dispatch(
            root,
            dispatch,
            expected_dispatch_id=expected_dispatch_id,
            _authority={"plan": plan, "contract": contract, "registry": registry},
        )
        dispatched_id = _clean_text(dispatch.get("candidate_id"))
        if supplied_id and supplied_id != dispatched_id:
            raise RevisionConflict("staged candidate id disagrees with host dispatch")
        dispatch_plan_revision = _require_nonnegative_int(
            dispatch.get("plan_revision"), "iteration_dispatch.plan_revision"
        )
        if dispatch_plan_revision != expected_plan_revision:
            raise RevisionConflict("staged plan revision disagrees with host dispatch")
        candidate_id = dispatched_id
        execution = (
            staged_record.get("execution")
            if isinstance(staged_record.get("execution"), dict)
            else {}
        )
        claimed = _clean_text(execution.get("executor_provider"))
        provider = _clean_text(dispatch.get("executor_provider"))
        if claimed and claimed != provider:
            execution["claimed_executor_provider"] = claimed
        execution["executor_provider"] = provider
        execution["started_at"] = _clean_text(dispatch.get("started_at"))
        staged_record["execution"] = execution
        validate_compute_execution(
            root,
            execution.get("compute") if isinstance(execution.get("compute"), dict) else {},
            _plan=plan,
        )
    else:
        contract, contract_hash = _read_object_snapshot(
            root / GOAL_CONTRACT_FILE, required=True
        )
        registry, registry_hash = _read_object_snapshot(
            root / APPROACH_REGISTRY_FILE, required=True
        )
        candidate_id = supplied_id or str(uuid.uuid4())
    staged_record["candidate_id"] = candidate_id
    if plan.get("enforcement_mode") == "enforce":
        _reject_secret_shaped_record(staged_record)
        # Ignore any worker-supplied manifest. The host snapshots every declared
        # evidence path into the candidate so prompt-only reviewers see exact,
        # immutable content rather than trusting invented identifiers.
        staged_record["evidence_artifacts"] = _snapshot_evidence_artifacts(
            root,
            staged_record,
            candidate_id=candidate_id,
        )
        validate_evidence_artifacts(
            staged_record,
            require_artifacts=True,
            candidate_id=candidate_id,
        )
    candidate = {
        "schema_version": ITERATION_CANDIDATE_SCHEMA,
        "candidate_id": candidate_id,
        "event_id": candidate_id,
        "status": "pending_review",
        "plan_revision": expected_plan_revision,
        "goal_revision": plan.get("goal_revision"),
        "registry_revision": plan.get("registry_revision"),
        "campaign_id": plan.get("campaign_id"),
        "approach_id": plan.get("approach_id"),
        "record": staged_record,
        "staged_at": utc_now(),
    }
    deletes: list[str] = []
    expected_hashes: dict[str, str] = {
        CURRENT_PLAN_FILE: str(plan_hash),
        GOAL_CONTRACT_FILE: str(contract_hash),
        APPROACH_REGISTRY_FILE: str(registry_hash),
    }
    if dispatch:
        resource_attestation: dict[str, Any] = {}
        if plan.get("enforcement_mode") == "enforce":
            resource_attestation = validate_provider_resource_attestation(
                host_resource_attestation
            )
        elif host_resource_attestation:
            resource_attestation = validate_provider_resource_attestation(
                host_resource_attestation
            )
        candidate["host_execution_attestation"] = {
            "schema_version": "host_execution_attestation.v1",
            "candidate_id": candidate_id,
            "dispatch_id": dispatch.get("dispatch_id"),
            "executor_provider": dispatch.get("executor_provider"),
            "executor_family": dispatch.get("executor_family"),
            "executor_attestation": copy.deepcopy(
                dispatch.get("executor_attestation")
            ),
            "evidence_root": dispatch.get("evidence_root"),
            "plan_fingerprint": dispatch.get("plan_fingerprint"),
            "goal_contract_fingerprint": dispatch.get("goal_contract_fingerprint"),
            "approach_registry_fingerprint": dispatch.get(
                "approach_registry_fingerprint"
            ),
            "source": "host_dispatch",
            "attested_at": utc_now(),
        }
        if resource_attestation:
            candidate["host_execution_attestation"][
                "resource_attestation"
            ] = resource_attestation
        deletes.append(ITERATION_DISPATCH_FILE)
        expected_hashes[ITERATION_DISPATCH_FILE] = dispatch_hash
    if plan.get("enforcement_mode") == "enforce":
        _reject_secret_shaped_record(candidate, path="candidate")
    tx = commit_transaction(
        root,
        json_files={PENDING_CANDIDATE_FILE: candidate},
        deletes=deletes,
        expected_revisions={
            CURRENT_PLAN_FILE: ("plan_revision", expected_plan_revision),
            GOAL_CONTRACT_FILE: ("goal_revision", plan.get("goal_revision")),
            APPROACH_REGISTRY_FILE: ("registry_revision", plan.get("registry_revision")),
        },
        expected_absent=[PENDING_CANDIDATE_FILE, CANDIDATE_QUARANTINE_FILE],
        expected_hashes=expected_hashes,
        transaction_id=_stable_id("stage", candidate_id),
    )
    return {"status": "staged", "candidate": candidate, "transaction": tx}


def validate_compute_execution(
    run_dir: str | Path,
    compute: Mapping[str, Any],
    *,
    _plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Reject reported execution outside the authoritative plan policy.

    An empty allowlist means that Goal-Focus did not add a service restriction;
    a non-empty allowlist is strict. When a restriction exists, unreported
    provenance cannot establish compliance and therefore cannot be staged.
    """

    if not isinstance(compute, Mapping):
        raise ValueError("compute provenance must be an object")
    root = Path(run_dir)
    plan = dict(_plan) if _plan is not None else load_current_plan(root)
    policy = plan.get("compute_policy") if isinstance(plan.get("compute_policy"), dict) else {}
    allowed = _policy_allowed(policy)
    forbidden = _compute_services(policy.get("forbidden_services"))
    status = _clean_text(compute.get("recording_status")).lower()
    if status not in COMPUTE_RECORDING_STATUSES:
        raise ValueError(
            "compute provenance recording_status must be explicit or unreported"
        )
    usage = _clean_text(compute.get("usage")).lower()
    rows = compute.get("services")
    if not isinstance(rows, list):
        raise ValueError("compute provenance services must be a list")
    if status == "unreported":
        if rows:
            raise ValueError("unreported compute provenance cannot contain service rows")
        if usage != "unknown":
            raise ValueError("unreported compute provenance usage must be unknown")
    used: set[str] = set()
    for index, row in enumerate(rows):
        label = f"compute provenance service row {index + 1}"
        if not isinstance(row, dict):
            raise ValueError(f"{label} must be an object")
        unexpected = sorted(set(row) - _COMPUTE_RUN_FIELDS)
        if unexpected:
            raise ValueError(
                f"{label} contains unsupported fields: " + ", ".join(unexpected)
            )
        raw_service_value = row.get("service")
        if not isinstance(raw_service_value, str):
            raise ValueError(f"{label} service must be a string")
        raw_service = raw_service_value.strip().lower()
        normalized_services = _compute_services([raw_service])
        service = next(iter(normalized_services), "")
        if not service:
            raise ValueError(f"{label} requires a service")
        if (
            service not in KNOWN_COMPUTE_SERVICES
            and _SAFE_CUSTOM_COMPUTE_SERVICE.fullmatch(service) is None
        ):
            raise ValueError(f"{label} has invalid service {raw_service!r}")
        run_status = _clean_text(row.get("status")).lower()
        if run_status not in COMPUTE_RUN_STATUSES:
            raise ValueError(
                f"{label} status must be one of {sorted(COMPUTE_RUN_STATUSES)}"
            )
        try:
            from compute_policy import normalize_compute_job_ref  # type: ignore
        except ImportError:  # pragma: no cover - package-style import
            from .compute_policy import normalize_compute_job_ref  # type: ignore
        job_ref = row.get("job_ref")
        if job_ref not in (None, ""):
            # Only string job_refs are accepted. Unsafe command-like strings are
            # slugified in place; non-strings remain a hard type error.
            if not isinstance(job_ref, str):
                raise ValueError(f"{label} job_ref must be a safe string identifier")
            safe_ref, residual = normalize_compute_job_ref(job_ref)
            if safe_ref is None:
                raise ValueError(f"{label} job_ref must be a safe string identifier")
            # In-place normalize so staged candidates keep a host-safe job_ref.
            row["job_ref"] = safe_ref
            if residual:
                existing_detail = row.get("detail")
                if existing_detail in (None, ""):
                    row["detail"] = residual[:500]
                elif (
                    isinstance(existing_detail, str)
                    and residual not in existing_detail
                ):
                    row["detail"] = f"{existing_detail}; cmd={residual}"[:500]
        detail = row.get("detail")
        if detail not in (None, "") and (
            not isinstance(detail, str) or len(detail) > 500
        ):
            raise ValueError(f"{label} detail must be a string of at most 500 characters")
        duration_value = row.get("duration_seconds")
        if duration_value not in (None, ""):
            if isinstance(duration_value, bool) or not isinstance(
                duration_value, (int, float)
            ):
                raise ValueError(f"{label}.duration_seconds must be a number")
            _finite_nonnegative_amount(
                duration_value, f"{label}.duration_seconds"
            )

        parsed_times: dict[str, datetime] = {}
        for field in ("started_at", "finished_at"):
            raw_time = row.get(field)
            if raw_time in (None, ""):
                continue
            if not isinstance(raw_time, str) or len(raw_time) > 40:
                raise ValueError(f"{label} {field} must be a bounded ISO-8601 timestamp")
            normalized_time = raw_time.strip()
            try:
                parsed = datetime.fromisoformat(
                    normalized_time[:-1] + "+00:00"
                    if normalized_time.endswith("Z")
                    else normalized_time
                )
            except ValueError as exc:
                raise ValueError(
                    f"{label} {field} must be a valid ISO-8601 timestamp"
                ) from exc
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError(f"{label} {field} must include a timezone")
            parsed_times[field] = parsed
        if (
            "started_at" in parsed_times
            and "finished_at" in parsed_times
            and parsed_times["finished_at"] < parsed_times["started_at"]
        ):
            raise ValueError(f"{label} finished_at precedes started_at")
        used.add(service)

    expected_usage = (
        "none"
        if not rows and status == "explicit"
        else "mixed"
        if len(used) > 1
        else next(iter(used), "unknown")
    )
    if status == "explicit" and usage != expected_usage:
        raise ValueError(
            f"explicit compute provenance usage must be {expected_usage!r}"
        )
    if (allowed or forbidden) and status == "unreported":
        raise ValueError(
            "compute provenance must explicitly report used services or no compute "
            "while a Goal-Focus compute policy is active"
        )
    outside = used - allowed if allowed else set()
    if outside:
        raise ValueError(
            "reported compute service is outside current_plan allowlist: "
            + ", ".join(sorted(outside))
        )
    prohibited = used & forbidden
    if prohibited:
        raise ValueError(
            "reported compute service is forbidden by current_plan: "
            + ", ".join(sorted(prohibited))
        )
    return {
        "allowed_services": sorted(allowed),
        "forbidden_services": sorted(forbidden),
        "used_services": sorted(used),
        "recording_status": status,
    }


def validate_host_staged_candidate(
    run_dir: str | Path,
    candidate: Mapping[str, Any],
    *,
    expected_dispatch_id: str = "",
    _authority: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate a pending candidate against host-pinned dispatch authority."""

    root = Path(run_dir)
    _reject_secret_shaped_record(candidate, path="candidate")
    authority = _authority or {}
    plan = dict(authority.get("plan") or load_current_plan(root))
    contract = dict(authority.get("contract") or load_goal_contract(root))
    registry = dict(authority.get("registry") or load_approach_registry(root))
    errors: list[str] = []
    if candidate.get("schema_version") != ITERATION_CANDIDATE_SCHEMA:
        errors.append("pending candidate schema is invalid")
    if candidate.get("status") != "pending_review":
        errors.append("pending candidate status is not pending_review")
    for field in ("plan_revision", "goal_revision", "registry_revision", "campaign_id", "approach_id"):
        if candidate.get(field) != plan.get(field):
            errors.append(f"pending candidate {field} disagrees with current_plan")
    attestation = (
        candidate.get("host_execution_attestation")
        if isinstance(candidate.get("host_execution_attestation"), dict)
        else {}
    )
    candidate_id = _clean_text(candidate.get("candidate_id"))
    if attestation.get("schema_version") != "host_execution_attestation.v1":
        errors.append("pending candidate lacks host execution attestation")
    if attestation.get("source") != "host_dispatch":
        errors.append("pending candidate attestation is not bound to a host dispatch")
    if _clean_text(attestation.get("candidate_id")) != candidate_id:
        errors.append("pending candidate attestation has a different candidate id")
    if expected_dispatch_id and _clean_text(attestation.get("dispatch_id")) != _clean_text(expected_dispatch_id):
        errors.append("pending candidate attestation has a different dispatch id")
    expected_evidence_root = Path(
        *EVIDENCE_ROOT_PARTS,
        candidate_id,
    ).as_posix()
    if _clean_text(attestation.get("evidence_root")) != expected_evidence_root:
        errors.append("pending candidate evidence root is not bound to its candidate id")
    if attestation.get("plan_fingerprint") != _object_fingerprint(plan):
        errors.append("pending candidate attestation is stale for current_plan")
    if attestation.get("goal_contract_fingerprint") != _object_fingerprint(contract):
        errors.append("pending candidate attestation is stale for goal_contract")
    if attestation.get("approach_registry_fingerprint") != _object_fingerprint(registry):
        errors.append("pending candidate attestation is stale for approach_registry")
    resource_attestation = (
        attestation.get("resource_attestation")
        if isinstance(attestation.get("resource_attestation"), Mapping)
        else None
    )
    if plan.get("enforcement_mode") == "enforce" or resource_attestation:
        try:
            validate_provider_resource_attestation(resource_attestation)
        except ValueError as exc:
            errors.append(str(exc))
    executor_attestation = (
        attestation.get("executor_attestation")
        if isinstance(attestation.get("executor_attestation"), Mapping)
        else {}
    )
    reviewed_attestation = (
        plan.get("dispatch_provider_attestation")
        if isinstance(plan.get("dispatch_provider_attestation"), Mapping)
        else {}
    )
    if not executor_attestation or executor_attestation != reviewed_attestation:
        errors.append(
            "pending executor executable identity differs from the reviewed plan"
        )
    else:
        try:
            import panel_parent as panel_review  # type: ignore
        except ImportError:  # pragma: no cover - package-style import
            from . import panel_parent as panel_review  # type: ignore
        try:
            live_attestation = panel_review.revalidate_provider_executable_attestation(
                executor_attestation,
                forbidden_roots=(root,),
            )
        except (OSError, ValueError, panel_review.PanelIsolationError) as exc:
            errors.append(f"pending executor executable attestation is invalid: {exc}")
        else:
            if live_attestation != executor_attestation:
                errors.append("pending executor executable attestation is not exact")
            if (
                _clean_text(attestation.get("executor_provider"))
                != _clean_text(live_attestation.get("provider"))
                or _clean_text(attestation.get("executor_family"))
                != _clean_text(live_attestation.get("family"))
                or _clean_text(plan.get("dispatch_provider_family"))
                != _clean_text(live_attestation.get("family"))
            ):
                errors.append(
                    "pending executor provider/family disagrees with its executable attestation"
                )
    record = candidate.get("record") if isinstance(candidate.get("record"), dict) else {}
    execution = record.get("execution") if isinstance(record.get("execution"), dict) else {}
    provider = _clean_text(execution.get("executor_provider"))
    if not provider or provider != _clean_text(attestation.get("executor_provider")):
        errors.append("pending executor provenance disagrees with host attestation")
    goal_focus = record.get("goal_focus") if isinstance(record.get("goal_focus"), dict) else {}
    for field, record_field in (
        ("plan_revision", "plan_revision"),
        ("campaign_id", "campaign_id"),
        ("approach_id", "approach_id"),
    ):
        if goal_focus.get(record_field) != plan.get(field):
            errors.append(f"pending record {record_field} disagrees with current_plan")
    claim_ids = [_clean_text(item) for item in record.get("claim_ids") or [] if _clean_text(item)]
    if not claim_ids or len(claim_ids) != len(set(claim_ids)):
        errors.append("pending candidate requires unique explicit claim ids")
    if claim_ids and not _record_evidence_ids(record):
        errors.append("pending candidate requires staged evidence for material claims")
    try:
        evidence_ids = validate_evidence_artifacts(
            record,
            require_artifacts=plan.get("enforcement_mode") == "enforce",
            candidate_id=candidate_id,
        )
    except ValueError as exc:
        errors.append(str(exc))
        evidence_ids = set()
    try:
        compute_check = validate_compute_execution(
            root,
            execution.get("compute") if isinstance(execution.get("compute"), dict) else {},
            _plan=plan,
        )
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
        compute_check = {}
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "executor_provider": provider,
        "claim_ids": claim_ids,
        "evidence_ids": sorted(evidence_ids),
        "compute": compute_check,
    }


def _reviewed_obligation_evidence(
    review: Mapping[str, Any], staged_evidence: set[str]
) -> dict[str, set[str]]:
    """Return only evidence cited for each obligation by validated reviewers."""

    provider_reviews = review.get("provider_reviews")
    payloads = (
        [payload for payload in provider_reviews.values() if isinstance(payload, Mapping)]
        if isinstance(provider_reviews, Mapping)
        else [review]
    )
    result: dict[str, set[str]] = {}
    for payload in payloads:
        for row in payload.get("obligation_reviews") or []:
            if not isinstance(row, Mapping):
                continue
            obligation_id = _clean_text(row.get("obligation_id"))
            if not obligation_id:
                continue
            verdict = _clean_text(row.get("verdict") or row.get("status")).lower()
            if verdict not in {"accept", "accepted", "supported", "pass"}:
                continue
            refs = {
                _clean_text(ref)
                for ref in row.get("evidence_refs") or []
                if _clean_text(ref) in staged_evidence
            }
            if refs:
                result.setdefault(obligation_id, set()).update(refs)
    return result


def _apply_transitions(
    contract: dict[str, Any],
    transitions: Sequence[Any],
    evidence_by_obligation: Mapping[str, Sequence[Any]],
) -> dict[str, Any]:
    out = copy.deepcopy(contract)
    obligations = out.get("obligations") if isinstance(out.get("obligations"), dict) else {}
    changed = False
    normalized = [dict(raw) for raw in transitions if isinstance(raw, dict)]
    for raw in _dependency_admissible_transitions(obligations, normalized):
        oid = _clean_text(raw.get("obligation_id"))
        target = raw.get("to")
        node = obligations.get(oid)
        if not isinstance(node, dict) or target not in OBLIGATION_STATUSES:
            continue
        evidence = evidence_by_obligation.get(oid) or []
        node["status"] = target
        node["evidence_refs"] = sorted(
            set(str(item) for item in (node.get("evidence_refs") or []) + list(evidence) if str(item))
        )
        changed = True
    if changed:
        out["goal_revision"] = _positive_int(out.get("goal_revision")) + 1
        out["updated_at"] = utc_now()
    return out


def _proposed_claim_ids(record: Mapping[str, Any]) -> set[str]:
    values: set[str] = {
        _clean_text(item)
        for item in record.get("claim_ids") or []
        if _clean_text(item)
    }
    evidence = record.get("evidence_checked")
    if isinstance(evidence, dict):
        values.update(
            _clean_text(item)
            for item in evidence.get("claim_ids") or []
            if _clean_text(item)
        )
    for item in record.get("claims") or []:
        if isinstance(item, dict):
            claim_id = _clean_text(item.get("claim_id") or item.get("id"))
            if claim_id:
                values.add(claim_id)
    return values


def _proposed_obligation_ids(record: Mapping[str, Any]) -> set[str]:
    values = {
        _clean_text(item.get("obligation_id"))
        for item in record.get("obligation_transitions") or []
        if isinstance(item, dict) and _clean_text(item.get("obligation_id"))
    }
    progress = record.get("progress_assessment")
    if isinstance(progress, dict):
        values.update(
            _clean_text(item)
            for item in progress.get("obligation_ids") or []
            if _clean_text(item)
        )
    return values


def proposed_obligation_targets(record: Mapping[str, Any]) -> dict[str, str]:
    """Resolve every proposed obligation to its exact requested post-state."""

    targets: dict[str, str] = {}

    def add(oid: str, target: str) -> None:
        if not oid:
            return
        if target not in {"partial", "satisfied", "closed"}:
            raise ValueError(f"obligation {oid} has invalid proposed target {target!r}")
        prior = targets.get(oid)
        if prior is not None and prior != target:
            raise ValueError(
                f"obligation {oid} has conflicting proposed targets {prior!r} and {target!r}"
            )
        targets[oid] = target

    for item in record.get("obligation_transitions") or []:
        if isinstance(item, dict):
            add(_clean_text(item.get("obligation_id")), _clean_text(item.get("to")))

    progress = record.get("progress_assessment")
    if isinstance(progress, dict):
        global_delta = _clean_text(progress.get("global_delta")).lower()
        if global_delta in {"reduced", "satisfied"}:
            target = "satisfied" if global_delta == "satisfied" else "partial"
            for item in progress.get("obligation_ids") or []:
                add(_clean_text(item), target)
    return targets


def _record_evidence_ids(record: Mapping[str, Any]) -> set[str]:
    values = {
        _clean_text(item) for item in record.get("evidence_ids") or [] if _clean_text(item)
    }
    checked = record.get("evidence_checked")
    if isinstance(checked, dict):
        values.update(
            _clean_text(item)
            for item in checked.get("evidence_ids") or []
            if _clean_text(item)
        )
    return values


def _safe_evidence_relative_path(
    value: str, *, candidate_id: str = ""
) -> Path:
    evidence_id = _clean_text(value)
    rel = Path(evidence_id)
    windows = PureWindowsPath(value)
    if (
        rel.is_absolute()
        or bool(windows.drive)
        or bool(windows.root)
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or len(rel.parts) != 1
        or any(part in {"", ".", ".."} for part in rel.parts)
    ):
        raise ValueError(f"evidence id must be one safe artifact name: {value!r}")
    suffix = rel.suffix.lower()
    if (
        evidence_id.startswith(".")
        or not _SAFE_EVIDENCE_COMPONENT.fullmatch(evidence_id)
        or _SENSITIVE_EVIDENCE_COMPONENT.search(evidence_id)
        or suffix in _SENSITIVE_EVIDENCE_SUFFIXES
    ):
        raise ValueError(
            f"evidence id is hidden, unsafe, or sensitive: {value!r}"
        )
    candidate = _clean_text(candidate_id)
    if not candidate or not _SAFE_EVIDENCE_COMPONENT.fullmatch(candidate):
        raise ValueError("evidence validation requires the exact dispatch candidate id")
    return Path(*EVIDENCE_ROOT_PARTS, candidate, evidence_id)


def _secret_environment_values() -> list[str]:
    marker = re.compile(r"(?i)(?:api[_-]?key|token|secret|password|passwd|auth)")
    return sorted(
        {
            str(value)
            for name, value in os.environ.items()
            if marker.search(name) and len(str(value)) >= 8
        },
        key=len,
        reverse=True,
    )


def _reject_secret_shaped_text(value: str, *, label: str) -> None:
    """Reject rather than redact material that would cross the reviewer boundary."""

    try:
        import notify_v2 as notify_contract  # type: ignore
    except ImportError:  # pragma: no cover - package-style import
        from . import notify_v2 as notify_contract  # type: ignore
    normalized = value.strip()
    if notify_contract.redact_text(normalized) != normalized or any(
        secret in value for secret in _secret_environment_values()
    ):
        raise ValueError(f"{label} contains secret-shaped material")


def _reject_secret_shaped_record(value: Any, *, path: str = "record") -> None:
    if isinstance(value, str):
        _reject_secret_shaped_text(value, label=path)
    elif isinstance(value, Mapping):
        for key, nested in value.items():
            _reject_secret_shaped_text(str(key), label=f"{path} key")
            _reject_secret_shaped_record(nested, path=f"{path}.{_clean_text(key, 80)}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reject_secret_shaped_record(nested, path=f"{path}[{index}]")


def _snapshot_evidence_artifacts(
    run_dir: Path, record: Mapping[str, Any], *, candidate_id: str
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    total = 0
    for evidence_id in sorted(_record_evidence_ids(record)):
        rel = _safe_evidence_relative_path(
            evidence_id, candidate_id=candidate_id
        )
        source = run_dir / rel
        try:
            payload = _read_regular_bytes(
                source,
                max_bytes=MAX_EVIDENCE_ARTIFACT_BYTES,
                require_single_link=True,
                require_current_owner=True,
            )
            content = payload.decode("utf-8")
        except FileNotFoundError as exc:
            raise ValueError(
                f"staged evidence artifact does not exist: {evidence_id}"
            ) from exc
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"staged evidence artifact must be complete UTF-8 text: {evidence_id}"
            ) from exc
        total += len(payload)
        if total > MAX_EVIDENCE_TOTAL_BYTES:
            raise ValueError(
                f"staged evidence exceeds {MAX_EVIDENCE_TOTAL_BYTES} total bytes"
            )
        _reject_secret_shaped_text(
            content, label=f"staged evidence artifact {evidence_id}"
        )
        artifacts.append(
            {
                "schema_version": EVIDENCE_ARTIFACT_SCHEMA,
                "evidence_id": evidence_id,
                "source_path": rel.as_posix(),
                "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
                "content_encoding": "utf-8",
                "content": content,
            }
        )
    return artifacts


def validate_evidence_artifacts(
    record: Mapping[str, Any],
    *,
    require_artifacts: bool = False,
    candidate_id: str = "",
) -> set[str]:
    """Validate the immutable evidence snapshots embedded by the host at stage."""

    declared = _record_evidence_ids(record)
    raw_artifacts = record.get("evidence_artifacts")
    if not isinstance(raw_artifacts, list):
        if require_artifacts and declared:
            raise ValueError("candidate lacks host-snapshotted evidence artifacts")
        return set()
    observed: set[str] = set()
    total = 0
    for index, raw in enumerate(raw_artifacts):
        if not isinstance(raw, dict):
            raise ValueError(f"evidence_artifacts[{index}] must be an object")
        evidence_id = _clean_text(raw.get("evidence_id"))
        rel = _safe_evidence_relative_path(
            evidence_id,
            candidate_id=candidate_id or _clean_text(record.get("candidate_id")),
        )
        if evidence_id in observed:
            raise ValueError(f"duplicate evidence artifact id: {evidence_id}")
        if raw.get("schema_version") != EVIDENCE_ARTIFACT_SCHEMA:
            raise ValueError(f"evidence artifact schema is invalid: {evidence_id}")
        if _clean_text(raw.get("source_path")) != rel.as_posix():
            raise ValueError(f"evidence artifact source path mismatch: {evidence_id}")
        if raw.get("content_encoding") != "utf-8" or not isinstance(
            raw.get("content"), str
        ):
            raise ValueError(f"evidence artifact content is not complete UTF-8: {evidence_id}")
        payload = raw["content"].encode("utf-8")
        total += len(payload)
        if len(payload) > MAX_EVIDENCE_ARTIFACT_BYTES or total > MAX_EVIDENCE_TOTAL_BYTES:
            raise ValueError("embedded evidence artifact exceeds review limits")
        if raw.get("size_bytes") != len(payload):
            raise ValueError(f"evidence artifact size mismatch: {evidence_id}")
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        if raw.get("sha256") != digest:
            raise ValueError(f"evidence artifact hash mismatch: {evidence_id}")
        _reject_secret_shaped_text(
            raw["content"], label=f"embedded evidence artifact {evidence_id}"
        )
        observed.add(evidence_id)
    if observed != declared:
        missing = sorted(declared - observed)
        unexpected = sorted(observed - declared)
        raise ValueError(
            "evidence artifact set differs from declared evidence ids"
            f" (missing={missing}, unexpected={unexpected})"
        )
    if require_artifacts and declared and not observed:
        raise ValueError("candidate has no host-snapshotted evidence artifacts")
    return observed


def _validate_accepted_review_coverage(
    record: Mapping[str, Any], review: Mapping[str, Any]
) -> None:
    proposed_claims = _proposed_claim_ids(record)
    supported_claims = {
        _clean_text(item.get("claim_id"))
        for item in review.get("claim_reviews") or []
        if isinstance(item, dict)
        and _clean_text(item.get("claim_id"))
        and _clean_text(item.get("status") or item.get("verdict")).lower()
        in {"supported", "accept", "accepted", "pass"}
        and bool(
            {
                _clean_text(ref)
                for ref in item.get("evidence_refs") or []
                if _clean_text(ref)
            }
            & _record_evidence_ids(record)
        )
    }
    reviewed_claims = {
        _clean_text(item.get("claim_id"))
        for item in review.get("claim_reviews") or []
        if isinstance(item, dict) and _clean_text(item.get("claim_id"))
    }
    unexpected_claims = reviewed_claims - proposed_claims
    if unexpected_claims:
        raise ValueError(
            "accepted review contains claim_ids not proposed by the candidate: "
            + ", ".join(sorted(unexpected_claims))
        )
    missing_claims = proposed_claims - supported_claims
    if missing_claims:
        raise ValueError(
            "accepted candidate has unsupported or unreviewed claim_ids: "
            + ", ".join(sorted(missing_claims))
        )
    proposed_targets = proposed_obligation_targets(record)
    staged_evidence = _record_evidence_ids(record)
    review_rows = [
        item for item in review.get("obligation_reviews") or [] if isinstance(item, dict)
    ]
    reviewed_ids = {
        _clean_text(item.get("obligation_id"))
        for item in review_rows
        if _clean_text(item.get("obligation_id"))
    }
    unexpected_obligations = reviewed_ids - set(proposed_targets)
    if unexpected_obligations:
        raise ValueError(
            "accepted review contains obligation_ids not proposed by the candidate: "
            + ", ".join(sorted(unexpected_obligations))
        )
    supported_obligations: set[str] = set()
    for oid, target in proposed_targets.items():
        for item in review_rows:
            refs = {
                _clean_text(ref)
                for ref in item.get("evidence_refs") or []
                if _clean_text(ref)
            }
            if (
                _clean_text(item.get("obligation_id")) == oid
                and _clean_text(item.get("target_status")).lower() == target
                and _clean_text(item.get("status") or item.get("verdict")).lower()
                in {"supported", "accept", "accepted", "pass"}
                and bool(refs & staged_evidence)
            ):
                supported_obligations.add(oid)
                break
    missing_obligations = set(proposed_targets) - supported_obligations
    if missing_obligations:
        raise ValueError(
            "accepted candidate has obligation transitions without exact target/evidence review: "
            + ", ".join(sorted(missing_obligations))
        )


def _validate_result_review_boundary(
    root: Path,
    candidate: Mapping[str, Any],
    record: Mapping[str, Any],
    review: Mapping[str, Any],
    *,
    accepted: bool,
    revalidate_executables: bool = True,
) -> None:
    """Fail closed unless a bank/reject decision carries validated review proof.

    The panel normally supplies ``result_review_summary.v2``.  Its outer rows
    are a convenience synthesis, so this boundary also validates every embedded
    ``result_review.v1`` and, for acceptance, proves that each reviewer actually
    inspected every staged artifact it cited.  A raw v1 response cannot carry a
    trustworthy provider identity by itself, so it is never accepted directly
    at this banking boundary; the host-owned summary supplies that association.
    """

    try:
        import panel_parent as panel_review  # type: ignore
    except ImportError:  # pragma: no cover - package-style import
        from . import panel_parent as panel_review  # type: ignore

    expected_id = _clean_text(candidate.get("candidate_id"))
    expected_fingerprint = candidate_fingerprint(candidate)
    schema = _clean_text(review.get("schema_version"))
    payloads: dict[str, Mapping[str, Any]]
    attestation = candidate.get("host_execution_attestation")
    executor_provider = _clean_text(
        attestation.get("executor_provider")
        if isinstance(attestation, dict)
        else ""
    )
    raw_executor_attestation = (
        attestation.get("executor_attestation")
        if isinstance(attestation, Mapping)
        and isinstance(attestation.get("executor_attestation"), Mapping)
        else {}
    )
    raw_review_executor_attestation = review.get("executor_attestation")
    if (
        not isinstance(raw_review_executor_attestation, Mapping)
        or not raw_executor_attestation
        or dict(raw_review_executor_attestation) != dict(raw_executor_attestation)
    ):
        raise ValueError(
            "result review executor identity differs from the staged host dispatch"
        )
    identity_validator = (
        panel_review.revalidate_provider_executable_attestation
        if revalidate_executables
        else panel_review.validate_archived_provider_executable_attestation
    )
    try:
        executor_attestation = identity_validator(
            raw_executor_attestation, forbidden_roots=(root,)
        )
    except (OSError, ValueError, panel_review.PanelIsolationError) as exc:
        raise ValueError(
            f"staged executor executable attestation is invalid: {exc}"
        ) from exc
    if dict(raw_executor_attestation) != executor_attestation:
        raise ValueError("staged executor executable attestation is not exact")
    executor_family = _clean_text(executor_attestation.get("family"))
    if (
        not executor_provider
        or executor_family == "unverified"
        or _clean_text(executor_attestation.get("provider")) != executor_provider
        or not isinstance(attestation, Mapping)
        or _clean_text(attestation.get("executor_family")) != executor_family
        or _clean_text(review.get("executor_provider")) != executor_provider
        or _clean_text(review.get("executor_family")) != executor_family
    ):
        raise ValueError(
            "result review executor provider/family disagrees with its executable attestation"
        )
    if schema == RESULT_REVIEW_SCHEMA:
        raise ValueError(
            "direct result_review.v1 cannot authorize finalization; "
            "a host-owned result_review_summary.v2 is required"
        )
    elif schema == "result_review_summary.v2":
        raw_payloads = review.get("provider_reviews")
        if not isinstance(raw_payloads, dict) or not raw_payloads:
            raise ValueError(
                "result_review_summary.v2 requires non-empty provider_reviews"
            )
        if any(
            not _clean_text(name) or not isinstance(payload, dict)
            for name, payload in raw_payloads.items()
        ):
            raise ValueError("result_review_summary.v2 provider_reviews are invalid")
        payloads = {
            _clean_text(name): payload for name, payload in raw_payloads.items()
        }
        declared = {
            _clean_text(name)
            for name in review.get("providers") or []
            if _clean_text(name)
        }
        if declared != set(payloads):
            raise ValueError(
                "result_review_summary.v2 providers do not match provider_reviews"
            )
        raw_provider_attestations = review.get("provider_execution_attestations")
        if not isinstance(raw_provider_attestations, Mapping) or set(
            raw_provider_attestations
        ) != set(payloads):
            raise ValueError(
                "result_review_summary.v2 provider attestations do not match provider_reviews"
            )
        try:
            provider_attestations = {
                provider: identity_validator(
                    raw_provider_attestations[provider], forbidden_roots=(root,)
                )
                for provider in sorted(payloads)
            }
        except (OSError, ValueError, panel_review.PanelIsolationError) as exc:
            raise ValueError(
                f"result reviewer executable attestation is invalid: {exc}"
            ) from exc
        if any(
            dict(raw_provider_attestations[provider]) != identity
            or _clean_text(identity.get("provider")) != provider
            or _clean_text(identity.get("family")) == "unverified"
            for provider, identity in provider_attestations.items()
        ):
            raise ValueError(
                "result reviewer executable attestations are not exact verified identities"
            )
        different = {
            _clean_text(name)
            for name in review.get("different_family_providers") or []
            if _clean_text(name)
        }
        if review.get("different_family") is not True or not different:
            raise ValueError(
                "result_review_summary.v2 lacks a different-family reviewer"
            )
        if not different.issubset(payloads):
            raise ValueError(
                "different_family_providers are not present in provider_reviews"
            )
        actual_different = {
            name
            for name in payloads
            if _clean_text(provider_attestations[name].get("family"))
            not in {"unverified", executor_family}
        }
        if executor_family == "unverified" or different != actual_different:
            raise ValueError(
                "different_family_providers do not prove independent-family review"
            )
        expected_reviewer_families = {
            _clean_text(identity.get("family"))
            for identity in provider_attestations.values()
        }
        if {
            _clean_text(family)
            for family in review.get("reviewer_families") or []
            if _clean_text(family)
        } != expected_reviewer_families:
            raise ValueError(
                "result_review_summary.v2 reviewer_families are inconsistent"
            )
        raw_verdicts = {
            _clean_text(payload.get("verdict")).lower()
            for payload in payloads.values()
        }
        if "fail" in raw_verdicts:
            derived_conservative = "fail"
        elif "partial" in raw_verdicts:
            derived_conservative = "partial"
        elif raw_verdicts == {"pass"}:
            derived_conservative = "pass"
        else:
            derived_conservative = "unavailable"
        declared_conservative = _clean_text(review.get("conservative_verdict")).lower()
        synthesis = review.get("structured_synthesis")
        synthesis_conservative = _clean_text(
            synthesis.get("conservative_verdict")
            if isinstance(synthesis, dict)
            else declared_conservative
        ).lower()
        if (
            declared_conservative != derived_conservative
            or synthesis_conservative != derived_conservative
        ):
            raise ValueError(
                "result_review_summary.v2 conservative verdict disagrees with raw reviews"
            )
        if accepted and derived_conservative != "pass":
            raise ValueError(
                "accepted result_review_summary.v2 requires conservative_verdict=pass"
            )
        if not accepted and derived_conservative not in {"fail", "partial"}:
            raise ValueError(
                "rejected result_review_summary.v2 requires conservative_verdict=fail or partial"
            )
    else:
        raise ValueError(
            "result review must validate as result_review.v1 or result_review_summary.v2"
        )

    proposed_claims = _proposed_claim_ids(record)
    proposed_targets = proposed_obligation_targets(record)
    staged_evidence = validate_evidence_artifacts(
        record,
        require_artifacts=accepted,
        candidate_id=expected_id,
    )
    for provider, payload in payloads.items():
        errors = panel_review.validate_result_review(payload)
        if errors:
            raise ValueError(
                f"invalid result_review.v1 from {provider}: " + "; ".join(errors)
            )
        if _clean_text(payload.get("candidate_id")) != expected_id:
            raise ValueError(
                f"result_review.v1 from {provider} names a different candidate"
            )
        if _clean_text(payload.get("candidate_fingerprint")) != expected_fingerprint:
            raise ValueError(
                f"result_review.v1 from {provider} is not bound to the pending candidate"
            )
        if not accepted:
            continue
        if payload.get("verdict") != "pass" or payload.get("safe_to_bank") is not True:
            raise ValueError(
                f"accepted candidate requires a safe passing review from {provider}"
            )
        inspected = {
            _clean_text(path)
            for path in payload.get("inspected_paths") or []
            if _clean_text(path)
        }
        claim_rows = {
            _clean_text(row.get("claim_id")): row
            for row in payload.get("claim_reviews") or []
            if isinstance(row, dict) and _clean_text(row.get("claim_id"))
        }
        if set(claim_rows) != proposed_claims:
            raise ValueError(
                f"result_review.v1 from {provider} does not exactly cover proposed claim_ids"
            )
        for claim_id, row in claim_rows.items():
            refs = {
                _clean_text(ref)
                for ref in row.get("evidence_refs") or []
                if _clean_text(ref)
            }
            if (
                row.get("status") != "supported"
                or not refs
                or not refs.issubset(staged_evidence)
                or not refs.issubset(inspected)
            ):
                raise ValueError(
                    f"passing review from {provider} lacks inspected staged evidence for claim {claim_id}"
                )
        obligation_rows = {
            _clean_text(row.get("obligation_id")): row
            for row in payload.get("obligation_reviews") or []
            if isinstance(row, dict) and _clean_text(row.get("obligation_id"))
        }
        if set(obligation_rows) != set(proposed_targets):
            raise ValueError(
                f"result_review.v1 from {provider} does not exactly cover proposed obligation_ids"
            )
        for obligation_id, target in proposed_targets.items():
            row = obligation_rows[obligation_id]
            refs = {
                _clean_text(ref)
                for ref in row.get("evidence_refs") or []
                if _clean_text(ref)
            }
            if (
                row.get("verdict") != "accept"
                or _clean_text(row.get("target_status")) != target
                or not refs
                or not refs.issubset(staged_evidence)
                or not refs.issubset(inspected)
            ):
                raise ValueError(
                    f"passing review from {provider} lacks exact inspected evidence for obligation {obligation_id}"
                )


def validate_host_finalized_goal_success(
    run_dir: str | Path, record: Mapping[str, Any]
) -> list[str]:
    """Prove that a Goal-Focus success stop is a complete host finalization.

    This is deliberately separate from the legacy machine-checkable proof
    artifact path.  A reviewed research resolution may not be a formal proof,
    so the validator instead binds the terminal ledger row to the archived
    staged candidate, independent result review, live evidence bytes, terminal
    goal contract/plan/state, and the host result-finalize decision.
    """

    root = Path(run_dir)
    errors: list[str] = []

    def reject(message: str) -> None:
        errors.append(f"host-reviewed goal success: {message}")

    if record.get("decision") != "stop":
        reject("ledger decision must be stop")
    if _clean_text(record.get("stop_reason")) != HOST_REVIEWED_GOAL_SUCCESS_REASON:
        reject("ledger stop_reason is invalid")
    if record.get("bank_status") != "accepted":
        reject("ledger bank_status must be accepted")
    if record.get("global_delta") != "satisfied":
        reject("ledger global_delta must be satisfied")

    candidate_id = _clean_text(record.get("candidate_id"))
    if not candidate_id or not _SAFE_EVIDENCE_COMPONENT.fullmatch(candidate_id):
        reject("ledger candidate_id is missing or unsafe")
        return errors
    archive_path = root / ".goal_focus" / "candidates" / f"{candidate_id}.json"
    try:
        archived = _read_object(archive_path, required=True)
    except (OSError, ValueError) as exc:
        reject(f"accepted candidate archive is unavailable: {exc}")
        return errors

    if archived.get("schema_version") != ITERATION_CANDIDATE_SCHEMA:
        reject("accepted candidate archive schema is invalid")
    if _clean_text(archived.get("candidate_id")) != candidate_id:
        reject("accepted candidate archive id differs from the ledger")
    if archived.get("status") != "accepted":
        reject("candidate archive status must be accepted")
    archive_review = archived.get("review")
    ledger_review = record.get("result_review")
    if not isinstance(archive_review, Mapping) or not isinstance(
        ledger_review, Mapping
    ):
        reject("candidate archive and ledger require a result review")
        return errors
    if dict(archive_review) != dict(ledger_review):
        reject("candidate archive review differs from the ledger review")
    if _clean_text(archived.get("finalized_at")) != _clean_text(
        record.get("finalized_at")
    ):
        reject("candidate archive finalized_at differs from the ledger")

    pending = copy.deepcopy(dict(archived))
    pending.pop("review", None)
    pending.pop("finalized_at", None)
    pending["status"] = "pending_review"
    expected_fingerprint = candidate_fingerprint(pending)
    if _clean_text(record.get("source_candidate_fingerprint")) != expected_fingerprint:
        reject("ledger source candidate fingerprint is invalid")
    if _clean_text(archive_review.get("candidate_fingerprint")) != expected_fingerprint:
        reject("result review is not bound to the archived pending candidate")
    if _clean_text(archive_review.get("candidate_id")) != candidate_id:
        reject("result review candidate_id differs from the archive")
    if _clean_text(record.get("candidate_id")) != _clean_text(
        pending.get("candidate_id")
    ):
        reject("ledger candidate_id differs from the reconstructed candidate")
    for field in ("plan_revision", "campaign_id", "approach_id"):
        if record.get(field) != pending.get(field):
            reject(f"ledger {field} differs from the reconstructed candidate")

    review_status = _clean_text(
        archive_review.get("status") or archive_review.get("verdict")
    ).lower()
    if (
        archive_review.get("schema_version") != "result_review_summary.v2"
        or review_status not in {"passed", "accepted", "pass"}
        or archive_review.get("different_family") is not True
    ):
        reject("result review is not a passed different-family v2 summary")

    candidate_record = pending.get("record")
    if not isinstance(candidate_record, Mapping):
        reject("reconstructed candidate record is missing")
        return errors
    try:
        embedded = candidate_record.get("evidence_artifacts")
        current = _snapshot_evidence_artifacts(
            root, candidate_record, candidate_id=candidate_id
        )
        if embedded != current:
            reject("live candidate evidence bytes differ from the archived snapshots")
        validate_evidence_artifacts(
            record, require_artifacts=True, candidate_id=candidate_id
        )
        _validate_result_review_boundary(
            root,
            pending,
            record,
            archive_review,
            accepted=True,
            revalidate_executables=False,
        )
        _validate_accepted_review_coverage(record, archive_review)
    except (OSError, ValueError) as exc:
        reject(f"candidate evidence or result review is invalid: {exc}")

    try:
        contract = load_goal_contract(root)
        plan = load_current_plan(root)
        state = _read_object(root / "loop_state.json", required=True)
        decisions = load_direction_decisions(root)
        rows = _read_jsonl(root / "iterations.jsonl")
    except (OSError, ValueError) as exc:
        reject(f"terminal authority bundle is unavailable: {exc}")
        return errors

    required = _criterion_ids(contract)
    completed = _coherent_completed_obligations(
        contract.get("obligations")
        if isinstance(contract.get("obligations"), Mapping)
        else {}
    )
    if not required or not required <= completed:
        reject("not all required goal obligations are satisfied")
    obligations = (
        contract.get("obligations")
        if isinstance(contract.get("obligations"), Mapping)
        else {}
    )
    for obligation_id in sorted(required):
        obligation = obligations.get(obligation_id)
        if not isinstance(obligation, Mapping) or not any(
            _clean_text(ref) for ref in obligation.get("evidence_refs") or []
        ):
            reject(f"required obligation {obligation_id} lacks reviewed evidence")

    if (
        plan.get("state") != "terminal"
        or plan.get("terminal_reason") != HOST_REVIEWED_GOAL_SUCCESS_REASON
    ):
        reject("current plan is not terminal for the reviewed goal success")
    if state.get("status") != "stopped" or state.get("last_iteration") != record.get(
        "iteration"
    ):
        reject("loop state is not stopped at the terminal ledger row")
    if not rows or rows[-1] != dict(record):
        reject("reviewed goal success is not the exact latest ledger row")

    matching_decisions = [
        item
        for item in decisions
        if isinstance(item, Mapping)
        and item.get("decision_type") == "result_finalize"
        and _clean_text(item.get("candidate_id")) == candidate_id
        and item.get("bank_status") == "accepted"
        and item.get("mode_plan_fingerprint") == _object_fingerprint(plan)
        and all(
            item.get(field) == plan.get(field)
            for field in ("plan_revision", "goal_revision", "registry_revision")
        )
    ]
    if len(matching_decisions) != 1:
        reject("terminal plan lacks one exact accepted result-finalize decision")
    if any(
        (root / name).exists()
        for name in (
            PENDING_CANDIDATE_FILE,
            ITERATION_DISPATCH_FILE,
            CANDIDATE_QUARANTINE_FILE,
        )
    ):
        reject("terminal success coexists with pending, dispatch, or quarantine state")
    return errors


def _negative_space_entry_for_finalize(
    run_dir: Path,
    *,
    candidate: Mapping[str, Any],
    record: Mapping[str, Any],
    review: Mapping[str, Any],
    registry: Mapping[str, Any],
    accepted: bool,
) -> dict[str, Any] | None:
    """Build a negative-space row when finalization permanently blocks a route.

    Triggers (reject path only):
    - record.block_approach is true, or
    - record.negative_space is a mapping with mechanism details, or
    - the approach is blocked/closed in the postimage registry
    """

    if accepted:
        return None
    approach_id = _clean_text(
        record.get("approach_id") or candidate.get("approach_id")
    )
    campaign_id = _clean_text(
        record.get("campaign_id") or candidate.get("campaign_id")
    )
    approach = (
        _find_approach(registry, campaign_id, approach_id) if approach_id else None
    )
    approach_status = (
        _clean_text(approach.get("status")).lower() if isinstance(approach, dict) else ""
    )
    explicit = record.get("negative_space")
    if not isinstance(explicit, Mapping):
        explicit = {}
    block = (
        record.get("block_approach") is True
        or bool(explicit)
        or approach_status in {"blocked", "closed"}
    )
    if not block or not approach_id:
        return None
    mechanism = _clean_text(
        explicit.get("mechanism_text")
        or record.get("failure_summary")
        or record.get("stop_reason")
        or review.get("summary")
        or f"rejected candidate on approach {approach_id}"
    )
    failure = _clean_text(
        explicit.get("failure_summary")
        or record.get("stop_reason")
        or "result_rejected"
    )
    reopen = _clean_text(
        explicit.get("reopen_condition")
        or (approach.get("reopen_condition") if isinstance(approach, dict) else "")
        or "new mechanism required after independent review"
    )
    evidence_ids = [
        _clean_text(x)
        for x in (explicit.get("evidence_ids") or record.get("evidence_ids") or [])
        if _clean_text(x)
    ]
    review_fp = _object_fingerprint(dict(review)) if isinstance(review, Mapping) else ""
    try:
        return ns.entry_for_blocked_approach(
            approach_id=approach_id,
            campaign_id=campaign_id or None,
            mechanism_text=mechanism,
            failure_summary=failure,
            reopen_condition=reopen,
            evidence_ids=evidence_ids or None,
            evidence_absent_reason=(
                None
                if evidence_ids
                else _clean_text(explicit.get("evidence_absent_reason"))
                or "rejected_candidate_without_staged_evidence_ids"
            ),
            iteration_id=str(record.get("iteration") or ""),
            candidate_fingerprint=_clean_text(
                record.get("source_candidate_fingerprint")
                or candidate.get("candidate_id")
            ),
            result_review_fingerprint=review_fp or None,
            registry_revision=_positive_int(registry.get("registry_revision")),
            kind=_clean_text(explicit.get("kind")) or "blocked_route",
        )
    except ValueError:
        return None


def finalize_candidate(
    run_dir: str | Path,
    *,
    accepted: bool,
    review: Mapping[str, Any],
    ledger_record: Mapping[str, Any] | None = None,
    state_postimage: Mapping[str, Any] | None = None,
    budget_postimage: Mapping[str, Any] | None = None,
    plan_postimage: Mapping[str, Any] | None = None,
    contract_postimage: Mapping[str, Any] | None = None,
    registry_postimage: Mapping[str, Any] | None = None,
    expected_plan_revision: int | None = None,
) -> dict[str, Any]:
    root = Path(run_dir)
    recover_transactions(root)
    if load_candidate_quarantine(root):
        raise RevisionConflict(
            "cannot finalize while a failed completion is quarantined"
        )
    candidate, candidate_hash = _read_object_snapshot(
        root / PENDING_CANDIDATE_FILE, required=False
    )
    if not candidate:
        raise ValueError("no iteration candidate is pending")
    plan, plan_hash = _read_object_snapshot(
        root / CURRENT_PLAN_FILE, required=True
    )
    current_contract, contract_hash = _read_object_snapshot(
        root / GOAL_CONTRACT_FILE, required=True
    )
    current_registry, registry_hash = _read_object_snapshot(
        root / APPROACH_REGISTRY_FILE, required=True
    )
    expected_hashes: dict[str, str] = {
        PENDING_CANDIDATE_FILE: str(candidate_hash),
        CURRENT_PLAN_FILE: str(plan_hash),
        GOAL_CONTRACT_FILE: str(contract_hash),
        APPROACH_REGISTRY_FILE: str(registry_hash),
    }
    expected_absent: list[str] = [CANDIDATE_QUARANTINE_FILE]
    expected = _require_nonnegative_int(
        expected_plan_revision
        if expected_plan_revision is not None
        else candidate.get("plan_revision"),
        "expected_plan_revision",
    )
    live_plan_revision = _require_nonnegative_int(
        plan.get("plan_revision"), "current_plan.plan_revision"
    )
    if live_plan_revision != expected or candidate.get("plan_revision") != expected:
        raise RevisionConflict("pending candidate references a stale plan revision")
    bound_review = bool(candidate.get("host_execution_attestation")) or review.get(
        "schema_version"
    ) == "result_review_summary.v2"
    if bound_review:
        validate_host_staged_candidate(
            root,
            candidate,
            _authority={
                "plan": plan,
                "contract": current_contract,
                "registry": current_registry,
            },
        )
        expected_candidate_fingerprint = candidate_fingerprint(candidate)
        if _clean_text(review.get("candidate_fingerprint")) != expected_candidate_fingerprint:
            raise ValueError(
                "result review is not bound to the exact pending candidate content"
            )
    if _clean_text(review.get("candidate_id")) != _clean_text(candidate.get("candidate_id")):
        raise ValueError("result review candidate_id does not match the pending candidate")
    review_status = _clean_text(
        review.get("verdict")
        if review.get("schema_version") == RESULT_REVIEW_SCHEMA
        else review.get("status")
    ).lower()
    if accepted and review_status not in {"passed", "accepted", "pass"}:
        raise ValueError("an accepted candidate requires a passed result review")
    if accepted and review.get("different_family") is not True:
        raise ValueError("an accepted candidate requires a different-family result review")
    if not accepted and review_status not in {"failed", "rejected", "fail"}:
        raise ValueError(
            "a rejected candidate requires a failed/rejected review; operational review errors remain pending"
        )
    # R4 anti-false-consensus: multi-LLM LGTM alone never banks (defense in depth
    # beyond different_family, which is already required above).
    machine_ok = False
    if isinstance(ledger_record, Mapping):
        machine_ok = ledger_record.get("machine_check_passed") is True
    bank_ok, bank_reason = afc.bankable_review_ok(
        review, accepted=accepted, machine_check_passed=machine_ok
    )
    if not bank_ok:
        raise ValueError(f"anti-false-consensus bank gate: {bank_reason}")
    # R2: optional prior review snapshot on the candidate refuses wording-only progress.
    prior_review = (
        candidate.get("prior_review_snapshot")
        if isinstance(candidate.get("prior_review_snapshot"), Mapping)
        else None
    )
    if prior_review is not None and accepted:
        current_snap = {
            "evidence_ids": list(
                (ledger_record or candidate.get("record") or {}).get("evidence_ids")
                or []
            ),
            "obligation_transitions": list(
                (ledger_record or candidate.get("record") or {}).get(
                    "obligation_transitions"
                )
                or []
            ),
            "summary": _clean_text(review.get("summary") or review.get("notes")),
        }
        ok_delta, delta_reason, _delta = afc.allows_review_round_progress(
            prior_review, current_snap
        )
        if not ok_delta:
            raise ValueError(
                f"anti-false-consensus evidence-delta gate: {delta_reason}"
            )

    record = copy.deepcopy(dict(ledger_record or candidate.get("record") or {}))
    if bound_review:
        source_candidate_fingerprint = candidate_fingerprint(candidate)
        if ledger_record is not None and _clean_text(
            record.get("source_candidate_fingerprint")
        ) != source_candidate_fingerprint:
            raise ValueError(
                "reviewed ledger record is not derived from the exact pending candidate"
            )
        record["source_candidate_fingerprint"] = source_candidate_fingerprint
    _validate_result_review_boundary(
        root,
        candidate,
        record,
        review,
        accepted=accepted,
    )
    if accepted:
        _validate_accepted_review_coverage(record, review)
    rows, iterations_hash = _read_jsonl_snapshot(root / "iterations.jsonl")
    if iterations_hash is None:
        expected_absent.append("iterations.jsonl")
    else:
        expected_hashes["iterations.jsonl"] = iterations_hash
    record["iteration"] = len(rows) + 1
    record.update(
        {
            "event_id": _stable_id("iteration", candidate["candidate_id"]),
            "candidate_id": candidate["candidate_id"],
            "bank_status": "accepted" if accepted else "rejected",
            "plan_revision": expected,
            "campaign_id": candidate.get("campaign_id"),
            "approach_id": candidate.get("approach_id"),
            "result_review": copy.deepcopy(dict(review)),
            "finalized_at": utc_now(),
        }
    )
    contract = copy.deepcopy(dict(contract_postimage or current_contract))
    registry = copy.deepcopy(dict(registry_postimage or current_registry))
    if accepted:
        record.update(classify_progress(record, contract))
        staged_evidence = _record_evidence_ids(record)
        obligation_evidence = _reviewed_obligation_evidence(
            review, staged_evidence
        )
        if contract_postimage is None:
            contract = _apply_transitions(
                contract,
                record.get("obligation_transitions") or [],
                obligation_evidence,
            )
        goal_satisfied = record.get("global_delta") == "satisfied"
        if goal_satisfied:
            if record.get("decision") != "stop":
                record["proposed_decision"] = record.get("decision")
                record["proposed_stop_reason"] = record.get("stop_reason")
            record["decision"] = "stop"
            record["stop_reason"] = HOST_REVIEWED_GOAL_SUCCESS_REASON
        elif record.get("decision") in {"stop", "blocked"}:
            record["proposed_decision"] = record.get("decision")
            record["proposed_stop_reason"] = record.get("stop_reason")
            record["decision"] = "revise"
            record["stop_reason"] = "terminal_claim_not_supported_by_goal_obligations"
    else:
        goal_satisfied = False
        record["proposed_decision"] = record.get("decision")
        record["proposed_stop_reason"] = record.get("stop_reason")
        if isinstance(record.get("progress_assessment"), dict):
            record["proposed_progress_assessment"] = copy.deepcopy(
                record["progress_assessment"]
            )
        record["decision"] = "revise"
        record["stop_reason"] = "result_rejected"
        record["claim_ids"] = []
        record["claims"] = []
        record["obligation_transitions"] = []
        record["goal_contribution"] = "none"
        record["campaign_delta"] = "none"
        record["global_delta"] = "none"
        record["progress_assessment"] = {
            "campaign_delta": "none",
            "global_delta": "none",
            "obligation_ids": [],
        }
        if isinstance(record.get("evidence_checked"), dict):
            record["evidence_checked"]["claim_ids"] = []

    state_path = root / "loop_state.json"
    budget_path = root / "budget.json"
    current_state, state_hash = _read_object_snapshot(state_path, required=False)
    current_budget, budget_hash = _read_object_snapshot(budget_path, required=False)
    if state_hash is None:
        expected_absent.append("loop_state.json")
    else:
        expected_hashes["loop_state.json"] = state_hash
    if budget_hash is None:
        expected_absent.append("budget.json")
    else:
        expected_hashes["budget.json"] = budget_hash
    _finite_nonnegative_amount(current_budget.get("max_usd", 0.0), "budget.max_usd")
    current_spent_usd = _finite_nonnegative_amount(
        current_budget.get("spent_usd", 0.0), "budget.spent_usd"
    )
    state = copy.deepcopy(dict(state_postimage or current_state))
    budget = copy.deepcopy(dict(budget_postimage or current_budget))
    if budget:
        _finite_nonnegative_amount(budget.get("max_usd", 0.0), "budget.max_usd")
        _finite_nonnegative_amount(budget.get("spent_usd", 0.0), "budget.spent_usd")
    if state_postimage is None and state:
        state["last_iteration"] = record["iteration"]
        state["updated_at"] = utc_now()
    elif state:
        if _positive_int(state.get("last_iteration")) != record["iteration"]:
            raise ValueError("state_postimage must advance last_iteration to the finalized row")
    if budget_postimage is None and budget:
        budget["spent_iterations"] = _positive_int(budget.get("spent_iterations")) + 1
        delta = record.get("budget_delta") if isinstance(record.get("budget_delta"), dict) else {}
        budget["spent_tokens"] = _positive_int(budget.get("spent_tokens")) + _positive_int(delta.get("tokens"))
        budget["spent_usd"] = current_spent_usd + _finite_nonnegative_amount(
            delta.get("usd", 0.0), "budget_delta.usd"
        )
        budget["updated_at"] = utc_now()
    elif budget:
        required_spent = _positive_int(current_budget.get("spent_iterations")) + 1
        if _positive_int(budget.get("spent_iterations")) < required_spent:
            raise ValueError("budget_postimage must consume the finalized attempt")

    max_iterations = budget.get("max_iterations")
    final_budget_rejection = bool(
        not accepted
        and isinstance(max_iterations, int)
        and not isinstance(max_iterations, bool)
        and _positive_int(budget.get("spent_iterations")) >= max_iterations
    )
    final_budget_unresolved = bool(
        accepted
        and not goal_satisfied
        and isinstance(max_iterations, int)
        and not isinstance(max_iterations, bool)
        and _positive_int(budget.get("spent_iterations")) >= max_iterations
    )
    if final_budget_rejection:
        # A rejected proof/result still consumes its attempt.  At the hard
        # iteration limit there is no legal continuing state, so normalize the
        # authoritative disposition to a non-success terminal block rather
        # than preserving a rejected `stop/proof_found` claim.
        record["decision"] = "blocked"
        record["stop_reason"] = "result_rejected_at_iteration_budget_limit"
        record["rejection_disposition"] = "terminal_budget_exhausted"
    if final_budget_unresolved:
        record["decision"] = "blocked"
        record["stop_reason"] = "iteration_budget_exhausted_without_goal_resolution"
    desired_state_status = (
        "blocked"
        if final_budget_rejection or final_budget_unresolved or record.get("decision") == "blocked"
        else "stopped"
        if goal_satisfied
        else "running"
    )
    if state:
        state["status"] = desired_state_status
        state["updated_at"] = utc_now()

    next_plan = copy.deepcopy(dict(plan_postimage or plan))
    if not accepted:
        next_plan["plan_revision"] = max(expected + 1, _positive_int(next_plan.get("plan_revision")))
        next_plan["state"] = "needs_replan"
        next_plan["updated_at"] = utc_now()
    if final_budget_rejection:
        next_plan["state"] = "terminal"
        next_plan["terminal_reason"] = "iteration_budget_exhausted_after_result_rejection"
        next_plan["next_action"] = (
            "Report the rejected final attempt and request a new iteration budget before replanning."
        )
        next_plan["updated_at"] = utc_now()
    if contract.get("goal_revision") != next_plan.get("goal_revision"):
        next_plan["goal_revision"] = contract.get("goal_revision")
        if accepted:
            next_plan["plan_revision"] = max(expected + 1, _positive_int(next_plan.get("plan_revision")))
            next_plan["state"] = "needs_replan"
            next_plan["updated_at"] = utc_now()
    if registry.get("registry_revision") != next_plan.get("registry_revision"):
        next_plan["registry_revision"] = registry.get("registry_revision")
    if goal_satisfied:
        next_plan["state"] = "terminal"
        next_plan["terminal_reason"] = HOST_REVIEWED_GOAL_SUCCESS_REASON
        next_plan["next_action"] = "Report the independently reviewed resolution."
        next_plan["updated_at"] = utc_now()
    elif final_budget_unresolved:
        next_plan["state"] = "terminal"
        next_plan["terminal_reason"] = "iteration_budget_exhausted_without_goal_resolution"
        next_plan["next_action"] = (
            "Report the unresolved goal and request a new iteration budget before replanning."
        )
        next_plan["updated_at"] = utc_now()

    archive_rel = (Path(".goal_focus") / "candidates" / f"{candidate['candidate_id']}.json").as_posix()
    expected_absent.append(archive_rel)
    archived = copy.deepcopy(candidate)
    archived.update(
        {
            "status": "accepted" if accepted else "rejected",
            "review": copy.deepcopy(dict(review)),
            "finalized_at": record["finalized_at"],
        }
    )
    mode_event_id = _stable_id(
        "decision", "result-finalize", candidate["candidate_id"], next_plan.get("plan_revision")
    )
    mode_event = {
        "schema_version": DIRECTION_DECISION_SCHEMA,
        "event_id": mode_event_id,
        "decision_id": mode_event_id,
        "decision_type": "result_finalize",
        "enforcement_mode": next_plan.get("enforcement_mode"),
        "mode_plan_fingerprint": _object_fingerprint(next_plan),
        "plan_revision": next_plan.get("plan_revision"),
        "goal_revision": next_plan.get("goal_revision"),
        "registry_revision": next_plan.get("registry_revision"),
        "candidate_id": candidate.get("candidate_id"),
        "bank_status": "accepted" if accepted else "rejected",
        "timestamp": utc_now(),
    }
    json_files: dict[str, Any] = {
        CURRENT_PLAN_FILE: next_plan,
        GOAL_CONTRACT_FILE: contract,
        APPROACH_REGISTRY_FILE: registry,
        archive_rel: archived,
    }
    if state:
        json_files["loop_state.json"] = state
    if budget:
        json_files["budget.json"] = budget

    jsonl_appends: dict[str, list[Any]] = {
        "iterations.jsonl": [record],
        DIRECTION_DECISIONS_FILE: [mode_event],
    }
    ns_entry = _negative_space_entry_for_finalize(
        root,
        candidate=candidate,
        record=record,
        review=review,
        registry=registry,
        accepted=accepted,
    )
    if ns_entry is not None:
        ns_rel = ns.NEGATIVE_SPACE_REL.as_posix()
        ns_path = root / ns.NEGATIVE_SPACE_REL
        if ns_path.exists():
            try:
                expected_hashes[ns_rel] = hashlib.sha256(
                    _read_regular_bytes(ns_path, max_bytes=16_000_000)
                ).hexdigest()
            except (OSError, ValueError, TypeError):
                # Fall back: let transaction create postimage without preimage hash.
                pass
        else:
            expected_absent.append(ns_rel)
        jsonl_appends[ns_rel] = [ns_entry]
        record["negative_space_entry_id"] = ns_entry.get("entry_id")

    tx = commit_transaction(
        root,
        json_files=json_files,
        jsonl_appends=jsonl_appends,
        deletes=[PENDING_CANDIDATE_FILE],
        expected_revisions={
            CURRENT_PLAN_FILE: ("plan_revision", expected),
            GOAL_CONTRACT_FILE: ("goal_revision", current_contract.get("goal_revision")),
            APPROACH_REGISTRY_FILE: ("registry_revision", current_registry.get("registry_revision")),
        },
        expected_absent=expected_absent,
        expected_hashes=expected_hashes,
        transaction_id=_stable_id("finalize", candidate["candidate_id"]),
    )
    # Reconciliation is a separate idempotent projection pass because accepted
    # obligation transitions can revise both goal and plan during finalization.
    reconcile_goal_focus(root, apply=True)
    return {
        "status": "accepted" if accepted else "rejected",
        "record": record,
        "plan": next_plan,
        "transaction": tx,
    }
