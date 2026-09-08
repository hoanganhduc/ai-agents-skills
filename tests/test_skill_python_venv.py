"""Shared skill Python venv: plan, apply, admission and verification.

No skill in the checked-in manifests needs a ``python`` block for these tests:
they use synthetic manifests and mock ``cli.load_manifests``.  Venvs are built
for real (``/usr/bin/python3 -I -m venv --without-pip``) inside a private
temporary root; every child the provisioner would start (venv, pip, the
interpreter probes) is answered by ``FakeRunner`` so nothing reaches the
network and nothing outside the temporary root is touched.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from installer.ai_agents_skills import cli, skill_python
from installer.ai_agents_skills.skill_python import (
    RECEIPT_NAME,
    RECEIPT_SCHEMA,
    SkillPythonError,
    SkillPythonUsageError,
    admit_skill_venv,
    apply_skill_python_plan,
    attested_base_python,
    build_skill_python_plan,
    check_modes,
    identify_skill_venv,
    skill_python_targets,
    verify_skill_python,
)
from tests import os_child_env


SYSTEM_PYTHON = "/usr/bin/python3"
SKILLS = ("zotero", "calibre", "docling")


def synthetic_manifests() -> dict[str, Any]:
    return {
        "runtime": {
            "skills": {
                "zotero": {
                    "runtime_dir": "zotero",
                    "python": {
                        "requirements": ["skills/zotero/requirements.txt"],
                        "modules": ["pyzotero", "requests"],
                        "provision": "default",
                    },
                },
                "calibre": {
                    "runtime_dir": "calibre",
                    "python": {"requirements": ["skills/calibre/requirements.txt"], "modules": ["ebooklib"]},
                },
                "docling": {
                    "runtime_dir": "docling",
                    "python": {
                        "requirements": ["skills/docling/requirements.txt"],
                        "modules": ["docling"],
                        "provision": "opt-in",
                    },
                },
                "graph-verifier": {"runtime_dir": "graph-verifier"},
            }
        }
    }


def completed(argv: list[str], returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


class FakeRunner:
    """Answer every child the provisioner starts.

    ``venv`` creation happens for real (without pip, so nothing is downloaded);
    pip and the interpreter probes are faked.  The fake ``pip install`` unpacks
    a group-writable package the way a wheel carrying its own modes would.
    """

    def __init__(
        self,
        *,
        version: str = "3.12.3 (main, fake) [GCC]",
        failing_modules: tuple[str, ...] = (),
        pip_check_rc: int = 0,
        pip_install_rc: int = 0,
        ensurepip_rc: int = 0,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.version = version
        self.failing_modules = failing_modules
        self.pip_check_rc = pip_check_rc
        self.pip_install_rc = pip_install_rc
        self.ensurepip_rc = ensurepip_rc
        self._real_run = subprocess.run

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        argv = [str(item) for item in argv]
        self.calls.append({"argv": argv, **kwargs})
        assert kwargs.get("encoding") == "utf-8", kwargs
        assert kwargs.get("check") is False, kwargs
        if argv[1:4] == ["-I", "-m", "venv"]:
            return self._real_run(
                [SYSTEM_PYTHON, "-I", "-m", "venv", "--without-pip", argv[4]],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if argv[1:3] == ["-I", "-c"] and argv[3] == "import ensurepip":
            return completed(argv, self.ensurepip_rc, stderr="" if not self.ensurepip_rc else "No module named ensurepip\n")
        if argv[1:3] == ["-I", "-c"] and argv[3] == skill_python.VERSION_PROBE:
            return completed(argv, 0, stdout=self.version + "\n")
        if argv[1:4] == ["-I", "-m", "pip"]:
            verb = argv[4]
            if verb == "--version":
                return completed(argv, 0, stdout="pip 24.0 (fake)\n")
            if verb == "install":
                if self.pip_install_rc:
                    return completed(argv, self.pip_install_rc, stderr="fake pip install failure\n")
                self.unpack_wheel(Path(argv[0]).parent.parent)
                return completed(argv, 0, stdout="Successfully installed fakepkg-1.0\n")
            if verb == "check":
                if self.pip_check_rc:
                    return completed(argv, self.pip_check_rc, stdout="fakepkg 1.0 requires missing, which is not installed.\n")
                return completed(argv, 0, stdout="No broken requirements found.\n")
            if verb == "freeze":
                return completed(argv, 0, stdout="fakepkg==1.0\n")
        if argv[1:3] == ["-I", "-c"] and argv[3] == skill_python.IMPORT_PROBE:
            missing = [module for module in argv[4:] if module in self.failing_modules]
            if missing:
                return completed(argv, 1, stderr=f"ModuleNotFoundError: No module named '{missing[0]}'\n")
            venv = Path(argv[0]).parent.parent
            return completed(argv, 0, stdout=f"{venv}\n{argv[0]}\n")
        raise AssertionError(f"unexpected child process: {argv}")

    @staticmethod
    def unpack_wheel(venv: Path) -> None:
        site = next(venv.glob("lib/python*/site-packages"))
        package = site / "fakepkg"
        package.mkdir(exist_ok=True)
        package.chmod(0o775)
        module = package / "__init__.py"
        module.write_text("VERSION = '1.0'\n" * 64, encoding="utf-8")
        module.chmod(0o664)

    def argv_calls(self, *prefix: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["argv"][1 : 1 + len(prefix)] == list(prefix)]


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def parse_json(text: str) -> dict[str, Any]:
    return json.loads(text[text.index("{"):])


def current_umask() -> int:
    value = os.umask(0)
    os.umask(value)
    return value


def cfg_lines(venv: Path) -> list[str]:
    return (venv / "pyvenv.cfg").read_text(encoding="utf-8").splitlines()


def write_cfg(venv: Path, lines: list[str]) -> None:
    (venv / "pyvenv.cfg").write_text("\n".join(lines) + "\n", encoding="utf-8")


def set_cfg(venv: Path, key: str, value: str) -> None:
    lines = cfg_lines(venv)
    write_cfg(venv, [f"{key} = {value}" if line.split("=")[0].strip() == key else line for line in lines])


def cfg_version_xy(venv: Path) -> str:
    for line in cfg_lines(venv):
        key, _, value = line.partition("=")
        if key.strip() == "version":
            return value.strip().rsplit(".", 1)[0]
    raise AssertionError("pyvenv.cfg carries no version")


@unittest.skipIf(os.name != "posix", "skill Python venvs are POSIX-only")
class SkillPythonCase(unittest.TestCase):
    def setUp(self) -> None:
        if not os.path.exists(SYSTEM_PYTHON):
            self.skipTest(f"{SYSTEM_PYTHON} is absent")
        previous = os.umask(0o077)
        self.addCleanup(os.umask, previous)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.root.chmod(0o700)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.checkout = self.root / "checkout"
        for skill in SKILLS:
            directory = self.checkout / "canonical" / "runtime" / "skills" / skill
            directory.mkdir(parents=True)
            (directory / "requirements.txt").write_text(f"{skill}-package==1.0\n", encoding="utf-8")
        self.attested = attested_base_python(None, preflight_ensurepip=False)
        self.venv = self.home / ".agents_skills_venv"
        self.manifests = synthetic_manifests()

    def requirement(self, skill: str) -> Path:
        return self.checkout / "canonical" / "runtime" / "skills" / skill / "requirements.txt"

    def build_plan(self, **overrides: Any) -> dict[str, Any]:
        params: dict[str, Any] = dict(
            skills=None, venv=self.venv, python=None, include_opt_in=False, recreate=False, remove=False, checkout=self.checkout
        )
        params.update(overrides)
        return build_skill_python_plan(self.home, self.manifests, **params)

    def make_venv(self, path: Path | None = None) -> Path:
        path = path or self.venv
        subprocess.run(
            [SYSTEM_PYTHON, "-I", "-m", "venv", "--without-pip", str(path)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return path

    def provision(self, runner: FakeRunner | None = None, **overrides: Any) -> tuple[FakeRunner, dict[str, Any]]:
        runner = runner or FakeRunner()
        plan = self.build_plan(**overrides)
        result = apply_skill_python_plan(plan, run=runner, log=lambda message: None)
        return runner, result

    def cli_args(self, command: str, *extra: str) -> list[str]:
        return [command, "--root", str(self.home), "--venv", str(self.venv), "--json", *extra]

    def receipt(self) -> dict[str, Any]:
        return json.loads((self.venv / RECEIPT_NAME).read_text(encoding="utf-8"))


class ModeNormalizationTests(SkillPythonCase):
    def _swapped_entry_leaves_external_file_private(self, *, directory: bool) -> None:
        self.venv.mkdir(mode=0o775)
        package = self.venv / "package"
        package.mkdir(mode=0o775)
        entry = package / "module.py"
        entry.write_text("pass\n", encoding="utf-8")
        entry.chmod(0o664)
        outside = self.home / "private"
        outside.mkdir(mode=0o700)
        secret = outside / "module.py"
        secret.write_text("private fixture\n", encoding="utf-8")
        secret.chmod(0o600)
        swapped = False
        original_walk = skill_python._walk_entries
        original_stat = os.stat

        def swap() -> None:
            nonlocal swapped
            if swapped:
                return
            swapped = True
            if directory:
                package.rename(self.venv / "old-package")
                package.symlink_to(outside, target_is_directory=True)
            else:
                entry.unlink()
                entry.symlink_to(secret)

        def swap_after_snapshot(root: Path):
            entries = original_walk(root)
            swap()
            return entries

        def swap_before_open(path, *args, **kwargs):
            info = original_stat(path, *args, **kwargs)
            name = "package" if directory else "module.py"
            if path == name and kwargs.get("dir_fd") is not None:
                swap()
            return info

        # Exercise the old eager walk and the descriptor walk at the same trust
        # boundary: after metadata was observed but before the entry is opened.
        with mock.patch.object(skill_python, "_walk_entries", side_effect=swap_after_snapshot), \
             mock.patch.object(skill_python.os, "stat", side_effect=swap_before_open):
            try:
                skill_python.normalize_modes(self.venv)
            except SkillPythonError:
                pass
        self.assertTrue(swapped, "the substitution must actually occur")
        self.assertEqual(stat.S_IMODE(secret.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o700)
        self.assertEqual(secret.read_text(encoding="utf-8"), "private fixture\n")

    def test_normalization_does_not_follow_a_replaced_file(self) -> None:
        self._swapped_entry_leaves_external_file_private(directory=False)

    def test_normalization_does_not_follow_a_replaced_directory(self) -> None:
        self._swapped_entry_leaves_external_file_private(directory=True)


class PlanTests(SkillPythonCase):
    def test_plan_lists_default_targets_and_excludes_opt_in(self) -> None:
        plan = self.build_plan()
        self.assertEqual([target["skill"] for target in plan["targets"]], ["calibre", "zotero"])
        self.assertEqual(plan["mode"], "create")
        self.assertEqual(plan["interpreter"], str(self.attested))
        self.assertEqual(plan["requirements"], [str(self.requirement("calibre")), str(self.requirement("zotero"))])
        self.assertTrue(plan["would"])
        self.assertTrue(json.dumps(plan))
        self.assertFalse(self.venv.exists())
        opted = self.build_plan(include_opt_in=True)
        self.assertEqual([target["skill"] for target in opted["targets"]], ["calibre", "docling", "zotero"])
        with mock.patch.object(cli, "load_manifests", return_value=self.manifests):
            code, out, _ = run_cli(self.cli_args("provision-skill-python"))
        self.assertEqual(code, 0)
        data = parse_json(out)
        self.assertEqual(data["status"], "dry-run")
        self.assertEqual([target["skill"] for target in data["targets"]], ["calibre", "zotero"])
        self.assertEqual(data["venv"], str(self.venv))
        self.assertFalse(self.venv.exists())

    def test_explicit_skills_override_opt_in_filter(self) -> None:
        plan = self.build_plan(skills={"docling"})
        self.assertEqual([target["skill"] for target in plan["targets"]], ["docling"])
        self.assertEqual(plan["targets"][0]["provision"], "opt-in")
        with mock.patch.object(cli, "load_manifests", return_value=self.manifests):
            code, out, _ = run_cli(self.cli_args("provision-skill-python", "--skills", "docling,zotero"))
            self.assertEqual(code, 0)
            self.assertEqual([target["skill"] for target in parse_json(out)["targets"]], ["docling", "zotero"])
            code, out, _ = run_cli(self.cli_args("provision-skill-python", "--skills", "docling", "--skills", "zotero"))
            self.assertEqual(code, 0)
            self.assertEqual([target["skill"] for target in parse_json(out)["targets"]], ["docling", "zotero"])

    def test_unknown_skill_exits_2(self) -> None:
        with self.assertRaises(SkillPythonUsageError) as caught:
            skill_python_targets(self.manifests, skills={"nope"}, include_opt_in=False)
        self.assertIn("known: calibre, docling, zotero", str(caught.exception))
        with mock.patch.object(cli, "load_manifests", return_value=self.manifests):
            argv = ["provision-skill-python", "--root", str(self.home), "--venv", str(self.venv), "--skills", "nope"]
            code, out, err = run_cli(argv)
            self.assertEqual(code, 2)
            self.assertIn("unknown skill Python target(s): nope", err)
            self.assertIn("known: calibre, docling, zotero", err)
            self.assertEqual(out, "")
            code, out, _ = run_cli(self.cli_args("provision-skill-python", "--skills", "nope"))
            self.assertEqual(code, 2)
            self.assertEqual(parse_json(out)["status"], "usage-error")
            code, _, err = run_cli(["verify-skill-python", "--root", str(self.home), "--venv", str(self.venv), "--skills", "nope"])
            self.assertEqual(code, 2)
            self.assertIn("unknown skill Python target(s): nope", err)
        self.assertFalse(self.venv.exists())

    def test_recreate_and_remove_are_mutually_exclusive(self) -> None:
        with mock.patch.object(cli, "load_manifests", return_value=self.manifests):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    cli.main(self.cli_args("provision-skill-python", "--recreate", "--remove"))
        self.assertEqual(caught.exception.code, 2)
        with self.assertRaises(SkillPythonUsageError):
            self.build_plan(recreate=True, remove=True)

    def test_protected_venv_locations_are_refused(self) -> None:
        for venv in (self.checkout / ".venv", self.home / ".course_venv", self.home / ".local" / "share" / "docling-venv"):
            with self.subTest(venv=str(venv)):
                with self.assertRaisesRegex(SkillPythonError, "skill Python venv path is protected"):
                    self.build_plan(venv=venv)


class ApplyTests(SkillPythonCase):
    def test_apply_creates_venv_installs_and_normalizes_modes(self) -> None:
        runner, result = self.provision()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mode"], "create")
        self.assertEqual(result["skills"], ["calibre", "zotero"])
        (venv_call,) = runner.argv_calls("-I", "-m", "venv")
        self.assertEqual(venv_call["argv"], [str(self.attested), "-I", "-m", "venv", str(self.venv)])
        self.assertEqual(venv_call["env"]["PATH"], "/usr/bin:/bin")
        self.assertEqual(venv_call["env"]["HOME"], str(self.home))
        self.assertEqual(len(runner.argv_calls("-I", "-c", "import ensurepip")), 1)
        (pip_call,) = runner.argv_calls("-I", "-m", "pip", "install")
        self.assertEqual(pip_call["argv"][0], str(self.venv / "bin" / "python"))
        self.assertIn("--require-virtualenv", pip_call["argv"])
        self.assertIn("--no-input", pip_call["argv"])
        self.assertEqual(
            pip_call["argv"][-4:], ["-r", str(self.requirement("calibre")), "-r", str(self.requirement("zotero"))]
        )
        self.assertEqual(pip_call["env"]["PIP_DISABLE_PIP_VERSION_CHECK"], "1")
        self.assertEqual(pip_call["timeout"], 3600)
        self.assertEqual(len(runner.argv_calls("-I", "-m", "pip", "check")), 1)
        self.assertEqual(check_modes(self.venv), [])
        self.assertGreater(result["normalized"], 0)
        self.assertGreater(result["installed_bytes"], 0)
        receipt_path = self.venv / RECEIPT_NAME
        self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
        receipt = self.receipt()
        self.assertEqual(receipt["schema"], RECEIPT_SCHEMA)
        self.assertEqual(receipt["venv"], str(self.venv))
        self.assertEqual(receipt["interpreter"], str(self.attested))
        self.assertEqual(receipt["python_version"], runner.version)
        self.assertEqual(receipt["freeze"], ["fakepkg==1.0"])
        self.assertEqual(sorted(receipt["skills"]), ["calibre", "zotero"])
        for skill in ("calibre", "zotero"):
            entry = receipt["skills"][skill]
            relative = f"skills/{skill}/requirements.txt"
            self.assertEqual(entry["requirements"], [relative])
            self.assertEqual(entry["sha256"], {relative: hashlib.sha256(self.requirement(skill).read_bytes()).hexdigest()})
            self.assertEqual(entry["installed_bytes"], result["installed_bytes"])
        self.assertEqual(receipt["skills"]["zotero"]["modules"], ["pyzotero", "requests"])
        self.assertEqual(admit_skill_venv(str(self.venv), attested_python=self.attested), (True, str(self.venv)))
        self.assertEqual(identify_skill_venv(self.venv, attested_python=self.attested), "receipt")
        _, again = self.provision(skills={"docling"})
        self.assertEqual(again["mode"], "adopt")
        merged = self.receipt()
        self.assertEqual(sorted(merged["skills"]), ["calibre", "docling", "zotero"])
        self.assertEqual(merged["skills"]["zotero"], receipt["skills"]["zotero"])

    def test_apply_adopts_an_existing_group_writable_venv(self) -> None:
        self.make_venv()
        for path in [self.venv, *self.venv.rglob("*")]:
            if path.is_symlink():
                continue
            path.chmod(0o775 if path.is_dir() else 0o664)
        self.assertNotEqual(check_modes(self.venv), [])
        runner, result = self.provision()
        self.assertEqual(result["mode"], "adopt")
        self.assertGreater(result["normalized"], 0)
        self.assertEqual(check_modes(self.venv), [])
        self.assertEqual(stat.S_IMODE(self.venv.stat().st_mode), 0o755 & ~0o022)
        self.assertEqual(runner.argv_calls("-I", "-m", "venv"), [])
        self.assertEqual(runner.argv_calls("-I", "-c", "import ensurepip"), [])
        self.assertEqual(stat.S_IMODE((self.venv / RECEIPT_NAME).stat().st_mode), 0o600)
        self.assertEqual(self.receipt()["venv"], str(self.venv))
        self.assertEqual(admit_skill_venv(str(self.venv), attested_python=self.attested), (True, str(self.venv)))

    def test_apply_refuses_existing_venv_from_another_python(self) -> None:
        self.make_venv()
        plan = self.build_plan()
        self.assertEqual(plan["mode"], "adopt")
        set_cfg(self.venv, "home", "/opt/x")
        with self.assertRaisesRegex(SkillPythonError, "refusing to modify it"):
            self.build_plan()
        with self.assertRaisesRegex(SkillPythonError, "refusing to modify it"):
            self.build_plan(recreate=True)
        with self.assertRaisesRegex(SkillPythonError, "refusing to modify it"):
            apply_skill_python_plan(plan, run=FakeRunner(), log=lambda message: None)
        self.assertIn("home = /opt/x", cfg_lines(self.venv))
        self.assertFalse((self.venv / RECEIPT_NAME).exists())
        # Identified through its receipt but no longer consistent with the attested Python:
        set_cfg(self.venv, "home", str(self.attested.parent))
        self.provision()
        set_cfg(self.venv, "include-system-site-packages", "true")
        with self.assertRaisesRegex(SkillPythonError, "pass --recreate to rebuild it"):
            self.build_plan()
        with self.assertRaisesRegex(SkillPythonError, "pass --recreate to rebuild it"):
            apply_skill_python_plan(plan, run=FakeRunner(), log=lambda message: None)

    def test_recreate_refuses_unidentified_directory(self) -> None:
        self.venv.mkdir()
        keep = self.venv / "keep.txt"
        keep.write_text("not a venv\n", encoding="utf-8")
        for flags in ({"recreate": True}, {"remove": True}, {}):
            with self.subTest(flags=flags):
                with self.assertRaisesRegex(SkillPythonError, "refusing to modify it"):
                    self.build_plan(**flags)
        plan = {
            "venv": str(self.venv),
            "home": str(self.home),
            "checkout": str(self.checkout),
            "python": None,
            "recreate": True,
            "remove": False,
            "targets": self.build_plan(venv=self.root / "elsewhere")["targets"],
        }
        with self.assertRaisesRegex(SkillPythonError, "refusing to modify it"):
            apply_skill_python_plan(plan, run=FakeRunner(), log=lambda message: None)
        self.assertEqual(keep.read_text(encoding="utf-8"), "not a venv\n")
        self.assertEqual(sorted(path.name for path in self.venv.iterdir()), ["keep.txt"])

    def test_apply_refuses_without_apply_flag(self) -> None:
        with mock.patch.object(cli, "load_manifests", return_value=self.manifests):
            with mock.patch.object(cli, "apply_skill_python_plan") as apply_mock:
                code, out, _ = run_cli(self.cli_args("provision-skill-python", "--skills", "zotero"))
        self.assertEqual(code, 0)
        self.assertEqual(parse_json(out)["status"], "dry-run")
        apply_mock.assert_not_called()
        self.assertFalse(self.venv.exists())
        self.assertFalse((self.home / ".ai-agents-skills").exists())

    def test_apply_refuses_non_attested_base_python(self) -> None:
        with self.assertRaisesRegex(SkillPythonError, "does not resolve to the attested system Python"):
            attested_base_python("/bin/sh", preflight_ensurepip=False)
        copied = self.root / "python3"
        shutil.copy2(self.attested, copied)
        with self.assertRaises(SkillPythonError):
            attested_base_python(str(copied), preflight_ensurepip=False)
        with self.assertRaises(SkillPythonError):
            self.build_plan(python="/bin/sh")
        with mock.patch.object(cli, "load_manifests", return_value=self.manifests):
            code, _, err = run_cli(
                ["provision-skill-python", "--root", str(self.home), "--venv", str(self.venv), "--python", "/bin/sh", "--apply"]
            )
        self.assertEqual(code, 2)
        self.assertIn("--python", err)
        self.assertFalse(self.venv.exists())
        runner = FakeRunner(ensurepip_rc=1)
        with self.assertRaisesRegex(SkillPythonError, "python3-venv"):
            self.provision(runner)
        self.assertEqual(runner.argv_calls("-I", "-m", "venv"), [])
        self.assertFalse(self.venv.exists())

    def test_umask_is_restored(self) -> None:
        os.umask(0o027)
        self.provision()
        self.assertEqual(current_umask(), 0o027)
        with self.assertRaisesRegex(SkillPythonError, "pip check failed"):
            self.provision(FakeRunner(pip_check_rc=1), skills={"docling"})
        self.assertEqual(current_umask(), 0o027)
        with self.assertRaisesRegex(SkillPythonError, "pip install failed"):
            self.provision(FakeRunner(pip_install_rc=1), skills={"docling"})
        self.assertEqual(current_umask(), 0o027)
        self.provision(remove=True)
        self.assertEqual(current_umask(), 0o027)
        self.assertFalse(self.venv.exists())

    def test_remove_deletes_only_an_identified_venv(self) -> None:
        with self.assertRaisesRegex(SkillPythonError, "does not exist"):
            self.build_plan(remove=True)
        self.provision()
        plan = self.build_plan(remove=True)
        self.assertEqual(plan["mode"], "remove")
        self.assertEqual(plan["targets"], [])
        self.assertTrue(self.venv.exists())
        result = apply_skill_python_plan(plan, run=FakeRunner(), log=lambda message: None)
        self.assertEqual(result["mode"], "remove")
        self.assertTrue(result["removed"])
        self.assertFalse(self.venv.exists())
        self.assertTrue(self.home.exists())


class VerifyTests(SkillPythonCase):
    def verify(self, runner: FakeRunner | None = None, targets: Any = None) -> dict[str, Any]:
        return verify_skill_python(self.venv, targets, attested_python=self.attested, run=runner or FakeRunner(), home=self.home)

    def test_verify_reports_missing_module_per_skill(self) -> None:
        self.provision()
        clean = self.verify()
        self.assertEqual(clean["status"], "ok", clean)
        self.assertEqual(sorted(clean["skills"]), ["calibre", "zotero"])
        self.assertTrue(clean["pip_check"]["ok"])
        self.assertEqual(clean["mode_violations"], [])
        result = self.verify(FakeRunner(failing_modules=("ebooklib",)))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["skills"]["calibre"]["status"], "failed")
        self.assertIn("ebooklib", result["skills"]["calibre"]["detail"])
        self.assertEqual(result["skills"]["zotero"]["status"], "ok")
        self.assertEqual(result["skills"]["zotero"]["modules"], ["pyzotero", "requests"])
        self.assertTrue(result["pip_check"]["ok"])
        explicit = self.verify(targets=skill_python_targets(self.manifests, skills={"docling", "zotero"}, include_opt_in=False))
        self.assertEqual(explicit["status"], "failed")
        self.assertEqual(explicit["skills"]["docling"]["status"], "failed")
        self.assertIn("not provisioned", explicit["skills"]["docling"]["detail"])
        self.assertEqual(explicit["skills"]["zotero"]["status"], "ok")

        def with_runner(runner: FakeRunner) -> Any:
            return lambda venv, targets, **kwargs: verify_skill_python(venv, targets, run=runner, **kwargs)

        with mock.patch.object(cli, "load_manifests", return_value=self.manifests):
            with mock.patch.object(cli, "verify_skill_python", new=with_runner(FakeRunner(failing_modules=("ebooklib",)))):
                code, out, _ = run_cli(self.cli_args("verify-skill-python"))
                self.assertEqual(code, 1)
                self.assertEqual(parse_json(out)["status"], "failed")
            with mock.patch.object(cli, "verify_skill_python", new=with_runner(FakeRunner())):
                code, out, _ = run_cli(self.cli_args("verify-skill-python"))
                self.assertEqual(code, 0)
                self.assertEqual(parse_json(out)["status"], "ok")

    def test_verify_detects_interpreter_version_change(self) -> None:
        runner, _ = self.provision()
        self.assertEqual(self.verify()["status"], "ok")
        result = self.verify(FakeRunner(version="3.13.0 (main, fake) [GCC]"))
        self.assertEqual(result["status"], "failed")
        self.assertIn(
            f"skill Python venv was built for {runner.version}; re-run provision-skill-python --apply", result["failures"]
        )
        self.assertEqual(result["skills"]["zotero"]["status"], "ok")

    def test_verify_walks_the_tree_for_mode_violations(self) -> None:
        self.provision()
        deep = next(self.venv.glob("lib/python*/site-packages")) / "fakepkg" / "__init__.py"
        deep.chmod(0o664)
        result = self.verify()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["mode_violations"], [str(deep)])
        self.assertTrue(any("group/other writable" in failure for failure in result["failures"]))
        self.assertEqual(admit_skill_venv(str(self.venv), attested_python=self.attested), (True, str(self.venv)))
        deep.chmod(0o644)
        self.assertEqual(self.verify()["status"], "ok")

    def test_verify_fails_closed_when_the_venv_is_not_admitted(self) -> None:
        result = self.verify()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failures"], [f"skill Python venv is not a directory: {self.venv}"])
        self.assertEqual(result["skills"], {})
        self.provision()
        (self.venv / RECEIPT_NAME).unlink()
        result = self.verify()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("receipt is missing" in failure for failure in result["failures"]))


class AdmissionTests(SkillPythonCase):
    def fresh_venv(self, name: str) -> Path:
        directory = self.root / name
        directory.mkdir(mode=0o700)
        return self.make_venv(directory / "venv")

    def admit(self, prefix: str | os.PathLike[str]) -> tuple[bool, str]:
        return admit_skill_venv(prefix, attested_python=self.attested)

    def test_admit_rules(self) -> None:
        with self.subTest("good venv admitted"):
            venv = self.fresh_venv("good")
            self.assertEqual(self.admit(venv), (True, str(venv)))
            self.assertEqual(self.admit(str(venv)), (True, str(venv)))
        with self.subTest("relative prefix refused"):
            self.assertEqual(self.admit("venv"), (False, "skill Python venv path must be absolute"))
        with self.subTest("g+w root refused"):
            venv = self.fresh_venv("gw-root")
            venv.chmod(0o770)
            self.assertEqual(self.admit(venv), (False, f"skill Python venv is not owner-controlled: {venv}"))
        with self.subTest("g+w parent refused"):
            venv = self.fresh_venv("gw-parent")
            venv.parent.chmod(0o770)
            self.assertEqual(self.admit(venv), (False, f"skill Python venv is not owner-controlled: {venv.parent}"))
        with self.subTest("symlinked prefix refused"):
            venv = self.fresh_venv("linked")
            link = venv.parent / "link"
            link.symlink_to(venv)
            self.assertEqual(self.admit(link), (False, f"skill Python venv is not a directory: {link}"))
        with self.subTest("missing prefix refused"):
            missing = self.root / "absent"
            self.assertEqual(self.admit(missing), (False, f"skill Python venv is not a directory: {missing}"))
        with self.subTest("non-canonical prefix/ refused"):
            venv = self.fresh_venv("trailing")
            self.assertEqual(self.admit(f"{venv}/"), (False, f"skill Python venv path is not canonical: {venv}/"))
        with self.subTest("non-canonical prefix/./x refused"):
            venv = self.fresh_venv("dotted")
            dotted = f"{venv.parent}/./{venv.name}"
            self.assertEqual(self.admit(dotted), (False, f"skill Python venv path is not canonical: {dotted}"))
        with self.subTest("bin/python copied not symlinked refused"):
            venv = self.fresh_venv("copied")
            (venv / "bin" / "python").unlink()
            shutil.copy2(self.attested, venv / "bin" / "python")
            self.assertEqual(self.admit(venv), (False, "skill Python venv bin/python is not a symlink"))
        with self.subTest("bin/python to /bin/sh refused"):
            venv = self.fresh_venv("shell")
            (venv / "bin" / "python").unlink()
            (venv / "bin" / "python").symlink_to("/bin/sh")
            self.assertEqual(self.admit(venv), (False, "skill Python venv bin/python is not the attested system Python"))
        with self.subTest("missing pyvenv.cfg refused"):
            venv = self.fresh_venv("no-cfg")
            (venv / "pyvenv.cfg").unlink()
            self.assertEqual(self.admit(venv), (False, f"skill Python venv has no regular pyvenv.cfg: {venv}"))
        with self.subTest("FIFO pyvenv.cfg refused"):
            venv = self.fresh_venv("fifo-cfg")
            (venv / "pyvenv.cfg").unlink()
            os.mkfifo(venv / "pyvenv.cfg", 0o600)
            self.assertEqual(self.admit(venv), (False, f"skill Python venv has no regular pyvenv.cfg: {venv}"))
        with self.subTest("symlinked pyvenv.cfg refused"):
            venv = self.fresh_venv("linked-cfg")
            (venv / "pyvenv.cfg").rename(venv / "pyvenv.real")
            (venv / "pyvenv.cfg").symlink_to(venv / "pyvenv.real")
            self.assertEqual(self.admit(venv), (False, f"skill Python venv has no regular pyvenv.cfg: {venv}"))
        with self.subTest("g+w pyvenv.cfg refused"):
            venv = self.fresh_venv("gw-cfg")
            (venv / "pyvenv.cfg").chmod(0o664)
            self.assertEqual(self.admit(venv), (False, f"skill Python venv is not owner-controlled: {venv}/pyvenv.cfg"))
        with self.subTest("symlinked lib refused"):
            venv = self.fresh_venv("linked-lib")
            (venv / "lib").rename(venv / "lib.real")
            (venv / "lib").symlink_to(venv / "lib.real")
            self.assertEqual(self.admit(venv), (False, f"skill Python venv component is a symlink: {venv}/lib"))
        with self.subTest("home mismatch refused"):
            venv = self.fresh_venv("home-mismatch")
            set_cfg(venv, "home", "/opt/x")
            self.assertEqual(self.admit(venv), (False, "skill Python venv was not built from the attested Python"))
        with self.subTest("include-system-site-packages = true refused"):
            venv = self.fresh_venv("system-site")
            set_cfg(venv, "include-system-site-packages", "true")
            self.assertEqual(self.admit(venv), (False, "skill Python venv must not include system site-packages"))
        with self.subTest("version format refused"):
            venv = self.fresh_venv("version-format")
            set_cfg(venv, "version", "3.12")
            self.assertEqual(self.admit(venv), (False, "skill Python venv pyvenv.cfg version must be X.Y.Z"))
        if skill_python.VERSIONED_BINARY_RE.fullmatch(self.attested.name):
            with self.subTest("version mismatch refused"):
                venv = self.fresh_venv("version-mismatch")
                set_cfg(venv, "version", "2.7.18")
                self.assertEqual(self.admit(venv), (False, "skill Python venv version does not match the attested Python"))
        with self.subTest("duplicate home key refused"):
            venv = self.fresh_venv("dup-home")
            write_cfg(venv, cfg_lines(venv) + [f"home = {self.attested.parent}"])
            self.assertEqual(
                self.admit(venv),
                (False, "skill Python venv pyvenv.cfg must carry home, version and include-system-site-packages exactly once"),
            )
        with self.subTest("g+w .pth refused"):
            venv = self.fresh_venv("gw-pth")
            site = next(venv.glob("lib/python*/site-packages"))
            pth = site / "extra.pth"
            pth.write_text("fakepkg\n", encoding="utf-8")
            pth.chmod(0o664)
            self.assertEqual(self.admit(venv), (False, f"skill Python venv startup file is not owner-controlled: {pth}"))
        with self.subTest("symlinked sitecustomize.py refused"):
            venv = self.fresh_venv("linked-startup")
            site = next(venv.glob("lib/python*/site-packages"))
            (site / "sitecustomize.py").symlink_to("/dev/null")
            self.assertEqual(
                self.admit(venv), (False, f"skill Python venv startup file is a symlink: {site / 'sitecustomize.py'}")
            )
        with self.subTest("regular-file bin/python3 refused"):
            venv = self.fresh_venv("regular-python3")
            # venv links bin/python -> python3; point it at the attested binary so only python3 is at fault
            (venv / "bin" / "python").unlink()
            (venv / "bin" / "python").symlink_to(self.attested)
            (venv / "bin" / "python3").unlink()
            (venv / "bin" / "python3").write_text("#!/bin/sh\n", encoding="utf-8")
            self.assertEqual(self.admit(venv), (False, "skill Python venv bin/python3 is not a symlink"))
        with self.subTest("bin/pythonX.Y symlink to /bin/sh refused"):
            venv = self.fresh_venv("versioned-shell")
            versioned = venv / "bin" / f"python{cfg_version_xy(venv)}"
            if versioned.is_symlink():
                versioned.unlink()
            versioned.symlink_to("/bin/sh")
            self.assertEqual(
                self.admit(venv),
                (False, f"skill Python venv bin/{versioned.name} is not the attested system Python"),
            )
        with self.subTest("foreign uid root refused (mocked lstat)"):
            venv = self.fresh_venv("foreign")
            real_lstat = os.lstat

            def foreign_lstat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
                info = real_lstat(path, *args, **kwargs)
                if os.fspath(path) == str(venv):
                    return os.stat_result((info.st_mode, info.st_ino, info.st_dev, info.st_nlink, 4242, info.st_gid, info.st_size, info.st_atime, info.st_mtime, info.st_ctime))
                return info

            with mock.patch.object(skill_python.os, "lstat", side_effect=foreign_lstat):
                self.assertEqual(self.admit(venv), (False, f"skill Python venv is not owner-controlled: {venv}"))
        with self.subTest("g+w package file inside site-packages accepted by admission but reported by verify"):
            venv = self.fresh_venv("gw-package")
            site = next(venv.glob("lib/python*/site-packages"))
            (site / "fakepkg").mkdir()
            module = site / "fakepkg" / "__init__.py"
            module.write_text("VERSION = '1.0'\n", encoding="utf-8")
            module.chmod(0o664)
            self.assertEqual(self.admit(venv), (True, str(venv)))
            result = verify_skill_python(venv, None, attested_python=self.attested, run=FakeRunner(), home=self.home)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["mode_violations"], [str(module)])

    # The launcher decides admission in bash (run_skill.sh skill_python_prefix)
    # and the provisioner, the verifier and the smoke harness decide it in
    # Python (admit_skill_venv).  Both must reach the same verdict with the same
    # reason on every fixture, otherwise a venv the harness reports as admitted
    # is refused at launch (or the reverse).
    def bash_admit(self, script: Path, prefix: str | os.PathLike[str]) -> tuple[bool, str]:
        env = {
            **os_child_env(),
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin",
            "AAS_SKILL_VENV": str(prefix),
        }
        completed = subprocess.run(
            ["bash", str(script), str(self.attested)],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if completed.returncode == 0:
            return True, completed.stdout.strip()
        self.assertEqual(completed.returncode, 1, completed.stderr)
        return False, completed.stderr.strip()

    def admission_fixtures(self) -> list[tuple[str, Path | str]]:
        """The ``test_admit_rules`` builders, minus the mock-only foreign-uid case."""
        fixtures: list[tuple[str, Path | str]] = []
        fixtures.append(("good", self.fresh_venv("p-good")))
        fixtures.append(("relative", "venv"))
        venv = self.fresh_venv("p-gw-root")
        venv.chmod(0o770)
        fixtures.append(("g+w root", venv))
        venv = self.fresh_venv("p-gw-parent")
        venv.parent.chmod(0o770)
        fixtures.append(("g+w parent", venv))
        venv = self.fresh_venv("p-link")
        link = venv.parent / "link"
        link.symlink_to(venv)
        fixtures.append(("symlinked prefix", link))
        fixtures.append(("missing", self.root / "p-absent"))
        venv = self.fresh_venv("p-trailing")
        fixtures.append(("trailing slash", f"{venv}/"))
        venv = self.fresh_venv("p-dotted")
        fixtures.append(("dotted", f"{venv.parent}/./{venv.name}"))
        venv = self.fresh_venv("p-copied")
        (venv / "bin" / "python").unlink()
        shutil.copy2(self.attested, venv / "bin" / "python")
        fixtures.append(("copied bin/python", venv))
        venv = self.fresh_venv("p-sh")
        (venv / "bin" / "python").unlink()
        (venv / "bin" / "python").symlink_to("/bin/sh")
        fixtures.append(("bin/python to /bin/sh", venv))
        venv = self.fresh_venv("p-nocfg")
        (venv / "pyvenv.cfg").unlink()
        fixtures.append(("missing cfg", venv))
        venv = self.fresh_venv("p-fifo")
        (venv / "pyvenv.cfg").unlink()
        os.mkfifo(venv / "pyvenv.cfg", 0o600)
        fixtures.append(("FIFO cfg", venv))
        venv = self.fresh_venv("p-linkcfg")
        (venv / "pyvenv.cfg").rename(venv / "pyvenv.real")
        (venv / "pyvenv.cfg").symlink_to(venv / "pyvenv.real")
        fixtures.append(("symlinked cfg", venv))
        venv = self.fresh_venv("p-gwcfg")
        (venv / "pyvenv.cfg").chmod(0o664)
        fixtures.append(("g+w cfg", venv))
        venv = self.fresh_venv("p-linklib")
        (venv / "lib").rename(venv / "lib.real")
        (venv / "lib").symlink_to(venv / "lib.real")
        fixtures.append(("symlinked lib", venv))
        venv = self.fresh_venv("p-home")
        set_cfg(venv, "home", "/opt/x")
        fixtures.append(("home mismatch", venv))
        venv = self.fresh_venv("p-system")
        set_cfg(venv, "include-system-site-packages", "true")
        fixtures.append(("system site-packages", venv))
        venv = self.fresh_venv("p-vfmt")
        set_cfg(venv, "version", "3.12")
        fixtures.append(("version format", venv))
        if skill_python.VERSIONED_BINARY_RE.fullmatch(self.attested.name):
            venv = self.fresh_venv("p-vmismatch")
            set_cfg(venv, "version", "2.7.18")
            fixtures.append(("version mismatch", venv))
        venv = self.fresh_venv("p-duphome")
        write_cfg(venv, cfg_lines(venv) + [f"home = {self.attested.parent}"])
        fixtures.append(("duplicate home", venv))
        venv = self.fresh_venv("p-gwpth")
        site = next(venv.glob("lib/python*/site-packages"))
        pth = site / "extra.pth"
        pth.write_text("fakepkg\n", encoding="utf-8")
        pth.chmod(0o664)
        fixtures.append(("g+w .pth", venv))
        venv = self.fresh_venv("p-sitecustom")
        site = next(venv.glob("lib/python*/site-packages"))
        (site / "sitecustomize.py").symlink_to("/dev/null")
        fixtures.append(("symlinked sitecustomize", venv))
        venv = self.fresh_venv("p-regpy3")
        (venv / "bin" / "python").unlink()
        (venv / "bin" / "python").symlink_to(self.attested)
        (venv / "bin" / "python3").unlink()
        (venv / "bin" / "python3").write_text("#!/bin/sh\n", encoding="utf-8")
        fixtures.append(("regular bin/python3", venv))
        venv = self.fresh_venv("p-versh")
        versioned = venv / "bin" / f"python{cfg_version_xy(venv)}"
        if versioned.is_symlink():
            versioned.unlink()
        versioned.symlink_to("/bin/sh")
        fixtures.append(("versioned symlink to /bin/sh", venv))
        venv = self.fresh_venv("p-gwpkg")
        site = next(venv.glob("lib/python*/site-packages"))
        (site / "fakepkg").mkdir()
        module = site / "fakepkg" / "__init__.py"
        module.write_text("VERSION = '1.0'\n", encoding="utf-8")
        module.chmod(0o664)
        fixtures.append(("g+w package accepted", venv))
        return fixtures

    def test_bash_and_python_admission_agree(self) -> None:
        if shutil.which("bash") is None:
            self.skipTest("bash is absent")
        launcher = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_skill.sh"
        source = launcher.read_text(encoding="utf-8")

        def bash_function(name: str) -> str:
            start = source.index(f"{name}() {{")
            end = source.index("\n}\n", start) + len("\n}\n")
            return source[start:end]

        script = self.root / "admit.sh"
        script.write_text(
            "set -uo pipefail\n"
            + bash_function("trusted_metadata")
            + bash_function("root_sticky_directory")
            + bash_function("skill_python_prefix")
            + 'skill_python_prefix "$1"\n',
            encoding="utf-8",
        )
        script.chmod(0o700)
        fixtures = self.admission_fixtures()
        self.assertGreaterEqual(len(fixtures), 24)
        verdicts: dict[str, tuple[bool, str]] = {}
        for label, prefix in fixtures:
            with self.subTest(label):
                python_verdict = self.admit(prefix)
                bash_verdict = self.bash_admit(script, prefix)
                self.assertEqual(bash_verdict, python_verdict)
                verdicts[label] = python_verdict
        self.assertEqual(verdicts["good"][0], True)
        self.assertEqual(verdicts["g+w package accepted"][0], True)
        self.assertEqual(sum(1 for ok, _ in verdicts.values() if ok), 2)


if __name__ == "__main__":
    unittest.main()
