"""Google Drive sync for Calibre library.

The Calibre library on Drive mirrors the local folder structure:
  <library_root>/
    metadata.db          ← main database
    Author Name/
      Book Title (Year)/
        Book_Title.epub
        Book_Title.pdf
        cover.jpg

Key operations:
  pull_db()            — download metadata.db to local cache path
  push_db()            — upload modified metadata.db back to Drive
  pull_book()          — download a specific book file by book_id + format
  push_book()          — upload a new book file to Drive
  delete_book_files()  — remove book folder from Drive
"""

import os
import json
import time
import shutil
import subprocess
from contextlib import contextmanager

try:
    import fcntl  # type: ignore
except ImportError:
    fcntl = None
    import msvcrt


class DriveSync:
    def __init__(self, config):
        self.config = config
        self.folder_id = config.get("gdrive_folder_id", "")
        self.db_local = config["db_local_path"]
        self.staging = config["staging_dir"]
        self._client = None
        self._folder_tree = None   # cached: relative_path → folder_id

    def _get_client(self):
        if self._client is None:
            from .gdrive import GDriveClient
            creds = self.config.get("GDRIVE_CREDENTIALS") or \
                    self.config.get("gdrive_credentials_file")
            if not creds:
                raise RuntimeError(
                    "GDRIVE_CREDENTIALS not set. "
                    "Add to secrets file or config.json."
                )
            self._client = GDriveClient(creds, self.folder_id)
        return self._client

    # ------------------------------------------------------------------ #
    # DB pull / push
    # ------------------------------------------------------------------ #

    def pull_db(self, force=False, progress_callback=None):
        """Download metadata.db from Drive to local cache path.

        Returns True if downloaded, False if already up-to-date (not forced).
        """
        client = self._get_client()
        os.makedirs(os.path.dirname(self.db_local), exist_ok=True)

        # Find metadata.db in the library root folder. Fall back to the older
        # tree search for compatibility with non-standard layouts.
        db_file = client.find_root_file("metadata.db")
        if not db_file:
            files = client.search("metadata.db", max_results=5)
            db_file = next(
                (f for f in files if f["name"] == "metadata.db"), None
            )
        if not db_file:
            raise FileNotFoundError(
                f"metadata.db not found in Drive folder {self.folder_id}. "
                "Is gdrive_folder_id pointing to the Calibre library root?"
            )

        # Check mtime to skip unnecessary downloads
        drive_mtime = db_file.get("modifiedTime", "")
        mtime_cache = self.db_local + ".mtime"
        if not force and os.path.exists(self.db_local) and os.path.exists(mtime_cache):
            with open(mtime_cache) as f:
                cached_mtime = f.read().strip()
            if cached_mtime == drive_mtime:
                return False  # already current

        # Download
        tmp_path = self.db_local + ".tmp"
        client.download_file(db_file["id"], tmp_path, progress_callback=progress_callback)
        os.replace(tmp_path, self.db_local)

        # Cache mtime
        with open(mtime_cache, "w") as f:
            f.write(drive_mtime)

        return True

    def push_db(self):
        """Upload local metadata.db to Drive, replacing the existing file."""
        if not os.path.exists(self.db_local):
            raise FileNotFoundError(f"Local metadata.db not found: {self.db_local}")

        client = self._get_client()
        files = client.search("metadata.db", max_results=5)
        db_file = next(
            (f for f in files if f["name"] == "metadata.db"), None
        )

        if db_file:
            client.update_file(db_file["id"], self.db_local)
        else:
            client.upload_file(self.db_local, "metadata.db", self.folder_id)

        # Invalidate mtime cache so next pull_db fetches fresh
        mtime_cache = self.db_local + ".mtime"
        if os.path.exists(mtime_cache):
            os.remove(mtime_cache)

    # ------------------------------------------------------------------ #
    # Book file pull / push / delete
    # ------------------------------------------------------------------ #

    def _resolve_folder(self, relative_path):
        """Traverse Drive folder hierarchy to find folder ID for relative_path.

        relative_path: e.g. "Author Name/Book Title (2023)"
        Returns folder ID string or None.
        """
        client = self._get_client()
        service = client._get_service()
        parts = [p for p in relative_path.replace("\\", "/").split("/") if p]

        parent_id = self.folder_id
        for part in parts:
            query = (
                f"'{parent_id}' in parents "
                f"and name='{part.replace(chr(39), chr(39)+chr(39))}' "
                f"and mimeType='application/vnd.google-apps.folder' "
                f"and trashed=false"
            )
            resp = service.files().list(
                q=query,
                fields="files(id, name)",
                pageSize=5,
            ).execute()
            items = resp.get("files", [])
            if not items:
                return None
            parent_id = items[0]["id"]
        return parent_id

    def _create_folder_path(self, relative_path):
        """Create nested folders on Drive, returning the deepest folder ID."""
        client = self._get_client()
        service = client._get_service()
        parts = [p for p in relative_path.replace("\\", "/").split("/") if p]

        parent_id = self.folder_id
        for part in parts:
            # Check if folder exists
            query = (
                f"'{parent_id}' in parents "
                f"and name='{part.replace(chr(39), chr(39)+chr(39))}' "
                f"and mimeType='application/vnd.google-apps.folder' "
                f"and trashed=false"
            )
            resp = service.files().list(
                q=query, fields="files(id)", pageSize=5
            ).execute()
            items = resp.get("files", [])
            if items:
                parent_id = items[0]["id"]
            else:
                # Create folder
                metadata = {
                    "name": part,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [parent_id],
                }
                created = service.files().create(
                    body=metadata, fields="id"
                ).execute()
                parent_id = created["id"]
        return parent_id

    def pull_book(self, book_path, fmt, file_name):
        """Download a book file from Drive.

        Args:
            book_path: relative path from Calibre db, e.g. "Author/Title (2023)"
            fmt: format string e.g. "epub"
            file_name: base filename without extension from data table

        Returns:
            local path to downloaded file, or None if not found
        """
        client = self._get_client()
        service = client._get_service()

        folder_id = self._resolve_folder(book_path)
        if not folder_id:
            return None

        drive_filename = f"{file_name}.{fmt.lower()}"
        query = (
            f"'{folder_id}' in parents "
            f"and name='{drive_filename.replace(chr(39), chr(39)+chr(39))}' "
            f"and trashed=false"
        )
        resp = service.files().list(
            q=query, fields="files(id, name)", pageSize=5
        ).execute()
        items = resp.get("files", [])
        if not items:
            return None

        local_path = os.path.join(self.staging, drive_filename)
        client.download_file(items[0]["id"], local_path)
        return local_path

    def push_book(self, local_path, book_path, fmt, file_name):
        """Upload a book file to Drive.

        Creates the folder hierarchy if it doesn't exist.

        Args:
            local_path: local path to the file
            book_path: Calibre relative path e.g. "Author/Title (2023)"
            fmt: format extension e.g. "epub"
            file_name: base filename without extension

        Returns:
            Drive file ID
        """
        client = self._get_client()
        service = client._get_service()

        folder_id = self._resolve_folder(book_path)
        if not folder_id:
            folder_id = self._create_folder_path(book_path)

        drive_filename = f"{file_name}.{fmt.lower()}"

        # Check if file already exists (update vs create)
        query = (
            f"'{folder_id}' in parents "
            f"and name='{drive_filename.replace(chr(39), chr(39)+chr(39))}' "
            f"and trashed=false"
        )
        resp = service.files().list(
            q=query, fields="files(id)", pageSize=5
        ).execute()
        existing = resp.get("files", [])

        if existing:
            file_id = client.update_file(existing[0]["id"], local_path)
        else:
            file_id = client.upload_file(local_path, drive_filename, folder_id)

        return file_id

    def delete_book_files(self, book_path):
        """Trash the entire book folder on Drive.

        Args:
            book_path: Calibre relative path e.g. "Author/Title (2023)"

        Returns:
            True if folder was found and trashed, False if not found
        """
        client = self._get_client()
        service = client._get_service()

        # Find deepest folder (the Title folder)
        folder_id = self._resolve_folder(book_path)
        if not folder_id:
            return False

        # Move to trash
        service.files().update(
            fileId=folder_id, body={"trashed": True}
        ).execute()
        return True

    # ------------------------------------------------------------------ #
    # DB lock (file-based, prevents concurrent DB writes)
    # ------------------------------------------------------------------ #

    @contextmanager
    def db_lock(self, timeout=30):
        """Acquire an exclusive lock on metadata.db operations."""
        lock_path = self.db_local + ".lock"
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        start = time.time()
        lock_file = open(lock_path, "a+b")
        try:
            if os.path.getsize(lock_path) == 0:
                lock_file.write(b"0")
                lock_file.flush()
            while True:
                try:
                    if fcntl is not None:
                        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    else:
                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.time() - start > timeout:
                        raise TimeoutError(
                            f"Could not acquire metadata.db lock after {timeout}s"
                        )
                    time.sleep(0.5)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
            else:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            lock_file.close()
