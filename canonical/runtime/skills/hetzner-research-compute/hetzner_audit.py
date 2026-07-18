"""Append-only audit log for the Hetzner lane (plan section 6).

One redacted JSONL record per lifecycle event -- provision, destroy, reap, kill-switch --
so agent-driven paid infrastructure leaves a forensic trail: what was provisioned, why,
the estimated cost, the labels that identify it, and whether the reaper later cleaned it
up. The log is the answer to "did a runaway or crashed agent leave a paid server behind".

Secret safety is belt-and-braces. The records are BUILT without the token (the driver and
reaper never put it in an event), and every serialized line is additionally passed through
the redaction filter before it is written, so even a record that accidentally embedded the
token cannot leak it to disk.

Cost fields are honest: `est_eur` is the pessimistic worst-case reservation from the budget
ledger (known at provision time). `real_eur` is left null because neither Modal nor Hetzner
exposes a queryable per-server billed amount (plan section 6.1); the reservation ledger, not
this log, is the live spend gate. When real metering is wired later it fills `real_eur`.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUDIT_FILENAME = "hetzner-audit.jsonl"

# Event types (closed set; keep in sync with the driver + reaper call sites).
EVENT_PROVISION = "provision"
EVENT_DESTROY = "destroy"
EVENT_REAP = "reap"
EVENT_KILL = "kill_switch"

REDACTED = "<REDACTED_HCLOUD_TOKEN>"


def audit_path(state_root: Path) -> Path:
    return Path(state_root) / AUDIT_FILENAME


def _redact(text: str, token: str | None) -> str:
    return text.replace(token, REDACTED) if token else text


def append(state_root: Path | None, record: dict[str, Any], *, token: str | None = None) -> Path | None:
    """Append one audit record as a single JSONL line and return the log path.

    No-op returning None when `state_root` is None, so callers without a ledger root (an
    emergency config-less reaper, say) never crash. The token defaults to the environment
    value; the fully serialized line is redacted before the write, never after."""
    if state_root is None:
        return None
    token = token if token is not None else os.environ.get("HCLOUD_TOKEN")
    entry = dict(record)
    entry.setdefault("ts", time.time())
    entry.setdefault("ts_iso", datetime.now(timezone.utc).isoformat())
    line = _redact(json.dumps(entry, sort_keys=True), token)
    path = audit_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return path


def read(state_root: Path) -> list[dict[str, Any]]:
    """Read all audit records (inspection / test helper)."""
    path = audit_path(state_root)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
