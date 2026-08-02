#!/usr/bin/env python3
"""Strict KEY=VALUE env loader for force-loop (no shell source).

Parses UTF-8 (CRLF-tolerant) files of the form:
  KEY=VALUE
  # comments and blank lines allowed
  export KEY=VALUE   # optional leading export stripped

Rejects:
  - shell metacharacters that imply expansion/injection ($ ` ; | & < > ( ) { } \\)
  - multi-line values
  - keys that are not [A-Za-z_][A-Za-z0-9_]*
  - empty keys
  - assignment without =

Does not expand variables, run subshells, or evaluate quotes beyond optional
matching single/double quotes around the entire value.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Characters that must not appear unquoted in values for this strict loader.
_UNSAFE_VALUE = re.compile(r"[`$|;&<>(){}\\]")


class EnvLoadError(ValueError):
    """Raised when a line or file is not a safe KEY=VALUE assignment."""


def _strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        inner = value[1:-1]
        if value[0] in inner:
            raise EnvLoadError("nested quotes are not allowed")
        return inner
    return value


def parse_env_text(text: str, *, source: str = "<memory>") -> dict[str, str]:
    """Parse KEY=VALUE text into a dict. Raises EnvLoadError on bad lines."""
    out: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise EnvLoadError(f"{source}:{lineno}: missing '='")
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key or not _KEY_RE.match(key):
            raise EnvLoadError(f"{source}:{lineno}: invalid key {key!r}")
        if "\n" in value or "\r" in value:
            raise EnvLoadError(f"{source}:{lineno}: multi-line values forbidden")
        value = _strip_optional_quotes(value)
        if _UNSAFE_VALUE.search(value):
            raise EnvLoadError(
                f"{source}:{lineno}: value for {key} contains unsafe characters"
            )
        out[key] = value
    return out


def load_env_file(path: Path | str) -> dict[str, str]:
    """Load one KEY=VALUE file (UTF-8). Missing file → empty dict."""
    p = Path(path)
    if not p.is_file():
        return {}
    text = p.read_text(encoding="utf-8")
    # Normalize Windows newlines without changing semantics.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return parse_env_text(text, source=str(p))


def merge_env_files(paths: list[Path | str]) -> dict[str, str]:
    """Load files in order; later files override earlier keys."""
    merged: dict[str, str] = {}
    for path in paths:
        merged.update(load_env_file(path))
    return merged


def apply_to_environ(
    mapping: Mapping[str, str],
    environ: dict[str, str] | None = None,
    *,
    override: bool = True,
) -> dict[str, str]:
    """Copy mapping into environ (default: os.environ copy pattern).

    Returns the environ dict for convenience. Does not print values.
    """
    import os

    env = environ if environ is not None else os.environ  # type: ignore[assignment]
    for key, value in mapping.items():
        if not override and key in env:
            continue
        env[key] = value
    return env  # type: ignore[return-value]


__all__ = [
    "EnvLoadError",
    "apply_to_environ",
    "load_env_file",
    "merge_env_files",
    "parse_env_text",
]
