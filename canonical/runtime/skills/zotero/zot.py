#!/usr/bin/env python3
"""Headless Zotero CLI — manages papers in Zotero library."""

import argparse
import json
import sys
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

# Deps are installed via pip into workspace/.local/ (persisted, in sandbox sys.path).
# Add skill dir to path for lib/ imports.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.config import load_config, default_workspace
from lib.zotero_client import ZoteroClient


LOCAL_ZOTERO_DB_RELATIVE_PATHS = [
    ("Zotero", "zotero.sqlite"),
    ("ZoteroSync", "zotero.sqlite"),
    ("Google Drive", "DATA", "Zotero", "zotero.sqlite"),
    ("AppData", "Roaming", "Zotero", "Zotero", "zotero.sqlite"),
    ("AppData", "Roaming", "Jurism", "Zotero", "zotero.sqlite"),
]


def _trigger_ingest(item_data):
    """Fire-and-forget: ingest Zotero item into memory. Does not block."""
    import subprocess
    script = os.path.join(
        default_workspace(),
        "scripts", "ingest_library.py",
    )
    if not os.path.exists(script):
        return
    subprocess.Popen(
        [sys.executable, script, "--source", "zotero", "--data", json.dumps(item_data)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
    )


def _config_path_list(value):
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str):
        return [p.strip() for p in value.split(os.pathsep) if p.strip()]
    return []


def _home_dirs():
    homes = []
    for key in ("USERPROFILE", "HOME"):
        value = os.environ.get(key)
        if value:
            homes.append(value)
    homes.append(str(Path.home()))
    return _dedupe_paths(homes, existing_only=False)


def _expand_path(path):
    return os.path.abspath(os.path.expandvars(os.path.expanduser(str(path))))


def _dedupe_paths(paths, existing_only=True):
    seen = set()
    result = []
    for path in paths:
        if not path:
            continue
        expanded = _expand_path(path)
        key = os.path.normcase(expanded)
        if key in seen:
            continue
        if existing_only and not os.path.exists(expanded):
            continue
        seen.add(key)
        result.append(expanded)
    return result


def _candidate_zotero_db_paths(config, existing_only=True):
    paths = []
    paths.extend(_config_path_list(config.get("local_zotero_db_paths")))
    paths.extend(_config_path_list(config.get("zotero_db_paths")))
    for home in _home_dirs():
        for parts in LOCAL_ZOTERO_DB_RELATIVE_PATHS:
            paths.append(os.path.join(home, *parts))
    return _dedupe_paths(paths, existing_only=existing_only)


def _candidate_storage_dirs(config):
    paths = []
    paths.extend(_config_path_list(config.get("local_storage_dirs")))
    paths.extend(_config_path_list(config.get("zotero_storage_dirs")))
    for db_path in _candidate_zotero_db_paths(config, existing_only=True):
        paths.append(os.path.join(os.path.dirname(db_path), "storage"))
    return _dedupe_paths(paths, existing_only=True)


def _sqlite_uri(path):
    return f"file:{quote(Path(path).as_posix(), safe='/:')}?mode=ro"


def _quick_check(conn):
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
        value = str(row[0]) if row else ""
        return {"status": "ok" if value.lower() == "ok" else "malformed", "quick_check": value}
    except sqlite3.DatabaseError as exc:
        return {"status": "error", "quick_check": str(exc)}


def _local_authors(conn, item_id):
    rows = conn.execute(
        """
        SELECT c.firstName, c.lastName, ic.orderIndex
        FROM itemCreators ic
        JOIN creators c ON c.creatorID = ic.creatorID
        WHERE ic.itemID = ?
        ORDER BY ic.orderIndex
        """,
        (item_id,),
    ).fetchall()
    authors = []
    for first, last, _ in rows:
        if first and last:
            authors.append({"creatorType": "author", "firstName": first, "lastName": last})
        elif last:
            authors.append({"creatorType": "author", "name": last})
        elif first:
            authors.append({"creatorType": "author", "name": first})
    return authors


def _search_local_zotero_db(path, query, limit):
    diag = {"path": path, "exists": os.path.exists(path)}
    if not diag["exists"]:
        return [], diag

    words = [w.lower() for w in query.split() if w.strip()]
    if not words:
        diag["status"] = "skipped"
        diag["message"] = "empty query"
        return [], diag

    items = []
    try:
        conn = sqlite3.connect(_sqlite_uri(path), uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        qc = _quick_check(conn)
        diag.update(qc)
        rows = conn.execute(
            """
            SELECT
                i.itemID,
                i.key,
                it.typeName,
                MAX(CASE WHEN f.fieldName = 'title' THEN v.value END) AS title,
                MAX(CASE WHEN f.fieldName = 'date' THEN v.value END) AS date,
                MAX(CASE WHEN f.fieldName = 'DOI' THEN v.value END) AS doi
            FROM items i
            JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
            LEFT JOIN itemData d ON d.itemID = i.itemID
            LEFT JOIN fields f ON f.fieldID = d.fieldID
            LEFT JOIN itemDataValues v ON v.valueID = d.valueID
            WHERE it.typeName NOT IN ('attachment', 'note')
            GROUP BY i.itemID, i.key, it.typeName
            ORDER BY i.dateModified DESC
            """
        )
        for row in rows:
            authors = _local_authors(conn, row["itemID"])
            author_text = " ".join(
                (a.get("name") or f"{a.get('lastName', '')} {a.get('firstName', '')}").lower()
                for a in authors
            )
            searchable = " ".join(
                str(part or "").lower()
                for part in (row["title"], row["doi"], author_text)
            )
            if not all(word in searchable for word in words):
                continue
            items.append({
                "key": row["key"],
                "data": {
                    "itemType": row["typeName"] or "",
                    "title": row["title"] or "",
                    "date": row["date"] or "",
                    "DOI": row["doi"] or "",
                    "creators": authors,
                    "collections": [],
                },
                "_source": "local-db",
                "_sqlite_status": diag.get("status"),
                "_sqlite_path": path,
                "_degraded": diag.get("status") != "ok",
            })
            if len(items) >= limit:
                break
        diag["matched"] = len(items)
        conn.close()
        return items, diag
    except sqlite3.DatabaseError as exc:
        diag.update({"status": "error", "message": str(exc)})
        return [], diag


def _search_local_zotero_dbs(config, query, limit):
    results = []
    diagnostics = []
    seen = set()
    for db_path in _candidate_zotero_db_paths(config, existing_only=True):
        items, diag = _search_local_zotero_db(db_path, query, max(limit - len(results), 0))
        diagnostics.append(diag)
        for item in items:
            key = item.get("key")
            if key in seen:
                continue
            seen.add(key)
            results.append(item)
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break
    return results, diagnostics


def _safe_attachment_names(att_data):
    names = []
    for key in ("filename", "title"):
        value = att_data.get(key)
        if value:
            names.append(value)
    path_value = att_data.get("path")
    if isinstance(path_value, str) and path_value.startswith("storage:"):
        names.append(path_value.split(":", 1)[1])

    safe = []
    for name in names:
        basename = os.path.basename(str(name).replace("\\", "/"))
        if basename and basename not in safe:
            safe.append(basename)
    return safe


def _is_pdf_file(path):
    if not os.path.isfile(path):
        return False
    if not path.lower().endswith(".pdf"):
        return False
    try:
        with open(path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


def _find_local_attachment_pdf(config, attachment):
    att_key = attachment.get("key")
    if not att_key:
        return None
    att_data = attachment.get("data", {})
    names = _safe_attachment_names(att_data)

    for storage_dir in _candidate_storage_dirs(config):
        folder = os.path.join(storage_dir, att_key)
        if not os.path.isdir(folder):
            continue
        for name in names:
            candidate = os.path.join(folder, name)
            if _is_pdf_file(candidate):
                return {"path": candidate, "storage_dir": storage_dir, "attachment_key": att_key}
        try:
            for name in sorted(os.listdir(folder)):
                candidate = os.path.join(folder, name)
                if _is_pdf_file(candidate):
                    return {"path": candidate, "storage_dir": storage_dir, "attachment_key": att_key}
        except OSError:
            continue
    return None


def cmd_search(args):
    from lib.cache import update_cache_from_search, search_cache

    diagnostics = None
    search_source = "zotero-api"

    if args.local_db and args.bibtex:
        _output({"status": "error", "action": "search", "message": "--bibtex requires Zotero API search", "code": "LOCAL_DB_BIBTEX_UNSUPPORTED"})
        return

    if args.local_db:
        config = load_config()
        items, diagnostics = _search_local_zotero_dbs(config, args.query, args.limit)
        search_source = "local-db"
    else:
        config = load_config(require=["ZOTERO_API_KEY"])
        client = ZoteroClient(config)

        if args.bibtex:
            items = client.search(args.query, limit=args.limit)
            for item in items:
                key = item["key"]
                try:
                    bib = client.zot.item(key, format="bibtex")
                    print(bib)
                except Exception as e:
                    print(f"% Error fetching bibtex for {key}: {e}", file=sys.stderr)
            return

        # Try API first, fall back to cache
        try:
            items = client.search(args.query, limit=args.limit)
            # Update cache with results
            update_cache_from_search(config, items)
        except Exception as e:
            _progress(f"API unreachable: {e} — searching cache")
            items, age = search_cache(config, args.query)
            search_source = "cache"
            if items:
                age_str = f"{age:.0f}h ago" if age else "unknown age"
                _progress(f"Found {len(items)} results in cache ({age_str})")
            else:
                _output({"status": "error", "action": "search",
                          "message": f"API unreachable and no cache available: {e}",
                          "code": "API_UNREACHABLE_USING_CACHE"})
                return

    if not items:
        payload = {"status": "ok", "action": "search", "source": search_source,
                   "results": [], "message": "No results found"}
        if diagnostics is not None:
            payload["diagnostics"] = diagnostics
        _output(payload)
        return

    results = []
    for item in items:
        d = item["data"]
        creators = d.get("creators", [])
        authors = []
        for c in creators:
            if c.get("creatorType") == "author":
                name = c.get("name") or f"{c.get('lastName', '')}, {c.get('firstName', '')}"
                authors.append(name)
        results.append({
            "key": item["key"],
            "title": d.get("title", ""),
            "authors": authors,
            "year": d.get("date", ""),
            "doi": d.get("DOI", ""),
            "type": d.get("itemType", ""),
            "collections": d.get("collections", []),
            "source": item.get("_source", search_source),
            "degraded": bool(item.get("_degraded", False)),
        })

    if args.json:
        payload = {"status": "ok", "action": "search", "source": search_source,
                   "results": results, "count": len(results)}
        if diagnostics is not None:
            payload["diagnostics"] = diagnostics
        _output(payload)
    else:
        for i, r in enumerate(results, 1):
            authors_str = "; ".join(r["authors"][:3])
            if len(r["authors"]) > 3:
                authors_str += " et al."
            print(f"{i}. {r['title']}")
            print(f"   {authors_str} ({r['year']})")
            if r["doi"]:
                print(f"   DOI: {r['doi']}")
            if r["source"] != "zotero-api":
                note = f"   Source: {r['source']}"
                if r["degraded"]:
                    note += " (degraded)"
                print(note)
            print()


def cmd_add(args):
    try:
        import fcntl
    except ImportError:
        fcntl = None  # Windows: skip file locking
    from lib.metadata import fetch_metadata, detect_input_type

    config = load_config(require=["ZOTERO_API_KEY"])
    client = ZoteroClient(config)

    # Batch file mode
    if args.batch_file:
        _cmd_add_batch(args, config, client)
        return

    # Manifest import mode
    if args.manifest:
        _cmd_add_from_manifest(args, config, client)
        return

    if not args.identifier:
        _output({"status": "error", "action": "add", "message": "No identifier provided", "code": "METADATA_FETCH_FAILED"})
        sys.exit(1)

    # Progress
    _progress("1/5 Fetching metadata...")

    try:
        metadata, input_type, normalized = fetch_metadata(
            args.identifier,
            config.get("translation_server", "http://localhost:1969"),
            config,
        )
    except ConnectionError as e:
        _output({"status": "error", "action": "add", "message": str(e), "code": "TRANSLATION_SERVER_DOWN"})
        sys.exit(1)
    except (ValueError, RuntimeError, TimeoutError) as e:
        _output({"status": "error", "action": "add", "message": str(e), "code": "METADATA_FETCH_FAILED"})
        sys.exit(1)

    title = metadata.get("title", "Unknown")
    doi = metadata.get("DOI", "")
    creators = metadata.get("creators", [])
    authors = []
    for c in creators:
        if c.get("creatorType") == "author":
            authors.append(c.get("name") or f"{c.get('lastName', '')}, {c.get('firstName', '')}")

    # Dry-run: preview only
    if args.dry_run:
        dry_result = {
            "status": "dry_run", "action": "add",
            "title": title,
            "authors": authors,
            "year": metadata.get("date", ""),
            "type": metadata.get("itemType", ""),
            "doi": doi,
            "duplicate": False,
            "pdf_available": None,
            "suggested_collections": [],
            "existing_collections": [],
        }
        # Check duplicate
        if doi:
            existing = client.search_by_doi(doi, title_hint=title)
            if existing and not args.force:
                dry_result["duplicate"] = True
                dry_result["existing_key"] = existing["key"]
        # Check PDF availability via Semantic Scholar
        if doi:
            try:
                import requests as _req
                ss_url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=openAccessPdf"
                ss_resp = _req.get(ss_url, timeout=10)
                if ss_resp.status_code == 200:
                    oa = ss_resp.json().get("openAccessPdf") or {}
                    if oa.get("url"):
                        dry_result["pdf_available"] = True
                        dry_result["pdf_source"] = f"Semantic Scholar ({oa['url'][:60]}...)"
                    else:
                        dry_result["pdf_available"] = False
                        dry_result["pdf_source"] = "Not found on Semantic Scholar"
            except Exception:
                pass
        # Get existing collections for suggestions
        try:
            colls = client.collections()
            dry_result["existing_collections"] = [c["data"]["name"] for c in colls]
        except Exception:
            pass
        _output(dry_result)
        return

    # Duplicate check with file lock
    if doi and not args.force:
        lock_fd = None
        lock_path = os.path.join(config["staging_dir"], f"{_hash_id(doi)}.lock")
        os.makedirs(config["staging_dir"], exist_ok=True)
        if fcntl:
            try:
                lock_fd = open(lock_path, "w")
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                _output({"status": "error", "action": "add",
                          "message": "Another zot add for this DOI is in progress", "code": "DUPLICATE_DOI"})
                sys.exit(1)

        try:
            existing = client.search_by_doi(doi, title_hint=title)
            if existing:
                _output({
                    "status": "exists", "action": "add", "title": title,
                    "key": existing["key"],
                    "collections": existing["data"].get("collections", []),
                    "message": "Already in library",
                })
                return
        finally:
            if fcntl and lock_fd:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    lock_fd.close()
                    os.remove(lock_path)
                except OSError:
                    pass

    # Resolve collection keys
    collection_keys = []
    if args.collections and not args.no_collection:
        all_colls = client.collections()
        coll_map = {}
        for c in all_colls:
            coll_map[c["data"]["name"].lower()] = c["key"]
            # Support Parent/Child path syntax
        for coll_name in args.collections:
            if "/" in coll_name:
                parts = coll_name.split("/")
                # Find the deepest child
                key = _resolve_collection_path(parts, all_colls)
                if key:
                    collection_keys.append(key)
                else:
                    _progress(f"Warning: collection path '{coll_name}' not found, skipping")
            else:
                key = coll_map.get(coll_name.lower())
                if key:
                    collection_keys.append(key)
                else:
                    _progress(f"Warning: collection '{coll_name}' not found, skipping")

    # Create item
    _progress("2/5 Creating item...")
    item_data = {k: v for k, v in metadata.items() if not k.startswith("_")}
    # Remove fields that shouldn't be in the creation payload
    for skip in ["attachments", "notes", "seeAlso", "id", "accessDate"]:
        item_data.pop(skip, None)

    result = client.create_item(item_data, collection_keys=collection_keys or None)

    if not result or not isinstance(result, dict):
        _output({"status": "error", "action": "add", "message": "Failed to create item", "code": "ZOTERO_API_ERROR"})
        sys.exit(1)

    item_key = result.get("key", result.get("data", {}).get("key", ""))

    # PDF download (unless --no-pdf)
    version = "metadata_only"
    verified = False
    pdf_source = None

    if not args.no_pdf:
        from lib.downloader import download as download_pdf
        from lib.renamer import rename as rename_pdf

        _progress("2/5 Downloading PDF...")
        dl_result = download_pdf(metadata, config, accept_short=args.accept_short)

        if dl_result["found"]:
            version = dl_result["version"]
            verified = dl_result["verified"]
            pdf_source = dl_result["source"]

            _progress("4/5 Renaming...")
            pattern = config.get("zotfile_pattern")
            new_name = rename_pdf(metadata, pattern) + ".pdf"
            new_path = os.path.join(os.path.dirname(dl_result["path"]), new_name)
            try:
                os.rename(dl_result["path"], new_path)
            except OSError:
                new_path = dl_result["path"]  # keep original if rename fails

            # WebDAV upload (if configured)
            if config.get("webdav_url") and config.get("WEBDAV_PASSWORD"):
                _progress("5/5 Uploading to WebDAV...")
                from lib.webdav import WebDAVClient, populate_imported_file_attachment

                # Create attachment child item (need key for zip filename)
                att_template = client.zot.item_template("attachment", "imported_file")
                att_template["title"] = os.path.basename(new_path)
                att_template["filename"] = os.path.basename(new_path)
                att_template["parentItem"] = item_key
                att_template["contentType"] = "application/pdf"
                populate_imported_file_attachment(att_template, new_path)
                att_result = client._retry(client.zot.create_items, [att_template])

                att_item = None
                att_key = None
                if att_result and "successful" in att_result:
                    att_item = list(att_result["successful"].values())[0]
                    att_key = att_item.get("key", att_item.get("data", {}).get("key", ""))

                if att_key:
                    webdav = WebDAVClient(config)
                    try:
                        webdav.upload(att_key, new_path, os.path.basename(new_path))
                        _progress(f"Uploaded as {att_key}.zip")
                        # Clean up staging file
                        try:
                            os.remove(new_path)
                        except OSError:
                            pass
                    except Exception as e:
                        # Rollback: delete attachment item
                        _progress(f"WebDAV upload failed: {e} — rolling back attachment")
                        try:
                            full_att = client.get_item(att_key)
                            client.delete_item(full_att)
                        except Exception as re:
                            # Log orphan for doctor cleanup
                            _log_orphan(config, att_key, str(e))
                            _progress(f"Rollback also failed: {re} — logged orphan {att_key}")
                else:
                    _progress("Failed to create attachment item — PDF not uploaded")
            else:
                _progress("5/5 PDF ready (WebDAV not configured)")
        else:
            _progress(f"No PDF found: {dl_result['reason']}")
            # Create watch for monitoring (Phase 8)

    coll_names = [_resolve_key_to_name(k, client) for k in collection_keys] if collection_keys else []

    output = {
        "status": "ok", "action": "add", "title": title, "key": item_key,
        "version": version, "verified": verified,
        "collections": coll_names,
        "message": _build_add_message(version, verified, pdf_source, coll_names, args.no_pdf),
    }

    if pdf_source:
        output["source"] = pdf_source

    # Append to local cache
    try:
        from lib.cache import append_to_cache
        cache_item = {"key": item_key, "data": {k: v for k, v in metadata.items() if not k.startswith("_")}}
        cache_item["data"]["collections"] = collection_keys
        append_to_cache(config, cache_item)
    except Exception:
        pass

    _output(output)
    _trigger_ingest({**metadata, "key": item_key})


def _cmd_add_batch(args, config, client):
    """Add papers from a file with one identifier per line."""
    import time as _time
    if not os.path.exists(args.batch_file):
        _output({"status": "error", "action": "add", "message": f"File not found: {args.batch_file}", "code": "CONFIG_MISSING"})
        sys.exit(1)

    with open(args.batch_file) as f:
        identifiers = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    import subprocess
    import threading
    from lib.parallel import get_workers

    def _add_one(ident):
        cmd = [sys.executable, __file__, "--json", "add", ident]
        if args.no_pdf:
            cmd.append("--no-pdf")
        if args.collections:
            for c in args.collections:
                cmd.extend(["--collection", c])
        elif args.no_collection:
            cmd.append("--no-collection")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            return {"status": "error", "identifier": ident, "message": r.stderr.strip()}

    workers = get_workers(io_bound=True, override=getattr(args, "parallel", 0) or None)
    workers = min(workers, 4)  # cap for Zotero API rate limiting

    if workers > 1 and len(identifiers) > 1:
        from concurrent.futures import ThreadPoolExecutor
        sem = threading.Semaphore(3)
        def _rate_limited(ident):
            with sem:
                return _add_one(ident)
        _progress(f"Batch adding {len(identifiers)} items ({workers} parallel)...")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_rate_limited, identifiers))
    else:
        results = []
        for i, ident in enumerate(identifiers):
            _progress(f"Batch {i+1}/{len(identifiers)}: {ident[:40]}...")
            results.append(_add_one(ident))
            if i < len(identifiers) - 1:
                _time.sleep(args.delay)

    added = len([r for r in results if r.get("status") == "ok"])
    dupes = len([r for r in results if r.get("status") == "exists"])
    errors = len([r for r in results if r.get("status") == "error"])
    _output({"status": "ok", "action": "batch_add", "total": len(identifiers),
              "added": added, "duplicates": dupes, "errors": errors, "results": results})


def _cmd_add_from_manifest(args, config, client):
    """Add papers from a getscipapers manifest file."""
    import time as _time
    if not os.path.exists(args.manifest):
        _output({"status": "error", "action": "add", "message": f"Manifest not found: {args.manifest}", "code": "CONFIG_MISSING"})
        sys.exit(1)

    with open(args.manifest) as f:
        manifest = json.load(f)

    items = manifest.get("items", manifest.get("papers", []))
    if not items:
        _output({"status": "ok", "action": "batch_add", "total": 0, "added": 0, "message": "Empty manifest"})
        return

    results = []
    for i, item in enumerate(items):
        ident = item.get("doi") or item.get("identifier") or item.get("arxiv_id", "")
        if not ident:
            continue

        _progress(f"Manifest {i+1}/{len(items)}: {ident[:40]}...")
        cmd = [sys.executable, __file__, "--json", "add", ident]
        if args.no_pdf:
            cmd.append("--no-pdf")
        if args.collections:
            for c in args.collections:
                cmd.extend(["--collection", c])

        import subprocess
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        try:
            results.append(json.loads(r.stdout))
        except json.JSONDecodeError:
            results.append({"status": "error", "identifier": ident, "message": r.stderr.strip()})

        if i < len(items) - 1:
            _time.sleep(args.delay)

    added = len([r for r in results if r.get("status") == "ok"])
    _output({"status": "ok", "action": "batch_add", "total": len(items),
              "added": added, "results": results})


def cmd_add_file(args):
    """Add a local file (PDF, EPUB, DJVU, image, etc.) to Zotero as a new item."""
    import shutil
    from lib.filetype import detect_content_type, is_pdf
    from lib.renamer import rename as rename_meta, rename_non_pdf

    config = load_config(require=["ZOTERO_API_KEY"])
    client = ZoteroClient(config)

    file_path = os.path.abspath(args.file_path)
    if not os.path.exists(file_path):
        _output({"status": "error", "action": "add-file",
                  "message": f"File not found: {file_path}", "code": "FILE_NOT_FOUND"})
        sys.exit(1)

    # Detect file type
    content_type, ext = detect_content_type(file_path)
    if not ext:
        ext = os.path.splitext(file_path)[1]
    is_pdf_file = is_pdf(content_type)

    # Step 1: Verify (full checks for PDFs, basic checks for other files)
    _progress("1/5 Verifying file...")
    verify_result = {"status": "accept", "reason": "Non-PDF file", "page_count": None}

    if is_pdf_file:
        from lib.verifier import verify as verify_pdf
        verify_result = verify_pdf(file_path, metadata=None, source_type="local",
                                   accept_short=args.accept_short)
        if verify_result["status"] == "reject":
            _output({"status": "error", "action": "add-file",
                      "message": f"PDF rejected: {verify_result['reason']}", "code": "FILE_REJECTED"})
            sys.exit(1)
    else:
        # Basic sanity for non-PDF: file not empty, not absurdly large
        size = os.path.getsize(file_path)
        if size == 0:
            _output({"status": "error", "action": "add-file",
                      "message": "File is empty", "code": "FILE_REJECTED"})
            sys.exit(1)
        if size > 500 * 1024 * 1024:  # 500MB
            _output({"status": "error", "action": "add-file",
                      "message": f"File too large ({size} bytes, max 500MB)", "code": "FILE_REJECTED"})
            sys.exit(1)

    # Step 2: Get metadata
    metadata = None
    doi_source = "none"
    if args.identifier:
        _progress("2/5 Fetching metadata...")
        from lib.metadata import fetch_metadata
        try:
            metadata, input_type, normalized = fetch_metadata(
                args.identifier,
                config.get("translation_server", "http://localhost:1969"),
                config,
            )
            doi_source = "identifier"
        except (ConnectionError, ValueError, RuntimeError, TimeoutError) as e:
            _progress(f"Metadata fetch failed: {e} — creating minimal item")

    # Auto-extract DOI from PDF content via getscipapers
    if metadata is None and is_pdf_file and not getattr(args, 'no_doi_extract', False):
        extracted_doi = _try_extract_doi(file_path)
        if extracted_doi:
            _progress(f"2/5 Found DOI: {extracted_doi} — fetching metadata...")
            from lib.metadata import fetch_metadata
            try:
                metadata, _, _ = fetch_metadata(
                    extracted_doi,
                    config.get("translation_server", "http://localhost:1969"),
                    config,
                )
                doi_source = "pdf_content"
            except (ConnectionError, ValueError, RuntimeError, TimeoutError) as e:
                _progress(f"Metadata fetch failed: {e} — using minimal extraction")

    if metadata is None:
        _progress("2/5 Extracting metadata from file...")
        if is_pdf_file:
            metadata = _extract_pdf_metadata(file_path)
        else:
            metadata = _extract_file_metadata(file_path, content_type)

    title = metadata.get("title", "Unknown")
    doi = metadata.get("DOI", "")

    # Dry-run
    if args.dry_run:
        _output({"status": "dry_run", "action": "add-file",
                  "title": title, "file_path": file_path,
                  "content_type": content_type,
                  "page_count": verify_result.get("page_count"),
                  "message": f"Would add '{title}' from local file ({content_type})"})
        return

    # Step 3: Duplicate check
    if doi and not args.force:
        existing = client.search_by_doi(doi, title_hint=title)
        if existing:
            _output({"status": "exists", "action": "add-file", "title": title,
                      "key": existing["key"],
                      "message": "Already in library (use --force to add anyway)"})
            return

    # Resolve collections
    collection_keys = []
    if args.collections and not args.no_collection:
        all_colls = client.collections()
        coll_map = {c["data"]["name"].lower(): c["key"] for c in all_colls}
        for coll_name in args.collections:
            if "/" in coll_name:
                key = _resolve_collection_path(coll_name.split("/"), all_colls)
            else:
                key = coll_map.get(coll_name.lower())
            if key:
                collection_keys.append(key)
            else:
                _progress(f"Warning: collection '{coll_name}' not found, skipping")

    # Step 4: Create item
    _progress("3/5 Creating item...")
    item_data = {k: v for k, v in metadata.items() if not k.startswith("_")}
    for skip in ["attachments", "notes", "seeAlso", "id", "accessDate"]:
        item_data.pop(skip, None)

    result = client.create_item(item_data, collection_keys=collection_keys or None)
    if not result or not isinstance(result, dict):
        _output({"status": "error", "action": "add-file",
                  "message": "Failed to create item", "code": "ZOTERO_API_ERROR"})
        sys.exit(1)

    item_key = result.get("key", result.get("data", {}).get("key", ""))

    # Step 5: Rename + upload
    _progress("4/5 Renaming...")
    pattern = config.get("zotfile_pattern")
    if is_pdf_file:
        new_name = rename_meta(metadata, pattern) + ".pdf"
    else:
        orig_name = os.path.basename(file_path)
        stem = rename_non_pdf(orig_name, metadata)
        new_name = stem + ext

    staging = config["staging_dir"]
    os.makedirs(staging, exist_ok=True)
    staged_path = os.path.join(staging, new_name)
    shutil.copy2(file_path, staged_path)

    # WebDAV upload
    if config.get("webdav_url") and config.get("WEBDAV_PASSWORD"):
        _progress("5/5 Uploading to WebDAV...")
        from lib.webdav import WebDAVClient, populate_imported_file_attachment

        att_template = client.zot.item_template("attachment", "imported_file")
        att_template["title"] = new_name
        att_template["filename"] = new_name
        att_template["parentItem"] = item_key
        att_template["contentType"] = content_type
        populate_imported_file_attachment(att_template, staged_path)
        att_result = client._retry(client.zot.create_items, [att_template])

        att_key = None
        if att_result and "successful" in att_result:
            att_item = list(att_result["successful"].values())[0]
            att_key = att_item.get("key", att_item.get("data", {}).get("key", ""))

        if att_key:
            webdav = WebDAVClient(config)
            try:
                webdav.upload(att_key, staged_path, new_name)
                _progress(f"Uploaded as {att_key}.zip")
                try:
                    os.remove(staged_path)
                except OSError:
                    pass
            except Exception as e:
                _progress(f"WebDAV upload failed: {e} — rolling back attachment")
                try:
                    full_att = client.get_item(att_key)
                    client.delete_item(full_att)
                except Exception as re:
                    _log_orphan(config, att_key, str(e))
                    _progress(f"Rollback also failed: {re} — logged orphan {att_key}")
        else:
            _progress("Failed to create attachment item — file not uploaded")
    else:
        _progress("5/5 File staged (WebDAV not configured)")

    coll_names = [_resolve_key_to_name(k, client) for k in collection_keys] if collection_keys else []

    _output({
        "status": "ok", "action": "add-file", "title": title, "key": item_key,
        "content_type": content_type,
        "verified": verify_result["status"] == "accept",
        "page_count": verify_result.get("page_count"),
        "collections": coll_names,
        "filename": new_name,
        "doi_source": doi_source,
        "message": f"Item created from local file ({content_type})",
    })

    _trigger_ingest({**metadata, "key": item_key})


# Keep add-pdf as an alias for backwards compatibility
cmd_add_pdf = cmd_add_file


def _extract_file_metadata(file_path, content_type=""):
    """Extract basic metadata from a non-PDF file for creating a Zotero item."""
    basename = os.path.basename(file_path)
    stem = os.path.splitext(basename)[0]

    # Map content types to reasonable Zotero itemTypes
    type_map = {
        "application/epub+zip": "book",
        "application/x-mobipocket-ebook": "book",
        "application/djvu": "document",
        "image/": "artwork",
        "video/": "videoRecording",
        "audio/": "audioRecording",
        "text/html": "webpage",
        "application/vnd.openxmlformats-officedocument.presentationml": "presentation",
        "application/vnd.openxmlformats-officedocument.wordprocessingml": "document",
    }

    item_type = "document"  # default
    for prefix, ztype in type_map.items():
        if content_type.startswith(prefix):
            item_type = ztype
            break

    # Try to parse a meaningful title from the filename
    # Common patterns: "Author - Title (Year)", "Author_Year_Title", etc.
    title = stem.replace("_", " ").replace("-", " — ", 1) if "_" in stem else stem

    metadata = {
        "itemType": item_type,
        "title": title,
        "creators": [],
        "date": "",
    }

    # For EPUB: try to extract metadata from the package
    if content_type == "application/epub+zip":
        try:
            import zipfile
            with zipfile.ZipFile(file_path) as zf:
                # Find the OPF file
                for name in zf.namelist():
                    if name.endswith(".opf"):
                        opf = zf.read(name).decode("utf-8", errors="replace")
                        import re
                        t = re.search(r"<dc:title[^>]*>([^<]+)</dc:title>", opf)
                        if t:
                            metadata["title"] = t.group(1).strip()
                        a = re.findall(r"<dc:creator[^>]*>([^<]+)</dc:creator>", opf)
                        for author in a:
                            author = author.strip()
                            parts = author.rsplit(" ", 1)
                            if len(parts) == 2:
                                metadata["creators"].append({
                                    "creatorType": "author",
                                    "firstName": parts[0], "lastName": parts[1],
                                })
                            else:
                                metadata["creators"].append({
                                    "creatorType": "author", "name": author,
                                })
                        d = re.search(r"<dc:date[^>]*>([^<]+)</dc:date>", opf)
                        if d:
                            metadata["date"] = d.group(1).strip()[:10]
                        isbn = re.search(
                            r'<dc:identifier[^>]*opf:scheme="ISBN"[^>]*>([^<]+)</dc:identifier>', opf)
                        if isbn:
                            metadata["ISBN"] = isbn.group(1).strip()
                        break
        except Exception:
            pass

    return metadata


def _extract_pdf_metadata(pdf_path):
    """Extract basic metadata from a PDF file for creating a Zotero item."""
    metadata = {
        "itemType": "document",
        "title": os.path.splitext(os.path.basename(pdf_path))[0],
        "creators": [],
        "date": "",
    }

    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        info = reader.metadata
        if info:
            if info.title:
                metadata["title"] = info.title
            if info.author:
                # Split on common delimiters
                import re
                authors = re.split(r"[,;&]| and ", info.author)
                for a in authors:
                    a = a.strip()
                    if a:
                        parts = a.rsplit(" ", 1)
                        if len(parts) == 2:
                            metadata["creators"].append({
                                "creatorType": "author",
                                "firstName": parts[0],
                                "lastName": parts[1],
                            })
                        else:
                            metadata["creators"].append({
                                "creatorType": "author",
                                "name": a,
                            })
            if info.get("/CreationDate"):
                import re
                m = re.search(r"(\d{4})", str(info.get("/CreationDate")))
                if m:
                    metadata["date"] = m.group(1)
    except Exception:
        pass

    # Try pdfplumber for title from first page if PyPDF2 metadata was empty
    if metadata["title"] == os.path.splitext(os.path.basename(pdf_path))[0]:
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                if pdf.pages:
                    text = pdf.pages[0].extract_text() or ""
                    lines = [l.strip() for l in text.split("\n") if l.strip()]
                    if lines:
                        # Heuristic: first non-trivial line is likely the title
                        for line in lines:
                            if len(line) > 10 and not line[0].isdigit():
                                metadata["title"] = line[:200]
                                break
        except Exception:
            pass

    return metadata


def _try_extract_doi(pdf_path):
    """Attempt to extract a DOI from PDF content using getscipapers CLI.

    Returns a DOI string or None.
    """
    import shutil
    import subprocess
    if not shutil.which("getscipapers"):
        _progress("DOI extraction: getscipapers not in PATH, skipping")
        return None
    try:
        _progress("2/5 Extracting DOI from PDF content...")
        result = subprocess.run(
            ["getscipapers", "getpapers", "--extract-doi-from-pdf", pdf_path, "--no-download"],
            capture_output=True, text=True, timeout=60
        )
        for line in result.stdout.splitlines():
            if "Extracted DOI from PDF:" in line:
                doi = line.split("Extracted DOI from PDF:")[-1].strip()
                if doi:
                    return doi
    except (subprocess.TimeoutExpired, OSError) as e:
        _progress(f"DOI extraction failed: {e}")
    return None


def _build_add_message(version, verified, source, collections, no_pdf):
    parts = ["Item created"]
    if no_pdf:
        parts.append("(metadata only)")
    elif version == "metadata_only":
        parts.append("(no PDF found)")
    elif version == "published":
        parts.append("with published PDF")
    elif version == "author_copy":
        if verified:
            parts.append(f"with author copy")
        else:
            parts.append("with unverified copy — please check")
    elif version == "preprint":
        parts.append("with arXiv preprint (not final version)")

    if not collections:
        parts.append("— no collection specified")
    return " ".join(parts)


def _hash_id(identifier):
    import hashlib
    return hashlib.sha256(identifier.encode()).hexdigest()[:16]


def _resolve_collection_path(parts, all_collections):
    """Resolve Parent/Child/... path to a collection key."""
    by_key = {c["key"]: c for c in all_collections}
    current_parent = None
    current_key = None
    for part in parts:
        found = False
        for c in all_collections:
            d = c["data"]
            if d["name"].lower() == part.lower():
                parent = d.get("parentCollection", False)
                if current_parent is None and not parent:
                    current_key = c["key"]
                    current_parent = c["key"]
                    found = True
                    break
                elif parent == current_parent:
                    current_key = c["key"]
                    current_parent = c["key"]
                    found = True
                    break
        if not found:
            return None
    return current_key


_collection_name_cache = {}

def _resolve_key_to_name(key, client):
    """Convert collection key to name (best effort, cached)."""
    if not _collection_name_cache:
        try:
            for c in client.collections():
                _collection_name_cache[c["key"]] = c["data"]["name"]
        except Exception:
            pass
    return _collection_name_cache.get(key, key)


def _log_orphan(config, attachment_key, error_msg):
    """Log an orphaned attachment key for doctor cleanup."""
    import datetime
    orphan_log = os.path.join(config["workspace"], "data", "research", "zotero", "orphaned-keys.log")
    os.makedirs(os.path.dirname(orphan_log), exist_ok=True)
    with open(orphan_log, "a") as f:
        f.write(f"{datetime.datetime.utcnow().isoformat()} {attachment_key} {error_msg}\n")


def _progress(msg):
    """Print progress to stderr."""
    print(f"[{msg}]", file=sys.stderr)


def cmd_get(args):
    config = load_config(require=["ZOTERO_API_KEY"])
    client = ZoteroClient(config)

    if args.link:
        if not config.get("gdrive_folder_id") or not (config.get("GDRIVE_CREDENTIALS") or config.get("gdrive_credentials_file")):
            _output({"status": "error", "action": "share", "message": "Google Drive not configured", "code": "GDRIVE_ERROR"})
            return
        from lib.gdrive import GDriveClient
        try:
            gd = GDriveClient(config)
        except Exception as e:
            _output({"status": "error", "action": "share", "message": f"GDrive auth failed: {e}", "code": "GDRIVE_ERROR"})
            return

        results = gd.search(args.query, max_results=5)
        if not results:
            _output({"status": "error", "action": "share", "message": f"No files matching '{args.query}' in Zotero GDrive folder", "code": "GDRIVE_ERROR"})
            return

        # Use first result (or could show list like get)
        target = results[0]
        try:
            link = gd.create_share_link(target["id"])
        except Exception as e:
            # Fall back to webViewLink if sharing fails
            link = target.get("webViewLink", "")
            if not link:
                _output({"status": "error", "action": "share", "message": f"Could not create share link: {e}", "code": "GDRIVE_ERROR"})
                return

        _output({"status": "ok", "action": "share", "link": link, "title": target["name"],
                  "file_id": target["id"]})
        return

    # Search for the paper
    items = client.search(args.query, limit=25)
    if not items:
        _output({
            "status": "error",
            "action": "get",
            "message": "No Zotero item found for query",
            "code": "ITEM_NOT_FOUND",
        })
        return

    # Filter to items with potential attachments (not notes, not attachments themselves)
    parent_items = [i for i in items if i["data"].get("itemType") not in ("note", "attachment")]
    if not parent_items:
        _output({
            "status": "error",
            "action": "get",
            "message": "No parent Zotero items found for query",
            "code": "ITEM_NOT_FOUND",
        })
        return

    # Multiple results → show list (unless --index given)
    if len(parent_items) > 1 and args.index is None:
        results = []
        for i, item in enumerate(parent_items, 1):
            d = item["data"]
            creators = d.get("creators", [])
            authors = [c.get("lastName", c.get("name", "")) for c in creators if c.get("creatorType") == "author"]
            results.append({
                "index": i, "key": item["key"],
                "title": d.get("title", ""), "year": d.get("date", ""),
                "authors": authors[:3],
            })
        _output({"status": "ok", "action": "get", "multiple": True, "results": results,
                  "message": f"{len(results)} results. Use --index N to select."})
        return

    # Select item
    if args.index is not None:
        idx = args.index - 1
        if idx < 0 or idx >= len(parent_items):
            _output({"status": "error", "action": "get", "message": f"Index {args.index} out of range (1-{len(parent_items)})", "code": "PDF_NOT_FOUND"})
            return
        selected = parent_items[idx]
    else:
        selected = parent_items[0]

    title = selected["data"].get("title", "Unknown")
    parent_key = selected["key"]

    # Find attachment
    children = client.children(parent_key)
    attachments = [c for c in children if c["data"].get("itemType") == "attachment"
                   and c["data"].get("contentType") == "application/pdf"]

    if not attachments:
        _output({"status": "no_attachment", "action": "get", "key": parent_key, "title": title,
                  "message": "No PDF attached. Want me to try downloading it now?"})
        return

    att = attachments[0]
    att_key = att["key"]

    if getattr(args, "local_storage", True):
        local_pdf = _find_local_attachment_pdf(config, att)
        if local_pdf:
            _output_get_success(
                args,
                local_pdf["path"],
                title,
                parent_key,
                selected["data"],
                source="local-storage",
                attachment_key=att_key,
            )
            return

    # Download from WebDAV
    if not config.get("webdav_url") or not config.get("WEBDAV_PASSWORD"):
        _output({"status": "error", "action": "get", "message": "WebDAV not configured", "code": "WEBDAV_ERROR"})
        return

    from lib.webdav import WebDAVClient
    webdav = WebDAVClient(config)
    staging = config["staging_dir"]
    os.makedirs(staging, exist_ok=True)

    try:
        pdf_path = webdav.download(att_key, staging)
    except Exception as e:
        _output({"status": "error", "action": "get", "message": f"WebDAV download failed: {e}", "code": "WEBDAV_ERROR"})
        return

    if not pdf_path:
        _output({"status": "error", "action": "get", "message": f"Attachment {att_key}.zip not found on WebDAV", "code": "WEBDAV_ERROR"})
        return

    _output_get_success(
        args,
        pdf_path,
        title,
        parent_key,
        selected["data"],
        source="webdav",
        attachment_key=att_key,
    )


def _output_get_success(args, pdf_path, title, parent_key, item_data, source, attachment_key=None):
    # --send: automatically send the file after download
    if getattr(args, "send", None):
        channel, target = args.send
        send_result = _send_file(pdf_path, channel, target, title)
        payload = {"status": "ok", "action": "get", "file_path": pdf_path, "title": title,
                   "key": parent_key, "source": source, "send": send_result}
    else:
        payload = {"status": "ok", "action": "get", "file_path": pdf_path, "title": title,
                   "key": parent_key, "source": source}
    if attachment_key:
        payload["attachment_key"] = attachment_key
    _output(payload)

    _trigger_ingest(item_data)


def _send_file(file_path, channel, target, title):
    """Call send_file.sh and return the parsed result."""
    import subprocess
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "send_file.sh")
    try:
        proc = subprocess.run(
            [script, channel, target, file_path, title],
            capture_output=True, text=True, timeout=180,
        )
        try:
            return json.loads(proc.stdout.strip())
        except (json.JSONDecodeError, ValueError):
            return {"status": "error", "message": proc.stdout.strip() or proc.stderr.strip() or f"exit {proc.returncode}"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "send_file.sh timed out (180s)"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def cmd_update(args):
    config = load_config(require=["ZOTERO_API_KEY"])
    client = ZoteroClient(config)

    # Fetch the existing item
    try:
        item = client.get_item(args.key)
    except Exception as e:
        _output({"status": "error", "action": "update", "message": f"Item not found: {e}", "code": "ZOTERO_API_ERROR"})
        sys.exit(1)

    data = item["data"]
    title = data.get("title", "Unknown")

    # --attach-pdf / --attach-file: download or use local file + verify + rename + attach + WebDAV upload
    local_file = getattr(args, "local_file", None) or getattr(args, "local_pdf", None)
    attach_any = args.attach_pdf or local_file

    if attach_any:
        from lib.renamer import rename as rename_meta, rename_non_pdf
        from lib.filetype import detect_content_type, is_pdf

        if local_file:
            # Local file path — detect type from the actual file
            local_file = os.path.abspath(local_file)
            content_type, ext = detect_content_type(local_file)
        else:
            content_type = "application/pdf"
            ext = ".pdf"

        is_pdf_file = is_pdf(content_type)

        # Check if attachment already exists (prevent duplicates)
        children = client.children(args.key)
        existing_atts = [c for c in children if c["data"].get("itemType") == "attachment"
                         and c["data"].get("contentType") == content_type]
        if existing_atts:
            _output({"status": "exists", "action": "update", "key": args.key, "title": title,
                      "message": f"Attachment of type {content_type} already exists"})
            return

        if local_file:
            # Use local file
            import shutil
            if not os.path.exists(local_file):
                _output({"status": "error", "action": "update", "key": args.key,
                          "message": f"File not found: {local_file}", "code": "FILE_NOT_FOUND"})
                return

            if is_pdf_file:
                from lib.verifier import verify as verify_pdf
                _progress("Verifying local PDF...")
                v = verify_pdf(local_file, metadata=data, source_type="local", accept_short=False)
                if v["status"] == "reject":
                    _output({"status": "error", "action": "update", "key": args.key,
                              "message": f"PDF rejected: {v['reason']}", "code": "FILE_REJECTED"})
                    return
            else:
                size = os.path.getsize(local_file)
                if size == 0:
                    _output({"status": "error", "action": "update", "key": args.key,
                              "message": "File is empty", "code": "FILE_REJECTED"})
                    return

            _progress("Renaming...")
            pattern = config.get("zotfile_pattern")
            if is_pdf_file:
                new_name = rename_meta(data, pattern) + ".pdf"
            else:
                orig_name = os.path.basename(local_file)
                if not ext:
                    ext = os.path.splitext(orig_name)[1]
                stem = rename_non_pdf(orig_name, data)
                new_name = stem + ext

            staging = config["staging_dir"]
            os.makedirs(staging, exist_ok=True)
            new_path = os.path.join(staging, new_name)
            shutil.copy2(local_file, new_path)

            version = "local"
            verified = True
        else:
            # Download PDF (original behavior — only for PDFs)
            from lib.metadata import detect_input_type
            doi = data.get("DOI", "")
            arxiv_id = ""
            if doi and doi.startswith("10.48550/arXiv."):
                arxiv_id = doi.replace("10.48550/arXiv.", "")
            extra = data.get("extra", "")
            if not arxiv_id and "arXiv:" in extra:
                import re
                m = re.search(r"arXiv:\s*(\S+)", extra)
                if m:
                    arxiv_id = m.group(1)

            input_type = "doi" if doi else "unknown"
            if arxiv_id:
                input_type = "arxiv" if not doi or doi.startswith("10.48550/arXiv") else "doi"

            metadata = dict(data)
            metadata["_input_type"] = input_type
            metadata["_normalized_id"] = arxiv_id if input_type == "arxiv" else (doi or arxiv_id or "")
            metadata["_arxiv_id"] = arxiv_id

            from lib.downloader import download as download_pdf

            _progress("Downloading PDF...")
            dl_result = download_pdf(metadata, config, accept_short=False)

            if not dl_result["found"]:
                _output({"status": "error", "action": "update", "key": args.key, "title": title,
                          "message": f"No PDF found: {dl_result['reason']}", "code": "PDF_NOT_FOUND"})
                return

            _progress("Renaming...")
            pattern = config.get("zotfile_pattern")
            new_name = rename_meta(data, pattern) + ".pdf"
            new_path = os.path.join(os.path.dirname(dl_result["path"]), new_name)
            try:
                os.rename(dl_result["path"], new_path)
            except OSError:
                new_path = dl_result["path"]

            version = dl_result["version"]
            verified = dl_result["verified"]

        # WebDAV upload (shared for both local and download paths)
        if config.get("webdav_url") and config.get("WEBDAV_PASSWORD"):
            _progress("Uploading to WebDAV...")
            from lib.webdav import WebDAVClient, populate_imported_file_attachment

            att_template = client.zot.item_template("attachment", "imported_file")
            att_template["title"] = os.path.basename(new_path)
            att_template["filename"] = os.path.basename(new_path)
            att_template["parentItem"] = args.key
            att_template["contentType"] = content_type
            populate_imported_file_attachment(att_template, new_path)
            att_result = client._retry(client.zot.create_items, [att_template])

            att_key = None
            if att_result and "successful" in att_result:
                att_item = list(att_result["successful"].values())[0]
                att_key = att_item.get("key", att_item.get("data", {}).get("key", ""))

            if att_key:
                webdav = WebDAVClient(config)
                try:
                    webdav.upload(att_key, new_path, os.path.basename(new_path))
                    try:
                        os.remove(new_path)
                    except OSError:
                        pass
                except Exception as e:
                    _progress(f"WebDAV upload failed: {e} — rolling back")
                    try:
                        full_att = client.get_item(att_key)
                        client.delete_item(full_att)
                    except Exception:
                        _log_orphan(config, att_key, str(e))

        _output({"status": "ok", "action": "update", "key": args.key, "title": title,
                  "version": version, "verified": verified, "content_type": content_type,
                  "message": f"File attached ({version}, {content_type})"})
        return

    # --add-collection / --remove-collection: refile item
    if args.add_collections or args.remove_collections:
        current_collections = list(data.get("collections", []))
        all_colls = client.collections()
        coll_map = {c["data"]["name"].lower(): c["key"] for c in all_colls}
        key_to_name = {c["key"]: c["data"]["name"] for c in all_colls}

        if args.add_collections:
            for name in args.add_collections:
                key = coll_map.get(name.lower())
                if key and key not in current_collections:
                    current_collections.append(key)
                elif not key:
                    _progress(f"Warning: collection '{name}' not found, skipping")

        if args.remove_collections:
            for name in args.remove_collections:
                key = coll_map.get(name.lower())
                if key and key in current_collections:
                    current_collections.remove(key)

        item["data"]["collections"] = current_collections
        client.update_item(item)

        coll_names = [key_to_name.get(k, k) for k in current_collections]
        _output({"status": "ok", "action": "update", "key": args.key, "title": title,
                  "collections": coll_names,
                  "message": f"Collections updated: {', '.join(coll_names) or '(none)'}"})
        return

    # --item-type: change the itemType of the item
    if getattr(args, "item_type", None):
        item["data"]["itemType"] = args.item_type
        client.update_item(item)
        _output({"status": "ok", "action": "update", "key": args.key, "title": title,
                  "item_type": args.item_type,
                  "message": f"itemType changed to '{args.item_type}'"})
        return

    _output({"status": "error", "action": "update",
              "message": "Specify --attach-pdf, --item-type, or --add-collection/--remove-collection",
              "code": "ZOTERO_API_ERROR"})


def cmd_doctor(args):
    from lib.doctor import run_doctor
    config = load_config()
    results = run_doctor(config)
    if args.json:
        _output({"status": "ok", "action": "doctor", "checks": results})
    else:
        for check in results:
            icon = "✅" if check["ok"] else "❌"
            print(f"  {icon} {check['name']}: {check['message']}")


def cmd_list_collections(args):
    config = load_config(require=["ZOTERO_API_KEY"])
    client = ZoteroClient(config)
    collections = client.collections()

    if args.json:
        tree = _build_collection_tree(collections)
        _output({"status": "ok", "action": "list_collections", "collections": tree})
    elif args.tree:
        tree = _build_collection_tree(collections)
        _print_collection_tree(tree, indent=0)
    else:
        for c in sorted(collections, key=lambda x: x["data"]["name"]):
            d = c["data"]
            count = d.get("numItems", 0)
            print(f"  {d['name']} ({count} items)")


def cmd_create_collection(args):
    config = load_config(require=["ZOTERO_API_KEY"])
    client = ZoteroClient(config)

    parent_key = None
    if args.parent:
        collections = client.collections()
        for c in collections:
            if c["data"]["name"].lower() == args.parent.lower():
                parent_key = c["key"]
                break
        if not parent_key:
            _output({"status": "error", "action": "create_collection",
                      "message": f"Parent collection '{args.parent}' not found", "code": "ZOTERO_API_ERROR"})
            sys.exit(1)

    result = client.create_collection(args.name, parent_key)
    _output({
        "status": "ok", "action": "create_collection",
        "name": args.name,
        "parent": args.parent or None,
        "key": result.get("key", result.get("data", {}).get("key", "")),
        "message": "Collection created",
    })


def cmd_sync_cache(args):
    from lib.cache import sync_full_library
    config = load_config(require=["ZOTERO_API_KEY"])
    client = ZoteroClient(config)
    _progress("Syncing full library to cache...")
    count = sync_full_library(config, client, progress_fn=_progress)
    _output({"status": "ok", "action": "sync_cache", "items": count,
              "message": f"Cache updated with {count} items"})


def cmd_trash(args):
    config = load_config(require=["ZOTERO_API_KEY"])
    client = ZoteroClient(config)

    # Find item by key or search query
    if args.key:
        try:
            item = client.get_item(args.key)
        except Exception as e:
            _output({"status": "error", "action": "trash", "message": f"Item not found: {e}", "code": "ZOTERO_API_ERROR"})
            sys.exit(1)
        items_to_trash = [item]
    elif args.query:
        results = client.search(args.query, limit=10)
        parent_items = [i for i in results if i["data"].get("itemType") not in ("note", "attachment")]
        if not parent_items:
            _output({"status": "error", "action": "trash", "message": "No matching items found", "code": "NOT_FOUND"})
            return
        if len(parent_items) > 1 and args.index is None:
            listing = []
            for i, item in enumerate(parent_items, 1):
                d = item["data"]
                authors = [c.get("lastName", c.get("name", "")) for c in d.get("creators", []) if c.get("creatorType") == "author"]
                listing.append({"index": i, "key": item["key"], "title": d.get("title", ""), "authors": authors[:3], "year": d.get("date", "")})
            _output({"status": "ok", "action": "trash", "multiple": True, "results": listing,
                      "message": f"{len(listing)} results. Use --index N to select which to trash."})
            return
        if args.index is not None:
            idx = args.index - 1
            if idx < 0 or idx >= len(parent_items):
                _output({"status": "error", "action": "trash", "message": f"Index {args.index} out of range (1-{len(parent_items)})", "code": "NOT_FOUND"})
                return
            items_to_trash = [parent_items[idx]]
        else:
            items_to_trash = [parent_items[0]]
    else:
        _output({"status": "error", "action": "trash", "message": "Provide --key or a search query", "code": "ZOTERO_API_ERROR"})
        sys.exit(1)

    title = items_to_trash[0]["data"].get("title", "Unknown")

    if args.dry_run:
        _output({"status": "dry_run", "action": "trash", "title": title, "key": items_to_trash[0]["key"],
                  "message": f"Would move to trash: {title}"})
        return

    for item in items_to_trash:
        client.trash_item(item)

    _output({"status": "ok", "action": "trash", "title": title, "key": items_to_trash[0]["key"],
              "message": f"Moved to trash: {title}"})


def cmd_remove_from_collection(args):
    config = load_config(require=["ZOTERO_API_KEY"])
    client = ZoteroClient(config)

    # Resolve collection name to key
    all_colls = client.collections()
    coll_map = {c["data"]["name"].lower(): c["key"] for c in all_colls}
    key_to_name = {c["key"]: c["data"]["name"] for c in all_colls}

    coll_key = coll_map.get(args.collection.lower())
    if not coll_key:
        # Try as a key directly
        if args.collection in key_to_name:
            coll_key = args.collection
        else:
            _output({"status": "error", "action": "remove_from_collection",
                      "message": f"Collection '{args.collection}' not found", "code": "NOT_FOUND"})
            sys.exit(1)

    coll_name = key_to_name.get(coll_key, args.collection)

    # Find item
    try:
        item = client.get_item(args.key)
    except Exception as e:
        _output({"status": "error", "action": "remove_from_collection", "message": f"Item not found: {e}", "code": "ZOTERO_API_ERROR"})
        sys.exit(1)

    title = item["data"].get("title", "Unknown")

    if coll_key not in item["data"].get("collections", []):
        _output({"status": "error", "action": "remove_from_collection",
                  "message": f"Item '{title}' is not in collection '{coll_name}'", "code": "NOT_FOUND"})
        return

    if args.dry_run:
        _output({"status": "dry_run", "action": "remove_from_collection", "title": title,
                  "collection": coll_name, "message": f"Would remove '{title}' from '{coll_name}'"})
        return

    client.remove_from_collection(coll_key, item)
    _output({"status": "ok", "action": "remove_from_collection", "title": title, "key": args.key,
              "collection": coll_name, "message": f"Removed '{title}' from '{coll_name}'"})


def cmd_list_trash(args):
    config = load_config(require=["ZOTERO_API_KEY"])
    client = ZoteroClient(config)
    items = client.list_trash(limit=args.limit)

    if not items:
        _output({"status": "ok", "action": "list_trash", "count": 0, "results": [], "message": "Trash is empty"})
        return

    results = []
    for item in items:
        d = item["data"]
        if d.get("itemType") in ("note", "attachment"):
            continue
        authors = [c.get("lastName", c.get("name", "")) for c in d.get("creators", []) if c.get("creatorType") == "author"]
        results.append({"key": item["key"], "title": d.get("title", ""), "authors": authors[:3],
                         "year": d.get("date", ""), "type": d.get("itemType", "")})

    if args.json:
        _output({"status": "ok", "action": "list_trash", "count": len(results), "results": results})
    else:
        for i, r in enumerate(results, 1):
            authors_str = "; ".join(r["authors"][:3])
            print(f"{i}. {r['title']}")
            print(f"   {authors_str} ({r['year']})")
            print()


def _collect_attachment_keys(client, items):
    """Find all PDF attachment keys for a list of parent items."""
    att_keys = []
    for item in items:
        item_type = item["data"].get("itemType", "")
        if item_type == "attachment":
            if item["data"].get("contentType") == "application/pdf":
                att_keys.append(item["key"])
        else:
            try:
                children = client.children(item["key"])
                for child in children:
                    if (child["data"].get("itemType") == "attachment"
                            and child["data"].get("contentType") == "application/pdf"):
                        att_keys.append(child["key"])
            except Exception:
                pass
    return att_keys


def _cleanup_webdav(config, att_keys):
    """Delete attachment zips from WebDAV. Returns (deleted, failed) counts."""
    if not config.get("webdav_url") or not config.get("WEBDAV_PASSWORD"):
        return 0, 0
    from lib.webdav import WebDAVClient
    webdav = WebDAVClient(config)
    deleted = 0
    failed = 0
    for key in att_keys:
        try:
            if webdav.delete(key):
                deleted += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    return deleted, failed


def cmd_empty_trash(args):
    config = load_config(require=["ZOTERO_API_KEY"])
    client = ZoteroClient(config)

    trashed = client.list_trash(limit=500)
    count = len(trashed)

    if count == 0:
        _output({"status": "ok", "action": "empty_trash", "deleted": 0, "message": "Trash is already empty"})
        return

    # Find attachment keys for WebDAV cleanup
    att_keys = _collect_attachment_keys(client, trashed)

    if args.dry_run:
        _output({"status": "dry_run", "action": "empty_trash", "count": count,
                  "webdav_files": len(att_keys),
                  "message": f"Would permanently delete {count} item(s) from trash and {len(att_keys)} file(s) from WebDAV"})
        return

    # Delete WebDAV files first (before Zotero items, so we still have the keys)
    webdav_deleted, webdav_failed = _cleanup_webdav(config, att_keys)

    # Then permanently delete from Zotero
    client.empty_trash()

    msg = f"Permanently deleted {count} item(s) from trash"
    if webdav_deleted:
        msg += f", removed {webdav_deleted} file(s) from WebDAV"
    if webdav_failed:
        msg += f" ({webdav_failed} WebDAV deletion(s) failed)"

    _output({"status": "ok", "action": "empty_trash", "deleted": count,
              "webdav_deleted": webdav_deleted, "webdav_failed": webdav_failed,
              "message": msg})


def cmd_notes(args):
    """List child notes for a parent item (resolved via search query)."""
    config = load_config(require=["ZOTERO_API_KEY"])
    client = ZoteroClient(config)

    # Resolve parent item via search
    items = client.search(args.query, limit=25)
    parent_items = [i for i in items if i["data"].get("itemType") not in ("note", "attachment")]

    if not parent_items:
        _output({"status": "error", "action": "notes", "message": "No matching items found",
                  "code": "NOT_FOUND"})
        return

    # Use first match
    parent = parent_items[0]
    parent_key = parent["key"]
    parent_title = parent["data"].get("title", "Unknown")

    # Get children, filter to notes
    children = client.children(parent_key)
    notes = [c for c in children if c["data"].get("itemType") == "note"]

    # Optionally filter by tag
    if args.tag:
        notes = [
            n for n in notes
            if args.tag in [t["tag"] for t in n["data"].get("tags", [])]
        ]

    results = []
    for note in notes:
        d = note["data"]
        results.append({
            "key": note["key"],
            "parent_key": parent_key,
            "parent_title": parent_title,
            "note_preview": d.get("note", "")[:200],
            "tags": [t["tag"] for t in d.get("tags", [])],
            "date_modified": d.get("dateModified", ""),
        })

    if args.json:
        _output({
            "status": "ok", "action": "notes",
            "parent_key": parent_key, "parent_title": parent_title,
            "count": len(results), "notes": results,
        })
    else:
        if not results:
            print(f"No notes found for '{parent_title}'" +
                  (f" with tag '{args.tag}'" if args.tag else "") + ".")
            return
        print(f"Notes for: {parent_title} (key: {parent_key})")
        print(f"  {len(results)} note(s)" + (f" with tag '{args.tag}'" if args.tag else "") + "\n")
        for i, r in enumerate(results, 1):
            tags_str = ", ".join(r["tags"]) if r["tags"] else "(no tags)"
            preview = r["note_preview"].replace("\n", " ").strip()[:100]
            print(f"{i}. [{r['key']}] {tags_str}")
            if preview:
                print(f"   {preview}...")
            print()


def cmd_clean_staging(args):
    config = load_config()
    staging = config["staging_dir"]
    if not os.path.exists(staging):
        print("Staging directory does not exist.")
        return
    import time as _time
    now = _time.time()
    removed = 0
    for f in os.listdir(staging):
        fp = os.path.join(staging, f)
        if os.path.isfile(fp) and (now - os.path.getmtime(fp)) > 86400:
            os.remove(fp)
            removed += 1
    print(f"Removed {removed} orphaned file(s) older than 24h.")


def _build_collection_tree(collections):
    """Build a nested tree from flat collection list."""
    by_key = {}
    for c in collections:
        d = c["data"]
        by_key[c["key"]] = {
            "key": c["key"],
            "name": d["name"],
            "numItems": d.get("numItems", 0),
            "parentCollection": d.get("parentCollection", False),
            "children": [],
        }

    roots = []
    for key, node in by_key.items():
        parent = node["parentCollection"]
        if parent and parent in by_key:
            by_key[parent]["children"].append(node)
        else:
            roots.append(node)

    return sorted(roots, key=lambda x: x["name"])


def _print_collection_tree(nodes, indent=0):
    for node in sorted(nodes, key=lambda x: x["name"]):
        prefix = "  " * indent
        print(f"{prefix}{'└─ ' if indent > 0 else ''}{node['name']} ({node['numItems']} items)")
        if node["children"]:
            _print_collection_tree(node["children"], indent + 1)


def _output(data):
    print(json.dumps(data, ensure_ascii=False))


def main():
    # Common args shared across all subcommands
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="Output JSON")
    common.add_argument("--dry-run", action="store_true", help="Preview without creating")

    parser = argparse.ArgumentParser(description="Headless Zotero CLI", parents=[common])
    sub = parser.add_subparsers(dest="command")

    # search
    p_search = sub.add_parser("search", help="Search Zotero library", parents=[common])
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--limit", type=int, default=25)
    p_search.add_argument("--bibtex", action="store_true", help="Output BibTeX format")
    p_search.add_argument("--local-db", action="store_true",
                          help="Search discovered zotero.sqlite files read-only instead of the Zotero API")

    # add
    p_add = sub.add_parser("add", help="Add paper to Zotero", parents=[common])
    p_add.add_argument("identifier", nargs="?", help="DOI, arXiv ID, URL, or ISBN")
    p_add.add_argument("--collection", action="append", dest="collections", help="Collection (repeatable)")
    p_add.add_argument("--no-collection", action="store_true")
    p_add.add_argument("--no-pdf", "--metadata-only", action="store_true", dest="no_pdf")
    p_add.add_argument("--force", action="store_true")
    p_add.add_argument("--accept-short", action="store_true")
    p_add.add_argument("--file", dest="batch_file", help="File with identifiers (one per line)")
    p_add.add_argument("--from-manifest", dest="manifest")
    p_add.add_argument("--delay", type=float, default=2.0)
    p_add.add_argument("--parallel", type=int, default=0, help="Parallel batch adds (0=auto, 1=sequential)")

    # add-file (also aliased as add-pdf)
    for cmd_name in ("add-file", "add-pdf"):
        p_af = sub.add_parser(cmd_name,
                              help="Add local file (PDF, EPUB, etc.) to Zotero" if cmd_name == "add-file"
                              else argparse.SUPPRESS,
                              parents=[common])
        p_af.add_argument("file_path", help="Path to local file")
        p_af.add_argument("--identifier", help="DOI, arXiv ID, URL, or ISBN for metadata lookup")
        p_af.add_argument("--collection", action="append", dest="collections", help="Collection (repeatable)")
        p_af.add_argument("--no-collection", action="store_true")
        p_af.add_argument("--force", action="store_true", help="Add even if duplicate DOI found")
        p_af.add_argument("--accept-short", action="store_true", help="Accept 1-2 page PDFs")
        p_af.add_argument("--no-doi-extract", action="store_true", help="Skip automatic DOI extraction from PDF content")

    # get
    p_get = sub.add_parser("get", help="Retrieve paper from library", parents=[common])
    p_get.add_argument("query", help="Search query")
    p_get.add_argument("--link", action="store_true", help="Get Google Drive share link")
    p_get.add_argument("--index", type=int, help="Select from multiple results")
    p_get.add_argument("--no-local-storage", action="store_false", dest="local_storage",
                       help="Skip local Zotero storage lookup and download from WebDAV")
    p_get.add_argument("--send", nargs=2, metavar=("CHANNEL", "TARGET"),
                        help="Send file after download: --send telegram <chat_id>")
    p_get.set_defaults(local_storage=True)

    # update
    p_update = sub.add_parser("update", help="Update existing item", parents=[common])
    p_update.add_argument("key", help="Zotero item key")
    p_update.add_argument("--attach-pdf", action="store_true", help="Download and attach PDF")
    p_update.add_argument("--attach-file", dest="local_file", help="Attach a local file (any type)")
    p_update.add_argument("--local", dest="local_pdf", help="Path to local PDF (use with --attach-pdf)")
    p_update.add_argument("--item-type", dest="item_type", help="Override itemType (e.g., manuscript)")
    p_update.add_argument("--add-collection", action="append", dest="add_collections")
    p_update.add_argument("--remove-collection", action="append", dest="remove_collections")

    # doctor
    sub.add_parser("doctor", help="Health check all components", parents=[common])

    # list-collections
    p_lc = sub.add_parser("list-collections", help="List Zotero collections", parents=[common])
    p_lc.add_argument("--tree", action="store_true")

    # create-collection
    p_cc = sub.add_parser("create-collection", help="Create a new collection", parents=[common])
    p_cc.add_argument("name", help="Collection name")
    p_cc.add_argument("--parent", help="Parent collection name")

    # trash
    p_trash = sub.add_parser("trash", help="Move an item to trash", parents=[common])
    p_trash.add_argument("query", nargs="?", help="Search query to find the item")
    p_trash.add_argument("--key", help="Item key (direct)")
    p_trash.add_argument("--index", type=int, help="Select from multiple results")

    # remove-from-collection
    p_rfc = sub.add_parser("remove-from-collection", help="Remove an item from a collection (item stays in library)", parents=[common])
    p_rfc.add_argument("key", help="Item key")
    p_rfc.add_argument("--collection", required=True, help="Collection name or key")

    # list-trash
    p_lt = sub.add_parser("list-trash", help="List items in the trash", parents=[common])
    p_lt.add_argument("--limit", type=int, default=50)

    # empty-trash
    sub.add_parser("empty-trash", help="Permanently delete all items in the trash", parents=[common])

    # clean-staging
    sub.add_parser("clean-staging", help="Remove orphaned staging files", parents=[common])

    # sync-cache
    sub.add_parser("sync-cache", help="Pull full library to local cache", parents=[common])

    # notes
    p_notes = sub.add_parser("notes", help="List child notes for a Zotero item", parents=[common])
    p_notes.add_argument("query", help="Search query to identify parent item")
    p_notes.add_argument("--tag", metavar="TAG", default=None, help="Filter notes by tag")

    args = parser.parse_args()
    argv = sys.argv[1:]
    if "--dry-run" in argv:
        args.dry_run = True
    if "--json" in argv:
        args.json = True

    if not args.command:
        parser.print_help()
        sys.exit(1)

    dispatch = {
        "search": cmd_search,
        "add": cmd_add,
        "add-file": cmd_add_file,
        "add-pdf": cmd_add_file,
        "get": cmd_get,
        "update": cmd_update,
        "doctor": cmd_doctor,
        "list-collections": cmd_list_collections,
        "create-collection": cmd_create_collection,
        "trash": cmd_trash,
        "remove-from-collection": cmd_remove_from_collection,
        "list-trash": cmd_list_trash,
        "empty-trash": cmd_empty_trash,
        "clean-staging": cmd_clean_staging,
        "sync-cache": cmd_sync_cache,
        "notes": cmd_notes,
    }

    handler = dispatch.get(args.command)
    if handler:
        handler(args)
    else:
        _output({"status": "error", "message": f"Unknown command: {args.command}", "code": "NOT_IMPLEMENTED"})
        sys.exit(1)


if __name__ == "__main__":
    main()
