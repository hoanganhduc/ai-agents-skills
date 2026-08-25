from __future__ import annotations

import json
import re
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from .target_surfaces import validate_target_surfaces


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = REPO_ROOT / "manifest"
SUPPORT_FILE_EXCLUSION_SUFFIXES = {
    "windows": frozenset({".sh"}),
    "linux": frozenset({".bat", ".cmd", ".ps1"}),
    "macos": frozenset({".bat", ".cmd", ".ps1"}),
    "wsl": frozenset({".bat", ".cmd", ".ps1"}),
}


class ManifestError(ValueError):
    pass


def load_json_yaml(path: Path) -> dict[str, Any]:
    """Load JSON-compatible YAML without requiring external dependencies."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        try:
            import yaml  # type: ignore
        except ImportError as import_exc:
            raise ManifestError(
                f"{path} is not JSON-compatible YAML and PyYAML is unavailable"
            ) from import_exc
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ManifestError(f"{path} must contain a mapping")
        return data
    except FileNotFoundError as exc:
        raise ManifestError(f"manifest file not found: {path}") from exc


def load_manifests() -> dict[str, Any]:
    skills = load_json_yaml(MANIFEST_DIR / "skills.yaml")
    profiles = load_json_yaml(MANIFEST_DIR / "profiles.yaml")
    dependencies = load_json_yaml(MANIFEST_DIR / "dependencies.yaml")
    external_dependencies = load_json_yaml(MANIFEST_DIR / "external-dependencies.yaml")
    artifacts = load_json_yaml(MANIFEST_DIR / "artifacts.yaml")
    system_dependencies = load_json_yaml(MANIFEST_DIR / "system-dependencies.yaml")
    runtime = load_json_yaml(MANIFEST_DIR / "runtime.yaml")
    delegation = load_json_yaml(MANIFEST_DIR / "delegation.yaml")
    validate_manifests(
        skills,
        profiles,
        dependencies,
        artifacts,
        system_dependencies,
        runtime,
        delegation,
        external_dependencies=external_dependencies,
    )
    validate_target_surfaces()
    return {
        "skills": skills,
        "profiles": profiles,
        "dependencies": dependencies,
        "external_dependencies": external_dependencies,
        "artifacts": artifacts,
        "system_dependencies": system_dependencies,
        "runtime": runtime,
        "delegation": delegation,
    }


EXTERNAL_BUNDLE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
EXTERNAL_SOURCE_DIRECTORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
EXTERNAL_REPOSITORY_RE = re.compile(
    r"^https://github\.com/hoanganhduc/[A-Za-z0-9_.-]+\.git$"
)
EXTERNAL_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
EXTERNAL_VENV_POINTER_RE = re.compile(r"^\.[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
EXTERNAL_DISTRIBUTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
EXTERNAL_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


def validate_external_dependencies(external_dependencies: dict[str, Any]) -> None:
    if external_dependencies.get("schema_version") != 1:
        raise ManifestError("external-dependencies.yaml must declare schema_version 1")
    bundles = external_dependencies.get("bundles")
    if not isinstance(bundles, dict) or not bundles:
        raise ManifestError("external-dependencies.yaml must contain a non-empty bundles object")
    for name, spec in bundles.items():
        if not isinstance(name, str) or EXTERNAL_BUNDLE_RE.fullmatch(name) is None:
            raise ManifestError(f"external dependency bundle has an invalid name: {name!r}")
        if not isinstance(spec, dict):
            raise ManifestError(f"external dependency bundle {name} must be an object")
        repository = spec.get("repository")
        if not isinstance(repository, str) or EXTERNAL_REPOSITORY_RE.fullmatch(repository) is None:
            raise ManifestError(f"external dependency bundle {name} must use an allowlisted GitHub HTTPS repository")
        revision = spec.get("revision")
        if not isinstance(revision, str) or EXTERNAL_REVISION_RE.fullmatch(revision) is None:
            raise ManifestError(f"external dependency bundle {name} must declare a full lowercase Git revision")
        source_directory = spec.get("source_directory")
        if not isinstance(source_directory, str) or EXTERNAL_SOURCE_DIRECTORY_RE.fullmatch(source_directory) is None:
            raise ManifestError(f"external dependency bundle {name} has an unsafe source_directory")
        venv_pointer = spec.get("venv_pointer")
        if not isinstance(venv_pointer, str) or EXTERNAL_VENV_POINTER_RE.fullmatch(venv_pointer) is None:
            raise ManifestError(f"external dependency bundle {name} has an unsafe venv_pointer")
        distribution = spec.get("distribution")
        if not isinstance(distribution, str) or EXTERNAL_DISTRIBUTION_RE.fullmatch(distribution) is None:
            raise ManifestError(f"external dependency bundle {name} has an invalid distribution name")
        version = spec.get("version")
        if not isinstance(version, str) or re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
            raise ManifestError(f"external dependency bundle {name} must declare a semver package version")
        for field in ("modules", "help_modules", "requirements"):
            values = spec.get(field)
            if not isinstance(values, list) or not values or not all(isinstance(value, str) and value for value in values):
                raise ManifestError(f"external dependency bundle {name} field {field} must be a non-empty string list")
        if any(EXTERNAL_MODULE_RE.fullmatch(value) is None for value in spec["modules"]):
            raise ManifestError(f"external dependency bundle {name} has an invalid Python module")
        if any(EXTERNAL_MODULE_RE.fullmatch(value) is None for value in spec["help_modules"]):
            raise ManifestError(f"external dependency bundle {name} has an invalid help module")
        for requirement in spec["requirements"]:
            if (
                "\n" in requirement
                or "\r" in requirement
                or requirement.startswith("-")
                or "://" in requirement
                or "@" in requirement
            ):
                raise ManifestError(f"external dependency bundle {name} has an unsafe requirement")


def validate_manifests(
    skills: dict[str, Any],
    profiles: dict[str, Any],
    dependencies: dict[str, Any],
    artifacts: dict[str, Any],
    system_dependencies: dict[str, Any],
    runtime: dict[str, Any],
    delegation: dict[str, Any],
    external_dependencies: dict[str, Any] | None = None,
) -> None:
    if "skills" not in skills or not isinstance(skills["skills"], dict):
        raise ManifestError("skills.yaml must contain a skills object")
    if "profiles" not in profiles or not isinstance(profiles["profiles"], dict):
        raise ManifestError("profiles.yaml must contain a profiles object")
    if "tools" not in dependencies or not isinstance(dependencies["tools"], dict):
        raise ManifestError("dependencies.yaml must contain a tools object")
    if external_dependencies is None:
        external_dependencies = load_json_yaml(MANIFEST_DIR / "external-dependencies.yaml")
    validate_external_dependencies(external_dependencies)
    packages = dependencies.get("packages", {})
    if not isinstance(packages, dict):
        raise ManifestError("dependencies.yaml packages must be an object")
    python_candidate_sets = dependencies.get("python_candidate_sets", {})
    python_site_candidate_sets = dependencies.get("python_site_candidate_sets", {})
    for name, spec in packages.items():
        if not isinstance(spec, dict):
            raise ManifestError(f"dependency package {name} must be an object")
        if spec.get("type") != "python":
            continue
        module = spec.get("module")
        if not isinstance(module, str) or not module:
            raise ManifestError(f"Python dependency package {name} must declare a module")
        modules = spec.get("modules")
        if modules is not None:
            if not isinstance(modules, list) or not modules or not all(
                isinstance(item, str) and item for item in modules
            ):
                raise ManifestError(f"Python dependency package {name} modules must be a non-empty string list")
            if module not in modules:
                raise ManifestError(f"Python dependency package {name} modules must include its primary module")
        candidate_set = spec.get("candidate_set", "default")
        if not isinstance(candidate_set, str) or candidate_set not in python_candidate_sets or candidate_set not in python_site_candidate_sets:
            raise ManifestError(f"Python dependency package {name} references unknown candidate set {candidate_set}")
        authoritative = spec.get("authoritative_first_existing")
        if authoritative is not None and not isinstance(authoritative, bool):
            raise ManifestError(
                f"Python dependency package {name} authoritative_first_existing must be boolean"
            )
    if "artifacts" not in artifacts or not isinstance(artifacts["artifacts"], dict):
        raise ManifestError("artifacts.yaml must contain an artifacts object")
    if "artifact_profiles" not in artifacts or not isinstance(artifacts["artifact_profiles"], dict):
        raise ManifestError("artifacts.yaml must contain an artifact_profiles object")
    for field in ("software", "python_packages"):
        if field not in system_dependencies or not isinstance(system_dependencies[field], dict):
            raise ManifestError(f"system-dependencies.yaml must contain a {field} object")

    for name, spec in skills["skills"].items():
        if not isinstance(spec, dict):
            raise ManifestError(f"skill {name} must be an object")
        for field in ("description", "profiles", "supported_agents", "verification"):
            if field not in spec:
                raise ManifestError(f"skill {name} is missing {field}")
        if "_" in name:
            raise ManifestError(f"skill {name} must use canonical kebab-case")
        version = spec.get("version")
        if version is not None and (
            not isinstance(version, str)
            or re.fullmatch(r"\d+\.\d+\.\d+", version) is None
        ):
            raise ManifestError(
                f'skill {name} version must be a semver string like "1.0.0"'
            )
        declared_dependencies = set(dependencies["tools"]) | set(packages)
        for field in ("required_dependencies", "optional_dependencies"):
            for dependency in spec.get(field, []):
                if dependency not in declared_dependencies:
                    raise ManifestError(f"skill {name} references unknown dependency {dependency}")
        declared_templates = artifacts.get("artifacts", {}).get("template", {})
        for template_slug in spec.get("recommended_templates", []):
            if template_slug not in declared_templates:
                raise ManifestError(f"skill {name} recommends unknown template {template_slug}")
        validate_neutral_platform_support_files(name, spec)

    for profile_name, spec in profiles["profiles"].items():
        if not isinstance(spec, dict):
            raise ManifestError(f"profile {profile_name} must be an object")
        profile_skills = spec.get("skills")
        if not isinstance(profile_skills, list):
            raise ManifestError(f"profile {profile_name} must contain a skills list")
        if profile_skills == ["*"]:
            continue
        for skill in profile_skills:
            if skill not in skills["skills"]:
                raise ManifestError(f"profile {profile_name} references unknown skill {skill}")
            if profile_name not in skills["skills"][skill].get("profiles", []):
                raise ManifestError(
                    f"profile {profile_name} references skill {skill} but skill does not list that profile"
                )
    profile_names = set(profiles["profiles"])
    explicit_profile_skills = {
        profile_name: set(spec.get("skills", []))
        for profile_name, spec in profiles["profiles"].items()
        if spec.get("skills") != ["*"]
    }
    for name, spec in skills["skills"].items():
        for profile in spec.get("profiles", []):
            if profile not in profile_names:
                raise ManifestError(f"skill {name} references unknown profile {profile}")
            if profile in explicit_profile_skills and name not in explicit_profile_skills[profile]:
                raise ManifestError(f"skill {name} references profile {profile} but profile does not list that skill")

    declared_artifacts = set()
    for artifact_type, by_name in artifacts["artifacts"].items():
        if not isinstance(by_name, dict):
            raise ManifestError(f"artifact type {artifact_type} must be an object")
        for name, spec in by_name.items():
            if "_" in name:
                raise ManifestError(f"artifact {artifact_type}:{name} must use canonical kebab-case")
            required_fields = ["description", "supported_agents"]
            if artifact_type != "management-notice":
                required_fields.append("source")
            for field in required_fields:
                if field not in spec:
                    raise ManifestError(f"artifact {artifact_type}:{name} is missing {field}")
            for skill in spec.get("depends_on_skills", []):
                if skill not in skills["skills"]:
                    raise ManifestError(f"artifact {artifact_type}:{name} references unknown skill {skill}")
            declared_artifacts.add(f"{artifact_type}:{name}")
    for profile_name, spec in artifacts["artifact_profiles"].items():
        if not isinstance(spec, dict):
            raise ManifestError(f"artifact profile {profile_name} must be an object")
        for item in spec.get("artifacts", []):
            if item not in declared_artifacts:
                raise ManifestError(f"artifact profile {profile_name} references unknown artifact {item}")

    if "runtime_profiles" not in runtime or not isinstance(runtime["runtime_profiles"], dict):
        raise ManifestError("runtime.yaml must contain runtime_profiles")
    if "skills" not in runtime or not isinstance(runtime["skills"], dict):
        raise ManifestError("runtime.yaml must contain skills")
    runtime_source_root = REPO_ROOT / "canonical" / "runtime"
    for entry in runtime.get("runners", []):
        validate_runtime_file(entry, runtime_source_root, "runner")
    for skill, spec in runtime["skills"].items():
        if skill not in skills["skills"]:
            raise ManifestError(f"runtime skill {skill} is not declared in skills.yaml")
        if "_" in spec.get("runtime_dir", ""):
            raise ManifestError(f"runtime skill {skill} runtime_dir must use canonical kebab-case")
        validate_runtime_smoke_coverage(skill, spec)
        for entry in spec.get("files", []):
            validate_runtime_file(entry, runtime_source_root, f"runtime skill {skill}")
        if "smoke" in spec:
            validate_runtime_smoke_contract(skill, spec["smoke"])

    validate_delegation_manifest(delegation)


def validate_neutral_platform_support_files(
    skill: str,
    spec: dict[str, Any],
) -> None:
    declarations = spec.get("neutral_platform_support_files")
    if declarations is None:
        return
    if not isinstance(declarations, dict):
        raise ManifestError(
            f"skill {skill} neutral_platform_support_files must be an object"
        )
    unknown_platforms = set(declarations) - set(SUPPORT_FILE_EXCLUSION_SUFFIXES)
    if unknown_platforms:
        raise ManifestError(
            f"skill {skill} neutral_platform_support_files references unknown platform(s): "
            + ", ".join(sorted(unknown_platforms))
        )
    skill_root = REPO_ROOT / "canonical" / "skills" / skill
    for platform, declared_paths in declarations.items():
        if not isinstance(declared_paths, list) or not all(
            isinstance(item, str) and item for item in declared_paths
        ):
            raise ManifestError(
                f"skill {skill} neutral_platform_support_files.{platform} "
                "must be a non-empty-string list"
            )
        if len(declared_paths) != len(set(declared_paths)):
            raise ManifestError(
                f"skill {skill} neutral_platform_support_files.{platform} "
                "must not contain duplicate paths"
            )
        for declared_path in declared_paths:
            relative = PurePosixPath(declared_path)
            if (
                relative.is_absolute()
                or relative == PurePosixPath(".")
                or ".." in relative.parts
                or "\\" in declared_path
                or relative.as_posix() != declared_path
            ):
                raise ManifestError(
                    f"skill {skill} neutral platform support path must be a safe "
                    f"canonical relative path: {declared_path}"
                )
            source = skill_root.joinpath(*relative.parts)
            if not source.is_file():
                raise ManifestError(
                    f"skill {skill} neutral platform support file does not exist: "
                    f"{declared_path}"
                )
            if relative.suffix.lower() not in SUPPORT_FILE_EXCLUSION_SUFFIXES[platform]:
                raise ManifestError(
                    f"skill {skill} neutral platform support file is applicable on "
                    f"{platform} and cannot be excluded: {declared_path}"
                )


def validate_delegation_manifest(delegation: dict[str, Any]) -> None:
    if delegation.get("schema_version") != 1:
        raise ManifestError("delegation.yaml schema_version must be 1")
    policy = delegation.get("policy")
    providers = delegation.get("providers")
    nested = delegation.get("nested_delegation")
    if not isinstance(policy, dict):
        raise ManifestError("delegation.yaml must contain a policy object")
    if not isinstance(providers, dict):
        raise ManifestError("delegation.yaml must contain a providers object")
    if not isinstance(nested, dict):
        raise ManifestError("delegation.yaml must contain a nested_delegation object")
    if policy.get("mode") not in {"off", "audit_only", "prefer", "require"}:
        raise ManifestError("delegation.yaml policy.mode is invalid")
    if policy.get("research_model_policy") != "latest_model_highest_reasoning_required":
        raise ManifestError("delegation.yaml must require latest model and highest reasoning for research")
    if policy.get("template_policy") not in {"prefer_installed_templates", "built_in_only"}:
        raise ManifestError("delegation.yaml policy.template_policy is invalid")
    for field in ("active_providers", "reference_only_providers"):
        if not isinstance(policy.get(field), list):
            raise ManifestError(f"delegation.yaml policy.{field} must be a list")
    provider_names = set(providers)
    referenced = set(policy["active_providers"]) | set(policy["reference_only_providers"])
    if referenced - provider_names:
        missing = ", ".join(sorted(referenced - provider_names))
        raise ManifestError(f"delegation.yaml policy references unknown providers: {missing}")
    for name, spec in providers.items():
        if "_" in name:
            raise ManifestError(f"delegation provider {name} must use canonical kebab-case")
        if not isinstance(spec, dict):
            raise ManifestError(f"delegation provider {name} must be an object")
        if spec.get("status") not in {"active", "reference_only"}:
            raise ManifestError(f"delegation provider {name} has invalid status")
        for field in ("recipient_profile", "default_role_family"):
            if field not in spec:
                raise ManifestError(f"delegation provider {name} is missing {field}")
    for field in ("enabled", "require_same_model_as_manager"):
        if not isinstance(nested.get(field), bool):
            raise ManifestError(f"delegation.yaml nested_delegation.{field} must be boolean")
    for field in ("max_depth", "max_child_workers_per_manager"):
        value = nested.get(field)
        if not isinstance(value, int) or value < 0:
            raise ManifestError(f"delegation.yaml nested_delegation.{field} must be a nonnegative integer")


def validate_runtime_file(entry: dict[str, Any], runtime_source_root: Path, owner: str) -> None:
    for field in ("source", "target", "platforms", "type", "newline", "mode"):
        if field not in entry:
            raise ManifestError(f"{owner} runtime file is missing {field}")
    source = runtime_source_root / entry["source"]
    if not source.is_file():
        raise ManifestError(f"{owner} runtime source does not exist: {entry['source']}")
    if Path(entry["source"]).is_absolute() or ".." in Path(entry["source"]).parts:
        raise ManifestError(f"{owner} runtime source must stay under canonical/runtime: {entry['source']}")
    if Path(entry["target"]).is_absolute() or ".." in Path(entry["target"]).parts:
        raise ManifestError(f"{owner} runtime target must be relative and contained: {entry['target']}")


def validate_runtime_smoke_contract(skill: str, smoke: Any) -> None:
    if not isinstance(smoke, dict):
        raise ManifestError(f"runtime skill {skill} smoke must be an object")
    if smoke.get("schema") != "runtime-smoke.v1":
        raise ManifestError(f"runtime skill {skill} smoke schema must be runtime-smoke.v1")
    if smoke.get("mode") != "offline":
        raise ManifestError(f"runtime skill {skill} smoke mode must be offline")
    command = smoke.get("command")
    if not isinstance(command, (dict, str)):
        raise ManifestError(f"runtime skill {skill} smoke command must be a string or object")
    commands = command.values() if isinstance(command, dict) else [command]
    for target in commands:
        if not isinstance(target, str) or not target.startswith("workspace/"):
            raise ManifestError(f"runtime skill {skill} smoke command must be workspace-relative")
        path = PurePosixPath(target)
        if path.is_absolute() or ".." in path.parts:
            raise ManifestError(f"runtime skill {skill} smoke command must stay under workspace")
    if not isinstance(smoke.get("args", []), list):
        raise ManifestError(f"runtime skill {skill} smoke args must be a list")
    timeout = smoke.get("timeout_seconds")
    if not isinstance(timeout, int) or timeout <= 0 or timeout > 120:
        raise ManifestError(f"runtime skill {skill} smoke timeout_seconds must be between 1 and 120")
    safety = smoke.get("safety")
    if not isinstance(safety, dict):
        raise ManifestError(f"runtime skill {skill} smoke safety must be an object")
    required_forbidden = ("network", "live_api", "package_install", "server_start", "config_write", "real_secrets")
    for field in required_forbidden:
        if safety.get(field) != "forbidden":
            raise ManifestError(f"runtime skill {skill} smoke safety.{field} must be forbidden")


def validate_runtime_smoke_coverage(skill: str, spec: dict[str, Any]) -> None:
    coverage = spec.get("smoke_coverage")
    if not isinstance(coverage, dict):
        raise ManifestError(f"runtime skill {skill} is missing smoke_coverage")
    status = coverage.get("status")
    allowed = {"offline-smoke", "doctor-only", "manual-native", "static-only", "unsupported", "not-applicable"}
    if status not in allowed:
        raise ManifestError(f"runtime skill {skill} smoke_coverage.status is invalid")
    reason = coverage.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ManifestError(f"runtime skill {skill} smoke_coverage.reason is required")
    if "smoke" in spec and status != "offline-smoke":
        raise ManifestError(f"runtime skill {skill} has smoke contract but smoke_coverage is not offline-smoke")
    if "smoke" not in spec and status == "offline-smoke":
        raise ManifestError(f"runtime skill {skill} smoke_coverage offline-smoke requires a smoke contract")


def skill_names(manifests: dict[str, Any]) -> list[str]:
    return sorted(manifests["skills"]["skills"])
