# Manim Math Animation Entrypoint

Route requests to animate math with Manim to the `manim-math-animation` skill:
write a typeset equation on screen (`Write`), morph one equation into the next
derivation step (`TransformMatchingTex`), or emphasize a step
(indicate/circumscribe/flash/wiggle), from a simple JSON scene spec.

Output is a silent clip normalized to the slides-to-video canonical profile, so
it can be spliced into a `slides-to-video` lecture (narration stays in
slides-to-video) or used standalone. Install the Manim toolchain (LaTeX with
dvisvgm + standalone/preview, cairo/pango, ffmpeg) and run `setup` before the
first render.
