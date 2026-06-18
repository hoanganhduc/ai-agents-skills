"""Language registry: locale -> voices, role prosody, and math verbalization.

The pipeline core is language-agnostic. Everything language-specific lives in
``s2v/languages/<lang>.json`` data files, so adding a first-class language is a
data change (a JSON lexicon + a covering font), never a code change.

Tier A (tuned): English, Vietnamese -> shipped lexicons with math + voices.
Tier B (generic): any other language -> edge-tts online via live voice
enumeration; offline only where a lexicon supplies a known Piper/Kokoro voice.
Tier C: math verbalization is the per-locale data; un-tuned languages fall back
to a passthrough and are corrected by the human at the approval gate.

Pure standard library only.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent / "languages"
TUNED_LANGS = ("en", "vi")
DEFAULT_PROSODY = {"rate": "+0%", "pitch": "+0Hz", "volume": "+0%"}


def lang_of(locale: str) -> str:
    """``en-US`` -> ``en``; ``vi-VN`` -> ``vi``."""
    return (locale or "").split("-")[0].lower()


@lru_cache(maxsize=None)
def _load_file(lang: str) -> dict:
    path = DATA_DIR / f"{lang}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def load_lexicon(locale: str) -> dict:
    """Merge the generic lexicon with the language-specific one (language wins)."""
    generic = _load_file("generic")
    lang = _load_file(lang_of(locale))
    merged = dict(generic)
    merged.update(lang)
    for key in ("voices", "roles", "math"):
        nested = dict(generic.get(key, {}))
        nested.update(lang.get(key, {}))
        merged[key] = nested
    return merged


def is_tuned(locale: str) -> bool:
    return lang_of(locale) in TUNED_LANGS


def font_hint(locale: str) -> str:
    preferred = load_lexicon(locale).get("font_hint", "Noto Sans")
    try:
        from .fonts import best_caption_font

        return best_caption_font(preferred)
    except Exception:
        return preferred


def engines_with_voice(locale: str) -> set[str]:
    """Which engines we can actually drive for this language.

    edge-tts is always an online candidate (voices resolve via live
    enumeration even without a shipped mapping); kokoro/piper only when the
    lexicon names a voice for the language.
    """
    voices = load_lexicon(locale).get("voices", {})
    out = {"edge"}
    for engine in ("kokoro", "piper"):
        if voices.get(engine):
            out.add(engine)
    return out


def engine_ladder(locale: str, policy: str = "auto") -> list[str]:
    """Language-aware engine order.

    ``auto`` is online-first (edge) then offline (kokoro, piper), dropping any
    engine that has no voice for this language -- this is the fix that keeps
    Vietnamese (no Kokoro voice) from ever routing into Kokoro.
    """
    if policy in ("edge", "kokoro", "piper"):
        return [policy]
    has = engines_with_voice(locale)
    return [engine for engine in ("edge", "kokoro", "piper") if engine in has]


def resolve_voice(
    locale: str,
    engine: str,
    role: str = "presenter",
    gender: Optional[str] = None,
    explicit: Optional[str] = None,
) -> Optional[str]:
    """Pick a voice id for (locale, engine). ``None`` -> resolve at runtime.

    For edge-tts a ``None`` result means "enumerate voices for this locale live".
    """
    if explicit:
        return explicit
    voices = load_lexicon(locale).get("voices", {}).get(engine)
    if not voices:
        return None
    if gender and gender in voices:
        return voices[gender]
    return voices.get("default") or next(iter(voices.values()), None)


def prosody_for(locale: str, role: str) -> dict:
    roles = load_lexicon(locale).get("roles", {})
    return dict(roles.get(role) or roles.get("presenter") or DEFAULT_PROSODY)


def verbalize_math(text: str, locale: str) -> str:
    """Rewrite math symbols/notation into speakable words for the locale.

    edge-tts/Piper/Kokoro take plain text (no SSML ``say-as``), so this runs at
    transcript-draft time and the result is what the human reviews/approves.
    Returns the input unchanged for languages without a math lexicon.
    """
    math = load_lexicon(locale).get("math", {})
    for pattern in math.get("patterns", []):
        text = re.sub(pattern["re"], pattern["repl"], text)
    symbols = math.get("symbols", {})
    for sym in sorted(symbols, key=len, reverse=True):
        if sym:
            text = text.replace(sym, symbols[sym])
    return re.sub(r"\s+", " ", text).strip()
