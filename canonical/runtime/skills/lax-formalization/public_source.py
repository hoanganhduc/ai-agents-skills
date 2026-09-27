#!/usr/bin/env python3
"""Read-only inventory and reviewed export into a NEW public source tree.

No Git initialization, compilation, network, source mutation or publication.
Controller plans/reports are private inputs outside both source and destination.
POSIX export is descriptor-bound; native Windows export is not qualified.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import unicodedata

from lax_formalization import digest, read_json, regular_bytes
from tex_sanitize import POLICY as TEX_POLICY, sanitize_tex

POLICY = "public-source.v1"
MAX_FILE = 64 * 1024**2
MAX_TOTAL = 512 * 1024**2
MAX_FILES = 100000
GENERATED = {".git", ".lake", "node_modules", "__pycache__", ".venv"}
DENIED = GENERATED | {".env", ".ssh", ".aws", ".codex", ".claude", ".learnings"}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def relative_name(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 1024:
        raise ValueError("invalid-relative-path")
    parts = name.split("/")
    if len(parts) > 40 or any(p in {"", ".", ".."} or p != p.rstrip(" .")
            or re.search(r'[<>:"|?*\\\x00-\x1f]', p)
            or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p)
            for p in parts):
        raise ValueError("invalid-relative-path")
    if any(p.casefold() in DENIED for p in parts): raise ValueError("private-or-generated-path")
    return name


def public_origin(url: str) -> str:
    if not isinstance(url, str) or not re.fullmatch(
            r"https://(?:github\.com|gitlab\.com|codeberg\.org|bitbucket\.org)/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", url):
        raise ValueError("invalid-public-origin")
    if url == "https://github.com/local/local": raise ValueError("placeholder-public-origin")
    return url


def ordinary_root(path: Path) -> Path:
    path = Path(path).absolute()
    if path.is_symlink() or not path.is_dir(): raise ValueError("root-must-be-directory")
    return path.resolve(strict=True)


def disjoint(a: Path, b: Path) -> bool:
    return a != b and a not in b.parents and b not in a.parents


@contextmanager
def directory_fd(path: Path):
    if os.name != "posix": raise ValueError("descriptor-export-requires-posix")
    # Hold the actual root, never follow a candidate-controlled parent at use.
    resolved = path.resolve(strict=True)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in resolved.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
        yield fd
    finally: os.close(fd)


def selected_bytes(root: Path, name: str) -> tuple[bytes, bool]:
    relative_name(name)
    if os.name != "posix":
        path = root / name
        if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
            raise ValueError("selected-link")
        info = path.lstat()
        if getattr(info, "st_file_attributes", 0) & 0x400: raise ValueError("selected-reparse-point")
        if info.st_nlink != 1: raise ValueError("selected-hardlink")
        return regular_bytes(path, MAX_FILE), bool(info.st_mode & 0o111)
    with directory_fd(root) as root_fd:
        fd = os.dup(root_fd)
        try:
            parts = name.split("/")
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd = child
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(file_fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_FILE:
                    raise ValueError("selected-file-not-qualified")
                raw = stream.read(MAX_FILE + 1); after = os.fstat(stream.fileno())
            if len(raw) > MAX_FILE or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("selected-file-changed")
            return raw, bool(before.st_mode & 0o111)
        except OSError:
            raise ValueError("selected-path-not-qualified") from None
        finally: os.close(fd)


def private_path(path: Path, *, excluded: tuple[Path, ...] = ()) -> Path:
    path = Path(path).absolute()
    parent = ordinary_root(path.parent)
    if path.is_symlink() or path.parent.resolve() != parent:
        raise ValueError("controller-path-link")
    resolved = parent / path.name
    if any(not disjoint(parent, root) for root in excluded):
        raise ValueError("controller-file-must-be-outside-project")
    if os.name == "posix":
        info = parent.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("controller-directory-must-be-owner-only")
    if resolved.exists():
        info = resolved.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("controller-file-not-regular")
    return resolved


def read_private_json(path: Path, *, excluded: tuple[Path, ...] = ()) -> dict:
    return read_json(private_path(path, excluded=excluded))


def write_private_json(path: Path, value: dict) -> None:
    path = private_path(path)
    raw = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".controller-", delete=False) as stream:
        tmp = Path(stream.name); stream.write(raw)
    try: tmp.replace(path)
    finally: tmp.unlink(missing_ok=True)


def git_classes(root: Path) -> dict[str, str]:
    if not (root / ".git/config").is_file(): return {}
    from lax_executor import git_command, command_environment
    from git_safety import admit_git_config
    env = command_environment(); admit_git_config(root, env)
    result = {}
    for label, args in [("tracked", ["--cached"]), ("untracked", ["--others", "--exclude-standard"]),
                        ("ignored", ["--others", "--ignored", "--exclude-standard"])]:
        with tempfile.TemporaryFile() as output:
            subprocess.run(git_command("-C", str(root), "ls-files", "-z", *args), env=env,
                           stdout=output, stderr=subprocess.DEVNULL, check=True, timeout=30)
            if output.tell() > 16 * 1024**2: raise ValueError("git-inventory-limit")
            output.seek(0)
            for name in output.read().decode("utf-8").split("\0"):
                if name: result[name] = label
    return result


def lean_imports(text: str) -> tuple[list[str], bool]:
    gate_file = Path(__file__).resolve().parent.parent / "lean-strict-verification-gate/lean_strict_verification_gate.py"
    spec = importlib.util.spec_from_file_location("_source_inventory_lean_gate", gate_file)
    if spec is None or spec.loader is None: raise ValueError("strict-gate-unavailable")
    gate = importlib.util.module_from_spec(spec); spec.loader.exec_module(gate)
    code, errors = gate._strip_comments_and_strings(text)
    imports = []; opaque = bool(errors)
    for line in code.splitlines():
        m = re.match(r"^\s*(?:(?:public|meta)\s+)*import\s+(.+?)\s*$", line)
        if not m: continue
        names = m[1].split()
        if any(not re.fullmatch(r"[A-Za-z_][\w.']*", n) for n in names): opaque = True
        else: imports.extend(names)
    return sorted(set(imports)), opaque


def inspect_source(root: Path) -> dict:
    root = ordinary_root(root); files = []; omitted = []; total = 0
    classes = git_classes(root)
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in list(dirs):
            path = Path(directory) / name
            if name in GENERATED or path.is_symlink():
                dirs.remove(name); omitted.append({"path": path.relative_to(root).as_posix(), "reason": "generated-or-link"})
        for name in sorted(names):
            path = Path(directory) / name; rel = path.relative_to(root).as_posix()
            entry = {"path": rel, "git_status": classes.get(rel, "not-classified")}
            try:
                raw, executable = selected_bytes(root, rel); total += len(raw)
                if total > MAX_TOTAL: raise ValueError("inventory-byte-limit")
                entry.update(bytes=len(raw), sha256=sha(raw), executable=executable, selectable=True)
                if path.suffix == ".lean":
                    imports, opaque = lean_imports(raw.decode("utf-8"))
                    entry.update(imports=imports, needs_import_review=opaque)
                if path.suffix == ".tex":
                    try: entry["input_candidates"] = sanitize_tex(raw.decode("utf-8"))["input_candidates"]
                    except ValueError: entry["needs_tex_review"] = True
            except (ValueError, OSError, UnicodeError):
                entry["selectable"] = False; entry["reason"] = "requires-review-or-excluded"
            files.append(entry)
            if len(files) > MAX_FILES or total > MAX_TOTAL: raise ValueError("inventory-limit")
    return {"schema_version": "source-inventory.v1", "analysis_status": "static-candidates-only",
            "source_root": str(root), "files": files, "omitted_directories": omitted,
            "limitations": ["imports and includes are candidates, not a complete dependency closure",
                            "privacy and distribution rights require review", "no source was executed"]}


def transform(raw: bytes, entry: dict) -> bytes:
    if entry["transform"] == "copy": return raw
    if entry["transform"] == "tex":
        return sanitize_tex(raw.decode("utf-8"), keep_comment_lines=entry.get("keep_comment_lines", []))["text"].encode("utf-8")
    raise ValueError("unknown-transform")


def dependency_candidates(root: Path, modules: list[str], module_roots: list[str] | None = None) -> dict:
    inventory = inspect_source(root); module_roots = module_roots or ["."]
    index = {}; files = {e["path"]: e for e in inventory["files"]}
    for prefix in module_roots:
        if prefix != ".": relative_name(prefix)
        for name, entry in files.items():
            if not name.endswith(".lean") or not entry.get("selectable"): continue
            if prefix != "." and not name.startswith(prefix + "/"): continue
            rel = name if prefix == "." else name[len(prefix) + 1:]
            module = rel[:-5].replace("/", ".")
            if module in index and index[module] != name: raise ValueError("ambiguous-module-root")
            index[module] = name
    if not modules or any(m not in index for m in modules): raise ValueError("root-module-not-found")
    pending = list(modules); selected = set(); external = set(); opaque = []
    while pending:
        module = pending.pop()
        if module in selected: continue
        selected.add(module); entry = files[index[module]]
        if entry.get("needs_import_review"): opaque.append(module)
        for imported in entry.get("imports", []):
            if imported in index: pending.append(imported)
            else: external.add(imported)
    return {"schema_version": "lean-dependency-candidates.v1", "analysis_status": "static-candidates-only",
            "root_modules": modules, "module_roots": module_roots,
            "required_files": sorted(index[m] for m in selected), "external_imports": sorted(external),
            "imports_needing_review": sorted(opaque),
            "limitations": ["review module-root mapping; custom syntax may hide dependencies",
                            "external imports need pinned dependency admission; isolated build remains required"]}


def plan_binding(plan: dict) -> str:
    return digest({k: v for k, v in plan.items() if k != "approval"})


def plan_selection(source: Path, destination: Path, selections: list[dict], *, job_id: str, public_origin: str) -> dict:
    source = ordinary_root(source); destination = Path(destination).absolute()
    if not destination.parent.is_dir() or destination.is_symlink() or destination.exists():
        raise ValueError("destination-must-be-new")
    destination = destination.parent.resolve() / destination.name
    if not disjoint(source, destination): raise ValueError("source-destination-overlap")
    if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id): raise ValueError("invalid-job-id")
    globals()["public_origin"](public_origin)
    if not isinstance(selections, list) or not 1 <= len(selections) <= MAX_FILES: raise ValueError("invalid-selection")
    files = []; seen: set[str] = set(); total = 0
    for item in selections:
        if not isinstance(item, dict) or set(item) - {"source", "target", "transform", "reason", "provenance", "keep_comment_lines"}:
            raise ValueError("invalid-selection-fields")
        for key in ["reason", "provenance"]:
            if not isinstance(item.get(key), str) or not item[key].strip(): raise ValueError("selection-needs-reason-and-provenance")
        src = relative_name(item["source"]); target = relative_name(item["target"])
        normalized = unicodedata.normalize("NFC", target).casefold()
        if normalized in seen or any(normalized.startswith(x + "/") or x.startswith(normalized + "/") for x in seen):
            raise ValueError("target-path-collision")
        seen.add(normalized)
        raw, executable = selected_bytes(source, src); cleaned = transform(raw, item)
        total += len(raw) + len(cleaned)
        if total > MAX_TOTAL: raise ValueError("selection-byte-limit")
        files.append({**item, "source_sha256": sha(raw), "output_sha256": sha(cleaned),
                      "bytes": len(cleaned), "executable": executable})
    return {"schema_version": POLICY, "tex_policy": TEX_POLICY, "job_id": job_id,
            "source_root": str(source), "destination": str(destination), "public_origin": public_origin,
            "files": sorted(files, key=lambda e: e["target"]), "publication_enabled": False}


def approve_plan(path: Path, reviewer: str) -> None:
    plan = read_private_json(path)
    read_private_json(path, excluded=(Path(plan["source_root"]), Path(plan["destination"])))
    if not isinstance(reviewer, str) or not reviewer.strip(): raise ValueError("reviewer-required")
    plan["approval"] = {"reviewer": reviewer, "selection_digest": plan_binding(plan)}
    write_private_json(path, plan)


def write_at(root_fd: int, name: str, raw: bytes, executable: bool) -> None:
    parts = relative_name(name).split("/"); fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            try: os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError: pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
        out = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755 if executable else 0o644, dir_fd=fd)
        with os.fdopen(out, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    finally: os.close(fd)


def export_plan(path: Path, *, apply: bool = False) -> dict:
    plan = read_private_json(path)
    if plan.get("schema_version") != POLICY or plan.get("tex_policy") != TEX_POLICY:
        raise ValueError("unsupported-selection-policy")
    approval = plan.get("approval", {})
    if not approval.get("reviewer") or approval.get("selection_digest") != plan_binding(plan):
        raise ValueError("selection-review-missing-or-stale")
    source = Path(plan["source_root"]); destination = Path(plan["destination"])
    read_private_json(path, excluded=(source, destination))
    selections = [{k: v for k, v in f.items() if k not in {"source_sha256", "output_sha256", "bytes", "executable"}} for f in plan["files"]]
    current = plan_selection(source, destination, selections, job_id=plan["job_id"], public_origin=plan["public_origin"])
    if plan_binding(current) != plan_binding(plan): raise ValueError("selected-input-changed")
    blobs = []
    for item in plan["files"]:
        raw, executable = selected_bytes(source, item["source"])
        if sha(raw) != item["source_sha256"] or executable != item["executable"]: raise ValueError("selected-input-changed")
        cleaned = transform(raw, item)
        if sha(cleaned) != item["output_sha256"]: raise ValueError("transformation-changed")
        blobs.append((item, cleaned))
    inventory = [{"path": e["target"], "bytes": len(raw), "sha256": sha(raw), "executable": e["executable"]} for e, raw in blobs]
    result = {"schema_version": "public-export.v1", "status": "dry-run", "job_id": plan["job_id"],
              "selection_digest": plan_binding(plan), "source_digest": digest(inventory), "files": inventory,
              "privacy_status": "requires-final-review", "publication_enabled": False}
    if apply:
        if os.name != "posix": raise ValueError("export-not-qualified-on-native-windows")
        with directory_fd(destination.parent) as parent_fd:
            # mkdir is exclusive; there is no overwrite/merge or cleanup of a user's directory.
            os.mkdir(destination.name, 0o700, dir_fd=parent_fd)
            fd = os.open(destination.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            try:
                for item, raw in blobs: write_at(fd, item["target"], raw, item["executable"])
            except Exception:
                # No receipt is returned for a partial output. Never recursively
                # clean a path which another process could have exchanged.
                raise ValueError("export-incomplete-destination-quarantined") from None
            finally: os.close(fd)
        result["status"] = "exported"
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("inventory"); scan.add_argument("--source", type=Path, required=True); scan.add_argument("--out", type=Path, required=True)
    closure = sub.add_parser("closure"); closure.add_argument("--source", type=Path, required=True)
    closure.add_argument("--module", action="append", required=True); closure.add_argument("--module-root", action="append")
    closure.add_argument("--out", type=Path, required=True)
    plan = sub.add_parser("plan")
    for key in ["source", "destination", "selection", "out"]: plan.add_argument("--" + key, type=Path, required=True)
    plan.add_argument("--job-id", required=True); plan.add_argument("--public-origin", required=True)
    approve = sub.add_parser("approve"); approve.add_argument("--plan", type=Path, required=True); approve.add_argument("--reviewer", required=True)
    export = sub.add_parser("export"); export.add_argument("--plan", type=Path, required=True); export.add_argument("--out", type=Path, required=True); export.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)
    try:
        if hasattr(args, "out") and (args.out.exists() or args.out.is_symlink()):
            raise ValueError("output-must-be-new")
        if args.command == "closure":
            private_path(args.out, excluded=(ordinary_root(args.source),))
            value = dependency_candidates(args.source, args.module, args.module_root)
            write_private_json(args.out, value)
            summary = {"status": "dependency-candidates", "files": len(value["required_files"]), "analysis_status": value["analysis_status"]}
        elif args.command == "inventory":
            private_path(args.out, excluded=(ordinary_root(args.source),))
            value = inspect_source(args.source); write_private_json(args.out, value)
            summary = {"status": "inventoried", "files": len(value["files"]), "analysis_status": value["analysis_status"]}
        elif args.command == "plan":
            selected = read_private_json(args.selection, excluded=(ordinary_root(args.source), args.destination.resolve()))
            value = plan_selection(args.source, args.destination, selected["files"], job_id=args.job_id, public_origin=args.public_origin)
            private_path(args.out, excluded=(ordinary_root(args.source), args.destination.resolve()))
            write_private_json(args.out, value); summary = {"status": "review-required", "files": len(value["files"])}
        elif args.command == "approve":
            approve_plan(args.plan, args.reviewer); summary = {"status": "selection-reviewed"}
        else:
            value = read_private_json(args.plan)
            private_path(args.out, excluded=(Path(value["source_root"]), Path(value["destination"])))
            value = export_plan(args.plan, apply=args.apply); write_private_json(args.out, value)
            summary = {k: value[k] for k in ["status", "job_id", "source_digest", "privacy_status"]}
        print(json.dumps({**summary, "publication_enabled": False})); return 0
    except (ValueError, OSError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError):
        # Private file names and source text never enter stdout/CI errors.
        print(json.dumps({"status": "blocked", "reason": "source-preparation-refused", "publication_enabled": False})); return 1


if __name__ == "__main__": raise SystemExit(main())
