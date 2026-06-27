"""Output-path allocation and the ``result.json`` sidecar writer.

Captures land under ``AAS_RUNS_ROOT`` (falling back to a temp dir) at
``url-to-screenshot/<run_id>/<host>_<ts>.png`` with a minimal ``result.json``
sidecar carrying only non-sensitive metadata (redacted URL host, dimensions,
blank verdict, flags). Pure stdlib.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def runs_root() -> Path:
    env = os.environ.get("AAS_RUNS_ROOT")
    if env:
        return Path(env)
    return Path(tempfile.gettempdir()) / "ai-agents-skills-runs"


def _slug(text: str) -> str:
    cleaned = _SAFE.sub("-", text).strip("-")
    return cleaned[:64] or "page"


def allocate_output(host: str, *, run_id: str | None = None, ts: float | None = None) -> Path:
    """Allocate a deterministic-shaped output path for a capture of ``host``."""
    run_id = run_id or time.strftime("%Y%m%dT%H%M%S", time.gmtime(ts))
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(ts))
    out_dir = runs_root() / "url-to-screenshot" / _slug(run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{_slug(host)}_{stamp}.png"


def write_result_sidecar(png_path: Path, payload: dict) -> Path:
    """Write the ``result.json`` sidecar next to the PNG; returns its path."""
    sidecar = png_path.with_suffix(".result.json")
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return sidecar
