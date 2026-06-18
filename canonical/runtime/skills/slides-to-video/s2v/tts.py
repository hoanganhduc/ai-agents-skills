"""Language-aware TTS engine ladder.

Order is decided by ``languages.engine_ladder`` (online-first, dropping engines
that lack a voice for the language -- e.g. Vietnamese never routes into Kokoro).
On a 403 / empty-audio / import failure the ladder falls through to the next
engine. Every engine writes a file that ``audio.to_wav`` normalizes.

All engine SDK imports are LAZY so this module imports under base Python; only
actual synthesis needs the venv packages.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Optional

from . import languages
from .audio import to_wav


class TTSUnavailable(RuntimeError):
    pass


def _venv_script(name: str) -> str | None:
    suffix = ".exe" if os.name == "nt" else ""
    candidate = Path(sys.executable).resolve().parent / f"{name}{suffix}"
    return str(candidate) if candidate.exists() else None


@dataclass
class SynthResult:
    wav_path: Path
    engine: str
    voice: str


def _synth_edge(text: str, voice: Optional[str], locale: str, prosody: dict, out_mp3: Path) -> str:
    import asyncio

    import edge_tts  # noqa: F401  (lazy; online)

    async def _run(chosen: str) -> None:
        communicate = edge_tts.Communicate(
            text, chosen,
            rate=prosody.get("rate", "+0%"),
            volume=prosody.get("volume", "+0%"),
            pitch=prosody.get("pitch", "+0Hz"),
        )
        await communicate.save(str(out_mp3))

    chosen = voice
    if not chosen:  # generic language: enumerate live and match the locale
        voices = asyncio.run(edge_tts.list_voices())
        matches = [v["ShortName"] for v in voices if v.get("Locale", "").lower() == locale.lower()]
        if not matches:
            lang = languages.lang_of(locale)
            matches = [v["ShortName"] for v in voices if v.get("Locale", "").lower().startswith(lang + "-")]
        if not matches:
            raise TTSUnavailable(f"edge-tts has no voice for locale {locale!r}")
        chosen = matches[0]
    asyncio.run(_run(chosen))
    if not out_mp3.exists() or out_mp3.stat().st_size == 0:
        raise TTSUnavailable("edge-tts returned no audio (cloud IP filtering / endpoint change?)")
    return chosen


def _synth_kokoro(text: str, voice: Optional[str], locale: str, out_wav: Path) -> str:
    import soundfile as sf
    from kokoro import KPipeline

    lang_code = {"en": "a", "es": "e", "fr": "f", "hi": "h", "it": "i",
                 "ja": "j", "pt": "p", "zh": "z"}.get(languages.lang_of(locale))
    if lang_code is None:
        raise TTSUnavailable(f"Kokoro has no voice for {locale!r}")
    chosen = voice or "af_heart"
    pipeline = KPipeline(lang_code=lang_code)
    chunks = [audio for _, _, audio in pipeline(text, voice=chosen)]
    if not chunks:
        raise TTSUnavailable("Kokoro produced no audio")
    import numpy as np

    sf.write(str(out_wav), np.concatenate(chunks), 24000)
    return chosen


def _synth_piper(text: str, voice: Optional[str], out_wav: Path) -> str:
    import shutil
    import subprocess

    chosen = voice or ""
    if not chosen:
        raise TTSUnavailable("Piper requires a voice id for this language")
    piper = shutil.which("piper") or _venv_script("piper")
    if not piper:
        raise TTSUnavailable("piper CLI not found on PATH or in the active runtime venv")
    subprocess.run([piper, "-m", chosen, "-f", str(out_wav)],
                   input=text, text=True, check=True, capture_output=True)
    return chosen


def synth(text: str, locale: str, role: str, engine_policy: str, work: Path,
          index: int, explicit_voice: Optional[str] = None,
          gender: Optional[str] = None) -> SynthResult:
    """Try the language-aware ladder; return the first engine that yields audio."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    prosody = languages.prosody_for(locale, role)
    errors: list[str] = []
    for engine in languages.engine_ladder(locale, engine_policy):
        voice = languages.resolve_voice(locale, engine, role, gender, explicit_voice)
        try:
            if engine == "edge":
                raw = work / f"slide_{index:04d}_edge.mp3"
                used = _synth_edge(text, voice, locale, prosody, raw)
            elif engine == "kokoro":
                raw = work / f"slide_{index:04d}_kokoro.wav"
                used = _synth_kokoro(text, voice, locale, raw)
            elif engine == "piper":
                raw = work / f"slide_{index:04d}_piper.wav"
                used = _synth_piper(text, voice, raw)
            else:
                continue
        except Exception as exc:  # fall through to the next engine
            errors.append(f"{engine}: {exc}")
            continue
        wav = work / f"slide_{index:04d}.wav"
        to_wav(raw, wav)
        return SynthResult(wav_path=wav, engine=engine, voice=used)
    raise TTSUnavailable(f"no TTS engine could synthesize slide {index} ({locale}): " + " | ".join(errors))
