"""Offline tests for get-available-resources' Apple Silicon GPU parse.

The module had no test before this file.  What is pinned here is the one thing the
parse owes its caller: a malformed line in `system_profiler` output costs that line
and nothing else.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "get-available-resources"
    / "detect_resources.py"
)
_MISSING = object()


def _load_module():
    """Import the script with psutil stubbed: nothing here calls into it."""

    previous = sys.modules.get("psutil", _MISSING)
    if previous is _MISSING:
        sys.modules["psutil"] = types.ModuleType("psutil")
    try:
        spec = importlib.util.spec_from_file_location(
            "aas_detect_resources_under_test", MODULE_PATH
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is _MISSING:
            sys.modules.pop("psutil", None)


dr = _load_module()


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.returncode = 0


class AppleSiliconGpuParseTests(unittest.TestCase):
    BRAND = "Apple M2 Pro"

    @staticmethod
    def _profile(chipset_line: str) -> str:
        return "\n".join(
            [
                "Graphics/Displays:",
                "    Apple M2:",
                f"      {chipset_line}",
                "      Type: GPU",
                "      Bus: Built-In",
                "      Total Number of Cores: 16",
                "      Vendor: Apple (0x106b)",
            ]
        )

    def _detect(self, profile: str) -> dict | None:
        def fake_run(argv, **_kwargs):
            if argv[0] == "sysctl":
                return _Completed(f"{self.BRAND}\n")
            return _Completed(profile)

        with mock.patch.object(dr.platform, "system", return_value="Darwin"), \
                mock.patch.object(dr.subprocess, "run", side_effect=fake_run):
            return dr.detect_apple_silicon_gpu()

    def test_a_well_formed_profile_yields_both_fields(self) -> None:
        info = self._detect(self._profile("Chipset Model: Apple M2 Pro"))
        self.assertIsNotNone(info)
        self.assertEqual(info["chipset"], "Apple M2 Pro")
        self.assertEqual(info["gpu_cores"], "16")
        self.assertEqual(info["backend"], "Metal")

    def test_one_malformed_line_costs_only_that_line(self) -> None:
        """The regression proper.

        Both fields are read with the same split.  The core count used to be wrapped
        in a bare `except:` and the chipset in nothing at all, so a chipset line with
        no colon raised IndexError past the guard, into an `except Exception: pass`
        that abandoned the loop -- and the intact core-count line below it was lost
        with it.
        """

        info = self._detect(self._profile("Chipset Model Apple M2 Pro"))
        self.assertIsNotNone(info)
        self.assertNotIn("chipset", info)
        self.assertEqual(info["gpu_cores"], "16")

    def test_a_value_containing_a_colon_is_kept_whole(self) -> None:
        info = self._detect(self._profile("Chipset Model: Apple M2 Pro: 19-core"))
        self.assertEqual(info["chipset"], "Apple M2 Pro: 19-core")

    def test_a_non_apple_machine_is_not_claimed(self) -> None:
        with mock.patch.object(dr.platform, "system", return_value="Linux"):
            self.assertIsNone(dr.detect_apple_silicon_gpu())


if __name__ == "__main__":
    unittest.main()
