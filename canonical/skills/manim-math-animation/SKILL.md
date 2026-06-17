---
name: manim-math-animation
description: Use when the user wants animated math (handwritten-style equation writing, equation morphing between derivation steps, and emphasis) rendered with Manim, as a silent video clip that can stand alone or be spliced into a slides-to-video deck. The free, optional companion to slides-to-video for math lectures.
metadata:
  short-description: Manim math-animation clips (write/morph equations) for lectures
---

# Manim Math Animation

Render math animations with Manim from a simple JSON scene spec -- no hand-written
Manim Python required. Output is a SILENT clip normalized to the slides-to-video
canonical profile (resolution, fps, yuv420p, silent 48 kHz stereo audio), so it
splices cleanly into a slides-to-video deck as an interlude, or stands alone.

This is the optional Manim companion to `slides-to-video`. Keep narration in the
parent slides-to-video pipeline (one timing owner per segment): Manim owns only
the animation timing and is rendered silent.

## Windows Runtime Commands

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } elseif (Test-Path "$env:USERPROFILE\.codex\runtime") { "$env:USERPROFILE\.codex\runtime" } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/manim-math-animation/run_manim_math_animation.bat" <args>
& "$runtime\run_skill.bat" "skills/manim-math-animation/run_manim_math_animation.ps1" <args>
```

## When to use

- write an equation on screen in the 3Blue1Brown "being written" style (`Write`)
- morph one equation into the next derivation step (`TransformMatchingTex`)
- emphasize a step (indicate / circumscribe / flash / wiggle)
- produce a clip to drop into a `slides-to-video` lecture, or a standalone math clip

For plain narrated slides without authored animation, use `slides-to-video`
directly. For genuine cursive handwriting, that is out of scope (no free
Vietnamese-capable model); "handwriting" here means Manim `Write` of typeset math.

## Setup (one time, explicit)

Manim needs a LaTeX distro (with `dvisvgm` + the `standalone`/`preview` packages
+ `cm-super`), the cairo/pango dev libraries, and `ffmpeg`:

```bash
# Debian/Ubuntu
sudo apt-get install -y ffmpeg dvisvgm texlive texlive-latex-extra texlive-fonts-extra libcairo2-dev libpango1.0-dev build-essential
```

Then create the dedicated venv (installs Manim CE):

```bash
bash ~/.local/share/ai-agents-skills/runtime/run_skill.sh skills/manim-math-animation/run_manim_math_animation.sh setup
bash ~/.local/share/ai-agents-skills/runtime/run_skill.sh skills/manim-math-animation/run_manim_math_animation.sh doctor
```

`doctor` reports readiness; it never installs anything.

## Scene spec and subcommands

A scene spec is JSON, e.g.:

```json
{
  "title": "Hàm số bậc hai",
  "equations": ["f(x) = x^2 + 2x + 1", "f(x) = (x + 1)^2"],
  "emphases": [{"at": 1, "type": "circumscribe"}],
  "run_time": 1.5, "width": 1920, "height": 1080, "fps": 30
}
```

- `gen --spec spec.json --output scene.py` -- generate the Manim script (no render).
- `render --spec spec.json --output clip.mp4 [--quality -qh]` -- render + normalize to a
  silent, splice-ready clip.
- `doctor` / `selftest` -- environment check / offline smoke.

Equations are LaTeX (math is language-neutral). Use the `title` (Pango `Text`) for
Vietnamese/other-script prose; it renders with a Unicode font (Noto by default).

## Integration with slides-to-video

Render a clip here, then treat it as one slide's visual in slides-to-video: the
parent measures the clip duration with ffprobe and concatenates it like any other
slide, synthesizing that segment's narration via its own TTS ladder. Because the
Manim clip is silent and already on the canonical profile, the concat stays
drift-free. See `references/integration.md`.
