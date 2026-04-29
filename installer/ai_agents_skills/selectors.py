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


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]
