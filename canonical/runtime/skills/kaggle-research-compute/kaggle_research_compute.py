#!/usr/bin/env python3
"""Entrypoint for the Kaggle research-compute runtime skill.

Thin wrapper (mirrors the Modal / Hetzner lanes): it puts the broker workspace on sys.path so
the driver can import research_compute, puts the skill directory on sys.path so the driver
module is importable, and forwards to the Kaggle kernel-lifecycle driver.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    skill_dir = Path(__file__).resolve().parent
    workspace_root = skill_dir.parent.parent
    for entry in (str(workspace_root), str(skill_dir)):
        if entry not in sys.path:
            sys.path.insert(0, entry)

    import kaggle_driver

    return kaggle_driver.main()


if __name__ == "__main__":
    raise SystemExit(main())
