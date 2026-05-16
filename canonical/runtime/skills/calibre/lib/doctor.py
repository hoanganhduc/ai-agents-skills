"""Health checks for the Calibre skill."""

import os
import json


def run_checks(config):
    """Run all health checks. Returns list of {name, ok, message} dicts."""
    results = []

    # 1. Google Drive credentials
    results.append(_check_gdrive_creds(config))

    # 2. Google Drive folder access
    if results[-1]["ok"]:
        results.append(_check_gdrive_folder(config))
    else:
        results.append({"name": "gdrive_folder", "ok": False,
                         "message": "Skipped (no credentials)"})

    # 3. Local metadata.db
    results.append(_check_local_db(config))

    # 4. Staging directory
    results.append(_check_staging(config))

    # 5. Cache file
    results.append(_check_cache(config))

    # 6. ebook-convert (optional)
    results.append(_check_ebook_convert())

    # 7. ebooklib Python module (optional)
    results.append(_check_ebooklib())

    return results


def _check_gdrive_creds(config):
    creds = config.get("GDRIVE_CREDENTIALS") or config.get("gdrive_credentials_file")
    if not creds:
        return {"name": "gdrive_credentials", "ok": False,
                "message": "GDRIVE_CREDENTIALS not set in secrets file"}
    try:
        if isinstance(creds, str) and creds.strip().startswith("{"):
            json.loads(creds)
        elif isinstance(creds, str) and os.path.exists(creds):
            pass
        elif isinstance(creds, dict):
            pass
        else:
            return {"name": "gdrive_credentials", "ok": False,
                    "message": f"Credentials file not found: {creds}"}
        return {"name": "gdrive_credentials", "ok": True,
                "message": "Credentials present"}
    except Exception as e:
        return {"name": "gdrive_credentials", "ok": False,
                "message": f"Invalid credentials JSON: {e}"}


def _check_gdrive_folder(config):
    folder_id = config.get("gdrive_folder_id", "")
    if not folder_id:
        return {"name": "gdrive_folder", "ok": False,
                "message": "gdrive_folder_id not set in config.json"}
    try:
        from .gdrive import GDriveClient
        creds = config.get("GDRIVE_CREDENTIALS") or config.get("gdrive_credentials_file")
        client = GDriveClient(creds, folder_id)
        ok = client.check_connection()
        if ok:
            return {"name": "gdrive_folder", "ok": True,
                    "message": f"Drive folder accessible (id: {folder_id[:12]}...)"}
        else:
            return {"name": "gdrive_folder", "ok": False,
                    "message": "Drive folder not accessible — check folder_id and permissions"}
    except Exception as e:
        return {"name": "gdrive_folder", "ok": False,
                "message": f"Drive access error: {e}"}


def _check_local_db(config):
    db_path = config["db_local_path"]
    if not os.path.exists(db_path):
        return {"name": "local_db", "ok": False,
                "message": f"metadata.db not found at {db_path}. Run 'cal sync' first."}
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        conn.close()
        return {"name": "local_db", "ok": True,
                "message": f"metadata.db OK — {count} books"}
    except Exception as e:
        return {"name": "local_db", "ok": False,
                "message": f"metadata.db read error: {e}"}


def _check_staging(config):
    staging = config["staging_dir"]
    try:
        os.makedirs(staging, exist_ok=True)
        test_file = os.path.join(staging, ".write_test")
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
        return {"name": "staging_dir", "ok": True,
                "message": f"Staging writable: {staging}"}
    except Exception as e:
        return {"name": "staging_dir", "ok": False,
                "message": f"Staging not writable: {e}"}


def _check_cache(config):
    cache_path = config["cache_path"]
    if not os.path.exists(cache_path):
        return {"name": "cache", "ok": True,
                "message": "Cache not yet built (run 'cal sync' to populate)"}
    try:
        from .cache import load_cache
        items, age_hours = load_cache(cache_path)
        age_str = f"{age_hours:.1f}h old" if age_hours is not None else "unknown age"
        return {"name": "cache", "ok": True,
                "message": f"Cache OK — {len(items)} books, {age_str}"}
    except Exception as e:
        return {"name": "cache", "ok": False, "message": f"Cache read error: {e}"}


def _check_ebook_convert():
    import shutil
    path = shutil.which("ebook-convert")
    if path:
        return {"name": "ebook_convert", "ok": True,
                "message": f"ebook-convert found: {path}"}
    return {"name": "ebook_convert", "ok": False,
            "message": "ebook-convert not found — 'cal convert' will be unavailable"}


def _check_ebooklib():
    try:
        import ebooklib
        return {"name": "ebooklib", "ok": True, "message": "ebooklib available"}
    except ImportError:
        return {"name": "ebooklib", "ok": False,
                "message": "ebooklib not installed — EPUB metadata extraction limited "
                           "(pip install ebooklib)"}
