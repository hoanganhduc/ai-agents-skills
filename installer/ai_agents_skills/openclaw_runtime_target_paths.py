"""Neutral runtime-root validator for OpenClaw runtime delivery (issue 8).

The OpenClaw runtime/support surfaces must place executable runtime under a root
that is OUTSIDE the OpenClaw home and outside any synced, version-controlled,
agent-owned, or world-writable surface. This module enforces the reject rules the
design doc previously described only in prose (R1-R13), realpath-first so a
symlinked parent cannot smuggle the root under a forbidden surface.

The validator is intentionally fail-closed: any condition it cannot evaluate
returns a reject reason rather than silently allowing the root.
"""

from __future__ import annotations

import os
from pathlib import Path

from .manifest import REPO_ROOT

# Agent-home dot-directories a runtime root must never resolve under.
AGENT_HOME_DIRS = (".openclaw", ".codex", ".claude", ".deepseek", ".copilot", ".gemini", ".grok", ".kimi-code")
# Loader / config / state component names that are never valid inside a runtime root.
LOADER_STATE_NAMES = ("hooks", "plugins", "bin", "commands", "config", "qmd", ".ai-agents-skills")
# Markers identifying a Syncthing-synced folder (R3) or a git repo (R4) ancestor.
SYNC_MARKERS = (".stfolder", ".stignore")
GIT_MARKERS = (".git",)
# The only path shape eligible for the narrow R2 workspace exception (inert S3 data).
WORKSPACE_IGNORED_RUNTIME_SUFFIX = (".local", "share", "ai-agents-skills", "runtime")


def _ancestors(path: Path):
    yield path
    yield from path.parents


def _has_marker_ancestor(path: Path, markers: tuple[str, ...]) -> str | None:
    for anc in _ancestors(path):
        for marker in markers:
            if (anc / marker).exists():
                return f"{anc}/{marker}"
    return None


def _nearest_existing(path: Path) -> Path | None:
    for anc in _ancestors(path):
        if anc.exists():
            return anc
    return None


def _has_symlinked_parent(path: Path) -> bool:
    for anc in _ancestors(path):
        if anc.is_symlink():
            return True
    return False


def _is_workspace_ignored_runtime(real: Path, openclaw_home: Path) -> bool:
    """True only for exactly <workspace>/.local/share/ai-agents-skills/runtime[/...]
    under an OpenClaw home — the single shape the R2 carve-out may clear for inert S3."""
    try:
        rel = real.relative_to(openclaw_home)
    except ValueError:
        return False
    parts = rel.parts
    # workspace[...]/.local/share/ai-agents-skills/runtime[/...]
    if "workspace" not in parts:
        return False
    idx = parts.index("workspace")
    tail = parts[idx + 1 :]
    return tail[: len(WORKSPACE_IGNORED_RUNTIME_SUFFIX)] == WORKSPACE_IGNORED_RUNTIME_SUFFIX


def neutral_runtime_root_block_reason(
    runtime_root: Path,
    *,
    allow_workspace_ignored_exception: bool = False,
) -> str | None:
    """Return a reject reason for an unsuitable neutral runtime root, else None.

    realpath-canonicalizes first (R1). ``allow_workspace_ignored_exception`` enables
    the narrow R2 carve-out for inert S3 data under an ignored ``/workspace/.local``
    prefix; the caller must separately prove .stignore/.gitignore coverage and supply
    an approval token before relying on it.
    """
    raw = Path(runtime_root).expanduser()
    if not raw.is_absolute():
        return "runtime root must be an absolute path (R13)"
    real = raw.resolve(strict=False)

    workspace_exception_ok = False
    for anc in _ancestors(real):
        name = anc.name
        if name == ".openclaw":
            if allow_workspace_ignored_exception and _is_workspace_ignored_runtime(real, anc):
                workspace_exception_ok = True
                break
            return f"runtime root is under an OpenClaw home: {anc} (R2)"
        if name in AGENT_HOME_DIRS:
            return f"runtime root is under an agent home: {anc} (R6)"

    # R8: under the repository checkout / runtime source.
    try:
        real.relative_to(Path(REPO_ROOT).resolve(strict=False))
        return f"runtime root is under the repository checkout: {REPO_ROOT} (R8)"
    except ValueError:
        pass

    # R3/R4: synced or git surfaces (skipped only under the proven workspace exception).
    if not workspace_exception_ok:
        synced = _has_marker_ancestor(real, SYNC_MARKERS)
        if synced:
            return f"runtime root is under a Syncthing-synced folder: {synced} (R3)"
        gitrepo = _has_marker_ancestor(real, GIT_MARKERS)
        if gitrepo:
            return f"runtime root is under a git repository: {gitrepo} (R4)"

    # R7: a loader/state component name appears anywhere in the path.
    for anc in _ancestors(real):
        if anc.name in LOADER_STATE_NAMES:
            return f"runtime root contains a loader/state component: {anc.name} (R7)"

    # R1/R9/R10: symlinked parent, ownership, permissions (POSIX).
    if _has_symlinked_parent(real):
        return "runtime root has a symlinked parent (R1)"
    existing = _nearest_existing(real)
    if existing is not None and os.name == "posix":
        st = existing.stat()
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            return "runtime root is not owned by the current user (R9)"
        if st.st_mode & 0o022:
            return "runtime root (or nearest existing ancestor) is group/world-writable (R10)"
    return None
