"""manim-math-animation runtime package.

Renders Manim math scenes (handwritten-style equation Write, equation morphing
via TransformMatchingTex, and emphasis animations) to a SILENT video clip
normalized to the canonical slides-to-video A/V profile, so the clip can be
spliced into a slides-to-video deck as an interlude (the parent pipeline owns
narration and captions -- one timing owner per segment).

The clip is driven by a JSON "scene spec" so callers never hand-write Manim
Python. All heavy imports (manim, ffmpeg) are LAZY; this package imports under
the standard library alone so the offline selftest needs no venv, LaTeX, or
ffmpeg.
"""

__version__ = "0.1.0"
