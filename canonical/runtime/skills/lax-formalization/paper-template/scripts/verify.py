#!/usr/bin/env python3
"""Local authoring convenience; CI uses the separately pinned runtime copy."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(os.environ["AAS_REPO"])
script = root / "canonical/runtime/skills/lax-formalization/ci_verify.py"
raise SystemExit(subprocess.call([sys.executable, "-B", str(script), *sys.argv[1:]]))
