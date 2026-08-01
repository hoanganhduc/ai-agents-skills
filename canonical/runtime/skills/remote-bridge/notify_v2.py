#!/usr/bin/env python3
"""Pure notification schema and renderers for autonomous research loops.

The module deliberately performs no file, process, or network I/O.  Runtime
code supplies facts, while remote-bridge chooses and executes a transport.
"""

from __future__ import annotations

import copy
import hashlib
import html
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_ID = "aas.autoloop.notify.v2"
SCHEMA_VERSION = "2.1"
SUPPORTED_SCHEMA_VERSIONS = frozenset({"2.0", "2.1"})
BODY_PROFILES = frozenset({"operator_full", "legacy"})
DEFAULT_BODY_PROFILE = "operator_full"
FREEFORM_SECTION_MAX = 500
ISSUE_MESSAGE_MAX = 240
TELEGRAM_SAFE_MAX = 3300
SECTION_SENTINEL_NOT_RECORDED = "Not recorded."
SECTION_SENTINEL_NO_CLAIMS = "No claims banked."
SECTION_SENTINEL_PENDING_DECISION = "Pending (not finalized)"

ITERATION_STATUSES = frozenset(
    {"running", "success", "failure", "error", "waiting", "paused", "not_applicable"}
)
REVIEW_STATUSES = frozenset({"not_required", "pending", "passed", "failed", "error"})
LOOP_STATUSES = frozenset(
    {"initialized", "running", "paused", "blocked", "stopped", "completed", "error"}
)
COMPUTE_STATUSES = frozenset({"succeeded", "failed", "cancelled", "unknown"})
KNOWN_COMPUTE_SERVICES = frozenset(
    {"local", "hetzner", "kaggle", "modal", "github-actions"}
)

_SERVICE_ALIASES = {
    "github-action": "github-actions",
    "github_action": "github-actions",
    "github_actions": "github-actions",
    "github actions": "github-actions",
    "hetzner-cloud": "hetzner",
}
_SERVICE_LABELS = {
    "local": "Local",
    "hetzner": "Hetzner",
    "kaggle": "Kaggle",
    "modal": "Modal",
    "github-actions": "GitHub Actions",
}
_SAFE_CUSTOM_SERVICE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
_SAFE_TOPIC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_ISO_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
_BEARER_SECRET = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_COMMON_SECRET = re.compile(
    r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|"
    r"xox[baprs]-[A-Za-z0-9-]{12,})\b"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:[A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD)|"
    r"authorization|cookie|private[_-]?key)\b\s*[\"']?\s*[:=]\s*[\"']?)"
    r"([^\s\"',;}&<>]{4,})"
)
_URL_CREDENTIAL = re.compile(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@")
_PEM_SECRET = re.compile(
    r"-----BEGIN [^-\r\n]*(?:PRIVATE KEY|SECRET)[^-\r\n]*-----.*?"
    r"-----END [^-\r\n]*(?:PRIVATE KEY|SECRET)[^-\r\n]*-----",
    re.DOTALL | re.IGNORECASE,
)
REDACTION = "[REDACTED]"
PII_REDACTION = "[REDACTED_PII]"
_PII_EMAIL = re.compile(
    r"(?i)(?<![A-Z0-9._%+-])[A-Z0-9._%+-]{1,64}@[A-Z0-9.-]{1,253}\.[A-Z]{2,63}(?![A-Z0-9._%+-])"
)
_PII_PHONE = re.compile(
    r"(?<!\w)(?:\+[1-9][0-9 .()/-]{7,18}[0-9]|\(?[0-9]{3}\)?[ .-][0-9]{3}[ .-][0-9]{4})(?!\w)"
)
_PII_GOVERNMENT_ID = re.compile(
    r"(?i)(\b(?:ssn|social[_ -]?security|passport|national[_ -]?id|tax[_ -]?id)\b\s*[\"']?\s*[:=]\s*[\"']?)([^\s\"',;}&<>]{4,})"
)
_PII_PERSON_RECORD = re.compile(
    r"(?i)(\b(?:participant|patient|subject|research[_ -]?subject|data[_ -]?subject)"
    r"(?:[_ -]?(?:id|name|email|phone|address|dob|date[_ -]?of[_ -]?birth|birth[_ -]?date))?"
    r"\b\s*[\"']?\s*[:=]\s*[\"']?)([^\r\n,;}&<>]{2,})"
)
_PII_CONTACT_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:full[_ -]?name|contact[_ -]?name|email(?:[_ -]?address)?|"
    r"phone(?:[_ -]?number)?|home[_ -]?address|street[_ -]?address|"
    r"date[_ -]?of[_ -]?birth|birth[_ -]?date|dob)\b\s*[\"']?\s*[:=]\s*[\"']?)"
    r"([^\r\n,;}&<>]{2,})"
)
_PII_KEY = re.compile(
    r"(?i)(?:^|[_ -])(?:pii|personal[_ -]?data|participant|patient|subject|"
    r"research[_ -]?subject|data[_ -]?subject|full[_ -]?name|contact[_ -]?name|"
    r"email|phone|home[_ -]?address|street[_ -]?address|date[_ -]?of[_ -]?birth|"
    r"birth[_ -]?date|dob|ssn|passport|national[_ -]?id|tax[_ -]?id)(?:$|[_ -])"
)
_ZULIP_MARKDOWN_CONTROL = re.compile(r"([\\`*_~\[\]#>|])")

_ITERATION_MARKERS = {
    "running": "▶️",
    "success": "✅",
    "failure": "❌",
    "error": "⚠️",
    "waiting": "⏳",
    "paused": "⏸️",
    "not_applicable": "ℹ️",
}
_ITERATION_SUFFIXES = {
    "running": "running",
    "success": "succeeded",
    "failure": "failed review",
    "error": "encountered an error",
    "waiting": "waiting",
    "paused": "paused",
    "not_applicable": "updated",
}


class NotifyValidationError(ValueError):
    """Raised when a notification cannot satisfy the v2 contract."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    value = str(value).strip()
    return value or default


def _zulip_markdown_text(value: Any, default: str = "") -> str:
    """Render one untrusted value as inert, single-line Zulip Markdown text.

    Notification labels and layout are host-owned.  Every value derived from
    research/provider output is flattened, mention-neutralized, and escaped so
    it cannot create a wildcard/personal mention or inject a second section.
    """

    flattened = " ".join(_text(value, default).split())
    mention_safe = flattened.replace("@", "＠")
    return _ZULIP_MARKDOWN_CONTROL.sub(r"\\\1", mention_safe)


def redact_text(value: Any, secret_values: Sequence[str] | None = None) -> str:
    """Remove configured and common credential forms from outbound text."""

    out = _text(value)
    for secret in sorted(
        {
            str(item)
            for item in (secret_values or [])
            if item is not None and len(str(item)) >= 4
        },
        key=len,
        reverse=True,
    ):
        out = out.replace(secret, REDACTION)
    out = _PEM_SECRET.sub(REDACTION, out)
    out = _URL_CREDENTIAL.sub(r"\1" + REDACTION + "@", out)
    out = _BEARER_SECRET.sub("Bearer " + REDACTION, out)
    out = _COMMON_SECRET.sub(REDACTION, out)
    out = _SECRET_ASSIGNMENT.sub(lambda match: match.group(1) + REDACTION, out)
    out = redact_pii_text(out)
    return out


def redact_pii_text(value: Any) -> str:
    """Redact common explicit personal-data forms from outbound text.

    This conservative last-mile guard is not a claim of complete PII
    detection. External panel prompts use refusal/per-payload approval because
    silently redacting research evidence could change its meaning.
    """

    out = _text(value)
    out = _PII_EMAIL.sub(PII_REDACTION, out)
    out = _PII_PHONE.sub(PII_REDACTION, out)
    out = _PII_GOVERNMENT_ID.sub(
        lambda match: match.group(1) + PII_REDACTION, out
    )
    out = _PII_PERSON_RECORD.sub(
        lambda match: match.group(1) + PII_REDACTION, out
    )
    out = _PII_CONTACT_ASSIGNMENT.sub(
        lambda match: match.group(1) + PII_REDACTION, out
    )
    return out


def _integer(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _timestamp(value: Any, field: str, *, required: bool = False) -> str:
    """Validate a bounded timezone-aware ISO-8601 timestamp."""

    raw = _text(value)
    if not raw:
        if required:
            raise NotifyValidationError(f"{field} is required")
        return ""
    if len(raw) > 40 or not _ISO_TIMESTAMP.fullmatch(raw):
        raise NotifyValidationError(
            f"{field} must be a timezone-aware ISO-8601 timestamp"
        )
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError as exc:
        raise NotifyValidationError(
            f"{field} must be a valid ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NotifyValidationError(
            f"{field} must include a timezone"
        )
    return raw


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _first(source: Mapping[str, Any], names: Iterable[str], default: Any = None) -> Any:
    for name in names:
        value = source.get(name)
        if value not in (None, "", [], {}):
            return value
    return default


def slugify_topic(value: str, *, max_length: int = 48) -> str:
    """Return a stable Zulip-topic slug, excluding path separators."""
    raw = _text(value).lower()
    parts: list[str] = []
    pending_dash = False
    for char in raw:
        if char.isascii() and char.isalnum():
            parts.append(char)
            pending_dash = False
        elif char in {" ", "_", "-", ".", "/", "\\", ":"}:
            if parts and not pending_dash:
                parts.append("-")
                pending_dash = True
    slug = "".join(parts).strip("-")[:max_length].rstrip("-")
    return slug or "research"


def _normalize_service(value: Any) -> str:
    if not isinstance(value, str):
        raise NotifyValidationError("each compute record service must be a string")
    raw = _text(value).lower()
    if not raw:
        raise NotifyValidationError("each compute record requires a service")
    raw = _SERVICE_ALIASES.get(raw, raw)
    if raw in KNOWN_COMPUTE_SERVICES:
        return raw
    if raw.startswith("other:"):
        suffix = raw.split(":", 1)[1].strip().lower()
    else:
        suffix = slugify_topic(raw, max_length=64)
    if not _SAFE_CUSTOM_SERVICE.fullmatch(suffix):
        raise NotifyValidationError(f"invalid compute service: {value!r}")
    return f"other:{suffix}"


def normalize_compute_records(value: Any) -> dict[str, Any]:
    """Normalize compute provenance to ``{reported, runs}``.

    ``None`` means legacy/unreported.  An explicit empty list means no external
    computation was used.  Bare provider names are accepted only as an input
    compatibility convenience and become records with ``status=unknown``.
    """
    if value is None:
        return {"reported": False, "runs": []}
    if isinstance(value, Mapping) and ("runs" in value or "reported" in value):
        reported_value = value.get("reported", True)
        if not isinstance(reported_value, bool):
            raise NotifyValidationError("compute.reported must be boolean")
        reported = reported_value
        raw_runs = value.get("runs", [])
        if raw_runs is None:
            raw_runs = []
    else:
        reported = True
        raw_runs = value
    if isinstance(raw_runs, (str, Mapping)):
        raw_runs = [raw_runs]
    if not isinstance(raw_runs, Sequence):
        raise NotifyValidationError("compute records must be a list, object, string, or null")
    if not reported and raw_runs:
        raise NotifyValidationError("unreported compute provenance cannot contain runs")

    runs: list[dict[str, Any]] = []
    for raw in raw_runs:
        if isinstance(raw, str):
            record: dict[str, Any] = {"service": raw, "status": "unknown"}
        elif isinstance(raw, Mapping):
            record = dict(raw)
        else:
            raise NotifyValidationError("each compute record must be an object or service name")
        service = _normalize_service(record.get("service"))
        status = _text(record.get("status"), "unknown").lower().replace(" ", "_")
        if status not in COMPUTE_STATUSES:
            raise NotifyValidationError(f"invalid compute status: {status!r}")
        clean: dict[str, Any] = {"service": service, "status": status}
        for key in ("job_ref", "detail"):
            raw_value = record.get(key)
            if raw_value in (None, ""):
                continue
            if not isinstance(raw_value, str):
                raise NotifyValidationError(f"compute.{key} must be a string")
            val = raw_value.strip()
            if val:
                clean[key] = val[:500] if key == "detail" else val[:200]
        for key in ("started_at", "finished_at"):
            val = _timestamp(record.get(key), f"compute.{key}")
            if val:
                clean[key] = val
        raw_duration = record.get("duration_seconds")
        if raw_duration not in (None, ""):
            if isinstance(raw_duration, bool) or not isinstance(
                raw_duration, (int, float)
            ):
                raise NotifyValidationError(
                    "compute.duration_seconds must be a non-negative number"
                )
            duration = _number(raw_duration)
            if duration is None:
                raise NotifyValidationError(
                    "compute.duration_seconds must be a finite non-negative number"
                )
            clean["duration_seconds"] = duration
        runs.append(clean)
    return {"reported": reported, "runs": runs}


def normalize_agent_usage(value: Any, *, default_role: str) -> dict[str, Any]:
    """Normalize one agent-usage group to ``{reported, agents}``.

    ``None`` means the event did not report agent provenance.  An explicit
    empty list means the role was not used.  Strings are provider/agent names;
    mappings may additionally record provider, model, family, role, and detail.
    """
    if value is None:
        return {"reported": False, "agents": []}
    if isinstance(value, Mapping) and ("agents" in value or "reported" in value):
        reported_value = value.get("reported", True)
        if not isinstance(reported_value, bool):
            raise NotifyValidationError("agent usage reported flag must be boolean")
        reported = reported_value
        raw_agents = value.get("agents", [])
        if raw_agents is None:
            raw_agents = []
    else:
        reported = True
        raw_agents = value
    if isinstance(raw_agents, (str, Mapping)):
        raw_agents = [raw_agents]
    if not isinstance(raw_agents, Sequence):
        raise NotifyValidationError("agent usage must be a list, object, string, or null")
    if not reported and raw_agents:
        raise NotifyValidationError("unreported agent provenance cannot contain agents")

    agents: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_agents:
        if isinstance(raw, str):
            record: dict[str, Any] = {"name": raw}
        elif isinstance(raw, Mapping):
            record = dict(raw)
        else:
            raise NotifyValidationError("each agent record must be an object or name")
        name = _text(_first(record, ("name", "agent", "provider")))
        if not name:
            raise NotifyValidationError("each reported agent requires a name or provider")
        clean = {
            "name": name[:120],
            "role": _text(record.get("role"), default_role)[:80],
        }
        for key in ("provider", "model", "family", "detail"):
            val = _text(record.get(key))
            if val:
                clean[key] = val[:500] if key == "detail" else val[:160]
        signature = _canonical_json(clean)
        if signature not in seen:
            agents.append(clean)
            seen.add(signature)
    return {"reported": reported, "agents": agents}


def _normalize_status(value: Any, allowed: frozenset[str], default: str) -> str:
    status = _text(value, default).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "ok": "success",
        "succeeded": "success",
        "failed": "failure",
        "n/a": "not_applicable",
        "na": "not_applicable",
        "none": "not_applicable",
    }
    status = aliases.get(status, status)
    if status not in allowed:
        raise NotifyValidationError(f"invalid status {value!r}; expected one of {sorted(allowed)}")
    return status


def _normalize_review_status(value: Any) -> str:
    status = _text(value, "not_required").lower().replace("-", "_").replace(" ", "_")
    status = {"success": "passed", "failure": "failed", "not_applicable": "not_required"}.get(
        status, status
    )
    if status not in REVIEW_STATUSES:
        raise NotifyValidationError(
            f"invalid review status {value!r}; expected one of {sorted(REVIEW_STATUSES)}"
        )
    return status


def _normalize_loop_status(value: Any) -> str:
    status = _text(value, "running").lower().replace("-", "_").replace(" ", "_")
    status = {"success": "completed", "failure": "blocked", "waiting": "paused"}.get(
        status, status
    )
    if status not in LOOP_STATUSES:
        return "running"
    return status


def _clip_freeform(value: Any, *, limit: int = FREEFORM_SECTION_MAX, default: str = "") -> str:
    text = _text(value, default)
    if not text:
        return default
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _normalize_body_profile(value: Any) -> str:
    profile = _text(value, DEFAULT_BODY_PROFILE).casefold().replace("-", "_")
    if profile not in BODY_PROFILES:
        raise NotifyValidationError(
            f"presentation.body_profile must be one of {sorted(BODY_PROFILES)}"
        )
    return profile


def normalize_issue_records(
    value: Any,
    *,
    reported: bool | None = None,
) -> dict[str, Any]:
    """Normalize issues with tri-state honesty (like compute).

    - reported=False and empty lists → unreported (legacy / not filled)
    - reported=True and empty lists → host asserts none
    - reported=True with items → populated
    """

    def _one(item: Any) -> dict[str, str]:
        stage = ""
        if isinstance(item, str):
            code = "unspecified"
            message = item
        elif isinstance(item, Mapping):
            code = _text(item.get("code"), "unspecified") or "unspecified"
            message = _text(item.get("message") or item.get("detail") or item.get("reason"))
            stage = _text(item.get("stage"))
        else:
            raise NotifyValidationError("each issue record must be an object or string")
        if not message:
            raise NotifyValidationError("each issue record requires a message")
        out = {
            "code": _clip_freeform(code, limit=64, default="unspecified"),
            "message": _clip_freeform(message, limit=ISSUE_MESSAGE_MAX, default=""),
        }
        if stage:
            out["stage"] = _clip_freeform(stage, limit=64, default="")
        return out

    if value is None:
        return {"reported": False if reported is None else bool(reported), "errors": [], "failures": []}
    if isinstance(value, Mapping) and (
        "errors" in value or "failures" in value or "reported" in value
    ):
        raw_reported = value.get("reported")
        if raw_reported is None:
            flag = True if reported is None else bool(reported)
        elif isinstance(raw_reported, bool):
            flag = raw_reported
        else:
            raise NotifyValidationError("issues.reported must be boolean")
        errors_in = value.get("errors") or []
        failures_in = value.get("failures") or []
        if not isinstance(errors_in, list) or not isinstance(failures_in, list):
            raise NotifyValidationError("issues.errors and issues.failures must be lists")
        errors = [_one(item) for item in errors_in]
        failures = [_one(item) for item in failures_in]
        if not flag and (errors or failures):
            raise NotifyValidationError("unreported issues cannot contain records")
        return {"reported": flag, "errors": errors, "failures": failures}
    raise NotifyValidationError("issues must be an object with reported/errors/failures")


def build_event(
    *,
    event: str,
    title: str,
    goal: str,
    completed: str,
    current: str,
    plan: str,
    iteration_status: str,
    loop_status: str,
    review_status: str = "not_required",
    iteration_number: int | None = None,
    spent_iterations: int | None = None,
    max_iterations: int | None = None,
    goal_progress: str = "Not measured",
    executor: str = "Not recorded",
    driver_agent: Any = None,
    panel_agents: Any = None,
    other_agents: Any = None,
    compute: Any = None,
    occurred_at: str | None = None,
    finished_at: str | None = None,
    started_at: str | None = None,
    duration_seconds: float | None = None,
    event_id: str | None = None,
    topic_slug: str | None = None,
    decision: str = "",
    campaign_id: str = "",
    objective_id: str = "",
    scope: str = "",
    reviewer_families: Sequence[str] | None = None,
    success_criteria: Sequence[str] | str | None = None,
    goal_status: str = "open",
    plan_revision: int | str | None = None,
    results: str | None = None,
    decision_reason: str | None = None,
    issues: Any = None,
    body_profile: str | None = None,
    error_class: str | None = None,
    secret_values: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build one validated v2.1 event from host-resolved facts."""
    title_text = _text(title, "Research")
    event_name = _text(event, "notify")
    iteration_state = _normalize_status(iteration_status, ITERATION_STATUSES, "not_applicable")
    review_state = _normalize_review_status(review_status)
    loop_state = _normalize_loop_status(loop_status)
    occurred = _timestamp(
        _text(occurred_at) or _text(finished_at) or utc_now(),
        "occurred_at",
        required=True,
    )
    if iteration_state in {"running", "waiting", "paused"}:
        finished = ""
    else:
        finished = _timestamp(_text(finished_at) or occurred, "iteration.finished_at")

    if success_criteria is None:
        criteria: list[str] = []
    elif isinstance(success_criteria, str):
        criteria = [success_criteria.strip()] if success_criteria.strip() else []
    else:
        criteria = [_text(item) for item in success_criteria if _text(item)]

    spent = _integer(spent_iterations)
    maximum = _integer(max_iterations)
    remaining = max(0, maximum - spent) if spent is not None and maximum is not None else None
    compute_block = normalize_compute_records(compute)
    issues_block = normalize_issue_records(
        issues if issues is not None else {"reported": True, "errors": [], "failures": []}
    )
    executor_text = _text(executor, "Not recorded")
    driver_value = driver_agent
    if driver_value is None and executor_text != "Not recorded":
        driver_value = executor_text
    agents_block = {
        "driver": normalize_agent_usage(driver_value, default_role="driver"),
        "panel": normalize_agent_usage(panel_agents, default_role="panel reviewer"),
        "other": normalize_agent_usage(other_agents, default_role="other"),
    }
    duration = _number(duration_seconds)
    identity_seed = {
        "event": event_name,
        "occurred_at": occurred,
        "title": title_text,
        "iteration": _integer(iteration_number),
        "status": iteration_state,
        "plan_revision": plan_revision,
    }
    stable_id = _text(event_id) or (
        "notify-" + hashlib.sha256(_canonical_json(identity_seed).encode("utf-8")).hexdigest()[:24]
    )

    raw_decision = _clip_freeform(decision, limit=64, default="")
    if raw_decision:
        decision_display = raw_decision
    elif iteration_state in {"running", "waiting", "paused"}:
        decision_display = SECTION_SENTINEL_PENDING_DECISION
    else:
        decision_display = SECTION_SENTINEL_NOT_RECORDED
    decision_reason_text = _clip_freeform(
        decision_reason, default=SECTION_SENTINEL_NOT_RECORDED
    ) or SECTION_SENTINEL_NOT_RECORDED
    if results is None or not _text(results):
        results_text = SECTION_SENTINEL_NO_CLAIMS
    else:
        results_text = _clip_freeform(results, default=SECTION_SENTINEL_NO_CLAIMS)

    profile = _normalize_body_profile(body_profile or DEFAULT_BODY_PROFILE)
    error_class_text = _clip_freeform(error_class, limit=64, default="")

    result: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "event_id": stable_id,
        "event": event_name,
        "occurred_at": occurred,
        "presentation": {"body_profile": profile},
        "research": {
            "title": title_text,
            "topic_slug": slugify_topic(topic_slug or title_text),
            "goal": _clip_freeform(goal, default="Not recorded."),
            "success_criteria": criteria,
            "goal_status": _text(goal_status, "open"),
        },
        "iteration": {
            "number": _integer(iteration_number),
            "status": iteration_state,
            "decision": raw_decision,
            "campaign_id": _text(campaign_id),
            "objective_id": _text(objective_id),
            "scope": _text(scope),
            "executor": executor_text,
            "started_at": _timestamp(started_at, "iteration.started_at"),
            "finished_at": finished,
            "duration_seconds": duration,
        },
        "review": {
            "status": review_state,
            "families": sorted({_text(f) for f in (reviewer_families or []) if _text(f)}),
        },
        "loop": {"status": loop_state},
        "progress": {
            "spent_iterations": spent,
            "max_iterations": maximum,
            "remaining_iterations": remaining,
            "goal_summary": _text(goal_progress, "Not measured"),
        },
        "agents": agents_block,
        "compute": compute_block,
        "issues": issues_block,
        "sections": {
            "goal": _clip_freeform(goal, default="Not recorded."),
            "completed": _clip_freeform(completed, default="No new result was banked."),
            "current": _clip_freeform(
                current, default="Current research state was not recorded."
            ),
            "plan": _clip_freeform(plan, default="No next action was recorded."),
            "results": results_text,
            "decision": decision_display,
            "decision_reason": decision_reason_text,
        },
    }
    if error_class_text:
        result["iteration"]["error_class"] = error_class_text
    if plan_revision not in (None, ""):
        result["plan_revision"] = plan_revision
    errors = validate_event(result)
    if errors:
        raise NotifyValidationError("; ".join(errors))
    return redact_event(result, secret_values=secret_values)


def _legacy_iteration_status(payload: Mapping[str, Any], event: str) -> str:
    explicit = _first(payload, ("iteration_status", "outcome_status"))
    if explicit:
        try:
            return _normalize_status(explicit, ITERATION_STATUSES, "not_applicable")
        except NotifyValidationError:
            pass
    event_l = event.lower()
    if event_l in {"iteration_ok", "iteration", "result_accepted", "iteration_success"}:
        return "success"
    if event_l in {"result_rejected", "result_review_failed", "iteration_rejected"}:
        return "failure"
    if event_l in {"iteration_failed", "auth_failure", "driver_dead", "runtime_error"}:
        return "error"
    if event_l in {"quota_wait", "credit_wait", "review_wait", "waiting"}:
        return "waiting"
    if event_l == "paused":
        return "paused"
    if event_l in {"iteration_start", "panel_target_start", "result_review_start"}:
        return "running"
    return "not_applicable"


def from_legacy(
    payload: Mapping[str, Any],
    *,
    research: Mapping[str, Any] | None = None,
    secret_values: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Convert an existing flat ARL progress payload to the v2 envelope.

    The conversion never infers compute use from prose, filenames, or paths.
    """
    source = dict(payload)
    research_override = dict(research or {})
    event = _text(source.get("event"), "notify")
    iteration_status = _legacy_iteration_status(source, event)
    title = _text(
        _first(
            research_override,
            ("title", "research_title"),
            _first(source, ("research_title", "loop_name", "title"), "Research loop"),
        )
    )
    goal = _text(
        _first(
            research_override,
            ("goal", "main_goal"),
            _first(source, ("goal", "loop_goal", "main_goal"), "Not recorded in this legacy event."),
        )
    )
    result_text = _text(
        _first(source, ("completed_summary", "result", "output_preview", "output"))
    )
    if iteration_status == "success":
        completed = result_text or "Iteration completed; no plain-language result was recorded."
    elif iteration_status == "failure":
        completed = "No result was banked because the candidate failed review."
    elif iteration_status == "error":
        completed = "No new result was banked; the iteration encountered an operational error."
    elif iteration_status in {"running", "waiting", "paused"}:
        completed = "No new result has been banked by this event."
    else:
        completed = result_text or "No new result was banked by this event."

    current = _text(
        _first(
            source,
            ("current_summary", "current", "where", "progress_note", "goal_contribution_detail"),
            "Current research state was not recorded in this legacy event.",
        )
    )
    plan = _text(
        _first(
            source,
            ("next_action", "plan", "next_preferred_path", "why", "objective"),
            "No next action was recorded in this legacy event.",
        )
    )
    compute_value: Any = None
    if source.get("compute_none") is True:
        compute_value = []
    elif "compute_runs" in source:
        raw_compute = source.get("compute_runs")
        compute_value = (
            None
            if raw_compute is None
            or (isinstance(raw_compute, str) and not raw_compute.strip())
            or (isinstance(raw_compute, Mapping) and not raw_compute)
            else raw_compute
        )
    elif "compute" in source:
        raw_compute = source.get("compute")
        compute_value = (
            None
            if raw_compute is None
            or (isinstance(raw_compute, str) and not raw_compute.strip())
            or (isinstance(raw_compute, Mapping) and not raw_compute)
            else raw_compute
        )

    timestamp = _text(_first(source, ("occurred_at", "timestamp", "finished_at"))) or utc_now()
    finished_at = timestamp if iteration_status not in {"running", "waiting", "paused"} else None
    status_candidate = _first(source, ("loop_status", "status"), "running")
    review_status = _first(source, ("review_status",), "not_required")
    event_v2 = build_event(
        event=event,
        event_id=_text(source.get("event_id")) or None,
        occurred_at=timestamp,
        finished_at=finished_at,
        title=title,
        topic_slug=_text(
            _first(
                research_override,
                ("topic_slug", "job_slug"),
                _first(source, ("topic_slug", "job_slug", "remote_job_id"), title),
            )
        ),
        goal=goal,
        success_criteria=_first(research_override, ("success_criteria",), source.get("success_criteria")),
        goal_status=_text(_first(research_override, ("goal_status",), source.get("goal_status")), "open"),
        completed=completed,
        current=current,
        plan=plan,
        iteration_status=iteration_status,
        loop_status=_normalize_loop_status(status_candidate),
        review_status=_normalize_review_status(review_status),
        iteration_number=_integer(source.get("iteration")),
        spent_iterations=_integer(_first(source, ("spent_iterations", "iteration"))),
        max_iterations=_integer(source.get("max_iterations")),
        goal_progress=_text(
            _first(source, ("goal_progress", "obligation_progress", "where"), "Not measured")
        ),
        executor=_text(_first(source, ("executor", "provider")), "Not recorded"),
        driver_agent=_first(source, ("driver_agent", "executor", "provider")),
        panel_agents=_first(
            source,
            ("panel_agents", "reviewer_agents", "reviewer_providers", "usable_providers"),
        ),
        other_agents=source.get("other_agents"),
        compute=compute_value,
        started_at=_text(source.get("started_at")) or None,
        duration_seconds=_number(source.get("duration_seconds")),
        decision=_text(source.get("decision")),
        campaign_id=_text(source.get("campaign_id")),
        objective_id=_text(source.get("objective_id")),
        scope=_text(source.get("scope")),
        reviewer_families=source.get("reviewer_families") or [],
        plan_revision=source.get("plan_revision"),
        results="Not recorded in this legacy event.",
        decision_reason="Not recorded in this legacy event.",
        issues={"reported": False, "errors": [], "failures": []},
        body_profile=DEFAULT_BODY_PROFILE,
        secret_values=secret_values,
    )
    event_v2["legacy_source"] = {
        "schema_version": _text(source.get("schema_version")),
        "event": redact_text(event, secret_values),
    }
    return event_v2


def upgrade_event_to_current(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Upgrade a supported v2 envelope to SCHEMA_VERSION with honest defaults."""
    event = copy.deepcopy(dict(payload))
    version = str(event.get("schema_version") or "")
    if event.get("schema") != SCHEMA_ID and version not in SUPPORTED_SCHEMA_VERSIONS:
        raise NotifyValidationError(
            f"unsupported notification schema/version: {event.get('schema')!r}/{version!r}"
        )
    if version and version not in SUPPORTED_SCHEMA_VERSIONS:
        raise NotifyValidationError(f"unsupported schema_version: {version!r}")

    if "agents" not in event:
        iteration = event.get("iteration") if isinstance(event.get("iteration"), Mapping) else {}
        executor = _text(iteration.get("executor"))
        event["agents"] = {
            "driver": normalize_agent_usage(
                executor if executor and executor != "Not recorded" else None,
                default_role="driver",
            ),
            "panel": normalize_agent_usage(None, default_role="panel reviewer"),
            "other": normalize_agent_usage(None, default_role="other"),
        }

    presentation = event.get("presentation")
    if not isinstance(presentation, Mapping):
        event["presentation"] = {"body_profile": DEFAULT_BODY_PROFILE}
    else:
        profile = _text(presentation.get("body_profile"), DEFAULT_BODY_PROFILE)
        try:
            event["presentation"] = {
                "body_profile": _normalize_body_profile(profile or DEFAULT_BODY_PROFILE)
            }
        except NotifyValidationError:
            event["presentation"] = {"body_profile": DEFAULT_BODY_PROFILE}

    sections = event.get("sections")
    if not isinstance(sections, Mapping):
        sections = {}
    else:
        sections = dict(sections)
    for key, default in (
        ("goal", "Not recorded."),
        ("completed", "No new result was banked."),
        ("current", "Current research state was not recorded."),
        ("plan", "No next action was recorded."),
    ):
        if not _text(sections.get(key)):
            sections[key] = default
    if not _text(sections.get("results")):
        sections["results"] = (
            "Not recorded in this legacy event."
            if version == "2.0"
            else SECTION_SENTINEL_NO_CLAIMS
        )
    if not _text(sections.get("decision")):
        iteration = event.get("iteration") if isinstance(event.get("iteration"), Mapping) else {}
        raw = _text(iteration.get("decision"))
        status = _text(iteration.get("status"))
        if raw:
            sections["decision"] = raw
        elif status in {"running", "waiting", "paused"}:
            sections["decision"] = SECTION_SENTINEL_PENDING_DECISION
        else:
            sections["decision"] = (
                "Not recorded in this legacy event."
                if version == "2.0"
                else SECTION_SENTINEL_NOT_RECORDED
            )
    if not _text(sections.get("decision_reason")):
        sections["decision_reason"] = (
            "Not recorded in this legacy event."
            if version == "2.0"
            else SECTION_SENTINEL_NOT_RECORDED
        )
    for key in ("goal", "completed", "current", "plan", "results", "decision", "decision_reason"):
        sections[key] = _clip_freeform(sections.get(key), default=SECTION_SENTINEL_NOT_RECORDED)
    event["sections"] = sections

    if "issues" not in event:
        # Legacy v2.0 did not report structured issues → unreported, not "none".
        event["issues"] = {"reported": False, "errors": [], "failures": []}
    else:
        event["issues"] = normalize_issue_records(event.get("issues"))

    event["schema"] = SCHEMA_ID
    event["schema_version"] = SCHEMA_VERSION
    return event


def ensure_event(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a validated v2.1 copy, upgrading supported or flat legacy payloads."""
    if not isinstance(payload, Mapping):
        raise NotifyValidationError("notification JSON must be an object")
    version = str(payload.get("schema_version") or "")
    if payload.get("schema") == SCHEMA_ID or version in SUPPORTED_SCHEMA_VERSIONS:
        event = upgrade_event_to_current(payload)
        errors = validate_event(event)
        if errors:
            raise NotifyValidationError("; ".join(errors))
        return event
    return from_legacy(payload)


def redact_event(
    payload: Mapping[str, Any], *, secret_values: Sequence[str] | None = None
) -> dict[str, Any]:
    """Return a validated event with every nested string value scrubbed.

    Notification envelopes are also used for transport results and delivery
    deduplication, so redaction cannot be limited to fields rendered in the
    human message.  In particular, a secret-shaped caller-supplied event ID is
    replaced with a deterministic opaque ID derived from the already-redacted
    envelope before anything is returned or persisted.
    """

    event = ensure_event(payload)

    def scrub(value: Any, *, key_hint: str = "") -> Any:
        if isinstance(value, str):
            if key_hint and _PII_KEY.search(key_hint):
                return PII_REDACTION
            return redact_text(value, secret_values)
        if isinstance(value, (list, tuple)):
            return [scrub(item, key_hint=key_hint) for item in value]
        if isinstance(value, Mapping):
            return {
                key: scrub(item, key_hint=str(key))
                for key, item in value.items()
            }
        return value

    original_event_id = _text(event.get("event_id"))
    event = scrub(event)
    if not isinstance(event, dict):  # defensive: the validated root is an object
        raise NotifyValidationError("redacted event root is not an object")
    research = event.get("research")
    if isinstance(research, dict):
        research["topic_slug"] = slugify_topic(research.get("topic_slug"))

    if _text(event.get("event_id")) != original_event_id:
        identity_source = copy.deepcopy(event)
        identity_source["event_id"] = ""
        event["event_id"] = "notify-redacted-" + hashlib.sha256(
            _canonical_json(identity_source).encode("utf-8")
        ).hexdigest()[:24]

    errors = validate_event(event)
    if errors:
        raise NotifyValidationError("redacted event invalid: " + "; ".join(errors))
    return event


def validate_event(event: Mapping[str, Any]) -> list[str]:
    """Return all v2.1 contract violations without mutating the event."""
    errors: list[str] = []
    if event.get("schema") != SCHEMA_ID:
        errors.append(f"schema must be {SCHEMA_ID!r}")
    if str(event.get("schema_version")) != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")
    for field in ("event_id", "event", "occurred_at"):
        if not _text(event.get(field)):
            errors.append(f"{field} is required")
    try:
        _timestamp(event.get("occurred_at"), "occurred_at", required=True)
    except NotifyValidationError as exc:
        errors.append(str(exc))
    presentation = event.get("presentation")
    if not isinstance(presentation, Mapping):
        errors.append("presentation must be an object")
    else:
        profile = _text(presentation.get("body_profile"))
        if profile not in BODY_PROFILES:
            errors.append(
                f"presentation.body_profile must be one of {sorted(BODY_PROFILES)}"
            )
    research = event.get("research")
    if not isinstance(research, Mapping):
        errors.append("research must be an object")
    else:
        for field in ("title", "topic_slug", "goal"):
            if not _text(research.get(field)):
                errors.append(f"research.{field} is required")
        slug = _text(research.get("topic_slug"))
        if slug and not _SAFE_TOPIC.fullmatch(slug):
            errors.append("research.topic_slug must be a safe stable slug")
    iteration = event.get("iteration")
    if not isinstance(iteration, Mapping):
        errors.append("iteration must be an object")
    else:
        if _text(iteration.get("status")) not in ITERATION_STATUSES:
            errors.append("iteration.status is invalid")
        if not _text(iteration.get("executor")):
            errors.append("iteration.executor is required")
        try:
            _timestamp(iteration.get("started_at"), "iteration.started_at")
        except NotifyValidationError as exc:
            errors.append(str(exc))
        iteration_status = _text(iteration.get("status"))
        if iteration_status in {"success", "failure", "error", "not_applicable"}:
            if not _text(iteration.get("finished_at")):
                errors.append("iteration.finished_at is required for a finished event")
        try:
            _timestamp(
                iteration.get("finished_at"),
                "iteration.finished_at",
                required=iteration_status
                in {"success", "failure", "error", "not_applicable"},
            )
        except NotifyValidationError as exc:
            if str(exc) not in errors:
                errors.append(str(exc))
        duration = iteration.get("duration_seconds")
        if duration is not None and _number(duration) is None:
            errors.append("iteration.duration_seconds must be a non-negative number or null")
        if iteration.get("error_class") not in (None, ""):
            if not _text(iteration.get("error_class")):
                errors.append("iteration.error_class must be a string when set")
    review = event.get("review")
    if not isinstance(review, Mapping) or _text(review.get("status")) not in REVIEW_STATUSES:
        errors.append("review.status is invalid")
    loop = event.get("loop")
    if not isinstance(loop, Mapping) or _text(loop.get("status")) not in LOOP_STATUSES:
        errors.append("loop.status is invalid")
    if not isinstance(event.get("progress"), Mapping):
        errors.append("progress must be an object")
    agents = event.get("agents")
    if not isinstance(agents, Mapping):
        errors.append("agents must be an object")
    else:
        for group, role in (
            ("driver", "driver"),
            ("panel", "panel reviewer"),
            ("other", "other"),
        ):
            usage = agents.get(group)
            if not isinstance(usage, Mapping):
                errors.append(f"agents.{group} must be an object")
                continue
            try:
                normalized = normalize_agent_usage(usage, default_role=role)
                if normalized != dict(usage):
                    errors.append(f"agents.{group} records are not normalized")
            except NotifyValidationError as exc:
                errors.append(str(exc))
    sections = event.get("sections")
    if not isinstance(sections, Mapping):
        errors.append("sections must be an object")
    else:
        for field in (
            "goal",
            "completed",
            "current",
            "plan",
            "results",
            "decision",
            "decision_reason",
        ):
            if not _text(sections.get(field)):
                errors.append(f"sections.{field} is required")
    compute = event.get("compute")
    if not isinstance(compute, Mapping):
        errors.append("compute must be an object")
    else:
        if not isinstance(compute.get("reported"), bool):
            errors.append("compute.reported must be boolean")
        runs = compute.get("runs")
        if not isinstance(runs, list):
            errors.append("compute.runs must be a list")
        else:
            try:
                normalized = normalize_compute_records(compute)
                if normalized != dict(compute):
                    errors.append("compute records are not normalized")
            except NotifyValidationError as exc:
                errors.append(str(exc))
    issues = event.get("issues")
    if not isinstance(issues, Mapping):
        errors.append("issues must be an object")
    else:
        if not isinstance(issues.get("reported"), bool):
            errors.append("issues.reported must be boolean")
        if not isinstance(issues.get("errors"), list) or not isinstance(
            issues.get("failures"), list
        ):
            errors.append("issues.errors and issues.failures must be lists")
        else:
            try:
                normalize_issue_records(issues)
            except NotifyValidationError as exc:
                errors.append(str(exc))
    return errors


def _duration_text(value: Any) -> str:
    seconds = _number(value)
    if seconds is None:
        return ""
    whole = int(round(seconds))
    if whole < 60:
        return f"{whole}s"
    minutes, sec = divmod(whole, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s" if sec else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def _service_label(service: str) -> str:
    if service in _SERVICE_LABELS:
        return _SERVICE_LABELS[service]
    return service.split(":", 1)[1].replace("-", " ").title()


def format_compute(event: Mapping[str, Any]) -> str:
    compute = event.get("compute") if isinstance(event.get("compute"), Mapping) else {}
    if not compute.get("reported"):
        return "Not recorded (legacy/unreported)"
    runs = compute.get("runs") if isinstance(compute.get("runs"), list) else []
    if not runs:
        return "None"
    values: list[str] = []
    for run in runs:
        if not isinstance(run, Mapping):
            continue
        label = _service_label(_text(run.get("service")))
        status = _text(run.get("status"), "unknown").upper()
        pieces = [f"{label} ({status}"]
        if _text(run.get("job_ref")):
            pieces.append(f"job {_text(run.get('job_ref'))}")
        duration = _duration_text(run.get("duration_seconds"))
        if duration:
            pieces.append(duration)
        values.append(", ".join(pieces) + ")")
    return "; ".join(values) or "None"


def format_agent_usage(event: Mapping[str, Any], group: str) -> str:
    agents = event.get("agents") if isinstance(event.get("agents"), Mapping) else {}
    usage = agents.get(group) if isinstance(agents.get(group), Mapping) else {}
    if not usage.get("reported"):
        return "Not recorded (legacy/unreported)"
    records = usage.get("agents") if isinstance(usage.get("agents"), list) else []
    if not records:
        return "None"
    rendered: list[str] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        name = _text(record.get("name"), "Unknown")
        details: list[str] = []
        model = _text(record.get("model"))
        family = _text(record.get("family"))
        role = _text(record.get("role"))
        if model and model.casefold() != name.casefold():
            details.append(model)
        if family and family.casefold() not in {name.casefold(), model.casefold()}:
            details.append(f"family {family}")
        if group == "other" and role and role != "other":
            details.append(role)
        rendered.append(f"{name} ({', '.join(details)})" if details else name)
    return "; ".join(rendered) or "None"


def _progress_text(event: Mapping[str, Any]) -> str:
    progress = event.get("progress") if isinstance(event.get("progress"), Mapping) else {}
    spent = _integer(progress.get("spent_iterations"))
    maximum = _integer(progress.get("max_iterations"))
    if spent is not None and maximum is not None:
        budget = f"{spent}/{maximum} iteration budget used"
        remaining = _integer(progress.get("remaining_iterations"))
        if remaining is not None:
            budget += f" ({remaining} remaining)"
    elif spent is not None:
        budget = f"{spent} iterations used"
    else:
        number = _integer(
            event.get("iteration", {}).get("number")
            if isinstance(event.get("iteration"), Mapping)
            else None
        )
        budget = f"iteration {number}" if number is not None else "iteration budget not recorded"
    goal_summary = _text(progress.get("goal_summary"), "Not measured")
    return f"{budget}; goal progress: {goal_summary}"


def _finished_text(event: Mapping[str, Any]) -> str:
    iteration = event.get("iteration") if isinstance(event.get("iteration"), Mapping) else {}
    status = _text(iteration.get("status"))
    if status in {"running", "waiting", "paused"}:
        return "Not finished"
    finished = _text(iteration.get("finished_at")) or _text(event.get("occurred_at"))
    if not finished:
        return "Not recorded"
    duration = _duration_text(iteration.get("duration_seconds")) or "Not recorded"
    return f"{finished} · Duration: {duration}"


def _started_text(event: Mapping[str, Any]) -> str:
    """Started time from iteration.started_at only — never invent from finished_at."""
    iteration = event.get("iteration") if isinstance(event.get("iteration"), Mapping) else {}
    started = _text(iteration.get("started_at"))
    return started if started else "Not recorded"


def _body_profile(event: Mapping[str, Any]) -> str:
    presentation = event.get("presentation") if isinstance(event.get("presentation"), Mapping) else {}
    profile = _text(presentation.get("body_profile"), DEFAULT_BODY_PROFILE)
    return profile if profile in BODY_PROFILES else DEFAULT_BODY_PROFILE


def format_issues(event: Mapping[str, Any], kind: str) -> str | None:
    """Human text for errors/failures, or None when the line should be omitted."""
    issues = event.get("issues") if isinstance(event.get("issues"), Mapping) else {}
    if not issues.get("reported"):
        return "Not recorded (legacy/unreported)"
    records = issues.get(kind) if isinstance(issues.get(kind), list) else []
    if not records:
        return None  # omit empty ops noise when host asserts none
    values: list[str] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        code = _text(record.get("code"), "unspecified")
        message = _text(record.get("message"), "Not recorded.")
        stage = _text(record.get("stage"))
        piece = f"{code}: {message}"
        if stage:
            piece += f" ({stage})"
        values.append(piece)
    return "; ".join(values) if values else None


def _title_text(event: Mapping[str, Any]) -> str:
    research = event.get("research") if isinstance(event.get("research"), Mapping) else {}
    iteration = event.get("iteration") if isinstance(event.get("iteration"), Mapping) else {}
    title = _text(research.get("title"), "Research")
    status = _text(iteration.get("status"), "not_applicable")
    number = _integer(iteration.get("number"))
    subject = f"Iteration {number}" if number is not None else _text(event.get("event"), "Update")
    return f"{title} — {subject} {_ITERATION_SUFFIXES.get(status, 'updated')}"


def _status_text(event: Mapping[str, Any]) -> str:
    iteration = event.get("iteration") if isinstance(event.get("iteration"), Mapping) else {}
    loop = event.get("loop") if isinstance(event.get("loop"), Mapping) else {}
    review = event.get("review") if isinstance(event.get("review"), Mapping) else {}
    return (
        f"Iteration {_text(iteration.get('status'), 'unknown').upper()} · "
        f"Loop {_text(loop.get('status'), 'unknown').upper()} · "
        f"Review {_text(review.get('status'), 'unknown').upper().replace('_', ' ')}"
    )


def _render_lines(event: Mapping[str, Any], *, markdown: bool) -> list[str]:
    errors = validate_event(event)
    if errors:
        raise NotifyValidationError("; ".join(errors))
    iteration = event["iteration"]
    sections = event["sections"]
    marker = _ITERATION_MARKERS.get(_text(iteration.get("status")), "•")
    dynamic = _zulip_markdown_text if markdown else _text
    title = dynamic(_title_text(event))
    heading = f"{marker} **{title}**" if markdown else f"{marker} {title}"
    label = (lambda name: f"**{name}**") if markdown else (lambda name: name)
    profile = _body_profile(event)
    lines = [heading]

    if profile == "legacy":
        # Historical v2.0 layout (research sections only in the lead).
        for key, name in (
            ("goal", "Goal"),
            ("completed", "Completed"),
            ("current", "Current"),
            ("plan", "Plan"),
        ):
            lines.extend(["", label(name), dynamic(sections.get(key), "Not recorded.")])
        lines.extend(
            [
                "",
                f"{label('Status')}: {dynamic(_status_text(event))}",
                f"{label('Progress')}: {dynamic(_progress_text(event))}",
                f"{label('Finished')}: {dynamic(_finished_text(event))}",
                f"{label('Executor')}: {dynamic(iteration.get('executor'), 'Not recorded')}",
                f"{label('Driver agent')}: {dynamic(format_agent_usage(event, 'driver'))}",
                f"{label('Panel agents')}: {dynamic(format_agent_usage(event, 'panel'))}",
                f"{label('Other agents')}: {dynamic(format_agent_usage(event, 'other'))}",
                f"{label('Compute')}: {dynamic(format_compute(event))}",
            ]
        )
        return lines

    # operator_full: status early for triage; results/decision/reason in the lead.
    lines.extend(["", f"{label('Status')}: {dynamic(_status_text(event))}"])
    for key, name in (
        ("goal", "Goal"),
        ("completed", "Completed"),
        ("results", "Results"),
        ("current", "Current"),
        ("decision", "Decision"),
        ("decision_reason", "Decision reason"),
        ("plan", "Plan"),
    ):
        lines.extend(["", label(name), dynamic(sections.get(key), "Not recorded.")])
    lines.extend(
        [
            "",
            f"{label('Progress')}: {dynamic(_progress_text(event))}",
            f"{label('Started')}: {dynamic(_started_text(event))}",
            f"{label('Finished')}: {dynamic(_finished_text(event))}",
            f"{label('Executor')}: {dynamic(iteration.get('executor'), 'Not recorded')}",
            f"{label('Driver agent')}: {dynamic(format_agent_usage(event, 'driver'))}",
            f"{label('Panel agents')}: {dynamic(format_agent_usage(event, 'panel'))}",
            f"{label('Other agents')}: {dynamic(format_agent_usage(event, 'other'))}",
            f"{label('Compute')}: {dynamic(format_compute(event))}",
        ]
    )
    runtime_errors = format_issues(event, "errors")
    review_failures = format_issues(event, "failures")
    if runtime_errors is not None:
        lines.append(f"{label('Runtime errors')}: {dynamic(runtime_errors)}")
    if review_failures is not None:
        lines.append(f"{label('Review failures')}: {dynamic(review_failures)}")
    return lines


def render_markdown(
    event: Mapping[str, Any], *, secret_values: Sequence[str] | None = None
) -> str:
    safe = redact_event(event, secret_values=secret_values)
    return "\n".join(_render_lines(safe, markdown=True)).strip()


def render_plain(
    event: Mapping[str, Any], *, secret_values: Sequence[str] | None = None
) -> str:
    safe = redact_event(event, secret_values=secret_values)
    return "\n".join(_render_lines(safe, markdown=False)).strip()


def _bounded_html(value: Any, limit: int) -> str:
    """Escape without ever cutting an entity; output length is at most limit."""
    raw = _text(value)
    rendered: list[str] = []
    used = 0
    truncated = False
    for char in raw:
        escaped = html.escape(char, quote=False)
        if used + len(escaped) > max(0, limit - 1):
            truncated = True
            break
        rendered.append(escaped)
        used += len(escaped)
    if truncated and limit > 0:
        rendered.append("…")
    return "".join(rendered)


def render_telegram_html(
    event: Mapping[str, Any],
    *,
    max_chars: int = TELEGRAM_SAFE_MAX,
    secret_values: Sequence[str] | None = None,
) -> str:
    """Render bounded Telegram HTML without cutting tags or entities."""
    event = redact_event(event, secret_values=secret_values)
    errors = validate_event(event)
    if errors:
        raise NotifyValidationError("; ".join(errors))
    iteration = event["iteration"]
    sections = event["sections"]
    marker = _ITERATION_MARKERS.get(_text(iteration.get("status")), "•")

    profile = _body_profile(event)

    def render(section_budget: int, trailer_scale: float = 1.0) -> str:
        def tb(limit: int) -> int:
            return max(24, int(limit * trailer_scale))

        lines = [f"{marker} <b>{_bounded_html(_title_text(event), 190)}</b>"]
        if profile == "legacy":
            for key, name in (
                ("goal", "Goal"),
                ("completed", "Completed"),
                ("current", "Current"),
                ("plan", "Plan"),
            ):
                lines.extend(
                    ["", f"<b>{name}</b>", _bounded_html(sections.get(key), section_budget)]
                )
            lines.extend(
                [
                    "",
                    f"<b>Status</b>: {_bounded_html(_status_text(event), tb(140))}",
                    f"<b>Progress</b>: {_bounded_html(_progress_text(event), tb(160))}",
                    f"<b>Finished</b>: {_bounded_html(_finished_text(event), tb(100))}",
                    f"<b>Executor</b>: {_bounded_html(iteration.get('executor'), tb(80))}",
                    f"<b>Driver agent</b>: {_bounded_html(format_agent_usage(event, 'driver'), tb(120))}",
                    f"<b>Panel agents</b>: {_bounded_html(format_agent_usage(event, 'panel'), tb(160))}",
                    f"<b>Other agents</b>: {_bounded_html(format_agent_usage(event, 'other'), tb(120))}",
                    f"<b>Compute</b>: {_bounded_html(format_compute(event), tb(180))}",
                ]
            )
        else:
            # Protect decision/results with the section budget; shrink trailer first.
            lines.extend(
                ["", f"<b>Status</b>: {_bounded_html(_status_text(event), max(80, section_budget // 2))}"]
            )
            for key, name in (
                ("goal", "Goal"),
                ("completed", "Completed"),
                ("results", "Results"),
                ("current", "Current"),
                ("decision", "Decision"),
                ("decision_reason", "Decision reason"),
                ("plan", "Plan"),
            ):
                lines.extend(
                    ["", f"<b>{name}</b>", _bounded_html(sections.get(key), section_budget)]
                )
            lines.extend(
                [
                    "",
                    f"<b>Progress</b>: {_bounded_html(_progress_text(event), tb(120))}",
                    f"<b>Started</b>: {_bounded_html(_started_text(event), tb(80))}",
                    f"<b>Finished</b>: {_bounded_html(_finished_text(event), tb(100))}",
                    f"<b>Executor</b>: {_bounded_html(iteration.get('executor'), tb(60))}",
                    f"<b>Driver agent</b>: {_bounded_html(format_agent_usage(event, 'driver'), tb(100))}",
                    f"<b>Panel agents</b>: {_bounded_html(format_agent_usage(event, 'panel'), tb(120))}",
                    f"<b>Other agents</b>: {_bounded_html(format_agent_usage(event, 'other'), tb(80))}",
                    f"<b>Compute</b>: {_bounded_html(format_compute(event), tb(140))}",
                ]
            )
            runtime_errors = format_issues(event, "errors")
            review_failures = format_issues(event, "failures")
            if runtime_errors is not None:
                lines.append(
                    f"<b>Runtime errors</b>: {_bounded_html(runtime_errors, tb(120))}"
                )
            if review_failures is not None:
                lines.append(
                    f"<b>Review failures</b>: {_bounded_html(review_failures, tb(120))}"
                )
        return "\n".join(lines).strip()

    # Prefer shrinking trailer before research section budget.
    for section_budget, trailer_scale in (
        (360, 1.0),
        (300, 0.85),
        (240, 0.7),
        (200, 0.55),
        (160, 0.4),
        (120, 0.3),
        (80, 0.2),
        (48, 0.12),
    ):
        rendered = render(section_budget, trailer_scale)
        if len(rendered) <= max_chars:
            return rendered
    raise NotifyValidationError("Telegram rendering cannot fit mandatory notification fields")


def render_compact(
    event: Mapping[str, Any],
    *,
    max_chars: int = 1200,
    secret_values: Sequence[str] | None = None,
) -> str:
    event = redact_event(event, secret_values=secret_values)
    errors = validate_event(event)
    if errors:
        raise NotifyValidationError("; ".join(errors))
    iteration = event["iteration"]
    sections = event["sections"]
    marker = _ITERATION_MARKERS.get(_text(iteration.get("status")), "•")
    profile = _body_profile(event)

    def clip(value: Any, limit: int) -> str:
        clean = " ".join(_text(value, "Not recorded").split())
        return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"

    # Tiers: protect title/status/completed/results/decision before trailer.
    if profile == "legacy":
        protected = [
            (f"{marker} Title", _title_text(event), 100),
            ("Goal", sections.get("goal"), 120),
            ("Completed", sections.get("completed"), 260),
            ("Current", sections.get("current"), 180),
            ("Plan", sections.get("plan"), 150),
            ("Status", _status_text(event), 90),
        ]
        trailer = [
            ("Progress", _progress_text(event), 90),
            ("Finished", _finished_text(event), 60),
            ("Executor", iteration.get("executor"), 40),
            ("Driver agent", format_agent_usage(event, "driver"), 60),
            ("Panel agents", format_agent_usage(event, "panel"), 80),
            ("Other agents", format_agent_usage(event, "other"), 60),
            ("Compute", format_compute(event), 80),
        ]
    else:
        protected = [
            (f"{marker} Title", _title_text(event), 100),
            ("Status", _status_text(event), 90),
            ("Completed", sections.get("completed"), 200),
            ("Results", sections.get("results"), 160),
            ("Decision", sections.get("decision"), 80),
            ("Decision reason", sections.get("decision_reason"), 140),
            ("Goal", sections.get("goal"), 100),
            ("Current", sections.get("current"), 120),
            ("Plan", sections.get("plan"), 120),
        ]
        trailer = [
            ("Progress", _progress_text(event), 70),
            ("Started", _started_text(event), 50),
            ("Finished", _finished_text(event), 50),
            ("Executor", iteration.get("executor"), 40),
            ("Driver agent", format_agent_usage(event, "driver"), 50),
            ("Panel agents", format_agent_usage(event, "panel"), 60),
            ("Other agents", format_agent_usage(event, "other"), 40),
            ("Compute", format_compute(event), 60),
        ]
        runtime_errors = format_issues(event, "errors")
        review_failures = format_issues(event, "failures")
        if runtime_errors is not None:
            trailer.append(("Runtime errors", runtime_errors, 60))
        if review_failures is not None:
            trailer.append(("Review failures", review_failures, 60))

    for prot_scale, trail_scale in (
        (1.0, 1.0),
        (1.0, 0.7),
        (0.9, 0.5),
        (0.8, 0.35),
        (0.7, 0.25),
        (0.55, 0.15),
        (0.4, 0.1),
        (0.3, 0.05),
    ):
        parts = [
            f"{name}: {clip(raw, max(8, int(limit * prot_scale)))}"
            for name, raw, limit in protected
        ]
        parts.extend(
            f"{name}: {clip(raw, max(8, int(limit * trail_scale)))}"
            for name, raw, limit in trailer
        )
        value = " | ".join(parts)
        value = " ".join(value.split())
        if len(value) <= max_chars:
            return value
    raise NotifyValidationError("compact rendering cannot fit all mandatory fields")


def render_all(
    event: Mapping[str, Any], *, secret_values: Sequence[str] | None = None
) -> dict[str, str]:
    safe = redact_event(event, secret_values=secret_values)
    return {
        "markdown": render_markdown(safe),
        "telegram_html": render_telegram_html(safe),
        "plain": render_plain(safe),
        "compact": render_compact(safe),
    }


def topic_slug(event: Mapping[str, Any]) -> str:
    research = event.get("research") if isinstance(event.get("research"), Mapping) else {}
    explicit = _text(research.get("topic_slug"))
    return explicit if _SAFE_TOPIC.fullmatch(explicit) else slugify_topic(research.get("title", "research"))


def delivery_fingerprint(event: Mapping[str, Any]) -> str:
    """Fingerprint the fields that define one externally visible delivery."""
    validated = ensure_event(event)
    iteration = validated["iteration"]
    seed = {
        "event_id": validated["event_id"],
        "event": validated["event"],
        "iteration": iteration.get("number"),
        "status": iteration.get("status"),
        "plan_revision": validated.get("plan_revision"),
        "body_sha256": hashlib.sha256(render_markdown(validated).encode("utf-8")).hexdigest(),
    }
    return hashlib.sha256(_canonical_json(seed).encode("utf-8")).hexdigest()


def retry_fingerprint(event: Mapping[str, Any]) -> str:
    """Fingerprint semantics while excluding retry-only identity/timing drift.

    Remote delivery uses this digest as its cross-process serialization key.
    A rebuilt retry therefore cannot race the original merely because it has a
    fresh event ID or timing metadata, while material notification content
    (including status, sections, agents, or compute outcome) selects a distinct
    key and remains independently deliverable.
    """

    normalized = copy.deepcopy(ensure_event(event))
    normalized.pop("event_id", None)
    normalized.pop("occurred_at", None)
    iteration = normalized.get("iteration")
    if isinstance(iteration, dict):
        for field in ("started_at", "finished_at", "duration_seconds"):
            iteration.pop(field, None)
    compute = normalized.get("compute")
    if isinstance(compute, dict):
        for run in compute.get("runs") or []:
            if isinstance(run, dict):
                for field in ("started_at", "finished_at", "duration_seconds"):
                    run.pop(field, None)
    return hashlib.sha256(_canonical_json(normalized).encode("utf-8")).hexdigest()


def legacy_flat_fields(event: Mapping[str, Any]) -> dict[str, Any]:
    """Flat aliases for existing ARL consumers during the v2 transition."""
    validated = ensure_event(event)
    iteration = validated["iteration"]
    progress = validated["progress"]
    research = validated["research"]
    sections = validated["sections"]
    rendered = render_all(validated)
    return {
        "timestamp": validated["occurred_at"],
        "event": validated["event"],
        "iteration": iteration.get("number"),
        "spent_iterations": progress.get("spent_iterations"),
        "max_iterations": progress.get("max_iterations"),
        "remaining_iterations": progress.get("remaining_iterations"),
        "decision": iteration.get("decision") or "",
        "status": validated["loop"]["status"],
        "iteration_status": iteration["status"],
        "review_status": validated["review"]["status"],
        "objective": iteration.get("objective_id") or sections["plan"],
        "output_preview": sections["completed"][:500],
        "research_title": research["title"],
        "job_slug": research["topic_slug"],
        "text": rendered["markdown"],
        "text_html": rendered["telegram_html"],
        "text_compact": rendered["compact"],
    }


# Stable descriptive aliases used by runtime integrations.
build_notify_event = build_event
coerce_notify_event = ensure_event
format_notify_markdown = render_markdown
format_notify_telegram_html = render_telegram_html
format_notify_plain = render_plain
format_notify_compact = render_compact


__all__ = [
    "BODY_PROFILES",
    "COMPUTE_STATUSES",
    "DEFAULT_BODY_PROFILE",
    "ITERATION_STATUSES",
    "KNOWN_COMPUTE_SERVICES",
    "LOOP_STATUSES",
    "NotifyValidationError",
    "PII_REDACTION",
    "REVIEW_STATUSES",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "TELEGRAM_SAFE_MAX",
    "build_event",
    "build_notify_event",
    "coerce_notify_event",
    "delivery_fingerprint",
    "ensure_event",
    "format_compute",
    "format_issues",
    "format_notify_compact",
    "format_notify_markdown",
    "format_notify_plain",
    "format_notify_telegram_html",
    "from_legacy",
    "legacy_flat_fields",
    "normalize_compute_records",
    "normalize_issue_records",
    "redact_event",
    "redact_pii_text",
    "redact_text",
    "retry_fingerprint",
    "render_all",
    "render_compact",
    "render_markdown",
    "render_plain",
    "render_telegram_html",
    "slugify_topic",
    "topic_slug",
    "upgrade_event_to_current",
    "validate_event",
]
