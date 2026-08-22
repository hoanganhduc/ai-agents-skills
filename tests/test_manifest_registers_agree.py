"""Two manifests describe the same facts twice; they have to say the same thing.

Smoke coverage is recorded in `runtime.yaml` as `smoke_coverage.status` and in
`skills.yaml` as an `offline-smoke` token in `verification`.  Only the first is
read: the second is validated for presence and never for content, so it drifted
without anything noticing.  Seven skills -- deep-research-workflow,
formal-skeleton-helper, get-available-resources, graph-verifier,
lean-formalization-intake, lean-strict-verification-gate and
submission-venue-selector -- had a real smoke contract that `skills.yaml` did
not claim.

The dependency registry has the same shape of problem in a different direction:
an entry nothing points at.  `libreoffice-system-tool` was superseded by
`pptx-render-system-tool`, which carries the same `pptx-render` capability and
also finds PowerPoint on Windows, but the old entry stayed and kept rendering
into the dependency table as though something needed it -- with the narrower
candidate list a future author could have wired by mistake.
`beautifulsoup4-python-package` duplicated the `bs4` already inside
`vnu-eoffice-python-package`'s `modules`, and `telegram-bot-config` had no
consumer at all: remote-bridge takes its Telegram credentials from
`REMOTE_BRIDGE_SECRETS_FILE`, and the registry models neither of its
transports, Zulip included.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "installer"))

from ai_agents_skills.manifest import load_manifests  # noqa: E402

# Registry entries with no skill behind them, kept because installer code probes
# the host for them directly. Each names the module that reads it.
PROBED_BY_INSTALLER = {
    "git-cli": "installer/ai_agents_skills/cli.py",
    "ripgrep-cli": "installer/ai_agents_skills/cli.py",
    "powershell-runtime": "installer/ai_agents_skills/discovery.py",
    "wsl-runtime": "installer/ai_agents_skills/cli.py",
}


def _skill_dependency_names(skills: dict) -> set[str]:
    named: set[str] = set()
    for spec in skills.values():
        named.update(spec.get("required_dependencies") or [])
        named.update(spec.get("optional_dependencies") or [])
    return named


class SmokeCoverageRegistersAgreeTests(unittest.TestCase):
    def setUp(self) -> None:
        manifests = load_manifests()
        self.skills = manifests["skills"]["skills"]
        self.runtime = manifests["runtime"]["skills"]

    def _declared(self) -> set[str]:
        return {
            slug
            for slug, spec in self.skills.items()
            if "offline-smoke" in (spec.get("verification") or [])
        }

    def _covered(self) -> set[str]:
        return {
            slug
            for slug, spec in self.runtime.items()
            if ((spec or {}).get("smoke_coverage") or {}).get("status")
            == "offline-smoke"
        }

    def test_the_two_smoke_registers_name_the_same_skills(self) -> None:
        declared, covered = self._declared(), self._covered()
        self.assertEqual(
            covered - declared,
            set(),
            "runtime.yaml records offline smoke coverage that skills.yaml "
            "does not claim",
        )
        self.assertEqual(
            declared - covered,
            set(),
            "skills.yaml claims offline smoke coverage that runtime.yaml "
            "does not record",
        )

    def test_the_comparison_is_not_vacuous(self) -> None:
        """Both sides must be populated, or agreement means nothing."""

        self.assertGreater(len(self._declared()), 15)
        self.assertEqual(len(self._declared()), len(self._covered()))

    def test_the_seven_that_drifted_are_claimed_now(self) -> None:
        declared = self._declared()
        for slug in (
            "deep-research-workflow",
            "formal-skeleton-helper",
            "get-available-resources",
            "graph-verifier",
            "lean-formalization-intake",
            "lean-strict-verification-gate",
            "submission-venue-selector",
        ):
            with self.subTest(slug=slug):
                self.assertIn(slug, declared)

    def test_only_implemented_verification_tokens_are_declared(self) -> None:
        """`skills.yaml` validates presence, not content, so a typo would ship."""

        known = {"file-exists", "metadata-valid", "agent-visible", "offline-smoke"}
        used = {
            token
            for spec in self.skills.values()
            for token in (spec.get("verification") or [])
        }
        self.assertEqual(used - known, set())


class EveryRegistryEntryHasAConsumerTests(unittest.TestCase):
    def setUp(self) -> None:
        manifests = load_manifests()
        self.dependencies = manifests["dependencies"]
        self.skills = manifests["skills"]["skills"]
        self.named = _skill_dependency_names(self.skills)

    def _reachable(self) -> set[str]:
        reachable = set(self.named)
        for name, spec in self.dependencies["packages"].items():
            if name in reachable and isinstance(spec, dict) and spec.get("logical_tool"):
                reachable.add(spec["logical_tool"])
        return reachable

    def test_no_package_entry_is_unreachable(self) -> None:
        orphans = set(self.dependencies["packages"]) - self._reachable()
        self.assertEqual(orphans - set(PROBED_BY_INSTALLER), set())

    def test_no_tool_entry_is_unreachable(self) -> None:
        orphans = set(self.dependencies["tools"]) - self._reachable()
        self.assertEqual(orphans - set(PROBED_BY_INSTALLER), set())

    def test_the_installer_exemptions_are_really_probed(self) -> None:
        for name, module in PROBED_BY_INSTALLER.items():
            with self.subTest(name=name):
                source = (ROOT / module).read_text(encoding="utf-8")
                self.assertIn(name, source, f"{module} no longer probes {name}")

    def test_the_removed_entries_stay_removed(self) -> None:
        for name in (
            "libreoffice-system-tool",
            "beautifulsoup4-python-package",
            "telegram-bot-config",
        ):
            with self.subTest(name=name):
                self.assertNotIn(name, self.dependencies["packages"])
                self.assertNotIn(name, self.dependencies["tools"])

    def test_the_capability_the_removed_tool_carried_is_still_covered(self) -> None:
        """LibreOffice is still reachable, through the entry that supersedes it."""

        renderer = self.dependencies["tools"]["pptx-render-system-tool"]
        self.assertIn("pptx-render", renderer["capabilities"])
        self.assertIn("soffice", renderer["candidates"]["linux"])
        self.assertIn(
            "pptx-render-system-tool",
            self.skills["slides-to-video"]["optional_dependencies"],
        )

    def test_bs4_is_still_declared_where_it_is_actually_imported(self) -> None:
        vnu = self.dependencies["packages"]["vnu-eoffice-python-package"]
        self.assertIn("bs4", vnu["modules"])
        self.assertIn(
            "vnu-eoffice-python-package",
            self.skills["vnu-eoffice"]["required_dependencies"],
        )

    def test_the_scan_would_catch_a_fresh_orphan(self) -> None:
        """Non-vacuity: an entry nobody names is reported."""

        packages = dict(self.dependencies["packages"])
        packages["invented-python-package"] = {"type": "python", "module": "invented"}
        self.assertEqual(
            set(packages) - self._reachable() - set(PROBED_BY_INSTALLER),
            {"invented-python-package"},
        )


if __name__ == "__main__":
    unittest.main()
