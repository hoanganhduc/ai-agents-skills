"""Kaggle Kernels lifecycle driver for the research-compute Kaggle lane.

This is the `kaggle` kernel-lifecycle CLI referenced by the kaggle-research-compute skill.
Kaggle has no SSH and no persistent server: you push a script + kernel-metadata.json as a
kernel, poll its status, and download its output. A kernel is capped at a 12h session, so a
job that needs more wall time spans MULTIPLE kernel runs -- the `run` verb is the multi-run
resume loop that pushes a chunk-batch across up to ~5 concurrent kernels, polls them, fetches
their checkpoints, and re-pushes the remaining work with the checkpoints re-attached until the
job is DONE (bounded by max_runs).

Planning verbs (bootstrap, doctor, preflight) are free and never push a kernel. Lifecycle
verbs (push, status, wait, fetch, run) submit real kernels and require the new Kaggle API token
(KAGGLE_API_TOKEN, or ~/.kaggle/access_token) plus an explicit confirm.

Auth uses Kaggle's current "API Tokens (Recommended)" single token, NOT the legacy
KAGGLE_USERNAME + KAGGLE_KEY pair and NOT a kaggle.json. bootstrap validates/primes via
kagglehub (kagglehub.whoami() proves the token is valid and yields the authenticated username
used to address kernels/datasets); the kaggle CLI (>=1.8.0) then authenticates kernel
push/status/output with the same token.

Guardrails:
  * The Kaggle API token is read from KAGGLE_API_TOKEN (or ~/.kaggle/access_token) and injected
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
import importlib.util
import json
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from research_compute import kaggle_backend
from research_compute.config import default_config_path, load_config, workspace_root

MANAGED_BY = "ai-agents-skills"
KERNEL_WORKDIR = "/kaggle/working"
# A kernel's cumulative resume surface fetched back here; each completed work unit lands as a
# checkpoint file so a re-pushed kernel can skip it (resume).
DEFAULT_CHECKPOINT_GLOB = "unit-*.json"


class KaggleDriverError(RuntimeError):
    pass


# --- API token + redaction (env-first, never argv, never logged) --------------
#
# The new Kaggle API token is read from KAGGLE_API_TOKEN or ~/.kaggle/access_token (never the
# legacy KAGGLE_USERNAME + KAGGLE_KEY pair, never a kaggle.json). Resolution + presence live in
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

    proc = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=timeout)
    return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


# Tests replace this to guarantee no external command ever runs offline.
COMMAND_RUNNER: Callable[..., dict[str, Any]] = _default_command_runner


def _run(argv: list[str], *, timeout: float = 120.0, needs_creds: bool = True) -> dict[str, Any]:
    """Run an external command through COMMAND_RUNNER. The API token travels only via the
    environment (KAGGLE_API_TOKEN in os.environ, or the kaggle CLI reads ~/.kaggle/access_token);
    argv never carries it, so argv is safe to surface. Output is redacted before it is returned."""
    if needs_creds and not token_present():
        raise KaggleDriverError("KAGGLE_API_TOKEN is not set; refusing to run a Kaggle command")
    env = os.environ.copy()  # the token travels here, never on argv
    result = COMMAND_RUNNER(list(argv), env=env, timeout=timeout)
    result["stdout"] = _redact(result.get("stdout", ""))
    result["stderr"] = _redact(result.get("stderr", ""))
    if int(result.get("returncode", 1)) != 0:
        raise KaggleDriverError(
            f"command failed ({' '.join(argv)}): {result['stderr'].strip() or result['stdout'].strip()}"
        )
    return result


def run_kaggle(args: list[str], **kwargs: Any) -> dict[str, Any]:
    return _run(["kaggle", *args], **kwargs)


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
    }


def _total_units(manifest: dict[str, Any]) -> int:
    """Total resumable work units the job splits into. A checkpoint file per completed unit is
    fetched back, so the loop finishes when every unit has a checkpoint. Defaults to 1."""
    return max(1, int(manifest.get("total_units") or manifest.get("chunks") or 1))


def _checkpoint_glob(manifest: dict[str, Any]) -> str:
    return str(manifest.get("checkpoint_glob") or DEFAULT_CHECKPOINT_GLOB)


def units_done(out_dir: Path, glob: str = DEFAULT_CHECKPOINT_GLOB) -> int:
    """Count distinct completed-unit checkpoints in the cumulative out/ tree (a set by file
    name, so a re-downloaded checkpoint is never double-counted)."""
    out_dir = Path(out_dir)
    if not out_dir.is_dir():
        return 0
    return len({p.name for p in out_dir.glob(glob)})


# --- kernel packaging ---------------------------------------------------------

def _thin_runner(job_id: str, round_idx: int, chunk_idx: int, num_chunks: int) -> str:
    """The kernel's code_file: a thin runner that executes the bundle's run.sh at the kernel's
    cores over this chunk's slice, resuming from any attached checkpoints, and leaves the
    completed-unit checkpoints in the kernel working dir for `kaggle kernels output`."""
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"# {MANAGED_BY} kernel runner: job={job_id} round={round_idx} "
        f"chunk={chunk_idx}/{num_chunks}\n"
        f"cd {KERNEL_WORKDIR}\n"
        "CORES=$(nproc)\n"
        f"CHUNK_IDX={chunk_idx} NUM_CHUNKS={num_chunks} CORES=\"$CORES\" bash bundle/run.sh\n"
    )


def build_kernel_dir(*, job_id: str, job_dir: str | Path, round_idx: int, chunk_idx: int,
                     num_chunks: int, gpu: bool, checkpoints_dir: str | Path | None,
                     dest_root: str | Path, username: str | None = None) -> Path:
    """Assemble a kernel working directory: the portable bundle under bundle/, a thin runner
    code_file, kernel-metadata.json, and (on resume rounds) the prior checkpoints attached as
    the kernel's input dataset. No network here -- this is pure local file assembly."""
    dest = Path(dest_root) / f"kernel-r{round_idx}-c{chunk_idx}"
    dest.mkdir(parents=True, exist_ok=True)
    # Copy the portable bundle in as bundle/ so the thin runner can invoke bundle/run.sh.
    bundle_dst = dest / "bundle"
    if bundle_dst.exists():
        shutil.rmtree(bundle_dst)
    shutil.copytree(Path(job_dir).expanduser(), bundle_dst)

    code_file = f"run-r{round_idx}-c{chunk_idx}.sh"
    (dest / code_file).write_text(
        _thin_runner(job_id, round_idx, chunk_idx, num_chunks), encoding="utf-8")

    dataset_sources: list[str] = []
    if checkpoints_dir and units_done(Path(checkpoints_dir)) > 0:
        # Resume: the prior checkpoints ride along as the kernel's input dataset so the run
        # skips already-completed units.
        dataset_sources.append(dataset_ref(job_id, username=username))

    metadata = {
        "id": kernel_ref(job_id, round_idx, chunk_idx, username=username),
        "title": f"{MANAGED_BY} {job_id} r{round_idx} c{chunk_idx}",
        "code_file": code_file,
        "language": "bash",
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
    out["confirm_gate"] = "lifecycle verbs require KAGGLE_API_TOKEN (or ~/.kaggle/access_token) and --confirm"
    out["reaper"] = "none needed (kernels auto-stop at the 12h session cap and cost nothing)"
    return out


def bootstrap(config: Any | None) -> dict[str, Any]:
    """One-time readiness check. Confirms the kaggle CLI + kagglehub are installed and the API
    token is present, then validates/primes via kagglehub -- kagglehub.whoami() proves the token
    is valid and yields the authenticated username the kaggle CLI uses for kernel ops. The
    kagglehub validation goes through the mockable backend hook, so tests make no live call."""
    result: dict[str, Any] = {
        "kaggle_cli_available": shutil.which("kaggle") is not None,
        "kagglehub_available": importlib.util.find_spec("kagglehub") is not None,
        "api_token_present": token_present(),
    }
    if result["api_token_present"]:
        who = _whoami(config)
        result["account"] = {"usable": bool(who.get("usable")), "username": who.get("username"),
                             "reason": who.get("reason")}
    else:
        result["account"] = {"usable": False, "username": None, "reason": "no_kaggle_api_token"}
    result["doctor"] = doctor(config) if config is not None else {"error": "config not found"}
    if not result["kaggle_cli_available"] or not result["kagglehub_available"]:
        result["hint"] = ("install the Kaggle CLI + kagglehub: pip install 'kaggle>=1.8.0' "
                          "'kagglehub>=0.4.1' (KAGGLE_API_TOKEN in the env, or ~/.kaggle/access_token)")
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

    return {
        "backend": "kaggle",
        "job_id": manifest.get("job_id"),
        "kind": probe["kind"],
        "total_units": _total_units(manifest),
        "est_rounds": probe["est_runs"],
        "est_kernels": probe["est_kernels"],
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
                 work_root: Path, username: str | None = None) -> dict[str, Any]:
    kdir = build_kernel_dir(job_id=job_id, job_dir=job_dir, round_idx=round_idx,
                            chunk_idx=chunk_idx, num_chunks=num_chunks, gpu=gpu,
                            checkpoints_dir=checkpoints_dir, dest_root=work_root, username=username)
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


def _fetch_kernel(kernel: str, *, dest: Path) -> dict[str, Any]:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    run_kaggle(["kernels", "output", kernel, "-p", str(dest)], timeout=600.0)
    return {"kernel": kernel, "fetched_to": str(dest)}


# --- lifecycle verbs (submit real kernels) ------------------------------------

def push(*, job_dir: str | Path, config: Any, round_idx: int = 0, chunk_idx: int = 0,
         num_chunks: int = 1, gpu: bool | None = None, checkpoints_dir: str | Path | None = None,
         confirm: bool = False, dry_run: bool = False, work_root: str | Path | None = None) -> dict[str, Any]:
    """Push a single kernel run (one chunk). Manual/debug granularity; `run` orchestrates the
    full fan-out + resume loop. `--dry-run` prints the planned `kaggle kernels push` with no
    submission."""
    manifest = _read_manifest(job_dir)
    job_id = str(manifest.get("job_id") or _new_job_id())
    gpu = bool(manifest.get("gpu")) if gpu is None else bool(gpu)
    ckpt = Path(checkpoints_dir).expanduser() if checkpoints_dir else None
    if dry_run:
        return {"dry_run": True, "job_id": job_id, "kernel": kernel_ref(job_id, round_idx, chunk_idx),
                "gpu": gpu, "enable_internet": False,
                "command": ["kaggle", "kernels", "push", "-p", f"<kernel-dir r{round_idx} c{chunk_idx}>"]}
    if not token_present():
        raise KaggleDriverError("refusing to push: KAGGLE_API_TOKEN is not set")
    if not confirm:
        raise KaggleDriverError("refusing to push: explicit confirm is required")
    username = _resolve_username(config, required=True)
    root = Path(work_root).expanduser() if work_root else Path(tempfile.mkdtemp(prefix="aas-kaggle-"))
    return _push_kernel(job_id=job_id, job_dir=job_dir, round_idx=round_idx, chunk_idx=chunk_idx,
                        num_chunks=num_chunks, gpu=gpu, checkpoints_dir=ckpt, work_root=root,
                        username=username)


def status(*, kernel: str, config: Any) -> dict[str, Any]:
    """Kernel run state (`kaggle kernels status`). Free of side effects."""
    return {"kernel": kernel, "status": _kernel_status(kernel)}


def wait(*, kernel: str, config: Any, timeout: float | None = None,
         interval: float = 20.0) -> dict[str, Any]:
    """Poll a kernel until it completes / errors or the wall cap hits."""
    return _wait_kernel(kernel, timeout=timeout, interval=interval)


def fetch(*, kernel: str, config: Any, dest: str | Path | None = None) -> dict[str, Any]:
    """Download a kernel's output (checkpoints) with `kaggle kernels output`."""
    dest_dir = Path(dest).expanduser() if dest else Path.cwd() / "kaggle-results"
    return _fetch_kernel(kernel, dest=dest_dir)


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

    if not token_present():
        raise KaggleDriverError("refusing to run: KAGGLE_API_TOKEN is not set")
    if not confirm:
        raise KaggleDriverError("refusing to run: explicit confirm is required")
    if not probe["adequate"]:
        raise KaggleDriverError(f"job is inadequate for Kaggle: {probe['reason']}")
    if not probe["available"]:
        raise KaggleDriverError(f"Kaggle lane unavailable: {probe['reason']}")

    # Validate/prime once via kagglehub: whoami() yields the owner used for every kernel/dataset
    # ref this loop pushes.
    username = _resolve_username(config, required=True)

    gpu_reservation = None
    if gpu:
        # Fail-closed weekly GPU-hour gate + local ledger reservation before any push.
        gpu_reservation = kaggle_backend.gpu_budget_gate(
            job_id=job_id, estimate=estimate, config=config, state_root=Path(state_root))

    out_dir = (Path(dest).expanduser() if dest else Path.cwd() / "kaggle-results" / job_id) / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    work_root = Path(tempfile.mkdtemp(prefix=f"aas-kaggle-{job_id}-"))

    rounds: list[dict[str, Any]] = []
    dataset_created = False
    for round_idx in range(cap_runs):
        done = units_done(out_dir, glob)
        remaining = total - done
        if remaining <= 0:
            break
        n_kernels = min(fanout, remaining)
        # Resume rounds (checkpoints already present) re-attach them as an input dataset first:
        # create the checkpoint dataset on the first sync, version it thereafter.
        checkpoint_sync = None
        if done > 0:
            checkpoint_sync = _sync_checkpoint_dataset(
                job_id=job_id, checkpoints_dir=out_dir, first_time=not dataset_created,
                confirm=True, dry_run=False, username=username)
            dataset_created = True
        pushed: list[dict[str, Any]] = []
        for chunk_idx in range(n_kernels):
            pushed.append(_push_kernel(job_id=job_id, job_dir=job_dir, round_idx=round_idx,
                                       chunk_idx=chunk_idx, num_chunks=n_kernels, gpu=gpu,
                                       checkpoints_dir=out_dir if done > 0 else None,
                                       work_root=work_root, username=username))
        waits = [_wait_kernel(k["kernel"]) for k in pushed]
        for k in pushed:
            _fetch_kernel(k["kernel"], dest=out_dir)
        rounds.append({"round": round_idx, "kernels": [k["kernel"] for k in pushed],
                       "waits": [w["status"] for w in waits],
                       "checkpoint_sync": checkpoint_sync,
                       "units_done_after": units_done(out_dir, glob)})

    final_done = units_done(out_dir, glob)
    status_str = "completed" if final_done >= total else "incomplete_max_runs"
    return {
        "job_id": job_id, "kind": "gpu" if gpu else "cpu", "status": status_str,
        "total_units": total, "units_done": final_done, "rounds_used": len(rounds),
        "kernels_total": sum(len(r["kernels"]) for r in rounds), "concurrency": fanout,
        "max_runs": cap_runs, "out_dir": str(out_dir), "rounds": rounds,
        "gpu_reservation": gpu_reservation, "cost": "free",
    }


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

    status_p = sub.add_parser("status", help="Kernel run state")
    status_p.add_argument("kernel")

    wait_p = sub.add_parser("wait", help="Poll a kernel until it finishes or the wall cap hits")
    wait_p.add_argument("kernel")
    wait_p.add_argument("--timeout", type=float, default=None)

    fetch_p = sub.add_parser("fetch", help="Download a kernel's output (checkpoints)")
    fetch_p.add_argument("kernel")
    fetch_p.add_argument("--dest", default=None)

    run_p = sub.add_parser("run", help="Multi-run resume loop with concurrent fan-out until DONE")
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
                              checkpoints_dir=args.checkpoints, confirm=args.confirm, dry_run=args.dry_run)
            elif args.command == "status":
                result = status(kernel=args.kernel, config=config)
            elif args.command == "wait":
                result = wait(kernel=args.kernel, config=config, timeout=args.timeout)
            elif args.command == "fetch":
                result = fetch(kernel=args.kernel, config=config, dest=args.dest)
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
