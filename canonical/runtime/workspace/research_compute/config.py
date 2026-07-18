from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BrokerDefaults:
    auto_submit: bool = True
    wait_poll_seconds: int = 5


@dataclass
class FunctionMap:
    modal_cpu: str = "run_cpu_job"
    modal_highmem_cpu: str = "run_highmem_job"
    modal_gpu: str = "run_gpu_job"
    modal_sandbox_experimental: str = "run_sandbox_job"


@dataclass
class BrokerConfig:
    install_id: str
    platform: str
    broker_state_root: str
    default_materialize_root: str
    modal_profile: str | None
    modal_environment: str
    deployment_alias: str
    allowed_gpu_families: list[str]
    per_job_cost_cap_usd: float
    default_archive_backend: str
    routing_order: list[str] = field(default_factory=lambda: ["local", "kaggle", "modal", "hetzner", "gha"])
    gha_enabled: bool = False
    gha_included_minutes: int = 0
    gha_repos: dict[str, Any] = field(default_factory=dict)
    # GitHub Actions is available only while cumulative account-wide minutes used this
    # cycle + the job's worst case stay within this fraction of the included minutes,
    # reserving the remainder for the user's other workflows (plan §6.1).
    gha_max_usage_fraction: float = 0.60
    # GitHub Actions GPU is available only via PAID "larger runners" (Team/Enterprise; not
    # free minutes, not public repos), so it is opt-in and OFF by default (plan §5.1). When
    # on, the GHA lane is GPU-capable for GPU-requested jobs (still under the minutes cap).
    gha_gpu_enabled: bool = False
    modal_monthly_budget_usd: float = 0.0
    # Hetzner Cloud lane (disabled by default; configured under [hetzner]).
    hetzner_enabled: bool = False
    hetzner_server_types: dict[str, Any] = field(default_factory=dict)
    hetzner_gpu_server_types: list[str] = field(default_factory=list)
    hetzner_monthly_eur_cap: float = 0.0
    hetzner_max_eur_per_job: float = 3.0
    hetzner_max_eur_per_day: float = 3.0
    hetzner_max_server_hours: float = 6.0
    hetzner_max_concurrent_servers: int = 2
    # Current orderable Hetzner regions (fsn1 has no orderable types as of 2026); preflight
    # availability-checks the live datacenter list and picks an orderable location from here.
    hetzner_allowed_locations: list[str] = field(default_factory=lambda: ["nbg1", "hel1", "sin"])
    hetzner_location: str | None = "nbg1"
    hetzner_image: str | None = None
    # Kaggle Kernels lane (disabled by default; configured under [kaggle]). CPU is free and
    # quota-free; GPU is gated by a self-imposed weekly GPU-hour cap (local ledger). Kernels
    # auto-stop at the session cap and cost nothing, so there is no cost gate and no reaper.
    # The credential is the new single Kaggle API token, supplied via the KAGGLE_API_TOKEN
    # environment variable (or ~/.kaggle/access_token) -- never the legacy KAGGLE_USERNAME +
    # KAGGLE_KEY pair, and never stored in this TOML.
    kaggle_enabled: bool = False
    kaggle_weekly_gpu_hours_cap: float = 18.0
    kaggle_max_runs: int = 5
    kaggle_concurrency: int = 5
    kaggle_session_hours: float = 12.0
    kaggle_kernel_cores: int = 4
    kaggle_kernel_ram_gb: float = 32.0
    # Local-lane self-preservation veto thresholds (fractions of the logical core count).
    local_danger_load_frac: float = 0.5
    local_session_headroom_frac: float = 0.15
    local_soft_load_frac: float = 0.4
    local_hard_load_frac: float = 0.55
    local_wall_budget_h: float = 2.0
    # Multi-backend fan-out scheduler (v2; disabled by default; configured under [fanout]).
    # Fan-out splits ONE large divisible job (M chunks) across several lanes at once, each
    # sized to its spare capacity, to minimise makespan while minimising cost. Small jobs
    # keep using the single-lane router. speed_cost_weight in [0,1] blends the two goals
    # (0 = cheapest / free lanes only, 1 = fastest / recruit paid lanes); it is a per-job
    # override, defaulting here. The per-core-hour rates and per-eur rate feed ONLY the
    # objective's cost term -- the hard rails (budget caps, EUR3/day, GHA 60%, Kaggle quota,
    # local load-cap) are enforced separately and the knob can never breach them.
    fanout_enabled: bool = False
    fanout_speed_cost_weight: float = 0.5
    fanout_min_chunks: int = 8
    fanout_usd_per_eur: float = 1.08
    fanout_local_usd_per_core_hour: float = 0.006  # local Oracle box is NOT free
    fanout_modal_usd_per_core_hour: float = 0.10
    fanout_modal_slots: int = 16
    fanout_gha_slots: int = 20
    fanout_local_startup_seconds: float = 5.0
    fanout_kaggle_startup_seconds: float = 300.0
    fanout_modal_startup_seconds: float = 45.0
    fanout_hetzner_startup_seconds: float = 180.0
    fanout_gha_startup_seconds: float = 60.0
    functions: FunctionMap = field(default_factory=FunctionMap)
    defaults: BrokerDefaults = field(default_factory=BrokerDefaults)

    def state_root(self, workspace_root: Path) -> Path:
        root = Path(self.broker_state_root)
        return root if root.is_absolute() else workspace_root / root


def workspace_root() -> Path:
    env_root = os.environ.get("CODEX_RUNTIME_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path.cwd().resolve()


def caller_cwd() -> Path:
    value = os.environ.get("CODEX_CALLER_CWD") or os.environ.get("OLDPWD")
    if value:
        return Path(value).expanduser().resolve()
    return Path.cwd().resolve()


def default_config_path(root: Path | None = None) -> Path:
    base = root or workspace_root()
    return base / "config" / "research-compute.toml"


def example_config_path(root: Path | None = None) -> Path:
    base = root or workspace_root()
    return base / "config" / "research-compute.example.toml"


def modal_config_path() -> Path:
    override = os.environ.get("MODAL_CONFIG_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".modal.toml"


def load_config(path: Path | None = None) -> BrokerConfig:
    config_path = (path or default_config_path()).expanduser().resolve()
    data = load_toml(config_path)

    functions = FunctionMap(**data.get("functions", {}))
    defaults = BrokerDefaults(**data.get("defaults", {}))
    hetzner = data.get("hetzner", {}) or {}
    kaggle = data.get("kaggle", {}) or {}
    local = data.get("local", {}) or {}
    fanout = data.get("fanout", {}) or {}

    return BrokerConfig(
        install_id=data["install_id"],
        platform=data["platform"],
        broker_state_root=data.get("broker_state_root", "../../memories/research-compute"),
        default_materialize_root=data.get("default_materialize_root", ".research-compute"),
        modal_profile=data.get("modal_profile"),
        modal_environment=data.get("modal_environment", "main"),
        deployment_alias=data.get("deployment_alias", "research-compute-codex"),
        allowed_gpu_families=list(data.get("allowed_gpu_families", [])),
        per_job_cost_cap_usd=float(data.get("per_job_cost_cap_usd", 5.0)),
        default_archive_backend=data.get("default_archive_backend", "local"),
        routing_order=list(data.get("routing_order", ["local", "kaggle", "modal", "hetzner", "gha"])),
        gha_enabled=bool(data.get("gha", {}).get("enabled", False)),
        gha_included_minutes=int(data.get("gha", {}).get("included_minutes", 0)),
        gha_repos=dict(data.get("gha", {}).get("repos", {})),
        gha_max_usage_fraction=float(data.get("gha", {}).get("max_usage_fraction", 0.60)),
        gha_gpu_enabled=bool(data.get("gha", {}).get("gpu_enabled", False)),
        modal_monthly_budget_usd=float(data.get("modal_monthly_budget_usd", 0.0)),
        hetzner_enabled=bool(hetzner.get("enabled", False)),
        hetzner_server_types=dict(hetzner.get("server_types", {})),
        hetzner_gpu_server_types=list(hetzner.get("gpu_server_types", [])),
        hetzner_monthly_eur_cap=float(hetzner.get("monthly_eur_cap", 0.0)),
        hetzner_max_eur_per_job=float(hetzner.get("max_eur_per_job", 3.0)),
        hetzner_max_eur_per_day=float(hetzner.get("max_eur_per_day", 3.0)),
        hetzner_max_server_hours=float(hetzner.get("max_server_hours", 6.0)),
        hetzner_max_concurrent_servers=int(hetzner.get("max_concurrent_servers", 2)),
        hetzner_allowed_locations=list(hetzner.get("allowed_locations", ["nbg1", "hel1", "sin"])),
        hetzner_location=hetzner.get("location", "nbg1"),
        hetzner_image=hetzner.get("image"),
        kaggle_enabled=bool(kaggle.get("enabled", False)),
        kaggle_weekly_gpu_hours_cap=float(kaggle.get("weekly_gpu_hours_cap", 18.0)),
        kaggle_max_runs=int(kaggle.get("max_runs", 5)),
        kaggle_concurrency=int(kaggle.get("concurrency", 5)),
        kaggle_session_hours=float(kaggle.get("session_hours", 12.0)),
        kaggle_kernel_cores=int(kaggle.get("kernel_cores", 4)),
        kaggle_kernel_ram_gb=float(kaggle.get("kernel_ram_gb", 32.0)),
        local_danger_load_frac=float(local.get("danger_load_frac", 0.5)),
        local_session_headroom_frac=float(local.get("session_headroom_frac", 0.15)),
        local_soft_load_frac=float(local.get("soft", 0.4)),
        local_hard_load_frac=float(local.get("hard", 0.55)),
        local_wall_budget_h=float(local.get("local_wall_budget_h", 2.0)),
        fanout_enabled=bool(fanout.get("enabled", False)),
        fanout_speed_cost_weight=float(fanout.get("speed_cost_weight", 0.5)),
        fanout_min_chunks=int(fanout.get("min_chunks", 8)),
        fanout_usd_per_eur=float(fanout.get("usd_per_eur", 1.08)),
        fanout_local_usd_per_core_hour=float(fanout.get("local_usd_per_core_hour", 0.006)),
        fanout_modal_usd_per_core_hour=float(fanout.get("modal_usd_per_core_hour", 0.10)),
        fanout_modal_slots=int(fanout.get("modal_slots", 16)),
        fanout_gha_slots=int(fanout.get("gha_slots", 20)),
        fanout_local_startup_seconds=float(fanout.get("local_startup_seconds", 5.0)),
        fanout_kaggle_startup_seconds=float(fanout.get("kaggle_startup_seconds", 300.0)),
        fanout_modal_startup_seconds=float(fanout.get("modal_startup_seconds", 45.0)),
        fanout_hetzner_startup_seconds=float(fanout.get("hetzner_startup_seconds", 180.0)),
        fanout_gha_startup_seconds=float(fanout.get("gha_startup_seconds", 60.0)),
        functions=functions,
        defaults=defaults,
    )


def load_toml(path: Path) -> dict[str, Any]:
    # Imported lazily so the broker (and `bootstrap`) import without a TOML parser
    # installed -- only actual config loading needs it. On a bare Python 3.10 host
    # this lets `bootstrap --install-deps` install tomli before any config is read.
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
        import tomli as tomllib  # type: ignore[no-redef]
    with path.open("rb") as handle:
        return tomllib.load(handle)
