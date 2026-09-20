"""Kaggle Kernels lifecycle driver for the research-compute Kaggle lane.

This is the `kaggle` kernel-lifecycle CLI referenced by the kaggle-research-compute skill.
Kaggle has no SSH and no persistent server: you push a script + kernel-metadata.json as a
kernel, poll its status, and download its output. A kernel is capped at a 12h session, so a
job that needs more wall time spans MULTIPLE kernel runs -- the `run` verb is the multi-run
resume loop that pushes a chunk-batch across up to ~5 concurrent kernels, polls them, fetches
their checkpoints, and re-pushes the remaining work with the checkpoints re-attached until the
job is DONE (bounded by max_runs).

Planning verbs (bootstrap, doctor, preflight) are free and never push a kernel. Lifecycle
verbs (push, status, wait, fetch, run) submit real kernels and require a guarded
KAGGLE_API_TOKEN environment projection plus an explicit confirm.

Auth uses Kaggle's current "API Tokens (Recommended)" single token, NOT the legacy
KAGGLE_USERNAME + KAGGLE_KEY pair and NOT a kaggle.json. bootstrap validates/primes via
kagglehub (kagglehub.whoami() proves the token is valid and yields the authenticated username
used to address kernels/datasets); the Kaggle CLI module (>=2.2.4,<3) then authenticates
kernel push/status/output with the same token under the selected Python 3.11+.

Guardrails:
  * The Kaggle API token is read from KAGGLE_API_TOKEN and injected
    into the `kaggle` subprocess env, NEVER on argv (/proc/<pid>/cmdline is world-readable),
    NEVER logged, NEVER written to a legacy kaggle.json. A redaction filter covers all surfaced
    output.
  * GPU kernels pass through a fail-closed weekly GPU-hour gate (kaggle_backend.gpu_budget_gate)
    before the first push; CPU kernels are free and quota-free.
  * The concurrency cap (~5) bounds how many kernels a single round fans out; max_runs bounds
    the resume loop so a looping agent cannot push kernels forever.
  * Kernels auto-stop at the 12h session cap and cost nothing, so there is NO reaper and NO
    teardown -- Kaggle is materially lower-risk than a paid rented-server lane.

Offline safety: every external command goes through the module-level COMMAND_RUNNER hook,
which tests replace so no kernel is ever pushed. `--dry-run` prints the exact planned `kaggle`
commands with nothing submitted. ToS: the build and its tests make NO live Kaggle calls.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from research_compute import kaggle_backend
from research_compute.config import default_config_path, load_config, workspace_root

MANAGED_BY = "ai-agents-skills"
KERNEL_WORKDIR = "/kaggle/working"
# A kernel's cumulative resume surface fetched back here; each completed work unit lands as a
# checkpoint file so a re-pushed kernel can skip it (resume).
DEFAULT_CHECKPOINT_GLOB = "unit-*.json"
MAX_FETCH_FILES = 10_000
MAX_FETCH_BYTES = 1024 * 1024 * 1024
MAX_BUNDLE_FILES = 256
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
SAFE_OUTPUT_PATTERN = r"^out/(?:unit-[0-9]{4,8}\.json|result\.json)\Z"
SAFE_OUTPUT_BASENAME_PATTERN = r"^(?:unit-[0-9]{4,8}\.json|result\.json)\Z"
DENIED_UPLOAD_NAMES = frozenset({".env", "kaggle.json"})
DENIED_UPLOAD_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx"})


class KaggleDriverError(RuntimeError):
    pass


# --- API token + redaction (env-first, never argv, never logged) --------------
#
# The new Kaggle API token is read from the guarded KAGGLE_API_TOKEN environment projection
# (never the legacy KAGGLE_USERNAME + KAGGLE_KEY pair, a pathname-read token file, or a
# kaggle.json). Resolution + presence live in
# kaggle_backend so the routing probe and this driver agree. The authenticated username needed
# to address kernels/datasets (owner/slug) comes from kagglehub.whoami() at run time, not an env
# var.

def _token() -> str | None:
    return kaggle_backend.read_token()


def token_present() -> bool:
    return kaggle_backend.token_present()


def _redact(text: str | None) -> str:
    """Scrub the API token from any surfaced text."""
    text = text or ""
    token = _token()
    if token:
        text = text.replace(token, "<REDACTED_KAGGLE_TOKEN>")
    return text


# --- authenticated username via kagglehub (mockable through the backend hook) --

def _whoami(config: Any | None) -> dict[str, Any]:
    """Validate the token and return {usable, username, reason} via the mockable kagglehub hook."""
    return kaggle_backend.KAGGLEHUB_VALIDATE(config)


def _resolve_username(config: Any | None, *, required: bool) -> str | None:
    """The authenticated Kaggle username (the kernel/dataset owner), obtained by validating the
    token with kagglehub. Returns None (for dry-run placeholders) when the token is absent or
    invalid unless `required`, in which case it raises -- the real lifecycle paths require it."""
    if not token_present():
        if required:
            raise KaggleDriverError("KAGGLE_API_TOKEN is not set; refusing to run a Kaggle command")
        return None
    result = _whoami(config)
    username = result.get("username")
    if not result.get("usable") or not username:
        if required:
            raise KaggleDriverError(
                f"kagglehub could not validate the Kaggle API token "
                f"({result.get('reason', 'unknown')}); refusing to run")
        return None
    return str(username)


# --- command runner (single mockable hook for the kaggle CLI) -----------------

def _default_command_runner(argv: list[str], *, env: dict[str, str], timeout: float) -> dict[str, Any]:  # pragma: no cover - real subprocess path is never exercised offline
    import subprocess

    proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)
    return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


# Tests replace this to guarantee no external command ever runs offline.
COMMAND_RUNNER: Callable[..., dict[str, Any]] = _default_command_runner


def _run(argv: list[str], *, timeout: float = 120.0, needs_creds: bool = True,
         check: bool = True) -> dict[str, Any]:
    """Run an external command through COMMAND_RUNNER. The API token travels only via the
    environment (KAGGLE_API_TOKEN in os.environ);
    argv never carries it, so argv is safe to surface. Output is redacted before it is returned."""
    if needs_creds and not token_present():
        raise KaggleDriverError("KAGGLE_API_TOKEN is not set; refusing to run a Kaggle command")
    env = os.environ.copy()  # the token travels here, never on argv
    result = COMMAND_RUNNER(list(argv), env=env, timeout=timeout)
    result["stdout"] = _redact(result.get("stdout", ""))
    result["stderr"] = _redact(result.get("stderr", ""))
    if check and int(result.get("returncode", 1)) != 0:
        raise KaggleDriverError(
            f"command failed ({' '.join(argv)}): {result['stderr'].strip() or result['stdout'].strip()}"
        )
    return result


def run_kaggle(args: list[str], **kwargs: Any) -> dict[str, Any]:
    return _run([sys.executable, "-I", "-m", "kaggle", *args], **kwargs)


def kaggle_module_available() -> bool:
    """Whether the selected interpreter contains the Kaggle CLI module.

    The managed credential boundary deliberately does not search PATH: a user-writable
    console-script shim must never receive the Kaggle token. The runtime wrapper selects and
    attests the interpreter before this module starts.
    """

    return importlib.util.find_spec("kaggle") is not None


# --- naming + manifest --------------------------------------------------------

def _new_job_id() -> str:
    return f"kg_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", str(text).lower()).strip("-")
    return slug or "job"


def kernel_slug(job_id: str, round_idx: int, chunk_idx: int) -> str:
    """Per-kernel slug (kaggle requires lowercase alphanumeric + hyphens)."""
    return _slugify(f"{MANAGED_BY}-{job_id}-r{round_idx}-c{chunk_idx}")


def kernel_ref(job_id: str, round_idx: int, chunk_idx: int, *, username: str | None = None) -> str:
    """Fully-qualified kernel ref `owner/slug`. The owner is the kagglehub-resolved username;
    dry-run/planning paths pass none and fall back to a `<kaggle-user>` placeholder (no network)."""
    user = username or "<kaggle-user>"
    return f"{user}/{kernel_slug(job_id, round_idx, chunk_idx)}"


def dataset_slug(job_id: str) -> str:
    return _slugify(f"{MANAGED_BY}-ckpt-{job_id}")


def dataset_ref(job_id: str, *, username: str | None = None) -> str:
    """Fully-qualified checkpoint-dataset ref `owner/slug` (owner as in `kernel_ref`)."""
    user = username or "<kaggle-user>"
    return f"{user}/{dataset_slug(job_id)}"


def _read_manifest(job_dir: str | Path) -> dict[str, Any]:
    path = Path(job_dir).expanduser() / "manifest.json"
    if not path.is_file():
        raise KaggleDriverError(f"job bundle manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def estimate_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Backend-agnostic estimate read from the portable job bundle manifest."""
    core_hours = manifest.get("core_hours") or manifest.get("est_core_hours") or 0.0
    parallelism = manifest.get("parallelism") or manifest.get("cores") or 1
    peak_ram_gb = manifest.get("peak_ram_gb")
    if peak_ram_gb in (None, 0, 0.0):
        peak_ram_gb = float(int(manifest.get("memory_mb", 0) or 0)) / 1024.0
    return {
        "core_hours": float(core_hours),
        "parallelism": max(1, int(parallelism)),
        "peak_ram_gb": float(peak_ram_gb or 0.0),
        "gpu": bool(manifest.get("gpu")),
        "gpu_hours": float(manifest.get("gpu_hours", 0.0) or 0.0),
        # The run loop dispatches one kernel per remaining unit, so the fan-out the probe
        # estimates has to see the partitioning and not only the duration.
        "total_units": _total_units(manifest),
    }


def _total_units(manifest: dict[str, Any]) -> int:
    """Total resumable work units the job splits into. A checkpoint file per completed unit is
    fetched back, so the loop finishes when every unit has a checkpoint. Defaults to 1."""
    return max(1, int(manifest.get("total_units") or manifest.get("chunks") or 1))


def _checkpoint_glob(manifest: dict[str, Any]) -> str:
    return str(manifest.get("checkpoint_glob") or DEFAULT_CHECKPOINT_GLOB)


def _upload_relpaths(manifest: dict[str, Any]) -> list[PurePosixPath]:
    raw = manifest.get("upload_files")
    if not isinstance(raw, list) or not raw:
        raise KaggleDriverError("manifest.upload_files must be a non-empty explicit allowlist")
    paths: list[PurePosixPath] = []
    seen: set[str] = set()
    for value in raw:
        if not isinstance(value, str) or not value:
            raise KaggleDriverError("manifest.upload_files entries must be non-empty strings")
        rel = PurePosixPath(value)
        lower_name = rel.name.lower()
        if (
            rel.is_absolute()
            or len(rel.parts) == 0
            or any(part in {"", ".", ".."} for part in rel.parts)
            or "\\" in value
            or ":" in value
            or rel.parts[0] == "out"
            or "__pycache__" in rel.parts
            or lower_name in DENIED_UPLOAD_NAMES
            or PurePosixPath(lower_name).suffix in DENIED_UPLOAD_SUFFIXES
            or any(marker in lower_name for marker in ("secret", "token", "credential"))
        ):
            raise KaggleDriverError(f"unsafe upload_files entry: {value!r}")
        normalized = rel.as_posix()
        if normalized in seen:
            raise KaggleDriverError(f"duplicate upload_files entry: {value!r}")
        seen.add(normalized)
        paths.append(rel)
    required = {"manifest.json", "run.sh"}
    if not required.issubset(seen):
        raise KaggleDriverError("manifest.upload_files must include manifest.json and run.sh")
    if len(paths) > MAX_BUNDLE_FILES:
        raise KaggleDriverError("job bundle exceeds the upload file-count limit")
    return paths


def _read_upload_file(job_dir: Path, rel: PurePosixPath) -> bytes:
    cursor = job_dir
    for part in rel.parts[:-1]:
        cursor /= part
        try:
            directory_info = cursor.lstat()
        except OSError as exc:
            raise KaggleDriverError(f"upload directory is unavailable: {cursor.name}") from exc
        directory_attributes = int(getattr(directory_info, "st_file_attributes", 0))
        directory_reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        if (
            not stat.S_ISDIR(directory_info.st_mode)
            or stat.S_ISLNK(directory_info.st_mode)
            or bool(directory_attributes & directory_reparse)
        ):
            raise KaggleDriverError(f"upload path contains a reparse directory: {part}")
    path = job_dir.joinpath(*rel.parts)
    try:
        before = path.lstat()
    except OSError as exc:
        raise KaggleDriverError(f"upload file is unavailable: {rel.as_posix()}") from exc
    attributes = int(getattr(before, "st_file_attributes", 0))
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    if stat.S_ISLNK(before.st_mode) or bool(attributes & reparse):
        raise KaggleDriverError(f"upload file is a reparse point: {rel.as_posix()}")
    if not stat.S_ISREG(before.st_mode):
        raise KaggleDriverError(f"upload entry is not a regular file: {rel.as_posix()}")
    if int(getattr(before, "st_nlink", 1)) != 1:
        raise KaggleDriverError(f"upload file has multiple hard links: {rel.as_posix()}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        after = os.fstat(descriptor)
        after_attributes = int(getattr(after, "st_file_attributes", 0))
        if (
            not stat.S_ISREG(after.st_mode)
            or bool(after_attributes & reparse)
            or int(getattr(after, "st_nlink", 1)) != 1
            or (int(before.st_dev), int(before.st_ino))
            != (int(after.st_dev), int(after.st_ino))
            or int(before.st_size) != int(after.st_size)
        ):
            raise KaggleDriverError(f"upload file changed during snapshot: {rel.as_posix()}")
        chunks: list[bytes] = []
        remaining = int(after.st_size)
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise KaggleDriverError(f"upload file ended early: {rel.as_posix()}")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def units_done(out_dir: Path, glob: str = DEFAULT_CHECKPOINT_GLOB) -> int:
    """Count distinct completed-unit checkpoints in the cumulative out/ tree (a set by file
    name, so a re-downloaded checkpoint is never double-counted).

    The kernel runner writes checkpoints to $OUT = /kaggle/working/out and
    `kaggle kernels output -p DEST` preserves that layout, so a fetched checkpoint
    arrives at <out_dir>/out/. Both that nested path and the flat one are counted: a
    glob of out_dir alone finds nothing after a real run, which reports every unit as
    missing and stops resume from ever engaging."""
    out_dir = Path(out_dir)
    names: set[str] = set()
    for root in (out_dir, out_dir / "out"):
        if root.is_dir():
            names.update(p.name for p in root.glob(glob))
    return len(names)


# --- kernel packaging ---------------------------------------------------------

def _thin_runner_embedded(
    job_id: str,
    round_idx: int,
    chunk_idx: int,
    num_chunks: int,
    zip_b64: str,
    bundle_digest: str,
) -> str:
    """Self-contained Python runner: Kaggle script kernels only ship the code_file.

    Nested bundle/ directories are NOT uploaded by the Kaggle API. Embed the
    portable job as base64 zip, extract to bundle/, then invoke run.sh.
    """
    return (
        "#!/usr/bin/env python3\n"
        f"# {MANAGED_BY} kernel runner (embed-zip): job={job_id} round={round_idx} "
        f"chunk={chunk_idx}/{num_chunks}\n"
        "import base64, hashlib, io, os, subprocess, sys, zipfile\n"
        f"os.chdir({KERNEL_WORKDIR!r})\n"
        f"BUNDLE_ZIP_B64 = {zip_b64!r}\n"
        "raw = base64.b64decode(BUNDLE_ZIP_B64)\n"
        f"EXPECTED_BUNDLE_SHA256 = {bundle_digest!r}\n"
        "if hashlib.sha256(raw).hexdigest() != EXPECTED_BUNDLE_SHA256:\n"
        "    raise SystemExit('embedded bundle digest mismatch')\n"
        "if os.path.isdir('bundle'):\n"
        "    import shutil as _sh; _sh.rmtree('bundle')\n"
        "os.makedirs('bundle', exist_ok=True)\n"
        "with zipfile.ZipFile(io.BytesIO(raw)) as zf:\n"
        "    zf.extractall('bundle')\n"
        "os.chmod('bundle/run.sh', 0o755)\n"
        "cores = str(os.cpu_count() or 1)\n"
        "env = os.environ.copy()\n"
        f"env['CHUNK_IDX'] = str({chunk_idx})\n"
        f"env['NUM_CHUNKS'] = str({num_chunks})\n"
        "env['CORES'] = cores\n"
        "env['OUT'] = os.path.join(os.getcwd(), 'out')\n"
        "os.makedirs(env['OUT'], exist_ok=True)\n"
        "rc = subprocess.call(['bash', 'run.sh'], env=env, cwd='bundle')\n"
        # Copy unit checkpoints and engine JSON to /kaggle/working/out for fetch
        "import shutil, pathlib\n"
        "src = pathlib.Path('bundle/out')\n"
        "dst = pathlib.Path('out')\n"
        "dst.mkdir(exist_ok=True)\n"
        "if src.is_dir():\n"
        "    for p in src.iterdir():\n"
        "        if p.is_file():\n"
        "            shutil.copy2(p, dst / p.name)\n"
        "sys.exit(rc)\n"
    )


def _zip_job_bytes(job_dir: Path) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    job_dir = Path(os.path.abspath(Path(job_dir).expanduser()))
    root_info = job_dir.lstat()
    root_attributes = int(getattr(root_info, "st_file_attributes", 0))
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or stat.S_ISLNK(root_info.st_mode)
        or bool(root_attributes & reparse)
    ):
        raise KaggleDriverError("job bundle root must be a regular directory, not a reparse point")
    manifest = _read_manifest(job_dir)
    total_bytes = 0
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in _upload_relpaths(manifest):
            payload = _read_upload_file(job_dir, rel)
            total_bytes += len(payload)
            if total_bytes > MAX_BUNDLE_BYTES:
                raise KaggleDriverError("job bundle exceeds the upload byte limit")
            info = zipfile.ZipInfo(rel.as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            zf.writestr(info, payload)
    return buf.getvalue()


def bundle_sha256(job_dir: str | Path) -> str:
    return hashlib.sha256(_zip_job_bytes(Path(job_dir))).hexdigest()


def build_kernel_dir(*, job_id: str, job_dir: str | Path, round_idx: int, chunk_idx: int,
                     num_chunks: int, gpu: bool, checkpoints_dir: str | Path | None,
                     dest_root: str | Path, username: str | None = None,
                     bundle_zip: bytes | None = None) -> Path:
    """Assemble a kernel working directory: embed-zip code_file + metadata.

    Kaggle script kernels only upload the code_file; nested bundle/ is not
    shipped. The portable job is base64-embedded and extracted at runtime.
    """
    dest = Path(dest_root) / f"kernel-r{round_idx}-c{chunk_idx}"
    dest.mkdir(parents=True, exist_ok=True)
    bundle_zip = bytes(bundle_zip) if bundle_zip is not None else _zip_job_bytes(Path(job_dir))
    bundle_digest = hashlib.sha256(bundle_zip).hexdigest()
    zip_b64 = base64.b64encode(bundle_zip).decode("ascii")
    code_file = f"run-r{round_idx}-c{chunk_idx}.py"
    (dest / code_file).write_text(
        _thin_runner_embedded(
            job_id,
            round_idx,
            chunk_idx,
            num_chunks,
            zip_b64,
            bundle_digest,
        ),
        encoding="utf-8",
    )

    dataset_sources: list[str] = []
    if checkpoints_dir and units_done(Path(checkpoints_dir)) > 0:
        # Resume: the prior checkpoints ride along as the kernel's input dataset so the run
        # skips already-completed units.
        dataset_sources.append(dataset_ref(job_id, username=username))

    metadata = {
        "id": kernel_ref(job_id, round_idx, chunk_idx, username=username),
        "title": f"{MANAGED_BY} {job_id} r{round_idx} c{chunk_idx}",
        "code_file": code_file,
        "language": "python",
        "kernel_type": "script",
        "is_private": True,
        "enable_gpu": bool(gpu),
        "enable_internet": False,
        "dataset_sources": dataset_sources,
        "competition_sources": [],
        "kernel_sources": [],
    }
    (dest / "kernel-metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return dest


def _sync_checkpoint_dataset(*, job_id: str, checkpoints_dir: Path, first_time: bool,
                             confirm: bool, dry_run: bool, username: str | None = None) -> dict[str, Any]:
    """Push the accumulated checkpoints to a private Kaggle Dataset so the next round's kernels
    can attach them as input (the resume mechanism). Create on the first sync, version after.
    Mockable; --dry-run prints the planned command with no upload."""
    checkpoints_dir = Path(checkpoints_dir)
    meta = {
        "title": f"{MANAGED_BY} checkpoints {job_id}",
        "id": dataset_ref(job_id, username=username),
        "licenses": [{"name": "other"}],
    }
    (checkpoints_dir / "dataset-metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if first_time:
        argv = ["datasets", "create", "-p", str(checkpoints_dir), "--dir-mode", "zip"]
    else:
        argv = ["datasets", "version", "-p", str(checkpoints_dir), "-m", f"resume {job_id}", "--dir-mode", "zip"]
    if dry_run:
        return {"dry_run": True, "dataset": dataset_ref(job_id, username=username), "command": ["kaggle", *argv]}
    if not confirm:
        raise KaggleDriverError("refusing to sync checkpoints: explicit confirm is required")
    run_kaggle(argv, timeout=600.0)
    return {"dataset": dataset_ref(job_id), "created": first_time}


# --- planning verbs (free; no kernel) -----------------------------------------

def doctor(config: Any) -> dict[str, Any]:
    """Offline readiness snapshot. Reuses the backend doctor and adds a driver note."""
    out = dict(kaggle_backend.doctor(config))
    out["driver"] = "kaggle_driver"
    out["confirm_gate"] = "lifecycle verbs require guarded KAGGLE_API_TOKEN and --confirm"
    out["reaper"] = "none needed (kernels auto-stop at the 12h session cap and cost nothing)"
    return out


def bootstrap(config: Any | None) -> dict[str, Any]:
    """One-time readiness check. Confirms the kaggle CLI + kagglehub are installed and the API
    token is present, then validates/primes via kagglehub -- kagglehub.whoami() proves the token
    is valid and yields the authenticated username the kaggle CLI uses for kernel ops. The
    kagglehub validation goes through the mockable backend hook, so tests make no live call."""
    result: dict[str, Any] = {
        "kaggle_cli_available": kaggle_module_available(),
        "kaggle_cli_mode": "python-module",
        "kagglehub_available": importlib.util.find_spec("kagglehub") is not None,
        "api_token_present": token_present(),
    }
    if result["kaggle_cli_available"]:
        version = run_kaggle(["--version"], timeout=30.0, needs_creds=False, check=False)
        exit_code = int(version.get("returncode", 1))
        result["kaggle_cli_version"] = {
            "ok": exit_code == 0, "exit_code": exit_code,
            "output": (version.get("stdout", "") + version.get("stderr", "")).strip(),
        }
    else:
        result["kaggle_cli_version"] = {
            "ok": False, "exit_code": None, "output": "kaggle Python module not found",
        }
    if result["api_token_present"]:
        who = _whoami(config)
        result["account"] = {"usable": bool(who.get("usable")), "username": who.get("username"),
                             "reason": who.get("reason")}
    else:
        result["account"] = {"usable": False, "username": None, "reason": "no_kaggle_api_token"}
    result["doctor"] = doctor(config) if config is not None else {"error": "config not found"}
    if not result["kaggle_cli_available"] or not result["kagglehub_available"]:
        result["hint"] = ("install the Kaggle CLI + kagglehub into the selected trusted "
                          "Python >=3.11: pip install 'kaggle>=2.2.4,<3' "
                          "'kagglehub>=1.0.2,<2', then project KAGGLE_API_TOKEN "
                          "through the guarded runtime secret launcher")
    return result


def preflight(*, job_dir: str | Path, config: Any, state_root: Path | None = None) -> dict[str, Any]:
    """The plan the router consumes: kind (cpu/gpu), estimated resume rounds and kernel count,
    concurrency, session cap, GPU-hour estimate vs the weekly cap, adequacy, availability, and
    a budget verdict. No kernel is pushed and nothing is reserved."""
    manifest = _read_manifest(job_dir)
    estimate = estimate_from_manifest(manifest)
    probe = kaggle_backend.probe(estimate, config=config, resources=None, state_root=state_root)
    gpu = bool(estimate.get("gpu"))

    if not probe["adequate"]:
        verdict = "inadequate"
    elif not probe["available"]:
        verdict = "blocked"
    elif gpu:
        verdict = "gpu_within_weekly_cap"
    else:
        verdict = "free_cpu"

    kernel_cores = kaggle_backend.kernel_cores(config)
    return {
        "backend": "kaggle",
        "job_id": manifest.get("job_id"),
        "bundle_sha256": bundle_sha256(job_dir),
        "kind": probe["kind"],
        "total_units": _total_units(manifest),
        "est_rounds": probe["est_runs"],
        "est_kernels": probe["est_kernels"],
        # The hardware a kernel actually provides, so a caller can size the job against
        # the lane without reading research-compute.toml. Kaggle's usable capacity is
        # per-kernel x concurrency, not per-kernel: quoting only the per-kernel figure
        # understates the free lane and misroutes work to a paid one.
        "kernel_cores": kernel_cores,
        "kernel_ram_gb": kaggle_backend.kernel_ram_gb(config),
        "aggregate_cores": kernel_cores * probe["concurrency"],
        "concurrency": probe["concurrency"],
        "session_hours": probe["session_hours"],
        "max_runs": kaggle_backend.max_runs(config),
        "gpu_hours_est": probe["gpu_hours_est"],
        "gpu_hours_used_week": probe["gpu_hours_used_week"],
        "gpu_hours_cap": probe["gpu_hours_cap"],
        "within_gpu_cap": probe["within_gpu_cap"],
        "adequate": probe["adequate"],
        "available": probe["available"],
        "budget_verdict": verdict,
        "cost": "free",
        "reason": probe["reason"],
        "provisioned": False,
    }


# --- kernel lifecycle primitives (mockable; used by verbs and by `run`) --------

def _push_kernel(*, job_id: str, job_dir: str | Path, round_idx: int, chunk_idx: int,
                 num_chunks: int, gpu: bool, checkpoints_dir: Path | None,
                 work_root: Path, username: str | None = None,
                 bundle_zip: bytes | None = None) -> dict[str, Any]:
    kdir = build_kernel_dir(job_id=job_id, job_dir=job_dir, round_idx=round_idx,
                            chunk_idx=chunk_idx, num_chunks=num_chunks, gpu=gpu,
                            checkpoints_dir=checkpoints_dir, dest_root=work_root,
                            username=username, bundle_zip=bundle_zip)
    run_kaggle(["kernels", "push", "-p", str(kdir)], timeout=300.0)
    return {"kernel": kernel_ref(job_id, round_idx, chunk_idx, username=username), "dir": str(kdir),
            "gpu": gpu, "round": round_idx, "chunk": chunk_idx}


def _kernel_status(kernel: str) -> str:
    result = run_kaggle(["kernels", "status", kernel])
    text = (result.get("stdout") or "").lower()
    for state in ("complete", "error", "cancelAcknowledged".lower(), "running", "queued"):
        if state in text:
            return "complete" if state == "complete" else ("error" if state == "error" else state)
    return "unknown"


def _wait_kernel(kernel: str, *, timeout: float | None = None, interval: float = 20.0,
                 max_polls: int = 100000) -> dict[str, Any]:
    start = time.time()
    for poll in range(int(max_polls)):
        state = _kernel_status(kernel)
        if state in ("complete", "error"):
            return {"kernel": kernel, "status": state, "polls": poll + 1}
        if timeout is not None and time.time() - start > timeout:
            return {"kernel": kernel, "status": "timeout", "polls": poll + 1}
        time.sleep(interval)
    return {"kernel": kernel, "status": "timeout", "polls": int(max_polls)}


def _reparse_or_link(path: Path) -> bool:
    info = path.lstat()
    attributes = int(getattr(info, "st_file_attributes", 0))
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return path.is_symlink() or bool(attributes & reparse)


def _validate_fetched_tree(dest: Path, *, expected_log_name: str) -> dict[str, int]:
    root = Path(dest).resolve(strict=True)
    files = 0
    total_bytes = 0
    for current, directories, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        if current_path == root:
            if set(directories) - {"out"} or set(names) - {expected_log_name}:
                raise KaggleDriverError("fetched output root contains a non-allowlisted entry")
        elif current_path == root / "out":
            if directories:
                raise KaggleDriverError("fetched output directory must be flat")
        else:
            raise KaggleDriverError("fetched output escapes the fixed out/ layout")
        for name in [*directories, *names]:
            path = current_path / name
            if _reparse_or_link(path):
                raise KaggleDriverError(f"fetched output contains a reparse point: {path.name}")
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise KaggleDriverError("fetched output escapes the destination")
            info = path.stat(follow_symlinks=False)
            if path.is_file():
                if (
                    current_path == root / "out"
                    and re.fullmatch(SAFE_OUTPUT_BASENAME_PATTERN, name) is None
                ):
                    raise KaggleDriverError(f"fetched output name is not allowlisted: {name!r}")
                if int(getattr(info, "st_nlink", 1)) != 1:
                    raise KaggleDriverError(f"fetched output contains a hard-linked file: {path.name}")
                files += 1
                total_bytes += int(info.st_size)
                if files > MAX_FETCH_FILES or total_bytes > MAX_FETCH_BYTES:
                    raise KaggleDriverError("fetched output exceeds the safety limit")
    return {"files": files, "bytes": total_bytes}


def _prepare_fetch_destination(dest: Path, *, allow_existing: bool) -> Path:
    dest = Path(dest).expanduser()
    if dest.exists():
        if _reparse_or_link(dest) or not dest.is_dir():
            raise KaggleDriverError("fetch destination must be a regular directory")
        if not allow_existing and any(dest.iterdir()):
            raise KaggleDriverError("fetch destination must be empty")
    else:
        dest.mkdir(parents=True, exist_ok=False)
    return dest


def _fetch_kernel(kernel: str, *, dest: Path, allow_existing: bool = False) -> dict[str, Any]:
    dest = _prepare_fetch_destination(Path(dest), allow_existing=allow_existing)
    run_kaggle(
        [
            "kernels",
            "output",
            kernel,
            "-p",
            str(dest),
            "--file-pattern",
            SAFE_OUTPUT_PATTERN,
        ],
        timeout=600.0,
    )
    expected_log_name = kernel.rsplit("/", 1)[-1] + ".log"
    validation = _validate_fetched_tree(dest, expected_log_name=expected_log_name)
    return {"kernel": kernel, "fetched_to": str(dest), "validation": validation}


def verify_fetched_output(job_dir: str | Path, dest: str | Path) -> dict[str, Any]:
    job = Path(job_dir).expanduser().resolve()
    output = Path(dest).expanduser().resolve() / "out"
    if not output.is_dir():
        raise KaggleDriverError("fetched output is missing the required out/ directory")
    manifest = _read_manifest(job)
    total = _total_units(manifest)
    manifest_digest = hashlib.sha256(_read_upload_file(job, PurePosixPath("manifest.json"))).hexdigest()
    expected_names = {f"unit-{index:04d}.json" for index in range(total)} | {"result.json"}
    actual_names = {path.name for path in output.iterdir() if path.is_file()}
    if actual_names != expected_names:
        raise KaggleDriverError(
            "fetched output does not contain the exact checkpoint/result set"
        )
    rows: list[dict[str, Any]] = []
    for index in range(total):
        path = output / f"unit-{index:04d}.json"
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise KaggleDriverError(f"checkpoint {path.name} is not valid JSON") from exc
        if (
            not isinstance(row, dict)
            or row.get("status") != "PASS"
            or row.get("unit") != index
            or row.get("manifest_sha256") != manifest_digest
        ):
            raise KaggleDriverError(f"checkpoint {path.name} failed host verification")
        rows.append(row)
    try:
        result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise KaggleDriverError("result.json is not valid JSON") from exc
    if (
        not isinstance(result, dict)
        or result.get("status") != "PASS"
        or result.get("verified") is not True
        or result.get("units") != total
        or result.get("manifest_sha256") != manifest_digest
    ):
        raise KaggleDriverError("result.json failed host verification")
    verify = manifest.get("verify")
    if isinstance(verify, dict) and "expected_prime_count" in verify:
        expected = int(verify["expected_prime_count"])
        if result.get("prime_count") != expected or any(
            row.get("prime_count") != expected for row in rows
        ):
            raise KaggleDriverError("prime-count result does not match manifest.verify")
    return {
        "verified": True,
        "manifest_sha256": manifest_digest,
        "units": total,
        "result": result,
    }


# --- lifecycle verbs (submit real kernels) ------------------------------------

def _submission_intent_path(
    state_root: Path,
    *,
    job_id: str,
    round_idx: int,
    chunk_idx: int,
) -> Path:
    name = f"{kernel_slug(job_id, round_idx, chunk_idx)}.json"
    return Path(state_root) / "kaggle-submissions" / name


def _write_submission_intent(path: Path, payload: dict[str, Any], *, create: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if create:
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise KaggleDriverError(
                f"submission intent already exists for {payload['kernel']}; "
                "query status instead of pushing again"
            ) from exc
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def push(*, job_dir: str | Path, config: Any, round_idx: int = 0, chunk_idx: int = 0,
         num_chunks: int = 1, gpu: bool | None = None, checkpoints_dir: str | Path | None = None,
         confirm: bool = False, dry_run: bool = False, work_root: str | Path | None = None,
         state_root: str | Path | None = None,
         expected_bundle_sha256: str | None = None,
         expected_owner: str | None = None) -> dict[str, Any]:
    """Push a single kernel run (one chunk). Manual/debug granularity; `run` orchestrates the
    full fan-out + resume loop. `--dry-run` prints the planned `kaggle kernels push` with no
    submission."""
    manifest = _read_manifest(job_dir)
    job_id = str(manifest.get("job_id") or _new_job_id())
    bundle_zip = _zip_job_bytes(Path(job_dir))
    digest = hashlib.sha256(bundle_zip).hexdigest()
    gpu = bool(manifest.get("gpu")) if gpu is None else bool(gpu)
    ckpt = Path(checkpoints_dir).expanduser() if checkpoints_dir else None
    if dry_run:
        return {"dry_run": True, "job_id": job_id,
                "kernel": kernel_ref(job_id, round_idx, chunk_idx, username=expected_owner),
                "bundle_sha256": digest,
                "gpu": gpu, "enable_internet": False,
                "command": [sys.executable, "-I", "-m", "kaggle", "kernels", "push", "-p",
                            f"<kernel-dir r{round_idx} c{chunk_idx}>"]}
    if not token_present():
        raise KaggleDriverError("refusing to push: KAGGLE_API_TOKEN is not set")
    if not confirm:
        raise KaggleDriverError("refusing to push: explicit confirm is required")
    if _total_units(manifest) != 1 or num_chunks != 1 or chunk_idx != 0:
        raise KaggleDriverError(
            "live push is limited to one-unit bundles until resumable multi-run recovery is hardened"
        )
    if not expected_bundle_sha256 or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_bundle_sha256):
        raise KaggleDriverError("live push requires the reviewed --bundle-sha256 from dry-run")
    if digest.lower() != expected_bundle_sha256.lower():
        raise KaggleDriverError("job bundle changed after dry-run review")
    if state_root is None:
        raise KaggleDriverError("live push requires a durable submission-intent state root")
    if not expected_owner or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,49}", expected_owner) is None:
        raise KaggleDriverError("live push requires the reviewed --owner Kaggle username")
    kernel = kernel_ref(job_id, round_idx, chunk_idx, username=expected_owner)
    intent_path = _submission_intent_path(
        Path(state_root),
        job_id=job_id,
        round_idx=round_idx,
        chunk_idx=chunk_idx,
    )
    if intent_path.exists():
        raise KaggleDriverError(
            f"submission intent already exists for {kernel}; query status instead of pushing again"
        )
    if gpu:
        raise KaggleDriverError(
            "live GPU push is disabled until the weekly reservation gate is atomic across processes"
        )
    username = _resolve_username(config, required=True)
    if username.lower() != expected_owner.lower():
        raise KaggleDriverError("authenticated Kaggle owner does not match reviewed --owner")
    estimate = estimate_from_manifest(manifest)
    probe = kaggle_backend.probe(
        estimate,
        config=config,
        resources={"liveness": {"kaggle": {"usable": True, "reason": "validated-owner"}}},
        state_root=Path(state_root),
    )
    if not probe.get("adequate"):
        raise KaggleDriverError(f"job is inadequate for Kaggle: {probe.get('reason', 'unknown')}")
    if not probe.get("available"):
        raise KaggleDriverError(f"Kaggle lane unavailable: {probe.get('reason', 'unknown')}")
    root = Path(work_root).expanduser() if work_root else Path(tempfile.mkdtemp(prefix="aas-kaggle-"))
    intent = {
        "schema": "ai-agents-skills.kaggle-submission-intent.v1",
        "state": "prepared",
        "kernel": kernel,
        "bundle_sha256": digest,
        "gpu": gpu,
    }
    _write_submission_intent(intent_path, intent, create=True)
    result = _push_kernel(
        job_id=job_id,
        job_dir=job_dir,
        round_idx=round_idx,
        chunk_idx=chunk_idx,
        num_chunks=num_chunks,
        gpu=gpu,
        checkpoints_dir=ckpt,
        work_root=root,
        username=username,
        bundle_zip=bundle_zip,
    )
    intent["state"] = "submitted"
    _write_submission_intent(intent_path, intent, create=False)
    result["bundle_sha256"] = digest
    result["submission_intent"] = str(intent_path)
    return result


def status(*, kernel: str, config: Any) -> dict[str, Any]:
    """Kernel run state (`kaggle kernels status`). Free of side effects."""
    return {"kernel": kernel, "status": _kernel_status(kernel)}


def wait(*, kernel: str, config: Any, timeout: float | None = None,
         interval: float = 20.0) -> dict[str, Any]:
    """Poll a kernel until it completes / errors or the wall cap hits."""
    return _wait_kernel(kernel, timeout=timeout, interval=interval)


def fetch(*, kernel: str, config: Any, job_dir: str | Path,
          dest: str | Path | None = None) -> dict[str, Any]:
    """Download a kernel's output (checkpoints) with `kaggle kernels output`."""
    dest_dir = Path(dest).expanduser() if dest else Path.cwd() / "kaggle-results"
    result = _fetch_kernel(kernel, dest=dest_dir)
    result["verification"] = verify_fetched_output(job_dir, dest_dir)
    return result


def run(*, job_dir: str | Path, config: Any, state_root: Path, confirm: bool = False,
        dry_run: bool = False, dest: str | Path | None = None,
        max_runs: int | None = None) -> dict[str, Any]:
    """The multi-run resume loop with concurrent fan-out (the crux of this lane).

    Each ROUND fans out up to `concurrency` (~5) kernels, one per remaining work chunk, each a
    <=12h kernel run over its slice. The round then polls all kernels and fetches their
    checkpoints into a cumulative out/ tree. If work remains, the accumulated checkpoints are
    re-attached (as a Kaggle Dataset input) and the NEXT round re-pushes the remaining chunks --
    resuming from the checkpoints. The loop ends when every unit has a checkpoint (DONE) or
    max_runs rounds are used (bounded so a looping agent cannot push kernels forever).

    GPU jobs pass the fail-closed weekly GPU-hour gate once, before the first push; CPU jobs
    are free and quota-free. `--dry-run` prints the planned first round + the loop shape with
    nothing submitted."""
    manifest = _read_manifest(job_dir)
    job_id = str(manifest.get("job_id") or _new_job_id())
    estimate = estimate_from_manifest(manifest)
    gpu = bool(estimate.get("gpu"))
    total = _total_units(manifest)
    glob = _checkpoint_glob(manifest)
    cap_runs = int(max_runs) if max_runs is not None else kaggle_backend.max_runs(config)
    fanout = kaggle_backend.concurrency(config)
    probe = kaggle_backend.probe(estimate, config=config, resources=None, state_root=Path(state_root))

    if dry_run:
        first_round = min(fanout, total)
        return {
            "dry_run": True, "job_id": job_id, "kind": "gpu" if gpu else "cpu",
            "total_units": total, "concurrency": fanout, "max_runs": cap_runs,
            "session_hours": kaggle_backend.session_hours(config),
            "first_round_kernels": [kernel_ref(job_id, 0, c) for c in range(first_round)],
            "loop": ("push <=concurrency kernels -> wait -> fetch checkpoints -> if units "
                     "remain, re-attach checkpoints and re-push -> until DONE or max_runs"),
            "gpu_gate": "weekly GPU-hour gate applies before the first push" if gpu else "free (no gate)",
            "provisioned": False,
        }

    raise KaggleDriverError(
        "live multi-run is disabled until crash-safe status-first recovery and "
        "verified checkpoint merging are implemented; use one-unit push/status/wait/fetch"
    )


# --- CLI ----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kaggle-research-compute",
        description="Kaggle Kernels lifecycle driver for the research-compute Kaggle lane.",
    )
    parser.add_argument("--config", default=None, help="Path to research-compute.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("bootstrap", help="Check the kaggle CLI + credentials and report doctor (no kernel)")
    sub.add_parser("doctor", help="Offline lane / credentials / kaggle CLI / caps readiness")

    pf = sub.add_parser("preflight", help="Emit the Kaggle plan for a job bundle (no kernel)")
    pf.add_argument("--job", required=True, help="Path to a portable job-bundle directory")
    pf.add_argument("--json", action="store_true", help="(accepted for parity; output is always JSON)")

    push_p = sub.add_parser("push", help="Push one kernel run (a chunk)")
    push_p.add_argument("--job", required=True)
    push_p.add_argument("--round", type=int, default=0)
    push_p.add_argument("--chunk", type=int, default=0)
    push_p.add_argument("--num-chunks", type=int, default=1)
    push_p.add_argument("--gpu", action="store_true", default=None)
    push_p.add_argument("--checkpoints", default=None)
    push_p.add_argument("--confirm", action="store_true")
    push_p.add_argument("--dry-run", action="store_true")
    push_p.add_argument(
        "--bundle-sha256",
        help="reviewed bundle digest emitted by preflight/dry-run; required for live push",
    )
    push_p.add_argument("--owner", help="reviewed authenticated Kaggle username")

    status_p = sub.add_parser("status", help="Kernel run state")
    status_p.add_argument("kernel")

    wait_p = sub.add_parser("wait", help="Poll a kernel until it finishes or the wall cap hits")
    wait_p.add_argument("kernel")
    wait_p.add_argument("--timeout", type=float, default=None)

    fetch_p = sub.add_parser("fetch", help="Download a kernel's output (checkpoints)")
    fetch_p.add_argument("kernel")
    fetch_p.add_argument("--job", required=True)
    fetch_p.add_argument("--dest", default=None)

    run_p = sub.add_parser("run", help="Plan the bounded multi-run loop (live execution disabled)")
    run_p.add_argument("--job", required=True)
    run_p.add_argument("--confirm", action="store_true")
    run_p.add_argument("--dry-run", action="store_true")
    run_p.add_argument("--dest", default=None)
    run_p.add_argument("--max-runs", type=int, default=None)
    return parser


def _load(args: argparse.Namespace) -> tuple[Any | None, Path]:
    root = workspace_root()
    config_path = Path(args.config).expanduser().resolve() if args.config else default_config_path(root)
    state_root = config_path.parent.parent / "memories" / "research-compute"
    config: Any | None = None
    if config_path.exists():
        config = load_config(config_path)
        state_root = config.state_root(root)
    return config, state_root


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config, state_root = _load(args)
        if args.command == "bootstrap":
            result = bootstrap(config)
        else:
            if config is None:
                raise KaggleDriverError("research-compute.toml not found; run the broker bootstrap first")
            Path(state_root).mkdir(parents=True, exist_ok=True)
            if args.command == "doctor":
                result = doctor(config)
            elif args.command == "preflight":
                result = preflight(job_dir=args.job, config=config, state_root=Path(state_root))
            elif args.command == "push":
                result = push(job_dir=args.job, config=config, round_idx=args.round,
                              chunk_idx=args.chunk, num_chunks=args.num_chunks, gpu=args.gpu,
                              checkpoints_dir=args.checkpoints, confirm=args.confirm,
                              dry_run=args.dry_run, state_root=Path(state_root),
                              expected_bundle_sha256=args.bundle_sha256,
                              expected_owner=args.owner)
            elif args.command == "status":
                result = status(kernel=args.kernel, config=config)
            elif args.command == "wait":
                result = wait(kernel=args.kernel, config=config, timeout=args.timeout)
            elif args.command == "fetch":
                result = fetch(kernel=args.kernel, config=config, job_dir=args.job, dest=args.dest)
            elif args.command == "run":
                result = run(job_dir=args.job, config=config, state_root=Path(state_root),
                             confirm=args.confirm, dry_run=args.dry_run, dest=args.dest,
                             max_runs=args.max_runs)
            else:  # pragma: no cover - argparse guards this
                raise KaggleDriverError(f"unhandled command: {args.command}")
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": _redact(str(exc))}, indent=2))
        return 1
    print(json.dumps({"ok": True, **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
