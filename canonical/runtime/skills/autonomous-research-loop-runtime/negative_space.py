"""Permanent negative-space ledger for Goal Focus / ARL.

Failed explorations are append-only, queryable, and never bank positive claims.
Reopen requires a new mechanism fingerprint plus independent review binding.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

NEGATIVE_SPACE_REL = Path(".goal_focus") / "negative_space.jsonl"
NEGATIVE_SPACE_SCHEMA = "negative_space.v1"
NEGATIVE_SPACE_KINDS = frozenset(
    {
        "failed_exploration",
        "blocked_route",
        "falsified_hypothesis",
        "dead_end",
        "evidence_gap",
    }
)
NEGATIVE_SPACE_STATUSES = frozenset({"open", "superseded"})
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def negative_space_path(run_dir: str | Path) -> Path:
    return Path(run_dir) / NEGATIVE_SPACE_REL


def mechanism_fingerprint(mechanism_text: str) -> str:
    """Stable hash of a normalized mechanism description (reopen key)."""

    normalized = " ".join(str(mechanism_text or "").strip().lower().split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _clean(value: Any, limit: int = 10000) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        return text[:limit]
    return text


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    # Portable: reject symlinks and non-files without following the final path.
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ValueError(f"cannot read negative space ledger: {exc}") from exc
    import stat as _stat

    if _stat.S_ISLNK(info.st_mode) or not _stat.S_ISREG(info.st_mode):
        raise ValueError(f"negative space path is not a regular file: {path}")
    rows: list[dict[str, Any]] = []
    raw = path.read_text(encoding="utf-8")
    for index, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on negative_space line {index}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"negative_space line {index} must be a JSON object")
        rows.append(value)
    return rows


def load_negative_space(run_dir: str | Path) -> list[dict[str, Any]]:
    """Load ledger rows; missing file means empty (lazy create on first write)."""

    return _read_jsonl(negative_space_path(run_dir))


def open_entries(
    run_dir: str | Path,
    *,
    approach_id: str | None = None,
    campaign_id: str | None = None,
) -> list[dict[str, Any]]:
    rows = load_negative_space(run_dir)
    out: list[dict[str, Any]] = []
    for row in rows:
        if _clean(row.get("status")).lower() != "open":
            continue
        if approach_id is not None and _clean(row.get("approach_id")) != _clean(approach_id):
            continue
        if campaign_id is not None and _clean(row.get("campaign_id")) != _clean(campaign_id):
            continue
        out.append(row)
    return out


def open_mechanism_fingerprints(run_dir: str | Path, approach_id: str) -> set[str]:
    return {
        _clean(row.get("mechanism_fingerprint"))
        for row in open_entries(run_dir, approach_id=approach_id)
        if _clean(row.get("mechanism_fingerprint"))
    }


def approach_blocked_by_negative_space(run_dir: str | Path, approach_id: str) -> str:
    """Return exclusion reason when an open negative-space row covers the approach."""

    open_rows = open_entries(run_dir, approach_id=approach_id)
    if not open_rows:
        return ""
    entry_ids = sorted(_clean(r.get("entry_id")) for r in open_rows if _clean(r.get("entry_id")))
    return "negative_space_open:" + ",".join(entry_ids[:5])


def build_entry(
    *,
    kind: str,
    mechanism_text: str,
    failure_summary: str,
    reopen_condition: str,
    approach_id: str | None = None,
    campaign_id: str | None = None,
    evidence_ids: Sequence[str] | None = None,
    evidence_absent_reason: str | None = None,
    iteration_id: str | None = None,
    candidate_fingerprint: str | None = None,
    result_review_fingerprint: str | None = None,
    direction_decision_id: str | None = None,
    registry_revision_at_write: int = 0,
    entry_id: str | None = None,
    status: str = "open",
    closed_at: str | None = None,
    superseded_by: str | None = None,
) -> dict[str, Any]:
    kind_clean = _clean(kind).lower()
    if kind_clean not in NEGATIVE_SPACE_KINDS:
        raise ValueError(f"invalid negative_space kind: {kind!r}")
    status_clean = _clean(status).lower()
    if status_clean not in NEGATIVE_SPACE_STATUSES:
        raise ValueError(f"invalid negative_space status: {status!r}")
    mechanism = _clean(mechanism_text)
    if not mechanism:
        raise ValueError("mechanism_text is required")
    failure = _clean(failure_summary)
    if not failure:
        raise ValueError("failure_summary is required")
    reopen = _clean(reopen_condition)
    if not reopen:
        raise ValueError("reopen_condition is required")
    evidence = [_clean(x) for x in (evidence_ids or []) if _clean(x)]
    absent = _clean(evidence_absent_reason) or None
    if not evidence and kind_clean != "evidence_gap" and not absent:
        raise ValueError(
            "evidence_ids required unless kind is evidence_gap with evidence_absent_reason"
        )
    if not evidence and kind_clean == "evidence_gap" and not absent:
        raise ValueError("evidence_gap requires evidence_absent_reason when evidence_ids empty")
    eid = _clean(entry_id) or f"ns-{uuid.uuid4().hex}"
    if not _SAFE_ID.fullmatch(eid):
        raise ValueError(f"entry_id is unsafe: {eid!r}")
    return {
        "schema_version": NEGATIVE_SPACE_SCHEMA,
        "entry_id": eid,
        "kind": kind_clean,
        "status": status_clean,
        "created_at": utc_now(),
        "closed_at": closed_at,
        "campaign_id": _clean(campaign_id) or None,
        "approach_id": _clean(approach_id) or None,
        "mechanism_fingerprint": mechanism_fingerprint(mechanism),
        "mechanism_text": mechanism,
        "failure_summary": failure,
        "evidence_ids": evidence,
        "evidence_absent_reason": absent,
        "iteration_id": _clean(iteration_id) or None,
        "candidate_fingerprint": _clean(candidate_fingerprint) or None,
        "result_review_fingerprint": _clean(result_review_fingerprint) or None,
        "direction_decision_id": _clean(direction_decision_id) or None,
        "reopen_condition": reopen,
        "reopen_requires_new_mechanism": True,
        "superseded_by": _clean(superseded_by) or None,
        "registry_revision_at_write": int(registry_revision_at_write or 0),
    }


def validate_entry(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if row.get("schema_version") != NEGATIVE_SPACE_SCHEMA:
        errors.append(f"schema_version must be {NEGATIVE_SPACE_SCHEMA}")
    if _clean(row.get("kind")).lower() not in NEGATIVE_SPACE_KINDS:
        errors.append("kind is invalid")
    if _clean(row.get("status")).lower() not in NEGATIVE_SPACE_STATUSES:
        errors.append("status is invalid")
    if not _clean(row.get("entry_id")):
        errors.append("entry_id is required")
    elif not _SAFE_ID.fullmatch(_clean(row.get("entry_id"))):
        errors.append("entry_id is unsafe")
    if not _clean(row.get("mechanism_fingerprint")):
        errors.append("mechanism_fingerprint is required")
    if not _clean(row.get("mechanism_text")):
        errors.append("mechanism_text is required")
    if not _clean(row.get("failure_summary")):
        errors.append("failure_summary is required")
    if not _clean(row.get("reopen_condition")):
        errors.append("reopen_condition is required")
    if row.get("reopen_requires_new_mechanism") is not True:
        errors.append("reopen_requires_new_mechanism must be true")
    evidence = row.get("evidence_ids")
    if evidence is None:
        evidence = []
    if not isinstance(evidence, list):
        errors.append("evidence_ids must be a list")
        evidence = []
    if not evidence and _clean(row.get("kind")).lower() != "evidence_gap":
        if not _clean(row.get("evidence_absent_reason")):
            errors.append("evidence_ids empty without evidence_absent_reason")
    if _clean(row.get("status")).lower() == "superseded":
        if not _clean(row.get("superseded_by")):
            errors.append("superseded rows require superseded_by")
        if not _clean(row.get("closed_at")):
            errors.append("superseded rows require closed_at")
    return errors


def validate_negative_space(
    run_dir: str | Path,
    registry: Mapping[str, Any] | None = None,
    *,
    enforce: bool = False,
) -> dict[str, Any]:
    """Validate ledger integrity and optional coupling to approach_registry."""

    errors: list[str] = []
    warnings: list[str] = []
    path = negative_space_path(run_dir)
    try:
        rows = load_negative_space(run_dir)
    except (OSError, ValueError) as exc:
        return {
            "status": "error",
            "errors": [str(exc)],
            "warnings": warnings,
            "open_count": 0,
            "path": path.as_posix(),
        }

    seen_ids: set[str] = set()
    open_by_approach: dict[str, list[str]] = {}
    open_mech: dict[str, set[str]] = {}
    for index, row in enumerate(rows, start=1):
        row_errors = validate_entry(row)
        for err in row_errors:
            errors.append(f"negative_space row {index}: {err}")
        eid = _clean(row.get("entry_id"))
        if eid:
            if eid in seen_ids:
                errors.append(f"duplicate negative_space entry_id: {eid}")
            seen_ids.add(eid)
        if _clean(row.get("status")).lower() == "open":
            aid = _clean(row.get("approach_id"))
            if aid:
                open_by_approach.setdefault(aid, []).append(eid)
                fp = _clean(row.get("mechanism_fingerprint"))
                if fp:
                    open_mech.setdefault(aid, set()).add(fp)
                    if len(open_mech[aid]) != len(
                        {
                            _clean(r.get("mechanism_fingerprint"))
                            for r in rows
                            if _clean(r.get("status")).lower() == "open"
                            and _clean(r.get("approach_id")) == aid
                            and _clean(r.get("mechanism_fingerprint"))
                        }
                    ):
                        pass  # uniqueness checked below
    for aid, fps in open_mech.items():
        # Detect duplicate open fingerprints for same approach
        counts: dict[str, int] = {}
        for row in rows:
            if (
                _clean(row.get("status")).lower() == "open"
                and _clean(row.get("approach_id")) == aid
            ):
                fp = _clean(row.get("mechanism_fingerprint"))
                if fp:
                    counts[fp] = counts.get(fp, 0) + 1
        for fp, count in counts.items():
            if count > 1:
                errors.append(
                    f"duplicate open mechanism_fingerprint for approach {aid}: {fp}"
                )

    if isinstance(registry, Mapping):
        campaigns = registry.get("campaigns")
        if isinstance(campaigns, dict):
            for campaign_id, campaign in campaigns.items():
                if not isinstance(campaign, dict):
                    continue
                approaches = campaign.get("approaches")
                if not isinstance(approaches, dict):
                    continue
                for approach_id, approach in approaches.items():
                    if not isinstance(approach, dict):
                        continue
                    status = _clean(approach.get("status") or "eligible").lower()
                    if status in {"blocked", "closed"}:
                        has_row = any(
                            _clean(r.get("approach_id")) == str(approach_id)
                            for r in rows
                        )
                        if not has_row:
                            msg = (
                                f"approach {approach_id} is {status} without a "
                                "negative_space row"
                            )
                            if enforce:
                                errors.append(msg)
                            else:
                                warnings.append(msg)
                        if not _clean(approach.get("reopen_condition")):
                            msg = (
                                f"approach {approach_id} is {status} without reopen_condition"
                            )
                            if enforce:
                                errors.append(msg)
                            else:
                                warnings.append(msg)

    open_count = sum(1 for r in rows if _clean(r.get("status")).lower() == "open")
    return {
        "status": "error" if errors else "ok",
        "errors": errors,
        "warnings": warnings,
        "open_count": open_count,
        "path": NEGATIVE_SPACE_REL.as_posix(),
        "exists": path.exists(),
    }


def can_reopen_with_mechanism(
    run_dir: str | Path,
    *,
    approach_id: str,
    new_mechanism_text: str,
    different_family_review_fingerprint: str | None,
) -> tuple[bool, str]:
    """Whether a blocked approach may reopen under a new mechanism.

    Same mechanism fingerprint as any open row → wording_only_reopen (refuse).
    Missing different-family review binding → refuse.
    """

    new_fp = mechanism_fingerprint(new_mechanism_text)
    open_fps = open_mechanism_fingerprints(run_dir, approach_id)
    if new_fp in open_fps:
        return False, "wording_only_reopen"
    if not _clean(different_family_review_fingerprint):
        return False, "missing_different_family_review"
    return True, "ok"


def supersession_rows(
    *,
    old_entry: Mapping[str, Any],
    new_mechanism_text: str,
    failure_summary: str,
    reopen_condition: str,
    different_family_review_fingerprint: str,
    evidence_ids: Sequence[str] | None = None,
    registry_revision_at_write: int = 0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (updated_old_open→superseded, new_open_entry) for a legal reopen.

    Callers rewrite the ledger by replacing the old open row and appending the new
    open row (or use append-only supersede record + new entry if preferred).
    Here we mutate status on a copy of old and create a new open entry.
    """

    old = dict(old_entry)
    if _clean(old.get("status")).lower() != "open":
        raise ValueError("only open entries can be superseded")
    new_entry = build_entry(
        kind=_clean(old.get("kind")) or "failed_exploration",
        mechanism_text=new_mechanism_text,
        failure_summary=failure_summary,
        reopen_condition=reopen_condition,
        approach_id=_clean(old.get("approach_id")) or None,
        campaign_id=_clean(old.get("campaign_id")) or None,
        evidence_ids=list(evidence_ids or old.get("evidence_ids") or []),
        evidence_absent_reason=_clean(old.get("evidence_absent_reason")) or None,
        result_review_fingerprint=different_family_review_fingerprint,
        registry_revision_at_write=registry_revision_at_write,
    )
    if new_entry["mechanism_fingerprint"] == _clean(old.get("mechanism_fingerprint")):
        raise ValueError("wording_only_reopen")
    if not _clean(different_family_review_fingerprint):
        raise ValueError("missing_different_family_review")
    closed = dict(old)
    closed["status"] = "superseded"
    closed["closed_at"] = utc_now()
    closed["superseded_by"] = new_entry["entry_id"]
    return closed, new_entry


def rewrite_ledger(run_dir: str | Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    """Atomically replace the ledger with the given rows (UTF-8 JSONL)."""

    path = negative_space_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        json.dumps(dict(row), sort_keys=True, ensure_ascii=False) + "\n" for row in rows
    )
    # Atomic replace without following symlinks on the final path.
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return path


def append_entries(
    run_dir: str | Path, entries: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Validate and append entries; returns the full post-image list."""

    existing = load_negative_space(run_dir)
    existing_ids = {_clean(r.get("entry_id")) for r in existing}
    to_add: list[dict[str, Any]] = []
    for raw in entries:
        row = dict(raw)
        errs = validate_entry(row)
        if errs:
            raise ValueError("; ".join(errs))
        eid = _clean(row.get("entry_id"))
        if eid in existing_ids or eid in {_clean(r.get("entry_id")) for r in to_add}:
            raise ValueError(f"duplicate entry_id: {eid}")
        to_add.append(row)
    post = [*existing, *to_add]
    rewrite_ledger(run_dir, post)
    return post


def entry_for_blocked_approach(
    *,
    approach_id: str,
    campaign_id: str | None,
    mechanism_text: str,
    failure_summary: str,
    reopen_condition: str,
    evidence_ids: Sequence[str] | None = None,
    evidence_absent_reason: str | None = None,
    iteration_id: str | None = None,
    candidate_fingerprint: str | None = None,
    result_review_fingerprint: str | None = None,
    registry_revision: int = 0,
    kind: str = "blocked_route",
) -> dict[str, Any]:
    return build_entry(
        kind=kind,
        mechanism_text=mechanism_text,
        failure_summary=failure_summary,
        reopen_condition=reopen_condition,
        approach_id=approach_id,
        campaign_id=campaign_id,
        evidence_ids=evidence_ids,
        evidence_absent_reason=evidence_absent_reason
        or ("no_staged_artifact" if not evidence_ids else None),
        iteration_id=iteration_id,
        candidate_fingerprint=candidate_fingerprint,
        result_review_fingerprint=result_review_fingerprint,
        registry_revision_at_write=registry_revision,
    )


def summarize_open(run_dir: str | Path, *, limit: int = 20) -> list[dict[str, str]]:
    """Compact open-row summary for status / strategy review prompts."""

    rows = open_entries(run_dir)
    out: list[dict[str, str]] = []
    for row in rows[: max(0, limit)]:
        out.append(
            {
                "entry_id": _clean(row.get("entry_id")),
                "approach_id": _clean(row.get("approach_id")),
                "kind": _clean(row.get("kind")),
                "mechanism_text": _clean(row.get("mechanism_text"), limit=200),
                "failure_summary": _clean(row.get("failure_summary"), limit=200),
            }
        )
    return out
