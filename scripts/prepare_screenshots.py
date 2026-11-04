"""Prepare standalone simulated Orca screenshots using the real camera page."""

import argparse
import base64
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STATES = (
    "printing",  # Active job, with progress and finish estimates.
    "preparing",  # Layer zero, including early purge while heaters warm.
    "paused",  # Interrupted job, with estimates suppressed.
    "complete",  # Finished job, with current cooling temperatures.
)


def image_uri(camera=None):
    if camera is not None:
        data = Path(camera).read_bytes()
        if data.startswith(b"\xff\xd8"):
            mime = "image/jpeg"
        elif data.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        else:
            raise ValueError("The camera image must be a JPEG or PNG.")
        return "data:" + mime + ";base64," + base64.b64encode(data).decode()
    # Crop the chamber from the existing README screenshot, in its display coordinates.
    photo = base64.b64encode((ROOT / "docs/images/snapmaker-u1-camera.png").read_bytes()).decode()
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="35 300 1644 925">'
           '<image href="data:image/png;base64,' + photo + '" width="1714" height="1439"/></svg>')
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def screenshot_page(camera=None):
    page = (ROOT / "dist/ui/camera.html").read_text()
    fixtures = {
        "printing": {"state": "printing", "phase": "printing", "planned_total": 5200, "progress": .42, "current_layer": 36,
                     "total_layer": 86, "elapsed": 2160, "printing_duration": 2100,
                     "remaining": 3000, "tool": 3, "material": "PLA", "color": "#FFFFFF",
                     "bed": {"temperature": 59, "target": 60},
                     "hotend": {"temperature": 209, "target": 210}},
    }
    # Layer zero can include extrusion; show a plan-based after-time and activity bar.
    fixtures["preparing"] = dict(fixtures["printing"], progress=0, current_layer=0,
                                  phase="preparing", planned_total=2081,
                                  elapsed=96, printing_duration=30, remaining=None,
                                  bed={"temperature": 42, "target": 60},
                                  hotend={"temperature": 157, "target": 210})
    fixtures["paused"] = dict(fixtures["printing"], state="paused", remaining=None,
                               bed={"temperature": 60, "target": 60},
                               hotend={"temperature": 170, "target": 170})
    # Captured completed-job values from the U1; camera image remains illustrative.
    fixtures["complete"] = dict(fixtures["printing"], state="complete", progress=1,
                                 current_layer=86, elapsed=5118, printing_duration=4950,
                                 remaining=0, bed={"temperature": 42, "target": 0},
                                 hotend={"temperature": 41, "target": 0})
    bootstrap = r'''
<script>
// Screenshot-only transport. No messages or commands reach the real printer.
const NativeDate = window.Date;
window.Date = class extends NativeDate {
  constructor(...args) { super(...(args.length ? args : ['2026-11-02T12:00:00Z'])); }
  static now() { return new NativeDate('2026-11-02T12:00:00Z').getTime(); }
};
Object.defineProperty(document, 'hidden', {get: () => false});
const screenshotFixtures = __FIXTURES__;
const screenshotStates = Object.keys(screenshotFixtures);
const screenshotImage = __IMAGE__;
let screenshotState = __SCREENSHOT_STATE__, screenshotHandler, screenshotLight = true, screenshotStarted = false;
function publishScreenshot() {
  screenshotHandler({type:'target', generation:1, target:'Workshop · Snapmaker U1'});
  screenshotHandler({generation:1, printer:screenshotState === 'preparing' ? 'Preparing' : screenshotState,
    light:screenshotLight, stats:screenshotFixtures[screenshotState],
    image:screenshotImage, camera:'Live · 4.4 FPS'});
}
window.orca = {
  onMessage(callback) { screenshotHandler = callback; },
  postMessage(message) {
    if (message.type === 'activity' && message.active) {
      publishScreenshot();
      if (!screenshotStarted) {
        screenshotStarted = true;
        queueMicrotask(() => document.getElementById('play').click());
      }
    }
    if (message.type === 'light') { screenshotLight = message.enabled; publishScreenshot(); }
  }
};
window.addEventListener('DOMContentLoaded', () => {
  document.addEventListener('keydown', event => {
    if (event.ctrlKey || event.metaKey || event.altKey || event.target.closest('input,textarea,[contenteditable]')) return;
    if (/^[1-4]$/.test(event.key)) {
      screenshotState = screenshotStates[Number(event.key) - 1];
      publishScreenshot();
    }
    if (event.key.toLowerCase() === 'h') document.body.classList.toggle('screenshot-surface');
    if (event.key === '?') document.getElementById('screenshot-help').hidden = !document.getElementById('screenshot-help').hidden;
  });
});
</script>
<style>
body.screenshot-surface #stats { border-color:#ffffff26; background:#111820e8; box-shadow:0 8px 32px #0003; backdrop-filter:blur(18px); }
#screenshot-help { position:fixed; top:90px; right:30px; z-index:10; padding:16px; border:1px solid #ffffff40; border-radius:10px; background:#111820f5; font:13px/1.7 system-ui; }
</style>
'''
    bootstrap = bootstrap.replace("__FIXTURES__", json.dumps(fixtures)).replace("__IMAGE__", json.dumps(image_uri(camera)))
    page = page.replace("</head>", bootstrap + "</head>")
    page = page.replace("<body>", '<body><aside id="screenshot-help" hidden>Screenshot data · no printer connection<br>1 Printing · 2 Preparing · 3 Paused · 4 Complete<br>H Toggle stats surface · ? Toggle this help</aside>')
    return page


def prepare(output_dir=ROOT / "dist/screenshots", camera=None):
    page = screenshot_page(camera)
    photo = base64.b64encode((ROOT / "docs/images/snapmaker-u1-camera.png").read_bytes()).decode()
    # Reuse Orca's actual title and tab bar; the simulated body uses the current UI.
    chrome = ('<svg class="orca-chrome" viewBox="35 34 1644 86" width="1644" height="86" '
              'role="img" aria-label="Simulated OrcaSlicer window with Snapmaker U1 tab selected">'
              '<image href="data:image/png;base64,' + photo + '" width="1714" height="1439"/></svg>')
    shell_style = """<style>
.orca-chrome { position:fixed; inset:0 0 auto; width:100%; height:auto; display:block; z-index:5; }
body { --viewer-top:calc(100vw * .05231144); }
</style>"""
    page = page.replace("</head>", shell_style + "</head>")
    page = page.replace("<body>", "<body>" + chrome)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    for state in STATES:
        (output / (state + ".html")).write_text(page.replace("__SCREENSHOT_STATE__", json.dumps(state)))
    links = "".join('<a href="' + state + '.html">' + state.title() + '</a>' for state in STATES)
    index = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>U1 README screenshot views</title><style>
:root { color-scheme:dark; font:16px/1.6 system-ui; background:#10151b; color:#edf3f9; }
body { max-width:760px; margin:60px auto; padding:24px; }
h1 { font-size:28px; font-weight:550; } nav { display:flex; flex-wrap:wrap; gap:12px; margin:28px 0; }
a { color:#a0cbff; text-decoration:none; padding:10px 18px; border:1px solid #ffffff30; border-radius:8px; }
code { color:#a0cbff; } p { color:#c0cbd5; }
</style></head><body><h1>Snapmaker U1 screenshot views</h1>
<p>Simulated Orca window and selected plugin tab. Fixed sample data, production camera UI, no installation or printer connection.</p>
<nav aria-label="Screenshot states">__LINKS__</nav>
<p>Open any state, click the camera, then press <b>1</b> Printing, <b>2</b> Preparing, <b>3</b> Paused, or <b>4</b> Complete.
Press <b>H</b> to toggle the stats background, or <b>?</b> for help.</p>
<p>For matching README images, use the same browser size for all four states (for example 1440 × 1000), hide help, move the pointer away, and capture only the page content.</p>
<p>Use <code>--camera /path/to/frame.jpg</code> when preparing the views to replace the illustrative camera image.</p>
</body></html>""".replace("__LINKS__", links)
    entry = output / "index.html"
    entry.write_text(index)
    tiles = "".join('<section><h2>' + state.title() + '</h2><div class="crop"><iframe title="' +
                    state.title() + ' statistics" src="' + state + '.html"></iframe></div></section>'
                    for state in STATES[1:])
    (output / "showcase.html").write_text('''<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Snapmaker U1 · Camera and print states</title><style>
* { box-sizing:border-box; } html,body { margin:0; width:960px; height:1448px; overflow:hidden; }
body { padding:24px; background:#10151b; color:#edf3f9; font:18px system-ui; display:flex; flex-direction:column; gap:18px; }
section { display:flex; flex-direction:column; gap:10px; flex:none; }
h2 { margin:0; font-size:20px; line-height:24px; font-weight:550; }
iframe { display:block; border:0; background:#0b0e12; }
.hero iframe { width:100%; height:480px; border:1px solid #ffffff20; border-radius:12px; }
.crop { position:relative; height:240px; overflow:hidden; border:1px solid #ffffff20; border-radius:12px; }
.crop iframe { position:absolute; width:900px; height:650px; left:-18px; bottom:-10px; transform:scale(1.25); transform-origin:bottom left; }
</style></head><body><section class="hero"><h2>Camera view</h2><iframe title="Simulated Orca camera tab" src="printing.html"></iframe></section>''' + tiles + '</body></html>')
    return entry


def export_showcase(directory, destination):
    browser = shutil.which("chromium") or shutil.which("google-chrome")
    if not browser:
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if chrome.is_file():
            browser = str(chrome)
    if not browser:
        raise RuntimeError("Install Chrome or Chromium to export the PNG.")
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="u1-screenshot-browser-") as profile:
        result = subprocess.run([
            browser, "--headless", "--no-first-run", "--no-default-browser-check",
            "--disable-background-networking", "--disable-sync", "--hide-scrollbars",
            "--user-data-dir=" + profile, "--window-size=960,1448", "--force-device-scale-factor=1",
            "--virtual-time-budget=4000", "--screenshot=" + str(destination),
            (Path(directory).resolve() / "showcase.html").as_uri(),
        ], capture_output=True, text=True, timeout=45)
        if result.returncode or not destination.is_file():
            raise RuntimeError("Chrome could not render the screenshot: " + result.stderr[-2000:])
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=Path, help="Use a local JPEG or PNG camera frame instead of the README camera crop")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/screenshots")
    parser.add_argument("--export", type=Path, metavar="PNG", help="Export a four-state overview with local Chrome or Chromium")
    args = parser.parse_args()
    entry = prepare(args.output, args.camera)
    print(f"Prepared: {entry}")
    if args.export:
        print(f"Exported: {export_showcase(entry.parent, args.export)}")
    print("Open index.html in a browser. Each state includes a simulated Orca title and tab bar.")
    print("Click the camera, then use 1 Printing, 2 Preparing, 3 Paused, 4 Complete.")
    print("H toggles the stats background; ? toggles help. Hide help before capture.")
    print("Keep the same browser size and capture only page content for all four images.")
