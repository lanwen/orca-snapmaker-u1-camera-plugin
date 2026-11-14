"""Prepare standalone simulated Orca screenshots using the real camera page."""

import argparse
import base64
import hashlib
import json
import re
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
PREVIEW_LABELS = {
    "printing": "Printing",  # Model printing with an eligible estimate.
    "top-cover-missing": "Top cover disconnected",  # Optional cover is not detected.
    "preparing": "Heating",  # Preparation with heaters below target.
    "preparing-warm": "Preparing (warm)",  # Preparation after warm-up.
    "paused": "Paused",  # Interrupted print without a finish estimate.
    "finishing": "Finishing",  # Full slicer progress while still active.
    "complete": "Complete",  # Finished print with final measurements.
    "cancelled": "Cancelled",  # Stopped job with partial progress.
    "error": "Error",  # Failed print with retained measurements.
    "standby": "Standby",  # Idle printer with current temperatures.
    "unavailable": "Status unavailable",  # Missing printer status, independent of the camera.
    "status-waiting": "Waiting for status",  # Video before the first printer reading.
    "status-retrying": "Status retry after success",  # Failed status with last-success history.
    "status-no-success": "Status retry before success",  # Failed status without invented history.
    "status-expired": "Status expired",  # Video continues after measurements expire.
    "status-recovered": "Status recovered",  # Successful delivery after an interruption.
    "phase-unknown": "Unknown phase",  # Active print without reliable layers.
    "plan-only": "Estimate unavailable",  # Original plan without a current estimate.
    "preparing-no-plan": "Preparation without plan",  # Missing slicer metadata during preparation.
    "connecting": "Connecting to camera",  # Startup before the first camera image.
    "stream-waiting": "Camera retry (3s)",  # Temporary interruption without error prose.
    "stream-error": "Camera error (8s)",  # Persistent interruption with error details.
    "light-error": "Light command failed",  # Action feedback beside the light control.
    "permission-denied": "Preset access denied",  # Consent rejected; playback is blocked.
    "config-missing": "Printer address missing",  # No usable address in plugin Config or the preset.
    "printer-unavailable": "Printer unreachable",  # Connection failure before receiving any camera image.
}


def authorize_preview_scripts(page):
    """Allow only the final production script and offline fixture bootstrap."""
    scripts = re.findall(r"<script>(.*?)</script>", page, re.DOTALL)
    hashes = " ".join("'sha256-" + base64.b64encode(hashlib.sha256(
        script.encode("utf-8")).digest()).decode("ascii") + "'" for script in scripts)
    return re.sub(r"script-src [^;\"]+", lambda _: "script-src " + hashes, page)


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
                     "material_vendor": "Snapmaker", "material_subtype": "SnapSpeed",
                     "material_profile": "Snapmaker PLA SnapSpeed @Snapmaker U1 (0.4 nozzle)",
                     "psu_fan": {"speed": .6, "rpm": None},
                     "hotend_fan": {"speed": .75, "rpm": 16761},
                     "exhaust_fan": {"speed": .6, "rpm": None},
                     "circulation_fan": {"speed": 0, "rpm": 0},
                     "aux_fan": {"speed": .7, "rpm": 3200},
                     "part_fan": {"speed": 1, "rpm": 8200},
                     "bed": {"temperature": 59, "target": 60},
                     "hotend": {"temperature": 209, "target": 210}},
    }
    fixtures["top-cover-missing"] = dict(fixtures["printing"],
                                          exhaust_fan={"speed": None, "rpm": None},
                                          circulation_fan={"speed": None, "rpm": None})
    # Layer zero can include extrusion; show a plan-based after-time and activity bar.
    fixtures["preparing"] = dict(fixtures["printing"], tool=0, progress=0, current_layer=0,
                                  phase="preparing", planned_total=2081,
                                  elapsed=96, printing_duration=30, remaining=None,
                                  bed={"temperature": 42, "target": 60},
                                  hotend={"temperature": 157, "target": 210})
    fixtures["paused"] = dict(fixtures["printing"], tool=1, state="paused", progress=.67, current_layer=58, remaining=None,
                               material="PETG", material_vendor="Polymaker", material_subtype="",
                               material_profile="Polymaker PolyLite PETG @Snapmaker U1 (0.4 nozzle)",
                               bed={"temperature": 60, "target": 60},
                               hotend={"temperature": 170, "target": 170})
    # Captured completed-job values from the U1; camera image remains illustrative.
    fixtures["complete"] = dict(fixtures["printing"], state="complete", progress=1, material_profile=None,
                                 current_layer=86, elapsed=5118, printing_duration=4950,
                                 remaining=0, bed={"temperature": 42, "target": 0},
                                 hotend={"temperature": 41, "target": 0},
                                 aux_fan={"speed": 0, "rpm": 0}, part_fan={"speed": 0, "rpm": 0},
                                 psu_fan={"speed": 0, "rpm": None}, hotend_fan={"speed": 0, "rpm": 0},
                                 exhaust_fan={"speed": 0, "rpm": None}, circulation_fan={"speed": 0, "rpm": 0})
    fixtures["preparing-warm"] = dict(fixtures["preparing"],
                                     bed={"temperature": 60, "target": 60},
                                     hotend={"temperature": 210, "target": 130})
    fixtures["finishing"] = dict(fixtures["printing"], phase="finishing", progress=1,
                                 current_layer=86, remaining=None)
    for state in ("cancelled", "error"):
        fixtures[state] = dict(fixtures["printing"], state=state, progress=.23 if state == "cancelled" else .81,
                               current_layer=20 if state == "cancelled" else 70, remaining=None,
                               bed={"temperature": 45, "target": 0},
                               hotend={"temperature": 95, "target": 0})
    fixtures["standby"] = {"state": "standby", "bed": {"temperature": 24, "target": 0},
                           "hotend": {"temperature": 25, "target": 0}}
    fixtures["unavailable"] = {"state": "unavailable"}
    for state in ("status-waiting", "status-retrying", "status-no-success", "status-expired"):
        fixtures[state] = {"state": "unavailable"}
    fixtures["status-recovered"] = dict(fixtures["printing"])
    fixtures["phase-unknown"] = dict(fixtures["printing"], phase="unknown", progress=.09, current_layer=None,
                                     remaining=None)
    fixtures["plan-only"] = dict(fixtures["printing"], progress=.56, current_layer=48, remaining=None)
    fixtures["preparing-no-plan"] = dict(fixtures["preparing"], planned_total=None,
                                         material_vendor=None, material_subtype=None, material_profile=None)
    for state in ("connecting", "stream-waiting", "stream-error", "light-error"):
        fixtures[state] = dict(fixtures["printing"])
    for state in ("permission-denied", "config-missing", "printer-unavailable"):
        fixtures[state] = {"state": "unavailable"}
    for fixture in fixtures.values():
        active = fixture.get("tool")
        fixture["tools"] = [
            {"state": "connected" if tool == active else "parked" if tool < 3 else "unavailable",
             "material": ["PLA", "PETG", "PLA", None][tool],
             "material_vendor": ["Polymaker", "Prusament", "Snapmaker", None][tool],
             "color": ["#FFB387", "#83CEE5", "#DAE3B9", None][tool],
             "nozzle_diameter": .6 if tool == 1 else .4,
             "heater": {"temperature": 25, "target": 0}}
            for tool in range(4)]
        if active is not None:
            fixture["tools"][active].update({key: fixture.get(key) for key in
                ("material", "material_vendor", "material_subtype", "material_profile", "color")})
            fixture["tools"][active].update(state="connected", heater=fixture["hotend"])
            fixture["tools"][(active + 1) % 3]["heater"] = {"temperature": 157, "target": 210}
        fixture["layer_height"] = .2 if active is not None else None
    bootstrap = r'''
<script>
// Screenshot-only transport. No messages or commands reach the real printer.
const NativeDate = window.Date;
let screenshotOffset = 0;
window.Date = class extends NativeDate {
  constructor(...args) { super(...(args.length ? args : ['2026-11-02T12:00:00Z'])); }
  static now() { return new NativeDate('2026-11-02T12:00:00Z').getTime() + screenshotOffset; }
};
Object.defineProperty(document, 'hidden', {get: () => false});
const screenshotFixtures = __FIXTURES__;
const screenshotLabels = __LABELS__;
const screenshotStates = Object.keys(screenshotFixtures);
const screenshotImage = __IMAGE__;
let screenshotState = __SCREENSHOT_STATE__, screenshotHandler, screenshotLight = true;
let screenshotAppliedState, screenshotGeneration = 0;
function publishScreenshot() {
  const changed = screenshotAppliedState !== screenshotState;
  if (changed) { screenshotOffset = 0; screenshotAppliedState = screenshotState; screenshotGeneration++; }
  const generation = screenshotGeneration;
  const interrupted = screenshotState.startsWith('stream-');
  // Decode the fixed camera image only after a state change or playback restart.
  const needsImage = changed || !document.getElementById('image')?.hasAttribute('src');
  const trigger = document.getElementById('preview-state');
  if (trigger) trigger.title = 'Preview state: ' + screenshotLabels[screenshotState];
  document.querySelectorAll('[data-preview-state]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.previewState === screenshotState));
  });
  const blocked = {
    'permission-denied': 'Preset access is off. Set "host" in plugin Config, or reset "allow_preset_connection" to null to ask again.',
    'config-missing': 'Printer address missing. Set "host" in plugin Config or add an address to the selected U1 preset.'
  }[screenshotState];
  if (blocked) {
    screenshotHandler({error:blocked, supported:true});
    return;
  }
  const offline = screenshotState === 'printer-unavailable';
  screenshotHandler({type:'target', generation, target:screenshotState === 'status-retrying'
    ? 'Workshop printer with a long preset name · printer.example.invalid' : 'Workshop · Snapmaker U1'});
  const statusPreview = screenshotState.startsWith('status-');
  if (changed && ['status-retrying', 'status-expired', 'status-recovered'].includes(screenshotState)) {
    screenshotHandler({generation, stats:screenshotFixtures.printing, light:true});
    screenshotOffset = 12000;
  }
  if (changed && screenshotState === 'status-recovered') {
    screenshotHandler({generation, stats:null, status_error:'timeout', light:null});
    screenshotOffset = 15000;
  }
  screenshotHandler({generation, printer:screenshotLabels[screenshotFixtures[screenshotState].state],
    ...(!statusPreview || screenshotState === 'status-recovered' ? {light:offline ? null : screenshotLight} : {}), light_error:screenshotState === 'light-error' ? 'Could not change light — try again' : '',
    ...(!statusPreview || screenshotState === 'status-recovered' ? {stats:screenshotFixtures[screenshotState]} : {}),
    ...(['unavailable', 'status-retrying', 'status-no-success'].includes(screenshotState) ? {stats:null, status_error:'timeout', light:null} : {}),
    image:!needsImage || offline || screenshotState === 'connecting' || (interrupted && !changed) ? undefined : screenshotImage, fps:4.4, frozen:false});
  if (changed && screenshotState === 'status-expired') queueMicrotask(() => window.dispatchEvent(new Event('pageshow')));
  if (offline) {
    screenshotHandler({generation, failed:true, camera:'Cannot reach printer — check its address and connection'});
    screenshotOffset = 8000;
  }
  if (interrupted && !changed) screenshotHandler({generation, failed:true, camera:'Printer returned HTTP 404'});
  if (changed && offline) queueMicrotask(() => window.dispatchEvent(new Event('pageshow')));
  if (changed && interrupted) {
    const state = screenshotState;
    document.getElementById('image').addEventListener('load', () => {
      // Apply the interruption after the player's load handler, regardless of listener order.
      queueMicrotask(() => {
        if (screenshotState !== state) return;
        screenshotHandler({generation, failed:true, camera:'Printer returned HTTP 404'});
        screenshotOffset = state === 'stream-waiting' ? 3000 : 8000;
        window.dispatchEvent(new Event('pageshow'));
      });
    }, {once:true});
  }
  if (changed) queueMicrotask(() => {
    const play = document.getElementById('play');
    if (play && !play.hidden && !play.disabled) {
      screenshotAppliedState = undefined;
      screenshotOffset = 0;
      play.click();
    }
  });
}
window.orca = {
  onMessage(callback) { screenshotHandler = callback; },
  postMessage(message) {
    if (message.type === 'activity' && message.active) {
      publishScreenshot();
    }
    if (message.type === 'light') { screenshotLight = message.enabled; publishScreenshot(); }
  }
};
window.addEventListener('DOMContentLoaded', () => {
  const picker = document.getElementById('preview-picker');
  const trigger = document.getElementById('preview-state');
  const menu = document.getElementById('preview-menu');
  const showMenu = (open) => {
    if (!menu) return;
    menu.hidden = !open;
    trigger.setAttribute('aria-expanded', String(open));
  };
  picker?.addEventListener('pointerenter', () => showMenu(true));
  picker?.addEventListener('pointerleave', () => showMenu(false));
  picker?.addEventListener('focusout', event => {
    if (!picker.contains(event.relatedTarget)) showMenu(false);
  });
  trigger?.addEventListener('click', () => showMenu(menu.hidden));
  document.querySelectorAll('[data-preview-state]').forEach(button => {
    button.addEventListener('click', () => {
      screenshotState = button.dataset.previewState;
      publishScreenshot();
      showMenu(false);
      trigger.focus();
    });
  });
  document.querySelectorAll('[data-preview-step]').forEach(button => {
    button.addEventListener('click', () => {
      const index = screenshotStates.indexOf(screenshotState);
      screenshotState = screenshotStates[(index + Number(button.dataset.previewStep) + screenshotStates.length) % screenshotStates.length];
      publishScreenshot();
    });
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu && !menu.hidden) {
      showMenu(false);
      trigger.focus();
    }
  });
  document.addEventListener('keydown', event => {
    if (event.ctrlKey || event.metaKey || event.altKey || event.target.closest('input,textarea,select,[contenteditable]')) return;
    if (/^[1-4]$/.test(event.key)) {
      screenshotState = ['printing', 'preparing', 'paused', 'complete'][Number(event.key) - 1];
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
    bootstrap = (bootstrap.replace("__FIXTURES__", json.dumps(fixtures))
                 .replace("__LABELS__", json.dumps(PREVIEW_LABELS))
                 .replace("__IMAGE__", json.dumps(image_uri(camera))))
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
        view = page.replace("__SCREENSHOT_STATE__", json.dumps(state))
        (output / (state + ".html")).write_text(authorize_preview_scripts(view))
        surface_view = view.replace("<body>", '<body class="screenshot-surface">').replace(
            "</head>", """<style>
body.screenshot-surface #stats .filament,
body.screenshot-surface #stats .parked-temperature,
body.screenshot-surface #stats .trace-base { visibility:visible; }
body.screenshot-surface #stats .fan-island { border-color:#ffffff26; background:#111820e8; box-shadow:0 4px 16px #0003; backdrop-filter:blur(18px); }
</style></head>""")
        if state == "printing":
            showcase_style = """<style>
body .viewer footer { grid-template-columns:minmax(0, 1fr); row-gap:54px; }
body .viewer footer #stats { grid-column:1; grid-row:2; }
body .viewer footer .stream-controls { grid-column:1; grid-row:1; flex-direction:row; align-items:center; max-width:100%; }
body .viewer #stop { opacity:1; }
body .viewer #camera { background:#17202be8; border-color:#ffffff40; }
</style>"""
            (output / "printing-showcase.html").write_text(authorize_preview_scripts(surface_view.replace(
                "</head>", showcase_style + "</head>")))
        else:
            (output / (state + "-stats.html")).write_text(authorize_preview_scripts(surface_view.replace(
                "</head>", "<style>.stream-controls { display:none !important; }</style></head>")))
    options = "".join('<button type="button" data-preview-state="' + state +
                      '" aria-pressed="' + str(state == "preparing").lower() + '">' + label + '</button>'
                      for state, label in PREVIEW_LABELS.items())
    controls = ('<aside id="preview-controls" aria-label="Offline preview controls">'
                '<div id="preview-picker"><button type="button" id="preview-state" '
                'aria-label="Choose preview state" aria-expanded="false" aria-controls="preview-menu" '
                'title="Preview state: Heating"><span aria-hidden="true">◫</span></button>'
                '<div id="preview-menu" hidden><div class="preview-panel">'
                '<strong>Preview state</strong><small>Offline sample data</small>' + options +
                '<a href="showcase.html">Open showcase ↗</a></div></div></div>'
                '<button type="button" data-preview-step="-1" aria-label="Previous preview state" '
                'title="Previous state"><span aria-hidden="true">↑</span></button>'
                '<button type="button" data-preview-step="1" aria-label="Next preview state" '
                'title="Next state"><span aria-hidden="true">↓</span></button></aside>')
    preview_style = """<style>
#preview-controls { position:fixed; left:12px; top:40%; transform:translateY(-50%); z-index:10; width:42px; padding:4px; display:grid; gap:2px; border:1px solid #ffffff30; border-radius:24px; background:#17212ef5; box-shadow:0 12px 32px #0008, 0 2px 6px #0006, inset 0 1px #ffffff18; font:12px/1.3 system-ui; }
#preview-controls button { display:block; width:100%; border:0; border-radius:18px; padding:9px 0; background:transparent; color:#dce8f4; cursor:pointer; font:inherit; }
#preview-controls button:hover, #preview-controls button:focus-visible { background:#ffffff16; }
#preview-controls button:focus-visible, #preview-controls a:focus-visible { outline:2px solid #7abaff; outline-offset:1px; }
#preview-controls button span { font-size:18px; line-height:18px; }
#preview-picker { position:relative; }
#preview-controls #preview-state { background:#7abaff20; color:#a0cbff; }
#preview-menu { position:absolute; left:100%; top:50%; transform:translateY(-50%); padding-left:10px; width:min(242px, calc(100vw - 64px)); }
.preview-panel { max-height:calc(80dvh - 120px); overflow-y:auto; padding:10px; border:1px solid #ffffff30; border-radius:16px; background:#17212efa; box-shadow:0 16px 40px #0009, 0 2px 6px #0006; }
.preview-panel strong, .preview-panel small { display:block; padding:0 8px; }
.preview-panel strong { color:#edf3f9; font-size:12px; }
.preview-panel small { margin:3px 0 8px; color:#9aabb8; font-size:10px; }
#preview-controls .preview-panel button { padding:7px 8px; border-radius:7px; text-align:left; }
#preview-controls .preview-panel button[aria-pressed="true"] { background:#7abaff25; color:#b9daff; }
#preview-controls .preview-panel a { display:block; margin-top:6px; padding:8px; border-top:1px solid #ffffff20; color:#a0cbff; text-decoration:none; }
</style>"""
    index = (page.replace("__SCREENSHOT_STATE__", json.dumps("preparing"))
             .replace("</head>", preview_style + "</head>")
             .replace("<body>", "<body>" + controls))
    entry = output / "index.html"
    entry.write_text(authorize_preview_scripts(index))
    tiles = "".join('<section><h2>' + state.title() + '</h2><div class="crop"><iframe title="' +
                    state.title() + ' statistics" src="' + state + '-stats.html"></iframe></div></section>'
                    for state in STATES[1:])
    (output / "showcase.html").write_text('''<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Snapmaker U1 · Camera and print states</title><style>
* { box-sizing:border-box; } html,body { margin:0; width:100%; min-height:100%; }
body { max-width:960px; margin:0 auto; padding:24px; background:#10151b; color:#edf3f9; font:18px system-ui; display:flex; flex-direction:column; gap:18px; }
section { display:flex; flex-direction:column; gap:10px; flex:none; }
h2 { margin:0; font-size:20px; line-height:24px; font-weight:550; }
iframe { display:block; border:0; background:#0b0e12; }
.hero iframe { width:100%; height:480px; border:1px solid #ffffff20; border-radius:12px; }
.crop { position:relative; height:180px; overflow:hidden; border:1px solid #ffffff20; border-radius:12px; }
.crop iframe { position:absolute; width:1100px; height:650px; left:-20px; bottom:-14px; }
@media(max-width:650px) { body { padding:16px; } .crop { height:420px; } }
</style></head><body><section class="hero"><h2>Camera view</h2><iframe title="Simulated Orca camera tab" src="printing-showcase.html"></iframe></section>''' + tiles + '</body></html>')
    return entry


def find_browser():
    # Prefer packaged Chrome: Ubuntu AppArmor permits its sandboxed executable path.
    browser = (shutil.which("google-chrome-stable") or shutil.which("google-chrome")
               or shutil.which("chromium"))
    if not browser:
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if chrome.is_file():
            browser = str(chrome)
    if not browser:
        raise RuntimeError("Install Chrome or Chromium to export the PNG.")
    return browser


def export_showcase(directory, destination):
    browser = find_browser()
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="u1-screenshot-browser-") as profile:
        result = subprocess.run([
            browser, "--headless", "--no-first-run", "--no-default-browser-check",
            "--disable-background-networking", "--disable-sync", "--hide-scrollbars",
            "--user-data-dir=" + profile, "--window-size=960,1268", "--force-device-scale-factor=1",
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
    print(f"Use the state picker for all {len(PREVIEW_LABELS)} states; 1–4 switch the showcase states.")
    print("H toggles the stats background; ? toggles help. Hide help before capture.")
    print("Keep the same browser size and capture only page content for all four images.")
