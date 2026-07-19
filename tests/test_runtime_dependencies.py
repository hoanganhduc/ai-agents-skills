from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.runtime import build_runtime_actions, resolve_runtime_skills


class RuntimeDependencyResolutionTests(unittest.TestCase):
    def test_manifests_without_runtime_requires_remain_backward_compatible(self) -> None:
        manifest = {
            "runtime_profiles": {
                "auto": {"mode": "selected-skills"},
            },
            "skills": {
                "standalone": {},
            },
        }

        self.assertEqual(resolve_runtime_skills(["standalone"], manifest), ["standalone"])

    def test_selected_skill_resolves_deterministic_transitive_closure(self) -> None:
        manifest = {
            "runtime_profiles": {
                "auto": {"mode": "selected-skills"},
            },
            "skills": {
                "application": {"runtime_requires": ["browser", "shared"]},
                "browser": {"runtime_requires": ["transport"]},
                "shared": {"runtime_requires": ["transport"]},
                "transport": {},
            },
        }

        self.assertEqual(
            resolve_runtime_skills(["application"], manifest),
            ["application", "browser", "shared", "transport"],
        )

    def test_explicit_profile_resolves_transitive_closure(self) -> None:
        manifest = {
            "runtime_profiles": {
                "auto": {"mode": "selected-skills"},
                "bundle": {"skills": ["application"]},
            },
            "skills": {
                "application": {"runtime_requires": ["browser"]},
                "browser": {},
            },
        }

        self.assertEqual(
            resolve_runtime_skills([], manifest, "bundle"),
            ["application", "browser"],
        )

    def test_runtime_actions_include_selected_skills_runtime_dependencies(self) -> None:
        manifests = load_manifests()
        graph_runtime = manifests["runtime"]["skills"]["graph-verifier"]
        runtime_manifest = {
            "runtime_profiles": {
                "auto": {"mode": "selected-skills"},
            },
            "runners": [],
            "skills": {
                "application": {"runtime_requires": ["graph-verifier"], "files": []},
                "graph-verifier": graph_runtime,
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            actions = build_runtime_actions(
                root=Path(tmp),
                manifests={"runtime": runtime_manifest},
                selected_skills=["application"],
                agents=[SimpleNamespace(name="codex")],
                platform="linux",
            )

        self.assertTrue(actions)
        self.assertEqual({action["skill"] for action in actions}, {"graph-verifier"})

    def test_unknown_reachable_runtime_dependency_is_rejected(self) -> None:
        manifest = {
            "runtime_profiles": {
                "auto": {"mode": "selected-skills"},
            },
            "skills": {
                "application": {"runtime_requires": ["missing"]},
            },
        }

        with self.assertRaisesRegex(
            ValueError,
            "runtime skill application requires unknown runtime skill missing",
        ):
            resolve_runtime_skills(["application"], manifest)

    def test_runtime_requires_must_be_a_list_of_non_empty_strings(self) -> None:
        for invalid in ("browser", [""], [None]):
            with self.subTest(runtime_requires=invalid):
                manifest = {
                    "runtime_profiles": {
                        "auto": {"mode": "selected-skills"},
                    },
                    "skills": {
                        "application": {"runtime_requires": invalid},
                        "browser": {},
                    },
                }

                with self.assertRaisesRegex(ValueError, "runtime_requires"):
                    resolve_runtime_skills(["application"], manifest)

    def test_runtime_dependency_cycle_is_rejected_with_path(self) -> None:
        manifest = {
            "runtime_profiles": {
                "auto": {"mode": "selected-skills"},
            },
            "skills": {
                "application": {"runtime_requires": ["browser"]},
                "browser": {"runtime_requires": ["transport"]},
                "transport": {"runtime_requires": ["application"]},
            },
        }

        with self.assertRaisesRegex(
            ValueError,
            "runtime dependency cycle: application -> browser -> transport -> application",
        ):
            resolve_runtime_skills(["application"], manifest)

    def test_venue_runtime_declares_browser_runtime_dependency(self) -> None:
        manifests = load_manifests()

        self.assertIn("venue-ranking-evidence", manifests["skills"]["skills"])
        venue_skill = manifests["skills"]["skills"]["venue-ranking-evidence"]
        self.assertTrue(
            {"serious-research", "full-research"}.issubset(venue_skill["profiles"])
        )
        self.assertIn(
            "chromium-browser-system-tool",
            venue_skill["optional_dependencies"],
        )
        self.assertIn(
            "pdftotext-system-tool",
            venue_skill["optional_dependencies"],
        )
        self.assertEqual(
            manifests["dependencies"]["packages"]["pdftotext-system-tool"],
            {"type": "tool", "logical_tool": "pdftotext-system-tool"},
        )
        self.assertIn(
            "pdftotext",
            manifests["dependencies"]["tools"]["pdftotext-system-tool"]["candidates"]["linux"],
        )
        self.assertIn(
            "venue-ranking-evidence",
            manifests["system_dependencies"]["software"]["pdftotext"]["used_by"],
        )
        for profile in ("serious-research", "full-research"):
            self.assertIn(
                "venue-ranking-evidence",
                manifests["profiles"]["profiles"][profile]["skills"],
            )
        venue_runtime = manifests["runtime"]["skills"]["venue-ranking-evidence"]
        self.assertEqual(venue_runtime["runtime_requires"], ["url-to-screenshot-runtime"])
        self.assertEqual(venue_runtime["smoke"]["args"], ["smoke"])
        self.assertEqual(venue_runtime["smoke_coverage"]["status"], "offline-smoke")
        self.assertEqual(
            resolve_runtime_skills(
                ["venue-ranking-evidence"],
                manifests["runtime"],
            ),
            ["url-to-screenshot-runtime", "venue-ranking-evidence"],
        )


if __name__ == "__main__":
    unittest.main()
