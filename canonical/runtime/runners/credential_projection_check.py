#!/usr/bin/env python3
"""Fixed child-scope checker for the offline credential projection probe."""
from __future__ import annotations

import argparse
import os
import sys


POINTER_KEYS = {
    "AAS_SKILL_SECRETS_FILE",
    "AAS_COMPUTE_SECRETS_FILE",
    "AAS_PROVIDER_SECRETS_FILE",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--lane", choices=("skill", "compute", "provider"), required=True)
    parser.add_argument("--expect-any-key", action="append", default=[])
    parser.add_argument("--forbid-key", action="append", default=[])
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        print("FAIL reason=invalid-checker-arguments")
        return 2
    if any(os.environ.get(key) for key in POINTER_KEYS):
        print(f"FAIL lane={args.lane} reason=pointer-leak")
        return 1
    if any(os.environ.get(key) for key in args.forbid_key):
        print(f"FAIL lane={args.lane} reason=unrelated-key-leak")
        return 1
    if not args.expect_any_key or not any(os.environ.get(key) for key in args.expect_any_key):
        print(f"FAIL lane={args.lane} reason=expected-key-missing")
        return 1
    print(f"PASS lane={args.lane}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
