"""WebDAV client for Zotero file sync.

Zotero WebDAV format:
  - Files stored as <attachment_key>.zip in the zotero/ subdirectory
  - Each zip contains a single PDF with its renamed filename
  - Compression: deflate (zipfile.ZIP_DEFLATED)
"""

import os
import io
import hashlib
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

        # Upload
        url = f"{self.zotero_url}/{attachment_key}.zip"
        r = self._request("PUT", url, data=buf.getvalue(),
                          headers={"Content-Type": "application/zip"},
                          timeout=self.upload_timeout)

        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f"WebDAV upload failed: HTTP {r.status_code} for {url}")

        return True

    def download(self, attachment_key, output_dir):
        """Download and extract a zip from WebDAV.

        Args:
            attachment_key: Zotero attachment item key
            output_dir: directory to extract the PDF into

        Returns:
            Path to the extracted PDF, or None if not found.
        """
        url = f"{self.zotero_url}/{attachment_key}.zip"
        r = self._request("GET", url, timeout=60)

        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise RuntimeError(f"WebDAV download failed: HTTP {r.status_code} for {url}")

        os.makedirs(output_dir, exist_ok=True)
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        names = zf.namelist()
        if not names:
            return None

        # Extract the first (and typically only) file
        pdf_name = names[0]
        extracted_path = os.path.join(output_dir, pdf_name)
        zf.extract(pdf_name, output_dir)
        return extracted_path

    def delete(self, attachment_key):
        """Delete a zip from WebDAV."""
        url = f"{self.zotero_url}/{attachment_key}.zip"
        r = self._request("DELETE", url)
        return r.status_code in (200, 204, 404)

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
