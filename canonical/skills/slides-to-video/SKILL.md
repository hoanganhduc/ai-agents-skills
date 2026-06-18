---
name: slides-to-video
description: Use when the user wants to turn prepared slides (PNG, PDF, or PPTX) into a narrated, captioned video in a chosen language and presenter role, using only free tools. A three-phase human-in-the-loop flow (analyze, draft transcript, render) gates rendering behind an explicit transcript approval.
metadata:
  short-description: Narrated, captioned videos from prepared slides using free tools
---

# Slides to Video

Turn already-prepared slides into a narrated, captioned MP4 in a user-chosen
language and presenter role. Everything is free of charge and runs from a
dedicated Python virtualenv plus `ffmpeg`. Voice quality is maximized with a
language-aware TTS ladder; English and Vietnamese are first-class.

## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command
target. For Codex-only installs the runtime is usually `%USERPROFILE%\.codex\runtime`;
for multi-agent installs it is usually `%LOCALAPPDATA%\ai-agents-skills\runtime`.

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } elseif (Test-Path "$env:USERPROFILE\.codex\runtime") { "$env:USERPROFILE\.codex\runtime" } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/slides-to-video/run_slides_to_video.bat" <args>
& "$runtime\run_skill.bat" "skills/slides-to-video/run_slides_to_video.ps1" <args>
```

POSIX examples below use `run_skill.sh` and the `.sh` command target.

## When to use

Use this skill when the user wants to:

- narrate a deck they already exported as high-resolution PNG (1920x1080 or
  3840x2160), PDF, or PPTX
- produce a presentation/lecture video with a chosen voice, language, and role
  (presenter, teacher, lecturer, narrator, pitch)
- add Tier-1 visual effects (Ken Burns, highlight, spotlight, laser pointer,
  step reveal) on top of the slides
- generate synced captions (SRT + VTT), with Vietnamese diacritics handled

Do NOT use this for authoring math animations from scratch (handwritten
equations, equation morphing) -- that is the planned, separate `slides-to-video`
Manim extension. This skill consumes pre-made slides.

## Free-tools and licensing posture

- TTS: edge-tts (online, best voice), Kokoro (offline, Apache-2.0), Piper
  (offline, 30+ languages). XTTS-v2 is excluded (non-commercial license).
- Assembly: `ffmpeg` is the one required system tool. Use an LGPL build; the
  H.264 patent obligation applies to redistribution of output, not local use.
- Python packages run only in the dedicated venv; nothing is vendored.

## Setup (one time, explicit)

Step 1 - install the SYSTEM tools with your OS package manager. `ffmpeg`/`ffprobe`
is the only hard requirement; `espeak-ng` is needed only for offline TTS
(Kokoro/Piper); LibreOffice is needed only for PPTX input.

```bash
# Debian/Ubuntu
sudo apt-get update && sudo apt-get install -y ffmpeg espeak-ng libreoffice
# Fedora
sudo dnf install -y ffmpeg espeak-ng libreoffice
# Arch
sudo pacman -S ffmpeg espeak-ng libreoffice
# macOS (Homebrew)
brew install ffmpeg espeak-ng && brew install --cask libreoffice
# Windows (winget); or drop a static ffmpeg build on PATH
winget install Gyan.FFmpeg eSpeak-NG.eSpeak-NG
```

Step 2 - create the dedicated venv and check readiness. Once the skill is
installed into an agent home, use the managed runner:

```bash
bash ~/.codex/runtime/run_skill.sh skills/slides-to-video/run_slides_to_video.sh setup
bash ~/.codex/runtime/run_skill.sh skills/slides-to-video/run_slides_to_video.sh doctor
```

To try it straight from the repo before installing, call the script directly:

```bash
bash canonical/runtime/skills/slides-to-video/run_slides_to_video.sh setup
bash canonical/runtime/skills/slides-to-video/run_slides_to_video.sh doctor
```

`setup` creates `~/.local/share/slides-to-video-venv` and installs requirements.
`doctor` prints a JSON report of exactly what is present or missing and never
installs anything.

## The three-phase, human-in-the-loop flow

Rendering is impossible until the transcript is explicitly approved; editing the
transcript after approval re-blocks rendering (the approval is pinned to the
SHA-256 of `transcript.json`).

**Phase 1 - Analyze.** Ingest slides and record decisions.

```bash
... run_slides_to_video.sh analyze \
  --input "/path/to/slides_dir_or_file" \
  --work-dir "/path/to/deck_work" \
  --language vi-VN --role teacher --engine-policy auto --resolution 1920x1080
```

Writes `deck.json`, `config.json`, and ordered frames under `slides/`.
For PNG-only input there is no embedded text, so seed each slide's narration by
reading the slide image (vision) before/while drafting.

**Phase 2 - Draft transcript and iterate.** Scaffold, then write the narration.

```bash
... run_slides_to_video.sh draft --work-dir "/path/to/deck_work"
```

This creates `transcript.json` (authoritative spoken text + per-slide effects)
and `transcript.md` (human-readable view). Edit `transcript.json` to write the
actual narration per slide, then regenerate the view and present it to the user.
For math decks, run `verbalize` to rewrite notation into speakable words
(e.g. `x^2` -> "x squared" / "x binh phuong"); the user reviews the spoken form.
Iterate (edit specific slides, regenerate) until the user is satisfied.

**Approve (explicit gate).**

```bash
... run_slides_to_video.sh approve --work-dir "/path/to/deck_work"
```

Only do this on an explicit user "approve/render it". It pins the transcript SHA.

**Phase 3 - Render.**

```bash
... run_slides_to_video.sh render --work-dir "/path/to/deck_work"
```

Per slide: synthesize narration via the language-aware ladder, normalize to WAV,
measure the exact duration with `ffprobe`, build a clip of exactly that length
with any effects, then losslessly concatenate. Outputs `video.mp4`,
`captions.srt`/`.vtt`, and `render_report.json`. Refuses if not approved.

`status --work-dir ...` reports pipeline state at any time.

## Languages

The core is language-agnostic. English and Vietnamese are tuned (math lexicon,
voices, fonts). Any other language the engines cover is supported generically:
edge-tts serves ~70 locales online; Piper covers 30+ offline; Kokoro covers 9.
The ladder is language-aware -- e.g. Vietnamese never routes into Kokoro (which
has no Vietnamese voice); offline Vietnamese uses Piper `vi_VN-vais1000-medium`,
online uses edge-tts `vi-VN-HoaiMyNeural`/`NamMinhNeural`. To make another
language first-class, add `s2v/languages/<lang>.json` (voices + math lexicon)
and confirm a covering font; no code change.

## Effects

Tier-1 effects operate on the rendered slide pixels (no authoring): `ken_burns`,
`highlight`, `spotlight`, `laser`, `reveal`. Specify them per slide in
`transcript.json` under each slide's `effects` list. See
`references/effects.md`.

## Animated math interludes (Manim)

A slide's visual can be a pre-rendered video clip instead of a static image:
set `clip_path` on a slide in `transcript.json`, or insert one with `add-interlude`.
This is how a `manim-math-animation` clip is mixed into a narrated deck.

```bash
# 1) render a silent Manim clip with the manim-math-animation skill, then:
... run_slides_to_video.sh add-interlude --work-dir "/path/to/deck_work" \
  --clip "/path/to/manim_clip.mp4" --after 0 \
  --transcript "Completing the square gives a perfect square."
```

The clip becomes a new slide (inserted after the given index; slides are
reindexed and the deck/transcript stay 1:1). Narration is still synthesized by
the TTS ladder, so the Manim clip stays silent (one timing owner). The segment
runs to `max(clip_duration, narration_duration)`: a shorter clip freezes its last
frame, shorter narration is padded with silence -- so the lossless concat stays
drift-free. `add-interlude` invalidates any prior approval, so re-`approve`
before `render`. See `references/pipeline-and-sync.md`.

## References

- `references/pipeline-and-sync.md` - duration-driven sync, the 1:1 pairing
  contract, encoding settings, and the approval gate.
- `references/effects.md` - effect types and their parameters.
- `references/languages-and-tts.md` - the engine ladder, per-language data, and
  spoken-math verbalization.
- `references/design.md` - architecture, scope, and the deferred Manim extension.
