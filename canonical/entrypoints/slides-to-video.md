# Slides to Video Entrypoint

Route requests to turn prepared slides (PNG, PDF, or PPTX) into a narrated,
captioned video to the `slides-to-video` skill. Use for presentation and lecture
videos in a chosen language and presenter role; English and Vietnamese are tuned,
other languages are supported generically.

The skill runs a three-phase, human-in-the-loop flow (analyze, draft transcript,
render) and only renders after the transcript is explicitly approved. Install
`ffmpeg` (and `espeak-ng` for offline TTS) and run `setup` to create the dedicated
venv before the first render.
