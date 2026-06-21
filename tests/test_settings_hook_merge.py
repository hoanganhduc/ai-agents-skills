from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.apply import apply_plan
from installer.ai_agents_skills.lifecycle import apply_uninstall_action, plan_uninstall_action
from installer.ai_agents_skills.state import load_state

ENTRY = {"hooks": [{"type": "command", "command": "autoloop hook-check"}]}


def _action(settings_path: Path) -> dict:
    return {
        "kind": "json-merge",
        "agent": "claude",
        "skill": "autonomous-research-loop",
        "path": str(settings_path),
        "artifact_type": "settings-hook-merge",
        "artifact_id": "settings-hook:autoloop-stop",
        "operation": "merge",
        "event": "Stop",
        "managed_id": "autoloop-stop",
        "entry": ENTRY,
    }


class SettingsHookMergeLifecycleTests(unittest.TestCase):
    """Apply -> uninstall round trip through the real installer lifecycle."""

    def _round_trip(self, original: dict | None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = root / ".claude" / "settings.json"
            if original is not None:
                settings.parent.mkdir(parents=True)
                settings.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
            apply_plan(root, {"actions": [_action(settings)]}, dry_run=False)
            after_install = json.loads(settings.read_text(encoding="utf-8"))
            state = load_state(root)
            artifacts = [a for a in state["artifacts"] if a.get("artifact_type") == "settings-hook-merge"]
            self.assertEqual(len(artifacts), 1)
            uninstall_action = plan_uninstall_action(artifacts[0], root)
            self.assertEqual(uninstall_action["operation"], "merge-remove")
            res = apply_uninstall_action(uninstall_action, root)
            self.assertTrue(res["completed"])
            after_uninstall = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else None
            return after_install, after_uninstall

    def test_install_into_populated_settings_then_uninstall_restores(self) -> None:
        original = {
            "model": "opus",
            "permissions": {"allow": ["Bash(git *)"]},
            "hooks": {
                "Stop": [{"hooks": [{"type": "command", "command": "user-stop"}]}],
                "PreToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "fmt"}]}],
            },
        }
        after_install, after_uninstall = self._round_trip(original)
        self.assertEqual(after_install["model"], "opus")
        self.assertEqual(len(after_install["hooks"]["Stop"]), 2)
        self.assertEqual(after_install["hooks"]["Stop"][0], original["hooks"]["Stop"][0])
        self.assertTrue(
            any(e.get("_managedBy") == "ai-agents-skills" for e in after_install["hooks"]["Stop"])
        )
        # uninstall removes only our entry and restores the file exactly
        self.assertEqual(after_uninstall, original)

    def test_install_creates_settings_then_uninstall_deletes_it(self) -> None:
        after_install, after_uninstall = self._round_trip(None)
        self.assertIn("Stop", after_install["hooks"])
        self.assertTrue(
            any(e.get("_managedBy") == "ai-agents-skills" for e in after_install["hooks"]["Stop"])
        )
        # the file we created is removed on uninstall
        self.assertIsNone(after_uninstall)

    def test_uninstall_preserves_user_hook_added_after_install(self) -> None:
        # The user edits settings.json AFTER install (adds another Stop hook).
        # Uninstall must remove only ours and keep the user's later addition.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = root / ".claude" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({"model": "opus"}) + "\n", encoding="utf-8")
            apply_plan(root, {"actions": [_action(settings)]}, dry_run=False)
            data = json.loads(settings.read_text(encoding="utf-8"))
            data["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "late-user-hook"}]})
            settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            state = load_state(root)
            artifact = next(a for a in state["artifacts"] if a.get("artifact_type") == "settings-hook-merge")
            apply_uninstall_action(plan_uninstall_action(artifact, root), root)
            final = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(final["model"], "opus")
            stops = final["hooks"]["Stop"]
            self.assertEqual(len(stops), 1)
            self.assertEqual(stops[0]["hooks"][0]["command"], "late-user-hook")


class StopHookPlannerTests(unittest.TestCase):
    """The planner emits the json-merge action for claude only when the runtime is selected."""

    def _claude(self, root: Path):
        from installer.ai_agents_skills.agents import target_for

        return target_for(root, "claude")

    def test_emits_for_claude_when_runtime_selected(self) -> None:
        from installer.ai_agents_skills.manifest import load_manifests
        from installer.ai_agents_skills.planner import autoloop_stop_hook_actions

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actions = autoloop_stop_hook_actions(
                root, load_manifests(), ["autonomous-research-loop"], [self._claude(root)],
                "full", None, "linux", False, False,
            )
            self.assertEqual(len(actions), 1)
            a = actions[0]
            self.assertEqual(a["kind"], "json-merge")
            self.assertEqual(a["artifact_type"], "settings-hook-merge")
            self.assertEqual(a["managed_id"], "autoloop-stop")
            self.assertEqual(a["operation"], "merge")
            # Separator-agnostic so the assertion holds on POSIX and Windows alike.
            settings_path = Path(a["path"])
            self.assertEqual(settings_path.name, "settings.json")
            self.assertEqual(settings_path.parent.name, ".claude")
            # The Stop hook invokes the runtime's cross-platform hook-check directly
            # (no shell wrapper); platform="linux" here -> the python3 interpreter.
            command = a["entry"]["hooks"][0]["command"]
            self.assertIn("hook-check", command)
            self.assertIn("autonomous_research_loop_runtime.py", command)
            self.assertIn("python3", command)

    def test_skips_when_runtime_not_selected(self) -> None:
        from installer.ai_agents_skills.manifest import load_manifests
        from installer.ai_agents_skills.planner import autoloop_stop_hook_actions

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actions = autoloop_stop_hook_actions(
                root, load_manifests(), ["zotero"], [self._claude(root)],
                "auto", None, "linux", False, False,
            )
            self.assertEqual(actions, [])


if __name__ == "__main__":
    unittest.main()
