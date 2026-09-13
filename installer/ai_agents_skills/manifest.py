from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
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
RUNTIME_PYTHON_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
RUNTIME_PYTHON_PROVISIONS = frozenset({"default", "opt-in"})


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
            validate_runtime_smoke_contract(skill, spec["smoke"], runtime_source_root)
        for kind in ("functional_smoke", "live_check"):
            if kind in spec:
                validate_runtime_case_collection(skill, spec[kind], kind, runtime_source_root)
        if "python" in spec:
            validate_runtime_python_block(skill, spec["python"], runtime_source_root)

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


RUNTIME_SMOKE_COMMON_KEYS = frozenset({
    "schema", "command", "args", "timeout_seconds", "expect", "env", "fixtures",
})
RUNTIME_SMOKE_KEYS = RUNTIME_SMOKE_COMMON_KEYS | {
    "mode", "safety", "writes", "env_canaries", "secret_file_canaries",
}
RUNTIME_FUNCTIONAL_SMOKE_KEYS = RUNTIME_SMOKE_COMMON_KEYS | {"requires_python_modules", "safety"}
RUNTIME_LIVE_CHECK_KEYS = frozenset({
    "schema", "command", "args", "timeout_seconds", "read_only", "requires", "expect",
})
RUNTIME_SMOKE_RESERVED_ENV = frozenset({
    "HOME", "PATH", "AAS_RUNTIME_ROOT", "AAS_RUNTIME_WORKSPACE",
    "AAS_ALLOW_EXTERNAL_RUNTIME_WORKSPACE", "AAS_SKILL_VENV",
    "AAS_RUNTIME_PYTHON_PREFIX", "OPENCLAW_WORKSPACE",
    # The harness scrubs these before applying contract env, so contracts must
    # not restore interpreter overrides through that second step.
    "AAS_RUNTIME_PYTHON", "DOCLING_PYTHON", "PYTHONPATH", "VIRTUAL_ENV",
})
RUNTIME_POINTER_AUTHORITIES = frozenset({
    "AAS_SKILL_SECRETS_FILE", "AAS_COMPUTE_SECRETS_FILE", "AAS_PROVIDER_SECRETS_FILE",
    "AAS_ZOTERO_SECRETS_FILE", "AAS_CALIBRE_SECRETS_FILE", "AAS_FILE_DELIVERY_SECRETS_FILE",
    "SEND_EMAIL_SECRETS_FILE", "REMOTE_BRIDGE_SECRETS_FILE",
})
# Agent homes a live check must never pin, one per install target in manifest/target-state.yaml.
# The contract is shared by every target, so a literal `~/.openclaw/...` gates all of them on one
# agent's configuration; `{workspace}` expands per runtime root and is the portable form.
LIVE_CHECK_AGENT_HOMES = (".openclaw", ".codex", ".claude", ".deepseek", ".copilot", ".gemini",
                          ".grok", ".kimi-code", ".aider", ".chatgpt-local-coder", ".config/opencode")
RUNTIME_LIVE_MUTATING_VERBS = {
    "zotero": frozenset({"add", "update", "trash", "delete", "send", "purge"}),
    "calibre": frozenset({"add", "update", "add-tag", "remove-tag", "remove", "sync", "clean", "convert"}),
    "docling": frozenset({"ocrspace-smoke"}),
    "kaggle-research-compute": frozenset({"push", "run", "fetch"}),
    "hetzner-research-compute": frozenset({"up", "down", "kill"}),
    "modal-research-compute": frozenset({"submit", "run", "cancel", "deploy"}),
    "send-email": frozenset({"send"}),
    "remote-bridge": frozenset({"send", "approve"}),
    "submission-venue-selector": frozenset({"run", "select"}),
    "vnthuquan": frozenset({"download"}),
}
RUNTIME_JSON_TYPES = frozenset({"null", "boolean", "object", "array", "number", "integer", "string"})
RUNTIME_JSON_ASSERTION_OPS = frozenset({
    "equals", "regex", "exists", "absent", "min_len", "equals_path", "contains",
    "type", "minimum", "set_equals",
})
RUNTIME_EXPECT_KEYS = frozenset({
    "exit_code", "stdout_regex", "stderr_regex", "stdout_not_regex", "stderr_not_regex",
    "stdout_json", "files", "alternatives",
})
RUNTIME_JSON_PATH_RE = re.compile(
    r"(?:[^.\[\]\s]+(?:\[(?:[0-9]+|\*)\])*|(?:\[(?:[0-9]+|\*)\])+)"
    r"(?:\.[^.\[\]\s]+(?:\[(?:[0-9]+|\*)\])*)*"
)
RUNTIME_ENV_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]*")
RUNTIME_SECRET_NAME_RE = re.compile(r"TOKEN|SECRET|PASSWORD|CREDENTIAL|API_KEY|AUTH")


def _pinned_agent_homes(path: str) -> list[str]:
    # Case-folded so a home still matches on the case-insensitive filesystems macOS and Windows use.
    parts = tuple(part.lower() for part in PurePosixPath(path.replace("\\", "/")).parts)
    homes = []
    for home in LIVE_CHECK_AGENT_HOMES:
        run = tuple(home.split("/"))
        if any(parts[start:start + len(run)] == run for start in range(len(parts) - len(run) + 1)):
            homes.append(home)
    return homes


def _runtime_object(value: Any, allowed: set[str] | frozenset[str], owner: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{owner} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ManifestError(f"{owner} keys must be strings")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ManifestError(f"{owner} has unknown keys: {', '.join(unknown)}")
    return value


def _runtime_relative_path(value: Any, owner: str, *, smoke_relative: bool = True) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ManifestError(f"{owner} must be a non-empty relative path")
    relative = value.removeprefix("{smoke_dir}/") if smoke_relative else value
    path = PurePosixPath(relative)
    if (
        path.is_absolute() or path == PurePosixPath(".") or ".." in path.parts
        or "\\" in relative or re.match(r"^[A-Za-z]:", relative)
        or "{" in relative or "}" in relative
    ):
        raise ManifestError(f"{owner} must stay under {'smoke_dir' if smoke_relative else 'canonical/runtime'}")
    return relative


def _runtime_regex(value: Any, owner: str) -> None:
    if not isinstance(value, str):
        raise ManifestError(f"{owner} must be a regex string")
    try:
        re.compile(value, re.MULTILINE)
    except re.error as exc:
        raise ManifestError(f"{owner} has an invalid regex: {exc}") from exc


def _runtime_json_path(value: Any, owner: str) -> None:
    # The empty path selects the document itself, preserving root-type checks.
    if not isinstance(value, str) or (value and not RUNTIME_JSON_PATH_RE.fullmatch(value)):
        raise ManifestError(f"{owner} must be a dotted JSON path with [N] or [*] segments")


def _runtime_json_value(value: Any, owner: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            _runtime_json_value(item, owner)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _runtime_json_value(item, owner)
        return
    raise ManifestError(f"{owner} must be a JSON value")


def validate_runtime_json_assertions(assertions: Any, owner: str = "runtime stdout_json") -> None:
    if not isinstance(assertions, list):
        raise ManifestError(f"{owner} must be a list")
    for index, assertion in enumerate(assertions):
        label = f"{owner}[{index}]"
        _runtime_object(assertion, RUNTIME_JSON_ASSERTION_OPS | {"path"}, label)
        if "path" not in assertion or len(assertion) != 2:
            raise ManifestError(f"{label} must contain path and exactly one assertion operator")
        _runtime_json_path(assertion["path"], f"{label}.path")
        operator = next(key for key in assertion if key != "path")
        value = assertion[operator]
        if operator in {"equals", "contains"}:
            _runtime_json_value(value, f"{label}.{operator}")
        elif operator == "regex":
            _runtime_regex(value, f"{label}.regex")
        elif operator in {"exists", "absent"}:
            if value is not True:
                raise ManifestError(f"{label}.{operator} must be true")
        elif operator == "min_len":
            if type(value) is not int or value < 0:
                raise ManifestError(f"{label}.min_len must be a nonnegative integer")
        elif operator == "equals_path":
            _runtime_json_path(value, f"{label}.equals_path")
        elif operator == "type":
            if not isinstance(value, str) or value not in RUNTIME_JSON_TYPES:
                raise ManifestError(f"{label}.type must be a JSON type name")
        elif operator == "minimum":
            if type(value) not in {int, float} or (isinstance(value, float) and not math.isfinite(value)):
                raise ManifestError(f"{label}.minimum must be a finite number")
        elif operator == "set_equals":
            if not isinstance(value, list):
                raise ManifestError(f"{label}.set_equals must be a list of JSON values")
            _runtime_json_value(value, f"{label}.set_equals")


def validate_runtime_expect(expect: Any, owner: str = "runtime smoke expect", *, alternatives: bool = True) -> None:
    allowed = RUNTIME_EXPECT_KEYS if alternatives else RUNTIME_EXPECT_KEYS - {"alternatives"}
    _runtime_object(expect, allowed, owner)
    codes = expect.get("exit_code", 0)
    codes = codes if isinstance(codes, list) else [codes]
    if not codes or any(type(code) is not int for code in codes):
        raise ManifestError(f"{owner}.exit_code must be an integer or a non-empty integer list")
    for field in ("stdout_regex", "stderr_regex", "stdout_not_regex", "stderr_not_regex"):
        if field not in expect:
            continue
        expressions = expect[field]
        expressions = expressions if isinstance(expressions, list) else [expressions]
        for expression in expressions:
            _runtime_regex(expression, f"{owner}.{field}")
    if "stdout_json" in expect:
        validate_runtime_json_assertions(expect["stdout_json"], f"{owner}.stdout_json")
    if "files" in expect:
        if not isinstance(expect["files"], list):
            raise ManifestError(f"{owner}.files must be a list")
        for index, entry in enumerate(expect["files"]):
            label = f"{owner}.files[{index}]"
            if isinstance(entry, str):
                _runtime_relative_path(entry, label)
                continue
            _runtime_object(entry, {"path", "regex", "json", "type"}, label)
            if "path" not in entry or len(entry) != 2:
                raise ManifestError(f"{label} must contain path and exactly one file operator")
            _runtime_relative_path(entry["path"], f"{label}.path")
            if "regex" in entry:
                _runtime_regex(entry["regex"], f"{label}.regex")
            elif "json" in entry:
                validate_runtime_json_assertions(entry["json"], f"{label}.json")
            elif entry["type"] not in ("file", "directory"):
                raise ManifestError(f"{label}.type must be file or directory")
    if "alternatives" in expect:
        branches = expect["alternatives"]
        if not isinstance(branches, list) or not branches:
            raise ManifestError(f"{owner}.alternatives must be a non-empty list")
        for index, branch in enumerate(branches):
            validate_runtime_expect(branch, f"{owner}.alternatives[{index}]", alternatives=False)


def _runtime_environment(value: Any, owner: str, *, canaries: bool = False) -> None:
    if not isinstance(value, dict):
        raise ManifestError(f"{owner} must be an object")
    for key, entry in value.items():
        if not isinstance(key, str) or not RUNTIME_ENV_NAME_RE.fullmatch(key):
            raise ManifestError(f"{owner} keys must be uppercase environment names")
        if not isinstance(entry, str):
            raise ManifestError(f"{owner}.{key} must be a string")
        if key in RUNTIME_SMOKE_RESERVED_ENV or (not canaries and RUNTIME_SECRET_NAME_RE.search(key)):
            raise ManifestError(f"{owner} may not set reserved or secret-shaped name {key}")


def _runtime_fixtures(fixtures: Any, owner: str, runtime_source_root: Path) -> None:
    if not isinstance(fixtures, list):
        raise ManifestError(f"{owner} must be a list")
    root = Path(os.path.realpath(runtime_source_root))
    for index, fixture in enumerate(fixtures):
        label = f"{owner}[{index}]"
        _runtime_object(fixture, {"to", "content", "copy_from"}, label)
        if "to" not in fixture or len(fixture) != 2:
            raise ManifestError(f"{label} must contain to and exactly one of content or copy_from")
        _runtime_relative_path(fixture["to"], f"{label}.to")
        if "content" in fixture:
            if not isinstance(fixture["content"], str):
                raise ManifestError(f"{label}.content must be a string")
        else:
            relative = _runtime_relative_path(fixture["copy_from"], f"{label}.copy_from", smoke_relative=False)
            source = Path(os.path.realpath(root / relative))
            if not source.is_relative_to(root) or not source.is_file():
                raise ManifestError(f"{label}.copy_from must name a file under canonical/runtime")


def _validate_runtime_case(skill: str, contract: Any, kind: str, runtime_source_root: Path | None) -> None:
    owner = f"runtime skill {skill} {kind}"
    allowed, schema, maximum_timeout = {
        "smoke": (RUNTIME_SMOKE_KEYS, "runtime-smoke.v1", 120),
        "functional_smoke": (RUNTIME_FUNCTIONAL_SMOKE_KEYS, "runtime-functional-smoke.v1", 300),
        "live_check": (RUNTIME_LIVE_CHECK_KEYS, "runtime-live-check.v1", 600),
    }[kind]
    _runtime_object(contract, allowed, owner)
    if contract.get("schema") != schema:
        raise ManifestError(f"{owner} schema must be {schema}")
    command = contract.get("command")
    if not isinstance(command, (dict, str)) or not command:
        raise ManifestError(f"{owner} command must be a string or non-empty object")
    if isinstance(command, dict) and any(not isinstance(key, str) or not key for key in command):
        raise ManifestError(f"{owner} command platform names must be non-empty strings")
    for target in command.values() if isinstance(command, dict) else [command]:
        if not isinstance(target, str) or not target.startswith("workspace/"):
            raise ManifestError(f"{owner} command must be workspace-relative")
        _runtime_relative_path(target.removeprefix("workspace/"), f"{owner} command", smoke_relative=False)
        if kind == "live_check" and not target.startswith(f"workspace/skills/{skill}/"):
            raise ManifestError(f"{owner} command must stay under the declared skill's workspace/skills/{skill}/ namespace")
    args = contract.get("args", [])
    if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
        raise ManifestError(f"{owner} args must be a list of strings")
    timeout = contract.get("timeout_seconds")
    if type(timeout) is not int or not 1 <= timeout <= maximum_timeout:
        raise ManifestError(f"{owner} timeout_seconds must be between 1 and {maximum_timeout}")
    if "expect" not in contract:
        raise ManifestError(f"{owner} requires expect")
    validate_runtime_expect(contract["expect"], f"{owner} expect")
    if "env" in contract:
        _runtime_environment(contract["env"], f"{owner} env")
    if "fixtures" in contract:
        _runtime_fixtures(contract["fixtures"], f"{owner} fixtures", runtime_source_root or REPO_ROOT / "canonical" / "runtime")
    if kind == "smoke":
        if contract.get("mode") != "offline":
            raise ManifestError(f"{owner} mode must be offline")
        required = {"network", "live_api", "package_install", "server_start", "config_write", "real_secrets"}
        safety = _runtime_object(contract.get("safety"), required | {"provider_cli", "subagent_spawn"}, f"{owner} safety")
        for field in required | set(safety):
            if safety.get(field) != "forbidden":
                raise ManifestError(f"{owner} safety.{field} must be forbidden")
        writes = contract.get("writes", [])
        if not isinstance(writes, list):
            raise ManifestError(f"{owner} writes must be a list of smoke_dir-relative paths")
        for path in writes:
            _runtime_relative_path(path, f"{owner} writes")
        if "env_canaries" in contract:
            _runtime_environment(contract["env_canaries"], f"{owner} env_canaries", canaries=True)
        if "secret_file_canaries" in contract:
            canary = _runtime_object(contract["secret_file_canaries"], {"pointer_env", "format", "values"}, f"{owner} secret_file_canaries")
            if not isinstance(canary.get("pointer_env"), str) or canary["pointer_env"] not in RUNTIME_POINTER_AUTHORITIES:
                raise ManifestError(f"{owner} secret_file_canaries.pointer_env must name a launcher pointer authority")
            if canary.get("format") not in ("env", "json"):
                raise ManifestError(f"{owner} secret_file_canaries.format must be env or json")
            if not canary.get("values"):
                raise ManifestError(f"{owner} secret_file_canaries.values must be non-empty")
            _runtime_environment(canary["values"], f"{owner} secret_file_canaries.values", canaries=True)
    elif kind == "functional_smoke":
        modules = contract.get("requires_python_modules")
        if not isinstance(modules, list) or any(not isinstance(module, str) or not RUNTIME_PYTHON_MODULE_RE.fullmatch(module) for module in modules):
            raise ManifestError(f"{owner} requires_python_modules must be a list of module names")
        safety = _runtime_object(contract.get("safety"), {"network"}, f"{owner} safety")
        if safety.get("network") not in ("forbidden", "loopback"):
            raise ManifestError(f"{owner} safety.network must be forbidden or loopback")
    else:
        if contract.get("read_only") is not True:
            raise ManifestError(f"{owner} read_only must be true")
        requires = _runtime_object(contract.get("requires"), {"pointer_env", "config_files", "network"}, f"{owner} requires")
        if requires.get("network") is not True:
            raise ManifestError(f"{owner} requires.network must be true")
        pointers = requires.get("pointer_env", [])
        if not isinstance(pointers, list) or any(not isinstance(pointer, str) or pointer not in RUNTIME_POINTER_AUTHORITIES for pointer in pointers):
            raise ManifestError(f"{owner} requires.pointer_env must list launcher pointer authorities")
        paths = requires.get("config_files", [])
        if not isinstance(paths, list) or any(not isinstance(path, str) or not path for path in paths):
            raise ManifestError(f"{owner} requires.config_files must be a list of non-empty paths")
        pinned = sorted({home for path in paths for home in _pinned_agent_homes(path)})
        if pinned:
            raise ManifestError(
                f"{owner} requires.config_files pins an agent home ({', '.join(pinned)}); "
                "use {workspace} so each runtime root gates on its own config"
            )
        mutating = set(args) & RUNTIME_LIVE_MUTATING_VERBS.get(skill, frozenset())
        if skill == "hetzner-research-compute" and "reap" in args and "--dry-run" not in args:
            mutating.add("reap")
        if mutating:
            raise ManifestError(f"{owner} contains mutating verb: {', '.join(sorted(mutating))}")


def validate_runtime_smoke_contract(skill: str, smoke: Any, runtime_source_root: Path | None = None) -> None:
    _validate_runtime_case(skill, smoke, "smoke", runtime_source_root)


def validate_runtime_functional_smoke_contract(skill: str, contract: Any, runtime_source_root: Path | None = None) -> None:
    _validate_runtime_case(skill, contract, "functional_smoke", runtime_source_root)


def validate_runtime_live_check_contract(skill: str, contract: Any, runtime_source_root: Path | None = None) -> None:
    _validate_runtime_case(skill, contract, "live_check", runtime_source_root)


def validate_runtime_case_collection(skill: str, cases: Any, kind: str, runtime_source_root: Path | None = None) -> None:
    if not isinstance(cases, dict) or not cases:
        raise ManifestError(f"runtime skill {skill} {kind} must be a non-empty object")
    for name, contract in cases.items():
        if not isinstance(name, str) or not name.strip():
            raise ManifestError(f"runtime skill {skill} {kind} case names must be non-empty strings")
        _validate_runtime_case(skill, contract, kind, runtime_source_root)


def validate_runtime_python_block(skill: str, block: Any, runtime_source_root: Path) -> None:
    if not isinstance(block, dict):
        raise ManifestError(f"runtime skill {skill} python must be an object")
    unknown = sorted(set(block) - {"requirements", "modules", "provision"})
    if unknown:
        raise ManifestError(f"runtime skill {skill} python has unknown keys: {', '.join(unknown)}")
    requirements = block.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        raise ManifestError(f"runtime skill {skill} python requirements must be a non-empty list")
    root = Path(os.path.realpath(runtime_source_root))
    for requirement in requirements:
        if not isinstance(requirement, str) or not requirement:
            raise ManifestError(f"runtime skill {skill} python requirements entries must be non-empty strings")
        path = PurePosixPath(requirement)
        windows_path = PureWindowsPath(requirement)
        components = requirement.split("/")
        has_windows_alias = any(
            ":" in component
            or component.endswith((".", " "))
            or PureWindowsPath(component).is_reserved()
            for component in components
        )
        current = root
        has_case_alias = False
        for component in path.parts:
            try:
                names = [entry.name for entry in current.iterdir()]
            except OSError as exc:
                raise ManifestError(
                    f"runtime skill {skill} python requirements path spelling could not be verified "
                    f"under canonical/runtime: {requirement}"
                ) from exc
            if component in names:
                current /= component
                continue
            if any(name.casefold() == component.casefold() for name in names):
                has_case_alias = True
            break
        if (
            path.is_absolute()
            or ".." in path.parts
            or "" in components
            or "\\" in requirement
            or bool(windows_path.drive)
            or bool(windows_path.root)
            or has_windows_alias
            or has_case_alias
        ):
            raise ManifestError(
                f"runtime skill {skill} python requirements must be relative paths under canonical/runtime: {requirement}"
            )
        real = Path(os.path.realpath(root / requirement))
        if not real.is_relative_to(root):
            raise ManifestError(f"runtime skill {skill} python requirements must stay under canonical/runtime: {requirement}")
        if not real.is_file():
            raise ManifestError(f"runtime skill {skill} python requirements file does not exist: {requirement}")
    modules = block.get("modules")
    if not isinstance(modules, list):
        raise ManifestError(f"runtime skill {skill} python modules must be a list")
    for module in modules:
        if not isinstance(module, str) or not RUNTIME_PYTHON_MODULE_RE.fullmatch(module):
            raise ManifestError(f"runtime skill {skill} python modules entry is not a module name: {module!r}")
    provision = block.get("provision", "default")
    if not isinstance(provision, str) or provision not in RUNTIME_PYTHON_PROVISIONS:
        raise ManifestError(f"runtime skill {skill} python provision must be default or opt-in")


def validate_runtime_smoke_coverage(skill: str, spec: dict[str, Any]) -> None:
    coverage = spec.get("smoke_coverage")
    if not isinstance(coverage, dict):
        raise ManifestError(f"runtime skill {skill} is missing smoke_coverage")
    status = coverage.get("status")
    allowed = {"offline-smoke", "venv-smoke", "doctor-only", "manual-native", "static-only", "unsupported", "not-applicable"}
    if not isinstance(status, str) or status not in allowed:
        raise ManifestError(f"runtime skill {skill} smoke_coverage.status is invalid")
    reason = coverage.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ManifestError(f"runtime skill {skill} smoke_coverage.reason is required")
    if "smoke" in spec and status != "offline-smoke":
        raise ManifestError(f"runtime skill {skill} has smoke contract but smoke_coverage is not offline-smoke")
    if "smoke" not in spec and status == "offline-smoke":
        raise ManifestError(f"runtime skill {skill} smoke_coverage offline-smoke requires a smoke contract")
    if "functional_smoke" in spec and status not in {"offline-smoke", "venv-smoke"}:
        raise ManifestError(f"runtime skill {skill} functional_smoke requires offline-smoke or venv-smoke coverage")
    if status == "venv-smoke" and not spec.get("functional_smoke"):
        raise ManifestError(f"runtime skill {skill} smoke_coverage venv-smoke requires functional_smoke")


def skill_names(manifests: dict[str, Any]) -> list[str]:
    return sorted(manifests["skills"]["skills"])
