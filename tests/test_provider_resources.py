"""Linux behavioral tests for trusted-local provider resource enforcement.

These tests intentionally execute the production systemd/prlimit gate and the
bubblewrap containment command.  They are skipped only when the process has no
usable user systemd manager; on a configured Linux host a failed production
preflight is a test failure, not a skip.
"""

from __future__ import annotations

import errno
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path, PurePosixPath
from typing import Mapping

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "autonomous-research-loop-runtime"
)
sys.path.insert(0, str(RUNTIME_DIR))

import provider_resources as pr  # noqa: E402


_PYTHON = str(Path(sys.executable).resolve())
_MIB = 1024 * 1024
_RESOURCE_ENV = {
    "AAS_AUTOLOOP_RESOURCE_MEMORY_MIB": "1024",
    "AAS_AUTOLOOP_RESOURCE_SWAP_MIB": "0",
    "AAS_AUTOLOOP_RESOURCE_ADDRESS_SPACE_MIB": "2048",
    "AAS_AUTOLOOP_RESOURCE_CPU_SECONDS": "30",
    "AAS_AUTOLOOP_RESOURCE_CPU_QUOTA_PERCENT": "100",
    "AAS_AUTOLOOP_RESOURCE_MAX_PROCESSES": "16",
    "AAS_AUTOLOOP_RESOURCE_OPEN_FILES": "64",
    "AAS_AUTOLOOP_RESOURCE_FILE_SIZE_MIB": "2",
    "AAS_AUTOLOOP_RESOURCE_OUTPUT_MIB": "1",
}


@unittest.skipUnless(
    sys.platform.startswith("linux"),
    "trusted-local resource enforcement requires Linux",
)
class ProviderResourceLinuxBehaviorTests(unittest.TestCase):
    """Exercise kernel-backed limits through the exact production wrapper."""

    @classmethod
    def setUpClass(cls) -> None:
        missing = [
            name
            for name in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
            if not os.environ.get(name)
        ]
        if missing:
            raise unittest.SkipTest(
                "no user-manager environment: " + ", ".join(missing)
            )
        systemctl = Path("/usr/bin/systemctl")
        if not systemctl.is_file():
            raise unittest.SkipTest("systemctl is unavailable")
        manager = subprocess.run(
            [str(systemctl), "--user", "show-environment"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        if manager.returncode != 0:
            raise unittest.SkipTest("the user systemd manager is unavailable")

        try:
            pr.preflight_resource_backend(
                3,
                role="panel",
                environ={**os.environ, **_RESOURCE_ENV},
            )
        except pr.ProviderResourceError as exc:
            raise AssertionError(
                "configured trusted-local resource preflight failed"
            ) from exc

    def _source(self, overrides: Mapping[str, str] | None = None) -> dict[str, str]:
        source = {**os.environ, **_RESOURCE_ENV}
        if overrides:
            source.update({str(key): str(value) for key, value in overrides.items()})
        return source

    def _execution_environment(self) -> dict[str, str]:
        child = {
            name: os.environ[name]
            for name in ("HOME", "LANG", "LC_ALL", "LC_CTYPE", "USER")
            if os.environ.get(name)
        }
        return pr.resource_control_environment(child, environ=os.environ)

    def _wrapped_probe(
        self,
        script: str,
        *,
        cwd: Path,
        wall_time_seconds: int = 8,
        overrides: Mapping[str, str] | None = None,
        arguments: tuple[str, ...] = (),
    ) -> tuple[list[str], dict[str, int], str]:
        contained = pr.trusted_local_containment_command(
            [_PYTHON, "-I", "-S", "-c", script, *arguments],
            cwd=cwd,
        )
        return pr.resource_limited_command(
            contained,
            wall_time_seconds,
            role="panel",
            environ=self._source(overrides),
        )

    def _run_probe(
        self,
        script: str,
        *,
        cwd: Path | None = None,
        wall_time_seconds: int = 8,
        host_timeout_seconds: int | None = None,
        overrides: Mapping[str, str] | None = None,
        arguments: tuple[str, ...] = (),
    ) -> tuple[pr.BoundedProcessResult, dict[str, int], str]:
        workdir = (cwd or REPO_ROOT).resolve()
        command, limits, scope_unit = self._wrapped_probe(
            script,
            cwd=workdir,
            wall_time_seconds=wall_time_seconds,
            overrides=overrides,
            arguments=arguments,
        )
        result = pr.run_bounded_resource_process(
            command,
            env=self._execution_environment(),
            cwd=workdir,
            timeout_s=host_timeout_seconds or wall_time_seconds,
            output_limit_bytes=limits["output_max_bytes"],
            scope_unit=scope_unit,
        )
        return result, limits, scope_unit

    def assertCleanResult(self, result: pr.BoundedProcessResult) -> None:
        self.assertIsNone(result.capture_error)
        self.assertIsNone(result.cleanup_error)

    def test_output_limit_override_is_exact_and_bounded(self) -> None:
        exact = pr.provider_resource_limits(
            60,
            role="primary",
            environ=self._source(
                {
                    "AAS_AUTOLOOP_RESOURCE_FILE_SIZE_MIB": "17",
                    "AAS_AUTOLOOP_RESOURCE_OUTPUT_MIB": "16",
                }
            ),
        )
        self.assertEqual(exact["output_max_bytes"], 16 * _MIB)
        with self.assertRaises(pr.ProviderResourceError):
            pr.provider_resource_limits(
                60,
                role="primary",
                environ=self._source(
                    {
                        "AAS_AUTOLOOP_RESOURCE_FILE_SIZE_MIB": "18",
                        "AAS_AUTOLOOP_RESOURCE_OUTPUT_MIB": "17",
                    }
                ),
            )

    def _cleanup_live_scope(
        self,
        process: subprocess.Popen[bytes],
        scope_unit: str,
    ) -> None:
        scope_error = pr.cleanup_resource_scope(scope_unit)
        group_error = pr._terminate_bounded_process_group(process)
        final_scope_error = pr.cleanup_resource_scope(scope_unit)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        errors = [
            value
            for value in (scope_error, group_error, final_scope_error)
            if value is not None
        ]
        if errors:
            raise AssertionError("; ".join(errors))

    def test_live_scope_exposes_zero_swap_and_exact_cgroup_limits(self) -> None:
        command, limits, scope_unit = self._wrapped_probe(
            "import time; time.sleep(20)",
            cwd=REPO_ROOT,
            wall_time_seconds=20,
        )
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._execution_environment(),
            cwd=str(REPO_ROOT),
            start_new_session=True,
        )
        self.addCleanup(self._cleanup_live_scope, process, scope_unit)

        properties: dict[str, str] = {}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            observed = subprocess.run(
                [
                    "/usr/bin/systemctl",
                    "--user",
                    "show",
                    "--property=LoadState",
                    "--property=ActiveState",
                    "--property=ControlGroup",
                    "--property=MemoryMax",
                    "--property=MemorySwapMax",
                    "--property=TasksMax",
                    "--property=RuntimeMaxUSec",
                    scope_unit,
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(observed.returncode, 0, observed.stderr)
            properties = {
                key: value
                for line in observed.stdout.splitlines()
                if "=" in line
                for key, value in [line.split("=", 1)]
            }
            if properties.get("ActiveState") == "active":
                break
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=1)
                self.fail(
                    "resource scope exited before inspection: "
                    f"rc={process.returncode}, stdout={stdout!r}, stderr={stderr!r}"
                )
            time.sleep(0.05)
        else:
            self.fail("resource scope did not become active")

        self.assertEqual(properties.get("LoadState"), "loaded")
        self.assertEqual(int(properties["MemoryMax"]), limits["memory_max_bytes"])
        self.assertEqual(int(properties["MemorySwapMax"]), 0)
        self.assertEqual(int(properties["TasksMax"]), limits["tasks_max"])
        self.assertEqual(
            properties.get("RuntimeMaxUSec"),
            f"{limits['runtime_scope_seconds']}s",
        )

        relative = PurePosixPath(properties["ControlGroup"])
        self.assertTrue(relative.is_absolute())
        self.assertNotIn("..", relative.parts)
        cgroup_root = Path("/sys/fs/cgroup").resolve(strict=True)
        cgroup_leaf = (cgroup_root / str(relative).lstrip("/")).resolve(strict=True)
        self.assertIn(cgroup_root, cgroup_leaf.parents)
        self.assertEqual(
            int((cgroup_leaf / "memory.max").read_text(encoding="utf-8")),
            limits["memory_max_bytes"],
        )
        self.assertEqual(
            int((cgroup_leaf / "memory.swap.max").read_text(encoding="utf-8")),
            0,
        )
        self.assertEqual(
            int((cgroup_leaf / "pids.max").read_text(encoding="utf-8")),
            limits["tasks_max"],
        )
        cpu_quota, cpu_period = (
            (cgroup_leaf / "cpu.max").read_text(encoding="utf-8").split()
        )
        self.assertNotEqual(cpu_quota, "max")
        self.assertEqual(
            int(cpu_quota) * 100,
            limits["cpu_quota_percent"] * int(cpu_period),
        )

    def test_memory_max_kills_allocation_before_address_space_limit(self) -> None:
        script = """
chunks = []
for _ in range(24):
    chunks.append(bytearray(64 * 1024 * 1024))
print("allocation unexpectedly survived", flush=True)
""".strip()
        result, limits, _scope = self._run_probe(
            script,
            wall_time_seconds=12,
            overrides={"AAS_AUTOLOOP_RESOURCE_ADDRESS_SPACE_MIB": "4096"},
        )

        self.assertCleanResult(result)
        self.assertFalse(result.timed_out)
        self.assertFalse(result.oversized)
        self.assertGreater(limits["address_space_bytes"], 3 * limits["memory_max_bytes"])
        self.assertIn(result.return_code, {-signal.SIGKILL, 128 + signal.SIGKILL})
        self.assertNotIn(b"unexpectedly survived", result.stdout)

    def test_tasks_max_denies_thread_growth(self) -> None:
        script = """
import json
import threading

stop = threading.Event()
threads = []
denied = False
try:
    for _ in range(64):
        thread = threading.Thread(target=stop.wait, daemon=True)
        thread.start()
        threads.append(thread)
except RuntimeError:
    denied = True
print(json.dumps({"denied": denied, "started": len(threads)}), flush=True)
stop.set()
for thread in threads:
    thread.join()
raise SystemExit(0 if denied else 3)
""".strip()
        result, limits, _scope = self._run_probe(script)

        self.assertCleanResult(result)
        self.assertEqual(result.return_code, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertTrue(observed["denied"])
        self.assertLess(observed["started"], limits["tasks_max"])

    def test_tasks_max_denies_process_growth(self) -> None:
        script = """
import errno
import json
import subprocess

children = []
denied_errno = None
try:
    for _ in range(64):
        children.append(
            subprocess.Popen(
                ["/bin/sleep", "10"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
except OSError as exc:
    denied_errno = exc.errno
finally:
    print(
        json.dumps({"errno": denied_errno, "started": len(children)}),
        flush=True,
    )
    for child in children:
        child.kill()
    for child in children:
        child.wait()
raise SystemExit(0 if denied_errno == errno.EAGAIN else 3)
""".strip()
        result, limits, _scope = self._run_probe(script)

        self.assertCleanResult(result)
        self.assertEqual(result.return_code, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(observed["errno"], errno.EAGAIN)
        self.assertLess(observed["started"], limits["tasks_max"])

    def test_rlimit_as_denies_oversized_virtual_mapping(self) -> None:
        script = """
import json
import mmap
import resource

limit = resource.getrlimit(resource.RLIMIT_AS)
denied = False
try:
    mmap.mmap(-1, 3 * 1024 * 1024 * 1024)
except (BufferError, MemoryError, OSError):
    denied = True
print(json.dumps({"denied": denied, "limit": limit}), flush=True)
raise SystemExit(0 if denied else 3)
""".strip()
        result, limits, _scope = self._run_probe(script)

        self.assertCleanResult(result)
        self.assertEqual(result.return_code, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertTrue(observed["denied"])
        self.assertEqual(
            observed["limit"],
            [limits["address_space_bytes"], limits["address_space_bytes"]],
        )

    def test_rlimit_cpu_terminates_busy_process(self) -> None:
        started = time.monotonic()
        result, limits, _scope = self._run_probe(
            "while True: pass",
            wall_time_seconds=8,
            overrides={"AAS_AUTOLOOP_RESOURCE_CPU_SECONDS": "1"},
        )
        elapsed = time.monotonic() - started

        self.assertCleanResult(result)
        self.assertFalse(result.timed_out)
        self.assertFalse(result.oversized)
        self.assertEqual(limits["cpu_time_seconds"], 1)
        self.assertIn(
            result.return_code,
            {
                -signal.SIGXCPU,
                -signal.SIGKILL,
                128 + signal.SIGXCPU,
                128 + signal.SIGKILL,
            },
        )
        self.assertLess(elapsed, 6)

    def test_rlimit_nofile_denies_additional_opens(self) -> None:
        script = """
import errno
import json
import resource

limit = resource.getrlimit(resource.RLIMIT_NOFILE)
handles = []
denied_errno = None
try:
    for _ in range(256):
        handles.append(open("/dev/null", "rb"))
except OSError as exc:
    denied_errno = exc.errno
finally:
    for handle in handles:
        handle.close()
print(
    json.dumps({"errno": denied_errno, "limit": limit, "opened": len(handles)}),
    flush=True,
)
raise SystemExit(0 if denied_errno == errno.EMFILE else 3)
""".strip()
        result, limits, _scope = self._run_probe(script)

        self.assertCleanResult(result)
        self.assertEqual(result.return_code, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(observed["errno"], errno.EMFILE)
        self.assertEqual(
            observed["limit"],
            [limits["open_files_max"], limits["open_files_max"]],
        )
        self.assertLess(observed["opened"], limits["open_files_max"])

    def test_rlimit_fsize_stops_file_at_configured_ceiling(self) -> None:
        script = """
import errno
import json
import resource
import signal
import sys
from pathlib import Path

target = Path(sys.argv[1])
limit = resource.getrlimit(resource.RLIMIT_FSIZE)
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
denied_errno = None
try:
    with target.open("wb", buffering=0) as output:
        for _ in range(64):
            output.write(b"x" * (64 * 1024))
except OSError as exc:
    denied_errno = exc.errno
print(
    json.dumps({"errno": denied_errno, "limit": limit, "size": target.stat().st_size}),
    flush=True,
)
raise SystemExit(0 if denied_errno == errno.EFBIG else 3)
""".strip()
        with tempfile.TemporaryDirectory() as temporary:
            cwd = Path(temporary).resolve()
            target = cwd / "bounded-output.bin"
            result, limits, _scope = self._run_probe(
                script,
                cwd=cwd,
                arguments=(str(target),),
            )

            self.assertCleanResult(result)
            self.assertEqual(result.return_code, 0, result.stderr)
            observed = json.loads(result.stdout)
            self.assertEqual(observed["errno"], errno.EFBIG)
            self.assertEqual(
                observed["limit"],
                [limits["file_size_max_bytes"], limits["file_size_max_bytes"]],
            )
            self.assertEqual(observed["size"], limits["file_size_max_bytes"])
            self.assertEqual(target.stat().st_size, limits["file_size_max_bytes"])

    def test_rlimit_core_is_zero_and_abort_creates_no_core_file(self) -> None:
        script = """
import json
import os
import resource

print(json.dumps({"limit": resource.getrlimit(resource.RLIMIT_CORE)}), flush=True)
os.abort()
""".strip()
        with tempfile.TemporaryDirectory() as temporary:
            cwd = Path(temporary).resolve()
            result, limits, _scope = self._run_probe(script, cwd=cwd)

            self.assertCleanResult(result)
            self.assertFalse(result.timed_out)
            self.assertNotEqual(result.return_code, 0)
            observed = json.loads(result.stdout)
            self.assertEqual(
                observed["limit"],
                [limits["core_size_max_bytes"], limits["core_size_max_bytes"]],
            )
            self.assertEqual(list(cwd.iterdir()), [])

    def test_host_wall_timeout_cleans_runtime_limited_scope(self) -> None:
        started = time.monotonic()
        result, limits, scope_unit = self._run_probe(
            "import time; time.sleep(30)",
            wall_time_seconds=20,
            host_timeout_seconds=1,
        )
        elapsed = time.monotonic() - started

        self.assertCleanResult(result)
        self.assertTrue(result.timed_out)
        self.assertFalse(result.oversized)
        self.assertEqual(result.return_code, 124)
        self.assertLess(elapsed, 7)
        self.assertEqual(limits["runtime_scope_seconds"], 35)
        self.assertIsNone(pr.cleanup_resource_scope(scope_unit))

    def test_timeout_kills_detached_descendant_before_post_cleanup_effect(self) -> None:
        parent_script = """
import subprocess
import sys
import time

subprocess.Popen(
    [sys.executable, "-I", "-S", "-c", sys.argv[1], sys.argv[2]],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
print("descendant-started", flush=True)
time.sleep(30)
""".strip()
        child_script = """
import sys
import time
from pathlib import Path

time.sleep(2.5)
Path(sys.argv[1]).write_text("survived", encoding="utf-8")
""".strip()
        with tempfile.TemporaryDirectory() as temporary:
            cwd = Path(temporary).resolve()
            marker = cwd / "descendant-survived.txt"
            result, _limits, scope_unit = self._run_probe(
                parent_script,
                cwd=cwd,
                wall_time_seconds=10,
                host_timeout_seconds=1,
                arguments=(child_script, str(marker)),
            )

            self.assertCleanResult(result)
            self.assertTrue(result.timed_out)
            self.assertEqual(result.return_code, 124)
            self.assertIn(b"descendant-started", result.stdout)
            self.assertIsNone(pr.cleanup_resource_scope(scope_unit))
            time.sleep(3)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
