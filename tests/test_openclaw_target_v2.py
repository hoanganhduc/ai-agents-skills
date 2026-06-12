from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from installer.ai_agents_skills.cli import main
from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.openclaw_target_apply import (
    apply_target_manifest,
    openclaw_target_state_file,
    quiescence_checks,
    uninstall_target_manifest,
)
from installer.ai_agents_skills.openclaw_target_evidence import build_authorizing_target_evidence
from installer.ai_agents_skills.openclaw_target_manifest import (
    approve_target_manifest,
    build_skill_file_target_manifest,
    target_manifest_authorizes_real_writes,
)
from installer.ai_agents_skills.openclaw_target_paths import (
    OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE,
    checked_openclaw_target_relative_path,
    validate_openclaw_target_home,
)
from installer.ai_agents_skills.render import render_skill_md


CAPTURED_AT = "2026-06-12T00:00:00Z"


class OpenClawTargetV2Tests(unittest.TestCase):
    def test_canary_manifest_approves_applies_and_uninstalls_skill_file(self) -> None:
        with openclaw_root() as root:
            content = skill_content("model-router")
            manifest = build_manifest(root, "model-router", content, action_class="canary-skill-file")

            self.assertFalse(target_manifest_authorizes_real_writes(manifest))
            approved = approve_target_manifest(manifest, reviewer="unit-test", reviewed_at=CAPTURED_AT)
            self.assertTrue(target_manifest_authorizes_real_writes(approved))

            dry_run = apply_target_manifest(approved, root, dry_run=True)
            self.assertEqual(dry_run["actions"][0]["reason"], "ready")

            result = apply_target_manifest(
                approved,
                root,
                dry_run=False,
                confirm_phrase=OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE,
                post_apply_check=False,
            )
            target = root / ".openclaw" / "skills" / "model-router" / "SKILL.md"
            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), content)
            self.assertTrue(openclaw_target_state_file(root).exists())
            self.assertFalse((root / ".openclaw" / ".ai-agents-skills").exists())
            self.assertEqual(result["actions"][0]["installed_hash"], approved["actions"][0]["expected_hash"])

            uninstall = uninstall_target_manifest(
                root,
                manifest_id=approved["manifest_id"],
                dry_run=False,
                confirm_phrase=OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE,
            )
            self.assertFalse(target.exists())
            self.assertIn(f"{approved['manifest_id']}:{approved['actions'][0]['action_id']}", uninstall["removed"])
            self.assertTrue((root / ".openclaw" / "skills").exists())

    def test_managed_skill_manifest_requires_canary_evidence(self) -> None:
        with openclaw_root() as root:
            content = skill_content("model-router")
            evidence = evidence_items(root, include_canary=False)

            with self.assertRaisesRegex(ValueError, "does not authorize manifest action class"):
                build_skill_file_target_manifest(
                    root=root,
                    skill="model-router",
                    content=content,
                    evidence_items=evidence,
                    action_class="managed-skill-file",
                    created_at=CAPTURED_AT,
                )

            manifest = build_manifest(root, "model-router", content, action_class="managed-skill-file")
            approved = approve_target_manifest(manifest, reviewer="unit-test", reviewed_at=CAPTURED_AT)
            self.assertTrue(target_manifest_authorizes_real_writes(approved))

    def test_apply_requires_approval_and_confirmation(self) -> None:
        with openclaw_root() as root:
            manifest = build_manifest(root, "model-router", skill_content("model-router"))

            with self.assertRaisesRegex(ValueError, "must be approved before apply"):
                apply_target_manifest(
                    manifest,
                    root,
                    dry_run=False,
                    confirm_phrase=OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE,
                    post_apply_check=False,
                )

            approved = approve_target_manifest(manifest, reviewer="unit-test", reviewed_at=CAPTURED_AT)
            with self.assertRaisesRegex(ValueError, "confirmation phrase did not match"):
                apply_target_manifest(approved, root, dry_run=False, post_apply_check=False)

    def test_unmanaged_existing_file_blocks_manifest(self) -> None:
        with openclaw_root() as root:
            target = root / ".openclaw" / "skills" / "model-router" / "SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text("user-owned OpenClaw skill\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "already exists"):
                build_manifest(root, "model-router", skill_content("model-router"))

    def test_pre_state_drift_blocks_apply_without_overwrite(self) -> None:
        with openclaw_root() as root:
            manifest = build_manifest(root, "model-router", skill_content("model-router"))
            approved = approve_target_manifest(manifest, reviewer="unit-test", reviewed_at=CAPTURED_AT)
            target = root / ".openclaw" / "skills" / "model-router" / "SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text("late user file\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "target-pre-state-drift"):
                apply_target_manifest(
                    approved,
                    root,
                    dry_run=False,
                    confirm_phrase=OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE,
                    post_apply_check=False,
                )

            self.assertEqual(target.read_text(encoding="utf-8"), "late user file\n")

    def test_no_go_relative_paths_are_rejected(self) -> None:
        bad_paths = [
            "skills/model-router/references/helper.md",
            "skills/model-router/scripts/run.sh",
            "openclaw.json",
            "plugins/example.json",
            "skills/../model-router/SKILL.md",
            "/skills/model-router/SKILL.md",
        ]
        for relative_path in bad_paths:
            with self.subTest(relative_path=relative_path):
                with self.assertRaises(ValueError):
                    checked_openclaw_target_relative_path(relative_path, action_class="managed-skill-file")

    def test_cli_target_manifest_approve_and_apply_lifecycle(self) -> None:
        with openclaw_root() as root:
            evidence_paths = []
            for index, evidence in enumerate(evidence_items(root, include_canary=True)):
                path = root / f"evidence-{index}.json"
                path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                evidence_paths.extend(["--evidence", str(path)])

            manifest_payload = run_cli_json(
                [
                    "--root",
                    str(root),
                    "--json",
                    "openclaw-target-dry-run-manifest",
                    "--skill",
                    "model-router",
                    "--action-class",
                    "managed-skill-file",
                    "--created-at",
                    CAPTURED_AT,
                    *evidence_paths,
                ]
            )
            manifest_path = root / "target-manifest.json"
            manifest_path.write_text(json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            approved_payload = run_cli_json(
                [
                    "--root",
                    str(root),
                    "--json",
                    "openclaw-target-approve-manifest",
                    "--manifest",
                    str(manifest_path),
                    "--reviewer",
                    "unit-test",
                    "--reviewed-at",
                    CAPTURED_AT,
                ]
            )
            approved_path = root / "target-manifest-approved.json"
            approved_path.write_text(json.dumps(approved_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            apply_payload = run_cli_json(
                [
                    "--root",
                    str(root),
                    "--json",
                    "openclaw-target-apply-manifest",
                    "--manifest",
                    str(approved_path),
                    "--apply",
                    "--confirm-openclaw-real-write",
                    OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE,
                ]
            )

            self.assertFalse(apply_payload["dry_run"])
            self.assertTrue((root / ".openclaw" / "skills" / "model-router" / "SKILL.md").exists())

    def test_quiescence_ignores_empty_persistent_locks_directory(self) -> None:
        with openclaw_root() as root:
            (root / ".openclaw" / "locks").mkdir()
            completed = subprocess.CompletedProcess(
                ["ps"],
                0,
                stdout="123 bash bash\n",
                stderr="",
            )
            with patch("installer.ai_agents_skills.openclaw_target_apply.subprocess.run", return_value=completed):
                result = quiescence_checks(root)

            self.assertTrue(result["quiescent"])
            self.assertEqual(result["existing_lock_paths"], [])

    def test_quiescence_detects_openclaw_gateway_process(self) -> None:
        with openclaw_root() as root:
            completed = subprocess.CompletedProcess(
                ["ps"],
                0,
                stdout="1172 node /usr/bin/node /tmp/openclaw/dist/index.js gateway --port 18789\n",
                stderr="",
            )
            with patch("installer.ai_agents_skills.openclaw_target_apply.subprocess.run", return_value=completed):
                result = quiescence_checks(root)

            self.assertFalse(result["quiescent"])
            self.assertEqual(len(result["process_matches"]), 1)


@contextmanager
def openclaw_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".openclaw" / "skills").mkdir(parents=True)
        validate_openclaw_target_home(root)
        yield root


def skill_content(skill: str) -> str:
    manifests = load_manifests()
    return render_skill_md(skill, manifests["skills"]["skills"][skill], "openclaw")


def build_manifest(
    root: Path,
    skill: str,
    content: str,
    *,
    action_class: str = "canary-skill-file",
) -> dict[str, object]:
    return build_skill_file_target_manifest(
        root=root,
        skill=skill,
        content=content,
        evidence_items=evidence_items(root, include_canary=action_class == "managed-skill-file"),
        action_class=action_class,
        created_at=CAPTURED_AT,
    )


def evidence_items(root: Path, *, include_canary: bool) -> list[dict[str, object]]:
    paths = validate_openclaw_target_home(root)
    base_types = ["native-loader", "native-managed-skill-root", "target-pre-state", "quiescence-lock"]
    if include_canary:
        base_types.append("native-managed-skill-canary")
    return [
        build_authorizing_target_evidence(
            evidence_type=evidence_type,
            platform="linux",
            path_style="posix",
            observed_behavior=f"{evidence_type} fixture",
            target_realpath=paths["home_realpath"],
            managed_skills_realpath=paths["managed_skills_realpath"],
            checks={"fixture": True, "evidence_type": evidence_type},
            captured_at=CAPTURED_AT,
            openclaw_version="OpenClaw test",
        )
        for evidence_type in base_types
    ]


def run_cli_json(argv: list[str]) -> dict[str, object]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv)
    if code != 0:
        raise AssertionError(f"CLI failed with code {code}\nstdout={stdout.getvalue()}\nstderr={stderr.getvalue()}")
    payload = json.loads(stdout.getvalue())
    if not isinstance(payload, dict):
        raise AssertionError("CLI JSON output was not an object")
    return payload


if __name__ == "__main__":
    unittest.main()
