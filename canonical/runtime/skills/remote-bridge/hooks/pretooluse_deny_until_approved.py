#!/usr/bin/env python3
"""Local-only PreToolUse gate: deny until mailbox has matching allow reply.

Reads hook JSON from stdin. No network. Fast path for Grok (and Claude if configured).
Fail closed with explicit deny when possible; host may still fail-open on crash/timeout.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Allow importing sibling remote_bridge when installed side-by-side
_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent
if str(_SKILL) not in sys.path:
    sys.path.insert(0, str(_SKILL))

from remote_bridge import (  # noqa: E402
    Mailbox,
    action_digest,
    state_root,
)


def _deny(reason: str) -> None:
    print(json.dumps({"decision": "deny", "reason": reason}, ensure_ascii=False))
    sys.exit(2)


def _allow(reason: str = "remote-bridge approved") -> None:
    print(json.dumps({"decision": "allow", "reason": reason}, ensure_ascii=False))
    sys.exit(0)


def main() -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        _deny("remote-bridge: invalid hook JSON")
        return

    tool = (
        payload.get("tool_name")
        or payload.get("toolName")
        or (payload.get("tool") or {}).get("name")
        or ""
    )
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    truncated = bool(payload.get("toolInputTruncated") or payload.get("tool_input_truncated"))
    if truncated:
        _deny("remote-bridge: tool input truncated; remote approval not offered")
        return

    job_id = os.environ.get("AAS_REMOTE_JOB_ID") or ""
    provider = os.environ.get("AAS_REMOTE_PROVIDER") or "grok"
    cwd = os.environ.get("AAS_REMOTE_WORKSPACE") or os.environ.get("GROK_WORKSPACE_ROOT") or os.getcwd()

    if not job_id:
        # No armed job: do not block unrelated sessions
        _allow("remote-bridge: no AAS_REMOTE_JOB_ID; pass")
        return

    if truncated or tool_input is None:
        _deny("remote-bridge: missing tool input")
        return

    try:
        digest = action_digest(
            provider=provider,
            job_id=job_id,
            workspace_root=cwd,
            tool=str(tool),
            args=tool_input,
            nonce="gate",  # gate checks stored digest from request; use request path below
        )
    except Exception as exc:  # noqa: BLE001
        _deny(f"remote-bridge: digest error: {exc}")
        return

    # Prefer matching against pending requests' digests (nonce included at create time).
    mb = Mailbox(Path(os.environ.get("AAS_REMOTE_BRIDGE_STATE") or state_root()))
    jdir = mb.job_dir(job_id)
    if not jdir.is_dir():
        _deny(f"remote-bridge: job not armed: {job_id}")
        return

    # If any unconsumed allow exists for a request whose tool+args match via stored digest
    # we recompute using the request nonce.
    req_dir = jdir / "requests"
    matched_digest = None
    if req_dir.is_dir():
        for path in sorted(req_dir.glob("*.json")):
            req = mb.read_json(path)
            if not req or req.get("status") not in {"pending", "resolved"}:
                continue
            if req.get("tool") != str(tool):
                continue
            if req.get("truncated"):
                continue
            nonce = str(req.get("nonce") or "gate")
            try:
                d = action_digest(
                    provider=str(req.get("provider") or provider),
                    job_id=job_id,
                    workspace_root=cwd,
                    tool=str(tool),
                    args=tool_input,
                    nonce=nonce,
                )
            except Exception:
                continue
            if d == req.get("digest"):
                matched_digest = d
                break

    if matched_digest:
        reply = mb.check_approval(job_id, matched_digest)
        if reply and reply.get("decision") == "allow":
            _allow(f"remote-bridge: approved {reply.get('request_id')}")
            return
        # Reuse existing pending request id if present
        pending_id = None
        for path in sorted(req_dir.glob("*.json")):
            req = mb.read_json(path)
            if req and req.get("digest") == matched_digest and not (
                jdir / "replies" / f"{req['request_id']}.json"
            ).exists():
                pending_id = req["request_id"]
                break
        _deny(
            f"remote-bridge: pending approval req={pending_id or '?'} "
            f"digest={matched_digest[:12]}; reply /aas approve {pending_id or '<id>'}"
        )
        return

    # No matching request yet: create one (local only). Bridge/outbox may notify.
    try:
        req = mb.create_request(
            job_id,
            req_type="approve_tool",
            provider=provider,
            tool=str(tool),
            args=tool_input,
            summary=f"{tool} (gate)",
        )
    except Exception as exc:  # noqa: BLE001
        _deny(f"remote-bridge: cannot create request: {exc}")
        return
    _deny(
        f"remote-bridge: pending approval req={req['request_id']} "
        f"digest={req.get('digest_short')}; reply /aas approve {req['request_id']}"
    )


if __name__ == "__main__":
    main()
