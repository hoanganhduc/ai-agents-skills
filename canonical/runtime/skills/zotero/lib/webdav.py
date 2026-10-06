"""WebDAV client for Zotero file sync.

Zotero WebDAV format:
  - Files stored as <attachment_key>.zip in the zotero/ subdirectory
  - Each file has a <attachment_key>.prop sidecar with mtime/hash metadata
  - Each zip contains a single PDF with its renamed filename
  - Compression: deflate (zipfile.ZIP_DEFLATED)
"""

import os
import io
import hashlib
import re
import stat
from pathlib import Path
import zipfile
import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth


def file_sync_metadata(file_path):
    """Return Zotero attachment file-sync metadata for a local file."""
    digest = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    stat = os.stat(file_path)
    return {
        "md5": digest.hexdigest(),
        "mtime": int(stat.st_mtime * 1000),
    }


def populate_imported_file_attachment(template, file_path):
    """Populate md5/mtime fields Zotero Desktop expects for file sync."""
    template.update(file_sync_metadata(file_path))
    return template


def file_sync_properties_xml(file_path):
    """Return Zotero WebDAV .prop XML for a local file."""
    metadata = file_sync_metadata(file_path)
    return (
        '<properties version="1">'
        f'<mtime>{metadata["mtime"]}</mtime>'
        f'<hash>{metadata["md5"]}</hash>'
        '</properties>'
    ).encode("utf-8")


class WebDAVClient:
    def __init__(self, config):
        base_url = config["webdav_url"].rstrip("/")
        self.zotero_url = f"{base_url}/zotero"
        self.user = config["webdav_user"]
        self.password = config["WEBDAV_PASSWORD"]
        self.timeout = int(config.get("webdav_timeout", 30))
        self.upload_timeout = int(config.get("webdav_upload_timeout", max(self.timeout, 300)))
        self._auth = HTTPBasicAuth(self.user, self.password)
        self._auth_type = "basic"

    def _request(self, method, url, **kwargs):
        """Make a request with automatic auth type fallback."""
        kwargs.setdefault("timeout", self.timeout)
        r = requests.request(method, url, auth=self._auth, **kwargs)
        if r.status_code == 401 and self._auth_type == "basic":
            self._auth = HTTPDigestAuth(self.user, self.password)
            self._auth_type = "digest"
            r = requests.request(method, url, auth=self._auth, **kwargs)
        return r

    def upload(self, attachment_key, pdf_path, pdf_filename):
        """Zip a PDF and upload to WebDAV.

        Args:
            attachment_key: Zotero attachment item key (used as zip filename)
            pdf_path: local path to the PDF file
            pdf_filename: filename to use inside the zip (e.g., "Author_2016_Title [Type].pdf")

        Returns:
            True on success, raises on failure.
        """
        pdf_filename = os.path.basename(pdf_filename or "")
        if not pdf_filename:
            raise ValueError("pdf_filename must be a non-empty filename")

        # Create zip in memory
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(pdf_path, pdf_filename)
        buf.seek(0)

        # Upload zip and its Zotero file-sync property sidecar. Zotero Desktop
        # expects both files when syncing imported attachments from WebDAV.
        zip_url = f"{self.zotero_url}/{attachment_key}.zip"
        r = self._request("PUT", zip_url, data=buf.getvalue(),
                          headers={"Content-Type": "application/zip"},
                          timeout=self.upload_timeout)

        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f"WebDAV upload failed: HTTP {r.status_code} for {zip_url}")

        prop_url = f"{self.zotero_url}/{attachment_key}.prop"
        r = self._request("PUT", prop_url, data=file_sync_properties_xml(pdf_path),
                          headers={"Content-Type": "application/octet-stream"},
                          timeout=self.upload_timeout)

        if r.status_code not in (200, 201, 204):
            try:
                self._request("DELETE", zip_url)
            except Exception:
                pass
            raise RuntimeError(f"WebDAV property upload failed: HTTP {r.status_code} for {prop_url}")

        return True

    def download(self, attachment_key, output_dir, *, expected_member=None):
        """Download and extract a zip from WebDAV.

        Args:
            attachment_key: Zotero attachment item key
            output_dir: directory to extract the PDF into

        Returns:
            Path to the extracted PDF, or None if not found.
        """
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", attachment_key):
            raise ValueError("invalid attachment key")
        url = f"{self.zotero_url}/{attachment_key}.zip"
        r = self._request("GET", url, timeout=60)

        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise RuntimeError(f"WebDAV download failed: HTTP {r.status_code} for {url}")

        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            infos = zf.infolist()
            names = [entry.filename for entry in infos]
            if len(names) > 256 or len(names) != len(set(names)):
                raise ValueError("ambiguous or oversized attachment archive")
            candidates = [entry for entry in infos if not entry.is_dir()
                          and entry.filename.lower().endswith('.pdf')]
            if expected_member is not None:
                candidates = [entry for entry in candidates if entry.filename == expected_member]
            if not candidates and not names:
                return None
            if len(candidates) != 1:
                raise ValueError("select one exact PDF archive member before content access")
            entry = candidates[0]
            name = entry.filename
            mode = entry.external_attr >> 16
            if (name in {'.', '..'} or '/' in name or '\\' in name or ':' in name
                    or '\x00' in name or stat.S_ISLNK(mode) or entry.file_size > 128 * 1024**2):
                raise ValueError("unsafe or oversized selected attachment member")
            raw = zf.read(entry)
        root = Path(output_dir).absolute()
        if root.is_symlink() or root.resolve() != root:
            raise ValueError("attachment staging root must not traverse symlinks")
        root.mkdir(parents=True, exist_ok=True)
        directory = root / (attachment_key + '-' + hashlib.sha256(raw).hexdigest()[:20])
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink():
            raise ValueError("attachment destination must not be a symlink")
        target = directory / name
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        except FileExistsError:
            if target.is_symlink() or not target.is_file() or target.read_bytes() != raw:
                raise ValueError("existing attachment does not match the selected member")
        else:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
        return str(target)

    def delete(self, attachment_key):
        """Delete attachment zip and property sidecar from WebDAV."""
        ok = True
        for suffix in ("zip", "prop"):
            url = f"{self.zotero_url}/{attachment_key}.{suffix}"
            r = self._request("DELETE", url)
            ok = ok and r.status_code in (200, 204, 404)
        return ok

    def exists(self, attachment_key):
        """Check if a zip exists on WebDAV."""
        url = f"{self.zotero_url}/{attachment_key}.zip"
        r = self._request("HEAD", url)
        return r.status_code == 200

    def check_connection(self):
        """Test WebDAV connectivity. Returns (ok, message)."""
        try:
            r = self._request("PROPFIND", self.zotero_url,
                              headers={"Depth": "0"})
            if r.status_code < 400:
                return True, f"Connected ({self._auth_type} auth)"
            return False, f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)
