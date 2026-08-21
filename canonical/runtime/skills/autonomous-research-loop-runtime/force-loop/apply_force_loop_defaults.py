#!/usr/bin/env python3
"""Apply force-loop default pins to a loop directory.

Always materializes (not examples-only):
  - Goal Focus enforcement_mode=enforce (current_plan + standing_orders.goal_focus)
  - goal_priority enabled=true, discipline_mode=hard (+ env AAS_AUTOLOOP_GOAL_PRIORITY=on)
  - notify ON (AAS_AUTOLOOP_NOTIFY=auto; standing_orders.notify)
  - compute_policy.json + standing_orders.compute (profile-dependent)
  - formal standing/file pins when --profile formal

Idempotent: merges into existing files; does not delete campaign content.
Never prints secrets.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

PACK_DIR = Path(__file__).resolve().parent
DEFAULTS_DIR = PACK_DIR / "defaults"
if str(PACK_DIR) not in sys.path:
    sys.path.insert(0, str(PACK_DIR))
# The runtime package directory carries state_transaction (loop lock + journal
# recovery); pinning must take the same lock the drive takes.
if str(PACK_DIR.parent) not in sys.path:
    sys.path.insert(0, str(PACK_DIR.parent))

PROFILES = frozenset({"formal", "general"})
MAX_LEGACY_POLICY_BYTES = 16_384
# Compute lanes are not pure pins: the value selects which credential lane the
# start path is allowed to project, so it can never be inherited from an
# agent-writable legacy shadow.
NON_MIGRATABLE_POLICY_KEYS = frozenset({"AAS_FORCE_LOOP_COMPUTE_LANES"})
PENDING_JOURNAL_ERROR = (
    "a Goal-Focus transaction journal is pending; run drive or "
    "goal-focus recovery before re-applying pins"
)


@contextlib.contextmanager
def _pin_guard(run_dir: Path):
    """Finish any pending Goal-Focus journal, then hold the loop lock.

    ``loop_state.json`` and ``current_plan.json`` are transaction-managed:
    pinning them beside a live drive loses one side of the update, and pinning
    over a pending journal is reverted by the next replay.  The runtime import
    stays lazy because this module is documented as runnable standalone.
    """

    try:
        from state_transaction import (
            TRANSACTION_DIRNAME,
            LoopLock,
            TransactionError,
            recover_transactions,
        )
    except ImportError:
        if (run_dir / ".goal_focus_transactions").is_dir():
            raise ValueError(PENDING_JOURNAL_ERROR) from None
        yield
        return
    try:
        # recover_transactions takes the loop lock itself and LoopLock is a
        # non-reentrant flock, so replay has to finish before we acquire it.
        recover_transactions(run_dir)
        if (run_dir / TRANSACTION_DIRNAME).is_dir():
            raise ValueError(PENDING_JOURNAL_ERROR)
        with LoopLock(run_dir):
            yield
    except TransactionError as exc:
        raise ValueError(f"loop state is not safe to pin: {exc}") from exc


def _legacy_policy_preflight(run_dir: Path) -> tuple[Path | None, dict[str, str]]:
    """Admit only nonsecret legacy policy fields without copying old bytes."""

    from load_loop_env import POLICY_KEYS, EnvLoadError, parse_env_text

    candidates = (
        run_dir / "driver" / "force_loop.env",
        run_dir / "driver" / "force_loop_pin_backups" / "force_loop.env",
    )
    active: Path | None = None
    migrated: dict[str, str] = {}
    for candidate in candidates:
        if candidate.is_symlink():
            raise ValueError("refusing symlinked legacy force-loop policy artifact")
        if not candidate.exists():
            continue
        if not candidate.is_file():
            raise ValueError("legacy force-loop policy artifact has an unsafe type")
        if os.name != "posix":
            # POSIX descriptor ownership checks below (fstat uid) cannot bind
            # trust on native Windows; fail closed like load_loop_env.
            raise ValueError(
                "native Windows legacy force-loop policy preflight requires "
                "the PowerShell loader"
            )
        descriptor = os.open(
            candidate,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != os.geteuid()
                or before.st_size > MAX_LEGACY_POLICY_BYTES
            ):
                raise ValueError("legacy force-loop policy artifact is unsafe")
            payload = os.read(descriptor, MAX_LEGACY_POLICY_BYTES + 1)
            after = os.fstat(descriptor)
            stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            if len(payload) > MAX_LEGACY_POLICY_BYTES or any(
                getattr(before, field) != getattr(after, field) for field in stable
            ):
                raise ValueError("legacy force-loop policy changed during preflight")
        finally:
            os.close(descriptor)
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("legacy force-loop policy requires redacted manual migration") from exc
        observed_names = {
            line.split("=", 1)[0]
            for line in text.splitlines()
            if line and not line.startswith("#") and "=" in line
        }
        if not observed_names.issubset(POLICY_KEYS):
            raise ValueError(
                "legacy force-loop policy contains credential-capable or unsupported "
                "fields; promote configured credentials to canonical authorities and "
                "remove every shadow copy before retrying"
            )
        if observed_names & NON_MIGRATABLE_POLICY_KEYS:
            raise ValueError(
                "legacy force-loop policy selects compute lanes; set "
                f"{sorted(NON_MIGRATABLE_POLICY_KEYS)[0]} in the host policy file "
                "yourself and remove the loop-local shadow before retrying"
            )
        try:
            parsed = parse_env_text(text, source="<legacy-force-loop-policy>")
        except EnvLoadError as exc:
            raise ValueError("legacy force-loop policy requires redacted manual migration") from exc
        if candidate == candidates[1]:
            raise ValueError(
                "legacy force-loop backup shadow exists; verify it contains no credentials "
                "and remove it before retrying"
            )
        active = candidate
        migrated.update(parsed)
    return active, migrated


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=False)
            handle.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            if text and not text.endswith("\n"):
                handle.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_json_strict(path: Path) -> dict[str, Any]:
    """Read a file this module is about to rewrite, refusing damaged content.

    ``_read_json`` degrades to ``{}``, which is safe for inspection but would
    let a pin pass silently overwrite a corrupt or hand-edited campaign file
    with profile defaults.
    """

    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"{path.name} is unreadable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return data


def _backup(path: Path, backup_dir: Path) -> None:
    if not path.is_file():
        return
    backup_dir.mkdir(parents=True, exist_ok=True)
    # One backup per run; a fixed name would let a second apply-defaults
    # overwrite the only copy of the pre-pin state.
    dest = backup_dir / f"{path.name}.{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    shutil.copy2(path, dest)


def _load_profile_compute(profile: str) -> dict[str, Any]:
    name = (
        "compute_policy.formal.json"
        if profile == "formal"
        else "compute_policy.general.json"
    )
    path = DEFAULTS_DIR / name
    data = _read_json(path)
    if not data:
        raise FileNotFoundError(f"missing defaults file: {path}")
    return data


def _load_goal_priority_base() -> dict[str, Any]:
    path = DEFAULTS_DIR / "goal_priority.base.json"
    data = _read_json(path)
    if not data:
        raise FileNotFoundError(f"missing defaults file: {path}")
    return data


def apply_goal_priority(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "goal_priority.json"
    existing = _read_json_strict(path)
    base = _load_goal_priority_base()
    if existing:
        # Preserve campaign structure; force discipline pins.
        merged = dict(existing)
        merged["schema_version"] = existing.get("schema_version") or base["schema_version"]
        merged["enabled"] = True
        merged["discipline_mode"] = "hard"
        if not merged.get("primary_campaign"):
            merged["primary_campaign"] = base["primary_campaign"]
        if not merged.get("campaign_registry"):
            merged["campaign_registry"] = base["campaign_registry"]
        if "require_goal_contribution_in_ledger" not in merged:
            merged["require_goal_contribution_in_ledger"] = True
        if "panel_rank_by_goal_ev" not in merged:
            merged["panel_rank_by_goal_ev"] = True
    else:
        merged = dict(base)
        merged["enabled"] = True
        merged["discipline_mode"] = "hard"
    _atomic_write_json(path, merged)
    return merged


def apply_compute_policy(run_dir: Path, profile: str) -> dict[str, Any]:
    path = run_dir / "compute_policy.json"
    data = _load_profile_compute(profile)
    _atomic_write_json(path, data)
    return data


def apply_notify_identity(run_dir: Path, *, research_title: str | None) -> dict[str, Any]:
    path = run_dir / "notify.json"
    existing = _read_json_strict(path)
    data = dict(existing) if existing else {}
    title = research_title or data.get("research_title") or data.get("notify_title")
    if title:
        data.setdefault("research_title", str(title))
        data.setdefault("notify_title", str(title))
        data.setdefault("display_name", str(title))
    data.setdefault("body_profile", "operator_full")
    if not path.is_file() or research_title:
        _atomic_write_json(path, data)
    return data


def apply_formal_policy(run_dir: Path, profile: str) -> dict[str, Any] | None:
    if profile != "formal":
        return None
    formal_dir = run_dir / "formal"
    formal_dir.mkdir(parents=True, exist_ok=True)
    path = formal_dir / "formal_policy.json"
    existing = _read_json(path)
    cfg = {
        "schema_version": "formal_policy.v1",
        "policy": "on",
        "project": existing.get("project") or ".",
        "force_credits": int(existing.get("force_credits") or 3),
        "allow_path_steal": bool(existing.get("allow_path_steal", False)),
        "typecheck": True,
        "force_after_iteration": bool(existing.get("force_after_iteration", False)),
        "allow_create_skeleton": bool(existing.get("allow_create_skeleton", True)),
        "notes": existing.get("notes")
        if isinstance(existing.get("notes"), list)
        else ["force-loop formal profile"],
        "status": existing.get("status")
        if isinstance(existing.get("status"), dict)
        else {
            "phase": "",
            "lake_build": "",
            "sorry_count": None,
            "updated_at": "",
        },
    }
    _atomic_write_json(path, cfg)
    return cfg


def apply_current_plan_enforce(run_dir: Path) -> bool:
    """Report whether the plan already carries Goal Focus enforce.

    ``current_plan.enforcement_mode`` is authority-bound: it is only valid
    while a ``direction_decisions.jsonl`` row fingerprints the whole plan.
    Writing the field here breaks that binding permanently, so escalation
    belongs to ``goal-focus set-mode``, which rewrites plan and decision row
    inside one transaction.
    """

    path = run_dir / "current_plan.json"
    if not path.is_file():
        return False
    plan = _read_json_strict(path)
    return str(plan.get("enforcement_mode") or "").lower() == "enforce"


def apply_standing_orders(
    run_dir: Path,
    *,
    profile: str,
    compute: dict[str, Any],
    formal: dict[str, Any] | None,
    research_title: str | None,
) -> dict[str, Any]:
    path = run_dir / "loop_state.json"
    state = _read_json_strict(path)
    if not state:
        # Minimal standing shell so pins exist even before full init.
        state = {
            "schema_version": "1.0",
            "status": "initialized",
            "standing_orders": {},
        }
    so = state.get("standing_orders")
    if not isinstance(so, dict):
        so = {}
        state["standing_orders"] = so

    policy = compute.get("policy") if isinstance(compute.get("policy"), dict) else compute
    backends = list(policy.get("backends") or [])
    forbidden = list(policy.get("forbidden_services") or [])
    so["compute"] = {
        "backends": backends,
        "forbidden_services": forbidden,
        "note": str(compute.get("notes") or "force-loop compute pin"),
    }
    so["goal_focus"] = {
        "mode": "enforce",
        "note": "force-loop default: Goal Focus enforce + goal_priority hard",
    }
    so["goal_priority"] = {
        "enabled": True,
        "discipline_mode": "hard",
    }
    notify = so.get("notify") if isinstance(so.get("notify"), dict) else {}
    notify["mode"] = "auto"
    notify["schema"] = notify.get("schema") or "aas.autoloop.notify.v2"
    notify["schema_version"] = notify.get("schema_version") or "2.1"
    notify["body_profile"] = notify.get("body_profile") or "operator_full"
    if research_title:
        notify["research_title"] = research_title
    so["notify"] = notify

    if profile == "formal" and formal:
        so_formal = so.get("formal") if isinstance(so.get("formal"), dict) else {}
        so_formal.update(
            {
                "policy": formal.get("policy", "on"),
                "project": formal.get("project", "."),
                "typecheck": True,
                "force_after_iteration": bool(formal.get("force_after_iteration", False)),
                "force_credits": int(formal.get("force_credits") or 3),
                "allow_path_steal": bool(formal.get("allow_path_steal", False)),
                "allow_create_skeleton": bool(formal.get("allow_create_skeleton", True)),
                "note": "force-loop formal profile; distinct from formal_policy=force hygiene",
            }
        )
        so["formal"] = so_formal

    panel = so.get("panel") if isinstance(so.get("panel"), dict) else {}
    panel.setdefault("enabled", True)
    so["panel"] = panel

    state["standing_orders"] = so
    # Align top-level notify policy fields when present / useful for drive.
    state["notify_policy"] = state.get("notify_policy") or "on"
    _atomic_write_json(path, state)
    return so


def _validated_policy_path(run_dir: Path, policy_file: Path) -> Path:
    if not policy_file.is_absolute():
        raise ValueError("force-loop policy path must be absolute")
    dest = Path(os.path.abspath(policy_file))
    try:
        dest.relative_to(run_dir)
    except ValueError:
        pass
    else:
        raise ValueError("force-loop policy must be outside the loop tree")
    current = dest.parent
    while not current.exists():
        current = current.parent
    if current.is_symlink():
        raise ValueError("force-loop policy parent must not be symlinked")
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for parent in [dest.parent, *dest.parent.parents]:
        if parent.is_symlink():
            raise ValueError("force-loop policy parent chain must not contain links")
    return dest


def _reproject_windows_policy(dest: Path, values: dict[str, str]) -> None:
    """Republish the host policy this process just wrote, for this process.

    On native Windows ``load_env_file`` does not read the file: PowerShell owns
    the file checks and hands the validated policy over through the projection
    ``Load-LoopEnv.ps1`` took at process start.  Defaults written during a run
    were therefore invisible to the ``verify_effective`` call that follows, so a
    first bootstrap reported ``host policy missing AAS_AUTOLOOP_GOAL_PRIORITY=on``
    while an otherwise identical second run passed.  Republishing here keeps the
    projection and the file in step within the run that changed them.

    This widens nothing.  The keys were validated on the way in, the manifest
    names only what was written, and ``load_projected_env`` re-parses them under
    the same strict grammar.  Policy keys the write dropped are removed so a
    stale pin cannot outlive the file or reach a child process.
    """
    from load_loop_env import (
        POLICY_KEYS,
        WINDOWS_PROJECTION_ENV,
        WINDOWS_PROJECTION_SOURCE_ENV,
    )

    for key in sorted(values):
        os.environ[key] = values[key]
    for key in POLICY_KEYS - set(values):
        os.environ.pop(key, None)
    os.environ[WINDOWS_PROJECTION_ENV] = ",".join(sorted(values))
    os.environ[WINDOWS_PROJECTION_SOURCE_ENV] = str(dest)


def write_host_env_defaults(
    run_dir: Path,
    profile: str,
    policy_file: Path,
    *,
    migrated_policy: dict[str, str] | None = None,
) -> Path:
    """Write the host policy outside the agent-writable loop tree."""
    dest = _validated_policy_path(run_dir, policy_file)
    # Idempotence: preserve operator-set keys already in the destination
    # (e.g. AAS_FORCE_LOOP_COMPUTE_LANES) instead of silently deleting them
    # on re-runs; migrated and fixed defaults still override below.
    values: dict[str, str] = {}
    if dest.is_file():
        from load_loop_env import EnvLoadError, load_env_file

        try:
            values.update(load_env_file(dest, forbidden_root=run_dir))
        except EnvLoadError as exc:
            raise ValueError(
                f"existing host force-loop policy is unreadable; fix or move it first: {exc}"
            ) from exc
    values.update(migrated_policy or {})
    values.update(
        {
            "AAS_AUTOLOOP_GOAL_PRIORITY": "on",
            "AAS_AUTOLOOP_NOTIFY": "auto",
            "AAS_AUTOLOOP_FORMAL_POLICY": "on" if profile == "formal" else "off",
        }
    )
    if profile == "formal":
        values["AAS_AUTOLOOP_FORMAL_TYPECHECK"] = "1"
    else:
        values.pop("AAS_AUTOLOOP_FORMAL_TYPECHECK", None)
    lines = [
        "# Generated by apply_force_loop_defaults.py — strict KEY=VALUE only.",
        "# Load via load_loop_env.py; never shell-source agent-writable trees.",
    ]
    lines.extend(f"{key}={values[key]}" for key in sorted(values))
    _atomic_write_text(dest, "\n".join(lines) + "\n")
    if os.name == "posix":
        dest.chmod(0o600)
    else:
        _reproject_windows_policy(dest, values)
    return dest


def verify_effective(run_dir: Path, profile: str, policy_file: Path | None = None) -> list[str]:
    """Return list of missing-default errors (empty = ok)."""
    errors: list[str] = []
    gp = _read_json(run_dir / "goal_priority.json")
    if not gp.get("enabled"):
        errors.append("goal_priority.enabled is not true")
    if str(gp.get("discipline_mode") or "").lower() != "hard":
        errors.append("goal_priority.discipline_mode is not hard")

    state = _read_json(run_dir / "loop_state.json")
    so = state.get("standing_orders") if isinstance(state.get("standing_orders"), dict) else {}
    gf = so.get("goal_focus") if isinstance(so.get("goal_focus"), dict) else {}
    if str(gf.get("mode") or "").lower() != "enforce":
        errors.append("standing_orders.goal_focus.mode is not enforce")
    gpp = so.get("goal_priority") if isinstance(so.get("goal_priority"), dict) else {}
    if str(gpp.get("discipline_mode") or "").lower() != "hard":
        errors.append("standing_orders.goal_priority.discipline_mode is not hard")
    notify = so.get("notify") if isinstance(so.get("notify"), dict) else {}
    mode = str(notify.get("mode") or "").lower()
    if mode not in {"auto", "on"}:
        errors.append("standing_orders.notify.mode is not auto/on")
    channel = str(state.get("notify_channel") or "").lower()
    if mode in {"auto", "on"} and channel in {"off", "none", "disabled"}:
        errors.append("loop_state.notify_channel is off while the notify pin is auto/on")

    cp = _read_json(run_dir / "compute_policy.json")
    compute = cp.get("policy") if isinstance(cp.get("policy"), dict) else cp
    backends = set(compute.get("backends") or ()) if isinstance(compute, dict) else set()
    forbidden = set(compute.get("forbidden_services") or ()) if isinstance(compute, dict) else set()
    standing = so.get("compute") if isinstance(so.get("compute"), dict) else {}
    if not backends:
        errors.append("compute_policy backends missing")
    # A backend that is also forbidden is refused at dispatch, so an allowlist
    # that intersects the denylist is a silently unusable lane, not a pin.
    if backends & forbidden:
        errors.append("compute_policy backends intersect forbidden_services")
    if backends != set(standing.get("backends") or ()) or forbidden != set(
        standing.get("forbidden_services") or ()
    ):
        errors.append("standing_orders.compute does not mirror compute_policy.json")

    if policy_file is None:
        errors.append("host force-loop policy path missing")
    else:
        try:
            from load_loop_env import load_env_file

            policy = load_env_file(policy_file, forbidden_root=run_dir)
        except Exception as exc:
            errors.append(f"host force-loop policy is missing or unsafe: {exc}")
        else:
            if policy.get("AAS_AUTOLOOP_GOAL_PRIORITY") != "on":
                errors.append("host policy missing AAS_AUTOLOOP_GOAL_PRIORITY=on")
            if policy.get("AAS_AUTOLOOP_NOTIFY") not in {"auto", "on"}:
                errors.append("host policy missing notify ON")

    # current_plan.json is the only enforcement_mode any runtime reads; the
    # standing-orders mirror above is advisory.
    plan = _read_json(run_dir / "current_plan.json")
    if not plan:
        errors.append(
            "current_plan.json is missing; Goal Focus enforce is not established "
            "(re-init with --goal-focus-mode enforce or run goal-focus migrate)"
        )
    elif str(plan.get("enforcement_mode") or "").lower() != "enforce":
        errors.append(
            "current_plan.enforcement_mode is not enforce; escalate the mode with "
            "goal-focus set-mode --mode enforce --apply"
        )

    if profile == "formal":
        formal = _read_json(run_dir / "formal" / "formal_policy.json")
        if str(formal.get("policy") or "").lower() != "on":
            errors.append("formal_policy.policy is not on")
        if not formal.get("typecheck"):
            errors.append("formal_policy.typecheck is not true")

    return errors


def apply_defaults(
    run_dir: Path,
    *,
    profile: str = "formal",
    research_title: str | None = None,
    backup: bool = True,
    policy_file: Path | None = None,
) -> dict[str, Any]:
    profile = (profile or "formal").strip().lower()
    if profile not in PROFILES:
        raise ValueError(f"profile must be one of {sorted(PROFILES)}")
    run_dir = run_dir.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    if policy_file is None:
        raise ValueError("an explicit host --policy-file is required")
    policy_file = _validated_policy_path(run_dir, policy_file.expanduser())
    legacy_policy, migrated_policy = _legacy_policy_preflight(run_dir)

    with _pin_guard(run_dir):
        if backup:
            backup_dir = run_dir / "driver" / "force_loop_pin_backups"
            for name in (
                "goal_priority.json",
                "compute_policy.json",
                "loop_state.json",
                "current_plan.json",
                "notify.json",
            ):
                _backup(run_dir / name, backup_dir)
            _backup(run_dir / "formal" / "formal_policy.json", backup_dir)
        gp = apply_goal_priority(run_dir)
        compute = apply_compute_policy(run_dir, profile)
        formal = apply_formal_policy(run_dir, profile)
        notify = apply_notify_identity(run_dir, research_title=research_title)
        plan_enforced = apply_current_plan_enforce(run_dir)
        standing = apply_standing_orders(
            run_dir,
            profile=profile,
            compute=compute,
            formal=formal,
            research_title=research_title,
        )
        env_path = write_host_env_defaults(
            run_dir,
            profile,
            policy_file,
            migrated_policy=migrated_policy,
        )
        if legacy_policy is not None:
            legacy_policy.unlink()
        errors = verify_effective(run_dir, profile, policy_file)
    return {
        "ok": not errors,
        "profile": profile,
        "run_dir": str(run_dir),
        "goal_priority": {
            "enabled": gp.get("enabled"),
            "discipline_mode": gp.get("discipline_mode"),
        },
        "notify_mode": (standing.get("notify") or {}).get("mode"),
        "goal_focus_mode": (standing.get("goal_focus") or {}).get("mode"),
        "policy_path": str(env_path),
        "migrated_policy_keys": sorted(migrated_policy),
        "current_plan_enforced": plan_enforced,
        "notify_identity": {
            "research_title": notify.get("research_title"),
        },
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dir", required=True, help="loop directory")
    p.add_argument(
        "--profile",
        default="formal",
        choices=sorted(PROFILES),
        help="formal | general",
    )
    p.add_argument("--research-title", default=None)
    p.add_argument("--policy-file", required=True)
    p.add_argument("--no-backup", action="store_true")
    p.add_argument(
        "--verify-only",
        action="store_true",
        help="check effective defaults without writing",
    )
    args = p.parse_args(argv)
    run_dir = Path(args.dir).expanduser().resolve()
    if args.verify_only:
        errors = verify_effective(run_dir, args.profile, Path(args.policy_file).expanduser())
        print(json.dumps({"ok": not errors, "errors": errors, "run_dir": str(run_dir)}, indent=2))
        return 0 if not errors else 1
    result = apply_defaults(
        run_dir,
        profile=args.profile,
        research_title=args.research_title,
        backup=not args.no_backup,
        policy_file=Path(args.policy_file).expanduser(),
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
