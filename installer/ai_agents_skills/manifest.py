from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = REPO_ROOT / "manifest"


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
    artifacts = load_json_yaml(MANIFEST_DIR / "artifacts.yaml")
    validate_manifests(skills, profiles, dependencies, artifacts)
    return {
        "skills": skills,
        "profiles": profiles,
        "dependencies": dependencies,
        "artifacts": artifacts,
    }


def validate_manifests(
    skills: dict[str, Any],
    profiles: dict[str, Any],
    dependencies: dict[str, Any],
    artifacts: dict[str, Any],
) -> None:
    if "skills" not in skills or not isinstance(skills["skills"], dict):
        raise ManifestError("skills.yaml must contain a skills object")
    if "profiles" not in profiles or not isinstance(profiles["profiles"], dict):
        raise ManifestError("profiles.yaml must contain a profiles object")
    if "tools" not in dependencies or not isinstance(dependencies["tools"], dict):
        raise ManifestError("dependencies.yaml must contain a tools object")
    packages = dependencies.get("packages", {})
    if not isinstance(packages, dict):
        raise ManifestError("dependencies.yaml packages must be an object")
    if "artifacts" not in artifacts or not isinstance(artifacts["artifacts"], dict):
        raise ManifestError("artifacts.yaml must contain an artifacts object")
    if "artifact_profiles" not in artifacts or not isinstance(artifacts["artifact_profiles"], dict):
        raise ManifestError("artifacts.yaml must contain an artifact_profiles object")

    for name, spec in skills["skills"].items():
        if not isinstance(spec, dict):
            raise ManifestError(f"skill {name} must be an object")
        for field in ("description", "profiles", "supported_agents", "verification"):
            if field not in spec:
                raise ManifestError(f"skill {name} is missing {field}")
        if "_" in name:
            raise ManifestError(f"skill {name} must use canonical kebab-case")
        declared_dependencies = set(dependencies["tools"]) | set(packages)
        for field in ("required_dependencies", "optional_dependencies"):
            for dependency in spec.get(field, []):
                if dependency not in declared_dependencies:
                    raise ManifestError(f"skill {name} references unknown dependency {dependency}")

    declared_artifacts = set()
    for artifact_type, by_name in artifacts["artifacts"].items():
        if not isinstance(by_name, dict):
            raise ManifestError(f"artifact type {artifact_type} must be an object")
        for name, spec in by_name.items():
            if "_" in name:
                raise ManifestError(f"artifact {artifact_type}:{name} must use canonical kebab-case")
            for field in ("description", "source", "supported_agents"):
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


def skill_names(manifests: dict[str, Any]) -> list[str]:
    return sorted(manifests["skills"]["skills"])
