# Design, scope, and the deferred Manim extension

## Scope of this skill (first build)

Consume pre-made slides (PNG/PDF/PPTX) and produce a narrated, captioned MP4
with Tier-1 effects. Free tools only; Python packages in a dedicated venv;
`ffmpeg` as the one system tool. English + Vietnamese first-class; other
languages supported generically.

## Architecture

- Core is language- and engine-agnostic. Everything language-specific is data
  (`s2v/languages/*.json`).
- All third-party imports (Pillow, PyMuPDF, python-pptx, edge-tts, kokoro,
  piper, soundfile, numpy) are lazy, so the package imports under base Python and
  the offline `selftest` (the CI smoke) needs no venv, network, or ffmpeg.
- Three phases with a SHA-pinned approval gate (see pipeline-and-sync.md).

## What is intentionally deferred (Manim extension)

A separate optional extension will add authored math animation: handwritten
equations (`Write(MathTex)`), equation morphing (`TransformMatchingTex`), and a
glow laser pointer. It is deferred because Manim's toolchain (LaTeX + dvisvgm +
standalone/preview, cairo/pango) is far heavier than this repo's lightweight CI
smoke contract, so it must ship as a separate install with a presence-only smoke
and its own heavier `manim-tex-runtime` system dependency.

Integration rule for that extension (recorded so it is not lost): Manim clips are
rendered SILENT and spliced as interlude clips; narration and captions stay in
this skill's TTS ladder (one timing owner per segment), and Manim clips are
normalized to the same canonical A/V profile before concat.

## Decisions declined

- Genuine cursive handwriting synthesis: English/ASCII-only, no Vietnamese
  diacritic support, and no usable license. "Handwriting" means Manim `Write`
  (animated typography), in the deferred extension.
- XTTS-v2: non-commercial model license; excluded from the TTS ladder.

## Verification

- Offline: `run_slides_to_video.sh selftest` (the runtime smoke) validates the
  deterministic core (pairing, re-basing, ladder, verbalization, effect
  filtergraph, captions, clip args, approval gate) with no network/ffmpeg/venv.
- Local end-to-end: install `ffmpeg`, run `setup`, then analyze -> draft ->
  approve -> render on a small PNG deck.
