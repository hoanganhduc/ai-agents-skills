"""Health checks for all zot components."""

import os
import json
import shutil
import subprocess


def run_doctor(config):
    checks = []
    checks.append(_check_translation_server(config))
    checks.append(_check_wsl_translation_helper(config))
    checks.append(_check_zotero_api(config))
    checks.append(_check_webdav(config))
    checks.append(_check_gdrive(config))
    checks.append(_check_getscipapers())
    checks.append(_check_staging_dir(config))
    checks.append(_check_orphaned_attachments(config))
    return checks


def _check_translation_server(config):
    url = config.get("translation_server", "http://localhost:1969")
    try:
        import requests
        r = requests.get(url, timeout=5)
        if r.status_code < 500:
            return {"name": "Translation Server", "ok": True, "message": f"Reachable at {url}"}
        return {"name": "Translation Server", "ok": False,
                "message": f"Server returned {r.status_code}. Try: docker compose up -d"}
    except Exception as e:
        return {"name": "Translation Server", "ok": True,
                "message": (
                    f"Unreachable at {url}. "
                    "Direct DOI/arXiv/ISBN fallback is enabled; generic URL metadata can also use "
                    "the WSL helper path when configured."
                )}


def _check_wsl_translation_helper(config):
    try:
        from lib.metadata import _fetch_url_via_wsl
        item = _fetch_url_via_wsl("https://en.wikipedia.org/wiki/Zotero", config)
        title = (item or {}).get("title", "").strip() or "metadata returned"
        return {
            "name": "WSL URL Translation",
            "ok": True,
            "message": f"WSL helper returned metadata for generic URLs ({title})",
        }
    except Exception as e:
        return {
            "name": "WSL URL Translation",
            "ok": False,
            "message": str(e),
        }


def _check_zotero_api(config):
    api_key = config.get("ZOTERO_API_KEY")
    user_id = config.get("zotero_user_id")
    if not api_key or not user_id:
        return {"name": "Zotero API", "ok": False,
                "message": "Missing ZOTERO_API_KEY or zotero_user_id"}
    try:
        from pyzotero import zotero
        zot = zotero.Zotero(user_id, "user", api_key)
        count = zot.count_items()
        return {"name": "Zotero API", "ok": True,
                "message": f"Authenticated. Library has {count} items"}
    except Exception as e:
        return {"name": "Zotero API", "ok": False, "message": str(e)}


def _check_webdav(config):
    url = config.get("webdav_url")
    if not url:
        return {"name": "WebDAV", "ok": True, "message": "Not configured (skipped)"}
    user = config.get("webdav_user", "")
    password = config.get("WEBDAV_PASSWORD", "")
    if not password:
        return {"name": "WebDAV", "ok": False, "message": "webdav_url set but WEBDAV_PASSWORD missing"}
    try:
        import requests
        from requests.auth import HTTPBasicAuth, HTTPDigestAuth
        # Check the zotero/ subdirectory (some servers block PROPFIND on root)
        zotero_url = url.rstrip("/") + "/zotero/"
        auth = HTTPBasicAuth(user, password)
        r = requests.request("PROPFIND", zotero_url, auth=auth, timeout=10, headers={"Depth": "0"})
        if r.status_code == 401:
            auth = HTTPDigestAuth(user, password)
            r = requests.request("PROPFIND", zotero_url, auth=auth, timeout=10, headers={"Depth": "0"})
        if r.status_code < 400:
            return {"name": "WebDAV", "ok": True, "message": f"Accessible at {zotero_url}"}
        return {"name": "WebDAV", "ok": False, "message": f"HTTP {r.status_code} from {zotero_url}"}
    except Exception as e:
        return {"name": "WebDAV", "ok": False, "message": str(e)}


def _check_gdrive(config):
    creds = config.get("GDRIVE_CREDENTIALS") or config.get("gdrive_credentials_file")
    folder_id = config.get("gdrive_folder_id")
    if not creds or not folder_id:
        return {"name": "Google Drive", "ok": True, "message": "Not configured (skipped)"}
    try:
        from lib.gdrive import GDriveClient
        gd = GDriveClient(config)
        ok, msg = gd.check_connection()
        return {"name": "Google Drive", "ok": ok, "message": msg}
    except Exception as e:
        return {"name": "Google Drive", "ok": False, "message": str(e)}


def _check_getscipapers():
    # Check PATH first
    if shutil.which("getscipapers"):
        return {"name": "getscipapers", "ok": True, "message": "Found in PATH"}
    # Check if importable as python module
    try:
        result = subprocess.run(
            ["python3", "-m", "getscipapers", "--help"],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0:
            return {"name": "getscipapers", "ok": True, "message": "Available as python module"}
    except Exception:
        pass
    return {"name": "getscipapers", "ok": False,
            "message": "Not found. Install in workspace venv or add to sandbox Dockerfile"}


def _check_staging_dir(config):
    staging = config.get("staging_dir", "")
    if not staging:
        return {"name": "Staging directory", "ok": False, "message": "Path not configured"}
    if os.path.isdir(staging):
        # Check writable
        test_file = os.path.join(staging, ".write_test")
        try:
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
            return {"name": "Staging directory", "ok": True, "message": f"Writable at {staging}"}
        except OSError as e:
            return {"name": "Staging directory", "ok": False, "message": f"Not writable: {e}"}
    else:
        try:
            os.makedirs(staging, exist_ok=True)
            return {"name": "Staging directory", "ok": True, "message": f"Created at {staging}"}
        except OSError as e:
            return {"name": "Staging directory", "ok": False, "message": f"Cannot create: {e}"}


def _check_orphaned_attachments(config):
    workspace = config.get("workspace", "")
    orphan_log = os.path.join(workspace, "data", "research", "zotero", "orphaned-keys.log")
    if os.path.exists(orphan_log):
        with open(orphan_log) as f:
            lines = [l.strip() for l in f if l.strip()]
        if lines:
            return {"name": "Orphaned attachments", "ok": False,
                    "message": f"{len(lines)} orphaned attachment key(s) need cleanup"}
    return {"name": "Orphaned attachments", "ok": True, "message": "None found"}
