from __future__ import annotations

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from installer.ai_agents_skills.agents import detect_agents
from installer.ai_agents_skills.apply import apply_plan
from installer.ai_agents_skills.cli import main
from installer.ai_agents_skills.delegation import (
    PROVIDER_CLI_SPECS,
    PROVIDER_ENDPOINT_ENV_NAMES,
    build_external_agent_prechecks,
    endpoint_summary,
)
from installer.ai_agents_skills.delegation_dispatch import (
    EXTERNAL_PROVIDERS,
    build_capability_profile,
    build_dispatch_plan,
    default_dispatch_command,
    dispatch_external_agents,
    dispatch_command,
    ensure_run_dir,
    expand_auto_providers,
    resolve_grok_dispatch_command,
    probe_grok_remote_profile,
    provider_subprocess_options,
    provider_env_defaults,
    resolve_model_profile,
    resolve_run_dir,
    run_external_participant,
    run_command,
    validate_resolved_model_syntax_before_prechecks,
)
from installer.ai_agents_skills.discovery import split_command
from installer.ai_agents_skills.grok import (
    GROK_CLI_TOOL_SPEC,
    parse_grok_available_models,
    probe_grok_model_membership,
)
from installer.ai_agents_skills.delegation_packets import RESULT_FIELDS, TASK_FIELDS, validate_result, validate_task
from installer.ai_agents_skills.lifecycle import rollback, uninstall
from installer.ai_agents_skills.manifest import REPO_ROOT, load_manifests
from installer.ai_agents_skills.planner import build_plan
from installer.ai_agents_skills.runtime_smoke import selected_runtime_skills
from installer.ai_agents_skills.selectors import resolve_skills
from installer.ai_agents_skills.state import load_state
from installer.ai_agents_skills.target_prechecks import build_target_prechecks
from installer.ai_agents_skills.verify import verify


class Args:
    skill = None
    skills = None
    profile = None
    exclude = None
    no_skills = False
    artifact = None
    artifacts = None
    artifact_profile = None
    exclude_artifact = None
    with_deps = False
    install_mode = "auto"


SKILL = "cross-agent-delegation"
EXAMPLES = REPO_ROOT / "canonical" / "skills" / SKILL / "references" / "examples.md"
TEMPLATES = REPO_ROOT / "canonical" / "skills" / SKILL / "references" / "research-workflow-templates.md"
PROFILES = REPO_ROOT / "canonical" / "skills" / SKILL / "references" / "recipient-profiles.md"

REFERENCE_FILES = {
    "task-packet-contract.md",
    "result-packet-contract.md",
    "recipient-profiles.md",
    "research-workflow-templates.md",
    "research-workflow-integration.md",
    "safety.md",
    "examples.md",
}


def grok_profile_status_payload(
    status: str = "ready",
    *,
    model_id: str | None = "grok-current",
) -> dict[str, Any]:
    configured = status in {"ready", "degraded"}
    return {
        "schema_version": "grok-remote.profile-status.v1",
        "status": status,
        "profile_name": "default" if configured else None,
        "profile_sha256": "a" * 64 if configured else None,
        "release_id": "b" * 64 if configured else None,
        "grok_release_id": "sha256:" + "c" * 64 if configured else None,
        "model_id": model_id if configured else None,
        "eligible_rungs": ["vpn"] if configured else [],
        "missing_rungs": ["home:windows"] if status == "degraded" else [],
        "reason_code": {
            "ready": "ready",
            "degraded": "ready_with_missing_optional_rungs",
            "blocked": "active_profile_invalid",
            "unconfigured": "no_active_profile",
        }[status],
    }


def write_test_cli(path: Path, *, posix: str, windows: str) -> Path:
    actual = path.with_suffix(".cmd") if os.name == "nt" else path
    actual.write_text(windows if os.name == "nt" else posix, encoding="utf-8")
    actual.chmod(0o755)
    return actual


def command_executable(command: str) -> Path:
    """First argv of a rendered dispatch command (handles shlex.quote on Windows)."""
    parts = split_command(command)
    if not parts:
        raise AssertionError(f"empty dispatch command: {command!r}")
    return Path(parts[0])


def write_fake_grok_remote(path: Path) -> Path:
    return write_test_cli(
        path,
        posix=(
            "#!/bin/sh\n"
            "if [ \"$1\" = --help ]; then\n"
            "  printf '%s\\n' '  grok-remote doctor --json   report managed profile readiness'\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"$1\" = doctor ] && [ \"$2\" = --json ]; then\n"
            "  printf '%s\\n' \"$AAS_TEST_GROK_PROFILE_JSON\"\n"
            "  exit \"$AAS_TEST_GROK_PROFILE_EXIT\"\n"
            "fi\n"
            "exit 97\n"
        ),
        windows=(
            "@echo off\r\n"
            "if \"%~1\"==\"--help\" (\r\n"
            "  echo   grok-remote doctor --json   report managed profile readiness\r\n"
            "  exit /b 0\r\n"
            ")\r\n"
            "if \"%~1\"==\"doctor\" if \"%~2\"==\"--json\" (\r\n"
            "  echo %AAS_TEST_GROK_PROFILE_JSON%\r\n"
            "  exit /b %AAS_TEST_GROK_PROFILE_EXIT%\r\n"
            ")\r\n"
            "exit /b 97\r\n"
        ),
    )


def write_fake_bare_grok(
    path: Path,
    *,
    available_model: str = "grok-current",
    exit_code: int = 0,
) -> Path:
    return write_test_cli(
        path,
        posix=(
            "#!/bin/sh\n"
            "if [ \"$1\" = models ]; then\n"
            f"  printf '%s\\n' 'Default model: {available_model}'\n"
            "  printf '%s\\n' 'Available models:'\n"
            f"  printf '%s\\n' '* {available_model} (default)'\n"
            f"  exit {exit_code}\n"
            "fi\n"
            "exit 97\n"
        ),
        windows=(
            "@echo off\r\n"
            "if \"%~1\"==\"models\" (\r\n"
            f"  echo Default model: {available_model}\r\n"
            "  echo Available models:\r\n"
            f"  echo * {available_model} ^(default^)\r\n"
            f"  exit /b {exit_code}\r\n"
            ")\r\n"
            "exit /b 97\r\n"
        ),
    )


def write_marker_cli(path: Path, marker: Path) -> Path:
    return write_test_cli(
        path,
        posix=(
            "#!/bin/sh\n"
            f"printf '%s\\n' invoked > '{marker}'\n"
            "exit 99\n"
        ),
        windows=(
            "@echo off\r\n"
            f">\"{marker}\" echo invoked\r\n"
            "exit /b 99\r\n"
        ),
    )


def create_agent_homes(root: Path, *agents: str) -> None:
    for agent in agents:
        (root / f".{agent}").mkdir(parents=True, exist_ok=True)


def parse_fixtures() -> list[dict[str, Any]]:
    text = EXAMPLES.read_text(encoding="utf-8")
    pattern = re.compile(
        r"<!-- fixture: id=(?P<id>[-a-z0-9]+) kind=(?P<kind>task|result) "
        r"valid=(?P<valid>true|false) errors=(?P<errors>[-A-Z0-9_,]+|none) -->\n"
        r"```json\n(?P<body>.*?)\n```",
        re.S,
    )
    fixtures = []
    for match in pattern.finditer(text):
        errors = [] if match.group("errors") == "none" else match.group("errors").split(",")
        fixtures.append(
            {
                "id": match.group("id"),
                "kind": match.group("kind"),
                "valid": match.group("valid") == "true",
                "errors": sorted(errors),
                "packet": json.loads(match.group("body")),
            }
        )
    return fixtures


class CrossAgentDelegationManifestTests(unittest.TestCase):
    def test_manifest_profile_and_runtime_boundaries(self) -> None:
        manifests = load_manifests()
        spec = manifests["skills"]["skills"][SKILL]
        self.assertEqual(set(spec["supported_agents"]), {"codex", "claude", "deepseek", "opencode", "antigravity"})
        self.assertEqual(set(spec["profiles"]), {"multi-agent", "serious-research", "full-research"})
        self.assertEqual(spec["required_dependencies"], [])
        self.assertNotIn(SKILL, manifests["runtime"]["skills"])

        args = Args()
        args.profile = "multi-agent"
        self.assertEqual(
            set(resolve_skills(args, manifests)),
            {
                "agent-group-discuss",
                "autonomous-research-loop",
                "autonomous-research-loop-runtime",
                "decision-doubt-loop",
                "model-router",
                "prose",
                SKILL,
            },
        )
        args.profile = "research-core"
        self.assertNotIn(SKILL, resolve_skills(args, manifests))
        args.profile = "full-research"
        self.assertIn(SKILL, resolve_skills(args, manifests))
        args.profile = None
        self.assertNotIn(SKILL, resolve_skills(args, manifests))

        skill_text = (REPO_ROOT / "canonical" / "skills" / SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: cross-agent-delegation", skill_text)
        self.assertIn("This skill emits and validates delegation packets. It does not execute them.", skill_text)
        linked_refs = set(re.findall(r"`references/([^`]+\.md)`", skill_text))
        self.assertEqual(linked_refs, REFERENCE_FILES)
        for ref_name in REFERENCE_FILES:
            self.assertTrue((REPO_ROOT / "canonical" / "skills" / SKILL / "references" / ref_name).is_file(), ref_name)

        with self.assertRaises(ValueError):
            selected_runtime_skills(manifests, {SKILL})

    def test_recipient_profiles_cover_candidates_and_reference_only(self) -> None:
        text = PROFILES.read_text(encoding="utf-8")
        for profile in (
            "codex-like-coding-reviewer",
            "claude-like-research-reviewer",
            "deepseek-like-model-reviewer",
            "copilot-like-code-reviewer",
            "antigravity-like-code-reviewer",
            "grok-like-code-reviewer",
            "model-only-api-reviewer",
            "openclaw-host-reference",
        ):
            self.assertIn(profile, text)
        self.assertIn("may route to a live CodeWhale or DeepSeek-like CLI only", text)
        self.assertIn("capability probes satisfy the run policy", text)
        self.assertIn("OpenClaw is not a V1 `supported_agents` target", text)

    def test_delegation_auto_provider_boundary_excludes_axle_mcp_and_reference_targets(self) -> None:
        manifests = load_manifests()
        delegation = manifests["delegation"]
        active = set(delegation["policy"]["active_providers"])
        reference_only = set(delegation["policy"]["reference_only_providers"])
        axle_skill = manifests["skills"]["skills"]["axiom-axle-mcp"]
        lean_explore_skill = manifests["skills"]["skills"]["lean-explore-mcp"]

        self.assertEqual(EXTERNAL_PROVIDERS, {"claude", "deepseek", "copilot", "antigravity", "grok"})
        self.assertEqual(set(PROVIDER_CLI_SPECS), {"claude", "deepseek", "copilot", "antigravity", "grok"})
        self.assertFalse(active.intersection({"axiom-axle-mcp", "axle", "lean-explore-mcp", "lean-explore", "mcp", "openclaw"}))
        self.assertEqual(reference_only, {"openclaw"})
        self.assertEqual(set(axle_skill["profiles"]), {"formal-research-remote", "full-research"})
        self.assertEqual(set(lean_explore_skill["profiles"]), {"formal-research", "formal-research-remote", "full-research"})
        self.assertNotIn("axiom-axle-mcp", delegation["providers"])
        self.assertNotIn("lean-explore-mcp", delegation["providers"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prechecks = build_external_agent_prechecks(root, "linux", delegation, env={})
            auto_providers = expand_auto_providers(["auto"], prechecks, max_providers=10)
            self.assertEqual(auto_providers, ["claude", "deepseek", "copilot", "antigravity", "grok"])

            explicit_plan = build_dispatch_plan(
                root,
                "linux",
                delegation,
                prechecks,
                ["openclaw", "axle", "lean-explore", "mcp"],
                max_providers=10,
                research=True,
                resolved_model="current-frontier",
                resolved_thinking="xhigh",
                env={},
            )
            reasons = {item["provider"]: item["reason"] for item in explicit_plan}
            self.assertEqual(reasons["openclaw"], "provider is not an active external CLI provider")
            self.assertEqual(reasons["axle"], "provider is not declared in delegation policy")
            self.assertEqual(reasons["lean-explore"], "provider is not declared in delegation policy")
            self.assertEqual(reasons["mcp"], "provider is not declared in delegation policy")

    def test_agd_docs_require_parent_owned_artifacts_evidence_mapping_and_redaction(self) -> None:
        skill_text = (REPO_ROOT / "canonical" / "skills" / "agent-group-discuss" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        execution_text = (REPO_ROOT / "canonical" / "skills" / "agent-group-discuss" / "EXECUTION.md").read_text(
            encoding="utf-8"
        )
        external_text = (
            REPO_ROOT
            / "canonical"
            / "skills"
            / "agent-group-discuss"
            / "references"
            / "external-cli-agents.md"
        ).read_text(encoding="utf-8")

        for text in (skill_text, execution_text, external_text):
            self.assertIn("parent-owned", text)
            self.assertIn("evidence mapping", text)
            self.assertIn("redaction", text)
        self.assertIn("research `evidence.jsonl`", execution_text)
        self.assertIn('evidence_type: "agd_result"', execution_text)
        self.assertNotIn('kind: "agd_result"', execution_text)
        self.assertIn("recovery", skill_text)
        self.assertIn("stale capability profile", external_text)


class CrossAgentDelegationFixtureTests(unittest.TestCase):
    def test_examples_use_canonical_fixture_metadata_and_validate(self) -> None:
        fixtures = parse_fixtures()
        self.assertGreaterEqual(len(fixtures), 6)
        self.assertEqual(len({fixture["id"] for fixture in fixtures}), len(fixtures))
        self.assertEqual(EXAMPLES.read_text(encoding="utf-8").count("```json"), len(fixtures))

        for fixture in fixtures:
            packet = fixture["packet"]
            actual_errors = validate_task(packet) if fixture["kind"] == "task" else validate_result(packet)
            if fixture["valid"]:
                self.assertEqual(actual_errors, [], fixture["id"])
                self.assertEqual(fixture["errors"], [])
            else:
                self.assertEqual(actual_errors, fixture["errors"], fixture["id"])

    def test_task_contract_rejects_missing_required_fields_and_bad_enums(self) -> None:
        packet = deepcopy(next(fixture["packet"] for fixture in parse_fixtures() if fixture["id"] == "valid-inert-task"))
        for field in TASK_FIELDS:
            mutated = deepcopy(packet)
            mutated.pop(field, None)
            self.assertIn("MISSING_REQUIRED_FIELD", validate_task(mutated), field)

        bad_schema = deepcopy(packet)
        bad_schema["schema_version"] = "cross-agent-delegation.task.v0"
        self.assertIn("TASK_SCHEMA_VERSION_INVALID", validate_task(bad_schema))

        bad_profile = deepcopy(packet)
        bad_profile["recipient_profile"]["profile_version"] = "v2"
        self.assertIn("PROFILE_VERSION_INVALID", validate_task(bad_profile))

        bad_confirmation = deepcopy(packet)
        bad_confirmation["confirmation_requirement"] = "agent_may_execute"
        self.assertIn("CONFIRMATION_REQUIREMENT_INVALID", validate_task(bad_confirmation))

        bad_failure_policy = deepcopy(packet)
        bad_failure_policy["failure_policy"] = "retry_until_success"
        self.assertIn("FAILURE_POLICY_INVALID", validate_task(bad_failure_policy))

    def test_result_contract_rejects_missing_required_fields_and_bad_enums(self) -> None:
        packet = deepcopy(next(fixture["packet"] for fixture in parse_fixtures() if fixture["id"] == "valid-partial-result"))
        for field in RESULT_FIELDS:
            mutated = deepcopy(packet)
            mutated.pop(field, None)
            self.assertIn("MISSING_REQUIRED_FIELD", validate_result(mutated), field)

        bad_schema = deepcopy(packet)
        bad_schema["schema_version"] = "cross-agent-delegation.result.v0"
        self.assertIn("RESULT_SCHEMA_VERSION_INVALID", validate_result(bad_schema))

        bad_task_schema = deepcopy(packet)
        bad_task_schema["task_schema_version"] = "cross-agent-delegation.task.v0"
        self.assertIn("TASK_SCHEMA_VERSION_INVALID", validate_result(bad_task_schema))

        bad_status = deepcopy(packet)
        bad_status["status"] = "executed"
        self.assertIn("RESULT_STATUS_INVALID", validate_result(bad_status))

        bad_next_step = deepcopy(packet)
        bad_next_step["next_step"] = "agent_continues"
        self.assertIn("RESULT_NEXT_STEP_INVALID", validate_result(bad_next_step))

        bad_provenance = deepcopy(packet)
        bad_provenance["provenance"] = [{"ref_id": "src-1", "source": "paper summary"}]
        self.assertIn("MISSING_REQUIRED_FIELD", validate_result(bad_provenance))

        bad_disposition = deepcopy(packet)
        bad_disposition["evidence"][0]["evidence_disposition"] = "parent_accepted"
        self.assertIn("EVIDENCE_DISPOSITION_INVALID", validate_result(bad_disposition))

    def test_contract_rejects_nested_runtime_authority_fields(self) -> None:
        task_packet = deepcopy(next(fixture["packet"] for fixture in parse_fixtures() if fixture["id"] == "valid-inert-task"))
        result_packet = deepcopy(next(fixture["packet"] for fixture in parse_fixtures() if fixture["id"] == "valid-partial-result"))
        forbidden_mutations = [
            (task_packet, ("expected_output", "commands"), ["claude -p prompt"]),
            (task_packet, ("recipient_capability_snapshot", "provider_configs"), {"claude": "configured"}),
            (task_packet, ("artifact_refs", 0, "session_id"), "session-123"),
            (result_packet, ("findings", 0, "accepted_by_parent"), True),
            (result_packet, ("evidence", 0, "participant_probe_status"), "passed"),
            (result_packet, ("artifacts", 0, "env"), {"TOKEN": "redacted"}),
        ]

        for packet, path, value in forbidden_mutations:
            mutated = deepcopy(packet)
            target = mutated
            for key in path[:-1]:
                if isinstance(key, int):
                    while len(target) <= key:
                        target.append({})
                    target = target[key]
                else:
                    if key not in target:
                        target[key] = [] if isinstance(path[path.index(key) + 1], int) else {}
                    target = target[key]
            target[path[-1]] = value
            errors = validate_task(mutated) if packet is task_packet else validate_result(mutated)
            self.assertIn("FORBIDDEN_AUTHORITY_FIELD", errors, path)

    def test_budget_constraints_use_closed_grammar_and_parent_bounds(self) -> None:
        packet = deepcopy(next(fixture["packet"] for fixture in parse_fixtures() if fixture["id"] == "valid-inert-task"))
        packet["constraints"] = [
            "Ordinary inert review constraint.",
            "model_policy=same_resolved_model; reasoning=parent_required_highest_available",
            "max_depth=1",
            "max_hops=2",
        ]
        packet["scope_constraints"] = [
            "max_tokens=50000",
            "max_usd=25.00",
            "budget_policy_ref=researchPolicy#default",
        ]
        self.assertEqual(validate_task(packet), [])

        duplicate = deepcopy(packet)
        duplicate["scope_constraints"].append("max_depth=1")
        self.assertIn("DUPLICATE_BUDGET_CONSTRAINT", validate_task(duplicate))

        invalid_owner = deepcopy(packet)
        invalid_owner["constraints"] = ["parent_budget_owner=child-agent"]
        invalid_owner["scope_constraints"] = []
        self.assertIn("BUDGET_CONSTRAINT_INVALID", validate_task(invalid_owner))

        malformed_cases = {
            "model_policy=same_resolved_model": "BUDGET_CONSTRAINT_INVALID",
            "max_depth=-1": "BUDGET_CONSTRAINT_INVALID",
            "max_hops=0": "BUDGET_CONSTRAINT_INVALID",
            "max_tokens=0": "BUDGET_CONSTRAINT_INVALID",
            "max_usd=1.234": "BUDGET_CONSTRAINT_INVALID",
            "budget_policy_ref=../policy": "BUDGET_CONSTRAINT_INVALID",
            "budget_policy_ref=https://example.test/policy": "BUDGET_CONSTRAINT_INVALID",
            "budget_policy_ref=policy?x=1": "BUDGET_CONSTRAINT_INVALID",
            "budget_policy_ref=$POLICY": "BUDGET_CONSTRAINT_INVALID",
            "budget_policy_ref=bad policy": "BUDGET_CONSTRAINT_INVALID",
            "max_depth=2": "BUDGET_CONSTRAINT_EXCEEDS_PARENT_POLICY",
            "max_hops=5": "BUDGET_CONSTRAINT_EXCEEDS_PARENT_POLICY",
            "max_tokens=100001": "BUDGET_CONSTRAINT_EXCEEDS_PARENT_POLICY",
            "max_usd=100.01": "BUDGET_CONSTRAINT_EXCEEDS_PARENT_POLICY",
        }
        for constraint, expected_error in malformed_cases.items():
            mutated = deepcopy(packet)
            mutated["constraints"] = [constraint]
            mutated["scope_constraints"] = []
            self.assertIn(expected_error, validate_task(mutated), constraint)

    def test_task_and_result_reject_recursive_budget_model_and_secret_material(self) -> None:
        task_packet = deepcopy(next(fixture["packet"] for fixture in parse_fixtures() if fixture["id"] == "valid-inert-task"))
        result_packet = deepcopy(next(fixture["packet"] for fixture in parse_fixtures() if fixture["id"] == "valid-partial-result"))

        runtime_key = deepcopy(task_packet)
        runtime_key["expected_output"]["metadata"] = {"budget_spent": {"spent_usd": "0.01"}}
        self.assertIn("FORBIDDEN_RUNTIME_STATE_FIELD", validate_task(runtime_key))

        model_key = deepcopy(task_packet)
        model_key["recipient_capability_snapshot"] = {"nested": [{"resolved_model": "gpt-example"}]}
        self.assertIn("FORBIDDEN_MODEL_POLICY_FIELD", validate_task(model_key))

        secret_key = deepcopy(task_packet)
        secret_key["expected_output"] = {"metadata": {"api_key": "redacted"}}
        self.assertIn("SECRET_MATERIAL", validate_task(secret_key))

        secret_value = deepcopy(task_packet)
        secret_value["audit_notes"] = ["Bearer " + "abcdefghijklmnop123456"]
        self.assertIn("SECRET_MATERIAL", validate_task(secret_value))

        runtime_result = deepcopy(result_packet)
        runtime_result["findings"][0]["budget_owner"] = "child-agent"
        self.assertIn("FORBIDDEN_RUNTIME_STATE_FIELD", validate_result(runtime_result))

        model_result = deepcopy(result_packet)
        model_result["evidence"][0]["resolved_thinking"] = "xhigh"
        self.assertIn("FORBIDDEN_MODEL_POLICY_FIELD", validate_result(model_result))

        secret_result = deepcopy(result_packet)
        secret_result["artifacts"].append(
            {
                "artifact_id": "A-secret",
                "kind": "note",
                "ref_id": "artifact:secret",
                "description": "github" + "_pat_" + "abcdefghijklmnopqrstuvwxyz",
            }
        )
        self.assertIn("SECRET_MATERIAL", validate_result(secret_result))

    def test_cli_validate_delegation_packet_reports_errors(self) -> None:
        packet = deepcopy(next(fixture["packet"] for fixture in parse_fixtures() if fixture["id"] == "valid-inert-task"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task.json"
            path.write_text(json.dumps(packet), encoding="utf-8")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main(["--json", "validate-delegation-packet", "--kind", "task", "--file", str(path)])
            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["errors"], [])

            packet["confirmation_requirement"] = "agent_may_execute"
            path.write_text(json.dumps(packet), encoding="utf-8")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main(["--json", "validate-delegation-packet", "--kind", "task", "--file", str(path)])
            self.assertEqual(code, 1)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["status"], "failed")
            self.assertIn("CONFIRMATION_REQUIREMENT_INVALID", payload["errors"])

    def test_examples_are_portable_and_secret_safe(self) -> None:
        text = EXAMPLES.read_text(encoding="utf-8")
        forbidden_patterns = [
            r"/home/",
            r"/Users/",
            r"[A-Za-z]:\\",
            r"\\\\",
            r"/mnt/[A-Za-z]/",
            r"%USERPROFILE%",
            r"\$HOME",
            r"~",
            r"\bsk-[A-Za-z0-9]{12,}\b",
            r"Authorization:",
            r"BEGIN [A-Z ]*PRIVATE KEY",
        ]
        for pattern in forbidden_patterns:
            self.assertIsNone(re.search(pattern, text), pattern)

    def test_research_templates_have_required_ids_and_contract_terms(self) -> None:
        text = TEMPLATES.read_text(encoding="utf-8")
        template_ids = [
            "literature-scout-review",
            "citation-integrity-check",
            "counterexample-search",
            "proof-gap-review",
            "methodology-critique",
            "result-synthesis-review",
            "formalization-readiness-check",
            "reproducibility-audit",
            "cross-provider-research-panel",
            "manager-worker-research-review",
            "repo-comparison-research",
            "evidence-synthesis-critique",
        ]
        for template_id in template_ids:
            self.assertEqual(text.count(f"### {template_id}"), 1, template_id)
        for term in (
            "unverified_leads",
            "source ID policy",
            "claim-to-source mappings",
            "supported`, `unsupported`, `unchecked`, or `contradicted",
            "finding_id",
            "recommended_parent_action",
        ):
            self.assertIn(term, text)


class CrossAgentDelegationInstallerTests(unittest.TestCase):
    def test_auto_plan_artifact_boundary_and_install_modes(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex", "claude", "deepseek")
            plan = build_plan(
                root,
                manifests,
                [SKILL],
                detect_agents(root),
                runtime_profile="none",
            )
            artifact_types = {action.get("artifact_type") for action in plan["actions"]}
            self.assertLessEqual(artifact_types, {"skill-file", "skill-support-file", "instruction-block"})
            self.assertNotIn("runtime-file", artifact_types)
            skill_files = {
                action["agent"]: action
                for action in plan["actions"]
                if action.get("artifact_type") == "skill-file"
            }
            self.assertEqual(set(skill_files), {"codex", "claude", "deepseek"})
            self.assertEqual(skill_files["codex"]["install_mode"], "reference")
            self.assertEqual(skill_files["claude"]["install_mode"], "symlink")
            self.assertEqual(skill_files["deepseek"]["install_mode"], "reference")
            support_agents = {
                action["agent"]
                for action in plan["actions"]
                if action.get("artifact_type") == "skill-support-file"
            }
            self.assertEqual(support_agents, {"claude"})
            support_relpaths = {
                str(Path(action["path"]).relative_to(root / f".{action['agent']}" / "skills" / SKILL)).replace("\\", "/")
                for action in plan["actions"]
                if action.get("artifact_type") == "skill-support-file"
            }
            self.assertEqual(support_relpaths, {f"references/{name}" for name in REFERENCE_FILES})

    def test_forced_reference_and_copy_modes_are_artifact_placement_only(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex", "claude", "deepseek")
            reference_plan = build_plan(
                root,
                manifests,
                [SKILL],
                detect_agents(root),
                install_mode="reference",
                runtime_profile="none",
            )
            self.assertFalse([
                action for action in reference_plan["actions"]
                if action.get("artifact_type") == "skill-support-file"
            ])

            copy_plan = build_plan(
                root,
                manifests,
                [SKILL],
                detect_agents(root),
                install_mode="copy",
                runtime_profile="none",
            )
            support_agents = {
                action["agent"]
                for action in copy_plan["actions"]
                if action.get("artifact_type") == "skill-support-file"
            }
            self.assertEqual(support_agents, {"codex", "claude", "deepseek"})
            self.assertFalse([
                action for action in copy_plan["actions"]
                if action.get("artifact_type") == "runtime-file"
            ])

    def test_lifecycle_uninstall_and_rollback_are_scoped(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex", "claude", "deepseek")
            plan = build_plan(root, manifests, [SKILL], detect_agents(root), runtime_profile="none")
            apply_plan(root, plan, dry_run=False)
            self.assertEqual(verify(root)["status"], "ok")
            state_text = json.dumps(load_state(root))
            for forbidden in ("task-valid-inert-001", "raw prompts", "delegated output"):
                self.assertNotIn(forbidden, state_text)

            dry = uninstall(root, skills={SKILL}, dry_run=True)
            self.assertTrue(dry["dry_run"])
            applied = uninstall(root, skills={SKILL}, dry_run=False)
            self.assertFalse(applied["dry_run"])
            self.assertEqual(verify(root)["status"], "no-managed-artifacts")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex", "claude", "deepseek")
            plan = build_plan(root, manifests, [SKILL], detect_agents(root), runtime_profile="none")
            result = apply_plan(root, plan, dry_run=False)
            dry = rollback(root, run_id=result["run_id"], dry_run=True)
            self.assertTrue(dry["dry_run"])
            applied = rollback(root, run_id=result["run_id"], dry_run=False)
            self.assertTrue(applied["restored"])
            self.assertEqual(verify(root)["status"], "no-managed-artifacts")

    def test_openclaw_explicit_target_fails_closed_without_v1_artifacts(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "openclaw")
            plan = build_plan(
                root,
                manifests,
                [SKILL],
                detect_agents(root, ["openclaw"]),
                runtime_profile="none",
                requested_agents=["openclaw"],
            )
            self.assertTrue(plan["actions"] or plan["skipped_agents"])
            active = [
                action for action in plan["actions"]
                if action.get("classification") != "blocked" and action.get("operation") != "skip"
            ]
            self.assertEqual(active, [])
            artifact_types = {action.get("artifact_type") for action in plan["actions"]}
            self.assertNotIn("instruction-block", artifact_types)
            self.assertNotIn("runtime-file", artifact_types)
            self.assertFalse(any(action.get("artifact_type") == "target-support-file" for action in plan["actions"]))


class DeepSeekEndpointDispatchTests(unittest.TestCase):
    def test_endpoint_summary_flags_missing_deepseek_base_url(self):
        self.assertEqual(PROVIDER_ENDPOINT_ENV_NAMES["deepseek"], ("DEEPSEEK_BASE_URL",))
        missing = endpoint_summary("deepseek", {})
        self.assertEqual(missing["status"], "not-detected")
        self.assertEqual(missing["endpoint_sources"][0]["name"], "DEEPSEEK_BASE_URL")
        self.assertIn("DEEPSEEK_BASE_URL", missing["reason"])
        present = endpoint_summary("deepseek", {"DEEPSEEK_BASE_URL": "https://api.deepseek.com"})
        self.assertEqual(present["status"], "env-present")

    def test_endpoint_summary_not_needed_for_other_providers(self):
        self.assertEqual(endpoint_summary("claude", {})["status"], "not-needed")
        self.assertEqual(endpoint_summary("copilot", {})["status"], "not-needed")
        self.assertEqual(endpoint_summary("antigravity", {})["status"], "not-needed")
        self.assertEqual(endpoint_summary("grok", {})["status"], "not-needed")

    def test_provider_env_defaults_supplies_deepseek_base_url(self):
        self.assertEqual(
            provider_env_defaults("deepseek", {}),
            {"DEEPSEEK_BASE_URL": "https://api.deepseek.com"},
        )
        # Never override a caller-provided value.
        self.assertEqual(
            provider_env_defaults("deepseek", {"DEEPSEEK_BASE_URL": "https://proxy.example"}), {}
        )
        # Other providers get nothing. In particular, Grok routing and
        # multi-session selection belong to grok-remote's managed profile.
        self.assertEqual(provider_env_defaults("claude", {}), {})
        self.assertEqual(provider_env_defaults("antigravity", {}), {})
        self.assertEqual(provider_env_defaults("grok", {}), {})
        self.assertEqual(
            provider_env_defaults("grok", {"GROK_MULTI_SESSION": "0"}),
            {},
        )

    def test_antigravity_dispatch_uses_agy_print_without_ls_address(self):
        self.assertEqual(default_dispatch_command("antigravity", "agy"), "agy --print")

        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            write_test_cli(
                bin_dir / "agy",
                posix="#!/bin/sh\nprintf 'agy test cli\\n'\n",
                windows="@echo off\r\necho agy test cli\r\n",
            )

            old_path = os.environ.get("PATH", "")
            old_ls = os.environ.pop("ANTIGRAVITY_LS_ADDRESS", None)
            os.environ["PATH"] = str(bin_dir)
            try:
                prechecks = build_external_agent_prechecks(root, "linux", manifests["delegation"], env={})
                by_provider = {item["provider"]: item for item in prechecks["providers"]}
                self.assertIn("antigravity", by_provider)
                self.assertIn(
                    by_provider["antigravity"]["status"],
                    {"runtime-probe-required", "degraded-runtime-probe-required"},
                )
                plan = build_dispatch_plan(
                    root,
                    "linux",
                    manifests["delegation"],
                    prechecks,
                    ["antigravity"],
                    max_providers=1,
                    research=False,
                    resolved_model=None,
                    resolved_thinking=None,
                    env={},
                )
            finally:
                os.environ["PATH"] = old_path
                if old_ls is not None:
                    os.environ["ANTIGRAVITY_LS_ADDRESS"] = old_ls

            self.assertEqual(plan[0]["status"], "ready")
            self.assertEqual(plan[0]["provider"], "antigravity")
            self.assertIn("--print", plan[0]["command"])
            self.assertNotIn("ANTIGRAVITY_LS_ADDRESS", json.dumps(plan[0]))

    def test_grok_dispatch_uses_prompt_file_and_oidc_session(self):
        self.assertEqual(default_dispatch_command("grok", "grok"), "grok --prompt-file /dev/stdin")
        self.assertEqual(
            default_dispatch_command("grok", "/usr/local/bin/grok-remote"),
            "/usr/local/bin/grok-remote --prompt-file /dev/stdin",
        )
        self.assertEqual(
            default_dispatch_command("grok", "grok-remote --host windows"),
            "grok-remote --host windows --prompt-file /dev/stdin",
        )
        self.assertEqual(
            dispatch_command(
                "grok",
                REPO_ROOT,
                "linux",
                {"AAS_GROK_DISPATCH_COMMAND": "grok-remote --prompt-file /dev/stdin"},
            ),
            "grok-remote --prompt-file /dev/stdin",
        )
        self.assertEqual(
            dispatch_command(
                "grok",
                REPO_ROOT,
                "linux",
                {"AAS_GROK_DISPATCH_COMMAND": "grok-remote --ios iphone-xr --prompt-file /dev/stdin"},
            ),
            "grok-remote --ios iphone-xr --prompt-file /dev/stdin",
        )
        self.assertEqual(
            dispatch_command(
                "grok",
                REPO_ROOT,
                "linux",
                {"AAS_GROK_DISPATCH_COMMAND": "grok-remote --vpn --prompt-file /dev/stdin"},
            ),
            "grok-remote --vpn --prompt-file /dev/stdin",
        )

        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            write_test_cli(
                bin_dir / "grok",
                posix="#!/bin/sh\nprintf 'grok test cli\\n'\n",
                windows="@echo off\r\necho grok test cli\r\n",
            )

            old_path = os.environ.get("PATH", "")
            old_aas = os.environ.pop("AAS_GROK", None)
            os.environ["PATH"] = str(bin_dir)
            try:
                prechecks = build_external_agent_prechecks(root, "linux", manifests["delegation"], env={})
                by_provider = {item["provider"]: item for item in prechecks["providers"]}
                self.assertIn("grok", by_provider)
                self.assertIn(
                    by_provider["grok"]["status"],
                    {"runtime-probe-required", "degraded-runtime-probe-required"},
                )
                # Grok uses an interactive OIDC session, not an API-key env var.
                self.assertEqual(by_provider["grok"]["auth"]["status"], "not-detected")
                plan = build_dispatch_plan(
                    root,
                    "linux",
                    manifests["delegation"],
                    prechecks,
                    ["grok"],
                    max_providers=1,
                    research=False,
                    resolved_model=None,
                    resolved_thinking=None,
                    env={},
                )
            finally:
                os.environ["PATH"] = old_path
                if old_aas is not None:
                    os.environ["AAS_GROK"] = old_aas

            self.assertEqual(plan[0]["status"], "ready")
            self.assertEqual(plan[0]["provider"], "grok")
            self.assertIn("--prompt-file", plan[0]["command"])

    def test_grok_model_probe_requires_exact_anchored_available_row(self):
        linux_candidates = GROK_CLI_TOOL_SPEC["candidates"]["linux"]
        self.assertIn("grok", linux_candidates)
        self.assertNotIn("grok-remote", linux_candidates)
        output = "\n".join(
            [
                "Default model: grok-4.5",
                "Available models:",
                "  * grok-4.5 (default)",
                "note: grok-4.6 is coming soon",
                "* grok-4.50",
            ]
        )
        self.assertEqual(parse_grok_available_models(output), ["grok-4.5", "grok-4.50"])
        with tempfile.TemporaryDirectory() as tmp:
            bare = write_fake_bare_grok(Path(tmp) / "grok", available_model="grok-4.50")
            probe = probe_grok_model_membership(str(bare), "grok-4.5", os.environ.copy())
        self.assertEqual(probe["status"], "not-confirmed")
        self.assertEqual(probe["reason_code"], "resolved_model_not_listed")
        self.assertEqual(probe["available_models"], ["grok-4.50"])

    @unittest.skipUnless(os.name == "posix", "POSIX umask behavior")
    def test_grok_model_probe_uses_private_posix_umask(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            home.mkdir()
            bare = write_test_cli(
                root / "grok",
                posix=(
                    "#!/bin/sh\n"
                    "mkdir -p \"$HOME/.grok\"\n"
                    ": > \"$HOME/.grok/models_cache.json\"\n"
                    "printf '%s\\n' '* grok-4.5 (default)'\n"
                ),
                windows="@echo off\r\nexit /b 97\r\n",
            )
            previous_umask = os.umask(0o002)
            try:
                probe = probe_grok_model_membership(
                    str(bare),
                    "grok-4.5",
                    {**os.environ, "HOME": str(home)},
                )
            finally:
                os.umask(previous_umask)
            cache = home / ".grok" / "models_cache.json"
            self.assertEqual(probe["status"], "confirmed")
            self.assertEqual(cache.stat().st_mode & 0o777, 0o600)

    def test_grok_invalid_resolved_model_blocks_before_any_cli_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            marker = root / "remote-invoked"
            write_marker_cli(bin_dir / "grok-remote", marker)
            env = {**os.environ, "PATH": str(bin_dir)}
            with patch.dict(
                os.environ,
                {"PATH": str(bin_dir), "AAS_GROK": ""},
                clear=False,
            ):
                selection = resolve_grok_dispatch_command(root, "linux", env, "_invalid")
            self.assertEqual(selection["status"], "blocked")
            self.assertEqual(selection["model_probe"]["reason_code"], "resolved_model_invalid")
            self.assertFalse(marker.exists())
            profile = resolve_model_profile("grok", True, "_invalid", "high", env)
            self.assertEqual(profile["status"], "blocked")
            self.assertIn("model ID is invalid", profile["reason"])

    def test_grok_invalid_model_top_level_dispatch_executes_no_cli(self):
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            bare_marker = root / "bare-invoked"
            remote_marker = root / "remote-invoked"
            write_marker_cli(bin_dir / "grok", bare_marker)
            write_marker_cli(bin_dir / "grok-remote", remote_marker)
            args = SimpleNamespace(
                root=root,
                platform="linux",
                task="review this claim",
                task_file=None,
                providers=None,
                provider="grok",
                resolved_model="_invalid",
            )
            with patch.dict(
                os.environ,
                {
                    "PATH": str(bin_dir),
                    "AAS_GROK": "",
                    "AAS_GROK_DISPATCH_COMMAND": "",
                },
                clear=False,
            ):
                with self.assertRaisesRegex(ValueError, "resolved Grok model ID is invalid"):
                    dispatch_external_agents(args, manifests)
            self.assertFalse(bare_marker.exists())
            self.assertFalse(remote_marker.exists())

    def test_precheck_model_syntax_gate_does_not_apply_grok_rules_to_other_providers(self):
        validate_resolved_model_syntax_before_prechecks(["claude"], "_invalid", {})

    def test_grok_generic_precheck_is_bare_only_on_remote_only_path(self):
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            marker = root / "remote-invoked"
            write_marker_cli(bin_dir / "grok-remote", marker)
            env = {**os.environ, "PATH": str(bin_dir)}
            with patch.dict(
                os.environ,
                {"PATH": str(bin_dir), "AAS_GROK": ""},
                clear=False,
            ):
                prechecks = build_external_agent_prechecks(
                    root,
                    "linux",
                    manifests["delegation"],
                    env=env,
                )
                target_prechecks = build_target_prechecks(root, "linux", ["grok"], [])
            grok = next(item for item in prechecks["providers"] if item["provider"] == "grok")
            self.assertEqual(grok["cli"]["status"], "missing")
            self.assertEqual(target_prechecks[0]["cli"]["status"], "missing")
            self.assertFalse(marker.exists())

    def test_grok_auto_resolution_confirms_bare_before_remote_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            bare = write_fake_bare_grok(bin_dir / "grok", available_model="grok-4.5")
            write_fake_grok_remote(bin_dir / "grok-remote")
            with patch.dict(os.environ, {"PATH": str(bin_dir)}, clear=False):
                selection = resolve_grok_dispatch_command(
                    root,
                    "linux",
                    {**os.environ, "PATH": str(bin_dir)},
                    "grok-4.5",
                )
            self.assertEqual(selection["status"], "ok")
            self.assertEqual(selection["source"], "bare-model-confirmed")
            self.assertEqual(selection["model_probe"]["status"], "confirmed")
            self.assertEqual(command_executable(selection["command"]), bare)
            self.assertNotIn("grok-remote", selection["command"])

    def test_grok_auto_resolution_uses_remote_only_after_bare_nonconfirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            write_fake_bare_grok(bin_dir / "grok", available_model="grok-4.4")
            remote = write_fake_grok_remote(bin_dir / "grok-remote")
            with patch.dict(os.environ, {"PATH": str(bin_dir)}, clear=False):
                selection = resolve_grok_dispatch_command(
                    root,
                    "linux",
                    {**os.environ, "PATH": str(bin_dir)},
                    "grok-4.5",
                )
            self.assertEqual(selection["status"], "ok")
            self.assertEqual(selection["source"], "remote-fallback-after-bare-nonconfirmation")
            self.assertEqual(selection["model_probe"]["reason_code"], "resolved_model_not_listed")
            self.assertEqual(command_executable(selection["command"]), remote)

    def test_grok_no_resolved_model_uses_bare_and_never_authorizes_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            bare = write_fake_bare_grok(bin_dir / "grok")
            write_fake_grok_remote(bin_dir / "grok-remote")
            with patch.dict(os.environ, {"PATH": str(bin_dir)}, clear=False):
                selection = resolve_grok_dispatch_command(
                    root,
                    "linux",
                    {**os.environ, "PATH": str(bin_dir)},
                    None,
                )
            self.assertEqual(selection["source"], "bare-default-no-resolved-model")
            self.assertEqual(selection["model_probe"]["status"], "not-performed")
            self.assertEqual(command_executable(selection["command"]), bare)
            self.assertNotIn("grok-remote", selection["command"])

    def test_grok_explicit_bare_override_does_not_silently_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            bare = write_fake_bare_grok(bin_dir / "grok", available_model="grok-4.4")
            write_fake_grok_remote(bin_dir / "grok-remote")
            env = {
                **os.environ,
                "PATH": str(bin_dir),
                "AAS_GROK": str(bare),
            }
            selection = resolve_grok_dispatch_command(root, "linux", env, "grok-4.5")
            self.assertEqual(selection["status"], "blocked")
            self.assertEqual(selection["source"], "explicit-binary-override")
            self.assertEqual(selection["model_probe"]["reason_code"], "resolved_model_not_listed")

    def test_grok_research_auto_uses_confirmed_bare_and_pins_model(self):
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            bare = write_fake_bare_grok(bin_dir / "grok", available_model="grok-4.5")
            # If the bare selection accidentally probes this old proxy, the plan
            # will fail closed rather than report ready.
            write_test_cli(
                bin_dir / "grok-remote",
                posix="#!/bin/sh\nexit 99\n",
                windows="@echo off\r\nexit /b 99\r\n",
            )
            env = {**os.environ, "PATH": str(bin_dir)}
            with patch.dict(os.environ, {"PATH": str(bin_dir)}, clear=False):
                prechecks = build_external_agent_prechecks(
                    root,
                    "linux",
                    manifests["delegation"],
                    env=env,
                )
                plan = build_dispatch_plan(
                    root,
                    "linux",
                    manifests["delegation"],
                    prechecks,
                    ["grok"],
                    max_providers=1,
                    research=True,
                    resolved_model="grok-4.5",
                    resolved_thinking="high",
                    env=env,
                )
            self.assertEqual(plan[0]["status"], "ready", plan[0])
            self.assertEqual(plan[0]["grok_selection"]["source"], "bare-model-confirmed")
            self.assertNotIn("grok_profile_status", plan[0])
            self.assertEqual(command_executable(plan[0]["command"]), bare)
            self.assertIn("--model grok-4.5", plan[0]["command"])

    def test_grok_research_auto_falls_back_remote_after_bare_nonconfirmation(self):
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            write_fake_bare_grok(bin_dir / "grok", available_model="grok-4.4")
            remote = write_fake_grok_remote(bin_dir / "grok-remote")
            payload = grok_profile_status_payload(model_id="grok-4.5")
            env = {
                **os.environ,
                "PATH": str(bin_dir),
                "AAS_TEST_GROK_PROFILE_JSON": json.dumps(payload),
                "AAS_TEST_GROK_PROFILE_EXIT": "0",
            }
            with patch.dict(os.environ, {"PATH": str(bin_dir)}, clear=False):
                prechecks = build_external_agent_prechecks(
                    root,
                    "linux",
                    manifests["delegation"],
                    env=env,
                )
                plan = build_dispatch_plan(
                    root,
                    "linux",
                    manifests["delegation"],
                    prechecks,
                    ["grok"],
                    max_providers=1,
                    research=True,
                    resolved_model="grok-4.5",
                    resolved_thinking="high",
                    env=env,
                )
            self.assertEqual(plan[0]["status"], "ready", plan[0])
            self.assertEqual(
                plan[0]["grok_selection"]["source"],
                "remote-fallback-after-bare-nonconfirmation",
            )
            self.assertEqual(
                plan[0]["grok_selection"]["model_probe"]["reason_code"],
                "resolved_model_not_listed",
            )
            self.assertEqual(plan[0]["grok_profile_status"], payload)
            self.assertEqual(command_executable(plan[0]["command"]), remote)
            self.assertIn("--model grok-4.5", plan[0]["command"])

    def test_grok_remote_profile_probe_accepts_ready_and_degraded_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            grok_remote = write_fake_grok_remote(Path(tmp) / "grok-remote")
            for status in ("ready", "degraded"):
                with self.subTest(status=status):
                    payload = grok_profile_status_payload(status)
                    env = {
                        **os.environ,
                        "AAS_TEST_GROK_PROFILE_JSON": json.dumps(payload),
                        "AAS_TEST_GROK_PROFILE_EXIT": "0",
                    }
                    observed, error = probe_grok_remote_profile(
                        f"{grok_remote} --prompt-file /dev/stdin",
                        env,
                    )
                    self.assertIsNone(error)
                    self.assertEqual(observed, payload)

    @unittest.skipUnless(os.name == "posix", "POSIX umask behavior")
    def test_grok_remote_help_and_doctor_use_private_posix_umask(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = root / "cache"
            cache_dir.mkdir()
            remote = write_test_cli(
                root / "grok-remote",
                posix=(
                    "#!/bin/sh\n"
                    "if [ \"$1\" = --help ]; then\n"
                    "  : > \"$AAS_TEST_CACHE_DIR/help-cache\"\n"
                    "  printf '%s\\n' 'grok-remote doctor --json'\n"
                    "  exit 0\n"
                    "fi\n"
                    "if [ \"$1\" = doctor ] && [ \"$2\" = --json ]; then\n"
                    "  : > \"$AAS_TEST_CACHE_DIR/doctor-cache\"\n"
                    "  printf '%s\\n' \"$AAS_TEST_GROK_PROFILE_JSON\"\n"
                    "  exit 0\n"
                    "fi\n"
                    "exit 97\n"
                ),
                windows="@echo off\r\nexit /b 97\r\n",
            )
            payload = grok_profile_status_payload()
            env = {
                **os.environ,
                "AAS_TEST_CACHE_DIR": str(cache_dir),
                "AAS_TEST_GROK_PROFILE_JSON": json.dumps(payload),
            }
            previous_umask = os.umask(0o002)
            try:
                observed, error = probe_grok_remote_profile(str(remote), env)
            finally:
                os.umask(previous_umask)
            self.assertIsNone(error)
            self.assertEqual(observed, payload)
            self.assertEqual((cache_dir / "help-cache").stat().st_mode & 0o777, 0o600)
            self.assertEqual((cache_dir / "doctor-cache").stat().st_mode & 0o777, 0o600)

    def test_grok_remote_profile_probe_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            grok_remote = write_fake_grok_remote(Path(tmp) / "grok-remote")

            for status in ("blocked", "unconfigured"):
                with self.subTest(status=status):
                    payload = grok_profile_status_payload(status)
                    env = {
                        **os.environ,
                        "AAS_TEST_GROK_PROFILE_JSON": json.dumps(payload),
                        "AAS_TEST_GROK_PROFILE_EXIT": "2",
                    }
                    observed, error = probe_grok_remote_profile(str(grok_remote), env)
                    self.assertEqual(observed, payload)
                    self.assertIn(payload["reason_code"], error or "")

            configured_blocked = grok_profile_status_payload("ready")
            configured_blocked.update(
                {
                    "status": "blocked",
                    "missing_rungs": ["home:windows"],
                    "reason_code": "required_rungs_missing",
                }
            )
            env = {
                **os.environ,
                "AAS_TEST_GROK_PROFILE_JSON": json.dumps(configured_blocked),
                "AAS_TEST_GROK_PROFILE_EXIT": "2",
            }
            observed, error = probe_grok_remote_profile(str(grok_remote), env)
            self.assertEqual(observed, configured_blocked)
            self.assertIn("required_rungs_missing", error or "")

            invalid = grok_profile_status_payload()
            invalid["endpoint"] = "private.example"
            env = {
                **os.environ,
                "AAS_TEST_GROK_PROFILE_JSON": json.dumps(invalid),
                "AAS_TEST_GROK_PROFILE_EXIT": "0",
            }
            observed, error = probe_grok_remote_profile(str(grok_remote), env)
            self.assertIsNone(observed)
            self.assertEqual(error, "grok-remote profile readiness output is invalid")

            invalid_payloads = []
            invalid_status = grok_profile_status_payload()
            invalid_status["status"] = []
            invalid_payloads.append(invalid_status)
            missing_identity = grok_profile_status_payload()
            missing_identity["profile_name"] = None
            invalid_payloads.append(missing_identity)
            invalid_reason = grok_profile_status_payload()
            invalid_reason["reason_code"] = "private endpoint: secret.example"
            invalid_payloads.append(invalid_reason)
            inconsistent_ready_reason = grok_profile_status_payload()
            inconsistent_ready_reason["reason_code"] = "arbitrary_ready_reason"
            invalid_payloads.append(inconsistent_ready_reason)
            configured_redacted_reason = grok_profile_status_payload("ready")
            configured_redacted_reason.update(
                {
                    "status": "blocked",
                    "reason_code": "active_profile_invalid",
                }
            )
            invalid_payloads.append(configured_redacted_reason)
            redacted_profile_bound_reason = grok_profile_status_payload("blocked")
            redacted_profile_bound_reason["reason_code"] = "required_rungs_missing"
            invalid_payloads.append(redacted_profile_bound_reason)
            degraded_without_missing = grok_profile_status_payload("degraded")
            degraded_without_missing["missing_rungs"] = []
            invalid_payloads.append(degraded_without_missing)
            unconfigured_with_identity = grok_profile_status_payload("unconfigured")
            unconfigured_with_identity["profile_name"] = "default"
            invalid_payloads.append(unconfigured_with_identity)
            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    env = {
                        **os.environ,
                        "AAS_TEST_GROK_PROFILE_JSON": json.dumps(payload),
                        "AAS_TEST_GROK_PROFILE_EXIT": "0",
                    }
                    observed, error = probe_grok_remote_profile(str(grok_remote), env)
                    self.assertIsNone(observed)
                    self.assertEqual(error, "grok-remote profile readiness output is invalid")

            inconsistent = grok_profile_status_payload("ready")
            env = {
                **os.environ,
                "AAS_TEST_GROK_PROFILE_JSON": json.dumps(inconsistent),
                "AAS_TEST_GROK_PROFILE_EXIT": "2",
            }
            observed, error = probe_grok_remote_profile(str(grok_remote), env)
            self.assertEqual(observed, inconsistent)
            self.assertEqual(error, "grok-remote profile readiness exit status is inconsistent")

            old_grok_remote = Path(tmp) / "old" / "grok-remote"
            old_grok_remote.parent.mkdir()
            old_grok_remote = write_test_cli(
                old_grok_remote,
                posix="#!/bin/sh\nprintf '%s\\n' 'old grok-remote help'\n",
                windows="@echo off\r\necho old grok-remote help\r\n",
            )
            observed, error = probe_grok_remote_profile(str(old_grok_remote), os.environ.copy())
            self.assertIsNone(observed)
            self.assertEqual(error, "grok-remote does not support managed-profile readiness")

    def test_grok_dispatch_requires_matching_managed_profile(self):
        manifests = load_manifests()
        prechecks = build_external_agent_prechecks(
            REPO_ROOT,
            "linux",
            manifests["delegation"],
            env={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            grok_remote = write_fake_grok_remote(Path(tmp) / "grok-remote")
            payload = grok_profile_status_payload()
            env = {
                **os.environ,
                "AAS_GROK_DISPATCH_COMMAND": f"{grok_remote} --prompt-file /dev/stdin",
                "AAS_TEST_GROK_PROFILE_JSON": json.dumps(payload),
                "AAS_TEST_GROK_PROFILE_EXIT": "0",
            }
            plan = build_dispatch_plan(
                REPO_ROOT,
                "linux",
                manifests["delegation"],
                prechecks,
                ["grok"],
                max_providers=1,
                research=False,
                resolved_model="grok-current",
                resolved_thinking=None,
                env=env,
            )
            self.assertEqual(plan[0]["status"], "ready")
            self.assertEqual(plan[0]["grok_profile_status"], payload)
            self.assertEqual(
                build_capability_profile(plan[0])["grok_profile_status"],
                payload,
            )

            mismatched = build_dispatch_plan(
                REPO_ROOT,
                "linux",
                manifests["delegation"],
                prechecks,
                ["grok"],
                max_providers=1,
                research=False,
                resolved_model="different-model",
                resolved_thinking=None,
                env=env,
            )
            self.assertEqual(mismatched[0]["status"], "blocked")
            self.assertIn("model does not match", mismatched[0]["reason"])
            self.assertEqual(mismatched[0]["grok_profile_status"], payload)

            for status in ("blocked", "unconfigured"):
                with self.subTest(status=status):
                    nonready_payload = grok_profile_status_payload(status)
                    env.update(
                        {
                            "AAS_TEST_GROK_PROFILE_JSON": json.dumps(nonready_payload),
                            "AAS_TEST_GROK_PROFILE_EXIT": "2",
                        }
                    )
                    nonready = build_dispatch_plan(
                        REPO_ROOT,
                        "linux",
                        manifests["delegation"],
                        prechecks,
                        ["grok"],
                        max_providers=1,
                        research=False,
                        resolved_model="grok-current",
                        resolved_thinking=None,
                        env=env,
                    )
                    self.assertEqual(nonready[0]["status"], "blocked")
                    self.assertEqual(nonready[0]["grok_profile_status"], nonready_payload)

            degraded_payload = grok_profile_status_payload("degraded")
            env.update(
                {
                    "AAS_TEST_GROK_PROFILE_JSON": json.dumps(degraded_payload),
                    "AAS_TEST_GROK_PROFILE_EXIT": "0",
                }
            )
            degraded = build_dispatch_plan(
                REPO_ROOT,
                "linux",
                manifests["delegation"],
                prechecks,
                ["grok"],
                max_providers=1,
                research=False,
                resolved_model="grok-current",
                resolved_thinking=None,
                env=env,
            )
            self.assertEqual(degraded[0]["status"], "ready")
            self.assertEqual(degraded[0]["grok_profile_status"], degraded_payload)

    def test_grok_dispatch_delivers_prompt_end_to_end(self):
        # Regression guard for the prompt-transport contract. run_command sends the prompt on STDIN
        # (input=prompt); real grok's -p/--single takes the prompt as an argv VALUE and does not read
        # stdin, so the default shape must use `--prompt-file /dev/stdin`. Execute the resolved command
        # against a fake mimicking grok's arg contract and assert the prompt round-trips -- and that the
        # old `--single` shape does NOT (that is the bug that shipped: it errored before the prompt sent).
        fake_grok = (
            "#!/bin/sh\n"
            'if [ "$1" = "--prompt-file" ]; then\n'
            '  [ -n "$2" ] || { echo "missing path" >&2; exit 2; }\n'
            '  cat "$2"; echo\n'
            'elif [ "$1" = "--single" ] || [ "$1" = "-p" ]; then\n'
            '  [ -n "$2" ] || { echo "value required for --single" >&2; exit 2; }\n'
            '  printf "%s\\n" "$2"\n'
            "else\n"
            '  echo "unexpected: $*" >&2; exit 2\n'
            "fi\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            grok = write_test_cli(
                Path(tmp) / "grok",
                posix=fake_grok,
                windows=(
                    "@echo off\r\n"
                    "if \"%~1\"==\"--prompt-file\" (\r\n"
                    "  if \"%~2\"==\"\" exit /b 2\r\n"
                    "  more\r\n"
                    "  exit /b 0\r\n"
                    ")\r\n"
                    "if \"%~1\"==\"--single\" exit /b 2\r\n"
                    "if \"%~1\"==\"-p\" exit /b 2\r\n"
                    "exit /b 2\r\n"
                ),
            )
            env = dict(os.environ)

            good = default_dispatch_command("grok", str(grok))
            self.assertIn("--prompt-file /dev/stdin", good)
            result = run_command(good, "PROMPT_ROUND_TRIP", timeout=30, env=env, final_marker="M")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PROMPT_ROUND_TRIP", result.stdout)  # the stdin prompt reached grok

            # The old `--single` shape drops the stdin prompt and errors at arg-parse.
            broken = run_command(f"{grok} --single", "PROMPT_ROUND_TRIP", timeout=30, env=env, final_marker="M")
            self.assertNotEqual(broken.returncode, 0)
            self.assertNotIn("PROMPT_ROUND_TRIP", broken.stdout)

    @unittest.skipUnless(os.name == "posix", "POSIX umask behavior")
    def test_grok_smoke_and_task_children_use_private_posix_umask(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = resolve_run_dir(root, None, "umask-test")
            cache_dir = root / "cache"
            cache_dir.mkdir()
            ensure_run_dir(root, run_dir)
            for name in ("timeout_events.jsonl", "truncation_events.jsonl", "evidence-map.jsonl"):
                (run_dir / name).write_text("", encoding="utf-8")
            grok = write_test_cli(
                root / "grok",
                posix=(
                    "#!/bin/sh\n"
                    "count=0\n"
                    "[ ! -f \"$AAS_TEST_CACHE_DIR/count\" ] || read count < \"$AAS_TEST_CACHE_DIR/count\"\n"
                    "count=$((count + 1))\n"
                    "printf '%s\\n' \"$count\" > \"$AAS_TEST_CACHE_DIR/count\"\n"
                    ": > \"$AAS_TEST_CACHE_DIR/child-$count\"\n"
                    "printf '%s\\n' 'AAS_RESULT_JSON_START'\n"
                    "printf '%s\\n' '{\"status\":\"ok\",\"findings\":[],\"limitations\":[],\"warnings\":[]}'\n"
                    "printf '%s\\n' 'AAS_RESULT_JSON_END'\n"
                    "printf '%s\\n' \"$AAS_DELEGATION_FINAL_MARKER\"\n"
                ),
                windows="@echo off\r\nexit /b 97\r\n",
            )
            plan = {
                "provider": "grok",
                "participant_id": "grok-1",
                "command": str(grok),
                "command_shape": "grok --prompt-file /dev/stdin",
                "recipient_profile": "grok-cli-reviewer",
                "research_model_policy": {
                    "status": "ok",
                    "resolved_model": "grok-4.5",
                    "resolved_thinking": "high",
                },
            }
            env = {**os.environ, "AAS_TEST_CACHE_DIR": str(cache_dir)}
            previous_umask = os.umask(0o002)
            try:
                result = run_external_participant(
                    root,
                    run_dir,
                    plan,
                    "review this claim",
                    role="reviewer",
                    template=None,
                    timeout=30,
                    env=env,
                )
            finally:
                os.umask(previous_umask)
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual((cache_dir / "child-1").stat().st_mode & 0o777, 0o600)
            self.assertEqual((cache_dir / "child-2").stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX umask behavior")
    def test_run_command_non_grok_paths_preserve_ambient_posix_umask(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = write_test_cli(
                root / "child",
                posix="#!/bin/sh\n: > \"$AAS_TEST_CACHE_PATH\"\n",
                windows="@echo off\r\nexit /b 97\r\n",
            )
            previous_umask = os.umask(0o002)
            try:
                for provider in (None, "claude"):
                    cache = root / ("arbitrary-cache" if provider is None else "claude-cache")
                    completed = run_command(
                        str(child),
                        "prompt",
                        timeout=30,
                        env={**os.environ, "AAS_TEST_CACHE_PATH": str(cache)},
                        final_marker="M",
                        provider=provider,
                    )
                    self.assertEqual(completed.returncode, 0)
                    self.assertEqual(cache.stat().st_mode & 0o777, 0o664)
            finally:
                os.umask(previous_umask)

    def test_provider_subprocess_options_preserve_windows_behavior(self):
        self.assertEqual(provider_subprocess_options("claude"), {})
        with patch("installer.ai_agents_skills.delegation_dispatch.os.name", "nt"):
            self.assertEqual(provider_subprocess_options("grok"), {})

    def test_deepseek_precheck_reports_endpoint(self):
        manifests = load_manifests()
        prechecks = build_external_agent_prechecks(REPO_ROOT, "linux", manifests["delegation"], env={})
        by_provider = {item["provider"]: item for item in prechecks["providers"]}
        self.assertIn("deepseek", by_provider)
        endpoint = by_provider["deepseek"]["endpoint"]
        self.assertIn(endpoint["status"], {"not-detected", "env-present"})
        self.assertEqual(endpoint["endpoint_sources"][0]["name"], "DEEPSEEK_BASE_URL")


if __name__ == "__main__":
    unittest.main()
