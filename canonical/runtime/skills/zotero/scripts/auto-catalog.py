#!/usr/bin/env python3
"""Auto-catalog papers from digest-bridge output into Zotero.

Usage:
  python3 auto-catalog.py [--source all|research|rss] [--min-score N] [--delay N]

Scans digests via digest-bridge, matches topics to existing Zotero collections,
and adds high-score papers to the library.
"""

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.config import load_config, default_workspace

WORKSPACE = default_workspace()
ZOT_PY = os.path.join(WORKSPACE, "skills", "zotero", "zot.py")
DIGEST_BRIDGE = os.path.join(WORKSPACE, "skills", "digest-bridge", "digest_bridge.py")


def main():
    parser = argparse.ArgumentParser(description="Auto-catalog digest papers to Zotero")
    parser.add_argument("--source", choices=["all", "research", "rss"], default="all")
    parser.add_argument("--min-score", type=int, default=None, help="Min relevance score (default: from config)")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between adds (seconds)")
    parser.add_argument("--parallel", type=int, default=0, help="Parallel adds (0=auto, 1=sequential)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_config()
    min_score = args.min_score if args.min_score is not None else config.get("auto_catalog_threshold", 80)

    # Scan digests
    print(f"Scanning digests (source={args.source}, min_score={min_score})...", file=sys.stderr)
    try:
        result = subprocess.run(
            [sys.executable, DIGEST_BRIDGE, "scan", "--source", args.source, "--min-score", str(min_score)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            print(f"Digest-bridge scan failed: {result.stderr}", file=sys.stderr)
            sys.exit(1)
        scan_data = json.loads(result.stdout)
    except Exception as e:
        print(f"Error scanning digests: {e}", file=sys.stderr)
        sys.exit(1)

    papers = scan_data.get("papers", [])
    if not papers:
        print(json.dumps({"status": "ok", "action": "auto_catalog", "added": 0,
                           "message": "No new papers to catalog"}))
        return

    # Get existing collections for topic matching
    try:
        coll_result = subprocess.run(
            [sys.executable, ZOT_PY, "--json", "list-collections", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        collections = json.loads(coll_result.stdout).get("collections", [])
        coll_names = _flatten_collection_names(collections)
    except Exception:
        coll_names = []

    # Add each paper (parallel with rate limiting for Zotero API)
    added = []
    skipped = []

    if args.dry_run:
        for paper in papers:
            matched = _match_collections(paper.get("title", ""), coll_names)
            added.append({"title": paper.get("title", ""), "identifier": paper["identifier"],
                           "collections": matched + ["Auto-cataloged"]})
    else:
        def _add_one(paper):
            identifier = paper["identifier"]
            title = paper.get("title", "")
            matched = _match_collections(title, coll_names)
            coll_args = ["--collection", "Auto-cataloged"]
            for c in matched:
                coll_args.extend(["--collection", c])
            cmd = [sys.executable, ZOT_PY, "add", identifier, "--no-pdf"] + coll_args
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if r.returncode == 0:
                    out = json.loads(r.stdout)
                    return {"title": out.get("title", title), "key": out.get("key", ""),
                            "status": out.get("status", ""), "collections": matched + ["Auto-cataloged"]}
                return {"title": title, "identifier": identifier, "error": r.stderr.strip(), "_skip": True}
            except Exception as e:
                return {"title": title, "identifier": identifier, "error": str(e), "_skip": True}

        _parallel = args.parallel
        if _parallel == 0:
            _cpus = os.cpu_count() or 2
            _parallel = min(_cpus, 4)  # cap at 4 for API rate limiting
        if _parallel > 1 and len(papers) > 1:
            import threading
            from concurrent.futures import ThreadPoolExecutor
            sem = threading.Semaphore(3)  # max 3 concurrent Zotero API calls
            def _rate_limited_add(paper):
                with sem:
                    return _add_one(paper)
            with ThreadPoolExecutor(max_workers=_parallel) as pool:
                results = list(pool.map(_rate_limited_add, papers))
        else:
            results = []
            for i, paper in enumerate(papers):
                results.append(_add_one(paper))
                if i < len(papers) - 1:
                    time.sleep(args.delay)

        for r in results:
            if r.get("_skip"):
                r.pop("_skip", None)
                skipped.append(r)
            else:
                added.append(r)

    # Summary
    output = {
        "status": "ok", "action": "auto_catalog",
        "added": len([a for a in added if a.get("status") != "exists"]),
        "duplicates": len([a for a in added if a.get("status") == "exists"]),
        "errors": len(skipped),
        "papers": added,
        "message": _build_summary(added, skipped),
    }
    print(json.dumps(output, ensure_ascii=False))


def _flatten_collection_names(tree, prefix=""):
    """Extract all collection names from tree."""
    names = []
    for node in tree:
        names.append(node["name"])
        if node.get("children"):
            names.extend(_flatten_collection_names(node["children"]))
    return names


def _match_collections(title, collection_names):
    """Simple keyword matching of paper title against collection names."""
    title_lower = title.lower()
    title_words = set(title_lower.split())
    matched = []
    for name in collection_names:
        name_lower = name.lower()
        name_words = set(name_lower.split())
        # Match if any significant collection name word appears in title
        significant = [w for w in name_words if len(w) > 3]
        if significant and any(w in title_lower for w in significant):
            matched.append(name)
    return matched[:3]  # cap at 3 matches


def _build_summary(added, skipped):
    parts = []
    real_added = [a for a in added if a.get("status") != "exists"]
    dupes = [a for a in added if a.get("status") == "exists"]
    if real_added:
        titles = [a.get("title", "?")[:50] for a in real_added[:5]]
        parts.append(f"Auto-cataloged {len(real_added)} papers: " + "; ".join(titles))
    if dupes:
        parts.append(f"{len(dupes)} skipped (duplicates)")
    if skipped:
        parts.append(f"{len(skipped)} failed")
    if not parts:
        parts.append("No papers to catalog")
    return ". ".join(parts)


if __name__ == "__main__":
    main()
