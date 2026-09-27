"""Supervisor-owned, nonpublishing source -> sealed capture -> replay pipeline."""
from __future__ import annotations

import hashlib
import io
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import re
import selectors
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from git_safety import admit_git_config

from lax_formalization import (BACKGROUND, ENVIRONMENT, MATHLIB_SHA, SPEC_SHA, VERSION,
    container_options, digest, extract_archive, proof_closure, read_json, regular_bytes,
    safe_name, tree_inventory, verification_binding)

HERE = Path(__file__).resolve().parent
LIMIT = 16 * 1024**2
REQUEST_KEYS = {"schema_version", "project_root", "submission", "database_root", "environment", "targets",
    "challenge_root", "semantic_review", "dependencies"}


def git_command(*args: str) -> list[str]:
    if "-C" in args:
        at = args.index("-C")
        admit_git_config(Path(args[at + 1]), command_environment(), allow_init=args[at + 2:at + 3] == ("init",))
    return ["git", "--no-pager", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
            "-c", "core.attributesFile=/dev/null", "-c", "diff.ignoreSubmodules=all", *args]


def command_environment() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0"}


def run(args: list[str], *, cwd: Path | None = None, timeout: int = 60, data: bytes | None = None) -> bytes:
    env = command_environment()
    if args[0] == "git": args = git_command(*args[1:])
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                stdout=output, stderr=errors, preexec_fn=lambda: limit_file_output(LIMIT))
        try:
            proc.communicate(data, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.communicate(); raise ValueError("command time limit exceeded")
        if output.tell() >= LIMIT or errors.tell() >= LIMIT: raise ValueError("command output limit exceeded")
        errors.seek(0); error = errors.read()
        output.seek(0); stdout = output.read()
    if proc.returncode:
        raise ValueError(f"{Path(args[0]).name} failed (exit {proc.returncode}): {error.decode(errors='replace')[-1200:]}")
    return stdout


def limit_file_output(limit: int) -> None:
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))


def git(root: Path, *args: str) -> str:
    return run(["git", "-C", str(root), *args]).decode().strip()


def source_inventory(root: Path) -> dict[str, Any]:
    entries = run(["git", "-C", str(root), "ls-files", "--stage", "-z"]).decode().split("\0")
    files = []; total = 0
    if len(entries) > 100001: raise ValueError("source inventory entry limit")
    for entry in entries:
        if not entry: continue
        header, name = entry.split("\t", 1); mode, _, stage = header.split()
        safe_name(name)
        if mode not in {"100644", "100755"} or stage != "0": raise ValueError("source contains link, submodule or unresolved index stage")
        p = root / name
        if any(parent.is_symlink() for parent in p.parents if parent != root.parent): raise ValueError("source path traverses a link")
        raw = regular_bytes(p); total += len(raw)
        if total > 2 * 1024**3: raise ValueError("source byte limit")
        files.append({"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "executable": mode == "100755"})
    files.sort(key=lambda f: f["path"])
    return {"sha256": digest(files), "files": files, "bytes": total}


def settings(config: Path | None = None) -> dict[str, Any]:
    p = config or Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "lax-formalization/config.json"
    if not p.is_file(): raise ValueError("executor not provisioned; follow the skill's executor setup reference")
    if p.is_symlink() or p.stat().st_mode & 0o022: raise ValueError("executor config must be operator-owned and not group/world writable")
    cfg = read_json(p)
    for key in ["package_root", "elan_home", "warm_root", "tools_root"]:
        path = Path(cfg[key])
        if path.is_symlink() or not path.is_dir(): raise ValueError(f"invalid executor {key}")
    if read_json(Path(cfg["package_root"]) / "package.json").get("version") != VERSION:
        raise ValueError("unqualified Lax version")
    if hashlib.sha256(regular_bytes(Path(cfg["package_root"]) / "spec.md")).hexdigest() != SPEC_SHA:
        raise ValueError("Lax specification drift")
    if tree_inventory(Path(cfg["package_root"]), exclude_generated=False, trusted_package_links=True)["sha256"] != cfg["package_sha256"]:
        raise ValueError("qualified Lax package bytes changed")
    return cfg


def trusted_inputs(cfg: dict[str, Any]) -> dict[str, str]:
    """Stream hash the exact trusted tool/cache bytes, without loading large libs."""
    cache_file = Path(cfg["tools_root"]).parent / "fingerprint-cache.json"
    cached = {}
    if cache_file.exists():
        info = cache_file.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("fingerprint cache must be a private operator-owned regular file")
        try:
            cached = json.loads(regular_bytes(cache_file, 128 * 1024**2))
            if not isinstance(cached, dict): cached = {}
        except (ValueError, OSError): cached = {}
    updated = {}
    roots = {"toolchain": Path(cfg["elan_home"]) / "toolchains/leanprover--lean4---v4.33.0",
             "elan_launchers": Path(cfg["elan_home"]) / "bin",
             "warm": Path(cfg["warm_root"]) / "v4.33.0-db584cd6d46c",
             "inspector": Path(cfg["tools_root"])}
    result = {}
    for label, root in roots.items():
        root = root.resolve(strict=True)
        entries = []
        for directory, names, files in os.walk(root, followlinks=False):
            if ".git" in names: names.remove(".git")
            for name in sorted(names + files):
                p = Path(directory) / name; info = p.lstat(); rel = p.relative_to(root).as_posix()
                if stat.S_ISLNK(info.st_mode):
                    target = p.resolve(strict=True)
                    if root not in target.parents: raise ValueError("trusted cache link escapes its qualified root")
                    if ".git" in target.relative_to(root).parts: raise ValueError("trusted cache link enters excluded metadata")
                    entries.append((rel, "link", os.readlink(p))); continue
                if stat.S_ISDIR(info.st_mode): continue
                if not stat.S_ISREG(info.st_mode): raise ValueError("trusted cache contains a special file")
                signature = [info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
                key = str(p); old = cached.get(key)
                if isinstance(old, dict) and old.get("stat") == signature and isinstance(old.get("sha256"), str) and re.fullmatch(r"[a-f0-9]{64}", old["sha256"]):
                    content_hash = old["sha256"]
                else:
                    h = hashlib.sha256()
                    with p.open("rb") as f:
                        while chunk := f.read(1024 * 1024): h.update(chunk)
                    content_hash = h.hexdigest()
                after = p.stat()
                if signature != [after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns]:
                    raise ValueError("trusted cache changed while hashing")
                updated[key] = {"stat": signature, "sha256": content_hash}
                entries.append((rel, info.st_size, bool(info.st_mode & 0o111), content_hash))
        if not entries: raise ValueError(f"trusted {label} input is absent")
        result[label] = digest(sorted(entries))
    with tempfile.NamedTemporaryFile(mode="w", dir=cache_file.parent, prefix="fingerprints-", delete=False) as f:
        temp = Path(f.name); json.dump(updated, f, separators=(",", ":"))
    temp.replace(cache_file)
    return result


def executor_identity() -> str:
    import git_safety
    files = [p for p in HERE.iterdir() if p.suffix in {".py", ".mjs"}]
    files += [Path(git_safety.__file__), HERE.parent / "lean-strict-verification-gate/lean_strict_verification_gate.py"]
    return digest({p.name: hashlib.sha256(regular_bytes(p)).hexdigest() for p in files})


def doctor() -> dict[str, Any]:
    result: dict[str, Any] = {"schema_version": "lax-doctor.v1", "status": "ok", "supported_lax": VERSION,
        "environment": ENVIRONMENT, "network_required": False, "installs_attempted": False,
        "publication_enabled": False, "executor_ready": False}
    try:
        cfg = settings(); result["executor_ready"] = True; result["executor_image"] = cfg["image"]
    except (OSError, ValueError, KeyError) as exc:
        result["note"] = str(exc)
    return result


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, indent=2); handle.write("\n")
    path.chmod(0o600)


def validate_request(path: Path) -> dict[str, Any]:
    req = read_json(path)
    if set(req) - REQUEST_KEYS or req.get("schema_version") != "lax-request.v1": raise ValueError("invalid request schema")
    if req.get("environment") != ENVIRONMENT: raise ValueError("unsupported Lax environment")
    if not isinstance(req.get("targets"), list) or not req["targets"] or len(req["targets"]) > 500:
        raise ValueError("a bounded nonempty target set is required")
    if any(not isinstance(t, str) or not re.fullmatch(r"[A-Za-z_][\w'.]*", t) for t in req["targets"]) or len(set(req["targets"])) != len(req["targets"]):
        raise ValueError("invalid or duplicate target")
    folder = req.get("submission", "submission")
    if folder != ".": safe_name(folder)
    root = Path(req["project_root"]).resolve(strict=True)
    if root == path.resolve() or root in path.resolve().parents: raise ValueError("request must be outside candidate project")
    for key in ["database_root", "challenge_root"]:
        p = Path(req[key]); p.resolve(strict=True)
        if p.is_symlink() or not p.is_dir(): raise ValueError(f"invalid {key}")
    challenge = Path(req["challenge_root"]).resolve()
    if challenge == root or root in challenge.parents: raise ValueError("challenge must be outside candidate project")
    deps = req.get("dependencies", {})
    if not isinstance(deps, dict) or len(deps) > 100 or any(not re.fullmatch(r"lax-[1-9][0-9]*", k) or not isinstance(v,str) or not v for k,v in deps.items()):
        raise ValueError("dependencies must map bounded archive IDs to request files")
    if "semantic_review" in req:
        review = req["semantic_review"]
        if (not isinstance(review,dict) or set(review) != {"status","reviewer","challenge_sha256","scope_digest"}
                or review["status"] not in {"accepted","pending","rejected"}
                or not isinstance(review["reviewer"],str) or not review["reviewer"].strip()
                or any(not isinstance(review[k],str) or not re.fullmatch(r"[a-f0-9]{64}",review[k]) for k in ["challenge_sha256","scope_digest"])):
            raise ValueError("invalid semantic review contract")
    return req


def export_source(root: Path, destination: Path) -> dict[str, str]:
    """Export an exact clean Git revision and reconstruct only its Git objects."""
    if git(root, "rev-parse", "--show-toplevel") != str(root.resolve()): raise ValueError("project_root must be the Git top level")
    if git(root, "status", "--porcelain", "--untracked-files=normal", "--ignore-submodules=all"): raise ValueError("source must be committed and clean")
    sha = git(root, "rev-parse", "HEAD")
    tree = git(root, "rev-parse", "HEAD^{tree}")
    with tempfile.TemporaryFile() as archive:
        p = subprocess.Popen(git_command("-C", str(root), "archive", "--format=tar", sha), stdout=archive, stderr=subprocess.PIPE,
                             env=command_environment(), preexec_fn=lambda: limit_file_output(2 * 1024**3))
        try: _, err = p.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill(); p.communicate(); raise ValueError("source export timeout")
        if p.returncode or archive.tell() > 2 * 1024**3: raise ValueError("source export failed or exceeded limit")
        archive.seek(0); destination.mkdir(); extract_archive(archive, destination)
    git(destination, "init", "--quiet")
    git(destination, "add", "--force", "--all")
    if git(destination, "write-tree") != tree: raise ValueError("source export differs from original Git tree (export-ignore/LFS?)")
    commit = run(["git", "-C", str(root), "cat-file", "commit", sha])
    restored = run(["git", "-C", str(destination), "hash-object", "-t", "commit", "-w", "--stdin"], data=commit).decode().strip()
    if restored != sha: raise ValueError("commit reconstruction mismatch")
    git(destination, "update-ref", "HEAD", sha)
    (destination / ".git/shallow").write_text(sha + "\n", encoding="utf-8")
    try: origin = git(root, "remote", "get-url", "origin")
    except ValueError: origin = "https://github.com/local/local"
    if not re.fullmatch(r"https://(?:github\.com|gitlab\.com|codeberg\.org|bitbucket\.org)/[A-Za-z0-9_.\-/]+", origin):
        raise ValueError("source origin must be credential-free canonical HTTPS")
    git(destination, "remote", "add", "origin", origin)
    return {"repository": origin.removesuffix(".git"), "commit": sha, "tree": tree}


def phase(cfg: dict[str, Any], mode: str, job: Path, mounts: list[tuple[Path, str, bool]], *, timeout: int = 1200) -> dict[str, Any]:
    _raise_verification_termination()
    output = job / f"result-{mode}"; output.mkdir()
    name = f"aas-lax-{uuid.uuid4().hex}"
    common = [(Path(cfg["package_root"]), "/lax-package", False), (HERE, "/adapter", False),
              (Path(cfg["elan_home"]), "/elan", False), (Path(cfg["warm_root"]), "/lax-home/warm", False),
              (Path(cfg["tools_root"]), "/lax-home/tools", mode == "prepare"),
              (job / "control", "/control", False)]
    phase_mounts = [(p, "/input/work" if target == "/work" else target, False) for p, target, _ in mounts]
    args = container_options(name, cfg["image"], common + phase_mounts, writable=mode == "prepare")
    args += ["node", "--disable-sigusr1", "/adapter/container_entry.mjs", mode]
    log = output / "transcript.log"
    with log.open("xb") as stream:
        p = None; selector = None
        received = 0; next_probe = 0.0; outcome = None
        try:
            p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            started = time.monotonic()
            selector = selectors.DefaultSelector(); selector.register(p.stdout, selectors.EVENT_READ)
            _raise_verification_termination()
            while outcome is None:
                _raise_verification_termination()
                for key, _ in selector.select(0.1):
                    chunk = os.read(key.fd, 65536)
                    if chunk:
                        received += len(chunk)
                        if received > LIMIT: raise ValueError("executor output limit exceeded")
                        stream.write(chunk); stream.flush()
                if p.poll() is not None: raise ValueError(f"executor terminated unexpectedly (exit {p.returncode})")
                now = time.monotonic()
                if now - started > timeout: raise ValueError("executor time limit exceeded")
                if now >= next_probe:
                    probe = subprocess.run(["docker", "exec", name, "cat", "/result/.finished.json"], capture_output=True, timeout=10)
                    if probe.returncode == 0:
                        if len(probe.stdout) > 1024: raise ValueError("invalid executor completion packet")
                        outcome = json.loads(probe.stdout)
                    next_probe = now + 0.5
            _raise_verification_termination()
            stopped = run(["docker", "exec", name, "node", "--disable-sigusr1", "/adapter/stop_children.mjs"])
            if stopped.strip() != b"descendants-stopped": raise ValueError("descendant teardown not confirmed")
            collect = output / "collected"; collect.mkdir()
            collect_container(name, "/result/.", collect, max_bytes=64 * 1024**2)
            for child in collect.iterdir():
                destination = output / child.name
                if destination.exists(): raise ValueError("phase output collides with supervisor file")
                child.rename(destination)
            collect.rmdir()
            if mode in {"concepts", "compile"} and outcome.get("exit") == 0:
                capture = job / f"work-{mode}/job/capture"; capture.mkdir(parents=True)
                collect_container(name, "/work/job/capture/.", capture, max_bytes=2 * 1024**3)
        finally:
            # Removing the container terminates every descendant, even after the
            # entrypoint exited. Confirm removal even on exceptional exits.
            try:
                try:
                    subprocess.run(["docker", "rm", "--force", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
                finally:
                    if p is not None:
                        if p.poll() is None: p.kill(); p.wait(timeout=10)
                        if p.stdout: p.stdout.close()
                    if selector is not None: selector.close()
            finally:
                probe = subprocess.run(["docker", "ps", "--all", "--quiet", "--filter", f"name=^/{name}$"],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
                if probe.returncode != 0 or probe.stdout.strip():
                    raise ValueError("container teardown could not be confirmed")
    _raise_verification_termination()
    if not isinstance(outcome, dict) or outcome.get("mode") != mode or outcome.get("exit") != 0:
        raise ValueError(f"{mode} failed; see {log.name} in phase evidence")
    return {"mode": mode, "exit": outcome["exit"], "teardown_confirmed": True,
            "seconds": round(time.monotonic()-started, 3), "transcript_sha256": hashlib.sha256(regular_bytes(log, LIMIT)).hexdigest()}


def collect_container(name: str, path: str, destination: Path, *, max_bytes: int) -> None:
    """Read bounded tmpfs after hostile children are gone; PID 1 only holds it."""
    with tempfile.TemporaryFile() as archive:
        p = subprocess.Popen(["docker", "exec", name, "/usr/bin/tar", "-C", path, "-cf", "-", "."], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        selector = selectors.DefaultSelector(); selector.register(p.stdout, selectors.EVENT_READ)
        total = 0; started = time.monotonic()
        try:
            while True:
                ready = selector.select(0.2)
                if time.monotonic() - started > 90: raise ValueError("artifact collection timeout")
                if ready:
                    chunk = os.read(p.stdout.fileno(), 65536)
                    if not chunk: break
                    total += len(chunk)
                    if total > max_bytes + 64 * 1024**2: raise ValueError("artifact collection byte limit")
                    archive.write(chunk)
                elif p.poll() is not None: break
            if p.wait(timeout=10): raise ValueError("artifact collection failed")
        finally:
            if p.poll() is None: p.kill(); p.wait(timeout=10)
            selector.close()
            if p.stdout: p.stdout.close()
        archive.seek(0)
        extract_archive(archive, destination, max_bytes=max_bytes, max_file_bytes=512 * 1024**2)


def copy_sealed(source: Path, destination: Path) -> dict[str, Any]:
    before = tree_inventory(source, exclude_generated=False)
    shutil.copytree(source, destination, symlinks=False)
    if tree_inventory(source, exclude_generated=False) != before or tree_inventory(destination, exclude_generated=False) != before:
        raise ValueError("artifact changed during sealing")
    return before


def admit_capture(capture: Path, inventory: dict[str, Any], source: Path) -> None:
    """A compiler cannot add a loadable shadow module to the checking path."""
    modules = [inventory["rootModule"], *inventory["modules"]]
    allowed = {"package/lakefile.toml", "package/lean-toolchain", "package/lake-manifest.json"}
    required = set()
    for module in modules:
        base = module.replace(".", "/")
        required.add("lib/" + base + ".olean")
        allowed.add("package/" + base + ".lean")
        for suffix in [".olean", ".olean.hash", ".ilean", ".ilean.hash", ".trace"]:
            allowed.add("lib/" + base + suffix)
        for suffix in [".c", ".c.hash"]: allowed.add("ir/" + base + suffix)
    actual = tree_inventory(capture, exclude_generated=False)
    names = {x["path"] for x in actual["files"]}
    if names - allowed or not required <= names:
        raise ValueError("captured files do not match the exact module inventory")
    for item in actual["files"]:
        rel = item["path"]
        if rel.startswith("package/") and rel != "package/lake-manifest.json":
            if hashlib.sha256(regular_bytes(source / rel.removeprefix("package/"))).hexdigest() != item["sha256"]:
                raise ValueError("captured authored input differs from frozen source")


_termination_state: ContextVar[dict[str, int | None] | None] = ContextVar("lax_termination", default=None)


def _raise_verification_termination():
    state = _termination_state.get()
    if state is not None and state["signal"] is not None:
        raise SystemExit(128 + state["signal"])


@contextmanager
def verification_termination_guard():
    """Deliver main-thread termination only at safe supervisor checkpoints."""
    if _termination_state.get() is not None or threading.current_thread() is not threading.main_thread():
        yield
        return
    state = {"signal": None}
    token = _termination_state.set(state)
    previous = {}
    def terminate(signum, frame):
        if state["signal"] is None:
            state["signal"] = signum
    try:
        for name in ["SIGTERM", "SIGHUP", "SIGINT"]:
            signum = getattr(signal, name, None)
            if signum is not None:
                previous[signum] = signal.signal(signum, terminate)
        yield
        _raise_verification_termination()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        _termination_state.reset(token)


def verify(request_path: Path, out: Path, *, dependency: bool = False, _active: set[str] | None = None) -> dict[str, Any]:
    with verification_termination_guard():
        return _verify(request_path, out, dependency=dependency, _active=_active)


def _verify(request_path: Path, out: Path, *, dependency: bool = False, _active: set[str] | None = None) -> dict[str, Any]:
    out = out.resolve()
    req = validate_request(request_path)
    req_hash = hashlib.sha256(regular_bytes(request_path)).hexdigest()
    cfg = settings()
    trust_before = trusted_inputs(cfg)
    if trust_before != cfg.get("trusted_inputs"): raise ValueError("trusted tool/cache bytes changed; explicit reprovisioning and review required")
    executor_before = executor_identity()
    root = Path(req["project_root"]).resolve(); database = Path(req["database_root"]).resolve()
    challenge = Path(req["challenge_root"]).resolve(); folder = req.get("submission", "submission")
    if out.exists() or root == out.resolve() or root in out.resolve().parents: raise ValueError("evidence directory must be new and outside project")
    if shutil.disk_usage(out.parent).free < 5 * 1024**3: raise ValueError("less than 5 GiB headroom for bounded verification artifacts")
    out.mkdir(parents=True, mode=0o700); job = out / "execution"; job.mkdir(); (job / "control").mkdir()
    source = job / "source"; identity = export_source(root, source)
    source_before = source_inventory(root)
    if source_before != source_inventory(source): raise ValueError("worktree bytes differ from exported revision")
    snapshot = job / "database"; db_identity = export_source(database, snapshot)
    database_before = source_inventory(database)
    if database_before != source_inventory(snapshot): raise ValueError("database worktree differs from exported revision")
    concept = source / folder / "concepts"
    challenge_before = tree_inventory(challenge)
    if tree_inventory(concept) != challenge_before: raise ValueError("candidate concepts do not match immutable challenge")
    from importlib.util import module_from_spec, spec_from_file_location
    gate_path = HERE.parent / "lean-strict-verification-gate/lean_strict_verification_gate.py"
    module_spec = spec_from_file_location("_lax_concept_gate", gate_path)
    if module_spec is None or module_spec.loader is None: raise ValueError("strict concept safety gate unavailable")
    gate = module_from_spec(module_spec); sys.modules[module_spec.name] = gate; module_spec.loader.exec_module(gate)
    for file in concept.rglob("*.lean"):
        checked = gate.scan_path(file, "final_candidate", set())
        bad = [f for f in checked["findings"] if f["detail"] != "axiom" and f["kind"] != "non_allowlisted_import"]
        if bad: raise ValueError("challenge concepts failed source safety gate")
    # Source-only static validation is run before any candidate Lean executes.
    id_match = re.search(r'(?m)^id:\s*["\']?(lax-[1-9][0-9]*)', (source / folder / "manifest.yaml").read_text(encoding="utf-8"))
    if id_match is None: raise ValueError("cannot determine submission ID")
    sid = id_match.group(1)
    active = set(_active or ())
    if sid in active: raise ValueError("dependency verification cycle")
    active.add(sid)
    if dependency:
        record = read_json(snapshot / sid / "record.json")
        expected = dict(identity); expected.pop("tree"); expected["folder"] = folder
        if record.get("state") != "registered" or record.get("source") != expected:
            raise ValueError("dependency source does not match registered record")
    control = {"environment": ENVIRONMENT, "folder": folder, "archiveSha": db_identity["commit"],
        "request": {"requestVersion": 1, "id": sid, "archiveSha": db_identity["commit"],
                    "source": {"repository": identity["repository"], "commit": identity["commit"], "folder": folder}}}
    write_json(job / "control/spec.json", control)
    phases = [phase(cfg, "static", job, [(source, "/source", False), (snapshot, "/database", False)])]
    static = read_json(job / "result-static/static.json")
    if static["manifest"]["mathlibVersion"] != MATHLIB_SHA: raise ValueError("mathlib pin mismatch")
    dependency_reports: dict[str, Any] = {}
    dependency_out: dict[str, Path] = {}
    for entry in static["resolution"]["all"]:
        dep_id = entry["submissionId"]
        if dep_id in dependency_reports: continue
        dep_request = req.get("dependencies", {}).get(dep_id)
        if not isinstance(dep_request, str): raise ValueError(f"independent dependency request missing: {dep_id}")
        dep_dir = out / "dependencies" / dep_id; dep_dir.parent.mkdir(exist_ok=True)
        dep_report = verify(Path(dep_request), dep_dir, dependency=True, _active=active)
        if dep_report["machine_status"] != "passed": raise ValueError("dependency verification failed")
        expected = entry["source"]
        actual_source = dep_report["source"]
        if (dep_report.get("submission_id") != dep_id or dep_report.get("submission_folder") != expected["folder"]
                or actual_source["repository"] != expected["repository"] or actual_source["commit"] != expected["commit"]
                or dep_report["database_commit"] != db_identity["commit"] or dep_report["database_digest"] != database_before["sha256"]
                or dep_report["tools"]["environment"] != ENVIRONMENT):
            raise ValueError("dependency evidence does not match parent's resolved source and database")
        dependency_reports[dep_id] = dep_report; dependency_out[dep_id] = dep_dir
    lib_mounts = []; libs = {}
    for entry in static["resolution"]["all"]:
        name = entry["packageName"]; kind = entry["kind"]
        location = dependency_out[entry["submissionId"]] / "sealed" / kind / "lib"
        target = f"/dependencies/{name}"
        lib_mounts.append((location, target, False)); libs[name] = target
    control["dependencyLibs"] = libs
    (job / "control/spec.json").unlink(); write_json(job / "control/spec.json", control)
    for mode in ["concepts", "compile"]:
        work = job / f"work-{mode}"; work.mkdir(); shutil.copytree(source, work / "repo")
        for kind in ["concepts", "proofs"]:
            for entry in static["resolution"]["all"]:
                dest = work / "repo" / folder / kind / ".lake/packages" / entry["packageName"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(dependency_out[entry["submissionId"]] / "execution/source", dest)
        phases.append(phase(cfg, mode, job, [(work, "/work", True), (snapshot, "/database", False)]))
        if source_inventory(work / "repo") != source_inventory(source): raise ValueError("source changed during compilation")
    sealed = out / "sealed"; sealed.mkdir()
    admit_capture(job / "work-concepts/job/capture/concepts", static["inventories"]["concepts"], source / folder / "concepts")
    admit_capture(job / "work-compile/job/capture/proofs", static["inventories"]["proofs"], source / folder / "proofs")
    copy_sealed(job / "work-concepts/job/capture/concepts", sealed / "concepts")
    copy_sealed(job / "work-compile/job/capture/proofs", sealed / "proofs")
    seal = tree_inventory(sealed, exclude_generated=False)
    phases.append(phase(cfg, "check", job, [(source, "/source", False), (snapshot, "/database", False), (sealed, "/capture", False)] + lib_mounts))
    if tree_inventory(sealed, exclude_generated=False) != seal: raise ValueError("sealed artifacts changed during checking")
    report = read_json(job / "result-check/checked.json")
    if report.get("ok") is not True: raise ValueError("fresh inspection failed")
    statements = {s["id"] for c in report["inspection"]["concepts"] for s in c["statements"]}
    if not set(req["targets"]) <= statements: raise ValueError("target missing from checked concept inventory")
    proofs = [{**p, "state": "registered" if dependency else "local", "independently_verified": True} for p in report["inspection"]["proofs"]]
    by_proof = {p["id"]: p for p in proofs}
    for dep in dependency_reports.values():
        for p in dep["verified_proofs"]:
            if p["id"] in by_proof and by_proof[p["id"]] != p: raise ValueError("inconsistent dependency witness identity")
            by_proof[p["id"]] = p
    proofs = list(by_proof.values())
    closure = proof_closure(req["targets"], proofs)
    used_statements = {by_proof[w]["conclusion"] for w in closure["witnesses"]}
    own_statements = statements
    reviewed_dependencies = set().union(*(set(d["targets"]) for d in dependency_reports.values())) if dependency_reports else set()
    dependency_scope_ok = (used_statements - own_statements) <= reviewed_dependencies
    if source_before != source_inventory(root) or database_before != source_inventory(database) or challenge_before != tree_inventory(challenge):
        raise ValueError("verification inputs changed")
    if hashlib.sha256(regular_bytes(request_path)).hexdigest() != req_hash: raise ValueError("request changed during verification")
    if git(root, "rev-parse", "HEAD") != identity["commit"] or git(database, "rev-parse", "HEAD") != db_identity["commit"]:
        raise ValueError("source or database revision changed")
    semantic = req.get("semantic_review", {})
    if trust_before != trusted_inputs(cfg) or executor_before != executor_identity(): raise ValueError("trusted executor inputs changed during verification")
    semantic_ok = (isinstance(semantic, dict) and semantic.get("status") == "accepted"
        and semantic.get("challenge_sha256") == challenge_before["sha256"] and semantic.get("scope_digest") == digest(req["targets"]) and isinstance(semantic.get("reviewer"), str)
        and bool(semantic["reviewer"].strip()) and dependency_scope_ok and all(d["semantic_status"] == "accepted" for d in dependency_reports.values()))
    result = {"schema_version": "lax-verification.v1", "backend": "lax", "status": "passed" if closure["closed"] else "failed",
        "machine_status": "passed", "closure_status": "closed" if closure["closed"] else "open", "closure": closure,
        "semantic_status": "accepted" if semantic_ok else "pending", "publication_status": "registered" if dependency else "local",
        "publication_enabled": False, "submission_id": sid, "submission_folder": folder,
        "source": identity, "source_digest": source_before["sha256"], "scope_digest": digest(req["targets"]),
        "targets": req["targets"], "challenge_digest": challenge_before["sha256"], "request_digest": req_hash,
        "database_commit": db_identity["commit"], "database_digest": database_before["sha256"],
        "tools": {"lax": VERSION, "spec_sha256": SPEC_SHA, "package_sha256": cfg["package_sha256"],
                  "executor_sha256": executor_before, "trusted_inputs": trust_before,
                  "environment": ENVIRONMENT, "mathlib": MATHLIB_SHA, "image": cfg["image"]},
        "capture": seal, "phases": phases, "coverage": report["coverage"], "verified_proofs": proofs,
        "dependency_digests": {k: verification_binding(v) for k,v in dependency_reports.items()},
        "limitations": ["kernel/toolchain and pinned mathlib are declared background trust", "no external comparator was run", "no remote publication was tested"]}
    _raise_verification_termination()
    write_json(out / "verification.json", result)
    return result


def publication_plan(request_path: Path, out: Path) -> dict[str, Any]:
    req = validate_request(request_path)
    result = {"schema_version": "lax-publication-plan.v1", "status": "proposal", "publication_enabled": False,
        "environment": req["environment"], "targets": req["targets"], "required": ["fresh exact-source independent verification", "accepted semantic review", "separate scoped publication authorization"],
        "stages": ["issue binding may change manifest", "commit/push binding and reverify", "submit exact source", "register only if separately authorized"]}
    write_json(out / "publication-plan.json", result)
    return result
