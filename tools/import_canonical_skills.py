from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from installer.ai_agents_skills.manifest import REPO_ROOT
from installer.ai_agents_skills.sanitize import sanitize_text


DEFAULT_INCLUDE_FILES = {
    "SKILL.md",
    "README.md",
    "EXECUTION.md",
    "MODEL_TIERS.md",
    "MODEL_TIERS.example.md",
    "TEMPLATES.md",
}
DEFAULT_INCLUDE_DIRS = {"references", "assets", "scripts", "templates", "agents"}
EXCLUDED_PARTS = {"implementation", "integration", "plans", "research", "__pycache__", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".bak", ".tmp"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Import local skill docs into canonical/skills safely.")
    parser.add_argument("--source", type=Path, default=Path.home() / ".codex" / "skills")
    parser.add_argument("--dest", type=Path, default=REPO_ROOT / "canonical" / "skills")
    parser.add_argument("--manifest", type=Path, default=REPO_ROOT / "manifest" / "skills.yaml")
    parser.add_argument("--skill")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-dest-outside-repo", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    skills = list(manifest["skills"])
    if args.skill:
        if args.skill not in manifest["skills"]:
            raise ValueError(f"unknown manifest skill: {args.skill}")
        skills = [args.skill]
    aliases = manifest.get("legacy_aliases", {})
    if not args.dry_run:
        validate_dest_root(args.dest, allow_outside_repo=args.allow_dest_outside_repo)

    results = []
    for skill in skills:
        source_dir = find_source_dir(args.source, skill, aliases.get(skill, []))
        if source_dir is None:
            results.append({"skill": skill, "status": "missing-source"})
            continue
        files = selected_files(source_dir)
        if not args.dry_run:
            write_skill(args.dest, skill, source_dir, files, allow_dest_outside_repo=args.allow_dest_outside_repo)
        results.append(
            {
                "skill": skill,
                "status": "imported" if not args.dry_run else "would-import",
                "source_name": source_dir.name,
                "file_count": len(files),
                "files": [str(path.relative_to(source_dir)) for path in files],
            }
        )
    print(json.dumps({"results": results}, indent=2, sort_keys=True))
    return 0


def find_source_dir(base: Path, skill: str, aliases: list[str]) -> Path | None:
    base = base.expanduser()
    if base.is_symlink():
        raise ValueError(f"refusing symlinked source root: {base}")
    for name in [skill, *aliases]:
        candidate = base / name
        skill_file = candidate / "SKILL.md"
        if candidate.is_symlink() or skill_file.is_symlink():
            raise ValueError(f"refusing symlinked source skill: {candidate}")
        if candidate.is_dir() and skill_file.is_file():
            return candidate
    return None


def selected_files(source_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in source_dir.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"refusing symlinked source file: {path}")
        if not path.is_file():
            continue
        rel = path.relative_to(source_dir)
        if any(part in EXCLUDED_PARTS for part in rel.parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES or path.name.startswith("."):
            continue
        if rel.name in DEFAULT_INCLUDE_FILES or rel.parts[0] in DEFAULT_INCLUDE_DIRS:
            files.append(path)
    return sorted(files)


def write_skill(
    dest: Path,
    skill: str,
    source_dir: Path,
    files: list[Path],
    *,
    allow_dest_outside_repo: bool = False,
) -> None:
    validate_skill_name(skill)
    validate_dest_root(dest, allow_outside_repo=allow_dest_outside_repo)
    target_dir = dest / skill
    if target_dir.is_symlink():
        raise ValueError(f"refusing symlinked target directory: {target_dir}")
    if not path_within(dest, target_dir):
        raise ValueError(f"refusing target outside destination root: {target_dir}")
    if target_dir.exists():
        shutil.rmtree(target_dir)
    for source in files:
        rel = source.relative_to(source_dir)
        target = target_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if is_text_file(source):
            text = source.read_text(encoding="utf-8", errors="replace")
            target.write_text(sanitize_text(text, canonical_name=skill if rel.name == "SKILL.md" else None), encoding="utf-8")
        else:
            shutil.copy2(source, target)


def is_text_file(path: Path) -> bool:
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    return True


def validate_dest_root(dest: Path, *, allow_outside_repo: bool = False) -> None:
    if allow_outside_repo:
        return
    expected = (REPO_ROOT / "canonical" / "skills").resolve()
    actual = dest.expanduser().resolve()
    if actual != expected:
        raise ValueError(f"refusing destination outside canonical skills root: {dest}")


def validate_skill_name(skill: str) -> None:
    if not skill or skill in {".", ".."} or Path(skill).name != skill:
        raise ValueError(f"invalid skill name: {skill!r}")
    if any(separator and separator in skill for separator in (os.sep, os.altsep)):
        raise ValueError(f"invalid skill name: {skill!r}")


def path_within(root: Path, path: Path) -> bool:
    root_resolved = root.expanduser().resolve()
    path_resolved = path.expanduser().resolve()
    return path_resolved == root_resolved or root_resolved in path_resolved.parents


if __name__ == "__main__":
    raise SystemExit(main())
