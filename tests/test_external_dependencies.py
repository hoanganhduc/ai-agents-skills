"""Offline checks for the guarded external-dependency bootstrap."""

from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from installer.ai_agents_skills import cli
from installer.ai_agents_skills.external_dependencies import (
    EXTERNAL_EXECUTION_RISK_CONFIRMATION_ENV,
    EXTERNAL_PROVISION_CONFIRMATION_ENV,
    _activate_pointer,
    _assert_pointer_admissible,
    _child_environment,
    _create_venv,
    _git_argv,
    _new_external_transaction,
    _require_private_directory_chain,
    _require_private_root,
    _recover_external_transaction,
    _runtime_record_integrity,
    _run_quiet,
    _safe_extract_source,
    _save_external_transaction,
    _tool_path,
    apply_external_dependency_plan,
    build_external_dependency_plan,
    bundle_paths,
    external_confirmation_phrase,
    external_execution_risk_confirmation_phrase,
    external_provision_lock,
    external_transaction_path,
    load_external_state,
    plan_digest,
    save_external_state,
    _verify_runtime_venv,
    wheel_records,
    write_hash_lock,
)
from installer.ai_agents_skills.manifest import (
    ManifestError,
    load_manifests,
    validate_external_dependencies,
)


def run_cli(argv: list[str]) -> tuple[int, dict[str, object]]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        code = cli.main(argv)
    text = buffer.getvalue()
    return code, json.loads(text[text.index("{") :])


def write_wheel(path: Path, *, name: str, version: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{name.replace('-', '_')}-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        )


class ExternalDependencyManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifests = load_manifests()

    def test_two_fixed_bundles_are_registered(self) -> None:
        bundles = self.manifests["external_dependencies"]["bundles"]
        self.assertEqual(set(bundles), {"course-management", "vnu-eoffice"})
        self.assertEqual(
            bundles["course-management"]["revision"],
            "b3f8f647d4329d212958641f9ab18ecb154a21a8",
        )
        self.assertEqual(
            bundles["vnu-eoffice"]["revision"],
            "66d3ab694654bc5b11ca5c8253afeec1f0f00fae",
        )

    def test_manifest_rejects_an_unallowlisted_repository(self) -> None:
        malformed = copy.deepcopy(self.manifests["external_dependencies"])
        malformed["bundles"]["vnu-eoffice"]["repository"] = "https://example.invalid/repo.git"
        with self.assertRaisesRegex(ManifestError, "allowlisted GitHub HTTPS repository"):
            validate_external_dependencies(malformed)

    def test_manifest_rejects_a_non_pinned_revision(self) -> None:
        malformed = copy.deepcopy(self.manifests["external_dependencies"])
        malformed["bundles"]["course-management"]["revision"] = "main"
        with self.assertRaisesRegex(ManifestError, "full lowercase Git revision"):
            validate_external_dependencies(malformed)


class ExternalDependencyPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifests = load_manifests()
        patcher = mock.patch(
            "installer.ai_agents_skills.external_dependencies._wheel_lock_target",
            return_value="linux-aarch64",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_plan_is_pure_and_binds_pre_state_and_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            plan = build_external_dependency_plan(root, self.manifests, platform="linux")
            after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))

            self.assertEqual(before, after)
            self.assertEqual(plan["plan_digest"], plan_digest(plan))
            self.assertEqual([item["name"] for item in plan["bundles"]], ["course-management", "vnu-eoffice"])
            for item in plan["bundles"]:
                self.assertEqual(item["pre_state"]["pointer"]["kind"], "missing")
                self.assertEqual(item["pre_state"]["source"]["kind"], "missing")
                self.assertTrue(item["paths"]["pointer"].startswith(str(root)))

    def test_unknown_bundle_fails_without_touching_the_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "unknown external dependency bundle"):
                build_external_dependency_plan(
                    root,
                    self.manifests,
                    platform="linux",
                    requested_bundles=["not-a-bundle"],
                )
            self.assertEqual(list(root.iterdir()), [])

    def test_cli_apply_requires_the_preceding_plan_digest_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, report = run_cli(
                ["--json", "--root", str(root), "provision-external", "--apply"]
            )
            self.assertEqual(code, 1)
            self.assertIn("--plan-digest is required", str(report["error"]))
            self.assertEqual(list(root.iterdir()), [])

    def test_apply_rejects_a_mismatched_plan_digest_before_creating_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = build_external_dependency_plan(root, self.manifests, platform="linux")
            with self.assertRaisesRegex(ValueError, "does not match the current external dependency plan"):
                apply_external_dependency_plan(
                    plan,
                    self.manifests,
                    expected_plan_digest="sha256:not-the-plan",
                )
            self.assertEqual(list(root.iterdir()), [])

    def test_apply_rejects_a_pre_state_change_under_its_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = build_external_dependency_plan(
                root,
                self.manifests,
                platform="linux",
                requested_bundles=["vnu-eoffice"],
            )
            pointer = bundle_paths(
                root,
                "linux",
                "vnu-eoffice",
                self.manifests["external_dependencies"]["bundles"]["vnu-eoffice"],
            )["pointer"]
            os.symlink("unexpected", pointer)
            with self.assertRaisesRegex(ValueError, "pre-state changed after plan approval"):
                apply_external_dependency_plan(
                    plan,
                    self.manifests,
                    expected_plan_digest=plan["plan_digest"],
                )

    def test_plan_binds_declared_requirements_and_reviewed_wheel_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_external_dependency_plan(
                Path(tmp),
                self.manifests,
                platform="linux",
                requested_bundles=["vnu-eoffice"],
            )
            build_input = plan["bundles"][0]["build_input"]
            self.assertEqual(build_input["requirements"], ["requests>=2.25", "beautifulsoup4>=4.9"])
            self.assertEqual(build_input["wheel_lock"]["status"], "ready")
            self.assertEqual(build_input["wheel_lock"]["platform"], "linux-aarch64")
            self.assertEqual(build_input["wheel_lock"]["path"], "manifest/external-dependency-locks/linux-aarch64/vnu-eoffice.txt")
            self.assertTrue(build_input["digest"].startswith("sha256:"))
            self.assertEqual(len(build_input["wheel_lock"]["records"]), 11)

    def test_apply_rejects_a_requirement_change_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = build_external_dependency_plan(
                root,
                self.manifests,
                platform="linux",
                requested_bundles=["vnu-eoffice"],
            )
            changed = copy.deepcopy(self.manifests)
            changed["external_dependencies"]["bundles"]["vnu-eoffice"]["requirements"].append("urllib3")
            with self.assertRaisesRegex(ValueError, "pre-state changed after plan approval"):
                apply_external_dependency_plan(
                    plan,
                    changed,
                    expected_plan_digest=plan["plan_digest"],
                )
            self.assertEqual(list(root.iterdir()), [])

    def test_apply_rejects_an_unsupported_wheel_lock_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch(
                "installer.ai_agents_skills.external_dependencies._wheel_lock_target",
                return_value=None,
            ):
                plan = build_external_dependency_plan(
                    root,
                    self.manifests,
                    platform="macos",
                    requested_bundles=["vnu-eoffice"],
                )
            self.assertEqual(plan["bundles"][0]["build_input"]["wheel_lock"]["status"], "unavailable")
            with mock.patch(
                "installer.ai_agents_skills.external_dependencies._wheel_lock_target",
                return_value=None,
            ):
                with self.assertRaisesRegex(ValueError, "no reviewed hash-pinned wheel lock"):
                    apply_external_dependency_plan(
                        plan,
                        self.manifests,
                        expected_plan_digest=plan["plan_digest"],
                    )
            self.assertEqual(list(root.iterdir()), [])


class ExternalDependencyConfirmationTests(unittest.TestCase):
    def test_confirmation_is_bound_to_the_plan_digest(self) -> None:
        phrase = external_confirmation_phrase("sha256:abc")
        risk_phrase = external_execution_risk_confirmation_phrase("sha256:abc")
        self.assertEqual(phrase, "I approve external dependency plan sha256:abc")
        self.assertEqual(
            risk_phrase,
            "I understand pinned external build code is not sandboxed for plan sha256:abc",
        )
        with mock.patch.dict(
            os.environ,
            {
                EXTERNAL_PROVISION_CONFIRMATION_ENV: phrase,
                EXTERNAL_EXECUTION_RISK_CONFIRMATION_ENV: risk_phrase,
            },
            clear=False,
        ):
            from installer.ai_agents_skills.external_dependencies import verify_external_provision_confirmation

            verify_external_provision_confirmation("sha256:abc")
        with mock.patch.dict(
            os.environ,
            {EXTERNAL_PROVISION_CONFIRMATION_ENV: "I approve external dependency plan sha256:other"},
            clear=False,
        ):
            from installer.ai_agents_skills.external_dependencies import verify_external_provision_confirmation

            with self.assertRaisesRegex(ValueError, "did not bind the plan digest"):
                verify_external_provision_confirmation("sha256:abc")

        with mock.patch.dict(
            os.environ,
            {
                EXTERNAL_PROVISION_CONFIRMATION_ENV: phrase,
                EXTERNAL_EXECUTION_RISK_CONFIRMATION_ENV: "I do not understand",
            },
            clear=False,
        ):
            from installer.ai_agents_skills.external_dependencies import verify_external_provision_confirmation

            with self.assertRaisesRegex(ValueError, "execution-risk confirmation did not bind"):
                verify_external_provision_confirmation("sha256:abc")


class ExternalDependencyFilesystemTests(unittest.TestCase):
    def test_private_lock_creates_only_a_private_state_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with external_provision_lock(root):
                lock = root / ".ai-agents-skills" / "external-dependencies.lock"
                self.assertTrue(lock.is_file())
            self.assertTrue(lock.is_file())

    @unittest.skipIf(os.name != "posix", "POSIX mode/ownership check")
    def test_group_writable_root_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o777)
            try:
                with self.assertRaisesRegex(ValueError, "group/world writable"):
                    _require_private_root(root)
            finally:
                root.chmod(0o700)

    def test_pointer_activation_only_replaces_a_managed_pointer(self) -> None:
        manifests = load_manifests()
        spec = manifests["external_dependencies"]["bundles"]["vnu-eoffice"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = bundle_paths(root, "linux", "vnu-eoffice", spec)
            paths["generation"].mkdir(parents=True)
            _activate_pointer(paths["pointer"], paths["generation"], previous=None)
            record = {
                "repository": spec["repository"],
                "revision": spec["revision"],
                "source_path": str(paths["source"]),
                "generation_path": str(paths["generation"]),
                "pointer_path": str(paths["pointer"]),
                "distribution": spec["distribution"],
                "version": spec["version"],
            }
            previous = _assert_pointer_admissible(
                paths["pointer"], paths["generation"], record, paths, spec
            )
            self.assertIsInstance(previous, str)
            self.assertTrue(paths["pointer"].is_symlink())

    def test_safe_source_extraction_rejects_parent_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "source.tar"
            with tarfile.open(archive, "w", encoding="utf-8") as bundle:
                safe = tarfile.TarInfo("source/ok.txt")
                safe.size = len(b"safe")
                bundle.addfile(safe, io.BytesIO(b"safe"))
            destination = root / "destination"
            _safe_extract_source(archive, destination)
            self.assertEqual((destination / "ok.txt").read_text(encoding="utf-8"), "safe")

            unsafe = root / "unsafe.tar"
            with tarfile.open(unsafe, "w", encoding="utf-8") as bundle:
                escape = tarfile.TarInfo("source/../escape.txt")
                escape.size = len(b"escape")
                bundle.addfile(escape, io.BytesIO(b"escape"))
            with self.assertRaisesRegex(ValueError, "unsafe entry"):
                _safe_extract_source(unsafe, root / "unsafe-destination")


class ExternalDependencyWheelTests(unittest.TestCase):
    def test_hash_lock_covers_every_downloaded_wheel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wheelhouse = Path(tmp) / "wheelhouse"
            wheelhouse.mkdir()
            write_wheel(wheelhouse / "alpha-1.2-py3-none-any.whl", name="alpha", version="1.2")
            write_wheel(wheelhouse / "beta-3.4-py3-none-any.whl", name="beta", version="3.4")
            records = wheel_records(wheelhouse)
            lock = Path(tmp) / "requirements.lock"
            digest = write_hash_lock(lock, records)

            text = lock.read_text(encoding="utf-8")
            self.assertEqual(len(records), 2)
            self.assertIn("alpha==1.2 --hash=sha256:", text)
            self.assertIn("beta==3.4 --hash=sha256:", text)
            self.assertTrue(digest.startswith("sha256:"))

    def test_duplicate_normalized_distribution_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wheelhouse = Path(tmp)
            write_wheel(wheelhouse / "a-1.0-py3-none-any.whl", name="same_name", version="1.0")
            write_wheel(wheelhouse / "b-1.0-py3-none-any.whl", name="same-name", version="1.0")
            with self.assertRaisesRegex(ValueError, "multiple wheels"):
                wheel_records(wheelhouse)

    def test_vendored_metadata_does_not_hide_the_top_level_distribution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wheelhouse = Path(tmp)
            wheel = wheelhouse / "setuptools-like-1.0-py3-none-any.whl"
            write_wheel(wheel, name="setuptools-like", version="1.0")
            with zipfile.ZipFile(wheel, "a") as archive:
                archive.writestr(
                    "package/_vendor/example-9.9.dist-info/METADATA",
                    "Metadata-Version: 2.1\nName: example\nVersion: 9.9\n",
                )
            records = wheel_records(wheelhouse)
            self.assertEqual(
                [(record["distribution"], record["version"]) for record in records],
                [("setuptools-like", "1.0")],
            )

    def test_runtime_record_integrity_rejects_a_changed_installed_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            venv = Path(tmp) / "venv"
            site_packages = venv / "lib" / "python3.12" / "site-packages"
            metadata = site_packages / "demo-1.0.dist-info"
            metadata.mkdir(parents=True)
            module = site_packages / "demo.py"
            module.write_bytes(b"value = 1\n")
            encoded = base64.urlsafe_b64encode(hashlib.sha256(module.read_bytes()).digest()).decode().rstrip("=")
            (metadata / "RECORD").write_text(
                f"demo.py,sha256={encoded},{module.stat().st_size}\n"
                "demo-1.0.dist-info/RECORD,,\n",
                encoding="utf-8",
            )
            alternate = site_packages / "alternate-1.0.dist-info"
            alternate.mkdir()
            alternate_bytes = b"value = alternate\n"
            alternate_encoded = base64.urlsafe_b64encode(hashlib.sha256(alternate_bytes).digest()).decode().rstrip("=")
            (alternate / "RECORD").write_text(
                f"demo.py,sha256={alternate_encoded},{len(alternate_bytes)}\n"
                "alternate-1.0.dist-info/RECORD,,\n",
                encoding="utf-8",
            )
            digest = _runtime_record_integrity(venv)
            self.assertTrue(digest.startswith("sha256:"))
            module.write_bytes(b"value = 2\n")
            with self.assertRaisesRegex(ValueError, "does not match"):
                _runtime_record_integrity(venv)


class ExternalDependencyTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifests = load_manifests()
        patcher = mock.patch(
            "installer.ai_agents_skills.external_dependencies._wheel_lock_target",
            return_value="linux-aarch64",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _receipt(paths: dict[str, Path], spec: dict[str, object]) -> dict[str, object]:
        return {
            "repository": spec["repository"],
            "revision": spec["revision"],
            "tree": "a" * 40,
            "archive_sha256": "sha256:" + "b" * 64,
            "source_path": str(paths["source"]),
            "generation_path": str(paths["generation"]),
            "pointer_path": str(paths["pointer"]),
            "distribution": spec["distribution"],
            "version": spec["version"],
            "modules": list(spec["modules"]),
            "lock_sha256": "sha256:" + "c" * 64,
            "runtime_record_sha256": "sha256:" + "d" * 64,
            "wheels": [],
            "installed_at": "2026-08-25T00:00:00+00:00",
        }

    def _apply_with_fake_build(self, root: Path, *, fail_vnu: bool) -> None:
        plan = build_external_dependency_plan(root, self.manifests, platform="linux")

        def fake_source(paths: dict[str, Path], _spec: dict[str, object], **_kwargs: object) -> tuple[Path, str]:
            return paths["source"], "a" * 40

        def fake_build(
            _root: Path,
            _platform: str,
            name: str,
            spec: dict[str, object],
            paths: dict[str, Path],
            **_kwargs: object,
        ) -> dict[str, object]:
            if fail_vnu and name == "vnu-eoffice":
                raise ValueError("simulated second-bundle failure")
            _require_private_directory_chain(_root, paths["generation_parent"])
            paths["generation"].mkdir(mode=0o700)
            return self._receipt(paths, spec)

        with (
            mock.patch("installer.ai_agents_skills.external_dependencies._tool_path", return_value="/usr/bin/git"),
            mock.patch("installer.ai_agents_skills.external_dependencies._ensure_source_checkout", side_effect=fake_source),
            mock.patch("installer.ai_agents_skills.external_dependencies._build_generation", side_effect=fake_build),
        ):
            apply_external_dependency_plan(
                plan,
                self.manifests,
                expected_plan_digest=plan["plan_digest"],
            )

    def test_failed_multi_bundle_apply_removes_new_generations_and_can_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "simulated second-bundle failure"):
                self._apply_with_fake_build(root, fail_vnu=True)
            bundles = self.manifests["external_dependencies"]["bundles"]
            for name, spec in bundles.items():
                paths = bundle_paths(root, "linux", name, spec)
                self.assertFalse(paths["generation"].exists())
                self.assertFalse(paths["pointer"].exists())
            self.assertFalse(external_transaction_path(root).exists())
            self.assertFalse((root / ".ai-agents-skills" / "external-dependencies.json").exists())

            self._apply_with_fake_build(root, fail_vnu=False)
            for name, spec in bundles.items():
                paths = bundle_paths(root, "linux", name, spec)
                self.assertTrue(paths["generation"].is_dir())
                self.assertTrue(paths["pointer"].is_symlink())
            self.assertFalse(external_transaction_path(root).exists())

    def test_receipt_write_failure_rolls_back_the_new_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = build_external_dependency_plan(
                root,
                self.manifests,
                platform="linux",
                requested_bundles=["vnu-eoffice"],
            )
            spec = self.manifests["external_dependencies"]["bundles"]["vnu-eoffice"]
            paths = bundle_paths(root, "linux", "vnu-eoffice", spec)

            def fake_source(source_paths: dict[str, Path], _spec: dict[str, object], **_kwargs: object) -> tuple[Path, str]:
                return source_paths["source"], "a" * 40

            def fake_build(
                _root: Path,
                _platform: str,
                _name: str,
                build_spec: dict[str, object],
                build_paths: dict[str, Path],
                **_kwargs: object,
            ) -> dict[str, object]:
                _require_private_directory_chain(_root, build_paths["generation_parent"])
                build_paths["generation"].mkdir(mode=0o700)
                return self._receipt(build_paths, build_spec)

            with (
                mock.patch("installer.ai_agents_skills.external_dependencies._tool_path", return_value="/usr/bin/git"),
                mock.patch("installer.ai_agents_skills.external_dependencies._ensure_source_checkout", side_effect=fake_source),
                mock.patch("installer.ai_agents_skills.external_dependencies._build_generation", side_effect=fake_build),
                mock.patch("installer.ai_agents_skills.external_dependencies.save_external_state", side_effect=OSError("receipt unavailable")),
            ):
                with self.assertRaisesRegex(OSError, "receipt unavailable"):
                    apply_external_dependency_plan(
                        plan,
                        self.manifests,
                        expected_plan_digest=plan["plan_digest"],
                    )
            self.assertFalse(paths["generation"].exists())
            self.assertFalse(paths["pointer"].exists())
            self.assertFalse(external_transaction_path(root).exists())

    def test_recovery_rolls_back_an_interrupted_switch_and_keeps_a_completed_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = self.manifests["external_dependencies"]["bundles"]["vnu-eoffice"]
            paths = bundle_paths(root, "linux", "vnu-eoffice", spec)
            pending = [("vnu-eoffice", spec, paths, {}, None)]
            transaction = _new_external_transaction(
                root,
                "linux",
                "sha256:" + "a" * 64,
                load_external_state(root),
                pending,
            )
            _save_external_transaction(root, transaction)
            _require_private_directory_chain(root, paths["generation_parent"])
            paths["generation"].mkdir(mode=0o700)
            _activate_pointer(paths["pointer"], paths["generation"], previous=None)
            self.assertTrue(_recover_external_transaction(root, "linux", self.manifests))
            self.assertFalse(paths["generation"].exists())
            self.assertFalse(paths["pointer"].exists())
            self.assertFalse(external_transaction_path(root).exists())

            completed = _new_external_transaction(
                root,
                "linux",
                "sha256:" + "b" * 64,
                load_external_state(root),
                pending,
            )
            _require_private_directory_chain(root, paths["generation_parent"])
            paths["generation"].mkdir(mode=0o700)
            receipt = self._receipt(paths, spec)
            state_after = load_external_state(root)
            state_after["bundles"]["vnu-eoffice"] = receipt
            completed["state_after"] = state_after
            _save_external_transaction(root, completed)
            _activate_pointer(paths["pointer"], paths["generation"], previous=None)
            save_external_state(root, state_after)
            self.assertTrue(_recover_external_transaction(root, "linux", self.manifests))
            self.assertTrue(paths["generation"].is_dir())
            self.assertTrue(paths["pointer"].is_symlink())
            self.assertFalse(external_transaction_path(root).exists())


class ExternalDependencyEnvironmentTests(unittest.TestCase):
    def test_tool_lookup_uses_the_standard_system_path(self) -> None:
        with mock.patch("installer.ai_agents_skills.external_dependencies.shutil.which", return_value="/usr/bin/git") as which:
            self.assertEqual(_tool_path("git"), "/usr/bin/git")
        which.assert_called_once_with("git", path=os.defpath)

    def test_child_environment_drops_ambient_secrets_and_pip_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "child-home"
            inherited = {
                "VNU_EOFFICE_PASSWORD": "do-not-forward",
                "AAS_FILE_DELIVERY_SECRETS_FILE": "/private/secrets.json",
                "PIP_INDEX_URL": "https://private.example/simple",
                "PYTHONPATH": "/private/python",
                "PATH": "/private/bin",
            }
            with mock.patch.dict(os.environ, inherited, clear=False):
                env = _child_environment(home)
            for name in set(inherited) - {"PATH"}:
                self.assertNotIn(name, env)
            self.assertEqual(env["PIP_CONFIG_FILE"], os.devnull)
            self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
            self.assertEqual(env["PYTHONNOUSERSITE"], "1")
            self.assertEqual(env["HOME"], str(home))
            self.assertEqual(env["PATH"], os.defpath)

    def test_child_process_uses_private_cwd_when_invoked_from_a_hostile_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = _child_environment(root / "child-home")
            hostile = root / "hostile"
            hostile.mkdir()
            (hostile / "sitecustomize.py").write_text("raise SystemExit('hostile cwd imported')\n", encoding="utf-8")
            expected_cwd = json.dumps(str(Path(env["AAS_EXTERNAL_CHILD_CWD"]).resolve()))
            previous_cwd = Path.cwd()
            os.chdir(hostile)
            try:
                _run_quiet(
                    [
                        sys.executable,
                        "-I",
                        "-c",
                        f"import pathlib; assert str(pathlib.Path.cwd()) == {expected_cwd}",
                    ],
                    env=env,
                    label="hostile-cwd isolation check",
                )
            finally:
                os.chdir(previous_cwd)

    def test_python_verification_commands_use_isolated_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "venv"
            env = _child_environment(root / "child-home")
            captured: list[list[str]] = []

            def fake_run(argv: list[str], **_kwargs: object) -> None:
                captured.append(argv)
                (destination / "bin").mkdir(parents=True, exist_ok=True)
                (destination / "bin" / "python").touch()

            with mock.patch(
                "installer.ai_agents_skills.external_dependencies._run_quiet",
                side_effect=fake_run,
            ):
                python = _create_venv(
                    "/usr/bin/python3",
                    destination,
                    platform="linux",
                    env=env,
                    label="test venv",
                )
            self.assertEqual(captured, [["/usr/bin/python3", "-I", "-m", "venv", str(destination)]])

            spec = {
                "distribution": "demo",
                "version": "1.0.0",
                "modules": ["demo"],
                "help_modules": ["demo.agent"],
            }
            captured.clear()
            with mock.patch(
                "installer.ai_agents_skills.external_dependencies._run_quiet",
                side_effect=lambda argv, **_kwargs: captured.append(argv),
            ):
                _verify_runtime_venv(python, spec, env=env)
            self.assertEqual(len(captured), 3)
            self.assertTrue(all(argv[1] == "-I" for argv in captured))

    def test_git_invocation_disables_hooks_file_protocol_and_submodules(self) -> None:
        argv = _git_argv(
            "/usr/bin/git",
            {"AAS_EXTERNAL_EMPTY_GIT_HOOKS": "/private/empty-hooks"},
            "clone",
            "--no-checkout",
        )
        self.assertEqual(argv[:7], [
            "/usr/bin/git",
            "-c",
            "core.hooksPath=/private/empty-hooks",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "submodule.recurse=false",
        ])
        self.assertEqual(argv[7:], ["clone", "--no-checkout"])


if __name__ == "__main__":
    unittest.main()
