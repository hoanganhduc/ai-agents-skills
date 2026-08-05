from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.apply import apply_plan
from installer.ai_agents_skills.planner import classify_file_action
from installer.ai_agents_skills.state import sha256_file


class SkillSourceAttestationTests(unittest.TestCase):
    def test_skill_receipt_binds_canonical_source_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "canonical/skills/canary/SKILL.md"
            source.parent.mkdir(parents=True)
            source.write_text("canonical source\n", encoding="utf-8")
            target = root / ".codex/skills/canary/SKILL.md"
            action = classify_file_action(
                agent="codex",
                skill="canary",
                path=target,
                content="rendered target content\n",
                artifact_type="skill-file",
                adopt=False,
                backup_replace=False,
                source_path=source,
            )

            result = apply_plan(root, {"actions": [action]}, dry_run=False)
            expected = sha256_file(source)
            self.assertEqual(result["actions"][0]["canonical_source_sha256"], expected)
            state = json.loads((root / ".ai-agents-skills/state.json").read_text(encoding="utf-8"))
            receipt = json.loads(
                (root / ".ai-agents-skills/runs" / f"{result['run_id']}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(state["artifacts"][0]["canonical_source_sha256"], expected)
            self.assertEqual(receipt["actions"][0], state["artifacts"][0])


if __name__ == "__main__":
    unittest.main()
