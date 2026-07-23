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

    lp = sub.add_parser(
        "live-preflight",
        help="Opt-in live readiness: resolve gauss/claude/lake/project without /prove",
    )
    lp.add_argument(
        "--project-root",
        default="",
        help="Lean project root with .gauss/project.yaml (optional)",
    )
    lp.add_argument(
        "--run-gauss-doctor",
        action="store_true",
        help="Execute `gauss doctor` (no LLM prove). Still local tooling only.",
    )

    lps = sub.add_parser(
        "live-prove-smoke",
        help="Opt-in live prove-path smoke (requires AAS_OPENGAUSS_LIVE_PROVE=1; never default CI)",
    )
    lps.add_argument("--project-root", required=True, help="Lean project root")
    lps.add_argument(
        "--backend",
        default="claude-code",
        choices=["claude-code", "codex"],
        help="Managed prove backend preference for the smoke",
    )
    lps.add_argument(
        "--timeout-sec",
        type=int,
        default=180,
        help="Wall timeout for backend ping / optional prove probe (default 180)",
    )
    lps.add_argument(
        "--attempt-prove",
        action="store_true",
        help="Also try a short non-interactive gauss chat probe (still not claim-support)",
    )
    lps.add_argument(
        "--work-dir",
        default="",
        help="Directory for live_prove_smoke.json report (default: project .gauss/runtime)",
    )

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
    if args.command == "live-preflight":
        return cmd_live_preflight(args)
    if args.command == "live-prove-smoke":
        return cmd_live_prove_smoke(args)
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
                "claude": tool_status("claude", env_keys=("AAS_CLAUDE", "CLAUDE_CLI")),
                "codex": tool_status("codex", env_keys=("AAS_CODEX", "CODEX_CLI")),
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
            "live_test_policy": live_test_policy(),
            "limitations": [
                "doctor is offline and never invokes gauss, lake, lean, or backend CLIs",
                "native Windows live OpenGauss is unsupported; use WSL2 or Morph",
                "missing OpenGauss is not failed theorem evidence",
                "auto-launch requires headless_qualified spike report in work-dir",
                "live-prove-smoke is opt-in (AAS_OPENGAUSS_LIVE_PROVE=1) and never default CI",
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


def live_test_policy() -> dict[str, Any]:
    return {
        "offline_smoke": "default CI — never launches gauss or LLM",
        "live_preflight": "local readiness: PATH/tool resolution; optional gauss doctor",
        "live_prove_smoke": {
            "default_ci": "skipped",
            "enable_env": "AAS_OPENGAUSS_LIVE_PROVE=1",
            "purpose": "backend ping + optional short gauss chat probe; provenance only",
            "not_claim_support": True,
            "not_formal_check": True,
        },
        "path_requirement": [
            "~/.local/bin (gauss, claude symlink)",
            "~/.npm-global/bin (claude-code install)",
        ],
    }


def project_preflight(project_root: str | None) -> dict[str, Any]:
    if not project_root:
        return {"checked": False, "status": "skipped", "reason": "no_project_root"}
    root = Path(project_root).expanduser().resolve()
    yaml_path = root / ".gauss" / "project.yaml"
    basic = root / "ExampleProject" / "Basic.lean"
    if not basic.is_file():
        # generic Lake layouts
        candidates = list(root.glob("**/Basic.lean"))[:5]
        basic_status = {
            "path": str(basic),
            "status": "missing_default",
            "other_basic_lean": [str(p) for p in candidates],
        }
    else:
        basic_status = {"path": str(basic), "status": "present"}
    return {
        "checked": True,
        "root": str(root),
        "exists": root.is_dir(),
        "project_yaml": {
            "path": str(yaml_path),
            "status": "present" if yaml_path.is_file() else "missing",
        },
        "lean_skeleton": basic_status,
        "status": "ok" if root.is_dir() and yaml_path.is_file() else "incomplete",
    }


def cmd_live_preflight(args: argparse.Namespace) -> int:
    """Resolve live tools/paths. Does not run /prove or bank math."""
    # Prefer standard user bins so managed agents with thin PATH still find claude.
    path_extra = []
    home = Path.home()
    for extra in (home / ".local" / "bin", home / ".npm-global" / "bin", home / ".elan" / "bin"):
        if extra.is_dir():
            path_extra.append(str(extra))
    merged_path = os.pathsep.join([*path_extra, os.environ.get("PATH", "")])
    env = {**os.environ, "PATH": merged_path}

    tools = {
        "gauss": _which_status("gauss", env),
        "claude": _which_status("claude", env),
        "codex": _which_status("codex", env),
        "lake": _which_status("lake", env),
        "lean": _which_status("lean", env),
    }
    project = project_preflight(getattr(args, "project_root", "") or None)
    gauss_doctor: dict[str, Any] = {"ran": False}
    if getattr(args, "run_gauss_doctor", False) and tools["gauss"]["status"] == "available":
        gauss_doctor = _run_capture(
            [tools["gauss"]["path"], "doctor"],
            env=env,
            timeout_sec=60,
            cwd=project.get("root") if project.get("exists") else None,
        )
        gauss_doctor["ran"] = True

    claude_ok = tools["claude"]["status"] == "available"
    gauss_ok = tools["gauss"]["status"] == "available"
    ok = gauss_ok and (claude_ok or tools["codex"]["status"] == "available")
    payload = {
        **base_payload("ok" if ok else "degraded"),
        "command": "live-preflight",
        "ok": ok,
        "path_augmented": path_extra,
        "tools": tools,
        "project": project,
        "gauss_doctor": gauss_doctor,
        "ready_for_claude_backend": bool(gauss_ok and claude_ok),
        "ready_for_codex_backend": bool(gauss_ok and tools["codex"]["status"] == "available"),
        "live_api_attempted": False,
        "evidence_policy": evidence_policy(),
        "live_test_policy": live_test_policy(),
        "notes": [
            "live-preflight does not run /prove and is not formal_check",
            "if claude is missing: ensure ~/.local/bin/claude symlink and PATH",
        ],
    }
    emit(payload)
    return 0 if ok else 2


def cmd_live_prove_smoke(args: argparse.Namespace) -> int:
    """Opt-in live prove-path smoke. Never claim-support; default CI must not enable."""
    if os.environ.get("AAS_OPENGAUSS_LIVE_PROVE", "").strip() != "1":
        emit(
            {
                **base_payload("refused"),
                "command": "live-prove-smoke",
                "ok": False,
                "error_code": "live_prove_disabled",
                "message": (
                    "Refusing live prove smoke. Set AAS_OPENGAUSS_LIVE_PROVE=1 explicitly. "
                    "Never enable this in default CI."
                ),
                "gauss_launched": False,
                "live_api_attempted": False,
                "live_test_policy": live_test_policy(),
            }
        )
        return 2

    # Reuse preflight with PATH fix.
    class _NS:
        project_root = args.project_root
        run_gauss_doctor = True

    # Build preflight inline (avoid double emit).
    home = Path.home()
    path_extra = [
        str(p)
        for p in (home / ".local" / "bin", home / ".npm-global" / "bin", home / ".elan" / "bin")
        if p.is_dir()
    ]
    env = {**os.environ, "PATH": os.pathsep.join([*path_extra, os.environ.get("PATH", "")])}
    tools = {
        "gauss": _which_status("gauss", env),
        "claude": _which_status("claude", env),
        "codex": _which_status("codex", env),
        "lake": _which_status("lake", env),
        "lean": _which_status("lean", env),
    }
    project = project_preflight(args.project_root)
    backend = args.backend
    timeout = max(30, int(args.timeout_sec))

    backend_ping: dict[str, Any] = {"backend": backend, "ran": False}
    live_api = False
    if backend == "claude-code":
        if tools["claude"]["status"] != "available":
            payload = {
                **base_payload("failed"),
                "command": "live-prove-smoke",
                "ok": False,
                "error_code": "claude_cli_missing",
                "message": "claude CLI not on PATH after augmenting ~/.local/bin and ~/.npm-global/bin",
                "tools": tools,
                "project": project,
                "gauss_launched": False,
                "live_api_attempted": False,
            }
            emit(payload)
            return 2
        live_api = True
        backend_ping = _run_capture(
            [
                tools["claude"]["path"],
                "-p",
                "Reply with exactly OPENGAUSS_LIVE_BACKEND_OK and nothing else.",
            ],
            env=env,
            timeout_sec=min(timeout, 120),
            cwd=project.get("root") if project.get("exists") else None,
        )
        backend_ping["backend"] = "claude-code"
        backend_ping["marker_ok"] = "OPENGAUSS_LIVE_BACKEND_OK" in (
            (backend_ping.get("stdout") or "") + (backend_ping.get("stderr") or "")
        )
    else:
        if tools["codex"]["status"] != "available":
            emit(
                {
                    **base_payload("failed"),
                    "command": "live-prove-smoke",
                    "ok": False,
                    "error_code": "codex_cli_missing",
                    "tools": tools,
                    "gauss_launched": False,
                    "live_api_attempted": False,
                }
            )
            return 2
        live_api = True
        backend_ping = _run_capture(
            [
                tools["codex"]["path"],
                "exec",
                "--sandbox",
                "read-only",
                "Reply with exactly OPENGAUSS_LIVE_BACKEND_OK and nothing else.",
            ],
            env=env,
            timeout_sec=min(timeout, 120),
            cwd=project.get("root") if project.get("exists") else None,
        )
        backend_ping["backend"] = "codex"
        backend_ping["marker_ok"] = "OPENGAUSS_LIVE_BACKEND_OK" in (
            (backend_ping.get("stdout") or "") + (backend_ping.get("stderr") or "")
        )

    prove_probe: dict[str, Any] = {"ran": False, "attempted": False}
    if args.attempt_prove and tools["gauss"]["status"] == "available" and project.get("exists"):
        prove_probe["attempted"] = True
        live_api = True
        # Short non-interactive chat probe — not a full /prove REPL driver.
        query = (
            f"Project root: {project['root']}. "
            "Do not edit any files. Confirm you can see this Lean project context and "
            "reply with exactly OPENGAUSS_LIVE_PROVE_PATH_OK."
        )
        prove_probe = _run_capture(
            [tools["gauss"]["path"], "chat", "-Q", "-q", query, "--yolo"],
            env=env,
            timeout_sec=timeout,
            cwd=project["root"],
        )
        prove_probe["ran"] = True
        prove_probe["marker_ok"] = "OPENGAUSS_LIVE_PROVE_PATH_OK" in (
            (prove_probe.get("stdout") or "") + (prove_probe.get("stderr") or "")
        )

    backend_ok = bool(backend_ping.get("ok") and backend_ping.get("marker_ok"))
    prove_ok = (not prove_probe.get("attempted")) or bool(prove_probe.get("marker_ok"))
    ok = backend_ok and prove_ok and project.get("status") == "ok"

    report = {
        **base_payload("ok" if ok else "failed"),
        "command": "live-prove-smoke",
        "ok": ok,
        "schema": "opengauss.live_prove_smoke.v1",
        "backend": backend,
        "tools": tools,
        "project": project,
        "backend_ping": _redact_run(backend_ping),
        "prove_probe": _redact_run(prove_probe),
        "live_api_attempted": live_api,
        "gauss_launched": bool(prove_probe.get("ran")),
        "claim_support": False,
        "formal_check": False,
        "evidence_policy": evidence_policy(),
        "live_test_policy": live_test_policy(),
        "notes": [
            "Success means backend path responded; not that F5 (or any theorem) is proved",
            "Record as opengauss_run provenance only",
        ],
    }

    work = Path(args.work_dir).expanduser() if args.work_dir else None
    if work is None and project.get("exists"):
        work = Path(project["root"]) / ".gauss" / "runtime"
    if work is not None:
        try:
            work.mkdir(parents=True, exist_ok=True)
            out = work / "live_prove_smoke.json"
            out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            report["report_path"] = str(out)
        except OSError as exc:
            report["report_write_error"] = str(exc)

    emit(report)
    return 0 if ok else 1


def _which_status(name: str, env: dict[str, str]) -> dict[str, Any]:
    path = shutil.which(name, path=env.get("PATH"))
    return {
        "name": name,
        "status": "available" if path else "tool_unavailable",
        "path": path,
        "executed": False,
        "checked_by": "shutil.which",
    }


def _run_capture(
    argv: list[str],
    *,
    env: dict[str, str],
    timeout_sec: int,
    cwd: str | None,
) -> dict[str, Any]:
    import subprocess

    started = time.time()
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            env=env,
            cwd=cwd,
            timeout=timeout_sec,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "argv0": argv[0],
            "argv_tail": argv[1:6],
            "stdout": (completed.stdout or "")[-4000:],
            "stderr": (completed.stderr or "")[-2000:],
            "duration_ms": int((time.time() - started) * 1000),
            "timeout": False,
        }
    except subprocess.TimeoutExpired as exc:
        raw_out = exc.stdout or ""
        if isinstance(raw_out, (bytes, bytearray)):
            raw_out = raw_out.decode("utf-8", errors="replace")
        return {
            "ok": False,
            "returncode": 124,
            "argv0": argv[0],
            "argv_tail": argv[1:6],
            "stdout": str(raw_out)[-4000:],
            "stderr": "timeout",
            "duration_ms": int((time.time() - started) * 1000),
            "timeout": True,
        }
    except OSError as exc:
        return {
            "ok": False,
            "returncode": 127,
            "argv0": argv[0],
            "error": str(exc),
            "duration_ms": int((time.time() - started) * 1000),
            "timeout": False,
        }


def _redact_run(run: dict[str, Any]) -> dict[str, Any]:
    """Drop bulky/noisy fields for stable reports while keeping markers."""
    if not run:
        return run
    out = dict(run)
    for key in ("stdout", "stderr"):
        text = out.get(key)
        if isinstance(text, str) and len(text) > 800:
            out[key] = text[:400] + "\n…\n" + text[-400:]
    return out


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
