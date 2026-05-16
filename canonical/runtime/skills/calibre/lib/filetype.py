"""File type detection for Zotero attachments.

Uses mimetypes (stdlib) as primary, with magic byte fallback for common types.
Maps to Zotero-compatible content types.
"""

import os
import mimetypes

# Ensure common types are registered
mimetypes.add_type("application/epub+zip", ".epub")
mimetypes.add_type("application/x-mobipocket-ebook", ".mobi")
mimetypes.add_type("text/markdown", ".md")

# Magic byte signatures → MIME type
_MAGIC = [
    (b"%PDF", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", None),        # needs subtype check (WEBP, AVI)
    (b"PK\x03\x04", None),  # ZIP-based: EPUB, DOCX, XLSX, PPTX, or plain ZIP
]


def detect_content_type(file_path):
    """Detect MIME type of a file.

    Args:
        file_path: path to the file

    Returns:
        (content_type, extension) — e.g. ("application/pdf", ".pdf")
    """
    ext = os.path.splitext(file_path)[1].lower()

    # 1. Try stdlib mimetypes (extension-based, fast)
    mime, _ = mimetypes.guess_type(file_path)
    if mime:
        return mime, ext or mimetypes.guess_extension(mime) or ""

    # 2. Fallback: magic bytes
    try:
        with open(file_path, "rb") as f:
            header = f.read(16)
    except OSError:
        return "application/octet-stream", ext

    for sig, mime_type in _MAGIC:
        if header.startswith(sig):
            if mime_type:
                return mime_type, ext or (mimetypes.guess_extension(mime_type) or "")
            if sig == b"PK\x03\x04":
                return _detect_zip_subtype(file_path, ext)
            if sig == b"RIFF" and len(header) >= 12 and header[8:12] == b"WEBP":
                return "image/webp", ext or ".webp"

    return "application/octet-stream", ext


def _detect_zip_subtype(file_path, ext):
    """Identify ZIP-based formats: EPUB, DOCX, XLSX, PPTX."""
    import zipfile
    try:
        with zipfile.ZipFile(file_path) as zf:
            names = zf.namelist()
            # EPUB: first entry is 'mimetype' containing 'application/epub+zip'
            if names and names[0] == "mimetype":
                try:
                    mt = zf.read("mimetype").decode("utf-8").strip()
                    if "epub" in mt:
                        return "application/epub+zip", ext or ".epub"
                except Exception:
                    pass
            # Office Open XML
            for name in names:
                if name.startswith("word/"):
                    return ("application/vnd.openxmlformats-officedocument"
                            ".wordprocessingml.document"), ext or ".docx"
                if name.startswith("xl/"):
                    return ("application/vnd.openxmlformats-officedocument"
                            ".spreadsheetml.sheet"), ext or ".xlsx"
                if name.startswith("ppt/"):
                    return ("application/vnd.openxmlformats-officedocument"
                            ".presentationml.presentation"), ext or ".pptx"
    except (zipfile.BadZipFile, OSError):
        pass
    return "application/zip", ext or ".zip"


def is_pdf(content_type):
    """Check if content type is PDF."""
    return content_type == "application/pdf"
