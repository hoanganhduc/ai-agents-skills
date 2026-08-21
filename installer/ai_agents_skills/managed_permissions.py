"""Bounded POSIX permission repair for installer-managed skill/runtime parents."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from .agents import AgentTarget, target_for
from .capabilities import normalized_path_within


ACTIVE_OPERATIONS = {
    "adopt",
    "backup-replace",
    "create",
    "migrate-install",
    "noop",
    "update",
}


def managed_parent_boundary(root: Path, action: dict[str, Any]) -> Path | None:
    """Return the narrow managed directory root for a skill/runtime file action."""
    path = Path(str(action.get("path") or ""))
    if action.get("kind") == "runtime-file":
        value = action.get("runtime_root")
        if not isinstance(value, str) or not value:
            return None
        boundary = Path(value)
        return boundary if normalized_path_within(boundary, path.parent) else None

    agent_name = str(action.get("agent") or "")
    skill = str(action.get("skill") or "")
    if not agent_name or not skill or agent_name == "runtime":
        return None
    try:
        target = target_for(root, agent_name)
    except ValueError:
        return None
    candidates = {target.skills_dir, target.support_dir_for(skill).parent}
    contained = [candidate for candidate in candidates if normalized_path_within(candidate, path.parent)]
    if not contained:
        return None
    return max(contained, key=lambda candidate: len(candidate.parts))


def managed_boundary_dirs(target: AgentTarget) -> list[Path]:
    """Return the managed directory roots an install would normalize for ``target``.

    This mirrors the candidate set ``managed_parent_boundary`` selects from.  The
    support-directory parent does not depend on the skill, so the placeholder
    name never reaches the result.
    """
    return sorted({target.skills_dir, target.support_dir_for("_").parent}, key=str)


def managed_boundary_block_reason(root: Path, target: AgentTarget) -> str | None:
    """Return why ``target``'s managed directories cannot be planned, if any.

    ``plan_managed_parent_chain`` fails closed on a component that is a symlink,
    is not a directory, or is owned by neither root nor the caller.  It raises
    from the per-action loop, so one unusable target aborts the whole plan and
    takes every other agent down with it -- an agent CLI that migrates its own
    layout and leaves a compatibility symlink where the skills directory used to
    be is enough to do that.  Detecting the same condition per target turns the
    abort into a reported skip, without relaxing any check: a blocked target is
    still never written to.

    A missing directory is not a block.  Installing into a fresh agent home is
    the normal case, and the chain planner creates what is absent.
    """
    if os.name != "posix":
        return None
    for boundary in managed_boundary_dirs(target):
        if not normalized_path_within(root, boundary):
            continue
        current = root
        for component in boundary.relative_to(root).parts:
            current /= component
            try:
                info = current.lstat()
            except OSError:
                break
            if stat.S_ISLNK(info.st_mode):
                return f"managed skill directory is a symlink: {current}"
            if not stat.S_ISDIR(info.st_mode):
                return f"managed skill directory is not a directory: {current}"
            if int(info.st_uid) not in {0, os.geteuid()}:
                return f"managed skill directory has an untrusted owner: {current}"
    return None


def _normalize_managed_parent_chain(
    root: Path,
    action: dict[str, Any],
    *,
    create: bool,
) -> list[dict[str, Any]]:
    """Remove group/other write bits along one declared managed parent chain.

    The selected install root and agent home are never chmodded. Traversal starts at the
    managed boundary's already-existing parent and uses no-follow directory descriptors, so a
    concurrent link swap cannot redirect repair outside the declared surface.
    """
    if os.name != "posix" or action.get("operation") not in ACTIVE_OPERATIONS:
        return []
    boundary = managed_parent_boundary(root, action)
    if boundary is None:
        return []
    target_parent = Path(str(action["path"])).parent
    if (
        boundary == root
        or not normalized_path_within(root, boundary)
        or not normalized_path_within(boundary, target_parent)
    ):
        raise ValueError("refusing managed permission repair outside the selected root")

    relative = target_parent.relative_to(boundary)
    components = (boundary.name, *relative.parts)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    # Fresh fake roots legitimately lack an agent home or runtime prefix. Create that
    # *unmanaged* prefix without chmodding existing directories, then begin normalization at
    # the narrow declared boundary. Descriptor-relative no-follow traversal prevents a
    # missing-prefix race from redirecting creation outside the selected root.
    try:
        prefix_components = boundary.parent.relative_to(root).parts
    except ValueError as exc:
        raise ValueError("managed permission boundary parent escapes the selected root") from exc
    parent_fd = os.open(root, flags)
    prefix_display = root
    changes: list[dict[str, Any]] = []
    try:
        for component in prefix_components:
            if component in {"", ".", ".."} or Path(component).name != component:
                raise ValueError("managed permission prefix contains an unsafe component")
            prefix_display /= component
            prefix_fd: int | None = None
            created = False
            try:
                try:
                    prefix_fd = os.open(component, flags, dir_fd=parent_fd)
                except FileNotFoundError:
                    if not create:
                        os.close(parent_fd)
                        return []
                    os.mkdir(component, mode=0o700, dir_fd=parent_fd)
                    prefix_fd = os.open(component, flags, dir_fd=parent_fd)
                    created = True
                prefix_info = os.fstat(prefix_fd)
                if (
                    not stat.S_ISDIR(prefix_info.st_mode)
                    or int(prefix_info.st_uid) not in {0, os.geteuid()}
                ):
                    raise ValueError(
                        f"managed permission prefix has an unsafe owner or type: {prefix_display}"
                    )
                if created:
                    changes.append(
                        {
                            "path": str(prefix_display),
                            "created_directory": True,
                            "previous_mode": None,
                            "installed_mode": f"{stat.S_IMODE(prefix_info.st_mode):04o}",
                        }
                    )
                os.close(parent_fd)
                parent_fd = prefix_fd
                prefix_fd = None
            finally:
                if prefix_fd is not None:
                    os.close(prefix_fd)
    except Exception:
        os.close(parent_fd)
        raise
    display = boundary.parent
    try:
        for component in components:
            if component in {"", ".", ".."} or Path(component).name != component:
                raise ValueError("managed permission chain contains an unsafe component")
            display /= component
            created = False
            child_fd: int | None = None
            try:
                try:
                    child_fd = os.open(component, flags, dir_fd=parent_fd)
                except FileNotFoundError:
                    if not create:
                        break
                    os.mkdir(component, mode=0o700, dir_fd=parent_fd)
                    child_fd = os.open(component, flags, dir_fd=parent_fd)
                    created = True
                info = os.fstat(child_fd)
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError(f"managed permission path is not a directory: {display}")
                if int(info.st_uid) not in {0, os.geteuid()}:
                    raise ValueError(f"managed permission path has an untrusted owner: {display}")
                previous = stat.S_IMODE(info.st_mode)
                desired = previous & ~0o022
                if created:
                    desired = 0o700
                if desired != previous:
                    os.fchmod(child_fd, desired)
                if created:
                    changes.append(
                        {
                            "path": str(display),
                            "created_directory": True,
                            "previous_mode": None,
                            "installed_mode": f"{desired:04o}",
                        }
                    )
                elif desired != previous:
                    changes.append(
                        {
                            "path": str(display),
                            "previous_mode": f"{previous:04o}",
                            "installed_mode": f"{desired:04o}",
                        }
                    )
                os.close(parent_fd)
                parent_fd = child_fd
                child_fd = None
            finally:
                if child_fd is not None:
                    os.close(child_fd)
    finally:
        os.close(parent_fd)
    return changes


def plan_managed_parent_chain(root: Path, action: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the exact directory creations/chmods an apply would attempt.

    This is intentionally read-only so dry-run output exposes permission
    effects for review.  Apply rechecks this snapshot and fails closed on
    drift.
    """
    if os.name != "posix" or action.get("operation") not in ACTIVE_OPERATIONS:
        return []
    boundary = managed_parent_boundary(root, action)
    if boundary is None:
        return []
    target_parent = Path(str(action["path"])).parent
    if (
        boundary == root
        or not normalized_path_within(root, boundary)
        or not normalized_path_within(boundary, target_parent)
    ):
        raise ValueError("refusing managed permission planning outside the selected root")

    prefix = list(boundary.parent.relative_to(root).parts)
    managed = [boundary.name, *target_parent.relative_to(boundary).parts]
    planned: list[dict[str, Any]] = []
    current = root
    missing = False
    for index, component in enumerate([*prefix, *managed]):
        if component in {"", ".", ".."} or Path(component).name != component:
            raise ValueError("managed permission path contains an unsafe component")
        current /= component
        normalize_mode = index >= len(prefix)
        if missing or not current.exists():
            if current.is_symlink():
                raise ValueError(f"managed permission path is an unsafe dangling link: {current}")
            missing = True
            planned.append(
                {
                    "path": str(current),
                    "created_directory": True,
                    "previous_mode": None,
                    "installed_mode": "0700",
                }
            )
            continue
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"managed permission path is not a real directory: {current}")
        if int(info.st_uid) not in {0, os.geteuid()}:
            raise ValueError(f"managed permission path has an untrusted owner: {current}")
        if normalize_mode:
            previous = stat.S_IMODE(info.st_mode)
            desired = previous & ~0o022
            if desired != previous:
                planned.append(
                    {
                        "path": str(current),
                        "previous_mode": f"{previous:04o}",
                        "installed_mode": f"{desired:04o}",
                    }
                )
    return planned


def _change_key(change: dict[str, Any]) -> tuple[str, bool, str | None, str | None]:
    return (
        str(change.get("path")),
        bool(change.get("created_directory")),
        change.get("previous_mode"),
        change.get("installed_mode"),
    )


def _open_managed_directory(root: Path, path: Path) -> int:
    if not normalized_path_within(root, path):
        raise ValueError(f"managed permission path escapes selected root: {path}")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = os.open(root, flags)
    try:
        for component in path.relative_to(root).parts:
            child = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except Exception:
        os.close(fd)
        raise


def _open_managed_file(root: Path, path: Path) -> int:
    parent_fd = _open_managed_directory(root, path.parent)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        return os.open(path.name, flags, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def restore_managed_modes(root: Path, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Restore prior modes iff the object still has the installed mode."""
    restored: list[dict[str, Any]] = []
    if os.name != "posix":
        return restored
    for change in reversed(changes):
        previous_text = change.get("previous_mode")
        installed_text = change.get("installed_mode")
        if change.get("created_directory") or not previous_text or not installed_text:
            continue
        path = Path(str(change.get("path") or ""))
        try:
            fd = (
                _open_managed_file(root, path)
                if change.get("object_type") == "file"
                else _open_managed_directory(root, path)
            )
        except (OSError, ValueError):
            continue
        try:
            info = os.fstat(fd)
            if int(info.st_uid) not in {0, os.geteuid()}:
                continue
            if change.get("object_type") == "file" and not stat.S_ISREG(info.st_mode):
                continue
            if change.get("object_type") != "file" and not stat.S_ISDIR(info.st_mode):
                continue
            current = stat.S_IMODE(info.st_mode)
            installed = int(installed_text, 8)
            previous = int(previous_text, 8)
            if current != installed:
                continue
            os.fchmod(fd, previous)
            restored.append(dict(change))
        finally:
            os.close(fd)
    return restored


def normalize_managed_parent_chain(
    root: Path,
    action: dict[str, Any],
    *,
    create: bool,
    expected: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Apply reviewed permission changes and roll them back on any drift."""
    if expected is not None:
        live = plan_managed_parent_chain(root, action)
        expected_keys = {_change_key(item) for item in expected}
        live_keys = {_change_key(item) for item in live}
        # A preceding action in the same plan may already have installed a
        # shared parent mode.  Everything else is concurrent drift.
        missing = expected_keys - live_keys
        extra = live_keys - expected_keys
        for item in list(missing):
            path_text, _created, _previous, installed_text = item
            path = Path(path_text)
            try:
                info = path.lstat()
                satisfied = (
                    stat.S_ISDIR(info.st_mode)
                    and not stat.S_ISLNK(info.st_mode)
                    and f"{stat.S_IMODE(info.st_mode):04o}" == installed_text
                )
            except OSError:
                satisfied = False
            if satisfied:
                missing.remove(item)
        if missing or extra:
            raise ValueError("managed parent permissions changed after planning")
    changes = _normalize_managed_parent_chain(root, action, create=create)
    if expected is None:
        return changes
    allowed = {_change_key(item) for item in expected}
    unexpected = [item for item in changes if _change_key(item) not in allowed]
    if unexpected:
        restore_managed_modes(root, changes)
        raise ValueError("managed parent permissions changed during apply")
    return changes
