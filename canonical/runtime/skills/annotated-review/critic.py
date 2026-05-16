"""Schema validation and formatting utilities for annotated-review."""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_VALID_THINKING = {"none", "low", "medium", "high", "max"}
_VALID_SEVERITY = {"critical", "major", "minor", "suggestion"}
_VALID_TYPE = {"logic", "math", "consistency", "notation", "presentation", "missing", "unsupported"}
_VALID_VER_STATUS = {"confirmed", "disputed", "partial"}
_VALID_TRUST_STATUS = {"verified", "unverified", "suspicious"}


def validate_review(data: Any) -> List[str]:
    """Validate the full review JSON structure.

    Returns a list of error strings.  Empty list means valid.
    """
    errors: List[str] = []

    if not isinstance(data, dict):
        return ["root must be a JSON object"]

    # ── top-level keys ──────────────────────────────────────────────────────
    for key in ("meta", "annotations"):
        if key not in data:
            errors.append(f"missing required top-level key: '{key}'")

    if errors:
        return errors

    # ── meta ────────────────────────────────────────────────────────────────
    meta = data["meta"]
    if not isinstance(meta, dict):
        errors.append("'meta' must be an object")
    else:
        for field in ("reviewed_at", "focus", "agents"):
            if field not in meta:
                errors.append(f"meta missing required field: '{field}'")

        if "reviewed_at" in meta:
            if not isinstance(meta["reviewed_at"], str) or not meta["reviewed_at"].strip():
                errors.append("meta.reviewed_at must be a non-empty ISO 8601 string")

        if "focus" in meta and not isinstance(meta["focus"], str):
            errors.append("meta.focus must be a string")

        if "agents" in meta:
            if not isinstance(meta["agents"], list) or len(meta["agents"]) == 0:
                errors.append("meta.agents must be a non-empty list")
            else:
                for i, agent in enumerate(meta["agents"]):
                    errs = _validate_agent(agent, f"meta.agents[{i}]")
                    errors.extend(errs)

    # ── annotations ─────────────────────────────────────────────────────────
    annotations = data["annotations"]
    if not isinstance(annotations, list):
        errors.append("'annotations' must be a list")
    else:
        for i, ann in enumerate(annotations):
            errs = _validate_annotation(ann, f"annotations[{i}]")
            errors.extend(errs)

    # ── verification (optional) ─────────────────────────────────────────────
    if "verification" in data:
        ver = data["verification"]
        if not isinstance(ver, dict):
            errors.append("'verification' must be an object")
        else:
            errs = _validate_verification(ver, len(data.get("annotations", [])))
            errors.extend(errs)

    # ── trust_verification (optional) ───────────────────────────────────────
    if "trust_verification" in data:
        tv = data["trust_verification"]
        if not isinstance(tv, dict):
            errors.append("'trust_verification' must be an object")
        else:
            errs = _validate_trust_verification(tv)
            errors.extend(errs)

    return errors


def _validate_agent(agent: Any, path: str) -> List[str]:
    errors: List[str] = []
    if not isinstance(agent, dict):
        return [f"{path} must be an object"]
    for field in ("role", "model", "thinking"):
        if field not in agent:
            errors.append(f"{path} missing required field: '{field}'")
    if "role" in agent and not isinstance(agent["role"], str):
        errors.append(f"{path}.role must be a string")
    if "model" in agent and not isinstance(agent["model"], str):
        errors.append(f"{path}.model must be a string")
    if "thinking" in agent and agent["thinking"] not in _VALID_THINKING:
        errors.append(
            f"{path}.thinking must be one of {sorted(_VALID_THINKING)}, "
            f"got '{agent['thinking']}'"
        )
    return errors


def _validate_annotation(ann: Any, path: str) -> List[str]:
    errors: List[str] = []
    if not isinstance(ann, dict):
        return [f"{path} must be an object"]

    # file: str or null
    if "file" in ann and ann["file"] is not None and not isinstance(ann["file"], str):
        errors.append(f"{path}.file must be a string or null")

    # pdf_line_start / pdf_line_end: int or null
    for field in ("pdf_line_start", "pdf_line_end"):
        if field in ann and ann[field] is not None:
            if not isinstance(ann[field], int):
                errors.append(f"{path}.{field} must be an integer or null")

    # page: required int
    if "page" not in ann:
        errors.append(f"{path} missing required field: 'page'")
    elif not isinstance(ann["page"], int):
        errors.append(f"{path}.page must be an integer")

    # quote: required str 10-200 chars
    if "quote" not in ann:
        errors.append(f"{path} missing required field: 'quote'")
    elif not isinstance(ann["quote"], str):
        errors.append(f"{path}.quote must be a string")
    elif not (10 <= len(ann["quote"]) <= 200):
        errors.append(
            f"{path}.quote length must be 10–200 chars, got {len(ann['quote'])}"
        )

    # severity
    if "severity" not in ann:
        errors.append(f"{path} missing required field: 'severity'")
    elif ann["severity"] not in _VALID_SEVERITY:
        errors.append(
            f"{path}.severity must be one of {sorted(_VALID_SEVERITY)}, "
            f"got '{ann['severity']}'"
        )

    # type
    if "type" not in ann:
        errors.append(f"{path} missing required field: 'type'")
    elif ann["type"] not in _VALID_TYPE:
        errors.append(
            f"{path}.type must be one of {sorted(_VALID_TYPE)}, "
            f"got '{ann['type']}'"
        )

    # title
    if "title" not in ann:
        errors.append(f"{path} missing required field: 'title'")
    elif not isinstance(ann["title"], str):
        errors.append(f"{path}.title must be a string")

    # body
    if "body" not in ann:
        errors.append(f"{path} missing required field: 'body'")
    elif not isinstance(ann["body"], str):
        errors.append(f"{path}.body must be a string")

    return errors


def _validate_verification(ver: dict, num_annotations: int) -> List[str]:
    errors: List[str] = []

    if "agent" in ver:
        errs = _validate_agent(ver["agent"], "verification.agent")
        errors.extend(errs)
    else:
        errors.append("verification missing required field: 'agent'")

    if "verified_at" not in ver:
        errors.append("verification missing required field: 'verified_at'")
    elif not isinstance(ver["verified_at"], str):
        errors.append("verification.verified_at must be a string")

    if "results" in ver:
        if not isinstance(ver["results"], list):
            errors.append("verification.results must be a list")
        else:
            for i, result in enumerate(ver["results"]):
                if not isinstance(result, dict):
                    errors.append(f"verification.results[{i}] must be an object")
                    continue
                if "annotation_index" not in result:
                    errors.append(f"verification.results[{i}] missing 'annotation_index'")
                elif not isinstance(result["annotation_index"], int):
                    errors.append(f"verification.results[{i}].annotation_index must be int")
                if "status" not in result:
                    errors.append(f"verification.results[{i}] missing 'status'")
                elif result["status"] not in _VALID_VER_STATUS:
                    errors.append(
                        f"verification.results[{i}].status must be one of "
                        f"{sorted(_VALID_VER_STATUS)}, got '{result['status']}'"
                    )
                if "comment" not in result:
                    errors.append(f"verification.results[{i}] missing 'comment'")
                elif not isinstance(result["comment"], str):
                    errors.append(f"verification.results[{i}].comment must be a string")

    if "additional_issues" in ver:
        if not isinstance(ver["additional_issues"], list):
            errors.append("verification.additional_issues must be a list")
        else:
            for i, issue in enumerate(ver["additional_issues"]):
                errs = _validate_annotation(issue, f"verification.additional_issues[{i}]")
                errors.extend(errs)

    return errors


def _validate_trust_verification(tv: dict) -> List[str]:
    errors: List[str] = []

    if "agent" in tv:
        errs = _validate_agent(tv["agent"], "trust_verification.agent")
        errors.extend(errs)
    else:
        errors.append("trust_verification missing required field: 'agent'")

    if "verified_at" not in tv:
        errors.append("trust_verification missing required field: 'verified_at'")
    elif not isinstance(tv["verified_at"], str):
        errors.append("trust_verification.verified_at must be a string")

    if "references_checked" in tv:
        if not isinstance(tv["references_checked"], list):
            errors.append("trust_verification.references_checked must be a list")
        else:
            for i, ref in enumerate(tv["references_checked"]):
                if not isinstance(ref, dict):
                    errors.append(f"trust_verification.references_checked[{i}] must be an object")
                    continue
                if "status" in ref and ref["status"] not in _VALID_TRUST_STATUS:
                    errors.append(
                        f"trust_verification.references_checked[{i}].status must be one of "
                        f"{sorted(_VALID_TRUST_STATUS)}"
                    )

    if "summary" in tv:
        s = tv["summary"]
        if not isinstance(s, dict):
            errors.append("trust_verification.summary must be an object")

    return errors


# ---------------------------------------------------------------------------
# Color / emoji helpers
# ---------------------------------------------------------------------------

def severity_color_latex(severity: str) -> str:
    mapping = {
        "critical": "red!25",
        "major": "orange!25",
        "minor": "yellow!20",
        "suggestion": "blue!15",
    }
    return mapping.get(severity, "gray!20")


def severity_emoji(severity: str) -> str:
    mapping = {
        "critical": "\U0001f534",   # 🔴
        "major": "\U0001f7e0",      # 🟠
        "minor": "\U0001f7e1",      # 🟡
        "suggestion": "\U0001f535", # 🔵
    }
    return mapping.get(severity, "")


def status_color_latex(status: str) -> str:
    mapping = {
        "confirmed": "green!20",
        "disputed": "purple!20",
        "partial": "teal!20",
        "additional": "cyan!20",
    }
    return mapping.get(status, "gray!20")


def status_emoji(status: str) -> str:
    mapping = {
        "confirmed": "\u2705",      # ✅
        "disputed": "\u274c",       # ❌
        "partial": "\u26a0\ufe0f",  # ⚠️
        "additional": "\u2795",     # ➕
    }
    return mapping.get(status, "")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_datetime(iso_str: str) -> str:
    """Format ISO 8601 string as 'YYYY-MM-DD HH:MM:SS UTC+HH:MM'."""
    if not iso_str:
        return iso_str
    try:
        # Handle various ISO 8601 formats
        s = iso_str.strip()
        # Replace space with T if needed
        s = re.sub(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", r"\1T\2", s)
        # Parse timezone offset manually if present
        tz_match = re.search(r"([+-])(\d{2}):?(\d{2})$", s)
        utc_label = "UTC"
        if tz_match:
            sign = tz_match.group(1)
            h = int(tz_match.group(2))
            m = int(tz_match.group(3))
            utc_label = f"UTC{sign}{h:02d}:{m:02d}"
            # strip tz from string so we can parse datetime
            s_stripped = s[: tz_match.start()]
            offset = timedelta(hours=h, minutes=m)
            if sign == "-":
                offset = -offset
            tz = timezone(offset)
        elif s.endswith("Z"):
            s_stripped = s[:-1]
            tz = timezone.utc
            utc_label = "UTC+00:00"
        else:
            s_stripped = s
            tz = timezone.utc
            utc_label = "UTC+00:00"

        # Remove fractional seconds if present
        s_stripped = re.sub(r"\.\d+$", "", s_stripped)

        dt = datetime.strptime(s_stripped, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=tz)
        return dt.strftime("%Y-%m-%d %H:%M:%S") + f" {utc_label}"
    except Exception:
        return iso_str


def fmt_line_range(ann: dict) -> str:
    """Return 'L{start}–{end}' if both set, else 'Page {page}'."""
    start = ann.get("pdf_line_start")
    end = ann.get("pdf_line_end")
    if start is not None and end is not None:
        return f"L{start}\u2013{end}"
    return f"Page {ann.get('page', '?')}"


def count_annotations(annotations: list) -> dict:
    """Return {critical, major, minor, suggestion} counts."""
    counts = {"critical": 0, "major": 0, "minor": 0, "suggestion": 0}
    for ann in annotations:
        sev = ann.get("severity", "")
        if sev in counts:
            counts[sev] += 1
    return counts


def count_verification(verification: Optional[dict]) -> dict:
    """Return {confirmed, disputed, partial, additions} counts."""
    counts = {"confirmed": 0, "disputed": 0, "partial": 0, "additions": 0}
    if not verification:
        return counts
    for result in verification.get("results", []):
        st = result.get("status", "")
        if st in counts:
            counts[st] += 1
    counts["additions"] = len(verification.get("additional_issues", []))
    return counts


def count_trust(trust_verification: Optional[dict]) -> dict:
    """Return {total, verified, unverified, suspicious} counts from trust_verification."""
    counts = {"total": 0, "verified": 0, "unverified": 0, "suspicious": 0}
    if not trust_verification:
        return counts
    # Use summary if present
    summary = trust_verification.get("summary")
    if isinstance(summary, dict):
        counts["total"] = summary.get("total", 0)
        counts["verified"] = summary.get("verified", 0)
        counts["unverified"] = summary.get("unverified", 0)
        counts["suspicious"] = summary.get("suspicious", 0)
        return counts
    # Fall back to counting references_checked
    refs = trust_verification.get("references_checked", [])
    counts["total"] = len(refs)
    for ref in refs:
        st = ref.get("status", "")
        if st in ("verified", "unverified", "suspicious"):
            counts[st] += 1
    return counts
