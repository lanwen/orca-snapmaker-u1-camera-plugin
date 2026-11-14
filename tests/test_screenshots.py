"""Exercise standalone screenshot views and their production-page fixtures."""

from html.parser import HTMLParser
import importlib.util
from pathlib import Path
import subprocess
import pytest

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


class TestScreenshot:
    def test_all_preview_states_render_and_switch_without_host_requests(self, tmp_path):
        directory = str(tmp_path)
        entry = screenshots.prepare(directory)
        for document in [entry, Path(directory) / "showcase.html"]:
            links = Links()
            links.feed(document.read_text())
            assert links.targets, 'The overview links to prepared views'
            for target in links.targets:
                assert (document.parent / target).is_file(), 'Every overview link opens a prepared local view'
        subprocess.run(["node", str(ROOT / "tests/screenshots.test.cjs"), directory], check=True)

    def test_custom_camera_rejects_non_images(self, tmp_path):
        directory = str(tmp_path)
        camera = Path(directory) / "frame.txt"
        camera.write_text("not a camera frame")
        with pytest.raises(ValueError):
            screenshots.prepare(Path(directory) / "output", camera)
        assert not (Path(directory) / 'output').exists()
