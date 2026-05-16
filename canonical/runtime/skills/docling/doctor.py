#!/usr/bin/env python3
import json, shutil, subprocess, sys
out = {'python': sys.version.split()[0], 'docling_import': False, 'docling_cli': False}
try:
    import docling  # noqa: F401
    out['docling_import'] = True
except Exception as exc:
    out['docling_import_error'] = str(exc)
cli = shutil.which('docling')
out['docling_cli_path'] = cli
if cli:
    try:
        subprocess.run([cli, '--help'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        out['docling_cli'] = True
    except Exception as exc:
        out['docling_cli_error'] = str(exc)
print(json.dumps(out, indent=2))
