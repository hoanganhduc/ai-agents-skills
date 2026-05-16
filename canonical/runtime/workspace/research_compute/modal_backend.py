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

    return {
        "modal_sdk_available": sdk_ok,
        "modal_sdk_detail": sdk_detail,
        "modal_cli_available": cli_ok,
        "modal_cli_path": cli_detail,
        "modal_config_path": str(modal_config_path),
        "modal_config_exists": modal_config_path.exists(),
        "modal_tokens_in_env": token_env,
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
