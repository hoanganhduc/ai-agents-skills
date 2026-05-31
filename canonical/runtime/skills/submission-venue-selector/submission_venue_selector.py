#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
DELIVERY_READY = "ready"
DELIVERY_CAVEATS = "ready-with-caveats"
DELIVERY_NOT_READY = "not-ready"
REQUIRED_ARTIFACTS = (
    "run_status.json",
    "selection_plan.json",
    "draft.json",
    "references.jsonl",
    "papers.jsonl",
    "sources.jsonl",
    "queries.jsonl",
    "provider_status.json",
    "evidence.jsonl",
    "claims.jsonl",
    "guards.jsonl",
    "venues.jsonl",
    "venue_profiles.jsonl",
    "recent_papers.jsonl",
    "scores.jsonl",
    "scorecards.jsonl",
    "base_rate_sources.jsonl",
    "chance_estimates.jsonl",
    "delivery.json",
    "recommendation.md",
)
NETWORK_PROVIDERS = {"openalex", "crossref", "semantic-scholar"}
SAFE_PROVIDER_DOMAINS = {
    "openalex": "api.openalex.org",
    "crossref": "api.crossref.org",
    "semantic-scholar": "api.semanticscholar.org",
}
IMPLEMENTED_LIVE_PROVIDERS = {"openalex"}
COMPARATOR_EVIDENCE_LEVELS = {"metadata_only", "abstract_inspected", "full_text_inspected"}
READY_COMPARATOR_EVIDENCE_LEVELS = {"abstract_inspected", "full_text_inspected"}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class SelectorError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return text[:80] or "unknown"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise SelectorError(f"{path.name} must contain a JSON object")
    return payload


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise SelectorError(f"{path.name}:{line_number} must be a JSON object")
            rows.append(payload)
    return rows


def workspace(args: argparse.Namespace) -> Path:
    raw = getattr(args, "dir", None)
    if not raw:
        raise SelectorError("--dir is required")
    return Path(raw).expanduser().resolve()


def runtime_source_root() -> Path:
    return Path(__file__).resolve().parents[3]


def repo_root_guess() -> Path | None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "manifest").is_dir() and (parent / "canonical").is_dir():
            return parent
    return None


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def ensure_workspace(path: Path, unsafe_ok: bool = False) -> None:
    forbidden = [runtime_source_root()]
    repo_root = repo_root_guess()
    if repo_root is not None:
        forbidden.append(repo_root)
    home = Path.home()
    for relative in (".codex/skills", ".claude/skills", ".deepseek/skills", ".copilot/skills"):
        forbidden.append(home / relative)
    if not unsafe_ok:
        for base in forbidden:
            try:
                base = base.resolve()
            except OSError:
                continue
            if is_relative_to(path, base):
                raise SelectorError(
                    f"workspace {path} is inside managed source or agent skill directory; "
                    "choose another path or pass --unsafe-workspace-ok"
                )
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def update_status(run_dir: Path, stage: str, stage_status: str, **extra: Any) -> dict[str, Any]:
    current = read_json(run_dir / "run_status.json")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": current.get("run_id") or f"RUN-{int(time.time())}-{sha256_text(str(run_dir))[:8]}",
        "stage": stage,
        "stage_status": stage_status,
        "updated_at": now_iso(),
        "input_hashes": current.get("input_hashes", {}),
        "artifact_hashes": current.get("artifact_hashes", {}),
        "completed_steps": current.get("completed_steps", []),
        "failed_steps": current.get("failed_steps", []),
        "retry_after": None,
        "resume_policy": "preserve-existing-artifacts",
    }
    if stage_status == "ok" and stage not in payload["completed_steps"]:
        payload["completed_steps"].append(stage)
    if stage_status != "ok" and stage not in payload["failed_steps"]:
        payload["failed_steps"].append(stage)
    payload.update(extra)
    write_json(run_dir / "run_status.json", payload)
    return payload


def json_result(payload: dict[str, Any], exit_code: int = 0) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return exit_code


def title_words(text: str) -> set[str]:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "using",
        "study",
        "paper",
        "draft",
        "method",
        "results",
        "analysis",
    }
    return {w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", text.lower()) if w not in stop}


def redacted_path(path: Path) -> str:
    try:
        return str(path.expanduser().resolve().relative_to(Path.home()))
    except ValueError:
        return f"<PATH:{sha256_text(str(path.resolve()))[:12]}>"


def extract_bib_entries(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for match in re.finditer(r"@\w+\s*\{\s*([^,]+)\s*,(.*?)(?=\n@\w+\s*\{|\Z)", text, re.S):
        key = match.group(1).strip()
        body = match.group(2)
        fields: dict[str, str] = {}
        for field, value in re.findall(r"(\w+)\s*=\s*[\{\"](.+?)[\}\"]\s*,?", body, re.S):
            cleaned = re.sub(r"\s+", " ", value).strip()
            fields[field.lower()] = cleaned
        title = fields.get("title") or key
        entries.append(
            {
                "key": key,
                "raw": f"@entry{{{key}}}",
                "title": title,
                "authors": fields.get("author", ""),
                "year": fields.get("year", ""),
                "doi": normalize_doi(fields.get("doi", "")),
                "venue": fields.get("journal") or fields.get("booktitle") or fields.get("publisher") or "",
            }
        )
    return entries


def normalize_doi(value: str) -> str:
    value = value.strip().rstrip(".")
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value, flags=re.I)
    return value


def extract_line_references(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    lines = [line.strip() for line in text.splitlines()]
    for line in lines:
        if len(line) < 20:
            continue
        doi_match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", line, re.I)
        year_match = re.search(r"\b(19|20)\d{2}\b", line)
        looks_reference = bool(doi_match or year_match) and any(ch in line for ch in ".:")
        if not looks_reference:
            continue
        title = line
        if "." in line:
            parts = [part.strip() for part in line.split(".") if part.strip()]
            if len(parts) >= 2:
                title = max(parts, key=len)
        entries.append(
            {
                "key": slug(title)[:40],
                "raw": line[:500],
                "title": re.sub(r"\s+", " ", title)[:240],
                "authors": "",
                "year": year_match.group(0) if year_match else "",
                "doi": normalize_doi(doi_match.group(0)) if doi_match else "",
                "venue": infer_venue_from_citation_line(line),
            }
        )
    return entries


def infer_venue_from_citation_line(line: str) -> str:
    markers = ["In Proceedings of ", "Proceedings of ", "Journal of ", "Transactions on "]
    for marker in markers:
        if marker in line:
            tail = line.split(marker, 1)[1]
            return (marker.strip() + " " + re.split(r"[,.;]", tail, 1)[0]).strip()
    return ""


def dedupe_refs(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for entry in entries:
        key = entry.get("doi") or slug(entry.get("title", "")) or entry.get("key", "")
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result


def load_fixture_dir(args: argparse.Namespace) -> Path | None:
    fixture = getattr(args, "fixture_dir", None)
    if fixture:
        path = Path(fixture).expanduser().resolve()
        if not path.is_dir():
            raise SelectorError(f"fixture dir does not exist: {path}")
        return path
    return None


def load_fixture_jsonl(args: argparse.Namespace, name: str) -> list[dict[str, Any]]:
    fixture_dir = load_fixture_dir(args)
    if fixture_dir is None:
        return []
    return read_jsonl(fixture_dir / name)


def latest_privacy_guard_ok(run_dir: Path) -> bool:
    guards = [
        guard
        for guard in read_jsonl(run_dir / "guards.jsonl")
        if guard.get("guard_type") == "privacy_gate"
    ]
    return bool(guards and guards[-1].get("status") == "ok")


def allowed_providers(args: argparse.Namespace) -> set[str]:
    return set(getattr(args, "allow_provider", None) or [])


def ensure_network_allowed(run_dir: Path, args: argparse.Namespace) -> set[str]:
    if getattr(args, "offline", False):
        raise SelectorError("--offline and --allow-network cannot be combined")
    providers = allowed_providers(args)
    if not providers:
        raise SelectorError("--allow-network requires at least one explicit --allow-provider")
    unsupported = providers - NETWORK_PROVIDERS
    if unsupported:
        raise SelectorError(f"unsupported network provider(s): {', '.join(sorted(unsupported))}")
    if not latest_privacy_guard_ok(run_dir):
        raise SelectorError("network access requires a prior ok privacy-gate in this workspace")
    queries = read_jsonl(run_dir / "queries.jsonl")
    if not queries or any(query.get("redaction_status") != "redacted" for query in queries):
        raise SelectorError("network access requires redacted queries.jsonl")
    return providers


def request_caps(run_dir: Path) -> dict[str, int]:
    plan = read_json(run_dir / "selection_plan.json")
    caps = plan.get("request_caps", {}) if isinstance(plan.get("request_caps"), dict) else {}
    return {
        "max_requests": int(caps.get("max_requests", 25)),
        "timeout_seconds": int(caps.get("timeout_seconds", 20)),
        "max_response_bytes": int(caps.get("max_response_bytes", MAX_RESPONSE_BYTES)),
    }


def redacted_query_params(params: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in params.items():
        if key.lower() in {"search", "query", "title", "filter"}:
            redacted[key] = f"<redacted:{sha256_text(value)[:12]}>"
        else:
            redacted[key] = value
    return redacted


def build_selection_plan(draft: dict[str, Any] | None = None) -> dict[str, Any]:
    keywords = []
    if draft:
        keywords = draft.get("topic_keywords", [])[:12]
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_id": "PLAN-1",
        "created_at": now_iso(),
        "venue_type_constraints": ["journal", "conference"],
        "field_topic_keywords": keywords,
        "provider_capabilities_required": ["resolve_by_doi", "resolve_by_title", "venue_recent_by_source"],
        "request_caps": {
            "max_requests": 25,
            "timeout_seconds": 20,
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "max_hop": 1,
            "max_papers": 50,
        },
        "year_window": 5,
        "scorecard_criteria": [
            "venue_topic_fit",
            "comparator_pattern_fit",
            "scope_article_type_fit",
            "evidence_completeness",
            "presentation_discourse_alignment",
        ],
        "acceptance_chance_model": {
            "base_rate_fallback_interval": [0.05, 0.25],
            "calculation": "base_rate_interval times eligibility, venue_fit, submission_readiness, and evidence_confidence modifiers",
            "output": "heuristic_interval_not_prediction",
        },
        "unresolved_assumptions": [
            "offline bibliography overlap and placeholders are discovery-only until comparator evidence is available"
        ],
    }


def command_init(args: argparse.Namespace) -> int:
    run_dir = workspace(args)
    ensure_workspace(run_dir, args.unsafe_workspace_ok)
    draft_path = Path(args.draft).expanduser().resolve()
    if not draft_path.is_file():
        raise SelectorError(f"draft not found: {draft_path}")
    text = read_text(draft_path)
    reference_text = " ".join(entry.get("title", "") for entry in (extract_bib_entries(text) or extract_line_references(text)))
    words = sorted(title_words(reference_text), key=lambda w: (-reference_text.lower().count(w), w))[:20]
    draft = {
        "schema_version": SCHEMA_VERSION,
        "draft_id": "DRAFT-1",
        "draft_path": redacted_path(draft_path),
        "draft_hash": sha256_file(draft_path),
        "sensitivity_class": "unpublished",
        "redaction_status": "redacted",
        "artifact_visibility": "local-private",
        "retains_raw_text": bool(args.retain_draft_text),
        "topic_keywords": words,
        "created_at": now_iso(),
    }
    if args.retain_draft_text:
        draft["raw_text"] = text
    write_json(run_dir / "draft.json", draft)
    write_json(run_dir / "selection_plan.json", build_selection_plan(draft))
    update_status(
        run_dir,
        "init",
        "ok",
        input_hashes={"draft": draft["draft_hash"]},
    )
    return json_result({"status": "ok", "dir": str(run_dir), "draft_id": draft["draft_id"]})


def command_plan(args: argparse.Namespace) -> int:
    run_dir = workspace(args)
    ensure_workspace(run_dir, args.unsafe_workspace_ok)
    draft = read_json(run_dir / "draft.json")
    plan = build_selection_plan(draft)
    write_json(run_dir / "selection_plan.json", plan)
    update_status(run_dir, "plan", "ok")
    return json_result({"status": "ok", "path": str(run_dir / "selection_plan.json")})


def command_extract(args: argparse.Namespace) -> int:
    run_dir = workspace(args)
    draft = read_json(run_dir / "draft.json")
    draft_path = draft.get("draft_path", "")
    source_path = Path(args.draft).expanduser().resolve() if getattr(args, "draft", None) else None
    if source_path is None or not source_path.is_file():
        # Try the original path if it was relative to home and still exists.
        candidate = Path.home() / str(draft_path)
        source_path = candidate if candidate.is_file() else None
    if source_path is None:
        raise SelectorError("draft path is required for extraction when original source cannot be resolved")
    text = read_text(source_path)
    entries = dedupe_refs(extract_bib_entries(text) or extract_line_references(text))
    refs: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, 1):
        refs.append(
            {
                "schema_version": SCHEMA_VERSION,
                "reference_id": f"R{index}",
                "raw_citation": entry.get("raw", "")[:500],
                "title": entry.get("title", ""),
                "authors": entry.get("authors", ""),
                "year": entry.get("year", ""),
                "doi": entry.get("doi", ""),
                "venue_hint": entry.get("venue", ""),
                "provider_ids": {},
                "resolution_status": "unresolved",
                "candidate_work_ids": [],
                "selected_work_id": "",
                "resolution_reason": "not resolved yet",
            }
        )
    write_jsonl(run_dir / "references.jsonl", refs)
    update_status(run_dir, "extract", "ok")
    return json_result({"status": "ok", "references": len(refs), "path": str(run_dir / "references.jsonl")})


def command_privacy_gate(args: argparse.Namespace) -> int:
    run_dir = workspace(args)
    draft = read_json(run_dir / "draft.json")
    refs = read_jsonl(run_dir / "references.jsonl")
    queries: list[dict[str, Any]] = []
    unsafe: list[str] = []
    for index, ref in enumerate(refs, 1):
        title = ref.get("title", "")
        query = " ".join(sorted(title_words(title))[:8])
        query_id = f"Q{index}"
        raw = ref.get("raw_citation", "")
        if len(query.split()) > 12 or re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|\bAcknowledg(e)?ments?\b|\bTheorem\s+[A-Z]", raw, re.I):
            unsafe.append(query_id)
        queries.append(
            {
                "schema_version": SCHEMA_VERSION,
                "query_id": query_id,
                "purpose": "reference_resolution",
                "query_text": query,
                "source_reference_id": ref.get("reference_id", ""),
                "redaction_status": "redacted",
                "allow_network": bool(args.allow_network),
            }
        )
    guard = {
        "schema_version": SCHEMA_VERSION,
        "guard_id": "G-privacy",
        "guard_type": "privacy_gate",
        "status": "blocked" if unsafe and args.allow_network else "ok",
        "finding_count": len(unsafe),
        "unsafe_query_ids": unsafe,
        "created_at": now_iso(),
        "summary": "network blocked for unsafe queries" if unsafe and args.allow_network else "queries redacted",
    }
    write_jsonl(run_dir / "queries.jsonl", queries)
    write_jsonl(run_dir / "guards.jsonl", [*read_jsonl(run_dir / "guards.jsonl"), guard])
    update_status(run_dir, "privacy-gate", "ok" if guard["status"] == "ok" else "blocked")
    return json_result({"status": guard["status"], "unsafe_query_ids": unsafe}, 1 if guard["status"] == "blocked" else 0)


def provider_records(args: argparse.Namespace) -> list[dict[str, Any]]:
    allow = set(args.allow_provider or [])
    base = [
        ("openalex", ["resolve_by_doi", "resolve_by_title", "venue_recent_by_source", "citation_refs"]),
        ("crossref", ["resolve_by_doi", "resolve_by_title", "venue_recent_by_source"]),
        ("semantic-scholar", ["resolve_by_doi", "resolve_by_title", "citation_refs", "citation_citers"]),
        ("arxiv", ["resolve_by_title", "preprint_published_link"]),
        ("biorxiv", ["preprint_published_link"]),
        ("pubmed", ["biomed_related"]),
        ("unpaywall", ["oa_status"]),
    ]
    rows = []
    for name, capabilities in base:
        implemented = name in IMPLEMENTED_LIVE_PROVIDERS
        configured = implemented and (
            name not in {"semantic-scholar", "unpaywall"}
            or bool(os.environ.get("SEMANTIC_SCHOLAR_API_KEY" if name == "semantic-scholar" else "UNPAYWALL_EMAIL"))
        )
        allowed = bool(args.allow_network and allow and name in allow)
        if not implemented:
            provider_status = "unsupported"
        elif not configured:
            provider_status = "configured_missing"
        elif not allowed:
            provider_status = "skipped"
        else:
            provider_status = "ok"
        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "provider": name,
                "provider_status": provider_status,
                "capabilities": capabilities if implemented else [],
                "declared_capabilities": capabilities,
                "domain": SAFE_PROVIDER_DOMAINS.get(name, ""),
                "auth_configured": configured,
                "email_configured": bool(os.environ.get("UNPAYWALL_EMAIL")) if name == "unpaywall" else False,
                "network_allowed": bool(allowed and configured),
                "cache_ttl_days": 30,
                "checked_at": now_iso(),
            }
        )
    return rows


def command_providers(args: argparse.Namespace) -> int:
    run_dir = workspace(args)
    rows = provider_records(args)
    write_json(run_dir / "provider_status.json", {"schema_version": SCHEMA_VERSION, "providers": rows})
    update_status(run_dir, "providers", "ok")
    return json_result({"status": "ok", "providers": rows})


def normalize_work_from_ref(ref: dict[str, Any], index: int) -> dict[str, Any]:
    title = ref.get("title") or ref.get("raw_citation", "")
    venue = ref.get("venue_hint", "")
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_id": f"P{index}",
        "title": title,
        "year": ref.get("year", ""),
        "doi": ref.get("doi", ""),
        "authors": ref.get("authors", ""),
        "venue_name": venue,
        "provider_ids": {},
        "source_reference_ids": [ref.get("reference_id", "")],
        "resolution_confidence": 0.75 if ref.get("doi") else 0.55,
        "resolution_status": "resolved" if title else "unresolved",
    }


def source_record(
    source_id: str,
    provider: str,
    endpoint: str,
    params: dict[str, str],
    query_id: str,
    response_status: str = "ok",
) -> dict[str, Any]:
    redacted = redacted_query_params(params)
    cache_key = sha256_text(json.dumps({"provider": provider, "endpoint": endpoint, "params": params}, sort_keys=True))[:16]
    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": source_id,
        "provider": provider,
        "endpoint": endpoint,
        "query_id": query_id,
        "query": redacted,
        "query_hash": sha256_text(json.dumps(params, sort_keys=True)),
        "cache_key": cache_key,
        "retrieved_at": now_iso(),
        "current_as_of": now_iso(),
        "staleness_policy": "30d",
        "response_status": response_status,
    }


def live_json(
    provider: str,
    path: str,
    params: dict[str, str],
    timeout: int,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
) -> dict[str, Any]:
    domain = SAFE_PROVIDER_DOMAINS[provider]
    query = urllib.parse.urlencode(params)
    url = f"https://{domain}{path}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "ai-agents-skills submission-venue-selector"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(max_response_bytes + 1)
            if len(raw) > max_response_bytes:
                raise SelectorError(f"{provider} response exceeded {max_response_bytes} byte cap")
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SelectorError(f"{provider} HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise SelectorError(f"{provider} network failed: {exc.reason}") from exc


def command_resolve(args: argparse.Namespace) -> int:
    run_dir = workspace(args)
    refs = read_jsonl(run_dir / "references.jsonl")
    fixture_papers = load_fixture_jsonl(args, "papers.jsonl")
    papers = fixture_papers[:] if fixture_papers else []
    sources: list[dict[str, Any]] = read_jsonl(run_dir / "sources.jsonl")
    evidence: list[dict[str, Any]] = read_jsonl(run_dir / "evidence.jsonl")
    providers = ensure_network_allowed(run_dir, args) if args.allow_network else set()
    caps = request_caps(run_dir)
    request_count = 0
    if not papers:
        for index, ref in enumerate(refs, 1):
            paper = normalize_work_from_ref(ref, index)
            if "openalex" in providers:
                try:
                    if request_count >= caps["max_requests"]:
                        raise SelectorError("request cap reached before OpenAlex resolution")
                    params = {"search": paper["title"], "per_page": "1", "select": "id,doi,title,publication_year,primary_location"}
                    query_id = f"Q-live-{len(sources)+1}"
                    data = live_json(
                        "openalex",
                        "/works",
                        params,
                        min(int(args.timeout), caps["timeout_seconds"]),
                        caps["max_response_bytes"],
                    )
                    request_count += 1
                    source_id = f"S{len(sources)+1}"
                    sources.append(source_record(source_id, "openalex", "/works", params, query_id))
                    result = (data.get("results") or [{}])[0]
                    if result:
                        paper["provider_ids"]["openalex"] = result.get("id", "")
                        paper["title"] = result.get("title") or paper["title"]
                        paper["year"] = str(result.get("publication_year") or paper["year"])
                        paper["doi"] = normalize_doi(result.get("doi") or paper["doi"])
                        source = (result.get("primary_location") or {}).get("source") or {}
                        paper["venue_name"] = source.get("display_name") or paper["venue_name"]
                        paper["provider_ids"]["openalex_source_id"] = source.get("id", "")
                        paper["source_ids"] = sorted(set([*paper.get("source_ids", []), source_id]))
                        paper["resolution_confidence"] = 0.85
                except SelectorError as exc:
                    evidence.append(evidence_record(f"E{len(evidence)+1}", "provider_gap", [], [paper["paper_id"]], [], str(exc), 0.2))
            papers.append(paper)
    for paper in papers:
        evidence.append(
            evidence_record(
                f"E{len(evidence)+1}",
                "paper_resolution",
                [],
                [paper["paper_id"]],
                [],
                f"Resolved reference as paper: {paper.get('title', '')}",
                paper.get("resolution_confidence", 0.5),
            )
        )
    write_jsonl(run_dir / "papers.jsonl", papers)
    write_jsonl(run_dir / "sources.jsonl", sources)
    write_jsonl(run_dir / "evidence.jsonl", evidence)
    update_status(run_dir, "resolve", "ok")
    return json_result({"status": "ok", "papers": len(papers), "network_used": bool(providers), "requests": request_count})


def evidence_record(
    evidence_id: str,
    evidence_type: str,
    source_ids: list[str],
    paper_ids: list[str],
    venue_ids: list[str],
    summary: str,
    confidence: float,
    claim_ids: list[str] | None = None,
    provider: str = "runtime",
    query_id: str = "",
    artifact_ref: str = "",
    limitations: list[str] | None = None,
    evidence_level: str = "metadata_only",
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_id": evidence_id,
        "evidence_type": evidence_type,
        "source_ids": source_ids,
        "paper_ids": paper_ids,
        "venue_ids": venue_ids,
        "claim_ids": claim_ids or [],
        "provider": provider,
        "query_id": query_id,
        "artifact_ref": artifact_ref,
        "summary": summary,
        "created_at": now_iso(),
        "inspection_status": "inspected",
        "confidence": confidence,
        "limitations": limitations or [],
        "evidence_level": evidence_level,
    }


def command_expand(args: argparse.Namespace) -> int:
    run_dir = workspace(args)
    papers = read_jsonl(run_dir / "papers.jsonl")
    evidence = read_jsonl(run_dir / "evidence.jsonl")
    expanded = load_fixture_jsonl(args, "expanded_papers.jsonl")
    if expanded:
        for index, paper in enumerate(expanded, 1):
            paper.setdefault("schema_version", SCHEMA_VERSION)
            paper.setdefault("paper_id", f"PX{index}")
            paper.setdefault("resolution_status", "resolved")
            paper.setdefault("edge_type", "fixture_provider_edge")
            paper.setdefault("exclusion_reason", "")
            paper.setdefault("provider_ids", {})
            paper.setdefault("source_reference_ids", [])
    else:
        # Offline expansion is intentionally discovery-only. Do not create
        # placeholder papers because downstream ranking must not treat them as
        # comparator evidence.
        expanded = []
    if expanded:
        evidence.append(
            evidence_record(
                f"E{len(evidence)+1}",
                "citation_expansion",
                sorted({source_id for p in expanded for source_id in p.get("source_ids", [])}),
                [p["paper_id"] for p in expanded],
                [],
                "Fixture-backed expansion recorded provider-like citation edges.",
                0.7,
                provider="fixture",
                artifact_ref="expanded_papers.jsonl",
            )
        )
    write_jsonl(run_dir / "papers.jsonl", [*papers, *expanded])
    write_jsonl(run_dir / "evidence.jsonl", evidence)
    update_status(run_dir, "expand", "ok")
    return json_result({"status": "ok", "expanded_papers": len(expanded), "max_hop": args.max_hop})


def venue_type(name: str) -> str:
    lower = name.lower()
    if "proceedings" in lower or "conference" in lower or "symposium" in lower:
        return "conference"
    if "arxiv" in lower or "biorxiv" in lower or "medrxiv" in lower:
        return "preprint-server"
    if not name:
        return "unknown"
    return "journal"


def command_venues(args: argparse.Namespace) -> int:
    run_dir = workspace(args)
    papers = read_jsonl(run_dir / "papers.jsonl")
    evidence = read_jsonl(run_dir / "evidence.jsonl")
    by_key: dict[str, dict[str, Any]] = {}
    for paper in papers:
        name = paper.get("venue_name", "").strip()
        if not name:
            continue
        key = slug(name)
        venue_id = f"V{len(by_key)+1}" if key not in by_key else by_key[key]["venue_id"]
        by_key.setdefault(
            key,
            {
                "schema_version": SCHEMA_VERSION,
                "venue_id": venue_id,
                "canonical_name": name,
                "venue_type": venue_type(name),
                "venue_series": name if venue_type(name) == "conference" else "",
                "venue_instance": "",
                "submission_cycle": "unknown",
                "aliases": sorted({name}),
                "issn": [],
                "eissn": [],
                "issn_l": "",
                "openalex_source_id": "",
                "crossref_member": "",
                "s2_publication_venue_id": "",
                "nlm_ta": "",
                "publisher_or_org": "",
                "sponsor": "",
                "homepage_url": "",
                "scope_text": "",
                "submission_url": "",
                "current_as_of": now_iso(),
                "eligibility_status": "eligible" if venue_type(name) in {"journal", "conference"} else "excluded",
                "exclusion_reason": "" if venue_type(name) in {"journal", "conference"} else "not a submission venue",
                "classification_evidence_ids": [],
                "provenance_evidence_ids": [],
                "paper_ids": [],
            },
        )
        by_key[key]["paper_ids"].append(paper.get("paper_id", ""))
        openalex_source_id = paper.get("provider_ids", {}).get("openalex_source_id", "")
        if openalex_source_id and not by_key[key].get("openalex_source_id"):
            by_key[key]["openalex_source_id"] = openalex_source_id
    venues = list(by_key.values())
    profiles: list[dict[str, Any]] = []
    for venue in venues:
        ev_id = f"E{len(evidence)+1}"
        venue["classification_evidence_ids"].append(ev_id)
        venue["provenance_evidence_ids"].append(ev_id)
        evidence.append(
            evidence_record(
                ev_id,
                "venue_identity",
                [],
                venue.get("paper_ids", []),
                [venue["venue_id"]],
                f"Venue derived from resolved paper metadata: {venue['canonical_name']}",
                0.65 if venue["eligibility_status"] == "eligible" else 0.4,
            )
        )
        profiles.append(
            {
                "schema_version": SCHEMA_VERSION,
                "venue_profile_id": f"VP{len(profiles)+1}",
                "venue_id": venue["venue_id"],
                "aims_scope": venue.get("scope_text", "") or "unknown; requires provider-backed scope evidence",
                "article_types": ["research-article"] if venue["venue_type"] == "journal" else ["conference-paper"],
                "review_model": "unknown",
                "deadlines_or_frequency": "unknown",
                "apc_oa_policy": "unknown",
                "indexing": [],
                "length_constraints": "unknown",
                "audience": "unknown; requires provider-backed scope evidence",
                "exclusion_criteria": [venue["exclusion_reason"]] if venue.get("exclusion_reason") else [],
                "recent_sample_method": "pending-comparator-evidence",
                "evidence_ids": [ev_id],
            }
        )
    write_jsonl(run_dir / "venues.jsonl", venues)
    write_jsonl(run_dir / "venue_profiles.jsonl", profiles)
    write_jsonl(run_dir / "evidence.jsonl", evidence)
    update_status(run_dir, "venues", "ok")
    return json_result({"status": "ok", "venues": len(venues)})


def venue_lookup(venues: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for venue in venues:
        result[venue.get("venue_id", "")] = venue
        result[slug(venue.get("canonical_name", ""))] = venue
        for alias in venue.get("aliases", []):
            result[slug(str(alias))] = venue
    return {key: value for key, value in result.items() if key}


def is_placeholder_recent(row: dict[str, Any]) -> bool:
    return (
        row.get("provider") == "offline"
        or row.get("sampling_method") == "bibliography-overlap"
        or str(row.get("title", "")).startswith("Recent related sample")
        or not str(row.get("year", "")).strip()
    )


def is_valid_comparator_recent(row: dict[str, Any]) -> bool:
    if is_placeholder_recent(row):
        return False
    if row.get("evidence_level") not in COMPARATOR_EVIDENCE_LEVELS:
        return False
    if str(row.get("exclusion_status") or "included") not in {"included", "labeled"}:
        return False
    for key in ("provider_work_id", "venue_source_id", "query_id"):
        if not str(row.get(key, "")).strip():
            return False
    for key in ("source_ids", "evidence_ids"):
        if not row.get(key):
            return False
    for key in ("article_type", "topic_distance_rationale", "inspection_scope"):
        if not str(row.get(key, "")).strip():
            return False
    try:
        return int(str(row.get("year", "0"))) > 0
    except ValueError:
        return False


def is_ready_comparator_recent(row: dict[str, Any]) -> bool:
    return is_valid_comparator_recent(row) and row.get("evidence_level") in READY_COMPARATOR_EVIDENCE_LEVELS


def evidence_level_rank(row: dict[str, Any]) -> int:
    return {"metadata_only": 0, "abstract_inspected": 1, "full_text_inspected": 2}.get(str(row.get("evidence_level")), -1)


def normalize_recent_fixture_rows(
    run_dir: Path,
    fixture_rows: list[dict[str, Any]],
    venues: list[dict[str, Any]],
    per_venue: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    lookup = venue_lookup(venues)
    existing_sources = read_jsonl(run_dir / "sources.jsonl")
    evidence = read_jsonl(run_dir / "evidence.jsonl")
    recent: list[dict[str, Any]] = []
    per_venue_counts: dict[str, int] = {}
    for row in fixture_rows:
        venue_key = str(row.get("venue_id") or slug(str(row.get("venue_name") or row.get("canonical_name") or "")))
        venue = lookup.get(venue_key)
        if venue is None:
            continue
        venue_id = venue["venue_id"]
        if per_venue_counts.get(venue_id, 0) >= per_venue:
            continue
        provider = str(row.get("provider") or "fixture")
        params = {
            "venue": venue.get("canonical_name", ""),
            "title": str(row.get("title", "")),
            "year": str(row.get("year", "")),
        }
        query_id = str(row.get("query_id") or f"Q-fixture-{len(recent)+1}")
        source_ids = list(row.get("source_ids") or [])
        if not source_ids:
            source_id = f"S{len(existing_sources)+1}"
            existing_sources.append(source_record(source_id, provider, "fixture://recent_papers", params, query_id))
            source_ids = [source_id]
        evidence_id = f"E{len(evidence)+1}"
        normalized = {
            "schema_version": SCHEMA_VERSION,
            "recent_paper_id": str(row.get("recent_paper_id") or f"RP{len(recent)+1}"),
            "venue_id": venue_id,
            "title": str(row.get("title", "")),
            "year": str(row.get("year", "")),
            "doi": normalize_doi(str(row.get("doi", ""))),
            "provider": provider,
            "provider_work_id": str(row.get("provider_work_id") or row.get("work_id") or f"fixture:{sha256_text(str(row.get('title', '')))[:12]}"),
            "venue_source_id": str(row.get("venue_source_id") or venue.get("openalex_source_id") or f"fixture:{slug(venue.get('canonical_name', ''))}"),
            "source_ids": source_ids,
            "query_id": query_id,
            "evidence_ids": [evidence_id],
            "sampling_method": str(row.get("sampling_method") or "fixture-provider-cache"),
            "year_window": int(row.get("year_window") or read_json(run_dir / "selection_plan.json").get("year_window", 5) or 5),
            "total_hits": int(row.get("total_hits") or 1),
            "truncated": bool(row.get("truncated", False)),
            "evidence_level": str(row.get("evidence_level") or "metadata_only"),
            "abstract_available": bool(row.get("abstract_available", False)),
            "full_text_status": str(row.get("full_text_status") or "not_requested"),
            "article_type": str(row.get("article_type") or "research-article"),
            "exclusion_status": str(row.get("exclusion_status") or "included"),
            "exclusion_reason": str(row.get("exclusion_reason") or ""),
            "topic_distance_rationale": str(
                row.get("topic_distance_rationale")
                or "fixture comparator supplied for same or adjacent manuscript topic"
            ),
            "inspection_scope": str(row.get("inspection_scope") or row.get("evidence_level") or "metadata_only"),
            "similarity_method": str(row.get("similarity_method") or "fixture-topic-overlap"),
            "topic_similarity": float(row.get("topic_similarity", 0.0)),
            "matched_terms": list(row.get("matched_terms") or []),
            "limitations": list(row.get("limitations") or []),
            "current_as_of": str(row.get("current_as_of") or now_iso()),
        }
        evidence.append(
            evidence_record(
                evidence_id,
                "comparator_paper",
                source_ids,
                [normalized["recent_paper_id"]],
                [venue_id],
                f"Comparator paper for {venue.get('canonical_name', venue_id)}: {normalized['title']}",
                0.8,
                provider=provider,
                query_id=query_id,
                artifact_ref="recent_papers.jsonl",
                limitations=normalized["limitations"],
                evidence_level=normalized["evidence_level"],
            )
        )
        recent.append(normalized)
        per_venue_counts[venue_id] = per_venue_counts.get(venue_id, 0) + 1
    return recent, existing_sources, evidence


def command_recent(args: argparse.Namespace) -> int:
    run_dir = workspace(args)
    venues = read_jsonl(run_dir / "venues.jsonl")
    fixture_rows = load_fixture_jsonl(args, "recent_papers.jsonl")
    recent: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = read_jsonl(run_dir / "sources.jsonl")
    evidence: list[dict[str, Any]] = read_jsonl(run_dir / "evidence.jsonl")
    if fixture_rows:
        recent, sources, evidence = normalize_recent_fixture_rows(run_dir, fixture_rows, venues, int(args.per_venue))
    elif args.allow_network:
        providers = ensure_network_allowed(run_dir, args)
        if "openalex" not in providers:
            raise SelectorError("recent comparator collection currently requires --allow-provider openalex or fixture evidence")
        caps = request_caps(run_dir)
        request_count = 0
        for venue in venues:
            source_id_value = str(venue.get("openalex_source_id", ""))
            if not source_id_value:
                continue
            if request_count >= caps["max_requests"]:
                break
            from_year = max(1900, datetime.now(timezone.utc).year - int(args.years))
            params = {
                "filter": f"primary_location.source.id:{source_id_value},from_publication_date:{from_year}-01-01",
                "per_page": str(min(int(args.per_venue), 25)),
                "sort": "publication_date:desc",
                "select": "id,doi,title,publication_year,abstract_inverted_index",
            }
            query_id = f"Q-recent-{len(sources)+1}"
            data = live_json(
                "openalex",
                "/works",
                params,
                min(int(args.timeout), caps["timeout_seconds"]),
                caps["max_response_bytes"],
            )
            request_count += 1
            source_id = f"S{len(sources)+1}"
            sources.append(source_record(source_id, "openalex", "/works", params, query_id))
            for result in (data.get("results") or [])[: int(args.per_venue)]:
                evidence_id = f"E{len(evidence)+1}"
                title = result.get("title") or ""
                year = str(result.get("publication_year") or "")
                row = {
                    "schema_version": SCHEMA_VERSION,
                    "recent_paper_id": f"RP{len(recent)+1}",
                    "venue_id": venue["venue_id"],
                    "title": title,
                    "year": year,
                    "doi": normalize_doi(result.get("doi") or ""),
                    "provider": "openalex",
                    "provider_work_id": result.get("id", ""),
                    "venue_source_id": source_id_value,
                    "source_ids": [source_id],
                    "query_id": query_id,
                    "evidence_ids": [evidence_id],
                    "sampling_method": "openalex-source-recent",
                    "year_window": int(args.years),
                    "total_hits": int((data.get("meta") or {}).get("count") or len(data.get("results") or [])),
                    "truncated": bool((data.get("meta") or {}).get("count", 0) > int(args.per_venue)),
                    "evidence_level": "abstract_inspected" if result.get("abstract_inverted_index") else "metadata_only",
                    "abstract_available": bool(result.get("abstract_inverted_index")),
                    "full_text_status": "not_requested",
                    "article_type": "research-article",
                    "exclusion_status": "included",
                    "exclusion_reason": "",
                    "topic_distance_rationale": "recent work from same provider venue source; topic similarity not computed",
                    "inspection_scope": "abstract_inspected" if result.get("abstract_inverted_index") else "metadata_only",
                    "similarity_method": "provider-source-recent",
                    "topic_similarity": 0.0,
                    "matched_terms": [],
                    "limitations": ["topic similarity not computed for live provider result"],
                    "current_as_of": now_iso(),
                }
                evidence.append(
                    evidence_record(
                        evidence_id,
                        "comparator_paper",
                        [source_id],
                        [row["recent_paper_id"]],
                        [venue["venue_id"]],
                        f"OpenAlex recent comparator paper for {venue['canonical_name']}: {title}",
                        0.75,
                        provider="openalex",
                        query_id=query_id,
                        artifact_ref="recent_papers.jsonl",
                        limitations=row["limitations"],
                        evidence_level=row["evidence_level"],
                    )
                )
                recent.append(row)
    write_jsonl(run_dir / "recent_papers.jsonl", recent)
    write_jsonl(run_dir / "sources.jsonl", sources)
    write_jsonl(run_dir / "evidence.jsonl", evidence)
    update_status(run_dir, "recent", "ok")
    return json_result(
        {
            "status": "ok",
            "recent_papers": len(recent),
            "valid_comparator_papers": sum(1 for row in recent if is_valid_comparator_recent(row)),
        }
    )


def ordinal_score(value: float) -> int:
    if value <= 0:
        return 0
    if value < 0.35:
        return 1
    if value < 0.65:
        return 2
    if value < 0.85:
        return 3
    return 4


def fit_band(eligible: bool, countable_count: int, ready_count: int, ordinal_values: list[int]) -> str:
    if not eligible:
        return "not-ready/excluded"
    if ready_count >= 3:
        minimum = min(ordinal_values) if ordinal_values else 0
        return "strong fit" if minimum >= 3 else "plausible fit"
    if countable_count >= 3:
        return "evidence-limited"
    return "not-ready/excluded"


def score_support_status(band: str) -> str:
    if band in {"strong fit", "plausible fit"}:
        return "supported"
    if band == "evidence-limited":
        return "caveated"
    return "unsupported"


def interval_product(intervals: list[tuple[float, float]]) -> tuple[float, float]:
    low = 1.0
    high = 1.0
    for item_low, item_high in intervals:
        low *= item_low
        high *= item_high
    return max(0.0, min(low, 1.0)), max(0.0, min(high, 1.0))


def percent_interval(interval: tuple[float, float]) -> str:
    low, high = interval
    return f"{round(low * 100, 1)}-{round(high * 100, 1)}%"


def modifier_for_band(band: str) -> tuple[float, float]:
    return {
        "strong fit": (0.90, 1.15),
        "plausible fit": (0.75, 1.05),
        "evidence-limited": (0.40, 0.80),
        "not-ready/excluded": (0.00, 0.20),
    }.get(band, (0.20, 0.60))


def confidence_for_chance(source_class: str, band: str, ready_count: int, countable_count: int) -> str:
    if source_class == "fallback-heuristic" or band in {"evidence-limited", "not-ready/excluded"}:
        return "low"
    if ready_count >= 3 and countable_count >= 5:
        return "medium"
    return "low"


def normalize_base_rate_sources(
    run_dir: Path,
    venues: list[dict[str, Any]],
    fixture_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    lookup = venue_lookup(venues)
    rows_by_venue: dict[str, dict[str, Any]] = {}
    for row in fixture_rows:
        venue_key = str(row.get("venue_id") or slug(str(row.get("venue_name") or row.get("canonical_name") or "")))
        venue = lookup.get(venue_key)
        if venue is None:
            continue
        low = float(row.get("base_rate_low", row.get("rate_low", 0.05)))
        high = float(row.get("base_rate_high", row.get("rate_high", 0.25)))
        rows_by_venue[venue["venue_id"]] = {
            "schema_version": SCHEMA_VERSION,
            "base_rate_source_id": str(row.get("base_rate_source_id") or f"BR{len(rows_by_venue)+1}"),
            "venue_id": venue["venue_id"],
            "source_class": str(row.get("source_class") or "configured-prior"),
            "source": str(row.get("source") or "fixture base-rate source"),
            "rate_interval_low": max(0.0, min(low, high)),
            "rate_interval_high": min(1.0, max(low, high)),
            "current_as_of": str(row.get("current_as_of") or now_iso()),
            "limitations": list(row.get("limitations") or []),
        }
    plan = read_json(run_dir / "selection_plan.json")
    fallback = plan.get("acceptance_chance_model", {}).get("base_rate_fallback_interval", [0.05, 0.25])
    result: list[dict[str, Any]] = []
    for venue in venues:
        row = rows_by_venue.get(venue["venue_id"])
        if row is None:
            row = {
                "schema_version": SCHEMA_VERSION,
                "base_rate_source_id": f"BR{len(result)+1}",
                "venue_id": venue["venue_id"],
                "source_class": "fallback-heuristic",
                "source": "No journal-specific acceptance-rate source found; using broad configured fallback interval",
                "rate_interval_low": float(fallback[0]),
                "rate_interval_high": float(fallback[1]),
                "current_as_of": now_iso(),
                "limitations": ["not journal-specific", "low-confidence heuristic"],
            }
        result.append(row)
    return result


def build_chance_estimates(
    run_dir: Path,
    venues: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    base_sources: list[dict[str, Any]],
    countable_by_venue: dict[str, list[dict[str, Any]]],
    ready_by_venue: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    score_by_venue = {score["venue_id"]: score for score in scores}
    base_by_venue = {row["venue_id"]: row for row in base_sources}
    estimates: list[dict[str, Any]] = []
    for venue in venues:
        venue_id = venue["venue_id"]
        score = score_by_venue.get(venue_id, {})
        band = str(score.get("fit_band") or "not-ready/excluded")
        base = base_by_venue[venue_id]
        eligible = venue.get("eligibility_status") == "eligible"
        ready_count = len(ready_by_venue.get(venue_id, []))
        countable_count = len(countable_by_venue.get(venue_id, []))
        eligibility_modifier = (0.70, 1.00) if eligible else (0.00, 0.05)
        venue_fit_modifier = modifier_for_band(band)
        if ready_count >= 3:
            submission_modifier = (0.65, 0.95)
        elif countable_count >= 3:
            submission_modifier = (0.35, 0.70)
        else:
            submission_modifier = (0.10, 0.45)
        source_class = str(base.get("source_class", "fallback-heuristic"))
        evidence_modifier = (0.55, 0.85) if source_class == "fallback-heuristic" or ready_count < 3 else (0.80, 1.00)
        final = interval_product(
            [
                (float(base["rate_interval_low"]), float(base["rate_interval_high"])),
                eligibility_modifier,
                venue_fit_modifier,
                submission_modifier,
                evidence_modifier,
            ]
        )
        caveats = list(base.get("limitations") or [])
        if source_class == "fallback-heuristic":
            caveats.append("no official journal acceptance-rate source found")
        if ready_count < 3:
            caveats.append("fewer than 3 abstract/full-text comparator records")
        if countable_count < 5:
            caveats.append("fewer than 5 comparator candidates")
        if not eligible:
            caveats.append(venue.get("exclusion_reason") or "venue is not eligible for this manuscript type")
        estimates.append(
            {
                "schema_version": SCHEMA_VERSION,
                "chance_estimate_id": f"ACE{len(estimates)+1}",
                "venue_id": venue_id,
                "fit_band": band,
                "calculation_class": (
                    "official-rate-adjusted"
                    if source_class == "official"
                    else "field-prior-adjusted"
                    if source_class in {"field-prior", "configured-prior", "publisher-prior"}
                    else "fallback-heuristic"
                ),
                "base_rate_source_id": base["base_rate_source_id"],
                "base_rate_interval": [base["rate_interval_low"], base["rate_interval_high"]],
                "eligibility_modifier_interval": list(eligibility_modifier),
                "venue_fit_modifier_interval": list(venue_fit_modifier),
                "submission_readiness_modifier_interval": list(submission_modifier),
                "evidence_confidence_modifier_interval": list(evidence_modifier),
                "final_interval": [round(final[0], 4), round(final[1], 4)],
                "display_interval": percent_interval(final),
                "confidence": confidence_for_chance(source_class, band, ready_count, countable_count),
                "caveats": sorted(set(caveats)),
                "calculation_note": "Heuristic interval, not a prediction or guarantee of acceptance.",
                "created_at": now_iso(),
            }
        )
    return estimates


def command_score(args: argparse.Namespace) -> int:
    run_dir = workspace(args)
    venues = read_jsonl(run_dir / "venues.jsonl")
    recent = read_jsonl(run_dir / "recent_papers.jsonl")
    evidence = read_jsonl(run_dir / "evidence.jsonl")
    countable_recent_by_venue: dict[str, list[dict[str, Any]]] = {}
    ready_recent_by_venue: dict[str, list[dict[str, Any]]] = {}
    for item in recent:
        if is_valid_comparator_recent(item):
            countable_recent_by_venue.setdefault(item["venue_id"], []).append(item)
        if is_ready_comparator_recent(item):
            ready_recent_by_venue.setdefault(item["venue_id"], []).append(item)
    scores: list[dict[str, Any]] = []
    scorecards: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    for venue in venues:
        overlap = len(venue.get("paper_ids", []))
        countable_items = countable_recent_by_venue.get(venue["venue_id"], [])
        ready_items = ready_recent_by_venue.get(venue["venue_id"], [])
        countable_count = len(countable_items)
        ready_count = len(ready_items)
        eligible = venue.get("eligibility_status") == "eligible"
        venue_evidence_ids = [ev["evidence_id"] for ev in evidence if venue["venue_id"] in ev.get("venue_ids", [])]
        recent_evidence_ids = sorted({ev_id for item in countable_items for ev_id in item.get("evidence_ids", [])})
        ready_evidence_ids = sorted({ev_id for item in ready_items for ev_id in item.get("evidence_ids", [])})
        bibliography_evidence_ids = venue.get("classification_evidence_ids", []) or venue_evidence_ids
        scope_evidence_ids = venue.get("provenance_evidence_ids", []) or venue_evidence_ids
        venue_topic_score = ordinal_score(min(overlap, 5) / 5)
        comparator_score = ordinal_score(min(ready_count, 3) / 3 if ready_count else min(countable_count, 3) / 6)
        scope_score = 3 if eligible else 0
        evidence_score = ordinal_score(min(len(set(venue_evidence_ids + recent_evidence_ids)), 5) / 5)
        ordinal_values = [venue_topic_score, comparator_score, scope_score, evidence_score]
        band = fit_band(eligible, countable_count, ready_count, ordinal_values)
        support_status = score_support_status(band)
        criteria = [
            {
                "criterion_id": "venue_topic_fit",
                "raw_score": overlap,
                "ordinal_score": venue_topic_score,
                "anchor": "0=no topic signal, 4=multiple bibliography or comparator topic signals",
                "evidence_ids": bibliography_evidence_ids,
            },
            {
                "criterion_id": "comparator_pattern_fit",
                "raw_score": ready_count,
                "ordinal_score": comparator_score,
                "anchor": "0=no countable comparators, 4=at least 3 abstract/full-text comparators",
                "evidence_ids": recent_evidence_ids,
            },
            {
                "criterion_id": "scope_article_type_fit",
                "raw_score": 1 if eligible else 0,
                "ordinal_score": scope_score,
                "anchor": "0=excluded or wrong type, 3=eligible from current venue metadata, 4=verified current journal policy",
                "evidence_ids": scope_evidence_ids,
            },
            {
                "criterion_id": "evidence_completeness",
                "raw_score": len(set(venue_evidence_ids + recent_evidence_ids)),
                "ordinal_score": evidence_score,
                "anchor": "0=no evidence IDs, 4=well-provenanced venue and comparator evidence",
                "evidence_ids": sorted(set(venue_evidence_ids + recent_evidence_ids)),
            },
            {
                "criterion_id": "presentation_discourse_alignment",
                "raw_score": "not_scored",
                "ordinal_score": None,
                "anchor": "not_scored until draft and comparator full text are inspected",
                "evidence_ids": [],
            },
        ]
        normalized = round(sum(value for value in ordinal_values if value is not None) / (4 * len(ordinal_values)), 4)
        confidence = 0.3 + min(0.5, 0.05 * overlap + 0.08 * ready_count + 0.03 * countable_count)
        evidence_ids = sorted({ev_id for criterion in criteria for ev_id in criterion.get("evidence_ids", [])})
        score_id = f"SC{len(scores)+1}"
        scorecard_id = f"SCARD{len(scorecards)+1}"
        claim_id = f"C{len(claims)+1}"
        rank_band_order = {"strong fit": 0, "plausible fit": 1, "evidence-limited": 2, "not-ready/excluded": 3}
        scorecard = {
            "schema_version": SCHEMA_VERSION,
            "scorecard_id": scorecard_id,
            "venue_id": venue["venue_id"],
            "rubric_version": "venue-fit.ordinal.v2",
            "fit_band": band,
            "support_status": support_status,
            "countable_comparator_count": countable_count,
            "ready_comparator_count": ready_count,
            "criteria": criteria,
            "risk_flags": [
                {
                    "risk": "insufficient_ready_comparators",
                    "severity": "major" if ready_count < 3 and eligible else "minor",
                    "applies": ready_count < 3,
                },
                {
                    "risk": "metadata_only_evidence",
                    "severity": "major",
                    "applies": countable_count > 0 and ready_count == 0,
                },
            ],
            "evidence_ids": evidence_ids,
            "confidence": round(confidence, 3),
            "rank_band_order": rank_band_order.get(band, 99),
            "dominance_order": venue["canonical_name"].lower(),
        }
        scorecards.append(scorecard)
        scores.append(
            {
                "schema_version": SCHEMA_VERSION,
                "score_id": score_id,
                "venue_id": venue["venue_id"],
                "rubric_version": "venue-fit.ordinal.v2",
                "hard_gates": [{"gate": "eligible_submission_venue", "passed": eligible}],
                "scorecard_id": scorecard_id,
                "fit_band": band,
                "support_status": support_status,
                "criteria": criteria,
                "raw_score": normalized,
                "normalized_score": normalized,
                "evidence_ids": evidence_ids,
                "missing_data_policy": "downgrade-confidence",
                "confidence": round(confidence, 3),
                "sensitivity_result": "banded" if support_status != "supported" else "stable-band",
                "tie_breaker": venue["canonical_name"].lower(),
                "rationale": (
                    f"{venue['canonical_name']} has {overlap} bibliography overlaps, {countable_count} "
                    f"countable comparator papers, and {ready_count} abstract/full-text comparator papers."
                ),
                "claim_ids": [claim_id],
            }
        )
        claims.append(
            {
                "schema_version": SCHEMA_VERSION,
                "claim_id": claim_id,
                "claim_type": "venue_fit",
                "claim_scope": "venue_fit",
                "venue_id": venue["venue_id"],
                "score_id": score_id,
                "text": (
                    f"{venue['canonical_name']} is a {band} venue-fit candidate."
                    if support_status == "supported"
                    else f"{venue['canonical_name']} is {band}; evidence gaps prevent a final venue-fit claim."
                ),
                "evidence_ids": evidence_ids,
                "support_status": support_status,
            }
        )
    scores.sort(key=lambda row: ({"strong fit": 0, "plausible fit": 1, "evidence-limited": 2, "not-ready/excluded": 3}.get(row["fit_band"], 99), row["tie_breaker"]))
    scorecards.sort(key=lambda row: (row["rank_band_order"], row["dominance_order"]))
    base_sources = normalize_base_rate_sources(run_dir, venues, load_fixture_jsonl(args, "base_rate_sources.jsonl"))
    chance_estimates = build_chance_estimates(run_dir, venues, scores, base_sources, countable_recent_by_venue, ready_recent_by_venue)
    write_jsonl(run_dir / "scores.jsonl", scores)
    write_jsonl(run_dir / "scorecards.jsonl", scorecards)
    write_jsonl(run_dir / "base_rate_sources.jsonl", base_sources)
    write_jsonl(run_dir / "chance_estimates.jsonl", chance_estimates)
    write_jsonl(run_dir / "claims.jsonl", claims)
    update_status(run_dir, "score", "ok")
    return json_result({"status": "ok", "scores": len(scores), "chance_estimates": len(chance_estimates)})


def delivery_status(run_dir: Path) -> tuple[str, list[str]]:
    reasons: list[str] = []
    refs = read_jsonl(run_dir / "references.jsonl")
    papers = read_jsonl(run_dir / "papers.jsonl")
    venues = read_jsonl(run_dir / "venues.jsonl")
    claims = read_jsonl(run_dir / "claims.jsonl")
    guards = read_jsonl(run_dir / "guards.jsonl")
    scores = read_jsonl(run_dir / "scores.jsonl")
    recent = read_jsonl(run_dir / "recent_papers.jsonl")
    countable_recent_venues = {row.get("venue_id") for row in recent if is_valid_comparator_recent(row)}
    ready_recent_venues = {row.get("venue_id") for row in recent if is_ready_comparator_recent(row)}
    if not refs:
        reasons.append("no references extracted")
    if not papers:
        reasons.append("no papers resolved")
    if not venues:
        reasons.append("no candidate venues")
    eligible_score_count = sum(1 for score in scores if score.get("fit_band") != "not-ready/excluded")
    supported_score_count = sum(1 for score in scores if score.get("support_status") == "supported")
    caveated_score_count = sum(1 for score in scores if score.get("support_status") == "caveated")
    if scores and any(score.get("venue_id") not in countable_recent_venues and score.get("fit_band") != "not-ready/excluded" for score in scores):
        reasons.append("missing comparator-paper evidence for one or more ranked venues")
    if scores and not supported_score_count and not caveated_score_count:
        reasons.append("no supported or caveated venue-fit candidates")
    if any(claim.get("support_status") == "unsupported" and claim.get("claim_scope") != "venue_fit" for claim in claims):
        reasons.append("unsupported claims remain")
    if any(guard.get("status") == "blocked" for guard in guards):
        reasons.append("privacy guard blocked")
    chance_estimates = read_jsonl(run_dir / "chance_estimates.jsonl")
    if scores and len(chance_estimates) < len(venues):
        reasons.append("missing acceptance-chance estimates for one or more venues")
    if not reasons and any(p.get("resolution_confidence", 0) < 0.6 for p in papers):
        return DELIVERY_CAVEATS, ["some paper resolutions are low confidence"]
    if reasons:
        hard = {
            "no references extracted",
            "no candidate venues",
            "privacy guard blocked",
            "unsupported claims remain",
            "missing comparator-paper evidence for one or more ranked venues",
            "no supported or caveated venue-fit candidates",
            "missing acceptance-chance estimates for one or more venues",
        }
        return (DELIVERY_NOT_READY if hard.intersection(reasons) else DELIVERY_CAVEATS), reasons
    if scores and not supported_score_count and caveated_score_count:
        return DELIVERY_CAVEATS, ["only evidence-limited venue-fit candidates are available"]
    if scores and any(score.get("venue_id") not in ready_recent_venues and score.get("support_status") == "caveated" for score in scores):
        return DELIVERY_CAVEATS, ["some venue-fit candidates lack abstract/full-text comparator evidence"]
    return DELIVERY_READY, []


def command_report(args: argparse.Namespace) -> int:
    run_dir = workspace(args)
    scores = read_jsonl(run_dir / "scores.jsonl")
    venue_rows = read_jsonl(run_dir / "venues.jsonl")
    venues = {venue["venue_id"]: venue for venue in venue_rows}
    recent = read_jsonl(run_dir / "recent_papers.jsonl")
    recent_by_venue: dict[str, list[dict[str, Any]]] = {}
    for row in recent:
        recent_by_venue.setdefault(row.get("venue_id", ""), []).append(row)
    chance_estimates = read_jsonl(run_dir / "chance_estimates.jsonl")
    if len(chance_estimates) < len(venue_rows):
        countable_by_venue: dict[str, list[dict[str, Any]]] = {}
        ready_by_venue: dict[str, list[dict[str, Any]]] = {}
        for row in recent:
            if is_valid_comparator_recent(row):
                countable_by_venue.setdefault(row["venue_id"], []).append(row)
            if is_ready_comparator_recent(row):
                ready_by_venue.setdefault(row["venue_id"], []).append(row)
        base_sources = normalize_base_rate_sources(run_dir, venue_rows, load_fixture_jsonl(args, "base_rate_sources.jsonl"))
        chance_estimates = build_chance_estimates(run_dir, venue_rows, scores, base_sources, countable_by_venue, ready_by_venue)
        write_jsonl(run_dir / "base_rate_sources.jsonl", base_sources)
        write_jsonl(run_dir / "chance_estimates.jsonl", chance_estimates)
    chance_by_venue = {row["venue_id"]: row for row in chance_estimates}
    status, reasons = delivery_status(run_dir)
    lines = [
        "# Submission Venue Recommendation",
        "",
        "Acceptance-chance intervals below are heuristic estimates, not predictions or guarantees.",
        "",
    ]
    if status != DELIVERY_READY:
        lines.extend(["incomplete analysis", ""])
    lines.extend(
        [
        f"Delivery status: `{status}`",
        "",
        "## Journal List",
        "",
        ]
    )
    if not scores:
        lines.append("No venues were scored.")
    for rank, score in enumerate(scores, 1):
        venue = venues.get(score["venue_id"], {})
        comparator_count = sum(1 for row in recent_by_venue.get(score["venue_id"], []) if is_valid_comparator_recent(row))
        ready_count = sum(1 for row in recent_by_venue.get(score["venue_id"], []) if is_ready_comparator_recent(row))
        chance = chance_by_venue.get(score["venue_id"], {})
        lines.extend(
            [
                f"{rank}. **{venue.get('canonical_name', score['venue_id'])}**",
                f"   - Fit band: {score.get('fit_band', 'not-ready/excluded')}",
                f"   - Estimated acceptance chance if submitted as-is: {chance.get('display_interval', 'not calculated')}",
                f"   - Estimate confidence: {chance.get('confidence', 'low')}",
                f"   - Calculation class: {chance.get('calculation_class', 'fallback-heuristic')}",
                f"   - Base rate interval: {percent_interval(tuple(chance.get('base_rate_interval', [0.05, 0.25])))}",
                f"   - Modifiers: eligibility {chance.get('eligibility_modifier_interval', [])}, venue fit {chance.get('venue_fit_modifier_interval', [])}, submission readiness {chance.get('submission_readiness_modifier_interval', [])}, evidence confidence {chance.get('evidence_confidence_modifier_interval', [])}",
                f"   - Ordinal score: {score['normalized_score']}",
                f"   - Score confidence: {score['confidence']}",
                f"   - Evidence: {', '.join(score.get('evidence_ids', [])) or 'none'}",
                f"   - Comparator papers: {comparator_count} countable; {ready_count} abstract/full-text",
                f"   - Rationale: {score['rationale']}",
                f"   - Caveats: {', '.join(chance.get('caveats', [])) or 'none'}",
            ]
        )
    lines.extend(["", "## Comparator Evidence Matrix", ""])
    if not recent:
        lines.append("No comparator-paper evidence is available.")
    else:
        lines.append("| Venue | Comparator paper | Provider | Year | Evidence level | Evidence IDs |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for row in recent:
            venue = venues.get(row.get("venue_id", ""), {})
            lines.append(
                "| {venue} | {title} | {provider} | {year} | {level} | {evidence} |".format(
                    venue=venue.get("canonical_name", row.get("venue_id", "")),
                    title=str(row.get("title", "")).replace("|", "\\|"),
                    provider=row.get("provider", ""),
                    year=row.get("year", ""),
                    level=row.get("evidence_level", ""),
                    evidence=", ".join(row.get("evidence_ids", [])),
                )
            )
    lines.extend(["", "## Review Findings", ""])
    if reasons:
        for reason in reasons:
            lines.append(f"- {reason}")
    else:
        lines.append("- No unsupported rank-affecting claims detected by runtime validation.")
    lines.extend(["", "## Delivery Check", "", f"- Status: `{status}`"])
    if status != DELIVERY_READY:
        lines.append("- incomplete analysis")
    lines.extend(["", "## Acceptance Chance Estimate Contract", ""])
    lines.append("- Estimates are intervals with calculation breakdowns.")
    lines.append("- Estimates are heuristic and must not be read as predictions.")
    lines.append("- Comparator papers affect fit/readiness modifiers, not base acceptance rates.")
    report = "\n".join(lines) + "\n"
    (run_dir / "recommendation.md").write_text(report, encoding="utf-8")
    delivery = {
        "schema_version": SCHEMA_VERSION,
        "delivery_status": status,
        "review_findings_ref": "recommendation.md#review-findings",
        "delivery_check_ref": "recommendation.md#delivery-check",
        "unsupported_claim_count": sum(1 for claim in read_jsonl(run_dir / "claims.jsonl") if claim.get("support_status") == "unsupported"),
        "stale_source_count": 0,
        "privacy_finding_count": sum(1 for guard in read_jsonl(run_dir / "guards.jsonl") if guard.get("status") == "blocked"),
        "downgrade_reasons": reasons,
        "created_at": now_iso(),
    }
    write_json(run_dir / "delivery.json", delivery)
    update_status(run_dir, "report", "ok")
    return json_result({"status": "ok", "delivery_status": status, "report": str(run_dir / "recommendation.md")})


def validate_artifacts(run_dir: Path) -> tuple[str, list[str]]:
    findings: list[str] = []
    for name in REQUIRED_ARTIFACTS:
        if not (run_dir / name).exists():
            findings.append(f"missing artifact: {name}")
    ids: dict[str, set[str]] = {}
    for name, key in (
        ("references.jsonl", "reference_id"),
        ("papers.jsonl", "paper_id"),
        ("sources.jsonl", "source_id"),
        ("evidence.jsonl", "evidence_id"),
        ("claims.jsonl", "claim_id"),
        ("venues.jsonl", "venue_id"),
        ("recent_papers.jsonl", "recent_paper_id"),
        ("scores.jsonl", "score_id"),
        ("scorecards.jsonl", "scorecard_id"),
        ("base_rate_sources.jsonl", "base_rate_source_id"),
        ("chance_estimates.jsonl", "chance_estimate_id"),
    ):
        rows = read_jsonl(run_dir / name)
        seen: set[str] = set()
        for row in rows:
            if row.get("schema_version") != SCHEMA_VERSION:
                findings.append(f"{name} has unsupported schema_version")
            value = row.get(key)
            if not value:
                findings.append(f"{name} row missing {key}")
            elif value in seen:
                findings.append(f"{name} duplicate {key}: {value}")
            seen.add(value)
        ids[name] = seen
    evidence_ids = ids.get("evidence.jsonl", set())
    source_ids = ids.get("sources.jsonl", set())
    countable_recent_venues = {
        row.get("venue_id")
        for row in read_jsonl(run_dir / "recent_papers.jsonl")
        if is_valid_comparator_recent(row)
    }
    ready_recent_venues = {
        row.get("venue_id")
        for row in read_jsonl(run_dir / "recent_papers.jsonl")
        if is_ready_comparator_recent(row)
    }
    for source in read_jsonl(run_dir / "sources.jsonl"):
        raw_url = str(source.get("source_url", ""))
        if raw_url:
            findings.append(f"source {source.get('source_id')} stores raw source_url")
        serialized_source = json.dumps(source, sort_keys=True)
        if re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|\bBearer\s+[A-Za-z0-9._-]+", serialized_source, re.I):
            findings.append(f"source {source.get('source_id')} may contain private credential material")
    recent_rows = read_jsonl(run_dir / "recent_papers.jsonl")
    for recent in recent_rows:
        recent_id = recent.get("recent_paper_id")
        if is_placeholder_recent(recent):
            findings.append(f"recent paper {recent_id} is placeholder or missing required year")
        if recent.get("evidence_level") and recent.get("evidence_level") not in COMPARATOR_EVIDENCE_LEVELS:
            findings.append(f"recent paper {recent_id} has unsupported evidence_level")
        for key in ("article_type", "exclusion_status", "topic_distance_rationale", "inspection_scope"):
            if not str(recent.get(key, "")).strip():
                findings.append(f"recent paper {recent_id} missing {key}")
        for source_id in recent.get("source_ids", []):
            if source_id not in source_ids:
                findings.append(f"recent paper {recent_id} references missing source {source_id}")
        for evidence_id in recent.get("evidence_ids", []):
            if evidence_id not in evidence_ids:
                findings.append(f"recent paper {recent_id} references missing evidence {evidence_id}")
        if recent and not is_valid_comparator_recent(recent):
            findings.append(f"recent paper {recent_id} is not valid comparator evidence")
    for score in read_jsonl(run_dir / "scores.jsonl"):
        for evidence_id in score.get("evidence_ids", []):
            if evidence_id not in evidence_ids:
                findings.append(f"score {score.get('score_id')} references missing evidence {evidence_id}")
        if score.get("fit_band") not in {"strong fit", "plausible fit", "evidence-limited", "not-ready/excluded"}:
            findings.append(f"score {score.get('score_id')} has unsupported fit_band")
        if score.get("fit_band") in {"strong fit", "plausible fit"} and score.get("venue_id") not in ready_recent_venues:
            findings.append(f"score {score.get('score_id')} is ready despite lacking abstract/full-text comparator evidence")
        if score.get("fit_band") == "evidence-limited" and score.get("venue_id") not in countable_recent_venues:
            findings.append(f"score {score.get('score_id')} lacks comparator-paper evidence")
        for criterion in score.get("criteria", []):
            criterion_evidence_ids = criterion.get("evidence_ids", [])
            if criterion.get("raw_score") != "not_scored" and not criterion_evidence_ids:
                findings.append(f"score {score.get('score_id')} criterion {criterion.get('criterion_id')} lacks evidence IDs")
            for evidence_id in criterion_evidence_ids:
                if evidence_id not in evidence_ids:
                    findings.append(
                        f"score {score.get('score_id')} criterion {criterion.get('criterion_id')} references missing evidence {evidence_id}"
                    )
            if criterion.get("criterion_id") == "comparator_pattern_fit" and score.get("fit_band") in {"strong fit", "plausible fit"} and not criterion_evidence_ids:
                findings.append(f"score {score.get('score_id')} comparator_pattern_fit lacks comparator evidence")
    for claim in read_jsonl(run_dir / "claims.jsonl"):
        if claim.get("claim_scope") not in {"venue_fit", "submission_readiness", "acceptance_chance", ""}:
            findings.append(f"claim {claim.get('claim_id')} has unsupported claim_scope")
        if claim.get("support_status") not in {"supported", "caveated", "unsupported"}:
            findings.append(f"claim {claim.get('claim_id')} has unsupported support_status")
        if claim.get("support_status") == "unsupported" and claim.get("claim_scope") != "venue_fit":
            findings.append(f"claim {claim.get('claim_id')} is not supported")
    venue_ids = ids.get("venues.jsonl", set())
    base_rate_source_ids = ids.get("base_rate_sources.jsonl", set())
    for estimate in read_jsonl(run_dir / "chance_estimates.jsonl"):
        estimate_id = estimate.get("chance_estimate_id")
        if estimate.get("venue_id") not in venue_ids:
            findings.append(f"chance estimate {estimate_id} references missing venue")
        if estimate.get("base_rate_source_id") not in base_rate_source_ids:
            findings.append(f"chance estimate {estimate_id} references missing base rate source")
        final_interval = estimate.get("final_interval", [])
        if not isinstance(final_interval, list) or len(final_interval) != 2 or final_interval[0] >= final_interval[1]:
            findings.append(f"chance estimate {estimate_id} lacks a non-point final interval")
        if not estimate.get("display_interval") or "-" not in str(estimate.get("display_interval")):
            findings.append(f"chance estimate {estimate_id} lacks display interval")
        if not estimate.get("calculation_note"):
            findings.append(f"chance estimate {estimate_id} lacks calculation note")
        if estimate.get("calculation_class") == "fallback-heuristic" and estimate.get("confidence") != "low":
            findings.append(f"chance estimate {estimate_id} fallback heuristic is not low confidence")
    report_path = run_dir / "recommendation.md"
    if report_path.is_file():
        report = report_path.read_text(encoding="utf-8", errors="replace").lower()
        if re.search(r"\b(will be accepted|guaranteed acceptance|predicts? acceptance)\b", report):
            findings.append("recommendation report contains unsupported predictive acceptance language")
        if "estimated acceptance chance if submitted as-is" not in report:
            findings.append("recommendation report is missing acceptance chance journal list")
    delivery = read_json(run_dir / "delivery.json")
    status = delivery.get("delivery_status", DELIVERY_NOT_READY)
    if findings and status == DELIVERY_READY:
        findings.append("delivery is ready despite validation findings")
    if findings:
        return DELIVERY_NOT_READY, findings
    return status if status else DELIVERY_CAVEATS, []


def command_validate(args: argparse.Namespace) -> int:
    run_dir = workspace(args)
    status, findings = validate_artifacts(run_dir)
    update_status(run_dir, "validate", "ok" if status != DELIVERY_NOT_READY else "failed")
    return json_result({"status": status, "findings": findings}, 1 if status == DELIVERY_NOT_READY else 0)


def command_purge(args: argparse.Namespace) -> int:
    run_dir = workspace(args)
    removed = []
    for name in (".cache", "queries.jsonl"):
        path = run_dir / name
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(name)
        elif path.is_file():
            path.unlink()
            removed.append(name)
    update_status(run_dir, "purge", "ok")
    return json_result({"status": "ok", "removed": removed})


def command_run(args: argparse.Namespace) -> int:
    if not hasattr(args, "max_hop"):
        args.max_hop = 1
    if not hasattr(args, "max_papers"):
        args.max_papers = int(args.max_requests)
    if not hasattr(args, "years"):
        args.years = 5
    if not hasattr(args, "per_venue"):
        args.per_venue = 5
    command_init(args)
    command_plan(args)
    command_extract(args)
    privacy_code = command_privacy_gate(args)
    if privacy_code != 0:
        return privacy_code
    command_providers(args)
    command_resolve(args)
    command_expand(args)
    command_venues(args)
    command_recent(args)
    command_score(args)
    command_report(args)
    return command_validate(args)


def command_smoke(_: argparse.Namespace) -> int:
    payload = {
        "status": "ok",
        "smoke_mode": "offline",
        "network_required": False,
        "live_api_attempted": False,
        "package_install_attempted": False,
        "server_started": False,
        "config_written": False,
        "real_secrets_read": False,
        "downloads_attempted": False,
        "mutations_attempted": False,
        "canary_leaked": False,
        "schemas": sorted(name for name in REQUIRED_ARTIFACTS if name.endswith((".json", ".jsonl"))),
    }
    return json_result(payload)


def add_common(parser: argparse.ArgumentParser, draft: bool = False) -> None:
    parser.add_argument("--dir", required=True)
    if draft:
        parser.add_argument("--draft", required=True)
    else:
        parser.add_argument("--draft")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--fixture-dir")
    parser.add_argument("--cache-dir")
    parser.add_argument("--max-requests", type=int, default=25)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--allow-provider", action="append")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--retain-draft-text", action="store_true")
    parser.add_argument("--allow-downloads", action="store_true")
    parser.add_argument("--allow-zotero-mutation", action="store_true")
    parser.add_argument("--allow-unpaywall-email", action="store_true")
    parser.add_argument("--unsafe-workspace-ok", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="submission_venue_selector")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, needs_draft in (
        ("init", command_init, True),
        ("plan", command_plan, False),
        ("extract", command_extract, False),
        ("privacy-gate", command_privacy_gate, False),
        ("providers", command_providers, False),
        ("resolve", command_resolve, False),
        ("venues", command_venues, False),
        ("score", command_score, False),
        ("report", command_report, False),
        ("validate", command_validate, False),
        ("purge", command_purge, False),
        ("run", command_run, True),
    ):
        child = sub.add_parser(name)
        add_common(child, draft=needs_draft)
        if name == "providers":
            child.add_argument("--check", action="store_true")
        child.set_defaults(func=func)
    expand = sub.add_parser("expand")
    add_common(expand)
    expand.add_argument("--max-hop", type=int, default=1)
    expand.add_argument("--max-papers", type=int, default=50)
    expand.set_defaults(func=command_expand)
    recent = sub.add_parser("recent")
    add_common(recent)
    recent.add_argument("--years", type=int, default=5)
    recent.add_argument("--per-venue", type=int, default=5)
    recent.set_defaults(func=command_recent)
    smoke = sub.add_parser("smoke")
    smoke.set_defaults(func=command_smoke)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SelectorError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(json.dumps({"status": "error", "error": f"invalid JSON: {exc}"}, indent=2, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
