# Languages and TTS

## Engine ladder (language-aware)

`engine_policy: auto` is online-first, then offline, dropping any engine that
lacks a voice for the language:

- English: edge-tts (online, best) -> Kokoro (offline, Apache-2.0) -> Piper
  (offline).
- Vietnamese: edge-tts `vi-VN-HoaiMyNeural`/`NamMinhNeural` (online) -> Piper
  `vi_VN-vais1000-medium` (offline). Kokoro is skipped: it has no Vietnamese.
- Any other language: edge-tts online (~70 locales via live voice enumeration);
  offline only where a lexicon names a Piper/Kokoro voice.

Set `engine_policy` to `edge`, `kokoro`, or `piper` to force one engine. On a
failure (e.g. edge-tts 403 from a cloud IP, empty audio), the auto ladder falls
through to the next engine.

## Per-language data files

`s2v/languages/<lang>.json` holds, per language:

- `voices`: `{ edge, kokoro, piper }` with `default`/`male`/`female` voice ids
- `roles`: prosody (`rate`, `pitch`, `volume`) per presenter/teacher/lecturer/
  narrator/pitch
- `math`: a `symbols` map and regex `patterns` for spoken-math verbalization
- `font_hint`: a font that covers the script (for captions/burn-in)

English (`en.json`) and Vietnamese (`vi.json`) are shipped and tuned. To make a
new language first-class: add `<lang>.json` with voices + a math lexicon, confirm
a covering font is installed, and (optionally) ship/point at a Piper voice for
offline. No code change.

## Roles

The role shapes both the narration wording (the agent writes it) and delivery
prosody (rate/pitch/volume from the lexicon). edge-tts supports only
rate/pitch/volume (no `<break>`/`<say-as>`/emotion), so pacing/pauses come from
sentence structure in the transcript.

## Spoken math (verbalization)

TTS reads plain text, so math notation must be rewritten into words BEFORE
synthesis. `verbalize` applies the per-language lexicon, e.g.:

- English: `x^2` -> "x squared", `a in B` (∈) -> "a in B", `=` -> "equals".
- Vietnamese: `x^2` -> "x binh phuong", `∈` -> "thuoc", `=` -> "bang",
  `\frac{a}{b}` -> "a phan b".

Verbalization runs at draft time so the user reviews and corrects the spoken form
at the approval gate (it invalidates a prior approval). Languages without a math
lexicon pass through unchanged; the user edits the spoken text directly.

## Captions and fonts

Captions are NFC-normalized and emitted as SRT + VTT. For burned-in captions
(`burn_captions`), libass is forced to a Vietnamese-covering font. The language
lexicon's `font_hint` is preferred, then the runtime falls back to installed
Vietnamese-capable fonts such as Be Vietnam Pro, Arial, Calibri, Segoe UI,
Tahoma, Times New Roman, or DejaVu Sans. Verify fonts with `doctor`; on Windows
it inspects the font registry and font directories even when `fc-list` is not
available.
