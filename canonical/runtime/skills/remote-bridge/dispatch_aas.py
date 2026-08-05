#!/usr/bin/env python3
"""Dispatch /aas chat text to remote-bridge handle-command; print human_reply JSON.

Source of truth: ai-agents-skills canonical runtime (this file).
Existing OpenClaw workspace copies are legacy; automated publishing is blocked.

Works inside OpenClaw Docker sandbox (HOME=/workspace) and on the host.
The adapter uses workspace-owned config/state; it never synchronizes host paths.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
CONTROL_TEXT_MAX_BYTES = 64 * 1024

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
    """Deprecated inert shim; `/aas` dispatch never syncs legacy paths."""

    return None


def _workspace_root(env: dict[str, str]) -> Path:
    """Resolve the adapter-owned OpenClaw workspace without probing host state."""

    override = env.get("OPENCLAW_WORKSPACE") or env.get("AAS_OPENCLAW_WORKSPACE")
    if override:
        return Path(override).expanduser()
    if env.get("HOME") == "/workspace" or str(_HERE).startswith("/workspace/"):
        return Path("/workspace")
    return Path.home() / ".openclaw" / "workspace"


def _child_environment(parent: dict[str, str], workspace: Path) -> dict[str, str]:
    """Build the narrow adapter child environment without ambient credentials."""

    child: dict[str, str] = {
        "HOME": str(workspace),
        "XDG_CONFIG_HOME": str(workspace / ".config"),
        "PATH": os.defpath,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "REMOTE_BRIDGE_SECRETS_FILE": str(
            workspace / ".config" / "remote-bridge" / "secrets.json"
        ),
        "AAS_REMOTE_BRIDGE_STATE": str(workspace / ".remote-bridge-state"),
        "AAS_REMOTE_JOB_ID": str(parent.get("AAS_REMOTE_JOB_ID") or "example-job"),
    }
    for name in (
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
    ):
        value = parent.get(name)
        if value:
            child[name] = value
    return child


def _read_control_stdin() -> str:
    """Read one bounded UTF-8 external command without exposing it in argv."""

    binary = getattr(sys.stdin, "buffer", None)
    if binary is not None:
        payload = binary.read(CONTROL_TEXT_MAX_BYTES + 1)
    else:  # pragma: no cover - direct tests may replace stdin with StringIO
        payload = sys.stdin.read(CONTROL_TEXT_MAX_BYTES + 1).encode("utf-8")
    if len(payload) > CONTROL_TEXT_MAX_BYTES:
        raise ValueError("external /aas text exceeds the byte limit")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("external /aas stdin must be UTF-8") from exc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Dispatch /aas text through remote-bridge handle-command"
    )
    ap.add_argument(
        "--text-stdin",
        action="store_true",
        required=True,
        help="read one bounded UTF-8 /aas message from stdin",
    )
    ap.add_argument("--principal", default="")
    ap.add_argument("--rb", default="")
    ap.add_argument("--bot-username", default="")
    args = ap.parse_args(argv)

    principal = (args.principal or "").strip()
    if not principal or principal == "cli":
        print(
            json.dumps(
                {
                    "ok": False,
                    "human_reply": "external /aas dispatch requires a non-local sender identity",
                    "error_code": "missing_principal",
                }
            )
        )
        return 2
    try:
        control_text = _read_control_stdin()
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "human_reply": str(exc),
                    "error_code": "invalid_text_stdin",
                }
            )
        )
        return 2

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

    cmd = [
        sys.executable,
        str(rb),
        "handle-command",
        "--text-stdin",
        "--principal",
        principal,
    ]
    if args.bot_username:
        cmd.extend(["--bot-username", args.bot_username])

    parent_env = os.environ.copy()
    workspace = _workspace_root(parent_env)
    env = _child_environment(parent_env, workspace)

    try:
        completed = subprocess.run(
            cmd,
            input=control_text,
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
