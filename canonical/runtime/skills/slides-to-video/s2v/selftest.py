"""Offline self-test (the CI runtime smoke).

Exercises the deterministic core with the standard library only -- no network,
no package installs, no ffmpeg, no TTS. Validates the load-bearing invariants:
1:1 pairing, duration re-basing, the language-aware engine ladder (Vietnamese
skips Kokoro), math verbalization, effect filtergraph building, caption
formatting, clip-arg building, and the SHA-pinned approval gate.

Exit 0 = all checks pass; exit 1 = a check failed.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from . import assemble, captions, effects, languages, orchestrator
from .model import (
    Config,
    Deck,
    EffectSpec,
    SlidePlan,
    SlideRecord,
    assert_pairing,
    rebase_segments,
    write_json,
)


class _Checks:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def ok(self, name: str, cond: bool, detail: str = "") -> None:
        self.results.append((name, bool(cond), detail))

    def raises(self, name: str, fn, exc: type) -> None:
        try:
            fn()
            self.results.append((name, False, "expected exception, none raised"))
        except exc:
            self.results.append((name, True, ""))
        except Exception as other:  # wrong exception type
            self.results.append((name, False, f"raised {type(other).__name__}"))

    @property
    def passed(self) -> bool:
        return all(r[1] for r in self.results)


def _check_model(c: _Checks) -> None:
    cfg = Config(language="vi-VN", role="teacher")
    c.ok("config_roundtrip", Config.from_dict(cfg.to_dict()).role == "teacher")
    eff = EffectSpec.from_dict({"type": "highlight", "params": {"x": 1}, "start": 1.0, "duration": 2.0})
    c.ok("effect_roundtrip", EffectSpec.from_dict(eff.to_dict()).type == "highlight")
    c.raises("pairing_mismatch_raises", lambda: assert_pairing([1, 2], ["a"]), ValueError)
    assert_pairing([1, 2], ["a", "b"])
    segs = rebase_segments([2.0, 3.0, 1.0])
    c.ok("rebase_starts", [s.start for s in segs] == [0.0, 2.0, 5.0])
    c.ok("rebase_durations", [s.duration for s in segs] == [2.0, 3.0, 1.0])


def _check_languages(c: _Checks) -> None:
    c.ok("ladder_vi_skips_kokoro", languages.engine_ladder("vi-VN", "auto") == ["edge", "piper"],
         str(languages.engine_ladder("vi-VN", "auto")))
    c.ok("ladder_en_full", languages.engine_ladder("en-US", "auto") == ["edge", "kokoro", "piper"],
         str(languages.engine_ladder("en-US", "auto")))
    c.ok("ladder_generic_online_only", languages.engine_ladder("de-DE", "auto") == ["edge"],
         str(languages.engine_ladder("de-DE", "auto")))
    c.ok("voice_vi_edge", languages.resolve_voice("vi-VN", "edge") == "vi-VN-HoaiMyNeural")
    c.ok("voice_en_male", languages.resolve_voice("en-US", "edge", gender="male") == "en-US-AndrewMultilingualNeural")
    en = languages.verbalize_math("x^2 = 25", "en-US")
    c.ok("verbalize_en", "squared" in en and "equals" in en, en)
    vi = languages.verbalize_math("x^2 với a ∈ B", "vi-VN")
    c.ok("verbalize_vi", "bình phương" in vi and "thuộc" in vi, vi)


def _check_effects(c: _Checks) -> None:
    fg = effects.build_filtergraph(1920, 1080, 30, 5.0, [
        EffectSpec("ken_burns", {"zoom": 1.2}),
        EffectSpec("highlight", {"x": 10, "y": 10, "w": 100, "h": 50}, 1.0, 2.0),
    ])
    c.ok("fg_zoompan", "zoompan" in fg)
    c.ok("fg_drawbox", "drawbox" in fg)
    c.ok("fg_enable_window", "between(t,1.000,3.000)" in fg, fg)
    c.ok("fg_yuv420p", fg.endswith("format=yuv420p"))
    for et, params in [("spotlight", {"x": 100, "y": 100, "w": 200, "h": 200}),
                       ("laser", {"from": [0, 0], "to": [100, 100]}),
                       ("reveal", {"covers": [{"x": 0, "y": 500, "w": 1920, "h": 200, "at": 2.0}]})]:
        c.ok(f"fg_{et}", "drawbox" in effects.build_filtergraph(1920, 1080, 30, 4.0, [EffectSpec(et, params)]))
    c.raises("fg_bad_effect", lambda: effects.build_filtergraph(1920, 1080, 30, 4.0, [EffectSpec("nope")]), ValueError)


def _check_captions(c: _Checks) -> None:
    cues = captions.segment_cues("One sentence. Two sentence. Three.", 0.0, 6.0)
    c.ok("cue_count", len(cues) == 3, str(len(cues)))
    c.ok("cue_last_end", abs(cues[-1].end - 6.0) < 1e-6)
    srt = captions.to_srt(cues)
    c.ok("srt_timestamp", "00:00:00,000 -->" in srt)
    vtt = captions.to_vtt(cues)
    c.ok("vtt_header", vtt.startswith("WEBVTT"))
    viet = captions.to_srt([captions.Cue(0.0, 1.0, "Đạo hàm của hàm số")])
    c.ok("srt_vietnamese_nfc", "Đạo hàm" in viet)
    c.raises("deck_cues_mismatch", lambda: captions.deck_cues([SlidePlan(0, "a")], []), ValueError)


def _check_assemble(c: _Checks) -> None:
    args = assemble.build_clip_args("a.png", "a.wav", 3.5, 1920, 1080, 30, [], "out.mp4")
    c.ok("clip_loop", "-loop" in args)
    c.ok("clip_codec", "libx264" in args)
    c.ok("clip_duration", "-t" in args and "3.500" in args)
    c.ok("clip_pixfmt", "yuv420p" in args)
    cat = assemble.build_concat_args("list.txt", "final.mp4")
    c.ok("concat_copy", "concat" in cat and "copy" in cat)
    seg = assemble.build_clip_segment_args("clip.mp4", "a.wav", 5.0, 1920, 1080, 30, "out.mp4")
    c.ok("clip_segment_codec", "libx264" in seg)
    c.ok("clip_segment_tpad_freeze", any("tpad=stop_mode=clone" in a for a in seg))
    c.ok("clip_segment_duration", "-t" in seg and "5.000" in seg)
    c.ok("clip_segment_maps_audio", "1:a" in seg and any("[0:v]" in a for a in seg))


def _check_clip_integration(c: _Checks, work: Path) -> None:
    rec = SlideRecord(0, "", clip_path="anim.mp4")
    c.ok("sliderecord_clip_roundtrip", SlideRecord.from_dict(rec.to_dict()).clip_path == "anim.mp4")
    plan = SlidePlan(0, "", clip_path="anim.mp4")
    c.ok("slideplan_clip_roundtrip", SlidePlan.from_dict(plan.to_dict()).clip_path == "anim.mp4")

    deck = Deck("png", [SlideRecord(0, "s0.png", "zero"), SlideRecord(1, "s1.png", "one")])
    write_json(orchestrator.deck_path(work), deck.to_dict())
    orchestrator.save_config(work, Config(language="en-US"))
    orchestrator.draft(work)
    orchestrator.approve(work)
    res = orchestrator.add_interlude(work, "anim.mp4", after_index=0, transcript="interlude narration")
    c.ok("interlude_count", res["slides"] == 3 and res["interlude_at"] == 1)
    deck2 = Deck.from_dict(orchestrator.read_json(orchestrator.deck_path(work)))
    plans2 = orchestrator.load_plans(work)
    c.ok("interlude_pairing", len(deck2.slides) == len(plans2) == 3)
    c.ok("interlude_reindexed",
         [s.index for s in deck2.slides] == [0, 1, 2] and [p.index for p in plans2] == [0, 1, 2])
    c.ok("interlude_clip_set", deck2.slides[1].clip_path == "anim.mp4" and plans2[1].clip_path == "anim.mp4")
    c.ok("interlude_keeps_others",
         deck2.slides[0].image_path == "s0.png" and deck2.slides[2].image_path == "s1.png")
    c.ok("interlude_invalidates_approval", orchestrator.status(work).get("approved") is False)


def _check_approval_gate(c: _Checks, work: Path) -> None:
    deck = Deck("png", [SlideRecord(0, "s0.png", "Slide zero text"),
                        SlideRecord(1, "s1.png", "Slide one text")])
    write_json(orchestrator.deck_path(work), deck.to_dict())
    orchestrator.save_config(work, Config(language="en-US"))
    orchestrator.draft(work)
    c.ok("draft_made_transcript", orchestrator.transcript_json_path(work).exists())
    c.ok("draft_made_md", orchestrator.transcript_md_path(work).exists())
    c.ok("status_unapproved", orchestrator.status(work).get("approved") is False)
    c.raises("render_blocked_before_approve",
             lambda: orchestrator.check_approved(work), orchestrator.ApprovalRequired)
    orchestrator.approve(work)
    orchestrator.check_approved(work)  # should not raise
    c.ok("approved_status", orchestrator.status(work).get("approved") is True)
    # Edit after approval -> SHA changes -> gate re-blocks.
    plans = orchestrator.load_plans(work)
    plans[0].transcript = "edited after approval"
    orchestrator.save_plans(work, plans)
    c.raises("render_blocked_after_edit",
             lambda: orchestrator.check_approved(work), orchestrator.ApprovalRequired)
    c.ok("status_stale", orchestrator.status(work).get("approval_stale") is True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="slides-to-video selftest")
    parser.add_argument("--output", default=None, help="write the JSON report here")
    parser.add_argument("--work-dir", default=None, help="scratch dir (default: a temp dir)")
    args = parser.parse_args(argv)

    work = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="s2v_selftest_"))
    work.mkdir(parents=True, exist_ok=True)

    c = _Checks()
    _check_model(c)
    _check_languages(c)
    _check_effects(c)
    _check_captions(c)
    _check_assemble(c)
    _check_approval_gate(c, work)
    interlude_dir = work / "interlude_check"
    interlude_dir.mkdir(parents=True, exist_ok=True)
    _check_clip_integration(c, interlude_dir)

    report = {
        "ok": c.passed,
        "passed": sum(1 for _, ok, _ in c.results if ok),
        "total": len(c.results),
        "failures": [{"check": n, "detail": d} for n, ok, d in c.results if not ok],
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    return 0 if c.passed else 1
