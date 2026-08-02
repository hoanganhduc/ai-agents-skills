"""Offline tests for the pure autonomous-loop notification v2 contract."""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NOTIFY = (
    REPO
    / "canonical"
    / "runtime"
    / "skills"
    / "autonomous-research-loop-runtime"
    / "notify_v2.py"
)
SUPERVISOR = NOTIFY.parent / "arl_drive_supervisor.sh"

sys.dont_write_bytecode = True


def _module():
    name = "aas_notify_v2_test"
    spec = importlib.util.spec_from_file_location(name, NOTIFY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class NotifyV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.notify = _module()

    def event(self, **overrides):
        values = {
            "event": "iteration_ok",
            "event_id": "evt-7",
            "occurred_at": "2026-07-29T12:00:00Z",
            "finished_at": "2026-07-29T12:00:00Z",
            "title": "Sample reconfiguration question",
            "topic_slug": "sample-question",
            "goal": "Resolve the main open sample reconfiguration question.",
            "completed": "Verified and banked the A3 obstruction certificate.",
            "current": "A3 is active; the manuscript-level bridge remains open.",
            "plan": "Test the named bridge obligation with the bounded verifier.",
            "iteration_status": "success",
            "loop_status": "running",
            "review_status": "passed",
            "iteration_number": 7,
            "spent_iterations": 7,
            "max_iterations": 20,
            "goal_progress": "2/6 named obligations satisfied",
            "executor": "Claude",
            "driver_agent": {
                "name": "Claude",
                "provider": "claude",
                "model": "claude-fable-5",
            },
            "panel_agents": ["Codex", "CodeWhale"],
            "other_agents": [],
            "compute": [
                {
                    "service": "kaggle",
                    "status": "succeeded",
                    "job_ref": "kernel-7",
                    "duration_seconds": 62,
                }
            ],
            "duration_seconds": 125,
            "plan_revision": 4,
        }
        values.update(overrides)
        return self.notify.build_event(**values)

    def test_all_renderers_contain_required_fields(self) -> None:
        event = self.event(started_at="2026-07-29T11:58:00Z")
        rendered = self.notify.render_all(event)
        for name, body in rendered.items():
            with self.subTest(renderer=name):
                for label in (
                    "Status",
                    "Event time",
                    "Progress",
                    "Finished",
                    "Executor",
                    "Driver agent",
                    "Panel agents",
                    "Compute",
                    "Goal",
                    "Completed",
                    "Current",
                    "Plan",
                ):
                    self.assertIn(label, body)
                # Empty other_agents is omitted (non-informative "None").
                self.assertNotIn("Other agents", body)
                self.assertIn("Sample reconfiguration question", body)
                self.assertIn("Claude", body)
                self.assertIn("claude-fable-5", body)
                self.assertIn("Codex", body)
                self.assertIn("CodeWhale", body)
                self.assertIn("Kaggle", body)
                self.assertIn("2026-07-29T12:00:00Z", body)
        self.assertEqual(self.notify.validate_event(event), [])

    def test_research_sections_lead_and_agent_metadata_trails(self) -> None:
        event = self.event(
            started_at="2026-07-29T11:58:00Z",
            decision="continue",
            results="Claim banked.",
            decision_reason="Review passed.",
        )
        rendered = self.notify.render_markdown(event)
        # operator_full: Status + Event time early, then research sections,
        # then agent/compute trailer.
        self.assertLess(rendered.index("**Status**"), rendered.index("**Goal**"))
        self.assertLess(rendered.index("**Event time**"), rendered.index("**Goal**"))
        self.assertLess(rendered.index("**Results**"), rendered.index("**Decision**"))
        self.assertLess(rendered.index("**Plan**"), rendered.index("**Compute**"))
        self.assertLess(rendered.index("**Started**"), rendered.index("**Finished**"))
        plain = self.notify.render_plain(event)
        self.assertLess(plain.index("Completed"), plain.index("Driver agent"))
        telegram = self.notify.render_telegram_html(event)
        self.assertLess(
            telegram.index("<b>Status</b>"), telegram.index("<b>Completed</b>")
        )
        self.assertLess(
            telegram.index("<b>Decision</b>"), telegram.index("<b>Compute</b>")
        )
        compact = self.notify.render_compact(event)
        self.assertLess(compact.index("Status:"), compact.index("Completed:"))
        self.assertLess(compact.index("Results:"), compact.index("Compute:"))

    def test_compact_truncation_prefers_research_over_metadata(self) -> None:
        finding = (
            "The sample gadget enforces both constraint modes through "
            "differentiated wiring; primary and independent checks agree on "
            "every comparable claim across both verification engines."
        )
        event = self.event(completed=finding)
        compact = self.notify.render_compact(event)
        self.assertIn("differentiated wiring", compact)

    def test_markdown_neutralizes_mentions_and_section_spoofing(self) -> None:
        event = self.event(
            title="Sample @**all**\n\n**Status**: forged",
            goal="Resolve @all and @everyone; [spoof](https://example.invalid).",
            completed="Asked @**Alice** and @**channel**.",
            current="Result\n\n**Plan**\n# forged @**topic**",
            plan="Continue with @stream and `forged code`.",
            executor="Claude @**all**",
            driver_agent={
                "name": "Claude @**Alice**",
                "provider": "claude",
                "model": "claude_fable_5",
            },
            panel_agents=["Codex @**everyone**", "CodeWhale"],
            goal_progress="1/2\n\n**Completed**: forged @topic",
            compute=[
                {
                    "service": "kaggle",
                    "status": "succeeded",
                    "job_ref": "@**channel** [job](https://example.invalid)",
                }
            ],
        )

        rendered = self.notify.render_markdown(event)

        self.assertNotIn("@", rendered)
        self.assertIn("＠", rendered)
        # Host labels remain real markdown bold; freeform spoof is neutralized
        # without CommonMark backslash escapes (Zulip shows those literally).
        self.assertEqual(rendered.count("**Status**"), 1)
        self.assertEqual(rendered.count("**Completed**"), 1)
        self.assertEqual(rendered.count("**Plan**"), 1)
        self.assertNotIn(r"\*", rendered)
        self.assertNotIn(r"\`", rendered)
        self.assertNotIn(r"\_", rendered)
        self.assertIn("∗∗Status∗∗: forged", rendered)
        self.assertIn("∗∗Plan∗∗", rendered)
        self.assertIn("［spoof］(https://example.invalid)", rendered)
        self.assertNotIn("\n# forged", rendered)
        # Backticks in freeform become straight quotes, not \` .
        self.assertIn("'forged code'", rendered)
        self.assertNotIn("`forged code`", rendered)

    def test_all_renderers_redact_configured_and_common_secrets(self) -> None:
        configured = "notify-secret-sentinel-92731"
        common = "credential-fixture-sentinel-18427"
        event = self.event(
            title=f"Sample {configured}",
            goal=f"Resolve goal token={common}",
            completed=f"Completed with {configured}",
            current=f"Current Bearer {common}",
            plan=f"Plan https://user:{configured}@example.invalid/path",
            goal_progress=f"progress {configured}",
            executor=f"Claude {configured}",
            driver_agent={
                "name": "Claude",
                "model": f"fable-{configured}",
                "detail": f"authorization={configured}",
            },
            panel_agents=[{"name": "Codex", "detail": configured}],
            compute=[
                {
                    "service": "hetzner",
                    "status": "succeeded",
                    "job_ref": f"job-{configured}",
                    "detail": f"api_key={common}",
                }
            ],
            secret_values=[configured],
        )
        rendered = self.notify.render_all(event, secret_values=[configured])
        for body in rendered.values():
            self.assertNotIn(configured, body)
            self.assertNotIn(common, body)
            self.assertTrue(
                self.notify.REDACTION in body
                or r"\[REDACTED\]" in body,
            )
            self.assertIn("Claude", body)
            self.assertIn("Hetzner", body)
        serialized = json.dumps(event)
        self.assertNotIn(configured, serialized)
        self.assertNotIn(common, serialized)

    def test_redaction_covers_event_id_and_unselected_nested_values(self) -> None:
        configured = "notify-secret-sentinel-92731"
        event = self.event()
        event["event_id"] = f"evt-{configured}"
        event["extension"] = {
            "note": f"unrendered {configured}",
            "nested": [f"Bearer {configured}"],
        }

        safe = self.notify.redact_event(event, secret_values=[configured])
        serialized = json.dumps(safe, sort_keys=True)

        self.assertNotIn(configured, serialized)
        self.assertTrue(safe["event_id"].startswith("notify-redacted-"))
        self.assertEqual(safe["extension"]["note"], "unrendered [REDACTED]")
        self.assertEqual(safe["extension"]["nested"], ["Bearer [REDACTED]"])
        self.assertEqual(self.notify.validate_event(safe), [])

    def test_pii_is_redacted_across_nested_event_and_rendered_output(self) -> None:
        email = "participant" + chr(64) + "example.invalid"
        phone = "+1 (202) 555-0187"
        subject_id = "SUBJECT-88421"
        event = self.event(
            title=f"Contact {email}",
            goal=f"Call {phone} after verification",
            completed=f"participant_id: {subject_id}",
            current="No personal data should leave the host.",
        )
        event["extension"] = {
            "patient_email": email,
            "nested": [{"date_of_birth": "1990-04-05"}],
        }

        safe = self.notify.redact_event(event)
        serialized = json.dumps(safe, sort_keys=True)
        rendered = json.dumps(self.notify.render_all(safe), sort_keys=True)

        for sensitive in (email, phone, subject_id, "1990-04-05"):
            self.assertNotIn(sensitive, serialized)
            self.assertNotIn(sensitive, rendered)
        self.assertIn(self.notify.PII_REDACTION, serialized)
        self.assertIn(self.notify.PII_REDACTION, rendered)
        self.assertEqual(self.notify.validate_event(safe), [])

    def test_status_finished_and_explicit_no_compute(self) -> None:
        event = self.event(
            event="quota_wait",
            iteration_status="waiting",
            review_status="pending",
            compute=[],
        )
        plain = self.notify.render_plain(event)
        self.assertIn("Iteration WAITING", plain)
        # In-flight finish/empty-compute sentinels are omitted from the body.
        self.assertNotIn("Not finished", plain)
        self.assertNotIn("Finished:", plain)
        self.assertNotIn("Compute: None", plain)
        self.assertIn("Event time:", plain)
        self.assertEqual(event["iteration"]["finished_at"], "")

    def test_all_compute_services_are_structured(self) -> None:
        records = [
            {"service": service, "status": "succeeded"}
            for service in ("local", "hetzner", "kaggle", "modal", "github-actions")
        ] + [{"service": "other:slurm", "status": "unknown"}]
        event = self.event(compute=records)
        self.assertTrue(event["compute"]["reported"])
        self.assertEqual(len(event["compute"]["runs"]), 6)
        text = self.notify.format_compute(event)
        for label in ("Local", "Hetzner", "Kaggle", "Modal", "GitHub Actions", "Slurm"):
            self.assertIn(label, text)

    def test_missing_compute_is_not_inferred(self) -> None:
        event = self.event(compute=None)
        self.assertFalse(event["compute"]["reported"])
        self.assertEqual(event["compute"]["runs"], [])
        self.assertEqual(
            self.notify.format_compute(event),
            "Not recorded (legacy/unreported)",
        )

    def test_agent_usage_distinguishes_none_from_unreported(self) -> None:
        event = self.event(driver_agent=None, executor="Not recorded", panel_agents=[], other_agents=None)
        self.assertFalse(event["agents"]["driver"]["reported"])
        self.assertTrue(event["agents"]["panel"]["reported"])
        self.assertFalse(event["agents"]["other"]["reported"])
        self.assertEqual(
            self.notify.format_agent_usage(event, "driver"),
            "Not recorded (legacy/unreported)",
        )
        self.assertEqual(self.notify.format_agent_usage(event, "panel"), "None")
        self.assertEqual(
            self.notify.format_agent_usage(event, "other"),
            "Not recorded (legacy/unreported)",
        )

    def test_non_empty_other_agent_provenance_is_preserved_and_rendered(self) -> None:
        event = self.event(
            other_agents=[
                {
                    "name": "Sage verifier",
                    "provider": "local",
                    "model": "SageMath 10",
                    "role": "certificate verifier",
                }
            ]
        )

        record = event["agents"]["other"]["agents"][0]
        self.assertEqual(record["provider"], "local")
        self.assertEqual(record["role"], "certificate verifier")
        for name, body in self.notify.render_all(event).items():
            with self.subTest(renderer=name):
                self.assertIn("Other agents", body)
                self.assertIn("Sage verifier", body)
                self.assertIn("SageMath 10", body)
                # Compact may truncate the role under trailer pressure; full
                # renderers keep the role string.
                if name != "compact":
                    self.assertIn("certificate verifier", body)

    def test_existing_v2_event_without_agents_is_compatibly_upgraded(self) -> None:
        event = self.event()
        del event["agents"]
        upgraded = self.notify.ensure_event(event)
        self.assertEqual(
            self.notify.format_agent_usage(upgraded, "driver"),
            "Claude",
        )
        self.assertFalse(upgraded["agents"]["panel"]["reported"])

    def test_invalid_compute_status_is_rejected(self) -> None:
        with self.assertRaises(self.notify.NotifyValidationError):
            self.event(compute=[{"service": "kaggle", "status": "probably"}])

    def test_compute_requires_service_and_exact_reported_boolean(self) -> None:
        for compute in (
            [{}],
            [{"status": "succeeded"}],
            {"reported": "false", "runs": []},
            [{"service": True, "status": "unknown"}],
            [{"service": "kaggle", "status": "unknown", "job_ref": 42}],
            [
                {
                    "service": "kaggle",
                    "status": "unknown",
                    "duration_seconds": "60",
                }
            ],
        ):
            with self.subTest(compute=compute), self.assertRaises(
                self.notify.NotifyValidationError
            ):
                self.event(compute=compute)

        with self.assertRaises(self.notify.NotifyValidationError):
            self.event(panel_agents={"reported": "false", "agents": []})

    def test_legacy_empty_compute_remains_unreported_without_invention(self) -> None:
        base = {
            "schema_version": "1.0",
            "timestamp": "2026-07-29T12:00:00Z",
            "event": "iteration_failed",
            "research_title": "Legacy topic",
            "goal": "Prove the theorem",
            "provider": "codex",
        }
        for empty_value in ("", "   ", {}):
            with self.subTest(empty_value=empty_value):
                event = self.notify.from_legacy({**base, "compute": empty_value})
                self.assertEqual(
                    event["compute"], {"reported": False, "runs": []}
                )

        with self.assertRaises(self.notify.NotifyValidationError):
            self.notify.from_legacy({**base, "compute": [{}]})

    def test_legacy_driver_failure_maps_to_error_without_bank_claim(self) -> None:
        event = self.notify.from_legacy(
            {
                "schema_version": "1.0",
                "timestamp": "2026-07-29T12:00:00Z",
                "event": "iteration_failed",
                "research_title": "Legacy topic",
                "goal": "Prove the theorem",
                "iteration": 9,
                "spent_iterations": 8,
                "max_iterations": 40,
                "status": "running",
                "provider": "codex",
                "progress_note": "Provider process exited before ledger advance.",
                "next_preferred_path": "Retry with Claude.",
            }
        )
        self.assertEqual(event["iteration"]["status"], "error")
        self.assertIn("No new result was banked", event["sections"]["completed"])
        self.assertEqual(event["sections"]["plan"], "Retry with Claude.")
        self.assertEqual(self.notify.format_compute(event), "Not recorded (legacy/unreported)")

    def test_telegram_html_is_escaped_bounded_and_balanced(self) -> None:
        hostile = "<script>&" * 4000
        event = self.event(goal=hostile, completed=hostile, current=hostile, plan=hostile)
        rendered = self.notify.render_telegram_html(event)
        self.assertLessEqual(len(rendered), self.notify.TELEGRAM_SAFE_MAX)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;", rendered)
        self.assertIn("&amp;", rendered)
        self.assertEqual(rendered.count("<b>"), rendered.count("</b>"))
        self.assertIsNone(re.search(r"&(?!amp;|lt;|gt;)", rendered))

    def test_fingerprint_and_legacy_fields_are_stable(self) -> None:
        event = self.event()
        self.assertEqual(
            self.notify.delivery_fingerprint(event),
            self.notify.delivery_fingerprint(event),
        )
        flat = self.notify.legacy_flat_fields(event)
        self.assertEqual(flat["iteration_status"], "success")
        self.assertEqual(flat["research_title"], "Sample reconfiguration question")
        self.assertIn("**Goal**", flat["text"])
        changed = self.event(event_id="evt-8")
        self.assertNotEqual(
            self.notify.delivery_fingerprint(event),
            self.notify.delivery_fingerprint(changed),
        )

    def test_retry_fingerprint_ignores_retry_identity_and_timing_only(self) -> None:
        original = self.event(
            started_at="2026-07-29T11:57:55Z",
            compute=[
                {
                    "service": "kaggle",
                    "status": "succeeded",
                    "job_ref": "kernel-7",
                    "started_at": "2026-07-29T11:58:00Z",
                    "finished_at": "2026-07-29T11:59:02Z",
                    "duration_seconds": 62,
                }
            ],
        )
        retry = copy.deepcopy(original)
        retry["event_id"] = "evt-7-retry"
        retry["occurred_at"] = "2026-07-29T12:01:00Z"
        retry["iteration"]["started_at"] = "2026-07-29T11:58:40Z"
        retry["iteration"]["finished_at"] = "2026-07-29T12:01:00Z"
        retry["iteration"]["duration_seconds"] = 140
        retry["compute"]["runs"][0]["started_at"] = "2026-07-29T11:59:00Z"
        retry["compute"]["runs"][0]["finished_at"] = "2026-07-29T12:00:10Z"
        retry["compute"]["runs"][0]["duration_seconds"] = 70

        self.assertNotEqual(
            self.notify.delivery_fingerprint(original),
            self.notify.delivery_fingerprint(retry),
        )
        self.assertEqual(
            self.notify.retry_fingerprint(original),
            self.notify.retry_fingerprint(retry),
        )

        retry["sections"]["current"] = "A materially different current result."
        self.assertNotEqual(
            self.notify.retry_fingerprint(original),
            self.notify.retry_fingerprint(retry),
        )

    def test_validation_reports_missing_section(self) -> None:
        event = self.event()
        del event["sections"]["plan"]
        self.assertIn("sections.plan is required", self.notify.validate_event(event))

    def test_validation_rejects_unreported_compute_and_agents_with_records(self) -> None:
        event = self.event()
        event["compute"]["reported"] = False
        event["agents"]["panel"]["reported"] = False
        errors = self.notify.validate_event(event)
        self.assertTrue(
            any("compute" in error and "reported" in error for error in errors),
            errors,
        )
        self.assertTrue(
            any("agent provenance" in error for error in errors),
            errors,
        )

    def test_validation_rejects_finished_status_without_time_and_negative_duration(self) -> None:
        event = self.event()
        event["iteration"]["finished_at"] = ""
        event["iteration"]["duration_seconds"] = -1
        errors = self.notify.validate_event(event)
        self.assertIn(
            "iteration.finished_at is required for a finished event",
            errors,
        )
        self.assertIn(
            "iteration.duration_seconds must be a non-negative number or null",
            errors,
        )

    def test_timestamp_fields_reject_free_text_and_secret_payloads(self) -> None:
        secret = "token=notify-secret-sentinel-92731"
        with self.assertRaisesRegex(
            self.notify.NotifyValidationError,
            "iteration.finished_at must be a timezone-aware ISO-8601 timestamp",
        ):
            self.event(finished_at=secret)

        event = self.event()
        event["occurred_at"] = secret
        event["iteration"]["started_at"] = secret
        event["iteration"]["finished_at"] = secret
        event["compute"]["runs"][0]["started_at"] = secret
        errors = self.notify.validate_event(event)
        self.assertTrue(any("occurred_at" in error for error in errors), errors)
        self.assertTrue(any("iteration.started_at" in error for error in errors), errors)
        self.assertTrue(any("iteration.finished_at" in error for error in errors), errors)
        self.assertTrue(any("compute.started_at" in error for error in errors), errors)
        with self.assertRaises(self.notify.NotifyValidationError):
            self.notify.render_all(event)

    def test_builder_omits_non_finite_iteration_duration_and_rejects_compute(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                event = self.event(duration_seconds=value)
                self.assertIsNone(event["iteration"]["duration_seconds"])
                self.assertEqual(self.notify.validate_event(event), [])
                with self.assertRaises(self.notify.NotifyValidationError):
                    self.event(
                        compute=[
                            {
                                "service": "kaggle",
                                "status": "succeeded",
                                "duration_seconds": value,
                            }
                        ]
                    )

    def test_validation_rejects_non_finite_persisted_notification_durations(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                event = self.event()
                event["iteration"]["duration_seconds"] = value
                event["compute"]["runs"][0]["duration_seconds"] = value

                errors = self.notify.validate_event(event)

                self.assertIn(
                    "iteration.duration_seconds must be a non-negative number or null",
                    errors,
                )
                self.assertIn(
                    "compute.duration_seconds must be a finite non-negative number",
                    errors,
                )

    def test_compact_renderer_bounds_hostile_long_agent_and_compute_provenance(self) -> None:
        hostile = "<agent>&" + ("very-long-provider-name-" * 80)
        event = self.event(
            driver_agent={
                "name": hostile,
                "provider": hostile,
                "model": hostile,
                "detail": hostile,
            },
            panel_agents=[
                {"name": hostile, "provider": "codex", "model": hostile},
                {"name": "CodeWhale", "provider": "codewhale", "detail": hostile},
            ],
            other_agents=[{"name": hostile, "role": "verifier"}],
            compute=[
                {
                    "service": "hetzner",
                    "status": "succeeded",
                    "job_ref": hostile,
                    "detail": hostile,
                },
                {
                    "service": "kaggle",
                    "status": "failed",
                    "job_ref": hostile,
                },
            ],
        )
        rendered = self.notify.render_compact(event, max_chars=1200)
        self.assertLessEqual(len(rendered), 1200)
        for label in (
            "Driver agent",
            "Panel agents",
            "Other agents",
            "Compute",
            "Goal",
            "Completed",
            "Current",
            "Plan",
        ):
            self.assertIn(label + ":", rendered)
        self.assertIn("Hetzner", rendered)
        full_compute = self.notify.format_compute(event)
        self.assertIn("Hetzner", full_compute)
        self.assertIn("Kaggle", full_compute)
        with self.assertRaises(self.notify.NotifyValidationError):
            self.notify.render_compact(event, max_chars=80)

    def test_terminal_notification_preserves_explicit_compute_provenance(self) -> None:
        event = self.event(
            event="terminal",
            iteration_status="not_applicable",
            loop_status="completed",
            executor="Claude",
            compute=[
                {
                    "service": "hetzner",
                    "status": "succeeded",
                    "job_ref": "server-42",
                    "duration_seconds": 301,
                },
                {
                    "service": "kaggle",
                    "status": "failed",
                    "job_ref": "kernel-9",
                    "duration_seconds": 18,
                },
            ],
        )
        self.assertEqual(self.notify.validate_event(event), [])
        plain = self.notify.render_plain(event)
        self.assertIn("Finished: 2026-07-29T12:00:00Z", plain)
        self.assertIn("Hetzner", plain)
        self.assertIn("server-42", plain)
        self.assertIn("Kaggle", plain)
        self.assertIn("kernel-9", plain)

    def test_supervisor_local_log_does_not_emit_raw_title_or_message(self) -> None:
        script = SUPERVISOR.read_text(encoding="utf-8")
        notify_body = script.split("notify() {", 1)[1].split("\n}", 1)[0]
        stderr_lines = [
            line for line in notify_body.splitlines() if ">&2" in line or "printf" in line
        ]
        local_log = "\n".join(stderr_lines)

        self.assertIn("structured notification emitted", local_log)
        self.assertNotIn("RESEARCH_TITLE", local_log)
        self.assertNotRegex(local_log, r'\$(?:1|msg)(?:\b|["}])')



    def test_v21_literal_legacy_upgrade(self) -> None:
        """A serialized v2.0 envelope upgrades without inventing empty issues as none."""
        legacy = {
            "schema": self.notify.SCHEMA_ID,
            "schema_version": "2.0",
            "event_id": "legacy-evt",
            "event": "iteration_ok",
            "occurred_at": "2026-07-29T12:00:00Z",
            "research": {
                "title": "Legacy title",
                "topic_slug": "legacy-title",
                "goal": "Legacy goal text for the open problem.",
                "success_criteria": [],
                "goal_status": "open",
            },
            "iteration": {
                "number": 3,
                "status": "success",
                "decision": "continue",
                "campaign_id": "",
                "objective_id": "",
                "scope": "",
                "executor": "Codex",
                "started_at": "",
                "finished_at": "2026-07-29T12:00:00Z",
                "duration_seconds": 10,
            },
            "review": {"status": "passed", "families": []},
            "loop": {"status": "running"},
            "progress": {
                "spent_iterations": 3,
                "max_iterations": 10,
                "remaining_iterations": 7,
                "goal_summary": "Not measured",
            },
            "agents": {
                "driver": {
                    "reported": True,
                    "agents": [{"name": "Codex", "role": "driver"}],
                },
                "panel": {"reported": True, "agents": []},
                "other": {"reported": False, "agents": []},
            },
            "compute": {"reported": True, "runs": []},
            "sections": {
                "goal": "Legacy goal text for the open problem.",
                "completed": "Banked a legacy result.",
                "current": "Still open.",
                "plan": "Continue.",
            },
        }
        upgraded = self.notify.ensure_event(legacy)
        self.assertEqual(upgraded["schema_version"], "2.1")
        self.assertEqual(upgraded["presentation"]["body_profile"], "operator_full")
        self.assertIn("results", upgraded["sections"])
        self.assertIn("decision", upgraded["sections"])
        self.assertIn("decision_reason", upgraded["sections"])
        self.assertFalse(upgraded["issues"]["reported"])
        md = self.notify.render_markdown(upgraded)
        # Legacy freeform may still carry honesty text; unreported issues/agents
        # are omitted rather than printed as "Not recorded (legacy/unreported)".
        self.assertNotIn("Not recorded (legacy/unreported)", md)
        self.assertIn("Event time", md)

    def test_operator_full_renders_decision_results_started(self) -> None:
        event = self.event(
            decision="revise",
            results="Claim a3-x supported; claim a3-y disputed.",
            decision_reason="Different-family review rejected the bridge claim.",
            started_at="2026-07-29T11:58:00Z",
            issues={
                "reported": True,
                "errors": [],
                "failures": [
                    {
                        "code": "result_review_rejected",
                        "message": "claim a3-y disputed",
                        "stage": "result_review",
                    }
                ],
            },
        )
        md = self.notify.render_markdown(event)
        for token in (
            "**Results**",
            "**Decision**",
            "**Decision reason**",
            "**Started**",
            "Review failures",
            "claim a3-y disputed",
            "2026-07-29T11:58:00Z",
        ):
            self.assertIn(token, md)
        # Empty errors omitted when reported with empty list.
        self.assertNotIn("Runtime errors", md)

    def test_legacy_body_profile_omits_new_lead_labels(self) -> None:
        event = self.event(body_profile="legacy", decision="continue")
        md = self.notify.render_markdown(event)
        self.assertIn("**Goal**", md)
        self.assertIn("**Completed**", md)
        # New lead labels are not used in legacy layout.
        self.assertNotIn("**Results**", md)
        self.assertNotIn("**Decision reason**", md)
        self.assertNotIn("**Started**", md)

    def test_sensitive_issue_messages_are_length_capped_and_redacted(self) -> None:
        # Use the same non-sk sentinel style as other notify redaction tests so
        # repo sanitizer checks do not false-positive on fixture secrets.
        secret = "notify-secret-sentinel-notify-v21-48107"
        event = self.event(
            decision_reason=f"failed with token {secret}",
            issues={
                "reported": True,
                "errors": [
                    {
                        "code": "sensitive_output",
                        "message": "x" * 500,
                    }
                ],
                "failures": [],
            },
        )
        self.assertLessEqual(len(event["issues"]["errors"][0]["message"]), 240)
        rendered = self.notify.render_all(event, secret_values=[secret])
        blob = json.dumps(rendered)
        self.assertNotIn(secret, blob)
        # Underscores are not Zulip-escaped (Zulip does not use _ for italics).
        self.assertIn("sensitive_output", rendered["plain"])
        self.assertIn("sensitive_output", rendered["markdown"])
        self.assertNotIn("sensitive\\_output", rendered["markdown"])

    def test_zulip_markdown_does_not_backslash_escape_underscores(self) -> None:
        event = self.event(
            goal="Prove isRigidOn_univ_iff (Lemma 1(b)).",
            current="Campaign `tier-B` active",
            iteration_status="not_applicable",
            review_status="not_required",
        )
        md = self.notify.render_markdown(event)
        self.assertIn("isRigidOn_univ_iff", md)
        self.assertNotIn("isRigidOn\\_univ\\_iff", md)
        self.assertIn("NOT_APPLICABLE", md)
        self.assertNotIn("NOT\\_APPLICABLE", md)
        self.assertIn("Campaign 'tier-B' active", md)
        self.assertNotIn("\\`", md)
        self.assertNotIn("`tier-B`", md)

    def test_operator_compact_omits_trailer_noise(self) -> None:
        event = self.event(body_profile="operator_compact", compute=None, other_agents=None)
        md = self.notify.render_markdown(event)
        self.assertIn("**Status**", md)
        self.assertIn("**Event time**", md)
        self.assertIn("**Completed**", md)
        self.assertNotIn("**Goal**", md)
        self.assertNotIn("**Compute**", md)
        self.assertNotIn("**Driver agent**", md)

    def test_omit_empty_started_and_pending_decision_on_waiting(self) -> None:
        event = self.event(
            event="strategy_review_wait",
            iteration_status="waiting",
            review_status="pending",
            decision=None,
            decision_reason=None,
            results=None,
            compute=[],
        )
        md = self.notify.render_markdown(event)
        self.assertIn("**Event time**", md)
        self.assertNotIn("**Started**", md)
        self.assertNotIn("**Finished**", md)
        self.assertNotIn("Pending (not finalized)", md)
        self.assertNotIn("No claims banked", md)


if __name__ == "__main__":
    unittest.main()
