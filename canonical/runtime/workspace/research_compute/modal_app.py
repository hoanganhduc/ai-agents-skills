from __future__ import annotations

import os

import modal

from .template_runner import execute_manifest


APP_NAME = os.environ.get("RESEARCH_COMPUTE_APP_NAME", "research-compute-codex")
app = modal.App(APP_NAME)

base_image = modal.Image.debian_slim().pip_install("numpy", "networkx")
gpu_image = base_image.pip_install("torch")


@app.function(
    image=base_image,
    cpu=4.0,
    memory=8192,
    timeout=3600,
    startup_timeout=600,
)
def run_cpu_job(job: dict) -> dict:
    return execute_manifest(job, resource_class="cpu")


@app.function(
    image=base_image,
    cpu=16.0,
    memory=65536,
    timeout=21600,
    startup_timeout=1200,
)
def run_highmem_job(job: dict) -> dict:
    return execute_manifest(job, resource_class="highmem_cpu")


@app.function(
    image=gpu_image,
    gpu="L4",
    cpu=8.0,
    memory=32768,
    timeout=21600,
    startup_timeout=1200,
)
def run_gpu_job(job: dict) -> dict:
    return execute_manifest(job, resource_class="gpu")


@app.function(
    image=base_image,
    cpu=2.0,
    memory=4096,
    timeout=3600,
    startup_timeout=600,
)
def run_sandbox_job(job: dict) -> dict:
    raise NotImplementedError(
        "Sandbox execution is planned but not implemented in this v1 integration."
    )
