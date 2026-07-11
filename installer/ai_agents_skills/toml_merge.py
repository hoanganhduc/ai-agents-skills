"""Idempotent merge of a managed block into a TOML config file.

TOML has no structured managed-marker convention like the ``_managedBy``/``_id``
tags json_merge.py uses for JSON settings, so a managed region is delimited by a
pair of TOML comment markers, ``# ai-agents-skills:<id>:start`` and
``# ai-agents-skills:<id>:end``. The markers make the block idempotently
upsertable and removable without moving or modifying any user-authored TOML
outside the markers. A merge-then-remove round trip restores a file that the
merge created to empty (so the caller can delete it) and restores a pre-existing
file to its original user content.
"""

from __future__ import annotations

from pathlib import Path

MANAGED_BY = "ai-agents-skills"


def load_toml_text(path: Path) -> tuple[str, bool]:
    """Read the TOML config as ``(text, existed)``; a missing file is empty."""
    if not path.exists():
        return "", False
    return path.read_text(encoding="utf-8"), True


def block_markers(managed_id: str) -> tuple[str, str]:
    return f"# {MANAGED_BY}:{managed_id}:start", f"# {MANAGED_BY}:{managed_id}:end"


def managed_block_issue(text: str, managed_id: str) -> str | None:
    start_marker, end_marker = block_markers(managed_id)
    start_count = text.count(start_marker)
    end_count = text.count(end_marker)
    if start_count == 0 and end_count == 0:
        return "missing"
    if start_count != 1 or end_count != 1:
        return "malformed-or-duplicated"
    if text.find(start_marker) > text.find(end_marker):
        return "malformed-or-duplicated"
    return None


def managed_block_span(text: str, managed_id: str) -> tuple[int, int] | None:
    if managed_block_issue(text, managed_id) is not None:
        return None
    start_marker, end_marker = block_markers(managed_id)
    start = text.find(start_marker)
    end = text.find(end_marker, start)
    return start, end + len(end_marker)


def has_unmanaged_table(text: str, managed_id: str, table: str) -> bool:
    """Report whether ``text`` declares a ``[table]`` header outside the block.

    The managed block owns its own ``[table]`` header, so a blind append would
    emit a second copy whenever the user already authored that table. Strict
    TOML parsers reject a table declared twice, so the caller must detect this
    collision and skip rather than corrupt the file. Whitespace inside the
    header is ignored; array-of-tables (``[[...]]``) headers never collide.
    """
    span = managed_block_span(text, managed_id)
    if span is None:
        region = text
    else:
        start, end = span
        region = text[:start] + text[end:]
    target = f"[{table}]".replace(" ", "")
    for raw in region.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("[[") or not line.startswith("["):
            continue
        if line.replace(" ", "") == target:
            return True
    return False


def render_managed_block(managed_id: str, body: str) -> str:
    start_marker, end_marker = block_markers(managed_id)
    return f"{start_marker}\n{body.strip(chr(10))}\n{end_marker}"


def merge_managed_block(text: str, managed_id: str, body: str) -> tuple[str, bool]:
    """Idempotently upsert the managed block. Returns ``(merged, changed)``.

    An existing well-formed block is replaced in place; a missing block is
    appended after the user content. User-authored TOML outside the markers is
    never moved or modified.
    """
    block = render_managed_block(managed_id, body)
    span = managed_block_span(text, managed_id)
    if span is not None:
        start, end = span
        if text[start:end] == block:
            return text, False
        return text[:start] + block + text[end:], True
    if not text:
        return block + "\n", True
    prefix = text if text.endswith("\n") else text + "\n"
    return prefix + "\n" + block + "\n", True


def remove_managed_block(text: str, managed_id: str) -> tuple[str, bool]:
    """Remove the managed block, leaving all user-authored TOML intact.

    Returns ``(merged, changed)``. A merge-then-remove round trip restores a
    file the merge created to empty and a pre-existing file to its original
    content.
    """
    span = managed_block_span(text, managed_id)
    if span is None:
        return text, False
    start, end = span
    if end < len(text) and text[end] == "\n":
        end += 1
    # Consume the single blank-line separator that merge inserted before an
    # appended block so the removal is an exact inverse of the append.
    if start >= 2 and text[start - 2:start] == "\n\n":
        start -= 1
    return text[:start] + text[end:], True
