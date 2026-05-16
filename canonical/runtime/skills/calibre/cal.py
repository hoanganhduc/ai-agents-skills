#!/usr/bin/env python3
"""Calibre library manager CLI — reads/writes a Calibre library on Google Drive.

Commands:
  search      Search books by title / author / tag / series / ISBN
  add         Add a book file (EPUB/PDF/MOBI) or look up by ISBN
  get         Retrieve a book file, optionally send to Telegram/Zulip
  update      Update book metadata (title, authors, tags, series, etc.)
  list-shelves  List tags, series, and publishers
  add-tag     Add a tag to a book
  remove-tag  Remove a tag from a book
  sync        Pull metadata.db from Drive and rebuild local cache
  remove      Remove a book from the library
  doctor      Health check for all components
  clean       Remove stale temp files from staging
  convert     Convert book format (requires ebook-convert)
  export      Export metadata as JSON or BibTeX

All commands output JSON to stdout. Progress/status logs go to stderr.
"""

import argparse
import json
import os
import sys
import shutil
import subprocess
import time

# Add skill dir to path for lib/ imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.config import load_config
from lib.cache import load_cache, save_cache, append_to_cache, remove_from_cache, search_cache
from lib.calibre_db import CalibreDB
from lib.drive_sync import DriveSync
from lib.renamer import make_filename, make_drive_folder_name, make_author_folder_name


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _err(msg, **extra):
    _out({"status": "error", "message": msg, **extra})
    sys.exit(1)


def _progress(msg):
    print(msg, file=sys.stderr, flush=True)


def _format_bytes(value):
    if value is None:
        return "unknown"
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "unknown"
    units = ["B", "KB", "MB", "GB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024


def _trigger_ingest(book_data):
    """Fire-and-forget: ingest Calibre book into memory. Does not block."""
    import subprocess
    script = os.path.join(
        os.environ.get("AAS_RUNTIME_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE") or os.path.join(os.path.expanduser("~"), ".codex", "runtime", "workspace"),
        "scripts", "ingest_library.py",
    )
    if not os.path.exists(script):
        return
    subprocess.Popen(
        [sys.executable, script, "--source", "calibre", "--data", json.dumps(book_data)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
    )


def _ensure_db(config, sync=False):
    """Ensure local metadata.db exists, pulling from Drive if needed."""
    db_path = config["db_local_path"]
    if not os.path.exists(db_path) or sync:
        ds = DriveSync(config)
        with ds.db_lock():
            downloaded = ds.pull_db(force=sync)
        if not os.path.exists(db_path):
            _err("metadata.db not available locally. Run 'cal sync' or check Drive credentials.")
    return db_path


def _lookup_isbn(isbn, config):
    """Fetch book metadata from Open Library by ISBN.

    Returns a partial book dict or None.
    """
    import requests
    url = config.get("isbn_lookup_url", "https://openlibrary.org/api/books")
    try:
        resp = requests.get(
            url,
            params={"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
            timeout=10,
        )
        data = resp.json()
        key = f"ISBN:{isbn}"
        if key not in data:
            return None
        book = data[key]
        authors = [a["name"] for a in book.get("authors", [])]
        year = None
        pub_date = book.get("publish_date", "")
        if pub_date:
            import re
            m = re.search(r"\b(\d{4})\b", pub_date)
            if m:
                year = int(m.group(1))
        publishers = [p["name"] for p in book.get("publishers", [])]
        subjects = [s["name"] for s in book.get("subjects", [])][:10]
        return {
            "title": book.get("title", ""),
            "authors": authors,
            "year": year,
            "publisher": publishers[0] if publishers else None,
            "tags": subjects,
            "identifiers": {"isbn": isbn},
            "description": book.get("notes", {}).get("value", "") if isinstance(book.get("notes"), dict) else "",
        }
    except Exception as e:
        return None


def _extract_epub_metadata(file_path):
    """Extract title/authors/ISBN from an EPUB file.

    Tries ebooklib first, falls back to zipfile + OPF parsing.
    """
    try:
        import ebooklib
        from ebooklib import epub
        book = epub.read_epub(file_path)
        title = book.get_metadata("DC", "title")
        title = title[0][0] if title else os.path.splitext(os.path.basename(file_path))[0]
        creators = book.get_metadata("DC", "creator")
        authors = [c[0] for c in creators] if creators else []
        identifiers = {}
        for ident in book.get_metadata("DC", "identifier"):
            val = ident[0]
            attrs = ident[1]
            scheme = attrs.get("opf:scheme", attrs.get("id", "")).lower()
            if "isbn" in scheme or (val.replace("-", "").isdigit() and len(val.replace("-", "")) in (10, 13)):
                identifiers["isbn"] = val.replace("-", "")
            elif "doi" in scheme:
                identifiers["doi"] = val
        dates = book.get_metadata("DC", "date")
        year = None
        if dates:
            import re
            m = re.search(r"\b(\d{4})\b", dates[0][0])
            if m:
                year = int(m.group(1))
        return {"title": title, "authors": authors, "year": year, "identifiers": identifiers}
    except ImportError:
        pass
    except Exception:
        pass

    # Fallback: parse OPF directly from zip
    try:
        import zipfile, re
        from xml.etree import ElementTree as ET
        with zipfile.ZipFile(file_path, "r") as z:
            # Find content.opf
            opf_path = None
            if "META-INF/container.xml" in z.namelist():
                container = z.read("META-INF/container.xml").decode("utf-8", errors="ignore")
                m = re.search(r'full-path="([^"]+\.opf)"', container)
                if m:
                    opf_path = m.group(1)
            if not opf_path:
                opf_path = next((n for n in z.namelist() if n.endswith(".opf")), None)
            if not opf_path:
                return {}
            opf = z.read(opf_path).decode("utf-8", errors="ignore")
        ns = {
            "dc": "http://purl.org/dc/elements/1.1/",
            "opf": "http://www.idpf.org/2007/opf",
        }
        root = ET.fromstring(opf)
        title_el = root.find(".//dc:title", ns)
        title = title_el.text if title_el is not None else ""
        creators = root.findall(".//dc:creator", ns)
        authors = [c.text for c in creators if c.text]
        date_el = root.find(".//dc:date", ns)
        year = None
        if date_el is not None and date_el.text:
            m = re.search(r"\b(\d{4})\b", date_el.text)
            if m:
                year = int(m.group(1))
        return {"title": title, "authors": authors, "year": year, "identifiers": {}}
    except Exception:
        return {}


def _extract_pdf_metadata(file_path):
    """Extract title/author from a PDF file."""
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(file_path)
        info = reader.metadata or {}
        title = info.get("/Title", "") or ""
        author = info.get("/Author", "") or ""
        authors = [a.strip() for a in author.split(",")] if author else []
        return {"title": title, "authors": authors, "year": None, "identifiers": {}}
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_search(args):
    config = load_config()
    items, age_hours = load_cache(config["cache_path"])

    # Auto-refresh if stale and Drive creds available
    if not items or (age_hours and age_hours > config["cache_max_age_hours"]):
        try:
            _ensure_db(config, sync=True)
            with CalibreDB(config["db_local_path"]) as db:
                items = db.search("", limit=99999)
            save_cache(config["cache_path"], items)
            age_hours = 0
        except Exception:
            pass  # Use stale cache if refresh fails

    source = "cache" if age_hours else "live"
    results = search_cache(
        items, args.query or "",
        tag=args.tag, series=args.series,
        fmt=args.format, limit=args.limit,
    )
    _out({"results": results, "count": len(results), "source": source,
          "cache_age_hours": round(age_hours, 1) if age_hours else None})


def cmd_add(args):
    config = load_config(require=["gdrive_folder_id"])
    ds = DriveSync(config)

    # Determine metadata
    meta = {}

    if args.isbn:
        fetched = _lookup_isbn(args.isbn, config)
        if fetched:
            meta.update(fetched)
        else:
            meta["identifiers"] = {"isbn": args.isbn}

    if args.file:
        if not os.path.exists(args.file):
            _err(f"File not found: {args.file}")
        fmt = os.path.splitext(args.file)[1].lower().lstrip(".")
        if fmt == "epub":
            file_meta = _extract_epub_metadata(args.file)
        elif fmt == "pdf":
            file_meta = _extract_pdf_metadata(args.file)
        else:
            file_meta = {}
        # File metadata fills gaps (doesn't override CLI/ISBN data)
        for k, v in file_meta.items():
            if v and not meta.get(k):
                meta[k] = v

    # CLI overrides take highest priority
    if args.title:
        meta["title"] = args.title
    if args.author:
        meta["authors"] = [a.strip() for a in args.author.split(";")]
    if args.year:
        meta["year"] = int(args.year)
    if args.publisher:
        meta["publisher"] = args.publisher
    if args.tag:
        meta.setdefault("tags", [])
        meta["tags"] = list(set(meta["tags"] + [t.strip() for t in args.tag.split(",")]))
    if args.series:
        meta["series"] = args.series
    if args.series_index:
        meta["series_index"] = float(args.series_index)

    if not meta.get("title"):
        if args.file:
            meta["title"] = os.path.splitext(os.path.basename(args.file))[0]
        else:
            _err("Cannot determine title. Provide --title or --isbn or a file.")

    if args.dry_run:
        _out({"status": "dry_run", "would_add": meta})
        return

    # Ensure local DB
    _ensure_db(config)

    with ds.db_lock():
        file_name = None
        file_size = 0
        if args.file:
            fmt = os.path.splitext(args.file)[1].lower().lstrip(".")
            file_name = make_filename(meta["title"], meta.get("authors"), meta.get("year"))
            file_size = os.path.getsize(args.file)

        with CalibreDB(config["db_local_path"]) as db:
            book_id = db.add_book(
                title=meta["title"],
                authors=meta.get("authors", ["Unknown"]),
                year=meta.get("year"),
                publisher=meta.get("publisher"),
                tags=meta.get("tags", []),
                series=meta.get("series"),
                series_index=meta.get("series_index", 1.0),
                identifiers=meta.get("identifiers", {}),
                description=meta.get("description"),
                fmt=fmt if args.file else None,
                file_name=file_name,
                file_size=file_size,
            )
            book = db.get_book(book_id)

        # Upload file to Drive
        drive_path = None
        if args.file:
            author_folder = make_author_folder_name(meta.get("authors", []))
            title_folder = make_drive_folder_name(meta["title"], meta.get("year"))
            book_path = f"{author_folder}/{title_folder}"

            ds.push_book(args.file, book_path, fmt, file_name)
            drive_path = f"{book_path}/{file_name}.{fmt}"

        # Push updated DB back to Drive
        ds.push_db()

    # Update cache
    append_to_cache(config["cache_path"], book)

    _out({
        "status": "ok",
        "id": book_id,
        "title": book["title"],
        "authors": book["authors"],
        "formats": book["formats"],
        "drive_path": drive_path,
    })
    _trigger_ingest(book)


def cmd_get(args):
    config = load_config(require=["gdrive_folder_id"])

    items, _ = load_cache(config["cache_path"])
    if not items:
        _ensure_db(config)
        with CalibreDB(config["db_local_path"]) as db:
            items = db.search("", limit=99999)
        save_cache(config["cache_path"], items)

    results = search_cache(items, args.query or "", limit=20)
    if not results:
        _err(f"No books found matching: {args.query}")

    # Handle multiple results
    if len(results) > 1 and args.index is None:
        _out({
            "status": "multiple",
            "message": "Multiple books found. Re-run with --index N.",
            "results": [
                {"index": i, "id": b["id"], "title": b["title"],
                 "authors": b["authors"], "year": b["year"], "formats": b["formats"]}
                for i, b in enumerate(results)
            ],
        })
        return

    book = results[args.index] if args.index is not None else results[0]
    book_id = book["id"]

    # Choose format
    fmt = args.format or config.get("preferred_format", "epub")
    if fmt not in book["formats"]:
        if book["formats"]:
            fmt = book["formats"][0]
        else:
            _err(f"Book {book_id} has no formats recorded.")

    # Download from Drive
    _ensure_db(config)
    with CalibreDB(config["db_local_path"]) as db:
        db_book = db.get_book(book_id)
        if not db_book:
            _err(f"Book id {book_id} not found in database.")
        file_name = db.get_book_format_name(book_id, fmt)

    if not file_name:
        _err(f"Format '{fmt}' not available for book {book_id}.")

    ds = DriveSync(config)
    local_path = ds.pull_book(db_book["path"], fmt, file_name)
    if not local_path:
        _err(f"Could not download book file from Drive. Path: {db_book['path']}/{file_name}.{fmt}")

    result = {
        "status": "ok",
        "id": book_id,
        "title": book["title"],
        "authors": book["authors"],
        "format": fmt,
        "local_path": local_path,
    }

    # Send to channel
    if args.send:
        send_script = os.path.join(os.path.dirname(__file__), "..", "zotero", "send_file.sh")
        if not os.path.exists(send_script):
            send_script = os.path.join(os.path.dirname(__file__), "send_file.sh")
        parts = args.send.split(":", 1)
        channel = parts[0]
        target = parts[1] if len(parts) > 1 else ""
        caption = f"{book['title']} — {', '.join(book['authors'])}"
        if os.path.exists(send_script):
            proc = subprocess.run(
                ["bash", send_script, channel, target, local_path, caption],
                capture_output=True, text=True
            )
            try:
                result["send_result"] = json.loads(proc.stdout)
            except Exception:
                result["send_result"] = {"raw": proc.stdout.strip()}
        else:
            result["send_result"] = {"status": "error", "message": "send_file.sh not found"}

    _out(result)
    _trigger_ingest(book)


def cmd_update(args):
    config = load_config(require=["gdrive_folder_id"])
    _ensure_db(config)

    fields = {}
    if args.title:
        fields["title"] = args.title
    if args.author:
        fields["authors"] = [a.strip() for a in args.author.split(";")]
    if args.year:
        fields["year"] = int(args.year)
    if args.publisher:
        fields["publisher"] = args.publisher
    if args.tags:
        fields["tags"] = [t.strip() for t in args.tags.split(",")]
    if args.series:
        fields["series"] = args.series
    if args.series_index is not None:
        fields["series_index"] = float(args.series_index)
    if args.isbn:
        fields["identifiers"] = {"isbn": args.isbn}
    if args.description:
        fields["description"] = args.description

    if not fields:
        _err("No update fields provided. Use --title, --author, --tags, etc.")

    ds = DriveSync(config)
    with ds.db_lock():
        with CalibreDB(config["db_local_path"]) as db:
            if not db.get_book(args.id):
                _err(f"Book id {args.id} not found.")
            db.update_metadata(args.id, **fields)
            book = db.get_book(args.id)
        ds.push_db()

    append_to_cache(config["cache_path"], book)
    _out({"status": "ok", "id": args.id, "updated_fields": list(fields.keys()), "book": book})


def cmd_add_tag(args):
    config = load_config(require=["gdrive_folder_id"])
    _ensure_db(config)

    ds = DriveSync(config)
    with ds.db_lock():
        with CalibreDB(config["db_local_path"]) as db:
            if not db.get_book(args.id):
                _err(f"Book id {args.id} not found.")
            db.add_tag(args.id, args.tag)
            book = db.get_book(args.id)
        ds.push_db()

    append_to_cache(config["cache_path"], book)
    _out({"status": "ok", "id": args.id, "tag": args.tag, "tags": book["tags"]})


def cmd_remove_tag(args):
    config = load_config(require=["gdrive_folder_id"])
    _ensure_db(config)

    ds = DriveSync(config)
    with ds.db_lock():
        with CalibreDB(config["db_local_path"]) as db:
            if not db.get_book(args.id):
                _err(f"Book id {args.id} not found.")
            db.remove_tag(args.id, args.tag)
            book = db.get_book(args.id)
        ds.push_db()

    append_to_cache(config["cache_path"], book)
    _out({"status": "ok", "id": args.id, "tag": args.tag, "tags": book["tags"]})


def cmd_list_shelves(args):
    config = load_config()
    _ensure_db(config)

    # Default: show all three unless specific flags given
    show_tags = args.tags or (not args.series and not args.publishers)
    show_series = args.series or (not args.tags and not args.publishers)
    show_publishers = args.publishers

    result = {}
    with CalibreDB(config["db_local_path"]) as db:
        if show_tags:
            result["tags"] = db.list_tags()
        if show_series:
            result["series"] = db.list_series()
        if show_publishers:
            result["publishers"] = db.list_publishers()

    _out(result)


def cmd_sync(args):
    config = load_config(require=["gdrive_folder_id"])
    ds = DriveSync(config)
    _progress("metadata.db: checking Google Drive")

    last_reported_bucket = {"value": None}

    def report_download(downloaded, total, progress_fraction, done):
        if total:
            percent = int(round((progress_fraction or 0) * 100))
            bucket = 100 if done else (percent // 10) * 10
            if bucket == last_reported_bucket["value"]:
                return
            last_reported_bucket["value"] = bucket
            _progress(
                "metadata.db: downloading "
                f"{percent}% ({_format_bytes(downloaded)} / {_format_bytes(total)})"
            )
            return
        _progress(f"metadata.db: downloaded {_format_bytes(downloaded)}")

    with ds.db_lock():
        downloaded = ds.pull_db(force=args.force, progress_callback=report_download)

    if not downloaded and not args.force:
        items, age_hours = load_cache(config["cache_path"])
        _progress("metadata.db: already up-to-date")
        _out({"status": "ok", "message": "Already up-to-date",
              "cached_books": len(items),
              "cache_age_hours": round(age_hours, 1) if age_hours else None})
        return

    _progress("metadata.db: rebuilding local cache")
    with CalibreDB(config["db_local_path"]) as db:
        items = db.search("", limit=999999)
        count = db.count_books()

    save_cache(config["cache_path"], items)
    _progress(f"metadata.db: rebuilt local cache for {count} books")
    _out({"status": "ok", "message": "Synced from Drive", "books": count})


def cmd_remove(args):
    config = load_config(require=["gdrive_folder_id"])
    _ensure_db(config)

    # Resolve book
    if args.id:
        with CalibreDB(config["db_local_path"]) as db:
            book = db.get_book(args.id)
        if not book:
            _err(f"Book id {args.id} not found.")
        book_list = [book]
    else:
        items, _ = load_cache(config["cache_path"])
        book_list = search_cache(items, args.query or "", limit=20)
        if not book_list:
            _err(f"No books found matching: {args.query}")
        if len(book_list) > 1 and args.index is None:
            _out({
                "status": "multiple",
                "message": "Multiple books found. Re-run with --index N.",
                "results": [
                    {"index": i, "id": b["id"], "title": b["title"], "authors": b["authors"]}
                    for i, b in enumerate(book_list)
                ],
            })
            return
        book = book_list[args.index] if args.index is not None else book_list[0]
        book_list = [book]

    book = book_list[0]

    if args.dry_run:
        _out({"status": "dry_run", "would_remove": {
            "id": book["id"], "title": book["title"],
            "authors": book["authors"], "formats": book["formats"],
        }})
        return

    ds = DriveSync(config)
    with ds.db_lock():
        with CalibreDB(config["db_local_path"]) as db:
            book_path = db.remove_book(book["id"])

        if book_path:
            ds.delete_book_files(book_path)

        ds.push_db()

    remove_from_cache(config["cache_path"], book["id"])
    _out({"status": "ok", "removed": {
        "id": book["id"], "title": book["title"], "authors": book["authors"],
    }})


def cmd_doctor(args):
    config = load_config()
    from lib.doctor import run_checks
    results = run_checks(config)
    all_ok = all(r["ok"] for r in results)
    _out({"status": "ok" if all_ok else "issues_found", "checks": results})


def cmd_clean(args):
    config = load_config()
    staging = config["staging_dir"]
    if not os.path.exists(staging):
        _out({"status": "ok", "removed": 0, "message": "Staging dir does not exist"})
        return

    cutoff = time.time() - 24 * 3600
    removed = []
    for fname in os.listdir(staging):
        fpath = os.path.join(staging, fname)
        if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
            os.remove(fpath)
            removed.append(fname)

    _out({"status": "ok", "removed": len(removed), "files": removed})


def cmd_convert(args):
    config = load_config(require=["gdrive_folder_id"])
    ebook_convert = shutil.which("ebook-convert")
    if not ebook_convert:
        _err("ebook-convert not found. Install Calibre to enable format conversion.")

    _ensure_db(config)

    with CalibreDB(config["db_local_path"]) as db:
        book = db.get_book(args.id)
        if not book:
            _err(f"Book id {args.id} not found.")

        # Find a source format
        src_fmt = args.from_format
        if not src_fmt:
            # Prefer: epub > pdf > first available
            for f in ("epub", "pdf"):
                if f in book["formats"]:
                    src_fmt = f
                    break
            if not src_fmt:
                src_fmt = book["formats"][0] if book["formats"] else None
        if not src_fmt:
            _err(f"Book {args.id} has no available formats.")

        file_name = db.get_book_format_name(args.id, src_fmt)

    ds = DriveSync(config)
    src_path = ds.pull_book(book["path"], src_fmt, file_name)
    if not src_path:
        _err(f"Could not download source file from Drive ({src_fmt}).")

    dst_fmt = args.to_format.lower()
    dst_name = make_filename(book["title"], book["authors"], book["year"])
    dst_path = os.path.join(config["staging_dir"], f"{dst_name}.{dst_fmt}")

    proc = subprocess.run(
        [ebook_convert, src_path, dst_path],
        capture_output=True, text=True
    )
    if proc.returncode != 0:
        _err(f"ebook-convert failed: {proc.stderr[:500]}")

    # Add the new format to DB and Drive
    file_size = os.path.getsize(dst_path)
    ds2 = DriveSync(config)
    with ds2.db_lock():
        with CalibreDB(config["db_local_path"]) as db:
            db.add_format(args.id, dst_fmt, dst_name, file_size)
            book = db.get_book(args.id)
        ds2.push_book(dst_path, book["path"], dst_fmt, dst_name)
        ds2.push_db()

    append_to_cache(config["cache_path"], book)
    _out({
        "status": "ok",
        "id": args.id,
        "converted_from": src_fmt,
        "converted_to": dst_fmt,
        "local_path": dst_path,
        "formats": book["formats"],
    })


def cmd_export(args):
    config = load_config()
    _ensure_db(config)

    with CalibreDB(config["db_local_path"]) as db:
        book = db.get_book(args.id)
    if not book:
        _err(f"Book id {args.id} not found.")

    fmt = (args.format or "json").lower()

    if fmt == "json":
        _out(book)
    elif fmt == "bibtex":
        print(_to_bibtex(book))
    else:
        _err(f"Unsupported export format: {fmt}. Use 'json' or 'bibtex'.")


def _to_bibtex(book):
    """Convert a book dict to a BibTeX @book entry."""
    import re
    key = re.sub(r"\W+", "", (book["authors"][0].split()[-1] if book["authors"] else "Unknown"))
    key += str(book["year"] or "")
    title_word = re.sub(r"\W+", "", book["title"].split()[0]) if book["title"] else "Book"
    key += title_word

    authors_bibtex = " and ".join(book["authors"]) if book["authors"] else "Unknown"
    isbn = book["identifiers"].get("isbn", "")

    lines = [f"@book{{{key},"]
    lines.append(f"  title     = {{{book['title']}}},")
    lines.append(f"  author    = {{{authors_bibtex}}},")
    if book["year"]:
        lines.append(f"  year      = {{{book['year']}}},")
    if book["publisher"]:
        lines.append(f"  publisher = {{{book['publisher']}}},")
    if book["series"]:
        lines.append(f"  series    = {{{book['series']}}},")
    if isbn:
        lines.append(f"  isbn      = {{{isbn}}},")
    lines.append("}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Argument parser
# --------------------------------------------------------------------------- #

def build_parser():
    p = argparse.ArgumentParser(
        prog="cal",
        description="Calibre library manager — reads/writes a Calibre library on Google Drive.",
    )
    sub = p.add_subparsers(dest="command")

    # search
    ps = sub.add_parser("search", help="Search books")
    ps.add_argument("query", nargs="?", default="", help="Search query")
    ps.add_argument("--limit", type=int, default=25)
    ps.add_argument("--tag", help="Filter by tag")
    ps.add_argument("--series", help="Filter by series name")
    ps.add_argument("--format", help="Filter by format (epub/pdf/mobi)")

    # add
    pa = sub.add_parser("add", help="Add a book")
    pa.add_argument("file", nargs="?", help="Path to book file (EPUB/PDF/MOBI)")
    pa.add_argument("--isbn", help="ISBN for metadata lookup")
    pa.add_argument("--title", help="Override title")
    pa.add_argument("--author", help="Author(s), semicolon-separated")
    pa.add_argument("--year", help="Publication year")
    pa.add_argument("--publisher")
    pa.add_argument("--tag", help="Tags, comma-separated")
    pa.add_argument("--series")
    pa.add_argument("--series-index", dest="series_index", type=float)
    pa.add_argument("--dry-run", action="store_true")

    # get
    pg = sub.add_parser("get", help="Retrieve a book file")
    pg.add_argument("query", nargs="?", default="")
    pg.add_argument("--id", type=int, help="Book ID (skips search)")
    pg.add_argument("--index", type=int, help="Index when multiple results")
    pg.add_argument("--format", help="Preferred format")
    pg.add_argument("--send", help="Send to channel: telegram:CHAT_ID or zulip:stream:topic")

    # update
    pu = sub.add_parser("update", help="Update book metadata")
    pu.add_argument("--id", type=int, required=True, help="Book ID")
    pu.add_argument("--title")
    pu.add_argument("--author", help="Author(s), semicolon-separated")
    pu.add_argument("--year")
    pu.add_argument("--publisher")
    pu.add_argument("--tags", help="Replace all tags, comma-separated")
    pu.add_argument("--series")
    pu.add_argument("--series-index", dest="series_index", type=float)
    pu.add_argument("--isbn")
    pu.add_argument("--description")

    # list-shelves
    pl = sub.add_parser("list-shelves", help="List tags, series, publishers")
    pl.add_argument("--tags", action="store_true")
    pl.add_argument("--series", action="store_true")
    pl.add_argument("--publishers", action="store_true")

    # add-tag
    pat = sub.add_parser("add-tag", help="Add a tag to a book")
    pat.add_argument("--id", type=int, required=True)
    pat.add_argument("--tag", required=True)

    # remove-tag
    prt = sub.add_parser("remove-tag", help="Remove a tag from a book")
    prt.add_argument("--id", type=int, required=True)
    prt.add_argument("--tag", required=True)

    # sync
    psy = sub.add_parser("sync", help="Pull metadata.db from Drive")
    psy.add_argument("--force", action="store_true")

    # remove
    prm = sub.add_parser("remove", help="Remove a book")
    prm.add_argument("query", nargs="?", default="")
    prm.add_argument("--id", type=int)
    prm.add_argument("--index", type=int)
    prm.add_argument("--dry-run", action="store_true")

    # doctor
    sub.add_parser("doctor", help="Health check")

    # clean
    sub.add_parser("clean", help="Remove stale staging files")

    # convert
    pc = sub.add_parser("convert", help="Convert book format")
    pc.add_argument("--id", type=int, required=True)
    pc.add_argument("--to", dest="to_format", required=True, help="Target format (epub/pdf/mobi)")
    pc.add_argument("--from", dest="from_format", help="Source format (auto-detected if omitted)")

    # export
    pe = sub.add_parser("export", help="Export metadata")
    pe.add_argument("--id", type=int, required=True)
    pe.add_argument("--format", default="json", help="Output format: json or bibtex")

    return p


COMMANDS = {
    "search": cmd_search,
    "add": cmd_add,
    "get": cmd_get,
    "update": cmd_update,
    "list-shelves": cmd_list_shelves,
    "add-tag": cmd_add_tag,
    "remove-tag": cmd_remove_tag,
    "sync": cmd_sync,
    "remove": cmd_remove,
    "doctor": cmd_doctor,
    "clean": cmd_clean,
    "convert": cmd_convert,
    "export": cmd_export,
}

if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    fn = COMMANDS.get(args.command)
    if not fn:
        _err(f"Unknown command: {args.command}")
    fn(args)
