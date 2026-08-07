from __future__ import annotations

import os

import modal

from .modal_backend import FUNCTION_CAPACITY
from .template_runner import execute_manifest


APP_NAME = os.environ.get("RESEARCH_COMPUTE_APP_NAME", "research-compute-codex")
app = modal.App(APP_NAME)

base_image = modal.Image.debian_slim().pip_install("numpy", "networkx")
gpu_image = base_image.pip_install("torch")


def _spec(function_name: str, image: modal.Image) -> dict:
    """Decorator keywords for a function, taken from the declared capacity table.

    The planner sizes jobs against `FUNCTION_CAPACITY` before dispatch, so the
    deployed limits are read from the same table rather than restated here.
    """
    capacity = dict(FUNCTION_CAPACITY[function_name])
    spec = {
        "image": image,
        "cpu": capacity["cpu"],
        "memory": capacity["memory_mb"],
        "timeout": capacity["timeout"],
        "startup_timeout": capacity["startup_timeout"],
    }
    if capacity.get("gpu"):
        spec["gpu"] = capacity["gpu"]
    return spec


@app.function(**_spec("run_cpu_job", base_image))
def run_cpu_job(job: dict) -> dict:
    return execute_manifest(job, resource_class="cpu")


@app.function(**_spec("run_highmem_job", base_image))
def run_highmem_job(job: dict) -> dict:
    return execute_manifest(job, resource_class="highmem_cpu")


@app.function(**_spec("run_gpu_job", gpu_image))
def run_gpu_job(job: dict) -> dict:
    return execute_manifest(job, resource_class="gpu")


@app.function(**_spec("run_sandbox_job", base_image))
def run_sandbox_job(job: dict) -> dict:
    raise NotImplementedError(
        "Sandbox execution is planned but not implemented in this v1 integration."
    )
