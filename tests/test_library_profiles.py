from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

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
            self.assertEqual(candidate["allowed_operations"], [])
            self.assertFalse(candidate["authoritative"])

    def test_env_overrides_are_checked_before_default_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "custom" / "zotero.sqlite"
            db.parent.mkdir(parents=True)
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("create table items (itemID integer primary key)")
                conn.commit()

            with patch.dict("os.environ", {"AAS_ZOTERO_DB": str(db)}, clear=False):
                result = audit_library_profiles(root, platform="linux", system_profile="linux-local")

            candidate = result["zotero"]["candidates"][0]
            self.assertEqual(Path(candidate["path"]), db)
            self.assertEqual(candidate["sqlite"]["status"], "ok")

    def test_wrong_sqlite_schema_is_not_read_or_mutation_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "Zotero" / "zotero.sqlite"
            db.parent.mkdir(parents=True)
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("create table unrelated (id integer primary key)")
                conn.commit()

            result = audit_library_profiles(root, platform="linux", system_profile="linux-local")
            candidate = result["zotero"]["candidates"][0]

            self.assertEqual(candidate["sqlite"]["status"], "ok")
            self.assertFalse(candidate["schema_valid"])
            self.assertFalse(candidate["readable"])
            self.assertEqual(candidate["allowed_operations"], [])

    def test_zotero_profiles_ini_candidates_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / ".zotero" / "zotero" / "abc.default"
            profile.mkdir(parents=True)
            db = profile / "zotero.sqlite"
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("create table items (itemID integer primary key)")
                conn.commit()
            (root / ".zotero" / "zotero" / "profiles.ini").write_text(
                "[Profile0]\nName=default\nIsRelative=1\nPath=abc.default\n",
                encoding="utf-8",
            )

            result = audit_library_profiles(root, platform="linux", system_profile="linux-local")

            self.assertTrue(any(Path(item["path"]) == db for item in result["zotero"]["candidates"]))

    def test_calibre_library_env_candidate_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "Books"
            library.mkdir()
            db = library / "metadata.db"
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("create table books (id integer primary key, path text)")
                conn.commit()

            with patch.dict("os.environ", {"CALIBRE_LIBRARY": str(library)}, clear=False):
                result = audit_library_profiles(root, platform="linux", system_profile="linux-local")

            self.assertEqual(Path(result["calibre"]["candidates"][0]["path"]), db)

    def test_calibre_cache_candidate_is_read_only_even_when_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / ".codex" / "runtime" / "workspace" / "data" / "calibre" / "cache" / "metadata.db"
            db.parent.mkdir(parents=True)
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("create table books (id integer primary key, path text)")
                conn.execute("insert into books (id, path) values (1, 'Author/Book (1)')")
                conn.commit()

            result = audit_library_profiles(root, platform="linux", system_profile="linux-local")
            candidate = result["calibre"]["candidates"][0]

            self.assertIn("runtime-cache", candidate["classification"])
            self.assertEqual(candidate["sqlite"]["status"], "ok")
            self.assertNotIn("mutate", candidate["allowed_operations"])
            self.assertFalse(candidate["authoritative"])

    def test_sqlite_sidecars_are_reported_as_snapshot_recommended(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "Zotero" / "zotero.sqlite"
            db.parent.mkdir(parents=True)
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("create table items (itemID integer primary key)")
                conn.commit()
            Path(str(db) + "-wal").write_bytes(b"wal")

            result = audit_library_profiles(root, platform="linux", system_profile="linux-local")
            candidate = result["zotero"]["candidates"][0]

            self.assertEqual(candidate["sidecars"]["classification"], "live-wal")
            self.assertTrue(candidate["sidecars"]["snapshot_recommended"])

    def test_windows_mounted_profile_records_posix_dialect_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mounted_root = Path(tmp) / "windows" / "Users" / "alice"
            db = mounted_root / "Calibre Library" / "metadata.db"
            book_dir = mounted_root / "Calibre Library" / "Author" / "Book (1)"
            book_dir.mkdir(parents=True)
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("create table books (id integer primary key, path text)")
                conn.execute("insert into books (id, path) values (1, 'Author/Book (1)')")
                conn.commit()

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

    def test_mnt_windows_profile_is_classified_as_mounted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mounted_root = Path(tmp) / "mnt" / "c" / "Users" / "alice"
            db = mounted_root / "Calibre Library" / "metadata.db"
            db.parent.mkdir(parents=True)
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("create table books (id integer primary key, path text)")
                conn.commit()

            result = audit_library_profiles(mounted_root, platform="linux", system_profile="windows-mounted")
            candidate = result["calibre"]["candidates"][0]

            self.assertIn("mounted-windows", candidate["classification"])


if __name__ == "__main__":
    unittest.main()
