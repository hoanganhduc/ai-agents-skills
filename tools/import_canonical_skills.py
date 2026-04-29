from __future__ import annotations

import argparse
import json
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
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    skills = list(manifest["skills"])
    if args.skill:
        skills = [args.skill]
    aliases = manifest.get("legacy_aliases", {})

    results = []
    for skill in skills:
        source_dir = find_source_dir(args.source, skill, aliases.get(skill, []))
        if source_dir is None:
            results.append({"skill": skill, "status": "missing-source"})
            continue
        files = selected_files(source_dir)
        if not args.dry_run:
            write_skill(args.dest, skill, source_dir, files)
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
    for name in [skill, *aliases]:
        candidate = base / name
        if (candidate / "SKILL.md").exists():
            return candidate
    return None


def selected_files(source_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in source_dir.rglob("*"):
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


def write_skill(dest: Path, skill: str, source_dir: Path, files: list[Path]) -> None:
    target_dir = dest / skill
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


if __name__ == "__main__":
    raise SystemExit(main())
