"""Local metadata cache for Calibre library.

Mirrors the pattern from zotero/lib/cache.py: a JSON file with book records
for offline search and reduced Drive API calls.
"""

import json
import os
import time


def load_cache(cache_path):
    """Load the cache file.

    Returns:
        (items, age_hours) — items is a list of book dicts,
                             age_hours is float or None if no cache.
    """
    if not os.path.exists(cache_path):
        return [], None

    try:
        with open(cache_path) as f:
            data = json.load(f)
        updated = data.get("updated", 0)
        age_hours = (time.time() - updated) / 3600 if updated else None
        return data.get("items", []), age_hours
    except Exception:
        return [], None


def save_cache(cache_path, items):
    """Write items list to cache file."""
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump({"items": items, "updated": time.time()}, f)


def append_to_cache(cache_path, book_dict):
    """Add or replace a single book in the cache (by id)."""
    items, _ = load_cache(cache_path)
    items = [b for b in items if b.get("id") != book_dict["id"]]
    items.insert(0, book_dict)
    save_cache(cache_path, items)


def remove_from_cache(cache_path, book_id):
    """Remove a book by id from the cache."""
    items, _ = load_cache(cache_path)
    items = [b for b in items if b.get("id") != book_id]
    save_cache(cache_path, items)


def search_cache(items, query, tag=None, series=None, fmt=None, limit=25):
    """Search the in-memory items list (offline fallback).

    Matches all query words (case-insensitive) against title + authors +
    tags + series + identifier values.
    """
    words = query.lower().split() if query else []
    results = []
    for book in items:
        # Tag filter
        if tag and tag.lower() not in [t.lower() for t in book.get("tags", [])]:
            continue
        # Series filter
        if series and (not book.get("series") or
                       series.lower() not in book["series"].lower()):
            continue
        # Format filter
        if fmt and fmt.lower() not in [f.lower() for f in book.get("formats", [])]:
            continue

        if words:
            haystack = " ".join([
                book.get("title", ""),
                " ".join(book.get("authors", [])),
                " ".join(book.get("tags", [])),
                book.get("series") or "",
                " ".join(book.get("identifiers", {}).values()),
            ]).lower()
            if not all(w in haystack for w in words):
                continue

        results.append(book)
        if len(results) >= limit:
            break
    return results
