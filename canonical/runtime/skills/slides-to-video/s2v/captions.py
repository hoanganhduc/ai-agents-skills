"""Engine-agnostic captions.

Caption timing is derived from the *measured* per-slide audio durations and
re-based by each slide's cumulative start, so captions stay correct no matter
which TTS engine produced each slide (edge-tts word boundaries are a bonus we
do not depend on). Text is NFC-normalized so Vietnamese diacritics render in
players and in libass burn-in.

Pure standard library only.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .model import SegmentTiming, SlidePlan

_SENTENCE_RE = re.compile(r"(?<=[.!?。…?!])\s+")


@dataclass
class Cue:
    start: float
    end: float
    text: str


def split_sentences(text: str) -> list[str]:
    text = " ".join(text.split())
    if not text:
        return []
    parts = [p.strip() for p in _SENTENCE_RE.split(text) if p.strip()]
    return parts or [text]


def segment_cues(text: str, seg_start: float, seg_duration: float) -> list[Cue]:
    """Distribute a slide's text across its window, weighted by sentence length."""
    sentences = split_sentences(text)
    if not sentences or seg_duration <= 0:
        return []
    total = sum(len(s) for s in sentences) or 1
    cues: list[Cue] = []
    cursor = seg_start
    for sentence in sentences:
        dur = seg_duration * (len(sentence) / total)
        cues.append(Cue(start=cursor, end=cursor + dur, text=sentence))
        cursor += dur
    cues[-1].end = seg_start + seg_duration  # snap to avoid float drift
    return cues


def deck_cues(plans: list[SlidePlan], timings: list[SegmentTiming]) -> list[Cue]:
    if len(plans) != len(timings):
        raise ValueError("deck_cues requires one timing per plan")
    cues: list[Cue] = []
    for plan, timing in zip(plans, timings):
        cues.extend(segment_cues(plan.transcript, timing.start, timing.duration))
    return cues


def _fmt_ts(seconds: float, sep: str) -> str:
    if seconds < 0:
        seconds = 0.0
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{millis:03d}"


def to_srt(cues: list[Cue]) -> str:
    blocks = []
    for i, cue in enumerate(cues, start=1):
        blocks.append(
            f"{i}\n{_fmt_ts(cue.start, ',')} --> {_fmt_ts(cue.end, ',')}\n"
            f"{unicodedata.normalize('NFC', cue.text)}\n"
        )
    return "\n".join(blocks)


def to_vtt(cues: list[Cue]) -> str:
    lines = ["WEBVTT", ""]
    for cue in cues:
        lines.append(f"{_fmt_ts(cue.start, '.')} --> {_fmt_ts(cue.end, '.')}")
        lines.append(unicodedata.normalize("NFC", cue.text))
        lines.append("")
    return "\n".join(lines)


def write_captions(out_dir: Path, cues: list[Cue], formats: list[str]) -> list[Path]:
    out_dir = Path(out_dir)
    written: list[Path] = []
    if "srt" in formats:
        path = out_dir / "captions.srt"
        path.write_text(to_srt(cues), encoding="utf-8")
        written.append(path)
    if "vtt" in formats:
        path = out_dir / "captions.vtt"
        path.write_text(to_vtt(cues), encoding="utf-8")
        written.append(path)
    return written
