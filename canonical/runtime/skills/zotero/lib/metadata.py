"""Metadata resolver with Translation Server + direct DOI/arXiv/ISBN fallback."""

import html
import json
import os
import re
import subprocess
from xml.etree import ElementTree
from urllib.parse import quote

import requests

# Patterns for input type detection
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/[^\s]+$")
DOI_URL_PATTERN = re.compile(r"https?://(?:dx\.)?doi\.org/(10\.\d{4,9}/[^\s]+)")
ARXIV_PATTERN = re.compile(r"^(?:arXiv:)?(\d{4}\.\d{4,5}(?:v\d+)?)$", re.IGNORECASE)
ARXIV_URL_PATTERN = re.compile(r"https?://arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)")
ARXIV_OLD_PATTERN = re.compile(r"^(?:arXiv:)?([a-z-]+/\d{7}(?:v\d+)?)$", re.IGNORECASE)
ISBN_PATTERN = re.compile(r"^(?:ISBN[:\s-]?)?([\d-]{10,17}[X]?)$", re.IGNORECASE)
URL_PATTERN = re.compile(r"^https?://")


def detect_input_type(identifier):
    """Detect input type and normalize. Returns (type, normalized_value).

    Types: 'doi', 'arxiv', 'isbn', 'url'
    """
    identifier = identifier.strip()

    # DOI URL → extract DOI
    m = DOI_URL_PATTERN.match(identifier)
    if m:
        return "doi", m.group(1)

    # arXiv URL → extract ID
    m = ARXIV_URL_PATTERN.match(identifier)
    if m:
        return "arxiv", m.group(1)

    # Bare DOI
    if DOI_PATTERN.match(identifier):
        return "doi", identifier

    # arXiv ID (new format: 2301.12345)
    m = ARXIV_PATTERN.match(identifier)
    if m:
        return "arxiv", m.group(1)

    # arXiv ID (old format: math/0601001)
    m = ARXIV_OLD_PATTERN.match(identifier)
    if m:
        return "arxiv", m.group(1)

    # ISBN
    m = ISBN_PATTERN.match(identifier)
    if m:
        isbn = re.sub(r"[^0-9X]", "", m.group(1).upper())
        return "isbn", isbn

    # Generic URL
    if URL_PATTERN.match(identifier):
        return "url", identifier

    # Fallback: try as DOI if it contains a slash
    if "/" in identifier and not identifier.startswith("http"):
        return "doi", identifier

    return "unknown", identifier


def _build_lookup_url(input_type, normalized):
    if input_type == "doi":
        return f"https://doi.org/{normalized}"
    if input_type == "arxiv":
        return f"https://arxiv.org/abs/{normalized}"
    if input_type == "isbn":
        return f"https://www.worldcat.org/isbn/{normalized}"
    if input_type == "url":
        return normalized
    raise ValueError(f"Cannot determine identifier type for: {normalized}")


def _split_name(name):
    parts = [p for p in (name or "").strip().split() if p]
    if not parts:
        return {"name": ""}
    if len(parts) == 1:
        return {"name": parts[0]}
    return {"firstName": " ".join(parts[:-1]), "lastName": parts[-1]}


def _author_list(entries):
    creators = []
    for entry in entries or []:
        if isinstance(entry, dict):
            given = (entry.get("given") or "").strip()
            family = (entry.get("family") or "").strip()
            literal = (entry.get("name") or "").strip()
            if family or given:
                creator = {"creatorType": "author"}
                if given:
                    creator["firstName"] = given
                if family:
                    creator["lastName"] = family
                creators.append(creator)
                continue
            if literal:
                creator = _split_name(literal)
                creator["creatorType"] = "author"
                creators.append(creator)
                continue
        elif entry:
            creator = _split_name(str(entry))
            creator["creatorType"] = "author"
            creators.append(creator)
    return creators


def _format_date_parts(parts):
    if not parts:
        return ""
    nums = [str(p) for p in parts if p not in (None, "")]
    out = []
    for idx, num in enumerate(nums[:3]):
        out.append(num if idx == 0 else num.zfill(2))
    return "-".join(out)


def _crossref_date(message):
    for key in ("published-print", "published-online", "issued", "created"):
        date_parts = (((message.get(key) or {}).get("date-parts") or [[]])[0])
        if date_parts:
            return _format_date_parts(date_parts)
    return ""


def _crossref_item_type(message):
    mapping = {
        "journal-article": "journalArticle",
        "proceedings-article": "conferencePaper",
        "proceedings": "conferencePaper",
        "book": "book",
        "book-chapter": "bookSection",
        "book-part": "bookSection",
        "posted-content": "manuscript",
        "report": "report",
        "dissertation": "thesis",
        "reference-entry": "encyclopediaArticle",
    }
    return mapping.get((message.get("type") or "").lower(), "journalArticle")


def _fetch_via_translation_server(lookup_url, translation_server):
    server_ok = False
    try:
        requests.get(translation_server, timeout=5)
        server_ok = True
    except (requests.ConnectionError, ConnectionError):
        if "host.docker.internal" in translation_server:
            fallback = translation_server.replace("host.docker.internal", "localhost")
            try:
                requests.get(fallback, timeout=5)
                translation_server = fallback
                server_ok = True
            except (requests.ConnectionError, ConnectionError):
                pass
    if not server_ok:
        raise ConnectionError(
            f"Translation Server unreachable at {translation_server}. "
            "Direct DOI/arXiv/ISBN fallback will be used when possible."
        )

    headers = {"Content-Type": "text/plain"}
    try:
        resp = requests.post(
            f"{translation_server}/web",
            data=lookup_url,
            headers=headers,
            timeout=30,
        )
    except requests.Timeout:
        raise TimeoutError(f"Translation Server timed out for {lookup_url}")

    if resp.status_code == 501:
        raise ValueError(f"Translation Server could not process: {lookup_url} (no translator found)")
    if resp.status_code != 200:
        raise RuntimeError(f"Translation Server returned {resp.status_code} for {lookup_url}")

    items = resp.json()
    if not items:
        raise ValueError(f"Translation Server returned empty result for {lookup_url}")
    return items[0]


def _fetch_doi_direct(doi):
    url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
    resp = requests.get(
        url,
        timeout=20,
        headers={"User-Agent": "Codex-Zotero-Skill/1.0"},
    )
    if resp.status_code == 404:
        raise ValueError(f"Crossref could not find DOI: {doi}")
    if resp.status_code != 200:
        raise RuntimeError(f"Crossref returned {resp.status_code} for DOI {doi}")

    message = (resp.json() or {}).get("message") or {}
    title = ((message.get("title") or [""]) or [""])[0]
    container = ((message.get("container-title") or [""]) or [""])[0]
    short_container = ((message.get("short-container-title") or [""]) or [""])[0]
    item_type = _crossref_item_type(message)
    metadata = {
        "itemType": item_type,
        "title": title,
        "creators": _author_list(message.get("author")),
        "date": _crossref_date(message),
        "DOI": (message.get("DOI") or doi),
        "url": message.get("URL") or f"https://doi.org/{doi}",
        "publisher": message.get("publisher", ""),
        "volume": message.get("volume", ""),
        "issue": message.get("issue", ""),
        "pages": message.get("page", ""),
        "journalAbbreviation": short_container,
    }
    if item_type == "bookSection":
        metadata["bookTitle"] = container
    else:
        metadata["publicationTitle"] = container
    return metadata


def _fetch_arxiv_direct(arxiv_id):
    url = f"https://export.arxiv.org/api/query?id_list={quote(arxiv_id, safe='')}"
    resp = None
    api_error = None
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Codex-Zotero-Skill/1.0"})
    except requests.RequestException as exc:
        api_error = exc

    if resp is not None and resp.status_code == 200:
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }
        root = ElementTree.fromstring(resp.text)
        entry = root.find("atom:entry", ns)
        if entry is None:
            raise ValueError(f"arXiv returned no entry for {arxiv_id}")

        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
        summary = (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip()
        published = (entry.findtext("atom:published", default="", namespaces=ns) or "").strip()
        doi = (entry.findtext("arxiv:doi", default="", namespaces=ns) or "").strip()
        creators = []
        for author in entry.findall("atom:author", ns):
            name = (author.findtext("atom:name", default="", namespaces=ns) or "").strip()
            if name:
                creator = _split_name(name)
                creator["creatorType"] = "author"
                creators.append(creator)

        return {
            "itemType": "manuscript",
            "title": title,
            "creators": creators,
            "date": published[:10],
            "DOI": doi or f"10.48550/arXiv.{arxiv_id}",
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "abstractNote": summary,
            "archive": "arXiv",
            "archiveID": arxiv_id,
        }

    try:
        page = requests.get(
            f"https://arxiv.org/abs/{quote(arxiv_id, safe='')}",
            timeout=20,
            headers={"User-Agent": "Codex-Zotero-Skill/1.0"},
        )
    except requests.RequestException as exc:
        if api_error is not None:
            raise RuntimeError(
                f"arXiv API failed ({api_error}) and abs page fallback failed ({exc}) for {arxiv_id}"
            ) from exc
        raise RuntimeError(f"arXiv abs page fallback failed ({exc}) for {arxiv_id}") from exc
    if page.status_code != 200:
        api_status = resp.status_code if resp is not None else f"error: {api_error}"
        raise RuntimeError(f"arXiv returned {api_status} for API and {page.status_code} for abs page ({arxiv_id})")

    def _meta_all(name):
        pattern = re.compile(
            rf'<meta\s+name="{re.escape(name)}"\s+content="([^"]*)"',
            re.IGNORECASE,
        )
        return [html.unescape(m) for m in pattern.findall(page.text)]

    def _meta_one(name):
        values = _meta_all(name)
        return values[0].strip() if values else ""

    creators = []
    for name in _meta_all("citation_author"):
        creator = _split_name(name)
        creator["creatorType"] = "author"
        creators.append(creator)

    return {
        "itemType": "manuscript",
        "title": _meta_one("citation_title"),
        "creators": creators,
        "date": _meta_one("citation_date"),
        "DOI": _meta_one("citation_doi") or f"10.48550/arXiv.{arxiv_id}",
        "url": _meta_one("citation_abstract_html_url") or f"https://arxiv.org/abs/{arxiv_id}",
        "abstractNote": _meta_one("citation_abstract"),
        "archive": "arXiv",
        "archiveID": arxiv_id,
    }


def _fetch_isbn_direct(isbn):
    url = (
        "https://openlibrary.org/api/books"
        f"?bibkeys=ISBN:{quote(isbn, safe='')}&format=json&jscmd=data"
    )
    resp = requests.get(url, timeout=20, headers={"User-Agent": "Codex-Zotero-Skill/1.0"})
    if resp.status_code != 200:
        raise RuntimeError(f"Open Library returned {resp.status_code} for ISBN {isbn}")

    data = resp.json() or {}
    book = data.get(f"ISBN:{isbn}")
    if not book:
        raise ValueError(f"Open Library returned no record for ISBN {isbn}")

    return {
        "itemType": "book",
        "title": book.get("title", ""),
        "creators": _author_list([a.get("name", "") for a in book.get("authors", [])]),
        "date": book.get("publish_date", ""),
        "publisher": ", ".join(p.get("name", "") for p in book.get("publishers", []) if p.get("name")),
        "url": book.get("url", ""),
        "ISBN": isbn,
    }


def _fetch_direct(input_type, normalized):
    if input_type == "doi":
        return _fetch_doi_direct(normalized)
    if input_type == "arxiv":
        return _fetch_arxiv_direct(normalized)
    if input_type == "isbn":
        return _fetch_isbn_direct(normalized)
    raise NotImplementedError(f"No direct metadata fallback for input type: {input_type}")


def _finalize_metadata(metadata, input_type, normalized):
    doi = metadata.get("DOI", "")
    arxiv_id = ""
    if input_type == "arxiv":
        arxiv_id = normalized
        if not doi:
            metadata["DOI"] = f"10.48550/arXiv.{normalized}"
    elif doi and doi.startswith("10.48550/arXiv."):
        arxiv_id = doi.replace("10.48550/arXiv.", "")

    if metadata.get("itemType") == "preprint" or input_type == "arxiv":
        metadata["itemType"] = "manuscript"

    metadata["_input_type"] = input_type
    metadata["_normalized_id"] = normalized
    metadata["_arxiv_id"] = arxiv_id
    return metadata


def _windows_to_wsl_path(path):
    normalized = os.path.abspath(path).replace("\\", "/")
    if len(normalized) >= 2 and normalized[1] == ":":
        drive = normalized[0].lower()
        return f"/mnt/{drive}{normalized[2:]}"
    return normalized


def _fetch_url_via_wsl(url, config):
    distro = config.get("wsl_translation_distro", "Ubuntu-24.04")
    helper_windows = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "scripts", "wsl_url_translate.sh")
    )
    helper_wsl = _windows_to_wsl_path(helper_windows)
    env_repo = config.get("wsl_translation_repo", "~/zotero-translation-server")
    command = [
        "wsl",
        "-d",
        distro,
        "--",
        "env",
        f"ZOTERO_WSL_TRANSLATION_REPO={env_repo}",
        "bash",
        helper_wsl,
        url,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"WSL URL translation timed out for {url}") from exc
    except OSError as exc:
        raise RuntimeError(f"Failed to invoke WSL URL translation helper: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"WSL URL translation failed for {url}: {detail}")

    try:
        items = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("WSL URL translation returned invalid JSON") from exc

    if not items:
        raise ValueError(f"WSL URL translation returned empty result for {url}")
    if not isinstance(items, list):
        raise RuntimeError(f"WSL URL translation returned unexpected payload for {url}")
    return items[0]


def fetch_metadata(identifier, translation_server="http://localhost:1969", config=None):
    """Fetch metadata via Translation Server, then direct fallback if needed.

    Returns (metadata_dict, input_type, normalized_id) or raises.
    """
    config = config or {}
    input_type, normalized = detect_input_type(identifier)
    lookup_url = _build_lookup_url(input_type, normalized)
    translation_error = None

    if input_type == "url":
        try:
            metadata = _fetch_url_via_wsl(normalized, config)
            return _finalize_metadata(metadata, input_type, normalized), input_type, normalized
        except (ValueError, RuntimeError, TimeoutError, ConnectionError) as exc:
            translation_error = exc

    try:
        metadata = _fetch_via_translation_server(lookup_url, translation_server)
        return _finalize_metadata(metadata, input_type, normalized), input_type, normalized
    except (ConnectionError, ValueError, RuntimeError, TimeoutError) as exc:
        translation_error = exc

    if input_type in {"doi", "arxiv", "isbn"}:
        metadata = _fetch_direct(input_type, normalized)
        return _finalize_metadata(metadata, input_type, normalized), input_type, normalized

    raise translation_error
