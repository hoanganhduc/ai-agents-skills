"""Focused regressions for the OpenClaw `/aas` dispatch boundary."""

from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
DISPATCH = (
    REPO
    / "canonical"
    / "runtime"
    / "skills"
    / "remote-bridge"
    / "dispatch_aas.py"
)


class RemoteBridgeDispatchBoundary(unittest.TestCase):
    def _mod(self):
        spec = importlib.util.spec_from_file_location(
            "aas_remote_bridge_dispatch_test", DISPATCH
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_dispatch_never_runs_legacy_sync_and_uses_workspace_state(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "openclaw-workspace"
            helper_dir = workspace / "skills" / "aas-remote-bridge" / "scripts"
            helper_dir.mkdir(parents=True)
            marker = root / "legacy-sync-imported"
            (helper_dir / "sync_remote_bridge_paths.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n",
                encoding="utf-8",
            )
            rb = root / "remote_bridge.py"
            rb.write_text("# test runtime sentinel\n", encoding="utf-8")
            captured: dict[str, object] = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = cmd
                captured["env"] = kwargs["env"]
                captured["input"] = kwargs["input"]
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout='{"ok":true,"human_reply":"ready"}\n',
                    stderr="",
                )

            environ = {
                "HOME": str(root),
                "OPENCLAW_WORKSPACE": str(workspace),
                "AAS_REMOTE_BRIDGE_SYNC": "1",
                "REMOTE_BRIDGE_SECRETS_FILE": str(root / "host-secrets.json"),
                "AAS_REMOTE_BRIDGE_STATE": str(root / "host-state"),
                "HCLOUD_TOKEN": "ambient-hetzner-secret",
                "KAGGLE_API_TOKEN": "ambient-kaggle-secret",
                "OPENAI_API_KEY": "ambient-openai-secret",
                "ZULIP_API_KEY": "ambient-zulip-secret",
            }
            with mock.patch.dict(
                mod.os.environ, environ, clear=True
            ), mock.patch.object(
                mod.sys,
                "stdin",
                io.StringIO("/aas status Bearer secret-dispatch-text-92731"),
            ), mock.patch.object(
                mod,
                "_maybe_sync_paths",
                side_effect=AssertionError("legacy sync was invoked"),
            ) as sync, mock.patch.object(
                mod.subprocess, "run", side_effect=fake_run
            ), redirect_stdout(io.StringIO()):
                result = mod.main(
                    [
                        "--text-stdin",
                        "--principal",
                        "operator",
                        "--rb",
                        str(rb),
                    ]
                )

            self.assertEqual(result, 0)
            sync.assert_not_called()
            self.assertFalse(marker.exists())
            dispatched_cmd = captured["cmd"]
            self.assertIsInstance(dispatched_cmd, list)
            self.assertIn("--text-stdin", dispatched_cmd)
            self.assertNotIn("--allow-local-cli", dispatched_cmd)
            self.assertNotIn("secret-dispatch-text-92731", " ".join(dispatched_cmd))
            self.assertEqual(
                captured["input"],
                "/aas status Bearer secret-dispatch-text-92731",
            )
            dispatched_env = captured["env"]
            self.assertIsInstance(dispatched_env, dict)
            self.assertEqual(
                dispatched_env["REMOTE_BRIDGE_SECRETS_FILE"],
                str(
                    workspace
                    / ".config"
                    / "remote-bridge"
                    / "secrets.json"
                ),
            )
            self.assertEqual(
                dispatched_env["REMOTE_BRIDGE_SECRETS_FILE"],
                str(
                    Path(dispatched_env["XDG_CONFIG_HOME"])
                    / "remote-bridge"
                    / "secrets.json"
                ),
            )
            self.assertEqual(
                dispatched_env["AAS_REMOTE_BRIDGE_STATE"],
                str(workspace / ".remote-bridge-state"),
            )
            for name in (
                "HCLOUD_TOKEN",
                "KAGGLE_API_TOKEN",
                "OPENAI_API_KEY",
                "ZULIP_API_KEY",
                "AAS_REMOTE_BRIDGE_SYNC",
                "OPENCLAW_WORKSPACE",
            ):
                self.assertNotIn(name, dispatched_env)

    def test_adapter_docs_name_only_the_restored_workspace_authority(self) -> None:
        docs = (
            REPO
            / "canonical"
            / "runtime"
            / "skills"
            / "remote-bridge"
            / "openclaw-adapter"
        )
        texts = (
            docs / "SKILL.md",
            docs / "README.md",
            REPO / "targets" / "openclaw" / "README.md",
        )
        text = "\n".join(path.read_text(encoding="utf-8") for path in texts)
        self.assertIn("/workspace/.config/remote-bridge/secrets.json", text)
        self.assertNotIn("/workspace/secrets/remote-bridge/secrets.json", text)
        self.assertNotIn(
            "~/.openclaw/workspace/secrets/remote-bridge/secrets.json", text
        )

    def test_legacy_sync_compatibility_shim_is_inert(self) -> None:
        mod = self._mod()
        with mock.patch.object(importlib.util, "spec_from_file_location") as importer:
            self.assertIsNone(mod._maybe_sync_paths())
        importer.assert_not_called()

    def test_missing_empty_and_local_principal_refuse_before_spawn(self) -> None:
        mod = self._mod()
        for argv in (
            ["--text-stdin"],
            ["--text-stdin", "--principal", ""],
            ["--text-stdin", "--principal", "cli"],
        ):
            with self.subTest(argv=argv), mock.patch.object(
                mod.subprocess, "run"
            ) as run, redirect_stdout(io.StringIO()) as output:
                result = mod.main(argv)
            self.assertEqual(result, 2)
            run.assert_not_called()
            self.assertEqual(
                __import__("json").loads(output.getvalue())["error_code"],
                "missing_principal",
            )

    def test_oversized_stdin_refuses_before_spawn(self) -> None:
        mod = self._mod()
        oversized = "x" * (mod.CONTROL_TEXT_MAX_BYTES + 1)
        with mock.patch.object(
            mod.sys, "stdin", io.StringIO(oversized)
        ), mock.patch.object(mod.subprocess, "run") as run, redirect_stdout(
            io.StringIO()
        ) as output:
            result = mod.main(["--text-stdin", "--principal", "operator"])
        self.assertEqual(result, 2)
        run.assert_not_called()
        self.assertEqual(
            __import__("json").loads(output.getvalue())["error_code"],
            "invalid_text_stdin",
        )


if __name__ == "__main__":
    unittest.main()
