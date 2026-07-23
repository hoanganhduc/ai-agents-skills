#!/usr/bin/env python3
"""opengauss: OpenGauss formal-lane helper for AAS.

Phase 0: offline doctor / config-snippet / smoke (never install or launch).
Phase 1+: spike report, handoff helpers, fail-closed ARL adapter verbs.

Never auto-installs OpenGauss. Never reads secret values from disk.
Live launch is refuse-by-default unless a headless_qualified spike file exists.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "opengauss.v1"
OPENGAUSS_GITHUB = "https://github.com/math-inc/OpenGauss"
OPENGAUSS_SITE = "https://www.math.inc/opengauss"
MORPH_TEMPLATE = "https://morph.new/opengauss-0-2-2"
PLACEHOLDER_TOKEN = "<GAUSS_BACKEND_TOKEN>"
PLACEHOLDER_API_KEY = "<ANTHROPIC_OR_OPENAI_API_KEY>"
SMOKE_CANARY = "OPENGAUSS-SMOKE-CANARY"
SPIKE_OUTCOMES = {"headless_qualified", "interactive_only", "failed"}
DEFAULT_AUTO_WORKFLOWS = ("prove", "draft", "formalize")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opengauss")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor")
    sub.add_parser("config-snippet")
    sub.add_parser("smoke")
    sub.add_parser("selftest")

    sp = sub.add_parser("spike")
    sp.add_argument("--work-dir", required=True, help="Directory for spike report JSON")
    sp.add_argument(
        "--assume-outcome",
        choices=sorted(SPIKE_OUTCOMES),
        default="",
        help="Test-only override; production probes tools without launching gauss",
    )

    for name in ("preflight", "launch", "status", "harvest", "kill"):
        p = sub.add_parser(name)
        p.add_argument("--work-dir", required=True)
        if name == "launch":
            p.add_argument("--workflow", default="prove", choices=list(DEFAULT_AUTO_WORKFLOWS) + ["review"])
            p.add_argument("--project-root", default="")
            p.add_argument("--standing-auth", default="", help="Path to standing-auth JSON")
            p.add_argument("--force-unqualified", action="store_true", help="Forbidden in production; tests only")
        if name in {"status", "harvest", "kill"}:
            p.add_argument("--run-id", required=True)

    hp = sub.add_parser("handoff-intake")
    hp.add_argument("--claim-id", required=True)
    hp.add_argument("--informal-statement-ref", required=True)
    hp.add_argument("--project-root", required=True)
    hp.add_argument("--decision", default="proceed", choices=["proceed", "defer", "not_applicable", "blocked"])

    hg = sub.add_parser("handoff-gate")
    hg.add_argument("--run-id", required=True)
    hg.add_argument("--project-root", required=True)
    hg.add_argument("--workflow", default="prove")
    hg.add_argument("--gauss-exit", default="success", choices=["success", "partial", "blocked", "unavailable"])

    args = parser.parse_args(argv)

    if args.command == "doctor":
        emit(doctor_payload())
        return 0
    if args.command == "config-snippet":
        emit(config_snippet_payload())
        return 0
    if args.command in {"smoke", "selftest"}:
        emit(smoke_payload())
        return 0
    if args.command == "spike":
        return cmd_spike(args)
    if args.command == "preflight":
        return cmd_preflight(args)
    if args.command == "launch":
        return cmd_launch(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "harvest":
        return cmd_harvest(args)
    if args.command == "kill":
        return cmd_kill(args)
    if args.command == "handoff-intake":
        emit(handoff_intake_payload(args))
        return 0
    if args.command == "handoff-gate":
        emit(handoff_gate_payload(args))
        return 0
    raise AssertionError(args.command)


def base_payload(status: str = "ok") -> dict[str, Any]:
    return {
        "status": status,
        "schema_version": SCHEMA_VERSION,
        "skill": "opengauss",
        "no_auto_install": True,
        "installs_attempted": False,
        "network_required": False,
        "live_api_attempted": False,
        "config_written": False,
        "server_started": False,
        "package_install_attempted": False,
        "real_secrets_read": False,
        "gauss_launched": False,
    }


def doctor_payload() -> dict[str, Any]:
    plat = platform_status()
    payload = base_payload()
    payload.update(
        {
            "helper_python": python_status(),
            "platform": plat,
            "tool_status": {
                "gauss": tool_status("gauss", env_keys=("AAS_GAUSS", "GAUSS_CLI")),
                "uv": tool_status("uv"),
                "uvx": tool_status("uvx"),
                "rg": tool_status("rg"),
                "lake": tool_status("lake", env_keys=("AAS_LAKE",)),
                "lean": tool_status("lean", env_keys=("AAS_LEAN",)),
                "tmux": tool_status("tmux"),
            },
            "backend_auth_status": {
                "claude": auth_presence(("ANTHROPIC_API_KEY", "CLAUDE_API_KEY")),
                "codex_openai": auth_presence(("OPENAI_API_KEY",)),
                "note": "reports present|missing|empty only; never reads ~/.gauss/.env contents",
            },
            "live_execution_support": plat["live_execution_support"],
            "spike_outcome_hint": probe_spike_outcome_hint(),
            "manual_live_use": manual_live_use(),
            "evidence_policy": evidence_policy(),
            "adapter_policy": adapter_policy(),
            "limitations": [
                "doctor is offline and never invokes gauss, lake, lean, or backend CLIs",
                "native Windows live OpenGauss is unsupported; use WSL2 or Morph",
                "missing OpenGauss is not failed theorem evidence",
                "auto-launch requires headless_qualified spike report in work-dir",
            ],
        }
    )
    return payload


def config_snippet_payload() -> dict[str, Any]:
    payload = base_payload()
    payload.update(
        {
            "redaction_status": "placeholder-only",
            "local_install_snippet": {
                "posix": [
                    f"git clone {OPENGAUSS_GITHUB}.git",
                    "cd OpenGauss",
                    "./scripts/install.sh",
                    f"# echo 'export ANTHROPIC_API_KEY={PLACEHOLDER_API_KEY}' >> ~/.gauss/.env",
                    "gauss doctor  # when installed",
                ],
                "windows_wsl": [
                    "wsl --install -d Ubuntu  # if needed",
                    "wsl",
                    f"git clone {OPENGAUSS_GITHUB}.git ~/OpenGauss",
                    "cd ~/OpenGauss && ./scripts/install.sh",
                ],
                "native_windows": {
                    "status": "unsupported",
                    "message": "Use WSL2 install path or Morph cloud template; do not expect native gauss.exe",
                },
            },
            "manual_workflows_mvp": {
                "prove": "gauss then /prove <scope> inside an active project",
                "draft": "gauss then /draft <topic>",
                "note": "Interactive REPL is the documented upstream UX; AAS auto-launch is fail-closed",
            },
            "project_yaml_example": {
                "path": ".gauss/project.yaml",
                "content_placeholder": {
                    "name": "<project-name>",
                    "root": "<absolute-or-repo-relative-lean-root>",
                },
            },
            "morph_cloud": {
                "url": MORPH_TEMPLATE,
                "setup": "manual-only",
                "credential": PLACEHOLDER_TOKEN,
            },
            "manual_live_use": manual_live_use(),
            "warnings": [
                "copy snippets manually; this helper never writes config or secrets",
                f"do not replace {PLACEHOLDER_API_KEY} or {PLACEHOLDER_TOKEN} in the repo",
                "OpenGauss workflow success is opengauss_run evidence, never formal_check alone",
            ],
            "snippet_contains_placeholder": True,
        }
    )
    return payload


def smoke_payload() -> dict[str, Any]:
    snippet = config_snippet_payload()
    doctor = doctor_payload()
    serialized = json.dumps(snippet, sort_keys=True)
    payload = base_payload()
    payload.update(
        {
            "smoke_mode": "offline",
            "command": "smoke",
            "ok": True,
            "tool_status": doctor["tool_status"],
            "platform": doctor["platform"],
            "snippet_contains_placeholder": PLACEHOLDER_API_KEY in serialized
            or PLACEHOLDER_TOKEN in serialized,
            "snippet_has_install_pointer": OPENGAUSS_GITHUB in serialized,
            "native_windows_refused": (
                snippet.get("local_install_snippet", {}).get("native_windows", {}).get("status")
                == "unsupported"
            ),
            "evidence_policy": evidence_policy(),
            "adapter_policy": adapter_policy(),
            "manual_live_use": manual_live_use(),
            "checks": [
                "offline_only",
                "no_auto_install",
                "placeholder_redaction",
                "windows_live_policy",
                "evidence_policy_present",
                "adapter_fail_closed",
            ],
        }
    )
    if SMOKE_CANARY in json.dumps(payload, sort_keys=True):
        payload["status"] = "error"
        payload["ok"] = False
        payload["error"] = "smoke canary leaked into payload"
    return payload


def probe_spike_outcome_hint() -> str:
    """Non-executing classification from tool presence only."""
    gauss = tool_status("gauss", env_keys=("AAS_GAUSS", "GAUSS_CLI"))
    if platform.system().lower() == "windows" and not platform_status().get("in_wsl"):
        return "failed"
    if gauss["status"] != "available":
        return "failed"
    # Without a proven non-TTY scripted interface, never claim headless_qualified.
    return "interactive_only"


def cmd_spike(args: argparse.Namespace) -> int:
    work = Path(args.work_dir).expanduser()
    work.mkdir(parents=True, exist_ok=True)
    if args.assume_outcome:
        outcome = args.assume_outcome
        reasons = [f"assumed_outcome={outcome}"]
    else:
        outcome = probe_spike_outcome_hint()
        reasons = [
            "non_executing_probe",
            "headless_qualified_requires_dated_manual_spike_evidence",
            f"gauss_status={tool_status('gauss', env_keys=('AAS_GAUSS', 'GAUSS_CLI'))['status']}",
        ]
        if outcome == "interactive_only":
            reasons.append("gauss_present_but_headless_interface_not_proven_in_aas")
        if outcome == "failed":
            reasons.append("gauss_unavailable_or_native_windows")

    report = {
        **base_payload(),
        "command": "spike",
        "outcome": outcome,
        "reasons": reasons,
        "platform": platform_status(),
        "tool_status": {
            "gauss": tool_status("gauss", env_keys=("AAS_GAUSS", "GAUSS_CLI")),
            "lake": tool_status("lake", env_keys=("AAS_LAKE",)),
            "lean": tool_status("lean", env_keys=("AAS_LEAN",)),
        },
        "exit_criteria": {
            "no_tty": False,
            "durable_run_id": False,
            "process_tree_kill": False,
            "backend_nontty_auth": False,
            "setsid_survival": False,
            "note": "Set true only after operator documents a real headless spike; do not invent success",
        },
        "unlocks_phase3_auto": outcome == "headless_qualified",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = work / "spike_report.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report["report_path"] = str(path)
    # config_written false: report is operator work product, not client secrets
    emit(report)
    return 0 if outcome != "failed" else 2


def load_spike(work: Path) -> dict[str, Any] | None:
    path = work / "spike_report.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def cmd_preflight(args: argparse.Namespace) -> int:
    work = Path(args.work_dir).expanduser()
    work.mkdir(parents=True, exist_ok=True)
    spike = load_spike(work)
    doctor = doctor_payload()
    headroom = host_headroom_probe()
    ok = True
    reasons: list[str] = []
    if spike is None:
        ok = False
        reasons.append("missing_spike_report")
    elif spike.get("outcome") != "headless_qualified":
        ok = False
        reasons.append(f"spike_outcome={spike.get('outcome')}")
    if doctor["tool_status"]["gauss"]["status"] != "available":
        ok = False
        reasons.append("gauss_unavailable")
    if not headroom.get("host_headroom_ok"):
        ok = False
        reasons.append("host_headroom_not_ok")
    if platform.system().lower() == "windows" and not platform_status().get("in_wsl"):
        ok = False
        reasons.append("native_windows_unsupported")

    payload = {
        **base_payload("ok" if ok else "blocked"),
        "command": "preflight",
        "ok": ok,
        "auto_launch_allowed": ok,
        "reasons": reasons,
        "spike_outcome": (spike or {}).get("outcome"),
        "host_headroom": headroom,
        "tool_status": doctor["tool_status"],
        "adapter_policy": adapter_policy(),
    }
    emit(payload)
    return 0 if ok else 3


def cmd_launch(args: argparse.Namespace) -> int:
    work = Path(args.work_dir).expanduser()
    work.mkdir(parents=True, exist_ok=True)
    if args.force_unqualified and os.environ.get("AAS_OPENGAUSS_ALLOW_FORCE") != "1":
        emit(
            {
                **base_payload("error"),
                "command": "launch",
                "ok": False,
                "error_code": "force_forbidden",
                "message": "force-unqualified requires AAS_OPENGAUSS_ALLOW_FORCE=1 (tests only)",
            }
        )
        return 4

    # Reuse preflight logic unless test force
    class NS:
        work_dir = str(work)

    if not args.force_unqualified:
        # Capture preflight by calling logic
        spike = load_spike(work)
        if spike is None or spike.get("outcome") != "headless_qualified":
            emit(
                {
                    **base_payload("blocked"),
                    "command": "launch",
                    "ok": False,
                    "error_code": "not_headless_qualified",
                    "message": "Auto-launch blocked until spike_report.json outcome is headless_qualified",
                    "spike_outcome": (spike or {}).get("outcome"),
                    "gauss_launched": False,
                }
            )
            return 3
        headroom = host_headroom_probe()
        if not headroom.get("host_headroom_ok"):
            emit(
                {
                    **base_payload("blocked"),
                    "command": "launch",
                    "ok": False,
                    "error_code": "host_headroom",
                    "message": "Auto-launch blocked: host headroom insufficient",
                    "host_headroom": headroom,
                    "gauss_launched": False,
                }
            )
            return 3

    # Still fail-closed on actual gauss process: AAS does not script interactive REPL.
    run_id = f"og-{uuid.uuid4().hex[:12]}"
    run_dir = work / "gauss_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "run_id": run_id,
        "workflow": args.workflow,
        "project_root": args.project_root or "",
        "status": "refused_no_headless_driver",
        "gauss_launched": False,
        "message": (
            "Even with qualification flags, this AAS adapter refuses to spawn gauss "
            "until a documented headless driver exists. Use manual interactive Gauss "
            "and harvest artifacts into the work dir."
        ),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (run_dir / "status.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    emit(
        {
            **base_payload("blocked"),
            "command": "launch",
            "ok": False,
            "error_code": "no_headless_driver",
            "run_id": run_id,
            "run_dir": str(run_dir),
            "gauss_launched": False,
            "message": record["message"],
        }
    )
    return 5


def cmd_status(args: argparse.Namespace) -> int:
    work = Path(args.work_dir).expanduser()
    path = work / "gauss_runs" / args.run_id / "status.json"
    if not path.is_file():
        emit({**base_payload("error"), "command": "status", "ok": False, "error_code": "unknown_run"})
        return 2
    data = json.loads(path.read_text(encoding="utf-8"))
    emit({**base_payload(), "command": "status", "ok": True, "run": data, "gauss_launched": False})
    return 0


def cmd_harvest(args: argparse.Namespace) -> int:
    work = Path(args.work_dir).expanduser()
    run_dir = work / "gauss_runs" / args.run_id
    status_path = run_dir / "status.json"
    if not status_path.is_file():
        emit({**base_payload("error"), "command": "harvest", "ok": False, "error_code": "unknown_run"})
        return 2
    data = json.loads(status_path.read_text(encoding="utf-8"))
    evidence = {
        "evidence_type": "opengauss_run",
        "run_id": args.run_id,
        "workflow": data.get("workflow"),
        "terminal_state": data.get("status"),
        "gauss_launched": False,
        "harvest_status": "empty",
        "artifact_paths": [],
        "limitations": [
            "no live gauss process was started by AAS adapter",
            "opengauss_run is provenance only; never formal_check",
        ],
        "no_claim_support": True,
    }
    out = run_dir / "opengauss_run_evidence.json"
    out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    emit(
        {
            **base_payload(),
            "command": "harvest",
            "ok": True,
            "evidence_path": str(out),
            "evidence": evidence,
            "next": "run lean-strict-verification-gate on any Lean artifacts; do not promote claim support from this row",
        }
    )
    return 0


def cmd_kill(args: argparse.Namespace) -> int:
    work = Path(args.work_dir).expanduser()
    path = work / "gauss_runs" / args.run_id / "status.json"
    if not path.is_file():
        emit({**base_payload("error"), "command": "kill", "ok": False, "error_code": "unknown_run"})
        return 2
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = "killed"
    data["killed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    emit({**base_payload(), "command": "kill", "ok": True, "run_id": args.run_id, "gauss_launched": False})
    return 0


def handoff_intake_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        **base_payload(),
        "command": "handoff-intake",
        "handoff": {
            "schema": "opengauss.intake_handoff.v1",
            "claim_id": args.claim_id,
            "formalization_decision": args.decision,
            "informal_statement_ref": args.informal_statement_ref,
            "target_project_root": args.project_root,
            "allowed_workflows": list(DEFAULT_AUTO_WORKFLOWS),
            "no_claim_support": True,
        },
    }


def handoff_gate_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        **base_payload(),
        "command": "handoff-gate",
        "handoff": {
            "schema": "opengauss.gate_handoff.v1",
            "run_id": args.run_id,
            "project_root": args.project_root,
            "workflow": args.workflow,
            "gauss_exit": args.gauss_exit,
            "no_claim_support": True,
            "next_gate": "lean-strict-verification-gate",
        },
    }


def host_headroom_probe() -> dict[str, Any]:
    """Lightweight loadavg-based headroom (no psutil required)."""
    cores = os.cpu_count() or 1
    load1 = None
    try:
        load1 = os.getloadavg()[0]  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        load1 = None
    # Conservative: require load1 < cores * 0.85 when available
    if load1 is None:
        ok = True
        note = "loadavg_unavailable_platform_assume_ok_for_preflight_only"
    else:
        ok = load1 < cores * 0.85
        note = "loadavg_vs_cores"
    return {
        "cores": cores,
        "load1": load1,
        "host_headroom_ok": ok,
        "note": note,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def platform_status() -> dict[str, Any]:
    system = platform.system().lower()
    in_wsl = False
    if system == "linux":
        try:
            with open("/proc/version", encoding="utf-8", errors="ignore") as fh:
                in_wsl = "microsoft" in fh.read().lower()
        except OSError:
            in_wsl = False

    if system == "windows":
        live = "unsupported"
        note = "native Windows live OpenGauss unsupported; use WSL2"
    elif in_wsl:
        live = "wsl_supported_path"
        note = "WSL path is the supported Windows host strategy when AAS+Lean+Gauss share the distro"
    elif system == "darwin":
        live = "experimental"
        note = "macOS live execution is experimental until dated evidence exists"
    elif system == "linux":
        live = "primary"
        note = "linux is the primary live target"
    else:
        live = "unknown"
        note = f"unrecognized platform {system}"

    return {
        "system": system,
        "in_wsl": in_wsl,
        "native_windows_live": "unsupported" if system == "windows" else "n/a",
        "live_execution_support": live,
        "note": note,
    }


def python_status() -> dict[str, Any]:
    return {
        "status": "available",
        "version": ".".join(str(part) for part in sys.version_info[:3]),
        "executable": sys.executable,
    }


def tool_status(name: str, env_keys: tuple[str, ...] = ()) -> dict[str, Any]:
    path = ""
    for key in env_keys:
        candidate = os.environ.get(key, "").strip()
        if candidate and os.path.isfile(candidate):
            path = candidate
            break
    if not path:
        found = shutil.which(name)
        path = found or ""
    return {
        "status": "available" if path else "tool_unavailable",
        "path": path,
        "checked_by": "env_or_shutil.which",
        "executed": False,
    }


def auth_presence(env_keys: tuple[str, ...]) -> str:
    seen_empty = False
    for key in env_keys:
        if key not in os.environ:
            continue
        if os.environ.get(key) == "":
            seen_empty = True
            continue
        return "present"
    if seen_empty:
        return "empty"
    return "missing"


def manual_live_use() -> dict[str, Any]:
    return {
        "product": "OpenGauss",
        "site": OPENGAUSS_SITE,
        "source_repository": OPENGAUSS_GITHUB,
        "morph_template": MORPH_TEMPLATE,
        "install": "manual-native (clone + ./scripts/install.sh; Windows via WSL2)",
        "workflows_mvp": ["/prove", "/draft"],
        "workflows_gated": ["/swarm", "/autoprove", "/autoformalize"],
        "backends": ["claude-code", "codex"],
        "auto_launch": "fail-closed until headless_qualified spike + headless driver",
    }


def evidence_policy() -> dict[str, Any]:
    return {
        "opengauss_run": "provenance only; never formal_check or claim promotion by itself",
        "formal_check": "requires lean-strict-verification-gate local scan/typecheck evidence",
        "claim_support": "uses deep-research CLAIM_SUPPORT_STATUSES; lead/human for supports_claim_after_equivalence_review",
        "missing_gauss": "tool_unavailable / defer — not failed theorem evidence",
    }


def adapter_policy() -> dict[str, Any]:
    return {
        "auto_default": False,
        "require_spike_outcome": "headless_qualified",
        "require_host_headroom": True,
        "caps_immutable_by_agents": True,
        "usd_enforcement": "advisory_unless_measurable",
        "launch_without_driver": "always_refused",
    }


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
