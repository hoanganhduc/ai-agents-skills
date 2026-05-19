#!/usr/bin/env python3
import argparse
import json
import shutil
import subprocess
import sys

from docling_runtime import add_common_arguments, discover_config_path, resolve_runtime_options


def main() -> int:
    parser = argparse.ArgumentParser()
    add_common_arguments(parser)
    args = parser.parse_args()

    out = {
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "docling_import": False,
        "docling_cli": False,
        "config_path": str(discover_config_path(args)) if discover_config_path(args) else None,
        "local_only": True,
        "ocrspace": "not enabled; Phase 2 only, and must use OCR Engine 3 if added later",
    }
    try:
        out["effective_options"] = resolve_runtime_options(args)
    except Exception as exc:
        out["config_error"] = str(exc)

    try:
        import docling  # noqa: F401
        out["docling_import"] = True
    except Exception as exc:
        out["docling_import_error"] = str(exc)

    cli = shutil.which("docling")
    out["docling_cli_path"] = cli
    if cli:
        try:
            subprocess.run([cli, "--help"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            out["docling_cli"] = True
        except Exception as exc:
            out["docling_cli_error"] = str(exc)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
