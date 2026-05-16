from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


GPU_TASK_MARKERS = (
    "gpu",
    "embedding",
    "rerank",
    "reranking",
    "vlm",
    "ocr",
    "tensor",
    "spectral",
)

CPU_HEAVY_FAMILIES = {
    "enumeration",
    "counterexample_search",
    "parameter_sweep",
    "sat_search",
    "search",
}

SUPPORTED_TEMPLATES = {
    "counterexample_search",
    "enumerate_objects",
    "parameter_sweep",
}


def normalize_job(job: dict[str, Any], *, config: Any) -> dict[str, Any]:
    normalized = dict(job)
    normalized.setdefault("job_id", make_job_id())
    normalized.setdefault("environment_name", config.modal_environment)
    normalized.setdefault("deployment_alias", config.deployment_alias)
    normalized.setdefault("template_version", "v1")
    normalized.setdefault("payload", {})
    normalized.setdefault("constraints", {})
    normalized.setdefault("policy", {})
    normalized.setdefault("provenance", {})
    return normalized


def plan_job(job: dict[str, Any], *, config: Any, resources: dict[str, Any] | None = None, modal_ready: bool = False) -> dict[str, Any]:
    task_family = str(job.get("task_family", "") or "").lower()
    task_type = str(job.get("task_type", "") or "").lower()
    template = str(job.get("template", "") or "").lower()
    payload = dict(job.get("payload", {}) or {})
    constraints = dict(job.get("constraints", {}) or {})
    policy = dict(job.get("policy", {}) or {})
    parameters = dict(payload.get("parameters", {}) or {})

    resource_class = str(constraints.get("resource_class", "") or "").lower()
    requested_gpu = constraints.get("gpu")
    allow_remote = bool(policy.get("allow_remote", True))
    allow_gpu = bool(policy.get("allow_gpu", False))
    execution_primitive = str(constraints.get("execution_primitive", "function") or "function")

    risk_flags: list[str] = []
    reasoning: list[str] = []

    if template and template not in SUPPORTED_TEMPLATES:
        risk_flags.append("template_not_yet_supported")

    if not allow_remote:
        reasoning.append("Remote execution is disabled by policy.")
        return finalize_plan(
            decision="local_cpu",
            execution_primitive=execution_primitive,
            accepted=True,
            estimated_cost_usd=0.0,
            estimated_runtime_sec=estimate_runtime_sec(parameters, constraints),
            risk_flags=risk_flags,
            required_policy_exceptions=[],
            reasoning_summary=" ".join(reasoning),
        )

    local_ram_gb = nested_get(resources, "memory", "total_gb", default=0.0)
    local_gpu_count = nested_get(resources, "gpu", "total_gpus", default=0)
    local_disk_gb = nested_get(resources, "disk", "available_gb", default=0.0)

    requested_mem_mb = int(constraints.get("memory_mb", 0) or 0)
    requested_disk_mb = int(constraints.get("ephemeral_disk_mb", 0) or 0)
    requested_cpu = float(constraints.get("cpu", 0) or 0)
    estimated_runtime_sec = estimate_runtime_sec(parameters, constraints)

    gpu_signal = bool(requested_gpu) or resource_class == "gpu" or any(marker in task_family or marker in task_type for marker in GPU_TASK_MARKERS)
    cpu_heavy_signal = task_family in CPU_HEAVY_FAMILIES or resource_class in {"cpu", "highmem_cpu"} or parameters.get("max_vertices", 0) >= 40
    highmem_signal = resource_class == "highmem_cpu" or requested_mem_mb >= 32768 or (local_ram_gb and requested_mem_mb > int(local_ram_gb * 1024 * 0.75))
    disk_pressure = bool(local_disk_gb and local_disk_gb < 5.0) or requested_disk_mb >= 65536
    heavy_signal = bool(requested_cpu >= 8 or requested_mem_mb >= 16384 or estimated_runtime_sec >= 900 or parameters.get("batch_size", 0) >= 1024)

    if disk_pressure:
        risk_flags.append("local_disk_constrained")
        reasoning.append("Local disk headroom is tight or the job requests substantial scratch space.")

    if gpu_signal:
        if allow_gpu:
            decision = "modal_gpu"
            reasoning.append("The job is explicitly GPU-suitable or requests GPU resources.")
        else:
            decision = "modal_cpu"
            risk_flags.append("gpu_requested_but_disallowed")
            reasoning.append("The job looks GPU-suitable, but GPU routing is disabled by policy.")
    elif highmem_signal or cpu_heavy_signal and (heavy_signal or disk_pressure):
        decision = "modal_highmem_cpu" if highmem_signal else "modal_cpu"
        reasoning.append("The job is a CPU-heavy search or enumeration workload better suited to remote CPU resources.")
    elif heavy_signal and (not local_gpu_count or requested_mem_mb > int(local_ram_gb * 1024 * 0.6 if local_ram_gb else 8192)):
        decision = "modal_cpu"
        reasoning.append("The job is heavy enough that remote CPU execution is safer than local execution.")
    else:
        decision = "local_cpu"
        reasoning.append("The job does not exceed the current remote-offload thresholds.")

    if execution_primitive == "sandbox" and decision.startswith("modal_"):
        decision = "modal_sandbox_experimental"
        reasoning.append("The job explicitly requested sandbox execution.")

    estimated_cost_usd = estimate_cost_usd(
        decision=decision,
        estimated_runtime_sec=estimated_runtime_sec,
        requested_cpu=requested_cpu,
        requested_mem_mb=requested_mem_mb,
    )

    budget_cap = min(
        float(policy.get("max_estimated_cost_usd", config.per_job_cost_cap_usd)),
        float(config.per_job_cost_cap_usd),
    )

    if decision.startswith("modal_") and estimated_cost_usd > budget_cap:
        return finalize_plan(
            decision="rejected",
            execution_primitive=execution_primitive,
            accepted=False,
            estimated_cost_usd=estimated_cost_usd,
            estimated_runtime_sec=estimated_runtime_sec,
            risk_flags=risk_flags + ["estimated_cost_exceeds_budget"],
            required_policy_exceptions=["budget"],
            reasoning_summary=f"Estimated cost {estimated_cost_usd:.2f} exceeds budget cap {budget_cap:.2f}.",
        )

    if decision.startswith("modal_") and not modal_ready:
        risk_flags.append("modal_not_ready_on_host")

    return finalize_plan(
        decision=decision,
        execution_primitive=execution_primitive,
        accepted=decision != "rejected",
        estimated_cost_usd=estimated_cost_usd,
        estimated_runtime_sec=estimated_runtime_sec,
        risk_flags=risk_flags,
        required_policy_exceptions=[],
        reasoning_summary=" ".join(reasoning),
    )


def finalize_plan(
    *,
    decision: str,
    execution_primitive: str,
    accepted: bool,
    estimated_cost_usd: float,
    estimated_runtime_sec: int,
    risk_flags: list[str],
    required_policy_exceptions: list[str],
    reasoning_summary: str,
) -> dict[str, Any]:
    return {
        "accepted": accepted,
        "decision": decision,
        "execution_primitive": execution_primitive,
        "estimated_cost_usd": round(estimated_cost_usd, 4),
        "estimated_runtime_sec": estimated_runtime_sec,
        "risk_flags": risk_flags,
        "required_policy_exceptions": required_policy_exceptions,
        "reasoning_summary": reasoning_summary.strip(),
    }


def estimate_runtime_sec(parameters: dict[str, Any], constraints: dict[str, Any]) -> int:
    timeout_sec = int(constraints.get("timeout_sec", 0) or 0)
    if timeout_sec:
        return max(60, int(timeout_sec * 0.25))

    size_hint = int(parameters.get("max_vertices", 0) or 0) + int(parameters.get("batch_size", 0) or 0) // 128
    return max(120, min(21600, 300 + size_hint * 30))


def estimate_cost_usd(*, decision: str, estimated_runtime_sec: int, requested_cpu: float, requested_mem_mb: int) -> float:
    hours = max(estimated_runtime_sec / 3600.0, 1 / 60.0)
    cpu_factor = max(requested_cpu, 1.0)
    mem_factor = max(requested_mem_mb / 8192.0, 1.0)

    if decision == "modal_gpu":
        base_rate = 0.80
    elif decision == "modal_highmem_cpu":
        base_rate = 0.30
    elif decision == "modal_cpu":
        base_rate = 0.12
    else:
        return 0.0

    return hours * base_rate * max(cpu_factor / 4.0, 1.0) * max(math.sqrt(mem_factor), 1.0)


def nested_get(data: dict[str, Any] | None, *keys: str, default: Any) -> Any:
    cur: Any = data or {}
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def make_job_id() -> str:
    now = datetime.now(timezone.utc)
    return f"rc_{now.strftime('%Y%m%d_%H%M%S')}"
