from __future__ import annotations

from typing import Any


def canonical_skill_name(name: str, manifests: dict[str, Any]) -> str | None:
    skills = manifests["skills"]["skills"]
    if name in skills:
        return name
    aliases = manifests["skills"].get("legacy_aliases", {})
    for canonical, legacy_names in aliases.items():
        if name in legacy_names:
            return canonical
    return None


def resolve_skills(args: Any, manifests: dict[str, Any]) -> list[str]:
    if getattr(args, "no_skills", False):
        return []
    skills = manifests["skills"]["skills"]
    profiles = manifests["profiles"]["profiles"]
    default_profile = manifests["profiles"].get("default_profile", "research-core")

    selected: set[str] = set()
    raw_skills: list[str] = []
    if getattr(args, "skill", None):
        raw_skills.append(args.skill)
    if getattr(args, "skills", None):
        raw_skills.extend(split_csv(args.skills))

    if raw_skills:
        for raw in raw_skills:
            canonical = canonical_skill_name(raw, manifests)
            if canonical is None:
                raise ValueError(f"unknown skill: {raw}")
            selected.add(canonical)
    else:
        profile_names = split_csv(getattr(args, "profile", None) or default_profile)
        for profile_name in profile_names:
            if profile_name not in profiles:
                raise ValueError(f"unknown profile: {profile_name}")
            profile_skills = profiles[profile_name]["skills"]
            if profile_skills == ["*"]:
                selected.update(skills)
            else:
                selected.update(profile_skills)

    for excluded in split_csv(getattr(args, "exclude", None)):
        canonical = canonical_skill_name(excluded, manifests)
        if canonical:
            selected.discard(canonical)

    return sorted(selected)


def resolve_artifacts(args: Any, manifests: dict[str, Any]) -> list[tuple[str, str]]:
    selected: set[tuple[str, str]] = set()
    raw_artifacts: list[str] = []
    if getattr(args, "artifact", None):
        raw_artifacts.append(args.artifact)
    if getattr(args, "artifacts", None):
        raw_artifacts.extend(split_csv(args.artifacts))

    for raw in raw_artifacts:
        selected.add(canonical_artifact_name(raw, manifests))

    profile_names = split_csv(getattr(args, "artifact_profile", None))
    profiles = manifests.get("artifacts", {}).get("artifact_profiles", {})
    for profile_name in profile_names:
        if profile_name not in profiles:
            raise ValueError(f"unknown artifact profile: {profile_name}")
        for item in profiles[profile_name].get("artifacts", []):
            selected.add(canonical_artifact_name(item, manifests))

    for excluded in split_csv(getattr(args, "exclude_artifact", None)):
        selected.discard(canonical_artifact_name(excluded, manifests))

    return sorted(selected)


def canonical_artifact_name(raw: str, manifests: dict[str, Any]) -> tuple[str, str]:
    if ":" not in raw:
        matches = [
            (artifact_type, name)
            for artifact_type, specs in manifests.get("artifacts", {}).get("artifacts", {}).items()
            for name in specs
            if name == raw
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError(f"unknown artifact: {raw}")
        raise ValueError(f"ambiguous artifact name, use type:name: {raw}")
    artifact_type, name = raw.split(":", 1)
    specs = manifests.get("artifacts", {}).get("artifacts", {})
    if artifact_type not in specs or name not in specs[artifact_type]:
        raise ValueError(f"unknown artifact: {raw}")
    return artifact_type, name


def artifact_dependency_skills(artifacts: list[tuple[str, str]], manifests: dict[str, Any]) -> set[str]:
    specs = manifests.get("artifacts", {}).get("artifacts", {})
    dependencies: set[str] = set()
    for artifact_type, name in artifacts:
        dependencies.update(specs[artifact_type][name].get("depends_on_skills", []))
    return dependencies


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]
