"""Fail-closed contract for native Windows installer mutations."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer.ai_agents_skills import antigravity_fixup as antigravity_module
from installer.ai_agents_skills import apply as apply_module
from installer.ai_agents_skills import lifecycle
from installer.ai_agents_skills import openclaw_apply
from installer.ai_agents_skills import openclaw_runtime_target_apply
from installer.ai_agents_skills import openclaw_target_apply
from installer.ai_agents_skills import windows_security


class WindowsMutationGateTests(unittest.TestCase):
    def test_all_installer_target_mutations_fail_before_input_or_path_access(self) -> None:
        missing = Path("C:/definitely-missing/aas-input.json")
        root = Path("C:/definitely-missing/aas-root")
        operations = (
            lambda: apply_module.apply_plan(root, {"actions": []}, dry_run=False),
            lambda: lifecycle.uninstall(root, dry_run=False),
            lambda: lifecycle.rollback(root, dry_run=False),
            lambda: openclaw_apply.apply_manifest({}, root, dry_run=False),
            lambda: openclaw_apply.uninstall_manifest(root, dry_run=False),
            lambda: openclaw_target_apply.apply_target_manifest({}, root, dry_run=False),
            lambda: openclaw_target_apply.uninstall_target_manifest(root, dry_run=False),
            lambda: openclaw_runtime_target_apply.apply_runtime_target_manifest_file(
                missing,
                root,
                runtime_root=root / "runtime",
                dry_run=False,
            ),
            lambda: antigravity_module.antigravity_fixup(root, apply=True),
        )
        with mock.patch.object(
            windows_security,
            "host_is_native_windows",
            return_value=True,
        ):
            for operation in operations:
                with self.subTest(operation=operation), self.assertRaisesRegex(
                    windows_security.WindowsSecurityError,
                    "native Windows mutation is disabled",
                ):
                    operation()

    def test_generic_dry_run_remains_available_on_native_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            windows_security,
            "host_is_native_windows",
            return_value=True,
        ):
            root = Path(temporary)
            apply_result = apply_module.apply_plan(
                root,
                {"actions": []},
                dry_run=True,
            )
            uninstall_result = lifecycle.uninstall(root, dry_run=True)
            rollback_result = lifecycle.rollback(root, dry_run=True)

        self.assertTrue(apply_result["dry_run"])
        self.assertTrue(uninstall_result["dry_run"])
        self.assertTrue(rollback_result["dry_run"])

    def test_non_windows_hosts_do_not_trip_the_gate(self) -> None:
        with mock.patch.object(
            windows_security,
            "host_is_native_windows",
            return_value=False,
        ):
            windows_security.require_handle_bound_mutation("test mutation")


if __name__ == "__main__":
    unittest.main()
