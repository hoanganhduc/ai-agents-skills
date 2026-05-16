"""Google Drive client — scoped folder search, file upload/download, share links."""

import json
import os
import io
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload


SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]


class GDriveClient:
    def __init__(self, creds_or_config, folder_id=None):
        """Accepts either:
          GDriveClient(config_dict)            — Zotero-style
          GDriveClient(creds_value, folder_id) — Calibre drive_sync style
        """
        if isinstance(creds_or_config, dict):
            config = creds_or_config
            creds_value = config.get("GDRIVE_CREDENTIALS") or config.get("gdrive_credentials_file", "")
            self.folder_id = config.get("gdrive_folder_id", "")
            self.share_permission = config.get("gdrive_share_permission", "anyone_with_link")
        else:
            creds_value = creds_or_config
            self.folder_id = folder_id or ""
            self.share_permission = "anyone_with_link"

        if not creds_value:
            raise ValueError("GDrive credentials not configured (GDRIVE_CREDENTIALS)")

        credentials = _load_credentials(creds_value)
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self._folder_cache = None

    def _get_service(self):
        return self.service

    def search(self, query, max_results=10):
        """Search for files by name within the scoped folder tree.

        Args:
            query: filename search string (partial match)
            max_results: max number of results

        Returns:
            list of dicts with keys: id, name, mimeType, webViewLink
        """
        folder_ids = self._get_folder_tree()

        results = []
        for fid in folder_ids:
            q = f"'{fid}' in parents and name contains '{_escape(query)}' and trashed = false"
            resp = self.service.files().list(
                q=q,
                fields="files(id, name, mimeType, webViewLink)",
                pageSize=max_results,
            ).execute()
            results.extend(resp.get("files", []))
            if len(results) >= max_results:
                break

        return results[:max_results]

    def find_root_file(self, filename):
        """Find one exact filename directly under the configured root folder."""
        q = (
            f"'{self.folder_id}' in parents "
            f"and name = '{_escape(filename)}' "
            "and trashed = false"
        )
        resp = self.service.files().list(
            q=q,
            fields="files(id, name, mimeType, webViewLink, modifiedTime, size)",
            pageSize=5,
        ).execute()
        files = resp.get("files", [])
        return files[0] if files else None

    def create_share_link(self, file_id):
        """Create a share link for a file.

        Returns:
            share URL string
        """
        if self.share_permission == "anyone_with_link":
            self.service.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
            ).execute()

        file_meta = self.service.files().get(
            fileId=file_id, fields="webViewLink"
        ).execute()
        return file_meta.get("webViewLink", "")

    def check_connection(self):
        """Test GDrive connectivity. Returns True on success."""
        try:
            resp = self.service.files().get(
                fileId=self.folder_id, fields="id, name"
            ).execute()
            return bool(resp.get("id"))
        except Exception:
            return False

    def download_file(self, file_id, local_path, progress_callback=None):
        """Download a Drive file to local_path.

        progress_callback, when provided, is called after each chunk as:
            callback(downloaded_bytes, total_bytes, progress_fraction, done)
        """
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        request = self.service.files().get_media(fileId=file_id)
        with open(local_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request, chunksize=1024 * 1024)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if progress_callback and status:
                    progress = status.progress()
                    progress_callback(
                        getattr(status, "resumable_progress", None),
                        getattr(status, "total_size", None),
                        progress,
                        done,
                    )

    def upload_file(self, local_path, filename, parent_folder_id):
        """Upload a local file to Drive. Returns file ID."""
        import mimetypes
        mime, _ = mimetypes.guess_type(local_path)
        mime = mime or "application/octet-stream"
        file_metadata = {"name": filename, "parents": [parent_folder_id]}
        media = MediaFileUpload(local_path, mimetype=mime, resumable=True)
        created = self.service.files().create(
            body=file_metadata, media_body=media, fields="id"
        ).execute()
        return created["id"]

    def update_file(self, file_id, local_path):
        """Replace the content of an existing Drive file. Returns file ID."""
        import mimetypes
        mime, _ = mimetypes.guess_type(local_path)
        mime = mime or "application/octet-stream"
        media = MediaFileUpload(local_path, mimetype=mime, resumable=True)
        updated = self.service.files().update(
            fileId=file_id, media_body=media, fields="id"
        ).execute()
        return updated["id"]

    def _get_folder_tree(self):
        """Get all folder IDs in the tree rooted at self.folder_id (cached)."""
        if self._folder_cache is not None:
            return self._folder_cache

        folder_ids = [self.folder_id]
        queue = [self.folder_id]

        while queue:
            parent = queue.pop(0)
            q = f"'{parent}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            resp = self.service.files().list(
                q=q, fields="files(id)", pageSize=100
            ).execute()
            for f in resp.get("files", []):
                folder_ids.append(f["id"])
                queue.append(f["id"])

        self._folder_cache = folder_ids
        return folder_ids


def _load_credentials(creds_value):
    """Load service account credentials from a file path or JSON string.

    Accepts either:
      - A file path to a service account JSON file
      - A raw JSON string containing service account credentials
    """
    # Try as JSON string first (works inside sandbox where file paths may not exist)
    if creds_value.strip().startswith("{"):
        try:
            info = json.loads(creds_value)
            return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        except (json.JSONDecodeError, ValueError):
            pass

    # Try as file path
    if os.path.exists(creds_value):
        return service_account.Credentials.from_service_account_file(creds_value, scopes=SCOPES)

    if creds_value.strip().startswith("{"):
        raise FileNotFoundError("GDrive credentials: invalid inline JSON credentials")
    raise FileNotFoundError("GDrive credentials: configured credential file was not found")


def _escape(s):
    """Escape single quotes for GDrive query."""
    return s.replace("\\", "\\\\").replace("'", "\\'")
