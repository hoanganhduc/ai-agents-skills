"""JSON-serializable data model for the slides-to-video pipeline.

Pure standard library only. Every dataclass round-trips through ``to_dict`` /
``from_dict`` so the three phases (Analyze, Transcript, Render) can persist and
reload state from the per-deck working directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

EFFECT_TYPES = ("ken_burns", "highlight", "spotlight", "laser", "reveal")
ROLES = ("presenter", "teacher", "lecturer", "narrator", "pitch")
ENGINE_POLICIES = ("auto", "edge", "kokoro", "piper")
SOURCE_FORMATS = ("png", "pdf", "pptx")


@dataclass
class EffectSpec:
    """A single Tier-1 visual effect applied to one slide's clip.

    ``start``/``duration`` are wall-clock seconds inside that slide's narration
    window; ``duration=None`` means "until the end of the slide".
    """

    type: str
    params: dict[str, Any] = field(default_factory=dict)
    start: float = 0.0
    duration: Optional[float] = None

    def validate(self) -> None:
        if self.type not in EFFECT_TYPES:
            raise ValueError(f"unknown effect type {self.type!r}; valid: {EFFECT_TYPES}")
        if self.start < 0:
            raise ValueError("effect.start must be >= 0")
        if self.duration is not None and self.duration <= 0:
            raise ValueError("effect.duration must be > 0 when set")

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "params": self.params, "start": self.start, "duration": self.duration}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EffectSpec":
        return cls(
            type=d["type"],
            params=dict(d.get("params", {})),
            start=float(d.get("start", 0.0)),
            duration=None if d.get("duration") is None else float(d["duration"]),
        )


@dataclass
class SlideRecord:
    """One slide frame plus the text used to seed its narration draft."""

    index: int
    image_path: str
    seed_text: str = ""
    source: str = ""
    width: int = 0
    height: int = 0
    flags: dict[str, Any] = field(default_factory=dict)
    clip_path: Optional[str] = None  # if set, this slide's visual is a pre-rendered video clip

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "image_path": self.image_path,
            "seed_text": self.seed_text,
            "source": self.source,
            "width": self.width,
            "height": self.height,
            "flags": self.flags,
            "clip_path": self.clip_path,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SlideRecord":
        return cls(
            index=int(d["index"]),
            image_path=d.get("image_path", ""),
            seed_text=d.get("seed_text", ""),
            source=d.get("source", ""),
            width=int(d.get("width", 0)),
            height=int(d.get("height", 0)),
            flags=dict(d.get("flags", {})),
            clip_path=d.get("clip_path"),
        )


@dataclass
class Deck:
    """Phase-1 output: the ordered slide frames and where they came from."""

    source_format: str
    slides: list[SlideRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"source_format": self.source_format, "slides": [s.to_dict() for s in self.slides]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Deck":
        return cls(
            source_format=d["source_format"],
            slides=[SlideRecord.from_dict(s) for s in d.get("slides", [])],
        )


@dataclass
class Config:
    """Phase-1 decisions, persisted to ``config.json`` and echoed for approval."""

    language: str = "en-US"
    role: str = "presenter"
    engine_policy: str = "auto"
    voice: Optional[str] = None  # explicit voice override; else resolved per language
    resolution: str = "1920x1080"
    fps: int = 30
    lead_pad: float = 0.4
    tail_pad: float = 0.6
    captions: list[str] = field(default_factory=lambda: ["srt", "vtt"])
    burn_captions: bool = False
    approved: bool = False
    approved_transcript_sha: Optional[str] = None

    def validate(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"unknown role {self.role!r}; valid: {ROLES}")
        if self.engine_policy not in ENGINE_POLICIES:
            raise ValueError(f"unknown engine_policy {self.engine_policy!r}; valid: {ENGINE_POLICIES}")
        if "x" not in self.resolution:
            raise ValueError("resolution must look like WIDTHxHEIGHT, e.g. 1920x1080")
        if self.fps <= 0:
            raise ValueError("fps must be > 0")

    def dimensions(self) -> tuple[int, int]:
        w, h = self.resolution.lower().split("x", 1)
        return int(w), int(h)

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "role": self.role,
            "engine_policy": self.engine_policy,
            "voice": self.voice,
            "resolution": self.resolution,
            "fps": self.fps,
            "lead_pad": self.lead_pad,
            "tail_pad": self.tail_pad,
            "captions": self.captions,
            "burn_captions": self.burn_captions,
            "approved": self.approved,
            "approved_transcript_sha": self.approved_transcript_sha,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Config":
        return cls(
            language=d.get("language", "en-US"),
            role=d.get("role", "presenter"),
            engine_policy=d.get("engine_policy", "auto"),
            voice=d.get("voice"),
            resolution=d.get("resolution", "1920x1080"),
            fps=int(d.get("fps", 30)),
            lead_pad=float(d.get("lead_pad", 0.4)),
            tail_pad=float(d.get("tail_pad", 0.6)),
            captions=list(d.get("captions", ["srt", "vtt"])),
            burn_captions=bool(d.get("burn_captions", False)),
            approved=bool(d.get("approved", False)),
            approved_transcript_sha=d.get("approved_transcript_sha"),
        )


@dataclass
class SlidePlan:
    """Phase-2 output for one slide: the exact speakable text + its effects."""

    index: int
    image_path: str
    transcript: str = ""
    language: Optional[str] = None  # per-segment override (bilingual decks)
    voice: Optional[str] = None
    effects: list[EffectSpec] = field(default_factory=list)
    clip_path: Optional[str] = None  # pre-rendered video (e.g. Manim) used as this slide's visual

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "image_path": self.image_path,
            "transcript": self.transcript,
            "language": self.language,
            "voice": self.voice,
            "effects": [e.to_dict() for e in self.effects],
            "clip_path": self.clip_path,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SlidePlan":
        return cls(
            index=int(d["index"]),
            image_path=d.get("image_path", ""),
            transcript=d.get("transcript", ""),
            language=d.get("language"),
            voice=d.get("voice"),
            effects=[EffectSpec.from_dict(e) for e in d.get("effects", [])],
            clip_path=d.get("clip_path"),
        )


@dataclass
class SegmentTiming:
    """Phase-3 timing: one rendered audio segment and where it lands in the deck."""

    index: int
    audio_path: str
    duration: float
    start: float = 0.0  # cumulative start in the final timeline

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "audio_path": self.audio_path, "duration": self.duration, "start": self.start}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SegmentTiming":
        return cls(
            index=int(d["index"]),
            audio_path=d["audio_path"],
            duration=float(d["duration"]),
            start=float(d.get("start", 0.0)),
        )


def rebase_segments(durations: list[float], offset: float = 0.0) -> list[SegmentTiming]:
    """Assign cumulative start times so segments concatenate without drift.

    The single source of truth for both clip duration AND caption timing.
    """
    out: list[SegmentTiming] = []
    cursor = offset
    for i, dur in enumerate(durations):
        out.append(SegmentTiming(index=i, audio_path="", duration=float(dur), start=cursor))
        cursor += float(dur)
    return out


def assert_pairing(slides: list[Any], audio_paths: list[Any]) -> None:
    """Enforce the 1:1 slide<->audio contract before assembly (fail fast)."""
    if len(slides) != len(audio_paths):
        raise ValueError(
            f"slide/audio count mismatch: {len(slides)} slides vs {len(audio_paths)} audio segments; "
            "every slide must map to exactly one audio segment (placeholder silence for un-narrated slides)"
        )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
