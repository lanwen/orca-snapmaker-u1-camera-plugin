"""Check emitted script hashes and real browser CSP enforcement, without a printer."""

import base64
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_screenshots import prepare, find_browser
from review_preview import browser


class TestScriptPolicy:
    def assert_script_hashes(self, page, count):
        scripts = re.findall(r"<script>(.*?)</script>", page, re.DOTALL)
        directive = re.search(r"script-src ([^;\"]+)", page).group(1)
        expected = ["'sha256-" + base64.b64encode(hashlib.sha256(
            script.encode("utf-8")).digest()).decode("ascii") + "'" for script in scripts]
        assert len(scripts) == count
        assert directive.split() == expected
        assert '__CAMERA_SCRIPT_HASH__' not in page

    def test_production_and_final_preview_scripts_have_exact_hashes(self, tmp_path):
        self.assert_script_hashes((ROOT / "dist/ui/camera.html").read_text(), 1)
        directory = str(tmp_path)
        prepare(directory)
        for page in Path(directory).glob("*.html"):
            if page.name != "showcase.html":
                self.assert_script_hashes(page.read_text(), 2)

    def test_chromium_executes_production_and_preview_but_blocks_extra_inline_script(self, tmp_path):
        try:
            find_browser()
        except RuntimeError:
            pytest.skip('Chrome/Chromium is not installed')
        directory = str(tmp_path)
        with browser() as client:
            preview = prepare(directory)
            for source in (ROOT / "dist/ui/camera.html", preview):
                page = Path(directory) / ("production-probe.html" if source != preview else "preview-probe.html")
                page.write_text(source.read_text().replace("</body>",
                    '<script>window.unapprovedInline = true</script></body>'))
                client.call("Page.navigate", {"url": page.as_uri()})
                client.wait_for("Boolean(document.querySelector('#play'))")
                assert not client.evaluate('window.unapprovedInline === true')
                if source == preview:
                    client.wait_for("document.querySelector('#stats').dataset.state === 'preparing'")
                    client.evaluate("document.querySelector('[data-preview-state=paused]').click()")
                    client.wait_for("document.querySelector('#stats').dataset.state === 'paused'")

    @pytest.mark.skipif(not (sys.platform == 'darwin' and shutil.which('swiftc')), reason='Requires macOS WebKit and Swift')
    def test_webkit_executes_hashed_script_and_native_bridge_but_blocks_extra_inline(self, tmp_path):
        directory = str(tmp_path)
        binary = Path(directory) / "csp-host"
        subprocess.run(["swiftc", str(ROOT / "tests/csp_host.swift"), "-o", str(binary),
                        "-module-cache-path", str(Path(directory) / "modules")], check=True,
                       capture_output=True, text=True, timeout=120)
        page = Path(directory) / "probe.html"
        page.write_text((ROOT / "dist/ui/camera.html").read_text().replace("</body>",
            '<script>window.unapprovedInline = true</script></body>'))
        result = subprocess.run([str(binary), str(page)], check=True, capture_output=True,
                                text=True, timeout=30)
        assert 'CSP WebKit passed' in result.stdout
