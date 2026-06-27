"""Cookie-consent overlay dismissal (CDP DOM op) and the blank-fallback matrix.

Consent removal is a Tier-2 (CDP) ``Runtime.evaluate`` DOM operation: it targets
common consent/cookie overlays only -- never age gates, paywalls, or login walls.
After removal an ``innerText``-collapse guard checks the page did not blank.

The fallback matrix (S6/C2) is a pure function so the selftest can assert that a
``--full-page`` request never silently degrades to a viewport capture.
"""

from __future__ import annotations

# Selectors for common consent/cookie overlays. Intentionally scoped to consent
# affordances; this list never includes age/paywall/login selectors.
CONSENT_SELECTORS: tuple[str, ...] = (
    "#cookie-banner",
    "#cookie-consent",
    "#cookieConsent",
    "#onetrust-banner-sdk",
    "#onetrust-consent-sdk",
    ".cookie-banner",
    ".cookie-consent",
    ".cc-window",
    ".qc-cmp2-container",
    "#CybotCookiebotDialog",
    "[aria-label='cookieconsent']",
    "[data-testid='cookie-policy-banner']",
    "div[class*='cookie'][class*='consent']",
)

# What to do when CDP + consent removal blanks the page, keyed on capture mode.
FALLBACK_ONESHOT = "oneshot"  # viewport request: drop to a one-shot viewport capture
FALLBACK_CDP_NO_CONSENT = "cdp-no-consent"  # full-page request: retry full-page in CDP, no consent removal
FALLBACK_UNVERIFIED = "unverified"  # still blank: emit BLANK_OUTPUT / UNVERIFIED


def build_removal_expression(selectors: tuple[str, ...] = CONSENT_SELECTORS) -> str:
    """Build the ``Runtime.evaluate`` JS that removes consent overlays.

    Returns the script source; the engine sends it over CDP. Pure string builder,
    so the selftest validates it without a browser.
    """
    joined = ",".join(repr(sel) for sel in selectors)
    return (
        "(() => {"
        f"const sels=[{joined}];"
        "let removed=0;"
        "for (const s of sels) {"
        "  for (const el of document.querySelectorAll(s)) { el.remove(); removed++; }"
        "}"
        "document.documentElement.style.overflow='auto';"
        "return removed;"
        "})()"
    )


def consent_blank_fallback(full_page: bool, *, retried_without_consent: bool = False) -> str:
    """Decide the next action after a consent-removal blanked the page.

    For a viewport request, fall back to a one-shot viewport capture. For a
    ``--full-page`` request, re-attempt full-page in CDP WITHOUT consent removal
    (a one-shot cannot do full-page); if that retry already happened and the page
    is still blank, emit UNVERIFIED rather than mislabeling a viewport capture as
    full-page.
    """
    if not full_page:
        return FALLBACK_ONESHOT
    if retried_without_consent:
        return FALLBACK_UNVERIFIED
    return FALLBACK_CDP_NO_CONSENT
