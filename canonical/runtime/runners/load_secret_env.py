#!/usr/bin/env python3
"""Strict child-scope KEY=value secret projection for managed AAS launchers."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Mapping


MAX_SECRET_FILE_BYTES = 65_536
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
PROTECTED_SECRET_IDENTITIES_ENV = "AAS_PROTECTED_SECRET_FILE_IDS"
MINIMAL_CHILD_ENV_KEYS = frozenset(
    {
        # OS/runtime identity needed by standard-library and user-scoped config.
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        # Managed runtime metadata. Values are set or validated by run_skill.
        "AAS_RUNTIME_ROOT",
        "AAS_RUNTIME_WORKSPACE",
        "OPENCLAW_WORKSPACE",
        "AAS_RUNTIME_PYTHON",
        "AAS_RUNTIME_COMMAND_FD",
        "AAS_RUNTIME_COMMAND_PATH",
        "AAS_RUNTIME_REQUIRE_TRUSTED",
        "AAS_RUNTIME_PYTHON_ISOLATED",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUTF8",
        "PYTHONIOENCODING",
        PROTECTED_SECRET_IDENTITIES_ENV,
    }
)
FIXED_CHILD_PATH = "/usr/bin:/bin"


class SecretEnvError(ValueError):
    """A protected secret environment file failed closed."""


def _stability_tuple(info: os.stat_result) -> tuple[int, int, int, int, int]:
    """Metadata that changes for in-place writes even when mtime is restored."""

    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
    )


def parse_secret_env_text(
    text: str,
    *,
    allowed_keys: frozenset[str],
    source: str = "<secret-env>",
) -> dict[str, str]:
    """Parse strict comments and KEY=value records without evaluation."""

    values: dict[str, str] = {}
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if raw != raw.strip():
            raise SecretEnvError(
                f"{source}:{line_number}: assignment has surrounding whitespace"
            )
        if "=" not in raw:
            raise SecretEnvError(f"{source}:{line_number}: missing '='")
        key, value = raw.split("=", 1)
        if not KEY_RE.fullmatch(key):
            raise SecretEnvError(f"{source}:{line_number}: invalid key {key!r}")
        if key not in allowed_keys:
            raise SecretEnvError(f"{source}:{line_number}: unsupported key {key}")
        if key in values:
            raise SecretEnvError(f"{source}:{line_number}: duplicate key {key}")
        if not value or value != value.strip():
            raise SecretEnvError(f"{source}:{line_number}: empty or padded value for {key}")
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
            raise SecretEnvError(f"{source}:{line_number}: control character in value for {key}")
        values[key] = value
    return values


def parse_secret_json_text(
    text: str,
    *,
    allowed_keys: frozenset[str],
    source: str = "<secret-json>",
) -> dict[str, str]:
    """Parse one flat, exact-key JSON projection without duplicate keys."""
    try:
        pairs = json.loads(text, object_pairs_hook=lambda value: value)
    except json.JSONDecodeError as exc:
        raise SecretEnvError(f"{source}: invalid JSON") from exc
    if not isinstance(pairs, list):
        raise SecretEnvError(f"{source}: expected one JSON object")
    values: dict[str, str] = {}
    for item in pairs:
        if not isinstance(item, tuple) or len(item) != 2:
            raise SecretEnvError(f"{source}: nested values are not allowed")
        key, value = item
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            raise SecretEnvError(f"{source}: invalid key")
        if key not in allowed_keys:
            raise SecretEnvError(f"{source}: unsupported key {key}")
        if key in values:
            raise SecretEnvError(f"{source}: duplicate key {key}")
        if not isinstance(value, str) or not value or value != value.strip():
            raise SecretEnvError(f"{source}: {key} must be a non-empty string")
        if "\x00" in value:
            raise SecretEnvError(f"{source}: NUL is not allowed in {key}")
        values[key] = value
    return values


def _open_directory_nofollow(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(absolute.anchor or os.sep, flags)
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def read_protected_secret_env(
    path_value: str,
    *,
    identity_out: list[str] | None = None,
) -> str:
    """Read one absolute, bounded, single-link secret file without link traversal."""

    supplied = Path(path_value)
    if not supplied.is_absolute():
        raise SecretEnvError("secret env pointer must name an absolute path")
    if os.name == "nt":  # pragma: no cover - native Windows uses load_secret_env.ps1
        raise SecretEnvError(
            "native Windows secret loading requires the managed PowerShell authority engine"
        )
    absolute = Path(os.path.abspath(supplied))
    parent_descriptor: int | None = None
    try:
        parent_descriptor = _open_directory_nofollow(absolute.parent)
        path_info = os.stat(
            absolute.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(path_info.st_mode)
            or int(getattr(path_info, "st_nlink", 1)) != 1
            or int(path_info.st_size) > MAX_SECRET_FILE_BYTES
        ):
            raise OSError("secret env path is not a bounded single-link regular file")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_BINARY", 0)
        )
        file_descriptor = os.open(
            absolute.name,
            flags,
            dir_fd=parent_descriptor,
        )
        try:
            before = os.fstat(file_descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or int(getattr(before, "st_nlink", 1)) != 1
                or int(before.st_size) > MAX_SECRET_FILE_BYTES
            ):
                raise OSError("secret env file is not a bounded single-link regular file")
            if os.name == "posix" and (
                int(before.st_uid) != int(os.geteuid())
                or stat.S_IMODE(before.st_mode) & 0o077
            ):
                raise OSError("secret env file is not owner-private")
            if (int(path_info.st_dev), int(path_info.st_ino)) != (
                int(before.st_dev),
                int(before.st_ino),
            ):
                raise OSError("secret env path changed while opening")
            chunks: list[bytes] = []
            remaining = MAX_SECRET_FILE_BYTES + 1
            while remaining > 0:
                chunk = os.read(file_descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            after = os.fstat(file_descriptor)
            if len(payload) > MAX_SECRET_FILE_BYTES:
                raise OSError("secret env file is oversized")
            if _stability_tuple(before) != _stability_tuple(after):
                raise OSError("secret env file changed while reading")
            protected_identity = f"{int(after.st_dev)}:{int(after.st_ino)}"
        finally:
            os.close(file_descriptor)
    except OSError as exc:
        raise SecretEnvError(f"could not securely load secret env file: {exc}") from exc
    finally:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretEnvError("secret env file must be UTF-8") from exc
    if identity_out is not None:
        identity_out.append(protected_identity)
    return text


def load_pointer_secret_env(
    pointer_env: str,
    *,
    allowed_keys: frozenset[str],
    environ: Mapping[str, str] | None = None,
    identity_out: list[str] | None = None,
    file_format: str = "env",
) -> dict[str, str]:
    source = os.environ if environ is None else environ
    path_value = str(source.get(pointer_env) or "")
    if not path_value:
        return {}
    if path_value != path_value.strip():
        raise SecretEnvError(f"{pointer_env} has surrounding whitespace")
    text = read_protected_secret_env(path_value, identity_out=identity_out)
    if file_format == "json":
        return parse_secret_json_text(text, allowed_keys=allowed_keys)
    return parse_secret_env_text(text, allowed_keys=allowed_keys)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pointer-env", required=True)
    parser.add_argument("--format", choices=("env", "json"), default="env", dest="file_format")
    parser.add_argument("--allow-key", action="append", default=[], dest="allowed_keys")
    parser.add_argument(
        "--no-load",
        action="store_true",
        help="scrub and launch without reading the pointer (child owns a structured authority)",
    )
    parser.add_argument(
        "--export-key",
        action="append",
        default=[],
        dest="export_keys",
        help=(
            "export only this allowed key to the child; repeat as needed "
            "(the default exports every allowed key)"
        ),
    )
    parser.add_argument(
        "--scrub-key",
        action="append",
        default=[],
        dest="scrub_keys",
        help="remove this ambient variable from the child whether or not it is loaded",
    )
    parser.add_argument(
        "--retain-env",
        action="append",
        default=[],
        dest="retain_env",
        help=(
            "retain one trusted command-specific runtime metadata variable; "
            "repeat as needed"
        ),
    )
    parser.add_argument(
        "--retain-pointer",
        action="store_true",
        help="retain the validated pointer path in the child environment",
    )
    parser.add_argument(
        "--export-subset",
        action="store_true",
        help=(
            "activate child-scope subset projection even when no export key is "
            "selected"
        ),
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("secret env loader requires a child command", file=sys.stderr)
        return 2
    allowed_keys = frozenset(str(key) for key in args.allowed_keys)
    export_keys = frozenset(str(key) for key in args.export_keys)
    retain_env = frozenset(str(key) for key in args.retain_env)
    if export_keys and not export_keys.issubset(allowed_keys):
        print("secret env export keys must be a subset of allowed keys", file=sys.stderr)
        return 2
    if any(not KEY_RE.fullmatch(key) for key in retain_env):
        print("secret env retained metadata keys are invalid", file=sys.stderr)
        return 2
    try:
        protected_identities: list[str] = []
        loaded = (
            {}
            if args.no_load
            else load_pointer_secret_env(
                str(args.pointer_env),
                allowed_keys=allowed_keys,
                identity_out=protected_identities,
                file_format=str(args.file_format),
            )
        )
    except SecretEnvError as exc:
        print(f"secret env load failed: {exc}", file=sys.stderr)
        return 2
    scrub_keys = frozenset(str(key) for key in args.scrub_keys)
    retained_names = (MINIMAL_CHILD_ENV_KEYS | retain_env) - scrub_keys
    child_env = {
        key: str(os.environ[key])
        for key in retained_names
        if os.environ.get(key)
    }
    child_env["PATH"] = FIXED_CHILD_PATH
    subset_mode = bool(args.export_subset or export_keys)
    selected_keys = export_keys if subset_mode else allowed_keys
    for key in allowed_keys:
        child_env.pop(key, None)
    pointer_name = str(args.pointer_env)
    pointer_value = str(os.environ.get(pointer_name) or "")
    child_env.pop(pointer_name, None)
    if args.retain_pointer and pointer_value:
        child_env[pointer_name] = pointer_value
    if subset_mode:
        existing_identities = {
            value
            for value in str(child_env.get(PROTECTED_SECRET_IDENTITIES_ENV) or "").split(",")
            if re.fullmatch(r"[0-9]+:[0-9]+", value)
        }
        existing_identities.update(protected_identities)
        if existing_identities:
            child_env[PROTECTED_SECRET_IDENTITIES_ENV] = ",".join(
                sorted(existing_identities)
            )
    child_env.update(
        {key: value for key, value in loaded.items() if key in selected_keys}
    )
    try:
        os.execvpe(command[0], command, child_env)
    except OSError as exc:
        print(f"secret env child launch failed: {exc}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SecretEnvError",
    "load_pointer_secret_env",
    "parse_secret_env_text",
    "parse_secret_json_text",
    "read_protected_secret_env",
]
