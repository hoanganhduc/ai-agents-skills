"""Repeated hook installs must retain verification/rollback identity."""
import json
from pathlib import Path
import tempfile
import unittest

from installer.ai_agents_skills.apply import apply_json_merge_action
from installer.ai_agents_skills.json_merge import extract_hook_entry


class HookIdentityTests(unittest.TestCase):
    def test_noop_retains_the_exact_managed_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'settings.json'
            action = {'kind': 'json-merge', 'agent': 'claude', 'skill': 'autonomous-research-loop',
                'path': str(path), 'artifact_type': 'settings-hook-merge', 'operation': 'merge',
                'classification': 'managed', 'event': 'Stop', 'managed_id': 'fixture-hook',
                'entry': {'hooks': [{'type': 'command', 'command': 'fixture-runtime hook-check'}]}}
            first = apply_json_merge_action(root, 'first', action)
            before = path.read_bytes()
            second = apply_json_merge_action(root, 'second', {**action, 'operation': 'noop'})
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(second['applied'])
            self.assertEqual(second.get('event'), 'Stop')
            self.assertEqual(second.get('managed_id'), 'fixture-hook')
            entry = extract_hook_entry(json.loads(path.read_text(encoding='utf-8')), 'Stop', 'fixture-hook')
            self.assertEqual(second.get('managed_entry'), entry)
            self.assertEqual(first.get('managed_entry'), entry)
            altered = json.loads(path.read_text(encoding='utf-8'))
            altered['hooks']['Stop'][0]['hooks'][0]['command'] = 'unreviewed command'
            path.write_text(json.dumps(altered), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'changed'):
                apply_json_merge_action(root, 'third', {**action, 'operation': 'noop'})


if __name__ == '__main__': unittest.main()
