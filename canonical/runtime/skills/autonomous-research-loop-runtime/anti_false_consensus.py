"""Anti-false-consensus helpers for multi-agent review and Goal Focus banking.

Portable rules (R1–R4):
  R1 bound-and-escalate (max review rounds; never persist-until-approve)
  R2 evidence-delta required between critique rounds
  R3 residual uncertainty mandatory on unfinished load-bearing claims
  R4 bankable claims require different-family review and/or machine check;
     multi-LLM LGTM alone never banks
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

DEFAULT_MAX_REVIEW_ROUNDS = 3
HARD_MAX_REVIEW_ROUNDS = 5

_BANKABLE_PASS = frozenset({"passed", "accepted", "pass", "pass_with_notes"})
_APPROVE_WORDS = frozenset(
    {
        "approve",
        "approved",
        "lgtm",
        "looks good",
        "looks_good",
        "pass",
        "passed",
        "accepted",
        "agree",
        "agreed",
        "consensus",
    }
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _as_set(values: Any) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        text = _clean(values)
        return {text} if text else set()
    if isinstance(values, (list, tuple, set)):
        return {_clean(v) for v in values if _clean(v)}
    return set()


def normalize_max_review_rounds(value: Any, *, default: int = DEFAULT_MAX_REVIEW_ROUNDS) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    if n < 1:
        n = 1
    if n > HARD_MAX_REVIEW_ROUNDS:
        n = HARD_MAX_REVIEW_ROUNDS
    return n


def review_rounds_exhausted(rounds_used: Any, max_rounds: Any = DEFAULT_MAX_REVIEW_ROUNDS) -> bool:
    try:
        used = int(rounds_used)
    except (TypeError, ValueError):
        used = 0
    return used >= normalize_max_review_rounds(max_rounds)


def forbid_persist_until_approve(policy_text: str) -> bool:
    """True when policy language requests co-math-style infinite approve loops."""

    text = " ".join(_clean(policy_text).lower().split())
    needles = (
        "until all approve",
        "until all reviewers approve",
        "continue until unanimous",
        "persist until approve",
        "approve until green",
        "re-review until pass",
    )
    return any(n in text for n in needles)


def evidence_delta(
    prior: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
) -> dict[str, list[str]]:
    """Compute R2 evidence delta between two review/iteration snapshots."""

    prior = prior or {}
    current = current or {}
    prior_e = _as_set(prior.get("evidence_ids") or prior.get("new_evidence_ids"))
    curr_e = _as_set(current.get("evidence_ids") or current.get("new_evidence_ids"))
    prior_ns = _as_set(prior.get("negative_space_entry_ids") or prior.get("ns_entry_ids"))
    curr_ns = _as_set(current.get("negative_space_entry_ids") or current.get("ns_entry_ids"))
    prior_sup = _as_set(prior.get("superseded_ns_entry_ids"))
    curr_sup = _as_set(current.get("superseded_ns_entry_ids"))
    prior_ob = _as_set(prior.get("obligation_transitions") or prior.get("obligation_ids"))
    curr_ob = _as_set(current.get("obligation_transitions") or current.get("obligation_ids"))
    # Also accept list-of-dicts obligation transitions with ids
    for key, bucket in (
        ("obligation_transitions", "obligation_id"),
        ("obligation_reviews", "obligation_id"),
    ):
        for source, target_set in ((prior, prior_ob), (current, curr_ob)):
            raw = source.get(key)
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, Mapping):
                        oid = _clean(item.get(bucket) or item.get("id"))
                        if oid:
                            target_set.add(oid)
                    else:
                        oid = _clean(item)
                        if oid:
                            target_set.add(oid)
    return {
        "new_evidence_ids": sorted(curr_e - prior_e),
        "new_ns_entry_ids": sorted(curr_ns - prior_ns),
        "superseded_ns_entry_ids": sorted(curr_sup - prior_sup),
        "obligation_transitions": sorted(curr_ob - prior_ob),
    }


def has_substantive_evidence_delta(delta: Mapping[str, Sequence[str]]) -> bool:
    """R2: NS-only churn is not enough to approve; evidence or obligation change is."""

    return bool(delta.get("new_evidence_ids") or delta.get("obligation_transitions"))


def allows_review_round_progress(
    prior: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
) -> tuple[bool, str, dict[str, list[str]]]:
    """Whether a follow-up critique round is justified (R2)."""

    delta = evidence_delta(prior, current)
    if has_substantive_evidence_delta(delta):
        return True, "ok", delta
    # Wording / agreement-only changes
    prior_text = _clean((prior or {}).get("summary") or (prior or {}).get("prose"))
    curr_text = _clean((current or {}).get("summary") or (current or {}).get("prose"))
    if prior_text and curr_text and prior_text != curr_text and not has_substantive_evidence_delta(delta):
        return False, "wording_only_convergence", delta
    if not has_substantive_evidence_delta(delta):
        return False, "empty_evidence_delta", delta
    return True, "ok", delta


def multi_llm_lgtm_only(review: Mapping[str, Any] | None) -> bool:
    """True when review is agreement theater without DF / machine-check support."""

    review = review or {}
    different_family = review.get("different_family") is True
    machine = (
        review.get("machine_check_passed") is True
        or review.get("machine_checkable") is True
        or _clean(review.get("machine_check_status")).lower() in {"pass", "passed", "ok"}
    )
    if different_family or machine:
        return False
    # Explicit multi-provider approve lists without DF flag
    providers = review.get("providers") or review.get("provider_reviews") or []
    if isinstance(providers, list) and len(providers) >= 2:
        return True
    votes = review.get("approvals") or review.get("votes") or []
    if isinstance(votes, list) and len(votes) >= 2:
        return True
    text = " ".join(
        _clean(review.get(key)).lower()
        for key in ("summary", "verdict_text", "notes", "status", "verdict")
    )
    if any(word in text for word in _APPROVE_WORDS) and not different_family and not machine:
        # Single soft LGTM without DF is also not bankable as sole evidence
        if review.get("safe_to_bank") is True and not different_family and not machine:
            return True
    return False


def bankable_review_ok(
    review: Mapping[str, Any] | None,
    *,
    accepted: bool,
    machine_check_passed: bool = False,
) -> tuple[bool, str]:
    """R4: accepted bank requires different-family and/or machine check."""

    if not accepted:
        return True, "reject_path"
    review = review or {}
    different_family = review.get("different_family") is True
    machine = machine_check_passed or (
        review.get("machine_check_passed") is True
        or _clean(review.get("machine_check_status")).lower() in {"pass", "passed", "ok"}
    )
    if different_family or machine:
        return True, "ok"
    if multi_llm_lgtm_only(review):
        return False, "multi_llm_lgtm_not_bank"
    return False, "missing_different_family_or_machine_check"


def residual_uncertainty_labels(
    *,
    load_bearing_claims: Sequence[Mapping[str, Any]] | Sequence[str] | None,
    unfinished_claim_ids: Sequence[str] | None = None,
    open_negative_space_ids: Sequence[str] | None = None,
    blocked_checks: Sequence[str] | None = None,
) -> list[str]:
    """R3: forced residual uncertainty labels for delivery/synthesis."""

    labels: list[str] = []
    unfinished = {_clean(x) for x in (unfinished_claim_ids or []) if _clean(x)}
    for claim in load_bearing_claims or []:
        if isinstance(claim, Mapping):
            cid = _clean(claim.get("claim_id") or claim.get("id"))
            status = _clean(claim.get("status") or claim.get("support_status")).lower()
            if status in {"", "supported", "proved", "pass"}:
                if cid and cid in unfinished:
                    labels.append(f"unfinished:{cid}")
                continue
            if cid:
                labels.append(f"{status or 'uncertain'}:{cid}")
            else:
                labels.append(status or "uncertain")
        else:
            text = _clean(claim)
            if text:
                labels.append(text)
    for cid in unfinished:
        tag = f"unfinished:{cid}"
        if tag not in labels:
            labels.append(tag)
    for ns in open_negative_space_ids or []:
        text = _clean(ns)
        if text:
            labels.append(f"negative_space:{text}")
    for check in blocked_checks or []:
        text = _clean(check)
        if text:
            labels.append(f"blocked_check:{text}")
    return labels


def synthesis_erases_disagreement(
    prior_disputes: Sequence[str] | None,
    final_text: str,
    *,
    residual_labels: Sequence[str] | None = None,
) -> bool:
    """True when prior disputes vanish without residual uncertainty labels (R3)."""

    disputes = [_clean(x).lower() for x in (prior_disputes or []) if _clean(x)]
    if not disputes:
        return False
    if residual_labels:
        return False
    text = _clean(final_text).lower()
    # If none of the dispute tokens appear and no residual labels, treat as erasure
    return not any(d in text for d in disputes if len(d) >= 4)


def escalate_unfinished_payload(
    *,
    reason: str,
    open_negative_space: Sequence[Mapping[str, Any]] | None = None,
    residual_uncertainty: Sequence[str] | None = None,
    rounds_used: int = 0,
    max_rounds: int = DEFAULT_MAX_REVIEW_ROUNDS,
) -> dict[str, Any]:
    """R1 deliverable when review cannot converge within the round budget."""

    return {
        "schema_version": "anti_false_consensus.escalate.v1",
        "status": "unfinished",
        "reason": _clean(reason) or "review_rounds_exhausted",
        "rounds_used": int(rounds_used),
        "max_rounds": normalize_max_review_rounds(max_rounds),
        "residual_uncertainty": list(residual_uncertainty or []),
        "open_negative_space": [
            {
                "entry_id": _clean(row.get("entry_id")),
                "approach_id": _clean(row.get("approach_id")),
                "failure_summary": _clean(row.get("failure_summary")),
            }
            for row in (open_negative_space or [])
            if isinstance(row, Mapping)
        ],
        "bankable": False,
    }
