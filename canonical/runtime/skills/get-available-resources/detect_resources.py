#!/usr/bin/env python3
"""
System Resource Detection Script

Detects available compute resources including CPU, GPU, memory, and disk space.
Outputs a JSON file that Claude Code can use to make informed decisions about
computational approaches (e.g., whether to use Dask, Zarr, Joblib, etc.).

Supports: macOS, Linux, Windows
GPU Detection: NVIDIA (CUDA), AMD (ROCm), Apple Silicon (Metal)
"""

import datetime
import json
import os
import platform
import psutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional


def _read_first_line(path: str) -> Optional[str]:
    """First line of a sysfs file, or None when it is absent or unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.readline().strip()
    except OSError:
        return None


def _quota_cores(quota_text: Optional[str], period_text: Optional[str]) -> Optional[float]:
    """Whole cores for a quota/period pair, or None for "no cap" and unreadable.

    Both cgroup versions spell "unlimited" differently: v2 writes the word `max`,
    v1 writes `-1`. Either way the answer is the same -- there is no cap, so the
    caller should keep whatever budget it already had.
    """
    if not quota_text or quota_text == "max":
        return None
    try:
        quota = int(quota_text)
        period = int(period_text) if period_text else 100000
    except ValueError:
        return None
    if quota <= 0 or period <= 0:
        return None
    return quota / period


def cgroup_cpu_quota() -> Optional[float]:
    """The cgroup CPU quota in whole cores, or None when there is no cap.

    A container started with `--cpus=2` on a 64-core host still reports 64 online
    cores to every psutil field; the cap exists only here. v2 keeps it in one file
    as "<quota> <period>", v1 splits it across two.
    """
    v2 = _read_first_line("/sys/fs/cgroup/cpu.max")
    if v2 is not None:
        quota_text, _, period_text = v2.partition(" ")
        return _quota_cores(quota_text, period_text)
    return _quota_cores(
        _read_first_line("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"),
        _read_first_line("/sys/fs/cgroup/cpu/cpu.cfs_period_us"),
    )


def usable_cpu_count() -> tuple:
    """How many cores this process may actually use, and where that came from.

    `psutil.cpu_count(logical=True)` is the machine's online core count. Two
    mechanisms bound a process below it and appear in no psutil field: CPU
    affinity -- taskset, a cpuset, a scheduler policy -- and the cgroup CPU quota.
    Sizing a worker pool off the machine count over-subscribes whichever one is in
    force, which is the outcome this preflight exists to prevent. The machine
    count is still reported, under `machine_logical_cores`.
    """
    online = psutil.cpu_count(logical=True) or os.cpu_count() or 1
    budget, source = online, "online_cores"

    get_affinity = getattr(os, "sched_getaffinity", None)
    if get_affinity is not None:
        try:
            affinity = len(get_affinity(0))
        except OSError:
            affinity = 0
        if affinity and affinity < budget:
            budget, source = affinity, "affinity"

    quota = cgroup_cpu_quota()
    if quota is not None:
        capped = max(1, int(quota))
        if capped < budget:
            budget, source = capped, "cgroup_quota"

    return budget, source


def cgroup_memory_limit_bytes() -> Optional[int]:
    """The cgroup memory cap in bytes, or None when there is no cap.

    v2 writes the word `max` when uncapped. v1 has no sentinel word: it writes a
    number near the top of the address space, so anything at or above the
    machine's own RAM is read here as "no cap" rather than as a limit.
    """
    v2 = _read_first_line("/sys/fs/cgroup/memory.max")
    v1 = None
    if v2 is None:
        v1 = _read_first_line("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    text = v2 if v2 is not None else v1
    if not text or text == "max":
        return None
    try:
        limit = int(text)
    except ValueError:
        return None
    return limit if limit > 0 else None


def cgroup_memory_usage_bytes() -> Optional[int]:
    """Bytes charged to this cgroup, or None when the counter is unreadable."""
    text = _read_first_line("/sys/fs/cgroup/memory.current")
    if text is None:
        text = _read_first_line("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    try:
        return int(text) if text else None
    except ValueError:
        return None


def get_cpu_info() -> Dict[str, Any]:
    """Detect CPU information, bounded by affinity and cgroup quota."""
    usable, budget_source = usable_cpu_count()
    cpu_info = {
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": usable,
        "machine_logical_cores": psutil.cpu_count(logical=True),
        "cpu_budget_source": budget_source,
        "max_frequency_mhz": None,
        "architecture": platform.machine(),
        "processor": platform.processor(),
    }

    # Get CPU frequency if available
    try:
        freq = psutil.cpu_freq()
        if freq:
            cpu_info["max_frequency_mhz"] = freq.max
            cpu_info["current_frequency_mhz"] = freq.current
    except Exception:
        pass

    return cpu_info


def get_memory_info() -> Dict[str, Any]:
    """Detect memory information, bounded by the cgroup limit when one applies.

    `psutil.virtual_memory()` reads /proc/meminfo, which is the machine's memory
    even inside a container that will be OOM-killed well below it. When a cgroup
    caps this process, that cap is what a memory strategy has to be chosen
    against; the machine figure is still reported, under `machine_total_gb`.
    """
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    info = {
        "total_gb": round(mem.total / (1024**3), 2),
        "available_gb": round(mem.available / (1024**3), 2),
        "used_gb": round(mem.used / (1024**3), 2),
        "percent_used": mem.percent,
        "machine_total_gb": round(mem.total / (1024**3), 2),
        "memory_budget_source": "machine",
        "swap_total_gb": round(swap.total / (1024**3), 2),
        "swap_available_gb": round((swap.total - swap.used) / (1024**3), 2),
    }

    limit = cgroup_memory_limit_bytes()
    if limit is not None and limit < mem.total:
        used = cgroup_memory_usage_bytes()
        if used is None:
            used = min(mem.used, limit)
        used = min(used, limit)
        info.update({
            "total_gb": round(limit / (1024**3), 2),
            "available_gb": round((limit - used) / (1024**3), 2),
            "used_gb": round(used / (1024**3), 2),
            "percent_used": round(used / limit * 100, 1),
            "memory_budget_source": "cgroup_limit",
        })

    return info


def get_disk_info(path: str = None) -> Dict[str, Any]:
    """Detect disk space information for working directory or specified path.

    The failure shape carries the same numeric keys as the success shape, set to
    None. Both consumers -- `generate_recommendations` and `main` -- index
    `available_gb` and `total_gb` unconditionally, so a payload that omits them
    turns a handled probe failure into a KeyError that loses the entire
    preflight, CPU and memory and GPU sections included. `os.getcwd()` sits
    inside the try for the same reason: it raises FileNotFoundError when the
    working directory has been removed underneath the process.
    """
    try:
        target = os.getcwd() if path is None else path
        disk = psutil.disk_usage(target)
        return {
            "path": target,
            "total_gb": round(disk.total / (1024**3), 2),
            "available_gb": round(disk.free / (1024**3), 2),
            "used_gb": round(disk.used / (1024**3), 2),
            "percent_used": disk.percent,
        }
    except Exception as e:
        return {
            "path": path,
            "total_gb": None,
            "available_gb": None,
            "used_gb": None,
            "percent_used": None,
            "error": str(e),
        }


def _optional_number(text: str, cast):
    """A numeric nvidia-smi field, or None when the driver could not report it.

    nvidia-smi prints `[N/A]` for any field it cannot supply for a given GPU or
    driver. The conversions used to sit unguarded inside the parse loop, so one
    such field raised out of the loop into a bare `except ...: pass` and every
    GPU after it was discarded -- and when it landed on the first line, a GPU host
    reported none at all. The GPU is still there; only that field is unknown.
    """
    try:
        return cast(text)
    except (TypeError, ValueError):
        return None


def detect_nvidia_gpus() -> List[Dict[str, Any]]:
    """Detect NVIDIA GPUs using nvidia-smi."""
    gpus = []

    try:
        # Try to run nvidia-smi
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.free,driver_version,compute_cap",
             "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5
        )

        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                parts = [p.strip() for p in line.split(',')]
                if len(parts) < 6:
                    continue
                gpus.append({
                    "index": _optional_number(parts[0], int),
                    "name": parts[1],
                    "memory_total_mb": _optional_number(parts[2], float),
                    "memory_free_mb": _optional_number(parts[3], float),
                    "driver_version": parts[4],
                    "compute_capability": parts[5],
                    "type": "NVIDIA",
                    "backend": "CUDA"
                })
    except (subprocess.SubprocessError, OSError):
        # nvidia-smi absent, or it hung past the timeout. Either way this host has
        # no CUDA to report and nothing went wrong; anything else that raises is
        # reported by `get_gpu_info` rather than read as "no NVIDIA GPU".
        pass

    return gpus


def detect_amd_gpus() -> List[Dict[str, Any]]:
    """Detect AMD GPUs using rocm-smi."""
    gpus = []

    try:
        # Try to run rocm-smi
        result = subprocess.run(
            ["rocm-smi", "--showid", "--showmeminfo", "vram"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5
        )

        if result.returncode == 0:
            # Parse rocm-smi output (basic parsing, may need refinement)
            lines = result.stdout.strip().split('\n')
            gpu_index = 0
            for line in lines:
                if 'GPU' in line and 'DID' in line:
                    gpus.append({
                        "index": gpu_index,
                        "name": "AMD GPU",
                        "type": "AMD",
                        "backend": "ROCm",
                        "info": line.strip()
                    })
                    gpu_index += 1
    except (subprocess.SubprocessError, OSError):
        # rocm-smi absent or unresponsive; see the note in detect_nvidia_gpus.
        pass

    return gpus


def detect_apple_silicon_gpu() -> Optional[Dict[str, Any]]:
    """Detect Apple Silicon GPU (M1/M2/M3/etc.)."""
    if platform.system() != "Darwin":
        return None

    try:
        # Check if running on Apple Silicon
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5
        )

        cpu_brand = result.stdout.strip()

        # Check for Apple Silicon (M1, M2, M3, etc.)
        if "Apple" in cpu_brand and any(chip in cpu_brand for chip in ["M1", "M2", "M3", "M4"]):
            # Get GPU core count if possible
            gpu_info = {
                "name": cpu_brand,
                "type": "Apple Silicon",
                "backend": "Metal",
                "unified_memory": True,  # Apple Silicon uses unified memory
            }

            # Try to get GPU core information
            try:
                result = subprocess.run(
                    ["system_profiler", "SPDisplaysDataType"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10
                )

                # Parse GPU core info from system_profiler.  Both fields are the
                # same split, so they get the same guard: a line carrying the field
                # name without a colon used to raise IndexError out of the chipset
                # branch into the `except Exception: pass` below, which abandoned the
                # loop and lost every field after it -- including a core count whose
                # own line was intact.  Skipping the malformed line reads the rest.
                for line in result.stdout.split('\n'):
                    if ':' not in line:
                        continue
                    value = line.split(':', 1)[1].strip()
                    if 'Chipset Model' in line:
                        gpu_info["chipset"] = value
                    elif 'Total Number of Cores' in line:
                        gpu_info["gpu_cores"] = value
            except Exception:
                pass

            return gpu_info
    except Exception:
        pass

    return None


def get_gpu_info() -> Dict[str, Any]:
    """Detect all available GPUs.

    A probe that fails unexpectedly is named under `probe_errors` instead of
    reading as an absent GPU. "The NVIDIA probe broke" and "there is no NVIDIA
    GPU" route work differently, and a preflight that cannot tell them apart
    reports the second with the confidence of the first.
    """
    probe_errors: Dict[str, str] = {}

    def probe(name: str, detect, empty):
        """Run one detector; its own expected failures are handled inside it."""
        try:
            return detect()
        except Exception as exc:
            probe_errors[name] = f"{type(exc).__name__}: {exc}"
            return empty

    gpu_info = {
        "nvidia_gpus": probe("nvidia", detect_nvidia_gpus, []),
        "amd_gpus": probe("amd", detect_amd_gpus, []),
        "apple_silicon": probe("apple_silicon", detect_apple_silicon_gpu, None),
        "total_gpus": 0,
        "available_backends": [],
        "probe_errors": probe_errors,
    }

    # Count total GPUs and available backends
    if gpu_info["nvidia_gpus"]:
        gpu_info["total_gpus"] += len(gpu_info["nvidia_gpus"])
        gpu_info["available_backends"].append("CUDA")

    if gpu_info["amd_gpus"]:
        gpu_info["total_gpus"] += len(gpu_info["amd_gpus"])
        gpu_info["available_backends"].append("ROCm")

    if gpu_info["apple_silicon"]:
        gpu_info["total_gpus"] += 1
        gpu_info["available_backends"].append("Metal")

    return gpu_info


def get_os_info() -> Dict[str, Any]:
    """Get operating system information."""
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
    }


def detect_all_resources(output_path: str = None) -> Dict[str, Any]:
    """
    Detect all system resources and save to JSON.

    Args:
        output_path: Optional path to save JSON. Defaults to .codex_resources.json in cwd.

    Returns:
        Dictionary containing all resource information.
    """
    if output_path is None:
        output_path = os.path.join(os.getcwd(), ".codex_resources.json")

    resources = {
        "timestamp": datetime.datetime.now().isoformat(),
        "os": get_os_info(),
        "cpu": get_cpu_info(),
        "memory": get_memory_info(),
        "disk": get_disk_info(),
        "gpu": get_gpu_info(),
    }

    # Add computational recommendations
    resources["recommendations"] = generate_recommendations(resources)

    # Save to JSON file
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(resources, f, indent=2)

    return resources


def generate_recommendations(resources: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate computational approach recommendations based on available resources.
    """
    recommendations = {
        "parallel_processing": {},
        "memory_strategy": {},
        "gpu_acceleration": {},
        "large_data_handling": {}
    }

    # CPU recommendations
    cpu_cores = resources["cpu"]["logical_cores"]
    if cpu_cores >= 8:
        recommendations["parallel_processing"]["strategy"] = "high_parallelism"
        recommendations["parallel_processing"]["suggested_workers"] = max(cpu_cores - 2, 1)
        recommendations["parallel_processing"]["libraries"] = ["joblib", "multiprocessing", "dask"]
    elif cpu_cores >= 4:
        recommendations["parallel_processing"]["strategy"] = "moderate_parallelism"
        recommendations["parallel_processing"]["suggested_workers"] = max(cpu_cores - 1, 1)
        recommendations["parallel_processing"]["libraries"] = ["joblib", "multiprocessing"]
    else:
        recommendations["parallel_processing"]["strategy"] = "sequential"
        # `suggested_workers` is the field a caller sizes a pool from, so this
        # branch has to answer it too: absent reads as "unknown", and sequential
        # is not unknown -- it is one worker.
        recommendations["parallel_processing"]["suggested_workers"] = 1
        recommendations["parallel_processing"]["libraries"] = []
        recommendations["parallel_processing"]["note"] = "Limited cores, prefer sequential processing"

    # Memory recommendations
    available_memory_gb = resources["memory"]["available_gb"]
    total_memory_gb = resources["memory"]["total_gb"]

    if available_memory_gb < 4:
        recommendations["memory_strategy"]["strategy"] = "memory_constrained"
        recommendations["memory_strategy"]["libraries"] = ["zarr", "dask", "h5py"]
        recommendations["memory_strategy"]["note"] = "Use out-of-core processing for large datasets"
    elif available_memory_gb < 16:
        recommendations["memory_strategy"]["strategy"] = "moderate_memory"
        recommendations["memory_strategy"]["libraries"] = ["dask", "zarr"]
        recommendations["memory_strategy"]["note"] = "Consider chunking for datasets > 2GB"
    else:
        recommendations["memory_strategy"]["strategy"] = "memory_abundant"
        recommendations["memory_strategy"]["note"] = "Can load most datasets into memory"

    # GPU recommendations
    gpu_info = resources["gpu"]
    if gpu_info["total_gpus"] > 0:
        recommendations["gpu_acceleration"]["available"] = True
        recommendations["gpu_acceleration"]["backends"] = gpu_info["available_backends"]

        if "CUDA" in gpu_info["available_backends"]:
            recommendations["gpu_acceleration"]["suggested_libraries"] = [
                "pytorch", "tensorflow", "jax", "cupy", "rapids"
            ]
        elif "Metal" in gpu_info["available_backends"]:
            recommendations["gpu_acceleration"]["suggested_libraries"] = [
                "pytorch-mps", "tensorflow-metal", "jax-metal"
            ]
        elif "ROCm" in gpu_info["available_backends"]:
            recommendations["gpu_acceleration"]["suggested_libraries"] = [
                "pytorch-rocm", "tensorflow-rocm"
            ]
    else:
        recommendations["gpu_acceleration"]["available"] = False
        recommendations["gpu_acceleration"]["note"] = "No GPU detected, use CPU-based libraries"

    # Large data handling recommendations
    disk_available_gb = resources["disk"].get("available_gb")
    if disk_available_gb is None:
        recommendations["large_data_handling"]["strategy"] = "unknown"
        recommendations["large_data_handling"]["note"] = (
            "Disk space could not be measured; size intermediate files conservatively"
        )
    elif disk_available_gb < 10:
        recommendations["large_data_handling"]["strategy"] = "disk_constrained"
        recommendations["large_data_handling"]["note"] = "Limited disk space, use streaming or compression"
    elif disk_available_gb < 100:
        recommendations["large_data_handling"]["strategy"] = "moderate_disk"
        recommendations["large_data_handling"]["libraries"] = ["zarr", "h5py", "parquet"]
    else:
        recommendations["large_data_handling"]["strategy"] = "disk_abundant"
        recommendations["large_data_handling"]["note"] = "Sufficient space for large intermediate files"

    return recommendations


def main():
    """Main entry point for CLI usage."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Detect system resources for scientific computing"
    )
    parser.add_argument(
        "-o", "--output",
        default=".codex_resources.json",
        help="Output JSON file path (default: .codex_resources.json)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print resources to stdout"
    )

    args = parser.parse_args()

    print("Detecting system resources...")
    resources = detect_all_resources(args.output)

    print(f"Resources detected and saved to: {args.output}")

    if args.verbose:
        print("\n" + "="*60)
        print(json.dumps(resources, indent=2))
        print("="*60)

    # Print summary
    print("\nResource Summary:")
    print(f"  OS: {resources['os']['system']} {resources['os']['release']}")
    cpu = resources['cpu']
    cpu_limit = "" if cpu['cpu_budget_source'] == "online_cores" else (
        f", limited by {cpu['cpu_budget_source']} from {cpu['machine_logical_cores']}"
    )
    print(f"  CPU: {cpu['logical_cores']} cores ({cpu['physical_cores']} physical){cpu_limit}")
    memory = resources['memory']
    mem_limit = "" if memory['memory_budget_source'] == "machine" else (
        f", limited by {memory['memory_budget_source']} from {memory['machine_total_gb']} GB"
    )
    print(f"  Memory: {memory['total_gb']} GB total, {memory['available_gb']} GB available{mem_limit}")
    disk = resources['disk']
    if disk.get("error"):
        print(f"  Disk: unavailable ({disk['error']})")
    else:
        print(f"  Disk: {disk['total_gb']} GB total, {disk['available_gb']} GB available")

    if resources['gpu']['total_gpus'] > 0:
        print(f"  GPU: {resources['gpu']['total_gpus']} detected ({', '.join(resources['gpu']['available_backends'])})")
    else:
        print("  GPU: None detected")

    print("\nRecommendations:")
    recs = resources['recommendations']
    print(f"  Parallel Processing: {recs['parallel_processing'].get('strategy', 'N/A')}")
    print(f"  Memory Strategy: {recs['memory_strategy'].get('strategy', 'N/A')}")
    print(f"  GPU Acceleration: {'Available' if recs['gpu_acceleration'].get('available') else 'Not Available'}")


if __name__ == "__main__":
    main()
