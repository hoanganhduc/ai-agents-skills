"""Local metadata cache for offline search.

Updates on both zot search and zot add. Falls back to cache when API is unreachable.
zot sync-cache does a full library pull.
"""

import json
import os
import time


def _cache_path(config):
    return os.path.join(config["workspace"], "data", "research", "zotero", "cache", "library.json")


def load_cache(config):
    """Load cached library. Returns (items_list, cache_age_hours) or ([], None)."""
    path = _cache_path(config)
    if not os.path.exists(path):
        return [], None
    try:
        with open(path) as f:
            data = json.load(f)
        mtime = os.path.getmtime(path)
        age_hours = (time.time() - mtime) / 3600
        return data.get("items", []), age_hours
    except (json.JSONDecodeError, OSError):
        return [], None


def save_cache(config, items):
    """Save items to cache."""
    path = _cache_path(config)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"items": items, "updated": time.time()}, f, ensure_ascii=False)


def append_to_cache(config, item):
    """Append a single item to the cache (after zot add)."""
    items, _ = load_cache(config)
    # Deduplicate by key
    keys = {i.get("key") for i in items}
    if item.get("key") not in keys:
        items.append(item)
        save_cache(config, items)


def update_cache_from_search(config, search_results):
    """Merge search results into existing cache."""
    items, _ = load_cache(config)
    existing_keys = {i.get("key") for i in items}
    added = 0
    for item in search_results:
        if item.get("key") not in existing_keys:
            items.append(item)
            existing_keys.add(item.get("key"))
            added += 1
    if added:
        save_cache(config, items)
    return added


def search_cache(config, query):
    """Search the local cache. Returns matching items."""
    items, age = load_cache(config)
    if not items:
        return [], age

    query_lower = query.lower()
    query_words = query_lower.split()
    results = []

    for item in items:
        data = item.get("data", item)
        title = data.get("title", "").lower()
        doi = data.get("DOI", "").lower()
        creators = data.get("creators", [])
        author_text = " ".join(
            c.get("lastName", c.get("name", "")).lower() for c in creators
        )
        searchable = f"{title} {doi} {author_text}"
        if all(w in searchable for w in query_words):
            results.append(item)

    return results, age


def sync_full_library(config, client, progress_fn=None):
    """Pull entire library into cache. Returns item count."""
    all_items = []
    start = 0
    limit = 100

    while True:
        if progress_fn:
            progress_fn(f"Syncing cache... {len(all_items)} items")
        items = client._retry(client.zot.top, start=start, limit=limit)
        if not items:
            break
        all_items.extend(items)
        if len(items) < limit:
            break
        start += limit

    save_cache(config, all_items)
    return len(all_items)
