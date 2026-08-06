#!/usr/bin/env python3
"""Request one exact compute-lane driver from the ARL credential broker."""

from __future__ import annotations

import argparse
import os
import sys

from arl_credential_client import BrokerError, request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane", choices=("hetzner", "kaggle", "modal"))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    arguments = list(args.arguments)
    if arguments and arguments[0] == "--":
        arguments = arguments[1:]
    if any("\x00" in value for value in arguments):
        parser.error("compute arguments must not contain NUL")
    try:
        response = request(
            {
                "operation": "compute",
                "lane": args.lane,
                "arguments": arguments,
                "cwd": os.getcwd(),
            },
            timeout_s=86_400,
        )
    except (BrokerError, OSError, ValueError) as exc:
        print(f"compute broker failed: {exc}", file=sys.stderr)
        return 126
    stdout = str(response.get("stdout") or "")
    stderr = str(response.get("stderr") or "")
    if stdout:
        sys.stdout.write(stdout)
    if stderr:
        sys.stderr.write(stderr)
    return int(response.get("returncode", 126))


if __name__ == "__main__":
    raise SystemExit(main())
