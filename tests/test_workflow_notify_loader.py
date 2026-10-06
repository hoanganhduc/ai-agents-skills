"""The lazy notification loader must not mutate its installed source tree."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SKILLS = Path(__file__).resolve().parents[1] / "canonical/runtime/skills"
PROBE = """
import importlib.util
import json
from pathlib import Path
import sys

bridge_path = Path(sys.argv[1])
notify_path = Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("offline_notify_loader", bridge_path)
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
exec(compile(bridge_path.read_bytes(), str(bridge_path), "exec"), bridge.__dict__)
sys.dont_write_bytecode = False
notify = bridge.load_notify_v2_module()
print(json.dumps({
    "callable_result": notify.slugify_topic("Offline Loader Check"),
    "module_file": notify.__file__,
    "module_origin": notify.__spec__.origin,
    "reused": bridge.load_notify_v2_module() is notify,
    "bytecode_disabled": sys.dont_write_bytecode,
    "cache_files": [str(path) for path in notify_path.parent.rglob("*.pyc")],
    "cache_directory": (notify_path.parent / "__pycache__").exists(),
}))
"""


class NotifyLoaderTests(unittest.TestCase):
    def test_real_renderer_loads_without_bytecode_when_bytecode_is_enabled(self):
        for adjacent in (True, False):
            with self.subTest(adjacent=adjacent), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bridge = root / "remote-bridge/remote_bridge.py"
                bridge.parent.mkdir()
                bridge.write_bytes((SKILLS / "remote-bridge/remote_bridge.py").read_bytes())
                notify = (bridge.parent if adjacent else root / "autonomous-research-loop-runtime") / "notify_v2.py"
                notify.parent.mkdir(exist_ok=True)
                expected_bytes = (SKILLS / "autonomous-research-loop-runtime/notify_v2.py").read_bytes()
                notify.write_bytes(expected_bytes)
                env = dict(os.environ)
                env.pop("PYTHONDONTWRITEBYTECODE", None)
                result = subprocess.run([sys.executable, "-c", PROBE, str(bridge), str(notify)],
                    capture_output=True, encoding="utf-8", env=env, timeout=20, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout)
                self.assertEqual(data["callable_result"], "offline-loader-check")
                self.assertEqual(data["module_file"], str(notify))
                self.assertEqual(data["module_origin"], str(notify))
                self.assertTrue(data["reused"])
                self.assertFalse(data["bytecode_disabled"])
                self.assertEqual(data["cache_files"], [])
                self.assertFalse(data["cache_directory"])
                self.assertEqual(notify.read_bytes(), expected_bytes)


if __name__ == "__main__":
    unittest.main()
