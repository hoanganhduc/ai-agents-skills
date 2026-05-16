#!/usr/bin/env python3
"""Bridge between research/RSS digests and getscipapers paper retrieval."""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(os.environ.get("AAS_RUNTIME_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE") or os.path.join(os.path.expanduser("~"), ".codex", "runtime", "workspace"))
RESEARCH_DIGEST = WORKSPACE_ROOT / "data" / "research" / "alerts" / "digests" / "latest-digest.md"
RSS_DIGEST_DIR = WORKSPACE_ROOT / "data" / "research" / "rss" / "digests"
BRIDGE_STATE_FILE = WORKSPACE_ROOT / "data" / "research" / "digest-bridge-state.json"
GSP_HELPER = Path(__file__).resolve().parent.parent / "getscipapers_requester" / "gsp_openclaw_helper.py"

ARXIV_ID_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arXiv:)(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s)\]>,;\"']+)", re.IGNORECASE)
LINK_RE = re.compile(r"^- Link:\s*(.+)$", re.MULTILINE)
SCORE_RE = re.compile(r"^- (?:Relevance|Score):\s*(\d+)", re.MULTILINE)
TITLE_RE = re.compile(r"^## \d+\.\s+(.+?)(?:\s*\[.*\])?\s*$", re.MULTILINE)


def load_state() -> dict:
    if BRIDGE_STATE_FILE.exists():
        try:
            return json.loads(BRIDGE_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"requested": []}


def save_state(state: dict) -> None:
    BRIDGE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BRIDGE_STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def extract_papers_from_digest(text: str) -> list[dict]:
    """Extract paper entries with titles, links, scores, and identifiers."""
    papers = []
    sections = re.split(r"(?=^## \d+\.)", text, flags=re.MULTILINE)
    for section in sections:
        title_m = TITLE_RE.search(section)
        if not title_m:
            continue
        title = title_m.group(1).strip()

        link = ""
        link_m = LINK_RE.search(section)
        if link_m:
            link = link_m.group(1).strip()

        score = 0
        score_m = SCORE_RE.search(section)
        if score_m:
            score = int(score_m.group(1))

        # Extract identifiers from link and section text
        arxiv_ids = ARXIV_ID_RE.findall(section)
        dois = DOI_RE.findall(section)

        # Prefer arXiv ID, then DOI
        identifier = ""
        id_type = ""
        if arxiv_ids:
            identifier = arxiv_ids[0]
            id_type = "arxiv"
        elif dois:
            identifier = dois[0].rstrip(".")
            id_type = "doi"

        if identifier:
            papers.append({
                "title": title,
                "link": link,
                "score": score,
                "identifier": identifier,
                "identifier_type": id_type,
            })
    return papers


def scan_digests(sources: list[str]) -> list[dict]:
    """Scan digest files and extract all paper identifiers."""
    all_papers = []

    if "research" in sources and RESEARCH_DIGEST.exists():
        text = RESEARCH_DIGEST.read_text(encoding="utf-8", errors="replace")
        if "No papers exceeded" not in text:
            papers = extract_papers_from_digest(text)
            for p in papers:
                p["source"] = "research-digest"
            all_papers.extend(papers)

    if "rss" in sources and RSS_DIGEST_DIR.exists():
        for md_file in sorted(RSS_DIGEST_DIR.glob("rss-*.md")):
            tag = md_file.stem.replace("rss-", "")
            if tag in ("all",):
                continue  # skip aggregate file
            text = md_file.read_text(encoding="utf-8", errors="replace")
            if "No new items" in text:
                continue
            papers = extract_papers_from_digest(text)
            for p in papers:
                p["source"] = f"rss-{tag}"
            all_papers.extend(papers)

    # Deduplicate by identifier
    seen = set()
    unique = []
    for p in all_papers:
        key = p["identifier"]
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def filter_new(papers: list[dict], state: dict, min_score: int) -> list[dict]:
    """Filter out already-requested papers and those below min_score."""
    requested_set = set(state.get("requested", []))
    return [
        p for p in papers
        if p["identifier"] not in requested_set
        and (p["score"] >= min_score or min_score == 0)
    ]


def create_manifest(papers: list[dict]) -> dict | None:
    """Create a getscipapers manifest from paper identifiers."""
    if not GSP_HELPER.exists():
        print(f"ERROR: gsp_openclaw_helper.py not found at {GSP_HELPER}", file=sys.stderr)
        return None

    # Build a text block of identifiers for manifest creation
    lines = []
    for p in papers:
        if p["identifier_type"] == "arxiv":
            lines.append(f"arXiv:{p['identifier']}")
        elif p["identifier_type"] == "doi":
            lines.append(p["identifier"])
    identifier_text = "\n".join(lines)

    try:
        result = subprocess.run(
            [sys.executable, str(GSP_HELPER), "build-manifest", "--kind", "auto", "--source", identifier_text],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: manifest creation failed: {exc}", file=sys.stderr)
    return None


def create_watches(papers: list[dict]) -> list[dict]:
    """Create watches for papers via gsp_openclaw_helper (parallel)."""
    def _create_one(p):
        identifier = p["identifier"]
        if p["identifier_type"] == "arxiv":
            identifier = f"10.48550/arXiv.{p['identifier']}"
        try:
            result = subprocess.run(
                [sys.executable, str(GSP_HELPER), "watch-create",
                 "--identifier-type", "doi",
                 "--identifier", identifier,
                 "--services", "all"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return {"identifier": p["identifier"], "status": "created"}
            return {"identifier": p["identifier"], "status": "error", "error": result.stderr.strip()}
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {"identifier": p["identifier"], "status": "error", "error": str(exc)}

    if len(papers) > 1:
        _cpus = os.cpu_count() or 2
        _workers = min(_cpus * 2, len(papers), 8)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=_workers) as pool:
            return list(pool.map(_create_one, papers))
    return [_create_one(p) for p in papers]


def cmd_scan(args):
    sources = [args.source] if args.source != "all" else ["research", "rss"]
    papers = scan_digests(sources)
    state = load_state()
    new_papers = filter_new(papers, state, args.min_score)

    print(json.dumps({
        "ok": True,
        "total_found": len(papers),
        "new_papers": len(new_papers),
        "already_requested": len(papers) - len(new_papers),
        "min_score": args.min_score,
        "papers": new_papers,
    }, indent=2))


def cmd_request(args):
    sources = [args.source] if args.source != "all" else ["research", "rss"]
    papers = scan_digests(sources)
    state = load_state()
    new_papers = filter_new(papers, state, args.min_score)

    if not new_papers:
        print(json.dumps({"ok": True, "message": "No new papers to request", "total_scanned": len(papers)}, indent=2))
        return

    manifest = create_manifest(new_papers)

    watch_results = []
    if args.watch:
        watch_results = create_watches(new_papers)

    # Mark as requested
    requested = state.get("requested", [])
    for p in new_papers:
        if p["identifier"] not in requested:
            requested.append(p["identifier"])
    # Keep last 500 entries
    state["requested"] = requested[-500:]
    save_state(state)

    print(json.dumps({
        "ok": True,
        "requested_count": len(new_papers),
        "papers": new_papers,
        "manifest": manifest,
        "watches": watch_results if watch_results else None,
    }, indent=2))


def main():
    ap = argparse.ArgumentParser(description="Bridge digest outputs to getscipapers retrieval")
    sub = ap.add_subparsers(dest="command")

    scan_p = sub.add_parser("scan", help="Scan digests and show available papers")
    scan_p.add_argument("--source", choices=["all", "research", "rss"], default="all")
    scan_p.add_argument("--min-score", type=int, default=0)
    scan_p.set_defaults(func=cmd_scan)

    req_p = sub.add_parser("request", help="Create manifest and optionally watches")
    req_p.add_argument("--source", choices=["all", "research", "rss"], default="all")
    req_p.add_argument("--min-score", type=int, default=0)
    req_p.add_argument("--watch", action="store_true", help="Also create watches for monitoring")
    req_p.set_defaults(func=cmd_request)

    args = ap.parse_args()
    if args.command is None:
        ap.print_help()
        raise SystemExit(1)
    args.func(args)


if __name__ == "__main__":
    main()
