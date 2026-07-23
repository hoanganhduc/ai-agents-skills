#!/usr/bin/env python3
"""Dispatch /aas chat text to remote-bridge handle-command; print human_reply JSON.

Source of truth: ai-agents-skills canonical runtime (this file).
OpenClaw workspace copies are published from here via publish_openclaw_adapter.py.

Works inside OpenClaw Docker sandbox (HOME=/workspace) and on the host.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

CANDIDATE_RB = [
    os.environ.get("AAS_REMOTE_BRIDGE_PY") or "",
    # Same package / published vendor tree
    str(_HERE / "remote_bridge.py"),
    str(_HERE.parent / "vendor" / "remote_bridge.py")
    if _HERE.name == "scripts"
    else "",
    str(_HERE / "vendor" / "remote_bridge.py"),
    # Sandbox/workspace-relative (skill is under /workspace/skills/...)
    "/workspace/skills/aas-remote-bridge/vendor/remote_bridge.py",
    # Installed AAS runtime (host; often bind-mounted into sandbox at same path)
    str(
        Path.home()
        / ".local/share/ai-agents-skills/runtime/workspace/skills/remote-bridge/remote_bridge.py"
    ),
]

CANDIDATE_SYNC = [
    os.environ.get("AAS_REMOTE_BRIDGE_SYNC_PY") or "",
    str(_HERE / "sync_remote_bridge_paths.py"),
    str(_HERE.parent / "scripts" / "sync_remote_bridge_paths.py")
    if _HERE.name != "scripts"
    else "",
    "/workspace/skills/aas-remote-bridge/scripts/sync_remote_bridge_paths.py",
    str(
        Path.home()
        / ".local/share/ai-agents-skills/runtime/workspace/skills/remote-bridge"
        / "sync_remote_bridge_paths.py"
    ),
]


def _first_file(candidates: list[str]) -> Path | None:
    for raw in candidates:
        if not raw:
            continue
        p = Path(raw).expanduser()
        if p.is_file():
            return p
    return None


def find_rb(explicit: str | None) -> Path | None:
    ordered: list[str] = []
    if explicit:
        ordered.append(explicit)
    ordered.extend(CANDIDATE_RB)
    return _first_file(ordered)


def _maybe_sync_paths() -> None:
    """Best-effort host↔workspace secrets/state sync (no secrets printed)."""
    try:
        sync_py = _first_file(CANDIDATE_SYNC)
        if sync_py is None:
            return
        import importlib.util

        spec = importlib.util.spec_from_file_location("aas_rb_sync", sync_py)
        if spec is None or spec.loader is None:
            return
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "sync_once"):
            mod.sync_once(quiet=True)
    except Exception:  # noqa: BLE001 — best-effort
        return


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Dispatch /aas text through remote-bridge handle-command"
    )
    ap.add_argument("--text", required=True)
    ap.add_argument("--principal", default="")
    ap.add_argument("--rb", default="")
    ap.add_argument("--bot-username", default="")
    args = ap.parse_args()

    _maybe_sync_paths()

    rb = find_rb(args.rb or None)
    if rb is None:
        tried = [c for c in CANDIDATE_RB if c]
        print(
            json.dumps(
                {
                    "ok": False,
                    "human_reply": (
                        "remote-bridge runtime not found in this environment.\n\n"
                        "Tried:\n- " + "\n- ".join(tried)
                    ),
                    "error_code": "rb_missing",
                }
            )
        )
        return 2

    principal = (args.principal or "").strip() or "cli"
    cmd = [
        sys.executable,
        str(rb),
        "handle-command",
        "--text",
        args.text,
        "--principal",
        principal,
    ]
    if principal == "cli":
        cmd.append("--allow-local-cli")
    if args.bot_username:
        cmd.extend(["--bot-username", args.bot_username])

    env = os.environ.copy()
    env.setdefault("AAS_REMOTE_JOB_ID", "example-job")
    # Prefer host-mounted secrets/state when present
    for secrets in (
        env.get("REMOTE_BRIDGE_SECRETS_FILE"),
        "/workspace/secrets/remote-bridge/secrets.json",
        str(Path.home() / ".openclaw/workspace/secrets/remote-bridge/secrets.json"),
        str(Path.home() / ".config/remote-bridge/secrets.json"),
    ):
        if secrets and Path(secrets).is_file():
            env["REMOTE_BRIDGE_SECRETS_FILE"] = secrets
            break
    # State root (mailbox jobs)
    for state in (
        env.get("AAS_REMOTE_BRIDGE_STATE"),
        "/workspace/.remote-bridge-state",
        str(Path.home() / ".openclaw/workspace/.remote-bridge-state"),
        str(Path.home() / ".local/share/ai-agents-skills/remote-bridge"),
    ):
        if state and Path(state).exists():
            env["AAS_REMOTE_BRIDGE_STATE"] = state
            break

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "human_reply": f"dispatch failed: {exc}"}))
        return 1

    raw = (completed.stdout or "").strip()
    err = (completed.stderr or "").strip()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {
            "ok": completed.returncode == 0,
            "human_reply": raw or err or "empty remote-bridge response",
            "stderr": err,
            "rb": str(rb),
        }

    if not payload.get("human_reply"):
        if payload.get("ok"):
            payload["human_reply"] = json.dumps(payload, indent=2)[:3500]
        else:
            payload["human_reply"] = (
                payload.get("message")
                or payload.get("error_code")
                or err
                or "remote-bridge failed"
            )
    payload.setdefault("rb", str(rb))
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
