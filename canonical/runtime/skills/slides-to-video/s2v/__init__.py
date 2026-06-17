"""slides-to-video runtime package.

Turns prepared slides (PNG/PDF/PPTX) into a narrated, captioned MP4 in a
user-chosen language and presenter role, using only free tools.

Design invariants (see references/pipeline-and-sync.md):
  * Duration-driven sync: per-slide audio is synthesized, normalized to WAV,
    measured with ffprobe, and the slide clip is set to exactly that duration.
  * 1:1 slide<->audio pairing contract, asserted before concat.
  * Engine- and language-agnostic core; per-language tuning lives in data files
    under ``s2v/languages/``.
  * All third-party imports (Pillow, PyMuPDF, python-pptx, edge-tts, ...) are
    LAZY (inside functions) so this package imports under the standard library
    alone and the offline ``selftest`` runs with no venv.
"""

__version__ = "0.1.0"
