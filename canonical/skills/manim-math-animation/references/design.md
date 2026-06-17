# Design and scope

Optional Manim companion to `slides-to-video`, deferred from the original
slides-to-video plan and built as a separate skill because the Manim toolchain
(LaTeX + dvisvgm + standalone/preview, cairo/pango) is far heavier than the
lightweight CI smoke contract.

## Architecture

- Input is a JSON `SceneSpec` (equations + optional title + per-step emphasis +
  output profile). Callers never hand-write Manim Python.
- `scenegen` turns the spec into a Manim `Scene` source string (pure stdlib,
  fully unit-tested offline). LaTeX is embedded with `repr` so backslashes
  survive.
- `render` writes the script, runs the `manim` CLI, then normalizes the output
  with ffmpeg to the canonical profile and adds a silent audio track.
- All heavy imports (manim, ffmpeg) are lazy, so the package imports under base
  Python and the offline `selftest` (the CI smoke) needs no venv/LaTeX/ffmpeg.

## Smoke posture

Default `selftest` is offline and presence-free: it validates spec round-trips,
the generated Manim source (Write/MathTex/TransformMatchingTex/emphasis), and the
manim/ffmpeg argv builders -- without importing Manim or rendering. A real render
needs `setup` (the venv) plus the LaTeX/cairo/pango/ffmpeg system tools, verified
by `doctor`; that is intentionally not part of default CI.

## Animation coverage (first increment)

- `Write` of typeset equations (handwriting feel).
- `TransformMatchingTex` morphing between successive equations.
- Emphasis: `Indicate`, `Circumscribe`, `Flash`, `Wiggle`.
- Title via Pango `Text` (Vietnamese/other-script prose).

## Deliberately deferred / declined

- manim-voiceover baked audio (kept in slides-to-video -- one timing owner).
- Glow laser pointer along a path, 3D scenes, manim-slides full decks.
- Genuine cursive handwriting synthesis (no free Vietnamese-capable model).
- Vietnamese inside LaTeX (use the Pango title path instead).
