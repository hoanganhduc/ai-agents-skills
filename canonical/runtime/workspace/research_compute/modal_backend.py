from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def modal_sdk_status() -> tuple[bool, str | None]:
    try:
        import modal  # noqa: F401
    except ModuleNotFoundError as exc:
        return False, str(exc)
    return True, None


def modal_cli_status() -> tuple[bool, str | None]:
    path = shutil.which("modal")
    if path:
        return True, path
    return False, None


def modal_ready_summary(config: Any, modal_config_path: Path) -> dict[str, Any]:
    sdk_ok, sdk_detail = modal_sdk_status()
    cli_ok, cli_detail = modal_cli_status()
    token_env = bool(os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"))

    config_exists = modal_config_path.exists()
    authenticated = token_env or config_exists
    return {
        "modal_sdk_available": sdk_ok,
        "modal_sdk_detail": sdk_detail,
        "modal_cli_available": cli_ok,
        "modal_cli_path": cli_detail,
        "modal_config_path": str(modal_config_path),
        "modal_config_exists": config_exists,
        "modal_tokens_in_env": token_env,
        "modal_authenticated": authenticated,
        "modal_ready": bool(sdk_ok and authenticated),
        "modal_profile": config.modal_profile,
        "modal_environment": config.modal_environment,
        "deployment_alias": config.deployment_alias,
    }


def deploy_modal_app(*, config: Any, workspace_root: Path) -> dict[str, Any]:
    cli_ok, cli_path = modal_cli_status()
    if not cli_ok:
        raise RuntimeError("Modal CLI is not installed on this host. Install it before running deploy.")

    env = os.environ.copy()
    if config.modal_profile:
        env["MODAL_PROFILE"] = config.modal_profile
    env["MODAL_ENVIRONMENT"] = config.modal_environment

    command = [
        cli_path,
        "deploy",
        "-m",
        "research_compute.modal_app",
        "--name",
        config.deployment_alias,
        "-e",
        config.modal_environment,
    ]
    result = subprocess.run(command, cwd=workspace_root, env=env, capture_output=True, text=True)
    return {
        "ok": result.returncode == 0,
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def submit_remote_job(*, job: dict[str, Any], plan: dict[str, Any], config: Any) -> dict[str, Any]:
    sdk_ok, sdk_error = modal_sdk_status()
    if not sdk_ok:
        raise RuntimeError(
            "Modal Python SDK is not installed on this host. Install `modal` to enable remote submission."
        )

    import modal

    env = os.environ.copy()
    if config.modal_profile:
        env["MODAL_PROFILE"] = config.modal_profile
        os.environ["MODAL_PROFILE"] = config.modal_profile
    os.environ["MODAL_ENVIRONMENT"] = config.modal_environment

    function_name = function_name_for_decision(plan["decision"], config)
    function = modal.Function.from_name(
        config.deployment_alias,
        function_name,
        environment_name=config.modal_environment,
    )
    function_call = function.spawn(job)
    return {
        "function_name": function_name,
        "function_call_id": function_call.object_id,
    }


def run_remote_job(*, job: dict[str, Any], plan: dict[str, Any], config: Any) -> dict[str, Any]:
    sdk_ok, _ = modal_sdk_status()
    if not sdk_ok:
        raise RuntimeError(
            "Modal Python SDK is not installed on this host. Install `modal` to enable remote execution."
        )

    import modal

    if config.modal_profile:
        os.environ["MODAL_PROFILE"] = config.modal_profile
    os.environ["MODAL_ENVIRONMENT"] = config.modal_environment

    function_name = function_name_for_decision(plan["decision"], config)
    function = modal.Function.from_name(
        config.deployment_alias,
        function_name,
        environment_name=config.modal_environment,
    )
    result_manifest = function.remote(job)
    return {
        "function_name": function_name,
        "result_manifest": result_manifest,
    }


def wait_for_result(*, function_call_id: str, timeout: float | None = None) -> dict[str, Any]:
    sdk_ok, _ = modal_sdk_status()
    if not sdk_ok:
        raise RuntimeError("Modal Python SDK is required to wait on remote function calls.")

    import modal

    call = modal.FunctionCall.from_id(function_call_id)
    return call.get(timeout=timeout)


def cancel_function_call(*, function_call_id: str) -> dict[str, Any]:
    sdk_ok, _ = modal_sdk_status()
    if not sdk_ok:
        raise RuntimeError("Modal Python SDK is required to cancel remote function calls.")

    import modal

    call = modal.FunctionCall.from_id(function_call_id)
    call.cancel()
    return {"cancelled": True, "function_call_id": function_call_id}


def function_name_for_decision(decision: str, config: Any) -> str:
    if decision == "modal_cpu":
        return config.functions.modal_cpu
    if decision == "modal_highmem_cpu":
        return config.functions.modal_highmem_cpu
    if decision == "modal_gpu":
        return config.functions.modal_gpu
    if decision == "modal_sandbox_experimental":
        return config.functions.modal_sandbox_experimental
    raise RuntimeError(f"No Modal function is defined for decision '{decision}'.")


# The capacity each Modal function is deployed with. `modal_app.py` builds its
# `@app.function(...)` decorators from this table, so a planner check made here
# cannot drift from what is actually running. This module imports only the
# standard library, so the planner can read the table offline -- `modal_app.py`
# imports the `modal` SDK and is not importable without it.
FUNCTION_CAPACITY: dict[str, dict[str, Any]] = {
    "run_cpu_job": {"cpu": 4.0, "memory_mb": 8192, "timeout": 3600, "startup_timeout": 600},
    "run_highmem_job": {"cpu": 16.0, "memory_mb": 65536, "timeout": 21600, "startup_timeout": 1200},
    "run_gpu_job": {
        "cpu": 8.0, "memory_mb": 32768, "gpu": "L4", "timeout": 21600, "startup_timeout": 1200,
    },
    "run_sandbox_job": {"cpu": 2.0, "memory_mb": 4096, "timeout": 3600, "startup_timeout": 600},
}


def capacity_for_decision(decision: str, config: Any) -> dict[str, Any]:
    """Declared capacity of the function a routing decision maps to."""
    name = function_name_for_decision(decision, config)
    capacity = FUNCTION_CAPACITY.get(name)
    if capacity is None:
        raise RuntimeError(
            f"Modal function '{name}' (decision '{decision}') has no declared capacity. "
            f"Add it to modal_backend.FUNCTION_CAPACITY."
        )
    return dict(capacity)


def capacity_fit(estimate: dict[str, Any], decision: str, config: Any) -> tuple[bool, str]:
    """Whether an estimate fits the declared capacity of the mapped Modal function.

    Modal enforces `cpu=`/`memory=` in its scheduler, outside the gVisor guest, so the
    guest's own `os.cpu_count()` and cgroup report the worker host and reveal nothing
    about the allocation. The two limits fail differently, so only one of them vetoes:

    - **memory** is a hard ceiling. A job whose peak RSS exceeds it is OOM-killed after
      boot, having already been accepted and paid for. That is a routing veto.
    - **cpu** is a reserved floor, not a cap. More workers than reserved cores
      over-subscribe and run slower; the job still completes. That is sizing
      information the caller needs in the trail, not grounds to refuse the lane.

    The API liveness probe checks neither, so adequacy has to come from here.
    """
    try:
        capacity = capacity_for_decision(decision, config)
    except RuntimeError as exc:
        return False, f"modal_capacity_unknown:{exc}"

    function = function_name_for_decision(decision, config)
    want_ram_gb = float(estimate.get("peak_ram_gb", 0.0) or 0.0)
    have_ram_gb = float(capacity["memory_mb"]) / 1024.0
    if want_ram_gb > have_ram_gb:
        return False, (
            f"modal_capacity_exceeded:ram_gb requested={want_ram_gb:g} "
            f"declared={have_ram_gb:g} function={function}"
        )

    want_cores = float(estimate.get("parallelism", 1) or 1)
    have_cores = float(capacity["cpu"])
    note = ""
    if want_cores > have_cores:
        note = f" cores_oversubscribed={want_cores:g}>{have_cores:g}"
    return True, (
        f"modal_capacity_ok:cores={have_cores:g} ram_gb={have_ram_gb:g} "
        f"function={function}{note}"
    )
