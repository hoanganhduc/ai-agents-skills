from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .agents import target_for
from .discovery import current_platform, discover_tool, split_command
from .state import load_state


CODEX_CLI_TOOL_SPEC: dict[str, Any] = {
    "candidates": {
        "linux": ["${AAS_CODEX}", "codex", "~/.local/bin/codex", "~/.npm-global/bin/codex"],
        "macos": [
            "${AAS_CODEX}",
            "codex",
            "/opt/homebrew/bin/codex",
            "/usr/local/bin/codex",
        ],
        "wsl": ["${AAS_CODEX}", "codex", "~/.local/bin/codex", "~/.npm-global/bin/codex"],
        "windows": [
            "%AAS_CODEX%",
            "%APPDATA%\\npm\\codex.cmd",
            "codex.cmd",
            "codex.exe",
            "codex",
        ],
    }
}

ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
MAX_PROMPT_INPUT_BYTES = 4 * 1024 * 1024
CODEX_VERSION_RE = re.compile(r"\bcodex-cli\s+([0-9]+(?:\.[0-9]+){2}(?:[-+][A-Za-z0-9.-]+)?)\b")


def run_codex_native_smoke(
    root: Path,
    *,
    agents: set[str] | None = None,
    platform: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    if agents is not None and "codex" not in agents:
        return {"status": "skipped", "reason": "Codex target not selected"}
    target = target_for(root, "codex")
    state = load_state(root)
    router_records = [
        item
        for item in state.get("artifacts", [])
        if item.get("agent") == "codex"
        and item.get("artifact_id") == "instruction-block:writing-instructions"
        and item.get("managed") is True
    ]
    if not router_records:
        return {"status": "skipped", "reason": "no managed Codex writing router"}
    if not target.home.exists():
        return {"status": "skipped", "reason": "Codex target home is missing"}

    checks: list[dict[str, Any]] = []
    router_record = router_records[0]
    managed_block = router_record.get("managed_block")
    try:
        agents_text = target.instructions_file.read_text(encoding="utf-8")
    except OSError:
        agents_text = ""
    block_exact = (
        isinstance(managed_block, str)
        and bool(managed_block.strip())
        and agents_text.count(managed_block.strip()) == 1
    )
    checks.append(check("codex-writing-router-state-match", block_exact))
    if not block_exact:
        return public_result("degraded", checks, reason="managed Codex writing router does not match state")
    override = target.home / "AGENTS.override.md"
    checks.append(check("codex-agents-override-absent", not override.exists()))
    if override.exists():
        return public_result("degraded", checks, reason="AGENTS.override.md shadows the managed AGENTS.md router")

    cli = discover_tool("codex-cli", CODEX_CLI_TOOL_SPEC, current_platform(platform), root)
    if cli.get("status") != "ok" or not cli.get("command"):
        return public_result("skipped", checks, reason="Codex CLI is unavailable or not host-executable")

    base_command = split_command(str(cli["command"]))
    command = [*base_command, "debug", "prompt-input", "writing-router-smoke"]
    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "TERM",
        "NO_COLOR",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(
        {
            "CODEX_HOME": str(target.home),
            "HOME": str(root),
            "USERPROFILE": str(root),
        }
    )
    cli_version = codex_version(base_command, env, timeout)
    checks.append(check("codex-version-detected", cli_version is not None))
    if cli_version is None:
        return public_result("degraded", checks, reason="Codex CLI version could not be established")
    try:
        with tempfile.TemporaryDirectory(prefix="aas-codex-writing-smoke-") as scratch:
            output_path = Path(scratch) / "prompt-input.json"
            with output_path.open("wb") as output:
                completed = subprocess.run(
                    command,
                    text=False,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    timeout=timeout,
                    check=False,
                    env=env,
                    cwd=scratch,
                )
            bounded = output_path.stat().st_size <= MAX_PROMPT_INPUT_BYTES
            stdout = output_path.read_bytes() if bounded else b""
    except Exception as exc:
        checks.append({"name": "codex-prompt-input", "ok": False, "status": "error", "error": type(exc).__name__})
        return public_result("degraded", checks)

    checks.append(check("codex-prompt-input-bounded", bounded))
    if not bounded:
        return public_result("degraded", checks, reason="Codex prompt-input output exceeded the smoke bound")
    text = ANSI_ESCAPE_RE.sub("", stdout.decode("utf-8", errors="replace"))
    try:
        payload = json.loads(text)
        valid_json = True
    except json.JSONDecodeError:
        valid_json = False
    checks.append(check("codex-prompt-input-json", completed.returncode == 0 and valid_json))
    router_visible = valid_json and codex_prompt_has_managed_block(
        payload,
        str(managed_block).strip(),
    )
    checks.extend(
        [
            check("codex-writing-router-visible", router_visible),
            check("codex-writing-base-route", "writing-style-settings.md" in str(managed_block)),
            check("codex-writing-math-route", "math-manuscript-style.md" in str(managed_block)),
            check("codex-writing-graph-route", "graph-combinatorics-style.md" in str(managed_block)),
            check("codex-writing-review-route", "mathscinet-zbmath-review-style.md" in str(managed_block)),
            check("codex-writing-code-separation", "do not apply them as code-writing rules" in str(managed_block)),
            check("codex-retired-writing-route-absent", "claim-preserving-writing.md" not in text),
        ]
    )
    status = "ok" if all(item["ok"] for item in checks) else "degraded"
    return public_result(status, checks, cli_version=cli_version)


def check(name: str, ok: bool) -> dict[str, Any]:
    return {"name": name, "ok": ok, "status": "ok" if ok else "failed"}


def codex_version(command: list[str], env: dict[str, str], timeout: int) -> str | None:
    try:
        with tempfile.TemporaryFile() as output:
            completed = subprocess.run(
                [*command, "--version"],
                text=False,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
                env=env,
            )
            size = output.tell()
            output.seek(0)
            stdout = output.read(1025)
    except Exception:
        return None
    if completed.returncode != 0 or size > 1024:
        return None
    match = CODEX_VERSION_RE.search(stdout.decode("utf-8", errors="replace"))
    return match.group(1) if match else None


def codex_prompt_has_managed_block(payload: Any, needle: str) -> bool:
    if not isinstance(payload, list):
        return False
    for item in payload:
        if not isinstance(item, dict) or item.get("type") != "message" or item.get("role") != "user":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if (
                isinstance(part, dict)
                and part.get("type") == "input_text"
                and isinstance(part.get("text"), str)
                and needle in part["text"]
            ):
                return True
    return False


def public_result(
    status: str,
    checks: list[dict[str, Any]],
    *,
    reason: str | None = None,
    cli_version: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status, "checked": len(checks), "checks": checks}
    if reason is not None:
        result["reason"] = reason
    if cli_version is not None:
        result["cli_version"] = cli_version
    return result
