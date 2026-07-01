#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)
ISBN_CAND_RE = re.compile(r"\b(?:97[89][\- ]?)?\d[\d\- ]{8,16}[\dXx]\b")
DEFAULT_TIMEOUT = 45
UA = "openclaw-getscipapers-skill/2.0"


def default_host_workspace() -> Path:
    env_workspace = os.environ.get("AAS_RUNTIME_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE")
    if env_workspace:
        return Path(env_workspace)
    return Path(__file__).resolve().parents[2]


HOST_WORKSPACE = default_host_workspace()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def json_print(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


@dataclass
class Settings:
    download_dir: Path
    state_dir: Path
    manifest_dir: Path
    telegram_max_bytes: int


def load_settings() -> Settings:
    config = os.environ.get("GETSCIPAPERS_SKILL_CONFIG")
    if config:
        cfg_path = Path(config)
    else:
        if HOST_WORKSPACE.is_dir():
            cfg_path = HOST_WORKSPACE / "data" / "research" / "getscipapers_bot" / "state" / "config.json"
        else:
            cfg_path = Path(__file__).resolve().parent.parent.parent / "data" / "research" / "getscipapers_bot" / "state" / "config.json"
    data = read_json(cfg_path, {})
    # sanitize host paths to container-visible /workspace only when running inside a container
    _in_container = Path("/workspace").is_dir() and not HOST_WORKSPACE.is_dir()
    def _sanitize(p):
        if not p or not _in_container:
            return p
        s = str(p)
        host_runtime = str(HOST_WORKSPACE.parent).replace("\\", "/")
        s_norm = s.replace("\\", "/")
        if s_norm.startswith(host_runtime):
            return s_norm.replace(host_runtime, "/workspace")
        return s
    default_base_raw = os.environ.get("OPENCLAW_WORKSPACE_OVERRIDE")
    default_base = Path(default_base_raw or ("/workspace" if _in_container else str(HOST_WORKSPACE)))
    base_norm = str(default_base).replace("\\", "/").rstrip("/")
    if base_norm.endswith("/data"):
        default_root = default_base / "research" / "getscipapers_bot"
    else:
        default_root = default_base / "data" / "research" / "getscipapers_bot"
    download_dir = Path(_sanitize(data.get("download_dir") or os.environ.get("GETSCIPAPERS_DOWNLOAD_DIR") or (default_root / "downloads")))
    state_dir = Path(_sanitize(data.get("state_dir") or os.environ.get("GETSCIPAPERS_STATE_DIR") or (default_root / "state")))
    manifest_dir = Path(_sanitize(data.get("manifest_dir") or (state_dir / "manifests")))
    telegram_max_bytes = int(data.get("telegram_max_bytes") or os.environ.get("SCI_PAPERS_TELEGRAM_MAX_BYTES") or 52428800)
    return Settings(download_dir=download_dir, state_dir=state_dir, manifest_dir=manifest_dir, telegram_max_bytes=telegram_max_bytes)


def fallback_settings(settings: Settings) -> Settings:
    root = Path(os.environ.get("GETSCIPAPERS_FALLBACK_ROOT") or (Path(tempfile.gettempdir()) / "getscipapers_bot"))
    state_dir = root / "state"
    return Settings(
        download_dir=root / "downloads",
        state_dir=state_dir,
        manifest_dir=state_dir / "manifests",
        telegram_max_bytes=settings.telegram_max_bytes,
    )


def ensure_settings_dirs(settings: Settings) -> Settings:
    try:
        settings.download_dir.mkdir(parents=True, exist_ok=True)
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        settings.manifest_dir.mkdir(parents=True, exist_ok=True)
        return settings
    except OSError:
        fallback = fallback_settings(settings)
        fallback.download_dir.mkdir(parents=True, exist_ok=True)
        fallback.state_dir.mkdir(parents=True, exist_ok=True)
        fallback.manifest_dir.mkdir(parents=True, exist_ok=True)
        return fallback


def norm_doi(raw: str) -> str:
    raw = raw.strip()
    prefixes = [
        "doi:",
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
    ]
    for p in prefixes:
        if raw.lower().startswith(p):
            raw = raw[len(p):]
            break
    return raw.strip().rstrip(".,;)]}")


def norm_isbn(raw: str) -> str:
    return re.sub(r"[^0-9Xx]", "", raw).upper()


def isbn10_checksum_ok(code: str) -> bool:
    if not re.fullmatch(r"\d{9}[\dX]", code):
        return False
    total = sum((10 - i) * (10 if ch == "X" else int(ch)) for i, ch in enumerate(code))
    return total % 11 == 0


def isbn13_checksum_ok(code: str) -> bool:
    if not re.fullmatch(r"\d{13}", code):
        return False
    total = 0
    for i, ch in enumerate(code[:12]):
        total += int(ch) * (1 if i % 2 == 0 else 3)
    check = (10 - (total % 10)) % 10
    return check == int(code[-1])


def extract_isbns(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in ISBN_CAND_RE.finditer(text):
        token = norm_isbn(m.group(0))
        if len(token) == 10 and isbn10_checksum_ok(token) and token not in seen:
            out.append(token)
            seen.add(token)
        elif len(token) == 13 and isbn13_checksum_ok(token) and token not in seen:
            out.append(token)
            seen.add(token)
    return out


def extract_dois(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in DOI_RE.finditer(text):
        token = norm_doi(m.group(0))
        if token and token not in seen:
            out.append(token)
            seen.add(token)
    return out


def read_text_source(value: str) -> str:
    if value == "-":
        return sys.stdin.read()
    p = Path(value)
    if p.exists() and p.is_file():
        return p.read_text(encoding="utf-8", errors="replace")
    return value


def http_json(url: str, timeout: int = DEFAULT_TIMEOUT) -> Any:
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def search_crossref(query: str, limit: int = 5) -> list[dict[str, Any]]:
    url = (
        "https://api.crossref.org/works?rows=%d&select=DOI,title,author,issued,container-title,type,score"
        "&query.bibliographic=%s" % (limit, quote_plus(query))
    )
    data = http_json(url)
    items = data.get("message", {}).get("items", [])
    out = []
    for item in items:
        title = " ".join(item.get("title") or [])
        container = " ".join(item.get("container-title") or [])
        authors = []
        for a in item.get("author") or []:
            name = " ".join(x for x in [a.get("given"), a.get("family")] if x)
            if name:
                authors.append(name)
        year = None
        parts = ((item.get("issued") or {}).get("date-parts") or [])
        if parts and parts[0]:
            year = parts[0][0]
        out.append({
            "doi": item.get("DOI"),
            "title": title,
            "container": container,
            "authors": authors,
            "year": year,
            "type": item.get("type"),
            "score": item.get("score"),
        })
    return out


def search_google_books(query: str, limit: int = 5) -> list[dict[str, Any]]:
    url = f"https://www.googleapis.com/books/v1/volumes?q={quote_plus(query)}&maxResults={limit}"
    data = http_json(url)
    out = []
    for item in data.get("items") or []:
        info = item.get("volumeInfo") or {}
        ids = []
        for ident in info.get("industryIdentifiers") or []:
            t = ident.get("type")
            v = ident.get("identifier")
            if v and t in {"ISBN_10", "ISBN_13"}:
                ids.append(norm_isbn(v))
        out.append({
            "title": info.get("title"),
            "authors": info.get("authors") or [],
            "publishedDate": info.get("publishedDate"),
            "isbn": ids,
            "publisher": info.get("publisher"),
        })
    return out


def search_openlibrary(query: str, limit: int = 5) -> list[dict[str, Any]]:
    url = f"https://openlibrary.org/search.json?q={quote_plus(query)}&limit={limit}"
    data = http_json(url)
    out = []
    for item in data.get("docs") or []:
        isbns = [norm_isbn(x) for x in (item.get("isbn") or []) if isinstance(x, str)]
        out.append({
            "title": item.get("title"),
            "authors": item.get("author_name") or [],
            "year": item.get("first_publish_year"),
            "isbn": isbns[:5],
            "publisher": (item.get("publisher") or [None])[0],
        })
    return out


def clean_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def token_overlap(a: str, b: str) -> float:
    sa = set(clean_text(a).split())
    sb = set(clean_text(b).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(1, len(sa | sb))


def similarity(a: str, b: str) -> float:
    aa = clean_text(a)
    bb = clean_text(b)
    if not aa or not bb:
        return 0.0
    return SequenceMatcher(None, aa, bb).ratio()


def year_from_query(text: str) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return int(m.group(0)) if m else None


def score_paper_candidate(query: str, cand: dict[str, Any]) -> dict[str, Any]:
    qy = year_from_query(query)
    title = cand.get("title") or ""
    score = 0.55 * similarity(query, title) + 0.25 * token_overlap(query, title)
    crossref_score = cand.get("score")
    if isinstance(crossref_score, (int, float)):
        score += min(float(crossref_score) / 100.0, 0.12)
    if qy and cand.get("year") == qy:
        score += 0.08
    if "doi" in cand and cand.get("doi"):
        score += 0.05
    return {"score": round(score, 4), "confidence": confidence_band(score)}


def score_book_candidate(query: str, cand: dict[str, Any]) -> dict[str, Any]:
    title = cand.get("title") or ""
    score = 0.62 * similarity(query, title) + 0.25 * token_overlap(query, title)
    if cand.get("isbn"):
        score += 0.08
    if cand.get("authors"):
        score += 0.03
    return {"score": round(score, 4), "confidence": confidence_band(score)}


def confidence_band(score: float) -> str:
    if score >= 0.88:
        return "very_high"
    if score >= 0.74:
        return "high"
    if score >= 0.58:
        return "medium"
    return "low"


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def run_subprocess(argv: list[str], timeout: int = DEFAULT_TIMEOUT, cwd: str | None = None) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            check=False,
        )
        return {
            "argv": argv,
            "returncode": proc.returncode,
            "stdout": _coerce_text(proc.stdout),
            "stderr": _coerce_text(proc.stderr),
            "elapsed": round(time.time() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": argv,
            "returncode": 124,
            "stdout": _coerce_text(exc.stdout),
            "stderr": _coerce_text(exc.stderr) + "\n[timeout]",
            "elapsed": round(time.time() - started, 3),
        }


def find_getscipapers() -> str | None:
    candidates = [
        os.environ.get("GETSCIPAPERS_BIN"),
        str(Path.home() / ".local" / "bin" / "getscipapers"),
        str(Path.home() / ".getscipapers_venv" / "bin" / "getscipapers"),
        str(Path.home() / ".getscipapers_venv" / "Scripts" / "getscipapers.exe"),
        shutil.which("getscipapers"),
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        # os.access(..., X_OK) is unreliable on Windows, where executability is
        # determined by file extension rather than a POSIX permission bit.
        if os.name == "nt" or os.access(candidate, os.X_OK):
            return candidate
    return None


def introspect() -> dict[str, Any]:
    exe = find_getscipapers()
    info: dict[str, Any] = {"getscipapers_path": exe, "available": bool(exe)}
    if not exe:
        return info
    top = run_subprocess([exe, "--help"], timeout=20)
    info["top_help"] = top
    subcommands: list[str] = []

    # Rather than only scanning the top-level help text, try invoking
    # potential subcommands with --help to detect which modules actually
    # exist on this installation. Some distributions hide subcommand
    # names from the top help text but support them when called directly.
    candidates = ["getpapers", "requestpapers", "request", "gui", "getpaper", "get"]
    for name in candidates:
        res = run_subprocess([exe, name, "--help"], timeout=10)
        # treat returncode 0 as available; some tools print usage and exit 0
        # others may return nonzero but still include help text — check stdout/stderr
        helptext = (res.get("stdout") or "") + "\n" + (res.get("stderr") or "")
        if res.get("returncode") == 0 or "usage:" in helptext.lower() or name in helptext:
            subcommands.append(name)
    info["subcommands_seen"] = subcommands
    subhelp: dict[str, Any] = {}
    features: dict[str, list[str]] = {}
    for name in subcommands:
        # We already ran --help above; call again to capture full help payload
        subhelp[name] = run_subprocess([exe, name, "--help"], timeout=20)
        helptext = (subhelp[name].get("stdout") or "") + "\n" + (subhelp[name].get("stderr") or "")
        present = []
        for flag in ["--doi", "--doi-file", "--search", "--isbn", "--extract-doi-from-pdf", "--no-download", "--non-interactive", "--download-folder"]:
            if flag in helptext:
                present.append(flag)
        features[name] = present
    info["subhelp"] = subhelp
    info["features"] = features
    return info


def latest_files(download_dir: Path, limit: int = 10) -> list[dict[str, Any]]:
    out = []
    if not download_dir.exists():
        return out
    for p in download_dir.rglob("*"):
        if p.is_file():
            st = p.stat()
            out.append({
                "path": str(p),
                "name": p.name,
                "size": st.st_size,
                "mtime": st.st_mtime,
            })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[:limit]


def sha256_file(path: Path, max_bytes: int = 50_000_000) -> str | None:
    if not path.is_file():
        return None
    if path.stat().st_size > max_bytes:
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def file_info(path: str, settings: Settings) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"exists": False, "path": str(p)}
    st = p.stat()
    return {
        "exists": True,
        "path": str(p),
        "size": st.st_size,
        "mtime": st.st_mtime,
        "telegram_auto_send_possible": st.st_size <= settings.telegram_max_bytes,
        "sha256": sha256_file(p),
    }


def resolve_auto(kind: str, query: str) -> dict[str, Any]:
    text = read_text_source(query)
    dois = extract_dois(text)
    isbns = extract_isbns(text)
    out: dict[str, Any] = {
        "kind": kind,
        "query": query,
        "embedded_dois": dois,
        "embedded_isbns": isbns,
        "candidates": {},
    }
    # Parallel API queries (I/O-bound: CrossRef, Google Books, OpenLibrary)
    from concurrent.futures import ThreadPoolExecutor
    tasks = []
    if kind in {"auto", "paper"} and not dois:
        tasks.append(("crossref", lambda: search_crossref(text), "paper"))
    if kind in {"auto", "book"} and not isbns:
        tasks.append(("google_books", lambda: search_google_books(text), "book"))
        tasks.append(("openlibrary", lambda: search_openlibrary(text), "book"))

    def _run_search(task):
        name, fn, score_type = task
        try:
            cands = fn()
            scorer = score_paper_candidate if score_type == "paper" else score_book_candidate
            for c in cands:
                c["rank"] = scorer(text, c)
            cands.sort(key=lambda x: x["rank"]["score"], reverse=True)
            return name, cands, None
        except Exception as exc:
            return name, None, f"{name}: {exc}"

    if len(tasks) > 1:
        _cpus = os.cpu_count() or 2
        _workers = min(_cpus * 2, len(tasks), 8)
        with ThreadPoolExecutor(max_workers=_workers) as pool:
            results = list(pool.map(_run_search, tasks))
    else:
        results = [_run_search(t) for t in tasks]

    for name, cands, error in results:
        if error:
            out.setdefault("errors", []).append(error)
        elif cands is not None:
            out["candidates"][name] = cands
    return out


def choose_best_identifier(kind: str, query: str) -> dict[str, Any]:
    data = resolve_auto(kind, query)
    if data["embedded_dois"]:
        data["selected"] = {
            "identifier_type": "doi",
            "identifier": data["embedded_dois"][0],
            "reason": "embedded_doi",
            "confidence": "very_high",
        }
        return data
    if data["embedded_isbns"]:
        data["selected"] = {
            "identifier_type": "isbn",
            "identifier": data["embedded_isbns"][0],
            "reason": "embedded_isbn",
            "confidence": "very_high",
        }
        return data

    pooled: list[dict[str, Any]] = []
    if kind in {"auto", "paper"}:
        for c in data.get("candidates", {}).get("crossref", []):
            if c.get("doi"):
                pooled.append({
                    "identifier_type": "doi",
                    "identifier": c["doi"],
                    "source": "crossref",
                    "score": c["rank"]["score"],
                    "confidence": c["rank"]["confidence"],
                    "title": c.get("title"),
                    "authors": c.get("authors"),
                    "year": c.get("year"),
                })
    if kind in {"auto", "book"}:
        for src in ("google_books", "openlibrary"):
            for c in data.get("candidates", {}).get(src, []):
                for isbn in c.get("isbn") or []:
                    pooled.append({
                        "identifier_type": "isbn",
                        "identifier": isbn,
                        "source": src,
                        "score": c["rank"]["score"],
                        "confidence": c["rank"]["confidence"],
                        "title": c.get("title"),
                        "authors": c.get("authors"),
                        "year": c.get("year") or c.get("publishedDate"),
                    })
                    break

    pooled.sort(key=lambda x: x["score"], reverse=True)
    data["ranked_identifiers"] = pooled[:5]
    if not pooled:
        data["selected"] = None
        data["selection_status"] = "none"
        return data

    top = pooled[0]
    second = pooled[1] if len(pooled) > 1 else None
    if top["score"] >= 0.74 and (second is None or (top["score"] - second["score"] >= 0.06)):
        data["selected"] = top
        data["selection_status"] = "auto"
    else:
        data["selected"] = None
        data["selection_status"] = "ambiguous"
    return data


def iter_meaningful_lines(text: str) -> list[tuple[int, str]]:
    out = []
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if len(line) < 8:
            continue
        if line.startswith("#"):
            continue
        out.append((i, line))
    return out


def build_manifest(kind: str, source: str, settings: Settings) -> dict[str, Any]:
    text = read_text_source(source)
    items = []
    seen_keys: set[tuple[str, str]] = set()
    for lineno, line in iter_meaningful_lines(text):
        dois = extract_dois(line)
        isbns = extract_isbns(line)
        if dois:
            for doi in dois:
                key = ("doi", doi)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                items.append({
                    "source_line": lineno,
                    "source_text": line,
                    "identifier_type": "doi",
                    "identifier": doi,
                    "confidence": "very_high",
                    "status": "embedded",
                })
            continue
        if isbns:
            for isbn in isbns:
                key = ("isbn", isbn)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                items.append({
                    "source_line": lineno,
                    "source_text": line,
                    "identifier_type": "isbn",
                    "identifier": isbn,
                    "confidence": "very_high",
                    "status": "embedded",
                })
            continue
        best = choose_best_identifier(kind, line)
        if best.get("selected"):
            ident = best["selected"]["identifier"]
            itype = best["selected"]["identifier_type"]
            key = (itype, ident)
            if key not in seen_keys:
                seen_keys.add(key)
                items.append({
                    "source_line": lineno,
                    "source_text": line,
                    "identifier_type": itype,
                    "identifier": ident,
                    "confidence": best["selected"].get("confidence", "medium"),
                    "status": "resolved",
                    "resolution": best["selected"],
                })
        else:
            items.append({
                "source_line": lineno,
                "source_text": line,
                "identifier_type": None,
                "identifier": None,
                "confidence": "low",
                "status": best.get("selection_status", "unresolved"),
                "ranked_identifiers": best.get("ranked_identifiers", []),
                "errors": best.get("errors", []),
            })

    manifest = {
        "kind": kind,
        "created_at": int(time.time()),
        "source": source,
        "items": items,
        "counts": {
            "total_items": len(items),
            "dois": sum(1 for x in items if x.get("identifier_type") == "doi"),
            "isbns": sum(1 for x in items if x.get("identifier_type") == "isbn"),
            "unresolved": sum(1 for x in items if not x.get("identifier")),
        },
    }
    digest = hashlib.sha1((kind + "\n" + text).encode("utf-8", errors="replace")).hexdigest()[:12]
    manifest_path = settings.manifest_dir / f"manifest-{digest}.json"
    write_json(manifest_path, manifest)

    doi_values = [x["identifier"] for x in items if x.get("identifier_type") == "doi" and x.get("identifier")]
    doi_file = None
    if doi_values:
        doi_file = settings.manifest_dir / f"manifest-{digest}.doi.txt"
        doi_file.write_text("\n".join(doi_values) + "\n", encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    manifest["doi_file"] = str(doi_file) if doi_file else None
    write_json(manifest_path, manifest)
    return manifest


def ensure_watch_store(settings: Settings) -> Path:
    path = settings.state_dir / "watches.json"
    if not path.exists():
        write_json(path, {"items": []})
    return path


def _watch_key(payload: dict[str, Any]) -> str:
    services = ",".join(sorted([x.strip() for x in payload.get("services", []) if x.strip()]))
    base = f'{payload.get("kind","")}|{payload.get("identifier_type","")}|{payload.get("identifier","")}|{services}'
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def create_watch(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    store = ensure_watch_store(settings)
    data = read_json(store, {"items": []})
    payload = dict(payload)
    payload["services"] = sorted([x.strip() for x in payload.get("services", []) if x.strip()])
    key = _watch_key(payload)
    now = int(time.time())
    for item in data.get("items", []):
        if item.get("watch_key") == key and item.get("status") in {"active", "waiting", "posted"}:
            item["updated_at"] = now
            item.setdefault("notes_history", []).append({"ts": now, "note": "duplicate create-watch reused existing record"})
            write_json(store, data)
            reused = dict(item)
            reused["reused"] = True
            return reused
    item_id = f"watch-{now}-{key[:8]}"
    payload.update({
        "id": item_id,
        "watch_key": key,
        "created_at": now,
        "updated_at": now,
        "status": "active",
        "sent_file_hashes": [],
        "check_count": 0,
    })
    data.setdefault("items", []).append(payload)
    write_json(store, data)
    return payload


def list_watches(settings: Settings) -> dict[str, Any]:
    store = ensure_watch_store(settings)
    return read_json(store, {"items": []})


def update_watch(settings: Settings, watch_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    store = ensure_watch_store(settings)
    data = read_json(store, {"items": []})
    for item in data.get("items", []):
        if item.get("id") == watch_id:
            if patch.get("sent_file_hash"):
                item.setdefault("sent_file_hashes", [])
                if patch["sent_file_hash"] not in item["sent_file_hashes"]:
                    item["sent_file_hashes"].append(patch["sent_file_hash"])
            for k, v in patch.items():
                if k != "sent_file_hash" and v not in ("", None):
                    item[k] = v
            item["updated_at"] = int(time.time())
            write_json(store, data)
            return item
    raise SystemExit(f"unknown watch id: {watch_id}")


def cmd_doctor(args: argparse.Namespace, settings: Settings) -> None:
    exe = find_getscipapers()
    payload = introspect()
    payload.update({
        "python": sys.executable,
        "download_dir": str(settings.download_dir),
        "state_dir": str(settings.state_dir),
        "manifest_dir": str(settings.manifest_dir),
        "telegram_max_bytes": settings.telegram_max_bytes,
        "openclaw_path": shutil.which("openclaw"),
        "message_help": run_subprocess(["openclaw", "message", "--help"], timeout=20) if shutil.which("openclaw") else None,
    })
    if args.network:
        checks = {}
        try:
            checks["crossref"] = {"ok": bool(search_crossref("graph theory", limit=1))}
        except Exception as exc:
            checks["crossref"] = {"ok": False, "error": str(exc)}
        try:
            checks["google_books"] = {"ok": bool(search_google_books("introduction to algorithms", limit=1))}
        except Exception as exc:
            checks["google_books"] = {"ok": False, "error": str(exc)}
        try:
            checks["openlibrary"] = {"ok": bool(search_openlibrary("graph theory", limit=1))}
        except Exception as exc:
            checks["openlibrary"] = {"ok": False, "error": str(exc)}
        payload["network_checks"] = checks
    json_print(payload)


def cmd_extract(args: argparse.Namespace, settings: Settings) -> None:
    text = read_text_source(args.source)
    json_print({"dois": extract_dois(text), "isbns": extract_isbns(text)})


def cmd_resolve(args: argparse.Namespace, settings: Settings) -> None:
    if args.best:
        json_print(choose_best_identifier(args.kind, args.query))
    else:
        json_print(resolve_auto(args.kind, args.query))


def cmd_manifest(args: argparse.Namespace, settings: Settings) -> None:
    json_print(build_manifest(args.kind, args.source, settings))


def cmd_introspect(args: argparse.Namespace, settings: Settings) -> None:
    json_print(introspect())


def _apply_runner_proxy_default(module_argv: list[str]) -> list[str]:
    """Default the getpapers module to ``--no-proxy``.

    A stale or invalid getscipapers proxy configuration makes the getpapers
    doi.org DOI-resolution step fail ("not a valid DOI"), even when direct
    access works. The runner therefore defaults getpapers to ``--no-proxy``;
    callers may still override with an explicit ``--proxy`` / ``--no-proxy`` /
    ``--auto-proxy``.
    """
    if module_argv and module_argv[0] == "getpapers" and not (
        {"--proxy", "--no-proxy", "--auto-proxy"} & set(module_argv)
    ):
        return module_argv + ["--no-proxy"]
    return module_argv


def cmd_run(args: argparse.Namespace, settings: Settings) -> None:
    exe = find_getscipapers()
    if not exe:
        raise SystemExit("getscipapers not found in PATH")

    # Sanitize argv: callers sometimes pass a leading '--' or duplicate the
    # getscipapers token. Normalize so we pass: [exe, <module>, <flags...>]
    raw = list(args.argv)
    if raw and raw[0] == "--":
        raw = raw[1:]

    # If the caller accidentally included the executable name or the
    # 'getscipapers' token, drop it.
    if raw and (os.path.basename(raw[0]) == os.path.basename(exe) or raw[0] == "getscipapers"):
        raw = raw[1:]

    raw = _apply_runner_proxy_default(raw)
    argv = [exe] + raw
    payload = {
        "argv": argv,
        "dry_run": bool(args.dry_run),
    }
    if args.dry_run:
        json_print(payload)
        return
    result = run_subprocess(argv, timeout=args.timeout, cwd=args.cwd)
    payload.update(result)
    json_print(payload)
    raise SystemExit(result["returncode"])


def cmd_latest(args: argparse.Namespace, settings: Settings) -> None:
    json_print({"files": latest_files(settings.download_dir, limit=args.limit)})


def cmd_file_info(args: argparse.Namespace, settings: Settings) -> None:
    json_print(file_info(args.path, settings))


def cmd_create_watch(args: argparse.Namespace, settings: Settings) -> None:
    payload = {
        "kind": args.kind,
        "label": args.label,
        "identifier_type": args.identifier_type,
        "identifier": args.identifier,
        "services": [x.strip() for x in (args.services or "").split(",") if x.strip()],
        "notes": args.notes,
        "deadline_ts": int(time.time()) + max(0, args.deadline_hours) * 3600 if args.deadline_hours else None,
    }
    json_print(create_watch(settings, payload))


def cmd_list_watches(args: argparse.Namespace, settings: Settings) -> None:
    json_print(list_watches(settings))


def cmd_update_watch(args: argparse.Namespace, settings: Settings) -> None:
    patch = {
        "status": args.status,
        "last_note": args.last_note,
        "last_checked_at": int(time.time()) if args.bump_check else None,
        "sent_file_hash": args.sent_file_hash,
    }
    if args.bump_check:
        store = list_watches(settings)
        for item in store.get("items", []):
            if item.get("id") == args.watch_id:
                patch["check_count"] = int(item.get("check_count", 0)) + 1
                break
    json_print(update_watch(settings, args.watch_id, patch))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gsp_openclaw_helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("doctor")
    sp.add_argument("--network", action="store_true")

    sp = sub.add_parser("extract")
    sp.add_argument("source")

    sp = sub.add_parser("resolve")
    sp.add_argument("kind", choices=["auto", "paper", "book"])
    sp.add_argument("query")
    sp.add_argument("--best", action="store_true")

    sp = sub.add_parser("make-manifest")
    sp.add_argument("kind", choices=["auto", "paper", "book"])
    sp.add_argument("source")

    sub.add_parser("introspect")

    sp = sub.add_parser("run-getscipapers")
    sp.add_argument("--timeout", type=int, default=180)
    sp.add_argument("--cwd", default=None)
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("argv", nargs=argparse.REMAINDER)

    sp = sub.add_parser("latest-downloads")
    sp.add_argument("--limit", type=int, default=10)

    sp = sub.add_parser("file-info")
    sp.add_argument("path")

    sp = sub.add_parser("create-watch")
    sp.add_argument("--kind", choices=["paper", "book"], required=True)
    sp.add_argument("--label", required=True)
    sp.add_argument("--identifier-type", choices=["doi", "isbn", "search"], required=True)
    sp.add_argument("--identifier", required=True)
    sp.add_argument("--services", default="")
    sp.add_argument("--notes", default="")
    sp.add_argument("--deadline-hours", type=int, default=72)

    sub.add_parser("list-watches")

    sp = sub.add_parser("update-watch")
    sp.add_argument("watch_id")
    sp.add_argument("--status", default="")
    sp.add_argument("--last-note", default="")
    sp.add_argument("--sent-file-hash", default="")
    sp.add_argument("--bump-check", action="store_true")
    return p


def main() -> None:
    settings = ensure_settings_dirs(load_settings())
    parser = build_parser()
    args = parser.parse_args()
    dispatch = {
        "doctor": cmd_doctor,
        "extract": cmd_extract,
        "resolve": cmd_resolve,
        "make-manifest": cmd_manifest,
        "introspect": cmd_introspect,
        "run-getscipapers": cmd_run,
        "latest-downloads": cmd_latest,
        "file-info": cmd_file_info,
        "create-watch": cmd_create_watch,
        "list-watches": cmd_list_watches,
        "update-watch": cmd_update_watch,
    }
    dispatch[args.cmd](args, settings)


if __name__ == "__main__":
    main()
