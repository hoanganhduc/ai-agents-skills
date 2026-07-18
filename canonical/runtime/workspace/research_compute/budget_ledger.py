"""File-backed reservation ledger for remote-compute budgets (GitHub Actions minutes,
Modal dollars). The ledger is the *live* gate: reservations are written BEFORE dispatch
and reconciled to actuals on completion, so concurrent submits can never collectively
exceed a budget even while an external billing API lags (see the experiment-runner plan).

Generic over the unit: GHA reserves in "minutes", Modal in "usd".
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl  # POSIX
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]


def _ledger_path(state_root: Path, backend: str) -> Path:
    return Path(state_root) / f"{backend}-reservations.jsonl"


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.with_suffix(".lock")
    handle = lock_file.open("w")
    try:
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    tmp.replace(path)


def outstanding(state_root: Path, backend: str) -> float:
    """Sum of reservations not yet reconciled (the amount already committed)."""
    path = _ledger_path(state_root, backend)
    return sum(float(r["amount"]) for r in _read(path) if r.get("state") == "reserved")


def reserved_job_ids(state_root: Path, backend: str) -> set[str]:
    """Set of job ids with an outstanding (reserved, not reconciled) reservation.

    This is the live active-jobs set the Hetzner reaper consults: a tagged server whose
    job-id label is not in this set is orphaned (its controlling session finished, crashed,
    or died) and must be deleted."""
    path = _ledger_path(state_root, backend)
    return {str(r["job_id"]) for r in _read(path)
            if r.get("state") == "reserved" and r.get("job_id") is not None}


def reserve(state_root: Path, backend: str, job_id: str, amount: float, unit: str) -> None:
    path = _ledger_path(state_root, backend)
    with _lock(path):
        rows = _read(path)
        rows.append(
            {"job_id": job_id, "amount": float(amount), "unit": unit,
             "state": "reserved", "reserved_at": time.time()}
        )
        _write(path, rows)


def reconcile(state_root: Path, backend: str, job_id: str, actual: float | None = None) -> None:
    """Mark a job's reservation reconciled (releasing the difference vs the worst case)."""
    path = _ledger_path(state_root, backend)
    with _lock(path):
        rows = _read(path)
        for row in rows:
            if row.get("job_id") == job_id and row.get("state") == "reserved":
                row["state"] = "reconciled"
                row["reconciled_at"] = time.time()
                if actual is not None:
                    row["actual"] = float(actual)
        _write(path, rows)


def check_and_reserve(
    *, state_root: Path, backend: str, job_id: str, worst_case: float,
    available: float, unit: str,
) -> dict[str, Any]:
    """Atomic gate: refuse if worst_case + outstanding would exceed `available`; else
    reserve worst_case. Returns {"ok": bool, "reserved": float, "outstanding": float,
    "available": float, "reason": str|None}."""
    path = _ledger_path(state_root, backend)
    with _lock(path):
        rows = _read(path)
        out = sum(float(r["amount"]) for r in rows if r.get("state") == "reserved")
        if worst_case + out > available:
            return {"ok": False, "reserved": 0.0, "outstanding": out, "available": available,
                    "reason": f"worst_case {worst_case} + outstanding {out} > available {available} {unit}"}
        rows.append(
            {"job_id": job_id, "amount": float(worst_case), "unit": unit,
             "state": "reserved", "reserved_at": time.time()}
        )
        _write(path, rows)
        return {"ok": True, "reserved": float(worst_case), "outstanding": out,
                "available": available, "reason": None}
