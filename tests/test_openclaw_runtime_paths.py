from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import os
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.openclaw_target_paths import path_leak_scan, path_leak_block_reason
from installer.ai_agents_skills.openclaw_runtime_target_paths import neutral_runtime_root_block_reason


class PathLeakScanTest(unittest.TestCase):
    def test_machine_specific_paths_are_flagged(self) -> None:
        self.assertEqual(path_leak_scan("/home" "/ubuntu/.local/share/x"), ["posix-home-path"])
        self.assertEqual(path_leak_scan("bash ~/.codex/runtime/run_skill.sh"), ["codex-runtime-path"])
        self.assertIn("windows-aas-runtime-path", path_leak_scan(r"%LOCALAPPDATA%\ai-agents-skills\runtime"))
        self.assertIn("macos-home-path", path_leak_scan("/Users/alice/x"))

    def test_workspace_is_portable_sandbox_home(self) -> None:
        # HOME=/workspace in the OpenClaw sandbox -> byte-identical everywhere, and it
        # is also the runtime "workspace/" subdir; not a machine-specific leak.
        self.assertEqual(path_leak_scan("cd /workspace/.local"), [])
        self.assertEqual(path_leak_scan("<runtime_root>/workspace/skills/x"), [])

    def test_portable_references_are_allowed(self) -> None:
        for portable in (
            "$HOME/.local/share/ai-agents-skills/runtime",
            "~/.local/share/x",
            r"%USERPROFILE%\Documents",
            "$AAS_RUNTIME_ROOT/runners/run_skill.sh",
            "$AAS_BROKER_ENDPOINT",
        ):
            self.assertEqual(path_leak_scan(portable), [], portable)
        self.assertIsNone(path_leak_block_reason("$HOME/.local/share/ai-agents-skills"))

    def test_block_reason_is_superset_of_legacy_markers(self) -> None:
        self.assertIsNotNone(path_leak_block_reason("uses $CODEX_HOME here"))
        self.assertIsNotNone(path_leak_block_reason(r"%USERPROFILE%\.codex\runtime"))


def _env_is_clean(tmp: Path) -> bool:
    """True if the tempdir has no .git/.stfolder ancestor (so 'clean root' /
    permission assertions aren't masked by a stray marker under /tmp)."""
    reason = neutral_runtime_root_block_reason(tmp / "probe-root")
    return reason is None


class NeutralRuntimeRootTest(unittest.TestCase):
    def test_accepts_clean_neutral_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            if not _env_is_clean(Path(tmp)):
                self.skipTest("tempdir has an uncontrollable .git/.stfolder ancestor")
            root = Path(tmp) / "aas-runtime"
            self.assertIsNone(neutral_runtime_root_block_reason(root))

    def test_rejects_under_openclaw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".openclaw" / "rt"
            reason = neutral_runtime_root_block_reason(root)
            self.assertIsNotNone(reason)
            self.assertIn("(R2)", reason)

    def test_rejects_under_agent_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".codex" / "runtime"
            self.assertIn("(R6)", neutral_runtime_root_block_reason(root) or "")

    def test_rejects_under_syncthing_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            synced = Path(tmp) / "synced"
            synced.mkdir()
            (synced / ".stfolder").mkdir()
            self.assertIn("(R3)", neutral_runtime_root_block_reason(synced / "rt") or "")

    def test_rejects_under_git_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            self.assertIn("(R4)", neutral_runtime_root_block_reason(repo / "rt") or "")

    @unittest.skipUnless(os.name == "posix", "POSIX permission semantics")
    def test_rejects_world_writable_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            if not _env_is_clean(Path(tmp)):
                self.skipTest("tempdir has an uncontrollable .git/.stfolder ancestor")
            ww = Path(tmp) / "ww"
            ww.mkdir()
            os.chmod(ww, 0o777)
            self.assertIn("(R10)", neutral_runtime_root_block_reason(ww / "rt") or "")

    def test_workspace_exception_only_with_flag_and_exact_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".openclaw" / "workspace" / ".local" / "share" / "ai-agents-skills" / "runtime"
            # Without the flag: hard R2 reject.
            self.assertIn("(R2)", neutral_runtime_root_block_reason(root) or "")
            # With the flag + exact ignored-runtime shape: accepted.
            self.assertIsNone(neutral_runtime_root_block_reason(root, allow_workspace_ignored_exception=True))
            # Flag set but a NON-ignored shape under .openclaw: still rejected.
            bad = Path(tmp) / ".openclaw" / "workspace" / "secrets"
            self.assertIn("(R2)", neutral_runtime_root_block_reason(bad, allow_workspace_ignored_exception=True) or "")


if __name__ == "__main__":
    unittest.main()
