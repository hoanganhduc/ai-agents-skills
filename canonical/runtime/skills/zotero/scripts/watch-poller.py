#!/usr/bin/env python3
"""Watch poller — checks getscipapers watches and auto-attaches PDFs.

Runs via cron every 4 hours. Checks for watches with status 'found',
maps watch_id → zotero_key via watch-keys.json, and runs
zot update <key> --attach-pdf for each.
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.config import default_workspace

WORKSPACE = default_workspace()
ZOT_PY = os.path.join(WORKSPACE, "skills", "zotero", "zot.py")
GSP_HELPER = os.path.join(WORKSPACE, "skills", "getscipapers_requester", "gsp_openclaw_helper.py")
WATCH_KEYS_FILE = os.path.join(WORKSPACE, "data", "research", "zotero", "watch-keys.json")


def load_watch_keys():
    if os.path.exists(WATCH_KEYS_FILE):
        with open(WATCH_KEYS_FILE) as f:
            return json.load(f)
    return {}


def save_watch_keys(data):
    os.makedirs(os.path.dirname(WATCH_KEYS_FILE), exist_ok=True)
    with open(WATCH_KEYS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def main():
    # List watches
    try:
        result = subprocess.run(
            [sys.executable, GSP_HELPER, "list-watches"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"Failed to list watches: {result.stderr}", file=sys.stderr)
            return
        watches = json.loads(result.stdout)
    except Exception as e:
        print(f"Error listing watches: {e}", file=sys.stderr)
        return

    if not watches:
        print("No active watches.", file=sys.stderr)
        return

    watch_keys = load_watch_keys()
    attached = 0
    cleared = 0

    # Separate found watches from expired/failed
    found_watches = []
    for watch in watches:
        watch_id = watch.get("id", "")
        status = watch.get("status", "")
        if status == "found":
            zotero_key = watch_keys.get(watch_id)
            if zotero_key:
                found_watches.append((watch_id, zotero_key))
            else:
                print(f"Watch {watch_id}: no Zotero key mapping, skipping", file=sys.stderr)
        elif status in ("expired", "failed"):
            watch_keys.pop(watch_id, None)
            cleared += 1

    # Process found watches in parallel
    def _attach_one(item):
        watch_id, zotero_key = item
        print(f"Watch {watch_id}: attaching PDF to {zotero_key}...", file=sys.stderr)
        try:
            r = subprocess.run(
                [sys.executable, ZOT_PY, "update", zotero_key, "--attach-pdf"],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode == 0:
                out = json.loads(r.stdout)
                print(f"  → {out.get('message', 'done')}", file=sys.stderr)
                return True
            else:
                print(f"  → failed: {r.stderr.strip()}", file=sys.stderr)
                return False
        except Exception as e:
            print(f"  → error: {e}", file=sys.stderr)
            return False

    if found_watches:
        _cpus = os.cpu_count() or 2
        _workers = min(_cpus, 4, len(found_watches))  # cap at 4 for API rate limiting
        if _workers > 1 and len(found_watches) > 1:
            import threading
            from concurrent.futures import ThreadPoolExecutor
            sem = threading.Semaphore(3)
            def _rate_limited(item):
                with sem:
                    return _attach_one(item)
            with ThreadPoolExecutor(max_workers=_workers) as pool:
                results = list(pool.map(_rate_limited, found_watches))
        else:
            results = [_attach_one(w) for w in found_watches]

        for (watch_id, _), success in zip(found_watches, results):
            if success:
                attached += 1
            watch_keys.pop(watch_id, None)
            cleared += 1

    if cleared:
        save_watch_keys(watch_keys)

    print(json.dumps({
        "status": "ok", "action": "watch_poll",
        "attached": attached, "cleared": cleared,
        "remaining": len(watch_keys),
    }))


if __name__ == "__main__":
    main()
