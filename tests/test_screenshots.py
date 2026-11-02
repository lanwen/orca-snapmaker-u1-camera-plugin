"""Exercise standalone screenshot views and their production-page fixtures."""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("screenshots", ROOT / "scripts/prepare_screenshots.py")
screenshots = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshots)


class ScreenshotTests(unittest.TestCase):
    def test_all_four_states_render_and_switch_without_host_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = screenshots.prepare(directory)
            showcase = (Path(directory) / "showcase.html").read_text()
            for state in screenshots.STATES:
                self.assertIn(state + ".html", entry.read_text())
                self.assertIn('src="' + state + '.html"', showcase)
                page = (Path(directory) / (state + ".html")).read_text()
                self.assertIn('aria-label="Simulated OrcaSlicer window with Snapmaker U1 tab selected"', page)
            subprocess.run(["node", str(ROOT / "tests/screenshots.test.cjs"), directory], check=True)

    def test_custom_camera_rejects_non_images(self):
        with tempfile.TemporaryDirectory() as directory:
            camera = Path(directory) / "frame.txt"
            camera.write_text("not a camera frame")
            with self.assertRaisesRegex(ValueError, "JPEG or PNG"):
                screenshots.prepare(Path(directory) / "output", camera)
            self.assertFalse((Path(directory) / "output").exists())
