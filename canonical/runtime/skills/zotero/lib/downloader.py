"""PDF download chain — branched by input type.

DOI:   getscipapers --doi → Semantic Scholar → arXiv fallback
ISBN:  getscipapers --isbn
arXiv: getscipapers --arxiv → DOI chain if publisher DOI found

Each source returns (pdf_path, version_tag, source_label) or None.
"""

import os
import sys
import time
import hashlib
import subprocess
import requests

from lib.verifier import verify


def download(metadata, config, accept_short=False):
    """Run the download chain for a paper.

    Args:
        metadata: dict with _input_type, _normalized_id, DOI, _arxiv_id, title, etc.
        config: loaded config dict (staging_dir, semantic_scholar_api_key, etc.)
        accept_short: pass through to verifier

    Returns:
        dict with keys:
          found: bool
          path: str or None (path to verified PDF)
          version: "published", "author_copy", "preprint", or None
          verified: bool
          source: str or None
          reason: str (if not found)
    """
    input_type = metadata.get("_input_type", "doi")
    doi = metadata.get("DOI", "")
    arxiv_id = metadata.get("_arxiv_id", "")
    normalized = metadata.get("_normalized_id", "")
    staging = config["staging_dir"]
    os.makedirs(staging, exist_ok=True)

    sources = _build_source_chain(input_type, doi, arxiv_id, normalized, config)

    for source_fn, version_tag, source_label in sources:
        _progress(f"Trying {source_label}...")
        try:
            pdf_path = source_fn(staging, metadata)
        except Exception as e:
            _progress(f"{source_label} failed: {e}")
            continue

        if not pdf_path or not os.path.exists(pdf_path):
            continue

        # Verify
        source_type = "semantic_scholar" if "Semantic Scholar" in source_label else "getscipapers"
        result = verify(pdf_path, metadata=metadata, source_type=source_type, accept_short=accept_short)

        if result["status"] == "reject":
            _progress(f"{source_label}: rejected — {result['reason']}")
            _safe_remove(pdf_path)
            continue

        return {
            "found": True,
            "path": pdf_path,
            "version": version_tag,
            "verified": result["status"] == "accept",
            "source": source_label,
            "reason": result["reason"],
            "page_count": result.get("page_count"),
        }

    return {
        "found": False, "path": None, "version": None,
        "verified": False, "source": None,
        "reason": "All download sources exhausted",
    }


def _build_source_chain(input_type, doi, arxiv_id, normalized, config):
    """Build ordered list of (download_fn, version_tag, label) based on input type."""
    chain = []

    if input_type == "doi":
        if doi:
            chain.append((
                lambda s, m: _getscipapers_doi(s, doi),
                "published", f"getscipapers --doi {doi}"
            ))
            chain.append((
                lambda s, m: _semantic_scholar(s, doi, config),
                "author_copy", f"Semantic Scholar (DOI:{doi})"
            ))
        if arxiv_id:
            chain.append((
                lambda s, m: _getscipapers_arxiv(s, arxiv_id),
                "preprint", f"getscipapers --arxiv {arxiv_id}"
            ))
            chain.append((
                lambda s, m: _arxiv_direct(s, arxiv_id),
                "preprint", f"arXiv direct (https://arxiv.org/pdf/{arxiv_id})"
            ))

    elif input_type == "isbn":
        chain.append((
            lambda s, m: _getscipapers_isbn(s, normalized),
            "published", f"getscipapers --isbn {normalized}"
        ))

    elif input_type == "arxiv":
        chain.append((
            lambda s, m: _getscipapers_arxiv(s, normalized),
            "preprint", f"getscipapers --arxiv {normalized}"
        ))
        chain.append((
            lambda s, m: _arxiv_direct(s, normalized),
            "preprint", f"arXiv direct (https://arxiv.org/pdf/{normalized})"
        ))
        if doi and not doi.startswith("10.48550/arXiv"):
            chain.append((
                lambda s, m: _getscipapers_doi(s, doi),
                "published", f"getscipapers --doi {doi}"
            ))
            chain.append((
                lambda s, m: _semantic_scholar(s, doi, config),
                "author_copy", f"Semantic Scholar (DOI:{doi})"
            ))

    return chain


def _staging_path(staging_dir, identifier):
    """Generate unique staging filename."""
    h = hashlib.sha256(identifier.encode()).hexdigest()[:12]
    ts = int(time.time())
    return os.path.join(staging_dir, f"{h}_{ts}.pdf")


def _getscipapers_doi(staging_dir, doi):
    """Download via getscipapers --doi."""
    return _run_getscipapers(staging_dir, ["--doi", doi], doi)


def _getscipapers_arxiv(staging_dir, arxiv_id):
    """Download via getscipapers --arxiv."""
    return _run_getscipapers(staging_dir, ["--arxiv", arxiv_id], arxiv_id)


def _getscipapers_isbn(staging_dir, isbn):
    """Download via getscipapers --isbn."""
    return _run_getscipapers(staging_dir, ["--isbn", isbn], isbn)


def _run_getscipapers(staging_dir, args, identifier):
    """Run getscipapers getpapers and return path to downloaded PDF or None."""
    output_path = _staging_path(staging_dir, identifier)
    cmd = ["getscipapers", "getpapers"] + args + ["--output", output_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return output_path
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        _progress(f"getscipapers error: {e}")
    return None


def _semantic_scholar(staging_dir, doi, config):
    """Search Semantic Scholar for open access PDF."""
    api_key = config.get("semantic_scholar_api_key", "")
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    try:
        url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=openAccessPdf,externalIds"
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None

        data = resp.json()
        oa_pdf = data.get("openAccessPdf")
        if not oa_pdf or not oa_pdf.get("url"):
            return None

        pdf_url = oa_pdf["url"]
        output_path = _staging_path(staging_dir, doi + "_ss")

        # Download the PDF
        pdf_resp = requests.get(pdf_url, timeout=60, stream=True,
                                headers={"User-Agent": "zot-cli/1.0 (research tool)"})
        if pdf_resp.status_code != 200:
            return None

        with open(output_path, "wb") as f:
            for chunk in pdf_resp.iter_content(8192):
                f.write(chunk)

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return output_path

    except Exception as e:
        _progress(f"Semantic Scholar error: {e}")

    return None


def _arxiv_direct(staging_dir, arxiv_id):
    """Download PDF directly from arXiv."""
    output_path = _staging_path(staging_dir, f"arxiv_{arxiv_id}")
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    try:
        resp = requests.get(pdf_url, timeout=60, stream=True,
                            headers={"User-Agent": "zot-cli/1.0 (research tool)"})
        if resp.status_code != 200:
            return None
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return output_path
    except Exception as e:
        _progress(f"arXiv direct download error: {e}")
    return None


def _safe_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _progress(msg):
    print(f"[{msg}]", file=sys.stderr)
