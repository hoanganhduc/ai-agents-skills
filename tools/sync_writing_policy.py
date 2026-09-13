#!/usr/bin/env python3
"""Keep each writing-style index in step with its instruction document.

The instruction documents under ``canonical/instructions`` are the source of
truth for the writing policy. Each sidecar index records a ``content_hash`` of
the document it describes. Digests normalize text newlines to LF so the result
is reproducible across Git checkouts on Windows and POSIX systems.

Usage:

    python tools/sync_writing_policy.py --check   # report drift, exit 1 if any
    python tools/sync_writing_policy.py --write   # update the hashes in place
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from types import MappingProxyType

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTRUCTIONS_ROOT = REPO_ROOT / "canonical" / "instructions"

MAX_POLICY_BYTES = 8 * 1024 * 1024
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400)

# The mapping is intentionally exact. An index may hash only the document it
# was created to describe, never an arbitrary path supplied by the index JSON.
INDEX_SPECS = MappingProxyType({
    "writing-style-settings.index.json": (
        "policy_ref",
        "canonical/instructions/writing-style-settings.md",
    ),
    "math-manuscript-style.index.json": (
        "overlay_ref",
        "canonical/instructions/math-manuscript-style.md",
    ),
    "graph-combinatorics-style.index.json": (
        "overlay_ref",
        "canonical/instructions/graph-combinatorics-style.md",
    ),
    "mathscinet-zbmath-review-style.index.json": (
        "overlay_ref",
        "canonical/instructions/mathscinet-zbmath-review-style.md",
    ),
})


def dumped(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def compact_scalar_arrays(text: str) -> str:
    """Collapse arrays of plain strings back onto one line.

    ``json.dumps`` expands every array, which turns a one-line list of ids into
    a block and makes an otherwise small diff unreadable. The sidecars were
    written with those arrays inline, so restore that shape.
    """
    pattern = re.compile(r'\[\n(?P<items>(?:\s+"(?:\\.|[^"\\])*",?\n)+)\s*\]')
    return pattern.sub(
        lambda match: json.dumps(
            json.loads(match.group(0)), ensure_ascii=False, separators=(", ", ": ")
        ),
        text,
    )


def _is_link_like(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & REPARSE_POINT
    )


def _file_state(info: os.stat_result) -> tuple[int, ...]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
        int(info.st_nlink),
    )


def _validate_components(path: Path, *, directory: bool = False) -> os.stat_result:
    try:
        relative = path.relative_to(REPO_ROOT)
    except ValueError:
        raise SystemExit(f"writing policy path escapes the repository: {path}") from None

    cursor = REPO_ROOT
    final: os.stat_result | None = None
    for position, part in enumerate(relative.parts):
        cursor /= part
        try:
            info = os.lstat(cursor)
        except OSError as exc:
            raise SystemExit(f"writing policy path is missing: {cursor}") from exc
        if _is_link_like(info):
            raise SystemExit(f"writing policy path contains a link or reparse point: {cursor}")
        is_final = position == len(relative.parts) - 1
        if not is_final or directory:
            if not stat.S_ISDIR(info.st_mode):
                raise SystemExit(f"writing policy path component is not a directory: {cursor}")
        elif not stat.S_ISREG(info.st_mode) or int(info.st_nlink) != 1:
            raise SystemExit(f"writing policy file must be a single-link regular file: {cursor}")
        final = info
    if final is None:
        raise SystemExit(f"writing policy path does not name a file: {path}")
    return final


def _read_regular_bytes(path: Path) -> tuple[bytes, os.stat_result]:
    before = _validate_components(path)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SystemExit(f"writing policy file cannot be opened safely: {path}") from exc
    try:
        opened = os.fstat(descriptor)
        if _is_link_like(opened) or not stat.S_ISREG(opened.st_mode):
            raise SystemExit(f"writing policy file is not a regular file: {path}")
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise SystemExit(f"writing policy file changed while it was opened: {path}")
        if int(opened.st_size) < 0 or int(opened.st_size) > MAX_POLICY_BYTES:
            raise SystemExit(f"writing policy file exceeds {MAX_POLICY_BYTES} bytes: {path}")

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_POLICY_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_POLICY_BYTES:
                raise SystemExit(f"writing policy file exceeds {MAX_POLICY_BYTES} bytes: {path}")

        after = os.fstat(descriptor)
        if _file_state(opened) != _file_state(after) or total != int(after.st_size):
            raise SystemExit(f"writing policy file changed while it was read: {path}")
        current = _validate_components(path)
        if _file_state(after) != _file_state(current):
            raise SystemExit(f"writing policy file changed after it was read: {path}")
        return b"".join(chunks), current
    finally:
        os.close(descriptor)


def referenced_document(relative: str) -> Path:
    allowed = {document for _key, document in INDEX_SPECS.values()}
    if not isinstance(relative, str) or relative not in allowed or ":" in relative:
        raise SystemExit(f"index names an unexpected instruction document: {relative!r}")
    path = REPO_ROOT.joinpath(*relative.split("/"))
    _validate_components(path)
    return path


def document_digest(relative: str) -> str:
    path = referenced_document(relative)
    data, _state = _read_regular_bytes(path)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SystemExit(f"instruction document is not UTF-8: {relative}") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _load_index(path: Path) -> tuple[dict[str, object], os.stat_result]:
    data, state = _read_regular_bytes(path)
    try:
        index = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"invalid writing policy index {path.name}: {exc}") from exc
    if not isinstance(index, dict):
        raise SystemExit(f"writing policy index must contain a JSON object: {path.name}")
    return index, state


def reference_of(index: dict[str, object], index_name: str) -> str:
    expected_key, expected_reference = INDEX_SPECS[index_name]
    present = {key for key in ("policy_ref", "overlay_ref") if key in index}
    if present != {expected_key} or index.get(expected_key) != expected_reference:
        raise SystemExit(
            f"{index_name} must name {expected_reference} with {expected_key}"
        )
    return expected_reference


def _write_index_atomic(
    path: Path,
    content: bytes,
    expected_state: os.stat_result,
) -> None:
    parent_state = _validate_components(path.parent, directory=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(temporary, stat.S_IMODE(expected_state.st_mode))

        current_parent = _validate_components(path.parent, directory=True)
        if (parent_state.st_dev, parent_state.st_ino) != (
            current_parent.st_dev,
            current_parent.st_ino,
        ):
            raise SystemExit(f"writing policy directory changed before replace: {path.parent}")
        current = _validate_components(path)
        if _file_state(current) != _file_state(expected_state):
            raise SystemExit(f"writing policy index changed before replace: {path.name}")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="update the recorded hashes")
    mode.add_argument("--check", action="store_true", help="report drift without writing")
    args = parser.parse_args(argv)

    drifted: list[str] = []
    for name in INDEX_SPECS:
        path = INSTRUCTIONS_ROOT / name
        index, index_state = _load_index(path)
        expected = document_digest(reference_of(index, name))
        if index.get("content_hash") == expected:
            continue
        drifted.append(name)
        if args.write:
            index["content_hash"] = expected
            content = compact_scalar_arrays(dumped(index)).encode("utf-8")
            _write_index_atomic(path, content, index_state)

    if not drifted:
        print("writing policy hashes: ok")
        return 0
    if args.write:
        print("writing policy hashes updated:")
    else:
        print("writing policy hash drift:")
    for name in drifted:
        print(f"- {name}")
    return 0 if args.write else 1


if __name__ == "__main__":
    sys.exit(main())
