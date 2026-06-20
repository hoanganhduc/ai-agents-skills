from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import unittest

from installer.ai_agents_skills.state import (
    STATE_SCHEMA_VERSION,
    build_state_provenance,
    default_state,
    legacy_incomplete_provenance,
    validate_state,
    validate_state_provenance,
)


class StateProvenanceTest(unittest.TestCase):
    def test_default_state_is_v2_without_provenance(self) -> None:
        st = default_state()
        self.assertEqual(st["schema_version"], 2)
        self.assertEqual(STATE_SCHEMA_VERSION, 2)
        self.assertNotIn("provenance", st)  # provenance is per-install, not in the empty default

    def test_validate_accepts_v1_and_v2_rejects_others(self) -> None:
        validate_state({"schema_version": 1, "artifacts": [], "runs": []})
        validate_state({"schema_version": 2, "artifacts": [], "runs": [], "uninstall_records": []})
        with self.assertRaisesRegex(ValueError, "unsupported schema_version"):
            validate_state({"schema_version": 3, "artifacts": []})

    def test_complete_and_legacy_provenance_validate(self) -> None:
        prov = build_state_provenance(
            source_commit="abc123", content_id="content_xyz", installer_version="9.9",
            installed_at="2026-06-20T00:00:00Z", host_id="host-1")
        validate_state({"schema_version": 2, "artifacts": [], "provenance": prov})
        validate_state({"schema_version": 2, "artifacts": [], "provenance": legacy_incomplete_provenance()})

    def test_malformed_provenance_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "provenance must be an object"):
            validate_state_provenance(["not", "a", "dict"])
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_state_provenance({"provenance_version": 1, "evil": "x"})
        with self.assertRaisesRegex(ValueError, "provenance_status is invalid"):
            validate_state_provenance({"provenance_status": "totally-fine"})
        with self.assertRaisesRegex(ValueError, "content_id must be a string"):
            validate_state_provenance({"content_id": 123})

    def test_malformed_provenance_rejected_through_validate_state(self) -> None:
        with self.assertRaises(ValueError):
            validate_state({"schema_version": 2, "artifacts": [], "provenance": {"evil": "x"}})


if __name__ == "__main__":
    unittest.main()
