from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import unittest

from installer.ai_agents_skills.openclaw_runtime_target_evidence import (
    build_runtime_target_evidence,
    runtime_action_is_executable,
    runtime_actions_require_helper_invocation,
    runtime_target_evidence_authorizes_real_writes,
    sign_runtime_evidence,
    validate_runtime_target_evidence,
    verify_runtime_evidence_signature,
)

RP = dict(
    target_realpath="/home" "/u/.openclaw",
    managed_skills_realpath="/home" "/u/.openclaw/skills",
    runtime_realpath="/home" "/u/.local/share/ai-agents-skills/runtime",
)


def _ev(etype: str):
    return build_runtime_target_evidence(
        evidence_type=etype,
        platform="linux",
        path_style="posix",
        observed_behavior=f"probed {etype}",
        checks={"ok": True, "type": etype},
        **RP,
    )


class RuntimeEvidenceTest(unittest.TestCase):
    def test_build_validate_roundtrip_and_content_address(self) -> None:
        ev = _ev("neutral-runtime-root")
        validate_runtime_target_evidence(ev)  # no raise
        ev["checks"]["ok"] = False  # tamper -> content address mismatch
        with self.assertRaisesRegex(ValueError, "content address"):
            validate_runtime_target_evidence(ev)

    def test_support_file_predicate_requires_its_set(self) -> None:
        items = [_ev(t) for t in ("native-loader", "quiescence-lock", "compatibility-tuple-match", "support-file-pre-state")]
        self.assertTrue(runtime_target_evidence_authorizes_real_writes(
            items, action_class="managed-support-file", requires_helper_invocation=False))
        # drop one required type -> not authorized
        self.assertFalse(runtime_target_evidence_authorizes_real_writes(
            items[:-1], action_class="managed-support-file", requires_helper_invocation=False))

    def test_runtime_file_predicate_helper_invocation_conditional(self) -> None:
        base = [_ev(t) for t in (
            "native-loader", "quiescence-lock", "neutral-runtime-root", "runtime-pre-state", "compatibility-tuple-match")]
        # inert runtime file: helper-invocation not required -> authorized
        self.assertTrue(runtime_target_evidence_authorizes_real_writes(
            base, action_class="shared-runtime-file", requires_helper_invocation=False))
        # executable helper: helper-invocation required but missing -> NOT authorized
        self.assertFalse(runtime_target_evidence_authorizes_real_writes(
            base, action_class="shared-runtime-file", requires_helper_invocation=True))
        # add helper-invocation -> authorized
        self.assertTrue(runtime_target_evidence_authorizes_real_writes(
            base + [_ev("helper-invocation")], action_class="shared-runtime-file", requires_helper_invocation=True))

    def test_unknown_action_class_and_multi_realpath_refused(self) -> None:
        items = [_ev(t) for t in ("native-loader", "quiescence-lock", "compatibility-tuple-match", "support-file-pre-state")]
        self.assertFalse(runtime_target_evidence_authorizes_real_writes(
            items, action_class="managed-skill-file", requires_helper_invocation=False))
        # A-cannot-authorize-B: a second realpath in the bundle
        other = build_runtime_target_evidence(
            evidence_type="native-loader", platform="linux", path_style="posix",
            observed_behavior="other host", checks={"x": 1},
            target_realpath="/other/.openclaw", managed_skills_realpath="/other/.openclaw/skills",
            runtime_realpath="/other/rt")
        self.assertFalse(runtime_target_evidence_authorizes_real_writes(
            items + [other], action_class="managed-support-file", requires_helper_invocation=False))

    def test_helper_invocation_detection_from_action_list(self) -> None:
        self.assertTrue(runtime_action_is_executable({"target_relpath": "x/tool.py", "mode": "0644"}))
        self.assertTrue(runtime_action_is_executable({"target_relpath": "x/run.sh", "mode": "0664"}))  # 0664+.sh
        self.assertTrue(runtime_action_is_executable({"target_relpath": "x/data.json", "mode": "0755"}))  # mode
        self.assertFalse(runtime_action_is_executable({"target_relpath": "x/data.json", "mode": "0644"}))
        self.assertTrue(runtime_actions_require_helper_invocation(
            [{"target_relpath": "a.json", "mode": "0644"}, {"target_relpath": "b.py", "mode": "0644"}]))
        self.assertFalse(runtime_actions_require_helper_invocation(
            [{"target_relpath": "a.json", "mode": "0644"}, {"target_relpath": "b.yaml", "mode": "0644"}]))

    def test_host_key_signature_roundtrip_and_tamper(self) -> None:
        ev = _ev("runtime-pre-state")
        key = b"per-host-secret-key-bytes"
        signed = sign_runtime_evidence(ev, key=key)
        self.assertTrue(verify_runtime_evidence_signature(signed, key=key))
        # wrong key
        self.assertFalse(verify_runtime_evidence_signature(signed, key=b"different-key"))
        # tampered payload
        tampered = dict(signed)
        tampered["observed_behavior"] = "forged"
        self.assertFalse(verify_runtime_evidence_signature(tampered, key=key))
        # unsigned evidence
        self.assertFalse(verify_runtime_evidence_signature(ev, key=key))


if __name__ == "__main__":
    unittest.main()
