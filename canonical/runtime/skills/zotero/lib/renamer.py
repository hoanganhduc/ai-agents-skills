"""ZotFile-style PDF renaming engine.

Pattern: {%a_}{%y_}{%t} {[%T]}
  %a = authors (last names)
  %y = year
  %t = title (truncated after . or : and by max length)
  %T = item type label (e.g., "Journal Article", "Conference Paper")

Options:
  - Truncate title after first . or :
  - Max title length: 150
  - Max authors: 3
  - Authors shown when omitted: 1
  - Omission suffix: " et al"
"""

import os
import re
import unicodedata


DEFAULT_PATTERN = "{%a_}{%y_}{%t} {[%T]}"
MAX_TITLE_LENGTH = 150
MAX_AUTHORS = 3
AUTHORS_WHEN_OMITTED = 1
OMISSION_SUFFIX = " et al"

# Zotero itemType → human-readable label
ITEM_TYPE_LABELS = {
    "journalArticle": "Journal Article",
    "conferencePaper": "Conference Paper",
    "preprint": "Preprint",
    "book": "Book",
    "bookSection": "Book Section",
    "thesis": "Thesis",
    "report": "Report",
    "manuscript": "Manuscript",
    "magazineArticle": "Magazine Article",
    "newspaperArticle": "Newspaper Article",
    "webpage": "Webpage",
    "presentation": "Presentation",
    "patent": "Patent",
    "letter": "Letter",
    "document": "Document",
}


def rename(metadata, pattern=None):
    """Generate a ZotFile-style filename from Zotero metadata.

    Args:
        metadata: dict with Zotero item fields (title, creators, date, itemType)
        pattern: ZotFile pattern string (default: DEFAULT_PATTERN)

    Returns:
        Sanitized filename without extension (e.g., "Parker et al_2016_Decision Comfort [Journal Article]")
    """
    if pattern is None:
        pattern = DEFAULT_PATTERN

    authors_str = _format_authors(metadata.get("creators", []))
    year = _extract_year(metadata.get("date", ""))
    title = _format_title(metadata.get("title", "Untitled"))
    item_type = ITEM_TYPE_LABELS.get(metadata.get("itemType", ""), metadata.get("itemType", ""))

    # Apply pattern substitutions
    result = pattern
    result = result.replace("%a", authors_str)
    result = result.replace("%y", year)
    result = result.replace("%t", title)
    result = result.replace("%T", item_type)

    # Remove braces (used for grouping in ZotFile patterns)
    result = result.replace("{", "").replace("}", "")

    # Remove empty brackets/parens that result from missing fields
    result = re.sub(r"\[\s*\]", "", result)
    result = re.sub(r"\(\s*\)", "", result)

    # Clean up multiple spaces/underscores
    result = re.sub(r"[_\s]{2,}", " ", result)
    result = result.strip(" _-")

    # Sanitize for filesystem
    result = _sanitize_filename(result)

    return result


def _format_authors(creators):
    """Format author last names with ZotFile rules."""
    authors = []
    for c in creators:
        if c.get("creatorType") != "author":
            continue
        name = c.get("lastName") or c.get("name", "")
        if name:
            authors.append(name)

    if not authors:
        return ""

    if len(authors) <= MAX_AUTHORS:
        return "_".join(authors)
    else:
        shown = authors[:AUTHORS_WHEN_OMITTED]
        return "_".join(shown) + OMISSION_SUFFIX


def _extract_year(date_str):
    """Extract 4-digit year from a date string."""
    if not date_str:
        return ""
    m = re.search(r"\b(\d{4})\b", date_str)
    return m.group(1) if m else ""


def _format_title(title):
    """Format title: truncate after first . or : and apply max length."""
    if not title:
        return "Untitled"

    # Truncate after first . or : (but not after common abbreviations)
    # Look for . or : that are NOT part of abbreviations like "e.g." "i.e." "vs."
    truncated = title
    for sep in [".", ":"]:
        idx = _find_truncation_point(truncated, sep)
        if idx is not None:
            truncated = truncated[:idx].rstrip()
            break

    # Apply max length (truncate at word boundary)
    if len(truncated) > MAX_TITLE_LENGTH:
        truncated = truncated[:MAX_TITLE_LENGTH]
        # Try to break at last space
        last_space = truncated.rfind(" ")
        if last_space > MAX_TITLE_LENGTH * 0.7:
            truncated = truncated[:last_space]

    return truncated.strip()


def _find_truncation_point(title, sep):
    """Find the position to truncate at for a separator.

    Returns index of separator, or None if no good truncation point found.
    Skips separators that appear too early (< 10 chars, likely abbreviation).
    """
    pos = 0
    while True:
        idx = title.find(sep, pos)
        if idx == -1:
            return None
        # Skip if too early (likely "e.g.", "i.e.", "Dr.", "vs.", etc.)
        if idx < 10:
            pos = idx + 1
            continue
        # For '.', skip if preceded by a single uppercase letter (initial)
        # or common abbreviation patterns
        if sep == "." and idx > 0:
            before = title[:idx].rstrip()
            last_word = before.split()[-1] if before.split() else ""
            if len(last_word) <= 3 and last_word[0].isupper():
                pos = idx + 1
                continue
        return idx
    return None


def rename_non_pdf(original_filename, metadata=None):
    """Generate a renamed filename for non-PDF attachments.

    Args:
        original_filename: original filename with extension (e.g., "figure1.png")
        metadata: optional Zotero metadata dict (title, creators, date, itemType)

    Returns:
        Sanitized filename without extension.
    """
    stem = os.path.splitext(original_filename)[0]

    if metadata is None:
        return stem

    authors_str = _format_authors(metadata.get("creators", []))
    year = _extract_year(metadata.get("date", ""))

    parts = []
    if authors_str:
        parts.append(authors_str)
    if year:
        parts.append(year)
    parts.append(stem)

    result = "_".join(parts)
    return _sanitize_filename(result)


def _sanitize_filename(name):
    """Remove/replace characters that are unsafe in filenames."""
    # Normalize unicode (decompose accented chars, keep base)
    name = unicodedata.normalize("NFKD", name)
    # Keep ASCII letters, digits, spaces, hyphens, underscores, brackets
    name = re.sub(r"[^\w\s\-\[\]()]", "", name, flags=re.UNICODE)
    # Replace multiple spaces
    name = re.sub(r"\s+", " ", name)
    return name.strip()
