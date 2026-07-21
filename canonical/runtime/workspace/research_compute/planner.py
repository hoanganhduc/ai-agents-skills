from __future__ import annotations

import math
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

from . import github_actions_backend, hetzner_backend, kaggle_backend
from .config import DEFAULT_ROUTING_ORDER, SUPPORTED_BACKENDS, routing_order_error

# Runtime-watchdog verdicts (plan §5).
WATCH_OK = "completed"
WATCH_ABORT_FALLBACK = "ABORT_FALLBACK"


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

SUPPORTED_DATA_LOCALITIES = frozenset({"public", "private", "secret"})


def policy_boolean(policy: dict[str, Any], key: str, *, default: bool) -> tuple[bool, str | None]:
    """Read a JSON policy boolean without truthiness coercion.

    Strings such as ``"false"`` must not silently become true at a trust boundary.
    """
    value = policy.get(key, default)
    if not isinstance(value, bool):
        return default, f"policy.{key} must be a boolean"
    return value, None


def resolve_data_locality(
    constraints: dict[str, Any], policy: dict[str, Any]
) -> tuple[str, str | None]:
    """Normalize locality and conservatively merge its two supported input locations.

    Either source declaring ``secret`` wins, so a less restrictive constraint cannot
    shadow a secret policy. Non-string values are rejected instead of stringified.
    """
    normalized: list[str] = []
    for label, value in (
        ("constraints.data_locality", constraints.get("data_locality")),
        ("policy.data_locality", policy.get("data_locality")),
    ):
        if value is None:
            continue
        if not isinstance(value, str):
            return "", f"{label} must be a string"
        locality = value.strip().lower()
        if locality:
            if locality not in SUPPORTED_DATA_LOCALITIES:
                return "", (
                    f"{label} must be one of "
                    f"{sorted(SUPPORTED_DATA_LOCALITIES)}"
                )
            normalized.append(locality)
    if "secret" in normalized:
        return "secret", None
    if "private" in normalized:
        return "private", None
    return ("public" if "public" in normalized else ""), None


def explicit_gpu_request(
    constraints: dict[str, Any], policy: dict[str, Any]
) -> tuple[bool, str | None]:
    """Resolve the two explicit GPU flags without truthiness coercion."""
    requested = False
    for label, value in (
        ("constraints.gpu", constraints.get("gpu", False)),
        ("policy.gpu", policy.get("gpu", False)),
    ):
        if not isinstance(value, bool):
            return False, f"{label} must be a boolean"
        requested = requested or value
    return requested, None


def backend_override_value(policy: dict[str, Any]) -> tuple[str, str | None]:
    """Return a normalized backend hard pin; reject present non-string values."""
    if "backend" not in policy:
        return "", None
    value = policy["backend"]
    if not isinstance(value, str):
        return "", "policy.backend must be a backend-name string"
    normalized = value.strip().lower()
    if not normalized:
        return "", "policy.backend must be a non-empty backend-name string"
    return normalized, None


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


def _default_modal_liveness_probe(config: Any) -> tuple[bool, str]:  # pragma: no cover - live CLI path
    """Authenticated, read-only Modal API liveness check.

    Listing apps proves the configured credentials, profile, environment, and API are usable
    without spawning paid work. Provider-side spend refusal can still occur later, so the
    planner also enforces its local per-job USD estimate cap.
    """
    from .modal_backend import modal_cli_status

    cli_ok, cli_path = modal_cli_status()
    if not cli_ok:
        return False, "modal_cli_unavailable"
    env = os.environ.copy()
    profile = getattr(config, "modal_profile", None)
    if profile:
        env["MODAL_PROFILE"] = str(profile)
    environment = str(getattr(config, "modal_environment", "main") or "main")
    try:
        result = subprocess.run(
            [cli_path, "app", "list", "--json", "-e", environment],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"modal_liveness_failed:{exc.__class__.__name__}"
    if result.returncode != 0:
        return False, "modal_auth_or_api_unavailable"
    return True, "modal_authenticated_api_usable"


# Tests replace this hook (in-process) so the real check never runs offline.
MODAL_LIVENESS_PROBE = _default_modal_liveness_probe


def modal_lane_available(config: Any, resources: dict[str, Any] | None,
                         modal_ready: bool) -> tuple[bool, str]:
    """Modal lane availability = SDK/config readiness AND authenticated API liveness.
    Injection-first (resources['liveness']['modal'] with optional 'ready'/'usable' keys) so
    offline tests are deterministic and host-independent; otherwise readiness plus the
    MODAL_LIVENESS_PROBE hook. Returns (available, reason)."""
    injected = nested_get(resources, "liveness", "modal", default=None)
    if isinstance(injected, dict):
        if "usable" not in injected:
            return False, "modal_liveness_snapshot_incomplete"
        ready = injected.get("ready", modal_ready)
        usable = injected["usable"]
        if not isinstance(ready, bool) or not isinstance(usable, bool):
            return False, "modal_liveness_snapshot_must_use_booleans"
        reason = str(injected.get("reason") or ("available" if (ready and usable) else "modal_unavailable"))
        return (ready and usable), reason
    if not modal_ready:
        return False, "modal_not_ready_on_host"
    usable, reason = MODAL_LIVENESS_PROBE(config)
    return bool(usable), str(reason)


def select_remote_lane(*, order: list[str], modal_decision: str, gpu_signal: bool,
                       hz: Any, hz_in_order: bool,
                       kg: Any = None, kg_in_order: bool = False,
                       modal_ok: Any,
                       gha_ok: Any) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    """Order-driven cascade over routing_order for the first AVAILABLE + ADEQUATE REMOTE
    lane (the caller owns the local lane). The walk is driven entirely by `order`, so it
    stays correct if the routing priority is reordered. Kaggle, Hetzner, and GitHub Actions
    are CPU lanes here (GPU work goes through select_gpu_lane). Kaggle sits ahead of the paid
    lanes because its CPU compute is free and quota-free. Provider arguments may be cached
    zero-argument guards; each is resolved only when the walk reaches that lane. Returns
    (decision | None, backend | None, trail)."""
    trail: list[dict[str, Any]] = []
    for backend in order:
        if backend == "local":
            continue
        if backend == "kaggle":
            if not kg_in_order:
                continue
            kg_result = kg() if callable(kg) else kg
            adequate = bool(kg_result.get("adequate")) and not gpu_signal
            available = bool(kg_result.get("available"))
            reason = "gpu_out_of_scope_for_cpu_cascade" if gpu_signal else str(kg_result.get("reason", ""))
            trail.append({"backend": "kaggle", "available": available, "adequate": adequate, "reason": reason})
            if available and adequate:
                return "kaggle", "kaggle", trail
        elif backend == "modal":
            ok, reason = modal_ok() if callable(modal_ok) else modal_ok
            trail.append({"backend": "modal", "available": bool(ok), "adequate": True, "reason": reason})
            if ok:
                return modal_decision, "modal", trail
        elif backend == "hetzner":
            if not hz_in_order:
                continue
            hz_result = hz() if callable(hz) else hz
            adequate = bool(hz_result.get("adequate")) and not gpu_signal
            available = bool(hz_result.get("available"))
            reason = "gpu_out_of_scope_v1" if gpu_signal else str(hz_result.get("reason", ""))
            trail.append({"backend": "hetzner", "available": available, "adequate": adequate, "reason": reason})
            if available and adequate:
                return "hetzner", "hetzner", trail
        elif backend == "gha":
            ok, reason = gha_ok()
            adequate = not gpu_signal and not str(reason).startswith("inadequate:")
            trail.append({"backend": "gha", "available": bool(ok) and adequate,
                          "adequate": adequate, "reason": reason})
            if ok and adequate:
                return "gha", "gha", trail
    return None, None, trail


def select_gpu_lane(*, order: list[str], local_gpu: bool, config: Any,
                    kg: Any = None, kg_in_order: bool = False,
                    modal_ok: Any,
                    gha_ok: Any) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    """Order-driven cascade over routing_order for the first GPU-CAPABLE + AVAILABLE lane
    (plan §5.1). A GPU-requested job (auto-signalled OR explicitly requested) must land on a
    GPU-capable lane; the walk follows `order`, so it stays correct if the priority is
    reordered, and it is cheapest-GPU-first by construction (local -> Kaggle -> Modal by
    default).

    Per-lane GPU capability (grounded, verified 2026):
      - local   -- GPU-capable only if the resource snapshot shows a local GPU.
      - kaggle  -- GPU-capable and FREE, within a self-imposed weekly GPU-hour cap (12h
                   sessions). `kg` carries the probe verdict (available already folds in the
                   weekly-cap check); GPU-quota-exhausted => unavailable => fall through.
      - modal   -- always GPU-capable (on-demand serverless GPU; the paid GPU workhorse).
      - hetzner -- NEVER: Hetzner Cloud has no on-demand GPU, so a GPU job always skips it.
      - gha     -- GPU only via PAID "larger runners" (Team/Enterprise, not free minutes, not
                   public repos): opt-in via ``gha.gpu_enabled`` (OFF by default). When on,
                   GHA is GPU-capable but still gated by the cumulative Actions-minutes cap
                   (`gha_ok`).

    Provider arguments may be cached zero-argument guards, so a lane probe runs only if the
    cascade reaches it. Returns (decision | None, backend | None, trail); decision is None
    when no GPU lane is available, so the caller rejects."""
    trail: list[dict[str, Any]] = []
    for backend in order:
        if backend == "local":
            trail.append({"backend": "local", "gpu_capable": local_gpu, "available": local_gpu,
                          "adequate": local_gpu,
                          "reason": "local_gpu_present" if local_gpu else "no_local_gpu"})
            if local_gpu:
                return "local_gpu", "local", trail
        elif backend == "kaggle":
            if not kg_in_order:
                continue
            # Kaggle offers free GPU kernels; `kg.available` already folds in the weekly
            # GPU-hour self-cap, so an exhausted quota makes the lane unavailable here.
            kg_result = kg() if callable(kg) else kg
            available = bool(kg_result.get("available"))
            trail.append({"backend": "kaggle", "gpu_capable": True, "available": available,
                          "adequate": available, "reason": str(kg_result.get("reason", ""))})
            if available:
                return "kaggle_gpu", "kaggle", trail
        elif backend == "modal":
            ok, reason = modal_ok() if callable(modal_ok) else modal_ok
            trail.append({"backend": "modal", "gpu_capable": True, "available": bool(ok),
                          "adequate": True, "reason": reason})
            if ok:
                return "modal_gpu", "modal", trail
        elif backend == "hetzner":
            # No on-demand GPU on Hetzner Cloud -> GPU-inadequate; always skipped.
            trail.append({"backend": "hetzner", "gpu_capable": False, "available": False,
                          "adequate": False, "reason": "hetzner_no_gpu"})
        elif backend == "gha":
            gpu_enabled = bool(getattr(config, "gha_gpu_enabled", False))
            if not gpu_enabled:
                trail.append({"backend": "gha", "gpu_capable": False, "available": False,
                              "adequate": False, "reason": "gha_gpu_disabled"})
                continue
            ok, reason = gha_ok()
            adequate = not str(reason).startswith("inadequate:")
            trail.append({"backend": "gha", "gpu_capable": True,
                          "available": bool(ok) and adequate,
                          "adequate": adequate, "reason": reason})
            if ok and adequate:
                return "gha", "gha", trail
        # Any other backend name is ignored until wired above.
    return None, None, trail


def plan_job(
    job: dict[str, Any],
    *,
    config: Any,
    resources: dict[str, Any] | None = None,
    modal_ready: bool = False,
    state_root: Any = None,
) -> dict[str, Any]:
    task_family = str(job.get("task_family", "") or "").lower()
    task_type = str(job.get("task_type", "") or "").lower()
    template = str(job.get("template", "") or "").lower()
    payload = dict(job.get("payload", {}) or {})
    constraints = dict(job.get("constraints", {}) or {})
    policy = dict(job.get("policy", {}) or {})
    parameters = dict(payload.get("parameters", {}) or {})

    resource_class = str(constraints.get("resource_class", "") or "").lower()
    allow_remote, allow_remote_error = policy_boolean(
        policy, "allow_remote", default=True
    )
    data_locality, data_locality_error = resolve_data_locality(constraints, policy)
    explicit_gpu, explicit_gpu_error = explicit_gpu_request(constraints, policy)
    execution_primitive = str(constraints.get("execution_primitive", "function") or "function")

    risk_flags: list[str] = []
    reasoning: list[str] = []

    # --- backend routing: explicit override > configured automatic order ---
    backend_override, backend_override_error = backend_override_value(policy)
    gha_target = str(job.get("gha_target", "") or template or "")
    gha_repos = dict(getattr(config, "gha_repos", {}) or {})
    gha_registered = bool(getattr(config, "gha_enabled", False)) and gha_target in gha_repos
    runtime_sec = estimate_runtime_sec(parameters, constraints)

    local_gpu_count = nested_get(resources, "gpu", "total_gpus", default=0)
    auto_gpu_signal = resource_class == "gpu" or any(
        marker in task_family or marker in task_type for marker in GPU_TASK_MARKERS
    )
    gpu_requested = auto_gpu_signal or explicit_gpu

    if allow_remote_error:
        return finalize_plan(
            decision="rejected",
            execution_primitive=execution_primitive,
            accepted=False,
            estimated_cost_usd=0.0,
            estimated_runtime_sec=runtime_sec,
            risk_flags=["invalid_allow_remote_policy"],
            required_policy_exceptions=[],
            reasoning_summary=allow_remote_error,
        )

    if data_locality_error:
        return finalize_plan(
            decision="rejected",
            execution_primitive=execution_primitive,
            accepted=False,
            estimated_cost_usd=0.0,
            estimated_runtime_sec=runtime_sec,
            risk_flags=["invalid_data_locality"],
            required_policy_exceptions=[],
            reasoning_summary=data_locality_error,
        )

    if explicit_gpu_error:
        return finalize_plan(
            decision="rejected",
            execution_primitive=execution_primitive,
            accepted=False,
            estimated_cost_usd=0.0,
            estimated_runtime_sec=runtime_sec,
            risk_flags=["invalid_gpu_request"],
            required_policy_exceptions=[],
            reasoning_summary=explicit_gpu_error,
        )

    if backend_override_error:
        return finalize_plan(
            decision="rejected",
            execution_primitive=execution_primitive,
            accepted=False,
            estimated_cost_usd=0.0,
            estimated_runtime_sec=runtime_sec,
            risk_flags=["invalid_backend_override"],
            required_policy_exceptions=[],
            reasoning_summary=backend_override_error,
        )

    local_estimate = build_estimate(
        parameters=parameters,
        constraints=constraints,
        policy=policy,
        gpu_signal=gpu_requested,
        runtime_sec=runtime_sec,
        data_locality=data_locality,
    )

    def constrained_local_capacity() -> tuple[dict[str, Any], int]:
        veto = local_self_preservation_probe(
            local_estimate, config=config, resources=resources
        )
        workers = int(veto["w_eff"] if veto["adequate"] else max(veto["w_safe"], 0))
        return veto, workers

    if backend_override and backend_override not in SUPPORTED_BACKENDS:
        return finalize_plan(
            decision="rejected",
            execution_primitive=execution_primitive,
            accepted=False,
            estimated_cost_usd=0.0,
            estimated_runtime_sec=runtime_sec,
            risk_flags=["invalid_backend_override"],
            required_policy_exceptions=[],
            reasoning_summary=(
                f"Unsupported policy.backend '{backend_override}'; expected one of "
                f"{list(DEFAULT_ROUTING_ORDER)}."
            ),
        )

    if backend_override == "local":
        # Forced-local self-preservation (plan section 6; Phase A deviation 3): an explicit
        # local override skips the PRE-LAUNCH veto, but the RUNTIME load-watchdog stays armed
        # and non-negotiable. On a hard load breach it checkpoints and aborts; the explicit
        # backend pin forbids remote fallback. watchdog_armed tells the executor to wrap the
        # run in run_local_watched.
        secret_local = data_locality == "secret"
        remote_fallback_allowed = False
        fallback_note = (
            "On a load breach it aborts without remote fallback because the data is secret."
            if secret_local
            else "On a load breach it checkpoints and aborts without remote fallback "
            "because the explicit local backend is a hard pin."
        )
        if gpu_requested and not local_gpu_count:
            return finalize_plan(
                decision="rejected",
                execution_primitive=execution_primitive,
                accepted=False,
                estimated_cost_usd=0.0,
                estimated_runtime_sec=runtime_sec,
                risk_flags=["forced_local_gpu_unavailable"],
                required_policy_exceptions=[],
                reasoning_summary=(
                    "Explicit local GPU execution was requested, but no local GPU is "
                    "available; the request cannot be silently downgraded to CPU."
                ),
                extra={"remote_fallback_allowed": False},
            )
        local_veto, forced_workers = constrained_local_capacity()
        if forced_workers < 1:
            return finalize_plan(
                decision="rejected",
                execution_primitive=execution_primitive,
                accepted=False,
                estimated_cost_usd=0.0,
                estimated_runtime_sec=runtime_sec,
                risk_flags=["forced_local_load_unsafe"],
                required_policy_exceptions=[],
                reasoning_summary=(
                    "Explicit local execution is hard-pinned, but no worker is safe under "
                    f"the current load boundary: {local_veto['reason']}."
                ),
                extra={"remote_fallback_allowed": False,
                       "routing_trail": [local_veto]},
            )
        local_risks = list(risk_flags)
        if not local_veto["adequate"]:
            local_risks.extend(["local_self_preservation_veto", "local_over_wall_budget"])
        local_decision = "local_gpu" if gpu_requested else "local_cpu"
        return finalize_plan(decision=local_decision, execution_primitive=execution_primitive,
            accepted=True, estimated_cost_usd=0.0, estimated_runtime_sec=runtime_sec,
            risk_flags=local_risks, required_policy_exceptions=[],
            reasoning_summary=("Explicit override: run locally. The runtime load-watchdog stays "
                               f"armed. {fallback_note}"),
            extra={"backend": "local", "forced_local": True, "watchdog_armed": True,
                   "remote_fallback_allowed": remote_fallback_allowed,
                   "gpu": gpu_requested,
                   "local_workers": forced_workers,
                   "w_safe": local_veto["w_safe"],
                   "w_needed": local_veto["w_needed"],
                   "routing_trail": [local_veto]})

    if not allow_remote:
        if backend_override:
            return finalize_plan(
                decision="rejected",
                execution_primitive=execution_primitive,
                accepted=False,
                estimated_cost_usd=0.0,
                estimated_runtime_sec=runtime_sec,
                risk_flags=["remote_override_forbidden"],
                required_policy_exceptions=[],
                reasoning_summary=(
                    f"policy.backend='{backend_override}' conflicts with allow_remote=false."
                ),
            )
        if gpu_requested and not local_gpu_count:
            return finalize_plan(
                decision="rejected",
                execution_primitive=execution_primitive,
                accepted=False,
                estimated_cost_usd=0.0,
                estimated_runtime_sec=runtime_sec,
                risk_flags=["remote_disabled_gpu_requires_local_gpu"],
                required_policy_exceptions=[],
                reasoning_summary=(
                    "Remote execution is disabled, GPU execution was requested, and no "
                    "local GPU is available; the request cannot be silently run on CPU."
                ),
                extra={"remote_fallback_allowed": False},
            )
        local_veto, forced_workers = constrained_local_capacity()
        if forced_workers < 1:
            return finalize_plan(
                decision="rejected",
                execution_primitive=execution_primitive,
                accepted=False,
                estimated_cost_usd=0.0,
                estimated_runtime_sec=runtime_sec,
                risk_flags=["remote_disabled_local_unsafe"],
                required_policy_exceptions=[],
                reasoning_summary=(
                    "Remote execution is disabled and no local worker is safe under the "
                    f"current load boundary: {local_veto['reason']}."
                ),
                extra={"remote_fallback_allowed": False,
                       "routing_trail": [local_veto]},
            )
        local_risks = list(risk_flags)
        if not local_veto["adequate"]:
            local_risks.extend(["local_self_preservation_veto", "local_over_wall_budget"])
        local_decision = "local_gpu" if gpu_requested else "local_cpu"
        return finalize_plan(
            decision=local_decision,
            execution_primitive=execution_primitive,
            accepted=True,
            estimated_cost_usd=0.0,
            estimated_runtime_sec=runtime_sec,
            risk_flags=local_risks,
            required_policy_exceptions=[],
            reasoning_summary=(
                "Remote execution is disabled by policy; run locally with the runtime "
                "load-watchdog armed and no remote fallback."
            ),
            extra={
                "backend": "local",
                "forced_local": True,
                "watchdog_armed": True,
                "remote_fallback_allowed": False,
                "gpu": gpu_requested,
                "local_workers": forced_workers,
                "w_safe": local_veto["w_safe"],
                "w_needed": local_veto["w_needed"],
                "routing_trail": [local_veto],
            },
        )

    if data_locality == "secret" and backend_override:
        return finalize_plan(
            decision="rejected",
            execution_primitive=execution_primitive,
            accepted=False,
            estimated_cost_usd=0.0,
            estimated_runtime_sec=runtime_sec,
            risk_flags=["secret_remote_override_forbidden"],
            required_policy_exceptions=[],
            reasoning_summary=(
                f"Secret-locality data cannot use the explicit remote backend "
                f"'{backend_override}'."
            ),
        )

    def gha_ok() -> tuple[bool, str]:
        # Lazy for automatic routing; explicit GHA calls it immediately. The usage check is
        # part of lane admission, not merely a submit-time accounting reservation.
        if not gha_registered:
            return False, "gha_not_registered"
        repo_cfg = dict(gha_repos.get(gha_target, {}))
        gha_constraints = dict(constraints)
        # build_estimate applies the broker's declared-work precedence
        # (constraints.core_hours, then payload.parameters.core_hours, then runtime).
        # Carry that normalized value into GHA admission so moving core_hours between the
        # two supported manifest locations cannot bypass the workflow timeout guard.
        gha_constraints["core_hours"] = local_estimate["core_hours"]
        adequate, adequacy_reason = github_actions_backend.job_adequacy(
            repo_cfg, gha_constraints
        )
        if not adequate:
            return False, f"inadequate:{adequacy_reason}"
        ready, ready_reason = github_actions_backend.repo_ready(repo_cfg, resources)
        if not ready:
            return False, ready_reason
        cells = int(constraints.get("matrix_cells", 1) or 1)
        ok, detail = github_actions_backend.usage_cap_ok(
            repo_cfg=repo_cfg,
            config=config,
            cells=cells,
            resources=resources,
            state_root=state_root,
        )
        return ok, str(detail.get("reason", "gha"))

    if backend_override == "gha":
        if not gha_registered:
            return finalize_plan(decision="rejected", execution_primitive=execution_primitive,
                accepted=False, estimated_cost_usd=0.0, estimated_runtime_sec=runtime_sec,
                risk_flags=risk_flags + ["gha_target_not_registered"],
                required_policy_exceptions=["gha_registration"],
                reasoning_summary=f"Explicit gha requested but target '{gha_target}' is not registered/enabled.")
        if gpu_requested and not bool(getattr(config, "gha_gpu_enabled", False)):
            return finalize_plan(
                decision="rejected",
                execution_primitive=execution_primitive,
                accepted=False,
                estimated_cost_usd=0.0,
                estimated_runtime_sec=runtime_sec,
                risk_flags=["gha_gpu_disabled"],
                required_policy_exceptions=[],
                reasoning_summary=(
                    "Explicit GHA GPU execution was requested, but paid GPU larger runners "
                    "are not enabled for this broker."
                ),
            )
        gha_available, gha_reason = gha_ok()
        if not gha_available:
            gha_risk = (
                "gha_inadequate"
                if gha_reason.startswith("inadequate:")
                else "gha_unavailable"
            )
            return finalize_plan(
                decision="rejected",
                execution_primitive=execution_primitive,
                accepted=False,
                estimated_cost_usd=0.0,
                estimated_runtime_sec=runtime_sec,
                risk_flags=[gha_risk],
                required_policy_exceptions=[],
                reasoning_summary=(
                    f"Explicit GHA requested but its lane guard failed: {gha_reason}."
                ),
                extra={"routing_trail": [{
                    "backend": "gha",
                    "available": False,
                    "adequate": not gha_reason.startswith("inadequate:"),
                    "reason": gha_reason,
                }]},
            )
        return finalize_plan(decision="gha", execution_primitive=execution_primitive,
            accepted=True, estimated_cost_usd=0.0, estimated_runtime_sec=runtime_sec,
            risk_flags=risk_flags, required_policy_exceptions=[],
            reasoning_summary=(
                f"Explicit override: GitHub Actions (target '{gha_target}'); the cumulative "
                "minutes-cap guard passed and the submit-time reservation still applies."
            ),
            extra={"backend": "gha", "gpu": gpu_requested,
                   "routing_trail": [{"backend": "gha", "available": True,
                                      "adequate": True, "reason": gha_reason}]})

    if backend_override == "hetzner" and gpu_requested:
        return finalize_plan(
            decision="rejected",
            execution_primitive=execution_primitive,
            accepted=False,
            estimated_cost_usd=0.0,
            estimated_runtime_sec=runtime_sec,
            risk_flags=["hetzner_gpu_unsupported"],
            required_policy_exceptions=[],
            reasoning_summary=(
                "Explicit Hetzner GPU execution was requested, but Hetzner Cloud has no "
                "on-demand GPU lane; the request cannot be silently downgraded to CPU."
            ),
        )

    if template and template not in SUPPORTED_TEMPLATES:
        risk_flags.append("template_not_yet_supported")

    local_ram_gb = nested_get(resources, "memory", "total_gb", default=0.0)
    local_disk_gb = nested_get(resources, "disk", "available_gb", default=0.0)

    requested_mem_mb = int(constraints.get("memory_mb", 0) or 0)
    requested_disk_mb = int(constraints.get("ephemeral_disk_mb", 0) or 0)
    requested_cpu = float(constraints.get("cpu", 0) or 0)
    estimated_runtime_sec = runtime_sec

    # GPU trigger (plan §5.1): gpu_requested = auto_gpu_signal OR an explicit request.
    # auto_gpu_signal is inferred from the job estimate -- a GPU task-family/type marker or an
    # explicit gpu resource_class. The explicit request is policy.gpu (or the equivalent job
    # constraint constraints.gpu). Because the trigger is a disjunction, an EXPLICIT request
    # ALWAYS wins: it forces a GPU lane even when auto-detection would classify the job as CPU.
    cpu_heavy_signal = task_family in CPU_HEAVY_FAMILIES or resource_class in {"cpu", "highmem_cpu"} or parameters.get("max_vertices", 0) >= 40
    highmem_signal = resource_class == "highmem_cpu" or requested_mem_mb >= 32768 or (local_ram_gb and requested_mem_mb > int(local_ram_gb * 1024 * 0.75))
    disk_pressure = bool(local_disk_gb and local_disk_gb < 5.0) or requested_disk_mb >= 65536
    heavy_signal = bool(requested_cpu >= 8 or requested_mem_mb >= 16384 or estimated_runtime_sec >= 900 or parameters.get("batch_size", 0) >= 1024)

    if disk_pressure:
        risk_flags.append("local_disk_constrained")
        reasoning.append("Local disk headroom is tight or the job requests substantial scratch space.")

    if gpu_requested:
        # Seed a Modal-GPU decision; the GPU cascade in the routing section below walks
        # routing_order and overrides this with the first GPU-capable + available lane
        # (local_gpu / kaggle_gpu / modal_gpu / gha), or rejects if no GPU lane is available.
        decision = "modal_gpu"
        reasoning.append("The job is GPU-requested (auto-signalled from the estimate or "
                         "explicitly requested); routing to a GPU-capable lane.")
    elif highmem_signal or cpu_heavy_signal and (heavy_signal or disk_pressure):
        decision = "modal_highmem_cpu" if highmem_signal else "modal_cpu"
        reasoning.append("The job is a CPU-heavy search or enumeration workload better suited to remote CPU resources.")
    elif heavy_signal and (not local_gpu_count or requested_mem_mb > int(local_ram_gb * 1024 * 0.6 if local_ram_gb else 8192)):
        decision = "modal_cpu"
        reasoning.append("The job is heavy enough that remote CPU execution is safer than local execution.")
    else:
        decision = "local_cpu"
        reasoning.append("The job does not exceed the current remote-offload thresholds.")

    # --- unified router: routing_order local > kaggle > modal > hetzner > gha, with the
    # local self-preservation veto (plan §5). The CPU remote-offload branches below walk
    # routing_order through the order-driven `select_remote_lane` cascade, and GPU-requested
    # jobs walk it through `select_gpu_lane` (plan §5.1), so the priority stays correct if
    # routing_order is reordered. Each lane's availability is an account-usable liveness check
    # (plan §6.1): Kaggle (credentials valid; CPU free/quota-free, GPU under a weekly GPU-hour
    # self-cap), Modal (authenticated API liveness), Hetzner (token valid + API reachable
    # + not payment-blocked), GHA (cumulative minutes cap). Kaggle sits right behind local
    # because its CPU compute is free. Hetzner has no on-demand GPU, so a GPU job always skips
    # it; GPU on GHA is opt-in (gha.gpu_enabled, off by default).
    configured_order = getattr(config, "routing_order", DEFAULT_ROUTING_ORDER)
    configured_order_error = routing_order_error(configured_order)
    order = (
        list(configured_order)
        if not configured_order_error
        else []
    )
    estimate = build_estimate(
        parameters=parameters,
        constraints=constraints,
        policy=policy,
        gpu_signal=gpu_requested,
        runtime_sec=estimated_runtime_sec,
        data_locality=data_locality,
    )

    # Secret-locality is a global trust boundary, not merely a condition on one local-veto
    # branch. It is resolved before any remote probe so credentials, liveness calls, and
    # dispatch decisions cannot observe or move a secret job.
    if data_locality == "secret":
        if gpu_requested:
            if local_gpu_count:
                local_veto, forced_workers = constrained_local_capacity()
                if forced_workers < 1:
                    return finalize_plan(
                        decision="rejected",
                        execution_primitive=execution_primitive,
                        accepted=False,
                        estimated_cost_usd=0.0,
                        estimated_runtime_sec=estimated_runtime_sec,
                        risk_flags=risk_flags + [
                            "local_self_preservation_veto",
                            "unfallable_secret_local_unsafe",
                        ],
                        required_policy_exceptions=[],
                        reasoning_summary=(
                            "Secret-locality GPU work cannot offload, and no local worker "
                            "is safe under the current load boundary."
                        ),
                        extra={
                            "remote_fallback_allowed": False,
                            "routing_trail": [local_veto],
                        },
                    )
                local_risks = list(risk_flags)
                if not local_veto["adequate"]:
                    local_risks.extend([
                        "local_self_preservation_veto", "local_over_wall_budget"
                    ])
                return finalize_plan(
                    decision="local_gpu",
                    execution_primitive=execution_primitive,
                    accepted=True,
                    estimated_cost_usd=0.0,
                    estimated_runtime_sec=estimated_runtime_sec,
                    risk_flags=local_risks,
                    required_policy_exceptions=[],
                    reasoning_summary=(
                        "Secret-locality data is constrained to the available local GPU "
                        f"at {forced_workers} safe worker(s); remote fallback is forbidden."
                    ),
                    extra={
                        "backend": "local",
                        "gpu": True,
                        "local_workers": forced_workers,
                        "w_safe": local_veto["w_safe"],
                        "w_needed": local_veto["w_needed"],
                        "remote_fallback_allowed": False,
                        "routing_trail": [local_veto],
                    },
                )
            return finalize_plan(
                decision="rejected",
                execution_primitive=execution_primitive,
                accepted=False,
                estimated_cost_usd=0.0,
                estimated_runtime_sec=estimated_runtime_sec,
                risk_flags=risk_flags + ["secret_gpu_requires_local_gpu"],
                required_policy_exceptions=[],
                reasoning_summary=(
                    "Secret-locality GPU work cannot offload and no local GPU is available."
                ),
            )

        veto = local_self_preservation_probe(estimate, config=config, resources=resources)
        trail = [{key: veto[key] for key in (
            "backend", "available", "adequate", "w_safe", "w_needed", "w_eff", "reason"
        )}]
        if veto["adequate"]:
            return finalize_plan(
                decision="local_cpu",
                execution_primitive=execution_primitive,
                accepted=True,
                estimated_cost_usd=0.0,
                estimated_runtime_sec=estimated_runtime_sec,
                risk_flags=risk_flags,
                required_policy_exceptions=[],
                reasoning_summary=(
                    "Secret-locality data is constrained to safe local CPU execution; "
                    "remote fallback is forbidden. " + str(veto["reason"])
                ),
                extra={
                    "backend": "local",
                    "local_workers": veto["w_eff"],
                    "w_safe": veto["w_safe"],
                    "w_needed": veto["w_needed"],
                    "remote_fallback_allowed": False,
                    "routing_trail": trail,
                },
            )
        if veto["w_safe"] >= 1:
            return finalize_plan(
                decision="local_cpu",
                execution_primitive=execution_primitive,
                accepted=True,
                estimated_cost_usd=0.0,
                estimated_runtime_sec=estimated_runtime_sec,
                risk_flags=risk_flags + [
                    "local_self_preservation_veto", "local_over_wall_budget"
                ],
                required_policy_exceptions=[],
                reasoning_summary=(
                    "Secret-locality data cannot offload; running throttled-local beyond "
                    f"the wall budget at {veto['w_safe']} worker(s)."
                ),
                extra={
                    "backend": "local",
                    "local_workers": veto["w_safe"],
                    "w_safe": veto["w_safe"],
                    "w_needed": veto["w_needed"],
                    "remote_fallback_allowed": False,
                    "routing_trail": trail,
                },
            )
        return finalize_plan(
            decision="rejected",
            execution_primitive=execution_primitive,
            accepted=False,
            estimated_cost_usd=0.0,
            estimated_runtime_sec=estimated_runtime_sec,
            risk_flags=risk_flags + [
                "local_self_preservation_veto", "unfallable_secret_local_unsafe"
            ],
            required_policy_exceptions=[],
            reasoning_summary=(
                "Secret-locality data cannot offload and local execution is load-unsafe."
            ),
            extra={"remote_fallback_allowed": False, "routing_trail": trail},
        )

    if not backend_override:
        if configured_order_error:
            return finalize_plan(
                decision="rejected",
                execution_primitive=execution_primitive,
                accepted=False,
                estimated_cost_usd=0.0,
                estimated_runtime_sec=estimated_runtime_sec,
                risk_flags=risk_flags + ["invalid_routing_order"],
                required_policy_exceptions=[],
                reasoning_summary=f"Invalid routing_order: {configured_order_error}.",
            )

    hetzner_in_order = "hetzner" in order
    kaggle_in_order = "kaggle" in order
    modal_in_order = "modal" in order
    automatic_routing = not backend_override
    hz = {"backend": "hetzner", "available": False, "adequate": False,
          "server_spec": None, "est_cost": 0.0, "est_wall_h": 0.0,
          "within_auto_approve": False, "reason": "hetzner_not_in_routing_order"}
    kg = {"backend": "kaggle", "available": False, "adequate": False,
          "reason": "kaggle_not_in_routing_order"}
    modal_ok = (False, "modal_not_selected")
    hz_loaded = False
    kg_loaded = False
    modal_loaded = False

    def get_hetzner() -> dict[str, Any]:
        nonlocal hz, hz_loaded
        if not hz_loaded:
            if backend_override == "hetzner" or (automatic_routing and hetzner_in_order):
                hz = hetzner_backend.probe(
                    estimate, config=config, resources=resources, state_root=state_root
                )
            hz_loaded = True
        return hz

    def get_kaggle() -> dict[str, Any]:
        nonlocal kg, kg_loaded
        if not kg_loaded:
            if backend_override == "kaggle" or (automatic_routing and kaggle_in_order):
                kg = kaggle_backend.probe(
                    estimate, config=config, resources=resources, state_root=state_root
                )
            kg_loaded = True
        return kg

    def get_modal() -> tuple[bool, str]:
        nonlocal modal_ok, modal_loaded
        if not modal_loaded:
            if backend_override == "modal" or (automatic_routing and modal_in_order):
                modal_ok = modal_lane_available(config, resources, modal_ready)
            modal_loaded = True
        return modal_ok

    routing_extra: dict[str, Any] = {}
    routing_trail: list[dict[str, Any]] = []

    def _attach_hetzner() -> None:
        spec = hz.get("server_spec") or {}
        routing_extra.update({
            "backend": "hetzner",
            "server_type": spec.get("name"),
            "server_vcpu": spec.get("vcpu"),
            "server_arch": spec.get("arch"),
            "estimated_cost_eur": hz.get("est_cost"),
            "estimated_wall_hours": hz.get("est_wall_h"),
            "budget_unit": "eur",
            "within_auto_approve": hz.get("within_auto_approve"),
        })

    def _attach_kaggle() -> None:
        routing_extra.update({
            "backend": "kaggle",
            "kernel_runs": kg.get("est_runs"),
            "kernel_count": kg.get("est_kernels"),
            "concurrency": kg.get("concurrency"),
            "session_hours": kg.get("session_hours"),
            "budget_unit": "free",
            "gpu_hours_est": kg.get("gpu_hours_est"),
            "gpu_hours_cap": kg.get("gpu_hours_cap"),
            "within_weekly_gpu_cap": kg.get("within_gpu_cap"),
        })

    def _apply_lane(chosen: str | None, backend_name: str | None, *, context: str) -> bool:
        """Commit the cascade's chosen lane to `decision` + reasoning. Returns whether a
        lane was chosen."""
        nonlocal decision
        if chosen is None:
            return False
        decision = chosen
        if backend_name == "kaggle":
            _attach_kaggle()
            reasoning.append(f"{context}: offloading free CPU work to Kaggle.")
        elif backend_name == "hetzner":
            _attach_hetzner()
            reasoning.append(f"{context}: offloading CPU work to Hetzner.")
        elif backend_name == "modal":
            reasoning.append(f"{context}: offloading to Modal.")
        else:  # gha
            reasoning.append(f"{context}: routing to GitHub Actions.")
        return True

    if backend_override == "kaggle":
        kg = get_kaggle()
        routing_trail.append({"backend": "kaggle", "available": kg["available"],
                              "adequate": kg["adequate"], "reason": kg["reason"]})
        if kg["available"] and kg["adequate"]:
            decision = "kaggle_gpu" if gpu_requested else "kaggle"
            _attach_kaggle()
            reasoning.append(
                "Explicit override: Kaggle GPU offload."
                if gpu_requested
                else "Explicit override: Kaggle CPU offload."
            )
        else:
            decision = "rejected"
            risk_flags.append("kaggle_unavailable")
            reasoning.append(
                f"Explicit kaggle requested but unavailable or inadequate: {kg['reason']}."
            )
    elif backend_override == "hetzner":
        hz = get_hetzner()
        routing_trail.append({"backend": "hetzner", "available": hz["available"],
                              "adequate": hz["adequate"], "reason": hz["reason"]})
        if hz["available"] and hz["adequate"]:
            decision = "hetzner"
            _attach_hetzner()
            reasoning.append("Explicit override: Hetzner Cloud (CPU offload).")
        else:
            decision = "rejected"
            risk_flags.append("hetzner_unavailable")
            reasoning.append(f"Explicit hetzner requested but unavailable: {hz['reason']}.")
    elif backend_override == "modal":
        modal_available, modal_reason = get_modal()
        routing_trail.append({
            "backend": "modal",
            "available": modal_available,
            "adequate": True,
            "reason": modal_reason,
        })
        if modal_available:
            if decision == "local_cpu":
                decision = "modal_cpu"
            routing_extra["backend"] = "modal"
            reasoning.append("Explicit override: Modal (forced remote).")
        else:
            decision = "rejected"
            risk_flags.append("modal_unavailable")
            reasoning.append(
                f"Explicit modal requested but unavailable: {modal_reason}."
            )
    elif not backend_override and gpu_requested:
        # GPU cascade (plan §5.1): a GPU-requested job walks routing_order for the first
        # GPU-capable + available lane. GPU-capable = local (only if the box has a GPU),
        # Kaggle (free GPU within its weekly GPU-hour self-cap), Modal (always), GitHub
        # Actions (only when gha.gpu_enabled -- paid larger runners, off by default). Hetzner
        # has no on-demand GPU and is always skipped. This branch runs before the CPU
        # branches, so a GPU request -- explicit OR auto-signalled -- never falls into the CPU
        # cascade.
        chosen, backend_name, gpu_trail = select_gpu_lane(
            order=order, local_gpu=bool(local_gpu_count), config=config,
            kg=get_kaggle, kg_in_order=kaggle_in_order,
            modal_ok=get_modal, gha_ok=gha_ok)
        routing_trail.extend(gpu_trail)
        if chosen is None:
            decision = "rejected"
            risk_flags.append("no_gpu_lane_available")
            reasoning.append("GPU was requested but no GPU-capable lane is available: the "
                             "local box has no GPU, Kaggle is unavailable or its weekly GPU-hour "
                             "cap is exhausted, Modal is unavailable, Hetzner has no on-demand "
                             "GPU, and GitHub Actions GPU is disabled.")
        elif backend_name == "local":
            decision = chosen
            routing_extra.update({"backend": "local", "gpu": True})
            reasoning.append("GPU routing: the local box has a GPU; running local-GPU.")
        elif backend_name == "kaggle":
            decision = chosen
            _attach_kaggle()
            reasoning.append("GPU routing: offloading to Kaggle GPU (free, within the weekly "
                             "GPU-hour self-cap).")
        elif backend_name == "gha":
            decision = chosen
            risk_flags.append("gha_gpu_larger_runner_paid")
            reasoning.append("GPU routing: routing to a GitHub Actions GPU larger-runner "
                             "(paid, plan-gated).")
        else:  # modal
            decision = chosen
            reasoning.append("GPU routing: offloading to Modal GPU.")
    elif not backend_override and decision == "local_cpu":
        veto = local_self_preservation_probe(estimate, config=config, resources=resources)
        routing_trail.append({k: veto[k] for k in
                              ("backend", "available", "adequate", "w_safe", "w_needed", "w_eff", "reason")})
        if veto["adequate"]:
            routing_extra.update({"backend": "local", "local_workers": veto["w_eff"],
                                  "w_safe": veto["w_safe"], "w_needed": veto["w_needed"]})
            reasoning.append(veto["reason"])
        else:
            # Local is load-unsafe: fall through per routing_order, EXCLUDING local.
            risk_flags.append("local_self_preservation_veto")
            reasoning.append(veto["reason"])
            # This fallthrough is CPU-only: gpu_requested jobs are handled by the GPU
            # cascade above and never reach the local veto. Secret-locality jobs return at
            # the global trust-boundary gate before any remote probe.
            modal_decision = "modal_cpu"
            chosen, backend_name, cascade_trail = select_remote_lane(
                order=order, modal_decision=modal_decision, gpu_signal=gpu_requested,
                hz=get_hetzner, hz_in_order=hetzner_in_order,
                kg=get_kaggle, kg_in_order=kaggle_in_order,
                modal_ok=get_modal, gha_ok=gha_ok)
            routing_trail.extend(cascade_trail)
            fell = _apply_lane(chosen, backend_name,
                               context="Local vetoed for self-preservation")
            if not fell:
                if veto["w_safe"] >= 1:
                    # Safe (throttled) but over the wall budget: run local rather than gamble.
                    decision = "local_cpu"
                    routing_extra.update({"backend": "local", "local_workers": veto["w_safe"],
                                          "w_safe": veto["w_safe"], "w_needed": veto["w_needed"]})
                    risk_flags.append("local_over_wall_budget")
                    reasoning.append(f"No remote backend available; running throttled-local at "
                                     f"{veto['w_safe']} worker(s), beyond the wall budget.")
                else:
                    decision = "rejected"
                    risk_flags.append("no_safe_backend")
                    reasoning.append("No safe backend: local is load-unsafe and no remote lane "
                                     "is available.")
    elif not backend_override and decision.startswith("modal_"):
        # The job is too heavy for local. Walk routing_order for the first available lane:
        # Kaggle (free CPU) is tried first, then Modal (if ready + account-usable) keeps the
        # base modal_* decision; otherwise the cascade falls through order-driven to Hetzner,
        # then GHA. Kaggle-before-Modal is the free-CPU-first fix; Hetzner-after-Modal is the
        # earlier fix for the old jump straight to GHA when Modal was out of credits.
        chosen, backend_name, cascade_trail = select_remote_lane(
            order=order, modal_decision=decision, gpu_signal=gpu_requested,
            hz=get_hetzner, hz_in_order=hetzner_in_order,
            kg=get_kaggle, kg_in_order=kaggle_in_order,
            modal_ok=get_modal, gha_ok=gha_ok)
        routing_trail.extend(cascade_trail)
        if backend_name == "kaggle":
            decision = "kaggle"
            _attach_kaggle()
            reasoning.append(
                "The configured routing order selected Kaggle as the first available "
                "adequate remote lane; offloading free CPU work there."
            )
        elif backend_name == "hetzner":
            decision = "hetzner"
            _attach_hetzner()
            reasoning.append(
                "The configured routing order selected Hetzner as the first available "
                "adequate remote lane; offloading CPU work there."
            )
        elif backend_name == "gha":
            decision = "gha"
            reasoning.append(
                "The configured routing order selected GitHub Actions as the first "
                "available adequate remote lane."
            )
        elif backend_name == "modal":
            decision = chosen  # keep the specific base modal_* decision
            reasoning.append(
                "The configured routing order selected Modal as the first available "
                "adequate remote lane."
            )
        else:
            decision = "rejected"
            risk_flags.append("no_remote_lane_available")
            reasoning.append(
                "No configured remote lane is both available and adequate; rejecting "
                "instead of retaining the seeded Modal decision."
            )

    if execution_primitive == "sandbox" and decision.startswith("modal_"):
        decision = "modal_sandbox_experimental"
        reasoning.append("The job explicitly requested sandbox execution.")

    if routing_trail:
        routing_extra["routing_trail"] = routing_trail

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
        extra=routing_extra,
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
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = {
        "accepted": accepted,
        "decision": decision,
        "execution_primitive": execution_primitive,
        "estimated_cost_usd": round(estimated_cost_usd, 4),
        "estimated_runtime_sec": estimated_runtime_sec,
        "risk_flags": risk_flags,
        "required_policy_exceptions": required_policy_exceptions,
        "reasoning_summary": reasoning_summary.strip(),
    }
    if extra:
        # Router-supplied fields (backend, server_type, estimated_cost_eur, local_workers,
        # routing_trail, ...) never clobber the core plan keys above.
        for key, value in extra.items():
            plan.setdefault(key, value)
    return plan


def build_estimate(
    *,
    parameters: dict[str, Any],
    constraints: dict[str, Any],
    policy: dict[str, Any],
    gpu_signal: bool,
    runtime_sec: int,
    data_locality: str | None = None,
) -> dict[str, Any]:
    """Backend-agnostic job estimate consumed by the local veto and the Hetzner probe.
    core_hours is the manifest's explicit core-hour estimate when present, else a floor of
    one core's wall time; parallelism is the requested fan-out width."""
    parallelism = int(constraints.get("parallelism") or constraints.get("cpu") or 1)
    core_hours = constraints.get("core_hours")
    if core_hours in (None, 0, 0.0):
        core_hours = parameters.get("core_hours")
    if core_hours in (None, 0, 0.0):
        core_hours = runtime_sec / 3600.0
    if data_locality is None:
        data_locality, _ = resolve_data_locality(constraints, policy)
    return {
        "core_hours": float(core_hours),
        "parallelism": max(1, parallelism),
        "peak_ram_gb": float(int(constraints.get("memory_mb", 0) or 0)) / 1024.0,
        "gpu": bool(gpu_signal),
        "data_locality": data_locality,
        "runtime_sec": int(runtime_sec),
    }


def local_cores(resources: dict[str, Any] | None) -> int:
    cores = nested_get(resources, "cpu", "logical_cores", default=None)
    if not cores:
        cores = os.cpu_count() or 1
    return max(1, int(cores))


def local_load_1m(resources: dict[str, Any] | None) -> float:
    """Current 1-minute load average. Reads an injected reading from the resource
    snapshot (resources['load']['load_1m']) when present -- so the self-preservation
    projection is deterministic offline -- otherwise the live os.getloadavg()."""
    injected = nested_get(resources, "load", "load_1m", default=None)
    if injected is not None:
        return float(injected)
    try:
        return float(os.getloadavg()[0])
    except (OSError, AttributeError):  # pragma: no cover - non-POSIX
        return 0.0


def local_self_preservation_probe(estimate: dict[str, Any], *, config: Any,
                                  resources: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pre-launch projection for the local lane (plan §5). Each CPU-bound worker adds
    ~1.0 sustained load and nice() does not lower loadavg, so the only control is the
    worker count. Project the safe worker ceiling and the workers the wall budget needs:

        w_safe   = floor(danger_load_frac*N - loadavg - session_headroom_frac*N)
        w_needed = ceil(core_hours / local_wall_budget_h)

    Reject -> fallback (adequate=False) if even one worker is unsafe (w_safe < 1) or the
    wall budget only fits at unsafe parallelism (w_needed > w_safe). Otherwise accept as
    safe throttled-local pinned to w_eff workers (enough to meet the budget, capped at
    the safe ceiling)."""
    n = local_cores(resources)
    load = local_load_1m(resources)
    danger = float(getattr(config, "local_danger_load_frac", 0.5))
    headroom = float(getattr(config, "local_session_headroom_frac", 0.15))
    wall_budget = float(getattr(config, "local_wall_budget_h", 2.0))

    w_safe = math.floor(danger * n - load - headroom * n)
    core_hours = float(estimate.get("core_hours") or 0.0)
    w_needed = math.ceil(core_hours / wall_budget) if wall_budget > 0 else 10 ** 9

    base = {"backend": "local", "n_cores": n, "loadavg_1m": round(load, 3),
            "w_safe": int(w_safe), "w_needed": int(w_needed)}
    if w_safe < 1:
        return {**base, "available": True, "adequate": False, "w_eff": 0,
                "reason": f"self_preservation_veto: w_safe={w_safe} < 1 at load "
                          f"{load:.2f} on {n} cores"}
    if w_needed > w_safe:
        return {**base, "available": True, "adequate": False, "w_eff": 0,
                "reason": f"self_preservation_veto: wall budget needs w_needed={w_needed} "
                          f"> w_safe={w_safe}"}
    w_eff = min(int(w_safe), max(1, int(w_needed)))
    return {**base, "available": True, "adequate": True, "w_eff": w_eff,
            "reason": f"safe throttled-local pinned to {w_eff} worker(s) "
                      f"(w_safe={w_safe}, w_needed={w_needed})"}


def run_local_watched(*, w_eff: int, config: Any, load_source: Any,
                      resources: dict[str, Any] | None = None, run_step: Any = None,
                      checkpoint: Any = None, max_polls: int = 1000) -> dict[str, Any]:
    """Runtime watchdog wrapper (Phase A stub -- it does NOT spawn real workers). Polls
    the 1-minute load through the injected `load_source` callable and, per plan §5, sheds
    a worker on a soft breach and checkpoints + aborts on a hard breach, returning
    ABORT_FALLBACK so the caller can re-route with local EXCLUDED (resuming from the
    checkpoint). Thresholds are fractions of N:

        soft = local_soft_load_frac * N   ->  shed one worker
        hard = local_hard_load_frac * N   ->  checkpoint + abort + fall back

    `run_step`, when given, advances one unit of simulated work per poll and returns
    False when the (offline) job is done. Injecting `load_source` keeps the control logic
    fully testable without generating any load."""
    n = local_cores(resources)
    soft = float(getattr(config, "local_soft_load_frac", 0.4)) * n
    hard = float(getattr(config, "local_hard_load_frac", 0.55)) * n
    workers = max(1, int(w_eff))
    trail: list[dict[str, Any]] = []
    for poll in range(int(max_polls)):
        load = float(load_source())
        event = {"poll": poll, "loadavg_1m": round(load, 3), "workers": workers}
        if load >= hard:
            event["action"] = "hard_breach"
            trail.append(event)
            checkpoint_result = checkpoint() if callable(checkpoint) else None
            return {"status": WATCH_ABORT_FALLBACK, "polls": poll + 1, "workers": workers,
                    "reason": f"hard load breach {load:.2f} >= {hard:.2f}; checkpointed and "
                              f"re-routing (exclude local)",
                    "checkpoint": checkpoint_result, "trail": trail}
        if load >= soft and workers > 1:
            workers -= 1
            event["action"] = "soft_breach_shed_worker"
        else:
            event["action"] = "ok"
        trail.append(event)
        if run_step is not None and not run_step():
            return {"status": WATCH_OK, "polls": poll + 1, "workers": workers,
                    "reason": "completed within the load ceiling", "trail": trail}
    return {"status": WATCH_OK, "polls": int(max_polls), "workers": workers,
            "reason": "poll budget exhausted without a breach", "trail": trail}


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
