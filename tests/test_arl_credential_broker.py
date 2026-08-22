"""Offline tests for the ARL credential broker's provider config projection."""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNERS_DIR = REPO_ROOT / "canonical" / "runtime" / "runners"
sys.path.insert(0, str(RUNNERS_DIR))

if os.name == "nt":
    raise unittest.SkipTest("the ARL credential broker is POSIX-only (pwd, AF_UNIX)")

import arl_credential_broker as broker  # noqa: E402


def _bare_state(provider_config: dict[str, str]) -> broker.CredentialState:
    """Build a state without __init__: the projection only reads provider_config."""
    state = broker.CredentialState.__new__(broker.CredentialState)
    state.provider_config = provider_config
    return state


class PrepareConfigProjectionTests(unittest.TestCase):
    def test_seed_state_provider_gets_writable_seeded_copy_without_mounts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "real-grok"
            child_home = root / "child-home"
            source.mkdir()
            child_home.mkdir()
            (source / "auth.json").write_text("{}", encoding="utf-8")
            (source / "config.toml").write_text("", encoding="utf-8")
            (source / "sessions.log").write_text("private", encoding="utf-8")
            state = _bare_state({"GROK_CONFIG_DIR": str(source)})
            projected, mounts = state._prepare_config_projection("grok", child_home)
            target = child_home / ".grok"
            self.assertEqual(projected, {"GROK_CONFIG_DIR": str(target)})
            self.assertEqual(mounts, {})
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            self.assertEqual(
                sorted(item.name for item in target.iterdir()),
                ["auth.json", "config.toml"],
            )
            for name in ("auth.json", "config.toml"):
                self.assertEqual(
                    stat.S_IMODE((target / name).stat().st_mode), 0o600
                )
            self.assertTrue(os.access(target, os.W_OK))

    def test_symlinked_seed_state_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "real-grok"
            child_home = root / "child-home"
            source.mkdir()
            child_home.mkdir()
            secret = root / "outside-secret"
            secret.write_text("{}", encoding="utf-8")
            (source / "auth.json").symlink_to(secret)
            state = _bare_state({"GROK_CONFIG_DIR": str(source)})
            with self.assertRaisesRegex(ValueError, "seed state"):
                state._prepare_config_projection("grok", child_home)

    def test_non_seed_provider_keeps_read_only_mount_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "real-codewhale"
            child_home = root / "child-home"
            source.mkdir()
            child_home.mkdir()
            state = _bare_state({"CODEWHALE_HOME": str(source)})
            projected, mounts = state._prepare_config_projection(
                "codewhale", child_home
            )
            target = child_home / ".codewhale"
            self.assertEqual(projected, {"CODEWHALE_HOME": str(target)})
            self.assertEqual(mounts, {str(target): str(source)})
            self.assertFalse(target.exists())


def _real_secret_loader():
    """Load load_secret_env.py directly; the broker's ownership gate requires a
    root-owned file (the published generation), which the mutable repo cannot
    satisfy, and that gate is orthogonal to the schema behavior under test."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "aas_exact_secret_loader_test", RUNNERS_DIR / "load_secret_env.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StartupSecretSchemaTests(unittest.TestCase):
    def test_nonconforming_compute_file_fails_closed_without_traceback(self) -> None:
        from unittest import mock

        with tempfile.TemporaryDirectory() as temporary:
            secret_file = Path(temporary) / "compute.env"
            secret_file.write_text("TOTALLY_BOGUS_KEY=x\n", encoding="utf-8")
            secret_file.chmod(0o600)
            captured = io.StringIO()
            with (
                mock.patch.object(
                    broker, "_load_module_file", return_value=_real_secret_loader()
                ),
                mock.patch.dict(
                    os.environ,
                    {broker.COMPUTE_POINTER: str(secret_file)},
                    clear=False,
                ),
                contextlib.redirect_stderr(captured),
            ):
                rc = broker.main(["--entry", "/bin/true"])
            self.assertEqual(rc, 2)
            self.assertIn("secret env rejected", captured.getvalue())
            self.assertIn("TOTALLY_BOGUS_KEY", captured.getvalue())
            self.assertNotIn("Traceback", captured.getvalue())

    def test_compute_schema_covers_every_advertised_lane(self) -> None:
        self.assertTrue(broker.COMPUTE_PROJECTION_KEYS <= broker.COMPUTE_KEYS)


# Runs in a subprocess with both TOML parsers blocked, standing in for the
# declared Python 3.10 floor on a host that never installed tomli.
_FLOOR_PROBE = """
import builtins, importlib.util, json, sys

blocked = {"tomllib", "tomli"}
real = builtins.__import__


def guard(name, *args, **kwargs):
    if name.split(".")[0] in blocked:
        raise ModuleNotFoundError("No module named %r" % name.split(".")[0])
    return real(name, *args, **kwargs)


builtins.__import__ = guard
sys.dont_write_bytecode = True

result = {}
spec = importlib.util.spec_from_file_location("arl_credential_broker", sys.argv[1])
module = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(module)
except BaseException as exc:
    print(json.dumps({"imported": False, "error": "%s: %s" % (type(exc).__name__, exc)}))
    raise SystemExit(0)
result["imported"] = True
result["verbs"] = sorted(v for v in ("main", "serve", "_load_modal_authority") if hasattr(module, v))
try:
    module._toml_parser()
except BaseException as exc:
    result["parser_error_type"] = type(exc).__name__
    result["parser_error"] = str(exc)
print(json.dumps(result))
"""


class TomlParserResolvesLateTests(unittest.TestCase):
    """The Modal authority is the only TOML the broker reads.

    Importing the parser at module scope made tomli a hard requirement for every
    broker verb on the declared Python 3.10 floor (manifest/dependencies.yaml
    pins ">=3.10"), so a host without it could not start the broker at all --
    not even for provider projections, Hetzner env files or Kaggle JSON, none of
    which are TOML. research_compute/config.py already deferred its own import
    for exactly this reason; the broker did not.
    """

    BROKER = RUNNERS_DIR / "arl_credential_broker.py"

    def _floor(self) -> dict:
        completed = subprocess.run(
            [sys.executable, "-c", _FLOOR_PROBE, str(self.BROKER)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_the_broker_imports_without_any_toml_parser(self) -> None:
        result = self._floor()
        self.assertTrue(result["imported"], result.get("error"))
        self.assertIn("main", result["verbs"])
        self.assertIn("_load_modal_authority", result["verbs"])

    def test_the_missing_parser_is_reported_with_the_way_out(self) -> None:
        result = self._floor()
        self.assertEqual("RuntimeError", result.get("parser_error_type"), result)
        message = result["parser_error"]
        self.assertIn("tomli", message)
        self.assertIn("3.10", message)
        self.assertIn("3.11", message)
        self.assertNotIn("Traceback", message)

    def test_a_host_with_a_parser_is_unaffected(self) -> None:
        parser = broker._toml_parser()
        self.assertIn(parser.__name__, {"tomllib", "tomli"})
        self.assertEqual({"a": 1}, parser.loads("a = 1"))

    def test_the_parser_is_not_bound_at_module_scope(self) -> None:
        """A module-level name would make the late resolution decorative."""
        self.assertFalse(hasattr(broker, "tomllib"), "tomllib rebound at import")
        self.assertFalse(hasattr(broker, "tomli"), "tomli rebound at import")

    def test_the_dependency_record_names_every_consumer(self) -> None:
        """The table said tomli was a Docling concern; four other lanes need it."""
        record = json.loads((REPO_ROOT / "manifest" / "system-dependencies.yaml").read_text(
            encoding="utf-8"
        ))["python_packages"]["tomli"]
        for consumer in (
            "docling",
            "modal-research-compute",
            "hetzner-research-compute",
            "kaggle-research-compute",
        ):
            self.assertIn(consumer, record["used_by"], record["used_by"])
        self.assertTrue(
            any("ARL" in entry for entry in record["used_by"]), record["used_by"]
        )
        self.assertNotIn("Docling TOML config parsing", record["requirement"])


if __name__ == "__main__":
    unittest.main()
