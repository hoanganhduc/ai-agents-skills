from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


SKIP_DIRS = {".git", ".venv", "__pycache__", "_build", ".mypy_cache", ".pytest_cache"}
RESERVED_WINDOWS_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def main() -> int:
    root = Path(".")
    errors: list[str] = []
    files = tracked_files(root)
    errors.extend(check_python_parse(files))
    errors.extend(check_shell_syntax(files))
    errors.extend(check_powershell_syntax(files))
    errors.extend(check_windows_path_hazards(files))
    errors.extend(check_newline_policy(files))
    if errors:
        print("static-check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("static-check=ok")
    return 0


def tracked_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def check_python_parse(files: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in files:
        if path.suffix != ".py":
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception as exc:  # SyntaxError and UnicodeDecodeError should both fail.
            errors.append(f"python-parse:{path}:{exc}")
    return errors


def check_shell_syntax(files: list[Path]) -> list[str]:
    bash = shutil.which("bash")
    if not bash:
        return []
    errors: list[str] = []
    for path in files:
        if path.suffix != ".sh":
            continue
        result = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True)
        if result.returncode != 0:
            errors.append(f"bash-syntax:{path}:{result.stderr.strip()}")
    return errors


def check_powershell_syntax(files: list[Path]) -> list[str]:
    shell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
    if not shell:
        return []
    errors: list[str] = []
    for path in files:
        if path.suffix.lower() != ".ps1":
            continue
        result = subprocess.run(
            [shell, "-NoProfile", "-Command", powershell_parse_script(path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            errors.append(f"powershell-syntax:{path}:{(result.stderr or result.stdout).strip()}")
    return errors


def powershell_parse_script(path: Path) -> str:
    absolute_path = powershell_single_quoted(str(path.resolve()))
    return (
        "$tokens=$null; $errs=$null; "
        f"$path={absolute_path}; "
        "[System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errs) | Out-Null; "
        "if ($errs.Count -gt 0) { $errs | ForEach-Object { Write-Error $_.Message }; exit 1 }"
    )


def powershell_single_quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def check_windows_path_hazards(files: list[Path]) -> list[str]:
    errors: list[str] = []
    by_parent: dict[Path, dict[str, Path]] = defaultdict(dict)
    for path in files:
        for part in path.parts:
            if part in {".", ".."}:
                errors.append(f"path-hazard:{path}:dot segment")
            if part.endswith((" ", ".")):
                errors.append(f"path-hazard:{path}:trailing space/dot in {part!r}")
            stem = part.split(".")[0].lower()
            if stem in RESERVED_WINDOWS_NAMES:
                errors.append(f"path-hazard:{path}:reserved windows name {part!r}")
            if ":" in part:
                errors.append(f"path-hazard:{path}:colon/ADS marker in {part!r}")
        parent = path.parent
        key = os.path.normcase(path.name).casefold()
        previous = by_parent[parent].get(key)
        if previous is not None and previous.name != path.name:
            errors.append(f"path-hazard:{path}:case collision with {previous}")
        by_parent[parent][key] = path
    return errors


def check_newline_policy(files: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in files:
        if path.suffix.lower() not in {".sh", ".py", ".md", ".yaml", ".yml", ".json", ".toml", ".ps1", ".bat", ".html"}:
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            errors.append(f"newline:{path}:{exc}")
            continue
        if path.suffix.lower() in {".sh", ".py", ".html"} and b"\r\n" in data:
            errors.append(f"newline:{path}:expected LF for Python/POSIX shell/HTML")
    return errors


if __name__ == "__main__":
    raise SystemExit(main())
