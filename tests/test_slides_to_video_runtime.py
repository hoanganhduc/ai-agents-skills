from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
S2V_ROOT = REPO_ROOT / "canonical" / "runtime" / "skills" / "slides-to-video"
if str(S2V_ROOT) not in sys.path:
    sys.path.insert(0, str(S2V_ROOT))


class SlidesToVideoRuntimeTests(unittest.TestCase):
    def test_pptx_error_mentions_powerpoint_or_libreoffice(self) -> None:
        from s2v import pptx_render

        with (
            mock.patch.object(pptx_render, "powerpoint_status", return_value=None),
            mock.patch.object(pptx_render.shutil, "which", return_value=None),
        ):
            with self.assertRaises(RuntimeError) as caught:
                pptx_render.pptx_to_pdf(Path("deck.pptx"), Path("work"))

        message = str(caught.exception)
        self.assertIn("Microsoft PowerPoint", message)
        self.assertIn("LibreOffice", message)

    def test_doctor_accepts_powerpoint_as_pptx_renderer(self) -> None:
        from s2v import doctor

        with (
            mock.patch.object(doctor, "_tool_version", return_value=None),
            mock.patch.object(doctor, "_which", return_value=None),
            mock.patch.object(doctor.pptx_render, "powerpoint_status", return_value="PowerPoint.Application COM registered"),
            mock.patch.object(doctor.fonts, "available_vietnamese_fonts", return_value=["Arial"]),
            mock.patch.object(doctor.fonts, "font_available", return_value=False),
            mock.patch.object(doctor.fonts, "best_caption_font", return_value="Arial"),
        ):
            report = doctor.collect()

        self.assertTrue(report["ready_for_pptx"])
        self.assertEqual(report["system_tools"]["powerpoint"], "PowerPoint.Application COM registered")
        self.assertEqual(report["fonts"]["caption_font"], "Arial")

    def test_font_hint_uses_installed_caption_font_fallback(self) -> None:
        from s2v import languages

        with mock.patch("s2v.fonts.best_caption_font", return_value="Arial"):
            self.assertEqual(languages.font_hint("vi-VN"), "Arial")


if __name__ == "__main__":
    unittest.main()
