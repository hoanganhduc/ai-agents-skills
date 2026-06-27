from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .delegation import PROVIDER_CLI_SPECS, build_external_agent_prechecks, redacted_command
from .delegation_packets import RESULT_SCHEMA_VERSION, TASK_SCHEMA_VERSION, validate_result
from .discovery import current_platform, discover_tool
from .state import now_run_id, preflight_state_path, sha256_text, state_dir, write_text_atomic


EXTERNAL_PROVIDERS = {"claude", "deepseek", "copilot"}

DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"


def provider_env_defaults(provider: str, env: dict[str, str]) -> dict[str, str]:
    """Non-secret endpoint defaults injected into the child env at dispatch time.

    The DeepSeek CLI (codewhale) reads its endpoint from DEEPSEEK_BASE_URL in its
    headless exec path, so default it when unset and delegation works without the
    caller exporting it. Never overrides a value the caller already provided.
    """
    if provider == "deepseek" and not env.get("DEEPSEEK_BASE_URL"):
        return {"DEEPSEEK_BASE_URL": DEEPSEEK_DEFAULT_BASE_URL}
    return {}
RESULT_START = "AAS_RESULT_JSON_START"
RESULT_END = "AAS_RESULT_JSON_END"
MAX_TASK_CHARS = 200_000


def dispatch_external_agents(args: Any, manifests: dict[str, Any]) -> dict[str, Any]:
    platform = current_platform(args.platform)
    env = dict(os.environ)
    task_text = read_task_text(args.task, args.task_file)
    requested = requested_providers(args)
    run_id = now_run_id()
    run_dir = resolve_run_dir(args.root, args.run_dir, run_id)
    prechecks = build_external_agent_prechecks(args.root, platform, manifests["delegation"], env=env)
    plan = build_dispatch_plan(
        args.root,
        platform,
        manifests["delegation"],
        prechecks,
        requested,
        max_providers=args.max_providers,
        research=args.research,
        resolved_model=args.resolved_model,
        resolved_thinking=args.resolved_thinking,
        env=env,
    )
    result: dict[str, Any] = {
        "schema_version": "external-agent-dispatch.v1",
        "status": plan_status(plan),
        "dry_run": bool(args.dry_run),
        "root": str(args.root),
        "run_id": run_id,
        "run_dir": str(run_dir),
        "task_ref": task_ref(args),
        "role": args.role,
        "template": args.template,
        "research": bool(args.research),
        "policy": prechecks["policy"],
        "dispatch_plan": public_dispatch_plan(plan),
    }
    if args.dry_run:
        return result
    if any(item["status"] == "ready" for item in plan):
        if not args.allow_external_cli:
            raise ValueError("live external CLI dispatch requires --allow-external-cli")
    if not any(item["status"] == "ready" for item in plan):
        return result

    ensure_run_dir(args.root, run_dir)
    write_json(root=args.root, path=run_dir / "transport_manifest.json", data=transport_manifest(plan))
    write_text(args.root, run_dir / "timeout_events.jsonl", "")
    write_text(args.root, run_dir / "truncation_events.jsonl", "")
    write_text(args.root, run_dir / "evidence-map.jsonl", "")
    participants = []
    for item in plan:
        if item["status"] != "ready":
            participants.append(item)
            continue
        participants.append(
            run_external_participant(
                args.root,
                run_dir,
                item,
                task_text,
                role=args.role,
                template=args.template,
                timeout=args.timeout,
                env=env,
            )
        )
    result["participants"] = participants
    result["status"] = dispatched_status(participants)
    write_json(args.root, run_dir / "manifest.json", run_manifest(result, participants))
    return result


def requested_providers(args: Any) -> list[str]:
    if args.providers:
        return [item.strip() for item in args.providers.split(",") if item.strip()]
    return [args.provider]


def read_task_text(task: str | None, task_file: Path | None) -> str:
    if bool(task) == bool(task_file):
        raise ValueError("provide exactly one of --task or --task-file")
    if task is not None:
        text = task
    else:
        path = task_file if task_file is not None else Path()
        if path.is_symlink():
            raise ValueError(f"refusing to read symlinked task file: {path}")
        if not path.is_file():
            raise ValueError(f"task file not found: {path}")
        text = path.read_text(encoding="utf-8")
    if len(text) > MAX_TASK_CHARS:
        raise ValueError(f"task text exceeds limit of {MAX_TASK_CHARS} characters")
    return text


def task_ref(args: Any) -> dict[str, Any]:
    if args.task_file:
        return {"kind": "task-file", "path": str(args.task_file), "sha256": sha256_file_text(args.task_file)}
    return {"kind": "inline-task", "sha256": sha256_text(args.task or "")}


def sha256_file_text(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8"))


def resolve_run_dir(root: Path, run_dir: Path | None, run_id: str) -> Path:
    if run_dir is not None:
        return run_dir
    return state_dir(root) / "delegation-runs" / run_id


def ensure_run_dir(root: Path, run_dir: Path) -> None:
    path = run_dir / "manifest.json"
    preflight_state_path(root, path)
    for child in ("profiles", "probes", "raw", "parsed", "validation"):
        child_path = run_dir / child / ".keep"
        preflight_state_path(root, child_path)
        child_path.parent.mkdir(parents=True, exist_ok=True)


def transport_manifest(plan: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "external-agent-transport-manifest.v1",
        "participants": [
            {
                "participant_id": item.get("participant_id"),
                "transport": item.get("transport"),
                "output_contract": item.get("output_contract"),
                "command_shape": item.get("command_shape"),
                "status": item.get("status"),
                "reason": item.get("reason"),
            }
            for item in plan
        ],
    }


def run_manifest(result: dict[str, Any], participants: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "external-agent-run-manifest.v1",
        "status": result.get("status"),
        "run_id": result.get("run_id"),
        "role": result.get("role"),
        "template": result.get("template"),
        "research": result.get("research"),
        "task_ref": result.get("task_ref"),
        "participants": [
            {
                "participant_id": item.get("participant_id"),
                "status": item.get("status"),
                "profile_ref": item.get("profile_ref"),
                "parsed_ref": item.get("parsed_ref"),
                "result_packet_ref": item.get("result_packet_ref"),
                "validation_ref": item.get("validation_ref"),
            }
            for item in participants
        ],
    }


def build_dispatch_plan(
    root: Path,
    platform: str,
    delegation: dict[str, Any],
    prechecks: dict[str, Any],
    requested: list[str],
    *,
    max_providers: int,
    research: bool,
    resolved_model: str | None,
    resolved_thinking: str | None,
    env: dict[str, str],
) -> list[dict[str, Any]]:
    provider_checks = {item["provider"]: item for item in prechecks["providers"]}
    providers = expand_auto_providers(requested, prechecks, max_providers)
    return [
        build_provider_dispatch_plan(
            root,
            platform,
            delegation,
            provider_checks,
            provider,
            research=research,
            resolved_model=resolved_model,
            resolved_thinking=resolved_thinking,
            env=env,
        )
        for provider in providers
    ]


def expand_auto_providers(requested: list[str], prechecks: dict[str, Any], max_providers: int) -> list[str]:
    if requested != ["auto"]:
        return requested
    candidates = [
        provider
        for provider in prechecks["policy"]["active_providers"]
        if provider in EXTERNAL_PROVIDERS
    ]
    return candidates[:max(1, max_providers)]


def build_provider_dispatch_plan(
    root: Path,
    platform: str,
    delegation: dict[str, Any],
    provider_checks: dict[str, dict[str, Any]],
    provider: str,
    *,
    research: bool,
    resolved_model: str | None,
    resolved_thinking: str | None,
    env: dict[str, str],
) -> dict[str, Any]:
    if provider == "codex":
        return {
            "provider": provider,
            "status": "codex-spawn-required",
            "reason": "Codex live work is launched by agent-group-discuss via spawn_agent, not this CLI subprocess adapter",
        }
    if provider not in provider_checks:
        return {"provider": provider, "status": "blocked", "reason": "provider is not declared in delegation policy"}
    check = provider_checks[provider]
    if provider not in EXTERNAL_PROVIDERS:
        return {"provider": provider, "status": "blocked", "reason": "provider is not an active external CLI provider"}
    if check["configured_status"] != "active":
        return {"provider": provider, "status": "blocked", "reason": "provider is not active"}

    command_template = dispatch_command(provider, root, platform, env)
    if command_template is None:
        return {"provider": provider, "status": "blocked", "reason": "dispatch CLI command missing"}
    model_profile = resolve_model_profile(provider, research, resolved_model, resolved_thinking, env)
    if model_profile["status"] != "ok":
        return {
            "provider": provider,
            "status": "blocked",
            "reason": model_profile["reason"],
            "research_model_policy": model_profile,
        }
    if research and not env.get(dispatch_command_env_name(provider)):
        return {
            "provider": provider,
            "status": "blocked",
            "reason": f"research dispatch requires {dispatch_command_env_name(provider)} so model/thinking selectors are explicit",
            "research_model_policy": model_profile,
        }
    command = render_model_placeholders(command_template, model_profile)
    spec = delegation["providers"][provider]
    return {
        "provider": provider,
        "status": "ready",
        "participant_id": f"{provider}-external-1",
        "recipient_profile": spec["recipient_profile"],
        "default_role_family": spec["default_role_family"],
        "command_shape": redacted_command(command),
        "command": command,
        "transport": "stdin",
        "output_contract": "json-envelope-final-marker",
        "research_model_policy": model_profile,
    }


def dispatch_command(provider: str, root: Path, platform: str, env: dict[str, str]) -> str | None:
    configured = env.get(dispatch_command_env_name(provider))
    if configured:
        return configured
    discovered = discover_tool(f"{provider}-cli", PROVIDER_CLI_SPECS[provider], platform, root)
    if discovered.get("status") not in {"ok", "degraded"}:
        return None
    command = discovered.get("command")
    return str(command) if command else None


def dispatch_command_env_name(provider: str) -> str:
    return f"AAS_{provider.upper()}_DISPATCH_COMMAND"


def resolve_model_profile(
    provider: str,
    research: bool,
    resolved_model: str | None,
    resolved_thinking: str | None,
    env: dict[str, str],
) -> dict[str, Any]:
    model = resolved_model or env.get(f"AAS_{provider.upper()}_LATEST_MODEL")
    thinking = resolved_thinking or env.get(f"AAS_{provider.upper()}_HIGHEST_THINKING")
    if research and (not model or not thinking):
        return {
            "status": "blocked",
            "policy": "latest_model_highest_reasoning_required",
            "reason": (
                f"research dispatch requires --resolved-model/--resolved-thinking "
                f"or AAS_{provider.upper()}_LATEST_MODEL and AAS_{provider.upper()}_HIGHEST_THINKING"
            ),
        }
    return {
        "status": "ok",
        "policy": "latest_model_highest_reasoning_required" if research else "not-required",
        "resolved_model": model or "not-specified",
        "resolved_thinking": thinking or "not-specified",
        "source": "argument-or-env" if (model or thinking) else "not-needed",
    }


def run_external_participant(
    root: Path,
    run_dir: Path,
    plan: dict[str, Any],
    task_text: str,
    *,
    role: str,
    template: str | None,
    timeout: int,
    env: dict[str, str],
) -> dict[str, Any]:
    participant_id = plan["participant_id"]
    profile = build_capability_profile(plan)
    write_json(root, run_dir / "profiles" / f"{participant_id}.json", profile)

    cmd_env = {**env, **provider_env_defaults(plan["provider"], env)}
    smoke_marker = f"AAS_FINAL_MARKER_{participant_id}_SMOKE"
    smoke = run_command(plan["command"], smoke_prompt(smoke_marker), timeout=timeout, env=cmd_env, final_marker=smoke_marker)
    write_probe(root, run_dir, participant_id, "smoke", smoke)
    smoke_validation = validate_command_output(smoke, smoke_marker)
    if smoke_validation["status"] != "ok":
        validation = {"status": "failed", "phase": "smoke", "errors": smoke_validation["errors"]}
        write_json(root, run_dir / "validation" / f"{participant_id}.json", validation)
        return participant_result(plan, run_dir, "failed", validation)

    task_marker = f"AAS_FINAL_MARKER_{participant_id}_TASK"
    prompt = participant_prompt(
        role=role,
        template=template,
        task_text=task_text,
        final_marker=task_marker,
        model_profile=plan["research_model_policy"],
    )
    completed = run_command(plan["command"], prompt, timeout=timeout, env=cmd_env, final_marker=task_marker)
    write_raw(root, run_dir, participant_id, completed, plan["command_shape"])
    parsed = parse_result_json(completed.stdout)
    write_json(root, run_dir / "parsed" / f"{participant_id}.json", parsed)
    normalized = normalize_result_packet(plan, parsed, task_packet_id=task_ref_id(task_text))
    write_json(root, run_dir / "parsed" / f"{participant_id}.result.json", normalized)
    validation = validate_command_output(completed, task_marker)
    validation["result_packet_validation"] = validate_result(normalized)
    if validation["result_packet_validation"]:
        validation["errors"].append("result_packet_contract_failed")
        validation["status"] = "failed"
    write_json(root, run_dir / "validation" / f"{participant_id}.json", validation)
    write_event_artifacts(root, run_dir, participant_id, completed, validation)
    write_evidence_map(root, run_dir, participant_id, parsed, validation)
    return participant_result(plan, run_dir, validation["status"], validation, parsed)


def build_capability_profile(plan: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "profile_id": f"{plan['provider']}-{now.strftime('%Y%m%d%H%M%S')}",
        "provider": plan["provider"],
        "cli_name": plan["provider"],
        "cli_version": "runtime-probed-redacted",
        "profile_source": "live-dispatch",
        "observed_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=24)).isoformat(),
        "cwd_assumptions": "selected root or caller cwd; task input is transported over stdin",
        "auth_status": "not_checked",
        "config_status": "not_checked",
        "input_transports_tested": ["stdin"],
        "output_modes_tested": ["json-envelope-final-marker"],
        "file_read_fidelity": "not_needed",
        "timeout_behavior": "checked",
        "truncation_status": "not_checked",
        "validated_capabilities": ["stdin", "json-envelope-final-marker"],
        "blocked_capabilities": [],
        "limitations": ["profile is valid only for this run and command shape"],
        "command_shape": plan["command_shape"],
        "research_model_policy": plan["research_model_policy"],
    }


def smoke_prompt(final_marker: str) -> str:
    return (
        "Return a minimal valid response for this external agent smoke test.\n"
        f"Use exactly this envelope:\n{RESULT_START}\n"
        "{\"status\":\"ok\",\"findings\":[],\"limitations\":[],\"warnings\":[]}\n"
        f"{RESULT_END}\n{final_marker}\n"
    )


def participant_prompt(
    *,
    role: str,
    template: str | None,
    task_text: str,
    final_marker: str,
    model_profile: dict[str, Any],
) -> str:
    template_line = template or "none"
    return (
        f"You are the {role} in a parent-owned cross-provider delegation run.\n"
        f"Template: {template_line}\n"
        f"Resolved model: {model_profile.get('resolved_model', 'not-specified')}\n"
        f"Resolved thinking: {model_profile.get('resolved_thinking', 'not-specified')}\n\n"
        "Task:\n"
        f"{task_text}\n\n"
        "Return only a JSON result inside this exact envelope. Do not include "
        "raw private prompts, credentials, hidden config, or unrelated files.\n"
        f"{RESULT_START}\n"
        "{\n"
        "  \"status\": \"ok | partial | failed\",\n"
        "  \"findings\": [\n"
        "    {\"id\": \"F1\", \"summary\": \"...\", \"evidence_refs\": [\"...\"]}\n"
        "  ],\n"
        "  \"limitations\": [],\n"
        "  \"warnings\": []\n"
        "}\n"
        f"{RESULT_END}\n"
        f"{final_marker}\n"
    )


def task_ref_id(task_text: str) -> str:
    return "task-" + sha256_text(task_text)[:12]


def normalize_result_packet(plan: dict[str, Any], parsed: dict[str, Any], *, task_packet_id: str) -> dict[str, Any]:
    raw_findings = parsed.get("findings", []) if isinstance(parsed, dict) else []
    findings = normalize_findings(raw_findings)
    evidence = normalize_evidence_from_findings(findings)
    status = normalize_result_status(parsed.get("status") if isinstance(parsed, dict) else None)
    participant_id = str(plan.get("participant_id", "external-participant"))
    profile_id = str(plan.get("recipient_profile", "external-cli-reviewer"))
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "result_id": f"{participant_id}-result",
        "task_packet_id": task_packet_id,
        "task_schema_version": TASK_SCHEMA_VERSION,
        "intended_recipient": "external-cli-participant",
        "adapter_spec_id": profile_id,
        "recipient_profile": {
            "profile_id": profile_id,
            "profile_version": "v1",
            "execution_status": "reference_only",
        },
        "produced_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "produced_by": "external-cli-participant",
        "provenance": [
            {
                "ref_id": "parent-task",
                "kind": "task",
                "source": "parent_dispatch_task",
                "sensitivity": "private",
                "access_note": "parent-owned task text outside result packet",
            }
        ],
        "status": status,
        "summary": normalize_summary(parsed),
        "coverage_scope": "bounded external participant output; parent validation required before use",
        "findings": findings,
        "evidence": evidence,
        "artifacts": [
            {
                "artifact_id": "A-parsed",
                "kind": "parsed-output",
                "ref_id": f"parsed/{participant_id}.json",
                "description": "Parent-owned parsed external participant output.",
            }
        ],
        "limitations": normalize_string_list(parsed.get("limitations") if isinstance(parsed, dict) else []),
        "warnings": normalize_diagnostics(parsed.get("warnings") if isinstance(parsed, dict) else []),
        "errors": normalize_diagnostics(parsed.get("errors") if isinstance(parsed, dict) else []),
        "parent_action_request": None,
        "next_step": "parent_decides" if status in {"completed", "partial"} else "discard",
    }


def normalize_result_status(value: Any) -> str:
    if value == "ok":
        return "completed"
    if value == "partial":
        return "partial"
    if value == "blocked":
        return "blocked"
    return "failed"


def normalize_summary(parsed: dict[str, Any] | None) -> str:
    if not isinstance(parsed, dict):
        return "No parseable result."
    summary = parsed.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    findings = parsed.get("findings")
    if isinstance(findings, list):
        return f"{len(findings)} finding(s) returned for parent validation."
    return "Parsed result returned for parent validation."


def normalize_findings(raw_findings: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_findings, list):
        return []
    findings: list[dict[str, Any]] = []
    for index, item in enumerate(raw_findings, start=1):
        if isinstance(item, dict):
            finding_id = str(item.get("id") or item.get("finding_id") or f"F{index}")
            summary = str(item.get("summary") or item.get("rationale") or "External participant finding.")
            evidence_refs = item.get("evidence_refs") if isinstance(item.get("evidence_refs"), list) else []
        else:
            finding_id = f"F{index}"
            summary = str(item)
            evidence_refs = []
        findings.append({
            "finding_id": finding_id,
            "severity": "info",
            "claim_or_object_ref": finding_id,
            "evidence_refs": [str(ref) for ref in evidence_refs],
            "confidence": "unknown",
            "validation_status": "unchecked",
            "rationale": summary,
            "recommended_parent_action": "validate evidence before use",
        })
    return findings


def normalize_evidence_from_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[str] = []
    for finding in findings:
        for ref in finding.get("evidence_refs", []):
            if ref not in refs:
                refs.append(ref)
    return [
        {
            "evidence_id": f"EV{index}",
            "ref_id": ref,
            "kind": "participant-cited-ref",
            "quote_or_summary": "Participant-supplied evidence ref; parent must validate.",
            "status": "unchecked",
            "evidence_disposition": "unchecked",
            "disposition_rationale": "External participant evidence is untrusted until parent validation.",
        }
        for index, ref in enumerate(refs, start=1)
    ]


def normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value in (None, ""):
        return []
    return [str(value)]


def normalize_diagnostics(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else ([] if value in (None, "") else [value])
    diagnostics = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, dict):
            diagnostics.append({
                "code": str(item.get("code") or f"D{index}"),
                "message": str(item.get("message") or item),
                "ref_id": item.get("ref_id") if item.get("ref_id") is None or isinstance(item.get("ref_id"), str) else str(item.get("ref_id")),
            })
        else:
            diagnostics.append({"code": f"D{index}", "message": str(item), "ref_id": None})
    return diagnostics


class CompletedCommand:
    def __init__(self, *, returncode: int, stdout: str, stderr: str, timed_out: bool) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out


def run_command(command: str, prompt: str, *, timeout: int, env: dict[str, str], final_marker: str) -> CompletedCommand:
    rendered = render_command_template(command, final_marker)
    try:
        parts = split_dispatch_command(rendered)
    except ValueError as exc:
        raise ValueError(f"invalid dispatch command: {exc}") from exc
    if not parts:
        raise ValueError("empty dispatch command")
    command_env = dict(env)
    command_env["AAS_DELEGATION_FINAL_MARKER"] = final_marker
    try:
        completed = subprocess.run(
            parts,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=command_env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CompletedCommand(
            returncode=124,
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=exc.stderr if isinstance(exc.stderr, str) else "",
            timed_out=True,
        )
    return CompletedCommand(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        timed_out=False,
    )


def split_dispatch_command(command: str) -> list[str]:
    parts = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        return [strip_wrapping_quotes(part) for part in parts]
    return parts


def strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def render_command_template(command: str, final_marker: str) -> str:
    return (
        command
        .replace("{final_marker}", shlex.quote(final_marker))
        .replace("{marker}", shlex.quote(final_marker))
    )


def render_model_placeholders(command: str, model_profile: dict[str, Any]) -> str:
    return (
        command
        .replace("{model}", shlex.quote(str(model_profile.get("resolved_model", "not-specified"))))
        .replace("{thinking}", shlex.quote(str(model_profile.get("resolved_thinking", "not-specified"))))
        .replace("{reasoning}", shlex.quote(str(model_profile.get("resolved_thinking", "not-specified"))))
    )


def parse_result_json(stdout: str) -> dict[str, Any]:
    match = re.search(
        rf"{re.escape(RESULT_START)}\s*(.*?)\s*{re.escape(RESULT_END)}",
        stdout,
        flags=re.DOTALL,
    )
    if not match:
        return {"status": "parse-failed", "errors": ["missing result envelope"]}
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        return {"status": "parse-failed", "errors": [f"invalid JSON: {exc}"]}
    if not isinstance(data, dict):
        return {"status": "parse-failed", "errors": ["result JSON must be an object"]}
    return data


def validate_command_output(completed: CompletedCommand, final_marker: str) -> dict[str, Any]:
    errors: list[str] = []
    if completed.timed_out:
        errors.append("timeout_no_final")
    if completed.returncode != 0:
        errors.append("nonzero_exit")
    if final_marker not in completed.stdout:
        errors.append("missing_final_marker")
    parsed = parse_result_json(completed.stdout)
    if parsed.get("status") == "parse-failed":
        errors.extend(parsed.get("errors", []))
    else:
        for field in ("status", "findings", "limitations", "warnings"):
            if field not in parsed:
                errors.append(f"missing_field:{field}")
        if "findings" in parsed and not isinstance(parsed["findings"], list):
            errors.append("field_not_list:findings")
        if "limitations" in parsed and not isinstance(parsed["limitations"], list):
            errors.append("field_not_list:limitations")
        if "warnings" in parsed and not isinstance(parsed["warnings"], list):
            errors.append("field_not_list:warnings")
    return {
        "status": "ok" if not errors else "failed",
        "errors": errors,
        "returncode": completed.returncode,
        "stdout_sha256": sha256_text(completed.stdout),
        "stderr_sha256": sha256_text(completed.stderr),
        "timed_out": completed.timed_out,
    }


def write_probe(root: Path, run_dir: Path, participant_id: str, name: str, completed: CompletedCommand) -> None:
    base = run_dir / "probes" / participant_id
    write_text(root, base / f"{name}-stdout.txt", completed.stdout)
    write_text(root, base / f"{name}-stderr.txt", completed.stderr)


def write_raw(root: Path, run_dir: Path, participant_id: str, completed: CompletedCommand, command_shape: str) -> None:
    base = run_dir / "raw" / participant_id
    write_text(root, base / "stdout.txt", completed.stdout)
    write_text(root, base / "stderr.txt", completed.stderr)
    write_text(root, base / "command-shape.txt", command_shape + "\n")


def write_event_artifacts(
    root: Path,
    run_dir: Path,
    participant_id: str,
    completed: CompletedCommand,
    validation: dict[str, Any],
) -> None:
    if completed.timed_out:
        append_jsonl(root, run_dir / "timeout_events.jsonl", {
            "participant_id": participant_id,
            "status": "timeout_no_final",
            "returncode": completed.returncode,
            "stdout_sha256": validation.get("stdout_sha256"),
            "stderr_sha256": validation.get("stderr_sha256"),
        })
    if "missing_final_marker" in validation.get("errors", []):
        append_jsonl(root, run_dir / "truncation_events.jsonl", {
            "participant_id": participant_id,
            "status": "missing_final_marker",
            "stdout_sha256": validation.get("stdout_sha256"),
            "stderr_sha256": validation.get("stderr_sha256"),
        })


def write_evidence_map(
    root: Path,
    run_dir: Path,
    participant_id: str,
    parsed: dict[str, Any],
    validation: dict[str, Any],
) -> None:
    findings = parsed.get("findings", []) if isinstance(parsed, dict) else []
    if not isinstance(findings, list):
        return
    for index, finding in enumerate(findings, start=1):
        finding_id = finding.get("id") if isinstance(finding, dict) else None
        append_jsonl(root, run_dir / "evidence-map.jsonl", {
            "participant_id": participant_id,
            "role": "external_cli",
            "parsed_finding_id": finding_id or f"finding-{index}",
            "validation_artifact": f"validation/{participant_id}.json",
            "source_artifact_refs": [f"parsed/{participant_id}.json"],
            "redaction_status": "raw_not_promoted",
            "parent_disposition": "pending_validation" if validation.get("status") == "ok" else "rejected",
            "target_evidence_id": None,
        })


def write_json(root: Path, path: Path, data: dict[str, Any]) -> None:
    write_text(root, path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def append_jsonl(root: Path, path: Path, data: dict[str, Any]) -> None:
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    write_text(root, path, existing + json.dumps(data, sort_keys=True) + "\n")


def write_text(root: Path, path: Path, text: str) -> None:
    preflight_state_path(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, text)


def participant_result(
    plan: dict[str, Any],
    run_dir: Path,
    status: str,
    validation: dict[str, Any],
    parsed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    participant_id = plan["participant_id"]
    result = {
        "provider": plan["provider"],
        "participant_id": participant_id,
        "status": status,
        "recipient_profile": plan["recipient_profile"],
        "command_shape": plan["command_shape"],
        "profile_ref": rel_ref(run_dir, run_dir / "profiles" / f"{participant_id}.json"),
        "parsed_ref": rel_ref(run_dir, run_dir / "parsed" / f"{participant_id}.json"),
        "validation_ref": rel_ref(run_dir, run_dir / "validation" / f"{participant_id}.json"),
        "validation": validation,
    }
    if parsed is not None and parsed.get("status") != "parse-failed":
        result["result"] = parsed
    return result


def rel_ref(base: Path, path: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.name


def plan_status(plan: list[dict[str, Any]]) -> str:
    if any(item["status"] == "ready" for item in plan):
        return "ready"
    if any(item["status"] == "codex-spawn-required" for item in plan):
        return "codex-spawn-required"
    return "blocked"


def dispatched_status(participants: list[dict[str, Any]]) -> str:
    statuses = [item.get("status") for item in participants]
    if statuses and all(status == "ok" for status in statuses):
        return "ok"
    if any(status == "ok" for status in statuses):
        return "partial"
    return "failed"


def public_dispatch_plan(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    public = []
    for item in plan:
        sanitized = {key: value for key, value in item.items() if key != "command"}
        public.append(sanitized)
    return public
