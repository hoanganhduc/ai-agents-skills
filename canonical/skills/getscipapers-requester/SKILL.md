---
name: getscipapers-requester
description: Use for external DOI/ISBN/title resolution, manifest creation from pasted text, and paper retrieval after the local library-first workflow does not satisfy the request or the user explicitly opts out of library use.
metadata:
  short-description: External paper retrieval fallback
---

# GetSciPapers Requester


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/getscipapers_requester/run_gsp_helper.bat" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

This is the external retrieval fallback. Do not use it before `zotero` for
normal paper/library requests, and for review tasks that need a paper/book do
not use it before both `zotero` and `calibre` have been checked. "Download" by
itself does not bypass the library-first workflow; bypass only when the user
explicitly says not to check/use the library or confirms outside retrieval after
the local miss/ambiguity is shown.

## When to use

- The paper is not in Zotero
- and, for review tasks, it is also not in Calibre
- The user explicitly says not to check/use the library, or confirms external
  retrieval after the library-first result is reported
- The task is DOI/ISBN/title resolution from external sources
- The user pasted many identifiers and wants batch retrieval

## Base path

- `$AAS_RUNTIME_WORKSPACE/skills/getscipapers_requester/`

Use the managed runtime runner rather than invoking `run_gsp_helper.sh` directly. The runner
sets `OPENCLAW_WORKSPACE`, `PYTHONPATH`, secrets, and workspace-local binaries.

Shared runner:

- `bash "$AAS_RUNTIME_ROOT/run_skill.sh"`

## Core commands

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/getscipapers_requester/run_gsp_helper.sh run-getscipapers --timeout 180 -- getpapers --doi <DOI>
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/getscipapers_requester/run_gsp_helper.sh resolve auto "<title>" --best
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/getscipapers_requester/run_gsp_helper.sh make-manifest auto "<text-or-file>"
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/getscipapers_requester/run_gsp_helper.sh doctor
```

## Z-Library and DJVU fallback

Use the zlib backend only as an external fallback for books or papers after the
library-first route has not satisfied the request, or when the user explicitly
asks for zlib/external retrieval. For review tasks, check `zotero` and then
`calibre` first unless the user explicitly opts out.

Before relying on the backend, check the local command help:

```bash
getscipapers zlib --help
```

Search before downloading:

```bash
getscipapers zlib --no-proxy --search "<title-or-isbn>" --search-limit 10
```

Download only after an exact match is established or the user selects a
numbered candidate. Do not print zlib configuration values or credentials; when
diagnosing configuration, report only file existence and key names. If the
downloaded file is DJVU, do not attach it to Zotero automatically unless the
user explicitly asks for that attachment. For "send it to me" requests, verify
the file first and send the verified file directly through the requested
channel.

For DJVU verification and reading, prefer DjVuLibre tools:

```bash
sudo apt-get install -y djvulibre-bin
djvused -e n book.djvu
djvudump book.djvu | sed -n '1,80p'
djvutxt book.djvu book.txt
djvutxt -page=<page> book.djvu page.txt
ddjvu -format=tiff -page=<page> book.djvu page.tiff
```

Use `djvudump` for structure, `djvused -e n` for page count, `djvutxt` or
`djvused -e 'print-pure-txt'` for hidden text, and `ddjvu` for rendering or
conversion. If Calibre is useful for conversion or metadata, use it as a
fallback rather than the first verifier; on hosts with user-site Python package
conflicts, run it with:

```bash
PYTHONNOUSERSITE=1 ebook-convert book.djvu book.txt
```

If the DJVU has no usable hidden text layer, render pages with `ddjvu` and then
run OCR with `tesseract` or `ocrmypdf` when available. Avoid treating `mutool`,
PIL, or generic ImageMagick probing as primary DJVU verification paths; they may
fail or hang depending on local delegates. ImageMagick is still useful after a
page has been rendered to TIFF/PNG.

## Workflow

1. If DOI/ISBN is available, use it directly.
2. Otherwise resolve from title or text.
3. When title/text resolution returns multiple plausible matches, show the
   numbered candidates with title, authors, and year when available, then wait
   for the user's selected index before using `--best`, retrieval, attachment,
   send, or review steps. Exact DOI/ISBN requests do not need this
   disambiguation.
4. For many papers, create a manifest first.
5. For large batches, prefer dry-run style validation first.
6. If retrieval fails, report the failure precisely instead of hand-waving.
