#!/usr/bin/env python3
"""Stdlib-only client for the Open Math OS model gateway.

The client talks to one fixed origin and never follows a redirect or a proxy.
Every outbound message is screened for credentials and email addresses before
any network I/O. Every remote byte is returned as untrusted data with its
provenance. A metadata-only send log and a durable budget ledger run on the
client side. A machine-wide request limiter, budget caps, retries and a circuit
breaker are available, but each applies only when its `ask` flag sets it: the
client sets no limit of its own (user decision, 2026-11-13). The model key
cannot read the shared team quota.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import email.utils
import getpass
import hashlib
import hmac
import http.client
import json
import math
import os
import random
import re
import secrets
import socket
import ssl
import stat
import sys
import threading
import time
import types
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover - native Windows
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]


VERSION = "0.1.0"
API_ORIGIN = "https://os.openmathmodel.org"
API_PREFIX = "/v1"
USER_AGENT = f"ai-agents-skills-open-math-os/{VERSION}"

KEY_ENV = "OPENMATH_API_KEY"
KEY_PREFIX = "nuc_ak_"
PLATFORM_KEY_ENV = "OPENMATH_PLATFORM_KEY"
PLATFORM_KEY_PREFIX = "nuc_oc_"
KEY_FILE_NAME = "openmath.env"
KEY_BODY_RE = re.compile(r"[A-Za-z0-9._~+/=-]{16,512}\Z")

PROVENANCE = {
    "provider_id": "openmathos",
    "provenance_label": "unverified-gateway:openmathos",
    "family": "unverified",
    "independence_eligible": False,
}
TRUST_LABEL = "untrusted-remote-output"

MODEL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
EFFORT_NAME_RE = re.compile(r"[a-z]{1,16}\Z")
LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
ERROR_CODE_RE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
FINISH_REASON_RE = re.compile(r"[a-z_]{1,32}\Z")
CLOUDFLARE_CODE_RE = re.compile(r"error code:\s*([0-9]{3,5})", re.IGNORECASE)

# The reasoning efforts that the gateway's catalog named on 2026-11-07.
EFFORT_NAMES = ("low", "medium", "high", "xhigh", "max")

# Bounds the agent chose (SKILL.md, "Bounds the agent chose"). They cap only
# what the client reads, keeps or prints, never a request.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_SSE_LINE_BYTES = 1024 * 1024
MAX_ERROR_BODY_BYTES = 64 * 1024
MAX_INPUT_FILE_BYTES = 1024 * 1024
MAX_SECRET_SOURCE_BYTES = 1024 * 1024
LIMITER_POLL_S = 0.5

# The windows of --day-max-tokens and --week-max-tokens.
DAY_S = 86400
WEEK_S = 7 * 86400


@dataclasses.dataclass(frozen=True)
class Limits:
    """The optional limits of `ask`. None has a default: each applies only when set."""

    deadline_s: float | None = None
    idle_timeout_s: float | None = None
    metadata_timeout_s: float | None = None
    max_tokens: int | None = None
    catalog_ttl_s: float | None = None
    max_inflight: int | None = None
    slot_wait_s: float | None = None
    run_max_requests: int | None = None
    run_max_tokens: int | None = None
    day_max_tokens: int | None = None
    week_max_tokens: int | None = None
    quota_floor_usd: float | None = None
    quota_max_age_s: float | None = None
    retries: int | None = None
    retry_after_max_s: float | None = None
    backoff_base_s: float | None = None
    backoff_cap_s: float | None = None
    breaker_threshold: int | None = None
    breaker_window_s: float | None = None
    breaker_open_s: float | None = None

    @property
    def breaker(self) -> bool:
        """The breaker runs only when its three flags are given together."""

        return self.breaker_threshold is not None


# Every optional limit of `ask`: flag, Limits field, type, metavar and help.
# None has a default (user decision, 2026-11-13).
LIMIT_FLAGS = (
    ("--deadline", "deadline_s", float, "SECONDS",
     "stop an attempt after SECONDS in total"),
    ("--idle-timeout", "idle_timeout_s", float, "SECONDS",
     "stop a streamed answer that sends nothing for SECONDS"),
    ("--metadata-timeout", "metadata_timeout_s", float, "SECONDS",
     "socket timeout of the catalog request"),
    ("--max-tokens", "max_tokens", int, "N",
     "send max_completion_tokens=N; without it the request carries none"),
    ("--catalog-ttl", "catalog_ttl_s", float, "SECONDS",
     "reuse a cached catalog younger than SECONDS; without it the catalog is read live"),
    ("--max-inflight", "max_inflight", int, "N",
     "at most N requests in flight on the machine, among processes that pass this flag"),
    ("--slot-wait", "slot_wait_s", float, "SECONDS",
     "with --max-inflight, wait at most SECONDS for a slot"),
    ("--run-max-requests", "run_max_requests", int, "N",
     "at most N requests per run"),
    ("--run-max-tokens", "run_max_tokens", int, "N",
     "at most N tokens per run"),
    ("--day-max-tokens", "day_max_tokens", int, "N",
     "at most N tokens of all runs over a rolling 24 hours"),
    ("--week-max-tokens", "week_max_tokens", int, "N",
     "at most N tokens of all runs over a rolling 7 days"),
    ("--quota-floor", "quota_floor_usd", float, "USD",
     "refuse a send when quota.json shows less than USD left in a team window"),
    ("--quota-max-age", "quota_max_age_s", float, "SECONDS",
     "with --quota-floor, ignore a quota.json older than SECONDS"),
    ("--retries", "retries", int, "N",
     "retry a failed attempt at most N times"),
    ("--retry-after-max", "retry_after_max_s", float, "SECONDS",
     "with --retries, stop instead of honouring a Retry-After longer than SECONDS"),
    ("--backoff-base", "backoff_base_s", float, "SECONDS",
     "with --retries and no Retry-After, wait a random time up to SECONDS, doubled per retry; "
     "without it a retry follows at once"),
    ("--backoff-cap", "backoff_cap_s", float, "SECONDS",
     "cap the wait of --backoff-base at SECONDS"),
    ("--breaker-threshold", "breaker_threshold", int, "N",
     "with --breaker-window and --breaker-open: N rate-limit responses within the window "
     "stop every process that passes these flags"),
    ("--breaker-window", "breaker_window_s", float, "SECONDS",
     "the window of --breaker-threshold"),
    ("--breaker-open", "breaker_open_s", float, "SECONDS",
     "how long an opened breaker stops sends"),
)

SENDS_LOG = "sends.jsonl"
SENDS_MAX_BYTES = 1024 * 1024
SENDS_KEEP = 3
BUDGET_LEDGER = "budget.jsonl"
BUDGET_MAX_BYTES = 16 * 1024 * 1024
BUDGET_COMPACT_BYTES = 1024 * 1024
CATALOG_FILE = "catalog.json"
BREAKER_FILE = "breaker.json"
HARD_QUOTA_MARKER = "hard_quota.json"
QUOTA_SNAPSHOT = "quota.json"
STATE_LOCK = "state.lock"
SMALL_STATE_BYTES = 256 * 1024

ORCHESTRATOR_LABEL_PREFIXES = ("agd:", "arl:")
# The ARL run markers reach the client only on a direct launch: under
# run_skill.sh the loader drops them, and the runner sets
# AAS_OPENMATH_ARL_LAUNCH=1 in their place.
ORCHESTRATOR_ENV_MARKERS = ("AAS_AUTOLOOP_RUN_ID", "AAS_ARL_BROKER_SOCKET", "AAS_OPENMATH_ARL_LAUNCH")
# Under run_skill.sh the loader drops every email-bearing variable, so the
# runner, and the wrapper on a direct launch, pass their values for screening
# as NAME=value lines under this one name.
SCREEN_VALUES_ENV = "AAS_OPENMATH_SCREEN_VALUES"
# The managed loader lists the st_dev:st_ino identities of the secret files it
# projected, comma-separated. An input file with one of them is refused.
PROTECTED_SECRET_IDENTITIES_ENV = "AAS_PROTECTED_SECRET_FILE_IDS"

# Exit codes. The quota, hard-quota and auth codes follow
# canonical/instructions/provider-credit-quota.md; the rest are local to this
# client and documented in references/api-contract.md.
EXIT_CODES = {
    "internal": 1,
    "usage": 2,
    "quota": 5,
    "edge_block": 6,
    "auth": 7,
    "bad_request": 11,
    "client_bug": 12,
    "transient": 13,
    "budget": 14,
    "blocked": 15,
    "state": 16,
    "hard_quota": 18,
}

# Patchable clock and sleep, so tests never wait for real backoff.
_wall = time.time
_mono = time.monotonic
_sleep = time.sleep


# ---------------------------------------------------------------------------
# Outbound screening.
#
# Vendored from canonical/runtime/skills/autonomous-research-loop-runtime/
# panel_parent.py. tests/test_open_math_os.py fails when the copies drift.
# ---------------------------------------------------------------------------

PANEL_SECRET_ENV_NAME = re.compile(
    r"(?:API[_-]?KEY|AUTH|BEARER|COOKIE|CREDENTIAL|OAUTH|PASS(?:WORD|WD)?|PRIVATE[_-]?KEY|SECRET|SESSION|TOKEN|OPENMATH_PLATFORM_KEY)",
    re.IGNORECASE,
)
PANEL_SECRET_CONTENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private-key-block",
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    ),
    (
        "credential-assignment",
        re.compile(
            r"(?i)(?:api[_-]?key|auth(?:orization)?|bearer|cookie|credential|oauth|pass(?:word|wd)?|private[_-]?key|secret|session|token)"
            r"\s*[=:]\s*[\"']?[A-Za-z0-9_./+@:-]{8,}"
        ),
    ),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github-token", re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[a-z]-[A-Za-z0-9-]{12,}\b")),
    (
        "embedded-url-credential",
        re.compile(r"(?i)https?://[^\s/:@]{1,128}:[^\s/@]{6,128}@"),
    ),
)
PANEL_PII_CONTENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "email",
        re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}\b"),
    ),
    (
        "phone",
        # Only phone-shaped digit groups count: a leading +country code, a
        # parenthesized area code, or separators around the middle group.
        # Bare digit runs stay unflagged — research payloads are full of
        # counts, seeds, and timing floats that a looser pattern rejects,
        # which blocked review prompts and redacted provider diagnostics.
        re.compile(
            r"(?<![A-Za-z0-9])(?:"
            r"\+\d{1,3}[ .-]?\(?\d{2,4}\)?[ .-]?\d{3}[ .-]?\d{4}"
            r"|\(\d{2,4}\)[ .-]?\d{3}[ .-]?\d{4}"
            r"|\d{2,4}[ .-]\d{3}[ .-]\d{4}"
            r")(?![A-Za-z0-9])"
        ),
    ),
    (
        "government-id",
        re.compile(
            r"(?i)\b(?:ssn|social security|passport|national id|government id|driver'?s license)"
            r"\s*(?:number|no\.?|id)?\s*[:=]\s*[^\s,;]{4,}"
        ),
    ),
    (
        "person-record",
        re.compile(
            r"(?i)\b(?:participant|patient|research[ _-]?subject|data[ _-]?subject|subject)"
            r"(?:[ _-]?(?:id|name|address|dob|date of birth|birth date))?"
            r"\s*[:=]\s*[^\n]{2,}"
        ),
    ),
    (
        "labeled-personal-data",
        re.compile(
            r"(?i)\b(?:full[ _-]?name|contact[ _-]?name|home[ _-]?address|"
            r"street[ _-]?address|date of birth|birth date|dob)\s*[:=]\s*[^\n]{2,}"
        ),
    ),
)
# The user forbids sending their email address anywhere, so an email match
# blocks the send. The other PII categories are reported and logged but not
# blocked: prompts are otherwise sent freely, and digit groups in mathematics
# can look like phone numbers.
PANEL_EMAIL_PATTERN = dict(PANEL_PII_CONTENT_PATTERNS)["email"]

# Credential-like names for the client's own patterns: the vendored names, "API
# key" with a space, and names that end in _KEY, CREDENTIALS or PASSPHRASE.
_CREDENTIAL_NAME = (
    r"(?:api[ _-]?key|[_-]key|auth(?:orization)?|bearer|cookie|credentials?|oauth|pass(?:word|wd|phrase)?"
    r"|private[_-]?key|secret|session|token)"
)
# Patterns this client adds on top of the vendored set. The token shapes are
# boundary-anchored. The extended credential assignment also takes a numbered
# name (HCLOUD_TOKEN_2), a closing quote, backslash-escaped as in a notebook cell
# or a JSON string, or a backtick after the name, a subscript bracket, Markdown
# bold, "=>" and an auth scheme word: JSON, a dict repr, os.environ["X"] = "v",
# `X`: `v` and "Authorization": "Bearer v". A .netrc entry may span lines and
# give its tokens in any order.
EXTRA_SECRET_CONTENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openmath-key", re.compile(r"(?<![A-Za-z0-9])nuc_(?:ak|oc)_[A-Za-z0-9_-]{16,}")),
    ("sk-key", re.compile(r"(?<![A-Za-z0-9])sk-(?:ant-|proj-|px-)?[A-Za-z0-9_-]{20,}")),
    ("github-pat", re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{20,}")),
    ("authorization-header", re.compile(r"(?i)\bauthorization:\s*(?:basic|bearer)\b")),
    ("pgp-private-key-block", re.compile(r"-----BEGIN PGP PRIVATE KEY BLOCK-----")),
    ("google-api-key", re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}")),
    ("huggingface-token", re.compile(r"(?<![A-Za-z0-9])hf_[A-Za-z0-9]{30,}")),
    (
        "jwt",
        re.compile(
            r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
        ),
    ),
    (
        "credential-assignment-extended",
        re.compile(
            r"(?i)" + _CREDENTIAL_NAME + r"(?:_[0-9]{1,2})?(?:\\*[\"'`])?\]?\**\s*(?:=>|[=:])\**\s*(?:\\*[\"'`])?"
            r"(?:(?:basic|bearer|token)\s+)?[A-Za-z0-9_./+@:-]{8,}"
        ),
    ),
    (
        "credential-env-call",
        re.compile(
            r"(?i)\b(?:putenv|setdefault|setenv)\(\s*\\*[\"'][A-Za-z0-9_]*" + _CREDENTIAL_NAME
            + r"\\*[\"']\s*,\s*\\*[\"'][A-Za-z0-9_./+@:-]{8,}"
        ),
    ),
    (
        "credential-flag",
        re.compile(
            r"(?i)(?<![A-Za-z0-9_-])--(?:[a-z0-9]+[_-])*(?:api[_-]?key|key|passphrase|passwd|password|secret|token)"
            r"[ \t]+[\"']?[A-Za-z0-9_./+@:-]{8,}"
        ),
    ),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_.~+/-]{20,}")),
    (
        "netrc-credential",
        re.compile(
            r"(?i)\b(?:machine\s+\S+(?:\s+(?:account|login|port)\s+\S+){0,3}"
            r"|default(?:\s+(?:account|login|port)\s+\S+){1,3})\s+password\s+\S+"
        ),
    ),
)
# Variables whose values carry an email address. The vendored environment rule
# only looks at credential-like names, so these get their own exact match.
EMAIL_BEARING_ENV_NAME = re.compile(r"EMAIL|^SMTP_(?:FROM|USER|REPLY_TO|CC|BCC)$", re.IGNORECASE)


def panel_prompt_secret_findings(prompt: str, *, environ: Mapping[str, str]) -> list[str]:
    """Return non-secret labels for credential material (vendored rule)."""

    findings: set[str] = set()
    for name, raw_value in environ.items():
        value = str(raw_value or "")
        if PANEL_SECRET_ENV_NAME.search(str(name)) and len(value) >= 6 and value in prompt:
            findings.add(f"environment:{name}")
    for label, pattern in PANEL_SECRET_CONTENT_PATTERNS:
        if pattern.search(prompt):
            findings.add(f"content:{label}")
    return sorted(findings)


_EMAIL_TOKEN_SPLIT = re.compile(r"[\s<>,;\"'()]+")
# The local-part class of the email pattern, and a bare word boundary.
_EMAIL_LOCAL_CHAR = re.compile(r"(?i)[A-Z0-9._%+-]")
_WORD_BOUNDARY = re.compile(r"\b")


def email_matches(text: str) -> Iterator[re.Match[str]]:
    """Yield the matches of ``PANEL_EMAIL_PATTERN.finditer(text)``, in linear time.

    A plain search retries the pattern from every word boundary of a long
    unspaced run such as ``x0+x1+...+xn``, which takes quadratic time. Every
    match holds one '@', and its local part ends just before that '@' inside
    one run of local-part characters. Every start in that run reaches the same
    '@' and then needs the same domain, so one attempt at the run's first word
    boundary decides each '@'. ``floor`` keeps matches apart, as finditer does.
    """

    floor = 0
    at = text.find("@")
    while at != -1:
        start = at
        while start > floor and _EMAIL_LOCAL_CHAR.match(text, start - 1):
            start -= 1
        match = None
        if start < at:
            boundary = _WORD_BOUNDARY.search(text, start, at)
            if boundary is not None and boundary.start() < at:
                match = PANEL_EMAIL_PATTERN.match(text, boundary.start())
        if match is not None:
            yield match
            floor = match.end()
        at = text.find("@", max(at + 1, floor))


def _email_candidates(value: str) -> set[str]:
    """Addresses inside one email-bearing value, including non-ASCII ones.

    Each address is kept case-folded, and also NFKC-normalized before the fold, the
    way the normalized copy is, because the two orders differ for some letters
    (casefold decomposes U+0390, which NFKC composes again).
    """

    addresses = {match.group(0) for match in email_matches(value)}
    for token in _EMAIL_TOKEN_SPLIT.split(value):
        if "@" in token and len(token) >= 6:
            addresses.add(token)
    return {form for address in addresses
            for form in (address.casefold(), unicodedata.normalize("NFKC", address).casefold())}


def email_bearing_values(environ: Mapping[str, str]) -> list[tuple[str, str]]:
    """Email-bearing (name, value) pairs: direct variables plus the runner's list."""

    pairs = [
        (str(name), str(value))
        for name, value in environ.items()
        if name != SCREEN_VALUES_ENV and value and EMAIL_BEARING_ENV_NAME.search(str(name))
    ]
    for line in str(environ.get(SCREEN_VALUES_ENV) or "").split("\n"):
        name, separator, value = line.partition("=")
        if separator and value and EMAIL_BEARING_ENV_NAME.search(name):
            pairs.append((name, value))
    return pairs


def _drop_format_characters(text: str) -> str:
    """Drop Unicode format characters (category Cf), such as the zero-width space."""

    if text.isascii():  # no ASCII character is in category Cf
        return text
    return "".join(character for character in text if unicodedata.category(character) != "Cf")


_WHITESPACE_RUN = re.compile(r"\s+")


def _canonical_copy(text: str) -> str:
    """NFKC, without format characters or whitespace, casefolded.

    The exact ``OPENMATH_*`` value check also runs on this copy, so a key that
    is hard-wrapped, split by a zero-width space or upper-cased is still found.
    """

    return _WHITESPACE_RUN.sub("", _drop_format_characters(unicodedata.normalize("NFKC", text))).casefold()


# The email screen also runs on a normalized copy of each message, which undoes
# the forms that references/api-contract.md lists. Deliberate encodings
# (homoglyphs, base64, a reversed or spelled-out address) are not undone.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")  # Cc except tab, LF and CR
_ENCODED_AT = re.compile(r"(?i)%40|&#0*64;|&#x0*40;|&commat;|\\u0040")
# [at], (at) or {at}, and [dot], with blanks or line breaks around them. The
# whitespace before the bracket is taken only from the start of its run, so a
# long run is scanned once rather than once per position.
_BRACKETED_AT = re.compile(r"(?i)(?:(?<!\s)\s+)?[\[({]\s*at\s*[\])}]\s*")
_BRACKETED_DOT = re.compile(r"(?i)(?:(?<!\s)\s+)?[\[({]\s*dot\s*[\])}]\s*")
# A grouped local part, "{a,b}@d" as in LNCS, "$\}$@d" in LaTeX source, with
# one line break allowed on each side of '@': the brace becomes a local-part
# character, so the address after it is found.
_GROUP_CLOSE_AT = re.compile(r"\}\$?[ \t]*(?:\r?\n[ \t]*)?@[ \t]*(?:\r?\n[ \t]*)?")
# An address with blanks or one line break around '@' ("ada @ example.org",
# "ada\n@ example.org", "ada@ example.org"), or with line breaks after dots in
# its domain, where \url breaks ("ada@example.\norg"); the whitespace is
# removed. Two local-part characters come before '@', and a blank or break
# before '@' needs one after it, so "x @ W", Lean's "exact @Nat.rec" and a
# decorator on the next line are left alone. The domain ends in a two- to
# four-letter lowercase label that no call, index or attribute follows, so the
# matrix products "evecs @ np.diag(w)" and "hidden @ self.weight" stay code.
_GAPPED_ADDRESS = re.compile(
    r"(?<=[A-Za-z0-9._%+-]{2})(?:[ \t]*(?:\r?\n[ \t]*)?@(?=[ \t\r\n])[ \t]*(?:\r?\n[ \t]*)?|@)"
    r"(?:[A-Za-z0-9-]+\.(?:[ \t]*\r?\n[ \t]*)?)+[a-z]{2,4}(?![A-Za-z0-9_(\[]|\.[A-Za-z0-9])"
)
_BREAK_AFTER_AT = re.compile(r"@[ \t]*\r?\n[ \t]*")
# A footnote mark glued after a two- to four-letter top-level label,
# "ada@example.org1" or "ada@example.org¹" (a digit after NFKC), is split off.
_FOOTNOTE_MARK = re.compile(r"(?<=[A-Za-z0-9._%+-]{2}@)((?:[A-Za-z0-9-]+\.)+[a-z]{2,4})(?=[0-9]{1,2}(?![A-Za-z0-9]))")
# A non-ASCII letter glued before the local part or after the domain removes the
# word boundary the email pattern needs. Only the pattern check sees a space there.
_GLUED_LETTER = re.compile(r"(?<=[^\W\x00-\x7f])(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])(?=[^\W\x00-\x7f])")


def _email_screen_copy(text: str) -> str:
    """The normalized copy of one message that the email screen also checks."""

    copy = _CONTROL_CHARACTERS.sub("", _drop_format_characters(unicodedata.normalize("NFKC", text)))
    copy = _ENCODED_AT.sub("@", copy)
    copy = _BRACKETED_DOT.sub(".", _BRACKETED_AT.sub("@", copy))
    copy = _GROUP_CLOSE_AT.sub("x@", copy)
    copy = _GAPPED_ADDRESS.sub(lambda match: _WHITESPACE_RUN.sub("", match.group()), copy)
    return _FOOTNOTE_MARK.sub(r"\1 ", _BREAK_AFTER_AT.sub("@", copy))


def screen_text(
    text: str,
    environ: Mapping[str, str],
    email_candidates: Iterable[tuple[str, set[str]]] | None = None,
) -> set[str]:
    findings = set(panel_prompt_secret_findings(text, environ=environ))
    for label, pattern in EXTRA_SECRET_CONTENT_PATTERNS:
        if pattern.search(text):
            findings.add(f"content:{label}")
    if next(email_matches(text), None) is not None:
        findings.add("pii:email")
    for name, raw_value in environ.items():
        value = str(raw_value or "")
        if str(name).startswith("OPENMATH_") and len(value) >= 6 and value in text:
            findings.add(f"environment:{name}")
    if email_candidates is None:
        email_candidates = [(name, _email_candidates(value)) for name, value in email_bearing_values(environ)]
    folded = text.casefold()
    for name, candidates in email_candidates:
        if any(candidate in folded for candidate in candidates):
            findings.add(f"environment:{name}")
    copy = _email_screen_copy(text)
    if next(email_matches(_GLUED_LETTER.sub(" ", copy)), None) is not None:
        findings.add("pii:email")
    folded_copy = copy.casefold()
    for name, candidates in email_candidates:
        if any(form in folded_copy for candidate in candidates
               for form in (candidate, unicodedata.normalize("NFKC", candidate))):
            findings.add(f"environment:{name}")
    return findings


def screen_outbound(messages: Iterable[Mapping[str, Any]], environ: Mapping[str, str]) -> list[str]:
    """Screen every outbound message: system prompt, history and prompt."""

    candidates = [(name, _email_candidates(value)) for name, value in email_bearing_values(environ)]
    findings: set[str] = set()
    canonical: list[str] = []
    for message in messages:
        content = str(message.get("content") or "")
        findings |= screen_text(content, environ, candidates)
        canonical.append(_canonical_copy(content))
    # One canonical copy of all messages joined holds each message's copy, and
    # a key split where one message ends and the next begins.
    joined = "".join(canonical)
    for name, raw_value in environ.items():
        if str(name).startswith("OPENMATH_"):
            value = _canonical_copy(str(raw_value or ""))
            if len(value) >= 6 and value in joined:
                findings.add(f"environment:{name}")
    return sorted(findings)


def report_outbound_pii(messages: Iterable[Mapping[str, Any]]) -> list[str]:
    """Labels of the PII categories other than email found in the messages.

    These never block the send. The caller reports them on stderr and in the
    send log, so the metadata log still shows what left the machine.
    """

    found: set[str] = set()
    for message in messages:
        content = str(message.get("content") or "")
        for label, pattern in PANEL_PII_CONTENT_PATTERNS:
            if label != "email" and pattern.search(content):
                found.add(f"pii:{label}")
    return sorted(found)


# ---------------------------------------------------------------------------
# Untrusted output: terminal control stripping.
# ---------------------------------------------------------------------------

_ESCAPE_MAX = 256


class TerminalSanitizer:
    """Drop ANSI CSI and OSC sequences, other escapes, and C0/C1 controls.

    Newline and tab survive. The state carries across ``feed`` calls, so an
    escape sequence split between two stream chunks is still removed.
    """

    def __init__(self) -> None:
        self._state = "text"
        self._consumed = 0

    def feed(self, text: str) -> str:
        out: list[str] = []
        for char in text:
            code = ord(char)
            state = self._state
            if state == "text":
                if char in "\n\t":
                    out.append(char)
                elif char == "\x1b":
                    self._enter("esc")
                elif code == 0x9B:
                    self._enter("csi")
                elif code in (0x90, 0x98, 0x9D, 0x9E, 0x9F):
                    self._enter("string")
                elif code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F:
                    pass
                else:
                    out.append(char)
                continue
            self._consumed += 1
            if self._consumed > _ESCAPE_MAX:
                self._state = "text"
                continue
            if state == "esc":
                if char == "[":
                    self._state = "csi"
                elif char in "]PX^_":
                    self._state = "string"
                elif 0x20 <= code <= 0x2F:
                    self._state = "esc_intermediate"
                else:
                    self._state = "text"
            elif state == "esc_intermediate":
                if not 0x20 <= code <= 0x2F:
                    self._state = "text"
            elif state == "csi":
                if 0x40 <= code <= 0x7E:
                    self._state = "text"
            elif state == "string":
                if char == "\x07" or code == 0x9C:
                    self._state = "text"
                elif char == "\x1b":
                    self._state = "string_esc"
            elif state == "string_esc":
                self._state = "text" if char == "\\" else "string"
        return "".join(out)

    def _enter(self, state: str) -> None:
        self._state = state
        self._consumed = 0


def strip_terminal_controls(text: str) -> str:
    return TerminalSanitizer().feed(text)


# ---------------------------------------------------------------------------
# Errors.
# ---------------------------------------------------------------------------


class OpenMathError(Exception):
    """A classified failure. ``code`` never carries remote prose or secrets."""

    def __init__(
        self,
        error_class: str,
        code: str,
        *,
        kind: str = "local",
        http_status: int | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
        bytes_received: int = 0,
        partial: "ChatResult | None" = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(f"{error_class}: {code}")
        if error_class not in EXIT_CODES:
            raise ValueError(f"unknown error class {error_class!r}")
        self.error_class = error_class
        self.code = code
        self.kind = kind
        self.http_status = http_status
        self.retryable = retryable
        self.retry_after = retry_after
        self.bytes_received = bytes_received
        self.partial = partial
        self.request_id = request_id
        self.catalog: Mapping[str, Any] | None = None

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.error_class]

    def stderr_line(self) -> str:
        if self.kind == "http" and self.http_status is not None:
            return f"HTTP {self.http_status} {self.error_class}: {self.code}"
        if self.kind == "transport":
            return f"HTTP 000 {self.error_class}: {self.code}"
        return f"refused {self.error_class}: {self.code}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "class": self.error_class,
            "code": self.code,
            "http_status": self.http_status,
            "exit_code": self.exit_code,
        }


def _safe_code(value: Any) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if isinstance(value, str) and ERROR_CODE_RE.fullmatch(value):
        return value
    return None


def _error_code(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        candidates: list[Any] = []
        error = payload.get("error")
        if isinstance(error, dict):
            candidates.extend([error.get("code"), error.get("type")])
        candidates.extend([payload.get("code"), payload.get("type")])
        for candidate in candidates:
            code = _safe_code(candidate)
            if code:
                return code
    match = CLOUDFLARE_CODE_RE.search(text)
    if match:
        return match.group(1)
    return "unknown"


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if text.isdigit():
        return float(int(text))
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, when.timestamp() - _wall())


def classify_http_error(
    status: int,
    body: bytes,
    retry_after_header: str | None = None,
    *,
    request_id: str | None = None,
) -> OpenMathError:
    """Map one non-success HTTP status onto the client's error classes."""

    code = _error_code(body)
    if status == 401:
        error_class = "auth"
    elif status == 402:
        error_class = "hard_quota"
    elif status == 403:
        cloudflare = CLOUDFLARE_CODE_RE.search(body.decode("utf-8", errors="replace"))
        if cloudflare:
            error_class, code = "edge_block", cloudflare.group(1)
        else:
            error_class = "auth"
    elif status == 429:
        error_class = "quota"
    elif status in (400, 404, 422):
        error_class = "bad_request"
    elif status == 405:
        error_class = "client_bug"
    elif status == 408 or 500 <= status <= 599:
        error_class = "transient"
    elif 300 <= status <= 399:
        error_class, code = "client_bug", "redirect_refused"
    elif 400 <= status <= 499:
        error_class = "bad_request"
    else:
        error_class = "client_bug"
    return OpenMathError(
        error_class,
        code,
        kind="http",
        http_status=status,
        retryable=error_class in ("quota", "transient"),
        retry_after=_parse_retry_after(retry_after_header),
        request_id=request_id,
    )


def _transport_code(exc: BaseException) -> str:
    reason: Any = getattr(exc, "reason", exc)
    if isinstance(reason, socket.timeout):
        return "timeout"
    if isinstance(reason, ssl.SSLError):
        return "tls_error"
    if isinstance(reason, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(reason, socket.gaierror):
        return "dns_error"
    if isinstance(reason, (ConnectionResetError, http.client.RemoteDisconnected)):
        return "connection_reset"
    if isinstance(reason, str) and "unknown url type" in reason:
        return "scheme_refused"
    return "connection_error"


def retry_delay(retry_after: float | None, attempt: int, limits: Limits) -> float | None:
    """Seconds to wait before a retry, or ``None`` when no retry is allowed."""

    if retry_after is not None:
        if limits.retry_after_max_s is not None and retry_after > limits.retry_after_max_s:
            return None
        return retry_after
    if limits.backoff_base_s is None:
        return 0.0
    bound = limits.backoff_base_s * (2 ** attempt)
    if limits.backoff_cap_s is not None:
        bound = min(limits.backoff_cap_s, bound)
    return random.uniform(0.0, bound)


# ---------------------------------------------------------------------------
# Paths and private local files.
# ---------------------------------------------------------------------------


def _home(environ: Mapping[str, str]) -> Path:
    home = str(environ.get("HOME") or "")
    if not home and os.name == "nt":
        home = str(environ.get("USERPROFILE") or "")
    if not home or not os.path.isabs(home):
        raise OpenMathError("state", "home_unavailable")
    return Path(home)


def state_dir(environ: Mapping[str, str]) -> Path:
    """State root, derived from HOME because credentialed launches drop XDG_*."""

    if os.name == "nt":
        base = str(environ.get("LOCALAPPDATA") or "")
        if not base or not os.path.isabs(base):
            raise OpenMathError("state", "localappdata_unavailable")
        return Path(base) / "ai-agents-skills" / "state" / "open-math-os"
    return _home(environ) / ".local" / "state" / "ai-agents-skills" / "open-math-os"


def config_dir(environ: Mapping[str, str]) -> Path:
    return _home(environ) / ".config" / "ai-agents-skills"


def claude_secrets_path(environ: Mapping[str, str]) -> Path:
    return _home(environ) / ".claude" / "secrets.json"


def managed_secret_locations(environ: Mapping[str, str]) -> list[Path]:
    """Credential stores that an input file is never read from."""

    home = _home(environ)
    return [
        config_dir(environ), claude_secrets_path(environ), home / ".claude" / ".credentials.json",
        home / ".codex" / "auth.json", home / ".openclaw" / "secrets.json", home / ".openclaw" / "openclaw.json",
        home / ".openclaw" / "credentials", home / ".ssh", home / ".gnupg", home / ".aws", home / ".docker",
        home / ".kaggle", home / ".netrc",
    ]


# Kernel views, refused by the path the kernel reports for the opened file only:
# /dev/stdin and /dev/fd/N pass through /proc on their way to an ordinary file,
# a here-document or a pipe.
KERNEL_VIEW_LOCATIONS = ("/proc", "/sys")


_REPARSE_POINT = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _is_reparse_point(info: os.stat_result) -> bool:
    return bool(int(getattr(info, "st_file_attributes", 0)) & _REPARSE_POINT)


def _windows_guard() -> Callable[..., Any]:
    # Native Windows only: the owner/DACL guard ships beside this module.
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    from windows_acl import private_path_guard

    return private_path_guard


@contextlib.contextmanager
def _guarded(path: Path, *, directory: bool) -> Iterator[None]:
    if os.name != "nt":
        yield
        return
    with _windows_guard()(path, directory=directory):
        yield


def _owner_private_dir(path: Path, *, repair: bool) -> None:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or _is_reparse_point(info):
        raise OpenMathError("state", "directory_not_a_real_directory")
    if os.name == "posix":
        if int(info.st_uid) != int(os.geteuid()):
            raise OpenMathError("state", "directory_foreign_owner")
        if stat.S_IMODE(info.st_mode) & 0o077:
            if not repair:
                raise OpenMathError("state", "directory_not_private")
            os.chmod(path, 0o700)
    else:
        with _guarded(path, directory=True):
            pass


def ensure_private_dir(path: Path, *, repair: bool = True) -> Path:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        _owner_private_dir(path, repair=repair)
    except OSError as exc:
        raise OpenMathError("state", "directory_unavailable") from exc
    return path


def _open_private_fd(path: Path, flags: int) -> int:
    """Open one owner-private, single-link regular file without following links."""

    flags |= (
        getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
    )
    if os.name == "nt":
        try:
            existing = os.lstat(path)
        except FileNotFoundError:
            existing = None
        if existing is not None and (_is_reparse_point(existing) or not stat.S_ISREG(existing.st_mode)):
            raise OpenMathError("state", "state_file_unsafe")
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            raise
        raise OpenMathError("state", "state_file_unsafe") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or int(getattr(info, "st_nlink", 1)) != 1:
            raise OpenMathError("state", "state_file_unsafe")
        if os.name == "posix":
            if int(info.st_uid) != int(os.geteuid()):
                raise OpenMathError("state", "state_file_foreign_owner")
            if stat.S_IMODE(info.st_mode) & 0o077:
                os.fchmod(descriptor, 0o600)
        else:
            with _guarded(path, directory=False):
                pass
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_fd(descriptor: int, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > max_bytes:
        raise OpenMathError("state", "state_file_oversized")
    return payload


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]


def _try_lock(descriptor: int, *, blocking: bool) -> bool:
    if os.name == "nt":
        if msvcrt is None:  # pragma: no cover - defensive
            raise OpenMathError("state", "locking_unavailable")
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        except OSError:
            if blocking:
                raise OpenMathError("state", "lock_timeout")
            return False
        return True
    if fcntl is None:  # pragma: no cover - defensive
        raise OpenMathError("state", "locking_unavailable")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock(descriptor: int) -> None:
    if os.name == "nt":
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc).timestamp()


class StateStore:
    """Owner-private state: send log, budget ledger, catalog, breaker, markers."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def ensure(self) -> None:
        ensure_private_dir(self.root)

    def exists(self) -> bool:
        return self.root.is_dir()

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        descriptor = _open_private_fd(self.root / STATE_LOCK, os.O_RDWR | os.O_CREAT)
        try:
            _try_lock(descriptor, blocking=True)
            try:
                yield
            finally:
                _unlock(descriptor)
        finally:
            os.close(descriptor)

    def read_bytes(self, name: str, max_bytes: int) -> bytes | None:
        try:
            descriptor = _open_private_fd(self.root / name, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        except FileNotFoundError:
            return None
        try:
            return _read_fd(descriptor, max_bytes)
        finally:
            os.close(descriptor)

    def read_json(self, name: str, max_bytes: int = SMALL_STATE_BYTES) -> Any:
        payload = self.read_bytes(name, max_bytes)
        if payload is None:
            return None
        try:
            return json.loads(payload.decode("utf-8"))
        except ValueError as exc:
            raise OpenMathError("state", "state_file_malformed") from exc

    def write_json(self, name: str, value: Any) -> None:
        payload = (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")
        temporary = self.root / f".{name}.{secrets.token_hex(8)}.tmp"
        descriptor = _open_private_fd(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            _write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self.root / name)

    def append_jsonl(self, name: str, record: Mapping[str, Any]) -> None:
        line = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
        descriptor = _open_private_fd(self.root / name, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            _write_all(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def read_jsonl(self, name: str, max_bytes: int) -> list[dict[str, Any]]:
        payload = self.read_bytes(name, max_bytes)
        rows: list[dict[str, Any]] = []
        for raw in (payload or b"").splitlines():
            try:
                row = json.loads(raw.decode("utf-8"))
            except ValueError:
                continue  # a torn final line after a crash
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def size(self, name: str) -> int:
        try:
            return int(os.lstat(self.root / name).st_size)
        except FileNotFoundError:
            return 0


# ---------------------------------------------------------------------------
# Send log, budget ledger, breaker, hard-quota marker, quota snapshot.
# ---------------------------------------------------------------------------


def _append_send(store: StateStore, record: Mapping[str, Any]) -> None:
    """Append one metadata-only record to sends.jsonl; the caller holds the lock."""

    line_bytes = len(json.dumps(record, sort_keys=True)) + 1
    if store.size(SENDS_LOG) + line_bytes > SENDS_MAX_BYTES:
        for index in range(SENDS_KEEP, 0, -1):
            source = SENDS_LOG if index == 1 else f"{SENDS_LOG}.{index - 1}"
            target = f"{SENDS_LOG}.{index}"
            if (store.root / source).exists():
                os.replace(store.root / source, store.root / target)
    store.append_jsonl(SENDS_LOG, record)


def log_send(store: StateStore, record: Mapping[str, Any]) -> None:
    """Append one metadata-only record to sends.jsonl, rotating by size."""

    with store.lock():
        _append_send(store, record)


def _budget_rows(store: StateStore) -> list[dict[str, Any]]:
    rows = []
    for row in store.read_jsonl(BUDGET_LEDGER, BUDGET_MAX_BYTES):
        ts = row.get("ts")
        tokens = row.get("tokens")
        if isinstance(ts, (int, float)) and isinstance(tokens, int) and not isinstance(tokens, bool):
            rows.append(row)
    return rows


def budget_usage(store: StateStore, now: float, run_key: str | None) -> dict[str, int]:
    usage = {"day_tokens": 0, "week_tokens": 0, "run_tokens": 0, "run_requests": 0}
    for row in _budget_rows(store):
        age = now - float(row["ts"])
        tokens = int(row["tokens"])
        if row.get("phase") != "settle":
            tokens = max(0, tokens)  # only a settlement may lower the charge of its reservation
        if age <= DAY_S:
            usage["day_tokens"] += tokens
        if age <= WEEK_S:
            usage["week_tokens"] += tokens
        if run_key is not None and row.get("run") == run_key:
            requests = row.get("requests")
            usage["run_tokens"] += tokens
            usage["run_requests"] += requests if isinstance(requests, int) and not isinstance(requests, bool) else 1
    for name in ("day_tokens", "week_tokens", "run_tokens"):
        usage[name] = max(0, usage[name])
    return usage


def _append_budget_row(store: StateStore, row: Mapping[str, Any], *, now: float) -> None:
    """Append one row to the durable ledger; the caller holds the state lock.

    The ledger is never rotated. Compaction prunes only rows past the weekly
    window, and a settlement carries the timestamp of its reservation, so the
    two always leave the window together.
    """

    store.append_jsonl(BUDGET_LEDGER, row)
    if store.size(BUDGET_LEDGER) > BUDGET_COMPACT_BYTES:
        keep = [kept for kept in _budget_rows(store) if now - float(kept["ts"]) <= WEEK_S + 3600]
        payload = "".join(json.dumps(kept, sort_keys=True) + "\n" for kept in keep).encode("utf-8")
        temporary = store.root / f".{BUDGET_LEDGER}.{secrets.token_hex(8)}.tmp"
        descriptor = _open_private_fd(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            _write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, store.root / BUDGET_LEDGER)


def reserve_attempt(
    store: StateStore,
    *,
    run_key: str,
    attempt_id: str,
    reserved_tokens: int,
    now: float,
    limits: Limits,
    send_record: Mapping[str, Any],
) -> None:
    """Admit one attempt and charge its reservation before any byte is sent.

    The marker, the breaker and the caps are checked again under the state
    lock, and the reservation row and a provisional send record are written in
    the same critical section, so concurrent processes never pass on one
    snapshot. An attempt killed in flight stays charged at its reservation and
    stays in the send log.
    """

    with store.lock():
        if hard_quota_marker(store) is not None:
            raise OpenMathError("hard_quota", "marker_present")
        if limits.breaker and breaker_open_until(store, now) is not None:
            raise OpenMathError("quota", "breaker_open")
        check_budget(store, run_key=run_key, reserved_tokens=reserved_tokens, now=now, limits=limits)
        _append_budget_row(store, {
            "ts": round(now, 3), "run": run_key, "requests": 1, "tokens": int(reserved_tokens),
            "attempt_id": attempt_id, "phase": "reserve",
        }, now=now)
        _append_send(store, send_record)


def settle_attempt(
    store: StateStore,
    *,
    run_key: str,
    attempt_id: str,
    reserved_at: float,
    reserved_tokens: int,
    charged_tokens: int,
    now: float,
) -> None:
    """Replace a reservation by the amount actually charged for the attempt."""

    with store.lock():
        _append_budget_row(store, {
            "ts": round(reserved_at, 3), "run": run_key, "requests": 0,
            "tokens": int(charged_tokens) - int(reserved_tokens),
            "attempt_id": attempt_id, "phase": "settle", "settled_at": round(now, 3),
        }, now=now)


def check_quota_snapshot(store: StateStore, now: float, limits: Limits) -> None:
    """Apply --quota-floor to quota.json, when the flag is set and the file exists."""

    if limits.quota_floor_usd is None:
        return
    try:
        snapshot = store.read_json(QUOTA_SNAPSHOT)
    except OpenMathError as exc:
        raise OpenMathError("budget", "quota_snapshot_invalid") from exc
    if snapshot is None:
        return
    fetched = _parse_iso(snapshot.get("fetched_at")) if isinstance(snapshot, dict) else None
    if fetched is None:
        raise OpenMathError("budget", "quota_snapshot_invalid")
    if limits.quota_max_age_s is not None and now - fetched > limits.quota_max_age_s:
        return
    windows = snapshot.get("windows")
    if not isinstance(windows, dict):
        raise OpenMathError("budget", "quota_snapshot_invalid")
    for name in ("five_hour", "week"):
        window = windows.get(name)
        remaining = window.get("remaining_usd") if isinstance(window, dict) else None
        if not isinstance(remaining, (int, float)) or isinstance(remaining, bool):
            raise OpenMathError("budget", "quota_snapshot_invalid")
        if remaining < limits.quota_floor_usd:
            raise OpenMathError("budget", f"quota_{name}_below_floor")


def check_budget(
    store: StateStore, *, run_key: str, reserved_tokens: int, now: float, limits: Limits
) -> dict[str, int]:
    """Apply the caps and the quota floor that the caller set."""

    usage = budget_usage(store, now, run_key)
    if limits.run_max_requests is not None and usage["run_requests"] + 1 > limits.run_max_requests:
        raise OpenMathError("budget", "run_max_requests")
    if limits.run_max_tokens is not None and usage["run_tokens"] + reserved_tokens > limits.run_max_tokens:
        raise OpenMathError("budget", "run_max_total_tokens")
    if limits.day_max_tokens is not None and usage["day_tokens"] + reserved_tokens > limits.day_max_tokens:
        raise OpenMathError("budget", "day_max_tokens")
    if limits.week_max_tokens is not None and usage["week_tokens"] + reserved_tokens > limits.week_max_tokens:
        raise OpenMathError("budget", "week_max_tokens")
    check_quota_snapshot(store, now, limits)
    return usage


def _breaker_state(store: StateStore) -> dict[str, Any]:
    state = store.read_json(BREAKER_FILE)
    if not isinstance(state, dict):
        return {"rate_limited_at": [], "open_until": None}
    events = [float(t) for t in state.get("rate_limited_at") or [] if isinstance(t, (int, float))]
    open_until = state.get("open_until")
    return {
        "rate_limited_at": events,
        "open_until": float(open_until) if isinstance(open_until, (int, float)) else None,
    }


def breaker_open_until(store: StateStore, now: float) -> float | None:
    open_until = _breaker_state(store)["open_until"]
    return open_until if open_until is not None and now < open_until else None


def breaker_record_rate_limit(store: StateStore, now: float, limits: Limits) -> bool:
    """Record one 429; return True when the machine-wide breaker opens.

    Only called when the three breaker flags are set. The file keeps the last
    ``--breaker-threshold`` rate-limit times, which is all the test needs.
    """

    threshold = int(limits.breaker_threshold or 0)
    window = float(limits.breaker_window_s or 0.0)
    open_s = float(limits.breaker_open_s or 0.0)
    with store.lock():
        state = _breaker_state(store)
        events = [t for t in state["rate_limited_at"] if now - t <= window] + [now]
        opened = len(events) >= threshold
        open_until = now + open_s if opened else state["open_until"]
        store.write_json(BREAKER_FILE, {"rate_limited_at": events[-threshold:], "open_until": open_until})
    return opened


def hard_quota_marker(store: StateStore) -> dict[str, Any] | None:
    try:
        marker = store.read_json(HARD_QUOTA_MARKER)
    except OpenMathError:
        return {"status": "unreadable"}
    if marker is None:
        return None
    return marker if isinstance(marker, dict) else {"status": "unreadable"}


def write_hard_quota_marker(store: StateStore, *, request_id: str | None, now: float) -> None:
    with store.lock():
        store.write_json(
            HARD_QUOTA_MARKER,
            {"created_at": _iso(now), "http_status": 402, "request_id": request_id,
             "clear": "delete this file after the team quota is restored"},
        )


# ---------------------------------------------------------------------------
# Machine-wide request limiter.
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def machine_slot(
    store: StateStore,
    label: str,
    *,
    slots: int | None,
    wait_s: float | None = None,
    poll_s: float | None = None,
) -> Iterator[dict[str, Any]]:
    """Hold one of the machine-wide in-flight slots for the duration of a request.

    Each slot is a lock file holding the owner's PID. The operating system
    drops a lock when its process dies, so a slot is never stuck; a slot found
    free with leftover owner metadata is reported as a recovered stale slot.
    With ``slots`` None (no --max-inflight) the request takes no slot. With
    ``wait_s`` None (no --slot-wait) the wait for a slot has no limit.
    """

    if slots is None:
        yield {"slot": None, "stale_recovered": False, "stale_pid": None}
        return
    poll = LIMITER_POLL_S if poll_s is None else poll_s
    deadline = None if wait_s is None else _mono() + wait_s
    while True:
        for index in range(slots):
            path = store.root / f"limiter-slot-{index}.lock"
            descriptor = _open_private_fd(path, os.O_RDWR | os.O_CREAT)
            try:
                if not _try_lock(descriptor, blocking=False):
                    continue
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    previous = _read_fd(descriptor, 4096)
                    stale_pid = None
                    if previous.strip():
                        with contextlib.suppress(ValueError, AttributeError):
                            stale_pid = json.loads(previous.decode("utf-8")).get("pid")
                    holder = {"pid": os.getpid(), "started_at": _iso(_wall()), "label": label}
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    os.ftruncate(descriptor, 0)
                    _write_all(descriptor, json.dumps(holder).encode("utf-8"))
                    try:
                        yield {
                            "slot": index,
                            "stale_recovered": bool(previous.strip()),
                            "stale_pid": stale_pid if isinstance(stale_pid, int) else None,
                        }
                    finally:
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        os.ftruncate(descriptor, 0)
                finally:
                    _unlock(descriptor)
                return
            finally:
                os.close(descriptor)
        if deadline is not None and _mono() >= deadline:
            raise OpenMathError("transient", "limiter_busy")
        _sleep(poll)


# ---------------------------------------------------------------------------
# HTTP transport.
# ---------------------------------------------------------------------------


def validated_origin(origin: str) -> str:
    """Return the fixed production origin, or a loopback origin for tests.

    There is no flag or environment variable for the origin. Only the internal
    ``_origin`` argument of ``main`` can point the client at a loopback server.
    """

    if origin == API_ORIGIN:
        return origin
    parsed = urllib.parse.urlsplit(origin)
    if (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        and parsed.port
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
    ):
        return origin.rstrip("/")
    raise OpenMathError("client_bug", "origin_refused")


def build_opener(origin: str) -> urllib.request.OpenerDirector:
    """An opener with no proxy, no redirect and no error-processing handlers.

    Without HTTPErrorProcessor every status comes back as a plain response, so
    a 3xx is never followed and is refused by the caller instead.
    """

    opener = urllib.request.OpenerDirector()
    opener.addheaders = []
    opener.add_handler(urllib.request.ProxyHandler({}))
    opener.add_handler(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    if origin != API_ORIGIN:
        opener.add_handler(urllib.request.HTTPHandler())
    return opener


def _request_id(headers: Any) -> str | None:
    value = headers.get("x-request-id") if headers is not None else None
    if isinstance(value, str) and REQUEST_ID_RE.fullmatch(value.strip()):
        return value.strip()
    return None


def _read_limited(response: Any, max_bytes: int) -> bytes:
    payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise OpenMathError("client_bug", "response_too_large", kind="http", http_status=response.status)
    return payload


class GatewayClient:
    def __init__(self, origin: str = API_ORIGIN) -> None:
        self.origin = validated_origin(origin)
        self.opener = build_opener(self.origin)

    def open(
        self,
        method: str,
        path: str,
        *,
        key: str | None,
        body: bytes | None = None,
        accept: str = "application/json",
        timeout: float | None = None,
    ) -> Any:
        url = self.origin + API_PREFIX + path
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Accept", accept)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        if key:
            request.add_unredirected_header("Authorization", "Bearer " + key)
        try:
            response = self.opener.open(request, timeout=timeout)
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
            raise OpenMathError("transient", _transport_code(exc), kind="transport", retryable=True) from exc
        if response is None:  # no handler for the scheme
            raise OpenMathError("client_bug", "scheme_refused")
        return response

    def get_json(
        self, path: str, *, key: str | None, max_bytes: int = MAX_RESPONSE_BYTES, timeout: float | None = None
    ) -> tuple[Any, str | None]:
        response = self.open("GET", path, key=key, timeout=timeout)
        with contextlib.closing(response):
            request_id = _request_id(response.headers)
            try:
                body = _read_limited(response, max_bytes if response.status == 200 else MAX_ERROR_BODY_BYTES)
            except (OSError, http.client.HTTPException) as exc:
                raise OpenMathError("transient", "read_interrupted", kind="transport") from exc
            if response.status != 200:
                raise classify_http_error(
                    response.status, body, response.headers.get("Retry-After"), request_id=request_id
                )
        try:
            return json.loads(body.decode("utf-8")), request_id
        except ValueError as exc:
            raise OpenMathError("client_bug", "malformed_json", kind="http", http_status=200) from exc


# ---------------------------------------------------------------------------
# Catalog.
# ---------------------------------------------------------------------------


def parse_catalog(payload: Any, *, now: float) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise OpenMathError("client_bug", "catalog_malformed", kind="http", http_status=200)
    models: list[dict[str, Any]] = []
    rejected = 0
    for entry in data:
        model_id = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(model_id, str) or not MODEL_ID_RE.fullmatch(model_id):
            rejected += 1
            continue
        efforts = entry.get("supported_reasoning_efforts")
        clean = [e for e in efforts if isinstance(e, str) and EFFORT_NAME_RE.fullmatch(e)] if isinstance(efforts, list) else []
        models.append({"id": model_id, "supported_reasoning_efforts": clean,
                       "efforts_declared": isinstance(efforts, list)})
    return {
        "schema": "open-math-os.catalog.v1",
        "fetched_at": _iso(now),
        "fetched_at_epoch": round(now, 3),
        "models": models,
        "rejected_entries": rejected,
    }


def _valid_catalog(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("schema") == "open-math-os.catalog.v1"
        and isinstance(value.get("fetched_at_epoch"), (int, float))
        and isinstance(value.get("models"), list)
    )


def load_catalog(
    client: GatewayClient, store: StateStore, key: str, *, refresh: bool, now: float, limits: Limits
) -> tuple[dict[str, Any], str]:
    """Read the catalog live, or from the cache when --catalog-ttl allows it."""

    ttl = limits.catalog_ttl_s
    cached = None if refresh or ttl is None else store.read_json(CATALOG_FILE)
    if ttl is not None and _valid_catalog(cached) and 0 <= now - float(cached["fetched_at_epoch"]) < ttl:
        return cached, "cache"
    with machine_slot(store, "catalog", slots=limits.max_inflight, wait_s=limits.slot_wait_s):
        payload, _request = client.get_json("/models", key=key, timeout=limits.metadata_timeout_s)
    catalog = parse_catalog(payload, now=_wall())
    with store.lock():
        store.write_json(CATALOG_FILE, catalog)
    return catalog, "live"


def catalog_entry(catalog: Mapping[str, Any], model: str) -> dict[str, Any] | None:
    for entry in catalog.get("models") or []:
        if isinstance(entry, dict) and entry.get("id") == model:
            return entry
    return None


# ---------------------------------------------------------------------------
# Keys.
# ---------------------------------------------------------------------------


def inspect_model_key(environ: Mapping[str, str]) -> dict[str, Any]:
    value = environ.get(KEY_ENV)
    if value is None:
        status = "missing"
    elif not value:
        status = "empty"
    elif value.startswith(PLATFORM_KEY_PREFIX):
        status = "platform_key_in_model_slot"
    elif not value.startswith(KEY_PREFIX):
        status = "wrong_prefix"
    elif not KEY_BODY_RE.fullmatch(value[len(KEY_PREFIX):]):
        status = "malformed"
    else:
        status = "present"
    return {"env": KEY_ENV, "status": status, "prefix_ok": status == "present"}


def require_model_key(environ: Mapping[str, str]) -> str:
    if environ.get(PLATFORM_KEY_ENV):
        raise OpenMathError("auth", "platform_key_present")
    info = inspect_model_key(environ)
    if info["status"] != "present":
        raise OpenMathError("auth", f"key_{info['status']}")
    return str(environ[KEY_ENV])


def _read_private_source(path: Path, max_bytes: int) -> bytes:
    """Bounded read of an owner-private, single-link regular file."""

    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0),
    )
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or int(getattr(info, "st_nlink", 1)) != 1:
            raise OpenMathError("state", "source_not_regular")
        if os.name == "posix" and (
            int(info.st_uid) != int(os.geteuid()) or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise OpenMathError("state", "source_not_private")
        return _read_fd(descriptor, max_bytes)
    finally:
        os.close(descriptor)


def claude_secret_key(environ: Mapping[str, str]) -> str:
    path = claude_secrets_path(environ)
    try:
        payload = _read_private_source(path, MAX_SECRET_SOURCE_BYTES)
    except FileNotFoundError as exc:
        raise OpenMathError("state", "claude_secrets_unavailable") from exc
    except OSError as exc:
        raise OpenMathError("state", "claude_secrets_unreadable") from exc
    try:
        data = json.loads(payload.decode("utf-8"))
    except ValueError as exc:
        raise OpenMathError("state", "claude_secrets_malformed") from exc
    value = data.get(KEY_ENV) if isinstance(data, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise OpenMathError("state", "claude_secrets_without_key")
    return value.strip()


def key_source_fingerprint(environ: Mapping[str, str], key: str) -> str:
    """Compare sha256 fingerprints of the loaded key and its source file."""

    try:
        source = claude_secret_key(environ)
    except OpenMathError as exc:
        return {
            "claude_secrets_unavailable": "source_unavailable",
            "claude_secrets_without_key": "source_without_key",
            "source_not_private": "source_not_private",
        }.get(exc.code, "source_unreadable")
    loaded = hashlib.sha256(key.encode("utf-8")).digest()
    expected = hashlib.sha256(source.encode("utf-8")).digest()
    return "match" if hmac.compare_digest(loaded, expected) else "stale"


def _load_secret_loader(environ: Mapping[str, str]) -> types.ModuleType:
    """Load the managed secret loader the runner uses, after the same trust checks.

    The pattern follows force-loop's ``force_loop_cli`` reuse of the loader.
    """

    here = Path(os.path.abspath(__file__)).parent
    candidates: list[Path] = []
    runtime_root = str(environ.get("AAS_RUNTIME_ROOT") or "")
    if runtime_root and os.path.isabs(runtime_root):
        candidates += [Path(runtime_root) / "load_secret_env.py", Path(runtime_root) / "runners" / "load_secret_env.py"]
    for parent in here.parents:
        if parent.name == "runtime":
            candidates += [parent / "load_secret_env.py", parent / "runners" / "load_secret_env.py"]
            break
    for candidate in candidates:
        try:
            descriptor = os.open(
                candidate,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0),
            )
        except OSError:
            continue
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or (os.name == "posix" and int(info.st_uid) not in {0, int(os.geteuid())})
                or stat.S_IMODE(info.st_mode) & 0o022
                or (os.name == "posix" and int(info.st_uid) != 0 and int(info.st_nlink) != 1)
            ):
                raise OpenMathError("state", "secret_loader_untrusted")
            source = _read_fd(descriptor, 1_000_000)
        finally:
            os.close(descriptor)
        module = types.ModuleType("aas_open_math_os_secret_env_loader")
        module.__file__ = str(candidate)
        exec(compile(source, str(candidate), "exec"), module.__dict__)
        return module
    raise OpenMathError("state", "secret_loader_unavailable")


# ---------------------------------------------------------------------------
# Chat completions.
# ---------------------------------------------------------------------------


def normalize_usage(raw: Any) -> dict[str, int]:
    usage: dict[str, int] = {}
    if not isinstance(raw, dict):
        return usage
    for name in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens"):
        value = raw.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            usage[name] = value
    details = raw.get("completion_tokens_details")
    if "reasoning_tokens" not in usage and isinstance(details, dict):
        value = details.get("reasoning_tokens")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            usage["reasoning_tokens"] = value
    return usage


class ChatResult:
    def __init__(self) -> None:
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.reasoning_details: list[Any] = []
        self.finish_reason: str | None = None
        self.usage: dict[str, int] = {}
        self.served_model: str | None = None
        self.request_id: str | None = None
        self.done = False
        self.bytes_received = 0

    @property
    def text(self) -> str:
        return "".join(self.content)

    def add_choice(self, choice: Mapping[str, Any], part: Mapping[str, Any], sink: Callable[[str], None] | None) -> None:
        content = part.get("content")
        if isinstance(content, str) and content:
            self.content.append(content)
            if sink is not None:
                sink(content)
        reasoning = part.get("reasoning")
        if not isinstance(reasoning, str):
            reasoning = part.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            self.reasoning.append(reasoning)
        details = part.get("reasoning_details")
        if isinstance(details, list):
            self.reasoning_details.extend(details)
        reason = choice.get("finish_reason")
        if isinstance(reason, str) and reason:
            self.finish_reason = reason if FINISH_REASON_RE.fullmatch(reason) else "other"

    def add_envelope(self, payload: Mapping[str, Any]) -> None:
        model = payload.get("model")
        if isinstance(model, str):
            self.served_model = model if MODEL_ID_RE.fullmatch(model) else "invalid"
        usage = normalize_usage(payload.get("usage"))
        if usage:
            self.usage = usage


def feed_sse_event(result: ChatResult, data: str, sink: Callable[[str], None] | None) -> None:
    """Apply one SSE ``data`` payload. ``[DONE]`` marks the end of the stream."""

    if data.strip() == "[DONE]":
        result.done = True
        return
    try:
        payload = json.loads(data)
    except ValueError as exc:
        raise OpenMathError("client_bug", "malformed_stream_chunk", kind="http", http_status=200,
                            bytes_received=result.bytes_received, partial=result) from exc
    if not isinstance(payload, dict):
        return
    error = payload.get("error")
    if error is not None:
        status = error.get("status") if isinstance(error, dict) else None
        if not isinstance(status, int) and isinstance(error, dict) and isinstance(error.get("code"), int):
            status = error.get("code")
        code = _error_code(json.dumps({"error": error}).encode("utf-8"))
        error_class = "transient"
        if isinstance(status, int) and not isinstance(status, bool):
            error_class = classify_http_error(status, b"").error_class
        raise OpenMathError(error_class, code, kind="http", http_status=200,
                            bytes_received=result.bytes_received, partial=result,
                            request_id=result.request_id)
    result.add_envelope(payload)
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        delta = choices[0].get("delta")
        if isinstance(delta, dict):
            result.add_choice(choices[0], delta, sink)
        elif choices[0].get("finish_reason"):
            result.add_choice(choices[0], {}, sink)


def iter_sse_data(lines: Iterable[bytes]) -> Iterator[str]:
    """Yield the ``data`` payload of each SSE event in a line stream."""

    data_lines: list[str] = []
    for raw in lines:
        text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not text:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if text.startswith(":"):
            continue
        field, _, value = text.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_lines.append(value)
    if data_lines:
        yield "\n".join(data_lines)


def _stream_lines(
    response: Any, result: ChatResult, started: float, deadline_s: float | None
) -> Iterator[bytes]:
    while True:
        if deadline_s is not None and _mono() - started > deadline_s:
            raise OpenMathError("transient", "deadline_exceeded", kind="transport",
                                bytes_received=result.bytes_received, partial=result,
                                request_id=result.request_id)
        line = response.readline(MAX_SSE_LINE_BYTES + 1)
        if not line:
            return
        result.bytes_received += len(line)
        if result.bytes_received > MAX_RESPONSE_BYTES or (
            len(line) > MAX_SSE_LINE_BYTES and not line.endswith(b"\n")
        ):
            raise OpenMathError("client_bug", "response_too_large", kind="http", http_status=200,
                                bytes_received=result.bytes_received, partial=result,
                                request_id=result.request_id)
        yield line


def read_stream(
    response: Any,
    result: ChatResult,
    *,
    started: float,
    deadline_s: float | None,
    sink: Callable[[str], None] | None,
) -> ChatResult:
    try:
        for data in iter_sse_data(_stream_lines(response, result, started, deadline_s)):
            feed_sse_event(result, data, sink)
            if result.done:
                break
    except socket.timeout as exc:
        raise OpenMathError("transient", "idle_timeout", kind="transport",
                            bytes_received=result.bytes_received, partial=result,
                            request_id=result.request_id) from exc
    except (OSError, http.client.HTTPException) as exc:
        raise OpenMathError("transient", "stream_interrupted", kind="transport",
                            bytes_received=result.bytes_received, partial=result,
                            request_id=result.request_id) from exc
    if not result.done:
        raise OpenMathError("transient", "stream_incomplete", kind="transport",
                            bytes_received=result.bytes_received, partial=result,
                            request_id=result.request_id)
    return result


def read_completion(response: Any, result: ChatResult, sink: Callable[[str], None] | None) -> ChatResult:
    try:
        body = _read_limited(response, MAX_RESPONSE_BYTES)
    except (OSError, http.client.HTTPException) as exc:
        raise OpenMathError("transient", "read_interrupted", kind="transport",
                            bytes_received=1, request_id=result.request_id) from exc
    result.bytes_received = len(body)
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise OpenMathError("client_bug", "malformed_json", kind="http", http_status=200,
                            bytes_received=len(body), request_id=result.request_id) from exc
    if not isinstance(payload, dict):
        raise OpenMathError("client_bug", "malformed_json", kind="http", http_status=200,
                            bytes_received=len(body), request_id=result.request_id)
    result.add_envelope(payload)
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        result.add_choice(choices[0], message if isinstance(message, dict) else {}, sink)
    result.done = True
    return result


class DeadlineWatch:
    """Shut the response socket down when the attempt's total deadline passes.

    The idle timeout bounds each receive, not the whole read, so a peer that
    trickles bytes inside one SSE line or one chunk-size line could otherwise
    hold the client far past ``--deadline``. After the shutdown every blocked
    or later read returns at once, and the caller reports the deadline.
    Without ``--deadline`` the watch does nothing.
    """

    def __init__(self, response: Any, remaining_s: float | None) -> None:
        self.expired = threading.Event()
        connection = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        self._socket = connection if isinstance(connection, socket.socket) else None
        self._timer = None if remaining_s is None else threading.Timer(max(0.0, remaining_s), self._expire)
        if self._timer is not None:
            self._timer.daemon = True

    def _expire(self) -> None:
        self.expired.set()
        if self._socket is not None:
            with contextlib.suppress(OSError):
                # The plain socket method: SSLSocket.shutdown would also drop
                # the TLS object under the reading thread.
                socket.socket.shutdown(self._socket, socket.SHUT_RDWR)

    def __enter__(self) -> "DeadlineWatch":
        if self._timer is not None:
            self._timer.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._timer is not None:
            self._timer.cancel()


def send_chat_once(
    client: GatewayClient,
    key: str,
    body: Mapping[str, Any],
    *,
    deadline_s: float | None,
    idle_timeout_s: float | None,
    sink: Callable[[str], None] | None,
) -> ChatResult:
    stream = bool(body.get("stream"))
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    started = _mono()
    # A streamed answer takes --idle-timeout as its socket timeout when it is
    # given. Otherwise the socket timeout is --deadline, or there is none.
    response = client.open(
        "POST",
        "/chat/completions",
        key=key,
        body=payload,
        accept="text/event-stream" if stream else "application/json",
        timeout=idle_timeout_s if stream and idle_timeout_s is not None else deadline_s,
    )
    with contextlib.closing(response):
        result = ChatResult()
        result.request_id = _request_id(response.headers)
        remaining = None if deadline_s is None else deadline_s - (_mono() - started)
        with DeadlineWatch(response, remaining) as watch:
            if response.status != 200:
                try:
                    error_body = _read_limited(response, MAX_ERROR_BODY_BYTES)
                except (OSError, http.client.HTTPException, OpenMathError):
                    error_body = b""
                raise classify_http_error(
                    response.status, error_body, response.headers.get("Retry-After"), request_id=result.request_id
                )
            try:
                content_type = str(response.headers.get("Content-Type") or "")
                if stream and "text/event-stream" in content_type:
                    return read_stream(response, result, started=started, deadline_s=deadline_s, sink=sink)
                return read_completion(response, result, sink)
            except Exception as exc:
                if not watch.expired.is_set():
                    raise
                received = exc.bytes_received if isinstance(exc, OpenMathError) else 0
                raise OpenMathError("transient", "deadline_exceeded", kind="transport",
                                    bytes_received=max(received, result.bytes_received),
                                    partial=result if stream else None,
                                    request_id=result.request_id) from exc


def build_messages(system: str | None, history: Iterable[Mapping[str, str]], prompt: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    for turn in history:
        role = str(turn.get("role") or "")
        if role not in ("user", "assistant"):
            raise OpenMathError("usage", "history_role_invalid")
        messages.append({"role": role, "content": str(turn.get("content") or "")})
    messages.append({"role": "user", "content": prompt})
    return messages


def run_key_for(label: str, *, process_nonce: str) -> str:
    """The run a label names: its first two colon-separated fields.

    A one-field label names no run, so that invocation is its own run.
    """

    fields = label.split(":")
    if len(fields) >= 2 and fields[0] and fields[1]:
        return ":".join(fields[:2])
    return f"{label}:{process_nonce}"


def is_orchestrated(label: str, environ: Mapping[str, str]) -> bool:
    return label.startswith(ORCHESTRATOR_LABEL_PREFIXES) or any(environ.get(name) for name in ORCHESTRATOR_ENV_MARKERS)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def chat_with_policy(
    client: GatewayClient,
    store: StateStore,
    key: str,
    body: Mapping[str, Any],
    *,
    label: str,
    run_key: str,
    orchestrated: bool,
    limits: Limits,
    sink: Callable[[str], None] | None,
    pii_reported: Iterable[str] = (),
) -> ChatResult:
    """One logical request: the limits that are set, retry rules and logging."""

    messages_bytes = json.dumps(body["messages"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    # Without --max-tokens the reservation holds only the prompt estimate.
    reserved = int(body.get("max_completion_tokens") or 0) + math.ceil(len(messages_bytes) / 4)
    reported = sorted(pii_reported)
    attempt = 0
    while True:
        now = _wall()
        if hard_quota_marker(store) is not None:
            raise OpenMathError("hard_quota", "marker_present")
        if limits.breaker and breaker_open_until(store, now) is not None:
            raise OpenMathError("quota", "breaker_open")
        check_budget(store, run_key=run_key, reserved_tokens=reserved, now=now, limits=limits)
        attempt_id = secrets.token_hex(8)
        common = {
            "label": label,
            "run": run_key,
            "model": body["model"],
            "effort": body["reasoning_effort"],
            "prompt_sha256": _sha256(messages_bytes),
            "prompt_bytes": len(messages_bytes),
            "pii_reported": reported,
            "attempt": attempt,
            "attempt_id": attempt_id,
        }
        outcome: ChatResult | None = None
        failure: OpenMathError | None = None
        with machine_slot(store, label, slots=limits.max_inflight, wait_s=limits.slot_wait_s):
            reserved_at = _wall()
            reserve_attempt(store, run_key=run_key, attempt_id=attempt_id, reserved_tokens=reserved,
                            now=reserved_at, limits=limits, send_record={
                                **common, "ts": _iso(reserved_at), "status": "inflight",
                                "reserved_tokens": reserved, "request_id": None, "response_sha256": None,
                                "response_bytes": 0, "usage": {}, "latency_s": None,
                                "error_class": None, "http_status": None,
                            })
            started = _mono()
            try:
                outcome = send_chat_once(client, key, body, deadline_s=limits.deadline_s,
                                         idle_timeout_s=limits.idle_timeout_s, sink=sink)
            except OpenMathError as exc:
                failure = exc
        latency = round(_mono() - started, 3)
        partial: ChatResult | None
        if outcome is not None:
            received, partial = outcome.bytes_received, outcome
            status = "truncated" if outcome.finish_reason == "length" else "ok"
        else:
            assert failure is not None
            received, partial = failure.bytes_received, failure.partial
            status = "incomplete" if received else "error"
        usage = partial.usage if partial is not None else {}
        if "total_tokens" in usage:
            charged = usage["total_tokens"]
        else:
            charged = reserved if received else 0
        settle_attempt(store, run_key=run_key, attempt_id=attempt_id, reserved_at=reserved_at,
                       reserved_tokens=reserved, charged_tokens=charged, now=_wall())
        text = partial.text.encode("utf-8") if partial is not None else b""
        log_send(store, {
            **common,
            "ts": _iso(_wall()),
            "request_id": partial.request_id if partial is not None else getattr(failure, "request_id", None),
            "response_sha256": _sha256(text) if text else None,
            "response_bytes": len(text),
            "usage": usage,
            "latency_s": latency,
            "status": status,
            "error_class": failure.error_class if failure is not None else None,
            "http_status": failure.http_status if failure is not None else 200,
        })
        if outcome is not None:
            return outcome
        assert failure is not None
        opened = False
        if failure.error_class == "quota" and limits.breaker:
            opened = breaker_record_rate_limit(store, _wall(), limits)
        if failure.error_class == "hard_quota":
            write_hard_quota_marker(store, request_id=failure.request_id, now=_wall())
        delay = None
        if (
            failure.retryable
            and not orchestrated
            and not opened
            and failure.bytes_received == 0
            and limits.retries is not None
            and attempt < limits.retries
        ):
            delay = retry_delay(failure.retry_after, attempt, limits)
        if delay is None:
            if opened:
                print(f"circuit breaker opened: sends pause for {limits.breaker_open_s:g} s", file=sys.stderr)
            raise failure
        _sleep(delay)
        attempt += 1


# ---------------------------------------------------------------------------
# Output.
# ---------------------------------------------------------------------------


class TextSink:
    """Stream sanitized content to the terminal between nonce-bearing markers."""

    def __init__(self, stream: Any, *, model: str) -> None:
        self.stream = stream
        self.model = model
        self.nonce = secrets.token_hex(4)
        self.sanitizer = TerminalSanitizer()
        self.started = False
        self.last = "\n"

    def _begin(self) -> None:
        if not self.started:
            self.started = True
            self.stream.write(
                f"[open-math-os {self.nonce}] begin trust={TRUST_LABEL} requested_model={self.model}\n"
            )

    def write(self, text: str) -> None:
        self._begin()
        clean = self.sanitizer.feed(text)
        if clean:
            self.stream.write(clean)
            self.last = clean[-1]
            self.stream.flush()

    def finish(self, status: str, provenance: Mapping[str, Any]) -> None:
        self._begin()
        if self.last != "\n":
            self.stream.write("\n")
        self.stream.write(f"[open-math-os {self.nonce}] end trust={TRUST_LABEL} status={status}\n")
        self.stream.write(f"[open-math-os {self.nonce}] provenance {json.dumps(provenance, sort_keys=True)}\n")
        self.stream.flush()


def provenance_block(
    *,
    requested_model: str | None,
    result: ChatResult | None,
    catalog: Mapping[str, Any] | None,
    request_id: str | None = None,
) -> dict[str, Any]:
    block = dict(PROVENANCE)
    block.update({
        "requested_model": requested_model,
        "served_model_claimed": result.served_model if result is not None else None,
        "request_id": (result.request_id if result is not None else None) or request_id,
        "captured_at": _iso(_wall()),
        "catalog_fetched_at": catalog.get("fetched_at") if catalog else None,
    })
    return block


def untrusted_output(result: ChatResult | None, *, partial: bool) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "trust": TRUST_LABEL,
        "partial": partial,
        "content": strip_terminal_controls(result.text),
        "reasoning": strip_terminal_controls("".join(result.reasoning)),
        "reasoning_details": result.reasoning_details,
    }


def _emit_json(payload: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Commands.
# ---------------------------------------------------------------------------


def _location_prefixes(locations: Iterable[str | Path]) -> list[str]:
    """Each location as written and with its own links resolved, case-normalized."""

    prefixes = set()
    for location in locations:
        prefixes.add(os.path.normcase(str(location)))
        prefixes.add(os.path.normcase(os.path.realpath(location)))
    return sorted(prefixes)


def _is_inside(name: str, prefixes: Iterable[str]) -> bool:
    folded = os.path.normcase(name)
    return any(folded == prefix or folded.startswith(prefix + os.sep) for prefix in prefixes)


def _input_path_names(path_value: str) -> list[str]:
    """Every name a path reaches its file by: as given, at each link, and resolved.

    A store file that is itself a link out of its store, as a dotfile manager
    makes it, is still named by its store path on the way.
    """

    names = [os.path.realpath(path_value)]
    current = os.path.abspath(path_value)
    for _ in range(40):  # the usual limit on links in one lookup
        directory = os.path.realpath(os.path.dirname(current))
        current = os.path.join(directory, os.path.basename(current))
        names.append(current)
        try:
            current = os.path.join(directory, os.readlink(current))
        except OSError:  # not a link: the walk ends here
            break
    return names


def _opened_path(descriptor: int) -> str | None:
    """The path the kernel reports for an open descriptor, where /proc/self/fd exists."""

    try:
        return os.readlink(f"/proc/self/fd/{descriptor}")
    except OSError:
        return None


def _read_input_file(path_value: str, environ: Mapping[str, str]) -> str:
    """Read a prompt or system file that is not a credential store.

    Before anything is opened, the path is refused when it, a link on the way or
    its resolved form lies in a credential store. The file is then opened with
    its links followed, as /dev/stdin needs. Where the kernel reports the path
    of the opened file (Linux), that path is checked against the stores and the
    kernel views before anything is read, so a link swapped in between is
    caught. A link to an ordinary file, a path under a linked directory and an
    unlinked here-document behind /dev/stdin are still read.
    """

    stores = _location_prefixes(managed_secret_locations(environ))
    if any(_is_inside(name, stores) for name in _input_path_names(path_value)):
        raise OpenMathError("blocked", "input_file_is_managed_secret")
    try:
        descriptor = os.open(
            path_value, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
        )
    except OSError as exc:
        raise OpenMathError("usage", "input_file_unreadable") from exc
    try:
        opened = _opened_path(descriptor)
        if opened is not None and _is_inside(opened, stores + _location_prefixes(KERNEL_VIEW_LOCATIONS)):
            raise OpenMathError("blocked", "input_file_is_managed_secret")
        info = os.fstat(descriptor)
        identity = f"{int(info.st_dev)}:{int(info.st_ino)}"
        if identity in str(environ.get(PROTECTED_SECRET_IDENTITIES_ENV) or "").split(","):
            raise OpenMathError("blocked", "input_file_is_managed_secret")
        if not stat.S_ISREG(info.st_mode):
            raise OpenMathError("usage", "input_file_not_regular")
        try:
            payload = _read_fd(descriptor, MAX_INPUT_FILE_BYTES)
        except OpenMathError as exc:
            raise OpenMathError("usage", "input_file_too_large") from exc
    finally:
        os.close(descriptor)
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OpenMathError("usage", "input_file_not_utf8") from exc


def _log_blocked(environ: Mapping[str, str], label: str, model: str, effort: str, findings: list[str]) -> None:
    """Log a refused send as metadata only; the refusal stands if the log fails."""

    with contextlib.suppress(OpenMathError, OSError):
        blocked_store = StateStore(state_dir(environ))
        blocked_store.ensure()
        log_send(blocked_store, {"ts": _iso(_wall()), "label": label, "model": model, "effort": effort,
                                 "status": "blocked", "findings": findings})


def ask_limits(args: argparse.Namespace) -> Limits:
    """Read the optional limits of `ask`; each stays off unless its flag is given."""

    values: dict[str, Any] = {}
    for flag, field, _kind, _metavar, _text in LIMIT_FLAGS:
        value = getattr(args, field, None)
        if value is not None and not (math.isfinite(value) and value > 0):
            raise OpenMathError("usage", flag[2:].replace("-", "_") + "_out_of_range")
        values[field] = value
    limits = Limits(**values)
    breaker = (limits.breaker_threshold, limits.breaker_window_s, limits.breaker_open_s)
    if any(value is not None for value in breaker) and any(value is None for value in breaker):
        raise OpenMathError("usage", "breaker_flags_incomplete")
    return limits


def command_ask(args: argparse.Namespace, environ: Mapping[str, str], origin: str) -> int:
    label = args.label or "manual"
    if not LABEL_RE.fullmatch(label):
        raise OpenMathError("usage", "label_invalid")
    model = args.model
    if not MODEL_ID_RE.fullmatch(model):
        raise OpenMathError("usage", "model_id_invalid")
    effort = args.effort
    limits = ask_limits(args)
    max_tokens = limits.max_tokens
    try:
        system = _read_input_file(args.system_file, environ) if args.system_file else args.system
        prompt = _read_input_file(args.prompt_file, environ) if args.prompt_file else args.prompt
    except OpenMathError as exc:
        if exc.error_class == "blocked":
            _log_blocked(environ, label, model, effort, [exc.code])
        raise
    if not prompt:
        raise OpenMathError("usage", "prompt_empty")
    messages = build_messages(system, [], prompt)
    findings = screen_outbound(messages, environ)
    if findings:
        _log_blocked(environ, label, model, effort, findings)
        raise OpenMathError("blocked", ",".join(findings))
    key = require_model_key(environ)
    store = StateStore(state_dir(environ))
    store.ensure()
    now = _wall()
    if hard_quota_marker(store) is not None:
        raise OpenMathError("hard_quota", "marker_present")
    if limits.breaker and breaker_open_until(store, now) is not None:
        raise OpenMathError("quota", "breaker_open")
    client = GatewayClient(origin)
    catalog, _source = load_catalog(client, store, key, refresh=False, now=now, limits=limits)
    entry = catalog_entry(catalog, model)
    if entry is None:
        raise OpenMathError("usage", "model_not_in_catalog")
    if effort not in entry.get("supported_reasoning_efforts", []):
        raise OpenMathError("usage", "effort_not_supported")
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "reasoning_effort": effort,
        "stream": bool(args.stream),
    }
    if max_tokens is not None:
        body["max_completion_tokens"] = max_tokens
    if args.stream:
        body["stream_options"] = {"include_usage": True}
    pii_reported = report_outbound_pii(messages)
    if pii_reported:
        print(f"note: the prompt matches {', '.join(pii_reported)}; it is sent, and the send log "
              "records the categories", file=sys.stderr)
    sink = None if args.json else TextSink(sys.stdout, model=model)
    run_key = run_key_for(label, process_nonce=f"{os.getpid()}-{secrets.token_hex(4)}")
    try:
        result = chat_with_policy(
            client, store, key, body,
            label=label, run_key=run_key, orchestrated=is_orchestrated(label, environ),
            limits=limits, sink=sink.write if sink is not None else None,
            pii_reported=pii_reported,
        )
    except OpenMathError as exc:
        exc.catalog = catalog
        if sink is not None and exc.partial is not None:
            sink.finish("incomplete", provenance_block(requested_model=model, result=exc.partial, catalog=catalog))
        raise
    status = "truncated" if result.finish_reason == "length" else "ok"
    provenance = provenance_block(requested_model=model, result=result, catalog=catalog)
    if status == "truncated":
        cut = (f"max_completion_tokens={max_tokens}" if max_tokens is not None
               else "a gateway limit; the request carried no max_completion_tokens")
        print(f"finish_reason=length: the answer stopped at {cut}", file=sys.stderr)
    if sink is not None:
        sink.finish(status, provenance)
        return 0
    _emit_json({
        "schema": "open-math-os.ask.v1",
        "status": status,
        "label": label,
        "requested_effort": effort,
        "max_completion_tokens": max_tokens,
        "finish_reason": result.finish_reason,
        "usage": result.usage,
        "error": None,
        "provenance": provenance,
        "output": untrusted_output(result, partial=False),
        "final": True,
    })
    return 0


def _ask_error_envelope(args: argparse.Namespace, exc: OpenMathError) -> dict[str, Any]:
    catalog = exc.catalog
    partial = exc.partial
    return {
        "schema": "open-math-os.ask.v1",
        "status": "blocked" if exc.error_class == "blocked" else ("incomplete" if partial is not None else "error"),
        "label": args.label or "manual",
        "requested_effort": args.effort,
        "max_completion_tokens": args.max_tokens,
        "finish_reason": partial.finish_reason if partial is not None else None,
        "usage": partial.usage if partial is not None else {},
        "error": exc.as_dict(),
        "provenance": provenance_block(requested_model=args.model, result=partial, catalog=catalog,
                                       request_id=exc.request_id),
        "output": untrusted_output(partial, partial=True),
        "final": True,
    }


def command_models(args: argparse.Namespace, environ: Mapping[str, str], origin: str) -> int:
    key = require_model_key(environ)
    store = StateStore(state_dir(environ))
    store.ensure()
    catalog, source = load_catalog(GatewayClient(origin), store, key, refresh=args.refresh, now=_wall(),
                                   limits=Limits())
    block = dict(PROVENANCE)
    block["catalog_fetched_at"] = catalog.get("fetched_at")
    _emit_json({
        "schema": "open-math-os.models.v1",
        "source": source,
        "models": catalog.get("models", []),
        "rejected_entries": catalog.get("rejected_entries", 0),
        "provenance": block,
        "final": True,
    })
    return 0


def _inspect_state(environ: Mapping[str, str]) -> dict[str, Any]:
    try:
        store = StateStore(state_dir(environ))
    except OpenMathError as exc:
        return {"status": exc.code}
    if not store.exists():
        return {"status": "absent"}
    try:
        _owner_private_dir(store.root, repair=False)
        now = _wall()
        catalog = store.read_json(CATALOG_FILE)
        usage = budget_usage(store, now, None)
        return {
            "status": "ok",
            "hard_quota_marker": hard_quota_marker(store) is not None,
            "breaker_open_until": breaker_open_until(store, now),
            "catalog_fetched_at": catalog.get("fetched_at") if _valid_catalog(catalog) else None,
            "budget": {"day_tokens": usage["day_tokens"], "week_tokens": usage["week_tokens"]},
        }
    except (OpenMathError, OSError) as exc:
        return {"status": getattr(exc, "code", "unreadable")}


def _live_checks(origin: str, environ: Mapping[str, str], key: str) -> tuple[dict[str, Any], OpenMathError | None]:
    """GET the OpenAPI document and the catalog. Never probes harness modes."""

    client = GatewayClient(origin)
    live: dict[str, Any] = {}
    try:
        store = StateStore(state_dir(environ))
        store.ensure()
        spec, _spec_request = client.get_json("/openapi.json", key=None)
        text = json.dumps(spec)
        info = spec.get("info") if isinstance(spec, dict) else None
        version = info.get("version") if isinstance(info, dict) else None
        paths = spec.get("paths") if isinstance(spec, dict) else None
        path_names = set(paths) if isinstance(paths, dict) else set()
        live["openapi"] = {
            "status": "ok",
            "version": version if isinstance(version, str) and ERROR_CODE_RE.fullmatch(version) else None,
            "chat_completions_path": bool({"/chat/completions", "/v1/chat/completions"} & path_names),
            "models_path": bool({"/models", "/v1/models"} & path_names),
            # A chat body is `ModelCall`, which names only `model`: the gateway keeps the fields
            # OpenAI's chat completion API defines, so `reasoning_effort` and
            # `max_completion_tokens` are not named in the spec and cannot be checked here.
            "fields_ok": "supported_reasoning_efforts" in text,
        }
        catalog, _source = load_catalog(client, store, key, refresh=True, now=_wall(), limits=Limits())
        models = catalog.get("models", [])
        live["models"] = {
            "status": "ok",
            "count": len(models),
            "rejected_entries": catalog.get("rejected_entries", 0),
            "fields_ok": bool(models) and all(entry.get("efforts_declared") for entry in models),
            "catalog_fetched_at": catalog.get("fetched_at"),
        }
    except OpenMathError as exc:
        live["error"] = exc.as_dict()
        return live, exc
    ok = (live["openapi"]["chat_completions_path"] and live["openapi"]["models_path"]
          and live["openapi"]["fields_ok"] and live["models"]["fields_ok"])
    live["status"] = "ok" if ok else "degraded"
    if not ok:
        return live, OpenMathError("client_bug", "api_contract_mismatch")
    return live, None


def command_doctor(args: argparse.Namespace, environ: Mapping[str, str], origin: str) -> int:
    key_info = inspect_model_key(environ)
    platform_present = bool(environ.get(PLATFORM_KEY_ENV))
    if key_info["status"] == "present":
        key_info["source_fingerprint"] = key_source_fingerprint(environ, str(environ[KEY_ENV]))
    else:
        key_info["source_fingerprint"] = "not_checked"
    payload: dict[str, Any] = {
        "schema": "open-math-os.doctor.v1",
        "version": VERSION,
        "api_origin": API_ORIGIN,
        "user_agent": USER_AGENT,
        "key": key_info,
        "platform_key_present": platform_present,
        "state": _inspect_state(environ),
        "live": None,
        "network_attempted": False,
    }
    failure: OpenMathError | None = None
    if platform_present:
        failure = OpenMathError("auth", "platform_key_present")
    elif key_info["status"] != "present":
        failure = OpenMathError("auth", f"key_{key_info['status']}")
    if args.live and failure is None:
        payload["network_attempted"] = True
        payload["live"], failure = _live_checks(origin, environ, str(environ[KEY_ENV]))
        payload["state"] = _inspect_state(environ)
    payload["status"] = "ok" if failure is None else "error"
    payload["problem"] = failure.as_dict() if failure is not None else None
    payload["provenance"] = dict(PROVENANCE)
    payload["final"] = True
    _emit_json(payload)
    if failure is not None:
        print(failure.stderr_line(), file=sys.stderr)
        return failure.exit_code
    return 0


def _read_key_from_stdin() -> str:
    if sys.stdin is not None and sys.stdin.isatty():
        return getpass.getpass("Open Math model key: ")
    raw = sys.stdin.buffer.read(4097) if sys.stdin is not None else b""
    if len(raw) > 4096:
        raise OpenMathError("usage", "key_input_too_large")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OpenMathError("usage", "key_input_not_utf8") from exc


def command_setup_key(args: argparse.Namespace, environ: Mapping[str, str], origin: str) -> int:
    del origin
    if os.name != "posix":
        raise OpenMathError("state", "setup_key_posix_only")
    key = (claude_secret_key(environ) if args.from_claude_secrets else _read_key_from_stdin()).strip()
    if key.startswith(PLATFORM_KEY_PREFIX):
        raise OpenMathError("usage", "platform_key_refused")
    if not key.startswith(KEY_PREFIX) or not KEY_BODY_RE.fullmatch(key[len(KEY_PREFIX):]):
        raise OpenMathError("usage", "key_prefix_or_shape_invalid")
    loader = _load_secret_loader(environ)
    text = f"{KEY_ENV}={key}\n"
    allowed = frozenset({KEY_ENV})
    try:
        loader.parse_secret_env_text(text, allowed_keys=allowed, source=KEY_FILE_NAME)
    except ValueError as exc:
        raise OpenMathError("usage", "key_rejected_by_loader") from exc
    directory = config_dir(environ)
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        _owner_private_dir(directory, repair=False)
    except OSError as exc:
        raise OpenMathError("state", "config_dir_unavailable") from exc
    path = directory / KEY_FILE_NAME
    data = text.encode("utf-8")
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
    except FileExistsError as exc:
        raise OpenMathError("state", "key_file_exists") from exc
    except OSError as exc:
        raise OpenMathError("state", "key_file_unwritable") from exc
    try:
        _write_all(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    try:
        reread = loader.read_protected_secret_env(str(path))
        parsed = loader.parse_secret_env_text(reread, allowed_keys=allowed, source=KEY_FILE_NAME)
    except ValueError as exc:
        os.unlink(path)
        raise OpenMathError("state", "key_file_rejected_by_loader") from exc
    if parsed.get(KEY_ENV) != key:
        os.unlink(path)
        raise OpenMathError("state", "key_file_mismatch")
    _emit_json({"path": str(path), "bytes": len(data)})
    return 0


def command_log(args: argparse.Namespace, environ: Mapping[str, str], origin: str) -> int:
    del origin
    if not 1 <= args.limit <= 1000:
        raise OpenMathError("usage", "limit_out_of_range")
    store = StateStore(state_dir(environ))
    records: list[dict[str, Any]] = []
    usage = {"day_tokens": 0, "week_tokens": 0}
    if store.exists():
        _owner_private_dir(store.root, repair=False)
        for name in [f"{SENDS_LOG}.{index}" for index in range(SENDS_KEEP, 0, -1)] + [SENDS_LOG]:
            records.extend(store.read_jsonl(name, SENDS_MAX_BYTES * 2))
        full = budget_usage(store, _wall(), None)
        usage = {"day_tokens": full["day_tokens"], "week_tokens": full["week_tokens"]}
    _emit_json({
        "schema": "open-math-os.log.v1",
        "records": records[-args.limit:],
        "budget": usage,
        "content_logged": False,
        "final": True,
    })
    return 0


_SMOKE_SSE = (
    b": keep-alive\n\n"
    b'data: {"id":"x","model":"vendor/model-1","choices":[{"index":0,"delta":{"reasoning":"r1"}}]}\n\n'
    b'data: {"id":"x","model":"vendor/model-1","choices":[{"index":0,"delta":{"content":"A\\u001b[31mB"}}]}\n\n'
    b'data: {"id":"x","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    b'data: {"id":"x","choices":[],"usage":{"prompt_tokens":3,"completion_tokens":4,"total_tokens":7}}\n\n'
    b"data: [DONE]\n\n"
)


def _smoke_checks(environ: Mapping[str, str]) -> list[tuple[str, bool]]:
    checks: list[tuple[str, bool]] = []

    def refused(origin: str) -> bool:
        try:
            validated_origin(origin)
        except OpenMathError:
            return True
        return False

    checks.append(("origin_fixed", validated_origin(API_ORIGIN) == API_ORIGIN
                   and refused("http://example.org") and refused("https://example.org")))
    checks.append(("user_agent", re.fullmatch(r"ai-agents-skills-open-math-os/\d+\.\d+\.\d+", USER_AGENT) is not None))
    checks.append(("no_redirect_or_proxy_handler", not any(
        isinstance(handler, (urllib.request.HTTPRedirectHandler, urllib.request.HTTPErrorProcessor))
        or (isinstance(handler, urllib.request.ProxyHandler) and handler.proxies)
        for handler in build_opener(API_ORIGIN).handlers)))
    unset = build_parser().parse_args(["ask", "--model", "vendor/model-1", "--prompt", "x"])
    checks.append(("limits_off_by_default", ask_limits(unset) == Limits()))
    checks.append(("model_id_rule", MODEL_ID_RE.fullmatch("deepseek/deepseek-v4.1-flash") is not None
                   and MODEL_ID_RE.fullmatch("../x") is None))
    synthetic_token = "gh" + "p_" + "Q7" * 18
    synthetic_email = "smoke" + "@" + "example" + ".org"
    clean = not screen_outbound([{"role": "user", "content": "Count spanning trees of the wheel W_6."}], {})
    flagged = screen_outbound([{"role": "user", "content": f"{synthetic_token} {synthetic_email}"}], {})
    checks.append(("outbound_screen", clean and "content:github-token" in flagged and "pii:email" in flagged))
    projected = str(environ.get(KEY_ENV) or "")
    if len(projected) >= 6:
        checks.append(("projected_key_screened", f"environment:{KEY_ENV}" in screen_outbound(
            [{"role": "user", "content": "x " + projected}], environ)))
    checks.append(("terminal_controls_stripped",
                   strip_terminal_controls("A\x1b[31mB\x1b]0;t\x07C\x00D\x9bE\n") == "ABCD\n"))
    parsed = ChatResult()
    for data in iter_sse_data(_SMOKE_SSE.splitlines(keepends=True)):
        feed_sse_event(parsed, data, None)
    checks.append(("sse_parser", parsed.done and parsed.text == "A\x1b[31mB" and parsed.reasoning == ["r1"]
                   and parsed.finish_reason == "stop" and parsed.usage.get("total_tokens") == 7))
    expected = {401: "auth", 402: "hard_quota", 429: "quota", 400: "bad_request", 405: "client_bug", 503: "transient"}
    classified = all(classify_http_error(status, b"{}").error_class == name for status, name in expected.items())
    edge = classify_http_error(403, b"error code: 1010")
    checks.append(("error_classes", classified and edge.error_class == "edge_block" and edge.code == "1010"
                   and EXIT_CODES["hard_quota"] == 18 and EXIT_CODES["quota"] == 5 and EXIT_CODES["auth"] == 7))
    checks.append(("provenance_static", PROVENANCE["independence_eligible"] is False
                   and PROVENANCE["family"] == "unverified"))
    return checks


def command_smoke(args: argparse.Namespace, environ: Mapping[str, str], origin: str) -> int:
    del args, origin
    checks = _smoke_checks(environ)
    failed = [name for name, ok in checks if not ok]
    _emit_json({
        "schema": "open-math-os.smoke.v1",
        "status": "ok" if not failed else "failed",
        "smoke_mode": "offline",
        "checks": [name for name, _ok in checks],
        "failed": failed,
        "network_required": False,
        "live_api_attempted": False,
        "config_written": False,
        "state_written": False,
        "auth_status": inspect_model_key(environ)["status"] if environ.get(KEY_ENV) is not None else "missing",
        "provenance": dict(PROVENANCE),
        "final": True,
    })
    return 0 if not failed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="open-math-os", description="Open Math OS model client")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="check the key and local state; --live also checks the API")
    doctor.add_argument("--live", action="store_true")
    models = sub.add_parser("models", help="show the model catalog")
    models.add_argument("--refresh", action="store_true")
    ask = sub.add_parser("ask", help="send one prompt to one model")
    ask.add_argument("--model", required=True)
    ask.add_argument("--effort", default="low", choices=EFFORT_NAMES)
    system = ask.add_mutually_exclusive_group()
    system.add_argument("--system")
    system.add_argument("--system-file")
    prompt = ask.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file")
    ask.add_argument("--stream", action=argparse.BooleanOptionalAction, default=True)
    ask.add_argument("--json", action="store_true")
    ask.add_argument("--label")
    limits = ask.add_argument_group(
        "optional limits", "None has a default: each limit applies only when its flag is given.")
    for flag, field, kind, metavar, text in LIMIT_FLAGS:
        limits.add_argument(flag, dest=field, type=kind, metavar=metavar, help=text)
    setup = sub.add_parser("setup-key", help="write the model key file")
    source = setup.add_mutually_exclusive_group(required=True)
    source.add_argument("--from-claude-secrets", action="store_true")
    source.add_argument("--from-stdin", action="store_true")
    log = sub.add_parser("log", help="show the metadata-only send log")
    log.add_argument("--limit", type=int, default=20)
    sub.add_parser("smoke", help="offline self-check")
    return parser


COMMANDS: dict[str, Callable[[argparse.Namespace, Mapping[str, str], str], int]] = {
    "doctor": command_doctor,
    "models": command_models,
    "ask": command_ask,
    "setup-key": command_setup_key,
    "log": command_log,
    "smoke": command_smoke,
}


def main(argv: list[str] | None = None, *, _origin: str | None = None, _environ: Mapping[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    environ = dict(os.environ if _environ is None else _environ)
    origin = API_ORIGIN if _origin is None else _origin
    try:
        return COMMANDS[args.command](args, environ, origin)
    except OpenMathError as exc:
        print(exc.stderr_line(), file=sys.stderr)
        if args.command == "ask" and getattr(args, "json", False):
            _emit_json(_ask_error_envelope(args, exc))
        return exc.exit_code
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - never print remote text or values
        print(f"refused internal: {type(exc).__name__}", file=sys.stderr)
        return EXIT_CODES["internal"]


if __name__ == "__main__":
    raise SystemExit(main())
