from __future__ import annotations

import json
import re
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

from installer.ai_agents_skills.agents import detect_agents
from installer.ai_agents_skills.apply import apply_plan
from installer.ai_agents_skills.lifecycle import rollback, uninstall
from installer.ai_agents_skills.manifest import REPO_ROOT, load_manifests
from installer.ai_agents_skills.planner import build_plan
from installer.ai_agents_skills.runtime_smoke import selected_runtime_skills
from installer.ai_agents_skills.selectors import resolve_skills
from installer.ai_agents_skills.state import load_state
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

TASK_FIELDS = {
    "schema_version",
    "packet_id",
    "created_at",
    "created_by",
    "intended_recipient",
    "adapter_spec_id",
    "recipient_profile",
    "recipient_capability_snapshot",
    "intent",
    "requested_actions",
    "side_effects",
    "success_criteria",
    "constraints",
    "provenance",
    "input_refs",
    "artifact_refs",
    "scope_constraints",
    "out_of_scope",
    "context_policy",
    "confirmation_requirement",
    "expected_output",
    "evidence_requirements",
    "failure_policy",
    "audit_notes",
}
RESULT_FIELDS = {
    "schema_version",
    "result_id",
    "task_packet_id",
    "task_schema_version",
    "intended_recipient",
    "adapter_spec_id",
    "recipient_profile",
    "produced_at",
    "produced_by",
    "provenance",
    "status",
    "summary",
    "coverage_scope",
    "findings",
    "evidence",
    "artifacts",
    "limitations",
    "warnings",
    "errors",
    "parent_action_request",
    "next_step",
}
REF_FIELDS = {"ref_id", "kind", "source", "sensitivity", "access_note"}
SIDE_EFFECT_FIELDS = {"writes_files", "external_service_posts", "network_calls", "subprocesses"}
PROFILE_FIELDS = {"profile_id", "profile_version", "execution_status"}
CONTEXT_FIELDS = {
    "forward_raw_chat",
    "forward_system_instructions",
    "summary_context_refs",
    "context_refs_to_include",
    "context_refs_to_exclude",
}
FINDING_FIELDS = {
    "finding_id",
    "severity",
    "claim_or_object_ref",
    "evidence_refs",
    "confidence",
    "validation_status",
    "rationale",
    "recommended_parent_action",
}
EVIDENCE_REQUIRED_FIELDS = {"evidence_id", "ref_id", "kind", "quote_or_summary", "status"}
EVIDENCE_FIELDS = EVIDENCE_REQUIRED_FIELDS | {"evidence_disposition", "disposition_rationale"}
ARTIFACT_FIELDS = {"artifact_id", "kind", "ref_id", "description"}
DIAGNOSTIC_FIELDS = {"code", "message", "ref_id"}
PARENT_ACTION_FIELDS = {"requested_action", "target_refs", "side_effects", "reversible", "reason"}
FORBIDDEN_KEYS = {
    "confirmed_by_parent",
    "execute",
    "execution_target",
    "execution_targets",
    "skip_confirmation",
    "approval_receipt",
    "approval_receipts",
    "command",
    "commands",
    "args",
    "cwd",
    "env",
    "environment_variables",
    "provider_config",
    "provider_configs",
    "model_config",
    "model_configs",
    "queue",
    "queues",
    "ledger",
    "session_id",
    "session_ids",
    "resume_token",
    "resume_tokens",
    "participant_probe_status",
    "probe_ref",
    "probe_source_ref",
    "parent_acceptance",
    "accepted_by_parent",
}
TASK_SCHEMA_VERSION = "cross-agent-delegation.task.v1"
RESULT_SCHEMA_VERSION = "cross-agent-delegation.result.v1"
PROFILE_VERSION = "v1"
CONFIRMATION_REQUIREMENTS = {"parent_decides_outside_packet", "parent_confirmation_required"}
FAILURE_POLICIES = {"block", "partial_allowed", "ask_parent"}
RESULT_STATUSES = {"completed", "partial", "blocked", "failed"}
RESULT_NEXT_STEPS = {"parent_decides", "revise_packet", "discard"}
EVIDENCE_DISPOSITIONS = {"supports_finding", "contradicts_finding", "context_only", "limited", "unchecked"}
REFERENCE_FILES = {
    "task-packet-contract.md",
    "result-packet-contract.md",
    "recipient-profiles.md",
    "research-workflow-templates.md",
    "research-workflow-integration.md",
    "safety.md",
    "examples.md",
}


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


def recursive_forbidden_key_errors(value: Any) -> list[str]:
    errors = []
    if isinstance(value, dict):
        if set(value).intersection(FORBIDDEN_KEYS):
            errors.append("FORBIDDEN_AUTHORITY_FIELD")
        for item in value.values():
            errors.extend(recursive_forbidden_key_errors(item))
    elif isinstance(value, list):
        for item in value:
            errors.extend(recursive_forbidden_key_errors(item))
    return errors


def validate_closed_object(value: dict[str, Any], allowed: set[str]) -> list[str]:
    return ["UNKNOWN_FIELD"] if set(value) - allowed else []


def validate_required_fields(value: dict[str, Any], required: set[str]) -> list[str]:
    return ["MISSING_REQUIRED_FIELD"] if not required.issubset(value) else []


def validate_enum(value: Any, allowed: set[str], code: str) -> list[str]:
    return [] if value in allowed else [code]


def validate_ref(ref: dict[str, Any]) -> list[str]:
    errors = validate_closed_object(ref, REF_FIELDS)
    errors.extend(validate_required_fields(ref, REF_FIELDS))
    if ref.get("kind") == "workspace" or ref.get("source") in {"entire_workspace", "all_files", "raw_chat"}:
        errors.append("OVERBROAD_REF")
    raw_target = str(ref.get("source", ""))
    if raw_target.startswith(("http://", "https://")) or re.search(r"(^|[A-Za-z]):\\", raw_target):
        errors.append("RAW_TARGET_REF")
    return errors


def validate_profile(packet: dict[str, Any]) -> list[str]:
    errors = []
    profile = packet.get("recipient_profile", {})
    if not isinstance(profile, dict):
        return ["RECIPIENT_PROFILE_INVALID"]
    errors.extend(validate_closed_object(profile, PROFILE_FIELDS))
    errors.extend(validate_required_fields(profile, PROFILE_FIELDS))
    if profile.get("profile_id") != packet.get("adapter_spec_id"):
        errors.append("PROFILE_ID_MISMATCH")
    if profile.get("profile_version") != PROFILE_VERSION:
        errors.append("PROFILE_VERSION_INVALID")
    if profile.get("execution_status") != "reference_only":
        errors.append("PROFILE_NOT_REFERENCE_ONLY")
    return errors


def validate_task(packet: dict[str, Any]) -> list[str]:
    errors = []
    errors.extend(validate_closed_object(packet, TASK_FIELDS))
    errors.extend(validate_required_fields(packet, TASK_FIELDS))
    if packet.get("schema_version") != TASK_SCHEMA_VERSION:
        errors.append("TASK_SCHEMA_VERSION_INVALID")
    errors.extend(validate_profile(packet))
    side_effects = packet.get("side_effects", {})
    errors.extend(validate_closed_object(side_effects, SIDE_EFFECT_FIELDS))
    errors.extend(validate_required_fields(side_effects, SIDE_EFFECT_FIELDS))
    if any(side_effects.values()) and packet.get("confirmation_requirement") != "parent_confirmation_required":
        errors.append("SIDE_EFFECT_REQUIRES_CONFIRMATION")
    context_policy = packet.get("context_policy", {})
    errors.extend(validate_closed_object(context_policy, CONTEXT_FIELDS))
    errors.extend(validate_required_fields(context_policy, CONTEXT_FIELDS))
    if context_policy.get("forward_raw_chat") or context_policy.get("forward_system_instructions"):
        errors.append("RAW_FORWARDING")
    errors.extend(validate_enum(packet.get("confirmation_requirement"), CONFIRMATION_REQUIREMENTS, "CONFIRMATION_REQUIREMENT_INVALID"))
    errors.extend(validate_enum(packet.get("failure_policy"), FAILURE_POLICIES, "FAILURE_POLICY_INVALID"))
    for field in ("input_refs", "artifact_refs"):
        for ref in packet.get(field, []):
            errors.extend(validate_ref(ref))
    for field in ("summary_context_refs", "context_refs_to_include", "context_refs_to_exclude"):
        for ref in context_policy.get(field, []):
            errors.extend(validate_ref(ref))
    expected = packet.get("expected_output", {})
    if isinstance(expected, dict) and "searched and verified" in str(expected.get("forbidden_claim", "")):
        errors.append("UNVERIFIED_SOURCE_CLAIM")
    errors.extend(recursive_forbidden_key_errors(packet))
    return sorted(set(errors))


def validate_result(packet: dict[str, Any]) -> list[str]:
    errors = []
    errors.extend(validate_closed_object(packet, RESULT_FIELDS))
    errors.extend(validate_required_fields(packet, RESULT_FIELDS))
    if packet.get("schema_version") != RESULT_SCHEMA_VERSION:
        errors.append("RESULT_SCHEMA_VERSION_INVALID")
    if packet.get("task_schema_version") != TASK_SCHEMA_VERSION:
        errors.append("TASK_SCHEMA_VERSION_INVALID")
    errors.extend(validate_profile(packet))
    errors.extend(validate_enum(packet.get("status"), RESULT_STATUSES, "RESULT_STATUS_INVALID"))
    errors.extend(validate_enum(packet.get("next_step"), RESULT_NEXT_STEPS, "RESULT_NEXT_STEP_INVALID"))
    for ref in packet.get("provenance", []):
        errors.extend(validate_ref(ref))
    for finding in packet.get("findings", []):
        errors.extend(validate_closed_object(finding, FINDING_FIELDS))
        errors.extend(validate_required_fields(finding, FINDING_FIELDS))
    for evidence in packet.get("evidence", []):
        errors.extend(validate_closed_object(evidence, EVIDENCE_FIELDS))
        errors.extend(validate_required_fields(evidence, EVIDENCE_REQUIRED_FIELDS))
        if "evidence_disposition" in evidence:
            errors.extend(validate_enum(evidence.get("evidence_disposition"), EVIDENCE_DISPOSITIONS, "EVIDENCE_DISPOSITION_INVALID"))
    for artifact in packet.get("artifacts", []):
        errors.extend(validate_closed_object(artifact, ARTIFACT_FIELDS))
        errors.extend(validate_required_fields(artifact, ARTIFACT_FIELDS))
    for field in ("warnings", "errors"):
        for diagnostic in packet.get(field, []):
            errors.extend(validate_closed_object(diagnostic, DIAGNOSTIC_FIELDS))
            errors.extend(validate_required_fields(diagnostic, DIAGNOSTIC_FIELDS))
    request = packet.get("parent_action_request")
    if request is not None:
        errors.extend(validate_closed_object(request, PARENT_ACTION_FIELDS))
        errors.extend(validate_required_fields(request, PARENT_ACTION_FIELDS))
        errors.extend(validate_closed_object(request.get("side_effects", {}), SIDE_EFFECT_FIELDS))
        errors.extend(validate_required_fields(request.get("side_effects", {}), SIDE_EFFECT_FIELDS))
        for ref in request.get("target_refs", []):
            errors.extend(validate_ref(ref))
    errors.extend(recursive_forbidden_key_errors(packet))
    return sorted(set(errors))


class CrossAgentDelegationManifestTests(unittest.TestCase):
    def test_manifest_profile_and_runtime_boundaries(self) -> None:
        manifests = load_manifests()
        spec = manifests["skills"]["skills"][SKILL]
        self.assertEqual(set(spec["supported_agents"]), {"codex", "claude", "deepseek"})
        self.assertEqual(set(spec["profiles"]), {"multi-agent", "full-research"})
        self.assertEqual(spec["required_dependencies"], [])
        self.assertNotIn(SKILL, manifests["runtime"]["skills"])

        args = Args()
        args.profile = "multi-agent"
        self.assertEqual(
            set(resolve_skills(args, manifests)),
            {"agent-group-discuss", "model-router", "prose", SKILL},
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
            "model-only-api-reviewer",
            "openclaw-host-reference",
        ):
            self.assertIn(profile, text)
        self.assertIn("DeepSeek V1 support is reference or instruction placement only", text)
        self.assertIn("OpenClaw is not a V1 `supported_agents` target", text)


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


if __name__ == "__main__":
    unittest.main()
