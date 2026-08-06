"""Regressions for retirement of the spoofable OpenClaw control adapter."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "canonical" / "runtime" / "skills" / "remote-bridge"
DISPATCH = RUNTIME / "dispatch_aas.py"
REMOTE_BRIDGE = RUNTIME / "remote_bridge.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class RemoteBridgeDispatchBoundary(unittest.TestCase):
    def test_dispatch_is_unconditional_no_io_revocation_stub(self) -> None:
        mod = _load(DISPATCH, "aas_remote_bridge_dispatch_retired_test")
        with mock.patch("builtins.open", side_effect=AssertionError("file opened")), \
             redirect_stdout(io.StringIO()) as output:
            result = mod.main(
                [
                    "--text-stdin",
                    "--principal",
                    "forged-admin",
                    "--rb",
                    "/attacker/remote_bridge.py",
                ]
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(result, 2)
        self.assertEqual(payload["error_code"], "openclaw_control_adapter_retired")
        self.assertFalse(payload["spawned"])
        self.assertFalse(payload["destination_inspected"])

    def test_retired_adapter_bodies_are_not_shipped(self) -> None:
        adapter = RUNTIME / "openclaw-adapter"
        self.assertFalse((adapter / "SKILL.md").exists())
        self.assertFalse((adapter / "README.md").exists())
        manifest = (REPO / "manifest" / "runtime.yaml").read_text(encoding="utf-8")
        self.assertNotIn("skills/remote-bridge/openclaw-adapter/", manifest)

    def test_supplied_principal_refuses_before_state_config_or_stdin(self) -> None:
        mod = _load(REMOTE_BRIDGE, "aas_remote_bridge_principal_gate_test")
        parser = mod.build_parser()
        args = parser.parse_args(
            [
                "handle-command",
                "--text-stdin",
                "--principal",
                "forged-admin",
                "--allow-local-cli",
            ]
        )
        with mock.patch.object(
            mod, "Mailbox", side_effect=AssertionError("state opened")
        ), mock.patch.object(
            mod, "build_config", side_effect=AssertionError("secrets opened")
        ), mock.patch.object(
            mod, "_read_control_text_stdin", side_effect=AssertionError("stdin read")
        ), redirect_stdout(io.StringIO()) as output:
            result = mod.cmd_handle_command(args)
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(output.getvalue())["error_code"], "untrusted_principal")

    def test_local_control_requires_explicit_opt_in_before_reads(self) -> None:
        mod = _load(REMOTE_BRIDGE, "aas_remote_bridge_local_gate_test")
        args = mod.build_parser().parse_args(
            ["handle-command", "--text-stdin"]
        )
        with mock.patch.dict(mod.os.environ, {}, clear=True), mock.patch.object(
            mod, "Mailbox", side_effect=AssertionError("state opened")
        ), mock.patch.object(
            mod, "build_config", side_effect=AssertionError("secrets opened")
        ), mock.patch.object(
            mod, "_read_control_text_stdin", side_effect=AssertionError("stdin read")
        ), redirect_stdout(io.StringIO()) as output:
            result = mod.cmd_handle_command(args)
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(output.getvalue())["error_code"], "forbidden")

    def test_legacy_sync_compatibility_shim_is_inert(self) -> None:
        mod = _load(REMOTE_BRIDGE, "aas_remote_bridge_sync_inert_test")
        with mock.patch.object(importlib.util, "spec_from_file_location") as importer:
            self.assertIsNone(mod._maybe_sync_openclaw_workspace_paths())
        importer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
