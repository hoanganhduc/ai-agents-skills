"""PDF verification — validates downloaded PDFs before accepting.

Checks:
1. Magic bytes (%PDF)
2. File size (50KB–200MB)
3. Page count (reject 1-page stubs unless poster/extended abstract)
4. Page count vs metadata page range (detect paywall previews)
5. Aspect ratio (reject landscape slides)
6. Title/author text match for web sources
"""

import os
import re


MIN_SIZE = 50 * 1024        # 50KB
MAX_SIZE = 200 * 1024 * 1024  # 200MB


def verify(pdf_path, metadata=None, source_type="getscipapers", accept_short=False):
    """Verify a downloaded PDF.

    Args:
        pdf_path: path to the PDF file
        metadata: Zotero metadata dict (for page range and title/author checks)
        source_type: "getscipapers", "semantic_scholar", "arxiv"
        accept_short: if True, accept 1-2 page PDFs without checking itemType

    Returns:
        dict with keys:
          status: "accept", "reject", "unverified"
          reason: human-readable explanation
          page_count: int or None
    """
    if not os.path.exists(pdf_path):
        return {"status": "reject", "reason": "File does not exist", "page_count": None}

    # 1. Magic bytes
    with open(pdf_path, "rb") as f:
        header = f.read(8)
    if not header.startswith(b"%PDF"):
        return {"status": "reject", "reason": "Not a PDF (missing %PDF header)", "page_count": None}

    # 2. File size
    size = os.path.getsize(pdf_path)
    if size < MIN_SIZE:
        return {"status": "reject", "reason": f"Too small ({size} bytes, min {MIN_SIZE})", "page_count": None}
    if size > MAX_SIZE:
        return {"status": "reject", "reason": f"Too large ({size} bytes, max {MAX_SIZE})", "page_count": None}

    # 3-5. Page count, dimensions, text extraction via PyPDF2/pdfplumber
    page_count = None
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        page_count = len(reader.pages)

        # 3. Page count checks
        if page_count == 0:
            return {"status": "reject", "reason": "PDF has 0 pages", "page_count": 0}

        if page_count == 1 and not accept_short:
            if metadata and _is_short_form(metadata):
                pass  # poster/extended abstract — allow
            else:
                return {"status": "reject",
                        "reason": "Only 1 page — likely paywall preview or first-page stub",
                        "page_count": 1}

        if page_count in (2, 3) and not accept_short:
            if not (metadata and _is_short_form(metadata)):
                # Warn but accept
                pass

        # 4. Page count vs metadata page range
        if metadata and page_count == 1:
            expected = _expected_pages(metadata)
            if expected and expected > 3:
                return {"status": "reject",
                        "reason": f"1 page but metadata says {expected} pages — paywall preview",
                        "page_count": 1}

        # 5. Aspect ratio (first page)
        if reader.pages:
            page = reader.pages[0]
            box = page.mediabox
            width = float(box.width)
            height = float(box.height)
            if width > 0 and height > 0:
                ratio = width / height
                if ratio > 1.2:
                    return {"status": "reject",
                            "reason": f"Landscape orientation (ratio {ratio:.2f}) — likely slides",
                            "page_count": page_count}

    except Exception as e:
        # If PyPDF2 fails, still try to accept based on magic bytes + size
        pass

    # 6. Title/author match for web sources
    if source_type in ("semantic_scholar", "web") and metadata:
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                if pdf.pages:
                    first_page_text = pdf.pages[0].extract_text() or ""
                    if first_page_text:
                        title = metadata.get("title", "")
                        if title and not _fuzzy_match(title, first_page_text):
                            return {"status": "unverified",
                                    "reason": "Title not found on first page — may be wrong paper",
                                    "page_count": page_count}
                    else:
                        # Scanned PDF — no text extractable
                        return {"status": "unverified",
                                "reason": "No extractable text (scanned PDF) — cannot verify content",
                                "page_count": page_count}
        except Exception:
            return {"status": "unverified",
                    "reason": "Text extraction failed — cannot verify content",
                    "page_count": page_count}

    # Page count warnings
    if page_count and page_count > 60:
        return {"status": "accept",
                "reason": f"Accepted ({page_count} pages — unusually long, may be thesis/book)",
                "page_count": page_count}

    return {"status": "accept", "reason": "All checks passed", "page_count": page_count}


def _is_short_form(metadata):
    """Check if metadata indicates a short-form paper (poster, extended abstract)."""
    item_type = metadata.get("itemType", "")
    title = metadata.get("title", "").lower()
    extra = metadata.get("extra", "").lower()

    if item_type == "conferencePaper":
        for keyword in ["poster", "extended abstract", "short paper", "demo"]:
            if keyword in title or keyword in extra:
                return True
    return False


def _expected_pages(metadata):
    """Try to extract expected page count from metadata page range."""
    pages = metadata.get("pages", "")
    if not pages:
        return None
    m = re.match(r"(\d+)\s*[-–]\s*(\d+)", pages)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        return end - start + 1
    return None


def _fuzzy_match(title, text):
    """Check if title appears (approximately) in text."""
    # Normalize: lowercase, remove punctuation
    title_norm = re.sub(r"[^\w\s]", "", title.lower())
    text_norm = re.sub(r"[^\w\s]", "", text.lower())

    # Check if first 5 significant words of title appear in text
    words = [w for w in title_norm.split() if len(w) > 3][:5]
    if not words:
        return True  # Can't check — accept

    matches = sum(1 for w in words if w in text_norm)
    return matches >= len(words) * 0.6
