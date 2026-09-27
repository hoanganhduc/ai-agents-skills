#!/usr/bin/env python3
"""Prepare a local archive with the trusted canonical runtime; never publish."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(os.environ["AAS_REPO"])
script = root / "canonical/runtime/skills/zenodo-artifact/zenodo_artifact.py"
raise SystemExit(subprocess.call([sys.executable, "-B", str(script), "prepare", *sys.argv[1:]]))
