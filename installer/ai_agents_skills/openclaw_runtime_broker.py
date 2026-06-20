"""OpenClaw host runtime broker — security-critical core (P7).

The broker runs on the HOST (reachable from the sandbox via host.docker.internal),
holds the validated runtime, and is the host-side root of trust:
  - per-agent capability tokens (a single shared token is insufficient — every
    container reaches host-gateway, so the token IS the identity)
  - verify-before-exec: re-hash runner+target against the approved manifest on
    EVERY spawn and refuse on mismatch
  - a strict env allowlist applied to the child process (the broker runs with the
    user's host env; OPENCLAW_*/secrets/tokens must never reach a helper)

This module is the pure, fully-testable core. The live HTTP bind + actual process
spawn are a thin host-gated shell (``serve``) that delegates every decision here.
INCOMPLETE-ANALYSIS: live-bind reachability + real sandbox exec require a live
OpenClaw host (Linux/UFW probe-backed; other OSes unverified).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Env allowlist: a child gets ONLY these (exact or prefix), and never anything the
# denylist catches (defense in depth — e.g. AAS_BROKER_TOKEN matches AAS_ but is denied).
ENV_ALLOW_EXACT = frozenset(
    {"PATH", "HOME", "LANG", "TZ", "TMPDIR", "TEMP", "TMP", "SHELL", "SYSTEMROOT", "COMSPEC", "PATHEXT"}
)
ENV_ALLOW_PREFIX = ("LC_", "PYTHON", "AAS_")
ENV_DENY_PREFIX = ("OPENCLAW_", "GETSCIPAPERS_", "ANTHROPIC", "OPENAI", "CODEX_", "DEEPSEEK", "ZULIP")
ENV_DENY_SUBSTR = ("SECRET", "TOKEN", "CREDENTIAL", "PASSWORD", "APIKEY", "API_KEY", "BASE_URL")


def env_var_allowed(name: str) -> bool:
    upper = name.upper()
    if any(upper.startswith(p) for p in ENV_DENY_PREFIX):
        return False
    if any(sub in upper for sub in ENV_DENY_SUBSTR):
        return False
    if name in ENV_ALLOW_EXACT:
        return True
    return any(name.startswith(p) for p in ENV_ALLOW_PREFIX)


def build_child_env(parent_env: dict[str, str]) -> dict[str, str]:
    """The env a broker-spawned helper receives — allowlist-first, deny-second."""
    return {k: v for k, v in parent_env.items() if env_var_allowed(k)}


@dataclass
class AgentToken:
    agent: str
    # set of allowed (skill, command) pairs this agent's token may invoke
    allowed: set[tuple[str, str]] = field(default_factory=set)


@dataclass
class BrokerState:
    runtime_root: Path
    # token string -> AgentToken (per-agent capability scoping)
    tokens: dict[str, AgentToken] = field(default_factory=dict)
    # (skill, command) -> {"target_rel": str, "expected_sha256": "sha256:..."} from the approved manifest
    commands: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def broker_authorize(token: str, skill: str, command: str, *, state: BrokerState) -> tuple[str | None, str | None]:
    """Return (agent, None) if authorized, else (None, reason). Fail-closed."""
    entry = state.tokens.get(token)
    if entry is None:
        return None, "unknown or missing broker token"
    if (skill, command) not in entry.allowed:
        return None, f"agent {entry.agent!r} not authorized for ({skill}, {command})"
    return entry.agent, None


def verify_before_exec(skill: str, command: str, *, state: BrokerState) -> tuple[Path | None, str | None]:
    """Re-hash the on-disk target against the approved expected_sha256 (the host-side
    trust root). Return (target_path, None) if it matches, else (None, reason)."""
    spec = state.commands.get((skill, command))
    if spec is None:
        return None, f"no approved command ({skill}, {command})"
    target = (state.runtime_root / spec["target_rel"]).resolve(strict=False)
    try:
        target.relative_to(state.runtime_root.resolve(strict=False))
    except ValueError:
        return None, "approved command target escapes the runtime root"
    actual = _sha256_file(target)
    if actual is None:
        return None, "approved command target is missing on disk"
    if actual != spec["expected_sha256"]:
        return None, "verify-before-exec failed: target hash does not match approved manifest"
    return target, None


def handle_run_request(request: dict[str, Any], *, state: BrokerState, parent_env: dict[str, str]) -> dict[str, Any]:
    """Pure request handler: auth -> verify-before-exec -> build child env -> return an
    execution PLAN (the live shell performs the actual spawn). Never executes here, so
    it is fully unit-testable. Fail-closed at every step."""
    token = str(request.get("token", ""))
    skill = str(request.get("skill", ""))
    command = str(request.get("command", ""))
    args = request.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return {"status": "error", "code": "bad-args", "message": "args must be a list of strings"}

    agent, reason = broker_authorize(token, skill, command, state=state)
    if agent is None:
        return {"status": "denied", "code": "unauthorized", "message": reason}

    target, reason = verify_before_exec(skill, command, state=state)
    if target is None:
        return {"status": "refused", "code": "verify-failed", "message": reason}

    child_env = build_child_env(parent_env)
    interpreter = state.commands[(skill, command)].get("interpreter")
    return {
        "status": "ok",
        "agent": agent,
        "skill": skill,
        "command": command,
        # argv as DATA passed to the verified target; the live shell uses shell=False.
        # An optional approved interpreter (e.g. python3) is prepended for script targets.
        "argv": [*([interpreter] if interpreter else []), str(target), *args],
        "cwd": str(state.runtime_root),
        "env": child_env,
        "verified_sha256": state.commands[(skill, command)]["expected_sha256"],
    }


def exec_plan(plan: dict[str, Any], *, timeout: float = 120.0) -> dict[str, Any]:
    """Execute a verified plan from ``handle_run_request`` (shell=False, filtered env).

    This is the one place the broker actually runs code; it only ever runs an argv
    whose first non-interpreter element was hash-verified host-side. Non-ok plans are
    passed through unchanged (fail-closed)."""
    import subprocess

    if plan.get("status") != "ok":
        return plan
    try:
        proc = subprocess.run(
            plan["argv"],
            cwd=plan.get("cwd"),
            env=plan.get("env"),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "agent": plan.get("agent"), "message": f"command exceeded {timeout}s"}
    return {
        "status": "completed",
        "agent": plan.get("agent"),
        "exit_code": proc.returncode,
        "stdout": proc.stdout[:1_000_000],
        "stderr": proc.stderr[:1_000_000],
    }


def serve(state: BrokerState, *, host: str, port: int, parent_env: dict[str, str], run: bool = False) -> Any:
    """Live host-gated HTTP shell: POST JSON request -> handle_run_request -> exec_plan.

    INCOMPLETE-ANALYSIS: binding host-gateway + executing inside the real sandbox
    network requires a live OpenClaw host; only the pure handler/exec above are
    unit-tested here. Bind the docker0 gateway IP (never 0.0.0.0 broadly) and pair
    with the managed firewall INPUT rule.
    """
    import json as _json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 (stdlib API name)
            length = int(self.headers.get("Content-Length", 0))
            try:
                request = _json.loads(self.rfile.read(length) or b"{}")
            except _json.JSONDecodeError:
                request = {}
            plan = handle_run_request(request, state=state, parent_env=parent_env)
            result = exec_plan(plan) if plan.get("status") == "ok" else plan
            body = _json.dumps(result).encode("utf-8")
            code = 200 if result.get("status") in {"ok", "completed"} else 403
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: Any) -> None:
            return

    httpd = ThreadingHTTPServer((host, port), _Handler)
    if run:  # pragma: no cover - live host only
        httpd.serve_forever()
    return httpd
