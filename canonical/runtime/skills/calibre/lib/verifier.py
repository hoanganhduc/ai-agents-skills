"""File verification for Calibre book files.

Supports PDF (same checks as Zotero) and EPUB (magic bytes + basic structure).
"""

import os


MIN_SIZE = 10 * 1024    # 10KB
MAX_SIZE = 500 * 1024 * 1024  # 500MB (books can be large)


def verify(file_path, fmt=None):
    """Verify a downloaded book file.

    Args:
        file_path: path to the file
        fmt: format hint e.g. "epub", "pdf", "mobi" (auto-detected if None)

    Returns:
        dict: {status: "accept"|"reject"|"unverified", reason, page_count}
    """
    if not os.path.exists(file_path):
        return {"status": "reject", "reason": "File does not exist", "page_count": None}

    size = os.path.getsize(file_path)
    if size < MIN_SIZE:
        return {"status": "reject",
                "reason": f"Too small ({size} bytes, min {MIN_SIZE})",
                "page_count": None}
    if size > MAX_SIZE:
        return {"status": "reject",
                "reason": f"Too large ({size} bytes, max {MAX_SIZE})",
                "page_count": None}

    with open(file_path, "rb") as f:
        header = f.read(16)

    detected = _detect_format(header, file_path)
    if fmt is None:
        fmt = detected

    if fmt in ("epub",):
        return _verify_epub(file_path, header)
    elif fmt in ("pdf",):
        return _verify_pdf(file_path, header)
    elif fmt in ("mobi", "azw", "azw3"):
        return _verify_mobi(header)
    else:
        # Unknown format: accept based on size + magic bytes
        return {"status": "unverified",
                "reason": f"Format '{fmt}' not deeply verified; size OK",
                "page_count": None}


def _detect_format(header, file_path):
    if header.startswith(b"%PDF"):
        return "pdf"
    if header[:4] == b"PK\x03\x04":  # ZIP (EPUB, DOCX, etc.)
        ext = os.path.splitext(file_path)[1].lower()
        return ext.lstrip(".") or "epub"
    if header[:4] in (b"MOBI", b"\xd0\xcf\x11\xe0"):
        return "mobi"
    if header[:4] == b"BOOK":
        return "mobi"
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    return ext or "unknown"


def _verify_pdf(file_path, header):
    if not header.startswith(b"%PDF"):
        return {"status": "reject", "reason": "Not a PDF (missing %PDF header)", "page_count": None}

    page_count = None
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(file_path)
        page_count = len(reader.pages)
        if page_count == 0:
            return {"status": "reject", "reason": "PDF has 0 pages", "page_count": 0}
    except Exception:
        pass

    return {"status": "accept", "reason": "PDF checks passed", "page_count": page_count}


def _verify_epub(file_path, header):
    if header[:4] != b"PK\x03\x04":
        return {"status": "reject", "reason": "Not a valid EPUB (not a ZIP file)", "page_count": None}

    try:
        import zipfile
        with zipfile.ZipFile(file_path, "r") as z:
            names = z.namelist()
            # EPUB must contain mimetype file
            if "mimetype" not in names:
                return {"status": "reject",
                        "reason": "Not a valid EPUB (missing mimetype entry)",
                        "page_count": None}
            mime = z.read("mimetype").decode("utf-8", errors="ignore").strip()
            if "epub" not in mime.lower():
                return {"status": "reject",
                        "reason": f"Not a valid EPUB (mimetype: {mime})",
                        "page_count": None}
    except Exception as e:
        return {"status": "reject",
                "reason": f"EPUB zip read failed: {e}",
                "page_count": None}

    return {"status": "accept", "reason": "EPUB checks passed", "page_count": None}


def _verify_mobi(header):
    # MOBI/AZW: just check it's not obviously wrong
    return {"status": "unverified",
            "reason": "MOBI format accepted based on size (deep check not implemented)",
            "page_count": None}
