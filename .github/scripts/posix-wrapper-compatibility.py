"""Run native wrapper regressions and write only allowlisted coverage metadata."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from tests import test_posix_wrapper_compatibility as compatibility  # noqa: E402


class CoverageResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        row = {"test": test.id(), "status": "ok" if err is None else "failed"}
        # Do not serialize arbitrary parameter values or exception text.
        for key in ("wrapper", "managed", "credentials"):
            if key in subtest.params:
                row[key] = subtest.params[key]
        self.cases.append(row)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    shell = subprocess.run(["/bin/bash", "-p", "-c", 'printf "%s" "$BASH_VERSION"'],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", check=True, timeout=10,
                           env={"PATH": "/usr/bin:/bin"}).stdout
    suite = unittest.defaultTestLoader.loadTestsFromModule(compatibility)
    result = unittest.TextTestRunner(verbosity=2, resultclass=CoverageResult).run(suite)
    expected = {case[1] for case in compatibility.CASES}
    covered = {row.get("wrapper") for row in result.cases if row["status"] == "ok"}
    ok = result.wasSuccessful() and not result.skipped and result.testsRun > 0 and expected <= covered
    report = {
        "schema": "ai-agents-skills.posix-wrapper-compatibility.v1",
        "status": "ok" if ok else "failed",
        "platform": platform.system(), "bash": shell, "python": platform.python_version(),
        "commit": os.environ.get("GITHUB_SHA"),
        "tests_run": result.testsRun, "skipped": len(result.skipped),
        "required_wrappers": sorted(expected), "covered_wrappers": sorted(covered - {None}),
        "cases": result.cases,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
