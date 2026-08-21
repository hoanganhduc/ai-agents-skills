"""Directory permissions on the surfaces the installer creates for itself.

Every file the installer writes is already private -- state.json is 0600, the
plugin configs are 0600, skill files land under a 0700 skills directory.  The
cases here are about the directories those files sit in, which were created
through plain ``mkdir(parents=True)`` and so took the ambient umask: 0775 under
the common 0002, 0777 under 0000.  A file mode does not settle who can replace
the file, because write permission on a directory is permission to unlink an
entry and create another under the same name.
"""
from __future__ import annotations

import contextlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from typing import Iterator

from installer.ai_agents_skills.agents import (
    KNOWN_AGENT_NAMES,
    detect_agents,
    target_for,
)
from installer.ai_agents_skills.apply import apply_plan
from installer.ai_agents_skills.lifecycle import rollback
from installer.ai_agents_skills.managed_permissions import (
    managed_boundary_candidates,
    managed_parent_boundary,
)
from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.planner import build_plan
from installer.ai_agents_skills.selectors import resolve_skills
from installer.ai_agents_skills.state import state_dir, state_file


POSIX_ONLY = unittest.skipUnless(
    os.name == "posix", "POSIX permission bits have no equivalent on this platform"
)


class Args:
    skill = None
    skills = None
    profile = None
    exclude = None
    no_skills = False
    artifact = None
    artifacts = None
    artifact_profile = None
    exclude_artifact = None
    with_deps = False
    install_mode = "auto"


@contextlib.contextmanager
def permissive_umask(value: int = 0o002) -> Iterator[None]:
    """Run the body under a umask that grants the group write.

    0002 is the default on Debian and Ubuntu when the user has a private group,
    and on any host where the login group is shared it is what makes this
    reachable.  Setting it explicitly is what keeps the test honest: inheriting
    whatever the suite happened to run under would let the assertions pass on a
    0022 developer machine while the shipped behaviour stayed broken.
    """
    previous = os.umask(value)
    try:
        yield
    finally:
        os.umask(previous)


@contextlib.contextmanager
def prepared_root(*agents: str) -> Iterator[Path]:
    """Yield a root whose agent homes exist and are already private.

    The homes belong to the agents, not to this installer, and are created here
    only so the targets are detected.  Putting them at 0700 up front keeps the
    harness from being the thing a loose-directory assertion finds.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        root.chmod(0o755)
        for agent in agents:
            home = target_for(root, agent).home
            home.mkdir(parents=True, exist_ok=True)
            for directory in (home, *(p for p in home.parents if root in p.parents)):
                directory.chmod(0o700)
        yield root


def install(root: Path, skills: str) -> dict:
    manifests = load_manifests()
    args = Args()
    args.skills = skills
    plan = build_plan(root, manifests, resolve_skills(args, manifests), detect_agents(root))
    return apply_plan(root, plan, dry_run=False)


def loose_directories(base: Path) -> list[str]:
    """Return the directories under ``base`` that group or other may write."""
    found = []
    for path in sorted([base, *base.rglob("*")]):
        if path.is_symlink() or not path.is_dir():
            continue
        if stat.S_IMODE(path.lstat().st_mode) & 0o022:
            found.append(f"{stat.S_IMODE(path.lstat().st_mode):04o} {path}")
    return found


@POSIX_ONLY
class JournalPermissionTests(unittest.TestCase):
    def test_the_journal_is_private_whatever_umask_the_caller_had(self) -> None:
        for umask_value in (0o002, 0o000):
            with self.subTest(umask=f"{umask_value:04o}"):
                with permissive_umask(umask_value), prepared_root("claude") as root:
                    install(root, "zotero")
                    self.assertEqual(loose_directories(state_dir(root)), [])
                    self.assertEqual(
                        stat.S_IMODE(state_dir(root).lstat().st_mode), 0o700
                    )

    def test_a_journal_left_loose_by_an_earlier_version_is_tightened(self) -> None:
        """The fix has to reach homes that were installed into before it existed.

        Creating new directories privately would leave every home managed by an
        older installer exactly as exposed as it was -- which is to say, the ones
        that have been managed longest.
        """
        with permissive_umask(), prepared_root("claude") as root:
            journal = state_dir(root)
            (journal / "runs").mkdir(parents=True)
            journal.chmod(0o775)
            (journal / "runs").chmod(0o775)

            install(root, "zotero")

            self.assertEqual(stat.S_IMODE(journal.lstat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((journal / "runs").lstat().st_mode), 0o700)

    def test_the_backup_tree_is_private(self) -> None:
        """Backups are copies of the user's own files, so reading them matters too.

        The backup path mirrors the absolute path of the file it snapshots, so
        the directories are created several levels deep in one call -- every one
        of them by the installer, and every one at the ambient umask before this.
        """
        with permissive_umask(), prepared_root("claude") as root:
            install(root, "zotero")
            install(root, "zotero,send-email")

            backups = state_dir(root) / "backups"
            self.assertTrue(backups.is_dir())
            self.assertTrue(any(path.is_file() for path in backups.rglob("*")))
            self.assertEqual(loose_directories(backups), [])

    def test_group_write_on_the_journal_is_what_a_forged_rollback_needs(self) -> None:
        """Name the consequence the mode is guarding against.

        Rewriting state.json to announce a run, dropping a run record beside it
        and a backup under it, then letting the victim roll that run back, copies
        attacker-chosen bytes over any path in the victim's home: a record with no
        recorded signature makes ``backup_integrity_ok`` true by construction, so
        nothing further in the rollback path objects.  Every step of that needs
        write permission on directories inside the journal and nothing else, which
        is why the directory mode is the whole control.
        """
        with permissive_umask(), prepared_root("claude") as root:
            victim = root / ".bashrc"
            victim.write_text("# the user's own shell profile\n", encoding="utf-8")
            install(root, "zotero")

            journal = state_dir(root)
            for path in [journal, *journal.rglob("*")]:
                if path.is_dir() and not path.is_symlink():
                    self.assertEqual(
                        stat.S_IMODE(path.lstat().st_mode) & 0o022,
                        0,
                        f"a second local user could plant a record in {path}",
                    )

            # With the owner's own permissions the forgery still works, which is
            # the point: the directory mode is the only thing standing between a
            # second local user and this.
            run_id = "20260101-000000-abcdef01"
            backup = journal / "backups" / run_id / "payload"
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text("PLANTED\n", encoding="utf-8")
            state = json.loads(state_file(root).read_text(encoding="utf-8"))
            state["runs"].append({"run_id": run_id, "action_count": 1})
            state_file(root).write_text(json.dumps(state), encoding="utf-8")
            (journal / "runs").mkdir(parents=True, exist_ok=True)
            (journal / "runs" / f"{run_id}.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "actions": [
                            {
                                "managed": True,
                                "key": f"claude:zotero:{victim}",
                                "artifact": str(victim),
                                "artifact_type": "skill-support-file",
                                "agent": "claude",
                                "skill": "zotero",
                                "applied": True,
                                "backup": str(backup),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            rollback(root, run_id, dry_run=False)
            self.assertEqual(victim.read_text(encoding="utf-8"), "PLANTED\n")


@POSIX_ONLY
class AntigravityPluginPermissionTests(unittest.TestCase):
    def test_the_plugin_package_is_private_for_a_narrow_selection(self) -> None:
        """plugin.json, mcp_config.json and hooks.json sit directly in the package.

        The permission pass bounded a file by the skills directory or by the
        support directory's parent, and these three are in neither, so they were
        written through the generic creator at the ambient umask.  A selection
        that also contributes a file deeper in the package creates it on the way
        to that deeper boundary and hides this, so the selection here is one that
        does not: zotero writes no support file under the package.
        """
        with permissive_umask(), prepared_root("antigravity") as root:
            install(root, "zotero")

            package = target_for(root, "antigravity").target_dir_for("plugin")
            present = {path.name for path in package.iterdir() if path.is_file()}
            self.assertEqual(
                present & {"plugin.json", "mcp_config.json", "hooks.json"},
                {"plugin.json", "mcp_config.json", "hooks.json"},
            )
            self.assertEqual(stat.S_IMODE(package.lstat().st_mode), 0o700)
            self.assertEqual(loose_directories(package), [])

    def test_the_agents_own_plugin_root_is_left_alone(self) -> None:
        """Tightening stops at the package this installer owns.

        The directory above it is the agent's, holding plugins written by other
        people, and changing its mode would reach outside the surface the
        installer declares.
        """
        with permissive_umask(), prepared_root("antigravity") as root:
            package = target_for(root, "antigravity").target_dir_for("plugin")
            vendor_root = package.parent
            (vendor_root / "someone-elses-plugin").mkdir(parents=True)
            vendor_root.chmod(0o755)

            install(root, "zotero")

            self.assertEqual(stat.S_IMODE(vendor_root.lstat().st_mode), 0o755)
            self.assertTrue((vendor_root / "someone-elses-plugin").is_dir())
            self.assertEqual(stat.S_IMODE(package.lstat().st_mode), 0o700)

    def test_the_package_bounds_a_file_written_directly_into_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = target_for(root, "antigravity").target_dir_for("plugin")
            boundary = managed_parent_boundary(
                root,
                {
                    "agent": "antigravity",
                    "skill": "zotero",
                    "operation": "create",
                    "path": str(package / "plugin.json"),
                },
            )
            self.assertEqual(boundary, package)

    def test_a_deeper_file_still_bounds_to_the_narrower_directory(self) -> None:
        """Adding the package must not widen the boundary for files below it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            support = target_for(root, "antigravity").support_dir_for("zotero")
            boundary = managed_parent_boundary(
                root,
                {
                    "agent": "antigravity",
                    "skill": "zotero",
                    "operation": "create",
                    "path": str(support / "references" / "notes.md"),
                },
            )
            self.assertEqual(boundary, support.parent)

    def test_no_other_target_treats_its_plugin_root_as_managed(self) -> None:
        """Every other agent's ``plugin`` entry is a directory it shares."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for agent in KNOWN_AGENT_NAMES:
                if agent == "antigravity":
                    continue
                target = target_for(root, agent)
                plugin_dir = target.artifact_dirs.get("plugin")
                if plugin_dir is None:
                    continue
                with self.subTest(agent=agent):
                    self.assertNotIn(
                        plugin_dir, managed_boundary_candidates(target, "zotero")
                    )


@POSIX_ONLY
class WholeRootPermissionTests(unittest.TestCase):
    def test_an_install_creates_no_group_writable_directory_anywhere(self) -> None:
        for selection in ("zotero", "zotero,agent-group-discuss,send-email"):
            with self.subTest(skills=selection):
                agents = ("claude", "codex", "antigravity", "copilot")
                with permissive_umask(), prepared_root(*agents) as root:
                    install(root, selection)
                    self.assertEqual(loose_directories(root / ".ai-agents-skills"), [])
                    for agent in agents:
                        home = target_for(root, agent).home
                        if home.is_dir():
                            self.assertEqual(loose_directories(home), [])


if __name__ == "__main__":
    unittest.main()
