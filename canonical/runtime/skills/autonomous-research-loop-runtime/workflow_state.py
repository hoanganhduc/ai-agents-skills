"""Parent bookkeeping for source bindings, task claims and native result admission.

This module starts no processes. A claim records controller ownership, not proof
of process isolation, worker liveness, mathematical validity, or permission to
publish. Host contracts and observations must come from the controlling host;
neither is authenticated by accepting a worker-authored JSON file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from state_transaction import (
    RevisionConflict, TransactionError, _read_bytes_nofollow,
    commit_transaction, recover_transactions,
)

STATE_PATH = ".workflow/state.json"
EVENT_PATH = ".workflow/events.jsonl"
CONTROLS = ("STOP_REQUESTED", "PAUSE", "BLOCKED")


class WorkflowError(ValueError):
    pass


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def _identity(value: Any) -> str:
    # These identifiers are JSON values, never filesystem components. Preserve
    # actual native task/session identifiers, including qualified names.
    if not isinstance(value, str) or not 1 <= len(value) <= 160 or value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise WorkflowError("invalid controller identity")
    return value


def _source_bytes(root: Path, relative: Any) -> bytes:
    if not isinstance(relative, str):
        raise WorkflowError("source path must be a relative local path")
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(p in {".", ".."} for p in path.parts) or "\\" in relative or ":" in relative:
        raise WorkflowError("source path escapes the approved local root")
    try:
        return _read_bytes_nofollow(root.joinpath(*path.parts))
    except (OSError, TransactionError) as exc:
        raise WorkflowError("source is unavailable or not a contained regular file") from exc


def build_source_catalog(source_root: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Capture exact local bytes and unambiguous explicit aliases; no retrieval."""
    sources = []
    aliases: dict[str, str] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) - {"source_id", "path", "kind", "aliases"}:
            raise WorkflowError("invalid source catalogue entry")
        source_id = _identity(record.get("source_id"))
        if record.get("kind") not in {"text", "tex", "pdf", "lean"}:
            raise WorkflowError("source kind must be text, tex, pdf or lean")
        names = record.get("aliases", [])
        if not isinstance(names, list):
            raise WorkflowError("source aliases must be an explicit list")
        for name in [source_id, *names]:
            name = _identity(name)
            if name in aliases:
                raise WorkflowError("ambiguous source identity or alias")
            aliases[name] = source_id
        raw = _source_bytes(Path(source_root), record.get("path"))
        if record["kind"] == "pdf" and not raw.startswith(b"%PDF-"):
            raise WorkflowError("PDF source header is missing")
        sources.append({"source_id": source_id, "path": record["path"], "kind": record["kind"],
                        "aliases": list(names), "sha256": hashlib.sha256(raw).hexdigest()})
    sources.sort(key=lambda item: item["source_id"])
    return {"schema_version": "workflow_source_catalog.v1", "sources": sources,
            "catalog_sha256": _digest(sources)}


def resolve_source_ref(catalog: Mapping[str, Any], ref: Any, source_root: Path) -> dict[str, Any]:
    """Resolve an explicit coordinate without guessing printed/physical pages."""
    if catalog.get("schema_version") != "workflow_source_catalog.v1" or catalog.get("catalog_sha256") != _digest(catalog.get("sources")):
        raise WorkflowError("source catalogue digest or schema mismatch")
    if not isinstance(ref, dict) or set(ref) != {"source_id", "locator"}:
        raise WorkflowError("source reference requires source_id and typed locator")
    matches = [s for s in catalog["sources"] if ref["source_id"] in [s["source_id"], *s["aliases"]]]
    if len(matches) != 1:
        raise WorkflowError("source reference is unknown or ambiguous")
    source = matches[0]
    raw = _source_bytes(Path(source_root), source["path"])
    if hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise WorkflowError("source bytes changed after catalogue capture")
    locator = ref["locator"]
    if not isinstance(locator, dict):
        raise WorkflowError("source locator must carry an explicit coordinate kind")
    kind = locator.get("kind")
    coordinate_status = "checked"
    if kind == "line":
        if set(locator) != {"kind", "start", "end"} or source["kind"] == "pdf":
            raise WorkflowError("line locator does not match source kind")
        start, end = locator["start"], locator["end"]
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(raw.splitlines()):
            raise WorkflowError("line coordinates are outside captured source")
    elif kind == "tex_label":
        if set(locator) != {"kind", "label"} or source["kind"] != "tex" or not isinstance(locator.get("label"), str):
            raise WorkflowError("TeX label locator does not match source kind")
        marker = ("\\label{" + locator["label"] + "}").encode()
        if raw.count(marker) != 1:
            raise WorkflowError("TeX label is missing or ambiguous")
        # A lexical label is a coordinate only, not proof of TeX semantics.
    elif kind == "declaration":
        if set(locator) != {"kind", "name", "line"} or source["kind"] != "lean":
            raise WorkflowError("declaration locator requires an explicit Lean line")
        line = locator["line"]
        lines = raw.decode("utf-8", errors="strict").splitlines()
        if type(line) is not int or not 1 <= line <= len(lines) or not isinstance(locator.get("name"), str):
            raise WorkflowError("invalid declaration coordinate")
        if locator["name"].split(".")[-1] not in lines[line - 1]:
            raise WorkflowError("declaration name is absent at the declared line")
        coordinate_status = "host_inspection_required"
    elif kind in {"physical_page", "printed_page"}:
        if set(locator) != {"kind", "page"} or source["kind"] != "pdf":
            raise WorkflowError("page locator does not match PDF source")
        page = locator["page"]
        if kind == "physical_page" and (type(page) is not int or page < 1):
            raise WorkflowError("physical page must be a positive one-based integer")
        if kind == "printed_page" and (not isinstance(page, str) or not page.strip()):
            raise WorkflowError("printed page label must be explicit")
        coordinate_status = "host_inspection_required"
    else:
        raise WorkflowError("unsupported or missing coordinate system")
    resolved = {"source_id": source["source_id"], "source_sha256": source["sha256"], "locator": locator}
    return {**resolved, "reference_sha256": _digest(resolved), "coordinate_status": coordinate_status}


def validate_lifecycle_stream(raw: bytes | None, *, attempt_id: str, response: str) -> dict[str, Any]:
    """Validate a host adapter's raw, ordered lifecycle sidecar, not CLI prose.

    No provider dialect is inferred. A native transport adapter must emit this
    explicit sidecar from actual host observations. Reconnection diagnostics
    are tolerated only when the same attempt ends with one exact final result.
    """
    result = {"status": "unverified", "complete": False, "errors": []}
    if raw is None:
        return result
    errors = result["errors"]
    if not isinstance(raw, bytes) or len(raw) > 16_000_000 or not raw.endswith(b"\n"):
        errors.append("lifecycle stream is missing, oversized or incomplete")
        return result
    try:
        events = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
        started = final = ended = False
        chunks = []
        for index, event in enumerate(events):
            if not isinstance(event, dict) or set(event) - {"schema_version", "seq", "attempt_id", "event", "text", "code"}:
                raise WorkflowError("invalid lifecycle event")
            if event.get("schema_version") != "host_lifecycle_event.v1" or type(event.get("seq")) is not int or event["seq"] != index or event.get("attempt_id") != attempt_id:
                raise WorkflowError("lifecycle order or attempt binding mismatch")
            kind = event.get("event")
            if ended or (not started and kind != "start"):
                raise WorkflowError("lifecycle is out of order")
            if kind == "start" and not started:
                started = True
            elif kind == "delta" and not final and isinstance(event.get("text"), str):
                chunks.append(event["text"])
            elif kind == "diagnostic" and not final and event.get("code") in {"reconnecting", "reconnected"}:
                pass
            elif kind == "final" and not final and event.get("text") == response:
                if chunks and "".join(chunks) != response:
                    raise WorkflowError("streamed text disagrees with exact final")
                final = True
            elif kind == "end" and final:
                ended = True
            else:
                raise WorkflowError("duplicate, failed or unmatched lifecycle event")
        if not (started and final and ended):
            raise WorkflowError("lifecycle has no complete exact final result")
    except (ValueError, UnicodeError) as exc:
        errors.append(str(exc))
    result.update(status="verified" if not errors else "rejected", complete=not errors,
                  stream_sha256=hashlib.sha256(raw).hexdigest(), captured_bytes=len(raw))
    return result


def validate_host_contract(host_contract: Mapping[str, Any] | None, *, host_context: Mapping[str, Any],
                           response: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
    assurances = {"source_coverage": False, "finding_coverage": False, "stream_complete": False}
    result = {"status": "unverified", "errors": [], "assurances": assurances}
    if host_contract is None:
        return result
    errors = result["errors"]
    try:
        expected_keys = {"schema_version", "source_root", "source_catalog", "source_refs", "original_finding_ids", "require_stream"}
        if set(host_contract) != expected_keys or host_contract.get("schema_version") != "workflow_host_contract.v1" or type(host_contract.get("require_stream")) is not bool:
            raise WorkflowError("invalid parent workflow contract")
        root = Path(host_contract["source_root"])
        if not root.is_absolute():
            raise WorkflowError("source root must be host-selected and absolute")
        expected = host_contract["source_refs"]
        observed = host_context.get("source_refs")
        if not isinstance(expected, list) or not isinstance(observed, list):
            raise WorkflowError("source reference coverage was not observed")
        if not isinstance(host_contract["original_finding_ids"], list):
            raise WorkflowError("original finding identities must be an explicit list")
        catalogue = host_contract["source_catalog"]
        wanted = [resolve_source_ref(catalogue, ref, root) for ref in expected]
        actual = [resolve_source_ref(catalogue, ref, root) for ref in observed]
        if sorted(r["reference_sha256"] for r in wanted) != sorted(r["reference_sha256"] for r in actual):
            raise WorkflowError("source references do not match the parent scope")
        observations = host_context.get("locator_observations", {})
        for ref in actual:
            if ref["coordinate_status"] != "checked" and observations.get(ref["reference_sha256"]) != "host-inspected":
                raise WorkflowError("reference coordinate needs host inspection")
        assurances["source_coverage"] = True
        from panel_parent import validate_review_dispositions
        disposition_errors = validate_review_dispositions(receipt.get("review_dispositions"),
            primary_finding_ids=host_contract["original_finding_ids"])
        errors.extend(disposition_errors)
        assurances["finding_coverage"] = not disposition_errors
        lifecycle = validate_lifecycle_stream(host_context.get("lifecycle_stream"),
            attempt_id=str(host_context.get("attempt_id") or ""), response=response)
        result["lifecycle"] = lifecycle
        assurances["stream_complete"] = lifecycle["complete"]
        if host_contract["require_stream"] and not lifecycle["complete"]:
            errors.extend(lifecycle["errors"] or ["host lifecycle stream is unverified"])
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        errors.append(str(exc))
    result["status"] = "verified" if not errors else "rejected"
    return result


def _read_state(run_dir: Path) -> tuple[dict[str, Any], str | None]:
    try:
        raw = _read_bytes_nofollow(run_dir / STATE_PATH)
    except FileNotFoundError:
        return {}, None
    state = json.loads(raw)
    if not isinstance(state, dict) or state.get("schema_version") != "workflow_state.v1" or not isinstance(state.get("tasks"), dict):
        raise WorkflowError("invalid workflow state")
    for name, task in state["tasks"].items():
        if (not isinstance(task, dict) or task.get("task_id") != name
                or type(task.get("attempt")) is not int or task["attempt"] < 0
                or type(task.get("wave")) is not int or task["wave"] < 0
                or task.get("status") not in {"pending", "claimed", "result_admitted", "incomplete", "stopped", "paused"}
                or not isinstance(task.get("depends_on"), list)
                or any(not isinstance(dep, str) or dep not in state["tasks"] for dep in task["depends_on"])):
            raise WorkflowError("invalid task state or dependency")
    return state, hashlib.sha256(raw).hexdigest()


def _control_status(root: Path) -> str | None:
    if (root / "STOP_REQUESTED").exists() or (root / "BLOCKED").exists():
        return "stopped"
    return "paused" if (root / "PAUSE").exists() else None


def _task_contract_digest(task: Mapping[str, Any]) -> str:
    return _digest({name: task.get(name) for name in (
        "task_id", "phase", "wave", "depends_on", "input_sha256",
        "host_contract", "required_assurances",
    )})


def _commit(root: Path, state: dict[str, Any], before: str | None, event: dict[str, Any], *, admission: bool = False) -> None:
    commit_transaction(root, json_files={STATE_PATH: state}, jsonl_appends={EVENT_PATH: [event]},
        expected_hashes={STATE_PATH: before} if before else {},
        expected_absent=([STATE_PATH] if before is None else []) + ([] if admission else list(CONTROLS)))


def initialize_workflow(run_dir: Path, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    root = Path(run_dir)
    if _control_status(root):
        raise WorkflowError("operator control blocks workflow initialization")
    rows = {}
    for task in tasks:
        name = _identity(task.get("task_id"))
        _identity(task.get("phase"))
        if name in rows or type(task.get("wave")) is not int or task["wave"] < 0 or not isinstance(task.get("depends_on"), list):
            raise WorkflowError("invalid or duplicate task definition")
        rows[name] = {**task, "status": "pending", "attempt": 0}
    for name, task in rows.items():
        if len(set(task["depends_on"])) != len(task["depends_on"]):
            raise WorkflowError("duplicate role dependency")
        if any(dep not in rows or dep == name or rows[dep]["wave"] > task["wave"] for dep in task["depends_on"]):
            raise WorkflowError("unknown or later-wave role dependency")
    def visit(name, trail):
        if name in trail:
            raise WorkflowError("cyclic role dependencies")
        for dep in rows[name]["depends_on"]:
            visit(dep, trail | {name})
    for name in rows:
        visit(name, set())
    state = {"schema_version": "workflow_state.v1", "tasks": rows}
    _commit(root, state, None, {"event_id": "workflow-initialized", "task_ids": sorted(rows)})
    return state


def workflow_readiness(run_dir: Path) -> dict[str, Any]:
    """Read only: do not recover a journal, create directories or start work."""
    root = Path(run_dir)
    control = _control_status(root)
    if (root / ".goal_focus_transactions").exists():
        return {"status": control or "recovery_required", "ready_tasks": [], "process_managed": False}
    state, _ = _read_state(root)
    if not state:
        return {"status": control or "uninitialized", "ready_tasks": [], "process_managed": False}
    tasks = state["tasks"]
    ready = []
    for name, task in tasks.items():
        if task["status"] not in {"pending", "incomplete"} or task["attempt"] >= 3:
            continue
        predecessors = set(task["depends_on"]) | {n for n, t in tasks.items() if t["wave"] < task["wave"]}
        if all(tasks[n]["status"] == "result_admitted" for n in predecessors):
            ready.append(name)
    status = "ready" if ready else "settled" if all(t["status"] == "result_admitted" for t in tasks.values()) else "waiting"
    return {"status": control or status, "ready_tasks": [] if control else ready,
            "tasks": tasks, "process_managed": False, "publication_authorized": False}


def claim_task(run_dir: Path, task_id: str, *, owner_id: str, request_id: str) -> dict[str, Any]:
    root = Path(run_dir)
    _identity(owner_id); _identity(request_id)
    for _ in range(16):
        recover_transactions(root)
        if _control_status(root):
            raise WorkflowError("operator stop/pause blocks task claim")
        state, before = _read_state(root)
        task = state.get("tasks", {}).get(task_id)
        if not isinstance(task, dict):
            raise WorkflowError("unknown controller task")
        if task.get("status") == "claimed" and task.get("owner_id") == owner_id and task.get("request_id") == request_id:
            return {**task, "process_managed": False}
        if task_id not in workflow_readiness(root)["ready_tasks"]:
            raise WorkflowError("task is owned, exhausted, or behind a dependency/wave barrier")
        task.update(status="claimed", attempt=task["attempt"] + 1, owner_id=owner_id,
                    request_id=request_id, attempt_id=uuid.uuid4().hex,
                    contract_sha256=_task_contract_digest(task))
        event = {"event_id": "claim-" + task["attempt_id"], "task_id": task_id,
                 "attempt_id": task["attempt_id"], "owner_id": owner_id, "process_managed": False}
        try:
            _commit(root, state, before, event)
            return {**task, "process_managed": False}
        except RevisionConflict:
            continue
    raise WorkflowError("task claim changed concurrently")


def release_incomplete(run_dir: Path, task_id: str, *, owner_id: str, attempt_id: str) -> None:
    """Host declares an unfinished callback; does not assert process cleanup."""
    root = Path(run_dir)
    recover_transactions(root)
    state, before = _read_state(root)
    task = state.get("tasks", {}).get(task_id, {})
    if task.get("owner_id") != owner_id or task.get("attempt_id") != attempt_id or task.get("status") != "claimed":
        raise WorkflowError("obsolete or unowned task completion")
    task["status"] = _control_status(root) or "incomplete"
    _commit(root, state, before, {"event_id": "incomplete-" + attempt_id,
        "task_id": task_id, "attempt_id": attempt_id, "status": task["status"]}, admission=True)


def record_host_result(run_dir: Path, task_id: str, *, receipt: Mapping[str, Any],
                       response: str, host_context: Mapping[str, Any]) -> dict[str, Any]:
    """Admit under the current exact claim, retaining late data without resuming."""
    from panel_parent import admit_host_panel_result
    root = Path(run_dir)
    recover_transactions(root)
    state, before = _read_state(root)
    task = state.get("tasks", {}).get(task_id, {})
    if task.get("attempt_id") != host_context.get("attempt_id") or task.get("owner_id") != host_context.get("owner_id"):
        raise WorkflowError("obsolete or unowned task completion")
    if task.get("contract_sha256") != _task_contract_digest(task) or task.get("contract_sha256") != host_context.get("workflow_contract_sha256"):
        raise WorkflowError("task contract differs from the independently held host dispatch")
    digest = _digest({"receipt": receipt, "response_sha256": hashlib.sha256(response.encode()).hexdigest()})
    if task.get("result_sha256") == digest:
        return {"status": "already_recorded", "result_sha256": digest, "publication_authorized": False}
    if task.get("status") != "claimed":
        raise WorkflowError("task has no outstanding owned claim")
    if not task.get("input_sha256") or task["input_sha256"] != host_context.get("input_sha256"):
        raise WorkflowError("host input provenance is missing or changed")
    admission = admit_host_panel_result(receipt, host_context=host_context, phase=task["phase"],
        response=response, required_assurances=task.get("required_assurances", []),
        host_contract=task.get("host_contract"))
    controlled = _control_status(root)
    task["status"] = controlled or ("result_admitted" if admission["admitted"] and admission["assurance_pass"] else "incomplete")
    task["result_sha256"] = digest
    task["admission"] = admission
    _commit(root, state, before, {"event_id": "result-" + task["attempt_id"],
        "task_id": task_id, "attempt_id": task["attempt_id"], "result_sha256": digest,
        "status": task["status"], "admission": admission}, admission=True)
    return {"status": task["status"], "admission": admission, "publication_authorized": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "readiness"])
    parser.add_argument("--dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = workflow_readiness(args.dir)
    except (OSError, ValueError, TransactionError) as exc:
        result = {"status": "invalid", "errors": [str(exc)], "ready_tasks": []}
    print(json.dumps(result, sort_keys=True))
    return 1 if result["status"] in {"invalid", "recovery_required"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
