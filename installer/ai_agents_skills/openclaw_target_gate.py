from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .capabilities import looks_like_real_system_root, normalized_path_within


GATE_POLICY_VERSION = "openclaw-target-gate.phase1.v1"
PHASE = "phase1-non-authorizing"

HOME_BLOCK_REASON = "OpenClaw target is fake-root only before native target evidence"
PLAN_BLOCK_REASON = "OpenClaw target writes are fake-root only before native target evidence"
WRITE_BLOCK_REASON = "refusing real-system OpenClaw writes before native target evidence"
RUNTIME_BLOCK_REASON = "refusing real-system OpenClaw runtime writes before native target evidence"
UNINSTALL_BLOCK_REASON = "refusing real-system OpenClaw uninstall before native target evidence"
ROLLBACK_BLOCK_REASON = "refusing real-system OpenClaw rollback before native target evidence"

NO_GO_SURFACES = (
    "real .openclaw writes",
    "approval-eligible real .openclaw action ids",
    "approval-ready real .openclaw target manifests",
    "real .openclaw write records",
    "support files",
    "runtime-backed OpenClaw skills",
    "symlink/reference/adopt/migrate",
    ".openclaw/ai-agents-skills",
    "hooks",
    "plugins",
    "qmd",
    "bin",
    "commands",
    "config",
    "openclaw.json",
    "state",
    "sessions",
    "memory",
    "workspaces",
    "shell profiles",
)

REAL_OPERATION_REASONS = {
    "detect": HOME_BLOCK_REASON,
    "precheck": HOME_BLOCK_REASON,
    "plan": PLAN_BLOCK_REASON,
    "apply": WRITE_BLOCK_REASON,
    "runtime": RUNTIME_BLOCK_REASON,
    "uninstall": UNINSTALL_BLOCK_REASON,
    "rollback": ROLLBACK_BLOCK_REASON,
}

ACTION_CLASS_REASONS = {
    "reference": "OpenClaw reference install mode requires native target evidence",
    "symlink": "OpenClaw symlink install mode requires native target evidence",
    "runtime-backed-skill": "OpenClaw runtime-backed skills require neutral runtime evidence",
    "support-file": "OpenClaw support files require target-support-file manifest metadata",
    "adopt": "OpenClaw adopt, backup-replace, and migrate require native target evidence",
    "backup-replace": "OpenClaw adopt, backup-replace, and migrate require native target evidence",
    "migrate": "OpenClaw adopt, backup-replace, and migrate require native target evidence",
}


@dataclass(frozen=True)
class OpenClawTargetDecision:
    target: str
    operation: str
    status: str
    allowed: bool
    reason: str | None
    gate_policy_version: str
    phase: str
    real_system: bool
    path_under_openclaw: bool
    action_class: str | None = None
    authorizes_real_writes: bool = False
    approval_eligible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def openclaw_target_capabilities() -> dict[str, Any]:
    return {
        "target": "openclaw",
        "phase": PHASE,
        "gate_policy_version": GATE_POLICY_VERSION,
        "real_write_status": "blocked",
        "real_openclaw_writes_allowed": False,
        "approval_eligible": False,
        "allowed_surfaces": {
            "fake_root": ["skill-file"],
            "real_system": [],
        },
        "allowed_install_modes": {
            "fake_root": ["copy"],
            "real_system": [],
        },
        "blocked_action_classes": sorted(ACTION_CLASS_REASONS),
        "required_evidence_classes_before_real_write": [
            "native-loader",
            "native-inertness",
            "artifact-specific",
            "helper-runtime",
            "quiescence-lock",
            "target-pre-state",
            "openclaw-specific-real-system-approval",
        ],
        "no_go_surfaces": list(NO_GO_SURFACES),
        "runtime_root_policy": {
            "phase1": "blocked for OpenClaw-associated writes",
            "future": "validated neutral ai-agents-skills runtime root outside known agent homes and active loader/config/runtime areas",
        },
    }


def openclaw_target_decision(
    root: Path,
    *,
    operation: str,
    path: Path | None = None,
    agent: str = "openclaw",
    action_class: str | None = None,
) -> dict[str, Any]:
    real_system = looks_like_real_system_root(root)
    path_under_openclaw = path is not None and normalized_path_within(root / ".openclaw", path)
    is_openclaw_target = agent == "openclaw" or path_under_openclaw
    reason = None

    if is_openclaw_target and real_system and (path is None or path_under_openclaw):
        reason = REAL_OPERATION_REASONS.get(operation, WRITE_BLOCK_REASON)
    elif agent == "openclaw" and action_class in ACTION_CLASS_REASONS:
        reason = ACTION_CLASS_REASONS[action_class]

    if reason is not None:
        return OpenClawTargetDecision(
            target="openclaw",
            operation=operation,
            status="blocked",
            allowed=False,
            reason=reason,
            gate_policy_version=GATE_POLICY_VERSION,
            phase=PHASE,
            real_system=real_system,
            path_under_openclaw=path_under_openclaw,
            action_class=action_class,
        ).to_dict()

    status = "fake-root-only" if agent == "openclaw" else "not-applicable"
    return OpenClawTargetDecision(
        target="openclaw",
        operation=operation,
        status=status,
        allowed=True,
        reason=None,
        gate_policy_version=GATE_POLICY_VERSION,
        phase=PHASE,
        real_system=real_system,
        path_under_openclaw=path_under_openclaw,
        action_class=action_class,
    ).to_dict()


def openclaw_target_block_reason(
    root: Path,
    *,
    operation: str,
    path: Path | None = None,
    agent: str = "openclaw",
    action_class: str | None = None,
) -> str | None:
    decision = openclaw_target_decision(
        root,
        operation=operation,
        path=path,
        agent=agent,
        action_class=action_class,
    )
    if decision["allowed"]:
        return None
    return str(decision["reason"])


def real_openclaw_path_block_reason(root: Path, path: Path, *, operation: str, agent: str = "openclaw") -> str | None:
    return openclaw_target_block_reason(root, operation=operation, path=path, agent=agent)
