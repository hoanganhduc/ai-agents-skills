# Pipeline and sync

## Working directory layout

```
<deck_work>/
  config.json          # Phase-1 decisions (language, role, engine, resolution, approval)
  deck.json            # ordered slide records (frames + seed text)
  slides/              # normalized one-PNG-per-slide frames
  transcript.json      # AUTHORITATIVE spoken text + per-slide effects (SHA-pinned)
  transcript.md        # human-readable view of transcript.json
  APPROVED             # marker holding the approved transcript SHA
  audio/               # per-slide synthesized + normalized WAVs
  clips/               # per-slide MP4 clips (one per slide)
  captions.srt, captions.vtt
  video.mp4            # final output
  render_report.json   # engines used, durations, captions, totals
```

## Duration-driven sync (the core invariant)

The narration is the source of truth for timing. Per slide:

1. Synthesize narration with the language-aware ladder.
2. Decode/normalize to a canonical WAV (48 kHz stereo) so mixed engines concat
   cleanly.
3. Add lead/tail silence so the slide lingers briefly before/after speech.
4. Measure the exact duration with `ffprobe` (`format=duration`).
5. Build a per-slide clip whose video duration equals that measurement exactly
   (`-loop 1 ... -t D_i`).
6. Concatenate clips losslessly with the concat demuxer (`-c copy`).

Because each clip is internally A/V-correct before joining, cumulative drift
across the deck is ~zero. Caption timings come from the same measured durations,
re-based by cumulative slide start, so captions stay in lockstep regardless of
which engine synthesized each slide.

## The 1:1 pairing contract

Every slide maps to exactly one audio segment. `render` asserts
`len(slides) == len(plans)` and fails fast on a mismatch. A slide with empty
narration gets a short placeholder silence rather than silently shifting later
slides' audio.

## Normalization (why no blind copy across heterogeneous sources)

All clips are forced to one profile before the `-c copy` concat: same
resolution, fps, `yuv420p`, and 48 kHz stereo AAC. This is what lets the join be
a lossless stream copy. (When the future Manim extension adds rendered clips,
they must be normalized to this same profile.)

## Encoding settings

`libx264 -crf 18 -preset slow -tune stillimage -pix_fmt yuv420p`, AAC 192k @
48 kHz stereo, `+faststart`. Source PNGs should be at or above the target
resolution; the pipeline downscales but never upscales (regenerate larger PNGs
instead, and never use JPEG for slides).

## The approval gate

`approve` records the SHA-256 of `transcript.json` into `config.json` and an
`APPROVED` marker. `render` recomputes the SHA and refuses unless it matches.
Editing the transcript after approval changes the SHA, so a stale approval can
never render edited narration -- re-approve after edits.

## ffmpeg

`ffmpeg` + `ffprobe` are the one required system tool. Use an LGPL build
(`apt-get install ffmpeg`, or a static build from johnvansickle.com/ffmpeg).
`doctor` reports presence; `render` raises a clear error if they are missing.
H.264/AAC patent obligations apply to distribution of encoded output, not to
local rendering.
