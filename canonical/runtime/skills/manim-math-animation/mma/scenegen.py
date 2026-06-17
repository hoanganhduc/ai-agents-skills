"""Generate a Manim Scene .py source string from a SceneSpec.

Pure standard library (string building only) so it is fully unit-testable
offline without importing Manim. LaTeX strings are embedded with ``repr`` so
backslashes survive into the generated source verbatim.
"""

from __future__ import annotations

from .model import SceneSpec

_EMPHASIS_CALL = {
    "indicate": "Indicate({v})",
    "circumscribe": "Circumscribe({v})",
    "flash": "Flash({v}.get_center())",
    "wiggle": "Wiggle({v})",
}


def generate_scene(spec: SceneSpec, scene_name: str = "GeneratedScene") -> str:
    spec.validate()
    lines = [
        "from manim import *",
        "",
        f"config.pixel_width = {spec.width}",
        f"config.pixel_height = {spec.height}",
        f"config.frame_rate = {spec.fps}",
        f"config.background_color = {spec.background!r}",
        "",
        f"class {scene_name}(Scene):",
        "    def construct(self):",
    ]
    body: list[str] = []
    if spec.title:
        body.append(f"title = Text({spec.title!r}, font={spec.font!r}).scale(0.7).to_edge(UP)")
        body.append(f"self.play(Write(title), run_time={spec.run_time})")

    emphasis_by_step = {e.at: e.type for e in spec.emphases}
    prev = None
    for i, equation in enumerate(spec.equations):
        var = f"eq_{i}"
        body.append(f"{var} = MathTex({equation!r}).scale({spec.scale})")
        if prev is None:
            body.append(f"self.play(Write({var}), run_time={spec.run_time})")
        else:
            body.append(f"{var}.move_to({prev})")
            body.append(f"self.play(TransformMatchingTex({prev}, {var}), run_time={spec.run_time})")
        if i in emphasis_by_step:
            body.append(f"self.play({_EMPHASIS_CALL[emphasis_by_step[i]].format(v=var)})")
        prev = var
    body.append(f"self.wait({spec.hold})")

    lines.extend("        " + line for line in body)
    lines.append("")
    return "\n".join(lines)
