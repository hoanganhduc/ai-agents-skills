from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import warnings
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
    errors.extend(check_subprocess_text_encoding(files))
    errors.extend(check_duplicate_keyword_spread(files))
    errors.extend(check_unclosed_file_handles(files))
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
            with warnings.catch_warnings():
                # An invalid escape sequence only warns, so a parse that merely
                # returns would let it through -- and it is a hard SyntaxError in a
                # future Python, so a file that warns today stops importing later.
                # CPython moved the diagnostic from DeprecationWarning to
                # SyntaxWarning in 3.12, and this runs on both, so promote either.
                warnings.simplefilter("error", SyntaxWarning)
                warnings.filterwarnings(
                    "error", category=DeprecationWarning, message="invalid escape sequence"
                )
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception as exc:  # SyntaxError and UnicodeDecodeError should both fail.
            errors.append(f"python-parse:{path}:{exc}")
    return errors


SUBPROCESS_TEXT_CALLS = {"run", "check_output", "check_call", "call", "Popen"}


def check_subprocess_text_encoding(files: list[Path]) -> list[str]:
    """A subprocess call that hands back str must say which codec produced it.

    With `text=True` and no `encoding=`, subprocess decodes the child's bytes with
    `locale.getpreferredencoding(False)` and errors='strict'. That is UTF-8 on the
    Linux runners and the ANSI code page on the Windows one, so the same child output
    becomes different strings per platform; and one byte the codec rejects -- a
    latin-1 copyright line in a version banner, an accented name in a TeX log --
    raises UnicodeDecodeError inside subprocess.run. Callers that wrap the call in
    `except Exception` then report a tool that exited 0 as failed or unknown, and
    callers that do not, crash.
    """

    errors: list[str] = []
    for path in files:
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception:
            continue  # check_python_parse owns reporting unparseable files.
        constants = module_dict_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in SUBPROCESS_TEXT_CALLS:
                continue
            if not (isinstance(func.value, ast.Name) and func.value.id == "subprocess"):
                continue
            names = {kw.arg for kw in node.keywords}
            textual = any(
                kw.arg in {"text", "universal_newlines"}
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            )
            # A module-level ``{"encoding": ..., "errors": ...}`` spread into the call
            # supplies the codec just as an inline keyword does.  Reading only inline
            # keywords made this check reject the shared constant, which pushes the
            # author into writing both -- and a keyword that arrives twice raises
            # TypeError at the call site, before the callee runs.
            spread = set()
            for keyword in node.keywords:
                if keyword.arg is None and isinstance(keyword.value, ast.Name):
                    spread |= constants.get(keyword.value.id, set())
            if textual and "encoding" not in names and "encoding" not in spread:
                errors.append(
                    f"subprocess-encoding:{path}:{node.lineno}:"
                    f"subprocess.{func.attr} decodes with the host locale; pass encoding="
                )
    return errors


def module_dict_constants(tree: ast.Module) -> dict[str, set[str]]:
    """Module-level names bound to a dict display, mapped to their string keys."""

    constants: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
            value = node.value
        else:
            continue
        if not isinstance(value, ast.Dict):
            continue
        keys = {
            key.value
            for key in value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        for target in targets:
            constants[target.id] = keys


    return constants


def check_duplicate_keyword_spread(files: list[Path]) -> list[str]:
    """An argument supplied twice is a TypeError, and the mock never sees the call.

    ``f(encoding="utf-8", **D)`` where ``D`` carries ``"encoding"`` raises
    ``TypeError: f() got multiple values for keyword argument 'encoding'`` while
    Python is still packing the arguments -- so the callee never runs, and a test
    that patched the callee records nothing and reports "called 0 times" rather
    than the collision.  Wrapped in ``except Exception``, it is silent: the branch
    that was supposed to run the child simply does not.
    """

    errors: list[str] = []
    for path in files:
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception:
            continue  # check_python_parse owns reporting unparseable files.
        constants = module_dict_constants(tree)
        if not constants:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            explicit = {keyword.arg for keyword in node.keywords if keyword.arg}
            if not explicit:
                continue
            for keyword in node.keywords:
                if keyword.arg is not None or not isinstance(keyword.value, ast.Name):
                    continue
                clash = explicit & constants.get(keyword.value.id, set())
                if clash:
                    errors.append(
                        f"duplicate-keyword:{path}:{node.lineno}:"
                        f"{', '.join(sorted(clash))} passed both inline and by "
                        f"**{keyword.value.id}"
                    )
    return errors


def check_unclosed_file_handles(files: list[Path]) -> list[str]:
    """``open(p).read()`` leaves the handle for the garbage collector to find.

    CPython usually closes it on the next collection, which is why the pattern
    survives review: the value is correct and the file is readable again.  It
    fails where it is least visible.  In a loop over a manuscript's ``\\input``
    files, or a library's ebooks, the descriptors accumulate faster than the
    collector runs and the command dies on ``OSError: [Errno 24] Too many open
    files`` partway through -- a failure with no relation to the file it names.
    On a write handle the data is worse than late: the buffer flushes whenever
    the object is finalised, so an interpreter that exits first leaves a
    truncated file behind.  ``with`` costs one line and is exact.

    A handle bound to a name is a different thing -- a lock or a log kept open
    on purpose and closed elsewhere -- so only the immediately-discarded form
    is reported here.
    """

    errors: list[str] = []
    for path in files:
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception:
            continue  # check_python_parse owns reporting unparseable files.
        managed: set[tuple[int, int]] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.With, ast.AsyncWith)):
                continue
            for item in node.items:
                for inner in ast.walk(item.context_expr):
                    if isinstance(inner, ast.Call):
                        managed.add((inner.lineno, inner.col_offset))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            method = node.func
            if not isinstance(method, ast.Attribute):
                continue
            opened = method.value
            if not isinstance(opened, ast.Call):
                continue
            if not isinstance(opened.func, ast.Name) or opened.func.id != "open":
                continue
            if (opened.lineno, opened.col_offset) in managed:
                continue
            errors.append(
                f"unclosed-file:{path}:{node.lineno}:"
                f"open(...).{method.attr}() discards the handle; "
                "use a with statement"
            )
    return errors

def check_shell_syntax(files: list[Path]) -> list[str]:
    bash = shutil.which("bash")
    if not bash:
        return []
    errors: list[str] = []
    for path in files:
        if path.suffix != ".sh":
            continue
        result = subprocess.run([bash, "-n", bash_syntax_path(path, bash)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            errors.append(f"bash-syntax:{path}:{result.stderr.strip()}")
    return errors


def bash_syntax_path(path: Path, bash: str) -> str:
    # Do not instantiate a platform-selected pathlib class after inspecting a
    # simulated target OS.  Unit tests deliberately exercise Windows routing
    # on POSIX, where pathlib.WindowsPath cannot be instantiated.
    absolute = os.path.abspath(os.fspath(path))
    if os.name != "nt":
        return absolute

    forward = absolute.replace("\\", "/")
    bash_parts = str(bash).replace("\\", "/").rstrip("/").split("/")
    if len(bash_parts) < 2 or bash_parts[-1].lower() != "bash.exe" or bash_parts[-2].lower() != "system32":
        return forward

    wsl = shutil.which("wsl.exe")
    if not wsl:
        return forward
    converted = subprocess.run(
        [wsl, "--", "wslpath", "-a", forward],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if converted.returncode == 0 and converted.stdout.strip():
        return converted.stdout.strip().splitlines()[0]
    return forward


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
            encoding="utf-8",
            errors="replace",
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
