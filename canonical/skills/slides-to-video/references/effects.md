# Tier-1 effects

Effects operate on the rendered slide pixels, so they work on any pre-made
PNG/PDF/PPTX slide (no Manim authoring). Each is a single ffmpeg `-vf` chain
fragment applied to the slide's looping image, so a slide is one clip and the
lossless concat is preserved.

Specify effects per slide in `transcript.json`, under each slide's `effects`
list. Each effect is `{ "type", "params", "start", "duration" }` where `start`
and `duration` are seconds inside that slide's narration window
(`duration: null` means until the end of the slide).

## Types and params

- `ken_burns` - slow zoom/pan to keep a static slide alive.
  - `params.zoom` (default `1.15`)
- `highlight` - semi-transparent box over a region.
  - `params.x,y,w,h` (pixels), `params.color` (default `yellow`),
    `params.opacity` (default `0.35`)
- `spotlight` - darken everything except a region (four dark bands around it).
  - `params.x,y,w,h`, `params.dim` (default `0.55`)
- `laser` - a small marker moving between two points (pointer along a
  derivation).
  - `params.from` `[x,y]`, `params.to` `[x,y]`, `params.radius` (default `14`),
    `params.color` (default `red`)
- `reveal` - opaque masks that lift at their reveal time (step/bullet reveal).
  - `params.covers`: list of `{ "x","y","w","h","at" }` where `at` is the
    seconds at which that region is revealed

## Example

```json
{
  "index": 2,
  "image_path": ".../slides/slide_0002.png",
  "transcript": "Consider the derivative. It equals two x.",
  "effects": [
    { "type": "ken_burns", "params": { "zoom": 1.12 } },
    { "type": "highlight", "params": { "x": 760, "y": 420, "w": 400, "h": 120 }, "start": 1.5, "duration": 2.0 }
  ]
}
```

## Notes

- Coordinates are in the target resolution's pixel space (e.g. within
  1920x1080).
- Effects compose; they are applied in list order within the slide's window.
- The polished MoviePy glow-dot laser and Manim handwriting/equation-morph
  effects are the deferred Manim extension; the `laser` here is a lightweight
  ffmpeg marker.
