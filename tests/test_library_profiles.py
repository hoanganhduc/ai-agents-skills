from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.library_profiles import audit_library_profiles


class LibraryProfileAuditTests(unittest.TestCase):
    def test_missing_local_databases_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = audit_library_profiles(root, platform="linux", system_profile="linux-local")
            self.assertEqual(result["zotero"]["status"], "local-db-missing")
            self.assertEqual(result["calibre"]["status"], "local-db-missing")

    def test_malformed_zotero_db_is_not_mutation_capable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "Zotero" / "zotero.sqlite"
            db.parent.mkdir(parents=True)
            db.write_bytes(b"not a sqlite database")

            result = audit_library_profiles(root, platform="linux", system_profile="linux-local")
            candidate = result["zotero"]["candidates"][0]

            self.assertEqual(candidate["sqlite"]["status"], "malformed")
            self.assertNotIn("mutate", candidate["allowed_operations"])
            self.assertFalse(candidate["authoritative"])

    def test_calibre_cache_candidate_is_read_only_even_when_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / ".codex" / "runtime" / "workspace" / "data" / "calibre" / "cache" / "metadata.db"
            db.parent.mkdir(parents=True)
            with sqlite3.connect(db) as conn:
                conn.execute("create table books (id integer primary key, path text)")
                conn.execute("insert into books (id, path) values (1, 'Author/Book (1)')")

            result = audit_library_profiles(root, platform="linux", system_profile="linux-local")
            candidate = result["calibre"]["candidates"][0]

            self.assertIn("runtime-cache", candidate["classification"])
            self.assertEqual(candidate["sqlite"]["status"], "ok")
            self.assertNotIn("mutate", candidate["allowed_operations"])
            self.assertFalse(candidate["authoritative"])

    def test_windows_mounted_profile_records_posix_dialect_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mounted_root = Path(tmp) / "windows" / "Users" / "alice"
            db = mounted_root / "Calibre Library" / "metadata.db"
            book_dir = mounted_root / "Calibre Library" / "Author" / "Book (1)"
            book_dir.mkdir(parents=True)
            with sqlite3.connect(db) as conn:
                conn.execute("create table books (id integer primary key, path text)")
                conn.execute("insert into books (id, path) values (1, 'Author/Book (1)')")

            result = audit_library_profiles(
                mounted_root,
                platform="linux",
                system_profile="windows-mounted",
            )
            candidate = result["calibre"]["candidates"][0]

            self.assertEqual(result["path_dialect"], "posix")
            self.assertIn("mounted-windows", candidate["classification"])
            self.assertEqual(candidate["file_tree"]["status"], "ok")
            self.assertNotIn("mutate", candidate["allowed_operations"])


if __name__ == "__main__":
    unittest.main()
