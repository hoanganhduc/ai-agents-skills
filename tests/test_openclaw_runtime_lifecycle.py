from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.openclaw_runtime_lifecycle import (
    broker_install,
    broker_refcount,
    broker_uninstall,
    classify_legacy_dir,
    new_broker_record,
    normalize_slug,
    plan_legacy_migration,
)


class BrokerRefcountTest(unittest.TestCase):
    def _rec(self):
        return new_broker_record(
            file_hashes={"broker.py": "sha256:x"}, unit_path="~/.config/systemd/user/aas-broker.service",
            token_path="/run/aas/broker.token", endpoint="host.docker.internal:18799",
            firewall_rule="INPUT -i docker0 -p tcp --dport 18799 -j ACCEPT")

    def test_refcount_lifecycle_teardown_at_zero(self) -> None:
        rec = self._rec()
        self.assertEqual(broker_refcount(rec), 0)
        rec = broker_install(rec, "workspace-host")
        rec = broker_install(rec, "workspace-review")
        rec = broker_install(rec, "workspace-host")  # idempotent
        self.assertEqual(broker_refcount(rec), 2)
        rec, teardown = broker_uninstall(rec, "workspace-host")
        self.assertEqual(broker_refcount(rec), 1)
        self.assertFalse(teardown)  # another workspace still references it
        rec, teardown = broker_uninstall(rec, "workspace-review")
        self.assertEqual(broker_refcount(rec), 0)
        self.assertTrue(teardown)  # last reference gone -> tear down broker + firewall rule

    def test_record_carries_firewall_rule_for_exact_removal(self) -> None:
        self.assertIn("docker0", self._rec()["firewall_rule"])


class LegacyMigrationTest(unittest.TestCase):
    def _setup(self, tmp: Path):
        canonical = tmp / "canonical"
        ws = tmp / "workspace" / "skills"
        for d in (canonical, ws):
            d.mkdir(parents=True)
        # canonical skills
        (canonical / "model-router").mkdir()
        (canonical / "model-router" / "SKILL.md").write_text("ROUTER-BODY\n", encoding="utf-8")
        (canonical / "getscipapers-requester").mkdir()
        (canonical / "getscipapers-requester" / "SKILL.md").write_text("GSP-BODY\n", encoding="utf-8")
        (canonical / "prose").mkdir()
        (canonical / "prose" / "SKILL.md").write_text("PROSE-BODY\n", encoding="utf-8")
        return canonical, ws

    def test_classify_adopt_divergent_unrecognized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            canonical, ws = self._setup(tmp)
            known = {"model-router", "getscipapers-requester", "prose"}
            # byte+slug match (underscore legacy slug -> hyphen canonical)
            (ws / "getscipapers_requester").mkdir()
            (ws / "getscipapers_requester" / "SKILL.md").write_text("GSP-BODY\n", encoding="utf-8")
            # slug matches but content differs -> divergent
            (ws / "prose").mkdir()
            (ws / "prose" / "SKILL.md").write_text("EDITED LOCALLY\n", encoding="utf-8")
            # slug not a current canonical skill -> unrecognized
            (ws / "smart-model-router").mkdir()
            (ws / "smart-model-router" / "SKILL.md").write_text("X\n", encoding="utf-8")

            adopt = classify_legacy_dir(ws / "getscipapers_requester", canonical_root=canonical, known_skills=known)
            self.assertEqual(adopt["decision"], "adopt-eligible")
            self.assertTrue(adopt["adopt_eligible"])

            div = classify_legacy_dir(ws / "prose", canonical_root=canonical, known_skills=known)
            self.assertEqual(div["decision"], "divergent")
            self.assertFalse(div["adopt_eligible"])

            unk = classify_legacy_dir(ws / "smart-model-router", canonical_root=canonical, known_skills=known)
            self.assertEqual(unk["decision"], "unrecognized")

    def test_plan_default_leaves_untouched_opt_in_adopts_only_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            canonical, ws = self._setup(tmp)
            known = {"model-router", "getscipapers-requester", "prose"}
            (ws / "getscipapers_requester").mkdir()
            (ws / "getscipapers_requester" / "SKILL.md").write_text("GSP-BODY\n", encoding="utf-8")
            (ws / "prose").mkdir()
            (ws / "prose" / "SKILL.md").write_text("EDITED LOCALLY\n", encoding="utf-8")

            default = {e["dir"]: e["action"] for e in
                       plan_legacy_migration(ws, canonical_root=canonical, known_skills=known)}
            self.assertEqual(set(default.values()), {"leave-untouched"})  # default never touches legacy

            opted = {e["dir"]: e["action"] for e in
                     plan_legacy_migration(ws, canonical_root=canonical, known_skills=known, adopt=True)}
            self.assertEqual(opted["getscipapers_requester"], "adopt")  # eligible
            self.assertEqual(opted["prose"], "leave-untouched")  # divergent stays untouched

    def test_normalize_slug(self) -> None:
        self.assertEqual(normalize_slug("getscipapers_requester"), "getscipapers-requester")
        self.assertEqual(normalize_slug("model-router"), "model-router")


if __name__ == "__main__":
    unittest.main()
