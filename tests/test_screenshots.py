"""Exercise standalone screenshot views and their production-page fixtures."""

from html.parser import HTMLParser
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("screenshots", ROOT / "scripts/prepare_screenshots.py")
screenshots = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshots)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in {"a", "iframe"}:
            self.targets.append(attributes.get("href") or attributes.get("src"))


class ScreenshotTests(unittest.TestCase):
    def test_all_four_states_render_and_switch_without_host_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = screenshots.prepare(directory)
            for document in [entry, Path(directory) / "showcase.html"]:
                links = Links()
                links.feed(document.read_text())
                self.assertTrue(links.targets, "The overview links to prepared views")
                for target in links.targets:
                    self.assertTrue((document.parent / target).is_file(),
                                    "Every overview link opens a prepared local view")
            subprocess.run(["node", str(ROOT / "tests/screenshots.test.cjs"), directory], check=True)

    def test_custom_camera_rejects_non_images(self):
        with tempfile.TemporaryDirectory() as directory:
            camera = Path(directory) / "frame.txt"
            camera.write_text("not a camera frame")
            with self.assertRaises(ValueError):
                screenshots.prepare(Path(directory) / "output", camera)
            self.assertFalse((Path(directory) / "output").exists())
