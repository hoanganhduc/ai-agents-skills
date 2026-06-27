"""Cross-OS Chromium / Chrome / Edge detector (detect-only; installs nothing).

Resolution order: the ``URL_TO_SCREENSHOT_BROWSER`` override, then ``PATH``
names, then per-OS install-location candidates. The logic is parameterized by an
injectable ``os_name`` and ``candidate_root`` so all three OS layouts can be
exercised deterministically from a single host (a Linux CI runner can verify the
Windows ``%PROGRAMFILES(X86)%`` globs and the macOS app-bundle paths). Fail-soft:
returns a record with ``path=None`` and ``status="missing"`` rather than raising.
"""

from __future__ import annotations

import glob
import os
import shutil
from dataclasses import dataclass, field
from typing import Callable, Sequence

# PATH-resolvable command names, per OS, in preference order.
_PATH_NAMES: dict[str, list[str]] = {
    "posix": [
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
        "chrome",
        "microsoft-edge",
        "microsoft-edge-stable",
    ],
    "darwin": ["chromium", "google-chrome", "google-chrome-stable", "microsoft-edge"],
    "nt": ["chrome.exe", "msedge.exe", "chromium.exe"],
}

# Absolute install-location candidates (may contain env vars / globs), per OS.
_LOCATION_CANDIDATES: dict[str, list[str]] = {
    "posix": [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/snap/bin/chromium",
        "/opt/google/chrome/chrome",
        "/opt/microsoft/msedge/msedge",
    ],
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ],
    "nt": [
        r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
        r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe",
        r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe",
        r"%LOCALAPPDATA%\Chromium\Application\chrome.exe",
    ],
}


@dataclass
class BrowserInfo:
    path: str | None
    family: str
    status: str = "available"
    channel: str = ""
    version: str = ""
    source: str = ""
    candidates_checked: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "family": self.family,
            "status": self.status,
            "channel": self.channel,
            "version": self.version,
            "source": self.source,
        }


def _family_for(name: str) -> str:
    lowered = name.lower()
    if "edge" in lowered or "msedge" in lowered:
        return "edge"
    if "chromium" in lowered:
        return "chromium"
    return "chrome"


def _normalize_os(os_name: str) -> str:
    if os_name == "nt":
        return "nt"
    if os_name == "darwin":
        return "darwin"
    return "posix"


def detect_browser(
    *,
    os_name: str | None = None,
    candidate_root: str | None = None,
    env: dict[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
    exists: Callable[[str], bool] | None = None,
    glob_fn: Callable[[str], Sequence[str]] | None = None,
) -> BrowserInfo:
    """Resolve a usable browser, fail-soft.

    ``candidate_root`` (when set) is prepended to every absolute install-location
    candidate so synthetic OS layouts can be probed under a temp dir without
    touching the real filesystem. ``which``/``exists``/``glob_fn`` are injectable
    for the same reason.
    """
    env = dict(os.environ if env is None else env)
    os_key = _normalize_os(os_name if os_name is not None else _platform_os_name())
    which = which or shutil.which
    exists = exists or os.path.exists
    glob_fn = glob_fn or glob.glob

    checked: list[str] = []

    override = env.get("URL_TO_SCREENSHOT_BROWSER")
    if override:
        checked.append(override)
        if exists(override):
            return BrowserInfo(
                path=override,
                family=_family_for(override),
                source="env-override",
                candidates_checked=checked,
            )

    for name in _PATH_NAMES.get(os_key, []):
        checked.append(name)
        found = which(name)
        if found:
            return BrowserInfo(
                path=found,
                family=_family_for(name),
                source="path",
                candidates_checked=checked,
            )

    for raw in _LOCATION_CANDIDATES.get(os_key, []):
        expanded = _expandvars(raw, env)
        rooted = _apply_root(expanded, candidate_root)
        checked.append(rooted)
        for resolved in _resolve_candidate(rooted, exists, glob_fn):
            return BrowserInfo(
                path=resolved,
                family=_family_for(resolved),
                source="install-location",
                candidates_checked=checked,
            )

    return BrowserInfo(
        path=None,
        family="",
        status="missing",
        source="",
        candidates_checked=checked,
    )


def _resolve_candidate(
    candidate: str,
    exists: Callable[[str], bool],
    glob_fn: Callable[[str], Sequence[str]],
) -> list[str]:
    if any(ch in candidate for ch in "*?[]"):
        return sorted(match for match in glob_fn(candidate))
    return [candidate] if exists(candidate) else []


def _expandvars(text: str, env: dict[str, str]) -> str:
    out = text
    for key, value in env.items():
        out = out.replace(f"%{key}%", value)
    # Leave unresolved %VAR% tokens in place; they simply will not match a file.
    return out


def _apply_root(path: str, candidate_root: str | None) -> str:
    if not candidate_root:
        return path
    # Strip a Windows-style "X:" drive prefix portably (os.path.splitdrive does
    # not recognize one on POSIX), then re-root the remaining tail under the
    # synthetic candidate_root so all three OS layouts can be probed on one host.
    tail = path
    if len(tail) >= 2 and tail[1] == ":" and tail[0].isalpha():
        tail = tail[2:]
    parts = [p for p in tail.replace("\\", "/").split("/") if p]
    return os.path.join(candidate_root, *parts)


def _platform_os_name() -> str:
    import sys

    if os.name == "nt":
        return "nt"
    if sys.platform == "darwin":
        return "darwin"
    return "posix"
