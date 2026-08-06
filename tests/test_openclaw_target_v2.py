from __future__ import annotations

import io
import json
import os
import stat
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
    attest_openclaw_executable,
    openclaw_target_state_file,
    probe_openclaw_target,
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
    openclaw_skill_file_attestation,
    validate_openclaw_target_home,
)
from installer.ai_agents_skills.render import canonical_skill_path, render_skill_md
from installer.ai_agents_skills.state import sha256_file


CAPTURED_AT = "2026-06-12T00:00:00Z"


def _attested_node_available() -> bool:
    """Mirror the production /usr/bin/node attestation (root-owned, nlink 1)."""
    try:
        info = os.lstat("/usr/bin/node")
    except OSError:
        return False
    return (
        stat.S_ISREG(info.st_mode)
        and int(info.st_nlink) == 1
        and int(info.st_uid) == 0
        and not stat.S_IMODE(info.st_mode) & 0o022
        and bool(stat.S_IMODE(info.st_mode) & 0o111)
    )


@unittest.skipUnless(
    os.name == "posix",
    "the strict Windows private-path DACL guard rejects runner %TEMP% by design",
)
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
                    canonical_source_hash=canonical_hash("model-router"),
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

    def test_identical_preexisting_file_is_attested_without_rewrite(self) -> None:
        with openclaw_root() as root:
            content = skill_content("model-router")
            target = root / ".openclaw" / "skills" / "model-router" / "SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text(content, encoding="utf-8")
            before = openclaw_skill_file_attestation(target)
            manifest = build_manifest(root, "model-router", content)
            self.assertEqual(manifest["actions"][0]["operation"], "no-op")
            approved = approve_target_manifest(manifest, reviewer="unit-test", reviewed_at=CAPTURED_AT)

            dry_run = apply_target_manifest(approved, root, dry_run=True)
            self.assertEqual(dry_run["actions"][0]["reason"], "ready-to-adopt")
            result = apply_target_manifest(
                approved,
                root,
                dry_run=False,
                confirm_phrase=OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE,
                post_apply_check=False,
            )

            self.assertFalse(result["actions"][0]["applied"])
            self.assertTrue(result["actions"][0]["adopted"])
            self.assertEqual(openclaw_skill_file_attestation(target), before)
            state = json.loads(openclaw_target_state_file(root).read_text(encoding="utf-8"))
            self.assertEqual(len(state["artifacts"]), 1)
            record = state["artifacts"][0]
            self.assertEqual(record["attestation"], "adopted-identical")
            self.assertEqual(record["source_hash"], manifest["actions"][0]["expected_hash"])
            self.assertEqual(record["installed_signature"], before)

            uninstall = uninstall_target_manifest(
                root,
                manifest_id=approved["manifest_id"],
                dry_run=False,
                confirm_phrase=OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE,
            )
            self.assertTrue(target.exists())
            self.assertEqual(uninstall["actions"][0]["operation"], "forget-adopted")

    def test_identical_file_identity_swap_after_approval_blocks_adoption(self) -> None:
        with openclaw_root() as root:
            content = skill_content("model-router")
            target = root / ".openclaw" / "skills" / "model-router" / "SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text(content, encoding="utf-8")
            manifest = build_manifest(root, "model-router", content)
            approved = approve_target_manifest(manifest, reviewer="unit-test", reviewed_at=CAPTURED_AT)
            replacement = target.with_name("replacement.md")
            replacement.write_text(content, encoding="utf-8")
            replacement.replace(target)

            with self.assertRaisesRegex(ValueError, "target-pre-state-drift"):
                apply_target_manifest(
                    approved,
                    root,
                    dry_run=False,
                    confirm_phrase=OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE,
                    post_apply_check=False,
                )

            self.assertEqual(target.read_text(encoding="utf-8"), content)

    def test_identical_group_writable_file_is_not_adopted(self) -> None:
        with openclaw_root() as root:
            content = skill_content("model-router")
            target = root / ".openclaw" / "skills" / "model-router" / "SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text(content, encoding="utf-8")
            target.chmod(0o664)

            with self.assertRaisesRegex(ValueError, "group/world writable"):
                build_manifest(root, "model-router", content)

    def test_identical_file_under_group_writable_parent_is_blocked(self) -> None:
        with openclaw_root() as root:
            content = skill_content("model-router")
            target = root / ".openclaw" / "skills" / "model-router" / "SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text(content, encoding="utf-8")
            manifest = build_manifest(root, "model-router", content)
            approved = approve_target_manifest(
                manifest,
                reviewer="unit-test",
                reviewed_at=CAPTURED_AT,
            )
            target.parent.chmod(0o775)

            dry_run = apply_target_manifest(approved, root, dry_run=True)
            self.assertTrue(dry_run["actions"][0]["blocked"])
            self.assertIn("parent is group/world writable", dry_run["actions"][0]["reason"])

    def test_adopted_file_is_rehashed_after_native_loader_use(self) -> None:
        with openclaw_root() as root:
            content = skill_content("model-router")
            target = root / ".openclaw" / "skills" / "model-router" / "SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text(content, encoding="utf-8")
            approved = approve_target_manifest(
                build_manifest(root, "model-router", content),
                reviewer="unit-test",
                reviewed_at=CAPTURED_AT,
            )

            def mutate_after_visibility(*_args: object, **_kwargs: object) -> bool:
                replacement = target.with_name("replacement.md")
                replacement.write_text("hostile replacement\n", encoding="utf-8")
                replacement.replace(target)
                return True

            with patch(
                "installer.ai_agents_skills.openclaw_target_apply.openclaw_skill_visible",
                side_effect=mutate_after_visibility,
            ), self.assertRaisesRegex(ValueError, "changed after apply/adoption"):
                apply_target_manifest(
                    approved,
                    root,
                    dry_run=False,
                    confirm_phrase=OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE,
                    post_apply_check=True,
                )

            state = json.loads(openclaw_target_state_file(root).read_text(encoding="utf-8"))
            self.assertEqual(state["artifacts"], [])
            self.assertEqual(state["transactions"][-1]["status"], "rolled-back-after-failure")

    def test_repeated_identical_attestation_upserts_one_state_record(self) -> None:
        with openclaw_root() as root:
            content = skill_content("model-router")
            target = root / ".openclaw" / "skills" / "model-router" / "SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text(content, encoding="utf-8")
            for _ in range(2):
                approved = approve_target_manifest(
                    build_manifest(root, "model-router", content),
                    reviewer="unit-test",
                    reviewed_at=CAPTURED_AT,
                )
                apply_target_manifest(
                    approved,
                    root,
                    dry_run=False,
                    confirm_phrase=OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE,
                    post_apply_check=False,
                )

            state = json.loads(openclaw_target_state_file(root).read_text(encoding="utf-8"))
            self.assertEqual(len(state["artifacts"]), 1)
            self.assertEqual(state["artifacts"][0]["attestation"], "adopted-identical")

    def test_six_skill_restore_inventory_records_every_applied_skill(self) -> None:
        skills = [
            "classroom50",
            "autonomous-research-loop",
            "course-canvas",
            "course-db",
            "course-google-classroom",
            "vnu-eoffice",
        ]
        with openclaw_root() as root:
            for index, skill in enumerate(skills):
                action_class = "canary-skill-file" if index == 0 else "managed-skill-file"
                approved = approve_target_manifest(
                    build_manifest(root, skill, skill_content(skill), action_class=action_class),
                    reviewer="unit-test",
                    reviewed_at=CAPTURED_AT,
                )
                apply_target_manifest(
                    approved,
                    root,
                    dry_run=False,
                    confirm_phrase=OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE,
                    post_apply_check=False,
                )

            state = json.loads(openclaw_target_state_file(root).read_text(encoding="utf-8"))
            self.assertEqual({record["skill"] for record in state["artifacts"]}, set(skills))
            self.assertEqual(len(state["artifacts"]), len(skills))
            self.assertEqual(len(state["runs"]), len(skills))
            self.assertTrue(all(item["status"] == "applied" for item in state["transactions"]))

    def test_failed_native_check_rolls_back_adoption_record_not_file(self) -> None:
        with openclaw_root() as root:
            content = skill_content("model-router")
            target = root / ".openclaw" / "skills" / "model-router" / "SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text(content, encoding="utf-8")
            approved = approve_target_manifest(
                build_manifest(root, "model-router", content),
                reviewer="unit-test",
                reviewed_at=CAPTURED_AT,
            )

            with patch(
                "installer.ai_agents_skills.openclaw_target_apply.openclaw_skill_visible",
                return_value=False,
            ), self.assertRaisesRegex(ValueError, "native loader"):
                apply_target_manifest(
                    approved,
                    root,
                    dry_run=False,
                    confirm_phrase=OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE,
                    post_apply_check=True,
                )

            self.assertTrue(target.exists())
            state = json.loads(openclaw_target_state_file(root).read_text(encoding="utf-8"))
            self.assertEqual(state["artifacts"], [])
            self.assertEqual(state["transactions"][-1]["status"], "rolled-back-after-failure")

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

    @unittest.skipUnless(
        _attested_node_available(),
        "the attested root-owned /usr/bin/node interpreter is unavailable",
    )
    def test_quiescence_ignores_empty_persistent_locks_directory(self) -> None:
        with openclaw_root() as root:
            (root / ".openclaw" / "locks").mkdir()
            executable = fake_openclaw(root)
            with patch(
                "installer.ai_agents_skills.openclaw_target_apply._trusted_process_snapshot",
                return_value=("ok", [("123", "bash", ["bash"])]),
            ):
                result = quiescence_checks(root, openclaw_bin=str(executable))

            self.assertTrue(result["quiescent"])
            self.assertEqual(result["existing_lock_paths"], [])
            self.assertEqual(result["process_enumeration"], "ok")

    @unittest.skipUnless(
        _attested_node_available(),
        "the attested root-owned /usr/bin/node interpreter is unavailable",
    )
    def test_quiescence_detects_openclaw_gateway_process(self) -> None:
        with openclaw_root() as root:
            executable = fake_openclaw(root)
            with patch(
                "installer.ai_agents_skills.openclaw_target_apply._trusted_process_snapshot",
                return_value=(
                    "ok",
                    [("1172", "node", ["/usr/bin/node", "/tmp/openclaw/dist/index.js", "gateway"])],
                ),
            ):
                result = quiescence_checks(root, openclaw_bin=str(executable))

            self.assertFalse(result["quiescent"])
            self.assertEqual(len(result["process_matches"]), 1)
            self.assertEqual(
                result["process_matches"][0],
                {"pid": "1172", "comm": "node", "reason": "openclaw-node-entrypoint"},
            )

    @unittest.skipUnless(
        _attested_node_available(),
        "the attested root-owned /usr/bin/node interpreter is unavailable",
    )
    def test_openclaw_probe_uses_attested_absolute_binary_and_scrubbed_environment(self) -> None:
        with openclaw_root() as root:
            observed = root / "observed-env"
            executable = fake_openclaw(root, observed_env=observed)
            hostile = root / "hostile-bin"
            hostile.mkdir()
            hostile_marker = root / "hostile-ran"
            hostile_openclaw = hostile / "openclaw"
            hostile_openclaw.write_text(
                f"#!/bin/sh\n: > {hostile_marker}\nexit 97\n",
                encoding="utf-8",
            )
            hostile_openclaw.chmod(0o700)
            with patch.dict(
                os.environ,
                {
                    "PATH": str(hostile),
                    "OPENAI_API_KEY": "openai-canary-must-not-cross",
                    "HCLOUD_TOKEN": "hcloud-canary-must-not-cross",
                    "AAS_PROVIDER_SECRETS_FILE": "provider-pointer-must-not-cross",
                },
                clear=False,
            ), patch(
                "installer.ai_agents_skills.openclaw_target_apply._trusted_process_snapshot",
                return_value=("ok", []),
            ):
                result = probe_openclaw_target(
                    root,
                    openclaw_bin=str(executable),
                    platform="linux",
                    captured_at=CAPTURED_AT,
                )

            self.assertEqual(result["status"], "ok")
            self.assertFalse(hostile_marker.exists())
            child_env = observed.read_text(encoding="utf-8")
            self.assertIn("PATH=/usr/bin:/bin", child_env)
            self.assertIn(f"OPENCLAW_STATE_DIR={root / '.openclaw'}", child_env)
            for canary in (
                "openai-canary-must-not-cross",
                "hcloud-canary-must-not-cross",
                "provider-pointer-must-not-cross",
            ):
                self.assertNotIn(canary, child_env)

    def test_openclaw_binary_rejects_path_lookup_and_symlink(self) -> None:
        with openclaw_root() as root:
            with self.assertRaisesRegex(ValueError, "absolute"):
                attest_openclaw_executable("openclaw")
            unsafe_bin = root / "unsafe" / "bin"
            unsafe_bin.mkdir(parents=True)
            unsafe = unsafe_bin / "openclaw"
            unsafe.symlink_to(root / "outside.mjs")
            with self.assertRaisesRegex(ValueError, "pinned npm"):
                attest_openclaw_executable(unsafe)


@contextmanager
def openclaw_root():
    with tempfile.TemporaryDirectory() as tmp:
        previous_umask = os.umask(0o077)
        try:
            root = Path(tmp)
            (root / ".openclaw" / "skills").mkdir(parents=True)
            (root / ".openclaw").chmod(0o700)
            (root / ".openclaw" / "skills").chmod(0o700)
            validate_openclaw_target_home(root)
            yield root
        finally:
            os.umask(previous_umask)


def skill_content(skill: str) -> str:
    manifests = load_manifests()
    return render_skill_md(skill, manifests["skills"]["skills"][skill], "openclaw")


def fake_openclaw(root: Path, *, observed_env: Path | None = None) -> Path:
    prefix = root / "npm"
    bin_dir = prefix / "bin"
    package_dir = prefix / "lib" / "node_modules" / "openclaw"
    bin_dir.mkdir(parents=True)
    package_dir.mkdir(parents=True)
    for directory in (prefix, bin_dir, prefix / "lib", prefix / "lib" / "node_modules", package_dir):
        directory.chmod(0o700)
    target = package_dir / "openclaw.mjs"
    observed_line = (
        f"writeFileSync({str(observed_env)!r}, Object.entries(process.env).map(([k,v]) => `${{k}}=${{v}}`).join('\\n'));\n"
        if observed_env is not None
        else ""
    )
    target.write_text(
        "#!/usr/bin/env node\n"
        "import { writeFileSync } from 'node:fs';\n"
        + observed_line
        + "const command = process.argv.slice(2).join(' ');\n"
        + "if (command === '--version') console.log('OpenClaw test');\n"
        + "else if (command === 'skills --help') console.log('list');\n"
        + "else if (command === 'skills list --json') console.log(JSON.stringify("
        + f"{{managedSkillsDir:{str(root / '.openclaw' / 'skills')!r},skills:[]}}));\n"
        + "else process.exitCode = 2;\n",
        encoding="utf-8",
    )
    target.chmod(0o700)
    executable = bin_dir / "openclaw"
    executable.symlink_to("../lib/node_modules/openclaw/openclaw.mjs")
    return executable


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
        canonical_source_hash=canonical_hash(skill),
        evidence_items=evidence_items(root, include_canary=action_class == "managed-skill-file"),
        action_class=action_class,
        created_at=CAPTURED_AT,
    )


def canonical_hash(skill: str) -> str:
    digest = sha256_file(canonical_skill_path(skill))
    if digest is None:
        raise AssertionError(f"missing canonical source fixture: {skill}")
    return digest


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
