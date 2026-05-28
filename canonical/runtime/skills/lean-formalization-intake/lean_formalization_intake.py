#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


TOOLS = ("lean", "lake", "elan", "npm", "npx", "pip")
FORMAL_REQUIREMENTS = {"not_requested", "optional", "explicitly_requested", "required_for_delivery"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lean-formalization-intake")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor")
    assess = sub.add_parser("assess")
    assess.add_argument("--claim", default="")
    assess.add_argument("--claim-id", default="")
    assess.add_argument("--formal-check-requirement", choices=sorted(FORMAL_REQUIREMENTS), default="optional")
    assess.add_argument("--output")

    args = parser.parse_args(argv)
    if args.command == "doctor":
        emit(doctor_payload())
        return 0
    if args.command == "assess":
        payload = assess_claim(args.claim, args.claim_id, args.formal_check_requirement)
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        emit(payload)
        return 0
    raise AssertionError(args.command)


def doctor_payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "tool_status": tool_status(),
        "no_auto_install": True,
        "network_required": False,
        "installs_attempted": False,
    }


def tool_status() -> dict[str, dict[str, Any]]:
    status: dict[str, dict[str, Any]] = {}
    for tool in TOOLS:
        path = shutil.which(tool)
        status[tool] = {
            "status": "available" if path else "tool_unavailable",
            "path": path or "",
        }
    return status


def assess_claim(claim: str, claim_id: str, formal_requirement: str) -> dict[str, Any]:
    normalized = claim.strip()
    tools = tool_status()
    lean_available = tools["lean"]["status"] == "available"
    if not normalized:
        decision = "not_applicable"
        reason = "no claim text was supplied"
        expected_cost = "unknown"
        next_step = "continue normal research evidence workflow"
    elif formal_requirement == "required_for_delivery" and not lean_available:
        decision = "blocked"
        reason = "formal support is required for delivery but Lean is not available"
        expected_cost = "unknown"
        next_step = "install/configure Lean manually or record a user-approved scope change"
    elif likely_formalizable(normalized):
        decision = "proceed" if lean_available else "defer"
        reason = (
            "claim shape looks suitable for a Lean statement"
            if lean_available
            else "claim shape looks suitable, but Lean is not available for optional local checking"
        )
        expected_cost = "medium"
        next_step = "create a Lean statement/stub and run the strict verification gate"
    else:
        decision = "defer"
        reason = "claim may require domain-specific definitions or semantic clarification before formalization"
        expected_cost = "high"
        next_step = "keep prose proof and computation evidence limits explicit"
    return {
        "schema_version": "lean-formalization-intake.v1",
        "claim_id": claim_id,
        "formalization_decision": decision,
        "reason": reason,
        "required_definitions": [],
        "expected_cost": expected_cost,
        "recommended_next_step": next_step,
        "formal_check_requirement": formal_requirement,
        "tool_status": tools,
        "limitations": [
            "intake is a routing decision, not proof evidence",
            "defer or unavailable status is not a Lean disproof",
        ],
    }


def likely_formalizable(claim: str) -> bool:
    lowered = claim.lower()
    markers = (
        "for every",
        "for all",
        "there exists",
        "if ",
        " then ",
        "finite",
        "graph",
        "tree",
        "lemma",
        "theorem",
    )
    return any(marker in lowered for marker in markers)


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
