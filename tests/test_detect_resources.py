"""Offline tests for the get-available-resources preflight.

Three contracts are pinned here.  The Apple Silicon parse owes its caller one
thing: a malformed line in `system_profiler` output costs that line and nothing
else.  The CPU and memory probes owe a harder one -- the numbers they report are
the budget *this process* has, not the machine's, because a worker count sized off
hardware the run may not touch is the outcome the preflight exists to prevent.  And
the disk probe owes a failure shape its own consumers can read, since both of them
index its numeric keys unconditionally.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "get-available-resources"
    / "detect_resources.py"
)
_MISSING = object()


def _load_module():
    """Import the script with psutil stubbed: nothing here calls into it."""

    previous = sys.modules.get("psutil", _MISSING)
    if previous is _MISSING:
        sys.modules["psutil"] = types.ModuleType("psutil")
    try:
        spec = importlib.util.spec_from_file_location(
            "aas_detect_resources_under_test", MODULE_PATH
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is _MISSING:
            sys.modules.pop("psutil", None)


dr = _load_module()


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.returncode = 0


class AppleSiliconGpuParseTests(unittest.TestCase):
    BRAND = "Apple M2 Pro"

    @staticmethod
    def _profile(chipset_line: str) -> str:
        return "\n".join(
            [
                "Graphics/Displays:",
                "    Apple M2:",
                f"      {chipset_line}",
                "      Type: GPU",
                "      Bus: Built-In",
                "      Total Number of Cores: 16",
                "      Vendor: Apple (0x106b)",
            ]
        )

    def _detect(self, profile: str) -> dict | None:
        def fake_run(argv, **_kwargs):
            if argv[0] == "sysctl":
                return _Completed(f"{self.BRAND}\n")
            return _Completed(profile)

        with mock.patch.object(dr.platform, "system", return_value="Darwin"), \
                mock.patch.object(dr.subprocess, "run", side_effect=fake_run):
            return dr.detect_apple_silicon_gpu()

    def test_a_well_formed_profile_yields_both_fields(self) -> None:
        info = self._detect(self._profile("Chipset Model: Apple M2 Pro"))
        self.assertIsNotNone(info)
        self.assertEqual(info["chipset"], "Apple M2 Pro")
        self.assertEqual(info["gpu_cores"], "16")
        self.assertEqual(info["backend"], "Metal")

    def test_one_malformed_line_costs_only_that_line(self) -> None:
        """The regression proper.

        Both fields are read with the same split.  The core count used to be wrapped
        in a bare `except:` and the chipset in nothing at all, so a chipset line with
        no colon raised IndexError past the guard, into an `except Exception: pass`
        that abandoned the loop -- and the intact core-count line below it was lost
        with it.
        """

        info = self._detect(self._profile("Chipset Model Apple M2 Pro"))
        self.assertIsNotNone(info)
        self.assertNotIn("chipset", info)
        self.assertEqual(info["gpu_cores"], "16")

    def test_a_value_containing_a_colon_is_kept_whole(self) -> None:
        info = self._detect(self._profile("Chipset Model: Apple M2 Pro: 19-core"))
        self.assertEqual(info["chipset"], "Apple M2 Pro: 19-core")

    def test_a_non_apple_machine_is_not_claimed(self) -> None:
        with mock.patch.object(dr.platform, "system", return_value="Linux"):
            self.assertIsNone(dr.detect_apple_silicon_gpu())



GIB = 1024 ** 3


def _psutil(*, cores: int = 64, physical: int = 32,
            total_gb: float = 512.0, available_gb: float = 400.0):
    """A psutil that reports a big machine, so any smaller number is a real bound."""

    total, available = int(total_gb * GIB), int(available_gb * GIB)
    fake = types.SimpleNamespace()
    fake.cpu_count = lambda logical=True: cores if logical else physical
    fake.cpu_freq = lambda: None
    fake.virtual_memory = lambda: types.SimpleNamespace(
        total=total,
        available=available,
        used=total - available,
        percent=round((total - available) / total * 100, 1),
    )
    fake.swap_memory = lambda: types.SimpleNamespace(total=0, used=0)
    fake.disk_usage = lambda path: types.SimpleNamespace(
        total=1000 * GIB, free=500 * GIB, used=500 * GIB, percent=50.0
    )
    return fake


def _sysfs(files: dict):
    """A `_read_first_line` serving exactly `files`; everything else is absent."""

    return lambda path: files.get(path)


class _Budget:
    """Patch context for the three inputs a budget is computed from.

    `sched_getaffinity` is patched with `create=True` throughout: it does not exist
    on Windows or macOS, and every assertion here is about the arithmetic, which
    must hold identically from any host.  Passing `affinity=None` reproduces that
    absence -- `usable_cpu_count` reaches the attribute through `getattr(os, ...,
    None)`, so a None attribute and a missing one take the same branch.
    """

    def __init__(self, *, psutil_stub, affinity=None, sysfs=None):
        self._patches = [
            mock.patch.object(dr, "psutil", psutil_stub),
            mock.patch.object(dr, "_read_first_line", _sysfs(sysfs or {}), create=True),
            mock.patch.object(
                dr.os,
                "sched_getaffinity",
                None if affinity is None else (lambda pid: set(range(affinity))),
                create=True,
            ),
        ]

    def __enter__(self):
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in reversed(self._patches):
            patch.stop()
        return False


class CpuBudgetIsTheProcessBudgetTests(unittest.TestCase):
    """`logical_cores` is what this run may use, not what the machine has.

    `psutil.cpu_count(logical=True)` is sysconf(_SC_NPROCESSORS_ONLN).  Two
    mechanisms bound a process below it and show up in no psutil field: CPU
    affinity and the cgroup CPU quota.  The number is not cosmetic -- the compute
    router reads it straight out of the saved snapshot (`planner.local_cores` ->
    `local_self_preservation_probe`) to project a safe worker ceiling, so reporting
    the machine over-subscribes exactly the run the preflight was asked to size.
    """

    def test_an_affinity_mask_below_the_machine_is_the_budget(self) -> None:
        with _Budget(psutil_stub=_psutil(cores=64), affinity=2):
            self.assertEqual(dr.usable_cpu_count(), (2, "affinity"))
            info = dr.get_cpu_info()
        self.assertEqual(info["logical_cores"], 2)
        self.assertEqual(info["machine_logical_cores"], 64)
        self.assertEqual(info["cpu_budget_source"], "affinity")

    def test_a_cgroup_v2_quota_below_the_machine_is_the_budget(self) -> None:
        with _Budget(
            psutil_stub=_psutil(cores=64),
            affinity=64,
            sysfs={"/sys/fs/cgroup/cpu.max": "200000 100000"},
        ):
            self.assertEqual(dr.cgroup_cpu_quota(), 2.0)
            self.assertEqual(dr.usable_cpu_count(), (2, "cgroup_quota"))

    def test_a_cgroup_v1_quota_is_read_from_its_two_files(self) -> None:
        with _Budget(
            psutil_stub=_psutil(cores=64),
            affinity=64,
            sysfs={
                "/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "400000",
                "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000",
            },
        ):
            self.assertEqual(dr.cgroup_cpu_quota(), 4.0)
            self.assertEqual(dr.usable_cpu_count(), (4, "cgroup_quota"))

    def test_the_tighter_of_the_two_bounds_wins(self) -> None:
        for affinity, quota, expected in ((8, "200000 100000", (2, "cgroup_quota")),
                                          (2, "800000 100000", (2, "affinity"))):
            with self.subTest(affinity=affinity, quota=quota):
                with _Budget(
                    psutil_stub=_psutil(cores=64),
                    affinity=affinity,
                    sysfs={"/sys/fs/cgroup/cpu.max": quota},
                ):
                    self.assertEqual(dr.usable_cpu_count(), expected)

    def test_an_uncapped_host_still_reports_every_online_core(self) -> None:
        """The control: neither mechanism in force must not shrink the answer."""

        for label, sysfs in (
            ("v2 says max", {"/sys/fs/cgroup/cpu.max": "max 100000"}),
            ("v1 says -1", {"/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "-1",
                            "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000"}),
            ("no cgroup files at all", {}),
        ):
            with self.subTest(label):
                with _Budget(psutil_stub=_psutil(cores=64), affinity=64, sysfs=sysfs):
                    self.assertIsNone(dr.cgroup_cpu_quota())
                    self.assertEqual(dr.usable_cpu_count(), (64, "online_cores"))

    def test_a_host_without_affinity_reporting_falls_back_to_the_machine(self) -> None:
        with _Budget(psutil_stub=_psutil(cores=64), affinity=None):
            self.assertEqual(dr.usable_cpu_count(), (64, "online_cores"))

    def test_an_unreadable_affinity_call_does_not_sink_the_probe(self) -> None:
        def refuse(pid):
            raise OSError("not permitted")

        with _Budget(psutil_stub=_psutil(cores=64), affinity=64):
            with mock.patch.object(dr.os, "sched_getaffinity", refuse, create=True):
                self.assertEqual(dr.usable_cpu_count(), (64, "online_cores"))

    def test_a_sub_core_quota_is_rounded_up_to_one_worker(self) -> None:
        """`--cpus=0.5` is a real cap; zero workers is not a runnable plan."""

        with _Budget(
            psutil_stub=_psutil(cores=64),
            affinity=64,
            sysfs={"/sys/fs/cgroup/cpu.max": "50000 100000"},
        ):
            self.assertEqual(dr.usable_cpu_count(), (1, "cgroup_quota"))

    def test_the_worker_count_follows_the_budget_not_the_machine(self) -> None:
        """The end-to-end claim: a 64-core host, pinned to one core, plans for one."""

        with _Budget(psutil_stub=_psutil(cores=64), affinity=1):
            resources = {
                "cpu": dr.get_cpu_info(),
                "memory": dr.get_memory_info(),
                "disk": dr.get_disk_info("/"),
                "gpu": {"total_gpus": 0, "available_backends": []},
            }
            parallel = dr.generate_recommendations(resources)["parallel_processing"]
        self.assertEqual(parallel["strategy"], "sequential")
        self.assertEqual(parallel["suggested_workers"], 1)

    def test_the_sequential_branch_answers_the_field_callers_size_pools_from(
        self,
    ) -> None:
        """`suggested_workers` absent reads as unknown; sequential means one."""

        recommendations = dr.generate_recommendations({
            "cpu": {"logical_cores": 2},
            "memory": {"available_gb": 8, "total_gb": 16},
            "disk": {"available_gb": 200},
            "gpu": {"total_gpus": 0, "available_backends": []},
        })
        self.assertEqual(
            recommendations["parallel_processing"]["suggested_workers"], 1
        )


class MemoryBudgetIsTheProcessBudgetTests(unittest.TestCase):
    """`total_gb` is the cap this process runs under when a cgroup sets one.

    /proc/meminfo reports the machine even inside a container that will be
    OOM-killed far below it, so a memory strategy chosen against it is chosen
    against memory the run cannot have.
    """

    def test_a_cgroup_v2_cap_below_machine_ram_is_the_budget(self) -> None:
        with _Budget(
            psutil_stub=_psutil(total_gb=512.0, available_gb=400.0),
            affinity=8,
            sysfs={
                "/sys/fs/cgroup/memory.max": str(2 * GIB),
                "/sys/fs/cgroup/memory.current": str(GIB // 2),
            },
        ):
            info = dr.get_memory_info()
        self.assertEqual(info["total_gb"], 2.0)
        self.assertEqual(info["available_gb"], 1.5)
        self.assertEqual(info["machine_total_gb"], 512.0)
        self.assertEqual(info["memory_budget_source"], "cgroup_limit")

    def test_a_cgroup_v1_cap_is_read_from_its_own_files(self) -> None:
        with _Budget(
            psutil_stub=_psutil(total_gb=512.0),
            affinity=8,
            sysfs={
                "/sys/fs/cgroup/memory/memory.limit_in_bytes": str(4 * GIB),
                "/sys/fs/cgroup/memory/memory.usage_in_bytes": str(GIB),
            },
        ):
            info = dr.get_memory_info()
        self.assertEqual((info["total_gb"], info["available_gb"]), (4.0, 3.0))

    def test_the_v1_unlimited_sentinel_is_not_read_as_a_cap(self) -> None:
        """v1 has no `max` keyword: it writes a number near the top of the address
        space, which is above machine RAM and so must not shrink anything."""

        with _Budget(
            psutil_stub=_psutil(total_gb=512.0, available_gb=400.0),
            affinity=8,
            sysfs={"/sys/fs/cgroup/memory/memory.limit_in_bytes": "9223372036854771712"},
        ):
            info = dr.get_memory_info()
        self.assertEqual(info["total_gb"], 512.0)
        self.assertEqual(info["memory_budget_source"], "machine")

    def test_an_uncapped_host_reports_the_machine_unchanged(self) -> None:
        for label, sysfs in (("v2 says max", {"/sys/fs/cgroup/memory.max": "max"}),
                             ("no cgroup files", {})):
            with self.subTest(label):
                with _Budget(
                    psutil_stub=_psutil(total_gb=512.0, available_gb=400.0),
                    affinity=8,
                    sysfs=sysfs,
                ):
                    info = dr.get_memory_info()
                self.assertEqual(info["total_gb"], 512.0)
                self.assertEqual(info["available_gb"], 400.0)
                self.assertEqual(info["memory_budget_source"], "machine")

    def test_an_unreadable_usage_counter_still_yields_a_capped_total(self) -> None:
        with _Budget(
            psutil_stub=_psutil(total_gb=512.0, available_gb=400.0),
            affinity=8,
            sysfs={"/sys/fs/cgroup/memory.max": str(2 * GIB)},
        ):
            info = dr.get_memory_info()
        self.assertEqual(info["total_gb"], 2.0)
        self.assertEqual(info["memory_budget_source"], "cgroup_limit")

    def test_the_memory_strategy_follows_the_cap(self) -> None:
        """The end-to-end claim: half a terabyte of RAM, capped at 2 GB, plans for 2."""

        with _Budget(
            psutil_stub=_psutil(total_gb=512.0, available_gb=400.0),
            affinity=8,
            sysfs={
                "/sys/fs/cgroup/memory.max": str(2 * GIB),
                "/sys/fs/cgroup/memory.current": str(GIB),
            },
        ):
            resources = {
                "cpu": dr.get_cpu_info(),
                "memory": dr.get_memory_info(),
                "disk": dr.get_disk_info("/"),
                "gpu": {"total_gpus": 0, "available_backends": []},
            }
            strategy = dr.generate_recommendations(resources)["memory_strategy"]
        self.assertEqual(strategy["strategy"], "memory_constrained")


class DiskProbeFailureStillYieldsAPreflightTests(unittest.TestCase):
    """A handled disk failure must not cost the CPU, memory and GPU sections.

    `generate_recommendations` and `main` both index `available_gb` and `total_gb`
    without checking, so an error payload that omits them raises KeyError out of
    `detect_all_resources` and the whole preflight is lost -- the one moment the
    caller most needs the sections that did succeed.
    """

    def test_the_failure_shape_carries_the_keys_both_consumers_read(self) -> None:
        with mock.patch.object(dr, "psutil", _psutil()):
            with mock.patch.object(
                dr.psutil, "disk_usage", mock.Mock(side_effect=OSError("no mount"))
            ):
                probe = dr.get_disk_info("/nowhere")
        self.assertIn("no mount", probe["error"])
        for key in ("total_gb", "available_gb", "used_gb", "percent_used"):
            self.assertIsNone(probe[key], key)

    def test_a_deleted_working_directory_is_reported_not_raised(self) -> None:
        """`os.getcwd()` raises FileNotFoundError once the directory is gone, so it
        belongs inside the handler rather than in front of it."""

        with mock.patch.object(dr, "psutil", _psutil()):
            with mock.patch.object(
                dr.os, "getcwd", mock.Mock(side_effect=FileNotFoundError(2, "gone"))
            ):
                probe = dr.get_disk_info()
        self.assertIn("gone", probe["error"])
        self.assertIsNone(probe["available_gb"])

    def test_the_recommendations_survive_and_say_the_probe_failed(self) -> None:
        recommendations = dr.generate_recommendations({
            "cpu": {"logical_cores": 8},
            "memory": {"available_gb": 32, "total_gb": 64},
            "disk": {"path": "/nowhere", "total_gb": None, "available_gb": None,
                     "used_gb": None, "percent_used": None, "error": "no mount"},
            "gpu": {"total_gpus": 0, "available_backends": []},
        })
        self.assertEqual(recommendations["large_data_handling"]["strategy"], "unknown")
        self.assertEqual(
            recommendations["parallel_processing"]["strategy"], "high_parallelism"
        )

    def test_a_working_probe_is_untouched(self) -> None:
        """The control: the success shape and its recommendation are unchanged."""

        with mock.patch.object(dr, "psutil", _psutil()):
            probe = dr.get_disk_info("/")
        self.assertEqual((probe["total_gb"], probe["available_gb"]), (1000.0, 500.0))
        self.assertNotIn("error", probe)
        recommendations = dr.generate_recommendations({
            "cpu": {"logical_cores": 8},
            "memory": {"available_gb": 32, "total_gb": 64},
            "disk": probe,
            "gpu": {"total_gpus": 0, "available_backends": []},
        })
        self.assertEqual(
            recommendations["large_data_handling"]["strategy"], "disk_abundant"
        )


if __name__ == "__main__":
    unittest.main()
