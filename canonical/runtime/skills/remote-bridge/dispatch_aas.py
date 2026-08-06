#!/usr/bin/env python3
"""Revocation stub for the retired OpenClaw `/aas` control adapter."""

from __future__ import annotations

import json
import sys


def main(argv: list[str] | None = None) -> int:
    del argv
    print(
        json.dumps(
            {
                "ok": False,
                "status": "retired",
                "error_code": "openclaw_control_adapter_retired",
                "human_reply": (
                    "OpenClaw `/aas` control dispatch is retired. Use the host "
                    "Remote Bridge transport, which derives sender identity from "
                    "the authenticated Zulip event."
                ),
                "spawned": False,
                "destination_inspected": False,
            },
            sort_keys=True,
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
