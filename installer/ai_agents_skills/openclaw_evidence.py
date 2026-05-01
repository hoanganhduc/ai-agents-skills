from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "openclaw.evidence.v1"
EVIDENCE_TYPES = ("fixture-only", "ci-container", "native-loader", "high-fidelity-loader", "upstream-doc", "manual-review")
AGENTS = ("codex", "claude", "deepseek")
PLATFORMS = ("linux", "macos", "windows", "wsl-native", "wsl-mounted-windows", "ci-container")
INSTALL_MODES = ("reference", "copy", "symlink", "native-command")
PATH_STYLES = ("posix", "windows-drive", "windows-unc", "wsl-posix", "mounted-windows")
SHELLS = ("posix-sh", "bash", "powershell", "cmd", "none")
NATIVE_EVIDENCE_TYPES = {"native-loader", "high-fidelity-loader"}


def load_evidence(path: Path) -> dict[str, Any]:
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("OpenClaw evidence file is not valid JSON") from exc
    if not isinstance(evidence, dict):
        raise ValueError("OpenClaw evidence file must contain a JSON object")
    validate_evidence(evidence)
    return evidence


def build_evidence(
    *,
    evidence_type: str,
    agent: str,
    platform: str,
    install_mode: str,
    path_style: str,
    observed_behavior: str,
    limitations: list[str],
    captured_at: str | None = None,
    agent_version: str | None = None,
    shell: str = "none",
    command_summary: str | None = None,
    artifact_hashes: list[str] | None = None,
) -> dict[str, Any]:
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "evidence_id": "evidence_pending",
        "evidence_type": evidence_type,
        "agent": agent,
        "platform": platform,
        "install_mode": install_mode,
        "path_style": path_style,
        "captured_at": captured_at or now_utc(),
        "observed_behavior": observed_behavior,
        "limitations": limitations,
    }
    if agent_version:
        evidence["agent_version"] = agent_version
    if shell:
        evidence["shell"] = shell
    if command_summary:
        evidence["command_summary"] = command_summary
    if artifact_hashes:
        evidence["artifact_hashes"] = artifact_hashes
    evidence["evidence_id"] = f"evidence_{stable_digest(canonical_evidence_payload(evidence))}"
    validate_evidence(evidence)
    return evidence


def validate_evidence(evidence: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "evidence_id",
        "evidence_type",
        "agent",
        "platform",
        "install_mode",
        "path_style",
        "captured_at",
        "observed_behavior",
        "limitations",
    }
    missing = sorted(required - set(evidence))
    if missing:
        raise ValueError(f"OpenClaw evidence is missing required fields: {', '.join(missing)}")
    if evidence["schema_version"] != SCHEMA_VERSION:
        raise ValueError("OpenClaw evidence schema version is not supported")
    enum_checks = (
        ("evidence_type", EVIDENCE_TYPES),
        ("agent", AGENTS),
        ("platform", PLATFORMS),
        ("install_mode", INSTALL_MODES),
        ("path_style", PATH_STYLES),
    )
    for field, allowed in enum_checks:
        if evidence[field] not in allowed:
            raise ValueError(f"OpenClaw evidence field {field} is not supported: {evidence[field]}")
    if evidence.get("shell", "none") not in SHELLS:
        raise ValueError("OpenClaw evidence shell is not supported")
    if not isinstance(evidence["limitations"], list):
        raise ValueError("OpenClaw evidence limitations must be a list")
    if not str(evidence["observed_behavior"]).strip():
        raise ValueError("OpenClaw evidence observed_behavior is required")
    if evidence["evidence_type"] in NATIVE_EVIDENCE_TYPES and not evidence.get("agent_version"):
        raise ValueError("native OpenClaw evidence requires agent_version")
    expected = f"evidence_{stable_digest(canonical_evidence_payload(evidence))}"
    if evidence["evidence_id"] != expected:
        raise ValueError("OpenClaw evidence content address does not match evidence_id")


def native_support_summary(evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    for item in evidence_items:
        validate_evidence(item)
    native = [
        item
        for item in evidence_items
        if item["evidence_type"] in NATIVE_EVIDENCE_TYPES
    ]
    supported = sorted(
        {
            f"{item['agent']}:{item['platform']}:{item['install_mode']}:{item['path_style']}"
            for item in native
        }
    )
    reference_only = sorted(
        agent
        for agent in AGENTS
        if not any(item["agent"] == agent for item in native)
    )
    return {
        "status": "evidence-recorded",
        "native_support_claims": supported,
        "reference_only_without_native_evidence": reference_only,
        "evidence_count": len(evidence_items),
    }


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_evidence_payload(evidence: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in evidence.items()
        if key != "evidence_id"
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def stable_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
