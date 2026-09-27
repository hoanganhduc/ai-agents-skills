"""Qualify a deliberately narrow local Git format before host-side inspection."""
import os
from pathlib import Path
import re
import stat
import subprocess


def admit_git_config(root: Path, env: dict[str, str], *, allow_init: bool = False,
                     allow_ssh_origin: bool = False) -> None:
    gitdir = root / ".git"
    if not gitdir.exists():
        if allow_init: return
        raise ValueError("project must own an ordinary Git directory; ancestor discovery is forbidden")
    if gitdir.is_symlink() or not gitdir.is_dir():
        raise ValueError("only ordinary Git directories are qualified; linked worktrees are unsupported")
    config = gitdir / "config"
    info = config.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
        raise ValueError("Git config must be a bounded regular file")
    # --file and --no-includes read configuration as data, without entering
    # the repository or invoking any worktree conversion/filter operation.
    output = subprocess.check_output(["git", "config", "--file", str(config),
        "--no-includes", "--null", "--list"], env=env, cwd="/", stderr=subprocess.DEVNULL, timeout=10)
    core = {"core.repositoryformatversion", "core.filemode", "core.bare",
            "core.logallrefupdates", "core.ignorecase", "core.precomposeunicode"}
    for item in output.decode().split("\0"):
        if not item: continue
        key, _, value = item.partition("\n")
        if key in core or key in {"user.name", "user.email", "remote.origin.fetch"}:
            continue
        if key == "remote.origin.url" and re.fullmatch(r"https://(?:github\.com|gitlab\.com|codeberg\.org|bitbucket\.org)/[A-Za-z0-9_.\-/]+", value):
            continue
        if key == "remote.origin.url" and allow_ssh_origin and re.fullmatch(
                r"(?:git@(?:github\.com|gitlab\.com|codeberg\.org|bitbucket\.org):|"
                r"ssh://git@(?:github\.com|gitlab\.com|codeberg\.org|bitbucket\.org)/)"
                r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
            continue  # Read-only source inventory only; no fetch or publication.
        if (key, value) == ("gc.auto", "0"):
            continue  # Pinned actions/checkout disables automatic Git GC.
        if (key, value) in {("remote.origin.promisor", "true"), ("remote.origin.partialclonefilter", "blob:none")}:
            continue  # Lax's database checkout; callers prohibit all fetch protocols.
        if re.fullmatch(r"branch\.[^\s]+\.(remote|merge)", key): continue
        raise ValueError(f"unqualified local Git configuration: {key}")
    for name in ["commondir", "objects/info/alternates", "objects/info/http-alternates"]:
        if (gitdir / name).exists(): raise ValueError("alternate Git stores are unsupported")
