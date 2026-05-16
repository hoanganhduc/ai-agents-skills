#!/usr/bin/env python3
import argparse
import csv
import hashlib
import html
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse

_WORKSPACE = Path(os.environ.get("AAS_RUNTIME_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE") or os.path.join(os.path.expanduser("~"), ".codex", "runtime", "workspace"))
DEFAULT_FEEDS_TSV = _WORKSPACE / "data" / "research" / "rss" / "feeds.tsv"
DEFAULT_LEGACY_FEEDS_FILE = _WORKSPACE / "data" / "research" / "rss" / "feeds.txt"
DEFAULT_PROFILES_FILE = _WORKSPACE / "data" / "research" / "rss" / "profiles.json"
DEFAULT_STATE_FILE = _WORKSPACE / "data" / "research" / "rss" / "state.json"
DEFAULT_DIGEST_DIR = _WORKSPACE / "data" / "research" / "rss" / "digests"
DEFAULT_BACKUP_DIR = _WORKSPACE / "data" / "research" / "rss" / "backups"
DEFAULT_FEEDS_BOOTSTRAP = "enabled\ttag\tpriority\tkind\turl\tnotes\n1\tresearch\t10\tarxiv\thttps://export.arxiv.org/rss/cs.CC\tComputational complexity\n1\tresearch\t10\tarxiv\thttps://export.arxiv.org/rss/cs.DS\tData structures and algorithms\n1\tresearch\t10\tarxiv\thttps://export.arxiv.org/rss/cs.DM\tDiscrete mathematics\n1\tresearch\t10\tarxiv\thttps://export.arxiv.org/rss/math.CO\tCombinatorics\n1\tresearch\t9\tblog\thttps://11011110.github.io/blog/feed.xml\tTheory blog\n1\tresearch\t9\tblog\thttp://blog.computationalcomplexity.org/feeds/posts/default\tComputational Complexity Blog\n1\tevents\t8\tcfp\thttp://www.wikicfp.com/cfp/rss?cat=algorithms\tAlgorithms CFPs\n1\tjobs\t8\tjobs\thttps://www.mathjobs.org/jobs?joblist-0-0----rss--\tMath jobs\n1\tgeneral\t3\tnews\thttps://www.quantamagazine.org/feed/\tQuanta\n"
DEFAULT_PROFILES_BOOTSTRAP = "{\n  \"graph_theory\": [\n    \"graph\",\n    \"combinatorics\",\n    \"coloring\",\n    \"reconfiguration\",\n    \"token\",\n    \"planar\",\n    \"permutation graph\",\n    \"independent set\"\n  ],\n  \"complexity\": [\n    \"complexity\",\n    \"pspace\",\n    \"np-hard\",\n    \"reduction\",\n    \"hardness\",\n    \"lower bound\",\n    \"constraint logic\"\n  ],\n  \"algorithms\": [\n    \"algorithm\",\n    \"data structure\",\n    \"dynamic programming\",\n    \"approximation\",\n    \"streaming\",\n    \"online\",\n    \"randomized\"\n  ],\n  \"ai_research\": [\n    \"ai\",\n    \"artificial intelligence\",\n    \"chatgpt\",\n    \"llm\",\n    \"large language model\",\n    \"gpt\",\n    \"reproducibility\",\n    \"papers\",\n    \"research\"\n  ]\n}"
DEFAULT_MAX_ITEMS = 25
DEFAULT_PER_FEED_LIMIT = 12
STATE_LIMIT = 5000
KNOWN_TAGS = ["research", "events", "jobs", "general", "video"]
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
ARXIV_RE = re.compile(r'(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?')
YT_RE = re.compile(r'(?:v=|/videos/|youtu\.be/)([A-Za-z0-9_-]{6,})')
SE_RE = re.compile(r'/questions/(\d+)/')


def now_ts() -> float:
    return time.time()


def ensure_feedparser():
    try:
        import feedparser  # type: ignore
    except ImportError:
        sys.stderr.write(
            "Missing dependency: feedparser. Install it with one of:\n"
            "  python3 -m pip install --user feedparser\n"
            "  or rerun the installer without --no-venv so it can create an isolated virtualenv.\n"
        )
        raise SystemExit(2)
    return feedparser


def clean_text(text: str, limit: int = 280) -> str:
    text = html.unescape(text or "")
    text = TAG_RE.sub(" ", text)
    text = SPACE_RE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def ensure_bootstrap_file(path: Path, content: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def ensure_profiles(path: Path) -> None:
    ensure_bootstrap_file(path, DEFAULT_PROFILES_BOOTSTRAP)


def utc_timestamp_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_backup_label(label: str) -> str:
    label = (label or "backup").strip().lower()
    label = re.sub(r"[^a-z0-9._-]+", "-", label)
    label = label.strip("-._")
    return label or "backup"


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def infer_kind(url: str) -> str:
    u = url.lower()
    if "youtube.com/feeds/videos.xml" in u:
        return "youtube"
    if "arxiv.org/rss/" in u:
        return "arxiv"
    if "wikicfp" in u or "cstheoryevents" in u:
        return "cfp"
    if "mathjobs" in u or "cstheory-jobs" in u:
        return "jobs"
    if any(x in u for x in ["bbc", "nytimes", "slashdot", "howtogeek", "ycombinator", "quanta"]):
        return "news"
    return "blog"


def infer_tag_priority(url: str):
    u = url.lower()
    kind = infer_kind(url)
    if kind == "youtube":
        return "video", 2, kind
    if kind == "cfp":
        return "events", 8, kind
    if kind == "jobs":
        return "jobs", 8, kind
    if kind == "news":
        return "general", 3, kind
    if "arxiv.org/rss/cs" in u and u.rstrip("/").endswith("/rss/cs"):
        return "research", 6, "arxiv"
    if "arxiv.org/rss/" in u:
        return "research", 10, "arxiv"
    if any(x in u for x in ["theory", "graph", "math", "combin", "complexity", "cstheory", "philtcs"]):
        return "research", 9, kind
    return "research", 7, kind


def migrate_legacy_feeds(legacy_file: Path, feeds_tsv: Path, force: bool = False):
    if feeds_tsv.exists() and not force:
        return False
    if not legacy_file.exists():
        return False
    urls = []
    for raw in legacy_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    feeds_tsv.parent.mkdir(parents=True, exist_ok=True)
    with feeds_tsv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(["enabled", "tag", "priority", "kind", "url", "notes"])
        for url in urls:
            tag, priority, kind = infer_tag_priority(url)
            writer.writerow([1, tag, priority, kind, url, "migrated from legacy feeds.txt"])
    return True


def ensure_feeds_tsv(feeds_tsv: Path, legacy_file: Path) -> None:
    if feeds_tsv.exists():
        return
    if migrate_legacy_feeds(legacy_file, feeds_tsv, force=False):
        return
    ensure_bootstrap_file(feeds_tsv, DEFAULT_FEEDS_BOOTSTRAP)


def load_profiles(path: Path):
    ensure_profiles(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, list):
                out[str(k)] = [str(x).strip().lower() for x in v if str(x).strip()]
    return out


def load_feeds(feeds_tsv: Path):
    ensure_feeds_tsv(feeds_tsv, DEFAULT_LEGACY_FEEDS_FILE)
    rows = []
    with feeds_tsv.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            url = (row.get("url") or "").strip()
            if not url:
                continue
            enabled_raw = str(row.get("enabled", "1")).strip().lower()
            enabled = enabled_raw not in {"0", "false", "no", "off"}
            tag = (row.get("tag") or "research").strip().lower() or "research"
            if tag not in KNOWN_TAGS:
                tag = "research"
            kind = (row.get("kind") or infer_kind(url)).strip().lower() or infer_kind(url)
            try:
                priority = int(str(row.get("priority") or "5").strip())
            except (ValueError, TypeError):
                priority = 5
            notes = (row.get("notes") or "").strip()
            rows.append({
                "enabled": enabled,
                "tag": tag,
                "priority": max(0, min(priority, 10)),
                "kind": kind,
                "url": url,
                "notes": notes,
            })
    return rows


def save_feeds(feeds_tsv: Path, rows) -> None:
    feeds_tsv.parent.mkdir(parents=True, exist_ok=True)
    from io import StringIO
    buf = StringIO()
    writer = csv.writer(buf, delimiter="\t")
    writer.writerow(["enabled", "tag", "priority", "kind", "url", "notes"])
    for row in rows:
        writer.writerow([
            1 if row.get("enabled", True) else 0,
            row.get("tag", "research"),
            row.get("priority", 5),
            row.get("kind", infer_kind(row.get("url", ""))),
            row.get("url", ""),
            row.get("notes", ""),
        ])
    atomic_write_text(feeds_tsv, buf.getvalue())


def ensure_backup_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def rotate_backups(backup_dir: Path, keep: int = 50) -> None:
    backups = sorted([p for p in backup_dir.glob("feeds-*.tsv") if p.is_file()])
    excess = len(backups) - keep
    if excess <= 0:
        return
    for path in backups[:excess]:
        try:
            path.unlink()
        except OSError:
            pass


def write_backup_snapshot(backup_dir: Path, content: str, reason: str = "manual") -> Path:
    ensure_backup_dir(backup_dir)
    name = f"feeds-{utc_timestamp_label()}-{safe_backup_label(reason)}.tsv"
    path = backup_dir / name
    atomic_write_text(path, content)
    rotate_backups(backup_dir)
    return path


def backup_current_feeds(feeds_tsv: Path, backup_dir: Path, reason: str = "manual"):
    if not feeds_tsv.exists():
        return None
    return write_backup_snapshot(backup_dir, feeds_tsv.read_text(encoding="utf-8"), reason=reason)


def list_backups(backup_dir: Path):
    if not backup_dir.exists():
        return []
    return sorted([p for p in backup_dir.glob("feeds-*.tsv") if p.is_file()], reverse=True)


def resolve_backup_path(backup_dir: Path, value: str) -> Path:
    raw = (value or "").strip()
    if not raw:
        backups = list_backups(backup_dir)
        if not backups:
            raise SystemExit(f"No backup files found under {backup_dir}")
        return backups[0]
    candidate = Path(raw)
    if candidate.exists():
        return candidate
    candidate = backup_dir / raw
    if candidate.exists():
        return candidate
    matches = [p for p in list_backups(backup_dir) if p.name == raw]
    if matches:
        return matches[0]
    raise SystemExit(f"Backup not found: {value}")


def save_feeds_with_backup(feeds_tsv: Path, rows, backup_dir: Path, reason: str):
    backup_path = backup_current_feeds(feeds_tsv, backup_dir, reason=reason)
    save_feeds(feeds_tsv, rows)
    return backup_path


def parse_feeds_tsv_text(text: str):
    rows = []
    from io import StringIO
    reader = csv.DictReader(StringIO(text), delimiter="\t")
    if not reader.fieldnames:
        raise SystemExit("Input TSV is empty or missing a header row.")
    fieldnames = [str(x).strip() for x in reader.fieldnames]
    if "url" not in fieldnames:
        raise SystemExit("Input TSV must contain a 'url' column.")
    for raw in reader:
        url = (raw.get("url") or "").strip()
        if not url:
            continue
        enabled_raw = str(raw.get("enabled", "1")).strip().lower()
        enabled = enabled_raw not in {"0", "false", "no", "off"}
        tag = (raw.get("tag") or "").strip().lower()
        kind = (raw.get("kind") or "").strip().lower()
        notes = (raw.get("notes") or "").strip()
        try:
            priority = int(str(raw.get("priority") or "").strip() or infer_tag_priority(url)[1])
        except (ValueError, TypeError):
            priority = infer_tag_priority(url)[1]
        if not tag:
            tag = infer_tag_priority(url)[0]
        if tag not in KNOWN_TAGS:
            tag = "research"
        if not kind:
            kind = infer_kind(url)
        rows.append({
            "enabled": enabled,
            "tag": tag,
            "priority": max(0, min(int(priority), 10)),
            "kind": kind,
            "url": url,
            "notes": notes,
        })
    return rows


def merge_feed_rows(base_rows, incoming_rows):
    out = []
    seen = {}
    for row in base_rows:
        norm = normalize_url(row.get("url", ""))
        if not norm:
            continue
        out.append(row)
        seen[norm] = len(out) - 1
    for row in incoming_rows:
        norm = normalize_url(row.get("url", ""))
        if not norm:
            continue
        if norm in seen:
            out[seen[norm]] = row
        else:
            out.append(row)
            seen[norm] = len(out) - 1
    return out


def load_state(path: Path):
    if not path.exists():
        return {"seen_order": [], "feeds": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"seen_order": [], "feeds": {}}
    if not isinstance(data, dict):
        return {"seen_order": [], "feeds": {}}
    seen_order = data.get("seen_order", [])
    if not isinstance(seen_order, list):
        seen_order = []
    feeds = data.get("feeds", {})
    if not isinstance(feeds, dict):
        feeds = {}
    return {"seen_order": [str(x) for x in seen_order], "feeds": feeds}


def save_state(path: Path, state):
    compact = {
        "seen_order": state.get("seen_order", [])[-STATE_LIMIT:],
        "feeds": state.get("feeds", {}),
    }
    atomic_write_text(path, json.dumps(compact, ensure_ascii=False, indent=2))


def normalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=False)
    filtered_query = []
    for key in sorted(query):
        if key.lower().startswith("utm_"):
            continue
        if key.lower() in {"feature", "si"}:
            continue
        for value in query[key]:
            filtered_query.append(f"{key}={value}")
    query_str = "&".join(filtered_query)
    return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path, "", query_str, ""))


def entry_timestamp(entry) -> float:
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if struct:
            try:
                return time.mktime(struct)
            except (TypeError, ValueError, OverflowError):
                pass
    for key in ("published", "updated"):
        raw = entry.get(key)
        if raw:
            try:
                return parsedate_to_datetime(raw).timestamp()
            except (TypeError, ValueError, OverflowError):
                pass
    return 0.0


def dedup_key(kind: str, entry) -> str:
    link = entry.get("link", "") or ""
    ident = entry.get("id", "") or ""
    raw = " | ".join([
        ident.strip(),
        normalize_url(link),
        (entry.get("title") or "").strip().lower(),
    ])
    if kind == "arxiv":
        for blob in (ident, link, entry.get("title", "")):
            m = ARXIV_RE.search(blob or "")
            if m:
                return f"arxiv:{m.group(1)}"
    if kind == "youtube":
        for blob in (link, ident):
            m = YT_RE.search(blob or "")
            if m:
                return f"yt:{m.group(1)}"
    if "stackexchange.com" in link or "stackexchange.com" in ident:
        for blob in (link, ident):
            m = SE_RE.search(blob or "")
            if m:
                return f"stackexchange:{m.group(1)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def keyword_bonus(profile_terms, text: str) -> int:
    if not profile_terms:
        return 0
    hay = text.lower()
    hits = 0
    for term in profile_terms:
        if term and term in hay:
            hits += 1
    return min(40, hits * 8)


def freshness_bonus(ts: float, tag: str) -> int:
    if ts <= 0:
        return 0
    age_hours = max(0.0, (now_ts() - ts) / 3600.0)
    if age_hours <= 12:
        bonus = 40
    elif age_hours <= 24:
        bonus = 30
    elif age_hours <= 72:
        bonus = 20
    elif age_hours <= 168:
        bonus = 10
    else:
        bonus = 0
    if tag == "video":
        bonus = min(bonus, 20)
    return bonus


def compute_score(feed_row, entry, profile_terms):
    text = " ".join([
        entry.get("title", "") or "",
        entry.get("summary", "") or "",
        entry.get("description", "") or "",
    ])
    score = int(feed_row["priority"]) * 100
    score += freshness_bonus(entry_timestamp(entry), feed_row["tag"])
    score += keyword_bonus(profile_terms, text)
    if feed_row["tag"] == "video":
        score -= 15
    if feed_row["tag"] == "general":
        score -= 10
    return score


def iso_dt(ts: float):
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def build_digest(title: str, items, profile_name: str = "") -> str:
    lines = [f"# {title}", ""]
    if profile_name:
        lines.append(f"Profile: {profile_name}")
        lines.append("")
    if not items:
        lines.append("No new items found.")
        lines.append("")
        return "\n".join(lines)
    for idx, item in enumerate(items, 1):
        lines.append(f"## {idx}. {item['title']}")
        lines.append(f"- Feed: {item['feed_title']}")
        lines.append(f"- Tag: {item['tag']}")
        lines.append(f"- Priority: {item['priority']}")
        lines.append(f"- Score: {item['score']}")
        if item["published"]:
            lines.append(f"- Published: {item['published']}")
        if item["link"]:
            lines.append(f"- Link: {item['link']}")
        lines.append("")
        lines.append(item["summary"] or "No summary available.")
        lines.append("")
    return "\n".join(lines)


def write_digest(path: Path, content: str) -> None:
    atomic_write_text(path, content)


def fetch_items(feeds, state, per_feed_limit: int, summary_limit: int, selected_tag: str = "", profile_terms=None, include_disabled: bool = False, mark_seen: bool = True, parallel=0):
    feedparser = ensure_feedparser()
    seen_order = list(state.get("seen_order", []))
    seen_set = set(seen_order)
    run_seen = set()
    profile_terms = profile_terms or []
    items = []
    health_rows = []
    feeds_state = state.setdefault("feeds", {})

    # Filter active feeds
    active_feeds = [f for f in feeds
                    if (include_disabled or f["enabled"])
                    and (not selected_tag or f["tag"] == selected_tag)]

    # Parallel feed fetching (I/O-bound)
    def _fetch_one(url):
        return feedparser.parse(url)

    _parallel = parallel
    if _parallel == 0:
        _cpus = os.cpu_count() or 2
        _parallel = min(_cpus * 2, 16)
    if _parallel > 1 and len(active_feeds) > 1:
        from concurrent.futures import ThreadPoolExecutor
        urls = [f["url"] for f in active_feeds]
        with ThreadPoolExecutor(max_workers=min(_parallel, len(urls))) as pool:
            parsed_results = list(pool.map(_fetch_one, urls))
    else:
        parsed_results = [_fetch_one(f["url"]) for f in active_feeds]

    # Process results sequentially (state mutation)
    for feed_row, parsed in zip(active_feeds, parsed_results):
        url = feed_row["url"]
        kind = feed_row["kind"]
        started = now_ts()
        status = "ok"
        last_error = ""
        entry_count = 0
        if parsed.get("bozo") and not parsed.get("entries"):
            status = "error"
            last_error = str(parsed.get("bozo_exception", "malformed feed"))

        meta = feeds_state.setdefault(url, {})
        meta["last_fetch"] = iso_dt(started)
        meta["tag"] = feed_row["tag"]
        meta["kind"] = kind
        meta["priority"] = feed_row["priority"]

        if status == "error" or parsed is None:
            meta["failure_count"] = int(meta.get("failure_count", 0)) + 1
            meta["last_error"] = last_error or "parse failed"
            health_rows.append({
                "tag": feed_row["tag"],
                "kind": kind,
                "url": url,
                "status": "error",
                "entries": 0,
                "new_items": 0,
                "last_success": meta.get("last_success", ""),
                "failure_count": meta.get("failure_count", 0),
                "last_error": meta.get("last_error", ""),
            })
            continue

        if getattr(parsed, "bozo", False):
            exc = getattr(parsed, "bozo_exception", None)
            if exc:
                status = "warning"
                last_error = str(exc)

        feed_title = parsed.feed.get("title", url)
        recent_entries = list(parsed.entries)[: max(0, per_feed_limit)]
        entry_count = len(recent_entries)
        new_items = 0
        last_ts = 0.0

        for entry in recent_entries:
            ts = entry_timestamp(entry)
            last_ts = max(last_ts, ts)
            key = dedup_key(kind, entry)
            if key in seen_set or key in run_seen:
                continue
            score = compute_score(feed_row, entry, profile_terms)
            item = {
                "key": key,
                "tag": feed_row["tag"],
                "kind": kind,
                "priority": feed_row["priority"],
                "score": score,
                "source_url": url,
                "feed_title": feed_title,
                "title": (entry.get("title") or "(untitled)").strip(),
                "link": entry.get("link", "") or "",
                "summary": clean_text(entry.get("summary", "") or entry.get("description", ""), limit=summary_limit),
                "published": entry.get("published", "") or entry.get("updated", "") or iso_dt(ts),
                "timestamp": ts,
            }
            items.append(item)
            run_seen.add(key)
            if mark_seen:
                seen_order.append(key)
                seen_set.add(key)
            new_items += 1

        if status == "ok":
            meta["last_success"] = iso_dt(now_ts())
            meta["failure_count"] = 0
            meta["last_error"] = ""
        else:
            meta["last_error"] = last_error
            meta["failure_count"] = int(meta.get("failure_count", 0))
        meta["last_feed_title"] = feed_title
        meta["last_item_time"] = iso_dt(last_ts)
        meta["last_entry_count"] = entry_count
        meta["last_new_items"] = new_items
        health_rows.append({
            "tag": feed_row["tag"],
            "kind": kind,
            "url": url,
            "status": status,
            "entries": entry_count,
            "new_items": new_items,
            "last_success": meta.get("last_success", ""),
            "failure_count": meta.get("failure_count", 0),
            "last_error": meta.get("last_error", ""),
        })

    state["seen_order"] = seen_order[-STATE_LIMIT:]
    return items, health_rows


def cmd_run(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    ensure_profiles(args.profiles_file)
    feeds = load_feeds(args.feeds_tsv)
    state = load_state(args.state_file)
    profiles = load_profiles(args.profiles_file)
    profile_terms = []
    active_profile = ""
    if args.profile:
        if args.profile not in profiles:
            print(f"WARNING: profile '{args.profile}' not found in {args.profiles_file}, ignoring", file=sys.stderr)
        else:
            profile_terms = profiles[args.profile]
            active_profile = args.profile

    selected_tag = "" if args.all_tags else args.tag
    items, _health = fetch_items(
        feeds=feeds,
        state=state,
        per_feed_limit=args.per_feed_limit,
        summary_limit=args.summary_limit,
        selected_tag=selected_tag,
        profile_terms=profile_terms,
        include_disabled=args.include_disabled,
        mark_seen=not args.no_mark_seen,
        parallel=getattr(args, "parallel", 0),
    )

    items.sort(key=lambda item: (item["score"], item["timestamp"]), reverse=True)

    _write_digest_stubs(items)

    by_tag = {}
    for item in items:
        by_tag.setdefault(item["tag"], []).append(item)

    outputs = {}
    args.digest_dir.mkdir(parents=True, exist_ok=True)
    if args.all_tags:
        for tag, tag_items in sorted(by_tag.items()):
            path = args.digest_dir / f"rss-{tag}.md"
            write_digest(path, build_digest(f"RSS Digest: {tag}", tag_items[: args.max_items], profile_name=active_profile))
            outputs[tag] = str(path)
        all_path = args.digest_dir / "rss-all.md"
        write_digest(all_path, build_digest("RSS Digest: all", items[: args.max_items], profile_name=active_profile))
        outputs["all"] = str(all_path)
    else:
        out_path = args.digest_dir / f"rss-{args.tag}.md"
        write_digest(out_path, build_digest(f"RSS Digest: {args.tag}", by_tag.get(args.tag, [])[: args.max_items], profile_name=active_profile))
        outputs[args.tag] = str(out_path)

    save_state(args.state_file, state)
    print(json.dumps({
        "status": "ok",
        "tag": "all" if args.all_tags else args.tag,
        "profile": args.profile,
        "count": len(items),
        "outputs": outputs,
    }, ensure_ascii=False, indent=2))


def cmd_doctor(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    ensure_profiles(args.profiles_file)
    feeds = load_feeds(args.feeds_tsv)
    state = load_state(args.state_file)
    profiles = load_profiles(args.profiles_file)
    profile_terms = []
    if args.profile:
        if args.profile not in profiles:
            print(f"WARNING: profile '{args.profile}' not found in {args.profiles_file}, ignoring", file=sys.stderr)
        else:
            profile_terms = profiles[args.profile]
    _items, health_rows = fetch_items(
        feeds=feeds,
        state=state,
        per_feed_limit=max(1, args.per_feed_limit),
        summary_limit=120,
        selected_tag=args.tag,
        profile_terms=profile_terms,
        include_disabled=args.include_disabled,
        mark_seen=False,
    )
    if not args.no_save_state:
        save_state(args.state_file, state)
    if args.json:
        print(json.dumps(health_rows, ensure_ascii=False, indent=2))
        return
    headers = ["tag", "kind", "status", "entries", "new_items", "failures", "last_success", "url"]
    print("\t".join(headers))
    for row in health_rows:
        print("\t".join([
            str(row.get("tag", "")),
            str(row.get("kind", "")),
            str(row.get("status", "")),
            str(row.get("entries", 0)),
            str(row.get("new_items", 0)),
            str(row.get("failure_count", 0)),
            str(row.get("last_success", "")),
            str(row.get("url", "")),
        ]))


def cmd_list_feeds(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    rows = load_feeds(args.feeds_tsv)
    print("enabled\ttag\tpriority\tkind\turl\tnotes")
    for row in rows:
        if args.tag and row["tag"] != args.tag:
            continue
        print("\t".join([
            "1" if row["enabled"] else "0",
            row["tag"],
            str(row["priority"]),
            row["kind"],
            row["url"],
            row["notes"],
        ]))


def find_feed_index(rows, url: str) -> int:
    target = normalize_url(url)
    for idx, row in enumerate(rows):
        if normalize_url(row.get("url", "")) == target:
            return idx
    return -1


def feed_matches_query(row, query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    hay = "\n".join([
        row.get("url", ""),
        row.get("tag", ""),
        row.get("kind", ""),
        row.get("notes", ""),
    ]).lower()
    return q in hay


def serialize_row(row):
    return {
        "enabled": bool(row.get("enabled", True)),
        "tag": row.get("tag", "research"),
        "priority": int(row.get("priority", 5)),
        "kind": row.get("kind", infer_kind(row.get("url", ""))),
        "url": row.get("url", ""),
        "notes": row.get("notes", ""),
    }


def cmd_backup_feeds(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    backup_path = backup_current_feeds(args.feeds_tsv, args.backup_dir, reason=args.reason or "manual")
    if backup_path is None:
        raise SystemExit(f"No feeds.tsv exists yet at {args.feeds_tsv}")
    print(json.dumps({
        "status": "backed_up",
        "backup": str(backup_path),
    }, ensure_ascii=False, indent=2))


def cmd_list_backups(args):
    backups = list_backups(args.backup_dir)
    if args.json:
        print(json.dumps([
            {"name": p.name, "path": str(p), "size": p.stat().st_size, "mtime": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()}
            for p in backups
        ], ensure_ascii=False, indent=2))
        return
    print("name	mtime_utc	size	path")
    for p in backups:
        stat = p.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        print("	".join([p.name, mtime, str(stat.st_size), str(p)]))


def cmd_restore_feeds_backup(args):
    ensure_backup_dir(args.backup_dir)
    backup_path = resolve_backup_path(args.backup_dir, args.backup)
    if not backup_path.is_file():
        raise SystemExit(f"Backup path is not a file: {backup_path}")
    pre_restore_backup = backup_current_feeds(args.feeds_tsv, args.backup_dir, reason="pre-restore")
    content = backup_path.read_text(encoding="utf-8")
    parse_feeds_tsv_text(content)
    atomic_write_text(args.feeds_tsv, content)
    print(json.dumps({
        "status": "restored",
        "restored_from": str(backup_path),
        "pre_restore_backup": str(pre_restore_backup) if pre_restore_backup else None,
        "feeds_tsv": str(args.feeds_tsv),
    }, ensure_ascii=False, indent=2))


def cmd_add_feed(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    rows = load_feeds(args.feeds_tsv)
    url = args.url.strip()
    idx = find_feed_index(rows, url)
    if idx >= 0:
        row = rows[idx]
        row["enabled"] = True
        row["url"] = url
        if args.tag:
            row["tag"] = args.tag
        if args.priority is not None:
            row["priority"] = args.priority
        if args.kind:
            row["kind"] = args.kind
        if args.notes is not None:
            row["notes"] = args.notes
        backup_path = save_feeds_with_backup(args.feeds_tsv, rows, args.backup_dir, reason="update-feed")
        print(json.dumps({"status": "updated", "url": url, "backup": str(backup_path) if backup_path else None}, ensure_ascii=False))
        return
    tag = args.tag
    kind = args.kind or ""
    priority = args.priority
    if not tag or not kind or priority is None:
        inferred_tag, inferred_priority, inferred_kind = infer_tag_priority(url)
        tag = tag or inferred_tag
        kind = kind or inferred_kind
        priority = priority if priority is not None else inferred_priority
    rows.append({
        "enabled": True,
        "tag": tag,
        "priority": int(priority),
        "kind": kind,
        "url": url,
        "notes": args.notes or "",
    })
    backup_path = save_feeds_with_backup(args.feeds_tsv, rows, args.backup_dir, reason="add-feed")
    print(json.dumps({"status": "added", "url": url, "tag": tag, "priority": priority, "kind": kind, "backup": str(backup_path) if backup_path else None}, ensure_ascii=False))


def set_feed_enabled(args, enabled: bool):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    rows = load_feeds(args.feeds_tsv)
    url = args.url.strip()
    idx = find_feed_index(rows, url)
    if idx < 0:
        raise SystemExit(f"Feed not found: {url}")
    rows[idx]["enabled"] = enabled
    backup_path = save_feeds_with_backup(args.feeds_tsv, rows, args.backup_dir, reason="enable-feed" if enabled else "disable-feed")
    print(json.dumps({"status": "ok", "enabled": enabled, "url": rows[idx]["url"], "backup": str(backup_path) if backup_path else None}, ensure_ascii=False))


def cmd_remove_feed(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    rows = load_feeds(args.feeds_tsv)
    url = args.url.strip()
    idx = find_feed_index(rows, url)
    if idx < 0:
        raise SystemExit(f"Feed not found: {url}")
    removed = rows.pop(idx)
    backup_path = save_feeds_with_backup(args.feeds_tsv, rows, args.backup_dir, reason="remove-feed")
    print(json.dumps({"status": "removed", "url": removed.get("url", url), "backup": str(backup_path) if backup_path else None}, ensure_ascii=False))


def cmd_search_feeds(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    rows = load_feeds(args.feeds_tsv)
    matches = []
    for row in rows:
        if args.tag and row["tag"] != args.tag:
            continue
        if args.enabled_only and not row["enabled"]:
            continue
        if args.disabled_only and row["enabled"]:
            continue
        if not feed_matches_query(row, args.query):
            continue
        matches.append(serialize_row(row))
    if args.json:
        print(json.dumps(matches, ensure_ascii=False, indent=2))
        return
    print("enabled	tag	priority	kind	url	notes")
    for row in matches:
        print("	".join([
            "1" if row["enabled"] else "0",
            row["tag"],
            str(row["priority"]),
            row["kind"],
            row["url"],
            row["notes"],
        ]))


def cmd_edit_feed(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    rows = load_feeds(args.feeds_tsv)
    url = args.url.strip()
    idx = find_feed_index(rows, url)
    if idx < 0:
        raise SystemExit(f"Feed not found: {url}")
    row = rows[idx]

    if args.set_url:
        new_url = args.set_url.strip()
        other_idx = find_feed_index(rows, new_url)
        if other_idx >= 0 and other_idx != idx:
            raise SystemExit(f"Another feed already exists with URL: {new_url}")
        row["url"] = new_url
    if args.tag:
        row["tag"] = args.tag
    if args.priority is not None:
        row["priority"] = max(0, min(int(args.priority), 10))
    if args.kind:
        row["kind"] = args.kind
    if args.notes is not None:
        row["notes"] = args.notes
    if args.enable:
        row["enabled"] = True
    if args.disable:
        row["enabled"] = False

    backup_path = save_feeds_with_backup(args.feeds_tsv, rows, args.backup_dir, reason="edit-feed")
    print(json.dumps({"status": "edited", "feed": serialize_row(row), "backup": str(backup_path) if backup_path else None}, ensure_ascii=False, indent=2))


def cmd_migrate_legacy_feeds(args):
    migrated = migrate_legacy_feeds(args.legacy_feeds_file, args.feeds_tsv, force=args.force)
    print(json.dumps({
        "status": "ok",
        "migrated": bool(migrated),
        "feeds_tsv": str(args.feeds_tsv),
        "legacy_file": str(args.legacy_feeds_file),
    }, ensure_ascii=False, indent=2))


def cmd_export_feeds_tsv(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    content = args.feeds_tsv.read_text(encoding="utf-8")
    if args.output == "-":
        sys.stdout.write(content)
    else:
        out_path = Path(args.output)
        atomic_write_text(out_path, content)
        print(json.dumps({
            "status": "exported",
            "source": str(args.feeds_tsv),
            "output": str(out_path),
        }, ensure_ascii=False, indent=2))


def cmd_import_feeds_tsv(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    if args.input == "-":
        content = sys.stdin.read()
    else:
        in_path = Path(args.input)
        content = in_path.read_text(encoding="utf-8")
    imported_rows = parse_feeds_tsv_text(content)
    existing_rows = [] if args.replace else load_feeds(args.feeds_tsv)
    merged_rows = imported_rows if args.replace else merge_feed_rows(existing_rows, imported_rows)
    backup_path = save_feeds_with_backup(args.feeds_tsv, merged_rows, args.backup_dir, reason="import-replace" if args.replace else "import-merge")
    print(json.dumps({
        "status": "imported",
        "mode": "replace" if args.replace else "merge",
        "imported_count": len(imported_rows),
        "final_count": len(merged_rows),
        "feeds_tsv": str(args.feeds_tsv),
        "backup": str(backup_path) if backup_path else None,
    }, ensure_ascii=False, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(description="Ranked RSS digest tool for OpenClaw and standalone use.")
    parser.add_argument("--feeds-tsv", type=Path, default=DEFAULT_FEEDS_TSV)
    parser.add_argument("--legacy-feeds-file", type=Path, default=DEFAULT_LEGACY_FEEDS_FILE)
    parser.add_argument("--profiles-file", type=Path, default=DEFAULT_PROFILES_FILE)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--digest-dir", type=Path, default=DEFAULT_DIGEST_DIR)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)

    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Fetch feeds and write ranked digests.")
    run.add_argument("--tag", choices=KNOWN_TAGS, default="research")
    run.add_argument("--all-tags", action="store_true")
    run.add_argument("--profile", default="")
    run.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    run.add_argument("--per-feed-limit", type=int, default=DEFAULT_PER_FEED_LIMIT)
    run.add_argument("--summary-limit", type=int, default=280)
    run.add_argument("--include-disabled", action="store_true")
    run.add_argument("--no-mark-seen", action="store_true")
    run.add_argument("--parallel", type=int, default=0, help="Parallel feed fetches (0=auto, 1=sequential)")
    run.set_defaults(func=cmd_run)

    doctor = sub.add_parser("doctor", help="Check feed health and fetch status.")
    doctor.add_argument("--tag", choices=[""] + KNOWN_TAGS, default="")
    doctor.add_argument("--profile", default="")
    doctor.add_argument("--per-feed-limit", type=int, default=1)
    doctor.add_argument("--include-disabled", action="store_true")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--no-save-state", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    list_feeds = sub.add_parser("list-feeds", help="List configured feeds.")
    list_feeds.add_argument("--tag", choices=[""] + KNOWN_TAGS, default="")
    list_feeds.set_defaults(func=cmd_list_feeds)

    backup_cmd = sub.add_parser("backup-feeds", help="Create a manual backup of feeds.tsv.")
    backup_cmd.add_argument("--reason", default="manual")
    backup_cmd.set_defaults(func=cmd_backup_feeds)

    list_backups_cmd = sub.add_parser("list-backups", help="List available feeds.tsv backups.")
    list_backups_cmd.add_argument("--json", action="store_true")
    list_backups_cmd.set_defaults(func=cmd_list_backups)

    restore_cmd = sub.add_parser("restore-feeds-backup", help="Restore feeds.tsv from a backup file name or path.")
    restore_cmd.add_argument("backup", nargs="?", default="", help="Backup file name/path. Defaults to the newest backup.")
    restore_cmd.set_defaults(func=cmd_restore_feeds_backup)

    add = sub.add_parser("add-feed", help="Add a feed or update an existing one.")
    add.add_argument("url")
    add.add_argument("--tag", choices=KNOWN_TAGS)
    add.add_argument("--priority", type=int)
    add.add_argument("--kind", default="")
    add.add_argument("--notes", default="")
    add.set_defaults(func=cmd_add_feed)

    disable = sub.add_parser("disable-feed", help="Disable a feed by URL.")
    disable.add_argument("url")
    disable.set_defaults(func=lambda a: set_feed_enabled(a, False))

    enable = sub.add_parser("enable-feed", help="Enable a feed by URL.")
    enable.add_argument("url")
    enable.set_defaults(func=lambda a: set_feed_enabled(a, True))

    remove = sub.add_parser("remove-feed", help="Remove a feed by URL.")
    remove.add_argument("url")
    remove.set_defaults(func=cmd_remove_feed)

    search = sub.add_parser("search-feeds", help="Search configured feeds by substring match.")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--tag", choices=[""] + KNOWN_TAGS, default="")
    search.add_argument("--enabled-only", action="store_true")
    search.add_argument("--disabled-only", action="store_true")
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=cmd_search_feeds)

    edit = sub.add_parser("edit-feed", help="Edit an existing feed by URL.")
    edit.add_argument("url")
    edit.add_argument("--set-url", default="")
    edit.add_argument("--tag", choices=KNOWN_TAGS)
    edit.add_argument("--priority", type=int)
    edit.add_argument("--kind", default="")
    edit.add_argument("--notes")
    edit.add_argument("--enable", action="store_true")
    edit.add_argument("--disable", action="store_true")
    edit.set_defaults(func=cmd_edit_feed)

    migrate = sub.add_parser("migrate-legacy-feeds", help="Convert a flat feeds.txt to feeds.tsv.")
    migrate.add_argument("--force", action="store_true")
    migrate.set_defaults(func=cmd_migrate_legacy_feeds)

    export_cmd = sub.add_parser("export-feeds-tsv", help="Export the current feeds.tsv for bulk editing.")
    export_cmd.add_argument("--output", default="-", help="Output path, or '-' for stdout.")
    export_cmd.set_defaults(func=cmd_export_feeds_tsv)

    import_cmd = sub.add_parser("import-feeds-tsv", help="Import a feeds.tsv file and merge or replace the current config.")
    import_cmd.add_argument("input", help="Input TSV path, or '-' for stdin.")
    import_cmd.add_argument("--replace", action="store_true", help="Replace the current feeds.tsv instead of merging by normalized URL.")
    import_cmd.set_defaults(func=cmd_import_feeds_tsv)

    return parser


def _write_digest_stubs(items):
    """Write minimal memory stubs for digest items not yet in memory/papers/."""
    workspace = os.environ.get("AAS_RUNTIME_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE") or os.path.join(os.path.expanduser("~"), ".codex", "runtime", "workspace")
    papers_dir = Path(workspace) / "memory" / "papers"
    ingested_file = Path(workspace) / "data" / "library" / "ingested.json"
    papers_dir.mkdir(parents=True, exist_ok=True)

    ingested_ids = set()
    if ingested_file.exists():
        try:
            records = json.loads(ingested_file.read_text())
            ingested_ids = {r["id"] for r in records if r.get("source") == "digest"}
        except Exception:
            pass

    now = datetime.now(timezone.utc).isoformat()
    new_records = []

    for item in items:
        item_id = item.get("key", "")
        if not item_id or item_id in ingested_ids:
            continue
        title = item.get("title", "Unknown")
        link = item.get("link", "")
        summary = item.get("summary", "")[:500]
        tag = item.get("tag", "")
        score = item.get("score", 0)
        published = item.get("published", "")
        feed_title = item.get("feed_title", "")

        slug = re.sub(r"[^\w]", "_", item_id.lower())[:60]
        out_file = papers_dir / f"digest_{slug}.md"
        if out_file.exists():
            continue

        content = (
            f"---\n"
            f"title: \"{title.replace(chr(34), chr(39))}\"\n"
            f"authors: []\n"
            f"year: \"{published[:4]}\"\n"
            f"type: paper\n"
            f"sources:\n"
            f"  zotero: null\n"
            f"  calibre: null\n"
            f"  digest: \"{item_id}\"\n"
            f"tags: [\"{tag}\"]\n"
            f"domain: \"{tag}\"\n"
            f"url: \"{link}\"\n"
            f"feed: \"{feed_title}\"\n"
            f"digest_score: {score}\n"
            f"full_text_available: false\n"
            f"processed_at: \"{now}\"\n"
            f"---\n\n"
            f"## Summary\n\n{summary}\n\n"
            f"## Key results / main ideas\n\n_To be filled on access._\n\n"
            f"## Connections to current research\n\n_To be filled on access._\n"
        )
        try:
            out_file.write_text(content)
            new_records.append({"source": "digest", "id": item_id, "processed_at": now})
        except Exception:
            pass

    if new_records:
        try:
            existing = json.loads(ingested_file.read_text()) if ingested_file.exists() else []
            ingested_file.parent.mkdir(parents=True, exist_ok=True)
            ingested_file.write_text(json.dumps(existing + new_records, indent=2))
        except Exception:
            pass


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
