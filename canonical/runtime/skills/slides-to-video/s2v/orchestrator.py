"""Three-phase, human-in-the-loop orchestration.

Phase 1 Analyze   -> deck.json, config.json, slides/
Phase 2 Transcript -> transcript.json (authoritative spoken text + effects),
                      transcript.md (human-readable view). The agent/user edits
                      these; nothing renders here.
Phase 3 Render     -> blocked behind an explicit, SHA-pinned approval gate; then
                      synth -> measure -> per-slide clips -> concat -> captions.

The approval pin is the SHA-256 of transcript.json (the authoritative artifact
the human reviews). Editing it after approval changes the SHA and re-blocks the
render, so a stale approval can never ship edited narration.

Heavy modules (ingest/tts/audio/assemble) are imported lazily inside the phase
that needs them, so this module and the offline selftest import under base
Python.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import captions, languages
from .model import (
    Config,
    Deck,
    SegmentTiming,
    SlidePlan,
    SlideRecord,
    assert_pairing,
    read_json,
    rebase_segments,
    write_json,
)


class ApprovalRequired(RuntimeError):
    pass


def _p(work_dir: Path) -> Path:
    return Path(work_dir)


def deck_path(work_dir: Path) -> Path:
    return _p(work_dir) / "deck.json"


def config_path(work_dir: Path) -> Path:
    return _p(work_dir) / "config.json"


def transcript_json_path(work_dir: Path) -> Path:
    return _p(work_dir) / "transcript.json"


def transcript_md_path(work_dir: Path) -> Path:
    return _p(work_dir) / "transcript.md"


def load_config(work_dir: Path) -> Config:
    return Config.from_dict(read_json(config_path(work_dir)))


def save_config(work_dir: Path, config: Config) -> None:
    write_json(config_path(work_dir), config.to_dict())


def load_plans(work_dir: Path) -> list[SlidePlan]:
    return [SlidePlan.from_dict(d) for d in read_json(transcript_json_path(work_dir))["slides"]]


def save_plans(work_dir: Path, plans: list[SlidePlan]) -> None:
    write_json(transcript_json_path(work_dir), {"slides": [p.to_dict() for p in plans]})


def transcript_sha(work_dir: Path) -> str:
    data = transcript_json_path(work_dir).read_bytes()
    return hashlib.sha256(data).hexdigest()


# -- Phase 1 ---------------------------------------------------------------

def analyze(source: Path, work_dir: Path, config: Config) -> dict:
    config.validate()
    work_dir = _p(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    from . import ingest  # lazy (Pillow/PyMuPDF/python-pptx)

    deck = ingest.ingest(source, work_dir, config.resolution)
    write_json(deck_path(work_dir), deck.to_dict())
    save_config(work_dir, config)
    return {"slides": len(deck.slides), "source_format": deck.source_format,
            "work_dir": str(work_dir)}


# -- Phase 2 ---------------------------------------------------------------

def draft(work_dir: Path) -> dict:
    """Scaffold transcript.json/.md from the deck. The agent fills in narration."""
    deck = Deck.from_dict(read_json(deck_path(work_dir)))
    plans = [
        SlidePlan(index=s.index, image_path=s.image_path, transcript=s.seed_text.strip(),
                  clip_path=s.clip_path)
        for s in deck.slides
    ]
    save_plans(work_dir, plans)
    render_md(work_dir)
    return {"slides": len(plans), "transcript": str(transcript_json_path(work_dir))}


def render_md(work_dir: Path) -> Path:
    config = load_config(work_dir)
    plans = load_plans(work_dir)
    lines = [
        "# Narration transcript",
        "",
        f"> Language: {config.language} | Role: {config.role} | Engine policy: {config.engine_policy}",
        "> Edit the spoken text under each slide. Then run `approve`. "
        "Rendering is blocked until the transcript is approved.",
        "",
    ]
    for plan in plans:
        lines.append(f"## Slide {plan.index + 1}")
        if plan.language:
            lines.append(f"<!-- language: {plan.language} -->")
        lines.append(plan.transcript or "_(write narration for this slide)_")
        if plan.effects:
            lines.append(f"<!-- effects: {len(plan.effects)} configured -->")
        lines.append("")
    path = transcript_md_path(work_dir)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def verbalize_transcripts(work_dir: Path) -> dict:
    """Rewrite math notation into speakable words; invalidates any approval."""
    config = load_config(work_dir)
    plans = load_plans(work_dir)
    for plan in plans:
        locale = plan.language or config.language
        plan.transcript = languages.verbalize_math(plan.transcript, locale)
    save_plans(work_dir, plans)
    render_md(work_dir)
    _invalidate_approval(work_dir)
    return {"slides": len(plans)}


def add_interlude(work_dir: Path, clip_path: str, after_index: int,
                  transcript: str = "", language: Optional[str] = None) -> dict:
    """Insert a pre-rendered video clip (e.g. a Manim math animation) as a new
    slide after ``after_index``. The clip becomes that slide's visual; narration
    is synthesized by the normal TTS ladder. Reindexes slides, keeps the deck and
    transcript 1:1, and invalidates any prior approval.
    """
    deck = Deck.from_dict(read_json(deck_path(work_dir)))
    plans = load_plans(work_dir)
    assert_pairing(deck.slides, plans)
    pos = max(-1, min(after_index, len(deck.slides) - 1)) + 1
    deck.slides.insert(pos, SlideRecord(index=pos, image_path="", source="clip", clip_path=clip_path))
    plans.insert(pos, SlidePlan(index=pos, image_path="", transcript=transcript,
                                language=language, clip_path=clip_path))
    for i, (slide, plan) in enumerate(zip(deck.slides, plans)):
        slide.index = i
        plan.index = i
    write_json(deck_path(work_dir), deck.to_dict())
    save_plans(work_dir, plans)
    render_md(work_dir)
    _invalidate_approval(work_dir)
    return {"slides": len(plans), "interlude_at": pos, "clip": clip_path}


# -- Approval gate ---------------------------------------------------------

def approve(work_dir: Path) -> dict:
    config = load_config(work_dir)
    sha = transcript_sha(work_dir)
    config.approved = True
    config.approved_transcript_sha = sha
    save_config(work_dir, config)
    (_p(work_dir) / "APPROVED").write_text(sha + "\n", encoding="utf-8")
    return {"approved": True, "sha": sha}


def _invalidate_approval(work_dir: Path) -> None:
    config = load_config(work_dir)
    if config.approved:
        config.approved = False
        config.approved_transcript_sha = None
        save_config(work_dir, config)
    marker = _p(work_dir) / "APPROVED"
    if marker.exists():
        marker.unlink()


def check_approved(work_dir: Path) -> None:
    config = load_config(work_dir)
    current = transcript_sha(work_dir)
    if not config.approved or config.approved_transcript_sha != current:
        raise ApprovalRequired(
            "render blocked: transcript is not approved, or it changed since approval. "
            "Review transcript.md, then run `approve`."
        )


def status(work_dir: Path) -> dict:
    work_dir = _p(work_dir)
    out: dict = {"work_dir": str(work_dir)}
    out["analyzed"] = deck_path(work_dir).exists()
    out["drafted"] = transcript_json_path(work_dir).exists()
    if config_path(work_dir).exists():
        config = load_config(work_dir)
        out["language"] = config.language
        out["role"] = config.role
        if out["drafted"]:
            current = transcript_sha(work_dir)
            out["approved"] = config.approved and config.approved_transcript_sha == current
            out["approval_stale"] = config.approved and config.approved_transcript_sha != current
    out["rendered"] = (work_dir / "video.mp4").exists()
    return out


# -- Phase 3 ---------------------------------------------------------------

def render(work_dir: Path) -> dict:
    work_dir = _p(work_dir)
    check_approved(work_dir)  # hard gate
    config = load_config(work_dir)
    deck = Deck.from_dict(read_json(deck_path(work_dir)))
    plans = load_plans(work_dir)
    assert_pairing(deck.slides, plans)  # 1:1 contract, fail fast

    from . import assemble, audio, tts  # lazy (ffmpeg / TTS SDKs)

    width, height = config.dimensions()
    audio_dir = work_dir / "audio"
    clips_dir = work_dir / "clips"
    audio_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)

    wavs: list[Path] = []
    used_engines: list[str] = []
    durations: list[float] = []
    for plan in plans:
        locale = plan.language or config.language
        text = plan.transcript.strip()
        clip_dur = audio.probe_duration(plan.clip_path) if plan.clip_path else 0.0
        if text:
            result = tts.synth(text, locale, config.role, config.engine_policy,
                               audio_dir, plan.index, plan.voice)
            wav = audio.pad_wav(result.wav_path, audio_dir / f"slide_{plan.index:04d}_pad.wav",
                                config.lead_pad, config.tail_pad)
            used_engines.append(result.engine)
        else:  # placeholder silence keeps the 1:1 contract (sized to the clip if any)
            wav = audio.make_silence(audio_dir / f"slide_{plan.index:04d}.wav",
                                     clip_dur if clip_dur > 0 else 2.0)
            used_engines.append("silence")
        audio_dur = audio.probe_duration(wav)
        # A clip-backed slide runs to max(clip, narration); pad narration to fill.
        seg = max(clip_dur, audio_dur)
        if seg > audio_dur + 1e-3:
            wav = audio.extend_to(wav, audio_dir / f"slide_{plan.index:04d}_seg.wav", seg)
        wavs.append(wav)
        durations.append(seg)

    timeline = rebase_segments(durations)

    clips: list[str] = []
    for plan, slide, wav, seg in zip(plans, deck.slides, wavs, timeline):
        out = clips_dir / f"clip_{plan.index:04d}.mp4"
        if plan.clip_path:
            assemble.render_clip_segment(plan.clip_path, str(wav), seg.duration,
                                         width, height, config.fps, str(out))
        else:
            assemble.render_clip(slide.image_path, str(wav), seg.duration,
                                 width, height, config.fps, plan.effects, str(out))
        clips.append(str(out))

    final = work_dir / "video.mp4"
    assemble.concat_clips(clips, str(final), work_dir)

    timings = [SegmentTiming(index=p.index, audio_path=str(w), duration=seg.duration, start=seg.start)
               for p, w, seg in zip(plans, wavs, timeline)]
    cues = captions.deck_cues(plans, timings)
    written = captions.write_captions(work_dir, cues, config.captions)

    burned = None
    if config.burn_captions and (work_dir / "captions.srt").exists():
        burned = work_dir / "video_subbed.mp4"
        import subprocess

        argv = assemble.build_burn_args(str(final), str(work_dir / "captions.srt"),
                                        str(burned), languages.font_hint(config.language),
                                        fps=config.fps, ffmpeg=audio.ffmpeg_bin())
        subprocess.run(argv, check=True, capture_output=True, text=True)

    report = {
        "video": str(final),
        "video_subbed": str(burned) if burned else None,
        "captions": [str(p) for p in written],
        "total_duration": round(sum(durations), 3),
        "slides": len(plans),
        "engines_used": used_engines,
        "language": config.language,
        "role": config.role,
    }
    write_json(work_dir / "render_report.json", report)
    return report
