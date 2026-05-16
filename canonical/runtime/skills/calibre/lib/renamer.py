"""Book file naming engine for Calibre.

Generates filenames following the Calibre convention:
  Book_Title_-_Author_Name.ext   (for file delivery)
  Author Name/Title (Year)/      (for Drive folder structure)

The base filename used in the data table and Drive follows Calibre's own
default: sanitized title, replacing spaces with underscores.
"""

import re
import unicodedata


def make_filename(title, authors=None, year=None):
    """Generate a base filename (without extension) for a book file.

    Follows Calibre convention: sanitized title. Used as data.name value.

    Examples:
      "One Hundred Years of Solitude", ["Gabriel García Márquez"] →
        "One_Hundred_Years_of_Solitude"
    """
    base = sanitize(title)
    # Calibre uses the title for the base filename
    return base[:100]


def make_drive_folder_name(title, year=None):
    """Generate the Drive folder name for a book: 'Title (Year)'."""
    clean = sanitize_path(title)
    if year:
        return f"{clean} ({year})"
    return clean


def make_author_folder_name(authors):
    """Generate the author folder name: first author's display name."""
    if not authors:
        return "Unknown"
    return sanitize_path(authors[0])[:80]


def sanitize(name):
    """Sanitize for use as a filename component (underscores, ASCII-safe)."""
    name = unicodedata.normalize("NFKD", str(name))
    name = name.encode("ascii", "ignore").decode()
    name = re.sub(r"[^\w\s\-.]", "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name


def sanitize_path(name):
    """Sanitize for use in a path segment (spaces allowed, no slashes)."""
    name = unicodedata.normalize("NFKD", str(name))
    name = name.encode("ascii", "ignore").decode()
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    return name.strip()[:80]
