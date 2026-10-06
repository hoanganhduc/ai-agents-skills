#!/usr/bin/env python3
"""Host-owned asynchronous native Lean verification through the existing Kaggle driver.

The request and state directory are operator-controlled, outside the candidate.
Native Lake code, its toolchain and the selected Kaggle runtime remain trusted
execution inputs. This does not provide the Lax Docker isolation contract.
"""
from __future__ import annotations

import hashlib
import base64
import inspect
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import time
import uuid
from typing import Any

from state_transaction import commit_transaction, RevisionConflict
from provider_resources import validate_process_evidence
from compute_policy import execution_policy_snapshot, require_execution_policy

HERE = Path(__file__).resolve().parent
REQUEST_SCHEMA = "remote_lean_request.v1"
RECEIPT_SCHEMA = "host_remote_lean_receipt.v1"
MAX_FILE = 128 * 1024 * 1024
LIMITATIONS = [
    "native Kaggle execution; no Lax Docker isolation guarantee",
    "Lean, Lake, bootstrap, dependencies and native runtime are trusted execution inputs",
    "dependency byte evidence covers the explicit host inventory, not an inferred complete installed environment",
    "Internet setting applies to the whole kernel, including verification",
    "provider output/status version selectors do not independently echo immutable identity",
    "machine verification supports formal statements; paper correspondence requires separate review",
]
_CHECKPOINT_WRITER: Any = None  # Set only by the fixed broker controller entrypoint.


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def regular_bytes(path: Path, *, private: bool = False, limit: int = MAX_FILE) -> bytes:
    path = path.absolute()
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("formal evidence path traverses a link")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise ValueError("formal evidence must be a bounded regular file")
        if private and os.name == "posix" and (before.st_uid != os.getuid() or before.st_mode & 0o077):
            raise ValueError("host formal record must be private and operator owned")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(descriptor)
        if len(raw) > limit or any(getattr(before, k) != getattr(after, k) for k in
                ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")):
            raise ValueError("formal evidence changed while reading")
        return raw
    finally:
        os.close(descriptor)


def relative(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("invalid formal relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {".", ".."} for part in path.parts) or str(path) != value:
        raise ValueError("invalid formal relative path")
    return value


def request_snapshot(path: Path, expected_sha256: str, project: Path) -> dict[str, Any]:
    if not path.is_absolute() or path.resolve().is_relative_to(project.resolve()):
        raise ValueError("remote request must be outside the candidate project")
    raw = regular_bytes(path, private=True, limit=1024 * 1024)
    if digest(raw) != expected_sha256:
        raise ValueError("pinned remote formal request changed")
    request = json.loads(raw)
    required = {"schema_version", "project_root", "state_root", "compute_config", "compute_config_sha256", "owner",
        "source_files", "protected_files", "bootstrap_script", "bootstrap_sha256", "gate_sha256",
        "targets", "lean_executable", "lake_executable", "lean_version", "lake_version",
        "dependency_files", "enable_internet", "require_kernel", "max_attempts", "timeout_seconds",
        "submission_authorized"}
    optional = {"checker_executable", "checker_sha256", "kernel_modules"}
    if not isinstance(request, dict) or set(request) - required - optional or required - set(request):
        raise ValueError("invalid remote formal request fields")
    if request["schema_version"] != REQUEST_SCHEMA or Path(request["project_root"]).resolve() != project.resolve():
        raise ValueError("remote request project/schema mismatch")
    for name in ("enable_internet", "require_kernel", "submission_authorized"):
        if type(request[name]) is not bool:
            raise ValueError(f"{name} must be boolean")
    for name, upper in (("max_attempts", 20), ("timeout_seconds", 3600)):
        if type(request[name]) is not int or not 1 <= request[name] <= upper:
            raise ValueError(f"invalid remote formal {name}")
    for name in ("state_root", "compute_config", "bootstrap_script"):
        value = Path(request[name])
        if not value.is_absolute() or value.resolve().is_relative_to(project.resolve()):
            raise ValueError(f"host {name} must be outside candidate")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,49}", request["owner"]):
        raise ValueError("invalid Kaggle owner")
    for name in ("bootstrap_sha256", "gate_sha256", "compute_config_sha256"):
        if not isinstance(request[name], str) or not re.fullmatch(r"[a-f0-9]{64}", request[name]):
            raise ValueError(f"invalid {name}")
    files = request["source_files"]
    if not isinstance(files, list) or not files or len(files) > 240 or len(set(files)) != len(files):
        raise ValueError("invalid bounded source allowlist")
    for name in files:
        relative(name)
        if name.startswith((".lake/", ".git/")):
            raise ValueError("generated or Git state cannot be uploaded as source")
    if not {"lean-toolchain", "lake-manifest.json"} <= set(files) or not {"lakefile.lean", "lakefile.toml"}.intersection(files):
        raise ValueError("remote Lean requires a pinned toolchain, dependency lock and Lake file")
    for name in ("protected_files", "dependency_files"):
        mapping = request[name]
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError(f"{name} requires pinned content hashes")
        for key, value in mapping.items():
            relative(key)
            if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ValueError(f"invalid {name} digest")
    if not {"lean-toolchain", "lake-manifest.json"} <= set(request["protected_files"]):
        raise ValueError("toolchain and dependency lock must be protected")
    for name in ("lean_executable", "lake_executable"):
        if relative(request[name]) not in request["dependency_files"]:
            raise ValueError("Lean/Lake executables require actual pinned byte hashes")
    if not all(isinstance(request[name], str) and request[name] for name in ("lean_version", "lake_version")):
        raise ValueError("exact Lean/Lake version outputs are required")
    targets = request["targets"]
    if not isinstance(targets, list) or not targets or len(targets) > 500 or len(set(targets)) != len(targets) or any(not isinstance(t, str) or not re.fullmatch(r"[A-Za-z_][\w'.]*", t) for t in targets):
        raise ValueError("invalid bounded Lean target set")
    if request["require_kernel"]:
        checker = relative(request.get("checker_executable"))
        if request.get("checker_sha256") != request["dependency_files"].get(checker):
            raise ValueError("kernel checker bytes must be pinned")
        if not isinstance(request.get("kernel_modules"), list) or not request["kernel_modules"]:
            raise ValueError("explicit kernel replay modules are required")
    gate = HERE.parent / "lean-strict-verification-gate/lean_strict_verification_gate.py"
    if digest(regular_bytes(gate)) != request["gate_sha256"]:
        raise ValueError("selected host gate identity changed")
    if digest(regular_bytes(Path(request["bootstrap_script"]), private=True)) != request["bootstrap_sha256"]:
        raise ValueError("bootstrap script identity changed")
    if digest(regular_bytes(Path(request["compute_config"]))) != request["compute_config_sha256"]:
        raise ValueError("host compute policy identity changed")
    return request


def source_snapshot(project: Path, request: dict[str, Any], *, run_dir: Path | None = None) -> dict[str, bytes]:
    files = {name: regular_bytes(project / name, limit=16 * 1024 * 1024) for name in request["source_files"]}
    if sum(map(len, files.values())) > 15 * 1024 * 1024:
        raise ValueError("formal source exceeds the existing upload budget")
    for name, expected in request["protected_files"].items():
        if name not in files or digest(files[name]) != expected:
            raise ValueError("protected Lean configuration changed")
    for path in project.rglob("*.lean"):
        rel = path.relative_to(project)
        if (run_dir is not None and run_dir.resolve() != project.resolve()
                and run_dir.resolve().is_relative_to(project.resolve())
                and path.resolve().is_relative_to(run_dir.resolve())):
            continue
        if not any(part in {".lake", ".git"} for part in rel.parts) and rel.as_posix() not in files:
            raise ValueError("Lean source is outside the approved upload allowlist")
    return files


def kernel_main():
    """Packaged adapter; invokes the existing gate, never a second Lean engine."""
    import base64
    import hashlib
    import importlib.util
    import json
    import os
    from pathlib import Path
    import sys
    bundle = Path.cwd()
    out = Path(os.environ["OUT"])
    manifest_raw = (bundle / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    plan = manifest["formal_request"]
    spec = importlib.util.spec_from_file_location("pinned_lean_gate", bundle / "gate.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    result = {"schema_version": "remote_lean_execution.v1", "status": "incomplete",
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(), "request_sha256": plan["request_sha256"],
        "input_digest": plan["input_digest"], "gate_sha256": hashlib.sha256((bundle / "gate.py").read_bytes()).hexdigest(),
        "dependency_files": {}, "source_files": {}, "phases": {}, "correspondence": "not_checked"}
    def write(name, value):
        (out / name).write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    def execute(phase, command, cwd):
        try:
            completed = gate.run_bounded_command(command, timeout=plan["timeout_seconds"], cwd=cwd,
                max_output_bytes=64 * 1024 * 1024, capture_evidence=True)
            evidence = completed.process_evidence
            report = json.loads(completed.stdout) if phase in {"scan", "build", "axiom", "kernel"} else None
            record = {"status": "passed" if completed.returncode == 0 else "failed",
                      "process_evidence": evidence, "report": report}
        except Exception as exc:
            record = {"status": "failed", "error_type": type(exc).__name__,
                      "process_evidence": getattr(exc, "process_evidence", None), "report": None}
        write(phase + ".json", record)
        result["phases"][phase] = record["status"]
        if record["status"] != "passed":
            raise RuntimeError(phase + " failed")
        return record
    try:
        os.environ["AAS_FORMAL_PROJECT"] = str(bundle / "project")
        execute("setup", ["bash", "bootstrap.sh"], bundle)
        for name, expected in plan["dependency_files"].items():
            actual = hashlib.sha256((bundle / name).read_bytes()).hexdigest()
            result["dependency_files"][name] = actual
            if actual != expected:
                raise RuntimeError("downloaded dependency identity mismatch")
        os.environ["AAS_LEAN"] = str(bundle / plan["lean_executable"])
        os.environ["AAS_LAKE"] = str(bundle / plan["lake_executable"])
        for name in ("lean", "lake"):
            record = execute(name + "_version", [os.environ["AAS_" + name.upper()], "--version"], bundle)
            version = base64.b64decode(record["process_evidence"]["stdout"]["base64"]).decode().strip()
            result[name + "_version"] = version
            if version != plan[name + "_version"]:
                raise RuntimeError("toolchain version mismatch")
        if plan["require_kernel"]:
            os.environ["AAS_LEAN4CHECKER"] = str(bundle / plan["checker_executable"])
        project = bundle / "project"
        for name, expected in plan["source_files"].items():
            if hashlib.sha256((project / name).read_bytes()).hexdigest() != expected:
                raise RuntimeError("source changed during setup")
        base = [sys.executable, str(bundle / "gate.py")]
        execute("scan", base + ["scan", "--input", str(project), "--artifact-stage", "final_candidate"], project)
        execute("build", base + ["verify", "--input", str(project), "--strict", "--timeout", str(plan["timeout_seconds"])], project)
        arguments = [item for target in plan["targets"] for item in ("--declaration", target)]
        execute("axiom", base + ["axiom-audit", "--input", str(project), "--strict", "--timeout", str(plan["timeout_seconds"]), *arguments], project)
        if plan["require_kernel"]:
            arguments = [item for module in plan["kernel_modules"] for item in ("--module", module)]
            execute("kernel", base + ["kernel-check", "--input", str(project), "--strict", "--timeout", str(plan["timeout_seconds"]), *arguments], project)
        else:
            result["phases"]["kernel"] = "not_requested"
        for name, expected in plan["source_files"].items():
            actual = hashlib.sha256((project / name).read_bytes()).hexdigest()
            result["source_files"][name] = actual
            if actual != expected:
                raise RuntimeError("source changed during verification")
        for name, expected in plan["dependency_files"].items():
            actual = hashlib.sha256((bundle / name).read_bytes()).hexdigest()
            if actual != expected:
                raise RuntimeError("dependency changed during verification")
        if hashlib.sha256((bundle / "gate.py").read_bytes()).hexdigest() != result["gate_sha256"]:
            raise RuntimeError("gate changed during verification")
        result["status"] = "completed"
    except Exception as exc:
        result["error_type"] = type(exc).__name__
    finally:
        # Host validation, not these compatibility checkpoint fields, decides proof support.
        for phase in ("setup", "lean_version", "lake_version", "scan", "build", "axiom", "kernel"):
            if not (out / (phase + ".json")).exists():
                write(phase + ".json", {"status": "not_run"})
        write("result.json", result)
        write("unit-0000.json", {"status": result["status"], "unit": 0, "manifest_sha256": result["manifest_sha256"]})


def prepare_bundle(destination: Path, request: dict[str, Any], request_sha: str,
                   files: dict[str, bytes], attempt_id: str) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    file_hashes = {name: digest(raw) for name, raw in files.items()}
    plan = {name: request[name] for name in ("targets", "dependency_files", "lean_executable", "lake_executable",
        "lean_version", "lake_version", "require_kernel", "timeout_seconds")}
    for name in ("checker_executable", "checker_sha256", "kernel_modules"):
        if name in request:
            plan[name] = request[name]
    plan.update(request_sha256=request_sha, source_files=file_hashes, input_digest=digest(canonical(file_hashes)))
    uploads = {"project/" + name: raw for name, raw in files.items()}
    uploads["bootstrap.sh"] = regular_bytes(Path(request["bootstrap_script"]), private=True)
    uploads["gate.py"] = regular_bytes(HERE.parent / "lean-strict-verification-gate/lean_strict_verification_gate.py")
    if digest(uploads["bootstrap.sh"]) != request["bootstrap_sha256"] or digest(uploads["gate.py"]) != request["gate_sha256"]:
        raise ValueError("host bootstrap or gate changed during packaging")
    uploads["runner.py"] = (inspect.getsource(kernel_main) + "\nkernel_main()\n").encode()
    uploads["run.sh"] = b"#!/bin/bash\nset -euo pipefail\nexec python3 runner.py\n"
    manifest = {"job_id": "lean-" + attempt_id, "gpu": False, "total_units": 1, "cores": 4,
        "memory_mb": 32768, "core_hours": request["timeout_seconds"] * 7 * 4 / 3600,
        "enable_internet": request["enable_internet"], "formal_request": plan,
        "upload_files": ["manifest.json", *sorted(uploads)],
        "output_files": ["out/" + name + ".json" for name in ("setup", "lean_version", "lake_version", "scan", "build", "axiom", "kernel")]}
    uploads["manifest.json"] = canonical(manifest)
    for name, raw in uploads.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            if regular_bytes(target) != raw:
                raise ValueError("interrupted formal bundle bytes changed")
            continue
        with target.open("xb") as stream:
            stream.write(raw)
        target.chmod(0o600)
    return manifest


def kaggle_modules():
    runtime = HERE.parent.parent
    workspace = runtime if (runtime / "research_compute").is_dir() else runtime / "workspace"
    for path in (workspace, HERE.parent / "kaggle-research-compute"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    import kaggle_driver
    from research_compute.config import load_config
    return kaggle_driver, load_config


def _process_pass(evidence: Any) -> bool:
    return (not validate_process_evidence(evidence, max_output_bytes=64 * 1024 * 1024) and evidence["return_code"] == 0
        and evidence["capture_complete"] is True and evidence["timed_out"] is False
        and evidence["oversized"] is False and evidence["capture_error"] is None
        and evidence["cleanup_error"] is None)


def validate_download(destination: Path, fetch: dict[str, Any], request: dict[str, Any],
                      manifest: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    provenance = fetch.get("host_provenance") or {}
    if provenance.get("accepted_identity") != identity or provenance.get("download_version_number") != identity.get("version_number"):
        raise ValueError("remote formal output version is not host-bound")
    if (fetch.get("pagination") or {}).get("complete") is not True:
        raise ValueError("remote formal output listing is incomplete")
    entries = fetch.get("output_manifest")
    if not isinstance(entries, list) or not entries:
        raise ValueError("missing output hash manifest")
    outputs = {}
    for entry in entries:
        name = relative(entry.get("path"))
        raw = regular_bytes(destination / name)
        if entry.get("complete") is not True or entry.get("bytes") != len(raw) or entry.get("sha256") != digest(raw):
            raise ValueError("downloaded evidence digest mismatch")
        if name in outputs:
            raise ValueError("duplicate output evidence")
        outputs[name] = json.loads(raw) if name.endswith(".json") else None
    result = outputs.get("out/result.json") or {}
    plan = manifest["formal_request"]
    if (result.get("schema_version") != "remote_lean_execution.v1" or result.get("status") != "completed"
            or "error_type" in result or result.get("correspondence") != "not_checked"):
        raise ValueError("remote execution did not complete all final integrity checks")
    for name in ("request_sha256", "input_digest", "source_files", "dependency_files", "lean_version", "lake_version"):
        if result.get(name) != plan[name]:
            raise ValueError(f"remote formal {name} mismatch")
    if result.get("gate_sha256") != request["gate_sha256"] or result.get("manifest_sha256") != digest(canonical(manifest)):
        raise ValueError("remote gate or manifest binding mismatch")
    reports = {}
    required = ["setup", "lean_version", "lake_version", "scan", "build", "axiom"]
    if request["require_kernel"]:
        required.append("kernel")
    phases = result.get("phases")
    if not isinstance(phases, dict) or any(phases.get(phase) != "passed" for phase in required):
        raise ValueError("remote execution has incomplete phase dispositions")
    if not request["require_kernel"] and phases.get("kernel") != "not_requested":
        raise ValueError("remote kernel phase disposition disagrees with host policy")
    for phase in required:
        record = outputs.get("out/" + phase + ".json") or {}
        if record.get("status") != "passed" or not _process_pass(record.get("process_evidence")):
            raise ValueError(f"remote {phase} process evidence is incomplete")
        reports[phase] = record.get("report")
        stdout = base64.b64decode(record["process_evidence"]["stdout"]["base64"], validate=True)
        if phase in {"scan", "build", "axiom", "kernel"}:
            if json.loads(stdout) != reports[phase]:
                raise ValueError("gate report differs from captured process bytes")
        elif phase in {"lean_version", "lake_version"} and stdout.decode().strip() != plan[phase]:
            raise ValueError("tool version differs from captured process bytes")
    scan, build, axiom = (reports[name] for name in ("scan", "build", "axiom"))
    for report in (scan, build, axiom):
        if not isinstance(report, dict) or report.get("schema_version") != "lean-strict-verification-gate.v1" or report.get("ok") is not True:
            raise ValueError("native gate refused formal evidence")
    if scan.get("findings") or not (scan.get("coverage") or {}).get("files_scanned"):
        raise ValueError("remote scan has findings or no source coverage")
    scanned = {row.get("file"): row.get("sha256") for row in scan["coverage"].get("files", [])}
    lean_sources = {name: value for name, value in plan["source_files"].items() if name.endswith(".lean")}
    if scanned != lean_sources:
        raise ValueError("remote scan is not bound to the complete Lean source set")
    if (build.get("lean_check_status") != "typechecked" or not _process_pass(build.get("process_evidence"))
            or not build.get("typecheck_modules_built") or build.get("typecheck_modules_unbuilt")
            or build.get("typecheck_modules_stale")):
        raise ValueError("remote Lake build was not completely checked")
    if axiom.get("axiom_audit_status") != "audited" or axiom.get("unsanctioned_axioms") or axiom.get("declarations_unparsed") or not _process_pass(axiom.get("process_evidence")):
        raise ValueError("remote axiom coverage is incomplete")
    declarations = axiom.get("declarations") or []
    if {row.get("declaration") for row in declarations} != set(request["targets"]) or any(row.get("status") != "sanctioned" for row in declarations):
        raise ValueError("remote axiom target set or trust base mismatch")
    if request["require_kernel"]:
        report = reports["kernel"]
        if not isinstance(report, dict) or report.get("ok") is not True or report.get("kernel_check_status") != "kernel_checked":
            raise ValueError("remote kernel replay unavailable")
        rows = report.get("modules") or []
        if {row.get("module") for row in rows} != set(request["kernel_modules"]) or any(row.get("status") != "kernel_checked" for row in rows):
            raise ValueError("remote kernel module coverage mismatch")
        evidence = report.get("process_evidence") or []
        if {row.get("module") for row in evidence} != set(request["kernel_modules"]) or any(not _process_pass(row.get("evidence")) for row in evidence):
            raise ValueError("remote kernel process evidence is incomplete")
    return {"status": "passed", "reports": reports, "phases": result.get("phases"), "correspondence": "not_checked"}


def _read_state(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        raw = regular_bytes(path, private=True, limit=4 * 1024 * 1024)
    except FileNotFoundError:
        return {"schema_version": "remote_lean_state.v1", "attempts": {}}, None
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schema_version") != "remote_lean_state.v1" or not isinstance(value.get("attempts"), dict):
        raise ValueError("invalid remote formal state")
    return value, digest(raw)


def _save(root: Path, state: dict[str, Any], previous: str | None) -> None:
    if _CHECKPOINT_WRITER is not None:
        _CHECKPOINT_WRITER(root / "state.json", state, kind="state", previous=previous)
        return
    commit_transaction(root, json_files={"state.json": state},
        expected_absent=["state.json"] if previous is None else [],
        expected_hashes={"state.json": previous} if previous is not None else {})


def _checkpoint_intent(path: Path, payload: dict[str, Any], *, create: bool) -> None:
    previous = None if create else digest(regular_bytes(path, private=True))
    _CHECKPOINT_WRITER(path, payload, kind="intent", previous=previous)


def advance(run_dir: Path, project: Path, pin: dict[str, Any], purpose_key: str,
            *, backend: Any = None, config: Any = None, broker_child: bool = False,
            expected_state_sha256: str | None = None,
            expected_compute_policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """One bounded lifecycle step; only first dispatch can submit, polls never do."""
    from arl_credential_client import broker_active, request as broker_request
    request_path = Path(str(pin.get("remote_request") or ""))
    request_sha = str(pin.get("remote_request_sha256") or "")
    request = request_snapshot(request_path, request_sha, project)
    compute_binding = execution_policy_snapshot(run_dir, "kaggle")
    if expected_compute_policy is not None and compute_binding != expected_compute_policy:
        raise ValueError("compute policy changed after host registration")
    if not re.fullmatch(r"[a-f0-9]{64}", purpose_key):
        raise ValueError("remote verification requires an exact host purpose digest")
    if backend is None and broker_active() and not broker_child:
        if any((run_dir / name).exists() for name in ("STOP_REQUESTED", "PAUSE", "BLOCKED")):
            return {"status": "incomplete", "execution_backend": "kaggle-cpu",
                    "detail": "operator_control_active", "admission_suspended": True}
        from panel_parent import _remaining_panel_wall_budget
        remaining = _remaining_panel_wall_budget(run_dir)
        if remaining == 0:
            return {"status": "verification_pending", "execution_backend": "kaggle-cpu",
                    "detail": "host_wall_budget_exhausted"}
        controller_timeout = min(900, remaining) if remaining is not None else 900
        registration = broker_request({"operation": "formal_register", "run_dir": str(run_dir),
            "project": str(project), "pin": pin}, timeout_s=min(10, controller_timeout))
        result = broker_request({"operation": "formal_advance", "registration_id": registration["registration_id"],
            "purpose_key": purpose_key}, timeout_s=controller_timeout)["result"]
        if result.get("status") == "passed":
            result["reports"] = validate_host_receipt(Path(result["receipt_path"]), result["receipt_sha256"], project=project)["reports"]
        return result
    if backend is None and not broker_child:
        return {"status": "incomplete", "execution_backend": "kaggle-cpu",
                "detail": "host_remote_authority_unavailable", "limitations": LIMITATIONS}
    driver, loader = kaggle_modules() if backend is None else (backend, None)
    config = config if config is not None else loader(Path(request["compute_config"]))
    files = source_snapshot(project, request, run_dir=run_dir)
    input_digest = digest(canonical({name: digest(raw) for name, raw in files.items()}))
    key = digest(canonical([request_sha, purpose_key, input_digest]))
    state_root = Path(request["state_root"])
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if state_root.is_symlink() or os.name == "posix" and state_root.stat().st_mode & 0o077:
        raise ValueError("remote state root must be private")
    state, old = _read_state(state_root / "state.json")
    if broker_child and old != expected_state_sha256:
        raise ValueError("remote state differs from broker-authorized preimage")
    attempt = state["attempts"].get(key)
    stopped = any((run_dir / name).exists() for name in ("STOP_REQUESTED", "PAUSE", "BLOCKED"))
    base = {"status": "verification_pending", "execution_backend": "kaggle-cpu", "input_digest": input_digest,
        "request_sha256": request_sha, "purpose_key": purpose_key, "limitations": LIMITATIONS}
    if attempt is None:
        pending = [row for row in state["attempts"].values()
                   if row.get("purpose_key") == purpose_key
                   and row.get("state") in {"preparing", "submitting", "submitted"}]
        if pending:
            return {**base, "detail": "input_changed_pending_reconciliation",
                    "pending_attempts": [{name: row.get(name) for name in
                        ("attempt_id", "input_digest", "submission_intent", "accepted_identity")} for row in pending]}
        if stopped or request["submission_authorized"] is not True:
            return {**base, "status": "incomplete", "detail": "submission_not_authorized_or_stopped"}
        if len(state["attempts"]) >= request["max_attempts"]:
            return {**base, "status": "incomplete", "detail": "remote_verification_budget_exhausted"}
        from panel_parent import _remaining_panel_wall_budget
        wall_remaining = _remaining_panel_wall_budget(run_dir)
        if wall_remaining is not None and wall_remaining < request["timeout_seconds"] * 7:
            return {**base, "status": "incomplete", "detail": "insufficient_wall_budget_for_verification"}
        attempt_id = uuid.uuid4().hex
        attempt = {"attempt_id": attempt_id, "purpose_key": purpose_key, "input_digest": input_digest,
            "request_sha256": request_sha, "state": "preparing", "created_at": time.time(),
            "compute_policy_binding": compute_binding}
        state["attempts"][key] = attempt
        _save(state_root, state, old)
    if attempt["state"] == "preparing":
        if stopped:
            return {**base, "status": "incomplete", "detail": "stopped_before_submission"}
        attempt_id = attempt["attempt_id"]
        attempt_root = state_root / "attempts" / attempt_id
        manifest = prepare_bundle(attempt_root / "bundle", request, request_sha, files, attempt_id)
        bundle_digest = driver.bundle_sha256(attempt_root / "bundle")
        state, old = _read_state(state_root / "state.json")
        attempt = state["attempts"][key]
        attempt.update(state="submitting", bundle_sha256=bundle_digest,
            submission_intent=str(driver._submission_intent_path(state_root, job_id=manifest["job_id"], round_idx=0, chunk_idx=0)))
        _save(state_root, state, old)
        if any((run_dir / name).exists() for name in ("STOP_REQUESTED", "PAUSE", "BLOCKED")):
            return {**base, "status": "incomplete", "detail": "stopped_before_submission"}
        # Recheck host authority immediately before the outward effect.
        request_snapshot(request_path, request_sha, project)
        require_execution_policy(run_dir, "kaggle", expected=attempt.get("compute_policy_binding", compute_binding))
        try:
            pushed = driver.push(job_dir=attempt_root / "bundle", config=config, state_root=state_root,
                work_root=attempt_root / "kernel", expected_bundle_sha256=bundle_digest,
                expected_owner=request["owner"], confirm=True,
                **({"checkpoint_writer": _checkpoint_intent} if _CHECKPOINT_WRITER is not None else {}))
        except Exception as exc:
            state, old = _read_state(state_root / "state.json")
            state["attempts"][key]["original_submission_failure"] = {"error_type": type(exc).__name__,
                "evidence": getattr(exc, "evidence", None)}
            _save(state_root, state, old)
            return {**base, "detail": "submission_acceptance_unknown", "attempt_id": attempt_id}
        state, old = _read_state(state_root / "state.json")
        attempt = state["attempts"][key]
        attempt.update(state="submitted", submission_intent=pushed["submission_intent"], accepted_identity=pushed["accepted_identity"])
        _save(state_root, state, old)
        return {**base, "attempt_id": attempt_id}
    attempt_root = state_root / "attempts" / attempt["attempt_id"]
    if attempt["state"] in {"preparing", "submitting"}:
        if attempt.get("submission_intent") and Path(attempt["submission_intent"]).is_file():
            recovered = driver.recover_submission(attempt["submission_intent"],
                **({"checkpoint_writer": _checkpoint_intent} if _CHECKPOINT_WRITER is not None else {}))
            if recovered.get("accepted_identity"):
                attempt.update(state="submitted", accepted_identity=recovered["accepted_identity"])
                _save(state_root, state, old)
                return base
        return {**base, "detail": "acceptance_unknown_never_repush"}
    if attempt["state"] == "passed":
        receipt_path = attempt_root / "receipt.json"
        receipt = inspect_receipt_content(receipt_path, attempt["receipt_sha256"], project=project)
        return {**base, **receipt, "receipt_path": str(receipt_path), "receipt_sha256": attempt["receipt_sha256"],
                "admission_suspended": any((run_dir / name).exists() for name in ("STOP_REQUESTED", "PAUSE", "BLOCKED"))}
    if attempt["state"] == "incomplete":
        return {**base, "status": "incomplete", "detail": attempt.get("detail", "verification_failed")}
    identity = driver.load_submission_identity(attempt["submission_intent"])
    if identity != attempt.get("accepted_identity") or identity.get("bundle_sha256") != attempt["bundle_sha256"]:
        raise ValueError("remote accepted execution identity changed")
    observed = driver.status(kernel=identity["kernel"], config=config, submission_intent=attempt["submission_intent"])
    if observed["status"] not in {"complete", "error", "cancelacknowledged"}:
        return {**base, "provider_status": observed["status"]}
    destination = attempt_root / ("fetch-" + uuid.uuid4().hex)
    fetched = driver.fetch(kernel=identity["kernel"], config=config, job_dir=attempt_root / "bundle", dest=destination,
        submission_intent=attempt["submission_intent"], validate_checkpoints=False)
    manifest = json.loads(regular_bytes(attempt_root / "bundle/manifest.json"))
    try:
        checked = validate_download(destination, fetched, request, manifest, identity)
        if observed["status"] != "complete":
            raise ValueError("remote kernel did not complete successfully")
    except ValueError as exc:
        state, old = _read_state(state_root / "state.json")
        state["attempts"][key].update(state="incomplete", detail=str(exc), fetch=fetched)
        _save(state_root, state, old)
        return {**base, "status": "incomplete", "detail": str(exc)}
    receipt = {**base, "schema_version": RECEIPT_SCHEMA, "status": "passed", "attempt_id": attempt["attempt_id"],
        "request_path": str(request_path), "run_dir": str(run_dir.resolve()), "project_root": str(project.resolve()), "targets": request["targets"],
        "accepted_identity": identity, "bundle_sha256": attempt["bundle_sha256"],
        "bundle_path": str(attempt_root / "bundle"), "submission_intent": attempt["submission_intent"],
        "fetch_destination": str(destination), "fetch": fetched, "phases": checked["phases"], "correspondence": "not_checked"}
    raw = canonical(receipt)
    state, old = _read_state(state_root / "state.json")
    state["attempts"][key].update(state="passed", receipt_sha256=digest(raw))
    if _CHECKPOINT_WRITER is not None:
        _CHECKPOINT_WRITER(attempt_root / "receipt.json", receipt, kind="receipt", previous=None)
        _save(state_root, state, old)
    else:
        commit_transaction(state_root, json_files={"state.json": state}, binary_files={str((attempt_root / "receipt.json").relative_to(state_root)): raw},
            expected_hashes={"state.json": old})
    return {**receipt, "reports": checked["reports"], "receipt_path": str(attempt_root / "receipt.json"),
            "receipt_sha256": digest(raw),
            "admission_suspended": any((run_dir / name).exists() for name in ("STOP_REQUESTED", "PAUSE", "BLOCKED"))}


def validate_host_receipt(path: Path, expected_sha256: str, *, project: Path,
                          targets: list[str] | None = None, authority_verifier: Any = None) -> dict[str, Any]:
    """Require an independent host authority before inspecting a receipt's data.

    Artifact paths, permissions and self-consistent hashes are not authority.
    A parent may pass a verifier held outside candidate data; otherwise only
    the master credential-broker capability can authenticate admission.
    """
    if authority_verifier is None:
        from arl_credential_client import broker_active, request as broker_request
        if not broker_active():
            raise ValueError("host remote receipt authority is unavailable")
        reply = broker_request({"operation": "formal_validate", "receipt_path": str(path),
            "receipt_sha256": expected_sha256, "project": str(project)}, timeout_s=30)
        if reply.get("authenticated") is not True:
            raise ValueError("remote receipt lacks host authentication")
    elif authority_verifier(path, expected_sha256, project) is not True:
        raise ValueError("remote receipt lacks host authentication")
    return inspect_receipt_content(path, expected_sha256, project=project, targets=targets)


def inspect_receipt_content(path: Path, expected_sha256: str, *, project: Path,
                            targets: list[str] | None = None) -> dict[str, Any]:
    """Validate bound bytes only; caller must separately authenticate admission."""
    if path.resolve().is_relative_to(project.resolve()):
        raise ValueError("remote host receipt cannot reside inside the candidate")
    raw = regular_bytes(path, private=True)
    if digest(raw) != expected_sha256:
        raise ValueError("remote host receipt digest mismatch")
    receipt = json.loads(raw)
    if receipt.get("schema_version") != RECEIPT_SCHEMA or receipt.get("status") != "passed" or receipt.get("execution_backend") != "kaggle-cpu":
        raise ValueError("invalid remote host receipt")
    request = request_snapshot(Path(receipt["request_path"]), receipt["request_sha256"], project)
    if receipt.get("targets") != request["targets"] or receipt.get("project_root") != str(project.resolve()):
        raise ValueError("remote receipt scope differs from the pinned host request")
    expected_path = Path(request["state_root"]) / "attempts" / receipt["attempt_id"] / "receipt.json"
    if path.absolute() != expected_path.absolute():
        raise ValueError("receipt is outside its registered host attempt")
    state, _ = _read_state(Path(request["state_root"]) / "state.json")
    key = digest(canonical([receipt["request_sha256"], receipt["purpose_key"], receipt["input_digest"]]))
    attempt = state["attempts"].get(key) or {}
    if attempt.get("state") != "passed" or attempt.get("attempt_id") != receipt["attempt_id"] or attempt.get("receipt_sha256") != expected_sha256:
        raise ValueError("receipt does not match the current host attempt admission")
    files = source_snapshot(project, request, run_dir=Path(receipt["run_dir"]))
    current = digest(canonical({name: digest(value) for name, value in files.items()}))
    if current != receipt["input_digest"] or targets is not None and not set(targets) <= set(request["targets"]):
        raise ValueError("remote receipt does not cover current source/targets")
    driver, _ = kaggle_modules()
    identity = driver.load_submission_identity(receipt["submission_intent"])
    if identity != receipt["accepted_identity"] or identity.get("bundle_sha256") != receipt["bundle_sha256"]:
        raise ValueError("remote receipt execution identity mismatch")
    bundle = Path(receipt["bundle_path"])
    if driver.bundle_sha256(bundle) != receipt["bundle_sha256"]:
        raise ValueError("remote receipt approved bundle changed")
    manifest = json.loads(regular_bytes(bundle / "manifest.json"))
    checked = validate_download(Path(receipt["fetch_destination"]), receipt["fetch"], request, manifest, identity)
    return {**receipt, "reports": checked["reports"]}


if __name__ == "__main__":
    # Invoked only by the credential broker with its private registration file.
    if len(sys.argv) != 3:
        raise SystemExit("remote_formal requires broker registration and purpose digest")
    registration = json.loads(regular_bytes(Path(sys.argv[1]), private=True, limit=1024 * 1024))
    from arl_credential_client import request as checkpoint_request
    def checkpoint(path, payload, *, kind, previous):
        checkpoint_request({"operation": "formal_checkpoint", "path": str(path), "payload": payload,
            "kind": kind, "expected_sha256": previous}, timeout_s=30)
    _CHECKPOINT_WRITER = checkpoint
    result = advance(Path(registration["run_dir"]), Path(registration["project"]), registration["pin"], sys.argv[2], broker_child=True,
        expected_state_sha256=registration["expected_state_sha256"],
        expected_compute_policy=registration["compute_policy_binding"])
    # Large verifier bytes remain in bounded host evidence files.
    result.pop("reports", None)
    bound = request_snapshot(Path(registration["pin"]["remote_request"]), registration["pin"]["remote_request_sha256"], Path(registration["project"]))
    state_path = Path(bound["state_root"]) / "state.json"
    result["controller_state_sha256"] = digest(regular_bytes(state_path, private=True)) if state_path.exists() else None
    print(json.dumps(result))
