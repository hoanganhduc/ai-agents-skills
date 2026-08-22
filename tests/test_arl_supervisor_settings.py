"""Tests for the ARL supervisor-pack settings CLIs.

``apply_failover_settings.py`` edits the file a running loop reads to decide
which provider it is on and what it calls itself, so the question these tests
ask is narrow: which invocations are allowed to change a value the operator
already set.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACK = (
    REPO
    / "canonical"
    / "runtime"
    / "skills"
    / "autonomous-research-loop-runtime"
)
APPLY_FAILOVER = PACK / "apply_failover_settings.py"
EXAMPLE = PACK / "failover.example.json"
RUNTIME_PY = PACK / "autonomous_research_loop_runtime.py"

# The runtime imports its siblings by package-relative name and falls back to a
# plain import, so the pack has to be importable before it is loaded by path.
if str(PACK) not in sys.path:
    sys.path.insert(0, str(PACK))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class ApplyFailoverSettingsTests(unittest.TestCase):
    """``--force`` is the flag that overwrites; nothing else may."""

    # A loop an operator has been running and tuning. Every value differs from
    # the shipped example, and ``operator_note`` is a key the example does not
    # carry at all.
    TUNED = {
        "schema_version": "failover.v1",
        "research_title": "TS_k acyclicity characterization",
        "job_slug": "tsk-acyclicity",
        "primary_order": ["grok", "claude", "deepseek"],
        "max_quota_waits_per_primary": 3,
        "retry_sleep_s": 900,
        "session_exclude_ttl_s": 3600,
        "failures_before_rotate": 5,
        "operator_note": "hand-tuned after the March quota incident",
    }

    def _tuned_loop(self, tmp: str) -> Path:
        run_dir = Path(tmp) / "loop"
        run_dir.mkdir()
        (run_dir / "failover.json").write_text(
            json.dumps(self.TUNED, indent=2) + "\n", encoding="utf-8"
        )
        return run_dir

    def _apply(self, run_dir: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-B", str(APPLY_FAILOVER), "--dir", str(run_dir), *args],
            check=False,
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=120,
        )

    def _written(self, run_dir: Path) -> dict:
        return json.loads((run_dir / "failover.json").read_text(encoding="utf-8"))

    def test_a_seed_does_not_overwrite_settings_the_loop_already_has(self) -> None:
        """``--from-json`` used to replace the file and report ``ok``.

        The seed took an exclusive branch that skipped the read of the existing
        file, and the refusal guarding an existing file excluded the same flag,
        so seeding a live loop reset every tuned value to the example's and
        dropped the keys the example does not carry.
        """

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._tuned_loop(tmp)
            result = self._apply(run_dir, "--from-json", str(EXAMPLE))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(json.loads(result.stdout)["ok"])
            written = self._written(run_dir)
            for key, value in self.TUNED.items():
                with self.subTest(key=key):
                    self.assertEqual(written.get(key), value)

    def test_a_seed_leaves_the_loop_under_its_own_name(self) -> None:
        """``failover.json`` is the first place the notify identity is read.

        So a seed that overwrote ``research_title`` renamed the loop and moved
        its Zulip topic mid-run, which is the operator-visible half of the same
        defect.
        """

        arl = _load("arl_supervisor_settings_identity", RUNTIME_PY)
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._tuned_loop(tmp)
            self._apply(run_dir, "--from-json", str(EXAMPLE))
            identity = arl.resolve_loop_notify_identity(run_dir)
            self.assertEqual(identity["title"], self.TUNED["research_title"])
            self.assertEqual(identity["slug"], "tsk-acyclicity")

    def test_a_seed_still_fills_in_fields_the_loop_does_not_have(self) -> None:
        """Picking up a field added to the example is why a seed merges at all."""

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._tuned_loop(tmp)
            self._apply(run_dir, "--from-json", str(EXAMPLE))
            written = self._written(run_dir)
            example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
            added = sorted(set(example) - set(self.TUNED))
            self.assertTrue(added, "the example must carry keys the loop lacks")
            for key in added:
                with self.subTest(key=key):
                    self.assertEqual(written.get(key), example[key])

    def test_force_still_replaces_the_file_wholesale(self) -> None:
        """``--force`` is documented as "overwrite existing file" and still is."""

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._tuned_loop(tmp)
            result = self._apply(run_dir, "--from-json", str(EXAMPLE), "--force")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            written = self._written(run_dir)
            example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
            self.assertNotIn("operator_note", written)
            for key, value in example.items():
                with self.subTest(key=key):
                    self.assertEqual(written.get(key), value)

    def test_a_named_flag_changes_only_the_field_it_names(self) -> None:
        """The named flags are the sanctioned way to edit a value that is set."""

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._tuned_loop(tmp)
            result = self._apply(
                run_dir,
                "--from-json",
                str(EXAMPLE),
                "--research-title",
                "Sliding tokens on cographs",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            written = self._written(run_dir)
            self.assertEqual(written["research_title"], "Sliding tokens on cographs")
            self.assertEqual(written["retry_sleep_s"], self.TUNED["retry_sleep_s"])
            self.assertEqual(written["primary_order"], self.TUNED["primary_order"])

    def test_a_fresh_loop_gets_the_seed_verbatim(self) -> None:
        """With nothing on disk to protect, the seed is simply the file."""

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            run_dir.mkdir()
            result = self._apply(run_dir, "--from-json", str(EXAMPLE))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            written = self._written(run_dir)
            example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
            for key, value in example.items():
                with self.subTest(key=key):
                    self.assertEqual(written.get(key), value)

    def test_a_bare_rerun_on_an_existing_file_is_still_refused(self) -> None:
        """Nothing to write and nothing forced: the file is left alone."""

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._tuned_loop(tmp)
            before = (run_dir / "failover.json").read_bytes()
            result = self._apply(run_dir)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertFalse(json.loads(result.stdout)["ok"])
            self.assertEqual((run_dir / "failover.json").read_bytes(), before)


class SyncPanelExcludeTests(unittest.TestCase):
    """Parking one provider must not un-park the others.

    ``load_panel_config`` merges ``panel.json`` first and then lets
    ``standing_orders.panel`` replace whole values, so whatever this helper
    writes into standing orders becomes the entire exclusion set. It seeded that
    write from the standing-orders block alone, so on a loop whose exclusions
    lived in ``panel.json`` the first sync replaced a full list with a
    one-element one -- and every provider parked for being out of credit was
    invited again.
    """

    PROVIDERS = ["claude", "codex", "grok", "deepseek"]

    @classmethod
    def setUpClass(cls) -> None:
        cls.sync = _load("arl_supervisor_sync_panel", PACK / "sync_panel_exclude.py")
        cls.panel = _load("arl_supervisor_panel_parent", PACK / "panel_parent.py")

    def _loop(self, tmp: str, *, panel_excludes, standing_excludes) -> Path:
        run_dir = Path(tmp) / "loop"
        run_dir.mkdir()
        panel_json = {"enabled": True, "providers": list(self.PROVIDERS)}
        if panel_excludes is not None:
            panel_json["exclude_until_credit"] = list(panel_excludes)
        (run_dir / "panel.json").write_text(
            json.dumps(panel_json, indent=2) + "\n", encoding="utf-8"
        )
        standing = {"enabled": True, "providers": list(self.PROVIDERS)}
        if standing_excludes is not None:
            standing["exclude_until_credit"] = list(standing_excludes)
        (run_dir / "loop_state.json").write_text(
            json.dumps(
                {
                    "status": "running",
                    "goal": "Prove X",
                    "standing_orders": {"panel": standing},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return run_dir

    def test_parking_one_provider_leaves_the_others_parked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._loop(
                tmp, panel_excludes=["claude", "codex"], standing_excludes=None
            )
            self.assertEqual(
                self.panel.load_panel_config(run_dir)["exclude_until_credit"],
                ["claude", "codex"],
            )
            result = self.sync.sync_exclude(run_dir, "grok")
            self.assertTrue(result["ok"], result)
            cfg = self.panel.load_panel_config(run_dir)
            self.assertEqual(
                sorted(cfg["exclude_until_credit"]), ["claude", "codex", "grok"]
            )
            self.assertEqual(cfg["providers"], ["deepseek"])

    def test_an_explicit_standing_orders_list_still_wins(self) -> None:
        """Standing orders overriding panel.json is the documented merge.

        A stale name in ``panel.json`` that standing orders deliberately dropped
        must stay dropped; the seed follows the merge, it does not union.
        """

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._loop(
                tmp,
                panel_excludes=["claude", "codex"],
                standing_excludes=["claude"],
            )
            self.sync.sync_exclude(run_dir, "grok")
            cfg = self.panel.load_panel_config(run_dir)
            self.assertEqual(sorted(cfg["exclude_until_credit"]), ["claude", "grok"])
            self.assertNotIn("codex", cfg["exclude_until_credit"])

    def test_a_loop_with_no_standing_orders_panel_uses_panel_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            run_dir.mkdir()
            (run_dir / "panel.json").write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "providers": list(self.PROVIDERS),
                        "exclude_until_credit": ["claude"],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            self.sync.sync_exclude(run_dir, "grok")
            cfg = self.panel.load_panel_config(run_dir)
            self.assertEqual(sorted(cfg["exclude_until_credit"]), ["claude", "grok"])

    def test_syncing_the_same_provider_twice_does_not_duplicate_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._loop(
                tmp, panel_excludes=["claude"], standing_excludes=None
            )
            self.sync.sync_exclude(run_dir, "grok")
            self.sync.sync_exclude(run_dir, "grok")
            cfg = self.panel.load_panel_config(run_dir)
            self.assertEqual(sorted(cfg["exclude_until_credit"]), ["claude", "grok"])

    def test_the_two_stores_agree_after_a_sync(self) -> None:
        """The helper reports writing both; both must end up saying the same."""

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._loop(
                tmp, panel_excludes=["claude", "codex"], standing_excludes=None
            )
            result = self.sync.sync_exclude(run_dir, "grok")
            self.assertEqual(result["updated"], ["standing_orders.panel", "panel.json"])
            panel_json = json.loads((run_dir / "panel.json").read_text(encoding="utf-8"))
            state = json.loads((run_dir / "loop_state.json").read_text(encoding="utf-8"))
            standing = state["standing_orders"]["panel"]["exclude_until_credit"]
            self.assertEqual(
                sorted(panel_json["exclude_until_credit"]), sorted(standing)
            )


if __name__ == "__main__":
    unittest.main()
