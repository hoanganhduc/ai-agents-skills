"""Shared skill Python venv: provisioning, admission and verification.

The venv (``~/.agents_skills_venv`` by default, ``AAS_SKILL_VENV`` overrides)
holds every third-party package the skills with a ``python`` block in
``manifest/runtime.yaml`` import.  It is always built from the attested
distribution interpreter (``/usr/bin/python3``); the launcher keeps executing
that binary and only lends it the venv's ``bin/python`` as ``argv[0]``, so
CPython reads ``<venv>/pyvenv.cfg`` and adds the venv's site-packages.

``admit_skill_venv`` is the Python mirror of ``skill_python_prefix`` in
``canonical/runtime/runners/run_skill.sh``: same checks, same order, same
reason strings, so the provisioner and the launcher agree on every venv.

Every child process starts through an injectable ``run`` callable so tests can
stand in for ``venv``, ``pip`` and the interpreter probes while keeping the
real filesystem effects.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .external_dependencies import _require_private_directory_chain, external_provision_lock
from .manifest import REPO_ROOT


DEFAULT_VENV_RELATIVE = Path(".agents_skills_venv")      # ~/.agents_skills_venv
RECEIPT_NAME = "aas-skill-python.json"
RECEIPT_SCHEMA = "aas-skill-python.v1"
ATTESTED_REALPATH = re.compile(r"/usr/bin/python3(\.[0-9]+)?")   # re.fullmatch on the realpath
STARTUP_FILES = ("sitecustomize.py", "usercustomize.py")        # plus *.pth
# Never provision into, adopt, recreate or remove any of these (compared by realpath):
PROTECTED_VENV_PREFIXES = ("/usr", "/etc", "/opt", "/var")
PROTECTED_VENV_NAMES = (".venv",)                               # the installer's own venv, relative to the checkout
PROTECTED_HOME_VENVS = (".course_venv", ".local/share/docling-venv",
                        ".local/share/manim-math-animation-venv", ".vnu-eoffice_venv")

DEFAULT_BASE_PYTHON = "/usr/bin/python3"
CHILD_PATH = "/usr/bin:/bin"
PYVENV_CFG_HEAD_BYTES = 4096
PYVENV_CFG_KEYS = ("home", "version", "include-system-site-packages")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
VERSIONED_BINARY_RE = re.compile(r"python[0-9]\.[0-9].*")   # the bash glob python[0-9].[0-9]*
CFG_WHITESPACE_RE = re.compile(r"[ \t\n\r\f\v]")              # the bash ${var//[[:space:]]/}
LIST_LIMIT = 20
VENV_TIMEOUT = 120
PROBE_TIMEOUT = 120
PIP_INSTALL_TIMEOUT = 3600
INSTALLED_BYTES_NOTE = "site-packages byte delta of the pip call that provisioned these skills together"
IMPORT_PROBE = (
    "import importlib,sys; [importlib.import_module(m) for m in sys.argv[1:]]; "
    "print(sys.prefix); print(sys.executable)"
)
VERSION_PROBE = "import sys; print(sys.version)"

Runner = Callable[..., Any]


class SkillPythonError(RuntimeError):
    """A provisioning, admission or verification failure (CLI exit 1)."""


class SkillPythonUsageError(SkillPythonError):
    """A request that cannot be honoured as written (CLI exit 2)."""


def _require_linux_skill_python() -> None:
    if not sys.platform.startswith("linux"):
        raise SkillPythonError("skill Python venv provisioning is Linux-only")


@dataclass(frozen=True)
class SkillPythonTarget:
    skill: str
    requirements: tuple[Path, ...]
    modules: tuple[str, ...]
    provision: str


def skill_python_targets(
    manifests: dict[str, Any], *, skills: set[str] | None, include_opt_in: bool
) -> list[SkillPythonTarget]:
    """Select the runtime skills whose ``python`` block this run provisions."""
    runtime_skills = manifests.get("runtime", {}).get("skills", {})
    known = {
        name: spec["python"]
        for name, spec in runtime_skills.items()
        if isinstance(spec, dict) and isinstance(spec.get("python"), dict)
    }
    if skills is not None:
        unknown = sorted(set(skills) - set(known))
        if unknown:
            raise SkillPythonUsageError(
                f"unknown skill Python target(s): {', '.join(unknown)}; "
                f"known: {', '.join(sorted(known)) or '(none)'}"
            )
        selected = sorted(skills)
    else:
        selected = sorted(
            name
            for name, block in known.items()
            if include_opt_in or block.get("provision", "default") == "default"
        )
    return [
        SkillPythonTarget(
            skill=name,
            requirements=tuple(Path(item) for item in known[name].get("requirements", [])),
            modules=tuple(known[name].get("modules", [])),
            provision=known[name].get("provision", "default"),
        )
        for name in selected
    ]


def _lstat_or_none(path: str | os.PathLike[str]) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except OSError:
        return None


def _owner_controlled(info: os.stat_result) -> bool:
    """The owner rule: owner is the invoking user or root, no group/other write."""
    return info.st_uid in {0, os.getuid()} and not (stat.S_IMODE(info.st_mode) & 0o022)


def _trusted_directory(info: os.stat_result) -> bool:
    return stat.S_ISDIR(info.st_mode) and _owner_controlled(info)


def _trusted_file(info: os.stat_result) -> bool:
    """The file rule adds ``st_nlink == 1`` unless root owns the file."""
    return (
        stat.S_ISREG(info.st_mode)
        and _owner_controlled(info)
        and (info.st_nlink == 1 or info.st_uid == 0)
    )


def _root_sticky_directory(info: os.stat_result) -> bool:
    """A ``/tmp``-style ancestor: root-owned and sticky."""
    return stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)


def _run_child(run: Runner, argv: list[str], *, env: dict[str, str], timeout: int) -> Any:
    try:
        return run(
            argv,
            env=env,
            timeout=timeout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SkillPythonError(f"{argv[0]} timed out after {timeout}s") from exc
    except OSError as exc:
        raise SkillPythonError(f"cannot run {argv[0]}: {exc}") from exc


def _child_output(completed: Any) -> str:
    return "".join(part for part in (completed.stdout, completed.stderr) if part).strip()


def _child_env(home: Path) -> dict[str, str]:
    return {"PATH": CHILD_PATH, "HOME": str(home), "LANG": os.environ.get("LANG", "C.UTF-8")}


def attested_base_python(
    candidate: str | None = None, *, preflight_ensurepip: bool, run: Runner = subprocess.run
) -> Path:
    """Return the realpath of the attested distribution interpreter.

    The candidate may be the distribution's ``/usr/bin/python3`` symlink; the
    file it resolves to must be owner-controlled with every ancestor the same.
    """
    candidate = candidate or DEFAULT_BASE_PYTHON
    current = candidate
    try:
        real = os.path.realpath(candidate)
        if not ATTESTED_REALPATH.fullmatch(real):
            raise SkillPythonError(
                f"{candidate} does not resolve to the attested system Python "
                f"(/usr/bin/python3 or /usr/bin/python3.N): {real}"
            )
        info = os.lstat(candidate)
        if stat.S_ISLNK(info.st_mode):
            if info.st_uid not in {0, os.getuid()}:
                raise SkillPythonError(f"{candidate} is a symlink owned by another user")
        elif not _trusted_file(info):
            raise SkillPythonError(f"{candidate} is not an owner-controlled regular file")
        current = real
        if not _trusted_file(os.lstat(real)):
            raise SkillPythonError(f"{real} is not an owner-controlled regular file")
        parent = os.path.dirname(real)
        while True:
            current = parent
            if not _trusted_directory(os.lstat(parent)):
                raise SkillPythonError(f"{parent} is not an owner-controlled directory")
            if parent == "/":
                break
            parent = os.path.dirname(parent)
    except OSError as exc:
        raise SkillPythonError(f"cannot attest {current}: {exc}") from exc
    if preflight_ensurepip:
        completed = _run_child(
            run, [candidate, "-I", "-c", "import ensurepip"], env={"PATH": CHILD_PATH}, timeout=VENV_TIMEOUT
        )
        if completed.returncode != 0:
            raise SkillPythonError(
                f"{real} cannot create a pip-bearing venv; install the distribution package "
                "python3-venv or supply a venv already built from /usr/bin/python3 via AAS_SKILL_VENV"
            )
    return Path(real)


def skill_venv_path(home: Path, override: str | None) -> Path:
    if override:
        return Path(override)
    return Path(home) / DEFAULT_VENV_RELATIVE


def refuse_protected_location(venv: Path, *, home: Path, checkout: Path) -> None:
    """Raise ``SkillPythonError`` when ``venv`` names a venv this tool must never touch."""
    real = os.path.realpath(venv)
    reason = ""
    for prefix in PROTECTED_VENV_PREFIXES:
        if real.startswith(prefix + "/"):
            reason = f"under {prefix}"
            break
    if not reason:
        for name in PROTECTED_VENV_NAMES:
            if real == os.path.realpath(Path(checkout) / name):
                reason = f"the installer's own {name}"
                break
    if not reason:
        for name in PROTECTED_HOME_VENVS:
            if real == os.path.realpath(Path(home) / name):
                reason = f"the protected home venv {name}"
                break
    if reason:
        raise SkillPythonError(f"skill Python venv path is protected: {real} ({reason})")


def _read_pyvenv_cfg(path: str | os.PathLike[str]) -> tuple[dict[str, str], dict[str, int]]:
    """Read the first 4096 bytes the way the launcher's ``read`` loop does.

    Lines split at the first ``=``, every whitespace character is removed from
    key and value, and a final line without a newline is dropped, because
    ``while read`` never runs its body for one.
    """
    values = {key: "" for key in PYVENV_CFG_KEYS}
    counts = {key: 0 for key in PYVENV_CFG_KEYS}
    try:
        with open(path, "rb") as handle:
            data = handle.read(PYVENV_CFG_HEAD_BYTES)
    except OSError:
        return values, counts
    text = data.decode("utf-8", "replace")
    lines = text.split("\n")
    if not text.endswith("\n"):
        lines = lines[:-1]
    for line in lines:
        key, _, value = line.partition("=")
        key = CFG_WHITESPACE_RE.sub("", key)
        value = CFG_WHITESPACE_RE.sub("", value)
        if key in values:
            values[key] = value
            counts[key] += 1
    return values, counts


def _load_receipt(venv: Path) -> dict[str, Any] | None:
    """The receipt when it exists, parses and carries the schema; else ``None``."""
    try:
        with open(Path(venv) / RECEIPT_NAME, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != RECEIPT_SCHEMA:
        return None
    return data


def identify_skill_venv(venv: Path, *, attested_python: Path) -> str:
    """``"receipt"`` or ``"pyvenv"`` for a venv this tool may modify; else raise."""
    real = os.path.realpath(venv)
    receipt = _load_receipt(Path(venv))
    if receipt is not None and receipt.get("venv") == real:
        return "receipt"
    cfg_path = Path(venv) / "pyvenv.cfg"
    info = _lstat_or_none(cfg_path)
    if info is not None and stat.S_ISREG(info.st_mode):
        values, _ = _read_pyvenv_cfg(cfg_path)
        if values["home"] == os.path.dirname(os.path.realpath(attested_python)):
            return "pyvenv"
    raise SkillPythonError(
        f"{venv} is not a skill Python venv built from the attested Python; refusing to modify it"
    )


def _non_canonical(prefix: str) -> bool:
    if prefix.endswith(("/", "/.", "/..")):
        return True
    if "/./" in prefix or "/../" in prefix or "//" in prefix:
        return True
    return os.path.normpath(prefix) != prefix


def admit_skill_venv(prefix: str | os.PathLike[str], *, attested_python: Path) -> tuple[bool, str]:
    """Mirror of ``skill_python_prefix`` in ``run_skill.sh``.

    Returns ``(True, real_path)`` when the venv is admitted and ``(False,
    reason)`` otherwise, where ``reason`` is the launcher's stderr line.
    """
    prefix = os.fspath(prefix)
    attested_real = os.path.realpath(attested_python)
    if not prefix.startswith("/"):
        return False, "skill Python venv path must be absolute"
    if _non_canonical(prefix):
        return False, f"skill Python venv path is not canonical: {prefix}"
    if os.path.islink(prefix) or not os.path.isdir(prefix):
        return False, f"skill Python venv is not a directory: {prefix}"
    real = os.path.realpath(prefix)
    if real != prefix:
        return False, f"skill Python venv path is not canonical: {prefix}"
    parent = real
    while True:
        info = _lstat_or_none(parent)
        if info is None or not _trusted_directory(info):
            if parent == real or info is None or not _root_sticky_directory(info):
                return False, f"skill Python venv is not owner-controlled: {parent}"
        if parent == "/":
            break
        parent = parent.rsplit("/", 1)[0] or "/"
    cfg_path = f"{real}/pyvenv.cfg"
    cfg_info = _lstat_or_none(cfg_path)
    if cfg_info is None or not stat.S_ISREG(cfg_info.st_mode):
        return False, f"skill Python venv has no regular pyvenv.cfg: {real}"
    if not _trusted_file(cfg_info):
        return False, f"skill Python venv is not owner-controlled: {real}/pyvenv.cfg"
    values, counts = _read_pyvenv_cfg(cfg_path)
    if any(counts[key] != 1 for key in PYVENV_CFG_KEYS):
        return False, (
            "skill Python venv pyvenv.cfg must carry home, version and "
            "include-system-site-packages exactly once"
        )
    if values["home"] != os.path.dirname(attested_real):
        return False, "skill Python venv was not built from the attested Python"
    if values["include-system-site-packages"] != "false":
        return False, "skill Python venv must not include system site-packages"
    if not VERSION_RE.fullmatch(values["version"]):
        return False, "skill Python venv pyvenv.cfg version must be X.Y.Z"
    version = values["version"].rsplit(".", 1)[0]
    binary = os.path.basename(attested_real)
    if VERSIONED_BINARY_RE.fullmatch(binary) and binary[len("python"):] != version:
        return False, "skill Python venv version does not match the attested Python"
    site_packages = f"{real}/lib/python{version}/site-packages"
    for target in (f"{real}/bin", f"{real}/lib", f"{real}/lib/python{version}", site_packages):
        info = _lstat_or_none(target)
        if info is not None and stat.S_ISLNK(info.st_mode):
            return False, f"skill Python venv component is a symlink: {target}"
        if info is None or not _trusted_directory(info):
            return False, f"skill Python venv is not owner-controlled: {target}"
    startup = sorted(glob.glob(os.path.join(glob.escape(site_packages), "*.pth")))
    startup.extend(f"{site_packages}/{name}" for name in STARTUP_FILES)
    for path in startup:
        info = _lstat_or_none(path)
        if info is None:
            continue
        if stat.S_ISLNK(info.st_mode):
            return False, f"skill Python venv startup file is a symlink: {path}"
        if not _trusted_file(info):
            return False, f"skill Python venv startup file is not owner-controlled: {path}"
    bin_python = f"{real}/bin/python"
    if not os.path.islink(bin_python):
        return False, "skill Python venv bin/python is not a symlink"
    if os.path.realpath(bin_python) != attested_real:
        return False, "skill Python venv bin/python is not the attested system Python"
    # python3 and python3.X are on the venv's PATH once the loader prepends bin/;
    # a regular file under either name would be executed in place of the attested binary.
    for relative in ("bin/python3", f"bin/python{version}"):
        path = f"{real}/{relative}"
        if not os.path.lexists(path):
            continue
        if not os.path.islink(path):
            return False, f"skill Python venv {relative} is not a symlink"
        if os.path.realpath(path) != attested_real:
            return False, f"skill Python venv {relative} is not the attested system Python"
    return True, real


def _walk_entries(root: Path) -> list[tuple[str, os.stat_result]]:
    """``(path, lstat)`` for ``root`` and everything below it, symlinks never followed."""
    entries: list[tuple[str, os.stat_result]] = []
    info = _lstat_or_none(root)
    if info is None:
        return entries
    entries.append((os.fspath(root), info))
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(dirnames + filenames):
            path = os.path.join(dirpath, name)
            info = _lstat_or_none(path)
            if info is not None:
                entries.append((path, info))
    return entries


def preflight_tree(venv: Path) -> list[str]:
    """Entries an adopt run could not normalize: foreign-owned, or hard-linked files."""
    uid = os.getuid()
    flagged: list[str] = []
    for path, info in _walk_entries(venv):
        if stat.S_ISLNK(info.st_mode):
            continue
        if info.st_uid != uid or (not stat.S_ISDIR(info.st_mode) and info.st_nlink > 1):
            flagged.append(path)
            if len(flagged) >= LIST_LIMIT:
                break
    return flagged


def normalize_modes(venv: Path) -> int:
    """Harden directories before descending, and change only bound, non-symlink inodes."""
    changed = 0
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK

    def normalize_opened(descriptor: int, path: Path, expected: os.stat_result | None = None) -> None:
        nonlocal changed
        info = os.fstat(descriptor)
        is_directory = stat.S_ISDIR(info.st_mode)
        if (
            info.st_uid != os.getuid()
            or not (is_directory or stat.S_ISREG(info.st_mode))
            or (not is_directory and info.st_nlink != 1)
        ):
            raise SkillPythonError(f"skill Python venv entry cannot be normalized safely: {path}")
        if expected is not None and (
            info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)
        ) != (expected.st_dev, expected.st_ino, stat.S_IFMT(expected.st_mode)):
            raise SkillPythonError(f"skill Python venv entry changed during mode normalization: {path}")
        mode = stat.S_IMODE(info.st_mode)
        if mode & 0o022:
            os.fchmod(descriptor, mode & ~0o022)
            changed += 1
        if is_directory:
            for name in sorted(os.listdir(descriptor)):
                observed = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISLNK(observed.st_mode):
                    continue
                if not (stat.S_ISDIR(observed.st_mode) or stat.S_ISREG(observed.st_mode)):
                    raise SkillPythonError(f"skill Python venv entry cannot be normalized safely: {path / name}")
                child_flags = flags | (os.O_DIRECTORY if stat.S_ISDIR(observed.st_mode) else 0)
                child = os.open(name, child_flags, dir_fd=descriptor)
                try:
                    normalize_opened(child, path / name, observed)
                finally:
                    os.close(child)

    try:
        descriptor = os.open(venv, flags | os.O_DIRECTORY)
        try:
            normalize_opened(descriptor, venv)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise SkillPythonError(f"skill Python venv changed or could not be opened during mode normalization: {venv}") from exc
    return changed


def check_modes(venv: Path) -> list[str]:
    """Non-mutating twin of ``normalize_modes``: the group/other writable entries."""
    flagged: list[str] = []
    for path, info in _walk_entries(venv):
        if stat.S_ISLNK(info.st_mode):
            continue
        if stat.S_IMODE(info.st_mode) & 0o022:
            flagged.append(path)
            if len(flagged) >= LIST_LIMIT:
                break
    return flagged


def site_packages_bytes(venv: Path) -> int:
    total = 0
    for site in sorted(glob.glob(os.path.join(glob.escape(os.fspath(venv)), "lib", "python*", "site-packages"))):
        for _, info in _walk_entries(Path(site)):
            if not stat.S_ISLNK(info.st_mode):
                total += info.st_size
    return total


def _check_adoptable(venv: Path, attested: Path) -> None:
    """An existing venv is adopted only when the attested Python built it."""
    attested_real = os.path.realpath(attested)
    values, _ = _read_pyvenv_cfg(Path(venv) / "pyvenv.cfg")
    bin_python = Path(venv) / "bin" / "python"
    if (
        values["home"] != os.path.dirname(attested_real)
        or values["include-system-site-packages"] != "false"
        or not os.path.islink(bin_python)
        or os.path.realpath(bin_python) != attested_real
    ):
        raise SkillPythonError(
            f"existing venv at {venv} was not built from the attested Python; pass --recreate to rebuild it"
        )


def _requirement_paths(checkout: Path, targets: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for target in targets:
        for relative in target["requirements"]:
            path = str(Path(checkout) / "canonical" / "runtime" / relative)
            if path not in paths:
                paths.append(path)
    return paths


def build_skill_python_plan(
    home: Path,
    manifests: dict[str, Any],
    *,
    skills: set[str] | None,
    venv: Path | str | None,
    python: str | None,
    include_opt_in: bool,
    recreate: bool,
    remove: bool,
    checkout: Path | None = None,
) -> dict[str, Any]:
    """Decide what a run would do; nothing is written."""
    _require_linux_skill_python()
    if recreate and remove:
        raise SkillPythonUsageError("--recreate and --remove are mutually exclusive")
    home = Path(home)
    checkout = Path(checkout) if checkout is not None else REPO_ROOT
    venv_path = Path(venv) if venv else skill_venv_path(home, None)
    refuse_protected_location(venv_path, home=home, checkout=checkout)
    attested = attested_base_python(python, preflight_ensurepip=False)
    targets = [] if remove else skill_python_targets(manifests, skills=skills, include_opt_in=include_opt_in)
    if not remove and not targets:
        raise SkillPythonError(
            "no skill Python targets selected; name skills with --skills or pass --include-opt-in"
        )
    if os.path.islink(venv_path):
        raise SkillPythonError(f"skill Python venv is a symlink: {venv_path}")
    exists = os.path.lexists(venv_path)
    would: list[str] = []
    if remove:
        if not exists:
            raise SkillPythonError(f"skill Python venv does not exist: {venv_path}")
        identify_skill_venv(venv_path, attested_python=attested)
        mode = "remove"
        would.extend(remove_skill_venv(venv_path, apply=False)["would"])
    elif exists:
        identify_skill_venv(venv_path, attested_python=attested)
        if recreate:
            mode = "recreate"
            would.append(f"remove {venv_path} and create it again with {attested}")
        else:
            mode = "adopt"
            _check_adoptable(venv_path, attested)
            would.append(f"adopt {venv_path}: clear group/other write bits, then admit it")
    else:
        mode = "create"
        would.append(f"create {venv_path} with {attested}")
    target_records = [
        {
            "skill": target.skill,
            "requirements": [str(item) for item in target.requirements],
            "modules": list(target.modules),
            "provision": target.provision,
        }
        for target in targets
    ]
    requirements = _requirement_paths(checkout, target_records)
    if targets:
        would.append(
            "pip install --require-virtualenv -r " + " -r ".join(requirements)
            + " (skills: " + ", ".join(target.skill for target in targets) + ")"
        )
        would.append(f"write receipt {venv_path / RECEIPT_NAME}")
    return {
        "venv": str(venv_path),
        "venv_real": os.path.realpath(venv_path),
        "home": str(home),
        "checkout": str(checkout),
        "python": python,
        "interpreter": str(attested),
        "mode": mode,
        "recreate": bool(recreate),
        "remove": bool(remove),
        "targets": target_records,
        "requirements": requirements,
        "would": would,
    }


def remove_skill_venv(venv: Path, *, apply: bool) -> dict[str, Any]:
    """Remove the venv directory (after the caller identified it) or say what would go."""
    venv = Path(venv)
    if os.path.islink(venv):
        raise SkillPythonError(f"skill Python venv is a symlink: {venv}")
    if not venv.is_dir():
        raise SkillPythonError(f"skill Python venv does not exist: {venv}")
    real = os.path.realpath(venv)
    if apply:
        shutil.rmtree(venv)
    return {"venv": real, "removed": bool(apply), "would": [f"remove {real}"]}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_receipt(venv: Path, receipt: dict[str, Any]) -> Path:
    path = Path(venv) / RECEIPT_NAME
    fd, tmp_name = tempfile.mkstemp(prefix=f".{RECEIPT_NAME}.", suffix=".tmp", dir=venv)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(receipt, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return path


def apply_skill_python_plan(plan: dict[str, Any], *, run: Runner = subprocess.run, log: Callable[[str], Any] = print) -> dict[str, Any]:
    """Perform the plan: identify, adopt or create, pip, normalize, admit, receipt."""
    _require_linux_skill_python()
    home = Path(plan["home"])
    venv = Path(plan["venv"])
    checkout = Path(plan["checkout"])
    old_umask = os.umask(0o077)
    try:
        with external_provision_lock(home):
            try:
                venv.parent.relative_to(home)
            except ValueError as exc:
                raise SkillPythonError(f"skill Python venv must live under the selected root {home}: {venv}") from exc
            _require_private_directory_chain(home, venv.parent)
            attested = attested_base_python(plan.get("python"), preflight_ensurepip=False, run=run)
            env = _child_env(home)
            normalized = 0
            if os.path.islink(venv):
                raise SkillPythonError(f"skill Python venv is a symlink: {venv}")
            exists = os.path.lexists(venv)
            if exists:
                identify_skill_venv(venv, attested_python=attested)
                if plan.get("remove"):
                    removed = remove_skill_venv(venv, apply=True)
                    log(f"removed skill Python venv {removed['venv']}")
                    return {"status": "ok", "mode": "remove", **removed}
                if plan.get("recreate"):
                    shutil.rmtree(venv)
                    log(f"removed skill Python venv {venv} for recreation")
                    exists = False
            if exists:
                mode = "adopt"
                _check_adoptable(venv, attested)
                flagged = preflight_tree(venv)
                if flagged:
                    raise SkillPythonError(
                        "skill Python venv holds entries not owned by the invoking user or hard-linked; "
                        "nothing was changed: " + ", ".join(flagged)
                    )
                normalized = normalize_modes(venv)
                log(f"normalized modes on {normalized} entries under {venv}")
                admitted, reason = admit_skill_venv(str(venv), attested_python=attested)
                if not admitted:
                    raise SkillPythonError(reason)
            else:
                mode = "create"
                attested = attested_base_python(plan.get("python"), preflight_ensurepip=True, run=run)
                completed = _run_child(run, [str(attested), "-I", "-m", "venv", str(venv)], env=env, timeout=VENV_TIMEOUT)
                if completed.returncode != 0:
                    raise SkillPythonError(f"venv creation failed at {venv}: {_child_output(completed)}")
                log(f"created skill Python venv {venv} with {attested}")
            targets = list(plan["targets"])
            for target in targets:
                for relative in target["requirements"]:
                    path = Path(checkout) / "canonical" / "runtime" / relative
                    if not path.is_file():
                        raise SkillPythonError(f"requirements file for {target['skill']} is missing: {path}")
            requirements = _requirement_paths(checkout, targets)
            venv_python = str(venv / "bin" / "python")
            completed = _run_child(run, [venv_python, "-I", "-m", "pip", "--version"], env=env, timeout=PROBE_TIMEOUT)
            if completed.returncode != 0:
                raise SkillPythonError(f"pip is not available in {venv}; pass --recreate to rebuild it")
            before = site_packages_bytes(venv)
            argv = [venv_python, "-I", "-m", "pip", "install", "--require-virtualenv", "--no-input", "--disable-pip-version-check"]
            for path in requirements:
                argv.extend(["-r", path])
            pip_env = {**env, "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
            completed = _run_child(run, argv, env=pip_env, timeout=PIP_INSTALL_TIMEOUT)
            if completed.returncode != 0:
                raise SkillPythonError(f"pip install failed in {venv}: {_child_output(completed)}")
            installed_bytes = site_packages_bytes(venv) - before
            log(f"pip installed {len(requirements)} requirements file(s) into {venv} ({installed_bytes} bytes)")
            completed = _run_child(run, [venv_python, "-I", "-m", "pip", "check"], env=env, timeout=PROBE_TIMEOUT)
            if completed.returncode != 0:
                raise SkillPythonError(f"pip check failed in {venv}: {_child_output(completed)}")
            normalized += normalize_modes(venv)
            admitted, reason = admit_skill_venv(str(venv), attested_python=attested)
            if not admitted:
                raise SkillPythonError(reason)
            completed = _run_child(run, [venv_python, "-I", "-c", VERSION_PROBE], env=env, timeout=PROBE_TIMEOUT)
            if completed.returncode != 0:
                raise SkillPythonError(f"cannot read the version of {venv_python}: {_child_output(completed)}")
            python_version = (completed.stdout or "").strip()
            completed = _run_child(run, [venv_python, "-I", "-m", "pip", "freeze"], env=env, timeout=PROBE_TIMEOUT)
            if completed.returncode != 0:
                raise SkillPythonError(f"pip freeze failed in {venv}: {_child_output(completed)}")
            freeze = [line for line in (completed.stdout or "").splitlines() if line.strip()]
            existing = _load_receipt(venv) or {}
            skills = dict(existing.get("skills", {})) if isinstance(existing.get("skills"), dict) else {}
            for target in targets:
                skills[target["skill"]] = {
                    "requirements": list(target["requirements"]),
                    "sha256": {
                        relative: _sha256_file(Path(checkout) / "canonical" / "runtime" / relative)
                        for relative in target["requirements"]
                    },
                    "modules": list(target["modules"]),
                    "installed_bytes": installed_bytes,
                }
            receipt = {
                "schema": RECEIPT_SCHEMA,
                "venv": os.path.realpath(venv),
                "interpreter": str(attested),
                "python_version": python_version,
                "skills": skills,
                "installed_bytes_note": INSTALLED_BYTES_NOTE,
                "freeze": freeze,
                "argv": list(sys.argv),
                "provisioned_at": datetime.now(timezone.utc).isoformat(),
            }
            receipt_path = _write_receipt(venv, receipt)
            log(f"wrote receipt {receipt_path}")
            return {
                "status": "ok",
                "mode": mode,
                "venv": receipt["venv"],
                "interpreter": receipt["interpreter"],
                "python_version": python_version,
                "normalized": normalized,
                "installed_bytes": installed_bytes,
                "skills": [target["skill"] for target in targets],
                "receipt": str(receipt_path),
            }
    finally:
        os.umask(old_umask)


def verify_skill_python(
    venv: Path,
    targets: list[SkillPythonTarget] | None,
    *,
    attested_python: Path,
    run: Runner = subprocess.run,
    home: Path | None = None,
) -> dict[str, Any]:
    """Read-only check of the venv, its receipt, its modes and every skill's imports."""
    venv_text = os.fspath(venv)
    attested_real = os.path.realpath(attested_python)
    failures: list[str] = []
    result: dict[str, Any] = {
        "status": "ok",
        "venv": venv_text,
        "interpreter": attested_real,
        "pip_check": {"ok": False, "output": ""},
        "skills": {},
        "mode_violations": [],
        "failures": failures,
    }
    admitted, detail = admit_skill_venv(venv_text, attested_python=attested_python)
    if not admitted:
        failures.append(detail)
        result["pip_check"]["output"] = "skipped: the venv was not admitted"
        result["status"] = "failed"
        return result
    real = detail
    result["venv"] = real
    venv_python = f"{real}/bin/python"
    env = _child_env(Path(home) if home is not None else Path.home())
    receipt = _load_receipt(Path(real))
    if receipt is None:
        failures.append(f"skill Python venv receipt is missing or invalid: {real}/{RECEIPT_NAME}")
    else:
        if receipt.get("venv") != real:
            failures.append(f"skill Python venv receipt names {receipt.get('venv')}, not {real}")
        if receipt.get("interpreter") != attested_real:
            failures.append(
                f"skill Python venv was built from {receipt.get('interpreter')}, not {attested_real}; "
                "re-run provision-skill-python --apply"
            )
    completed = _run_child(run, [venv_python, "-I", "-c", VERSION_PROBE], env=env, timeout=PROBE_TIMEOUT)
    if completed.returncode != 0:
        failures.append(f"cannot read the version of {venv_python}: {_child_output(completed)}")
    elif receipt is not None:
        current = (completed.stdout or "").strip()
        if receipt.get("python_version") != current:
            failures.append(
                f"skill Python venv was built for {receipt.get('python_version')}; "
                "re-run provision-skill-python --apply"
            )
    result["mode_violations"] = check_modes(Path(real))
    if result["mode_violations"]:
        failures.append("skill Python venv has group/other writable entries: " + ", ".join(result["mode_violations"]))
    completed = _run_child(run, [venv_python, "-I", "-m", "pip", "check"], env=env, timeout=PROBE_TIMEOUT)
    result["pip_check"] = {"ok": completed.returncode == 0, "output": _child_output(completed)}
    if completed.returncode != 0:
        failures.append("pip check failed")
    recorded = receipt.get("skills", {}) if receipt is not None else {}
    if not isinstance(recorded, dict):
        recorded = {}
    if targets is None:
        probes = {name: list(entry.get("modules", [])) for name, entry in sorted(recorded.items()) if isinstance(entry, dict)}
    else:
        probes = {target.skill: list(target.modules) for target in targets}
    for skill, modules in probes.items():
        if targets is not None and skill not in recorded:
            result["skills"][skill] = {"status": "failed", "modules": modules, "detail": "not provisioned (absent from the receipt)"}
            continue
        completed = _run_child(run, [venv_python, "-I", "-c", IMPORT_PROBE, *modules], env=env, timeout=PROBE_TIMEOUT)
        lines = (completed.stdout or "").splitlines()
        if completed.returncode == 0 and lines[-2:] == [real, venv_python]:
            result["skills"][skill] = {"status": "ok", "modules": modules, "detail": ""}
        else:
            result["skills"][skill] = {
                "status": "failed",
                "modules": modules,
                "detail": _child_output(completed) or f"import probe exited {completed.returncode}",
            }
    if failures or any(entry["status"] != "ok" for entry in result["skills"].values()):
        result["status"] = "failed"
    return result
