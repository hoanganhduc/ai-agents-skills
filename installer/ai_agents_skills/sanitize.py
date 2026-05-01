from __future__ import annotations

import os
import re
from pathlib import Path


TOKEN_PATTERNS = (
    re.compile(r"gh[opsu]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"xox[baprs]-[0-9A-Za-z-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |PGP |PRIVATE )?PRIVATE KEY-----.*?-----END (?:RSA |OPENSSH |PGP |PRIVATE )?PRIVATE KEY-----", re.DOTALL),
)

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
LINUX_HOME_PATTERN = re.compile(r"/home/[^/\s`'\")]+")
WINDOWS_HOME_PATTERN = re.compile(r"(?:/windows/Users|/mnt/[a-z]/Users)/[^/\s`'\")]+", re.IGNORECASE)
WINDOWS_NATIVE_HOME_PATTERN = re.compile(r"[A-Za-z]:\\Users\\[^\\\s`'\")]+")


def sanitize_text(text: str, canonical_name: str | None = None) -> str:
    result = text
    home = str(Path.home())
    if home:
        result = result.replace(home, "<HOME>")
    username = os.environ.get("USER") or os.environ.get("USERNAME")
    if username:
        result = re.sub(rf"\b{re.escape(username)}\b", "<USER>", result)

    result = WINDOWS_HOME_PATTERN.sub("<WINDOWS_HOME>", result)
    result = WINDOWS_NATIVE_HOME_PATTERN.sub("<WINDOWS_HOME>", result)
    result = LINUX_HOME_PATTERN.sub("<LINUX_HOME>", result)
    result = EMAIL_PATTERN.sub("<EMAIL>", result)
    for pattern in TOKEN_PATTERNS:
        result = pattern.sub("<REDACTED_SECRET>", result)
    if canonical_name:
        result = normalize_frontmatter_name(result, canonical_name)
    return result


def normalize_frontmatter_name(text: str, canonical_name: str) -> str:
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return text
    for index in range(1, min(len(lines), 25)):
        if lines[index].strip() == "---":
            break
        if lines[index].startswith("name:"):
            lines[index] = f"name: {canonical_name}"
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return text


def has_sensitive_material(text: str) -> bool:
    if EMAIL_PATTERN.search(text):
        return True
    if (
        LINUX_HOME_PATTERN.search(text)
        or WINDOWS_HOME_PATTERN.search(text)
        or WINDOWS_NATIVE_HOME_PATTERN.search(text)
    ):
        return True
    home = str(Path.home())
    if home and home in text:
        return True
    for pattern in TOKEN_PATTERNS:
        if pattern.search(text):
            return True
    return False
