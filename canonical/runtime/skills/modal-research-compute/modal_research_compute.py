#!/usr/bin/env python3
"""Entrypoint for the Codex Modal research-compute runtime skill."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    skill_dir = Path(__file__).resolve().parent
    workspace_root = skill_dir.parent.parent
    if str(workspace_root) not in sys.path:
        sys.path.insert(0, str(workspace_root))

    from research_compute.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
