"""Portable parallel execution utilities.

Auto-detects available CPU cores and provides thread-based parallel map
with optional rate limiting. Works on any machine — from 1-core VPS to
32-core servers.

Usage:
    from lib.parallel import parallel_map, get_workers

    results = parallel_map(process_item, items, max_workers=0, rate_limit=3)
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


def get_workers(io_bound=True, override=None):
    """Auto-detect optimal worker count.

    Args:
        io_bound: True for I/O-bound work (HTTP, file I/O) — use more threads.
                  False for CPU-bound work — match core count.
        override: explicit worker count. 0 = auto, 1 = sequential, N = explicit.
                  Also reads OPENCLAW_PARALLEL env var as global default.

    Returns:
        int: number of workers (minimum 1)
    """
    if override is not None and override > 0:
        return override

    env_val = os.environ.get("OPENCLAW_PARALLEL", "")
    if env_val.isdigit() and int(env_val) > 0:
        return int(env_val)

    cpus = os.cpu_count() or 2
    if io_bound:
        return min(cpus * 2, 16)
    return max(cpus, 1)


def parallel_map(fn, items, max_workers=0, rate_limit=0, timeout=None):
    """Execute fn(item) for each item in parallel, return results in order.

    Args:
        fn: callable taking a single item, returns result
        items: iterable of items to process
        max_workers: 0 = auto-detect, 1 = sequential (no threads), N = explicit
        rate_limit: max concurrent calls (0 = no limit). Use for API rate limiting
                    (e.g., rate_limit=3 for Zotero API). Independent of max_workers.
        timeout: per-item timeout in seconds (None = no timeout)

    Returns:
        list of results in same order as items
    """
    items = list(items)
    if not items:
        return []

    workers = get_workers(io_bound=True, override=max_workers if max_workers > 0 else None)

    # Sequential fallback
    if workers <= 1 or len(items) == 1:
        return [fn(item) for item in items]

    # Rate-limited wrapper
    if rate_limit > 0:
        semaphore = threading.Semaphore(rate_limit)
        original_fn = fn
        def fn(item):
            with semaphore:
                return original_fn(item)

    # Parallel execution preserving order
    results = [None] * len(items)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_idx = {executor.submit(fn, item): i for i, item in enumerate(items)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result(timeout=timeout)
            except Exception as e:
                results[idx] = e

    return results
