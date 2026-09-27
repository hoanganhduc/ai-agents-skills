#!/usr/bin/env python3
"""Explicit local executor provisioning; never called by doctor/search/verify."""
from __future__ import annotations
import argparse
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import tempfile

from lax_formalization import ENVIRONMENT, VERSION, tree_inventory
from lax_executor import HERE, phase, write_json, trusted_inputs


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    executable = shutil.which("lax")
    if not executable: raise SystemExit("install the qualified lax-archive CLI first")
    package = Path(executable).resolve().parents[1]
    if json.loads((package / "package.json").read_text(encoding="utf-8"))["version"] != VERSION:
        raise SystemExit("unsupported Lax CLI version")
    cache = Path.home() / ".cache/lax-formalization"
    config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "lax-formalization/config.json"
    cfg = {"schema_version": "lax-executor.v1", "package_root": str(package),
        "package_sha256": tree_inventory(package, exclude_generated=False, trusted_package_links=True)["sha256"],
        "elan_home": str(Path(os.environ.get("ELAN_HOME", str(Path.home() / ".elan")))),
        "warm_root": str(Path(os.environ.get("LAX_HOME", str(Path.home() / ".lax"))) / "warm"),
        "tools_root": str(cache / "tools")}
    # Official Node image manifests inspected 2026-09-27. These are native
    # platform images, not the AMD64-only image in the upstream hosted runner.
    bases = {"aarch64": "a0ddbc73510e98f5e824fd64266ffe1c2c343ba9cf260d95ca2985ad632a3f3e",
             "arm64": "a0ddbc73510e98f5e824fd64266ffe1c2c343ba9cf260d95ca2985ad632a3f3e",
             "x86_64": "25330af3531fb5e23318554a0aa911125b6e91b1b777edf7655501d207c067a2",
             "AMD64": "25330af3531fb5e23318554a0aa911125b6e91b1b777edf7655501d207c067a2"}
    if platform.machine() not in bases: raise SystemExit("executor architecture not qualified")
    cfg["base_image"] = "node:22-bookworm-slim@sha256:" + bases[platform.machine()]
    if not args.apply:
        print(json.dumps({"status": "dry-run", "will_build_local_image": True, "will_build_trusted_inspector": True,
                          "config": str(config), "publication_enabled": False}, indent=2)); return 0
    if not (Path(cfg["warm_root"]) / "v4.33.0-db584cd6d46c/.lax-warm-ok").is_file():
        raise SystemExit("run lax doctor to prepare the supported mathlib environment first")
    cache.mkdir(parents=True, exist_ok=True); cache.chmod(0o700)
    Path(cfg["tools_root"]).mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="provision-", dir=cache) as tmp:
        job = Path(tmp); (job / "control").mkdir()
        subprocess.run(["docker", "build", "--build-arg", "BASE_IMAGE=" + cfg["base_image"], "--iidfile", str(job / "image.id"), "-f", str(HERE / "Executor.Dockerfile"), str(HERE)], check=True)
        cfg["image"] = (job / "image.id").read_text(encoding="utf-8").strip()
        write_json(job / "control/spec.json", {"environment": ENVIRONMENT})
        phase(cfg, "prepare", job, [], timeout=1200)
        prepared = json.loads((job / "result-prepare/prepared.json").read_text(encoding="utf-8"))
        cfg["inspector_relative"] = prepared["inspector"].removeprefix("/lax-home/tools/")
    cfg["trusted_inputs"] = trusted_inputs(cfg)
    config.parent.mkdir(parents=True, exist_ok=True)
    staging = config.with_suffix(".tmp")
    if staging.exists(): raise SystemExit("unexpected existing config staging file")
    write_json(staging, cfg); staging.replace(config)
    print(json.dumps({"status": "provisioned", "image": cfg["image"], "publication_enabled": False}, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
