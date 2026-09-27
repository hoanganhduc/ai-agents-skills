#!/usr/bin/env python3
"""Local Lax discovery and independently replayed evidence. No publication API."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import unicodedata
from typing import Any

VERSION = "0.1.48"
ENVIRONMENT = "v4.33.0"
MATHLIB_SHA = "db584cd6d46c92f209a44c0f1c829460d327499d"
SPEC_SHA = "2f3f1fda37e99effb3138a38211422c4b708a0ca1bf84f1ee08bedb1d06c5d3d"
BACKGROUND = frozenset({"propext", "Classical.choice", "Quot.sound"})
MAX_JSON = 32 * 1024 * 1024
GENERATED = frozenset({".git", ".lake", "build-output.json", "lake-manifest.json", "package-overrides.json"})


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def read_json(path: Path) -> dict[str, Any]:
    raw = regular_bytes(path, MAX_JSON)
    obj = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")
    return obj


def regular_bytes(path: Path, limit: int = 64 * 1024 * 1024) -> bytes:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ValueError(f"not a bounded regular file: {path.name}")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("file changed while opening")
        raw = handle.read(limit + 1)
        after = os.fstat(handle.fileno())
    if len(raw) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("file changed or exceeded limit")
    return raw


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def verification_binding(report: dict[str, Any]) -> str:
    """Stable inputs only: timing and compiler trace timestamps are not pins."""
    keys = ["backend", "source", "source_digest", "scope_digest", "challenge_digest", "request_digest",
            "database_commit", "database_digest", "tools", "dependency_digests"]
    return digest({key: report[key] for key in keys})


def tree_inventory(root: Path, *, exclude_generated: bool = True, max_bytes: int = 2 * 1024**3,
                   trusted_package_links: bool = False) -> dict[str, Any]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("inventory root must be a real directory")
    files: list[dict[str, Any]] = []
    total = 0
    seen: set[str] = set()
    for directory, names, filenames in os.walk(root, followlinks=False):
        for name in sorted(names + filenames):
            p = Path(directory) / name
            if exclude_generated and name in GENERATED:
                if name in names: names.remove(name)
                continue
            info = p.lstat()
            rel = p.relative_to(root).as_posix()
            safe_name(rel)
            normalized = unicodedata.normalize("NFC", rel).casefold()
            if normalized in seen: raise ValueError("colliding inventory paths")
            seen.add(normalized)
            if len(seen) > 150000: raise ValueError("inventory entry limit exceeded")
            if stat.S_ISDIR(info.st_mode): continue
            if stat.S_ISLNK(info.st_mode) and trusted_package_links:
                resolved = p.resolve(strict=True)
                if root.resolve() not in resolved.parents or not resolved.is_file():
                    raise ValueError("trusted package link escapes package")
                raw = regular_bytes(resolved)
                total += len(raw)
                if total > max_bytes: raise ValueError("inventory byte limit exceeded")
                files.append({"path": rel, "link": os.readlink(p), "sha256": hashlib.sha256(raw).hexdigest()})
                continue
            raw = regular_bytes(p, 512 * 1024**2 if not exclude_generated else 64 * 1024**2)
            total += len(raw)
            if total > max_bytes: raise ValueError("inventory byte limit exceeded")
            files.append({"path": rel, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "executable": bool(info.st_mode & 0o111)})
    files.sort(key=lambda x: x["path"])
    return {"sha256": digest(files), "files": files, "bytes": total}


def safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (not name or name.startswith("/") or "\\" in name or ":" in name
            or any(x in {"", ".", ".."} or x.rstrip(" .") != x for x in name.split("/"))
            or len(path.parts) > 40 or len(name) > 1024 or "\0" in name):
        raise ValueError("unsafe relative path")
    return path


def extract_archive(stream: Any, destination: Path, *, max_bytes: int = 2 * 1024**3,
                    max_file_bytes: int = 64 * 1024**2) -> None:
    """Pre-admit every tar member before writing any archive-controlled path."""
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
        raise ValueError("extraction requires an empty real directory")
    with tarfile.open(fileobj=stream, mode="r:*") as archive:
        members = archive.getmembers()
        if len(members) > 100000: raise ValueError("archive entry limit")
        members = [m for m in members if not (m.isdir() and m.name in {".", "./"})]
        seen: dict[str, bool] = {}; total = 0
        for member in members:
            name = member.name.rstrip("/") if member.isdir() else member.name
            while name.startswith("./"): name = name[2:]
            member.name = name
            safe_name(name)
            key = unicodedata.normalize("NFC", name).casefold()
            if key in seen: raise ValueError("duplicate/colliding archive path")
            if not member.isdir() and not member.isfile(): raise ValueError("archive link or special entry")
            for ancestor in PurePosixPath(key).parents:
                if str(ancestor) in seen and not seen[str(ancestor)]: raise ValueError("archive file/parent collision")
            if not member.isdir() and any(x.startswith(key + "/") for x in seen): raise ValueError("archive parent collision")
            seen[key] = member.isdir(); total += member.size
            if member.size < 0 or member.size > max_file_bytes or total > max_bytes:
                raise ValueError("archive expansion limit")
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir(): target.mkdir(parents=True, exist_ok=True); continue
            target.parent.mkdir(parents=True, exist_ok=True)
            reader = archive.extractfile(member)
            if reader is None: raise ValueError("missing archive data")
            with reader, target.open("xb") as output:
                while chunk := reader.read(1024 * 1024): output.write(chunk)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)


def proof_closure(targets: list[str], proofs: list[dict[str, Any]]) -> dict[str, Any]:
    """Least fixed point of eligible, independently checked proof witnesses."""
    by_id: dict[str, dict[str, Any]] = {}
    eligible = []
    for proof in proofs:
        name = proof["id"]
        if name in by_id: raise ValueError("duplicate proof identity")
        by_id[name] = proof
        if proof.get("independently_verified") is True and proof.get("state") in {"registered", "local"}:
            eligible.append(proof)
    grounded: dict[str, str] = {}
    while True:
        changed = False
        for proof in sorted(eligible, key=lambda p: p["id"]):
            conclusion = proof["conclusion"]
            if conclusion not in grounded and all(a in grounded for a in proof["assumptions"]):
                grounded[conclusion] = proof["id"]; changed = True
        if not changed: break
    witnesses: set[str] = set(); open_nodes: set[str] = set(); visited: set[str] = set()
    work = list(targets)
    while work:
        node = work.pop()
        if node in visited: continue
        visited.add(node)
        if node in grounded:
            proof = by_id[grounded[node]]; witnesses.add(proof["id"]); work.extend(proof["assumptions"])
        else:
            open_nodes.add(node)
            for proof in eligible:
                if proof["conclusion"] == node: work.extend(proof["assumptions"])
    return {"closed": all(t in grounded for t in targets), "open": sorted(open_nodes), "witnesses": sorted(witnesses)}


def search_catalog(database: Path, query: str, environment: str) -> dict[str, Any]:
    result: dict[str, Any] = {"schema_version": "lax-reuse.v1", "query": query, "environment": environment,
        "coverage_status": "local-catalog", "candidates": [], "limitations": ["text search is not semantic equivalence or independent proof verification"]}
    if not database.is_dir() or database.is_symlink():
        result["coverage_status"] = "unavailable"; return result
    needle = query.casefold()
    for entry in sorted(database.glob("lax-*")):
        if not re.fullmatch(r"lax-[1-9][0-9]*", entry.name): continue
        try:
            if entry.is_symlink(): raise ValueError("linked record")
            record = read_json(entry / "record.json"); output = read_json(entry / "build-output.json")
            manifest = output.get("inputs", {}).get("manifest", {})
            if record.get("id") != entry.name or output.get("id") != entry.name: raise ValueError("record identity mismatch")
            if record.get("state") not in {"draft", "registered"} or manifest.get("leanVersion") != environment: continue
            for concept in output.get("concepts", []):
                if needle not in json.dumps([manifest.get("title"), concept], ensure_ascii=False).casefold(): continue
                result["candidates"].append({"submission": entry.name, "concept": concept["id"], "state": record["state"],
                    "source": record.get("source"), "environment": environment, "mathlib": manifest.get("mathlibVersion"),
                    "statements": concept.get("statements", []), "verification_status": "not_verified"})
        except (OSError, ValueError, KeyError, TypeError):
            result["coverage_status"] = "incomplete"
        if len(result["candidates"]) >= 100:
            result["candidates"] = result["candidates"][:100]; result["coverage_status"] = "truncated"; break
    return result


def container_options(name: str, image: str, mounts: list[tuple[Path, str, bool]], *, writable: bool) -> list[str]:
    if sys.platform != "linux" or not hasattr(os, "getuid") or os.getuid() == 0:
        raise ValueError("executor requires a non-root Linux/WSL operator; use offline doctor elsewhere")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image): raise ValueError("executor image must be pinned by ID")
    if not re.fullmatch(r"[a-zA-Z0-9-]+", name): raise ValueError("invalid container name")
    args = ["docker", "run", "--name", name, "--read-only", "--network=none", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=128", "--cpus=2", "--memory=6g", "--memory-swap=6g",
            "--user", f"{os.getuid()}:{os.getgid()}", "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m"]
    for target, size, inodes in [("/work", "2g", 65536), ("/result", "64m", 4096)]:
        args += ["--tmpfs", f"{target}:rw,nosuid,nodev,size={size},nr_inodes={inodes},uid={os.getuid()},gid={os.getgid()},mode=0700"]
    for source, target, allow_write in mounts:
        if "," in str(source) or source.is_symlink() or not source.exists(): raise ValueError("unsafe mount")
        if allow_write and not writable: raise ValueError("writable input in checking phase")
        args += ["--mount", f"type=bind,src={source.resolve()},dst={target}" + ("" if allow_write else ",readonly")]
    args += ["--env", "HOME=/tmp/home", "--env", "LAX_HOME=/lax-home", "--env", "ELAN_HOME=/elan",
             "--env", "NODE_OPTIONS=--disable-sigusr1",
             "--env", "LAX_DISABLE_UPDATE_CHECK=1", "--env", "LEAN_NUM_THREADS=2", "--env", "LAKE_ARTIFACT_CACHE=false",
             "--env", "PATH=/elan/bin:/usr/local/bin:/usr/bin:/bin", image]
    return args


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lax-formalization")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    search = commands.add_parser("search"); search.add_argument("--query", required=True)
    search.add_argument("--database", type=Path, required=True); search.add_argument("--environment", default=ENVIRONMENT)
    for name in ["verify", "verify-dependency", "publication-plan"]:
        command = commands.add_parser(name); command.add_argument("--request", type=Path, required=True); command.add_argument("--out", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        if args.command == "search": report = search_catalog(args.database, args.query, args.environment)
        else:
            from lax_executor import doctor, verify, publication_plan
            if args.command == "doctor": report = doctor()
            elif args.command == "publication-plan": report = publication_plan(args.request, args.out)
            else: report = verify(args.request, args.out, dependency=args.command == "verify-dependency")
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0 if report.get("status") not in {"failed", "blocked", "error"} else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"schema_version": "lax-error.v1", "status": "error", "reason": str(exc)[:500]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
