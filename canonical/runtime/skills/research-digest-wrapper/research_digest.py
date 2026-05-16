#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import io
import json
import math
import os
import re
import socket
from pathlib import Path
from urllib.parse import quote, urlencode

FEEDPARSER = None
REQUESTS = None

WORKSPACE_ROOT = Path(os.environ.get("OPENCLAW_WORKSPACE", "/workspace"))
ALERTS_DIR = WORKSPACE_ROOT / "data" / "research" / "alerts"
TOPICS_FILE = ALERTS_DIR / "topics.tsv"
LEGACY_TOPICS_FILE = ALERTS_DIR / "topics.txt"
STATE_FILE = ALERTS_DIR / "digest-state.json"
SEEN_FILE = ALERTS_DIR / "seen-papers.json"
SEED_FILE = ALERTS_DIR / "seed-papers.json"
CORPUS_FILE = ALERTS_DIR / "corpus.json"
TFIDF_FILE = ALERTS_DIR / "corpus-model.json"
BIB_URL = "https://reconf.wdfiles.com/local--files/papers/core-pubs.bib"
BIB_FILE = ALERTS_DIR / "core-pubs.bib"
DIGEST_FILE = ALERTS_DIR / "digests" / "latest-digest.md"
BACKUPS_DIR = ALERTS_DIR / "backups"
STATE_MD = WORKSPACE_ROOT / "STATE.md"
DEFAULT_TOPIC_ROWS = [
    {"topic": "graph reconfiguration", "tag": "reconfiguration", "priority": 10, "enabled": 1, "notes": "core"},
    {"topic": "permutation graphs", "tag": "graph theory", "priority": 9, "enabled": 1, "notes": "core"},
    {"topic": "directed token sliding", "tag": "reconfiguration", "priority": 10, "enabled": 1, "notes": "core"},
    {"topic": "caterpillar graphs", "tag": "graph theory", "priority": 8, "enabled": 1, "notes": "current"},
    {"topic": "Ramsey theory", "tag": "combinatorics", "priority": 5, "enabled": 1, "notes": "general"},
]
TOPIC_FIELDS = ["topic", "tag", "priority", "enabled", "notes"]
OLLAMA_URL = os.environ.get("OPENCLAW_OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OPENCLAW_OLLAMA_MODEL", "qwen2.5:7b")
MAX_FETCH = 50
MAX_PAPERS = 12
RELEVANCE_TH = 70
ABSTRACT_LEN_SCORING = 1200
ABSTRACT_LEN_SUMMARY = 1400
ABSTRACT_LEN_STORE = 1800
HTTP_CONNECT_TIMEOUT = float(os.environ.get("OPENCLAW_RESEARCH_HTTP_CONNECT_TIMEOUT", "5"))
HTTP_READ_TIMEOUT = float(os.environ.get("OPENCLAW_RESEARCH_HTTP_READ_TIMEOUT", "15"))
OLLAMA_TIMEOUT = float(os.environ.get("OPENCLAW_RESEARCH_OLLAMA_TIMEOUT", "12"))
DEFAULT_USE_LLM_SCORING = os.environ.get("OPENCLAW_RESEARCH_USE_LLM_SCORING", "0").strip().casefold() in {"1", "true", "yes", "on"}
DEFAULT_USE_LLM_SUMMARY = os.environ.get("OPENCLAW_RESEARCH_USE_LLM_SUMMARY", "0").strip().casefold() in {"1", "true", "yes", "on"}
MAX_LLM_SUMMARIES = int(os.environ.get("OPENCLAW_RESEARCH_MAX_LLM_SUMMARIES", "4"))
HTTP_USER_AGENT = os.environ.get("OPENCLAW_RESEARCH_USER_AGENT", "openclaw-research-digest/2.0 (+local skill)")
S2_API_KEY = os.environ.get("OPENCLAW_S2_API_KEY", "")
S2_GRAPH_URL = "https://api.semanticscholar.org/graph/v1"
S2_REC_URL = "https://api.semanticscholar.org/recommendations/v1"
S2_RATE_DELAY = 2.0


def dependency_status():
    status = {}
    try:
        import feedparser as _fp
        status["feedparser"] = {"ok": True, "version": getattr(_fp, "__version__", None)}
    except ImportError as e:
        status["feedparser"] = {"ok": False, "error": str(e)}
    try:
        import requests as _rq
        status["requests"] = {"ok": True, "version": getattr(_rq, "__version__", None)}
    except ImportError as e:
        status["requests"] = {"ok": False, "error": str(e)}
    return status


def ensure_http_deps():
    global FEEDPARSER, REQUESTS
    if FEEDPARSER is not None and REQUESTS is not None:
        return FEEDPARSER, REQUESTS
    status = dependency_status()
    if not status["feedparser"]["ok"] or not status["requests"]["ok"]:
        missing = []
        for name in ("feedparser", "requests"):
            if not status[name]["ok"]:
                missing.append(f"{name}: {status[name].get('error', 'not available')}")
        print(json.dumps({"ok": False, "error": "missing runtime dependencies", "details": missing}, indent=2))
        raise SystemExit(1)
    import feedparser as _fp
    import requests as _rq
    FEEDPARSER, REQUESTS = _fp, _rq
    return FEEDPARSER, REQUESTS


def http_timeout_tuple():
    return (HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT)


def http_headers(extra=None):
    headers = {"User-Agent": HTTP_USER_AGENT, "Accept": "application/json, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"}
    if extra:
        headers.update(extra)
    return headers


def fetch_bytes(url: str, params=None):
    _, requests = ensure_http_deps()
    r = requests.get(url, params=params, timeout=http_timeout_tuple(), headers=http_headers())
    r.raise_for_status()
    return r.content


def fetch_json(url: str, params=None):
    _, requests = ensure_http_deps()
    r = requests.get(url, params=params, timeout=http_timeout_tuple(), headers=http_headers({"Accept": "application/json"}))
    r.raise_for_status()
    return r.json()


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def current_timestamp():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def normalize_topic(topic: str) -> str:
    return re.sub(r"\s+", " ", (topic or "").strip())


def normalize_tag(tag: str) -> str:
    value = re.sub(r"\s+", " ", (tag or "").strip())
    return value or "general"


def normalize_priority(value) -> int:
    try:
        p = int(value)
    except (ValueError, TypeError):
        p = 5
    return max(0, min(10, p))


def normalize_enabled(value) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "y", "on"}:
        return 1
    if text in {"0", "false", "no", "n", "off"}:
        return 0
    return 1


def topic_key(value: str) -> str:
    return normalize_topic(value).casefold()


def parse_topic_text(text: str):
    lines = text.splitlines()
    meaningful = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    if not meaningful:
        return []
    first = meaningful[0]
    if "\t" in first:
        buf = io.StringIO("\n".join(meaningful))
        reader = csv.DictReader(buf, delimiter="\t")
        rows = []
        for raw in reader:
            if not raw:
                continue
            rows.append({
                "topic": normalize_topic(raw.get("topic", "")),
                "tag": normalize_tag(raw.get("tag", "general")),
                "priority": normalize_priority(raw.get("priority", 5)),
                "enabled": normalize_enabled(raw.get("enabled", 1)),
                "notes": re.sub(r"\s+", " ", (raw.get("notes", "") or "").strip()),
            })
        return rows
    rows = []
    for line in meaningful:
        rows.append({"topic": normalize_topic(line), "tag": "general", "priority": 5, "enabled": 1, "notes": "legacy"})
    return rows


def serialize_topic_rows(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=TOPIC_FIELDS, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({
            "topic": normalize_topic(row.get("topic", "")),
            "tag": normalize_tag(row.get("tag", "general")),
            "priority": normalize_priority(row.get("priority", 5)),
            "enabled": normalize_enabled(row.get("enabled", 1)),
            "notes": re.sub(r"\s+", " ", (row.get("notes", "") or "").strip()),
        })
    return out.getvalue()


def canonicalize_topic_rows(rows):
    ordered = []
    index = {}
    for row in rows:
        topic = normalize_topic(row.get("topic", ""))
        if not topic:
            continue
        key = topic.casefold()
        norm = {
            "topic": topic,
            "tag": normalize_tag(row.get("tag", "general")),
            "priority": normalize_priority(row.get("priority", 5)),
            "enabled": normalize_enabled(row.get("enabled", 1)),
            "notes": re.sub(r"\s+", " ", (row.get("notes", "") or "").strip()),
        }
        if key in index:
            ordered[index[key]] = norm
        else:
            index[key] = len(ordered)
            ordered.append(norm)
    return ordered


def load_topic_rows():
    if TOPICS_FILE.exists():
        rows = parse_topic_text(TOPICS_FILE.read_text(encoding="utf-8"))
        return canonicalize_topic_rows(rows) or list(DEFAULT_TOPIC_ROWS)
    if LEGACY_TOPICS_FILE.exists():
        rows = parse_topic_text(LEGACY_TOPICS_FILE.read_text(encoding="utf-8"))
        return canonicalize_topic_rows(rows) or list(DEFAULT_TOPIC_ROWS)
    return list(DEFAULT_TOPIC_ROWS)


def list_topic_backup_paths():
    if not BACKUPS_DIR.exists():
        return []
    return sorted(list(BACKUPS_DIR.glob("topics-*.tsv")) + list(BACKUPS_DIR.glob("topics-*.txt")), key=lambda p: p.name, reverse=True)


def current_topics_source_text():
    if TOPICS_FILE.exists():
        return TOPICS_FILE.read_text(encoding="utf-8"), ".tsv"
    if LEGACY_TOPICS_FILE.exists():
        return LEGACY_TOPICS_FILE.read_text(encoding="utf-8"), ".txt"
    return None, ".tsv"


def create_topics_backup(reason: str = "manual"):
    text, ext = current_topics_source_text()
    if text is None:
        return None
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = current_timestamp()
    safe_reason = re.sub(r"[^a-z0-9._-]+", "-", reason.strip().lower()).strip("-") or "manual"
    backup = BACKUPS_DIR / f"topics-{stamp}-{safe_reason}{ext}"
    atomic_write(backup, text)
    return backup


def save_topic_rows(rows, backup_reason: str = None):
    rows = canonicalize_topic_rows(rows)
    new_text = serialize_topic_rows(rows)
    old_text = TOPICS_FILE.read_text(encoding="utf-8") if TOPICS_FILE.exists() else ""
    if backup_reason and (TOPICS_FILE.exists() or LEGACY_TOPICS_FILE.exists()) and new_text != old_text:
        create_topics_backup(backup_reason)
    atomic_write(TOPICS_FILE, new_text)
    return rows


def append_state_log(text: str):
    STATE_MD.parent.mkdir(parents=True, exist_ok=True)
    with STATE_MD.open("a", encoding="utf-8") as f:
        f.write(text)


def ping_ollama(timeout=1.0):
    try:
        host = re.sub(r"^https?://", "", OLLAMA_URL).split("/", 1)[0]
        if ":" in host:
            hostname, port = host.rsplit(":", 1)
            port = int(port)
        else:
            hostname, port = host, 80
        with socket.create_connection((hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


def ollama_raw(prompt, temp=0.0):
    try:
        _, requests = ensure_http_deps()
        r = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": temp}},
            timeout=OLLAMA_TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except (OSError, KeyError, ValueError):
        return ""


def ollama_json(prompt, temp=0.0):
    text = ollama_raw(prompt, temp)
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def summarize_local(abstract: str) -> str:
    abstract = re.sub(r"\s+", " ", abstract or "").strip()
    if not abstract:
        return "No abstract available."
    parts = re.split(r"(?<=[.!?])\s+", abstract)
    return " ".join(parts[:2])[:500]


def active_topic_rows(tag=None, min_priority=None):
    rows = [row for row in load_topic_rows() if normalize_enabled(row.get("enabled", 1))]
    if tag:
        want = normalize_tag(tag).casefold()
        rows = [row for row in rows if normalize_tag(row.get("tag", "general")).casefold() == want]
    if min_priority is not None:
        rows = [row for row in rows if normalize_priority(row.get("priority", 5)) >= int(min_priority)]
    rows.sort(key=lambda r: (-normalize_priority(r.get("priority", 5)), normalize_topic(r.get("topic", "")).casefold()))
    return rows


def topic_terms_for_fetch(rows, limit=8):
    rows = sorted(rows, key=lambda r: (-normalize_priority(r.get("priority", 5)), normalize_topic(r.get("topic", "")).casefold()))
    return [r["topic"] for r in rows[:limit]]


_CACHED_TFIDF_MODEL = None


def _get_tfidf_model():
    global _CACHED_TFIDF_MODEL
    if _CACHED_TFIDF_MODEL is None:
        _CACHED_TFIDF_MODEL = load_tfidf_model() or {}
    return _CACHED_TFIDF_MODEL


def relevance_filter(title, abstract, rows, use_llm_scoring=False):
    text = f"{title} {abstract}".casefold()
    kw_hits = [r for r in rows if r["topic"].casefold() in text]

    # Keyword score
    if kw_hits:
        priority_sum = sum(normalize_priority(r.get("priority", 5)) for r in kw_hits[:5])
        kw_score = min(100, 35 + 6 * priority_sum)
        kw_reasons = ", ".join(f"{r['topic']} (p={normalize_priority(r.get('priority', 5))})" for r in kw_hits[:3])
        kw_result = {"score": kw_score, "keep": kw_score >= RELEVANCE_TH, "reason": f"keyword match: {kw_reasons}"}
    else:
        kw_result = {"score": 0, "keep": False, "reason": "no keyword match"}

    # Corpus similarity score
    model = _get_tfidf_model()
    corpus_result = corpus_relevance(title, abstract, model) if model else {"score": 0, "keep": False, "reason": "no corpus model"}

    # Take the higher of the two signals
    if corpus_result["score"] > kw_result["score"]:
        fallback = corpus_result
    else:
        fallback = kw_result
    if not use_llm_scoring or not ping_ollama(timeout=0.5):
        return fallback

    topic_desc = "; ".join(f"{r['topic']} [tag={r['tag']}, priority={normalize_priority(r.get('priority', 5))}]" for r in rows)
    prompt = f'''Rate relevance from 0 to 100 for a theoretical CS / graph theory researcher.
Tracked topics: {topic_desc}
Title: {title}
Abstract: {abstract[:ABSTRACT_LEN_SCORING]}
Return ONLY strict JSON like {{"score": 0, "reason": "one sentence"}}.
'''
    resp = ollama_json(prompt, 0.0)
    if not isinstance(resp, dict):
        return fallback
    try:
        score = int(resp.get("score", fallback_score))
    except (ValueError, TypeError):
        score = fallback_score
    reason = str(resp.get("reason", fallback["reason"]))[:240]
    return {"score": score, "keep": score >= RELEVANCE_TH, "reason": reason}


def llm_summary(title, abstract, rows, use_llm_summary=False):
    if not use_llm_summary or not ping_ollama(timeout=0.5):
        return summarize_local(abstract)
    topic_desc = ", ".join(f"{r['topic']} (tag={r['tag']}, p={normalize_priority(r.get('priority', 5))})" for r in rows[:8])
    prompt = f'''Write a concise 3-4 sentence summary for a graph theory / TCS researcher.
Focus on the main result, key technique, and likely relevance to: {topic_desc}.
Title: {title}
Abstract: {abstract[:ABSTRACT_LEN_SUMMARY]}
'''
    text = ollama_raw(prompt, 0.25)
    return text or summarize_local(abstract)


def normalize_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title or "").strip().casefold()
    return re.sub(r"[^a-z0-9 ]+", "", title)


ARXIV_CATEGORIES = ["cs.DM", "cs.DS", "cs.CC", "math.CO"]


def arxiv_recent(rows, days=90):
    topics = topic_terms_for_fetch(rows)
    if not topics:
        return []
    base_url = "https://export.arxiv.org/api/query"
    topic_clause = " OR ".join(f'all:"{topic}"' for topic in topics)
    cat_clause = " OR ".join(f"cat:{c}" for c in ARXIV_CATEGORIES)
    search_query = f"({topic_clause}) AND ({cat_clause})"
    params = {"search_query": search_query, "sortBy": "submittedDate", "sortOrder": "descending", "max_results": MAX_FETCH}
    feedparser, _ = ensure_http_deps()
    raw = fetch_bytes(base_url, params=params)
    feed = feedparser.parse(raw)
    cutoff = dt.date.today() - dt.timedelta(days=days)
    out = []
    for e in getattr(feed, "entries", []):
        if not getattr(e, "published_parsed", None):
            continue
        pub_date = dt.datetime(*e.published_parsed[:6]).date()
        if pub_date < cutoff:
            continue
        link = getattr(e, "link", "")
        out.append({
            "source": "arXiv",
            "title": getattr(e, "title", "").strip(),
            "authors": ", ".join(a.name for a in getattr(e, "authors", [])) or "—",
            "date": pub_date.isoformat(),
            "date_ord": pub_date.toordinal(),
            "link": link,
            "pdf": link.replace("/abs/", "/pdf/") if "/abs/" in link else link,
            "abstract": getattr(e, "summary", "").replace("\n", " ").strip(),
        })
    return out


def s2_headers():
    h = {"User-Agent": HTTP_USER_AGENT}
    if S2_API_KEY:
        h["x-api-key"] = S2_API_KEY
    return h


def s2_get(url, params=None, retries=2):
    import time as _time
    _, requests = ensure_http_deps()
    for attempt in range(retries + 1):
        r = requests.get(url, params=params, timeout=http_timeout_tuple(), headers=s2_headers())
        if r.status_code == 429 and attempt < retries:
            _time.sleep(S2_RATE_DELAY * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    return None


def s2_post(url, body, params=None, retries=2):
    import time as _time
    _, requests = ensure_http_deps()
    for attempt in range(retries + 1):
        r = requests.post(url, json=body, params=params, timeout=http_timeout_tuple(), headers=s2_headers())
        if r.status_code == 429 and attempt < retries:
            _time.sleep(S2_RATE_DELAY * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    return None


def _s2_paper_to_dict(w):
    pub = w.get("publicationDate") or ""
    try:
        pub_date = dt.datetime.strptime(pub, "%Y-%m-%d").date()
    except ValueError:
        pub_date = None
    eids = w.get("externalIds") or {}
    arxiv_id = eids.get("ArXiv", "")
    link = ""
    if arxiv_id:
        link = f"https://arxiv.org/abs/{arxiv_id}"
    elif eids.get("DOI"):
        link = f"https://doi.org/{eids['DOI']}"
    else:
        link = w.get("url", "")
    pdf = link.replace("/abs/", "/pdf/") if "/abs/" in link else ""
    authors = ", ".join(
        (a.get("name") or "") for a in (w.get("authors") or [])[:6]
    ).strip(", ")
    abstract = (w.get("abstract") or "No abstract available").replace("\n", " ").strip()
    return {
        "source": "S2",
        "title": (w.get("title") or "").strip(),
        "authors": authors or "—",
        "date": pub_date.isoformat() if pub_date else pub,
        "date_ord": pub_date.toordinal() if pub_date else 0,
        "link": link,
        "pdf": pdf,
        "abstract": abstract[:ABSTRACT_LEN_STORE],
    }


def load_seed_ids():
    data = load_json(SEED_FILE, {})
    return [s["id"] for s in data.get("seeds", []) if s.get("id")]


def s2_recommend(days=90):
    import time as _time
    seed_ids = load_seed_ids()
    if not seed_ids:
        return []
    cutoff = dt.date.today() - dt.timedelta(days=days)
    body = {"positivePaperIds": seed_ids}
    fields = "title,year,publicationDate,externalIds,abstract,authors"
    data = s2_post(
        f"{S2_REC_URL}/papers/",
        body=body,
        params={"fields": fields, "limit": MAX_FETCH},
    )
    if not data:
        return []
    out = []
    for w in data.get("recommendedPapers", []):
        p = _s2_paper_to_dict(w)
        if p["date_ord"] and dt.date.fromordinal(p["date_ord"]) < cutoff:
            continue
        out.append(p)
    return out


def s2_search(rows, days=90):
    import time as _time
    topics = topic_terms_for_fetch(rows, limit=4)
    if not topics:
        return []
    cutoff = dt.date.today() - dt.timedelta(days=days)
    cutoff_str = cutoff.isoformat()
    fields = "title,year,publicationDate,externalIds,abstract,authors"
    out = []
    for topic in topics:
        _time.sleep(S2_RATE_DELAY)
        try:
            data = s2_get(
                f"{S2_GRAPH_URL}/paper/search",
                params={
                    "query": topic,
                    "fields": fields,
                    "fieldsOfStudy": "Computer Science,Mathematics",
                    "publicationDateOrYear": f"{cutoff_str}:",
                    "limit": 15,
                },
            )
        except (OSError, ValueError, KeyError):
            continue
        if not data:
            continue
        for w in data.get("data", []):
            p = _s2_paper_to_dict(w)
            if p["date_ord"] and dt.date.fromordinal(p["date_ord"]) < cutoff:
                continue
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Corpus-based TF-IDF scoring
# ---------------------------------------------------------------------------

TFIDF_STOP = frozenset(
    "a about above after all also am an and any are as at be been before being "
    "between both but by can could did do does doing down during each few for from "
    "further get given had has have having he her here hers herself him himself his "
    "how however i if in into is it its itself just let me more most my myself no "
    "nor not now of on once only or other our ours ourselves out over own same she "
    "should so some such than that the their theirs them themselves then there these "
    "they this those through to too under until up us very was we were what when "
    "where which while who whom why will with would you your yours yourself "
    "using used use based via may new two one show prove study also results result "
    "paper problem problems graph graphs set sets number".split()
)


def tfidf_tokenize(text):
    words = re.findall(r"[a-z][a-z0-9]{1,}", text.lower())
    words = [w for w in words if w not in TFIDF_STOP]
    tokens = list(words)
    for i in range(len(words) - 1):
        tokens.append(f"{words[i]}_{words[i + 1]}")
    return tokens


def _vec_norm(vec):
    return math.sqrt(sum(v * v for v in vec.values())) or 1e-12


def _cosine_sim(a, b):
    dot = sum(a.get(k, 0) * v for k, v in b.items())
    return dot / (_vec_norm(a) * _vec_norm(b))


def parse_bib_file(text):
    entries = []
    for m in re.finditer(r"@\w+\{([^,]+),\s*\n(.*?)(?=\n@|\Z)", text, re.DOTALL):
        key = m.group(1).strip()
        if "\n" in key:
            continue
        body = m.group(2)
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*\{(.*?)\}(?:\s*,|\s*$)", body, re.DOTALL):
            fields[fm.group(1).lower()] = re.sub(r"\s+", " ", fm.group(2)).strip()
        title = fields.get("title", "").strip("{}")
        if not title:
            continue
        entries.append({
            "key": key,
            "title": title,
            "eprint": fields.get("eprint", ""),
            "year": fields.get("year", ""),
        })
    return entries


def fetch_corpus_abstracts(entries):
    import time as _time
    _, requests = ensure_http_deps()

    # Split into batches by arXiv ID
    with_arxiv = [e for e in entries if e.get("eprint")]
    without_arxiv = [e for e in entries if not e.get("eprint")]

    corpus = []
    found_keys = set()

    # Batch fetch papers with arXiv IDs (S2 POST /paper/batch, up to 500)
    for start in range(0, len(with_arxiv), 450):
        batch = with_arxiv[start : start + 450]
        ids = [f"ARXIV:{e['eprint']}" for e in batch]
        _time.sleep(S2_RATE_DELAY)
        try:
            r = requests.post(
                f"{S2_GRAPH_URL}/paper/batch",
                json={"ids": ids},
                params={"fields": "title,abstract,paperId,externalIds,year"},
                timeout=30,
                headers=s2_headers(),
            )
            if r.status_code == 200:
                for paper, entry in zip(r.json(), batch):
                    if paper and paper.get("title"):
                        corpus.append({
                            "key": entry["key"],
                            "title": paper.get("title", entry["title"]),
                            "abstract": paper.get("abstract", "") or "",
                            "year": paper.get("year") or entry.get("year", ""),
                            "s2id": paper.get("paperId", ""),
                        })
                        found_keys.add(entry["key"])
        except (OSError, ValueError):
            pass

    # Title-match for papers without arXiv IDs
    for entry in without_arxiv:
        if entry["key"] in found_keys:
            continue
        _time.sleep(S2_RATE_DELAY)
        try:
            data = s2_get(
                f"{S2_GRAPH_URL}/paper/search/match",
                params={"query": entry["title"], "fields": "title,abstract,paperId,year"},
            )
            if data and data.get("data"):
                paper = data["data"][0]
                corpus.append({
                    "key": entry["key"],
                    "title": paper.get("title", entry["title"]),
                    "abstract": paper.get("abstract", "") or "",
                    "year": paper.get("year") or entry.get("year", ""),
                    "s2id": paper.get("paperId", ""),
                })
                found_keys.add(entry["key"])
        except (OSError, ValueError):
            pass

    # Add entries we couldn't find (title-only, no abstract)
    for entry in entries:
        if entry["key"] not in found_keys:
            corpus.append({
                "key": entry["key"],
                "title": entry["title"],
                "abstract": "",
                "year": entry.get("year", ""),
                "s2id": "",
            })

    return corpus


def build_tfidf_model(corpus):
    N = len(corpus)
    if N == 0:
        return {"vocab": {}, "idf": {}, "centroid": {}, "n_docs": 0}

    df = {}
    doc_tfs = []
    for doc in corpus:
        text = f"{doc['title']} {doc['title']} {doc.get('abstract', '')}"
        tokens = tfidf_tokenize(text)
        tf = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        for t in tf:
            tf[t] = 1 + math.log(tf[t])
        doc_tfs.append(tf)
        for t in set(tokens):
            df[t] = df.get(t, 0) + 1

    vocab = sorted(t for t, c in df.items() if c >= 2)
    vocab_set = set(vocab)
    idf = {t: math.log(N / df[t]) + 1 for t in vocab}

    centroid = {}
    for tf in doc_tfs:
        for t, val in tf.items():
            if t in vocab_set:
                centroid[t] = centroid.get(t, 0) + val * idf[t]
    for t in centroid:
        centroid[t] /= N

    return {"vocab": vocab, "idf": idf, "centroid": centroid, "n_docs": N}


def save_tfidf_model(model):
    atomic_write(TFIDF_FILE, json.dumps(model))


def load_tfidf_model():
    if not TFIDF_FILE.exists():
        return None
    try:
        return json.loads(TFIDF_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def corpus_relevance(title, abstract, model=None):
    if model is None:
        model = load_tfidf_model()
    if not model or not model.get("centroid"):
        return {"score": 0, "keep": False, "reason": "no corpus model"}

    text = f"{title} {title} {abstract}"
    tokens = tfidf_tokenize(text)
    if not tokens:
        return {"score": 0, "keep": False, "reason": "no tokens"}

    idf = model["idf"]
    tf = {}
    for t in tokens:
        if t in idf:
            tf[t] = tf.get(t, 0) + 1
    for t in tf:
        tf[t] = (1 + math.log(tf[t])) * idf[t]

    sim = _cosine_sim(tf, model["centroid"])
    score = int(min(100, max(0, sim * 300 + 20)))

    top_terms = sorted(tf.keys(), key=lambda t: tf[t] * model["centroid"].get(t, 0), reverse=True)[:4]
    reason = f"corpus similarity ({', '.join(top_terms)})" if top_terms else "corpus similarity"
    return {"score": score, "keep": score >= RELEVANCE_TH, "reason": reason}


def command_rebuild_corpus(_args):
    import time as _time

    # Step 1: fetch bib file
    print(json.dumps({"status": "fetching bib file"}))
    _, requests = ensure_http_deps()
    r = requests.get(BIB_URL, timeout=30, headers=http_headers())
    r.raise_for_status()
    atomic_write(BIB_FILE, r.text)
    entries = parse_bib_file(r.text)
    print(json.dumps({"status": "parsed bib", "entries": len(entries)}))

    # Step 2: fetch abstracts
    print(json.dumps({"status": "fetching abstracts from Semantic Scholar (this takes a few minutes)"}))
    corpus = fetch_corpus_abstracts(entries)
    with_abstract = sum(1 for c in corpus if c.get("abstract"))
    atomic_write(CORPUS_FILE, json.dumps({"built": dt.date.today().isoformat(), "source": "core-pubs.bib", "papers": corpus}, indent=2))
    print(json.dumps({"status": "corpus built", "total": len(corpus), "with_abstract": with_abstract}))

    # Step 3: build TF-IDF model
    model = build_tfidf_model(corpus)
    save_tfidf_model(model)
    print(json.dumps({
        "ok": True,
        "corpus_size": len(corpus),
        "abstracts_found": with_abstract,
        "vocab_size": len(model.get("vocab", [])),
        "model_file": str(TFIDF_FILE),
        "corpus_file": str(CORPUS_FILE),
    }, indent=2))


def load_seen_papers():
    return load_json(SEEN_FILE, {})


def save_seen_papers(seen):
    cutoff = (dt.date.today() - dt.timedelta(days=60)).isoformat()
    pruned = {k: v for k, v in seen.items() if v >= cutoff}
    atomic_write(SEEN_FILE, json.dumps(pruned, indent=2))


def build_digest(rows, use_llm_scoring=False, use_llm_summary=False):
    source_errors = []
    papers = []
    try:
        papers.extend(arxiv_recent(rows))
    except Exception as exc:
        source_errors.append(f"arXiv fetch failed: {type(exc).__name__}: {exc}")
    try:
        papers.extend(s2_recommend())
    except Exception as exc:
        source_errors.append(f"S2 recommend failed: {type(exc).__name__}: {exc}")
    try:
        papers.extend(s2_search(rows))
    except Exception as exc:
        source_errors.append(f"S2 search failed: {type(exc).__name__}: {exc}")

    dedup = {}
    for p in papers:
        key = normalize_title(p.get("title", ""))
        existing = dedup.get(key)
        if existing is None or (p.get("source") == "arXiv" and existing.get("source") != "arXiv"):
            dedup[key] = p

    seen = load_seen_papers()
    new_papers = {k: v for k, v in dedup.items() if k not in seen}

    scored = []
    for p in new_papers.values():
        filt = relevance_filter(p.get("title", ""), p.get("abstract", ""), rows, use_llm_scoring=use_llm_scoring)
        if filt["keep"]:
            scored.append({**p, **filt})

    scored.sort(key=lambda x: (-x["score"], -x.get("date_ord", 0), x.get("title", "").casefold()))
    selected = scored[:MAX_PAPERS]
    for p in selected:
        p["summary"] = llm_summary(p.get("title", ""), p.get("abstract", ""), rows, use_llm_summary=use_llm_summary)

    today = dt.date.today().isoformat()
    for key in new_papers:
        seen[key] = today
    save_seen_papers(seen)

    return selected, source_errors


def command_run(args):
    rows = active_topic_rows(tag=args.tag, min_priority=args.min_priority)
    if not rows:
        print(json.dumps({"ok": False, "error": "no active topics selected", "tag": args.tag, "min_priority": args.min_priority}, indent=2))
        raise SystemExit(1)
    use_llm_scoring = DEFAULT_USE_LLM_SCORING or args.use_llm_scoring
    use_llm_summary = DEFAULT_USE_LLM_SUMMARY or args.use_llm_summary
    selected, source_errors = build_digest(rows, use_llm_scoring=use_llm_scoring, use_llm_summary=use_llm_summary)
    today = dt.date.today().isoformat()
    if not selected:
        digest = f"# Research Digest {today}\n\nNo papers exceeded relevance threshold {RELEVANCE_TH}.\n"
        if source_errors:
            digest += "\n## Source warnings\n" + "\n".join(f"- {err}" for err in source_errors) + "\n"
    else:
        topic_summary = ", ".join(f"{r['topic']} [{r['tag']}, p={r['priority']}]" for r in rows)
        lines = [f"# Research Digest {today}", "", f"Tracked topics: {topic_summary}", ""]
        if source_errors:
            lines.extend(["## Source warnings", *[f"- {err}" for err in source_errors], ""])
        for i, p in enumerate(selected, 1):
            link = p["pdf"] or p["link"]
            lines.append(f"## {i}. {p['title']} [{p['source']}]")
            lines.append(f"- Authors: {p['authors']}")
            lines.append(f"- Date: {p['date']}")
            lines.append(f"- Relevance: {p['score']}/100 ({p['reason']})")
            if link:
                lines.append(f"- Link: {link}")
            lines.append("")
            lines.append(p["summary"])
            lines.append("")
        digest = "\n".join(lines)
    atomic_write(DIGEST_FILE, digest)
    atomic_write(STATE_FILE, json.dumps({
        "date": today,
        "count": len(selected),
        "tag": args.tag,
        "min_priority": args.min_priority,
        "topic_count": len(rows),
        "topics": rows,
        "source_errors": source_errors,
        "use_llm_scoring": use_llm_scoring,
        "use_llm_summary": use_llm_summary,
    }, indent=2))
    append_state_log(f"\n## Digest {today}\nPapers after filter: {len(selected)}\n")
    print(json.dumps({
        "ok": True,
        "count": len(selected),
        "digest_file": str(DIGEST_FILE),
        "tag": args.tag,
        "min_priority": args.min_priority,
        "topics": rows,
    }, indent=2))


def command_list_topics(args):
    rows = load_topic_rows()
    if args.tag:
        want = normalize_tag(args.tag).casefold()
        rows = [r for r in rows if normalize_tag(r.get("tag", "general")).casefold() == want]
    if args.enabled_only:
        rows = [r for r in rows if normalize_enabled(r.get("enabled", 1))]
    print(json.dumps({"ok": True, "topics": rows, "topics_file": str(TOPICS_FILE)}, indent=2))


def command_add_topic(args):
    rows = load_topic_rows() + [{
        "topic": args.topic,
        "tag": args.tag,
        "priority": args.priority,
        "enabled": 0 if args.disabled else 1,
        "notes": args.notes or "",
    }]
    rows = save_topic_rows(rows, backup_reason="add-topic")
    print(json.dumps({"ok": True, "topics": rows, "topics_file": str(TOPICS_FILE)}, indent=2))


def command_edit_topic(args):
    target = topic_key(args.topic)
    rows = load_topic_rows()
    found = False
    for row in rows:
        if topic_key(row.get("topic", "")) != target:
            continue
        found = True
        if args.new_topic is not None:
            row["topic"] = args.new_topic
        if args.tag is not None:
            row["tag"] = args.tag
        if args.priority is not None:
            row["priority"] = args.priority
        if args.enabled is not None:
            row["enabled"] = args.enabled
        if args.notes is not None:
            row["notes"] = args.notes
        break
    if not found:
        print(json.dumps({"ok": False, "error": f"topic not found: {args.topic}"}, indent=2))
        raise SystemExit(1)
    rows = save_topic_rows(rows, backup_reason="edit-topic")
    print(json.dumps({"ok": True, "topics": rows, "topics_file": str(TOPICS_FILE)}, indent=2))


def command_remove_topic(args):
    target = topic_key(args.topic)
    rows = [r for r in load_topic_rows() if topic_key(r.get("topic", "")) != target]
    rows = save_topic_rows(rows, backup_reason="remove-topic")
    print(json.dumps({"ok": True, "topics": rows, "topics_file": str(TOPICS_FILE)}, indent=2))


def set_topic_enabled(topic: str, enabled: int, reason: str):
    target = topic_key(topic)
    rows = load_topic_rows()
    found = False
    for row in rows:
        if topic_key(row.get("topic", "")) == target:
            row["enabled"] = enabled
            found = True
            break
    if not found:
        print(json.dumps({"ok": False, "error": f"topic not found: {topic}"}, indent=2))
        raise SystemExit(1)
    rows = save_topic_rows(rows, backup_reason=reason)
    print(json.dumps({"ok": True, "topics": rows, "topics_file": str(TOPICS_FILE)}, indent=2))


def command_disable_topic(args):
    set_topic_enabled(args.topic, 0, "disable-topic")


def command_enable_topic(args):
    set_topic_enabled(args.topic, 1, "enable-topic")


def command_backup_topics(args):
    backup = create_topics_backup(args.reason)
    print(json.dumps({"ok": True, "backup": str(backup) if backup else None, "topics_file": str(TOPICS_FILE)}, indent=2))


def command_list_backups(_args):
    backups = [{"name": p.name, "path": str(p)} for p in list_topic_backup_paths()]
    print(json.dumps({"ok": True, "backup_dir": str(BACKUPS_DIR), "count": len(backups), "backups": backups}, indent=2))


def resolve_backup_path(value: str):
    if value:
        candidate = Path(value)
        if candidate.exists():
            return candidate
        candidate = BACKUPS_DIR / value
        if candidate.exists():
            return candidate
        return None
    paths = list_topic_backup_paths()
    return paths[0] if paths else None


def command_restore_backup(args):
    backup = resolve_backup_path(args.backup)
    if backup is None:
        print(json.dumps({"ok": False, "error": "no matching backup found"}, indent=2))
        raise SystemExit(1)
    if TOPICS_FILE.exists() or LEGACY_TOPICS_FILE.exists():
        create_topics_backup("pre-restore")
    rows = parse_topic_text(backup.read_text(encoding="utf-8"))
    rows = save_topic_rows(rows, backup_reason=None)
    print(json.dumps({"ok": True, "restored_from": str(backup), "topics_file": str(TOPICS_FILE), "topics": rows}, indent=2))


def command_export_topics(args):
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(out, serialize_topic_rows(load_topic_rows()))
    print(json.dumps({"ok": True, "output": str(out), "count": len(load_topic_rows())}, indent=2))


def command_import_topics(args):
    path = Path(args.path)
    if not path.exists():
        print(json.dumps({"ok": False, "error": f"missing file: {path}"}, indent=2))
        raise SystemExit(1)
    incoming = parse_topic_text(path.read_text(encoding="utf-8"))
    rows = incoming if args.replace else (load_topic_rows() + incoming)
    rows = save_topic_rows(rows, backup_reason="import-topics")
    print(json.dumps({"ok": True, "topics": rows, "topics_file": str(TOPICS_FILE)}, indent=2))


def command_doctor(_args):
    state = load_json(STATE_FILE, {})
    backups = list_topic_backup_paths()
    rows = load_topic_rows()
    by_tag = {}
    for row in rows:
        tag = normalize_tag(row.get("tag", "general"))
        by_tag[tag] = by_tag.get(tag, 0) + 1
    print(json.dumps({
        "ok": True,
        "workspace": str(WORKSPACE_ROOT),
        "alerts_dir": str(ALERTS_DIR),
        "topics_file": str(TOPICS_FILE),
        "legacy_topics_file": str(LEGACY_TOPICS_FILE),
        "digest_file": str(DIGEST_FILE),
        "state_file": str(STATE_FILE),
        "backup_dir": str(BACKUPS_DIR),
        "topic_count": len(rows),
        "active_topic_count": len([r for r in rows if normalize_enabled(r.get("enabled", 1))]),
        "topics_by_tag": by_tag,
        "topic_backup_count": len(backups),
        "latest_topic_backup": str(backups[0]) if backups else None,
        "dependencies": dependency_status(),
        "ollama_reachable": ping_ollama(timeout=0.5),
        "ollama_url": OLLAMA_URL,
        "ollama_model": OLLAMA_MODEL,
        "http_timeouts": {"connect": HTTP_CONNECT_TIMEOUT, "read": HTTP_READ_TIMEOUT, "ollama": OLLAMA_TIMEOUT},
        "llm_defaults": {"scoring": DEFAULT_USE_LLM_SCORING, "summary": DEFAULT_USE_LLM_SUMMARY, "max_llm_summaries": MAX_LLM_SUMMARIES},
        "s2_api_key_set": bool(S2_API_KEY),
        "s2_seed_count": len(load_seed_ids()),
        "s2_seed_file": str(SEED_FILE),
        "last_state": state,
    }, indent=2))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command")
    run = sub.add_parser("run")
    run.add_argument("--tag")
    run.add_argument("--min-priority", type=int)
    run.add_argument("--use-llm-scoring", action="store_true")
    run.add_argument("--use-llm-summary", action="store_true")
    run.set_defaults(func=command_run)
    lt = sub.add_parser("list-topics")
    lt.add_argument("--tag")
    lt.add_argument("--enabled-only", action="store_true")
    lt.set_defaults(func=command_list_topics)
    add = sub.add_parser("add-topic")
    add.add_argument("topic")
    add.add_argument("--tag", default="general")
    add.add_argument("--priority", type=int, default=5)
    add.add_argument("--notes", default="")
    add.add_argument("--disabled", action="store_true")
    add.set_defaults(func=command_add_topic)
    edit = sub.add_parser("edit-topic")
    edit.add_argument("topic")
    edit.add_argument("--new-topic")
    edit.add_argument("--tag")
    edit.add_argument("--priority", type=int)
    edit.add_argument("--enabled", type=int, choices=[0, 1])
    edit.add_argument("--notes")
    edit.set_defaults(func=command_edit_topic)
    rm = sub.add_parser("remove-topic")
    rm.add_argument("topic")
    rm.set_defaults(func=command_remove_topic)
    dis = sub.add_parser("disable-topic")
    dis.add_argument("topic")
    dis.set_defaults(func=command_disable_topic)
    en = sub.add_parser("enable-topic")
    en.add_argument("topic")
    en.set_defaults(func=command_enable_topic)
    bk = sub.add_parser("backup-topics")
    bk.add_argument("--reason", default="manual")
    bk.set_defaults(func=command_backup_topics)
    lb = sub.add_parser("list-topic-backups")
    lb.set_defaults(func=command_list_backups)
    rb = sub.add_parser("restore-topic-backup")
    rb.add_argument("backup", nargs="?")
    rb.set_defaults(func=command_restore_backup)
    ex = sub.add_parser("export-topics")
    ex.add_argument("--output", required=True)
    ex.set_defaults(func=command_export_topics)
    imp = sub.add_parser("import-topics")
    imp.add_argument("path")
    imp.add_argument("--replace", action="store_true")
    imp.set_defaults(func=command_import_topics)
    sub.add_parser("doctor").set_defaults(func=command_doctor)
    sub.add_parser("rebuild-corpus").set_defaults(func=command_rebuild_corpus)

    args = ap.parse_args()
    if args.command is None:
        args = ap.parse_args(["run"])
    args.func(args)


if __name__ == "__main__":
    main()
