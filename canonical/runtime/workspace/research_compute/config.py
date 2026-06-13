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
    routing_order: list[str] = field(default_factory=lambda: ["local", "modal", "gha"])
    gha_enabled: bool = False
    gha_included_minutes: int = 0
    gha_repos: dict[str, Any] = field(default_factory=dict)
    modal_monthly_budget_usd: float = 0.0
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
        routing_order=list(data.get("routing_order", ["local", "modal", "gha"])),
        gha_enabled=bool(data.get("gha", {}).get("enabled", False)),
        gha_included_minutes=int(data.get("gha", {}).get("included_minutes", 0)),
        gha_repos=dict(data.get("gha", {}).get("repos", {})),
        modal_monthly_budget_usd=float(data.get("modal_monthly_budget_usd", 0.0)),
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
