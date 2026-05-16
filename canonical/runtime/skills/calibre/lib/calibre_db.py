"""Direct SQLite interface for Calibre's metadata.db.

Supports: search, add, update, remove, tags, series, publishers, identifiers.
No calibredb binary required — reads/writes the SQLite schema directly.

Calibre schema notes:
  books(id, title, sort, timestamp, pubdate, series_index, author_sort, isbn,
        lccn, path, flags, uuid, has_cover, last_modified)
  authors(id, name, sort, link)
  tags(id, name)
  series(id, name, sort)
  publishers(id, name, sort)
  data(id, book, format, uncompressed_size, name)   ← book file records
  identifiers(id, book, type, val)
  comments(id, book, text)
  books_authors_link(id, book, author)
  books_tags_link(id, book, tag)
  books_series_link(id, book, series)
  books_publishers_link(id, book, publisher)
"""

import sqlite3
import os
import time
import uuid
import unicodedata
import re
from datetime import datetime, timezone
from contextlib import contextmanager


def _now_ts():
    """Return current UTC timestamp as Calibre float (Julian day or REAL)."""
    return datetime.now(timezone.utc).timestamp()


def _cal_date(year=None):
    """Format a year (int) as a Calibre pubdate REAL timestamp."""
    if not year:
        return None
    try:
        return datetime(int(year), 1, 1, tzinfo=timezone.utc).timestamp()
    except Exception:
        return None


def _ts_to_year(ts):
    """Convert Calibre REAL timestamp to year int."""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).year
    except Exception:
        return None


def _sort_name(name):
    """Convert 'First Last' to 'Last, First' sort form."""
    parts = name.strip().split()
    if len(parts) >= 2:
        return f"{parts[-1]}, {' '.join(parts[:-1])}"
    return name


class CalibreDB:
    """Context manager wrapping a Calibre metadata.db connection."""

    def __init__(self, db_path):
        self.db_path = db_path
        self._conn = None

    def __enter__(self):
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(
                f"metadata.db not found at {self.db_path}. Run 'cal sync' first."
            )
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._conn:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _book_row_to_dict(self, row, conn=None):
        """Convert a books table row + joins to a book dict."""
        c = conn or self._conn
        book_id = row["id"]

        # Authors
        authors = [
            r["name"]
            for r in c.execute(
                "SELECT a.name FROM authors a "
                "JOIN books_authors_link l ON a.id=l.author WHERE l.book=?",
                (book_id,),
            )
        ]

        # Tags
        tags = [
            r["name"]
            for r in c.execute(
                "SELECT t.name FROM tags t "
                "JOIN books_tags_link l ON t.id=l.tag WHERE l.book=?",
                (book_id,),
            )
        ]

        # Series
        series_row = c.execute(
            "SELECT s.name, l.id FROM series s "
            "JOIN books_series_link l ON s.id=l.series WHERE l.book=?",
            (book_id,),
        ).fetchone()
        series = series_row["name"] if series_row else None

        # Publisher
        pub_row = c.execute(
            "SELECT p.name FROM publishers p "
            "JOIN books_publishers_link l ON p.id=l.publisher WHERE l.book=?",
            (book_id,),
        ).fetchone()
        publisher = pub_row["name"] if pub_row else None

        # Formats (data table)
        formats = [
            r["format"].lower()
            for r in c.execute(
                "SELECT format FROM data WHERE book=?", (book_id,)
            )
        ]

        # Identifiers
        identifiers = {
            r["type"]: r["val"]
            for r in c.execute(
                "SELECT type, val FROM identifiers WHERE book=?", (book_id,)
            )
        }

        # Comments
        comment_row = c.execute(
            "SELECT text FROM comments WHERE book=?", (book_id,)
        ).fetchone()
        description = comment_row["text"] if comment_row else None

        return {
            "id": book_id,
            "title": row["title"],
            "authors": authors,
            "year": _ts_to_year(row["pubdate"]),
            "publisher": publisher,
            "tags": tags,
            "series": series,
            "series_index": row["series_index"] if series else None,
            "formats": formats,
            "identifiers": identifiers,
            "has_cover": bool(row["has_cover"]),
            "path": row["path"],
            "uuid": row["uuid"],
            "description": description,
        }

    def _get_or_create_author(self, name):
        row = self._conn.execute(
            "SELECT id FROM authors WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()
        if row:
            return row["id"]
        self._conn.execute(
            "INSERT INTO authors(name, sort, link) VALUES(?,?,'')",
            (name, _sort_name(name)),
        )
        return self._conn.execute(
            "SELECT id FROM authors WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()["id"]

    def _get_or_create_tag(self, name):
        row = self._conn.execute(
            "SELECT id FROM tags WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()
        if row:
            return row["id"]
        self._conn.execute("INSERT INTO tags(name) VALUES(?)", (name,))
        return self._conn.execute(
            "SELECT id FROM tags WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()["id"]

    def _get_or_create_series(self, name):
        row = self._conn.execute(
            "SELECT id FROM series WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()
        if row:
            return row["id"]
        self._conn.execute(
            "INSERT INTO series(name, sort) VALUES(?,?)", (name, name)
        )
        return self._conn.execute(
            "SELECT id FROM series WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()["id"]

    def _get_or_create_publisher(self, name):
        row = self._conn.execute(
            "SELECT id FROM publishers WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()
        if row:
            return row["id"]
        self._conn.execute(
            "INSERT INTO publishers(name, sort) VALUES(?,?)", (name, name)
        )
        return self._conn.execute(
            "SELECT id FROM publishers WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()["id"]

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def search(self, query, limit=25, tag=None, series=None, fmt=None):
        """Full-text search across title, authors, tags, series, identifiers.

        Returns list of book dicts.
        """
        words = query.lower().split() if query else []

        # Build base query joining authors
        rows = self._conn.execute(
            "SELECT DISTINCT b.id FROM books b "
            "LEFT JOIN books_authors_link l ON b.id=l.book "
            "LEFT JOIN authors a ON a.id=l.author "
            "LEFT JOIN books_tags_link tl ON b.id=tl.book "
            "LEFT JOIN tags t ON t.id=tl.tag "
            "LEFT JOIN books_series_link sl ON b.id=sl.book "
            "LEFT JOIN series s ON s.id=sl.series "
            "LEFT JOIN identifiers i ON b.id=i.book "
            "ORDER BY b.last_modified DESC"
        ).fetchall()

        results = []
        for row in rows:
            book_id = row["id"]
            book_row = self._conn.execute(
                "SELECT * FROM books WHERE id=?", (book_id,)
            ).fetchone()
            book = self._book_row_to_dict(book_row)

            # Apply tag filter
            if tag and tag.lower() not in [t.lower() for t in book["tags"]]:
                continue
            # Apply series filter
            if series and (not book["series"] or series.lower() not in book["series"].lower()):
                continue
            # Apply format filter
            if fmt and fmt.lower() not in book["formats"]:
                continue

            # Apply text search
            if words:
                haystack = " ".join([
                    book["title"],
                    " ".join(book["authors"]),
                    " ".join(book["tags"]),
                    book["series"] or "",
                    " ".join(book["identifiers"].values()),
                ]).lower()
                if not all(w in haystack for w in words):
                    continue

            results.append(book)
            if len(results) >= limit:
                break

        return results

    def get_book(self, book_id):
        """Fetch a single book by ID. Returns dict or None."""
        row = self._conn.execute(
            "SELECT * FROM books WHERE id=?", (book_id,)
        ).fetchone()
        if not row:
            return None
        return self._book_row_to_dict(row)

    def get_book_format_name(self, book_id, fmt):
        """Return the file name (without extension) for a given format."""
        row = self._conn.execute(
            "SELECT name FROM data WHERE book=? AND format=? COLLATE NOCASE",
            (book_id, fmt.upper()),
        ).fetchone()
        return row["name"] if row else None

    def add_book(self, title, authors, year=None, publisher=None, tags=None,
                 series=None, series_index=1.0, identifiers=None,
                 description=None, fmt=None, file_name=None, file_size=0):
        """Insert a new book record. Returns the new book ID.

        Args:
            title: book title string
            authors: list of author name strings
            year: publication year (int)
            publisher: publisher name string
            tags: list of tag strings
            series: series name string
            series_index: float position in series
            identifiers: dict like {"isbn": "...", "doi": "..."}
            description: HTML or plain text description/comments
            fmt: format string e.g. "epub", "pdf"
            file_name: base filename without extension (for data table)
            file_size: uncompressed file size in bytes
        """
        now = _now_ts()
        pubdate = _cal_date(year) or now
        author_sort = _sort_name(authors[0]) if authors else "Unknown"
        path = _build_path(authors[0] if authors else "Unknown", title, year)
        book_uuid = str(uuid.uuid4())

        # Insert into books
        self._conn.execute(
            "INSERT INTO books(title, sort, timestamp, pubdate, series_index, "
            "author_sort, isbn, path, has_cover, last_modified, uuid, flags) "
            "VALUES(?,?,?,?,?,?,?,?,0,?,?,1)",
            (
                title,
                title,
                now,
                pubdate,
                series_index,
                author_sort,
                identifiers.get("isbn", "") if identifiers else "",
                path,
                now,
                book_uuid,
            ),
        )
        book_id = self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Authors
        for name in (authors or ["Unknown"]):
            author_id = self._get_or_create_author(name)
            self._conn.execute(
                "INSERT OR IGNORE INTO books_authors_link(book, author) VALUES(?,?)",
                (book_id, author_id),
            )

        # Tags
        for tag in (tags or []):
            tag_id = self._get_or_create_tag(tag)
            self._conn.execute(
                "INSERT OR IGNORE INTO books_tags_link(book, tag) VALUES(?,?)",
                (book_id, tag_id),
            )

        # Series
        if series:
            series_id = self._get_or_create_series(series)
            self._conn.execute(
                "INSERT OR IGNORE INTO books_series_link(book, series) VALUES(?,?)",
                (book_id, series_id),
            )
            self._conn.execute(
                "UPDATE books SET series_index=? WHERE id=?",
                (series_index, book_id),
            )

        # Publisher
        if publisher:
            pub_id = self._get_or_create_publisher(publisher)
            self._conn.execute(
                "INSERT OR IGNORE INTO books_publishers_link(book, publisher) VALUES(?,?)",
                (book_id, pub_id),
            )

        # Identifiers
        for id_type, id_val in (identifiers or {}).items():
            self._conn.execute(
                "INSERT OR REPLACE INTO identifiers(book, type, val) VALUES(?,?,?)",
                (book_id, id_type, id_val),
            )

        # Comments / description
        if description:
            self._conn.execute(
                "INSERT INTO comments(book, text) VALUES(?,?)",
                (book_id, description),
            )

        # Data record (file format entry)
        if fmt and file_name:
            self._conn.execute(
                "INSERT OR REPLACE INTO data(book, format, uncompressed_size, name) "
                "VALUES(?,?,?,?)",
                (book_id, fmt.upper(), file_size, file_name),
            )

        return book_id

    def add_format(self, book_id, fmt, file_name, file_size=0):
        """Add or replace a format entry in the data table."""
        self._conn.execute(
            "INSERT OR REPLACE INTO data(book, format, uncompressed_size, name) "
            "VALUES(?,?,?,?)",
            (book_id, fmt.upper(), file_size, file_name),
        )
        self._conn.execute(
            "UPDATE books SET last_modified=? WHERE id=?",
            (_now_ts(), book_id),
        )

    def update_metadata(self, book_id, **fields):
        """Update metadata fields on an existing book.

        Supported fields: title, authors (list), year, publisher, tags (list),
        series, series_index, identifiers (dict), description.
        """
        now = _now_ts()

        if "title" in fields:
            self._conn.execute(
                "UPDATE books SET title=?, sort=?, last_modified=? WHERE id=?",
                (fields["title"], fields["title"], now, book_id),
            )

        if "year" in fields:
            pubdate = _cal_date(fields["year"])
            if pubdate:
                self._conn.execute(
                    "UPDATE books SET pubdate=? WHERE id=?", (pubdate, book_id)
                )

        if "authors" in fields:
            # Replace all authors
            self._conn.execute(
                "DELETE FROM books_authors_link WHERE book=?", (book_id,)
            )
            for name in fields["authors"]:
                author_id = self._get_or_create_author(name)
                self._conn.execute(
                    "INSERT OR IGNORE INTO books_authors_link(book, author) VALUES(?,?)",
                    (book_id, author_id),
                )
            if fields["authors"]:
                self._conn.execute(
                    "UPDATE books SET author_sort=? WHERE id=?",
                    (_sort_name(fields["authors"][0]), book_id),
                )

        if "tags" in fields:
            # Replace all tags
            self._conn.execute("DELETE FROM books_tags_link WHERE book=?", (book_id,))
            for tag in fields["tags"]:
                tag_id = self._get_or_create_tag(tag)
                self._conn.execute(
                    "INSERT OR IGNORE INTO books_tags_link(book, tag) VALUES(?,?)",
                    (book_id, tag_id),
                )

        if "series" in fields:
            self._conn.execute(
                "DELETE FROM books_series_link WHERE book=?", (book_id,)
            )
            if fields["series"]:
                series_id = self._get_or_create_series(fields["series"])
                self._conn.execute(
                    "INSERT OR IGNORE INTO books_series_link(book, series) VALUES(?,?)",
                    (book_id, series_id),
                )

        if "series_index" in fields:
            self._conn.execute(
                "UPDATE books SET series_index=? WHERE id=?",
                (fields["series_index"], book_id),
            )

        if "publisher" in fields:
            self._conn.execute(
                "DELETE FROM books_publishers_link WHERE book=?", (book_id,)
            )
            if fields["publisher"]:
                pub_id = self._get_or_create_publisher(fields["publisher"])
                self._conn.execute(
                    "INSERT OR IGNORE INTO books_publishers_link(book, publisher) VALUES(?,?)",
                    (book_id, pub_id),
                )

        if "identifiers" in fields:
            for id_type, id_val in fields["identifiers"].items():
                if id_val:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO identifiers(book, type, val) VALUES(?,?,?)",
                        (book_id, id_type, id_val),
                    )
                else:
                    self._conn.execute(
                        "DELETE FROM identifiers WHERE book=? AND type=?",
                        (book_id, id_type),
                    )

        if "description" in fields:
            self._conn.execute("DELETE FROM comments WHERE book=?", (book_id,))
            if fields["description"]:
                self._conn.execute(
                    "INSERT INTO comments(book, text) VALUES(?,?)",
                    (book_id, fields["description"]),
                )

        self._conn.execute(
            "UPDATE books SET last_modified=? WHERE id=?", (now, book_id)
        )

    def add_tag(self, book_id, tag):
        """Add a tag to a book. Creates tag if it doesn't exist."""
        tag_id = self._get_or_create_tag(tag)
        self._conn.execute(
            "INSERT OR IGNORE INTO books_tags_link(book, tag) VALUES(?,?)",
            (book_id, tag_id),
        )

    def remove_tag(self, book_id, tag):
        """Remove a tag from a book."""
        row = self._conn.execute(
            "SELECT id FROM tags WHERE name=? COLLATE NOCASE", (tag,)
        ).fetchone()
        if row:
            self._conn.execute(
                "DELETE FROM books_tags_link WHERE book=? AND tag=?",
                (book_id, row["id"]),
            )

    def remove_book(self, book_id):
        """Remove all records for a book. Returns the book path for Drive cleanup."""
        row = self._conn.execute(
            "SELECT path FROM books WHERE id=?", (book_id,)
        ).fetchone()
        path = row["path"] if row else None

        for table, col in [
            ("books_authors_link", "book"),
            ("books_tags_link", "book"),
            ("books_series_link", "book"),
            ("books_publishers_link", "book"),
            ("identifiers", "book"),
            ("comments", "book"),
            ("data", "book"),
        ]:
            self._conn.execute(f"DELETE FROM {table} WHERE {col}=?", (book_id,))
        self._conn.execute("DELETE FROM books WHERE id=?", (book_id,))
        return path

    def list_tags(self):
        """Return list of {name, count} dicts for all tags."""
        rows = self._conn.execute(
            "SELECT t.name, COUNT(l.book) as count FROM tags t "
            "LEFT JOIN books_tags_link l ON t.id=l.tag "
            "GROUP BY t.id ORDER BY count DESC, t.name ASC"
        ).fetchall()
        return [{"name": r["name"], "count": r["count"]} for r in rows]

    def list_series(self):
        """Return list of {name, count} dicts for all series."""
        rows = self._conn.execute(
            "SELECT s.name, COUNT(l.book) as count FROM series s "
            "LEFT JOIN books_series_link l ON s.id=l.series "
            "GROUP BY s.id ORDER BY s.name ASC"
        ).fetchall()
        return [{"name": r["name"], "count": r["count"]} for r in rows]

    def list_publishers(self):
        """Return list of {name, count} dicts for all publishers."""
        rows = self._conn.execute(
            "SELECT p.name, COUNT(l.book) as count FROM publishers p "
            "LEFT JOIN books_publishers_link l ON p.id=l.publisher "
            "GROUP BY p.id ORDER BY count DESC, p.name ASC"
        ).fetchall()
        return [{"name": r["name"], "count": r["count"]} for r in rows]

    def count_books(self):
        return self._conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]


def _build_path(author, title, year=None):
    """Build the Calibre-style relative path: Author/Title (Year)."""
    def sanitize(s):
        s = unicodedata.normalize("NFKD", str(s))
        s = s.encode("ascii", "ignore").decode()
        s = re.sub(r'[<>:"/\\|?*]', "_", s)
        return s.strip()[:80]

    author_part = sanitize(author.split(",")[0].strip() if "," in author else author)
    title_part = sanitize(title)
    if year:
        return f"{author_part}/{title_part} ({year})"
    return f"{author_part}/{title_part}"
