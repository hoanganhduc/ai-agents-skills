import json
import unittest
from pathlib import Path, PurePosixPath

from installer.ai_agents_skills.agents import DEFAULT_AGENT_NAMES


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifest" / "target-state.yaml"


class TargetStateContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_v3_declares_every_installer_target_once(self) -> None:
        self.assertEqual(self.data["schema"], "ai-agents-skills.target-state.v3")
        self.assertEqual(self.data["schema_version"], 3)
        installer_targets = {
            name
            for name, target in self.data["targets"].items()
            if not target.get("inventory_only")
        }
        self.assertEqual(installer_targets, set(DEFAULT_AGENT_NAMES))

    def test_file_delivery_queue_declares_distinct_host_authority_and_replay_state(self) -> None:
        queue = self.data["runtime_credential_authorities"]["file-delivery-queue"]
        self.assertEqual(queue["kind"], "strict-json-file")
        self.assertEqual(
            queue["path"],
            ".config/ai-agents-skills/file-delivery-queue.json",
        )
        self.assertEqual(queue["pointer_env"], "AAS_FILE_DELIVERY_SECRETS_FILE")
        self.assertEqual(
            queue["exact_keys"],
            [
                "version",
                "hmac_key_hex",
                "allowed",
                "max_job_age_seconds",
                "max_media_bytes",
                "replay_ledger_dir",
                "replay_retention_seconds",
                "max_replay_entries",
            ],
        )
        self.assertEqual(queue["generation_policy"], "explicit-allowlist-required")
        self.assertEqual(
            queue["replay_ledger_field"],
            {
                "exact_value": "aas-host-state:file-delivery-replay",
                "resolution": "authority-home/.local/state/ai-agents-skills/file-delivery-replay",
                "legacy_migration": "rewrite-old-default-only-preserve-other-fields-reject-conflicts",
            },
        )
        self.assertEqual(queue["restore_policy"], "authority")
        self.assertEqual(queue["readiness_when_absent"], "NOT_CONFIGURED")
        self.assertEqual(
            queue["distinct_from"],
            ".openclaw/workspace/.config/file-delivery/secrets.json",
        )
        ledger = queue["replay_ledger"]
        self.assertEqual(
            ledger["default_path"],
            ".local/state/ai-agents-skills/file-delivery-replay",
        )
        self.assertTrue(ledger["must_be_outside_agent_workspace"])
        self.assertTrue(ledger["backup_required"])
        self.assertEqual(
            ledger["retention_contract"],
            "used_at+replay_retention_seconds-strictly-before-now",
        )
        self.assertEqual(
            ledger["retention_minimum"], "max_job_age_seconds+60"
        )
        self.assertEqual(ledger["entry_bound_field"], "max_replay_entries")

    def test_contract_contains_no_absolute_or_parent_paths(self) -> None:
        for target_name, target in self.data["targets"].items():
            home = PurePosixPath(target["home"])
            self.assertFalse(home.is_absolute(), target_name)
            self.assertNotIn("..", home.parts, target_name)
            surfaces = target.get("surfaces", [target])
            for authority in (
                authority
                for surface in surfaces
                for authority in surface["credential_authorities"]
            ):
                if authority["kind"] == "environment":
                    self.assertNotIn("/", authority["path"])
                    continue
                path = PurePosixPath(authority["path"])
                self.assertFalse(path.is_absolute(), (target_name, path))
                self.assertNotIn("..", path.parts, (target_name, path))

    def test_each_target_has_cli_runtime_credentials_and_readiness(self) -> None:
        for target_name, target in self.data["targets"].items():
            with self.subTest(target=target_name):
                surfaces = target.get("surfaces", [target])
                self.assertTrue(surfaces)
                for surface in surfaces:
                    self.assertTrue(surface["cli_candidates"])
                    self.assertEqual(surface["version_argv"], ["--version"])
                    if not target.get("inventory_only"):
                        self.assertTrue(surface["credential_authorities"])
                self.assertTrue(target["runtime_requirements"])
                self.assertIn("cli-version", target["readiness"])
                if not target.get("inventory_only"):
                    self.assertIn("managed-skill-visibility", target["readiness"])

    def test_aider_is_inventory_only_and_fails_closed_without_native_authority(self) -> None:
        aider = self.data["targets"]["aider"]
        self.assertTrue(aider["inventory_only"])
        self.assertEqual(aider["cli_candidates"], ["aider"])
        self.assertEqual(aider["credential_authorities"], [])
        self.assertEqual(aider["readiness_when_authority_absent"], "NOT_CONFIGURED")
        self.assertEqual(aider["restore_policy"], "reauth-native-configuration")

    def test_github_cli_is_explicitly_classified_as_non_agent_integration(self) -> None:
        github = self.data["software_integrations"]["github-cli"]
        self.assertEqual(github["cli_candidates"], ["gh"])
        self.assertEqual(github["classification"], "non-agent-integration")
        self.assertEqual(
            github["credential_authorities"],
            [
                {
                    "kind": "file",
                    "path": ".config/gh/hosts.yml",
                    "structure": "github-hosts-v1",
                    "restore_policy": "portable-session",
                    "fallback_policy": "reauth-if-nonportable",
                }
            ],
        )
        self.assertEqual(github["readiness"], ["cli-version", "auth-structural"])
        self.assertTrue(github["declared_exclusion"])

    def test_antigravity_target_distinguishes_agy_and_gemini(self) -> None:
        surfaces = self.data["targets"]["antigravity"]["surfaces"]
        self.assertEqual([surface["id"] for surface in surfaces], ["antigravity", "gemini"])
        self.assertEqual(surfaces[0]["cli_candidates"], ["agy"])
        self.assertEqual(surfaces[1]["cli_candidates"], ["gemini"])

    def test_copilot_declares_the_backed_target_scoped_provider_authority(self) -> None:
        authorities = self.data["targets"]["copilot"]["credential_authorities"]
        projected = [
            authority
            for authority in authorities
            if authority["kind"] == "strict-env-file"
        ]
        self.assertEqual(len(projected), 1)
        self.assertEqual(
            projected[0]["path"],
            ".config/ai-agents-skills/providers/copilot.env",
        )
        self.assertEqual(
            projected[0]["keys"],
            [
                "COPILOT_GITHUB_TOKEN",
                "COPILOT_PROVIDER_API_KEY",
                "COPILOT_PROVIDER_BEARER_TOKEN",
                "GH_TOKEN",
                "GITHUB_TOKEN",
            ],
        )
        self.assertEqual(
            set(projected[0]["allowed_keys"]),
            {
                "COPILOT_GITHUB_TOKEN",
                "COPILOT_PROVIDER_API_KEY",
                "COPILOT_PROVIDER_BEARER_TOKEN",
                "GH_TOKEN",
                "GITHUB_TOKEN",
            },
        )
        self.assertEqual(
            projected[0]["restore_policy"], "target-scoped-projection"
        )
        self.assertIn(
            "credential-projection",
            self.data["targets"]["copilot"]["readiness"],
        )
        self.assertEqual(
            self.data["targets"]["copilot"]["credential_projection"],
            {
                "launcher": ".local/bin/copilot",
                "launcher_source": "system/bin/copilot",
                "closure_loader": ".npm-global/closures/sha256-{source_sha256}-{tree_sha256}/node_modules/@github/copilot/npm-loader.js",
                "compatibility_loader": ".npm-global/lib/node_modules/@github/copilot/npm-loader.js",
                "authority": ".config/ai-agents-skills/providers/copilot.env",
                "pointer_env": "AAS_PROVIDER_SECRETS_FILE",
                "argv": ["--csr-credential-probe"],
                "closure_contract": {
                    "kind": "content-addressed-npm-tree-v1",
                    "required_digest_evidence": [
                        "launcher_source_sha256",
                        "rendered_launcher_sha256",
                        "node_executable_sha256",
                        "npm_source_sha256",
                        "npm_tree_sha256",
                        "npm_loader_sha256",
                    ],
                    "verification_timing": "immediately-before-exec",
                    "missing_status": "NOT_CONFIGURED",
                    "drift_status": "TECHNICAL_FAIL",
                },
                "final_environment": {
                    "inheritance": "deny",
                    "fixed": {"PATH": "/usr/local/bin:/usr/bin:/bin"},
                    "copy_if_set": [
                        "HOME",
                        "LANG",
                        "LC_ALL",
                        "NO_COLOR",
                        "TERM",
                        "TZ",
                        "USER",
                        "XDG_CONFIG_HOME",
                    ],
                    "credential_keys": [
                        "COPILOT_GITHUB_TOKEN",
                        "COPILOT_PROVIDER_API_KEY",
                        "COPILOT_PROVIDER_BEARER_TOKEN",
                        "GH_TOKEN",
                        "GITHUB_TOKEN",
                    ],
                    "pointer_reaches_child": False,
                },
            },
        )

    def test_openclaw_declares_only_the_db_first_agent_auth_authority(self) -> None:
        authorities = self.data["targets"]["openclaw"]["credential_authorities"]
        agent_auth = [
            authority
            for authority in authorities
            if authority.get("evidence") == "agent-auth-closure"
        ]
        self.assertEqual(
            agent_auth,
            [
                {
                    "kind": "glob",
                    "path": ".openclaw/agents/*/agent/openclaw-agent.sqlite",
                    "restore_policy": "authority",
                    "evidence": "agent-auth-closure",
                }
            ],
        )
        self.assertEqual(
            PurePosixPath(agent_auth[0]["path"]).parts,
            (".openclaw", "agents", "*", "agent", "openclaw-agent.sqlite"),
        )
        self.assertNotIn("auth-profiles.json", json.dumps(authorities))

    def test_openclaw_pins_offline_agent_auth_closure_evidence(self) -> None:
        openclaw = self.data["targets"]["openclaw"]
        self.assertEqual(
            openclaw["evidence_contracts"],
            {
                "agent-auth-closure": {
                    "source": "openclaw-runtime-report",
                    "source_schema_version": 1,
                    "source_profile": "full",
                    "source_status": "passed",
                    "payload_key": "agent_auth",
                    "report_schema": "openclaw.agent-auth-closure/v2",
                    "expected_runtime_version": "2026.7.1-2",
                    "report_status": "PASS",
                    "verification_mode": "offline-structural-only",
                    "openclaw_executed": False,
                    "network_enabled": False,
                    "report_fields": [
                        "schema",
                        "status",
                        "runtimeVersion",
                        "verificationMode",
                        "openclawExecuted",
                        "networkEnabled",
                        "agents",
                        "failureCount",
                        "failures",
                    ],
                    "agent_fields": [
                        "agentId",
                        "status",
                        "canonicalStore",
                        "reasons",
                    ],
                    "canonical_store_fields": [
                        "exists",
                        "integrity",
                        "schemaVersion",
                        "appVersion",
                        "authStoreRows",
                        "profileCount",
                        "configured",
                        "authorityJsonValid",
                        "executableSecretRefFree",
                        "credentialSourceKinds",
                        "redactionSentinelFree",
                        "device",
                        "inode",
                        "size",
                        "mtimeNs",
                        "ctimeNs",
                    ],
                    "canonical_store_schema_version": 1,
                    "canonical_store_app_version_policy": (
                        "null-or-exact-runtime-version"
                    ),
                    "credential_source_kinds_allowed": ["env", "file"],
                    "provider_calls_allowed": False,
                }
            },
        )
        self.assertIn("agent-auth-closure", openclaw["readiness"])

    def test_openclaw_generic_readiness_does_not_claim_aas_writer_provenance(self) -> None:
        readiness = self.data["targets"]["openclaw"]["readiness"]
        self.assertNotIn("restricted-target-evidence", readiness)

    def test_secret_values_are_forbidden(self) -> None:
        self.assertFalse(self.data["scope"]["credential_values_allowed"])
        serialized = MANIFEST.read_text(encoding="utf-8").lower()
        for forbidden in ("api_key\":", "access_token\":", "refresh_token\":"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
