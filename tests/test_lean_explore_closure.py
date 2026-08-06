from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
HELPER = (
    REPO
    / "canonical/runtime/skills/lean-explore-mcp/lean_explore_mcp.py"
)
SPEC = importlib.util.spec_from_file_location("aas_lean_closure_test", HELPER)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@unittest.skipUnless(os.name == "posix", "held closure descriptors are POSIX-only")
class LeanExploreClosureTests(unittest.TestCase):
    def _closure(self, root: Path) -> tuple[Path, str]:
        closure = root / "lean-explore"
        relative = "lib/python3.12/site-packages"
        site = closure / relative
        package = site / "lean_explore"
        distribution = site / "lean_explore-1.2.1.dist-info"
        package.mkdir(parents=True)
        distribution.mkdir()
        (package / "__init__.py").write_text("VERSION = '1.2.1'\n", encoding="utf-8")
        (distribution / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: lean-explore\nVersion: 1.2.1\n",
            encoding="utf-8",
        )
        for directory in (closure, closure / "lib", closure / "lib/python3.12", site, package, distribution):
            directory.chmod(0o755)
        for file_path in (package / "__init__.py", distribution / "METADATA"):
            file_path.chmod(0o644)
        claim = MODULE._installed_content_manifest(
            closure, expected_owner=os.getuid()
        )
        marker = {
            "schema": MODULE.CLOSURE_MARKER_SCHEMA,
            "environment": "lean-explore",
            "distributions": [
                {
                    "name": "lean-explore",
                    "version": "1.2.1",
                    "filename": "lean_explore-1.2.1-py3-none-any.whl",
                    "sha256": "0" * 64,
                }
            ],
            "installedContent": claim,
        }
        marker_path = closure / MODULE.CLOSURE_MARKER
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        marker_path.chmod(0o444)
        return closure, relative

    def _verify(self, closure: Path, relative: str, **kwargs: object) -> str:
        descriptor = os.open(
            closure, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            return MODULE.verify_lean_explore_closure(
                descriptor,
                relative,
                expected_owner=os.getuid(),
                **kwargs,
            )
        finally:
            os.close(descriptor)

    def test_complete_marker_bound_closure_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            closure, relative = self._closure(Path(temporary))
            observed = self._verify(closure, relative)
            self.assertTrue(observed.endswith(relative))

    def test_same_version_dist_info_and_module_poison_are_rejected(self) -> None:
        for poison in ("dist-info", "module"):
            with self.subTest(poison=poison), tempfile.TemporaryDirectory() as temporary:
                closure, relative = self._closure(Path(temporary))
                site = closure / relative
                if poison == "dist-info":
                    forged = site / "forged_lean_explore-1.2.1.dist-info"
                    forged.mkdir()
                    (forged / "METADATA").write_text(
                        "Name: lean-explore\nVersion: 1.2.1\n", encoding="utf-8"
                    )
                    forged.chmod(0o755)
                    (forged / "METADATA").chmod(0o644)
                else:
                    poison_path = site / "lean_explore" / "poison.py"
                    poison_path.write_text(
                        "STOLEN = True\n", encoding="utf-8"
                    )
                    poison_path.chmod(0o644)
                with self.assertRaisesRegex(RuntimeError, "differs"):
                    self._verify(closure, relative)

    def test_post_validation_mutation_is_rejected_before_key_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            closure, relative = self._closure(Path(temporary))
            target = closure / relative / "lean_explore" / "__init__.py"

            def mutate() -> None:
                target.write_text("VERSION = 'forged'\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "changed after validation"):
                self._verify(closure, relative, between_passes=mutate)

    def test_held_generation_fd_ignores_post_open_path_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            closure, relative = self._closure(base)
            descriptor = os.open(
                closure, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            moved = base / "held-generation"
            closure.rename(moved)
            replacement = base / "lean-explore"
            replacement.mkdir()
            (replacement / "poison.py").write_text("STOLEN = True\n", encoding="utf-8")
            try:
                observed = MODULE.verify_lean_explore_closure(
                    descriptor,
                    relative,
                    expected_owner=os.getuid(),
                )
            finally:
                os.close(descriptor)
            self.assertIn("/fd/", observed)


if __name__ == "__main__":
    unittest.main()
