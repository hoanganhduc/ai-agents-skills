"""Extract Claude's terminal result from bounded JSON or NDJSON output."""

from __future__ import annotations

import json


_ERROR = "invalid Claude output envelope"


def _reject_constant(_value: str) -> None:
    raise ValueError(_ERROR)


def result_envelope(raw: str) -> dict:
    """Return one terminal result; callers enforce size and result schema.

    Accept a single JSON result object, or newline-delimited object events
    ending in exactly one result. Empty lines are ignored. This function does
    not interpret model output, result success, or provider schema fields.
    """
    if not isinstance(raw, str):
        raise ValueError(_ERROR)
    try:
        single = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        pass
    else:
        if isinstance(single, dict) and single.get("type") == "result":
            return single
        raise ValueError(_ERROR)

    result = None
    try:
        for line in raw.split("\n"):
            if not line.strip():
                continue
            event = json.loads(line, parse_constant=_reject_constant)
            if not isinstance(event, dict) or result is not None:
                raise ValueError(_ERROR)
            if event.get("type") == "result":
                result = event
    except (ValueError, RecursionError):
        raise ValueError(_ERROR) from None
    if result is None:
        raise ValueError(_ERROR)
    return result
