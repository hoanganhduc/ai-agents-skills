"""Google Drive client — scoped folder search + share link creation."""

import json
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]


class GDriveClient:
    def __init__(self, config):
        creds_value = config.get("GDRIVE_CREDENTIALS") or config.get("gdrive_credentials_file", "")
        if not creds_value:
            raise ValueError("GDrive credentials not configured (GDRIVE_CREDENTIALS)")

        self.folder_id = config["gdrive_folder_id"]
        self.share_permission = config.get("gdrive_share_permission", "anyone_with_link")

        credentials = _load_credentials(creds_value)
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self._folder_cache = None

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
        """Test GDrive connectivity. Returns (ok, message)."""
        try:
            resp = self.service.files().get(
                fileId=self.folder_id, fields="id, name"
            ).execute()
            name = resp.get("name", "?")
            return True, f"Folder '{name}' accessible"
        except Exception as e:
            return False, str(e)

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
