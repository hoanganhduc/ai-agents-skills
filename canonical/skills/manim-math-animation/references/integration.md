# Integration with slides-to-video

This skill produces a **silent** clip normalized to the slides-to-video canonical
A/V profile (default 1920x1080, 30 fps, `yuv420p`, libx264, silent 48 kHz stereo
AAC). That makes it concat-compatible with slides-to-video clips.

## The one rule: a single timing/narration owner per segment

Manim can time animations to narration (manim-voiceover), and slides-to-video
times slides to measured audio. If both drive the same segment you get double
audio and drift. So for the integrated path:

- Render Manim clips **silent** here (the default).
- Let slides-to-video own narration and captions for that segment.

## Recommended flow (interlude clip)

1. `render --spec spec.json --output interlude.mp4` (this skill).
2. In slides-to-video, insert the clip as a slide with `add-interlude` (or set
   `clip_path` on a slide in `transcript.json`). The parent measures the clip
   duration with `ffprobe`, runs the segment to `max(clip, narration)` (freezing
   the clip's last frame / padding narration as needed), and concatenates by
   measured duration, synthesizing the segment's narration via its TTS ladder:

   ```bash
   ... run_slides_to_video.sh add-interlude --work-dir <deck_work> \
     --clip <this_clip>.mp4 --after <index> --transcript "..."
   ```

Because the clip is already on the canonical profile, the parent's lossless
concat stays drift-free. If you change the canonical resolution/fps in
slides-to-video, pass matching `width`/`height`/`fps` in the scene spec.

## Why silent + normalized (not manim-voiceover here)

- One TTS ladder and one caption timeline live in slides-to-video.
- edge-tts / Kokoro / Piper are not built-in manim-voiceover backends, so keeping
  narration in the parent avoids custom speech-service wrappers.
- Normalizing here (fps/SAR/pix_fmt/sample-rate) is what lets the parent concat
  without re-encoding mismatched clips.

## Vietnamese / non-Latin text

Math (`MathTex`) is language-neutral. For Vietnamese or other-script prose use the
spec `title` (rendered with Pango `Text` + a Unicode font such as Noto). Rendering
Vietnamese inside LaTeX would need a XeLaTeX + fontspec template; prefer the Pango
title path. Ensure a Vietnamese-covering font is installed (the `doctor` checks for
Noto).
